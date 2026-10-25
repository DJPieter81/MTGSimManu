"""The AI's answers to the choices a resolving ability asks of its
controller (design doc 2026-09-29, A35): whether to perform an optional
("you may") effect, and which cards to take out of a pool the engine has
already narrowed to the legal ones.

The engine enumerates and never scores (`engine/effect_executors`); these
functions decide, on the existing valuation primitives, from the typed spec
alone -- no oracle text, no card names. `engine.game_runner.AICallbacks`
routes `choose_optional_effect`, `choose_cards` and `choose_cost_exile`
here.
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


def _literal_count(amount: Any) -> int:
    """The printed count of a LITERAL amount; an absent amount is one."""
    from engine.effect_spec import AmountKind
    if amount is None:
        return 1
    return amount.n if amount.kind is AmountKind.LITERAL else 0


def perform_optional_effect(game: Any, ctx: Any, spec: Any) -> bool:
    """"You may <effect>": perform it, except a discard that does not give
    back what it costs.

    - The optional effects that move the controller's own cards into its
      hand only add options. The pick that follows (`pick_cards`) may take
      none of an optional target's cards, so whether anything comes back
      is the pick's decision.
    - A discard costs its cards (CR 701.9a); its "if you do" branch is
      what it buys. A branch that draws more cards than the discard costs
      is performed, one that draws fewer is not. A rummage -- as many
      drawn as discarded -- trades the cards the discard owner ranks least
      needed (`callbacks.choose_discard`) for random draws, and is
      performed when a draw more likely improves on them than not
      (`ai.discard_advisor.draw_improves_on_discard`).

    A family that makes another costly optional effect executable adds its
    valuation here."""
    from engine.effect_spec import Verb
    if getattr(spec, "verb", None) is Verb.DISCARD:
        drawn = sum(_literal_count(t.amount) for t in spec.then
                    if t.verb is Verb.DRAW and "if_you_do" in t.flags)
        discarded = _literal_count(spec.amount)
        if drawn != discarded:
            return drawn > discarded
        from ai.discard_advisor import draw_improves_on_discard
        return draw_improves_on_discard(game, ctx.controller, discarded)
    return True


def pick_trigger_targets(game: Any, player_idx: int, source: Any, spec: Any,
                         req: Any, players: Sequence[int],
                         permanents: Sequence[Any],
                         x_value: int = 0) -> List[Any]:
    """The targets a triggered ability takes for one slot as it is put on
    the stack (CR 603.3d), or a chosen mode's player as its spell is cast
    (CR 601.2c), out of the legal choices the engine narrowed.
    - A damage spec's recipient is the AI's damage aim
      (`ai.damage_targets.choose_damage_recipient`: the one owner of where
      N damage goes -- lethal to the face, else the opposing permanent it
      destroys that is worth most, else the face).
    - A slot only a player fills, for an instruction that names its acting
      player, is the player it is worth most to name
      (`ai.modal.named_player_value`: tokens to the caster's side, a
      sacrifice to the opponent's, a mill to the graveyard it fuels); a
      tie keeps the default.
    - A characteristic change that only helps its recipient goes on the
      controller's own creature (`ai.pump_target.own_pump_target`: an
      attacking one first, else the one worth most), as a cast pump does.
    Any other spec, or an aim the slot does not admit, keeps the engine's
    default pick."""
    from engine.callbacks import default_trigger_targets
    from engine.effect_spec import Verb
    if players and not permanents and spec is not None \
            and spec.verb is not Verb.DAMAGE:
        from ai.modal import named_player_value
        values = {p: named_player_value(game, player_idx, spec, p, x_value)
                  for p in players}
        if all(v is not None for v in values.values()):
            best = max(values.values())
            top = [p for p in players if values[p] == best]
            if len(top) < len(players):
                return top[:1]
    if getattr(spec, "verb", None) is Verb.EXILE and permanents \
            and not players and getattr(req, "zone", None) == "graveyard":
        # An exile of a target card from a graveyard aims at the cards whose
        # removal is worth most to their owners -- the one owner of that
        # choice, as for a resolving effect's graveyard exile (`pick_cards`).
        from ai.graveyard_removal import pick_removals
        n = max(1, int(getattr(req, "count_max", 1) or 1))
        return pick_removals(game, player_idx, list(permanents), n,
                             int(getattr(req, "count_min", 0) or 0))
    if getattr(spec, "verb", None) in (Verb.EXILE, Verb.DESTROY) \
            and permanents and not players:
        # A removal of a target permanent aims at the opposing permanent
        # whose removal swings its controller's position most
        # (`ai.permanent_threat`), never one whose ward would counter the
        # ability (`ai.ward_targeting.ward_rules_out_target`; the trigger
        # commits no more mana); "up to" a count takes none rather than
        # one of the caster's own, and a required one the caster's least.
        from ai.permanent_threat import permanent_threat
        from ai.ward_targeting import ward_rules_out_target

        def threat(c):
            return permanent_threat(c, game.players[c.controller], game)
        n = max(1, int(getattr(req, "count_max", 1) or 1))
        opposing = [c for c in permanents if c.controller != player_idx
                    and not ward_rules_out_target(game, player_idx, source,
                                                  c, mana_committed=0)]
        if opposing:
            return sorted(opposing, key=threat, reverse=True)[:n]
        need = int(getattr(req, "count_min", 0) or 0)
        return sorted(permanents, key=threat)[:need]
    if getattr(spec, "verb", None) is Verb.CONTINUOUS and permanents:
        from ai.pump_target import beneficial_change, own_pump_target
        if beneficial_change(spec):
            best = own_pump_target(game, player_idx, permanents)
            if best is not None:
                return [best]
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


def pick_cost_exile(game: Any, player_idx: int, source: Any,
                    pool: Sequence[Any], n: int) -> List[Any]:
    """Which `n` cards of the payer's graveyard pay an exile cost (an
    activation's, a spell's additional cost, an escape's other cards): the
    ones that serve it least where they lie. A card that is a resource
    there by itself (`ai.predicates.is_graveyard_resource_itself`:
    castable from the graveyard, a creature its deck can reanimate) goes
    last; the others go first, in graveyard order. The payer's consumers
    are read from every zone it holds."""
    from ai.predicates import (is_graveyard_resource_itself,
                               own_graveyard_consumers)
    _size_matters, recursion = own_graveyard_consumers(game, player_idx)
    order = sorted(range(len(pool)), key=lambda i: (
        is_graveyard_resource_itself(pool[i].template, recursion), i))
    return [pool[i] for i in order[:n]]


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
    requires (`count_min`). For an exile of graveyard cards: the cards
    whose removal is worth most to their owners (`ai.graveyard_removal.
    pick_removals`). Any other destination keeps the engine's default
    pick."""
    from engine.callbacks import default_card_pick
    from engine.effect_spec import Verb
    target = getattr(spec, "target", None)
    if getattr(spec, "verb", None) is Verb.EXILE and target is not None \
            and target.zone == "graveyard":
        from ai.graveyard_removal import pick_removals
        return pick_removals(game, ctx.controller, list(pool), n,
                             target.count_min)
    from engine.effect_executors import (land_from_hand, library_search,
                                         reveal_supported)
    if reveal_supported(spec):
        # A card brought from outside the game into the hand (unit OG):
        # the hand-delivery choice, as a tutor's (`_deliver`).
        return _deliver(game, ctx.controller, list(pool), n)
    if library_search(spec):
        # A library search (unit TU): the lands a land search finds by the
        # land choice below, one at a time with the picks already made;
        # any other card by the tutor's delivery choice (`_deliver`).
        if not all(c.template.is_land for c in pool):
            return _deliver(game, ctx.controller, list(pool), n)
    if library_search(spec) or land_from_hand(spec):
        # The land the AI's one land valuation delivers (the land a land
        # tutor or the land drop would choose).
        from ai.activation_ev import _choose_land_delivery
        lands = list(pool)
        picked = []
        while lands and len(picked) < n:
            land = _choose_land_delivery(game, ctx.controller, lands,
                                         batch=tuple(picked))
            picked.append(land)
            lands = [c for c in lands if c is not land]
        return picked
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
