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
    return next((s.span for s in iter_specs(host.specs)
                 if s.target_slot == 0), None)


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
            game=game, face=1 - controller)    # -1: the opponent's face
    except ValueError:                         # no slot holds the position
        return None
