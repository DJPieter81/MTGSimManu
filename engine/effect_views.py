"""Field views over the clause grammar (design doc 2026-09-29, section 10 and
its per-field derivation table 10.1; E0, no caller).

Each legacy typed field the derivation table lists gets one
`FieldDerivation`: the field's value derived from `CardTemplate.effects`
(the typed spec model the grammar parses once per template), the family's
strict shape predicate the switched handler will test (A38), and -- where
legacy's domain is narrower than what the grammar types -- a
`_legacy_domain_*` mask that hides the growth so the field view equals
legacy (A39). Every other legacy field is named in `NON_EFFECT_FIELDS`
with the reason it is not effect data, so the completeness scan (A41)
accounts for every field of `CardTemplate`, `ActivatedAbility` and
`LoyaltyAbility`.

**Boundary.** The module reads specs and host text (`AbilityEffects.text`,
`EffectSpec.raw`, `TriggerHead.raw`, `TargetRequirement.raw_phrase`) and
nothing else: no oracle attribute is read here, and the printed text the
A40 views need comes from the grammar's own parse input
(`effect_grammar.template_inputs`) through `effect_grammar.printed_span`.
It is not in the runtime-parse ratchet's exclusions. Nothing in the engine
calls it in E0; the equivalence tool reads it and family commits switch
fields to it one at a time (section 10, migration protocol step 5).

**Legacy quirks.** A derivation reproduces the legacy value, quirks
included, so the tool can tell a SEMANTIC_FIX from an UNEXPLAINED
difference. Each reproduced quirk is a named module-level predicate
`_legacy_*`; each domain mask is a `_legacy_domain_*` predicate. Both are
counted by ratchet (c) of `tools/check_effect_parsers.py` (section 13), so
`LEGACY_PREDICATES` lists them.

**Tiers.** A: the field gets switched to the view in its family step; its
derivation aims at pool-wide equality after masks. B: a predicate the AI
reads; derived for the report. C: a payload behind a trigger head (stage
T). `partial` marks a derivation that covers only part of the legacy value
(compared on `compare` keys) or is known to diverge; the tool reports it,
it never switches.

**`host_for_override(template, text)`** is the static table the switched
handlers resolve an `oracle_override` through (section 11): the normalised
printed text of every MODE host, the kicked clause, the channel host and
the head-stripped body of every TRIGGERED host, keyed to its host. A text
two hosts print names none unless the trigger event narrows it. It is
built once per parsed `CardEffects` and a lookup never parses.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from itertools import combinations_with_replacement
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .effect_model import DurationKind, ModKind, SelectorKind
from .effect_spec import (AbilityEffects, AmountKind, CardEffects, Chooser,
                          ConditionKind, CounterSpec, EventHint, Granted,
                          HostKind, ManaSpec, Modification, RefKind, RefPart,
                          SubAbility, SubAbilityKind, TokenSpec, Verb,
                          iter_specs)

__all__ = ["FieldDerivation", "DERIVATIONS", "NON_EFFECT_FIELDS", "STRICT",
           "LEGACY_PREDICATES", "RUNTIME_CARRIERS", "SCOPES", "TIERS",
           "ESTEPS", "FAMILIES", "SCOPE_CARD", "SCOPE_MODE",
           "SCOPE_ACTIVATED", "SCOPE_LOYALTY", "kicked_clause",
           "channel_clause", "host_for_override", "override_key"]


# ── Record vocabulary ──────────────────────────────────────────────────

SCOPE_CARD = "card"            # a CardTemplate attribute
SCOPE_MODE = "mode"            # CardTemplate.modes[i] (key: mode index)
SCOPE_ACTIVATED = "activated"  # ActivatedAbility (key: its `index`)
SCOPE_LOYALTY = "loyalty"      # LoyaltyAbility (key: (face, slot))
SCOPES = frozenset({SCOPE_CARD, SCOPE_MODE, SCOPE_ACTIVATED, SCOPE_LOYALTY})
TIERS = frozenset({"A", "B", "C"})
# The family steps of section 14, plus T (the trigger-head stage, section 15).
ESTEPS = frozenset({"E1", "E2", "E3", "E4", "E5", "E6", "E7", "T"})

FAMILY_DAMAGE = "damage"
FAMILY_REMOVAL = "removal"
FAMILY_CARD_FLOW = "card_flow"
FAMILY_TOKENS_COUNTERS = "tokens_counters"
FAMILY_PUMP_RESTRICT = "pump_restrict"
FAMILY_STACK_MANA = "stack_mana"
FAMILY_TRIGGER = "trigger"
FAMILIES = frozenset({FAMILY_DAMAGE, FAMILY_REMOVAL, FAMILY_CARD_FLOW,
                      FAMILY_TOKENS_COUNTERS, FAMILY_PUMP_RESTRICT,
                      FAMILY_STACK_MANA, FAMILY_TRIGGER})

COMPARE_EQ = "eq"


@dataclass(frozen=True)
class FieldDerivation:
    """One legacy field and its view over `CardTemplate.effects`.

    `view(effects, key, template)` is the unmasked derivation; `derive`
    applies the `domain` mask (A39): outside legacy's domain the field
    view is the legacy default, and `masked` says so (MASKED_GROWTH).
    `template` is read only for printed-span views and type facts, never
    for oracle text. `legacy(template, key)` reads the legacy value the
    tool compares against. `strict` is the family's strict host shape
    (A38), the gate a switched handler tests before taking the new path.
    `compare` is ``"eq"`` or a tuple of dict keys a partial view covers."""
    field: str
    family: str
    scope: str
    tier: str
    estep: str
    view: Callable[..., Any]
    strict: Callable[[AbilityEffects], bool]
    legacy: Callable[..., Any]
    domain: Optional[Callable[..., bool]] = None
    compare: Any = COMPARE_EQ
    partial: bool = False
    default: Any = None
    note: str = ""

    def derive(self, effects: CardEffects, key: Any = None,
               template: Any = None) -> Any:
        if self.domain is not None and not self.domain(effects, key):
            return self.default
        return self.view(effects, key, template)

    def masked(self, effects: CardEffects, key: Any = None,
               template: Any = None) -> bool:
        """True when the mask hides a value the grammar types: the
        unmasked view differs from the masked one (MASKED_GROWTH)."""
        if self.domain is None or self.domain(effects, key):
            return False
        return self.view(effects, key, template) != self.default

    def keys(self, template: Any) -> Tuple[Any, ...]:
        """The keys this record compares on one template."""
        if self.scope == SCOPE_CARD:
            return (None,)
        if self.scope == SCOPE_MODE:
            return tuple(range(len(getattr(template, "modes", None) or ())))
        if self.scope == SCOPE_ACTIVATED:
            return tuple(a.index for a in
                         getattr(template, "activated_abilities", None) or ())
        out = []
        for face, attr in ((0, "loyalty_abilities"),
                           (1, "back_face_loyalty_abilities")):
            for slot in (getattr(template, attr, None) or {}):
                out.append((face, slot))
        return tuple(out)


# ── Spec helpers ───────────────────────────────────────────────────────

def _face(effects: CardEffects, face: int = 0) -> Tuple[AbilityEffects, ...]:
    faces = effects.faces
    return faces[face] if 0 <= face < len(faces) else ()


def _lit(amount) -> Optional[int]:
    """The printed integer of a LITERAL amount, else None."""
    if amount is not None and amount.kind is AmountKind.LITERAL:
        return amount.n
    return None


def _plain(s) -> bool:
    """No condition, choice, branch, replacement, duration or alternative."""
    return (s.condition is None and not s.optional and not s.then
            and not s.otherwise and not s.replaces and not s.alternatives
            and s.duration is None)


def _is_sub(s) -> bool:
    return isinstance(s.payload, SubAbility)


def _specs(effects: CardEffects, include_sub: bool = True):
    """(host, spec) over every host and every nested spec."""
    for h in effects.walk(include_sub=include_sub):
        for s in iter_specs(h.specs):
            yield h, s


def _has_verb(effects: CardEffects, *verbs: Verb) -> bool:
    return bool(effects.verbs & frozenset(verbs))


def _slot_types(s) -> frozenset:
    return s.target.types if s.target is not None else frozenset()


def _mod(s) -> Optional[Modification]:
    return s.payload if isinstance(s.payload, Modification) else None


def _mod_get(m: Optional[Modification], key: str, default=None):
    return default if m is None else m.get(key, default)


def _keywords_of(m: Optional[Modification]) -> Tuple[str, ...]:
    kws = _mod_get(m, "keywords", ()) or ()
    return tuple(k[0] if isinstance(k, tuple) else k for k in kws)


def _ref_kind(p) -> Optional[RefKind]:
    return getattr(p, "kind", None) if isinstance(getattr(p, "kind", None),
                                                  RefKind) else None


def _is_self(p) -> bool:
    return _ref_kind(p) is RefKind.SELF


def _head_hints(h: AbilityEffects) -> Tuple[EventHint, ...]:
    return h.trigger.event_hints if h.trigger is not None else ()


def _triggered(effects: CardEffects, *hints: EventHint):
    for h in _face(effects, 0):
        if h.kind is HostKind.TRIGGERED and set(hints) & set(_head_hints(h)):
            yield h


def _unwrap_delayed(specs):
    """Drop LOOK specs and unwrap one CREATE_TRIGGER(DELAYED) (10.1, the
    activated DAMAGE row): the specs the classifier reads."""
    specs = tuple(s for s in specs if not _is_look(s))
    if len(specs) == 1 and _is_sub(specs[0]) and \
            specs[0].payload.kind is SubAbilityKind.DELAYED:
        specs = tuple(s for s in specs[0].payload.host.specs
                      if not _is_look(s))
    return specs


def _is_look(s) -> bool:
    """A LOOK spec, typed or refused: looking changes no game state, so
    legacy's activation classifier drops it (`is_information_only_clause`)."""
    return s.verb is Verb.LOOK or (s.verb is Verb.UNMODELLED and
                                   getattr(s.payload, "lemma", "") == "look")


def _counter_n(s) -> Optional[int]:
    """How many counters a PUT_COUNTERS spec puts: its LITERAL amount, or
    one per counter word of the payload ("a +1/+1 counter")."""
    if s.amount is not None:
        return _lit(s.amount)
    if isinstance(s.payload, CounterSpec) and s.payload.kinds:
        return len(s.payload.kinds)
    return None


def _no_unparsed(s) -> bool:
    from .effect_spec import UNPARSED, residue_polarity
    return all(residue_polarity(c) != UNPARSED for c in s.residue)


# ── Strict host shapes per family (A38) ───────────────────────────────
#
# A switched handler takes the new path only for a host in its family's
# strict shape (and executable, and harness-identical). The shapes are
# fixed per family, so a later family's executors never silently switch
# an earlier family's host. E0 tolerates no residue (the dispatcher's
# LEGACY_RESIDUE_TOLERATED is empty until a family switch adds codes).

def _strict_host(h: AbilityEffects, verbs: frozenset, *,
                 durations: bool = False, conditions: bool = False) -> bool:
    if h is None or not h.specs:
        return False
    if h.trigger is not None and h.trigger.intervening_if is not None:
        return False
    for s in iter_specs(h.specs):
        if s.verb not in verbs or _is_sub(s) or s.residue:
            return False
        if s.duration is not None and not durations:
            return False
        if (s.condition is not None or s.replaces) and not conditions:
            return False
    return True


_DAMAGE_VERBS = frozenset({Verb.DAMAGE})
_REMOVAL_VERBS = frozenset({Verb.DESTROY, Verb.EXILE, Verb.MOVE,
                            Verb.SACRIFICE})
_CARD_FLOW_VERBS = frozenset({Verb.DRAW, Verb.DISCARD, Verb.LOOK,
                              Verb.REVEAL, Verb.MOVE, Verb.SEARCH,
                              Verb.SHUFFLE, Verb.MILL, Verb.SCRY,
                              Verb.SURVEIL, Verb.END_TURN})
_TOKEN_COUNTER_VERBS = frozenset({Verb.CREATE_TOKEN, Verb.PUT_COUNTERS,
                                  Verb.REMOVE_COUNTERS, Verb.PLAYER_COUNTERS,
                                  Verb.DOUBLE_COUNTERS, Verb.MOVE_COUNTERS})
_PUMP_VERBS = frozenset({Verb.CONTINUOUS, Verb.TAP, Verb.UNTAP})
_STACK_MANA_VERBS = frozenset({Verb.COUNTER, Verb.ADD_MANA, Verb.PAY,
                               Verb.END_TURN})


def strict_damage(h: AbilityEffects) -> bool:
    """One top-level DAMAGE (10.1, loyalty row), optionally replaced by
    printed upgrades -- "~ deals M damage instead if <condition>" (10.1,
    direct_damage_data row; 18.3 E1): each a conditional DAMAGE from the
    same source to the same target slot that replaces it and nothing
    else."""
    if not _strict_host(h, _DAMAGE_VERBS, conditions=True):
        return False
    base = [s for s in h.specs if not s.replaces]
    if len(base) != 1 or not _plain(base[0]):
        return False
    b = base[0]
    return all(r.replaces == (b.seq,) and r.condition is not None
               and not r.optional and not r.then and not r.otherwise
               and not r.alternatives and r.duration is None
               and r.target_slot == b.target_slot and r.other == b.other
               for r in h.specs if r is not b)


def strict_removal(h: AbilityEffects) -> bool:
    """One unconditional destroy / exile / bounce / sacrifice spec."""
    return (_strict_host(h, _REMOVAL_VERBS) and len(h.specs) == 1
            and _plain(h.specs[0]))


# Impulse draw (unit I, CR 406, 601.2a): "exile the top N cards of your
# library" and "<duration>, you may play / cast those cards" -- the one
# EXILE and the one CONTINUOUS card flow takes, each by its typed shape, so
# another family's exile or continuous effect never reads as card flow.
# The exile may take the controller's whole hand instead ("exile all the
# cards from your hand", unit HX), and a draw of "that many" -- the number
# that exile moved (CR 608.2c) -- may follow it.
_IMPULSE_VERBS = frozenset({Verb.EXILE, Verb.CONTINUOUS, Verb.DRAW})


def _library_top_exile(s) -> bool:
    f = s.filter
    return (s.verb is Verb.EXILE and f is not None
            and (getattr(f, "zone", None), getattr(f, "owner", None),
                 getattr(f, "position", None)) == ("library", "you", "top"))


def _hand_exile(s) -> bool:
    f = s.filter
    return (s.verb is Verb.EXILE and f is not None
            and (getattr(f, "zone", None), getattr(f, "owner", None),
                 getattr(f, "position", None)) == ("hand", "you", None))


def _draw_of_exiled(s, exiled) -> bool:
    """A draw of "that many" over one of `exiled` (spec seqs): the count is
    the exile's result. A draw of any other count is not this shape."""
    from .effect_executors import result_count
    return (s.verb is Verb.DRAW and result_count(s.amount)
            and s.amount.ref.index in exiled)


def _permission_over(s, exiled) -> bool:
    """A PERMIT to play or cast the cards one of `exiled` (spec seqs) put
    into exile."""
    m = s.payload
    return (s.verb is Verb.CONTINUOUS and isinstance(m, Modification)
            and m.kind is ModKind.PERMIT and m.action in ("play", "cast")
            and _ref_kind(s.ref) is RefKind.RESULT and s.ref.index in exiled)


# The card-flow verbs whose carriers have switched to the dispatcher:
# surveil (unit E.1) and the regrowth MOVE (E.2), with the impulse pair
# (unit I). A card-flow verb gains an executor for another carrier first --
# DRAW for the combat-damage carrier (R2) -- and joins this shape only when
# a unit switches the card-flow carriers to it and the per-host harness
# proves them (A38: a new executor never silently switches a host).
_CARD_FLOW_SWITCHED = frozenset({Verb.SURVEIL, Verb.MOVE})


