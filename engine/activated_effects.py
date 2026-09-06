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


def tutor_search_pool(player, spec: dict) -> List["CardInstance"]:
    """Every card inside a tutor's search ZONES, in one list.

    The library always; the graveyard as well when the parsed shape says
    "search your library and/or graveyard" (`spec['also_graveyard']`).
    One owner of the zone set so the X picker enumerates exactly what the
    resolver will search."""
    pool = list(player.library)
    if spec.get('also_graveyard'):
        pool.extend(player.graveyard)
    return pool


def choose_tutor_delivery(game: "GameState", controller: int,
                          source: "CardInstance",
                          eligible: List["CardInstance"]
                          ) -> Optional["CardInstance"]:
    """WHICH legal candidate is delivered — a strategic choice, so it goes
    through the callback seam (the `choose_fetch_target` pattern).  A
    missing or misbehaving callback falls back to the engine default
    (`default_tutor_rank`: highest mana value, P/T tie-break)."""
    if not eligible:
        return None
    chooser = getattr(game.callbacks, 'choose_tutor_target', None)
    found = chooser(game, controller, source, list(eligible)) \
        if chooser is not None else None
    if found is None or found not in eligible:
        found = max(eligible, key=default_tutor_rank)
    return found


def put_tutor_target(game: "GameState", found: "CardInstance",
                     controller: int, spec: dict, cause: str,
                     entry_counters: int = 0) -> None:
    """Move a found card to the parsed destination through the zone funnel
    (CR 603 zone-change triggers / CR 614 replacements), giving a
    battlefield entry the ETB fan-out.

    ``entry_counters`` is the "…onto the battlefield with N +1/+1 counters
    on it" rider: CR 614.1c makes those counters part of the entry itself,
    so they are on the permanent before the ETB fan-out (and before state-
    based actions can see a 0/0 body)."""
    if spec.get('dest') == 'battlefield':
        game.zone_mgr.move_card(
            game, found, found.zone, "battlefield",
            cause=cause, controller_override=controller)
        if spec.get('tapped'):
            found.tapped = True
        if entry_counters:
            found.add_plus_counters(entry_counters, game)
        game._handle_permanent_etb(found, controller)
    else:
        game.zone_mgr.move_card(game, found, found.zone, "hand", cause=cause)


def finish_library_search(game: "GameState", controller: int) -> None:
    """Close a library search: shuffle, count it, and let opponents'
    search-watching triggers see it (CR 701.19 — a failed search still
    shuffles and still counts as searching)."""
    player = game.players[controller]
    game.rng.shuffle(player.library)
    player.library_searches_this_game += 1
    game._trigger_library_search(controller)


def default_tutor_rank(card: "CardInstance") -> Tuple[int, int, int]:
    """Engine-default delivery ranking, rules-derived: a candidate that
    completes an unbounded mana engine with its controller's board (CR
    726.4 shortcut material — see
    `ActivationManager.would_complete_unbounded_engine`) ranks first; then
    highest mana value, P/T tie-break — the same ranking the GSZ resolver
    and `pick_creature_tutor_x_value` use (several premium targets are 0/0
    with a characteristic-defining ability). The engine key reads the
    instance's bound game; an unbound instance ranks on printed values."""
    game = getattr(card, '_game_state', None)
    completes = 0
    if game is not None:
        from .activation import ActivationManager
        completes = int(ActivationManager.would_complete_unbounded_engine(
            game, card.controller, card.template))
    return (completes, (card.template.cmc or 0),
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
    eligible = eligible_tutor_targets(
        tutor_search_pool(player, spec), spec, x_value)

    found = choose_tutor_delivery(game, controller, source, eligible)
    if found is not None:
        put_tutor_target(game, found, controller, spec,
                         cause=f"{source.name} tutor")
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {source.name} "
            f"activated — finds {found.name} "
            f"({'battlefield' if spec.get('dest') == 'battlefield' else 'hand'})")
    else:
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {source.name} "
            f"activated — search finds nothing")

    finish_library_search(game, controller)
    return True


