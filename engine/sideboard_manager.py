"""
Sideboard manager — extracted from GameRunner (Phase 4C).

Two backends:
  - Legacy (default): archetype-keyword string matching below.
  - Solver (opt-in via SB_SOLVER=new): oracle-driven marginal-value
    solver from ai/sideboard_solver.py. See
    docs/proposals/sideboard_solver.md.
"""
from __future__ import annotations

import os
from typing import Dict, Tuple


def sideboard(mainboard: Dict[str, int], sideboard_cards: Dict[str, int],
              my_deck: str, opponent_deck: str) -> Tuple[Dict[str, int], Dict[str, int]]:
    """AI sideboarding: swap cards between mainboard and sideboard.

    Returns (new_mainboard, new_sideboard).
    Also prints swap log to stderr for debugging.

    Backend: env var `SB_SOLVER=new` routes to oracle-driven solver;
    otherwise falls through to the legacy string-match logic below.
    """
    if not sideboard_cards:
        return mainboard, sideboard_cards

    if os.environ.get("SB_SOLVER", "old").lower() == "new":
        try:
            return _solver_sideboard(mainboard, sideboard_cards,
                                      my_deck, opponent_deck)
        except Exception as exc:  # pragma: no cover — fallback on any solver error
            import sys
            print(f"  [SB solver fell back to legacy: {exc}]", file=sys.stderr)
            # fall through to legacy

    new_main = dict(mainboard)
    new_side = dict(sideboard_cards)

    opp_lower = opponent_deck.lower()

    # Cards to board IN against specific matchups
    board_in_priority = []
    board_out_priority = []

    for card_name, count in sideboard_cards.items():
        card_lower = card_name.lower()

        # Graveyard hate vs graveyard decks
        # bojuka catches Bojuka Bog (a land that exiles a graveyard)
        if any(w in opp_lower for w in ["goryo", "living end", "dredge"]):
            if any(w in card_lower for w in ["relic", "rest in peace", "leyline of the void",
                                               "surgical", "nihil", "endurance",
                                               "tormod", "crypt", "cling to dust",
                                               "bojuka"]):
                board_in_priority.append((card_name, count, 10))

        # Artifact hate vs artifact decks
        # pithing catches Pithing Needle (Tron SB), meltdown catches Meltdown
        # (Storm/Izzet SB), boseiju catches Boseiju, Who Endures (LE/Omnath/4-5c SB).
        # "time raveler" catches Teferi, Time Raveler (bounces Urza's Saga tokens);
        # "orchid phantom" catches White Orchid Phantom (land destruction vs
        # Razortide / Silverbluff / Treasure Vault); "clarion conqueror" creates
        # 2/2 prison tokens that tax artifact attackers.
        #
        # H_ACT_3 fix (2026-05-02 Affinity diagnosis): added "damping"
        # (Damping Sphere — cost-tax vs Affinity ramp), "subtlety"
        # (flash bounce of Construct/Mox), "foundation breaker"
        # (Living End's evoke artifact removal), "trinisphere" (Eldrazi
        # Tron tax piece), "endurance" (3/4 flash reach blocker shuts
        # Plating's ground game) to the keyword list. Each appears in
        # at least one top deck's sideboard but pre-fix sat unused
        # vs Affinity, leaving 8 of 10 top decks under-tuned.
        if any(w in opp_lower for w in ["affinity", "tron", "pinnacle"]):
            if any(w in card_lower for w in ["wear", "force of vigor", "collector",
                                               "haywire", "shattering", "hurkyl",
                                               "pithing", "meltdown", "boseiju",
                                               "time raveler", "orchid phantom",
                                               "clarion conqueror",
                                               "damping", "subtlety",
                                               "foundation breaker",
                                               "trinisphere", "endurance"]):
                board_in_priority.append((card_name, count, 9))

        # Counterspells vs combo + artifact aggro
        # H_ACT_3 fix: artifact aggro (Affinity/Pinnacle) added to the
        # match list because cheap free-cast counterspells (Force of
        # Negation, Mystical Dispute) are excellent vs T4-kill decks.
        # Spell Pierce is included for the same reason — Affinity's
        # threats (Saga, Plating, Frogmite) all cost {1}-{2}.
        if any(w in opp_lower for w in ["storm", "living end", "goryo", "titan",
                                          "affinity", "pinnacle"]):
            if any(w in card_lower for w in ["flusterstorm", "mystical dispute",
                                               "spell pierce", "force of negation"]):
                board_in_priority.append((card_name, count, 8))

        # Board wipes vs creature decks (affinity/pinnacle get priority boost:
        # sweepers are the primary answer to their wide token boards)
        if any(w in opp_lower for w in ["energy", "zoo", "affinity", "prowess", "pinnacle"]):
            if any(w in card_lower for w in ["wrath", "verdict", "damnation",
                                               "explosives", "ratchet"]):
                prio = 9 if any(w in opp_lower for w in ["affinity", "pinnacle"]) else 7
                board_in_priority.append((card_name, count, prio))

        # Counterspells + lifegain vs burn/aggro
        if any(w in opp_lower for w in ["energy", "zoo", "prowess", "affinity"]):
            if any(w in card_lower for w in ["flusterstorm", "mystical dispute",
                                               "spell pierce", "negate"]):
                board_in_priority.append((card_name, count, 8))
            # Sheoldred is a house vs aggro (lifegain + drain)
            if "sheoldred" in card_lower:
                board_in_priority.append((card_name, count, 9))

        # Generic good cards
        if any(w in card_lower for w in ["celestial purge"]) and \
           any(w in opp_lower for w in ["energy", "storm"]):
            board_in_priority.append((card_name, count, 6))

        # Blood Moon vs multicolor / Tron / greedy mana bases
        if "blood moon" in card_lower:
            if any(w in opp_lower for w in ["tron", "titan", "omnath", "4c", "5c",
                                              "4/5c", "domain", "jeskai", "goryo",
                                              "control"]):
                board_in_priority.append((card_name, count, 9))

    board_in_priority.sort(key=lambda x: -x[2])

    # Determine cards to board out
    for card_name, count in mainboard.items():
        card_lower = card_name.lower()

        # Board out removal vs combo/creatureless decks
        if any(w in opp_lower for w in ["storm", "living end", "amulet", "titan"]):
            if any(w in card_lower for w in ["bolt", "push", "discharge",
                                               "dismember", "prismatic ending",
                                               "fatal", "wrath", "damnation"]):
                board_out_priority.append((card_name, min(count, 2), 8))

        # Board out slow/conditional cards vs combo
        if any(w in opp_lower for w in ["storm", "goryo", "living end"]):
            if any(w in card_lower for w in ["consider", "drown", "charm"]):
                board_out_priority.append((card_name, min(count, 2), 6))

        # Board out slow cards vs aggro (includes artifact aggro)
        if any(w in opp_lower for w in ["energy", "zoo", "prowess", "affinity", "pinnacle"]):
            if any(w in card_lower for w in ["charm", "command"]):
                board_out_priority.append((card_name, min(count, 2), 5))
            # Board out conditional removal and cantrips vs aggro
            if any(w in card_lower for w in ["drown in the loch", "consider"]):
                board_out_priority.append((card_name, min(count, 2), 6))

        # Board out slow engines vs fast artifact aggro.
        # Patterns cover commonly-slow / situational cards across decks that
        # struggle to find swap-out candidates vs T4-kill Affinity. Each
        # pattern justifies why it's weak in the artifact matchup:
        #   - "fable" = Fable of the Mirror-Breaker (3CMC enchantment engine,
        #     too slow). "consign" = Consign to Memory (dead counter vs already-
        #     resolved artifact spells). "witch enchanter" = slow creature w/o
        #     target vs artifact deck.
        #   - "elesh norn" (7CMC, Affinity kills T4-5 before it casts).
        #   - "endurance" (4CMC, graveyard hate not relevant vs Affinity).
        #   - "persist" (reanimate, needs GY setup — too slow vs T5 kill).
        #   - "undying evil" (1CMC combat trick, marginal vs wide boards).
        #   - "summoner's pact" (slow tutor, loses Amulet tempo).
        #   - "violent urge" (4CMC pump) and "mutagenic growth" (phyrexian
        #     pump; life cost bad vs Cranial Plating damage) — situational.
        #   - "vexing bauble" (1x noncommittal artifact).
        #   - "archon of cruelty" (7CMC reanimator target too slow to deploy).
        if any(w in opp_lower for w in ["affinity", "pinnacle"]):
            # NOTE: do NOT list "elesh norn" (shuts down Construct/Mox Opal
            # ETB triggers — anti-Affinity tech), "archon of cruelty"
            # (Goryo's reanimator payoff — needed IN the deck), or
            # "endurance" (3/4 flash reach blocker, actively useful vs
            # Affinity's ground attackers — boarding it out swaps a live
            # blocker for a single Boseiju land-destroy). Previous
            # versions caught these and caused 4c Omnath / Goryo's
            # regressions.
            #
            # H_ACT_3 fix (2026-05-02 Affinity diagnosis): real T1
            # decks (Azorius Control WST, Living End, 4c Omnath) had
            # NONE of the previously-listed slow patterns in their
            # mainboards, so the swap couldn't execute even when SB
            # hate was available. Added (each justified vs Affinity
            # specifically):
            #   "chalice of the void" — Memnite/Mox Opal at 0CMC
            #     dodge it; mostly dead in this matchup.
            #   "sanctifier en-vec" — anti-red/black creature hate;
            #     useless vs colorless Affinity.
            #   "wan shi tong" — 5CMC legendary, too slow vs T4 kill.
            #   "wrenn and six" — 3CMC planeswalker, slow value, no
            #     direct Affinity disruption.
            #   "phelia" — 2CMC flicker creature, slow value.
            #   "risen reef" — 3CMC ETB engine, slow vs T4 kill.
            #   "force of negation" — pitch counterspell; dead vs
            #     creature-only aggro (no game-ending sorceries to
            #     counter). Cuts free for hate in Living End / 4c
            #     Omnath. Both decks board into Force of Vigor or
            #     Foundation Breaker for the artifact answer.
            if any(w in card_lower for w in ["bombardment", "voice of victory",
                                               "static prison", "fable",
                                               "consign", "witch enchanter",
                                               "undying evil",
                                               "summoner's pact",
                                               "mutagenic growth",
                                               "vexing bauble",
                                               "chalice of the void",
                                               "sanctifier en-vec",
                                               "wan shi tong",
                                               "wrenn and six",
                                               "phelia",
                                               "risen reef",
                                               "force of negation"]):
                board_out_priority.append((card_name, min(count, 2), 6))

        # Board out Blood Moon vs mono-R and base-R aggro decks. Rationale:
        # SB slot pressure — these matchups have better anti-aggro boards-in
        # (Wrath, Wear/Tear, Bombardment retention) and Blood Moon at 3CMC
        # typically arrives T3-T4, after T5 kill decks have already set up.
        # Affinity/Pinnacle considered for this list during investigation
        # (their Saga lands would seem like great Blood Moon targets) but
        # falsified at N=20: keeping Blood Moon forces cuts of Bombardment +
        # Voice of Victory, both of which outperform it in the matchup. See
        # docs/experiments/2026-04-19_blood_moon_sb_hypothesis_failed.md.
        if any(w in opp_lower for w in ["prowess", "energy", "storm", "affinity", "pinnacle"]):
            if "blood moon" in card_lower:
                board_out_priority.append((card_name, min(count, 2), 8))

        # Board out graveyard hate and slow spells vs artifact decks
        if any(w in opp_lower for w in ["affinity", "pinnacle"]):
            if any(w in card_lower for w in ["surgical", "nihil", "cling",
                                               "leyline of the void", "rest in peace",
                                               "chant"]):
                board_out_priority.append((card_name, min(count, 2), 7))

        # Board out narrow interaction vs big mana / multicolor
        # (makes room for Blood Moon from SB)
        if any(w in opp_lower for w in ["tron", "titan", "omnath", "4c", "5c",
                                          "4/5c", "domain", "control"]):
            if any(w in card_lower for w in ["chant", "charm", "bombardment"]):
                board_out_priority.append((card_name, min(count, 2), 6))

    board_out_priority.sort(key=lambda x: -x[2])

    # Execute swaps. Default max 5, but artifact matchups need more coverage:
    # Affinity runs 18+ artifacts, so 5 hate pieces leaves most untouched.
    # Raise to 7 when the opponent is an artifact deck so the sideboarded
    # hate can actually change the matchup. Paired with the cards.py
    # artifact-scaling fix from session 3.
    swaps = 0
    max_swaps = 5
    if any(w in opp_lower for w in ["affinity", "pinnacle", "tron"]):
        max_swaps = 7
    in_idx = 0
    out_idx = 0
    # Track remaining counts separately (tuple values are immutable)
    in_remaining = [count for _, count, _ in board_in_priority]
    out_remaining = [count for _, count, _ in board_out_priority]

    while swaps < max_swaps and in_idx < len(board_in_priority) and out_idx < len(board_out_priority):
        in_card = board_in_priority[in_idx][0]
        in_count = in_remaining[in_idx]
        out_card = board_out_priority[out_idx][0]
        out_count = out_remaining[out_idx]

        swap_count = min(in_count, out_count, max_swaps - swaps,
                        new_main.get(out_card, 0),  # can't remove more than we have
                        new_side.get(in_card, 0))    # can't add more than SB has

        if swap_count == 0:
            # Out card already removed from main or in card exhausted in SB — skip
            if new_main.get(out_card, 0) == 0:
                out_idx += 1
            elif new_side.get(in_card, 0) == 0:
                in_idx += 1
            else:
                out_idx += 1
            continue

        if swap_count > 0:
            new_main[in_card] = new_main.get(in_card, 0) + swap_count
            new_side[in_card] = max(0, new_side.get(in_card, 0) - swap_count)
            if new_side.get(in_card, 0) == 0 and in_card in new_side:
                del new_side[in_card]

            new_main[out_card] = max(0, new_main.get(out_card, 0) - swap_count)
            if new_main.get(out_card, 0) == 0 and out_card in new_main:
                del new_main[out_card]
            new_side[out_card] = new_side.get(out_card, 0) + swap_count

            swaps += swap_count

        in_remaining[in_idx] -= swap_count
        out_remaining[out_idx] -= swap_count
        if in_remaining[in_idx] <= 0:
            in_idx += 1
        if out_remaining[out_idx] <= 0:
            out_idx += 1

    # Log swaps
    swap_log = []
    for card_name in set(list(mainboard.keys()) + list(new_main.keys())):
        old_count = mainboard.get(card_name, 0)
        new_count = new_main.get(card_name, 0)
        if new_count > old_count:
            swap_log.append(f"+{new_count - old_count} {card_name}")
        elif new_count < old_count:
            swap_log.append(f"-{old_count - new_count} {card_name}")
    if swap_log:
        import sys
        print(f"  Sideboard ({my_deck} vs {opponent_deck}): {', '.join(sorted(swap_log))}", file=sys.stderr)

    return new_main, new_side


