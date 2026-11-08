"""Costs a spell commits its caster to pay later, or lose the game.

"At the beginning of your next upkeep, pay <cost>. If you don't, you lose
the game." (CR 603.7, 118.12, 104.3c) is part of what casting such a spell
costs: the caster pays it at that upkeep or loses. A caster who cannot pay
it then loses the game for the spell, so the spell is cast only when the
cost will be payable at that upkeep.

At the beginning of the caster's next turn every permanent it controls
untaps (CR 502.3) and its mana pool is empty (CR 500.4), and a creature it
has controlled since that turn began can tap for mana (CR 302.6). The
upkeep payment is made from that board: the permanents the caster controls
now, every one untapped. A land not yet played is not counted -- the
upkeep comes before the next land drop.

Read from the spell's typed effects (`CardTemplate.effects`): a delayed
sub-ability whose host pays a cost and, if it is not paid, makes its
controller lose the game. No card names, no oracle text.
"""
from __future__ import annotations

from typing import Any, List


def deferred_loss_costs(template: Any) -> List[Any]:
    """The mana costs (`ManaCost`) the spell commits its caster to pay at a
    later step on pain of losing the game, in printed order."""
    from engine.effect_spec import SubAbilityKind, Verb, iter_specs
    from ai.unless_value import _loses_the_game
    effects = getattr(template, "effects", None)
    host = effects.spell(0) if effects is not None else None
    if host is None:
        return []
    out = []
    for s in iter_specs(host.specs):
        sub = s.payload if s.verb is Verb.CREATE_TRIGGER else None
        if sub is None or sub.kind is not SubAbilityKind.DELAYED:
            continue
        for pay in iter_specs(sub.host.specs):
            if pay.verb is Verb.PAY and any(_loses_the_game(o)
                                            for o in pay.otherwise):
                out.append(pay.payload.thaw().mana)
    return out


def deferred_costs_payable(game: Any, player_idx: int, card: Any) -> bool:
    """Will every cost the spell defers on pain of losing the game be
    payable at its controller's next upkeep (see the module docstring)?"""
    costs = deferred_loss_costs(card.template)
    if not costs:
        return True
    from engine.cast_manager import CastManager
    player = game.players[player_idx]
    board = list(player.battlefield)
    saved = [(c, c.tapped, c.summoning_sick) for c in board]
    pool = player.mana_pool
    saved_pool = dict(vars(pool))
    try:
        for c in board:
            c.tapped = False                       # CR 502.3
            c.summoning_sick = False               # CR 302.6
        for k, v in saved_pool.items():            # CR 500.4
            if isinstance(v, int):
                setattr(pool, k, 0)
        return all(CastManager.mana_payable(game, player_idx, cost)
                   for cost in costs)
    finally:
        for c, tapped, sick in saved:
            c.tapped, c.summoning_sick = tapped, sick
        for k, v in saved_pool.items():
            setattr(pool, k, v)