def _resolve_graveyard_exile(game: "GameState", source: "CardInstance",
                             controller: int, ability: "ActivatedAbility",
                             targets: Optional[List[int]]) -> bool:
    """Resolve an EXILE_FROM_GRAVEYARD activation (CR 406 — exile is a
    zone, so every card moved here goes through the zone funnel and
    leaves-the-graveyard triggers/replacements see it).

    ONE branch for all five parsed shapes; ``spec['scope']`` is the
    discriminator:

      * ``cards`` — only the DECLARED targets move. CR 608.2b: a target
        that has already left the graveyard is skipped, never silently
        redirected onto a different card.
      * ``target_player`` — the whole of one player's graveyard. The
        chosen player is the OPPONENT: this is the same declared-or-
        default contract `resolve_damage_to_chosen_target` already uses
        for "any target" ("an exhausted or empty target list falls
        through to the opponent"), and in a two-player game those are the
        only two choices. A multiplayer engine would put a callback seam
        here, as `choose_sacrifice` / `choose_tutor_target` do.
      * ``all`` — every graveyard INCLUDING the activator's own. That
        symmetry is the printed cost of the effect and the AI's
        valuation must pay it; the resolver simply applies it.
      * ``each_opponent`` — every graveyard except the activator's.
    """
    spec = ability.graveyard_exile_data or {}
    scope = spec.get('scope')
    cause = f"{source.name} graveyard exile"

    if scope == 'cards':
        moved = 0
        for tid in (targets or []):
            found = game.get_card_by_id(tid)
            if found is None or found.zone != "graveyard":
                continue  # CR 608.2b — illegal target, skipped
            game.zone_mgr.move_card_to_exile(game, found, cause=cause)
            moved += 1
        if moved:
            game.log.append(
                f"T{game.display_turn} P{controller+1}: {source.name} "
                f"activated — exiles {moved} card(s) from graveyards")
        return bool(moved)

    if scope == 'all':
        victims = list(range(len(game.players)))
    elif scope == 'each_opponent':
        victims = [i for i in range(len(game.players)) if i != controller]
    elif scope == 'target_player':
        victims = [i for i in range(len(game.players)) if i != controller]
    else:
        from .effect_diagnostics import record_unhandled_effect
        record_unhandled_effect(source.name, "activated")
        return False

    exiled = 0
    for idx in victims:
        for card in list(game.players[idx].graveyard):
            game.zone_mgr.move_card_to_exile(game, card, cause=cause)
            exiled += 1
    game.log.append(
        f"T{game.display_turn} P{controller+1}: {source.name} activated "
        f"— exiles {exiled} card(s) from "
        f"{', '.join(f'P{i+1}' for i in victims)} graveyard(s)")
    # A cleared-but-already-empty graveyard is still a RESOLVED ability
    # (nothing about it fizzles); whether it was worth paying for is the
    # AI's judgment, not a rules question.
    return True