# ─────────────────────────────────────────────────────────────
# Oracle-driven solver backend (SB_SOLVER=new)
# ─────────────────────────────────────────────────────────────

_SB_SOLVER_CARD_DB = None


def _get_card_db():
    """Lazy CardDatabase singleton — SB planning needs template lookups."""
    global _SB_SOLVER_CARD_DB
    if _SB_SOLVER_CARD_DB is None:
        from engine.card_database import CardDatabase
        _SB_SOLVER_CARD_DB = CardDatabase()
    return _SB_SOLVER_CARD_DB


def _load_gameplan(deck_name: str):
    """Load opp's DeckGameplan if one exists — richer GY-reliance signal."""
    try:
        from ai.gameplan import get_gameplan
        return get_gameplan(deck_name)
    except Exception:
        return None


def _solver_sideboard(mainboard: Dict[str, int],
                      sideboard_cards: Dict[str, int],
                      my_deck: str,
                      opponent_deck: str
                      ) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Oracle-driven sideboard backend.

    Loads opp's decklist from decks/modern_meta.py:MODERN_DECKS and
    delegates per-card value scoring to ai/sideboard_solver.sb_value.
    """
    from decks.modern_meta import MODERN_DECKS
    from ai.sideboard_solver import plan_sideboard

    opp_deck = MODERN_DECKS.get(opponent_deck)
    if opp_deck is None:
        # Unknown opp deck — fall through to legacy.
        raise ValueError(f"unknown opponent deck: {opponent_deck}")

    opp_main = opp_deck.get("mainboard") or {}
    card_db = _get_card_db()

    new_main, new_sb, log = plan_sideboard(
        mainboard, sideboard_cards,
        opp_deck_name=opponent_deck,
        card_db=card_db,
        opp_mainboard=opp_main,
        opp_gameplan_loader=_load_gameplan,
        my_deck_name=my_deck,
    )

    if log:
        import sys
        swap_summary = ", ".join(log)
        print(f"  SB-solver ({my_deck} vs {opponent_deck}): {swap_summary}",
              file=sys.stderr)

    return new_main, new_sb
