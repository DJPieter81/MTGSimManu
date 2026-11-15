"""What an edict takes from the players it names (AI, from CR 701.21a).

An edict ("each opponent sacrifices a creature of their choice", "target
player sacrifices a creature", "each player sacrifices two creatures") makes
each player it names sacrifice permanents that player chooses (CR 701.21a).
The AI models that choice the way it makes its own sacrifices: a player
gives up the permanents whose loss costs them least, by
`ai.permanent_threat.permanent_threat` (a planeswalker's loyalty pool
included). So an edict is worth to its caster what those permanents are
worth to their owner -- position-value units, the scale a targeted
removal's threat premium uses in `EVPlayer._score_spell` -- less what the
caster gives up when the edict names every player.

An edict removes those permanents as it resolves, so it is interaction this
turn (`ev_evaluator._is_immediate_interaction`), and the spell's score
carries its value (`EVPlayer._score_spell`): the board projection removes
nothing for it (the opponent, not the caster, chooses the victim) and the
snapshot holds no planeswalker term. One owner for both readers.

Which permanents an instruction admits is the description owner's
(`engine.effect_conditions.permanent_described`, unit PF); which modes a
modal edict performs is the mode chooser's (`ai.modal.select_modal_modes`).
Typed instructions only: no oracle text, no card names.
"""
from __future__ import annotations

from typing import Any, Iterator, List


def _opposing(s: Any) -> bool:
    """The instruction's acting players may be an opponent of its caster:
    each opponent, each player, or a player chosen as a target."""
    from engine.effect_model import SelectorKind
    from engine.effect_spec import Ref, RefKind
    kind = getattr(s.actor, "kind", None)
    if kind in (SelectorKind.OPPONENTS, SelectorKind.ALL_PLAYERS):
        return True
    return isinstance(s.actor, Ref) and kind is RefKind.TARGET


def _edicts(host: Any) -> Iterator[Any]:
    """The edict instructions of one host: SACRIFICE by acting players its
    caster may oppose, over a description the description owner reads."""
    from engine.effect_conditions import description_supported
    from engine.effect_spec import Verb, iter_specs
    if host is None:
        return
    for s in iter_specs(host.specs):
        if s.verb is Verb.SACRIFICE and _opposing(s) \
                and description_supported(s.filter):
            yield s


def is_edict(template: Any) -> bool:
    """Does casting the spell perform an edict: its own instructions, or
    one of its modes'?"""
    effects = getattr(template, "effects", None)
    if effects is None:
        return False
    host = effects.spell(0)
    if host is None:
        return False
    return any(True for h in (host, *host.modes) for _s in _edicts(h))


def _count(s: Any) -> int:
    """How many permanents each named player sacrifices: the printed
    number, one when none is printed."""
    from engine.effect_spec import AmountKind
    a = s.amount
    if a is None:
        return 1
    return max(0, a.n) if a.kind is AmountKind.LITERAL else 0


def victims(game: Any, player: int, s: Any, source: Any = None) -> List[Any]:
    """The permanents `player` gives up to the edict instruction `s`: of
    those its description admits now, the ones whose loss costs them least
    (CR 701.21a: the player chooses)."""
    from ai.permanent_threat import permanent_threat
    from engine.effect_conditions import permanent_described
    owner = game.players[player]
    admitted = [p for p in owner.battlefield
                if permanent_described(p, s.filter, source=source)]
    admitted.sort(key=lambda p: permanent_threat(p, owner, game))
    return admitted[:_count(s)]


def player_loss(game: Any, player: int, s: Any, source: Any = None) -> float:
    """What the permanents `player` gives up to `s` are worth to them."""
    from ai.permanent_threat import permanent_threat
    owner = game.players[player]
    return sum(permanent_threat(p, owner, game)
               for p in victims(game, player, s, source))


def _instruction_value(game: Any, caster: int, s: Any, source: Any) -> float:
    from engine.effect_model import SelectorKind
    opponents = [p for p in range(len(game.players)) if p != caster]
    theirs = sum(player_loss(game, p, s, source) for p in opponents)
    kind = getattr(s.actor, "kind", None)
    if kind is SelectorKind.OPPONENTS:
        return theirs
    mine = player_loss(game, caster, s, source)
    if kind is SelectorKind.ALL_PLAYERS:
        return theirs - mine
    # A player chosen as a target: the caster names the one it gains most
    # from (an opponent's loss, or its own, which it pays).
    return max([player_loss(game, p, s, source) for p in opponents]
               + [-mine])


def edict_value(game: Any, caster: int, card: Any) -> float:
    """What casting `card` takes through its edicts, net of what its caster
    gives up: the spell's own edict instructions, or those of the modes its
    caster chooses (`ai.modal.select_modal_modes`)."""
    effects = getattr(card.template, "effects", None)
    host = effects.spell(0) if effects is not None else None
    if host is None:
        return 0.0
    hosts = [host]
    if host.modes:
        from ai.modal import select_modal_modes
        hosts = [host.modes[i] for i in select_modal_modes(game, card, caster)
                 if 0 <= i < len(host.modes)]
    return sum(_instruction_value(game, caster, s, card)
               for h in hosts for s in _edicts(h))
