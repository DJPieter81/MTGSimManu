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
# an enter trigger with a target (damage, removal) is still chosen by its
# legacy resolver, which picks on entry; the dispatcher's unbound slot would
# not choose the same object (A36).
ETB_FAMILIES = ("card_flow",)


def etb_plan(face_hosts: Iterable[Any]) -> Optional[list]:
    """The enter-trigger carrier's plan for one face: ``[(host, family)]``
    for every TRIGGERED(SELF_ENTERS) host of `face_hosts` when each is in an
    `ETB_FAMILIES` family's strict shape, executable and untargeted; None
    when the face has no such host or any one of them does not qualify (a
    card is taken whole or not at all, so no trigger of it resolves twice
    or not at all). One owner, read by the carrier and the closure."""
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
                       and not h.targets), None)
        if family is None:
            return None
        plan.append((h, family))
    return plan


def dispatch_etb(game: Any, card: Any, controller: int) -> Optional[bool]:
    """The enter-trigger carrier (CR 603.2, 603.6a): the entering
    permanent's TRIGGERED(SELF_ENTERS) hosts on the face it shows, resolved
    through the dispatcher when `etb_plan` takes the face; None otherwise,
    and the legacy resolver runs. The engine resolves enter triggers on
    entry, with no targets: a slot reaches its owner unbound. Returns
    whether any host performed anything."""
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
        performed |= bool(dispatch(game, card, controller, h, (),
                                   family=family))
    return performed


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