def strict_card_flow(h: AbilityEffects) -> bool:
    """The switched card-flow verbs (`_CARD_FLOW_SWITCHED`), and the
    impulse shape: an EXILE of the top of the controller's library or of
    the controller's whole hand, a permission over what it exiled (the one
    spec with a duration), and a draw of "that many" of it. A draw of any
    other count stays with its legacy carrier (A38)."""
    if not _strict_host(h, _CARD_FLOW_SWITCHED | _IMPULSE_VERBS,
                        durations=True, conditions=True):
        return False
    exiled = {s.seq for s in iter_specs(h.specs)
              if _library_top_exile(s) or _hand_exile(s)}
    for s in iter_specs(h.specs):
        if s.verb is Verb.EXILE and s.seq not in exiled:
            return False
        if s.verb is Verb.CONTINUOUS and not _permission_over(s, exiled):
            return False
        if s.verb is Verb.DRAW and not _draw_of_exiled(s, exiled):
            return False
        if s.duration is not None and s.verb is not Verb.CONTINUOUS:
            return False
    return True


def strict_tokens_counters(h: AbilityEffects) -> bool:
    return _strict_host(h, _TOKEN_COUNTER_VERBS)


def strict_pump_restrict(h: AbilityEffects) -> bool:
    return _strict_host(h, _PUMP_VERBS, durations=True)


def strict_stack_mana(h: AbilityEffects) -> bool:
    return _strict_host(h, _STACK_MANA_VERBS, conditions=True)


# The draw carrier (unit D, `effect_carrier.dispatch_draw_triggers`): the
# verbs it resolves and the slot zones it picks a trigger's targets in.
FAMILY_DRAW_TRIGGER = "draw_trigger"
_DRAW_TRIGGER_VERBS = frozenset({Verb.DAMAGE, Verb.LOSE_LIFE, Verb.GAIN_LIFE,
                                 Verb.KEYWORD_ACTION})
_TRIGGER_TARGET_ZONES = frozenset({"any", "battlefield"})


# The self exile-and-return-transformed pair (`effect_executors.
# self_exile`, `self_return_transformed`, CR 400.7, 712).
_SELF_FLIP_VERBS = frozenset({Verb.EXILE, Verb.MOVE})


def strict_draw_trigger(h: AbilityEffects) -> bool:
    """A draw-triggered host the draw carrier takes (A38): the trigger
    family's strict shape with a typed draw head (`TriggerHead.draw`),
    every spec one of `_DRAW_TRIGGER_VERBS` -- or the self exile-and-
    return-transformed pair -- with no residue or sub-ability, and every
    target slot one the carrier picks as the trigger is put on the stack
    (any target, or a battlefield object)."""
    from .effect_executors import self_exile, self_return_transformed
    return (strict_trigger(h) and h.trigger.draw is not None
            and all(r.zone in _TRIGGER_TARGET_ZONES for r in h.targets)
            and _strict_host(h, _DRAW_TRIGGER_VERBS | _SELF_FLIP_VERBS,
                             conditions=True)
            and all(s.verb not in _SELF_FLIP_VERBS or self_exile(s)
                    or self_return_transformed(s)
                    for s in iter_specs(h.specs)))


# The combat-damage carrier (R2, `effect_carrier.
# dispatch_combat_damage_triggers`): the verbs it resolves.
FAMILY_COMBAT_DAMAGE_TRIGGER = "combat_damage_trigger"
_COMBAT_DAMAGE_TRIGGER_VERBS = frozenset({
    Verb.DRAW, Verb.CREATE_TOKEN, Verb.EXILE, Verb.CONTINUOUS, Verb.DAMAGE,
    Verb.LOSE_LIFE, Verb.GAIN_LIFE, Verb.KEYWORD_ACTION})
# The dealers it resolves: the source itself ("~"). An Equipment's or an
# Aura's "equipped / enchanted creature deals" waits for its own step.
_COMBAT_DAMAGE_DEALERS = frozenset({"self"})


def _event_library_top_exile(s) -> bool:
    """"Exile the top <N> cards of that player's library": the library of
    the player the trigger event names."""
    f = s.filter
    return (s.verb is Verb.EXILE and f is not None
            and (getattr(f, "zone", None), getattr(f, "position", None))
            == ("library", "top")
            and _ref_kind(getattr(f, "owner", None)) is RefKind.EVENT_PLAYER)


def strict_combat_damage_trigger(h: AbilityEffects) -> bool:
    """A combat-damage-triggered host the combat-damage carrier takes
    (A38): the trigger family's strict shape with a typed combat-damage
    head whose dealer the carrier reads, every spec one of
    `_COMBAT_DAMAGE_TRIGGER_VERBS` with no residue or sub-ability, an
    exile only of the top of a library and a continuous effect only a
    permission over what it exiled (the one spec with a duration), and
    every target slot one the carrier picks as the trigger is put on the
    stack."""
    cd = getattr(getattr(h, "trigger", None), "combat_damage", None)
    if not (strict_trigger(h) and cd is not None
            and cd.dealer in _COMBAT_DAMAGE_DEALERS
            and all(r.zone in _TRIGGER_TARGET_ZONES for r in h.targets)
            and _strict_host(h, _COMBAT_DAMAGE_TRIGGER_VERBS,
                             durations=True, conditions=True)):
        return False
    exiled = {s.seq for s in iter_specs(h.specs)
              if _event_library_top_exile(s) or _library_top_exile(s)}
    for s in iter_specs(h.specs):
        if s.verb is Verb.EXILE and s.seq not in exiled:
            return False
        if s.verb is Verb.CONTINUOUS and not _permission_over(s, exiled):
            return False
        if s.duration is not None and s.verb is not Verb.CONTINUOUS:
            return False
    return True


def strict_trigger(h: AbilityEffects) -> bool:
    """A typed TRIGGERED host with no intervening-if (stage T)."""
    return (h is not None and h.kind is HostKind.TRIGGERED
            and h.trigger is not None and h.trigger.intervening_if is None
            and bool(h.specs)
            and all(s.verb is not Verb.UNMODELLED
                    for s in iter_specs(h.specs)))


STRICT: Mapping[str, Callable[[AbilityEffects], bool]] = MappingProxyType({
    FAMILY_DAMAGE: strict_damage,
    FAMILY_REMOVAL: strict_removal,
    FAMILY_CARD_FLOW: strict_card_flow,
    FAMILY_TOKENS_COUNTERS: strict_tokens_counters,
    FAMILY_PUMP_RESTRICT: strict_pump_restrict,
    FAMILY_STACK_MANA: strict_stack_mana,
    FAMILY_TRIGGER: strict_trigger,
    FAMILY_DRAW_TRIGGER: strict_draw_trigger,
    FAMILY_COMBAT_DAMAGE_TRIGGER: strict_combat_damage_trigger,
})


# ── Legacy quirk predicates (ratchet (c)) ─────────────────────────────

# Words legacy's line scan treats as a resolution rider on a line other
# than the burn sentence (`oracle_parser._DIRECT_DMG_RIDER_TOKENS`); a host
# text holding one refuses the card, as legacy's printed-line scan does.
_DAMAGE_RIDER_WORDS = (
    "gain", "draw", "exile", "return", "counter", "scry", "create",
    "discard", "destroy", "+1/+1", "-1/-1", "sacrific", "instead",
    "divided", " each ", "loses")
_REMOVAL_RIDER_WORDS = (
    "search", "draw", "gain", "you may", "create", " then ", "sacrific",
    "loses", "counter", "put ", "shuffle", "instead", " if ", "scry",
    "return", "discard", "damage")
_SWEEP_RIDER_WORDS = (
    "gain", "draw", "create", "return", "exile", "you may", "put ",
    "deals", "damage", "scry", "discard", " each ", "loses", "search",
    " tap ")
# Host texts legacy skips as cost or casting lines, not resolution riders.
_COST_LINE_WORDS = ("can't be countered", "less to cast", "additional cost",
                    "rather than pay")
# Legacy's subject window before "deals" on the burn line (characters).
_PREFIX_WINDOW = 40  # the `^.{0,40}?` bound of `_DIRECT_DMG_RE`


def _legacy_prefix_window(host: AbilityEffects, s) -> bool:
    """Legacy reads the burn sentence only as the FIRST printed line, with
    at most a 40-character subject before "deals": the host must open the
    face and the spec's subject must fit the window."""
    if host.paragraphs[:1] not in ((0,), ()):
        return False
    head = s.raw.split(" deals ", 1)[0] if " deals " in s.raw else s.raw
    return len(head) <= _PREFIX_WINDOW


def _legacy_rider_tokens(effects: CardEffects, host: AbilityEffects,
                         words: Tuple[str, ...]) -> bool:
    """True when legacy's printed-line scan would refuse the card: another
    face-0 host that is not a keyword line or a cost line holds one of
    legacy's rider words."""
    for h in _face(effects, 0):
        if h is host or h.kind in (HostKind.KEYWORD,):
            continue
        text = h.text
        if any(w in text for w in _COST_LINE_WORDS):
            continue
        if any(w in text for w in words):
            return True
    return False


def _legacy_loyalty_damage_word(host: AbilityEffects) -> bool:
    """Legacy classifies a loyalty line as DAMAGE when the word "damage"
    appears anywhere in it (sub-abilities included), case-sensitively --
    so a sentence-initial "Damage" does not count. The quoted text of an
    emblem or token the line creates is part of the printed line."""
    for text in _line_texts(host):
        for m in re.finditer(r"damage", text):
            before = text[:m.start()].rstrip()
            if before and before[-1] not in ".:,":
                return True
    return False


def _line_texts(host: AbilityEffects):
    """The host's text and the texts of every host its specs create
    (sub-abilities, granted abilities, token abilities): one printed line."""
    yield host.text
    for s in iter_specs(host.specs):
        p = s.payload
        nested = ((p.host,) if isinstance(p, SubAbility) else
                  p.hosts if isinstance(p, Granted) else
                  p.granted if isinstance(p, TokenSpec) else ())
        for h in nested:
            yield from _line_texts(h)


def _legacy_tap_damage_one(n: int) -> int:
    """Legacy types tap damage only as the printed "deals 1 damage to
    you" phrase, so any other amount is 0."""
    return 1 if n == 1 else 0


def _legacy_when_head(host: AbilityEffects) -> bool:
    """Legacy's ETB-removal pattern reads only a "When ..." head, never a
    "Whenever ..." head."""
    raw = host.trigger.raw if host.trigger is not None else ""
    return raw.split(" ", 1)[0] == "when"


def _legacy_removal_scope_residue(s, etb: bool = False) -> bool:
    """Legacy's removal pattern accepts "an opponent controls" on the
    target (and, on an ETB trigger, "you don't control"); the solver keeps
    that as scope residue where it cannot narrow the requirement. Any
    other residue refuses."""
    ok = {"target.scope:opponent"}
    if etb:
        ok.add("target.scope:not_you")
    return set(s.residue) <= ok


def _legacy_colorless_counter(s) -> bool:
    """Legacy reads "counter target ... colorless spell" anywhere, even on
    a target the solver refuses (a triggered-ability-or-spell union)."""
    lemma = getattr(s.payload, "lemma", "")
    return ((s.verb is Verb.COUNTER or lemma == "counter")
            and "colorless spell" in s.raw)


def _legacy_domain_etb_removal(effects: CardEffects, key: Any = None) -> bool:
    """A39 mask: legacy's ETB-removal domain has no intervening-if (CR
    603.4) and no linked duration (CR 610.3). Outside it the grammar's
    growth is masked to legacy None until its own behaviour commit."""
    for h in _etb_removal_hosts(effects):
        if h.trigger.intervening_if is not None:
            return False
        if any(s.duration is not None for s in h.specs):
            return False
    return True


# ── E1: damage ─────────────────────────────────────────────────────────

def _face_legal(req) -> bool:
    """A face-legal burn target (legacy `_DIRECT_DMG_TARGET`): any target,
    or a player among the target's types; one target."""
    if req is None or req.count_min != 1 or req.count_max != 1:
        return False
    return req.types == frozenset({"any"}) or "player" in req.types


def _upgrade_condition(cond) -> Optional[str]:
    """The legacy label of an "instead" damage upgrade's condition."""
    if cond is None:
        return None
    if cond.label in ("delirium", "metalcraft"):
        return cond.label
    if cond.pred == "card_types":
        return "delirium"
    f = cond.filter
    if f is not None and "artifact" in f.types:
        return "metalcraft"
    return None


def _direct_damage(effects, key=None, template=None):
    h = effects.spell(0)
    if h is None:
        return None
    base, upgrade = None, None
    for s in h.specs:
        if s.verb is Verb.DAMAGE and base is None and _plain(s) \
                and _is_self(s.other) and _face_legal(s.target) \
                and _lit(s.amount) is not None and not s.residue:
            base = s
            continue
        if s.verb is Verb.DAMAGE and base is not None and upgrade is None \
                and s.replaces == (base.seq,) and _lit(s.amount) is not None:
            label = _upgrade_condition(s.condition)
            if label is not None:
                upgrade = {"upgrade_amount": _lit(s.amount),
                           "upgrade_condition": label}
                continue
        return None
    if base is None or not _legacy_prefix_window(h, base):
        return None
    if _legacy_rider_tokens(effects, h, _DAMAGE_RIDER_WORDS):
        return None
    data = {"amount": _lit(base.amount)}
    if upgrade:
        data.update(upgrade)
    return data


def _tap_damage(effects, key=None, template=None):
    if template is not None and not _is_type(template, "land"):
        return 0
    for h in _face(effects, 0):
        if h.kind is not HostKind.MANA_ABILITY:
            continue
        for s in h.specs:
            if s.verb is Verb.DAMAGE and _is_self(s.other) \
                    and _lit(s.amount) is not None and _names_you(s):
                return _legacy_tap_damage_one(_lit(s.amount))
    return 0


def _names_you(s) -> bool:
    subj = s.subject
    return (subj is not None and subj.kind is SelectorKind.PLAYER) or \
        "to you" in s.raw


def _is_energy(payload) -> bool:
    return isinstance(payload, CounterSpec) and "energy" in payload.kinds


def _energy_damage(effects, key=None, template=None):
    for h in effects.walk():
        verbs = {s.verb for s in iter_specs(h.specs)}
        if not {Verb.PLAYER_COUNTERS, Verb.PAY, Verb.DAMAGE} <= verbs:
            continue
        gains = any(s.verb is Verb.PLAYER_COUNTERS and _is_energy(s.payload)
                    for s in iter_specs(h.specs))
        pays = any(s.verb is Verb.PAY and _is_energy(s.payload)
                   for s in iter_specs(h.specs))
        that_much = any(s.verb is Verb.DAMAGE and s.amount is not None
                        and s.amount.kind is AmountKind.THAT_MUCH
                        for s in iter_specs(h.specs))
        targets = any({"creature", "planeswalker"} <= set(r.types)
                      for r in h.targets)
        if gains and pays and that_much and targets:
            return True
    return False


def _activated_host(effects, key):
    if key is None:
        return None
    return effects.activated(key, 0)


def _act_damage(effects, key=None, template=None):
    h = _activated_host(effects, key)
    if h is None:
        return None
    specs = _unwrap_delayed(h.specs)
    if len(specs) != 1:
        return None
    s = specs[0]
    if s.verb is Verb.DAMAGE and _plain(s) and _is_self(s.other) \
            and _slot_types(s) == frozenset({"any"}) \
            and _lit(s.amount) is not None:
        return _lit(s.amount)
    return None


# ── Loyalty classification (E1 / E2 / E5 rows) ────────────────────────

def _loyalty_host(effects, key):
    if not isinstance(key, tuple) or len(key) != 2:
        return None
    face, slot = key
    return effects.loyalty(slot, face)


