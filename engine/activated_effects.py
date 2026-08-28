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

from typing import TYPE_CHECKING, List, Optional, Tuple

from .cards import ActivationEffectKind

if TYPE_CHECKING:  # pragma: no cover
    from .cards import ActivatedAbility, CardInstance, CardTemplate
    from .game_state import GameState


# ── Activated tutors (TUTOR_* kinds) ─────────────────────────────────

def tutor_target_matches(template: "CardTemplate", spec: dict,
                         x_value: Optional[int] = 0) -> bool:
    """Does a library card satisfy an activated tutor's parsed search
    constraint (`ActivatedAbility.tutor_data`)? Pure predicate, zero
    preference — WHICH matching card is delivered is the callback's
    choice.

    ``x_value=None`` ignores an "mana value X or less" bound (used by the
    X picker to enumerate candidates across every X).
    """
    from .cards import CardType, Supertype
    from .mana import Color

    types = template.card_types
    for word in spec.get('types', ()):
        if word == 'permanent':
            if not any(t in types for t in (
                    CardType.CREATURE, CardType.ARTIFACT,
                    CardType.ENCHANTMENT, CardType.LAND,
                    CardType.PLANESWALKER)):
                return False
            continue
        try:
            if CardType(word) not in types:
                return False
        except ValueError:
            return False  # a type word the engine does not model
    for word in spec.get('not_types', ()):
        try:
            if CardType(word) in types:
                return False
        except ValueError:
            continue
    for word in spec.get('supertypes', ()):
        try:
            if Supertype(word) not in template.supertypes:
                return False
        except ValueError:
            return False
    if spec.get('subtypes'):
        have = {s.lower() for s in (template.subtypes or ())}
        if not all(word in have for word in spec['subtypes']):
            return False
    if spec.get('colors'):
        # Printed colour is the CR 105 characteristic ("green creature
        # card"); colour identity is the fallback for templates loaded
        # without printed colours (matches the GSZ resolver's behaviour).
        have_colors = template.colors or template.color_identity
        if not any(Color(letter) in have_colors
                   for letter in spec['colors']):
            return False
    cmc = template.cmc or 0
    if spec.get('max_mv') is not None and cmc > spec['max_mv']:
        return False
    if spec.get('mv_bound_is_x') and x_value is not None and cmc > x_value:
        return False
    return True


def eligible_tutor_targets(library: List["CardInstance"], spec: dict,
                           x_value: Optional[int] = 0
                           ) -> List["CardInstance"]:
    """Library cards a tutor with this constraint may deliver right now."""
    return [c for c in library
            if tutor_target_matches(c.template, spec, x_value)]


def default_tutor_rank(card: "CardInstance") -> Tuple[int, int]:
    """Engine-default delivery ranking: highest mana value first, P/T
    tie-break — the same ranking the GSZ resolver and
    `pick_creature_tutor_x_value` use (several premium targets are 0/0
    with a characteristic-defining ability)."""
    return ((card.template.cmc or 0),
            (card.template.power or 0) + (card.template.toughness or 0))


