"""What a spell's caster announces as it is cast (CR 601.2c).

The picker (`EVPlayer._choose_targets`) chooses the spell's objects and, for
a hand attack, whether to aim at its own hand. For a spell whose shape no
pick knows, the objects it requires are the engine's printed-data default
(`default_objects`): a legal announcement where such a spell used to be
cast at nothing. A player-only requirement ("target player", "target
opponent") the picks leave unchosen is announced here by the one owner of
what a player-only slot names
(`ai.resolution_choices.pick_trigger_targets`, which a triggered ability's
and a chosen mode's player slots already read): the admitted player the
slot's typed instruction is worth most to (`ai.modal.named_player_value`),
else the engine's printed default -- the opponent, the player such a spell
resolved against when it was cast at nothing.
"""
from __future__ import annotations

from typing import Any, List, Optional


def _slot_spec(spell: Any, req: Any) -> Optional[Any]:
    """The typed instruction the spell's slot for `req` is chosen for."""
    from engine.effect_carrier import slot_spec
    host = spell.template.effects.spell(0)
    if host is None:
        return None
    k = next((i for i, r in enumerate(host.targets) if r == req), None)
    return None if k is None else slot_spec(host, k)


def default_objects(game: Any, player_idx: int, spell: Any) -> List[Any]:
    """For a spell whose shape no pick knows, the engine's printed-data
    default (`engine.callbacks.default_cast_targets`) for the requirements
    an object answers; player-only requirements are announced by
    `with_required_players`."""
    from engine.callbacks import default_cast_targets
    from engine.cast_manager import CastManager
    objects = [r for r in CastManager.spell_requirements(spell)
               if not set(r.types) <= {"player"}]
    return default_cast_targets(game, player_idx, spell, objects,
                                CastManager.x_reach(game, player_idx, spell))


def with_required_players(game: Any, player_idx: int, spell: Any,
                          targets: List[Any]) -> List[Any]:
    """`targets` plus the player announced for each player-only
    requirement that binds whatever is chosen and that no chosen target
    answers. Unchanged when the spell still lacks a required target: the
    AI does not aim a spell it cannot cast."""
    from ai.resolution_choices import pick_trigger_targets
    from engine.cast_manager import CastManager
    from engine.constants import PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF
    from engine.target_solver import (binds_whatever_is_chosen,
                                      slot_admits_player, target_admitted)
    markers = {1 - player_idx: PLAYER_TARGET_OPPONENT,
               player_idx: PLAYER_TARGET_SELF}
    out = list(targets)
    for req in CastManager.spell_requirements(spell):
        if not binds_whatever_is_chosen(req) or \
                not set(req.types) <= {"player"}:
            continue
        if any(target_admitted(game, player_idx, [req], t) for t in out):
            continue
        players = [p for p in markers
                   if slot_admits_player(req, p, player_idx)]
        if not players:
            continue
        picked = pick_trigger_targets(
            game, player_idx, spell, _slot_spec(spell, req), req, players,
            [], CastManager.x_reach(game, player_idx, spell) or 0)
        out += [markers[p] for p in picked[:1] if p in markers]
    if CastManager.required_targets_chosen(game, player_idx, spell, out):
        return out
    return list(targets)