def _resolve_put_counter(game: "GameState", source: "CardInstance",
                         controller: int, ability: "ActivatedAbility",
                         targets: Optional[List[int]]) -> bool:
    """Put counters on the recipients this ability names (CR 121.1).

    Two scopes, one branch, mirroring the untap resolver: a DECLARED target
    receives the counters (CR 608.2b — one that has left the battlefield is
    skipped, never silently redirected onto something else), and the
    untargeted SELF form puts them on the source, only while the source is
    still there.

    Counters are written through `CardInstance.adjust_counters`, the same
    accessor the counter COST payer uses, so a +1/+1 counter moves power and
    toughness and a -1/-1 counter walks toughness toward the zero-toughness
    SBA. There is no parallel ledger and no until-end-of-turn expiry — that
    is the whole difference from `PUMP_SELF_UEOT`.
    """
    spec = ability.put_counter_data
    if not spec:
        return False
    counter_kind = spec['kind']
    amount = int(spec['amount'])
    if amount <= 0:
        return False

    if spec['self']:
        recipients = [source] if source.zone == "battlefield" else []
    else:
        recipients = []
        for tid in (targets or []):
            found = game.get_card_by_id(tid)
            if found is None or found.zone != "battlefield":
                continue
            recipients.append(found)

    applied = False
    for recipient in recipients:
        recipient.adjust_counters(counter_kind, amount)
        applied = True
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {source.name} "
            f"activated — {amount} {counter_kind} counter"
            f"{'s' if amount != 1 else ''} on {recipient.name}")
    return applied


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
        # CR 603.7 — a delayed effect does not happen now. It creates a
        # delayed triggered ability that fires at its stated step, ONCE,
        # and independently of this source (CR 603.7d): the closure below
        # captures the controller and a delay-free copy of the ability, so
        # a source that sacrificed itself to pay its own cost still
        # delivers. Dispatch is on the parsed field, never on oracle text.
        if ability.delayed_timing is not None:
            from dataclasses import replace as _dc_replace
            from .delayed_triggers import DelayedTrigger

            immediate = _dc_replace(ability, delayed_timing=None)
            captured_targets = list(targets or [])

            def _fire(g, _ab=immediate, _src=source, _ctrl=controller,
                      _tgts=captured_targets, _x=x_value):
                resolve_activated_ability(g, _src, _ctrl, _tgts,
                                          ability=_ab, x_value=_x)

            game.register_delayed_trigger(DelayedTrigger(
                timing=ability.delayed_timing,
                controller=controller,
                effect=_fire,
                description=f"{source.name}: {ability.effect_text}",
                created_turn=game.turn_number,
            ))
            game.log.append(
                f"T{game.display_turn} P{controller+1}: {source.name} "
                f"queues a delayed trigger "
                f"({ability.delayed_timing.value})")
            return True

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

        if kind is ActivationEffectKind.UNTAP_TARGET_PERMANENT:
            # Two shapes, one branch. A DECLARED target is untapped (CR
            # 608.2b: one that has left the battlefield is skipped, never
            # silently redirected); with no declared target the ability is
            # the untargeted SELF form ("Untap this creature") and the
            # source is what untaps — and only while the source is still
            # there, which is what makes the -1/-1-paid loop stop dead the
            # moment the zero-toughness SBA removes it.
            untapped = False
            for tid in (targets or []):
                found = game.get_card_by_id(tid)
                if found is None or found.zone != "battlefield":
                    continue
                found.tapped = False
                untapped = True
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: {source.name} "
                    f"activated — untaps {found.name}")
            if not targets:
                if source.zone != "battlefield":
                    return False
                source.tapped = False
                untapped = True
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: {source.name} "
                    f"activated — untaps itself")
            return untapped

        if kind in (ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD,
                    ActivationEffectKind.TUTOR_TO_HAND):
            return _resolve_activated_tutor(game, source, controller,
                                            ability, x_value)

        if kind is ActivationEffectKind.EXILE_FROM_GRAVEYARD:
            return _resolve_graveyard_exile(game, source, controller,
                                            ability, targets)

        if kind in (ActivationEffectKind.PUT_COUNTER_SELF,
                    ActivationEffectKind.PUT_COUNTER_TARGET):
            return _resolve_put_counter(game, source, controller,
                                        ability, targets)
        if kind is ActivationEffectKind.ADAPT:
            # CR 702.132a — "If this creature has no +1/+1 counters on it,
            # put N +1/+1 counters on it." The condition is checked on
            # RESOLUTION: an activation on an already-adapted creature was
            # legal, its cost is spent, and it does nothing here. A source
            # that left the battlefield has nothing to receive counters.
            from .cards import COUNTER_KIND_PLUS
            if source.zone != "battlefield":
                return False
            if source.counter_count(COUNTER_KIND_PLUS) > 0:
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: {source.name} "
                    f"adapt {ability.amount} — already has +1/+1 counters, "
                    f"nothing happens")
                return False
            # The ONE place adapt writes counters: the instance counter
            # funnel, so P/T moves and a counters-placed trigger hook
            # routes here in a single edit.
            source.adjust_counters(COUNTER_KIND_PLUS, ability.amount)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: {source.name} "
                f"adapt {ability.amount} — puts {ability.amount} +1/+1 "
                f"counter(s) on it (now {source.power}/{source.toughness})")
            return True

        # ANIMATE_SELF_UEOT is owned by `parse_land_animation` / `animate_land`
        # and must never be double-executed here; it reaches this branch only
        # if the enumerator's skip was bypassed.
        from .effect_diagnostics import record_unhandled_effect
        record_unhandled_effect(source.name, "activated")
        return False
    finally:
        game._activation_depth -= 1
