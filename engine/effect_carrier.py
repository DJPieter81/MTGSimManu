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

import functools
import operator
import weakref

from typing import Any, Iterable, Optional, Sequence

from . import effect_resolver as er
from .effect_spec import iter_specs


def dispatching() -> bool:
    """Is the dispatcher taking the hosts its carriers switched? False under
    `effect_resolver.legacy_only()`, the per-host harness's legacy side: a
    legacy handler that yields a switched host to a carrier keeps it then."""
    return er.dispatch_enabled()


def dispatch(game: Any, source: Any, controller: int, host: Any,
             legacy_targets: Optional[Sequence[Any]], *, family: str,
             x_value: int = 0, face_hosts: Iterable[Any] = (),
             source_lki: Any = None) -> Optional[bool]:
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
                              source_object=source, source_lki=source_lki)


# The families whose spell hosts the family-generic clause handler
# ("dispatched") takes. A family joins when its spell carriers switch: card
# flow joined with impulse draw (unit I), whose only carrier is this one --
# the tag-gated impulse handler it replaces moved the cards to hand. The
# type-change family joined with unit AT: a host that adds a type to an
# object, which no legacy handler applies (Enter the Avatar State, Jump
# Scare). The edict family joined with unit ED: "<players> sacrifice(s)
# <N> <permanents> of their choice", which no legacy handler applied. The
# token family joined with unit TK: a token with the characteristics its
# creator prints (CR 111.3), created by the players it names (CR 111.2);
# the legacy token clause it shares hosts with is switched to it too
# (`clause_resolver._a_token`).
TOKEN_FAMILY = "tokens_counters"
SPELL_FAMILIES = ("damage", "card_flow", "type_change", "edict", TOKEN_FAMILY)


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
    if host is None or not places_legacy_targets(host, effects.front()) \
            or not _announced_fit(template, host):
        return None
    from .effect_views import STRICT
    for family in SPELL_FAMILIES:
        strict = STRICT.get(family)
        if strict is not None and strict(host) and er.can_execute(host, family):
            return family
    return None


def _announced_fit(template: Any, host: Any) -> bool:
    """The targets a cast announces (CR 601.2c: one per requirement of the
    legality owner that binds whatever is chosen) fit the host's slots. A
    requirement the effect grammar types no slot for -- one read from a
    keyword's reminder text, an awaken's "target land you control" -- is
    announced all the same, and no carrier can place it on the host."""
    return _required_count(template.oracle_text or "") <= len(host.targets)


@functools.lru_cache(maxsize=None)
def _required_count(oracle_text: str) -> int:
    from .target_solver import binds_whatever_is_chosen, parse
    return sum(1 for r in parse(oracle_text) if binds_whatever_is_chosen(r))


def _as_chosen(spec: Any) -> Any:
    """A spec of a chosen mode: its target, grouped with the other modes'
    as the spell was printed (CR 700.2), is its own slot once the mode is
    chosen (CR 700.2a)."""
    import dataclasses
    t = spec.target
    if t is not None and t.mode_group is not None:
        spec = dataclasses.replace(
            spec, target=dataclasses.replace(t, mode_group=None))
    branches = {branch: tuple(_as_chosen(s) for s in getattr(spec, branch))
                for branch in er._BRANCH_FIELDS if getattr(spec, branch)}
    return dataclasses.replace(spec, **branches) if branches else spec


def _mode_as_host(mode: Any, **fields: Any) -> Any:
    """A typed mode (`AbilityEffects.modes[i]` of a modal host) as a host of
    its own: its grouped target requirements are its slots once the mode is
    chosen (CR 700.2a); `fields` replace the mode's own (a trigger's head)."""
    import dataclasses
    return dataclasses.replace(
        mode, specs=tuple(_as_chosen(s) for s in mode.specs),
        targets=tuple(dataclasses.replace(r, mode_group=None)
                      for r in mode.targets), **fields)


def chosen_mode_host(template: Any, index: int,
                     effects: Any = None) -> Optional[Any]:
    """A modal spell's chosen mode as a host of its own: the typed mode
    host (`AbilityEffects.modes[index]` of the spell host) whose grouped
    target requirements are its slots. None when the spell has no such
    mode."""
    effects = template.effects if effects is None else effects
    modes = effects.modes(0)
    if not 0 <= index < len(modes):
        return None
    return _mode_as_host(modes[index])


def trigger_mode_host(host: Any, index: int) -> Optional[Any]:
    """A modal triggered ability's mode as a host of its own (CR 700.2): the
    typed mode under its parent's trigger head -- what a carrier checks,
    picks targets for and resolves once the mode is chosen. None when the
    host has no such mode."""
    modes = getattr(host, "modes", None) or ()
    if not 0 <= index < len(modes):
        return None
    return _mode_as_host(modes[index], kind=host.kind, trigger=host.trigger)


def mode_hosts(host: Any) -> tuple:
    """Every mode of a modal triggered `host`, each a host of its own
    (`trigger_mode_host`), in printed order."""
    return tuple(trigger_mode_host(host, i)
                 for i in range(len(getattr(host, "modes", None) or ())))


def _takes(h: Any, family: str) -> bool:
    """Is the triggered host `h` in `family`'s strict shape and executable?
    A modal host is when it prints no instruction before its modes, its
    header asks for a number of modes it has, and each mode -- a host of
    its own under `h`'s head (`trigger_mode_host`) -- is (CR 700.2). One
    rule for every trigger carrier's plan."""
    from .effect_views import STRICT
    strict = STRICT[family]
    if not getattr(h, "modes", None):
        return strict(h) and er.can_execute(h, family)
    lo, hi = h.choose
    if h.specs or not 0 <= lo <= hi or hi < 1 or lo > len(h.modes):
        return False
    return all(strict(m) and er.can_execute(m, family)
               for m in mode_hosts(h))


def _mode_open(game: Any, source: Any, controller: int, mode: Any) -> bool:
    """CR 700.2a: a mode can be chosen only when each target it requires has
    a legal choice (`target_solver.legal_slot_choices`)."""
    from .target_solver import legal_slot_choices
    for req in mode.targets:
        if req.count_min:
            players, permanents = legal_slot_choices(game, controller, req,
                                                     source=source)
            if not players and not permanents:
                return False
    return True


def _held_modes(asked: Any, legal: Sequence[int], lo: int,
                hi: int) -> list:
    """The controller's answer held to the rules (CR 700.2): only legal,
    distinct modes, at most the header's most; short of its fewest, the
    lowest legal modes complete it (a deterministic engine completion, as
    `modal_spell.choose_modes`'s). In printed order."""
    chosen: list = []
    for i in asked or ():
        if isinstance(i, int) and not isinstance(i, bool) and i in legal \
                and i not in chosen and len(chosen) < hi:
            chosen.append(i)
    for i in legal:
        if len(chosen) >= lo:
            break
        if i not in chosen:
            chosen.append(i)
    return sorted(chosen)


def choose_trigger_modes(game: Any, source: Any, controller: int,
                         host: Any) -> tuple:
    """CR 603.3c, 700.2: the modes a modal triggered ability's controller
    chooses as it is put on the stack -- the controller's answer
    (`callbacks.choose_trigger_modes`) among the legal modes (`_mode_open`),
    held to the header (`_held_modes`) -- each a host of its own
    (`trigger_mode_host`), in printed order. () when fewer modes are legal
    than the header's fewest: the ability is removed from the stack."""
    lo, hi = host.choose
    hosts = mode_hosts(host)
    legal = [i for i, m in enumerate(hosts)
             if _mode_open(game, source, controller, m)]
    if len(legal) < lo:
        return ()
    asked = game.callbacks.choose_trigger_modes(
        game, controller, source, host, list(legal), (lo, hi))
    chosen = _held_modes(asked, legal, lo, hi)
    _audit_trigger_modes(game, source, host, chosen)
    return tuple(hosts[i] for i in chosen)


