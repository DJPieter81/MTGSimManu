"""Hand-denial valuation — the EV of a caster-chosen forced discard.

The strategic half of the targeted hand-attack class (the parser is
``engine/oracle_parser.py::parse_hand_attack``; resolution and the
revealed-hand restriction filter live in ``engine/oracle_resolver.py``).
Mirror of ``ai/land_denial.py``: the cast-time projection sees a forced
discard as a card-neutral trade (the caster's card for one average
opponent card), so a Thoughtseize-shaped spell is held for turns while
the opponent deploys the very threats it would have taken.  But the
caster does not take an average card — they take the BEST eligible card
in the hand.  The value of that choice is derived here, in the clock-
delta units the rest of ``ai/ev_player`` scoring uses, with zero
underived constants:

* **Observable pool.**  The opponent's hand is hidden, but its
  composition follows their pool — hand ∪ library, the same public-
  decklist premise ``ai.bhi.HandInferenceTracker.initialize_from_game``
  and ``ai.land_denial`` rest on.  The spell's own choose clause
  ("nonland", "with mana value 3 or less", "noncreature, nonland") is
  applied through the engine's revealed-hand filter, so the cards this
  valuation may count are exactly the cards the resolution may take.

* **Ranking.**  Eligible pool cards are ordered by
  ``ai.ev_evaluator.score_card_for_opponent_strip`` — the ranking the
  resolution itself uses to pick the strip — so the cast-time
  expectation and the resolved choice agree.

* **Order statistic.**  With an unknown hand of size H drawn from N pool
  cards, the k-th ranked eligible card is the strip exactly when no
  higher-ranked eligible card sits in the hand:
  P(k) = C(N−k, H)/C(N, H) − C(N−k−1, H)/C(N, H).  Exact hypergeometric;
  the probabilities sum to P(at least one eligible card in hand).

* **Denied value.**  A creature is valued by ``creature_threat_value``
  (the removal-priority scorer — the strip is removal that lands before
  the creature does); any other card by ``card_clock_impact`` (the clock
  change one average card buys).  The overlay retracts the projection's
  average-card credit, so the spell's net card credit is exactly the
  expected value of the best card it takes; an empty opponent hand is
  worth nothing.

Victim-chosen ("target player discards a card") and random forms carry
no selection premium — the victim gives up their worst card — and keep
the projection's average-card credit.
"""
from __future__ import annotations

from math import comb
from typing import TYPE_CHECKING, List, Optional, Tuple

from ai.clock import card_clock_impact

if TYPE_CHECKING:
    from engine.cards import CardInstance, CardTemplate
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot


def strip_rank_probabilities(n_pool: int, hand_size: int,
                             n_eligible: int) -> List[float]:
    """P(the k-th ranked eligible card is the best eligible card in an
    unknown hand of ``hand_size`` drawn uniformly from ``n_pool`` cards),
    for k = 0 .. n_eligible-1.

    Exact hypergeometric order statistic: P(none of the top k eligible
    cards is in the hand) = C(n_pool − k, hand_size) / C(n_pool,
    hand_size); the k-th card is the strip when the top k are absent and
    the top k+1 are not.  ``math.comb`` returns 0 when the pool left is
    smaller than the hand, which is the correct boundary.
    """
    if n_eligible <= 0:
        return []
    if hand_size <= 0 or n_pool <= 0 or hand_size > n_pool:
        return [0.0] * n_eligible
    total = comb(n_pool, hand_size)

    def none_of_top(k: int) -> float:
        return comb(n_pool - k, hand_size) / total

    return [none_of_top(k) - none_of_top(k + 1) for k in range(n_eligible)]


def _victim_gameplan(game: "GameState", victim_idx: int):
    """The victim's published gameplan (keystone lists feed the strip
    ranking), or None — the same lookup ``ai.discard_advisor`` makes."""
    deck_name = getattr(game.players[victim_idx], 'deck_name', '') or ''
    if not deck_name:
        return None
    try:
        from ai.gameplan import get_gameplan
        return get_gameplan(deck_name)
    except Exception:
        return None


def _eligible_pool(game: "GameState", victim_idx: int,
                   choose_clause: Optional[str]
                   ) -> Tuple[List["CardInstance"], int, int]:
    """(eligible cards, pool size, hand size) for the victim.  The pool is
    hand ∪ library; eligibility is the spell's own choose clause applied
    through the engine's revealed-hand filter."""
    from engine.oracle_resolver import _targeted_discard_candidates
    victim = game.players[victim_idx]
    pool = list(victim.hand) + list(victim.library)
    eligible = _targeted_discard_candidates(pool, choose_clause or '')
    return eligible, len(pool), len(victim.hand)


def _ranked(eligible: List["CardInstance"], snap: "EVSnapshot",
            victim_gameplan) -> List["CardInstance"]:
    """Eligible cards in the order the resolution would take them:
    highest strip score, then highest printed mana value, then stable."""
    from ai.ev_evaluator import score_card_for_opponent_strip
    scored = [
        (score_card_for_opponent_strip(c, snap, victim_gameplan),
         getattr(c.template, 'cmc', 0) or 0, idx, c)
        for idx, c in enumerate(eligible)
    ]
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [c for _score, _cmc, _idx, c in scored]


def _denied_value(card: "CardInstance", snap: "EVSnapshot") -> float:
    """What the victim forfeits by losing this card: the removal-priority
    value for a creature, the average card's clock impact otherwise."""
    from ai.ev_evaluator import creature_threat_value
    if getattr(card.template, 'is_creature', False):
        return float(creature_threat_value(card, snap))
    return card_clock_impact(snap)


def hand_denial_value(tmpl: "CardTemplate", game: "GameState",
                      caster_idx: int, snap: "EVSnapshot") -> float:
    """EV overlay for resolving a caster-chosen hand attack: the expected
    denied value of the best eligible card in the victim's hidden hand,
    minus the average-card credit the projection already gives.  Zero for
    non-caster-chosen forms and for an empty victim hand."""
    data = getattr(tmpl, 'hand_attack_data', None) or {}
    if data.get('chooser') != 'caster':
        return 0.0
    victim_idx = 1 - caster_idx
    eligible, n_pool, hand_size = _eligible_pool(
        game, victim_idx, data.get('choose_clause'))
    if hand_size <= 0:
        return 0.0
    ranked = _ranked(eligible, snap, _victim_gameplan(game, victim_idx))
    probs = strip_rank_probabilities(n_pool, hand_size, len(ranked))
    expected_best = sum(p * _denied_value(c, snap)
                        for p, c in zip(probs, ranked))
    return expected_best - card_clock_impact(snap)
