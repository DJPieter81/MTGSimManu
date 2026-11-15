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


def _loss_costs_of(sub: Any) -> List[Any]:
    """The mana costs a delayed sub-ability's host pays on pain of its
    controller losing the game."""
    from engine.effect_spec import Verb, iter_specs
    from ai.unless_value import _loses_the_game
    return [pay.payload.thaw().mana for pay in iter_specs(sub.host.specs)
            if pay.verb is Verb.PAY
            and any(_loses_the_game(o) for o in pay.otherwise)]


def deferred_loss_costs(template: Any) -> List[Any]:
    """The mana costs (`ManaCost`) the spell commits its caster to pay at a
    later step on pain of losing the game, in printed order."""
    from engine.effect_spec import SubAbilityKind, Verb, iter_specs
    effects = getattr(template, "effects", None)
    host = effects.spell(0) if effects is not None else None
    if host is None:
        return []
    out = []
    for s in iter_specs(host.specs):
        sub = s.payload if s.verb is Verb.CREATE_TRIGGER else None
        if sub is not None and sub.kind is SubAbilityKind.DELAYED:
            out += _loss_costs_of(sub)
    return out


def pending_loss_costs(game: Any, player_idx: int) -> List[Any]:
    """The pay-or-lose costs already waiting for the player in the one
    delayed-trigger queue (CR 603.7): the spells it cast before."""
    out = []
    for t in game.delayed_triggers.pending:
        sub = getattr(getattr(t, "effect", None), "keywords", {}).get("sub")
        if t.controller == player_idx and sub is not None:
            out += _loss_costs_of(sub)
    return out


def _total(costs: List[Any]) -> Any:
    """One mana cost paying every cost in `costs` (their pips summed)."""
    from engine.mana import ManaCost
    fields = ("generic", "white", "blue", "black", "red", "green",
              "colorless")
    return ManaCost(**{f: sum(getattr(c, f) for c in costs) for f in fields})


def deferred_costs_payable(game: Any, player_idx: int, card: Any) -> bool:
    """Will every cost the spell defers on pain of losing the game be
    payable at its controller's next upkeep, beside the ones already
    pending (see the module docstring)?"""
    costs = deferred_loss_costs(card.template)
    if not costs:
        return True
    # Every such cost already pending is paid at the same upkeep from the
    # same board: the spell is payable only beside them (their sum).
    costs = [_total(pending_loss_costs(game, player_idx) + costs)]
    return _upkeep_payable(game, player_idx, costs)


def _upkeep_payable(game: Any, player_idx: int, costs: List[Any]) -> bool:
    """Are `costs` payable from the player's board as it will stand at its
    next upkeep: every permanent untapped (CR 502.3), creatures able to tap
    (CR 302.6), the mana pool empty (CR 500.4)?"""
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


def keeps_pending_costs_payable(game: Any, player_idx: int,
                                extra_lands: List[Any]) -> bool:
    """Would every pay-or-lose cost already pending for the player be
    payable at its next upkeep with `extra_lands` on the battlefield too
    (a search delivering them)? True when none is pending. The board as
    it will stand at that upkeep, as `deferred_costs_payable` reads it."""
    pending = pending_loss_costs(game, player_idx)
    if not pending:
        return True
    player = game.players[player_idx]
    added = [c for c in extra_lands if c not in player.battlefield]
    player.battlefield.extend(added)
    try:
        return _upkeep_payable(game, player_idx, [_total(pending)])
    finally:
        for c in added:
            player.battlefield.remove(c)


# A cost's coloured pips by the colour letter a mana unit offers (CR
# 105.2: the five colours).
_PIP_LETTERS = (("white", "W"), ("blue", "U"), ("black", "B"), ("red", "R"),
                ("green", "G"))


def tap_colours(game: Any, player_idx: int, land: Any) -> set:
    """The colour letters `land` can tap for, read from the payment
    owner's own per-source units (`ManaPayment.land_mana_units`, what an
    upkeep payment actually spends): not `produces_mana`, which also lists
    a colour the land adds only as it enters (an any-colour enter mana on
    a land that taps for colourless)."""
    from engine.mana_payment import ManaPayment
    return {letter for unit in ManaPayment.land_mana_units(game, player_idx,
                                                           land)
            for letter in unit}


def short_colours(game: Any, player_idx: int, extra_lands: List[Any]) -> set:
    """The colours whose pips in the pending pay-or-lose costs outnumber
    the lands that can tap for them, with `extra_lands` added to the board.
    Each land counts once per colour it can tap for: the shortfall a
    search still delivering lands one at a time must close. Empty when
    nothing is pending."""
    pending = pending_loss_costs(game, player_idx)
    if not pending:
        return set()
    total = _total(pending)
    lands = [c for c in game.players[player_idx].battlefield
             if c.template.is_land] + list(extra_lands)
    colours = [tap_colours(game, player_idx, c) for c in lands]
    short = set()
    for field, letter in _PIP_LETTERS:
        need = getattr(total, field)
        supply = sum(1 for cs in colours if letter in cs)
        if need > supply:
            short.add(letter)
    return short