def _audit_trigger_modes(game: Any, source: Any, host: Any,
                         chosen: Sequence[int]) -> None:
    """CR 700.2, restated from the raw header: the chosen modes are distinct
    modes the host prints, as many as its header allows. Observation
    only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    lo, hi = host.choose
    n = len(host.modes)
    rules_audit.check(
        "700.2/trigger_modes",
        len(set(chosen)) == len(chosen) and all(0 <= i < n for i in chosen)
        and lo <= len(chosen) <= hi,
        f"{getattr(source, 'name', '?')}: modes {list(chosen)} chosen under "
        f"a header of {lo} to {hi} of {n}", game=game)


def put_on_stack(game: Any, source: Any, controller: int,
                 host: Any) -> list:
    """CR 603.3c-d: what a triggered ability takes as it is put on the
    stack -- a modal one's chosen modes (`choose_trigger_modes`), each a
    host of its own -- with each one's targets (`trigger_targets`):
    ``[(host, chosen)]`` in printed order. Empty when the ability is
    removed -- a required target has no legal choice, or no mode is
    chosen -- or countered by the ward of a permanent it targets
    (`ward_counters`, CR 702.21a)."""
    hosts = (choose_trigger_modes(game, source, controller, host)
             if getattr(host, "modes", None) else (host,))
    out = []
    for h in hosts:
        chosen = trigger_targets(game, source, controller, h)
        if chosen is None:
            return []
        out.append((h, chosen))
    if ward_counters(game, source, controller, out):
        return []
    return out


def _targeted_ids(parts: Sequence[Any]) -> list:
    """The instance ids of the permanents ``[(host, chosen)]`` targets."""
    return [v.instance_id for _, chosen in parts for slot in chosen
            for v in slot if isinstance(v, er.Handle)]


def _ward_gate(game: Any, source: Any, controller: int,
               ids: Sequence[int]) -> tuple:
    from .optional_costs import ward_gate
    return ward_gate(game, source, controller, ids,
                     what=f"{getattr(source, 'name', '?')}'s triggered "
                          f"ability")


def ward_counters(game: Any, source: Any, controller: int,
                  parts: Sequence[Any]) -> bool:
    """CR 702.21a: is a triggered ability, with the targets it took as it
    was put on the stack (``[(host, chosen)]``, CR 603.3d), countered by
    the ward of an opposing permanent it targets whose cost its controller
    does not pay? Asked of the one ward gate (`optional_costs.ward_gate`),
    as a spell's targets are; read by every carrier (`put_on_stack`) and
    by a parent's reflexive or delayed ability (`effect_resolver.
    resolve_sub_ability`). Auditor `702.21a/ward_paid`."""
    ids = _targeted_ids(parts)
    if not ids:
        return False
    survives, paid = _ward_gate(game, source, controller, ids)
    if survives:
        from .spell_resolution import ResolutionManager
        ResolutionManager._audit_ward_paid(game, source, controller, ids,
                                           paid)
    return not survives


def mode_family(template: Any, index: int,
                effects: Any = None) -> Optional[str]:
    """The landed spell family (`SPELL_FAMILIES`) whose strict shape and
    executors take a modal spell's chosen mode, its targets placeable from
    the cast-time list; None when no family does, and for a mode the modal
    spell owner does not choose (`modal_spell.in_scope`)."""
    from . import modal_spell
    if not modal_spell.in_scope(template, effects):
        return None
    host = chosen_mode_host(template, index, effects)
    if host is None or not places_legacy_targets(host, ()):
        return None
    from .effect_views import STRICT
    for family in SPELL_FAMILIES:
        strict = STRICT.get(family)
        if strict is not None and strict(host) and er.can_execute(host, family):
            return family
    return None


def dispatch_activation(game: Any, source: Any, controller: int, ability: Any,
                        legacy_targets: Optional[Sequence[Any]], *,
                        x_value: int = 0,
                        source_lki: Any = None) -> Optional[bool]:
    """An activated ability's switch. Its host is its source template's
    ACTIVATED host at the ability's index (`ActivatedAbility.index` is the
    host's `activation_index`); its family is the one its effect kind's
    derivation names. An ability its source template does not print (a
    granted one, a delay-free copy) has no host here: legacy."""
    template = getattr(source, "template", None)
    if template is None or not any(
            a is ability for a in (template.activated_abilities or ())):
        return None
    host = template.effects.activated(ability.index)
    family = activation_family(ability, host)
    if family is None:
        return None
    return dispatch(game, source, controller, host, legacy_targets,
                    family=family, x_value=x_value, source_lki=source_lki)


# The families an UNCLASSIFIED activation -- one the legacy classifier does
# not read, so it has no legacy apply and nothing can regress -- resolves
# in, when its typed host is in the family's strict shape (A38) and the
# dispatcher can execute it: card flow (unit A, an activated impulse draw:
# Cori Mountain Monastery), type change (unit AT, "target permanent
# becomes an artifact in addition to its other types until end of turn":
# Liquimetal Coating) and removal (unit LA, "destroy target [nonbasic]
# land", its basic-land search riders included: Field of Ruin, Ghost
# Quarter).
UNCLASSIFIED_ACTIVATION_FAMILIES = ("card_flow", "type_change", "removal")


def activation_family(ability: Any, host: Any) -> Optional[str]:
    """The family an activated ability dispatches in (one owner, read by
    the carrier, the closure and the harness): the one its effect kind's
    derivation names; for an UNCLASSIFIED ability, the first of
    `UNCLASSIFIED_ACTIVATION_FAMILIES` whose strict shape takes its typed
    host and that the dispatcher can execute; None otherwise."""
    from .effect_views import DERIVATIONS, STRICT
    kind = getattr(ability.effect_kind, "name", None)
    rec = DERIVATIONS.get(f"ActivatedAbility.effect_kind[{kind}]")
    if rec is not None:
        return rec.family
    if kind != "UNCLASSIFIED" or host is None:
        return None
    return next((f for f in UNCLASSIFIED_ACTIVATION_FAMILIES
                 if STRICT[f](host) and er.can_execute(host, f)), None)


# ── The loyalty carrier (unit LY, CR 606.1, 602.2b, 601.2c, 608.2c) ────

# The legacy kinds whose loyalty lines the carrier takes. UNCLASSIFIED: a
# line the legacy classifier does not read, which was refused, so nothing
# can regress. DAMAGE: a line its legacy resolver read only as a number --
# it dealt that many to the highest-mana-value creature it killed, else to
# the opponent, whatever the line's own target admitted.
LOYALTY_DISPATCH_KINDS = frozenset({"UNCLASSIFIED", "DAMAGE"})

# The landed families a loyalty line's typed host may resolve in, first
# match first.
LOYALTY_FAMILIES = ("damage", "removal", "card_flow", "tokens_counters",
                    "edict", "type_change")


def loyalty_family(ability: Any, host: Any) -> Optional[str]:
    """The family a loyalty line dispatches in (one owner, read by the
    carrier, the walker's menu, the closure and the harness): for a line of
    a `LOYALTY_DISPATCH_KINDS` kind, the first of `LOYALTY_FAMILIES` whose
    strict shape takes its typed host and that the dispatcher can execute;
    None otherwise (the line keeps its legacy kind's resolver)."""
    from .effect_views import STRICT
    kind = getattr(ability.effect_kind, "name", None)
    if kind not in LOYALTY_DISPATCH_KINDS or host is None:
        return None
    return next((f for f in LOYALTY_FAMILIES
                 if STRICT[f](host) and er.can_execute(host, f)), None)


def intervening_if_holds_now(game: Any, source: Any, controller: int,
                             host: Any, event: Any = None) -> bool:
    """Does `host`'s intervening "if" (CR 603.4) hold now for `controller`,
    `source` being the object whose ability it is -- asked of the
    dispatcher's own evaluator (`effect_resolver.holds`)? True for a host
    without one. `event` is the trigger event ("that player"), when the
    ability triggers on one. The AI reads it to value a trigger before it
    triggers (a loyalty line's "when you do" trigger, unit RX); a carrier
    reads it as the ability triggers (the step carrier, unit BT); only a
    host the dispatcher can execute is asked."""
    head = getattr(host, "trigger", None)
    if head is None or head.intervening_if is None:
        return True
    probe = er.Resolution(game=game, source=er.handle_of(source),
                          controller=controller, ability=host, chosen=(),
                          event=event, source_object=source)
    return bool(er.holds(probe, head.intervening_if))


def loyalty_host(pw: Any, ability: Any) -> Optional[Any]:
    """The typed host of the printed loyalty line `ability` of walker `pw`:
    the LOYALTY host of the face and slot that print it."""
    template = pw.template
    effects = getattr(template, "effects", None)
    if effects is None:
        return None
    for face, attr in ((0, "loyalty_abilities"),
                       (1, "back_face_loyalty_abilities")):
        for slot, a in (getattr(template, attr, None) or {}).items():
            if a is ability:
                return effects.loyalty(slot, face)
    return None


def loyalty_targets_exist(game: Any, pw: Any, controller: int,
                          ability: Any) -> bool:
    """Has every required target slot of a line the carrier takes a legal
    choice now (`target_solver.legal_slot_choices`)? A line without one
    cannot be activated (CR 602.2b, 601.2c), so it is not offered."""
    from .target_solver import legal_slot_choices
    host = loyalty_host(pw, ability)
    for req in host.targets:
        if req.count_min:
            players, permanents = legal_slot_choices(game, controller, req,
                                                     source=pw)
            if not (players or permanents):
                return False
    return True


def loyalty_targets(game: Any, pw: Any, controller: int,
                    ability: Any) -> Optional[er.Chosen]:
    """CR 602.2b, 601.2c: the targets a loyalty line the carrier takes is
    activated with, per slot, as a trigger's are put on the stack
    (`trigger_targets`: the controller's pick out of the legal choices).
    None when the carrier does not take the line, or a required target has
    no legal choice: the line cannot be activated."""
    host = loyalty_host(pw, ability)
    if loyalty_family(ability, host) is None:
        return None
    return trigger_targets(game, pw, controller, host)


def dispatch_loyalty(game: Any, pw: Any, controller: int, ability: Any,
                     chosen: Optional[Sequence[Any]] = None
                     ) -> Optional[bool]:
    """The loyalty carrier: walker `pw`'s line `ability` performed from its
    typed host (CR 608.2c), with the targets it was activated with
    (`chosen`, else chosen now by `loyalty_targets`). None when the carrier
    does not take the line (the legacy kind's resolver runs); else whether
    anything was performed."""
    if not er.dispatch_enabled():
        return None
    host = loyalty_host(pw, ability)
    family = loyalty_family(ability, host)
    if family is None:
        return None
    if chosen is None:
        chosen = trigger_targets(game, pw, controller, host)
        if chosen is None:
            return False
    _audit_loyalty_targets(game, pw, controller, host, chosen)
    return bool(er.resolve_ability(game, er.handle_of(pw), controller, host,
                                   tuple(chosen), family=family,
                                   source_object=pw))


def _audit_loyalty_targets(game: Any, pw: Any, controller: int, host: Any,
                           chosen: Sequence[Any]) -> None:
    """Rules audit (CR 602.2b, 115.1): each target a dispatched loyalty
    line was activated with is one its slot's requirement admits.
    Observation only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    for req, picked in zip(host.targets, chosen):
        for v in picked:
            target = v if isinstance(v, int) and not isinstance(v, bool) \
                else game.get_card_by_id(getattr(v, "instance_id", None))
            audit_loyalty_target(game, pw, controller, req, target)


def audit_loyalty_target(game: Any, pw: Any, controller: int, req: Any,
                         target: Any) -> None:
    """Rules audit (CR 602.2b, 115.1), restated from the requirement's raw
    fields: a player (an index) is a target only for a slot naming players
    ("any target" names them too), and a permanent only for a slot naming
    one of its card types, a permanent, or any target. Read by the carrier
    and by the legacy damage resolver. Observation only."""
    from . import rules_audit
    if not rules_audit.enabled() or req is None or target is None:
        return
    types = set(req.types or ())
    if isinstance(target, int) and not isinstance(target, bool):
        ok = bool(types & {"any", "player", "opponent"})
        what = f"player {target}"
    else:
        names = {t.name.lower() for t in target.template.card_types}
        if getattr(target, "effective_is_creature", False):
            names.add("creature")
        if getattr(target, "effective_is_planeswalker", False):
            names.add("planeswalker")
        ok = bool(types & ({"any", "permanent"} | names))
        what = target.name
    rules_audit.check("602.2b/loyalty_target", ok,
                      f"{pw.name}'s line targets {what}, which "
                      f"{sorted(types)} does not admit", game=game)


# The families whose hosts the enter-trigger carrier takes: card flow,
# tokens and counters (unit ET: "create <N> <token>" through the token owner
# with its typed spec, CR 111.3), and edicts naming a player set ("each
# player sacrifices a creature of their choice", unit ED-T, CR 701.21a: no
# slot to bind). An enter trigger whose target another
# family resolves (damage, removal) is still chosen by its legacy resolver,
# which picks on entry; the dispatcher's unbound slot would go to that
# owner's rule (A36), not to the same object. `_slots_picked_by_executors`
# keeps a tokens-and-counters host with a slot its executor does not pick.
ETB_FAMILIES = ("card_flow", "tokens_counters", "edict")


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
    at all). An enter event names no defending player (CR 506.2), so a host
    naming one is not taken. A modal host is taken as `_takes` takes one,
    each of its modes held to the same checks. One owner, read by the
    carrier and the closure."""
    from .effect_spec import EventHint, HostKind
    from .effect_views import names_defending_player
    hosts = [h for h in face_hosts
             if h.kind is HostKind.TRIGGERED and h.trigger is not None
             and EventHint.SELF_ENTERS in h.trigger.event_hints]
    if not hosts:
        return None
    plan = []
    for h in hosts:
        parts = mode_hosts(h) if h.modes else (h,)
        family = None if any(names_defending_player(x)
                             or not _slots_picked_by_executors(x)
                             for x in parts) else next(
            (f for f in ETB_FAMILIES if _takes(h, f)), None)
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
    picks there (CR 603.3d, A35); a modal host's modes are chosen then
    (`choose_trigger_modes`, CR 603.3c). Returns whether any host
    performed anything."""
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
        parts = choose_trigger_modes(game, card, controller, h) \
            if h.modes else (h,)
        for part in parts:
            performed |= bool(er.resolve_ability(
                game, er.handle_of(card), controller, part,
                tuple(() for _ in part.targets), family=family,
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
    shape and executable (`_takes`: a modal host when each of its
    modes is); None otherwise."""
    if h.trigger is None or not _takes(h, DRAW_FAMILY):
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


# ── The combat-damage carrier (CR 510.2, 603.2, 603.3d) ────────────────

# The strict shape the combat-damage carrier takes (`effect_views.
# strict_combat_damage_trigger`, A38) and the family label its
# resolutions carry.
COMBAT_DAMAGE_FAMILY = "combat_damage_trigger"


