"""
Best-of-3 match runner with sideboarding.

Runs a full bo3 match:
  - Game 1: mainboard vs mainboard
  - Sideboard: each player swaps cards based on matchup heuristics
  - Game 2: post-board decks
  - Game 3 (if needed): post-board decks

Captures per-game logs, sideboard decisions, and match result.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import copy
import random

from engine.card_database import CardDatabase
from engine.game_runner import GameRunner, GameResult


# ═══════════════════════════════════════════════════════════════════
# Sideboard strategy: tag-based matchup heuristics
# ═══════════════════════════════════════════════════════════════════

# Card tags for sideboard decisions
CARD_TAGS = {
    # Graveyard hate
    "Relic of Progenitus": {"graveyard_hate"},
    "Leyline of the Void": {"graveyard_hate"},
    "Surgical Extraction": {"graveyard_hate"},
    "Cling to Dust": {"graveyard_hate"},
    "Endurance": {"graveyard_hate", "creature"},
    "Nihil Spellbomb": {"graveyard_hate"},

    # Artifact/enchantment hate
    "Force of Vigor": {"artifact_hate"},
    "Wear // Tear": {"artifact_hate"},
    "Shattering Spree": {"artifact_hate"},
    "Haywire Mite": {"artifact_hate"},
    "Foundation Breaker": {"artifact_hate"},
    "Pick Your Poison": {"artifact_hate", "flexible_removal"},
    "Boseiju, Who Endures": {"artifact_hate"},
    "Engineered Explosives": {"sweeper", "artifact_hate"},

    # Counterspells / stack interaction
    "Flusterstorm": {"counter", "combo_hate"},
    "Mystical Dispute": {"counter", "blue_hate"},
    "Spell Pierce": {"counter"},
    "Metallic Rebuke": {"counter"},
    "Consign to Memory": {"counter"},
    "Warping Wail": {"counter", "flexible_removal"},

    # Sweepers
    "Wrath of the Skies": {"sweeper"},
    "Supreme Verdict": {"sweeper"},
    "Ratchet Bomb": {"sweeper"},

    # Anti-aggro
    "Celestial Purge": {"anti_aggro", "removal"},
    "Spatial Contortion": {"removal"},
    "Dispatch": {"removal"},
    "Prismatic Ending": {"removal"},

    # Anti-control / anti-midrange
    "Blood Moon": {"anti_greedy_mana"},
    "Trinisphere": {"anti_cheap_spells"},
    "Ethersworn Canonist": {"anti_storm"},
    "Dress Down": {"anti_creature"},
    "Teferi, Time Raveler": {"anti_counter", "anti_control"},

    # Value / threats
    "Tireless Tracker": {"value", "creature"},
    "Obstinate Baloth": {"anti_discard", "creature"},
    "Murktide Regent": {"threat", "creature"},
    "Empty the Warrens": {"alt_wincon"},
    "Grapeshot": {"alt_wincon"},
    "Pieces of the Puzzle": {"card_advantage"},

    # Combo pieces (should never be boarded out)
    "Goryo's Vengeance": {"combo_piece", "reanimation"},
    "Persist": {"combo_piece", "reanimation"},
    "Unmarked Grave": {"combo_piece", "enabler"},
    "Griselbrand": {"combo_piece", "reanimation_target"},
    "Atraxa, Grand Unifier": {"combo_piece", "reanimation_target"},
    "Unburial Rites": {"combo_piece", "reanimation"},
    "Primeval Titan": {"combo_piece", "threat"},
    "Amulet of Vigor": {"combo_piece", "enabler"},
    "Dryad of the Ilysian Grove": {"combo_piece", "enabler"},
    "Living End": {"combo_piece"},
    "Shardless Agent": {"combo_piece", "cascade"},
    "Violent Outburst": {"combo_piece", "cascade"},
    "Demonic Dread": {"combo_piece", "cascade"},
    "Ardent Plea": {"combo_piece", "cascade"},
    "Wish": {"combo_piece", "tutor"},
    "Past in Flames": {"combo_piece"},
    "Desperate Ritual": {"combo_piece", "mana"},
    "Pyretic Ritual": {"combo_piece", "mana"},
    "Manamorphose": {"combo_piece", "mana"},
    "Ruby Medallion": {"combo_piece", "enabler"},

    # Interaction / disruption
    "Thoughtseize": {"disruption", "discard"},
    "Solitude": {"removal", "creature"},
    "Ephemerate": {"value", "combo_adjacent"},
    "Faithful Mending": {"card_selection", "enabler"},
    "Undying Evil": {"combo_adjacent"},
    "Leyline of Sanctity": {"protection"},
}

# Deck archetype tags for matchup analysis
DECK_TAGS = {
    "Affinity": {"artifact_heavy", "aggro", "creature_based"},
    "Boros Energy": {"midrange", "creature_based", "aggro"},
    "Jeskai Blink": {"midrange", "value", "blue_deck"},
    "Ruby Storm": {"combo", "storm", "spell_based"},
    "Eldrazi Tron": {"ramp", "big_mana", "creature_based"},
    "Amulet Titan": {"ramp", "combo", "creature_based"},
    "Goryo's Vengeance": {"combo", "graveyard_based", "spell_based"},
    "Domain Zoo": {"aggro", "creature_based", "greedy_mana"},
    "Living End": {"combo", "graveyard_based", "cascade"},
    "Izzet Prowess": {"aggro", "spell_based", "blue_deck"},
    "Dimir Midrange": {"midrange", "blue_deck", "discard"},
    "4c Omnath": {"midrange", "value", "greedy_mana", "blue_deck"},
}


def _compute_sideboard_plan(
    my_deck: str,
    opp_deck: str,
    sideboard: Dict[str, int],
    mainboard: Dict[str, int],
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    Decide what to board in and what to board out.
    Returns (cards_in, cards_out) where each is {card_name: count}.
    """
    opp_tags = DECK_TAGS.get(opp_deck, set())
    my_tags = DECK_TAGS.get(my_deck, set())

    # Score each sideboard card for this matchup
    sb_scores: Dict[str, float] = {}
    for card_name, count in sideboard.items():
        tags = CARD_TAGS.get(card_name, set())
        score = 0.0

        # Graveyard hate vs graveyard decks
        if "graveyard_hate" in tags and "graveyard_based" in opp_tags:
            score += 10.0

        # Artifact hate vs artifact decks
        if "artifact_hate" in tags and "artifact_heavy" in opp_tags:
            score += 10.0

        # Sweepers vs creature aggro
        if "sweeper" in tags and "creature_based" in opp_tags and "aggro" in opp_tags:
            score += 8.0

        # Counters vs combo
        if "counter" in tags and "combo" in opp_tags:
            score += 8.0
        if "combo_hate" in tags and "combo" in opp_tags:
            score += 6.0

        # Anti-storm vs storm
        if "anti_storm" in tags and "storm" in opp_tags:
            score += 10.0

        # Blue hate vs blue decks
        if "blue_hate" in tags and "blue_deck" in opp_tags:
            score += 5.0

        # Blood Moon vs greedy mana
        if "anti_greedy_mana" in tags and "greedy_mana" in opp_tags:
            score += 9.0

        # Anti-discard vs discard decks
        if "anti_discard" in tags and "discard" in opp_tags:
            score += 7.0

        # Removal vs creature-based decks
        if "removal" in tags and "creature_based" in opp_tags:
            score += 5.0

        # Anti-control vs control/midrange
        if "anti_control" in tags and ("midrange" in opp_tags or "value" in opp_tags):
            score += 5.0

        # Value cards for grindy matchups
        if "value" in tags and "midrange" in opp_tags:
            score += 4.0

        # Extra threats vs removal-heavy decks
        if "threat" in tags and "midrange" in opp_tags:
            score += 4.0

        # Alt wincons for combo decks
        if "alt_wincon" in tags:
            score += 2.0

        # Flexible removal always has some value
        if "flexible_removal" in tags:
            score += 2.0

        sb_scores[card_name] = score

    # Board in cards with score > 3 (meaningful impact)
    cards_in: Dict[str, int] = {}
    total_in = 0
    for card_name, score in sorted(sb_scores.items(), key=lambda x: -x[1]):
        if score < 3.0:
            continue
        count = sideboard[card_name]
        cards_in[card_name] = count
        total_in += count

    # Need to board out the same number of cards
    # Score mainboard cards for weakness in this matchup
    mb_weakness: Dict[str, float] = {}
    for card_name, count in mainboard.items():
        tags = CARD_TAGS.get(card_name, set())
        weakness = 0.0

        # NEVER board out combo pieces - they are the deck's core strategy
        if "combo_piece" in tags:
            weakness = -100.0
            mb_weakness[card_name] = weakness
            continue

        # Graveyard hate is bad vs non-graveyard decks
        if "graveyard_hate" in tags and "graveyard_based" not in opp_tags:
            weakness += 5.0

        # Artifact hate is bad vs non-artifact decks
        if "artifact_hate" in tags and "artifact_heavy" not in opp_tags:
            weakness += 5.0

        # Slow value cards are bad vs fast combo
        if "value" in tags and "combo" in opp_tags:
            weakness += 3.0

        # Protection cards are weaker vs non-discard decks
        if "protection" in tags and "discard" not in opp_tags:
            weakness += 4.0

        # Combo-adjacent cards (Undying Evil, Ephemerate) are less critical
        # but still better than random cuts
        if "combo_adjacent" in tags:
            weakness += 1.0

        # Disruption (Thoughtseize) is worse vs aggro (too slow)
        if "disruption" in tags and "aggro" in opp_tags:
            weakness += 2.0

        # Generic heuristic: board out the worst cards
        # Use a small base weakness so we have something to cut
        weakness += 0.1

        # Lands should never be boarded out
        if card_name in mainboard:
            card_lower = card_name.lower()
            if any(w in card_lower for w in ["plains", "island", "swamp", "mountain", "forest",
                                              "shrine", "fountain", "grave", "pool", "garden",
                                              "strand", "flats", "mesa", "heath", "clearing",
                                              "courtyard", "triome"]):
                weakness = -50.0

        mb_weakness[card_name] = weakness

    # Board out weakest mainboard cards to match total_in
    cards_out: Dict[str, int] = {}
    total_out = 0
    for card_name, weakness in sorted(mb_weakness.items(), key=lambda x: -x[1]):
        if total_out >= total_in:
            break
        count = mainboard[card_name]
        can_cut = min(count, total_in - total_out)
        if can_cut > 0:
            cards_out[card_name] = can_cut
            total_out += can_cut

    # Ensure in/out counts match
    if total_out > total_in:
        # Trim cards_out
        diff = total_out - total_in
        for card_name in list(cards_out.keys()):
            if diff <= 0:
                break
            trim = min(cards_out[card_name], diff)
            cards_out[card_name] -= trim
            diff -= trim
            if cards_out[card_name] == 0:
                del cards_out[card_name]

    return cards_in, cards_out


