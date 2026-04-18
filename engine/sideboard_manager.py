"""
Sideboard manager — extracted from GameRunner (Phase 4C).

Handles AI sideboarding between games in a best-of-3 match.
Uses archetype-aware heuristics to determine what to board in/out.
"""
from __future__ import annotations

from typing import Dict, Tuple


def sideboard(mainboard: Dict[str, int], sideboard_cards: Dict[str, int],
              my_deck: str, opponent_deck: str) -> Tuple[Dict[str, int], Dict[str, int]]:
    """AI sideboarding: swap cards between mainboard and sideboard.

    Returns (new_mainboard, new_sideboard).
    Also prints swap log to stderr for debugging.
    """
    if not sideboard_cards:
        return mainboard, sideboard_cards

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
        if any(w in opp_lower for w in ["affinity", "tron", "pinnacle"]):
            if any(w in card_lower for w in ["wear", "force of vigor", "collector",
                                               "haywire", "shattering", "hurkyl",
                                               "pithing", "meltdown", "boseiju",
                                               "time raveler", "orchid phantom",
                                               "clarion conqueror"]):
                board_in_priority.append((card_name, count, 9))

        # Counterspells vs combo
        if any(w in opp_lower for w in ["storm", "living end", "goryo", "titan"]):
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

        # Board out slow engines vs fast artifact aggro
        # "fable" = Fable of the Mirror-Breaker (3CMC enchantment engine, too
        # slow vs T4-kill Affinity). "consign" = Consign to Memory (counters
        # artifact spells that are already resolved by the time you can hold
        # up UU — mostly dead). "witch enchanter" = slow creature without a
        # target vs artifact deck.
        if any(w in opp_lower for w in ["affinity", "pinnacle"]):
            if any(w in card_lower for w in ["bombardment", "voice of victory",
                                               "static prison", "fable",
                                               "consign", "witch enchanter"]):
                board_out_priority.append((card_name, min(count, 2), 6))

        # Board out Blood Moon vs R-based and aggro decks (their lands already produce R)
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