def combat_damage_matches(event: Any, controller: int, damaged: int,
                          to_planeswalker: bool = False) -> bool:
    """Does combat damage the source dealt to player `damaged` -- or, with
    `to_planeswalker`, to a planeswalker that player controls -- trigger a
    head typed `event` (`effect_spec.CombatDamageEvent`, dealer "~") on a
    source `controller` controls? "A player" and "a player or battle"
    name any player; "an opponent" one of the controller's opponents; "a
    player or planeswalker" a planeswalker too."""
    if to_planeswalker:
        return event.recipient == "player_or_planeswalker"
    if event.recipient == "opponent":
        return damaged != controller
    return event.recipient in ("player", "player_or_planeswalker",
                               "player_or_battle")


def _combat_damage_family(h: Any) -> Optional[str]:
    """`COMBAT_DAMAGE_FAMILY` when the combat-damage-triggered host `h` is
    in its strict shape and executable (`_takes`: a modal host when each
    of its modes is); None otherwise."""
    if h.trigger is None or not _takes(h, COMBAT_DAMAGE_FAMILY):
        return None
    return COMBAT_DAMAGE_FAMILY


def combat_damage_plan(face_hosts: Iterable[Any]) -> Optional[list]:
    """The combat-damage carrier's plan for one face: ``[(host, family)]``
    for every host whose head is a combat-damage event, when each is
    takeable (`_combat_damage_family`); None when the face has no such
    host or any one is not (a card is taken whole or not at all). One
    owner, read by the carrier and the closure."""
    from .effect_spec import EventHint, HostKind
    hosts = [h for h in face_hosts
             if h.kind is HostKind.TRIGGERED and h.trigger is not None
             and EventHint.COMBAT_DAMAGE_TO_PLAYER in h.trigger.event_hints]
    if not hosts:
        return None
    plan = []
    for h in hosts:
        family = _combat_damage_family(h)
        if family is None:
            return None
        plan.append((h, family))
    return plan