def _loyalty_return_shape(h: AbilityEffects):
    """(requirement, draws) when the whole line is one targeted return to
    hand plus draw riders (legacy `_loyalty_return_to_hand`), else None."""
    ret, draws = None, 0
    for s in h.specs:
        if s.verb is Verb.MOVE and s.dest is not None \
                and s.dest.zone == "hand" and s.target is not None \
                and ret is None and _plain(s):
            ret = s
            continue
        if s.verb is Verb.DRAW and _plain(s) and _lit(s.amount):
            draws += _lit(s.amount)
            continue
        return None
    if ret is None or len(h.targets) != 1:
        return None
    req = ret.target
    if req.zone not in ("battlefield", "graveyard"):
        return None
    return req, draws


def _loyalty_kind(effects, key) -> Optional[str]:
    h = _loyalty_host(effects, key)
    if h is None:
        return None
    if _loyalty_return_shape(h) is not None:
        return "RETURN_TO_HAND"
    if _legacy_loyalty_damage_word(h):
        return "DAMAGE"
    verbs = {s.verb for s in iter_specs(h.specs)}
    if "gain" in h.text and "draw" in h.text:
        return "GAIN_LIFE_AND_DRAW"
    if Verb.DRAW in verbs and Verb.UNTAP in verbs:
        return "DRAW_AND_UNTAP_LANDS"
    if any(s.verb is Verb.MOVE and s.target is not None and s.dest is not None
           and s.dest.zone == "library" for s in iter_specs(h.specs)):
        return "TUCK_TARGET_INTO_LIBRARY"
    if Verb.CREATE_EMBLEM in verbs and "exile" in h.text:
        return "EMBLEM_EXILE_PERMANENT"
    return "UNCLASSIFIED"


def _loyalty_kind_view(kind: str):
    def view(effects, key=None, template=None):
        return _loyalty_kind(effects, key) == kind
    view.__name__ = f"_loyalty_is_{kind.lower()}"
    return view


def _loyalty_target(effects, key=None, template=None):
    h = _loyalty_host(effects, key)
    shape = _loyalty_return_shape(h) if h is not None else None
    return shape[0] if shape else None


def _loyalty_draws(effects, key=None, template=None):
    h = _loyalty_host(effects, key)
    shape = _loyalty_return_shape(h) if h is not None else None
    return shape[1] if shape else 0


def _read_loyalty(template, key):
    if not isinstance(key, tuple):
        return None
    face, slot = key
    attr = "loyalty_abilities" if face == 0 else "back_face_loyalty_abilities"
    return (getattr(template, attr, None) or {}).get(slot)


def _read_loyalty_kind(kind: str):
    def legacy(template, key=None):
        ab = _read_loyalty(template, key)
        return ab is not None and ab.effect_kind.name == kind
    return legacy


# ── E2: removal ────────────────────────────────────────────────────────

# The nine removal typespecs legacy's pattern names (`_REMOVAL_TYPESPEC`).
_REMOVAL_TYPESPECS = frozenset(frozenset(t) for t in (
    ("permanent_nonland",), ("permanent",), ("creature",),
    ("creature", "planeswalker"), ("artifact",), ("enchantment",),
    ("artifact", "enchantment"), ("artifact", "creature"),
    ("artifact", "creature", "enchantment")))


def _removal_slot(s) -> bool:
    req = s.target
    return (req is not None and req.zone == "battlefield"
            and req.types in _REMOVAL_TYPESPECS
            and req.owner_scope in ("any", "opponent")
            and req.supertype is None and req.subtype is None)


def _removal_spec(s) -> bool:
    return (s.verb in (Verb.DESTROY, Verb.EXILE) and _plain(s)
            and s.filter is None and _removal_slot(s)
            and _legacy_removal_scope_residue(s))


def _removal_dict(s) -> Dict[str, Any]:
    req = s.target
    if req.count_max > 1:
        return {"action": s.verb.value, "types": sorted(req.types),
                "mv": None, "count": req.count_max}
    mv = "x" if req.max_mana_value_is_x else req.max_mana_value
    return {"action": s.verb.value, "types": sorted(req.types), "mv": mv}


def _single_removal(h: Optional[AbilityEffects]):
    if h is None or len(h.specs) != 1 or not _removal_spec(h.specs[0]):
        return None
    return h.specs[0]


def _targeted_removal(effects, key=None, template=None):
    h = effects.spell(0)
    s = _single_removal(h)
    if s is None or _legacy_rider_tokens(effects, h, _REMOVAL_RIDER_WORDS):
        return None
    return _removal_dict(s)


def _mode_host(effects, key):
    modes = effects.modes(0)
    if not isinstance(key, int) or not 0 <= key < len(modes):
        return None
    return modes[key]


def _mode_removal(effects, key=None, template=None):
    s = _single_removal(_mode_host(effects, key))
    return None if s is None else _removal_dict(s)


def _etb_removal_hosts(effects):
    for h in _face(effects, 0):
        if h.kind is HostKind.TRIGGERED and \
                _head_hints(h) == (EventHint.SELF_ENTERS,) and \
                _legacy_when_head(h) and len(h.specs) == 1:
            s = h.specs[0]
            if s.verb in (Verb.DESTROY, Verb.EXILE) and _removal_slot(s) \
                    and s.condition is None and not s.then \
                    and s.filter is None and \
                    _legacy_removal_scope_residue(s, etb=True) and \
                    not s.target.max_mana_value_is_x and \
                    s.target.count_max == 1:
                yield h


def _etb_removal(effects, key=None, template=None):
    for h in _etb_removal_hosts(effects):
        s = h.specs[0]
        req = s.target
        scope = "opponent" if (req.owner_scope == "opponent" or s.residue) \
            else "any"
        return {"action": s.verb.value, "types": sorted(req.types),
                "mv": req.max_mana_value, "owner_scope": scope,
                "optional": bool(s.optional)}
    return None


def _mv_bound(cond) -> Optional[int]:
    if cond is not None and cond.kind is ConditionKind.OBJECT and \
            cond.pred == "mana_value" and cond.op == "<=":
        return _lit(cond.n)
    return None


def _removal_mv_condition(effects, key=None, template=None):
    h = effects.spell(0)
    if h is None or not h.specs:
        return None
    s1 = h.specs[0]
    if s1.verb not in (Verb.DESTROY, Verb.EXILE) or not _removal_slot(s1):
        return None
    mv = _mv_bound(s1.condition)
    if mv is None:
        return None
    raised = None
    for s2 in h.specs[1:]:
        c = s2.condition
        if s2.replaces == (s1.seq,) and c is not None and \
                c.kind is ConditionKind.ALL_OF:
            hist = any(k.kind is ConditionKind.HISTORY and
                       k.pred == "permanent_left" for k in c.children)
            bound = next((_mv_bound(k) for k in c.children
                          if _mv_bound(k) is not None), None)
            if hist and bound is not None:
                raised = bound
    return {"mv": mv, "mv_if_permanent_left": raised}


def _board_sweep(effects, key=None, template=None):
    h = effects.spell(0)
    if h is None or len(h.specs) != 1:
        return None
    s = h.specs[0]
    f = s.filter
    if s.verb is not Verb.DESTROY or s.target is not None or f is None \
            or s.subject is None or s.subject.kind is not SelectorKind.FILTER:
        return None
    if f.as_tuple() != (("types", ("creature",)),):
        return None
    if s.flags - {"no_regeneration"} or not _plain(s):
        return None
    if _legacy_rider_tokens(effects, h, _SWEEP_RIDER_WORDS):
        return None
    return {"action": "destroy", "types": ["creature"]}


def _bounce_target(effects, key=None, template=None):
    for h, s in _specs(effects, include_sub=False):
        if s.verb is Verb.MOVE and s.dest is not None and \
                s.dest.zone == "hand" and s.target is not None and \
                s.target.zone == "battlefield":
            return s.target
    return None


def _land_destruction(effects, key=None, template=None):
    h = effects.spell(0)
    if h is None or not h.specs:
        return None
    s = h.specs[0]
    if s.verb is not Verb.DESTROY or s.target is None or \
            s.target.types not in (frozenset({"land"}),
                                   frozenset({"artifact", "land"})):
        return None
    req = s.target
    data = {
        "can_target_artifact": "artifact" in req.types,
        "nonbasic_only": req.supertype == "nonbasic",
        "opponent_controls_only": req.owner_scope == "opponent",
        "rider_search_basic": False,
        "rider_search_basic_tapped": False,
        "rider_damage": 0,
        "rider_damage_nonbasic_only": False,
        "rider_caster_draws": 0,
    }
    for r in iter_specs(h.specs[1:]):
        if r.verb is Verb.SEARCH:
            data["rider_search_basic"] = True
        elif r.verb is Verb.MOVE and r.dest is not None and \
                r.dest.zone == "battlefield":
            data["rider_search_basic_tapped"] = bool(r.dest.tapped)
        elif r.verb is Verb.DAMAGE and _lit(r.amount):
            data["rider_damage"] = _lit(r.amount)
            data["rider_damage_nonbasic_only"] = r.condition is not None
        elif r.verb is Verb.DRAW and _lit(r.amount):
            data["rider_caster_draws"] = _lit(r.amount)
        elif r.verb is Verb.SHUFFLE:
            continue
        else:
            return None
    return data


def _destroys_target_land(effects, key=None, template=None):
    return _land_destruction(effects, key, template) is not None


def _gy_to_battlefield(s) -> bool:
    return (s.verb is Verb.MOVE and s.dest is not None
            and s.dest.zone == "battlefield")


def _mass_graveyard_return(effects, key=None, template=None):
    for h, s in _specs(effects):
        if s.target is not None or _is_self(s.ref):
            continue
        f = s.filter
        if _gy_to_battlefield(s) and f is not None and \
                f.zone == "graveyard" and "creature" in f.types:
            return True
        if s.verb is Verb.EXILE and f is not None and \
                f.zone == "graveyard" and "creature" in f.types and \
                _actor_all(s):
            return True
    return False


def _actor_all(s) -> bool:
    a = s.actor
    return getattr(a, "kind", None) is SelectorKind.ALL_PLAYERS


def _symmetric_reanimation(effects, key=None, template=None):
    for h, s in _specs(effects):
        f = s.filter
        if _actor_all(s) and f is not None and f.zone == "graveyard" and \
                "creature" in f.types and s.verb in (Verb.EXILE, Verb.MOVE):
            return True
    return False


def _graveyard_exile(effects, key=None, template=None):
    h = _activated_host(effects, key)
    if h is None:
        return None
    specs = _unwrap_delayed(h.specs)
    if len(specs) != 1 or specs[0].verb is not Verb.EXILE:
        return None
    s = specs[0]
    if s.target is not None and s.target.zone == "graveyard":
        return {"scope": "cards", "count": s.target.count_max}
    f = s.filter
    whole = s.amount is not None and s.amount.kind is AmountKind.WHOLE_ZONE
    if f is not None and f.zone == "graveyard" and whole:
        if _ref_kind(f.owner) is RefKind.TARGET:
            return {"scope": "target_player"}
        if f.owner in ("opponent", "opponents"):
            return {"scope": "each_opponent"}
        return {"scope": "all"}
    return None


def _etb_exile_returns(effects, key=None, template=None):
    for h in _triggered(effects, EventHint.SELF_ENTERS):
        verbs = {s.verb for s in iter_specs(h.specs)}
        if Verb.REVEAL in verbs and Verb.EXILE in verbs and any(
                s.verb is Verb.EXILE and s.duration is not None and
                s.duration.kind is DurationKind.UNTIL_LEAVES
                for s in iter_specs(h.specs)):
            return True
    return False


# ── E3: card flow ──────────────────────────────────────────────────────

def _loot(effects, key=None, template=None):
    for h in effects.walk():
        specs = h.specs
        for i, s in enumerate(specs[:-1]):
            d = specs[i + 1]
            if s.verb is Verb.DRAW and d.verb is Verb.DISCARD and \
                    _lit(s.amount) and _lit(d.amount):
                return {"draw": _lit(s.amount), "discard": _lit(d.amount),
                        "random": "at_random" in d.flags or
                        d.chooser is Chooser.RANDOM or "at random" in d.raw,
                        "each_player": _actor_all(s)}
    return None


def _hand_attack(effects, key=None, template=None):
    h = effects.spell(0)
    if h is None or not h.targets:
        return None
    req = h.targets[0]
    tgt = "opponent" if "opponent" in req.types else \
        "player" if "player" in req.types else None
    if tgt is None:
        return None
    verbs = [s.verb for s in h.specs]
    if Verb.REVEAL in verbs and Verb.DISCARD in verbs:
        return {"chooser": "caster", "target": tgt}
    for s in h.specs:
        if s.verb is Verb.DISCARD and _ref_kind(s.actor) is RefKind.TARGET \
                and _lit(s.amount):
            return {"chooser": "random" if "at_random" in s.flags
                    else "victim", "target": tgt, "choose_clause": None,
                    "count": _lit(s.amount)}
    return None


# Verbs legacy's dig refuses alongside the dig (`_DIG_RIDER_TOKENS`).
_DIG_RIDER_VERBS = frozenset({Verb.CREATE_TOKEN, Verb.DAMAGE, Verb.SACRIFICE,
                              Verb.DESTROY, Verb.EXILE, Verb.GAIN_LIFE})


def _library_dig(effects, key=None, template=None):
    """Look at / reveal the top of the library, take a card to hand, the
    rest to the bottom or the graveyard: legacy reads the whole face, so
    any host may carry it, and a creation / damage / life / removal rider
    anywhere refuses it."""
    if any(s.verb in _DIG_RIDER_VERBS for _, s in _specs(effects)):
        return None
    for h in _face(effects, 0):
        rest, to_hand = None, False
        for s in iter_specs(h.specs):
            if s.verb is Verb.MOVE and s.ref is not None and \
                    s.dest is not None and \
                    s.dest.zone in ("library", "graveyard") and \
                    s.ref.part in (RefPart.REST, RefPart.ALL) and \
                    not _is_self(s.ref):
                rest = "bottom" if s.dest.zone == "library" else "graveyard"
            if s.verb is Verb.MOVE and s.dest is not None and \
                    s.dest.zone == "hand":
                to_hand = True
        if rest is not None and to_hand:
            return {"rest_destination": rest}
    return None


def _hand_refill(effects, key=None, template=None):
    """Each player shuffles their hand (and graveyard) into their library
    or discards their hand, then draws N; "if it's your turn, end the
    turn" rides along (CR 723)."""
    h = effects.spell(0)
    if h is None:
        return None
    draw = next((s for s in h.specs if s.verb is Verb.DRAW and _actor_all(s)
                 and _lit(s.amount)), None)
    if draw is None:
        return None
    whole_discard = any(
        s.verb is Verb.DISCARD and _actor_all(s) and s.amount is not None
        and s.amount.kind in (AmountKind.WHOLE_ZONE, AmountKind.ALL)
        for s in h.specs)
    shuffle = any(
        (s.verb is Verb.MOVE and s.dest is not None and
         s.dest.zone == "library" and s.filter is not None and
         s.filter.zone == "hand") or
        (s.verb is Verb.UNMODELLED and
         getattr(s.payload, "lemma", "") == "shuffle" and "hand" in s.raw)
        for s in h.specs)
    if not (whole_discard or shuffle):
        return None
    lead = next(s for s in h.specs if s.verb is not Verb.DRAW)
    return {"mode": "discard" if whole_discard else "shuffle",
            "graveyard": "graveyard" in lead.raw,
            "count": _lit(draw.amount),
            "ends_turn": any(s.verb is Verb.END_TURN for s in h.specs)}


def _search_specs(h):
    return [s for s in iter_specs(h.specs) if s.verb is Verb.SEARCH]


