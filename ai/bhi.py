"""Bayesian Hand Inference (BHI) — track opponent hand probabilities.

Updates beliefs about what the opponent holds based on observed actions:
- Priority passes (didn't counter/remove when they could have)
- Spells cast (reveals card type, reduces hand size)
- Mana availability (can't cast spells they can't afford)
- Cards drawn (increases hand uncertainty)

Core formula:
    P(H|E) = P(E|H) · P(H) / P(E)

Where:
    H = "opponent has card category X in hand"
    E = observed event (pass, cast, draw)

No hardcoded card names — all inference from tags, keywords, and mana.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.game_state import GameState, PlayerState


@dataclass
class HandBeliefs:
    """Probability beliefs about opponent's hand composition.

    Each field is P(at least one card of this type in hand).
    Updated via Bayesian inference as events are observed.
    """
    # Core interaction probabilities
    p_counter: float = 0.0      # P(has a counterspell)
    p_removal: float = 0.0      # P(has instant-speed removal)
    p_exile_removal: float = 0.0  # P(has exile-based removal)
    p_burn: float = 0.0         # P(has burn spell)
    p_combat_trick: float = 0.0 # P(has pump/protection instant)

    # Tracking
    observations: int = 0       # number of Bayesian updates applied
    last_hand_size: int = 0     # opponent hand size at last update


class BayesianHandTracker:
    """Tracks and updates beliefs about opponent's hand.

    Initialized from deck composition (prior), then updated
    as the game progresses based on observed evidence.
    """

    def __init__(self, player_idx: int):
        """Track beliefs about the opponent of player_idx."""
        self.player_idx = player_idx
        self.opponent_idx = 1 - player_idx
        self.beliefs = HandBeliefs()
        self._initialized = False

    def initialize_from_game(self, game: "GameState"):
        """Set prior probabilities from opponent's deck composition."""
        opp = game.players[self.opponent_idx]
        hand_size = len(opp.hand)
        total_cards = len(opp.library) + hand_size

        if total_cards == 0:
            return

        # Count card categories in opponent's total pool (library + hand)
        all_cards = list(opp.library) + list(opp.hand)
        counters = sum(1 for c in all_cards
                       if 'counterspell' in getattr(c.template, 'tags', set()))
        removal = sum(1 for c in all_cards
                      if 'removal' in getattr(c.template, 'tags', set())
                      and c.template.is_instant)
        exile = sum(1 for c in all_cards
                    if 'removal' in getattr(c.template, 'tags', set())
                    and 'exile' in (c.template.oracle_text or '').lower())
        burn = sum(1 for c in all_cards
                   if 'burn' in getattr(c.template, 'tags', set())
                   or ('damage' in (c.template.oracle_text or '').lower()
                       and c.template.is_instant))
        tricks = sum(1 for c in all_cards
                     if c.template.is_instant
                     and ('pump' in getattr(c.template, 'tags', set())
                          or 'protection' in (c.template.oracle_text or '').lower()))

        # Prior: P(has X) = 1 - P(none of X in hand)
        # P(none) = C(non_X, hand_size) / C(total, hand_size)
        # Approximated as: 1 - ((total - count) / total) ^ hand_size
        def prior(count):
            if count == 0 or hand_size == 0:
                return 0.0
            density = count / total_cards
            return 1.0 - (1.0 - density) ** hand_size

        self.beliefs.p_counter = prior(counters)
        self.beliefs.p_removal = prior(removal)
        self.beliefs.p_exile_removal = prior(exile)
        self.beliefs.p_burn = prior(burn)
        self.beliefs.p_combat_trick = prior(tricks)
        self.beliefs.last_hand_size = hand_size
        self._initialized = True

    def observe_priority_pass(self, game: "GameState",
                               spell_on_stack: bool = False,
                               spell_is_creature: bool = False,
                               opp_mana_available: int = 0):
        """Update beliefs when opponent passes priority.

        Key insight: if opponent COULD have countered/removed but didn't,
        either they don't have it, or they're saving it for something bigger.

        P(has_counter | passed) = P(passed | has_counter) · P(has_counter) / P(passed)

        P(passed | has_counter) = hold_probability (they might save it)
        P(passed | no_counter) = 1.0 (they had no choice)
        """
        if not self._initialized:
            self.initialize_from_game(game)

        # Only meaningful if opponent had mana to respond
        if opp_mana_available < 1:
            return

        # How likely are they to hold interaction even if they have it?
        # Depends on what's on the stack and game state.
        # High-value spells get countered; low-value spells get passed on.
        opp = game.players[self.opponent_idx]
        my_life = game.players[self.player_idx].life
        opp_life = opp.life

        if spell_on_stack and opp_mana_available >= 2:
            # Could have countered — update P(counter)
            # P(hold | has_counter): rational player holds counter ~30% of the time
            # for a bigger threat. Higher hold rate early game, lower when threatened.
            hold_rate = 0.3
            if opp_life <= 10:
                hold_rate = 0.15  # desperate, would counter almost anything
            elif game.turn_number <= 4:
                hold_rate = 0.4  # early game, saving for better target

            self.beliefs.p_counter = _bayesian_update(
                self.beliefs.p_counter, hold_rate, 1.0)

        if spell_is_creature and opp_mana_available >= 1:
            # Could have used instant removal — update P(removal)
            hold_rate = 0.25  # might save removal for bigger creature
            if opp_life <= 8:
                hold_rate = 0.1

            self.beliefs.p_removal = _bayesian_update(
                self.beliefs.p_removal, hold_rate, 1.0)
            self.beliefs.p_exile_removal = _bayesian_update(
                self.beliefs.p_exile_removal, hold_rate, 1.0)

        self.beliefs.observations += 1

    def observe_spell_cast(self, game: "GameState", card_tags: set):
        """Update beliefs when opponent casts a spell.

        Casting a non-counter spell is weak evidence they DON'T have
        a counter (they chose to tap mana for something else).
        Casting a counter/removal CONFIRMS they had one — but now it's used.
        """
        if not self._initialized:
            self.initialize_from_game(game)

        opp = game.players[self.opponent_idx]

        if 'counterspell' in card_tags:
            # They used a counter — recalculate from remaining deck
            self._recalculate_priors(game)
        elif 'removal' in card_tags:
            self._recalculate_priors(game)
        else:
            # Cast a non-interaction spell → slight evidence against holding
            # interaction (they chose to tap mana for something else)
            self.beliefs.p_counter *= 0.9  # slight reduction
            self.beliefs.p_removal *= 0.9

        self.beliefs.observations += 1

    def observe_card_drawn(self, game: "GameState"):
        """Update beliefs when opponent draws a card.

        Drawing increases hand size → increases probability of holding
        any given card type. Recalculate from deck composition.
        """
        if not self._initialized:
            self.initialize_from_game(game)
        self._recalculate_priors(game)

    def get_counter_probability(self) -> float:
        """Current belief: P(opponent has a counterspell)."""
        return self.beliefs.p_counter

    def get_removal_probability(self) -> float:
        """Current belief: P(opponent has instant removal)."""
        return self.beliefs.p_removal

    def get_exile_removal_probability(self) -> float:
        """Current belief: P(opponent has exile-based removal)."""
        return self.beliefs.p_exile_removal

    def _recalculate_priors(self, game: "GameState"):
        """Recalculate from current deck state after a card is revealed/used."""
        opp = game.players[self.opponent_idx]
        hand_size = len(opp.hand)
        remaining = len(opp.library)
        total = remaining + hand_size

        if total == 0:
            return

        # Count remaining interaction in library (hand is hidden)
        lib_counters = sum(1 for c in opp.library
                           if 'counterspell' in getattr(c.template, 'tags', set()))
        lib_removal = sum(1 for c in opp.library
                          if 'removal' in getattr(c.template, 'tags', set())
                          and c.template.is_instant)
        lib_exile = sum(1 for c in opp.library
                        if 'removal' in getattr(c.template, 'tags', set())
                        and 'exile' in (c.template.oracle_text or '').lower())

        # Estimate cards in hand from library density
        # (we don't know the hand, but library composition is ground truth)
        def updated_prior(lib_count):
            if lib_count == 0 or hand_size == 0 or remaining == 0:
                return 0.0
            density = lib_count / remaining
            return 1.0 - (1.0 - density) ** hand_size

        # Blend: keep some of old belief (from observed passes), mix with new prior
        # More observations = trust observations more
        obs_weight = min(0.7, self.beliefs.observations * 0.1)
        prior_weight = 1.0 - obs_weight

        new_counter = updated_prior(lib_counters)
        new_removal = updated_prior(lib_removal)
        new_exile = updated_prior(lib_exile)

        self.beliefs.p_counter = obs_weight * self.beliefs.p_counter + prior_weight * new_counter
        self.beliefs.p_removal = obs_weight * self.beliefs.p_removal + prior_weight * new_removal
        self.beliefs.p_exile_removal = obs_weight * self.beliefs.p_exile_removal + prior_weight * new_exile
        self.beliefs.last_hand_size = hand_size


def _bayesian_update(prior: float, p_evidence_if_true: float,
                     p_evidence_if_false: float) -> float:
    """Apply Bayes' rule: P(H|E) = P(E|H)·P(H) / P(E).

    prior: P(H) — current belief
    p_evidence_if_true: P(E|H) — probability of seeing this evidence if H is true
    p_evidence_if_false: P(E|¬H) — probability of seeing this evidence if H is false
    """
    if prior <= 0:
        return 0.0
    if prior >= 1:
        return 1.0

    p_evidence = p_evidence_if_true * prior + p_evidence_if_false * (1 - prior)
    if p_evidence <= 0:
        return prior  # no information

    posterior = p_evidence_if_true * prior / p_evidence
    return max(0.0, min(1.0, posterior))