def dispatch_combat_damage_triggers(game: Any, dealer: Any, damaged: int, *,
                                    to_planeswalker: bool = False
                                    ) -> Optional[bool]:
    """The combat-damage carrier (CR 510.2, 603.2): `dealer` dealt combat
    damage to player `damaged` (or to a planeswalker that player controls).
    Each of the dealer's combat-damage-triggered abilities whose typed head
    names that damage resolves through the dispatcher -- "that player" is
    the damaged player -- its targets picked as it is put on the stack
    (`trigger_targets`). None when the carrier does not take the dealer's
    card (no such host, or one it cannot take: a card is taken whole, and
    the rules audit records one it cannot, `603.2/
    combat_damage_trigger_unresolved`); else whether any host performed
    anything."""
    if not er.dispatch_enabled():
        return None
    hosts = _face_hosts(dealer)
    plan = combat_damage_plan(hosts)
    if plan is None:
        _audit_unresolved_combat_damage_trigger(game, dealer, hosts)
        return None
    performed = False
    controller = dealer.controller
    event = er.TriggerEvent(player=damaged)
    for h, family in plan:
        if not combat_damage_matches(h.trigger.combat_damage, controller,
                                     damaged, to_planeswalker):
            continue
        for part, chosen in put_on_stack(game, dealer, controller, h):
            performed |= bool(er.resolve_ability(
                game, er.handle_of(dealer), controller, part, chosen,
                family=family, event=event, source_object=dealer))
            if game.game_over:
                return performed
    return performed


def _audit_unresolved_combat_damage_trigger(game: Any, src: Any,
                                            hosts: Iterable[Any]) -> None:
    """Rules audit, a census once per card: `src` dealt combat damage and
    has a combat-damage-triggered head, but no carrier resolves the card's
    combat-damage triggers -- the trigger silently does nothing (CR
    603.2)."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    from .effect_spec import EventHint, HostKind
    for h in hosts:
        if h.kind is HostKind.TRIGGERED and h.trigger is not None and \
                EventHint.COMBAT_DAMAGE_TO_PLAYER in h.trigger.event_hints:
            rules_audit.census("603.2/combat_damage_trigger_unresolved",
                               src.name, detail=h.trigger.raw, game=game)
            return


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
    chosen = []
    for k, req in enumerate(host.targets):
        spec = slot_spec(host, k)
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
        _audit_another_target(game, source, req, picked)
    return tuple(chosen)


def _audit_another_target(game: Any, source: Any, req: Any,
                          picked: Sequence[Any]) -> None:
    """CR 115.1, 601.2c, restated from the chosen objects' instance ids: a
    target printed as "another" on its ability's first requirement is never
    the ability's own source. Observation only."""
    from . import rules_audit
    if not rules_audit.enabled() or not getattr(req, "excludes_source", False):
        return
    sid = getattr(source, "instance_id", None)
    rules_audit.check("115.1/another_target_not_source",
                      all(getattr(v, "instance_id", None) != sid
                          for v in picked if not isinstance(v, int)),
                      f"{getattr(source, 'name', '?')} targeted itself as "
                      f"{req.raw_phrase!r}", game=game)


def names_slot(s: Any, k: int) -> bool:
    """The spec targets slot `k`, or its acting player is the player
    chosen for slot `k` ("target player creates ...", "target player
    sacrifices ...")."""
    from .effect_spec import CardFilter, Ref, RefKind
    a = s.actor
    f = s.filter
    return s.target_slot == k or (isinstance(a, Ref)
                                  and a.kind is RefKind.TARGET
                                  and a.index == k) or (
        # "each creature target player controls": the group of the player
        # chosen for slot k
        isinstance(f, CardFilter) and f.controller == Ref(RefKind.TARGET, k))


def slot_spec(host: Any, k: int) -> Optional[Any]:
    """The first spec of `host` that names slot `k` (`names_slot`): what
    the target chosen for it is chosen for."""
    return next((s for s in iter_specs(host.specs) if names_slot(s, k)),
                None)


def _face_hosts(card: Any) -> tuple:
    faces = card.template.effects.faces
    if not faces:
        return ()
    face = 1 if getattr(card, "is_transformed", False) and len(faces) > 1 \
        else 0
    return faces[face]


def dispatch_draw_triggers(game: Any, drawer: int) -> bool:
    """The draw carrier (CR 603.2): each permanent's draw-triggered
    abilities whose typed head names this draw (`draw_matches`), resolved
    through the dispatcher with the drawer as the trigger event's player
    ("that player") and targets picked as they are put on the stack
    (`trigger_targets`). In stack order: the non-active player's triggers
    are put on the stack last and resolve first (CR 603.3b). A card whose
    draw triggers the carrier cannot take resolves none of them; the
    rules audit records it (`603.2/draw_trigger_unresolved`). Returns
    whether any host performed anything."""
    if not er.dispatch_enabled():
        return False
    performed = False
    ap = game.active_player
    order = [p for p in range(len(game.players)) if p != ap] + [ap]
    event = er.TriggerEvent(player=drawer)
    for idx in order:
        for src in list(game.players[idx].battlefield):
            template = getattr(src, "template", None)
            if template is None or getattr(template, "is_loyalty_clause",
                                           False):
                continue
            hosts = _face_hosts(src)
            plan = draw_plan(hosts)
            controller = src.controller
            if plan is None:
                _audit_unresolved_draw_trigger(game, src, hosts, drawer)
                continue
            for h, family in plan:
                if not draw_matches(game, h.trigger.draw, controller, drawer):
                    continue
                for part, chosen in put_on_stack(game, src, controller, h):
                    performed |= bool(er.resolve_ability(
                        game, er.handle_of(src), controller, part, chosen,
                        family=family, event=event, source_object=src))
                    if game.game_over:
                        return performed
    return performed