def _x_creature_tutor(effects, key=None, template=None):
    h = effects.spell(0)
    if h is None:
        return None
    for s in _search_specs(h):
        f = s.filter
        if f is None or "creature" not in f.types:
            continue
        x_bound = any(k == "mana_value" and a.kind is AmountKind.X
                      for k, _, a in f.stat_bounds)
        if not x_bound:
            continue
        move = next((m for m in iter_specs(h.specs) if m.verb is Verb.MOVE
                     and m.ref is not None and m.ref.kind is RefKind.RESULT
                     and m.ref.index == s.seq), None)
        if move is None or move.dest is None or \
                move.dest.zone != "battlefield":
            continue
        return {"dest": "battlefield", "types": ["creature"],
                "colors": sorted(f.colors), "mv_bound_is_x": True,
                "tapped": bool(move.dest.tapped),
                "self_shuffle_into_library": any(
                    m.verb is Verb.MOVE and _is_self(m.ref) and
                    m.dest is not None and m.dest.zone == "library"
                    for m in iter_specs(h.specs))}
    return None


def _act_tutor(dest: str):
    def view(effects, key=None, template=None):
        h = _activated_host(effects, key)
        if h is None:
            return False
        specs = _unwrap_delayed(h.specs)
        search = [s for s in specs if s.verb is Verb.SEARCH]
        if len(search) != 1:
            return False
        s = search[0]
        moves = [m for m in specs if m.verb is Verb.MOVE and
                 m.ref is not None and m.ref.kind is RefKind.RESULT]
        if len(moves) != 1 or moves[0].dest is None or \
                moves[0].dest.zone != dest:
            return False
        if dest == "battlefield":
            return s.filter is not None and "creature" in s.filter.types
        return True
    view.__name__ = f"_act_tutor_to_{dest}"
    return view


def _act_tutor_data(effects, key=None, template=None):
    h = _activated_host(effects, key)
    if h is None or not (_act_tutor("hand")(effects, key) or
                         _act_tutor("battlefield")(effects, key)):
        return None
    for s in _search_specs(h):
        move = next((m for m in iter_specs(h.specs) if m.verb is Verb.MOVE
                     and m.ref is not None and m.ref.kind is RefKind.RESULT),
                    None)
        if move is None or move.dest is None:
            continue
        return {"dest": move.dest.zone}
    return None


# What a fetch's ability does: search, put onto the battlefield, shuffle,
# and Fabled Passage's untap rider.
_FETCH_VERBS = frozenset({Verb.SEARCH, Verb.MOVE, Verb.SHUFFLE, Verb.UNTAP})

# Colours of the basic land types a fetch may name (CR 305.6), WUBRG order.
_BASIC_TYPE_COLORS = (("plains", "W"), ("island", "U"), ("swamp", "B"),
                      ("mountain", "R"), ("forest", "G"))


def _fetchland(effects, key=None, template=None):
    from .cards import FetchLandProfile
    for h in _face(effects, 0):
        if h.kind is not HostKind.ACTIVATED or h.cost is None:
            continue
        cost = dict(h.cost.items)
        if not cost.get("sacrifice_self"):
            continue
        search = _search_specs(h)
        if len(search) != 1 or search[0].filter is None or \
                search[0].actor is not None or \
                any(s.verb not in _FETCH_VERBS for s in iter_specs(h.specs)):
            continue
        f = search[0].filter
        move = next((m for m in iter_specs(h.specs) if m.verb is Verb.MOVE
                     and m.dest is not None
                     and m.dest.zone == "battlefield"), None)
        if move is None:
            continue
        names = set(f.subtypes)
        colors = tuple(c for t, c in _BASIC_TYPE_COLORS if t in names)
        if not colors and "basic" in f.supertypes and "land" in f.types:
            colors = tuple(c for _, c in _BASIC_TYPE_COLORS)
        if not colors:
            continue
        n = search[0].amount
        count = n.n if n is not None and n.kind in (AmountKind.LITERAL,
                                                    AmountKind.UP_TO) else 1
        return FetchLandProfile(colors=colors, life_cost=cost.get("life", 0),
                                count=max(count, 1),
                                target_enters_tapped=bool(move.dest.tapped))
    return None


def _land_sacrifice_tutor(effects, key=None, template=None):
    for h in effects.walk():
        specs = list(iter_specs(h.specs))
        sac = any(s.verb is Verb.SACRIFICE and s.amount is not None and
                  s.amount.kind is AmountKind.ANY_NUMBER and s.filter is not None
                  and "land" in s.filter.types for s in specs)
        search = any(s.verb is Verb.SEARCH and s.filter is not None and
                     "land" in s.filter.types for s in specs)
        if sac and search:
            return True
    return False


def _act_draw(effects, key=None, template=None):
    h = _activated_host(effects, key)
    if h is None:
        return None
    specs = _unwrap_delayed(h.specs)
    if len(specs) == 1 and specs[0].verb is Verb.DRAW and \
            _plain(specs[0]) and _lit(specs[0].amount) and \
            _legacy_draw_word_limit(_lit(specs[0].amount)):
        return _lit(specs[0].amount)
    return None


# Legacy's activated draw pattern reads "a / one / two / three" or digits;
# printed draw counts are number words, so four or more is unread.
_LEGACY_DRAW_WORDS_MAX = 3


def _legacy_draw_word_limit(n: int) -> bool:
    """Legacy's activated DRAW_N pattern names the count words a, one, two
    and three only, so a printed "draw seven cards" stays unclassified."""
    return 0 < n <= _LEGACY_DRAW_WORDS_MAX


def _act_delay(effects, key=None, template=None):
    h = _activated_host(effects, key)
    if h is None:
        return None
    specs = tuple(s for s in h.specs if not _is_look(s))
    if len(specs) == 1 and _is_sub(specs[0]) and \
            specs[0].payload.kind is SubAbilityKind.DELAYED:
        return specs[0].payload.timing
    return None


def _cycling_variant(effects, key=None, template=None):
    for h in _face(effects, 0):
        for kw in h.keywords:
            if kw.name.endswith("cycling") and kw.name != "cycling":
                return {"keyword": kw.name}
    return None


# ── E4: tokens and counters ───────────────────────────────────────────

def _act_counter(scope: str):
    def view(effects, key=None, template=None):
        h = _activated_host(effects, key)
        if h is None:
            return None
        specs = _unwrap_delayed(h.specs)
        if len(specs) != 1 or specs[0].verb is not Verb.PUT_COUNTERS:
            return None
        s = specs[0]
        n = _counter_n(s)
        if not _plain(s) or n is None:
            return None
        if scope == "self" and _is_self(s.ref):
            return n
        if scope == "target" and s.target is not None:
            return n
        if scope == "team" and s.subject is not None and \
                s.subject.kind is SelectorKind.FILTER:
            return n
        return None
    view.__name__ = f"_act_put_counter_{scope}"
    return view


def _act_adapt(effects, key=None, template=None):
    h = _activated_host(effects, key)
    if h is None:
        return None
    specs = _unwrap_delayed(h.specs)
    if len(specs) == 1 and specs[0].verb is Verb.KEYWORD_ACTION and \
            getattr(specs[0].payload, "name", "") == "adapt":
        return _lit(specs[0].payload.amount)
    return None


def _act_counter_data(effects, key=None, template=None):
    for scope in ("self", "target", "team"):
        n = _act_counter(scope)(effects, key)
        if n is not None:
            return {"amount": n, "self": scope == "self"}
    return None


def _token_specs(effects):
    for h, s in _specs(effects):
        if s.verb is Verb.CREATE_TOKEN:
            yield h, s


def _cast_trigger_token(effects, key=None, template=None):
    for h in _triggered(effects, EventHint.SPELL_CAST):
        for s in iter_specs(h.specs):
            if s.verb is Verb.CREATE_TOKEN:
                return {"count": _lit(s.amount) or 1}
    return None


def _scaling_token(effects, key=None, template=None):
    return any(s.amount is not None and s.amount.kind in
               (AmountKind.FOR_EACH, AmountKind.X, AmountKind.EQUAL_TO)
               for _, s in _token_specs(effects))


def _energy_production(effects, key=None, template=None):
    n = 0
    for _, s in _specs(effects):
        if s.verb is Verb.PLAYER_COUNTERS and _is_energy(s.payload):
            n += sum(1 for k in s.payload.kinds if k == "energy")
    return n


def _enters_type_counter(effects, key=None, template=None):
    for h in _triggered(effects, EventHint.OTHER_ENTERS):
        for s in h.specs:
            if s.verb is Verb.PUT_COUNTERS and _is_self(s.ref):
                return {"counter_power": _lit(s.amount) or 1}
    return None


def _counter_placement_trigger(effects, key=None, template=None):
    for h in _triggered(effects, EventHint.COUNTERS_PUT):
        verbs = {s.verb for s in h.specs}
        effect = "create_token" if Verb.CREATE_TOKEN in verbs else \
            "draw" if Verb.DRAW in verbs else "unresolved"
        return {"effect": effect}
    return None


def _chapter_one_token(effects, key=None, template=None):
    for h in _face(effects, 0):
        if h.kind is HostKind.CHAPTER and 1 in h.chapters:
            return any(s.verb in (Verb.CREATE_TOKEN, Verb.SEARCH, Verb.MOVE)
                       for s in iter_specs(h.specs))
    return False


# ── E5: pump, grants and restrictions ─────────────────────────────────

def _pt(m: Optional[Modification]) -> Tuple[Optional[int], Optional[int]]:
    return (_lit(_mod_get(m, "power")), _lit(_mod_get(m, "toughness")))


def _engine_keywords(m: Optional[Modification]) -> Tuple[str, ...]:
    """The granted keywords the engine models (the `Keyword` enum is the
    vocabulary), printed spelling ("double strike")."""
    from .cards import Keyword
    known = {k.value for k in Keyword}
    return tuple(k.replace("_", " ") for k in _keywords_of(m) if k in known)


def _until_eot(s) -> bool:
    return s.duration is not None and s.duration.kind is DurationKind.THIS_TURN


def _targets_creature(s) -> bool:
    return s.target_slot is not None or _ref_kind(s.ref) is RefKind.TARGET


def _pump_spell(effects):
    """(power, toughness, keywords) of the first non-negative "target
    creature gets +N/+M until end of turn [and gains <kw>]" spec on the
    face (legacy reads the whole text), else the spell's keyword-only
    grant; None when neither is printed. An "instead" sibling (a
    replacing upgrade) is not part of the base pump."""
    for h in _face(effects, 0):
        for s in h.specs:
            m = _mod(s)
            if s.verb is not Verb.CONTINUOUS or m is None or \
                    m.kind is not ModKind.MODIFY_PT or not _until_eot(s) or \
                    not _targets_creature(s) or s.replaces:
                continue
            p, t = _pt(m)
            if p is None or t is None or p < 0 or t < 0:
                continue
            kws = tuple(k for x in h.specs if x is not s and not x.replaces
                        and x.verb is Verb.CONTINUOUS and _targets_creature(x)
                        and _mod(x) is not None
                        and _mod(x).kind is ModKind.ADD_KEYWORDS
                        for k in _engine_keywords(_mod(x)))
            return p, t, kws
    h = effects.spell(0)
    grants = [s for s in (h.specs if h is not None else ())
              if not s.replaces]
    if grants and all(
            s.verb is Verb.CONTINUOUS and _mod(s) is not None and
            _mod(s).kind is ModKind.ADD_KEYWORDS and _until_eot(s) and
            _targets_creature(s) for s in grants):
        kws = _engine_keywords(_mod(grants[0]))
        if kws:
            return 0, 0, kws
    return None


def _pump_power(effects, key=None, template=None):
    r = _pump_spell(effects)
    return r[0] if r else 0


def _pump_toughness(effects, key=None, template=None):
    r = _pump_spell(effects)
    return r[1] if r else 0


def _pump_keyword(effects, key=None, template=None):
    r = _pump_spell(effects)
    return r[2][0] if r and r[2] else ""


def _pump_keywords(effects, key=None, template=None):
    r = _pump_spell(effects)
    return r[2] if r else ()


def _team_filter(s) -> bool:
    subj = s.subject
    if subj is None or subj.kind is not SelectorKind.FILTER:
        return False
    f = dict(subj.filter or ())
    return (f.get("types") == ("creature",) and f.get("controller") == "you"
            and set(f) <= {"types", "controller", "other"})


def _team_pump(effects, key=None, template=None):
    h = effects.spell(0)
    if h is None or not h.specs:
        return None
    power = toughness = 0
    kws = []
    for s in h.specs:
        m = _mod(s)
        if s.verb is not Verb.CONTINUOUS or m is None or not _team_filter(s):
            return None
        if m.kind is ModKind.MODIFY_PT:
            pp, tt = _pt(m)
            if pp is None or tt is None:
                return None
            power, toughness = pp, tt
        elif m.kind is ModKind.ADD_KEYWORDS:
            kws.extend(_keywords_of(m))
        else:
            return None
    others = any(dict(s.subject.filter or ()).get("other") for s in h.specs)
    return {"trigger": "spell", "power": power, "toughness": toughness,
            "scaling": "", "keywords": kws, "others_only": bool(others)}


def _next_turn_effect(effects, key=None, template=None):
    for _, s in _specs(effects):
        if s.duration is not None and \
                s.duration.kind is DurationKind.UNTIL_YOUR_NEXT_TURN:
            m = _mod(s)
            if m is not None and m.kind is ModKind.MODIFY_PT:
                return {"kind": "pt_mod",
                        "scope": "target" if s.target is not None
                        else "yours"}
            if m is not None and m.kind is ModKind.COST_DELTA:
                return {"kind": "cost_reduction"}
            if m is not None and m.kind is ModKind.PERMIT and \
                    m.action == "cast_as_flash":
                # a flash permission; a permission to play or cast named
                # objects (impulse, Nivix) is no next-turn effect kind
                return {"kind": "flash_permission"}
    return None


def _prohibits(effects, action: str, any_host: bool = False):
    for h, s in _specs(effects):
        m = _mod(s)
        if m is not None and m.kind is ModKind.PROHIBIT and \
                m.action == action and s.duration is not None and \
                (any_host or h.kind in (HostKind.SPELL, HostKind.MODE)):
            yield h, s


def _turn_scoped_restriction(effects, key=None, template=None):
    for _, s in _prohibits(effects, "cast"):
        if s.duration.kind is DurationKind.THIS_TURN:
            return "no_spells"
    for _, s in _prohibits(effects, "attack"):
        if s.duration.kind is DurationKind.THIS_TURN and s.target is None:
            return "no_attacks"
    for h, s in _specs(effects):
        m = _mod(s)
        if m is not None and m.kind is ModKind.PREVENT_DAMAGE and \
                h.kind is HostKind.SPELL and s.target is None:
            return "fog"
    return None


def _cast_prohibition(effects, key=None, template=None):
    for _, s in _prohibits(effects, "cast", any_host=True):
        if s.duration.kind is not DurationKind.THIS_TURN:
            continue
        who = "target" if s.target is not None else \
            "opponents" if getattr(s.subject, "kind", None) is \
            SelectorKind.OPPONENTS else "all"
        flt = _mod_get(_mod(s), "filter", "all") or "all"
        flt = flt[:-len(" spells")] if flt.endswith(" spells") else flt
        return {"who": who, "filter": "all" if flt == "spells" else flt}
    return None


def _object_restriction(effects, key=None, template=None):
    for h, s in _specs(effects):
        m = _mod(s)
        if m is None or m.kind is not ModKind.PROHIBIT or s.target is None \
                or m.action not in ("attack", "block", "attack_or_block"):
            continue
        if s.duration is None:
            continue
        return {"count": s.target.count_max,
                "duration": "until_next_turn" if s.duration.kind is
                DurationKind.UNTIL_YOUR_NEXT_TURN else "this_turn"}
    return None


