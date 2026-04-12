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
        # (Storm/Izzet SB), boseiju catches Boseiju, Who Endures (LE/Omnath/4-5c SB)
        if any(w in opp_lower for w in ["affinity", "tron"]):
            if any(w in card_lower for w in ["wear", "force of vigor", "collector",
                                               "haywire", "shattering", "hurkyl",
                                               "pithing", "meltdown", "boseiju"]):
                board_in_priority.append((card_name, count, 9))

        # Counterspells vs combo
        if any(w in opp_lower for w in ["storm", "living end", "goryo", "titan"]):
            if any(w in card_lower for w in ["flusterstorm", "mystical dispute",
                                               "spell pierce", "force of negation"]):
                board_in_priority.append((card_name, count, 8))

        # Board wipes vs creature decks
        if any(w in opp_lower for w in ["energy", "zoo", "affinity", "prowess"]):
            if any(w in card_lower for w in ["wrath", "verdict", "damnation",
                                               "explosives", "ratchet"]):
                board_in_priority.append((card_name, count, 7))

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

        # Board out graveyard hate and slow spells vs artifact decks
        if any(w in opp_lower for w in ["affinity", "pinnacle"]):
            if any(w in card_lower for w in ["surgical", "nihil", "cling",
                                               "leyline of the void", "rest in peace",
                                               "blood moon", "chant"]):
                board_out_priority.append((card_name, min(count, 2), 7))

    board_out_priority.sort(key=lambda x: -x[2])

    # Execute swaps (up to 5 cards)
    swaps = 0
    max_swaps = 5
    in_idx = 0
    out_idx = 0

    while swaps < max_swaps and in_idx < len(board_in_priority) and out_idx < len(board_out_priority):
        in_card, in_count, _ = board_in_priority[in_idx]
        out_card, out_count, _ = board_out_priority[out_idx]

        swap_count = min(in_count, out_count, max_swaps - swaps)

        if swap_count > 0:
            new_main[in_card] = new_main.get(in_card, 0) + swap_count
            new_side[in_card] = max(0, new_side.get(in_card, 0) - swap_count)
            if new_side[in_card] == 0:
                del new_side[in_card]

            new_main[out_card] = max(0, new_main.get(out_card, 0) - swap_count)
            if new_main[out_card] == 0:
                del new_main[out_card]
            new_side[out_card] = new_side.get(out_card, 0) + swap_count

            swaps += swap_count

        in_count -= swap_count
        out_count -= swap_count
        if in_count <= 0:
            in_idx += 1
        if out_count <= 0:
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