def _audit_unresolved_draw_trigger(game: Any, src: Any, hosts: Iterable[Any],
                                   drawer: int) -> None:
    """Rules audit, a census once per card: a draw-triggered head of `src`
    names this draw (or is a draw head the grammar could not type), but
    no carrier resolves the card's draw triggers -- the trigger silently
    does nothing (CR 603.2)."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    from .effect_spec import EventHint, HostKind
    for h in hosts:
        if h.kind is not HostKind.TRIGGERED or h.trigger is None \
                or EventHint.DRAW not in h.trigger.event_hints:
            continue
        d = h.trigger.draw
        if d is None or draw_matches(game, d, src.controller, drawer):
            rules_audit.census("603.2/draw_trigger_unresolved", src.name,
                               detail=h.trigger.raw, game=game)
            return


# ── The cast-trigger carrier (unit CT, CR 601.2i, 603.2) ───────────────

# The strict shape the cast carrier takes (`effect_views.
# strict_cast_trigger`, A38) and the family label its resolutions carry.
CAST_FAMILY = "cast_trigger"


def cast_matches(event: Any, controller: int, caster: int,
                 spell: Any) -> bool:
    """Does `caster` casting `spell` trigger a head typed `event`
    (`effect_spec.CastEvent`) on a permanent `controller` controls? Who
    casts, relative to the controller, and what kind of spell, read from
    the spell as it is cast (CR 601.2i)."""
    from .cards import CardType
    if event.caster == "you" and caster != controller:
        return False
    if event.caster == "opponent" and caster == controller:
        return False
    t = spell.template
    kind = event.spell
    if kind == "any":
        return True
    if kind == "noncreature":
        return not t.is_creature
    if kind == "creature":
        return bool(t.is_creature)
    if kind == "instant_or_sorcery":
        return bool(t.is_instant or t.is_sorcery)
    if kind == "artifact":
        return CardType.ARTIFACT in t.card_types
    if kind == "enchantment":
        return CardType.ENCHANTMENT in t.card_types
    colors = getattr(spell, "colors", None) or ()
    if kind == "colorless":
        return not colors
    if kind == "multicolored":
        return len(colors) >= 2
    return False


def _cast_family(h: Any) -> Optional[str]:
    """`CAST_FAMILY` when the cast-triggered host `h` is in its strict
    shape and executable (`_takes`: a modal host when each of its
    modes is); None otherwise."""
    if h.trigger is None or not _takes(h, CAST_FAMILY):
        return None
    return CAST_FAMILY


def cast_plan(face_hosts: Iterable[Any]) -> Optional[list]:
    """The cast carrier's plan for one face: ``[(host, family)]`` for every
    host whose head is a SPELL_CAST event when each is takeable
    (`_cast_family`); None when the face has no such host or any one is not
    (a card is taken whole or not at all, so no trigger of it resolves
    twice or not at all). One owner, read by the carrier, the legacy cast
    resolver and the closure."""
    from .effect_spec import EventHint, HostKind
    hosts = [h for h in face_hosts
             if h.kind is HostKind.TRIGGERED and h.trigger is not None
             and EventHint.SPELL_CAST in h.trigger.event_hints]
    if not hosts:
        return None
    plan = []
    for h in hosts:
        family = _cast_family(h)
        if family is None:
            return None
        plan.append((h, family))
    return plan


def dispatch_cast_triggers(game: Any, caster: int, spell: Any) -> set:
    """The cast carrier (CR 601.2i, 603.2, 603.3b): each permanent's
    cast-triggered abilities whose typed head names this cast
    (`cast_matches`), with the caster as the trigger event's player ("that
    player"). All of them trigger before any resolves, each taking its
    targets as it is put on the stack (`trigger_targets`, CR 603.3d); they
    resolve through the dispatcher in stack order -- the non-active
    player's first (CR 603.3b) -- with state-based actions checked before
    the next one resolves. Returns the instance ids of the permanents whose
    cast triggers the carrier took, performed or not, so the legacy cast
    resolver skips them; the rules audit records a matching head no carrier
    takes (`603.2/cast_trigger_unresolved`)."""
    taken: set = set()
    if not er.dispatch_enabled():
        return taken
    event = er.TriggerEvent(player=caster)
    triggered = []
    for src, hosts, plan in _cast_trigger_plans(game):
        if plan is None:
            _audit_unresolved_cast_trigger(game, src, hosts, caster, spell)
            continue
        taken.add(src.instance_id)
        controller = src.controller
        for h, family in plan:
            fires = cast_matches(h.trigger.cast, controller, caster, spell)
            _audit_cast_trigger_fires(game, src, controller, h, fires,
                                      caster, spell)
            if not fires:
                continue
            for part, chosen in put_on_stack(game, src, controller, h):
                triggered.append((src, er.handle_of(src), controller, part,
                                  family, chosen))
    for src, handle, controller, h, family, chosen in triggered:
        er.resolve_ability(game, handle, controller, h, chosen, family=family,
                           event=event, source_object=src)
        if game.game_over:
            break
        # CR 117.3b, 704.3: state-based actions are checked before the next
        # one resolves.
        game.check_state_based_actions()
        if game.game_over:
            break
    return taken


def _cast_trigger_plans(game: Any):
    """Each battlefield permanent with its face's hosts and its cast plan
    (`cast_plan`, None when the carrier does not take it), the non-active
    player's permanents first (CR 603.3b). The one selection the carrier
    and `performed_cast_triggers` read."""
    ap = game.active_player
    order = [p for p in range(len(game.players)) if p != ap] + [ap]
    for idx in order:
        for src in list(game.players[idx].battlefield):
            template = getattr(src, "template", None)
            if template is None or getattr(template, "is_loyalty_clause",
                                           False):
                continue
            hosts = _face_hosts(src)
            yield src, hosts, cast_plan(hosts)


def cast_taken(permanents: Iterable[Any]) -> frozenset:
    """The instance ids of `permanents` whose cast triggers the cast carrier
    takes (their face's `cast_plan`): no legacy cast pass performs their
    printed cast text. Empty while dispatch is off."""
    if not er.dispatch_enabled():
        return frozenset()
    return frozenset(p.instance_id for p in permanents
                     if cast_plan(_face_hosts(p)) is not None)


def performed_cast_triggers(game: Any, caster: int, spell: Any) -> list:
    """The (permanent, host) cast-triggered abilities the cast carrier
    performs when `caster` casts `spell` (`dispatch_cast_triggers`), read
    without resolving them -- what the AI asks of a cast before making it
    (a ritual whose cast triggers a draw digs as it is cast)."""
    if not er.dispatch_enabled():
        return []
    return [(src, h) for src, _hosts, plan in _cast_trigger_plans(game)
            if plan is not None
            for h, _family in plan
            if cast_matches(h.trigger.cast, src.controller, caster, spell)]


def _counter_owner_performs(src: Any, host: Any) -> bool:
    """Is `host` the cast-triggered counter its own owner performs from the
    template's typed `cast_counter` (unit LK, CR 701.6)?"""
    from .effect_spec import Verb, iter_specs
    return (getattr(src.template, "cast_counter", None) is not None
            and any(s.verb is Verb.COUNTER for s in iter_specs(host.specs)))


def _audit_unresolved_cast_trigger(game: Any, src: Any, hosts: Iterable[Any],
                                   caster: int, spell: Any) -> None:
    """Rules audit, a census once per card: a typed cast-triggered head of
    `src` names this cast, but neither the carrier nor the cast-triggered
    counter's owner performs it (its legacy cast branches may still resolve
    it, CR 603.2)."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    from .effect_spec import EventHint, HostKind
    for h in hosts:
        if h.kind is not HostKind.TRIGGERED or h.trigger is None \
                or EventHint.SPELL_CAST not in h.trigger.event_hints \
                or _counter_owner_performs(src, h):
            continue
        c = h.trigger.cast
        if c is not None and cast_matches(c, src.controller, caster, spell):
            rules_audit.census("603.2/cast_trigger_unresolved", src.name,
                               detail=h.trigger.raw, game=game)
            return


def _audit_cast_trigger_fires(game: Any, src: Any, controller: int,
                              host: Any, fires: bool, caster: int,
                              spell: Any) -> None:
    """CR 601.2i, 603.2, restated from the spell's raw fields: a cast head
    triggers for a spell exactly when its caster and kind name it -- "you"
    the ability's controller, "an opponent" another player; the kind read
    from the spell's printed card types and its colours (an effect's colour
    over the printed one) -- once for that spell, and never for another.
    Observation only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    from .cards import CardType
    ev = host.trigger.cast
    types = set(getattr(spell.template, "card_types", ()) or ())
    colors = getattr(spell, "cem_colors_set", None)
    if colors is None:
        colors = getattr(spell.template, "colors", ()) or ()
    who = {"you": caster == controller, "opponent": caster != controller,
           "player": True}.get(ev.caster, False)
    kind = {
        "any": True,
        "noncreature": CardType.CREATURE not in types,
        "creature": CardType.CREATURE in types,
        "instant_or_sorcery": bool(types & {CardType.INSTANT,
                                            CardType.SORCERY}),
        "artifact": CardType.ARTIFACT in types,
        "enchantment": CardType.ENCHANTMENT in types,
        "colorless": not colors,
        "multicolored": len(colors) > 1,      # CR 105.4: two or more colours
    }.get(ev.spell, False)
    due = who and kind
    rules_audit.check("603.2/cast_trigger_once", fires == due,
                      f"{src.name}: {host.trigger.raw!r} "
                      f"{'triggered' if fires else 'did not trigger'} for "
                      f"{getattr(spell, 'name', '?')}", game=game)


# ── The beginning-of-step carrier (unit BT, CR 603.2, 603.3b, 603.4) ──

# The strict shape the step carrier takes (`effect_views.
# strict_step_trigger`, A38) and the family label its resolutions carry.
STEP_FAMILY = "step_trigger"


def step_matches(event: Any, step: str, controller: int, active: int) -> bool:
    """Does step `step` of `active`'s turn trigger a head typed `event`
    (`effect_spec.StepEvent`) on a permanent `controller` controls? "Your"
    names the controller's own turn, "each opponent's" an opponent's turn,
    "each" every player's turn (CR 603.2)."""
    if event.step != step:
        return False
    if event.whose == "you":
        return active == controller
    if event.whose == "opponent":
        return active != controller
    return True


