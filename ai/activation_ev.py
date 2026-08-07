"""Generic activated-ability win-condition EV — Track H
(2026-07-05 calibration wave).

Activated win-condition lines — creature-land animation ("manlands"),
planeswalker ultimate trajectories (see ai/pw_ability.py), granted-
ability engines — were never enumerated in the main-phase decision:
control decks sat on their win conditions and lost on decking or
chip damage (Azorius P0, 23.3% field WR).

This module enumerates land-animation lines from battlefield
permanents as Play candidates for ai/ev_player.py's ACTIVATION
region.  All scoring derives from ai/clock.py primitives — no card
names, no deck gates, no bare numerics.  A gameplan can weight the
resulting play through the normal EV competition, but the mechanism
works with zero per-card data.

Race rule (pinned by
tests/test_creature_land_activated_when_it_wins_race.py):
activate exactly when

    post-animation my_clock  <  pre-animation my_clock   (it helps)
    post-animation my_clock  <=  opp_clock               (attacking
                                                          wins/holds
                                                          the race)

Both clocks compose ``ai.clock.combat_clock`` — the same primitive
``position_value`` uses — so the decision is consistent with the
rest of the EV pipeline.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Tuple

from ai.clock import combat_clock, creature_clock_impact
from ai.scoring_constants import CLOCK_IMPACT_LIFE_SCALING

if TYPE_CHECKING:  # pragma: no cover
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot


# Evasion keyword words shared with ai.clock.creature_clock_impact —
# power carried by these bypasses blockers in the clock model.
_EVASION_WORDS = {"flying", "menace", "trample"}


def land_animation_candidates(
        game: "GameState", player_idx: int, snap: "EVSnapshot",
) -> List[Tuple["CardInstance", float, str]]:
    """Enumerate profitable land-animation activations.

    Returns ``[(land, ev, reason), …]`` for every battlefield land
    whose animate line is affordable right now and whose activation
    wins the race per the module-level rule.  EV is the animated
    body's ``creature_clock_impact`` on the same
    ``CLOCK_IMPACT_LIFE_SCALING`` scale every other Play candidate
    uses, so the line competes fairly with casts and land drops.
    """
    from engine.oracle_parser import parse_land_animation

    me = game.players[player_idx]
    out: List[Tuple["CardInstance", float, str]] = []

    # Clocks shared by every candidate this call.
    my_clock_now = combat_clock(
        snap.my_power, snap.opp_life,
        snap.my_evasion_power, snap.opp_toughness)
    opp_clock = combat_clock(
        snap.opp_power, snap.my_life,
        snap.opp_evasion_power, snap.my_toughness)

    for land in me.lands:
        if land.tapped or getattr(land, 'is_animated', False):
            continue
        spec = parse_land_animation(land.template.oracle_text or '')
        if spec is None:
            continue
        kws = spec['keywords']
        # A land that entered this turn animates into a summoning-sick
        # creature — it cannot attack, so the activation buys nothing
        # (unless the line grants haste).
        if land.summoning_sick and 'haste' not in kws:
            continue
        payers = [l for l in me.untapped_lands if l is not land]
        if len(payers) < spec['cost']:
            continue

        power = spec['power']
        toughness = spec['toughness']
        evasive = power if (kws & _EVASION_WORDS) else 0
        my_clock_with = combat_clock(
            snap.my_power + power, snap.opp_life,
            snap.my_evasion_power + evasive, snap.opp_toughness)

        if my_clock_with >= my_clock_now:
            continue  # no clock improvement — save the mana
        if my_clock_with > opp_clock:
            continue  # attacking loses the race — hold for defense

        ev = creature_clock_impact(
            power, toughness, kws, snap) * CLOCK_IMPACT_LIFE_SCALING
        reason = (f"Animate: {land.name} ({power}/{toughness}) — "
                  f"clock {my_clock_now:.0f}→{my_clock_with:.0f} vs "
                  f"opp {opp_clock:.0f}")
        out.append((land, ev, reason))

    return out