def activated_tutor_x_budget(game: "GameState", player_idx: int,
                             ability: "ActivatedAbility") -> int:
    """Largest X the controller can afford for this ability right now:
    total castable capacity minus the fixed pips, divided by how many
    {X} pips the cost carries. ONE formula shared by the payment path
    (`ActivationManager.activate`) and the AI valuation layer, so the X
    the AI priced is the X the engine charges."""
    if ability.cost.x_count <= 0:
        return 0
    player = game.players[player_idx]
    capacity = (player.untapped_mana_capacity()
                + player.mana_pool.total()
                + player._tron_mana_bonus())
    return max(0, (capacity - ability.cost.mana.cmc) // ability.cost.x_count)


def pick_activated_tutor_x(game: "GameState", player_idx: int,
                           x_budget: int, ability: "ActivatedAbility"
                           ) -> Tuple[int, object, object]:
    """Engine-side X picker for X-bound activated tutors ("mana value X
    or less"). Module-level so the AI valuation layer consults the SAME
    picker the payment path uses — the activation EV is conditioned on
    exactly what the chosen X will deliver, and the chosen X is the
    cheapest one that delivers it (the `pick_creature_tutor_x_value`
    discipline, whose net-value function is reused directly).

    Returns ``(best_x, best_target, top_candidate)`` — for a tutor with
    no X bound, X is 0 and best_target IS the top candidate.
    """
    from .cast_manager import creature_tutor_x_net_value

    spec = ability.tutor_data or {}
    library = game.players[player_idx].library
    if not spec.get('mv_bound_is_x'):
        elig = eligible_tutor_targets(library, spec)
        if not elig:
            return 0, None, None
        top = max(elig, key=default_tutor_rank)
        return 0, top, top

    candidates = eligible_tutor_targets(library, spec, x_value=None)
    if not candidates:
        return 0, None, None
    top_candidate = max(candidates, key=default_tutor_rank)
    best_x = 0
    best_net: Optional[int] = None
    best_target = None
    for x in range(0, max(0, int(x_budget)) + 1):
        affordable = [c for c in candidates if (c.template.cmc or 0) <= x]
        if not affordable:
            continue
        target = max(affordable, key=default_tutor_rank)
        net = creature_tutor_x_net_value(x, target.template.cmc or 0)
        # Strict > keeps the LOWEST X among equal nets (cheapest first).
        if best_net is None or net > best_net:
            best_net = net
            best_x = x
            best_target = target
    return best_x, best_target, top_candidate


def _resolve_activated_tutor(game: "GameState", source: "CardInstance",
                             controller: int, ability: "ActivatedAbility",
                             x_value: int) -> bool:
    """Resolve a TUTOR_* activation through the shared library-search
    machinery: choose (callback seam) → move through the zone funnel
    (battlefield entry gets the ETB fan-out) → shuffle → search
    bookkeeping + opponents' search triggers — the same sequence the
    fetchland path performs, whiff included (CR 701.19: a failed search
    still shuffles and still counts as searching)."""
    player = game.players[controller]
    spec = ability.tutor_data or {}
    eligible = eligible_tutor_targets(player.library, spec, x_value)

    found = None
    if eligible:
        # The CHOICE among legal candidates is strategic — routed through
        # the callback seam (the choose_fetch_target pattern). A missing
        # or misbehaving callback falls back to the engine default.
        chooser = getattr(game.callbacks, 'choose_tutor_target', None)
        if chooser is not None:
            found = chooser(game, controller, source, list(eligible))
        if found is None or found not in eligible:
            found = max(eligible, key=default_tutor_rank)

        if spec.get('dest') == 'battlefield':
            game.zone_mgr.move_card(
                game, found, "library", "battlefield",
                cause=f"{source.name} tutor",
                controller_override=controller)
            if spec.get('tapped'):
                found.tapped = True
            game._handle_permanent_etb(found, controller)
        else:
            game.zone_mgr.move_card(game, found, "library", "hand",
                                    cause=f"{source.name} tutor")
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {source.name} "
            f"activated — finds {found.name} "
            f"({'battlefield' if spec.get('dest') == 'battlefield' else 'hand'})")
    else:
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {source.name} "
            f"activated — search finds nothing")

    game.rng.shuffle(player.library)
    player.library_searches_this_game += 1
    game._trigger_library_search(controller)
    return True


def resolve_activated_ability(game: "GameState", source: "CardInstance",
                              controller: int,
                              targets: Optional[List[int]] = None,
                              *, ability: "ActivatedAbility",
                              x_value: int = 0) -> bool:
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

        if kind is ActivationEffectKind.GRANT_HASTE_TARGET:
            # CR 702.10 haste, granted until end of turn. temp_keywords is
            # the engine's until-EOT keyword channel (Dash and Goryo's-style
            # reanimation use the same one): `CardInstance.keywords` unions
            # it in, `has_summoning_sickness` clears while HASTE is present,
            # and `cleanup_damage()` (cleanup step, CR 514) expires it.
            from .cards import CardType, Keyword
            for tid in (targets or []):
                found = game.get_card_by_id(tid)
                if (found is None or found.zone != "battlefield"
                        or not (CardType.CREATURE in found.template.card_types
                                or getattr(found, 'is_animated', False))):
                    continue  # CR 608.2b — an illegal target is skipped
                found.temp_keywords.add(Keyword.HASTE)
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: {source.name} "
                    f"grants {found.name} haste until end of turn")
                return True
            return False  # every declared target left the battlefield —
                          # the ability fizzles (CR 608.2b)

        if kind in (ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD,
                    ActivationEffectKind.TUTOR_TO_HAND):
            return _resolve_activated_tutor(game, source, controller,
                                            ability, x_value)

        # ANIMATE_SELF_UEOT is owned by `parse_land_animation` / `animate_land`
        # and must never be double-executed here; it reaches this branch only
        # if the enumerator's skip was bypassed.
        from .effect_diagnostics import record_unhandled_effect
        record_unhandled_effect(source.name, "activated")
        return False
    finally:
        game._activation_depth -= 1