def _step_family(h: Any) -> Optional[str]:
    """`STEP_FAMILY` when the beginning-of-step host `h` is in its strict
    shape and executable (`_takes`: a modal host when each of its
    modes is); None otherwise."""
    if h.trigger is None or not _takes(h, STEP_FAMILY):
        return None
    return STEP_FAMILY


def _step_hosts(face_hosts: Iterable[Any]) -> tuple:
    from .effect_spec import EventHint, HostKind
    return tuple(h for h in face_hosts
                 if h.kind is HostKind.TRIGGERED and h.trigger is not None
                 and EventHint.BEGINNING_OF in h.trigger.event_hints)


def step_plan(face_hosts: Iterable[Any]) -> Optional[list]:
    """The step carrier's plan for one face: ``[(host, family)]`` for every
    host whose head is a BEGINNING_OF event, when each is takeable
    (`_step_family`); None when the face has no such host or any one is
    not (a card is taken whole or not at all, so no trigger of it resolves
    twice or not at all). One owner, read by the carrier, the closure and
    the harness."""
    hosts = _step_hosts(face_hosts)
    if not hosts:
        return None
    plan = []
    for h in hosts:
        family = _step_family(h)
        if family is None:
            return None
        plan.append((h, family))
    return plan


def _step_faces(template: Any) -> tuple:
    """Per face of `template`: ``(plan, hosts)`` -- the step carrier's plan
    (`step_plan`) and the face's BEGINNING_OF hosts. Read once per parse:
    memoised on the template against its parsed-effects object, in the
    template's `_effects*` memo family, since the carrier asks it of every
    permanent at every step."""
    effects = getattr(template, "_effects", None)
    if effects is None:
        effects = getattr(template, "effects", None)
    hit = getattr(template, "_effects_step_faces", None)
    if hit is not None and hit[0] is effects:
        return hit[1]
    faces = []
    for face in getattr(effects, "faces", None) or ():
        hosts = _step_hosts(face)
        faces.append((step_plan(face) if hosts else None, hosts))
    out = tuple(faces)
    try:
        template._effects_step_faces = (effects, out)
    except AttributeError:       # a stand-in that takes no attribute
        pass
    return out


def dispatch_step_triggers(game: Any, step: str) -> set:
    """The beginning-of-step carrier (CR 603.2, 603.3b, 603.4): as step
    `step` of the active player's turn begins, each permanent's
    beginning-of-step abilities whose typed head names that step and turn
    (`step_matches`) trigger once. An intervening "if" is checked as the
    ability triggers and again as it resolves; targets are chosen as it is
    put on the stack (`trigger_targets`). All of them trigger before any
    resolves -- a permanent that enters meanwhile does not trigger -- and
    they resolve in stack order: the active player's are put on the stack
    first, so the non-active player's resolve first (CR 603.3b). "That
    player" is the active player. A card whose step triggers the carrier
    cannot take resolves none of them; the rules audit records it
    (`603.2/step_trigger_unresolved`). Returns the instance ids of the
    permanents whose step triggers the carrier took."""
    taken: set = set()
    if not er.dispatch_enabled():
        return taken
    active = game.active_player
    event = er.TriggerEvent(player=active)
    _audit_step_once(game, step)
    triggered = []
    order = [active] + [p for p in range(len(game.players)) if p != active]
    for idx in order:
        for src in list(game.players[idx].battlefield):
            template = getattr(src, "template", None)
            if template is None or getattr(template, "is_loyalty_clause",
                                           False):
                continue
            faces = _step_faces(template)
            if not faces:
                continue
            face = 1 if getattr(src, "is_transformed", False) \
                and len(faces) > 1 else 0
            plan, hosts = faces[face]
            if not hosts:
                continue
            controller = src.controller
            if plan is None:
                _audit_unresolved_step_trigger(game, src, hosts, step,
                                               controller, active)
                continue
            taken.add(src.instance_id)
            for h, family in plan:
                if not step_matches(h.trigger.step_event, step, controller,
                                    active):
                    continue
                holds = intervening_if_holds_now(game, src, controller, h,
                                                 event=event)
                _audit_step_intervening_if(game, src, controller, h, holds,
                                           "as it triggers")
                if not holds:
                    continue
                for part, chosen in put_on_stack(game, src, controller, h):
                    triggered.append((src, er.handle_of(src), controller,
                                      part, family, chosen))
    # CR 603.3b: the active player's triggers are put on the stack first,
    # so the non-active player's resolve first.
    triggered.sort(key=lambda t: t[2] == active)
    for src, handle, controller, h, family, chosen in triggered:
        restated = _restated_life_history(
            game, controller, h.trigger.intervening_if)
        performed = er.resolve_ability(game, handle, controller, h, chosen,
                                       family=family, event=event,
                                       source_object=src)
        if restated is False:
            _audit_step_intervening_if(game, src, controller, h,
                                       not performed, "as it resolves")
        _audit_step_named_turn(game, src, controller, h, step, active)
        if game.game_over:
            break
        # CR 117.3b, 704.3: after each resolves, a player would receive
        # priority -- state-based actions are checked before the next one
        # resolves (a creature dealt lethal damage is gone by then).
        game.check_state_based_actions()
        if game.game_over:
            break
    return taken


def _audit_unresolved_step_trigger(game: Any, src: Any, hosts: Iterable[Any],
                                   step: str, controller: int,
                                   active: int) -> None:
    """Rules audit, a census once per card: a beginning-of-step head of
    `src` names this step and turn (or is a step head the grammar could not
    type), but no carrier resolves the card's step triggers -- the trigger
    silently does nothing (CR 603.2)."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    for h in hosts:
        se = h.trigger.step_event
        if se is None or step_matches(se, step, controller, active):
            rules_audit.census("603.2/step_trigger_unresolved", src.name,
                               detail=h.trigger.raw, game=game)
            return


def _restated_life_history(game: Any, controller: int,
                           cond: Any) -> Optional[bool]:
    """A HISTORY intervening "if" over the turn's life tallies ("if you
    gained life this turn"), restated from the raw condition fields and the
    players' raw tallies -- never through the evaluator; None for any other
    condition."""
    from .effect_spec import ConditionKind
    ops = {">=": operator.ge, "<=": operator.le, "==": operator.eq,
           ">": operator.gt, "<": operator.lt}
    tallies = {"life_gained": "life_gained_this_turn",
               "life_lost": "life_lost_this_turn"}
    if cond is None or cond.kind is not ConditionKind.HISTORY \
            or cond.pred not in tallies or cond.op not in ops:
        return None
    n = getattr(cond.n, "n", None)
    payer = getattr(getattr(cond, "payer", None), "kind", None)
    players = {"PLAYER": [controller],
               "OPPONENTS": [i for i in range(len(game.players))
                             if i != controller],
               "ALL_PLAYERS": list(range(len(game.players)))
               }.get(getattr(payer, "name", None))
    if not isinstance(n, int) or players is None:
        return None
    return any(ops[cond.op](getattr(game.players[i], tallies[cond.pred]), n)
               for i in players)


def _audit_step_intervening_if(game: Any, src: Any, controller: int,
                               host: Any, collected: bool, when: str) -> None:
    """CR 603.4, restated from the raw turn tallies: a step trigger with an
    intervening "if" over the turn's life is put on the stack exactly when
    the condition holds as the step begins, and does nothing as it
    resolves when the condition no longer holds. Observation only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    restated = _restated_life_history(game, controller,
                                      host.trigger.intervening_if)
    if restated is None:
        return
    rules_audit.check("603.4/step_trigger_intervening_if",
                      collected == restated,
                      f"{src.name}: {host.trigger.intervening_if.raw!r} is "
                      f"{restated} {when}", game=game)


