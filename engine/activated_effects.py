"""Resolution of activated abilities (CR 602).

ONE generic callable for every card. Dispatch is on the parsed
``ActivationEffectKind`` enum — never on oracle substrings — so adding a card
to the pool adds no Python here, and adding an effect SHAPE is one new branch
that serves every card carrying that shape.

An effect this tranche does not execute is recorded via
``effect_diagnostics.record_unhandled_effect`` rather than silently no-opping.
A silently no-opping activation is the most likely failure mode of the whole
subsystem: the ability would appear to fire, the cost would be paid, and
nothing would happen.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from .cards import ActivationEffectKind

if TYPE_CHECKING:  # pragma: no cover
    from .cards import ActivatedAbility, CardInstance
    from .game_state import GameState


def resolve_activated_ability(game: "GameState", source: "CardInstance",
                              controller: int,
                              targets: Optional[List[int]] = None,
                              *, ability: "ActivatedAbility") -> bool:
    """Resolve one activated ability. Returns True when an effect applied.

    ``game._activation_depth`` is incremented here as well as on the push
    side. Guarding only the push misses re-entry that begins during
    RESOLUTION — which is the path a later tranche (a sacrifice cost routed
    through the death machinery, firing a dies-trigger that activates
    something) would actually take.
    """
    kind = ability.effect_kind
    game._activation_depth = getattr(game, '_activation_depth', 0) + 1
    try:
        if kind is ActivationEffectKind.DAMAGE_ANY_TARGET:
            from .oracle_resolver import resolve_damage_to_chosen_target
            return bool(resolve_damage_to_chosen_target(
                game, source, controller, ability.amount, targets))

        if kind is ActivationEffectKind.DRAW_N:
            drawn = game.draw_cards(controller, ability.amount)
            if drawn:
                names = ", ".join(c.name for c in drawn)
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{source.name} activated — draw {ability.amount} "
                    f"({names})")
            return True

        if kind is ActivationEffectKind.PUMP_SELF_UEOT:
            source.temp_power_mod += ability.power_mod
            source.temp_toughness_mod += ability.toughness_mod
            game.log.append(
                f"T{game.display_turn} P{controller+1}: {source.name} "
                f"gets +{ability.power_mod}/+{ability.toughness_mod} "
                f"until end of turn")
            return True

        # ANIMATE_SELF_UEOT is owned by `parse_land_animation` / `animate_land`
        # and must never be double-executed here; it reaches this branch only
        # if the enumerator's skip was bypassed.
        from .effect_diagnostics import record_unhandled_effect
        record_unhandled_effect(source.name, "activated")
        return False
    finally:
        game._activation_depth -= 1
