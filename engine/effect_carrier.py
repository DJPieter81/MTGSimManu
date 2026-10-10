"""The carrier switch: how a switched legacy carrier reaches the dispatcher.

Design doc: docs/design/2026-09-29_clause_and_trigger_grammar.md, section 11
("Carrier switch", F7, A37, A38). A switched carrier keeps its gate and its
place; its apply first offers the host it resolves to `dispatch`, and runs
its legacy apply only when `dispatch` declines. `dispatch` takes a host when,
and only when:

* dispatch is enabled (`effect_resolver.legacy_only()` turns it off for the
  legacy side of the per-host harness);
* the host is in its family's strict shape (`effect_views.STRICT`, A38);
* the dispatcher can execute every part of it (`can_execute`, A37);
* the carrier's legacy target list maps onto the host's slots
  (`chosen_from_legacy`, A36): the host holds at most one target slot, and
  when the list is the whole card's (a spell's cast-time targets) no other
  host of its face holds a target, so every entry is that slot's.

Gate parity (`tools/effect_spec_equivalence.py --gate-parity`) holds every
host that can take this path to a harness-identical run
(`tools/host_resolution_equivalence.py --switched`).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from . import effect_resolver as er
from .effect_spec import iter_specs


def dispatch(game: Any, source: Any, controller: int, host: Any,
             legacy_targets: Optional[Sequence[Any]], *, family: str,
             x_value: int = 0, face_hosts: Iterable[Any] = ()) -> Optional[bool]:
    """Resolve `host` through the dispatcher if the carrier may.

    `face_hosts` are the other hosts whose targets share the legacy list
    (a spell's face; empty for an ability, whose targets are its own).
    Returns None when the carrier must run its legacy apply, else what
    `resolve_ability` returned (whether anything was performed)."""
    if host is None or not er.dispatch_enabled():
        return None
    from .effect_views import STRICT
    strict = STRICT.get(family)
    if strict is None or not strict(host) or not er.can_execute(host, family):
        return None
    chosen = _chosen(game, controller, host, list(legacy_targets or ()),
                     face_hosts)
    if chosen is None:
        return None
    return er.resolve_ability(game, er.handle_of(source), controller, host,
                              chosen, family=family, x_value=x_value,
                              source_object=source)


# The families whose spell hosts the family-generic clause handler
# ("dispatched") takes. A family joins when its spell carriers switch; card
# flow is switched for enter triggers only (`ETB_FAMILIES`), so a card-flow
# spell keeps its legacy clause handler.
SPELL_FAMILIES = ("damage",)


def spell_family(template: Any, effects: Any = None) -> Optional[str]:
    """The landed spell family (`SPELL_FAMILIES`) whose strict shape and
    executors take this template's whole SPELL host, with its targets
    placeable from the cast-time list; None when no family does. A loyalty
    line's clause template has no spell host of its own. `effects` is the
    template's parsed effects when the caller already holds them."""
    if getattr(template, "is_loyalty_clause", False):
        return None
    effects = template.effects if effects is None else effects
    host = effects.spell(0)
    if host is None or not places_legacy_targets(host, effects.front()):
        return None
    from .effect_views import STRICT
    for family in SPELL_FAMILIES:
        strict = STRICT.get(family)
        if strict is not None and strict(host) and er.can_execute(host, family):
            return family
    return None


def dispatch_activation(game: Any, source: Any, controller: int, ability: Any,
                        legacy_targets: Optional[Sequence[Any]], *,
                        x_value: int = 0) -> Optional[bool]:
    """An activated ability's switch. Its host is its source template's
    ACTIVATED host at the ability's index (`ActivatedAbility.index` is the
    host's `activation_index`); its family is the one its effect kind's
    derivation names. An ability its source template does not print (a
    granted one, a delay-free copy) has no host here: legacy."""
    template = getattr(source, "template", None)
    if template is None or not any(
            a is ability for a in (template.activated_abilities or ())):
        return None
    from .effect_views import DERIVATIONS
    kind = getattr(ability.effect_kind, "name", None)
    rec = DERIVATIONS.get(f"ActivatedAbility.effect_kind[{kind}]")
    if rec is None:
        return None
    return dispatch(game, source, controller,
                    template.effects.activated(ability.index), legacy_targets,
                    family=rec.family, x_value=x_value)


# The families whose hosts the enter-trigger carrier takes. Card flow only:
# an enter trigger whose target another family resolves (damage, removal)
# is still chosen by its legacy resolver, which picks on entry; the
# dispatcher's unbound slot would go to that owner's rule (A36), not to the
# same object.
ETB_FAMILIES = ("card_flow",)


def _slots_picked_by_executors(host: Any) -> bool:
    """Is every spec of `host` that uses a target slot executed by an
    executor that picks an unbound slot itself (`picks_unbound_slots`: the
    controller's choice out of the legal objects, A35)? The enter-trigger
    carrier binds no targets -- the engine resolves enter triggers on entry
    -- so only such a host resolves as printed through it."""
    from .effect_spec import _SPEC_OWN_FIELDS, RefKind, _refs
    for s in iter_specs(host.specs):
        uses = s.target_slot is not None or any(
            r.kind is RefKind.TARGET
            for name in _SPEC_OWN_FIELDS for r in _refs(getattr(s, name)))
        if uses and not getattr(er.EXECUTORS.get(s.verb),
                                "picks_unbound_slots", False):
            return False
    return True


def etb_plan(face_hosts: Iterable[Any]) -> Optional[list]:
    """The enter-trigger carrier's plan for one face: ``[(host, family)]``
    for every TRIGGERED(SELF_ENTERS) host of `face_hosts` when each is in an
    `ETB_FAMILIES` family's strict shape, executable, and has its slots
    picked by its own executors (`_slots_picked_by_executors`); None when
    the face has no such host or any one of them does not qualify (a card
    is taken whole or not at all, so no trigger of it resolves twice or not
    at all). One owner, read by the carrier and the closure."""
    from .effect_spec import EventHint, HostKind
    from .effect_views import STRICT
    hosts = [h for h in face_hosts
             if h.kind is HostKind.TRIGGERED and h.trigger is not None
             and EventHint.SELF_ENTERS in h.trigger.event_hints]
    if not hosts:
        return None
    plan = []
    for h in hosts:
        family = next((f for f in ETB_FAMILIES
                       if STRICT[f](h) and er.can_execute(h, f)
                       and _slots_picked_by_executors(h)), None)
        if family is None:
            return None
        plan.append((h, family))
    return plan


def dispatch_etb(game: Any, card: Any, controller: int) -> Optional[bool]:
    """The enter-trigger carrier (CR 603.2, 603.6a): the entering
    permanent's TRIGGERED(SELF_ENTERS) hosts on the face it shows, resolved
    through the dispatcher when `etb_plan` takes the face; None otherwise,
    and the legacy resolver runs. The engine resolves enter triggers on
    entry, so every slot reaches its executor unbound and the controller
    picks there (CR 603.3d, A35). Returns whether any host performed
    anything."""
    template = getattr(card, "template", None)
    if template is None or getattr(template, "is_loyalty_clause", False) \
            or not er.dispatch_enabled():
        return None
    faces = template.effects.faces
    face = 1 if getattr(card, "is_transformed", False) and len(faces) > 1 \
        else 0
    plan = etb_plan(faces[face] if faces else ())
    if plan is None:
        return None
    performed = False
    for h, family in plan:
        performed |= bool(er.resolve_ability(
            game, er.handle_of(card), controller, h,
            tuple(() for _ in h.targets), family=family,
            source_object=card))
    return performed


# ── The draw carrier (CR 121.1, 603.2, 603.3d) ─────────────────────────

# The strict shape the draw carrier takes (`effect_views.
# strict_draw_trigger`, A38: a later family's executors never silently
# switch a draw host), and the family label its resolutions carry.
DRAW_FAMILY = "draw_trigger"


def draw_matches(game: Any, draw: Any, controller: int, drawer: int) -> bool:
    """Does `drawer` drawing a card now trigger a head typed `draw`
    (`effect_spec.DrawEvent`) on a permanent `controller` controls? Who
    draws, relative to the controller; the Nth card of the drawer's turn;
    and the printed exemption for the first card the drawer draws in their
    own draw step (counted by `GameState.draw_cards`)."""
    if draw.drawer == "you" and drawer != controller:
        return False
    if draw.drawer == "opponent" and drawer == controller:
        return False
    player = game.players[drawer]
    if draw.nth is not None and player.cards_drawn_this_turn != draw.nth:
        return False
    if draw.except_first_in_draw_step and \
            player.cards_drawn_in_draw_step == FIRST_DRAW_STEP_CARD and \
            drawer == game.active_player and _in_draw_step(game):
        return False
    return True


# CR 504.1: the first card a player draws in their draw step.
FIRST_DRAW_STEP_CARD = 1


def _in_draw_step(game: Any) -> bool:
    from .game_state import Phase
    return game.current_phase == Phase.DRAW


def _draw_family(h: Any) -> Optional[str]:
    """`DRAW_FAMILY` when the draw-triggered host `h` is in its strict
    shape and executable; None otherwise."""
    from .effect_views import STRICT
    if h.trigger is None or not STRICT[DRAW_FAMILY](h) \
            or not er.can_execute(h, DRAW_FAMILY):
        return None
    return DRAW_FAMILY


def draw_plan(face_hosts: Iterable[Any]) -> Optional[list]:
    """The draw carrier's plan for one face: ``[(host, family)]`` for every
    host whose trigger head is a DRAW event when each is takeable
    (`_draw_family`); None when the face has no such host or any one is
    not (a card is taken whole or not at all, so no trigger of it resolves
    twice or not at all). One owner, read by the carrier and the closure."""
    from .effect_spec import EventHint, HostKind
    hosts = [h for h in face_hosts
             if h.kind is HostKind.TRIGGERED and h.trigger is not None
             and EventHint.DRAW in h.trigger.event_hints]
    if not hosts:
        return None
    plan = []
    for h in hosts:
        family = _draw_family(h)
        if family is None:
            return None
        plan.append((h, family))
    return plan


def _member(v: Any, players: Sequence[int], permanents: Sequence[Any]) -> bool:
    if isinstance(v, int) and not isinstance(v, bool):
        return v in players
    return any(v is c for c in permanents)


def trigger_targets(game: Any, source: Any, controller: int,
                    host: Any) -> Optional[er.Chosen]:
    """CR 603.3d: the targets a triggered ability takes as it is put on the
    stack, per slot: the controller's pick (`callbacks.
    choose_trigger_targets`) out of the legal choices
    (`target_solver.legal_slot_choices`). Only legal, distinct choices
    count, at most the slot's count; a required target the pick leaves
    short is filled by the engine's default (`callbacks.
    default_trigger_targets`). None when a required target has no legal
    choice: the ability is removed from the stack."""
    from .callbacks import default_trigger_targets
    from .target_solver import legal_slot_choices
    specs = list(iter_specs(host.specs))
    chosen = []
    for k, req in enumerate(host.targets):
        spec = next((s for s in specs if s.target_slot == k), None)
        players, permanents = legal_slot_choices(game, controller, req,
                                                 source=source)
        n = max(0, int(req.count_max or 0))
        need = min(int(req.count_min or 0), n,
                   len(players) + len(permanents))
        if req.count_min and not (players or permanents):
            return None
        picked: list = []
        asked = game.callbacks.choose_trigger_targets(
            game, controller, source, spec, req, list(players),
            list(permanents)) if n else []
        for v in asked or ():
            if len(picked) < n and _member(v, players, permanents) \
                    and all(v is not x for x in picked):
                picked.append(v)
        if len(picked) < need:
            rest_p = [p for p in players
                      if not any(isinstance(x, int) and x == p for x in picked)]
            rest_c = [c for c in permanents if all(c is not x for x in picked)]
            picked += default_trigger_targets(
                controller, req, rest_p, rest_c)[:need - len(picked)]
        chosen.append(tuple(v if isinstance(v, int) else er.handle_of(v)
                            for v in picked))
    return tuple(chosen)


def _face_hosts(card: Any) -> tuple:
    faces = card.template.effects.faces
    if not faces:
        return ()
    face = 1 if getattr(card, "is_transformed", False) and len(faces) > 1 \
        else 0
    return faces[face]


def dispatch_draw_triggers(game: Any, drawer: int) -> tuple:
    """The draw carrier (CR 603.2): each permanent's draw-triggered
    abilities whose typed head names this draw (`draw_matches`), resolved
    through the dispatcher with the drawer as the trigger event's player
    ("that player") and targets picked as they are put on the stack
    (`trigger_targets`). In stack order: the non-active player's triggers
    are put on the stack last and resolve first (CR 603.3b). Returns (the
    ids of the sources the carrier took for this draw -- their legacy
    handlers do not run -- and whether any host performed anything)."""
    if not er.dispatch_enabled():
        return frozenset(), False
    taken, performed = set(), False
    ap = game.active_player
    order = [p for p in range(len(game.players)) if p != ap] + [ap]
    event = er.TriggerEvent(player=drawer)
    for idx in order:
        for src in list(game.players[idx].battlefield):
            template = getattr(src, "template", None)
            if template is None or getattr(template, "is_loyalty_clause",
                                           False):
                continue
            plan = draw_plan(_face_hosts(src))
            if plan is None:
                continue
            taken.add(src.instance_id)
            controller = src.controller
            for h, family in plan:
                if not draw_matches(game, h.trigger.draw, controller, drawer):
                    continue
                chosen = trigger_targets(game, src, controller, h)
                if chosen is None:
                    continue
                performed |= bool(er.resolve_ability(
                    game, er.handle_of(src), controller, h, chosen,
                    family=family, event=event, source_object=src))
                if game.game_over:
                    return frozenset(taken), performed
    return frozenset(taken), performed


def places_legacy_targets(host: Any, face_hosts: Iterable[Any] = ()) -> bool:
    """Can a carrier place its legacy target list on `host`'s slots
    without reading text? Yes for a host with no slot (its list is empty)
    or with one slot whose printed span is known, when no other host of
    `face_hosts` chose targets into the same list. The gate-parity closure
    asks the same question, so a host the switch cannot place is never
    counted on the new path."""
    if not host.targets:
        return True
    return (len(host.targets) == 1 and _slot_span(host) is not None
            and not any(h.targets for h in face_hosts if h is not host))


def _slot_span(host: Any) -> Optional[tuple]:
    """The printed span of slot 0's spec, when it lies inside the host's
    text (an empty or out-of-text span places nothing)."""
    span = next((s.span for s in iter_specs(host.specs)
                 if s.target_slot == 0), None)
    if span is None or not 0 <= span[0] < span[1] <= len(host.text or ""):
        return None
    return span


def _chosen(game: Any, controller: int, host: Any, legacy: list,
            face_hosts: Iterable[Any]) -> Optional[er.Chosen]:
    """The legacy list on the host's slots, or None when it cannot be
    placed without reading text (`places_legacy_targets`)."""
    face_hosts = tuple(face_hosts)
    if not places_legacy_targets(host, face_hosts):
        return None
    if not host.targets:
        return () if not legacy else None
    span = _slot_span(host)
    try:
        return er.chosen_from_legacy(
            host, legacy, [span[0]] * len(legacy), slot_spans=[span],
            game=game, face=1 - controller, self_face=controller)
    except ValueError:                         # no slot holds the position
        return None