def apply_sideboard(
    mainboard: Dict[str, int],
    sideboard: Dict[str, int],
    cards_in: Dict[str, int],
    cards_out: Dict[str, int],
) -> Dict[str, int]:
    """Apply sideboard swaps to a mainboard, returning the new deck list."""
    new_deck = dict(mainboard)

    # Remove cards_out
    for card_name, count in cards_out.items():
        if card_name in new_deck:
            new_deck[card_name] -= count
            if new_deck[card_name] <= 0:
                del new_deck[card_name]

    # Add cards_in
    for card_name, count in cards_in.items():
        new_deck[card_name] = new_deck.get(card_name, 0) + count

    return new_deck


# ═══════════════════════════════════════════════════════════════════
# Bo3 Match Result
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Bo3MatchResult:
    """Result of a best-of-3 match."""
    deck1_name: str
    deck2_name: str
    match_winner: int  # 0 or 1 (player index)
    match_score: Tuple[int, int]  # (deck1_wins, deck2_wins)
    game_results: List[GameResult]  # 2 or 3 GameResult objects
    sideboard_decisions: List[Dict]  # sideboard changes between games
    game_logs: List[List[str]]  # per-game log lines


# ═══════════════════════════════════════════════════════════════════
# Bo3 Match Runner
# ═══════════════════════════════════════════════════════════════════

