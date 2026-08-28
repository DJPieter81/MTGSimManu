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
from ai.scoring_constants import (CLOCK_IMPACT_LIFE_SCALING,
                                  HASTE_GRANT_COMBAT_STEPS_ADVANCED)

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


def choose_sacrifice_victim(game, player_idx, legal):
    """Pick which permanent pays a sacrifice cost — the AI half of the
    `choose_sacrifice` callback seam.

    Minimises what the board gives up, on values derived from the game
    state and printed card data (no bare score tiers): a creature's cost
    is its `creature_threat_value` on the current snapshot; a
    non-creature's proxy is its printed mana investment. The legal set is
    normally type-homogeneous (the cost names one permanent type), so the
    two scales rarely mix; when a wildcard "permanent" cost does mix
    them, cheap non-creatures rank as the smaller loss — the intended
    tie-break.
    """
    from ai.ev_evaluator import creature_threat_value, snapshot_from_game

    if not legal:
        return None
    snap = snapshot_from_game(game, player_idx)

    def _loss(c):
        if c.effective_is_creature:
            return creature_threat_value(c, snap)
        return float(c.template.cmc or 0)

    return min(legal, key=_loss)


def choose_tutor_delivery(game, player_idx, eligible):
    """Pick which card an activated library tutor delivers — the AI half
    of the `choose_tutor_target` callback seam (the engine default is the
    highest mana value satisfying the constraint).

    Plan-best on the EXISTING valuation primitives, mirror-image of
    `choose_sacrifice_victim`: a creature's worth is its
    `creature_threat_value` on the current snapshot (evasion, triggers and
    board context included — a bigger body at a smaller mana value beats
    an expensive small one); a non-creature's proxy is its printed mana
    investment, the same proxy the engine ranking uses.
    """
    from ai.ev_evaluator import creature_threat_value, snapshot_from_game

    if not eligible:
        return None
    snap = snapshot_from_game(game, player_idx)

    def _worth(c):
        if c.template.is_creature:
            return creature_threat_value(c, snap)
        return float(c.template.cmc or 0)

    return max(eligible, key=_worth)


def _counter_cost_pt_delta(cost) -> int:
    """Net power/toughness the source loses (negative) or gains (positive)
    from this activation's counter cost.

    Derived from the counters' own printed semantics, not a tuned weight:
    +1/+1 and -1/-1 counters move power and toughness by the same amount,
    so one signed number describes both. Kinds with no P/T meaning
    (charge, oil, page) contribute 0.
    """
    from engine.cards import COUNTER_KIND_MINUS, COUNTER_KIND_PLUS

    delta = 0
    if cost.put_counter_kind == COUNTER_KIND_PLUS:
        delta += cost.put_counter_amount
    elif cost.put_counter_kind == COUNTER_KIND_MINUS:
        delta -= cost.put_counter_amount
    if cost.remove_counter_kind == COUNTER_KIND_PLUS:
        delta -= cost.remove_counter_amount
    elif cost.remove_counter_kind == COUNTER_KIND_MINUS:
        delta += cost.remove_counter_amount
    return delta