def _group_restriction(effects, key=None, template=None):
    for h, s in _specs(effects):
        m = _mod(s)
        if m is None or m.kind is not ModKind.PROHIBIT or s.target is not None \
                or m.action not in ("attack", "block", "attack_or_block"):
            continue
        if s.subject is None or s.subject.kind is not SelectorKind.FILTER \
                or s.duration is None or \
                s.duration.kind is DurationKind.WHILE_SOURCE_ON_BATTLEFIELD:
            continue
        f = dict(s.subject.filter or ())
        if set(f) == {"types"}:
            continue          # the unqualified lock (combat prevention)
        return {"controller": "opponents" if f.get("controller") ==
                "opponent" else "any"}
    return None


def _attached_static(effects):
    for h in _face(effects, 0):
        if h.kind is not HostKind.STATIC:
            continue
        for s in h.specs:
            if s.verb is Verb.CONTINUOUS and \
                    _ref_kind(s.ref) is RefKind.ATTACHED and \
                    s.condition is None and s.amount is None and \
                    h.text.startswith("equipped"):
                yield s


def _equip_pt(effects):
    for s in _attached_static(effects):
        m = _mod(s)
        if m is not None and m.kind is ModKind.MODIFY_PT:
            p, t = _pt(m)
            if p is not None and t is not None:
                return p, t
    return 0, 0


def _equip_power(effects, key=None, template=None):
    return _equip_pt(effects)[0]


def _equip_toughness(effects, key=None, template=None):
    return _equip_pt(effects)[1]


def _equip_keywords(effects, key=None, template=None):
    out = set()
    for s in _attached_static(effects):
        m = _mod(s)
        if m is not None and m.kind is ModKind.ADD_KEYWORDS:
            out.update(k for k in _keywords_of(m)
                       if _legacy_equip_grantable(k))
    return frozenset(out)


# The combat keywords legacy's equipment grant reads
# (`oracle_parser._EQUIP_GRANTABLE_KEYWORDS`), in the grammar's spelling.
_EQUIP_GRANTABLE = frozenset({
    "first_strike", "double_strike", "deathtouch", "lifelink", "trample",
    "haste", "vigilance", "reach", "menace", "flying", "hexproof",
    "indestructible"})


def _legacy_equip_grantable(keyword: str) -> bool:
    """Legacy's equipment keyword grant reads only the combat keywords of
    its closed list; ward, protection and parameterised keywords are not
    granted."""
    return keyword in _EQUIP_GRANTABLE


def _team_keyword_grant(effects, key=None, template=None):
    for h in _face(effects, 0):
        if h.kind is not HostKind.STATIC:
            continue
        for s in h.specs:
            m = _mod(s)
            if s.verb is Verb.CONTINUOUS and m is not None and \
                    m.kind is ModKind.ADD_KEYWORDS and _team_filter(s) and \
                    s.condition is None:
                other = bool(dict(s.subject.filter or ()).get("other"))
                return {"keywords": frozenset(_keywords_of(m)),
                        "others_only": other}
    return None


def _act_continuous(effects, key):
    h = _activated_host(effects, key)
    if h is None:
        return None
    specs = _unwrap_delayed(h.specs)
    if len(specs) != 1 or specs[0].verb is not Verb.CONTINUOUS:
        return None
    return specs[0]


def _act_pump_self(effects, key=None, template=None):
    s = _act_continuous(effects, key)
    m = _mod(s) if s is not None else None
    if m is None or m.kind is not ModKind.MODIFY_PT or not _is_self(s.ref):
        return None
    p, t = _pt(m)
    if p is None or t is None or p < 0 or t < 0:
        return None
    return (p, t)


def _act_animate_self(effects, key=None, template=None):
    h = _activated_host(effects, key)
    if h is None:
        return False
    return any(s.verb is Verb.CONTINUOUS and _is_self(s.ref) and
               _mod(s) is not None and
               _mod(s).kind in (ModKind.SET_TYPES, ModKind.ADD_TYPES) and
               s.duration is not None and
               s.duration.kind is DurationKind.THIS_TURN
               for s in iter_specs(h.specs))


def _act_haste_target(effects, key=None, template=None):
    s = _act_continuous(effects, key)
    m = _mod(s) if s is not None else None
    return (m is not None and m.kind is ModKind.ADD_KEYWORDS and
            s.target is not None and _keywords_of(m) == ("haste",))


def _act_untap(effects, key=None, template=None):
    h = _activated_host(effects, key)
    if h is None:
        return False
    specs = _unwrap_delayed(h.specs)
    return len(specs) == 1 and specs[0].verb is Verb.UNTAP


def _grants_haste_activation(effects, key=None, template=None):
    return any(_act_haste_target(effects, h.activation_index)
               for h in _face(effects, 0)
               if h.kind is HostKind.ACTIVATED)


# ── E6: stack, mana and costs ──────────────────────────────────────────

def _counter_specs(effects):
    """COUNTER specs of the first face in printed order, every host and
    mode (legacy's OracleTextParser reads the whole text)."""
    for h in effects.walk():
        if h.face != 0:
            continue
        for s in iter_specs(h.specs):
            if s.verb is Verb.COUNTER:
                yield h, s


# The spell kinds legacy's counter effect names (`OracleEffect.target_type`).
_COUNTER_KINDS = frozenset({"spell", "creature_spell", "noncreature_spell",
                            "instant_or_sorcery_spell"})


def _legacy_counter_kind(req) -> Optional[str]:
    """Legacy types a counterspell only when its target is one of four
    spell kinds; "target instant spell" / "target sorcery spell" and
    ability targets are not counter effects to it."""
    if req is None or len(req.types) != 1:
        return None
    kind = next(iter(req.types))
    return kind if kind in _COUNTER_KINDS else None


def _is_counterspell(effects, key=None, template=None):
    return any(_legacy_counter_kind(s.target) for _, s in
               _counter_specs(effects))


def _counter_target_kind(effects, key=None, template=None):
    for _, s in _counter_specs(effects):
        kind = _legacy_counter_kind(s.target)
        if kind:
            return kind
    return ""


def _counter_tax(effects, key=None, template=None):
    for _, s in _counter_specs(effects):
        c = s.condition
        if c is not None and c.kind is ConditionKind.UNLESS and \
                c.cost is not None:
            mana = dict(dict(c.cost.items).get("mana", ()))
            return mana.get("generic", 0)
    return 0


def _counter_upgrade(effects, key=None, template=None):
    for _, s in _counter_specs(effects):
        if not s.replaces or s.condition is None:
            continue
        f = s.condition.filter
        bounds = f.stat_bounds if f is not None else ()
        for stat, op, amount in bounds:
            if stat == "power" and op == ">=" and _lit(amount) is not None \
                    and "creature" in f.types:
                return {"creature_power_at_least": _lit(amount)}
    return None


def _counters_colorless_only(effects, key=None, template=None):
    return any(_legacy_colorless_counter(s) for _, s in _specs(effects))


def _mana_specs(h):
    return [s for s in iter_specs(h.specs)
            if s.verb is Verb.ADD_MANA and isinstance(s.payload, ManaSpec)]


_RITUAL_COLORS = ("R", "G", "U", "B", "W", "C")
_WUBRG = ("W", "U", "B", "R", "G")
_ANY_MANA_RE = re.compile(r"\badd (\w+) mana\b")
_MANA_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def _ritual(effects, key=None, template=None):
    """Legacy's ritual read (`parse_ritual_mana`), applied to the whole
    text: the first "add" sentence with two or more pips of one colour
    gives (colour, count) -- the activation cost's pips in that sentence
    counted too (`_legacy_ritual_cost_pips`) -- else the first "add <n>
    mana" phrase anywhere gives ('any', n)."""
    first = None
    for h in _face(effects, 0):
        for s in _mana_specs(h):
            first = (h, s)
            break
        if first is not None:
            break
    if first is not None:
        h, s = first
        for c in _RITUAL_COLORS:
            n = s.payload.symbols.count(c) + _legacy_ritual_cost_pips(h, c)
            if n >= 2:
                return (c, n)
    for h in effects.walk(include_granted=True):
        if h.face != 0:
            continue
        for s in _mana_specs(h):
            m = _ANY_MANA_RE.match(s.raw)
            if m and _MANA_WORDS.get(m.group(1)):
                return ("any", _MANA_WORDS[m.group(1)])
    return None


_COST_COLOR = {"R": "red", "G": "green", "U": "blue", "B": "black",
               "W": "white", "C": "colorless"}


def _legacy_ritual_cost_pips(h: AbilityEffects, color: str) -> int:
    """Legacy counts the pips of the whole "add" sentence, so an
    activation cost printed in it ("{R}, Sacrifice ~: Add {R}{R}{R}")
    adds its own pips of that colour."""
    if h.cost is None or h.kind not in (HostKind.ACTIVATED,
                                        HostKind.MANA_ABILITY):
        return 0
    mana = dict(dict(h.cost.items).get("mana", ()))
    return int(mana.get(_COST_COLOR[color], 0) or 0)


_MANA_SYMBOL = re.compile(r"\{([^}]+)\}")
_UNIT_COLOURS = frozenset(_WUBRG) | {"C"}


def _bundle_units(choice) -> Optional[list]:
    """The units of a choice between mana bundles ("{W}{W}, {W}{U}, or
    {U}{U}"): one unit per produced mana, each the union of the bundles'
    colours, when every bundle has the same size and the bundles are
    exactly the per-mana picks of that union (CR 106.1b: a unit is the
    colours one mana can be). None when the choice is not independent per
    mana ({R}{R} or {G}{G} never gives {R}{G}) or names a non-colour."""
    bundles = [tuple(_MANA_SYMBOL.findall(c)) for c in choice]
    sizes = {len(b) for b in bundles}
    if len(sizes) != 1 or 0 in sizes:
        return None
    n = sizes.pop()
    union = sorted({c for b in bundles for c in b})
    if not set(union) <= _UNIT_COLOURS:
        return None
    printed = {tuple(sorted(b)) for b in bundles}
    picks = set(combinations_with_replacement(union, n))
    if printed != picks:
        return None
    return [list(union) for _ in range(n)]


def _units_of(spec) -> Optional[list]:
    """The units one ADD_MANA spec produces, or None when its choice is
    not a list of independent units (`_bundle_units`)."""
    p = spec.payload
    if p.choice:
        if all(len(_MANA_SYMBOL.findall(c)) == 1 for c in p.choice):
            return [list(dict.fromkeys(c.strip("{}") for c in p.choice))]
        return _bundle_units(p.choice)
    if p.any_color or "*" in p.symbols:
        return [sorted(_WUBRG)] * max(1, len(p.symbols))
    return [[c] for c in p.symbols]


def _host_units(h) -> Optional[list]:
    """Every unit a host's mana specs produce; None when one spec's
    choice is not a list of units."""
    units = []
    for s in _mana_specs(h):
        got = _units_of(s)
        if got is None:
            return None
        units.extend(got)
    return units


def _mana_units(effects, key=None, template=None):
    units = []
    for h in _face(effects, 0):
        if h.kind is HostKind.MANA_ABILITY and h.cost is not None and \
                dict(h.cost.items).get("tap_self"):
            got = _host_units(h)
            if got is None:
                return []           # refused: the legacy default
            units.extend(got)
    return units


def _sacrifice_mana_units(effects, key=None, template=None):
    for h in _face(effects, 0):
        if h.kind is HostKind.MANA_ABILITY and h.cost is not None and \
                dict(h.cost.items).get("sacrifice_self"):
            return _host_units(h) or []
    return []


def _colorless_count(spec) -> Optional[int]:
    """The amount of a plain colorless ADD_MANA spec, None otherwise."""
    p = spec.payload if spec.verb is Verb.ADD_MANA else None
    if not isinstance(p, ManaSpec) or p.choice or p.any_color or \
            not p.symbols or any(c != "C" for c in p.symbols):
        return None
    return len(p.symbols)


def _conditional_mana(effects, key=None, template=None):
    """A mana ability's INSTEAD upgrade (CR 614.1a) under a condition on
    what you control: {"bonus": the extra colorless mana the upgrade adds
    over the base it replaces}. Legacy's other keys (its condition label
    and the required land names) have no spec counterpart; the record
    compares on `bonus` only."""
    for h in _face(effects, 0):
        if h.kind is not HostKind.MANA_ABILITY:
            continue
        for s in h.specs:
            c = s.condition
            if not s.replaces or c is None or c.filter is None or \
                    c.filter.controller != "you":
                continue
            base_i = s.replaces[0]
            if not 0 <= base_i < len(h.specs):
                continue
            up, base = _colorless_count(s), _colorless_count(h.specs[base_i])
            if up is not None and base is not None and up > base:
                return {"bonus": up - base}
    return None


def _cost_deltas(effects, zone: str):
    for h in _face(effects, 0):
        if h.kind is HostKind.STATIC and h.from_zone == zone:
            for m in h.cost_modifiers:
                yield h, m
            for s in h.specs:
                m = _mod(s)
                if m is not None and m.kind is ModKind.COST_DELTA:
                    yield h, m


def _cost_reduction_amounts(effects, key=None, template=None):
    """The amounts of a face's static spell-cost reductions, in printed
    order; the subject's qualities are not compared until the grammar types
    them."""
    return tuple(_lit(_mod_get(m, "amount"))
                 for h, m in _cost_deltas(effects, "battlefield")
                 if _mod_get(m, "sign", -1) < 0
                 and _mod_get(m, "cost_of") == "cast")


def _template_reduction_amounts(template, key=None):
    return tuple(r['amount'] for r in (template.cost_reduction_rules or ()))


def _self_reduction(effects):
    for h, m in _cost_deltas(effects, "stack"):
        if _mod_get(m, "scope") == "this_spell" and \
                "for each" in h.text and "basic land type" not in h.text:
            return _lit(_mod_get(m, "amount")) or 0, h
    return 0, None


def _self_cost_amount(effects, key=None, template=None):
    """Legacy refuses a unit it cannot count live, amount and all."""
    n, _ = _self_reduction(effects)
    return n if _self_cost_unit(effects) else 0


def _self_cost_unit(effects, key=None, template=None):
    n, h = _self_reduction(effects)
    if h is None:
        return ""
    if "graveyard" in h.text and "card type" in h.text:
        return "graveyard_card_types"
    if "discarded" in h.text or "cycled" in h.text:
        return "discarded_or_cycled_this_turn"
    return ""


def _domain_reduction(effects, key=None, template=None):
    for h, m in _cost_deltas(effects, "stack"):
        if "basic land type" in h.text:
            return _lit(_mod_get(m, "amount")) or 1
    return 1 if _legacy_basic_land_type_words(effects) else 0


def _legacy_basic_land_type_words(effects) -> bool:
    """Legacy reads a domain reduction of 1 whenever the text names a
    "basic land type" and the word "less" anywhere, whatever it reduces."""
    texts = [h.text for h in _face(effects, 0)]
    return any("basic land type" in t for t in texts) and \
        any("less" in t for t in texts)


def _tap_for_mana_trigger(effects, key=None, template=None):
    for h in _triggered(effects, EventHint.TAPPED_FOR_MANA):
        if "mana_ability" in h.flags:
            units = _host_units(h)
            return None if units is None else {"units": units}
    return None


def _aura_mana_units(effects, key=None, template=None):
    for h in _triggered(effects, EventHint.TAPPED_FOR_MANA):
        if "enchanted" in h.trigger.raw:
            return _host_units(h) or []
    return []


# ── A40: printed-span views ────────────────────────────────────────────

def _printed(template, face: int, host: AbilityEffects,
             span: Tuple[int, int]) -> str:
    """The printed text behind `span` of top-level `host` of `face`,
    through the grammar's parse input and its one offset map owner."""
    from . import effect_grammar as G
    (_, texts, facts) = G.template_inputs(template)[0]
    if face >= len(texts):
        return ""
    return G.printed_span(texts, facts[face], face, host.index, span)


def _sentence_end(text: str, start: int) -> int:
    """The end of the sentence holding `start`: its period (or the line
    end, or the text end), exclusive of the period."""
    ends = [i for i in (text.find(".", start), text.find("\n", start))
            if i >= 0]
    return min(ends) if ends else len(text)