class Bo3Runner:
    """Runs a best-of-3 match with sideboarding between games."""

    def __init__(self, db: CardDatabase, rng: random.Random):
        self.db = db
        self.rng = rng

    def run_match(
        self,
        deck1_name: str,
        deck1_data: Dict,
        deck2_name: str,
        deck2_data: Dict,
    ) -> Bo3MatchResult:
        """Run a full best-of-3 match with sideboarding."""
        mainboard1 = deck1_data["mainboard"]
        mainboard2 = deck2_data["mainboard"]
        sideboard1 = deck1_data.get("sideboard", {})
        sideboard2 = deck2_data.get("sideboard", {})

        game_results = []
        game_logs = []
        sideboard_decisions = []
        score = [0, 0]

        # ── Game 1: mainboard vs mainboard ──
        runner1 = GameRunner(self.db, random.Random(self.rng.randint(0, 999999)))
        result1 = runner1.run_game(deck1_name, mainboard1, deck2_name, mainboard2, verbose=True)
        game_results.append(result1)
        game_logs.append(list(result1.game_log))

        if result1.winner == 0:
            score[0] += 1
        elif result1.winner == 1:
            score[1] += 1

        # ── Sideboard between Game 1 and Game 2 ──
        # Determine who won G1 to inform sideboard strategy
        g1_winner = result1.winner

        # Both players sideboard
        in1, out1 = _compute_sideboard_plan(deck1_name, deck2_name, sideboard1, mainboard1)
        in2, out2 = _compute_sideboard_plan(deck2_name, deck1_name, sideboard2, mainboard2)

        sb_decision_1 = {
            "between_games": "G1_to_G2",
            "player1": {
                "deck": deck1_name,
                "in": dict(in1),
                "out": dict(out1),
            },
            "player2": {
                "deck": deck2_name,
                "in": dict(in2),
                "out": dict(out2),
            },
        }
        sideboard_decisions.append(sb_decision_1)

        # Apply sideboard
        post_board1 = apply_sideboard(mainboard1, sideboard1, in1, out1)
        post_board2 = apply_sideboard(mainboard2, sideboard2, in2, out2)

        # ── Game 2: post-board ──
        runner2 = GameRunner(self.db, random.Random(self.rng.randint(0, 999999)))
        # Alternate who goes first: loser of G1 goes first in G2
        if g1_winner == 0:
            # P2 lost G1, P2 goes first in G2 (swap order)
            result2 = runner2.run_game(deck2_name, post_board2, deck1_name, post_board1, verbose=True)
            # Swap winner back to original player indices
            if result2.winner == 0:
                result2_winner = 1  # deck2 won
            elif result2.winner == 1:
                result2_winner = 0  # deck1 won
            else:
                result2_winner = -1
        else:
            # P1 lost G1, P1 goes first in G2
            result2 = runner2.run_game(deck1_name, post_board1, deck2_name, post_board2, verbose=True)
            result2_winner = result2.winner

        game_results.append(result2)
        game_logs.append(list(result2.game_log))

        if result2_winner == 0:
            score[0] += 1
        elif result2_winner == 1:
            score[1] += 1

        # ── Check if match is decided (2-0) ──
        if score[0] >= 2 or score[1] >= 2:
            match_winner = 0 if score[0] >= 2 else 1
            return Bo3MatchResult(
                deck1_name=deck1_name,
                deck2_name=deck2_name,
                match_winner=match_winner,
                match_score=tuple(score),
                game_results=game_results,
                sideboard_decisions=sideboard_decisions,
                game_logs=game_logs,
            )

        # ── Sideboard stays the same for Game 3 (same post-board config) ──
        sb_decision_2 = {
            "between_games": "G2_to_G3",
            "player1": {"deck": deck1_name, "in": {}, "out": {}, "note": "Same as G2 configuration"},
            "player2": {"deck": deck2_name, "in": {}, "out": {}, "note": "Same as G2 configuration"},
        }
        sideboard_decisions.append(sb_decision_2)

        # ── Game 3: post-board, higher seed goes first ──
        runner3 = GameRunner(self.db, random.Random(self.rng.randint(0, 999999)))
        # In Game 3, original P1 goes first (coin flip equivalent)
        result3 = runner3.run_game(deck1_name, post_board1, deck2_name, post_board2, verbose=True)
        game_results.append(result3)
        game_logs.append(list(result3.game_log))

        if result3.winner == 0:
            score[0] += 1
        elif result3.winner == 1:
            score[1] += 1

        match_winner = 0 if score[0] >= 2 else 1
        return Bo3MatchResult(
            deck1_name=deck1_name,
            deck2_name=deck2_name,
            match_winner=match_winner,
            match_score=tuple(score),
            game_results=game_results,
            sideboard_decisions=sideboard_decisions,
            game_logs=game_logs,
        )
