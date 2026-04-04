"""
Strategic Commentary Engine for MTG Replay Events.

Post-processes the structured events[] array in each snapshot and injects
'commentary' events that explain WHY a play was made, not just WHAT happened.

Commentary rules are pattern-based: they scan sequences of events within a
snapshot and match known strategic patterns from Modern MTG.
"""

import re
from typing import List, Dict, Optional, Tuple


def _commentary(text: str, category: str = "strategy", importance: str = "notable") -> dict:
    """Create a commentary event dict."""
    return {
        "category": "commentary",
        "type": "commentary",
        "player": None,
        "card": None,
        "text": text,
        "raw": "",
        "details": {
            "importance": importance,  # "notable", "key", "brilliant"
            "subcategory": category,
        },
    }


# ─── Pattern matchers ───────────────────────────────────────────────────

def _check_blink_etb(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect Ephemerate/blink targeting ETB creatures and explain the value."""
    results = []
    etb_values = {
        "Endurance": "shuffles opponent's graveyard into their library, denying graveyard strategies like Living End, Goryo's Vengeance, and Snapcaster Mage flashback targets",
        "Omnath, Locus of Creation": "triggers the enter-the-battlefield ability again, gaining 4 life and resetting the landfall trigger chain for additional mana and damage",
        "Solitude": "exiles another creature on re-entry, providing a second removal spell for the cost of a single white mana",
        "Snapcaster Mage": "grants flashback to another instant or sorcery in the graveyard, effectively drawing an extra spell",
        "Quantum Riddler": "draws 2 more cards on re-entry, generating massive card advantage",
        "Spell Queller": "temporarily exiles the quelled spell, then re-enters to exile a new spell from the stack — if nothing is on the stack, the original spell is permanently lost",
        "Subtlety": "puts another creature or planeswalker on top of its owner's library, acting as a second tempo removal",
        "Phlage, Titan of Fire's Fury": "triggers the enter-the-battlefield ability again, dealing 3 damage and gaining 3 life",
        "Atraxa, Grand Unifier": "reveals the top 10 cards again, drawing one of each card type found — potentially drawing 5+ cards",
        "Griselbrand": "re-enters with full loyalty/toughness, ready to activate the draw-7 ability again",
    }

    for i, e in enumerate(events):
        if e.get("type") == "blink":
            target = (e.get("card") or e.get("text", "").replace("Blink ", "")).strip()
            # Look backwards for what triggered this blink
            blink_source = None
            for j in range(i - 1, max(i - 5, -1), -1):
                prev = events[j]
                if prev.get("type") in ("cast_spell", "resolve"):
                    card = prev.get("card", "")
                    if card in ("Ephemerate", "Restoration Angel", "Yorion, Sky Nomad", "Felidar Guardian"):
                        blink_source = card
                        break

            if target in etb_values:
                source_text = f"{blink_source} on" if blink_source else "Blinking"
                commentary_text = f"Why {source_text} {target}? The re-entry {etb_values[target]}."
                importance = "key" if target in ("Endurance", "Omnath, Locus of Creation", "Solitude") else "notable"
                results.append((i + 1, _commentary(commentary_text, "blink_value", importance)))

    return results


def _check_evoke_sacrifice(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect Evoke creatures (Solitude, Subtlety, Grief, Fury, Endurance) and explain the pitch cost."""
    results = []
    evoke_explanations = {
        "Solitude": "exiles a creature for free by pitching a white card from hand — a zero-mana removal spell that trades card advantage for tempo",
        "Subtlety": "puts a creature or planeswalker on top of its owner's library by pitching a blue card — disrupts the opponent's board without spending mana",
        "Grief": "forces the opponent to discard by pitching a black card — a free Thoughtseize effect that strips their best card",
        "Endurance": "shuffles a graveyard into its owner's library by pitching a green card — shuts down graveyard strategies at instant speed for free",
        "Fury": "deals 4 damage divided among creatures by pitching a red card — a free board sweeper against small creatures",
    }

    for i, e in enumerate(events):
        if e.get("type") == "evoke":
            card = e.get("card", "")
            if card in evoke_explanations:
                exiled = (e.get("details") or {}).get("exiled", "a card")
                text = f"Evoke play: {card} {evoke_explanations[card]}. Pitched {exiled} as the alternate cost."
                results.append((i + 1, _commentary(text, "evoke_value", "notable")))

    return results


def _check_cascade_living_end(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect Cascade into Living End and explain the combo."""
    results = []
    has_cascade = any(e.get("type") == "cascade" for e in events)
    has_living_end = any(e.get("type") == "living_end" for e in events)

    if has_cascade and has_living_end:
        # Count creatures in graveyard context from surrounding events
        text = ("Cascade combo fires: the cascade spell has converted mana cost 3+, "
                "so it cascades past everything until hitting Living End (cost 0). "
                "Living End swaps all creatures in graveyards with all creatures on the battlefield — "
                "the Living End player has been cycling large creatures into their graveyard all game "
                "to set up this mass reanimation.")
        # Find the living_end event position
        for i, e in enumerate(events):
            if e.get("type") == "living_end":
                results.append((i + 1, _commentary(text, "combo", "brilliant")))
                break

    return results


def _check_fetch_shock_sequencing(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect fetch + shock land and explain the life cost decision."""
    results = []
    for i, e in enumerate(events):
        if e.get("type") == "fetch_crack":
            det = e.get("details") or {}
            fetch_life = det.get("fetch_life", 0)
            shock_life = det.get("shock_life", 0)
            total_life = fetch_life + shock_life
            found = det.get("found", "")
            tapped = det.get("tapped", False)

            if total_life >= 3:
                text = (f"Paying {total_life} life for an untapped dual land — "
                        f"this aggressive mana base enables casting spells on curve "
                        f"but the life loss adds up against aggressive decks.")
                results.append((i + 1, _commentary(text, "mana_management", "notable")))

    return results


def _check_griselbrand_activation(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect Griselbrand draw-7 and explain the strategy."""
    results = []
    for i, e in enumerate(events):
        if e.get("type") == "activated_ability" and e.get("card") == "Griselbrand":
            text = ("Griselbrand's activated ability: pay 7 life to draw 7 cards. "
                    "After cheating Griselbrand into play (usually via Goryo's Vengeance), "
                    "the goal is to draw enough cards to find a winning combination "
                    "before the opponent can respond.")
            results.append((i + 1, _commentary(text, "combo", "key")))
            break  # Only annotate the first activation per snapshot

    return results


def _check_storm_sequence(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect storm count buildup and explain the mechanic."""
    results = []
    for i, e in enumerate(events):
        if e.get("type") == "storm":
            copies = (e.get("details") or {}).get("copies", 0)
            if copies >= 3:
                text = (f"Storm triggers with {copies} copies! Each spell cast this turn "
                        f"added to the storm count. The storm player chains cheap rituals "
                        f"and cantrips to build a lethal storm count before casting the finisher.")
                results.append((i + 1, _commentary(text, "combo", "brilliant")))

    return results


def _check_pw_ultimate(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect planeswalker ultimates and explain their impact."""
    results = []
    for i, e in enumerate(events):
        if e.get("type") == "pw_activate":
            det = e.get("details") or {}
            lc = det.get("loyalty_change", 0)
            card = e.get("card", "")
            if lc <= -5:
                text = (f"{card} activates its ultimate ability ({lc:+d} loyalty). "
                        f"Ultimates are game-winning effects that require protecting "
                        f"the planeswalker for multiple turns to reach.")
                results.append((i + 1, _commentary(text, "planeswalker", "brilliant")))

    return results


def _check_counter_war(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect counterspell interactions."""
    results = []
    for i, e in enumerate(events):
        if e.get("type") == "countered":
            card = e.get("card", "")
            # Look for what countered it
            for j in range(i - 1, max(i - 4, -1), -1):
                prev = events[j]
                if prev.get("type") in ("cast_spell", "resolve") and prev.get("card", "") in (
                    "Consign to Memory", "Counterspell", "Force of Negation",
                    "Spell Pierce", "Flusterstorm", "Mystical Dispute",
                    "Dovin's Veto", "Negate", "Spell Queller"
                ):
                    counter_name = prev.get("card", "a counterspell")
                    text = (f"{card} is countered by {counter_name}. "
                            f"The opponent held up mana to deny this key spell — "
                            f"a critical tempo swing that wastes the caster's mana investment.")
                    results.append((i + 1, _commentary(text, "interaction", "key")))
                    break

    return results


def _check_board_wipe(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect board wipes and explain their impact."""
    results = []
    for i, e in enumerate(events):
        if e.get("type") == "board_wipe":
            card = e.get("card", "")
            det = e.get("details") or {}
            x_val = det.get("x_value", 0)
            text = (f"{card} sweeps the board (X={x_val}), destroying all creatures "
                    f"with mana value {x_val} or less. Board wipes reset the game state "
                    f"and are most effective when the opponent has committed more resources to the board.")
            results.append((i + 1, _commentary(text, "removal", "key")))

    return results


def _check_endurance_graveyard_hate(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect Endurance ETB shuffling many cards and explain the disruption."""
    results = []
    for i, e in enumerate(events):
        if e.get("type") == "other":
            txt = e.get("text", "") or ""
            m = re.search(r'Endurance ETB: P(\d+) shuffles (\d+) cards from GY into library', txt)
            if m:
                count = int(m.group(2))
                target_player = int(m.group(1))
                if count >= 5:
                    text = (f"Endurance shuffles {count} cards from the graveyard back into the library. "
                            f"This is devastating against graveyard-dependent strategies — "
                            f"it removes all flashback targets, delve fuel, escape fodder, "
                            f"and reanimation targets in one shot.")
                    results.append((i + 1, _commentary(text, "graveyard_hate", "key")))
                elif count >= 1:
                    text = (f"Endurance shuffles {count} card(s) from the graveyard into the library, "
                            f"denying potential flashback or recursion targets.")
                    results.append((i + 1, _commentary(text, "graveyard_hate", "notable")))

    return results


def _check_dash_tempo(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect Dash plays and explain the tempo advantage."""
    results = []
    for i, e in enumerate(events):
        if e.get("type") == "cast_spell":
            det = e.get("details") or {}
            if det.get("dash"):
                card = e.get("card", "")
                text = (f"{card} cast with Dash — it gets haste and returns to hand at end of turn. "
                        f"This avoids sorcery-speed removal and lets the player replay it next turn "
                        f"for repeated value, trading board presence for resilience.")
                results.append((i + 1, _commentary(text, "tempo", "notable")))

    return results


def _check_annihilator(events: List[dict]) -> List[Tuple[int, dict]]:
    """Detect Annihilator triggers and explain the devastation."""
    results = []
    for i, e in enumerate(events):
        if e.get("type") == "annihilator":
            count = (e.get("details") or {}).get("count", 0)
            if count >= 4:
                text = (f"Annihilator {count} triggers — the defending player must sacrifice "
                        f"{count} permanents before blockers are even declared. "
                        f"This is one of the most powerful attack triggers in Magic, "
                        f"often ending the game in 1-2 attacks regardless of the damage dealt.")
                results.append((i + 1, _commentary(text, "combat", "brilliant")))

    return results


def _check_big_life_swing(events: List[dict], snapshot: dict) -> List[Tuple[int, dict]]:
    """Detect large life total changes from combat damage."""
    results = []
    for i, e in enumerate(events):
        txt = e.get("text", "") or ""
        # Match "X damage to PLAYER" patterns
        m = re.search(r'(\d+)\s+damage\s+to\s+', txt)
        if m:
            dmg = int(m.group(1))
            if dmg >= 8:
                text = (f"Massive {dmg} damage swing! This kind of burst damage "
                        f"can end games quickly and forces the defending player "
                        f"to respect the board or risk being overwhelmed.")
                results.append((i + 1, _commentary(text, "combat", "key")))

    return results


# ─── Main annotator ─────────────────────────────────────────────────────

ALL_CHECKERS = [
    _check_blink_etb,
    _check_evoke_sacrifice,
    _check_cascade_living_end,
    _check_fetch_shock_sequencing,
    _check_griselbrand_activation,
    _check_storm_sequence,
    _check_pw_ultimate,
    _check_counter_war,
    _check_board_wipe,
    _check_endurance_graveyard_hate,
    _check_dash_tempo,
    _check_annihilator,
]


def annotate_snapshot(snapshot: dict) -> dict:
    """Add commentary events to a snapshot's events array.
    
    Returns the snapshot with commentary events injected at the right positions.
    """
    events = snapshot.get("events", [])
    if not events:
        return snapshot

    # Collect all commentary insertions: (position, commentary_event)
    insertions: List[Tuple[int, dict]] = []

    for checker in ALL_CHECKERS:
        try:
            insertions.extend(checker(events))
        except Exception:
            pass  # Never let commentary crash the pipeline

    # Also run the big-life-swing checker which needs the snapshot
    try:
        insertions.extend(_check_big_life_swing(events, snapshot))
    except Exception:
        pass

    if not insertions:
        return snapshot

    # Sort by position (descending) to insert from back to front
    insertions.sort(key=lambda x: x[0], reverse=True)

    # Deduplicate: max one commentary per position
    seen_positions = set()
    unique = []
    for pos, evt in insertions:
        if pos not in seen_positions:
            seen_positions.add(pos)
            unique.append((pos, evt))

    # Insert commentary events
    new_events = list(events)
    for pos, evt in unique:
        new_events.insert(min(pos, len(new_events)), evt)

    snapshot["events"] = new_events
    return snapshot


def annotate_replay(replay_data: dict) -> dict:
    """Annotate all snapshots in a full replay (BO3 match) with strategic commentary."""
    if not isinstance(replay_data, dict):
        return replay_data

    for game in replay_data.get("games", []):
        for i, snap in enumerate(game.get("snapshots", [])):
            game["snapshots"][i] = annotate_snapshot(snap)

    return replay_data