def activation_candidates(game, player_idx, snap, excluded=None):
    """Enumerate generic activated abilities worth activating right now.

    Returns ``[(permanent, ability_index, targets, ev, reason), ...]`` — a
    5-tuple, deliberately NOT merged with `land_animation_candidates`'s
    3-tuple: land animation is a different mechanic with its own call site and
    its own MAIN1 justification (it exists to attack this turn).

    Legality is NOT decided here. `ActivationManager.can_activate` owns every
    rules question; this function only asks "is it worth it", and only among
    abilities the engine has already declared legal. That split is the
    project's standing engine/AI boundary.

    Scoring is a single `position_value` delta: project the snapshot forward
    per effect kind and keep the improvement. No bare magnitudes — every number
    comes from parsed ability data (`amount`, `power_mod`, `toughness_mod`).
    Exception: GRANT_HASTE_TARGET cannot be a `position_value` delta (the
    snapshot's power terms already count summoning-sick creatures, so the
    grant changes no projected field) — it is scored directly on the
    `combat_clock` primitives under the land-animation race gates instead.
    """
    from engine.activation import ActivationManager
    from engine.cards import ActivationEffectKind as _K
    from engine.game_state import Phase as _Phase
    from ai.clock import position_value

    excluded = excluded or set()
    me = game.players[player_idx]
    out = []
    base = position_value(snap)

    for perm in list(me.battlefield):
        abilities = getattr(perm.template, 'activated_abilities', None) or []
        for ability in abilities:
            key = (perm.instance_id, ability.index)
            if key in excluded:
                continue
            if not ActivationManager.can_activate(game, player_idx, perm,
                                                  ability):
                continue

            # Cost terms shared by every effect kind. `position_value`'s
            # mana term is clamped at zero for spending, so holdback (applied
            # by the caller) carries the mana cost — but LIFE paid, a
            # SACRIFICED permanent (source or victim) and a DISCARDED card
            # are real position changes the projection must charge, or
            # "Pay 7 life: draw seven cards" scores as free.
            cost_updates = {}
            if ability.cost.life:
                cost_updates["my_life"] = snap.my_life - ability.cost.life
            # What leaves with a sacrificed permanent is derived from the
            # permanent itself: a land is a mana source, a creature is board
            # power. For a sacrifice-ANOTHER cost the charged permanent is
            # the victim the AI itself would choose at payment time — the
            # same chooser the callback seam uses, so projection and payment
            # cannot disagree.
            sacrificed = perm if ability.cost.sacrifice_self else None
            if ability.cost.sacrifice_type is not None:
                from engine.activation import ActivationManager as _AM
                sacrificed = choose_sacrifice_victim(
                    game, player_idx,
                    _AM.legal_sacrifice_victims(game, player_idx, perm,
                                                ability.cost))
                if sacrificed is None:
                    continue  # can_activate should have refused; defensive
            if sacrificed is not None:
                if sacrificed.template.is_land:
                    cost_updates["my_mana"] = max(0, snap.my_mana - 1)
                if sacrificed.effective_is_creature:
                    cost_updates["my_power"] = max(
                        0, snap.my_power - (sacrificed.power or 0))
            if ability.cost.discard_cards:
                cost_updates["my_hand_size"] = max(
                    0, snap.my_hand_size - ability.cost.discard_cards)
            # A counter cost that moves P/T is a real board change and must
            # be charged, or "Put a -1/-1 counter on this creature: ..."
            # scores as free right up to the point the creature dies. The
            # amount is the counter's own printed P/T semantics — +1/+1 and
            # -1/-1 are symmetric, so one number covers power and toughness.
            _pt_delta = _counter_cost_pt_delta(ability.cost)
            if _pt_delta and perm.effective_is_creature:
                cost_updates["my_power"] = max(
                    0, snap.my_power + _pt_delta)
                cost_updates["my_toughness"] = max(
                    0, snap.my_toughness + _pt_delta)

            # Merge effect deltas ON TOP of the cost terms — a discard-cost
            # draw must net the two hand-size changes, not overwrite one.
            updates = dict(cost_updates)
            kind = ability.effect_kind
            if kind is _K.DRAW_N:
                updates["my_hand_size"] = (
                    updates.get("my_hand_size", snap.my_hand_size)
                    + ability.amount)
                after = snap.fast_replace(**updates)
                reason = f"activate: draw {ability.amount}"
            elif kind is _K.DAMAGE_ANY_TARGET:
                updates["opp_life"] = snap.opp_life - ability.amount
                after = snap.fast_replace(**updates)
                reason = f"activate: {ability.amount} damage"
            elif kind is _K.PUMP_SELF_UEOT:
                # GATED, not merely scored. `position_value` has no
                # until-end-of-turn term, so a temporary pump reads as a
                # permanent gain and would be wildly over-valued. Restrict to
                # the case where the pump actually improves the combat race
                # this turn — the same rule already pinned by
                # tests/test_creature_land_activated_when_it_wins_race.py.
                if game.current_phase is not _Phase.MAIN1:
                    continue
                updates["my_power"] = (
                    updates.get("my_power", snap.my_power)
                    + ability.power_mod)
                after = snap.fast_replace(**updates)
                if not (after.my_clock_discrete < snap.my_clock_discrete
                        and after.my_clock_discrete <= snap.opp_clock_discrete):
                    continue
                reason = (f"activate: +{ability.power_mod}/"
                          f"+{ability.toughness_mod} improves the race")
            elif kind is _K.GRANT_HASTE_TARGET:
                # Pre-combat only, same MAIN1 justification as land
                # animation and pump: the grant's whole value is converting
                # an attack THIS turn.
                if game.current_phase is not _Phase.MAIN1:
                    continue
                from engine.cards import Keyword as _Kw
                # Valued only on OUR OWN summoning-sick would-be attacker.
                # Hasting an already-attackable body (or the opponent's
                # creature — engine-legal, the printed ability targets any
                # creature) buys nothing, so no candidate is emitted.
                sick = [c for c in me.creatures
                        if c.has_summoning_sickness and not c.tapped
                        and _Kw.DEFENDER not in c.keywords]
                if not sick:
                    continue
                tgt = max(sick, key=lambda c: max(0, c.power or 0))
                p = max(0, tgt.power or 0)
                if p <= 0:
                    continue
                evasive_p = p if ({k.value for k in tgt.keywords}
                                  & _EVASION_WORDS) else 0
                # Clock WITH the grant is the snapshot clock (snap.my_power
                # already counts every creature, sick or not — the clock
                # model does not know about summoning sickness); clock
                # WITHOUT is the same board minus this body's power, since
                # without haste it does not attack this turn.
                my_clock_with = combat_clock(
                    snap.my_power, snap.opp_life,
                    snap.my_evasion_power, snap.opp_toughness)
                my_clock_without = combat_clock(
                    max(0, snap.my_power - p), snap.opp_life,
                    max(0, snap.my_evasion_power - evasive_p),
                    snap.opp_toughness)
                opp_clock = combat_clock(
                    snap.opp_power, snap.my_life,
                    snap.opp_evasion_power, snap.my_toughness)
                if my_clock_with >= my_clock_without:
                    continue  # no clock improvement — save the mana
                if my_clock_with > opp_clock:
                    continue  # attacking loses the race — hold for defense
                # The credited delta is capped at the one combat step haste
                # actually advances (CR 702.10): the raw with/without
                # difference measures the creature's whole ongoing clock
                # contribution and hits the NO_CLOCK sentinel when it is
                # the board's only attacker.
                turns_saved = min(HASTE_GRANT_COMBAT_STEPS_ADVANCED,
                                  my_clock_without - my_clock_with)
                ev = turns_saved * CLOCK_IMPACT_LIFE_SCALING
                out.append((perm, ability.index, [tgt.instance_id], ev,
                            (f"activate: haste on {tgt.name} converts an "
                             f"attack this turn (clock "
                             f"{my_clock_without:.0f}→{my_clock_with:.0f} "
                             f"vs opp {opp_clock:.0f})")))
                continue
            elif kind in (_K.TUTOR_CREATURE_TO_BATTLEFIELD,
                          _K.TUTOR_TO_HAND):
                # Delivery-conditioned, through the SAME engine-side X
                # picker the payment path uses (`pick_activated_tutor_x`)
                # — the EV is priced on exactly what the chosen X will
                # deliver, the `_gate_x_tutor_payoff` discipline. The
                # once-each-turn ledger and the no-free-repeatable rule
                # are `can_activate`'s (checked above); the whiff is the
                # layer split: engine-legal (CR 701.19b), but paying for
                # a search that finds nothing is the AI throwing
                # resources away — no candidate.
                from engine.activated_effects import (
                    activated_tutor_x_budget, pick_activated_tutor_x)
                from ai.clock import mana_clock_impact

                x_budget = activated_tutor_x_budget(game, player_idx,
                                                    ability)
                best_x, target, _top = pick_activated_tutor_x(
                    game, player_idx, x_budget, ability)
                if target is None:
                    continue
                if kind is _K.TUTOR_TO_HAND:
                    # Selective card access projects like the draw it
                    # replaces: +1 hand on top of the charged costs.
                    updates["my_hand_size"] = (
                        updates.get("my_hand_size", snap.my_hand_size) + 1)
                    after = snap.fast_replace(**updates)
                    ev = position_value(after) - base
                    reason = f"activate: tutor {target.name} to hand"
                else:
                    after = snap.fast_replace(**updates)  # cost terms only
                    # Battlefield delivery: the credited value is the
                    # delivered body's mana value, X-waste charged 1:1
                    # inside the same net-value function the cast-side
                    # tutor gate uses, converted at the projection's
                    # per-mana clock scale.
                    delivered_cmc = target.template.cmc or 0
                    if (ability.tutor_data or {}).get('mv_bound_is_x'):
                        from engine.cast_manager import (
                            creature_tutor_x_net_value)
                        delivered_net = creature_tutor_x_net_value(
                            best_x, delivered_cmc)
                    else:
                        delivered_net = delivered_cmc
                    per_mana = (mana_clock_impact(snap)
                                * CLOCK_IMPACT_LIFE_SCALING)
                    ev = ((position_value(after) - base)
                          + delivered_net * per_mana)
                    reason = (f"activate: tutor {target.name} to "
                              f"battlefield"
                              + (f" (X={best_x})" if best_x else ""))
                if ev <= 0.0:
                    continue
                out.append((perm, ability.index, [], ev, reason))
                continue
            else:
                continue

            ev = position_value(after) - base
            if ev <= 0.0:
                continue  # an activation that does not improve position is
                          # not made; principled, not a tuned threshold
            out.append((perm, ability.index, [], ev, reason))
    return out