# The CR 702 keyword names of a kicker line (CR 702.33a, 702.33c).
_KICKER_KEYWORDS = frozenset({"kicker", "multikicker"})

# The kicked frame (CR 702.33d): "if <it / this spell / ~> was kicked,"
# on the normalised host text; the payoff runs from its end.
_KICKED_FRAME = re.compile(r"\bif (?:~|it|this spell|[a-z0-9' ,~]+?) was kicked,?\s+")


def _kicked_span(effects) -> Optional[Tuple[int, AbilityEffects,
                                              Tuple[int, int]]]:
    """(face, host, span) of the kicked payoff: from the end of the first
    kicked frame of the first face (a spell's CAST_FACT kicked condition,
    a SELF_CAST head's kicked intervening-if, or the frame of a host the
    grammar refused) to the end of its sentence."""
    for h in _face(effects, 0):
        m = _KICKED_FRAME.search(h.text)
        if m is not None:
            return 0, h, (m.end(), _sentence_end(h.text, m.end()))
    return None


def _legacy_domain_single_kicker(effects: CardEffects, key: Any = None
                                 ) -> bool:
    """A39 mask: legacy types the kicked clause only for a card with one
    single-cost mana kicker or multikicker line (`parse_kicker` refuses
    an "and/or" kicker and a non-mana kicker cost, and never reads another
    spell's kick)."""
    kickers = [kw for h in _face(effects, 0) if h.kind is HostKind.KEYWORD
               for kw in h.keywords if kw.name in _KICKER_KEYWORDS]
    return len(kickers) == 1 and (kickers[0].cost or "").startswith("{")


def kicked_clause(template, effects: Optional[CardEffects] = None
                  ) -> Optional[str]:
    """The kicked payoff as printed (A40): from the end of the kicked
    frame ("if this spell was kicked,") to the end of its sentence,
    reminder text stripped, printed case kept, "instead" included. A
    SELF_CAST trigger whose intervening-if is CAST_FACT kicked gives its
    body. None when no spec is gated on the kicker. `effects` is the
    template's parse when the caller holds it (default
    `template.effects`)."""
    hit = _kicked_span(template.effects if effects is None else effects)
    if hit is None:
        return None
    face, host, span = hit
    text = _printed(template, face, host, span).strip()
    return text or None


def _channel_host(effects) -> Optional[AbilityEffects]:
    for h in _face(effects, 0):
        if h.kind is HostKind.ACTIVATED and h.from_zone == "hand" and \
                h.text.startswith("channel"):
            return h
    return None


def _legacy_channel_to_face_end(effects, host: AbilityEffects
                                ) -> Tuple[AbilityEffects, ...]:
    """Legacy's channel clause runs from the first "channel -" to the end
    of the printed text, so every host printed after the channel host
    (a second channel ability included) is part of it."""
    hosts = _face(effects, 0)
    at = next(i for i, h in enumerate(hosts) if h is host)
    return hosts[at:]


def channel_clause(template, effects: Optional[CardEffects] = None) -> str:
    """The channel ability's printed host text (A40, section 8),
    lower-cased as legacy's field is, through the end of the face as
    legacy reads it; '' when the card has none. `effects` as for
    `kicked_clause`."""
    if effects is None:
        effects = template.effects
    h = _channel_host(effects)
    if h is None:
        return ""
    return "\n".join(_printed(template, 0, x, (0, len(x.text)))
                     for x in _legacy_channel_to_face_end(effects, h)
                     ).lower()


def _kicked_view(effects, key=None, template=None):
    return None if template is None else kicked_clause(template, effects)


def _channel_view(effects, key=None, template=None):
    return "" if template is None else channel_clause(template, effects)


# ── A41: host_for_override ─────────────────────────────────────────────

_QUOTE_MASK = re.compile(r"⟨q\d+⟩")
_SPACES = re.compile(r"\s+")


def override_key(text: str, facts=None) -> str:
    """The normalised form an override text and a host body share: L0
    normalisation (self-forms to `~`, reminder text stripped) with the
    card's facts, lower case, quote masks unnumbered, bullets and the
    trailing period dropped, whitespace collapsed."""
    from .effect_grammar import normalize as N
    norm = N.normalize(text or "", facts or N.Facts())
    return _host_key(norm.text)


def _host_key(text: str) -> str:
    t = _QUOTE_MASK.sub("⟨q⟩", (text or "").lower())
    t = t.replace("•", " ")
    t = _SPACES.sub(" ", t).strip()
    return t.rstrip(".").strip()


def _trigger_bodies(h: AbilityEffects):
    """(primary, secondary) head-stripped bodies of a TRIGGERED host. The
    primary body is the text after the head's comma -- what the card
    prints after its trigger condition, intervening-if and multipliers
    included. The secondary body starts at the first spec, so an override
    that drops an intervening-if or a "for each" frame still finds it, but
    only when no host prints that text as its primary body."""
    text = h.text
    raw = h.trigger.raw if h.trigger is not None else ""
    primary = ""
    if raw and text.startswith(raw):
        primary = text[len(raw):].lstrip(" ,")
    secondary = text[h.specs[0].span[0]:] if h.specs else ""
    return primary, secondary


_HostTable = Dict[str, Tuple[AbilityEffects, ...]]
_OVERRIDE_TABLES: "OrderedDict[int, Tuple[CardEffects, Tuple[_HostTable, _HostTable]]]" \
    = OrderedDict()
_OVERRIDE_TABLE_LIMIT = 4096   # bounded like the grammar memos (section 12)


def _override_table(template, effects: Optional[CardEffects] = None):
    """(primary, secondary): normalised key -> every distinct host that
    prints it, in card order. A key with more than one host is ambiguous;
    the lookup names none of them unless the trigger event narrows it."""
    if effects is None:
        effects = template.effects
    hit = _OVERRIDE_TABLES.get(id(effects))
    if hit is not None and hit[0] is effects:
        _OVERRIDE_TABLES.move_to_end(id(effects))
        return hit[1]
    primary: Dict[str, list] = {}
    secondary: Dict[str, list] = {}

    def put(table, text, host):
        k = _host_key(text)
        if not k:
            return
        hosts = table.setdefault(k, [])
        if not any(x is host for x in hosts):
            hosts.append(host)

    for face, hosts in enumerate(effects.faces):
        for h in hosts:
            for m in h.modes:
                put(primary, m.text, m)
            if h.kind is HostKind.TRIGGERED:
                body, tail = _trigger_bodies(h)
                put(primary, body, h)
                put(secondary, tail, h)
    kicked = _kicked_span(effects)
    if kicked is not None:
        _, host, (a, b) = kicked
        put(primary, host.text[a:b], host)
    channel = _channel_host(effects)
    if channel is not None:
        put(primary, channel.text, channel)
        put(primary, "\n".join(x.text for x in
                               _legacy_channel_to_face_end(effects, channel)),
            channel)
    for h in _face(effects, 0):
        if h.kind is HostKind.ACTIVATED and h.from_zone == "hand":
            put(primary, h.text, h)
    tables = tuple({k: tuple(v) for k, v in t.items()}
                   for t in (primary, secondary))
    _OVERRIDE_TABLES[id(effects)] = (effects, tables)
    if len(_OVERRIDE_TABLES) > _OVERRIDE_TABLE_LIMIT:
        _OVERRIDE_TABLES.popitem(last=False)
    return tables


def host_for_override(template, text: str, *,
                      event: Optional[EventHint] = None,
                      effects: Optional[CardEffects] = None
                      ) -> Optional[AbilityEffects]:
    """The host an `oracle_override` text names (section 11, A41): a MODE
    host for a mode clause, the host holding the kicked payoff for the
    kicked clause, the channel ACTIVATED host for the channel clause, a
    TRIGGERED host for its head-stripped body. A lookup in a table built
    once per parsed `CardEffects`; it never parses.

    A host's printed body wins over a body that only starts at another
    host's first spec. A text two hosts print is ambiguous: `event` (the
    trigger event the handler resolves for) keeps only the TRIGGERED hosts
    of that event, and a text still naming more than one host names none.
    None for a text the card does not print. `effects` is the
    template's parse when the caller holds it (default
    `template.effects`)."""
    from . import effect_grammar as G
    if not text:
        return None
    tables = _override_table(template, effects)
    facts = G.template_inputs(template)[1]
    key = override_key(text, facts[0] if facts else None)
    for table in tables:
        hosts = table.get(key, ())
        if event is not None:
            hosts = tuple(h for h in hosts if h.trigger is not None and
                          event in h.trigger.event_hints)
        if hosts:
            return hosts[0] if len(hosts) == 1 else None
    return None


# ── Tier B / C predicate builders ─────────────────────────────────────

def _any_spec(pred: Callable[[AbilityEffects, Any], bool], name: str):
    def view(effects, key=None, template=None):
        return any(pred(h, s) for h, s in _specs(effects))
    view.__name__ = name
    return view


def _any_head(*hints: EventHint, verbs: Tuple[Verb, ...] = (),
              name: str = "_head"):
    def view(effects, key=None, template=None):
        for h in _triggered(effects, *hints):
            if not verbs or any(s.verb in verbs for s in iter_specs(h.specs)):
                return True
        return False
    view.__name__ = name
    return view


def _ordinal_head(h) -> Optional[int]:
    raw = h.trigger.raw if h.trigger is not None else ""
    for word, n in (("second", 2), ("third", 3), ("fourth", 4)):
        if f" {word} " in f" {raw} ":
            return n
    return None


def _ordinal_cast_trigger(effects, key=None, template=None):
    for h in _triggered(effects, EventHint.SPELL_CAST):
        n = _ordinal_head(h)
        if n is not None:
            raw = h.trigger.raw
            scope = "opponent" if "opponent" in raw else \
                "any" if "a player" in raw else "you"
            return {"ordinal": n, "caster_scope": scope, "reset": "turn"}
    return None


def _creature_dies_observer(effects, key=None, template=None):
    for h in _triggered(effects, EventHint.OTHER_DIES):
        raw = h.trigger.raw
        scope = "you" if "you control" in raw else \
            "opponent" if "opponent" in raw else "any"
        return {"scope": scope, "another": "another" in raw}
    return None


def _attack_observer(effects, key=None, template=None):
    for h in _triggered(effects, EventHint.ATTACKS_OTHER):
        raw = h.trigger.raw
        if "attacks you" not in raw:
            continue
        return {"scope": "you_or_pw" if "planeswalker" in raw else "you"}
    return None


def _cycle_amount(verb: Verb):
    def view(effects, key=None, template=None):
        for h in _triggered(effects, EventHint.CYCLE):
            for s in h.specs:
                if s.verb is verb and _lit(s.amount):
                    return _lit(s.amount)
        return 0
    view.__name__ = f"_cycle_{verb.value}"
    return view


def _landfall_ordinal(verb: Verb, ordinal: int):
    def view(effects, key=None, template=None):
        for h in _triggered(effects, EventHint.LANDFALL):
            for s in iter_specs(h.specs):
                c = s.condition
                if s.verb is verb and c is not None and \
                        c.kind is ConditionKind.RESOLUTION_ORDINAL and \
                        _lit(c.n) == ordinal:
                    if verb is Verb.ADD_MANA:
                        return tuple(sorted({
                            u for unit in (_units_of(s) or ()) for u in unit}))
                    return _lit(s.amount) or 0
        return () if verb is Verb.ADD_MANA else 0
    view.__name__ = f"_landfall_{verb.value}_{ordinal}"
    return view


def _is_type(template, t: str) -> bool:
    return any(getattr(c, "value", c) == t
               for c in getattr(template, "card_types", ()) or ())


def _etb_return_land(effects, key=None, template=None) -> bool:
    """A land's entry trigger (CR 603.6a) whose resolution is a mandatory
    MOVE of a land you control to its owner's hand (the bounce-land
    karoo): a TRIGGERED(SELF_ENTERS) host without an intervening-if whose
    specs hold that MOVE, not optional."""
    if not _is_type(template, "land"):
        return False
    for h in _triggered(effects, EventHint.SELF_ENTERS):
        if h.trigger.intervening_if is not None:
            continue
        for s in h.specs:
            f = s.filter
            if s.verb is Verb.MOVE and not s.optional and \
                    s.dest is not None and s.dest.zone == "hand" and \
                    f is not None and f.types == frozenset({"land"}) and \
                    f.controller == "you":
                return True
    return False


# ── The derivation table (10.1) ────────────────────────────────────────

def _attr(name: str, default=None):
    def legacy(template, key=None):
        return getattr(template, name, default)
    legacy.__name__ = f"_read_{name}"
    return legacy


def _activated_ability(template, key):
    return next((a for a in getattr(template, "activated_abilities", None)
                 or () if a.index == key), None)


def _read_activated_kind(kind: str, value: Callable[[Any], Any],
                      none: Any = None):
    def legacy(template, key=None):
        ab = _activated_ability(template, key)
        if ab is None or ab.effect_kind.name != kind:
            return none
        return value(ab)
    return legacy


def _read_activated_attr(attr: str):
    def legacy(template, key=None):
        ab = _activated_ability(template, key)
        return None if ab is None else getattr(ab, attr)
    return legacy


def _read_mode_removal(template, key=None):
    modes = getattr(template, "modes", None) or ()
    if not isinstance(key, int) or not 0 <= key < len(modes):
        return None
    return modes[key].get("removal")


def _read_loyalty_attr(attr: str, none: Any):
    def legacy(template, key=None):
        ab = _read_loyalty(template, key)
        if ab is None or ab.effect_kind.name != "RETURN_TO_HAND":
            return none
        return getattr(ab, attr)
    return legacy


_ROWS = []


def _row(field, family, tier, estep, view, *, scope=SCOPE_CARD, legacy=None,
         domain=None, compare=COMPARE_EQ, partial=False, default=None,
         note=""):
    if legacy is None:
        legacy = _attr(field, default)
    _ROWS.append(FieldDerivation(
        field=field, family=family, scope=scope, tier=tier, estep=estep,
        view=view, strict=STRICT[family], legacy=legacy, domain=domain,
        compare=compare, partial=partial, default=default, note=note))


def _act_row(kind, family, estep, view, value, *, partial=False,
             default=None):
    """A record per classified activation kind: its payload (amount, P/T)
    or True, `default` when the ability is not of that kind."""
    _row(f"ActivatedAbility.effect_kind[{kind}]", family, "A", estep, view,
         scope=SCOPE_ACTIVATED,
         legacy=_read_activated_kind(kind, value, default),
         partial=partial, default=default)


def _loyalty_row(kind, family, estep):
    _row(f"LoyaltyAbility.effect_kind[{kind}]", family, "A", estep,
         _loyalty_kind_view(kind), scope=SCOPE_LOYALTY,
         legacy=_read_loyalty_kind(kind), default=False)


D, R, F, T, P, S, H = (FAMILY_DAMAGE, FAMILY_REMOVAL, FAMILY_CARD_FLOW,
                       FAMILY_TOKENS_COUNTERS, FAMILY_PUMP_RESTRICT,
                       FAMILY_STACK_MANA, FAMILY_TRIGGER)

# E1 damage
_row("direct_damage_data", D, "A", "E1", _direct_damage)
_act_row("DAMAGE_ANY_TARGET", D, "E1", _act_damage, lambda a: a.amount)
_loyalty_row("DAMAGE", D, "E1")
_loyalty_row("GAIN_LIFE_AND_DRAW", D, "E1")
_row("tap_damage", D, "A", "E1", _tap_damage, default=0)
_row("has_energy_damage_target", D, "A", "E1", _energy_damage, default=False)
_row("has_x_damage", D, "B", "E1", _any_spec(
    lambda h, s: s.verb is Verb.DAMAGE and s.amount is not None and
    s.amount.kind is AmountKind.X, "_x_damage"), default=False)
