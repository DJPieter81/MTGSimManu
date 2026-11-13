"""How many cards a spell's resolution draws, read from its typed instructions.

The draw-tax projection (`ai.ev_evaluator._projected_real_draws`) prices each
card a spell draws at what the opponent's draw-triggered abilities take
(`ai.bhi.opp_static_damage_per_card_event`). The count comes from the
grammar's typed spell (`CardTemplate.effects`), never from oracle text:

- a DRAW instruction's printed count (no count: one card);
- "draw that many" bound to an earlier instruction of the same spell
  (`Ref(RESULT, k)`) that moves every card of its controller's hand ("exile
  all the cards from your hand, then draw that many"): the hand it moves,
  the spell being cast not counted (it is on the stack by then);
- an instruction that replaces another, or one of several alternatives,
  counts as the largest of them, never their sum.

None means "a shape this does not count"; the caller falls back.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


def _moves_whole_hand(spec: Any) -> bool:
    """An instruction that moves every card of its controller's hand: a
    hand filter of the controller's own cards with no count."""
    f = getattr(spec, "filter", None)
    return (f is not None and getattr(f, "zone", None) == "hand"
            and getattr(f, "owner", None) == "you"
            and getattr(spec, "amount", None) is None
            and getattr(spec, "target_slot", None) is None)


def _count(spec: Any, by_seq: dict, card: Any, hand: Iterable[Any]) -> Optional[int]:
    from engine.effect_spec import AmountKind, RefKind
    a = spec.amount
    if a is None:
        return 1
    if a.kind is AmountKind.LITERAL and a.quantity is None and a.ref is None:
        return a.n
    if a.kind is AmountKind.THAT_MUCH and a.ref is not None \
            and a.ref.kind is RefKind.RESULT and a.ref.index in by_seq \
            and _moves_whole_hand(by_seq[a.ref.index]):
        return len([c for c in hand if c is not card])
    return None


def projected_draws(card: Any, hand: Iterable[Any]) -> Optional[int]:
    """The cards `card`'s spell draws when it resolves now, from the hand
    `hand` it is cast out of; None for a shape this does not count."""
    from engine.effect_spec import Verb, iter_specs
    effects = getattr(card.template, "effects", None)
    host = effects.spell(0) if effects is not None else None
    if host is None:
        return None
    specs = list(iter_specs(host.specs))
    draws = [s for s in specs if s.verb is Verb.DRAW]
    if not draws:
        return None
    by_seq = {s.seq: s for s in specs}
    hand = list(hand)
    counts = []
    for s in draws:
        n = _count(s, by_seq, card, hand)
        if n is None:
            return None
        counts.append(n)
    exclusive = any(s.replaces or s.alternatives for s in specs)
    return max(counts) if exclusive else sum(counts)
