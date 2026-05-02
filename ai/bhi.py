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
    p_free_counter: float = 0.0 # P(has pitch/free counterspell)
    # Hand-disruption prior - populated from opp's published gameplan.
    # When the opponent's gameplan declares a discard spell in
    # mulligan_keys / critical_pieces / always_early (oracle text
    # contains "target player ... discards"), we set p_discard to a
    # documented prior reflecting "the opp is planning to deploy
    # hand disruption". No Bayesian update yet - flat prior only.
    p_discard: float = 0.0      # P(opp plans hand disruption)
    # Density of artifact threats in the opponent's pool. Drives the
    # held-interaction value scaler in `_holdback_penalty` against
    # artifact-equipment archetypes (Affinity-class) where the held
    # counter is the only stack-side answer to equipment-grade
    # threats. Density-based prior over the live pool (library +
    # hand + battlefield), recomputed alongside the other priors.
    p_artifact_threat: float = 0.0

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
        # Free/pitch counters: evoke_pitch counterspells or "without paying" in oracle
        free_counters = sum(1 for c in all_cards
                            if 'counterspell' in getattr(c.template, 'tags', set())
                            and ('evoke_pitch' in getattr(c.template, 'tags', set())
                                 or 'rather than pay' in (c.template.oracle_text or '').lower()
                                 or 'without paying' in (c.template.oracle_text or '').lower()))
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
        # Artifact threat density — every non-land artifact in the
        # pool contributes. Includes equipment, mana rocks, and
        # artifact creatures: each one is a card the held
        # counter may need to answer when it enters the stack.
        from engine.cards import CardType
        artifacts = sum(1 for c in all_cards
                        if CardType.ARTIFACT in c.template.card_types
                        and not c.template.is_land)

        # Prior: P(has X) = 1 - P(none of X in hand)
        # P(none) = C(non_X, hand_size) / C(total, hand_size)
        # Approximated as: 1 - ((total - count) / total) ^ hand_size
        def prior(count):
            if count == 0 or hand_size == 0:
                return 0.0
            density = count / total_cards
            return 1.0 - (1.0 - density) ** hand_size

        self.beliefs.p_counter = prior(counters)
        self.beliefs.p_free_counter = prior(free_counters)
        self.beliefs.p_removal = prior(removal)
        self.beliefs.p_exile_removal = prior(exile)
        self.beliefs.p_burn = prior(burn)
        self.beliefs.p_combat_trick = prior(tricks)
        self.beliefs.p_artifact_threat = prior(artifacts)
        # -- Discard prior from opponent's gameplan --
        # Look up the opp's published gameplan and check whether any
        # card in its mulligan_keys / critical_pieces / always_early
        # has oracle text matching the canonical discard pattern
        # ("target player ... discards"). If yes, plant a flat prior;
        # otherwise leave at 0.0. No card names hardcoded - the
        # detection is gameplan + oracle driven.
        self.beliefs.p_discard = self._compute_discard_prior(opp, all_cards)
        self.beliefs.last_hand_size = hand_size
        self._initialized = True

    # Documented Bayesian prior. When the opponent's published
    # gameplan declares a discard spell as a mulligan key /
    # critical piece / always-early card, we estimate a 50%
    # chance they are planning to deploy it before our combo turn.
    # No observational evidence has been incorporated yet - this is
    # the flat prior used at game start. Sourced from the standard
    # "noninformative for present-or-absent" 0.5 prior.
    _DISCARD_PRIOR = 0.5

    def _compute_discard_prior(self, opp, all_cards) -> float:
        """Return the discard prior for the opponent.

        Detection rule (no card names):
          1. Look up `get_gameplan(opp.deck_name)`.
          2. Build the union of `mulligan_keys`, `critical_pieces`,
             and `always_early` - these are the cards the opp's own
             gameplan says it leans on.
          3. For each name, find the matching template in opp's
             current pool (library or hand) and check whether its
             oracle text matches the discard pattern
             "target player ... discards".
          4. If any match, return _DISCARD_PRIOR; else 0.0.
        """
        deck_name = getattr(opp, 'deck_name', '') or ''
        if not deck_name:
            return 0.0
        try:
            from ai.gameplan import get_gameplan
            plan = get_gameplan(deck_name)
        except Exception:
            return 0.0
        if plan is None:
            return 0.0

        signal_names = (set(getattr(plan, 'mulligan_keys', set()))
                        | set(getattr(plan, 'critical_pieces', set()))
                        | set(getattr(plan, 'always_early', set())))
        if not signal_names:
            return 0.0

        # Build a name -> template lookup from the opp's pool. Using
        # the live pool (library or hand) keeps this consistent with
        # the rest of initialize_from_game; no extra DB lookups.
        templates_by_name = {}
        for c in all_cards:
            t = getattr(c, 'template', None)
            if t is None:
                continue
            n = getattr(t, 'name', None) or getattr(c, 'name', None)
            if n and n not in templates_by_name:
                templates_by_name[n] = t

        for name in signal_names:
            t = templates_by_name.get(name)
            if t is None:
                continue
            oracle = (getattr(t, 'oracle_text', '') or '').lower()
            # Canonical discard pattern: "target player ... discards"
            # (covers Thoughtseize, Inquisition of Kozilek, and any
            # similar effect added later - purely oracle-driven).
            if 'target player' in oracle and 'discards' in oracle:
                return self._DISCARD_PRIOR

        return 0.0

    def get_discard_probability(self) -> float:
        """Current belief: P(opponent is planning to deploy
        hand-disruption). Flat prior only (no Bayesian update yet)."""
        return self.beliefs.p_discard

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

    def get_artifact_threat_probability(self) -> float:
        """Current belief: density of artifact threats in opp's pool.
        Used by `_holdback_penalty` to scale the per-CMC value of held
        interaction against artifact-heavy archetypes."""
        return self.beliefs.p_artifact_threat

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
        from engine.cards import CardType
        lib_artifacts = sum(1 for c in opp.library
                            if CardType.ARTIFACT in c.template.card_types
                            and not c.template.is_land)

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
        new_artifact = updated_prior(lib_artifacts)

        self.beliefs.p_counter = obs_weight * self.beliefs.p_counter + prior_weight * new_counter
        self.beliefs.p_removal = obs_weight * self.beliefs.p_removal + prior_weight * new_removal
        self.beliefs.p_exile_removal = obs_weight * self.beliefs.p_exile_removal + prior_weight * new_exile
        # No observed signal for artifact density; flat prior update.
        self.beliefs.p_artifact_threat = new_artifact
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