_row("deals_targeted_damage", D, "B", "E1", _any_spec(
    lambda h, s: s.verb is Verb.DAMAGE and s.target is not None,
    "_targeted_damage"), default=False)
_row("has_damage_equal_scaling", D, "B", "E1", _any_spec(
    lambda h, s: s.verb is Verb.DAMAGE and s.amount is not None and
    s.amount.kind is AmountKind.EQUAL_TO, "_equal_damage"), default=False)
_row("has_sacrifice_for_damage", D, "B", "E1", _any_spec(
    lambda h, s: s.verb is Verb.DAMAGE and h.cost is not None and
    bool(dict(h.cost.items).get("sacrifice_type")), "_sac_damage"),
    default=False, partial=True)
_row("can_target_player", D, "B", "E1", _any_spec(
    lambda h, s: bool(_slot_types(s) & {"any", "player", "opponent"}),
    "_targets_player"), default=False)
_row("can_target_planeswalker", D, "B", "E1", _any_spec(
    lambda h, s: bool(_slot_types(s) & {"any", "planeswalker"}),
    "_targets_planeswalker"), default=False)
_row("ordinal_cast_trigger", D, "C", "E1", _ordinal_cast_trigger,
     compare=("ordinal", "caster_scope", "reset"), partial=True)
_row("creature_dies_observer", D, "C", "E1", _creature_dies_observer,
     compare=("scope", "another"), partial=True)
_row("attack_observer", D, "C", "E1", _attack_observer,
     compare=("scope",), partial=True)
_row("has_cycling_watch_trigger", D, "C", "E1",
     _any_head(EventHint.CYCLE, name="_cycle_head"), default=False)
_row("cycling_watch_trigger_damage", D, "C", "E1", _cycle_amount(Verb.DAMAGE),
     default=0)
_row("cycling_watch_trigger_life_gain", D, "C", "E1",
     _cycle_amount(Verb.GAIN_LIFE), default=0)
_row("landfall_first_life_gain", D, "C", "E1",
     _landfall_ordinal(Verb.GAIN_LIFE, 1), default=0)
_row("landfall_third_damage", D, "C", "E1",
     _landfall_ordinal(Verb.DAMAGE, 3), default=0)
_row("landfall_second_mana_colors", D, "C", "E1",
     _landfall_ordinal(Verb.ADD_MANA, 2), default=())
_row("has_opponent_cast_damage", D, "C", "E1", _any_head(
    EventHint.SPELL_CAST, verbs=(Verb.DAMAGE, Verb.LOSE_LIFE),
    name="_opponent_cast_damage"), default=False, partial=True)
_row("has_another_creature_enters_lifegain", D, "C", "E1", _any_head(
    EventHint.OTHER_ENTERS, verbs=(Verb.GAIN_LIFE,),
    name="_other_enters_gain"), default=False)

# E2 removal
_row("targeted_removal_data", R, "A", "E2", _targeted_removal)
_row("modes[removal]", R, "A", "E2", _mode_removal, scope=SCOPE_MODE,
     legacy=_read_mode_removal)
_row("etb_targeted_removal_data", R, "A", "E2", _etb_removal,
     domain=_legacy_domain_etb_removal)
_row("removal_mv_condition", R, "A", "E2", _removal_mv_condition)
_row("board_sweep_data", R, "A", "E2", _board_sweep)
_row("etb_return_land", R, "C", "E2", _etb_return_land, default=False)
_row("bounce_target", R, "A", "E2", _bounce_target)
_row("land_destruction_data", R, "A", "E2", _land_destruction, partial=True)
_row("destroys_target_land", R, "A", "E2", _destroys_target_land,
     default=False)
_row("mass_graveyard_return", R, "A", "E2", _mass_graveyard_return,
     default=False)
_row("has_symmetric_reanimation", R, "B", "E2", _symmetric_reanimation,
     default=False)
_row("ActivatedAbility.graveyard_exile_data", R, "A", "E2", _graveyard_exile,
     scope=SCOPE_ACTIVATED, legacy=_read_activated_attr(
         "graveyard_exile_data"), compare=("scope",), partial=True)
_act_row("EXILE_FROM_GRAVEYARD", R, "E2",
         lambda e, k=None, t=None: _graveyard_exile(e, k) is not None,
         lambda a: True,
         default=False)
_loyalty_row("RETURN_TO_HAND", R, "E2")
_loyalty_row("TUCK_TARGET_INTO_LIBRARY", R, "E2")
_loyalty_row("EMBLEM_EXILE_PERMANENT", R, "E2")
_row("LoyaltyAbility.target", R, "A", "E2", _loyalty_target,
     scope=SCOPE_LOYALTY, legacy=_read_loyalty_attr("target", None))
_row("LoyaltyAbility.draws", R, "A", "E2", _loyalty_draws,
     scope=SCOPE_LOYALTY, legacy=_read_loyalty_attr("draws", 0), default=0)
_row("etb_exile_returns_on_leave", R, "A", "E2", _etb_exile_returns,
     default=False)
_row("can_destroy_artifact", R, "B", "E2", _any_spec(
    lambda h, s: s.verb in (Verb.DESTROY, Verb.EXILE) and
    bool(_slot_types(s) & {"artifact", "permanent", "permanent_nonland"}),
    "_hits_artifact"), default=False)
_row("can_destroy_enchantment", R, "B", "E2", _any_spec(
    lambda h, s: s.verb in (Verb.DESTROY, Verb.EXILE) and
    bool(_slot_types(s) & {"enchantment", "permanent", "permanent_nonland"}),
    "_hits_enchantment"), default=False)
_row("can_destroy_nonland_permanent", R, "B", "E2", _any_spec(
    lambda h, s: s.verb is Verb.DESTROY and
    bool(_slot_types(s) & {"permanent_nonland", "permanent"}),
    "_destroys_nonland"), default=False)
_row("can_exile_permanent", R, "B", "E2", _any_spec(
    lambda h, s: s.verb is Verb.EXILE and s.target is not None and
    s.target.zone == "battlefield", "_exiles_permanent"), default=False)
_row("exile_hits_noncreature", R, "B", "E2", _any_spec(
    lambda h, s: s.verb is Verb.EXILE and bool(_slot_types(s) - {"creature"})
    and s.target is not None and s.target.zone == "battlefield",
    "_exile_noncreature"), default=False)
_row("has_destroy_or_exile", R, "B", "E2", lambda e, k=None, t=None:
     _has_verb(e, Verb.DESTROY, Verb.EXILE), default=False)
_row("has_graveyard_hate", R, "B", "E2", _any_spec(
    lambda h, s: s.verb is Verb.EXILE and (
        (s.filter is not None and s.filter.zone == "graveyard") or
        (s.target is not None and s.target.zone == "graveyard")),
    "_graveyard_exile_any"), default=False)
_row("has_graveyard_target", R, "B", "E2", _any_spec(
    lambda h, s: s.target is not None and s.target.zone == "graveyard",
    "_graveyard_target"), default=False)
_row("requires_creature_target", R, "B", "E2", lambda e, k=None, t=None: (
    e.spell(0) is not None and bool(e.spell(0).targets) and
    all(r.types == frozenset({"creature"}) for r in e.spell(0).targets)),
    default=False, partial=True)
_row("reanimates_from_graveyard", R, "B", "E2", _any_spec(
    lambda h, s: _gy_to_battlefield(s) and s.target is not None and
    s.target.zone == "graveyard", "_reanimates"), default=False)
_row("has_exile_own_creature", R, "B", "E2", _any_spec(
    lambda h, s: s.verb is Verb.EXILE and s.target is not None and
    s.target.owner_scope == "you" and "creature" in s.target.types,
    "_exile_own"), default=False)
_row("has_mana_value_wipe", R, "B", "E2", _any_spec(
    lambda h, s: s.verb in (Verb.DESTROY, Verb.EXILE) and
    s.filter is not None and any(b[0] == "mana_value"
                                 for b in s.filter.stat_bounds) and
    s.target is None, "_mv_wipe"), default=False)

# E3 card flow
_row("loot_data", F, "A", "E3", _loot)
_row("hand_attack_data", F, "A", "E3", _hand_attack,
     compare=("chooser", "target"), partial=True)
_row("library_dig_data", F, "A", "E3", _library_dig,
     compare=("rest_destination",), partial=True)
_row("hand_refill", F, "A", "E3", _hand_refill, partial=True)
_row("x_creature_tutor_data", F, "A", "E3", _x_creature_tutor,
     compare=("dest", "types", "colors", "mv_bound_is_x", "tapped",
              "self_shuffle_into_library"), partial=True)
_act_row("TUTOR_TO_HAND", F, "E3", _act_tutor("hand"), lambda a: True,
         default=False)
_act_row("TUTOR_CREATURE_TO_BATTLEFIELD", F, "E3", _act_tutor("battlefield"),
         lambda a: True,
         default=False)
_row("ActivatedAbility.tutor_data", F, "A", "E3", _act_tutor_data,
     scope=SCOPE_ACTIVATED, legacy=_read_activated_attr("tutor_data"),
     compare=("dest",), partial=True)
_row("fetchland", F, "A", "E3", _fetchland, partial=True)
_row("is_land_sacrifice_tutor", F, "A", "E3", _land_sacrifice_tutor,
     default=False)
_act_row("DRAW_N", F, "E3", _act_draw, lambda a: a.amount)
_row("ActivatedAbility.delayed_timing", F, "B", "E3", _act_delay,
     scope=SCOPE_ACTIVATED, legacy=_read_activated_attr("delayed_timing"))
_row("cycling_variant_data", F, "C", "E3", _cycling_variant, partial=True)
_row("has_draw_effect", F, "B", "E3", lambda e, k=None, t=None:
     _has_verb(e, Verb.DRAW), default=False)
_row("has_discard_effect", F, "B", "E3", lambda e, k=None, t=None:
     _has_verb(e, Verb.DISCARD), default=False)
_row("is_tutor", F, "B", "E3", lambda e, k=None, t=None:
     _has_verb(e, Verb.SEARCH), default=False)
_row("has_surveil", F, "B", "E3", lambda e, k=None, t=None:
     _has_verb(e, Verb.SURVEIL), default=False)
_row("has_scry", F, "B", "E3", lambda e, k=None, t=None:
     _has_verb(e, Verb.SCRY), default=False)
_row("has_look_hand_selection", F, "B", "E3", _any_spec(
    lambda h, s: s.verb in (Verb.LOOK, Verb.REVEAL) and s.filter is not None
    and s.filter.zone == "hand" and s.target is None and
    _ref_kind(s.actor) is RefKind.TARGET, "_look_hand"), default=False,
    partial=True)
_row("has_cast_spell_draw", F, "B", "E3", _any_head(
    EventHint.SPELL_CAST, verbs=(Verb.DRAW,), name="_cast_draw"),
    default=False)
_row("has_dual_land_search", F, "B", "E3", _any_spec(
    lambda h, s: s.verb is Verb.SEARCH and s.filter is not None and
    len(s.filter.subtypes) >= 2, "_dual_search"), default=False,
    partial=True)
_row("has_sacrifice_search_land", F, "B", "E3", _any_spec(
    lambda h, s: s.verb is Verb.SEARCH and s.filter is not None and
    "land" in (s.filter.types | s.filter.subtypes) and h.cost is not None and
    bool(dict(h.cost.items).get("sacrifice_self")), "_sac_search"),
    default=False, partial=True)
_row("has_graveyard_recursion", F, "B", "E3", _any_spec(
    lambda h, s: s.verb is Verb.MOVE and s.dest is not None and
    s.dest.zone in ("hand", "battlefield") and (
        (s.target is not None and s.target.zone == "graveyard") or
        (s.filter is not None and s.filter.zone == "graveyard")),
    "_recursion"), default=False)
_row("has_may_play_or_cast", F, "B", "E3", _any_spec(
    lambda h, s: _mod(s) is not None and _mod(s).kind is ModKind.PERMIT,
    "_permit"), default=False, partial=True)
_row("has_recurring_draw_trigger", F, "B", "E3", _any_head(
    EventHint.BEGINNING_OF, verbs=(Verb.DRAW,), name="_upkeep_draw"),
    default=False)

# E4 tokens and counters
_act_row("PUT_COUNTER_SELF", T, "E4", _act_counter("self"),
         lambda a: a.amount)
_act_row("PUT_COUNTER_TARGET", T, "E4", _act_counter("target"),
         lambda a: a.amount)
_act_row("PUT_COUNTER_TEAM", T, "E4", _act_counter("team"),
         lambda a: a.amount)
_act_row("ADAPT", T, "E4", _act_adapt, lambda a: a.amount)
_row("ActivatedAbility.put_counter_data", T, "A", "E4", _act_counter_data,
     scope=SCOPE_ACTIVATED, legacy=_read_activated_attr("put_counter_data"),
     compare=("amount", "self"), partial=True)
_row("counter_placement_trigger", T, "C", "T", _counter_placement_trigger,
     compare=("effect",), partial=True)
_row("enters_type_counter", T, "C", "T", _enters_type_counter,
     compare=("counter_power",), partial=True)
_row("cast_trigger_token", T, "C", "T", _cast_trigger_token,
     compare=("count",), partial=True)
_row("has_token_effect", T, "B", "E4", lambda e, k=None, t=None:
     _has_verb(e, Verb.CREATE_TOKEN), default=False)
_row("has_scaling_token_finisher", T, "B", "E4", _scaling_token,
     default=False, partial=True)
_row("has_x_counter_scaling", T, "B", "E4", _any_spec(
    lambda h, s: s.verb is Verb.PUT_COUNTERS and s.amount is not None and
    s.amount.kind is AmountKind.X, "_x_counters"), default=False)
_row("energy_production", T, "B", "E4", _energy_production, default=0,
     partial=True)
_row("has_energy_production", T, "B", "E4", lambda e, k=None, t=None:
     _energy_production(e) > 0, default=False)
_row("has_lifegain_token_trigger", T, "C", "T", _any_head(
    EventHint.OTHER, verbs=(Verb.CREATE_TOKEN,), name="_lifegain_token"),
    default=False, partial=True)
_row("saga_chapter_one_material", T, "C", "E4", _chapter_one_token,
     default=False, partial=True)
_row("has_charge_counter_ability", T, "B", "E4", _any_spec(
    lambda h, s: s.verb is Verb.PUT_COUNTERS and isinstance(
        s.payload, CounterSpec) and "charge" in s.payload.kinds,
    "_charge_counters"), default=False, partial=True)

# E5 pump, grants, restrictions
_row("pump_spell_power", P, "A", "E5", _pump_power, default=0)
_row("pump_spell_toughness", P, "A", "E5", _pump_toughness, default=0)
_row("pump_spell_keyword", P, "A", "E5", _pump_keyword, default="")
_row("pump_spell_keywords", P, "A", "E5", _pump_keywords, default=())
_row("team_pump_data", P, "A", "E5", _team_pump,
     compare=("trigger", "power", "toughness", "keywords", "others_only"),
     partial=True)
_row("next_turn_effect", P, "A", "E5", _next_turn_effect,
     compare=("kind",), partial=True)
_row("turn_scoped_restriction", P, "A", "E5", _turn_scoped_restriction,
     partial=True)
_row("cast_prohibition", P, "A", "E5", _cast_prohibition, partial=True)
_row("object_restriction", P, "A", "E5", _object_restriction,
     compare=("count", "duration"), partial=True)
_row("group_restriction", P, "A", "E5", _group_restriction,
     compare=("controller",), partial=True)
_row("equip_power_grant", P, "A", "E5", _equip_power, default=0)
_row("equip_toughness_grant", P, "A", "E5", _equip_toughness, default=0)
_row("equip_keyword_grant", P, "A", "E5", _equip_keywords,
     default=frozenset())
_row("team_keyword_grant", P, "A", "E5", _team_keyword_grant, partial=True)
_act_row("PUMP_SELF_UEOT", P, "E5", _act_pump_self,
         lambda a: (a.power_mod, a.toughness_mod))