# The (turn, step) pairs the step carrier was asked about, per game, while
# the rules audit is on (observation only: never read by the engine).
_STEPS_DISPATCHED: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _audit_step_once(game: Any, step: str) -> None:
    """CR 603.2: each step of each turn begins once, so its triggers
    trigger once. Restated from the game's turn number and the step."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    key = (game.turn_number, step)
    seen = _STEPS_DISPATCHED.setdefault(game, set())
    rules_audit.check("603.2/step_trigger_once", key not in seen,
                      f"step {step} of turn {game.turn_number} dispatched "
                      f"twice", game=game)
    seen.add(key)


def _audit_step_named_turn(game: Any, src: Any, controller: int, host: Any,
                           step: str, active: int) -> None:
    """CR 603.2, restated from the raw head: a performed step trigger's
    head names this step, on this turn -- the controller's own for "your",
    an opponent's for "each opponent's", any for "each"."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    se = host.trigger.step_event
    own = active == controller
    named = (se is not None and se.step == step
             and {"you": own, "opponent": not own}.get(se.whose, True))
    rules_audit.check("603.2/step_trigger_once", named,
                      f"{src.name}: {host.trigger.raw!r} performed at "
                      f"{step} of player {active + 1}'s turn", game=game)


# ── The attack carrier (CR 508.1m, 508.2, 603.2, 603.3b, 603.3d, 603.4) ─

# The strict shape the attack carrier takes (`effect_views.
# strict_attack_trigger`) and the family label its resolutions carry.
ATTACK_FAMILY = "attack_trigger"


def _attack_hinted(h: Any) -> bool:
    from .effect_spec import EventHint, HostKind
    return (h.kind is HostKind.TRIGGERED and h.trigger is not None
            and (EventHint.SELF_ATTACKS in h.trigger.event_hints
                 or EventHint.ATTACKS_OTHER in h.trigger.event_hints))


def attack_matches(game: Any, event: Any, src: Any, controller: int,
                   attacker: Any) -> bool:
    """Is `attacker`, as it is declared, an attacker the head typed `event`
    (`effect_spec.AttackEvent`) on permanent `src` names, `src` controlled
    by `controller`? "~" is `src` itself; "equipped" / "enchanted creature"
    the creature `src` is attached to (`PermanentEffects.attached_host`);
    "a creature you control" / "you attack" a creature its controller
    attacks with (CR 508.1m, 603.2)."""
    if event.attacker == "self":
        return attacker is src
    if event.attacker in ("equipped", "enchanted"):
        from .permanent_effects import PermanentEffects
        return PermanentEffects.attached_host(game, src) is attacker
    return event.attacker == "yours" and attacker.controller == controller


def attack_count(game: Any, event: Any, src: Any, controller: int,
                 attackers: Sequence[Any]) -> int:
    """How many times one declaration of `attackers` triggers a head typed
    `event` on `src`: once for each attacker it names, or -- a head of the
    declaration ("whenever you attack", "one or more ... attack") -- once
    when it names any (CR 603.2c)."""
    n = sum(1 for a in attackers
            if attack_matches(game, event, src, controller, a))
    return min(n, 1) if event.per_combat else n


def _attack_family(h: Any) -> Optional[str]:
    """`ATTACK_FAMILY` when the attack-triggered host `h` is in its strict
    shape and executable (`_takes`: a modal host when each of its
    modes is); None otherwise."""
    if h.trigger is None or not _takes(h, ATTACK_FAMILY):
        return None
    return ATTACK_FAMILY


def attack_plan(face_hosts: Iterable[Any]) -> Optional[list]:
    """The attack carrier's plan for one face: ``[(host, family)]`` for
    every host whose head is an attack event, typed or not, when each is
    takeable (`_attack_family`); None when the face has no such host or any
    one is not (a card is taken whole or not at all, so no attack trigger of
    it resolves twice or not at all). One owner, read by the carrier, the
    closure and the harness."""
    hosts = [h for h in face_hosts if _attack_hinted(h)]
    if not hosts:
        return None
    plan = []
    for h in hosts:
        family = _attack_family(h)
        if family is None:
            return None
        plan.append((h, family))
    return plan


def _attack_faces(template: Any) -> tuple:
    """Per face of `template`: ``(plan, hosts)`` -- the attack carrier's
    plan (`attack_plan`) and the face's attack-event hosts. Read once per
    parse: memoised on the template against its parsed-effects object, in
    the template's `_effects*` memo family, since the carrier asks it of
    every permanent at every declaration."""
    effects = getattr(template, "_effects", None)
    if effects is None:
        effects = getattr(template, "effects", None)
    hit = getattr(template, "_effects_attack_faces", None)
    if hit is not None and hit[0] is effects:
        return hit[1]
    faces = []
    for face in getattr(effects, "faces", None) or ():
        hosts = tuple(h for h in face if _attack_hinted(h))
        faces.append((attack_plan(face) if hosts else None, hosts))
    out = tuple(faces)
    try:
        template._effects_attack_faces = (effects, out)
    except AttributeError:       # a stand-in that takes no attribute
        pass
    return out


def _shown_attack_face(card: Any) -> tuple:
    """``(plan, hosts)`` of the face `card` shows (`_attack_faces`); empty
    hosts for a card with none."""
    template = getattr(card, "template", None)
    if template is None or getattr(template, "is_loyalty_clause", False):
        return None, ()
    faces = _attack_faces(template)
    if not faces:
        return None, ()
    face = 1 if getattr(card, "is_transformed", False) and len(faces) > 1 \
        else 0
    return faces[face]


def attack_taken(attackers: Sequence[Any]) -> frozenset:
    """The instance ids of the `attackers` whose attack abilities the attack
    carrier takes (the face each shows has a plan): the legacy attack
    resolver performs none of their printed attack text. Empty while
    dispatch is off."""
    if not er.dispatch_enabled():
        return frozenset()
    return frozenset(a.instance_id for a in attackers
                     if _shown_attack_face(a)[0] is not None)


def defending_player(game: Any, active: int) -> Optional[int]:
    """The player `active`'s attack is made against (CR 506.2): in a
    two-player game the one opponent, as the combat manager records it;
    None with any other number of players (nothing is bound to a guessed
    player)."""
    others = [p for p in range(len(game.players)) if p != active]
    return others[0] if len(others) == 1 else None


def _audit_defending_player(game: Any, player: Optional[int],
                            attackers: Sequence[Any]) -> None:
    """CR 506.2, 508.5, restated from the attackers: the defending player
    an attack trigger names is a player in the game who controls none of
    the attacking creatures (the attack is made against them). Observation
    only."""
    from . import rules_audit
    if not rules_audit.enabled() or player is None:
        return
    attacking = {a.controller for a in attackers}
    rules_audit.check("508.5/defending_player",
                      0 <= player < len(game.players)
                      and player not in attacking,
                      f"P{player + 1} named defending player of an attack "
                      f"by P{sorted(p + 1 for p in attacking)}", game=game)


def dispatch_attack_triggers(game: Any, attackers: Sequence[Any],
                             active: int) -> set:
    """The attack carrier (CR 508.1m, 508.2, 603.2, 603.3b, 603.4): once
    `active` has declared `attackers`, each permanent's attack abilities
    whose typed head names the declaration trigger -- once for each
    attacker the head names (`attack_matches`), or once for the declaration
    (`AttackEvent.per_combat`, CR 603.2c). An intervening "if" is checked as
    the ability triggers and again as it resolves; targets are chosen as it
    is put on the stack (`trigger_targets`). All of them trigger before any
    resolves, and they resolve in stack order: the active player's are put
    on the stack first, so the non-active player's resolve first (CR
    603.3b). A card whose attack triggers the carrier cannot take resolves
    none of them here; the rules audit records it (`603.2/
    attack_trigger_unresolved`). Returns the instance ids of the permanents
    whose attack triggers the carrier took."""
    taken: set = set()
    if not er.dispatch_enabled() or not attackers:
        return taken
    _audit_attack_once(game)
    # CR 506.2, 508.5: the attack event names its defending player.
    event = er.TriggerEvent(defending_player=defending_player(game, active))
    _audit_defending_player(game, event.defending_player, attackers)
    triggered = []
    order = [active] + [p for p in range(len(game.players)) if p != active]
    for idx in order:
        for src in list(game.players[idx].battlefield):
            plan, hosts = _shown_attack_face(src)
            if not hosts:
                continue
            controller = src.controller
            if plan is None:
                _audit_unresolved_attack_trigger(game, src, hosts, attackers)
                continue
            taken.add(src.instance_id)
            for h, family in plan:
                n = attack_count(game, h.trigger.attack, src, controller,
                                 attackers)
                _audit_attack_count(game, src, controller, h, n, attackers)
                for _ in range(n):
                    if not intervening_if_holds_now(game, src, controller, h,
                                                    event=event):
                        continue
                    for part, chosen in put_on_stack(game, src, controller,
                                                     h):
                        triggered.append((src, er.handle_of(src), controller,
                                          part, family, chosen))
    # CR 603.3b: the active player's triggers are put on the stack first,
    # so the non-active player's resolve first.
    triggered.sort(key=lambda t: t[2] == active)
    for src, handle, controller, h, family, chosen in triggered:
        er.resolve_ability(game, handle, controller, h, chosen, family=family,
                           event=event, source_object=src)
        if game.game_over:
            break
        # CR 117.3b, 704.3: state-based actions are checked before the next
        # one resolves.
        game.check_state_based_actions()
        if game.game_over:
            break
    return taken


