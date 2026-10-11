"""The AI's answers to the choices a resolving ability asks of its
controller (design doc 2026-09-29, A35): whether to perform an optional
("you may") effect, and which cards to take out of a pool the engine has
already narrowed to the legal ones.

The engine enumerates and never scores (`engine/effect_executors`); these
functions decide, on the existing valuation primitives, from the typed spec
alone -- no oracle text, no card names. `engine.game_runner.AICallbacks`
routes `choose_optional_effect` and `choose_cards` here.
"""
from __future__ import annotations

from typing import Any, List, Sequence


def _to_own_hand(spec: Any) -> bool:
    """Does the spec put the controller's own cards into its hand ("return
    target card from your graveyard to your hand"; CR 400.3: a card goes to
    its owner's hand)?"""
    dest = getattr(spec, "dest", None)
    req = getattr(spec, "target", None)
    return (getattr(dest, "zone", None) == "hand" and req is not None
            and getattr(req, "owner_scope", None) == "you")


def perform_optional_effect(game: Any, ctx: Any, spec: Any) -> bool:
    """"You may <effect>": perform it. Every optional effect the dispatcher
    executes today moves the controller's own cards into its hand, which
    only adds options, and the pick that follows (`pick_cards`) may take
    none of an optional target's cards -- so whether anything comes back is
    the pick's decision. A family that makes a costly or harmful optional
    effect executable adds its valuation here."""
    return True


def pick_trigger_targets(game: Any, player_idx: int, source: Any, spec: Any,
                         req: Any, players: Sequence[int],
                         permanents: Sequence[Any]) -> List[Any]:
    """The targets a triggered ability takes for one slot as it is put on
    the stack (CR 603.3d), out of the legal choices the engine narrowed.
    A damage spec's recipient is the AI's damage aim
    (`ai.damage_targets.choose_damage_recipient`: the one owner of where N
    damage goes -- lethal to the face, else the opposing permanent it
    destroys that is worth most, else the face); any other spec, or an aim
    the slot does not admit, keeps the engine's default pick."""
    from engine.callbacks import default_trigger_targets
    from engine.effect_spec import Verb
    if getattr(spec, "verb", None) is Verb.DAMAGE:
        from ai.damage_targets import choose_damage_recipient
        from engine.effect_conditions import amount_value
        amount = amount_value(game, player_idx, spec.amount, 0)
        aim = choose_damage_recipient(game, player_idx, source, amount, req,
                                      mana_committed=0)
        if aim is not None:
            if aim.permanent is None:
                face = [p for p in players if p != player_idx]
                if face:
                    return face[:1]
            elif any(aim.permanent is c for c in permanents):
                return [aim.permanent]
    return default_trigger_targets(player_idx, req, players, permanents)


def _deliver(game: Any, player_idx: int, cards: Sequence[Any],
             k: int) -> List[Any]:
    """Up to `k` of `cards`, one at a time by the AI's hand-delivery choice
    (`ai.activation_ev.choose_tutor_delivery`: a lethal or engine-completing
    piece first, then threat value for a creature and mana investment
    otherwise). No tutor is being spent, so it is asked with no source."""
    from ai.activation_ev import choose_tutor_delivery
    remaining = list(cards)
    picked: List[Any] = []
    while remaining and len(picked) < k:
        card = choose_tutor_delivery(game, player_idx, remaining, source=None)
        if card is None:
            break
        picked.append(card)
        remaining = [c for c in remaining if c is not card]
    return picked


def pick_cards(game: Any, ctx: Any, spec: Any, pool: Sequence[Any],
               n: int) -> List[Any]:
    """Which cards of `pool` a resolving effect takes, up to `n`.

    For the controller's own cards going to its hand: a card that still
    serves its plan from the graveyard (`discard_advisor.
    serves_plan_from_graveyard`: the reanimation resource, a self-recurring
    spell) stays there; the others come by the hand-delivery choice
    (`_deliver`). A staying card is taken only to fill a target the effect
    requires (`count_min`). Any other destination keeps the engine's
    default pick."""
    from engine.callbacks import default_card_pick
    if not _to_own_hand(spec):
        return default_card_pick(pool, n)
    from ai.discard_advisor import serves_plan_from_graveyard
    player_idx = ctx.controller
    stays = [c for c in pool
             if serves_plan_from_graveyard(game, player_idx, c)]
    comes = [c for c in pool if all(c is not s for s in stays)]
    picked = _deliver(game, player_idx, comes, n)
    need = min(spec.target.count_min, n)
    if len(picked) < need:
        picked += _deliver(game, player_idx, stays, need - len(picked))
    return picked