_act_row("ANIMATE_SELF_UEOT", P, "E5", _act_animate_self, lambda a: True,
         default=False)
_act_row("GRANT_HASTE_TARGET", P, "E5", _act_haste_target, lambda a: True,
         default=False)
_act_row("UNTAP_TARGET_PERMANENT", P, "E5", _act_untap, lambda a: True,
         default=False)
_loyalty_row("DRAW_AND_UNTAP_LANDS", P, "E5")
_row("grants_haste_activation", P, "B", "E5", _grants_haste_activation,
     default=False)
_row("has_pump_grant", P, "B", "E5", _any_spec(
    lambda h, s: _mod(s) is not None and _mod(s).kind is ModKind.MODIFY_PT
    and s.duration is not None and s.duration.kind is DurationKind.THIS_TURN,
    "_pump_grant"), default=False, partial=True)

# E6 stack, mana, costs
_row("is_counterspell", S, "A", "E6", _is_counterspell, default=False)
_row("counter_target_kind", S, "A", "E6", _counter_target_kind, default="")
_row("counter_tax_amount", S, "A", "E6", _counter_tax, default=0)
_row("counter_upgrade_condition", S, "A", "E6", _counter_upgrade)
_row("counters_colorless_only", S, "A", "E6", _counters_colorless_only,
     default=False)
_row("targets_creature_spell", S, "B", "E6", _any_spec(
    lambda h, s: "creature_spell" in _slot_types(s), "_creature_spell_slot"),
    default=False)
_row("targets_planeswalker_spell", S, "B", "E6", _any_spec(
    lambda h, s: "planeswalker_spell" in _slot_types(s),
    "_planeswalker_spell_slot"), default=False, partial=True)
_row("ritual_mana", S, "A", "E6", _ritual)
_row("mana_units", S, "A", "E6", _mana_units, default=[], partial=True)
_row("sacrifice_mana_units", S, "A", "E6", _sacrifice_mana_units,
     default=[], partial=True)
_row("conditional_mana", S, "A", "E6", _conditional_mana,
     compare=("bonus",), partial=True)
_row("cost_reduction_rules", S, "A", "E6", _cost_reduction_amounts,
     legacy=_template_reduction_amounts, default=(), partial=True)
_row("self_cost_reduction_amount", S, "A", "E6", _self_cost_amount,
     default=0)
_row("self_cost_reduction_unit", S, "A", "E6", _self_cost_unit, default="")
_row("domain_reduction", S, "A", "E6", _domain_reduction, default=0)
_row("kicked_clause", S, "A", "E6", _kicked_view,
     domain=_legacy_domain_single_kicker)
_row("channel_clause", S, "A", "E6", _channel_view, default="")
_row("aura_mana_units", S, "C", "E6", _aura_mana_units, default=[],
     partial=True)
_row("tap_for_mana_trigger", S, "C", "E6", _tap_for_mana_trigger,
     compare=("units",), partial=True)

# Stage T: trigger heads (CR 603.2), the presence predicates the AI reads.
_row("has_attack_trigger", H, "C", "T", _any_head(
    EventHint.SELF_ATTACKS, name="_attack_head"), default=False)
_row("has_combat_damage_trigger", H, "C", "T", _any_head(
    EventHint.COMBAT_DAMAGE_TO_PLAYER, name="_combat_damage_head"),
    default=False, partial=True)
_row("has_combat_damage_player_trigger", H, "C", "T", _any_head(
    EventHint.COMBAT_DAMAGE_TO_PLAYER, name="_combat_damage_player_head"),
    default=False)
_row("has_landfall", H, "C", "T", _any_head(
    EventHint.LANDFALL, name="_landfall_head"), default=False)
_row("has_cast_trigger", H, "C", "T", _any_head(
    EventHint.SELF_CAST, EventHint.SPELL_CAST, name="_cast_head"),
    default=False)
_row("has_noncreature_spell_cast_trigger", H, "C", "T", lambda e, k=None,
     t=None: any("noncreature" in h.trigger.raw for h in _triggered(
         e, EventHint.SPELL_CAST)), default=False)
_row("has_self_trigger", H, "C", "T", _any_head(
    EventHint.SELF_ENTERS, EventHint.SELF_DIES, EventHint.SELF_ATTACKS,
    EventHint.SELF_LEAVES, name="_self_head"), default=False, partial=True)
_row("has_recurring_trigger", H, "C", "T", _any_head(
    EventHint.BEGINNING_OF, name="_recurring_head"), default=False)
_row("has_another_creature_enters_trigger", H, "C", "T", _any_head(
    EventHint.OTHER_ENTERS, name="_other_enters_head"), default=False)
_row("has_library_search_opponent_trigger", H, "C", "T", lambda e, k=None,
     t=None: any("search" in h.trigger.raw and "opponent" in h.trigger.raw
                 for h in _face(e, 0) if h.trigger is not None),
     default=False)
_row("library_search_trigger_draws_card", H, "C", "T", lambda e, k=None,
     t=None: any("search" in h.trigger.raw and any(
         s.verb is Verb.DRAW for s in iter_specs(h.specs))
         for h in _face(e, 0) if h.trigger is not None), default=False)

del D, R, F, T, P, S, H

DERIVATIONS: Mapping[str, FieldDerivation] = MappingProxyType(
    {r.field: r for r in _ROWS})
del _ROWS

# Named legacy quirk predicates and domain masks (ratchet (c)).
LEGACY_PREDICATES: Tuple[str, ...] = tuple(sorted(
    n for n, v in list(globals().items())
    if n.startswith("_legacy_") and callable(v)
    and getattr(v, "__module__", None) == __name__
    and getattr(v, "__qualname__", "") == n))


# ── Runtime carriers (section 10, "Carriers compared") ────────────────
#
# Not template fields: the shapes the resolution handlers recompute at
# runtime, compared by the tool on a game-less context. Named here so the
# report has one list.
RUNTIME_CARRIERS: Mapping[str, Tuple[str, str]] = MappingProxyType({
    # Tier B (10.1): the burn amount the resolver and the AI recompute
    # over direct_damage_data plus its upgrade.
    "effect_conditions.effective_direct_damage": (FAMILY_DAMAGE, "E1"),
    "ai.card_classes.burn_damage": (FAMILY_DAMAGE, "E1"),
    "oracle_resolver.resolve_self_cast_trigger": (FAMILY_REMOVAL, "E2"),
    "clause_resolver._reanimate_ability": (FAMILY_REMOVAL, "E2"),
    "clause_resolver._g_mass_reanimate": (FAMILY_REMOVAL, "E2"),
    "clause_resolver._bounce_shape": (FAMILY_REMOVAL, "E2"),
    "oracle_resolver._resolve_mass_mode_clause": (FAMILY_REMOVAL, "E2"),
    "clause_resolver._card_flow_effects": (FAMILY_CARD_FLOW, "E3"),
    "clause_resolver._a_hand_attack": (FAMILY_CARD_FLOW, "E3"),
    "clause_resolver._a_energy_damage": (FAMILY_DAMAGE, "E1"),
    "oracle_parser.parse_token_spec": (FAMILY_TOKENS_COUNTERS, "E4"),
    "clause_resolver._token_clause": (FAMILY_TOKENS_COUNTERS, "E4"),
    "clause_resolver._combat_prevention_shape": (FAMILY_PUMP_RESTRICT, "E5"),
    "clause_resolver._object_restriction_shape": (FAMILY_PUMP_RESTRICT, "E5"),
    # the cast_targets pseudo-field (G15): SPELL host targets against
    # the whole-oracle target parse
    "target_solver.parse": (FAMILY_STACK_MANA, "E6"),
    "card_database.OracleTextParser": (FAMILY_STACK_MANA, "E7"),
})


# ── Fields that are not effect data ────────────────────────────────────

_PRINTED = ("printed characteristic read from MTGJSON (CR 200-208), not "
            "effect text")
_KEYWORD = ("keyword ability or its printed cost (CR 702): a KEYWORD host's "
            "KeywordSpec, not a resolution effect")
_COST = ("casting cost or cost modification read at cast time (CR 601.2f), "
         "owned by the cost parsers")
_STATIC = ("static ability (CR 604) the derivation table does not list; a "
           "later StaticSpec family owns it")
_REPLACEMENT = "replacement effect (CR 614): deferred to ReplacementSpec"
_AI = ("AI classification heuristic over the whole text, not one effect "
       "shape")
_CONTAINER = ("container of per-item carriers whose own fields are "
              "derived per key (scoped records)")
_BACK = "back-face printed characteristic (CR 712), read from MTGJSON"
_LAND = ("land entry / mana-source rule read by the land manager (CR 305), "
         "not a resolution effect")

NON_EFFECT_FIELDS: Mapping[str, str] = MappingProxyType({
    # printed characteristics and bookkeeping
    "name": _PRINTED, "card_types": _PRINTED, "mana_cost": _PRINTED,
    "supertypes": _PRINTED, "subtypes": _PRINTED, "power": _PRINTED,
    "toughness": _PRINTED, "loyalty": _PRINTED, "keywords": _KEYWORD,
    "printed_keywords": _KEYWORD, "layout": _PRINTED,
    "abilities": ("OracleTextParser pipeline output: a runtime carrier "
                  "compared by the tool (E7), not a typed field"),
    "color_identity": _PRINTED, "colors": _PRINTED,
    "produces_mana": _LAND, "oracle_text": "the printed text itself",
    "tags": "tag table (decks / AI), not oracle-derived effect data",
    "has_mana_cost": _PRINTED, "phyrexian_pip_count": _PRINTED,
    "is_arcane": _PRINTED, "aura_enchant_restriction": (
        "enchant keyword restriction (CR 303.4a, 702.5)"),
    "aura_mana_color_chosen": "an entry choice of an aura (CR 614.12)",
    # land manager
    "enters_tapped": _LAND,
    "untap_life_cost": _LAND, "untap_max_other_lands": _LAND,
    "extra_land_drops": _STATIC, "land_type_bonuses": _AI,
    "has_bounce_land_oracle": (
        "whole-text phrase test for \"return a land you control\" (an "
        "activation cost or a trigger alike), an AI land-priority "
        "heuristic; the entry trigger itself is the etb_return_land record"),
    "has_mana_add_text": _AI,
    # modal containers
    "is_modal": "modal header fact (CR 700.2) of the SPELL host",
    "modal_choose_count": (
        "modal choose count (CR 700.2): AbilityEffects.choose"),
    "activated_abilities": _CONTAINER, "loyalty_abilities": _CONTAINER,
    "back_face_loyalty_abilities": _CONTAINER,
    # keyword costs and keyword facts
    "evoke_cost": _KEYWORD, "evoke_exile_color": _KEYWORD,
    "dash_cost": _KEYWORD, "warp_cost": _KEYWORD,
    "plot_cost": _KEYWORD, "escape_cost": _KEYWORD,
    "escape_exile_count": _KEYWORD, "equip_cost": _KEYWORD,
    "has_delve": _KEYWORD, "cycling_cost_data": _KEYWORD,
    "is_cascade": _KEYWORD, "splice_cost": _KEYWORD,
    "spectacle_cost": _KEYWORD, "madness_cost": _KEYWORD,
    "flashback_cost": _KEYWORD, "flashback_sacrifice_subtype": _KEYWORD,
    "kicker_cost": _KEYWORD, "multikicker": _KEYWORD,
    "modular_n": _KEYWORD, "ward_cost": _KEYWORD, "ward_life_cost": _KEYWORD,
    "protection_from_colors": _KEYWORD, "has_mobilize": _KEYWORD,
    "is_storm_spell": _KEYWORD, "has_converge": _KEYWORD,
    "alternate_exile_color": _COST, "alternate_exile_not_your_turn": _COST,
    "x_cost_data": _COST,
    "is_cost_reducer": "tag-derived (tags), not oracle-derived",
    "grants_flashback_to_gy_spells": _STATIC,
    "has_emry_graveyard_cast": _STATIC,
    # statics, replacements and AI classifications
    "power_scales_with": "characteristic-defining ability (CR 604.3)",
    "stax_class": _STATIC, "stax_forced_basic": _STATIC,
    "has_stax_ability": _STATIC, "has_pithing_needle_lock": _STATIC,
    "has_spell_chain_hate": _STATIC,
    "draw_limit": _STATIC, "prevents_graveyard_etb": _REPLACEMENT,
    "prevents_graveyard_casting": _STATIC,
    "exiles_cards_bound_for_graveyard": _REPLACEMENT,
    "graveyard_exile_replacements": _REPLACEMENT,
    "counter_placement_replacement": _REPLACEMENT,
    "has_charge_counter_wipe": _AI, "has_artifact_synergy": _AI,
    "has_scaling_effect": _AI, "has_each_opponent_effect": _AI,
    "has_lifegain_equal_power": _AI, "has_lifegain_effect": _AI,
    "has_delirium": _AI, "has_all_basic_land_types": _AI,
    "color_setting_scope": _STATIC, "has_artifact_count_scaling": _AI,
    "has_coin_flip": _AI, "has_transform_effect": _AI,
    "has_instant_or_sorcery_reference": _AI,
    "has_cc_tap_draw": _AI, "has_artifact_pump_equipment": _AI,
    "has_artifact_or_enchantment_scaling": _AI,
    "lifegain_token_type": ("token subtype name a lifegain trigger "
                            "passes to the token owner (stage T payload)"),
    # back face
    "back_face_oracle": _BACK, "back_face_loyalty": _BACK,
    "back_face_types": _BACK, "back_face_subtypes": _BACK,
    "back_face_power": _BACK, "back_face_toughness": _BACK,
    "back_face_keywords": _BACK,
    "back_face_cost_reduction_rules": (
        "the back face's static spell-cost reductions (CR 712.8e), parsed "
        "as the front face's `cost_reduction_rules` are; the grammar's "
        "face-1 view is not compared yet"),
    # ActivatedAbility carrier fields
    "ActivatedAbility.index": "carrier key: the activation ordinal",
    "ActivatedAbility.cost": ("activation cost (CR 602.1a): host.cost from "
                              "the one parse_activation_cost owner (A7)"),
    "ActivatedAbility.effect_text": "the printed effect text itself",
    "ActivatedAbility.amount": ("payload of effect_kind, derived inside the "
                                "effect_kind[...] records"),
    "ActivatedAbility.power_mod": ("payload of effect_kind[PUMP_SELF_UEOT], "
                                   "derived there"),
    "ActivatedAbility.toughness_mod": ("payload of "
                                       "effect_kind[PUMP_SELF_UEOT], "
                                       "derived there"),
    "ActivatedAbility.targets_required": ("targets come only from "
                                          "target_solver (section 13); "
                                          "compared through cast_targets"),
    "ActivatedAbility.target_requirements": ("targets come only from "
                                             "target_solver (section 13); "
                                             "compared through cast_targets"),
    "ActivatedAbility.sorcery_speed_only": ("activation restriction "
                                            "(CR 602.5): host flags"),
    "ActivatedAbility.once_each_turn": ("activation restriction "
                                        "(CR 602.5): host restrictions"),
    "ActivatedAbility.restrictions": ("activation restriction (CR 602.5): "
                                      "host restrictions"),
    "ActivatedAbility.from_battlefield": "host.from_zone fact",
    "ActivatedAbility.is_mana_ability": ("CR 605.1a host kind "
                                         "(MANA_ABILITY), A6"),
    # LoyaltyAbility carrier fields
    "LoyaltyAbility.slot": ("carrier key: the one loyalty_slot_for owner "
                            "(A12)"),
    "LoyaltyAbility.cost": "loyalty cost (CR 606.4): host.loyalty_cost",
    "LoyaltyAbility.text": "the printed ability text itself",
    "LoyaltyAbility.clause": ("clause template: its own effects are the "
                              "walker's LOYALTY host slice (section 12)"),
})