def _audit_unresolved_attack_trigger(game: Any, src: Any,
                                     hosts: Iterable[Any],
                                     attackers: Sequence[Any]) -> None:
    """Rules audit, a census once per card: an attack head of `src` names
    this declaration -- a typed head it matches, or an untyped head of an
    attacker itself ("attacks or blocks", "attacks alone") -- but no carrier
    resolves the card's attack triggers: the trigger silently does nothing
    (CR 603.2)."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    attacking = any(a is src for a in attackers)
    for h in hosts:
        ev = h.trigger.attack
        if (ev is None and attacking) or (ev is not None and attack_count(
                game, ev, src, src.controller, attackers)):
            rules_audit.census("603.2/attack_trigger_unresolved", src.name,
                               detail=h.trigger.raw, game=game)
            return


# The turns whose declaration the attack carrier was asked about, per game,
# while the rules audit is on (observation only: never read by the engine).
_ATTACKS_DISPATCHED: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _audit_attack_once(game: Any) -> None:
    """CR 508.1m: attackers are declared once in a combat (the engine runs
    one combat a turn), so their attack triggers trigger once. Restated
    from the game's turn number."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    seen = _ATTACKS_DISPATCHED.setdefault(game, set())
    rules_audit.check("603.2/attack_trigger_once",
                      game.turn_number not in seen,
                      f"attackers of turn {game.turn_number} dispatched twice",
                      game=game)
    seen.add(game.turn_number)


def _audit_attack_count(game: Any, src: Any, controller: int, host: Any,
                        n: int, attackers: Sequence[Any]) -> None:
    """CR 603.2, 603.2c, restated from the raw combat fields: an attack head
    triggers once for each attacker it names -- the source itself, the
    creature carrying its Equipment tag or its Aura's `attached_to_id`, a
    creature its controller controls -- and a head of the declaration at
    most once. Observation only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    ev = host.trigger.attack
    tag = f"equipped_{src.instance_id}"

    def bearer(a):
        return (tag in getattr(a, "instance_tags", ())
                or a.instance_id == getattr(src, "attached_to_id", None))
    named = {
        "self": lambda a: a.instance_id == src.instance_id,
        "equipped": bearer,
        "enchanted": bearer,
        "yours": lambda a: a.controller == controller,
    }.get(ev.attacker)
    due = sum(1 for a in attackers if named is not None and named(a))
    if ev.per_combat:
        due = min(due, 1)
    rules_audit.check("603.2/attack_trigger_once", n == due,
                      f"{src.name}: {host.trigger.raw!r} triggered {n} "
                      f"time(s), {due} due", game=game)


def places_legacy_targets(host: Any, face_hosts: Iterable[Any] = ()) -> bool:
    """Can a carrier place its legacy target list on `host`'s slots
    without reading text? Yes for a host with no slot (its list is empty)
    or with one slot whose printed span is known, when no other host of
    `face_hosts` chose targets into the same list. The gate-parity closure
    asks the same question, so a host the switch cannot place is never
    counted on the new path."""
    if not host.targets:
        return True
    if any(h.targets for h in face_hosts if h is not host):
        return False
    if len(host.targets) == 1:
        return _slot_span(host) is not None
    return _slot_spans(host) is not None


def _slot_spans(host: Any) -> Optional[list]:
    """The printed spans of a several-slot host's slots, in slot order,
    when each slot is one required target (CR 601.2c: the cast announces
    exactly one object or player for it) whose spec's span lies inside the
    host's text, and no two spans overlap: the cast's list then holds one
    entry per slot in printed order. None otherwise."""
    spans = []
    for k, req in enumerate(host.targets):
        if req.count_min != 1 or req.count_max != 1 or req.is_optional \
                or req.mode_group is not None:
            return None
        s = slot_spec(host, k)
        span = s.span if s is not None else None
        if span is None or not 0 <= span[0] < span[1] <= len(host.text or ""):
            return None
        spans.append(span)
    ordered = sorted(spans)
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])) \
            or ordered != spans:
        return None
    return spans


def _admits(game: Any, controller: int, req: Any, tid: Any) -> bool:
    """Does the slot requirement `req` admit the cast's target `tid` (a
    player marker or an object id)?"""
    from .constants import PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF
    from .target_solver import slot_admits_player, target_admitted
    if tid in (PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF):
        player = controller if tid == PLAYER_TARGET_SELF else 1 - controller
        return slot_admits_player(req, player, controller)
    return target_admitted(game, controller, [req], tid)


def _admits_kind(game: Any, controller: int, req: Any, tid: Any) -> bool:
    """Does the slot take `tid` by kind, whatever its legality now? A
    target that became illegal still sits on the slot it was chosen for,
    unaffected when the spell resolves (CR 608.2b): a player marker by the
    players the slot names, an object by `target_solver.slot_admits_kind`."""
    from .constants import PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF
    from .target_solver import slot_admits_kind
    if tid in (PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF):
        return _admits(game, controller, req, tid)
    card = game.get_card_by_id(tid)
    return card is not None and slot_admits_kind(req, card)


def _slot_span(host: Any) -> Optional[tuple]:
    """The printed span of slot 0's spec, when it lies inside the host's
    text (an empty or out-of-text span places nothing)."""
    s0 = slot_spec(host, 0)
    span = s0.span if s0 is not None else None
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
    if len(host.targets) == 1:
        spans = [_slot_span(host)]
        positions = [spans[0][0]] * len(legacy)
    else:
        # one entry per slot (`_slot_spans`), each placed on the one slot
        # whose requirement admits it (a player marker on a player slot,
        # an object on the slot of its kind): the cast's list is in
        # announcement order, not printed order. An entry no slot admits
        # now became illegal (CR 608.2b): it goes to the one slot of its
        # kind, where it is not affected.
        spans = _slot_spans(host)
        if len(legacy) != len(spans):
            return None
        positions = []
        for tid in legacy:
            ks = [k for k, req in enumerate(host.targets)
                  if _admits(game, controller, req, tid)] or \
                 [k for k, req in enumerate(host.targets)
                  if _admits_kind(game, controller, req, tid)]
            if len(ks) != 1:
                return None
            positions.append(spans[ks[0]][0])
        if len(set(positions)) != len(positions):
            return None
    try:
        chosen = er.chosen_from_legacy(
            host, legacy, positions, slot_spans=spans,
            game=game, face=1 - controller, self_face=controller)
    except ValueError:                         # no slot holds the position
        return None
    if len(host.targets) > 1:
        _audit_slots_placed(game, host, chosen)
    return chosen


def _audit_slots_placed(game: Any, host: Any, chosen: Any) -> None:
    """CR 601.2c, restated from the printed types of each slot: a player
    sits only on a slot that names players, an object only on a slot that
    names one of its card types (or any permanent / card). Observation
    only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    bad = []
    for req, values in zip(host.targets, chosen):
        types = set(req.types)
        for v in values:
            if isinstance(v, int) and not isinstance(v, bool):
                ok = bool(types & {"player", "opponent", "any"})
            else:
                card = game.get_card_by_id(getattr(v, "instance_id", None))
                kinds = {t.value for t in card.effective_card_types} \
                    if card is not None else set()
                ok = card is not None and bool(
                    types & ({"permanent", "card", "any"} | kinds))
            if not ok:
                bad.append((sorted(types), v))
    rules_audit.check("601.2c/slot_placed", not bad,
                      f"misplaced targets {bad}", game=game)
