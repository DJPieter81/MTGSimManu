#!/usr/bin/env python3
"""Legacy typed fields against their views over `CardTemplate.effects`.

Design doc: docs/design/2026-09-29_clause_and_trigger_grammar.md, section 10
(the equivalence tool, gate parity and the per-host harness) and 10.1 (the
per-field derivation table). E0: a report and a ratchet; nothing switches.

For every template of the pool and every `engine.effect_views.DERIVATIONS`
record (per key for the scoped carriers: a mode, an activated ability, a
loyalty line), the legacy value (`rec.legacy(template, key)`) is compared
with the derived value (`rec.derive(effects, key, template)` over the
eager pool parse the tool supplies; every view, the printed-span ones
included, reads that parse, so a run never parses through or pins
`template.effects`). A partial record compares its
`compare` projection. Each comparison gets one class:

* ``AGREE`` -- equal; ``MASKED_GROWTH`` -- equal because a
  `_legacy_domain_*` mask hid a value the grammar types (A39);
* an allowlist class -- the first row of
  `tools/effect_spec_equivalence_allowlist.json` whose fields include the
  field and whose printed-text pattern (or card list) matches: the known
  legacy-side disagreements section 10 records, each with a class and a
  reason (``LEGACY_*`` quirk classes, ``SEMANTIC_FIX`` with its test, the
  section-10 diff classes);
* ``DERIVED_COVERAGE_GROWTH`` -- legacy holds its empty value and the view
  types one; ``UNMODELLED_CLAUSE`` -- the view holds its empty value, legacy
  types one and a host the derivation read holds a refused (UNMODELLED,
  non-REPLACEMENT) clause (the read hosts are traced per comparison; a
  refusal on a host the derivation never read is no excuse);
* ``UNEXPLAINED`` -- everything else.

`tools/effect_spec_equivalence_baseline.json` pins the per-field class
counts. ``--check`` exits 1 when, for any field, UNEXPLAINED grows or AGREE
falls; when an allowlist row is stale (explains nothing on a full run); when
a SEMANTIC_FIX row names no existing test; when a frozen legacy snapshot
(`tools/effect_legacy_snapshots/<field>.json`, written at a family's
migration step 4) mismatches; when a switched field (one `card_database`
assigns from `effect_views`) differs from legacy on any template (A39); when
gate parity fails or its legacy-fallback count grows (``--check`` always
runs the closure); and, on a full run, when the baseline is stale -- an
UNEXPLAINED fall, an AGREE rise or a legacy-fallback fall not locked in
with ``--update`` in the same commit. An ``always`` allowlist row classes
every comparison of its cards, an equal one included (a legacy value that
depends on set iteration order).

``--closure`` lists every legacy handler (the clause_resolver registry, the
planeswalker_manager kind branches, the activation effect kinds, the ETB
carriers and `resolve_self_cast_trigger`) with the hosts its legacy gate
accepts -- their verbs and sub-ability kinds, split into registered-deck
mainboard, sideboard and pool -- the earliest family step at which every verb
has an executor (section 14) and whether the host matches the family's strict
view. ``--gate-parity`` fails if a host a legacy gate accepts is on the new
path without being strict and executable; every other accepted host is on
legacy fallback and counts toward ratchet (f) of
`tools/check_effect_parsers.py`.

Usage::

    python tools/effect_spec_equivalence.py                 # per-field table
    python tools/effect_spec_equivalence.py --check         # ratchet
    python tools/effect_spec_equivalence.py --update        # rewrite baseline
    python tools/effect_spec_equivalence.py --list --field bounce_target
    python tools/effect_spec_equivalence.py --class UNEXPLAINED --list
    python tools/effect_spec_equivalence.py --decks         # deck cards only
    python tools/effect_spec_equivalence.py --patterns      # allowlist hits
    python tools/effect_spec_equivalence.py --closure [--json]
    python tools/effect_spec_equivalence.py --gate-parity
    python tools/effect_spec_equivalence.py --timing
"""
from __future__ import annotations

import argparse
import ast
import collections
import dataclasses
import inspect
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ALLOWLIST_PATH = REPO / "tools" / "effect_spec_equivalence_allowlist.json"
BASELINE_PATH = REPO / "tools" / "effect_spec_equivalence_baseline.json"
SNAPSHOT_DIR = REPO / "tools" / "effect_legacy_snapshots"
# The committed `--closure` report (section 17, exit criterion 4): every
# legacy handler with the hosts its gate accepts; `--update` writes it and
# a full `--check` fails when it is stale.
CLOSURE_PATH = REPO / "tools" / "effect_closure_report.json"
# The switched pairs proven harness-identical (A38): written by
# `tools/host_resolution_equivalence.py --switched --pool --record`; gate
# parity holds every pair on the new path to it.
HARNESS_RECORD_PATH = REPO / "tools" / "host_harness_switched.json"
CARD_DATABASE = REPO / "engine" / "card_database.py"

AGREE = "AGREE"
MASKED_GROWTH = "MASKED_GROWTH"
UNEXPLAINED = "UNEXPLAINED"
DERIVED_COVERAGE_GROWTH = "DERIVED_COVERAGE_GROWTH"
UNMODELLED_CLAUSE = "UNMODELLED_CLAUSE"
SEMANTIC_FIX = "SEMANTIC_FIX"
# The section-10 diff classes an allowlist row may carry, besides the
# LEGACY_* quirk classes (any name with that prefix).
ROW_CLASSES = frozenset({
    "REMINDER_TEXT", UNMODELLED_CLAUSE, "RESIDUE_WIDENING",
    "RESIDUE_NARROWING", DERIVED_COVERAGE_GROWTH, SEMANTIC_FIX,
    "SUB_ABILITY_SCOPE"})
AUTOMATIC_CLASSES = (AGREE, MASKED_GROWTH, DERIVED_COVERAGE_GROWTH,
                     UNMODELLED_CLAUSE, UNEXPLAINED)


def _views():
    from engine import effect_views
    return effect_views


# ── comparison ────────────────────────────────────────────────────────

def norm(v: Any) -> Any:
    """An order-free comparable form: sequences as tuples, sets sorted,
    dicts by key (the form the deck-card pins compare)."""
    if isinstance(v, (list, tuple)):
        return tuple(norm(x) for x in v)
    if isinstance(v, (set, frozenset)):
        return tuple(sorted((norm(x) for x in v), key=repr))
    if isinstance(v, dict):
        return tuple(sorted(((k, norm(x)) for k, x in v.items()),
                            key=lambda kv: repr(kv[0])))
    return v


def views_equal(a: Any, b: Any) -> bool:
    return norm(a) == norm(b)


def projection(rec, value: Any) -> Any:
    """A partial record's compare projection: its `compare` keys of a
    dict (or attributes of an object), or the whole value for "eq"."""
    if rec.compare == "eq" or value is None:
        return value
    if isinstance(value, dict):
        return {k: value.get(k) for k in rec.compare}
    return {k: getattr(value, k, None) for k in rec.compare}


def _empty(v: Any) -> bool:
    """Legacy's "no value": None, False, 0, '' or an empty container."""
    return v is None or v is False or v == 0 or v == "" or \
        (isinstance(v, (list, tuple, dict, set, frozenset)) and not v)


def stable(v: Any) -> Any:
    """A hash-seed-independent, JSON-able form for reports."""
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: stable(getattr(v, f.name))
                for f in dataclasses.fields(v)}
    if hasattr(v, "name") and hasattr(v, "value") and \
            type(v).__module__ != "builtins" and not isinstance(v, (str, int)):
        return getattr(v, "name")
    if isinstance(v, dict):
        return {str(k): stable(x) for k, x in sorted(
            v.items(), key=lambda kv: repr(kv[0]))}
    if isinstance(v, (set, frozenset)):
        return sorted((stable(x) for x in v), key=repr)
    if isinstance(v, (list, tuple)):
        return [stable(x) for x in v]
    return v


# ── the allowlist ─────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class AllowRow:
    id: str
    cls: str
    fields: Tuple[str, ...]
    reason: str
    pattern: Optional[str] = None
    cards: Tuple[str, ...] = ()
    test: str = ""
    source: str = ""
    # An `always` row classes every comparison of its field on a matching
    # card, an equal one included: the legacy value there is no stable
    # comparison (it depends on set iteration order, so the hash seed
    # decides whether it agrees), and counting it as AGREE in one process
    # and UNEXPLAINED in another would make the pinned counts seed-bound.
    always: bool = False

    def matches(self, field: str, card: str, text: str) -> bool:
        if field not in self.fields:
            return False
        if card in self.cards:
            return True
        return bool(self.pattern) and re.search(self.pattern, text) is not None


def load_allowlist(path: Path = ALLOWLIST_PATH) -> List[AllowRow]:
    data = json.loads(Path(path).read_text())
    rows = []
    for r in data["rows"]:
        rows.append(AllowRow(
            id=r["id"], cls=r["class"], fields=tuple(r["fields"]),
            reason=r.get("reason", ""), pattern=r.get("pattern"),
            cards=tuple(r.get("cards", ())), test=r.get("test", ""),
            source=r.get("source", ""), always=bool(r.get("always"))))
    return rows


def load_unsurfaced(path: Path = ALLOWLIST_PATH) -> List[dict]:
    """The section-10 seed rows no field comparison surfaces."""
    return list(json.loads(Path(path).read_text()).get("unsurfaced", ()))


def validate_unsurfaced(entries: Iterable[dict]) -> List[str]:
    out = []
    for e in entries:
        for k in ("id", "carrier", "reason", "source"):
            if not str(e.get(k, "")).strip():
                out.append(f"unsurfaced {e.get('id', '?')}: no {k}")
    return out


def _test_exists(spec: str, repo: Path = REPO) -> bool:
    """`tests/file.py::test_name` names a test function that exists."""
    path, sep, name = spec.partition("::")
    p = repo / path
    if not sep or not name or not p.is_file():
        return False
    tree = ast.parse(p.read_text())
    return any(isinstance(n, ast.FunctionDef) and n.name == name
               for n in tree.body)


def validate_allowlist(rows: Iterable[AllowRow], *, repo: Path = REPO,
                       fields: Optional[Iterable[str]] = None) -> List[str]:
    """Problems with the rows themselves: an unknown class, an empty
    reason, a field with no derivation, a pattern that does not compile,
    a row with neither pattern nor cards, a duplicate id, a SEMANTIC_FIX
    row (or any row naming a test) whose test does not exist."""
    known = set(fields) if fields is not None else \
        set(_views().DERIVATIONS) | set(TOOL_CARRIERS)
    out, seen = [], set()
    for r in rows:
        if r.id in seen:
            out.append(f"{r.id}: duplicate id")
        seen.add(r.id)
        if r.cls not in ROW_CLASSES and not r.cls.startswith("LEGACY_"):
            out.append(f"{r.id}: unknown class {r.cls}")
        if not r.reason.strip():
            out.append(f"{r.id}: no reason")
        if not r.fields:
            out.append(f"{r.id}: no fields")
        for f in r.fields:
            if f not in known:
                out.append(f"{r.id}: field {f} has no derivation")
        if not r.pattern and not r.cards:
            out.append(f"{r.id}: neither a pattern nor cards")
        if r.pattern:
            try:
                re.compile(r.pattern)
            except re.error as e:
                out.append(f"{r.id}: pattern does not compile ({e})")
        if r.cls == SEMANTIC_FIX and not r.test:
            out.append(f"{r.id}: SEMANTIC_FIX row has no test")
        if r.test and not _test_exists(r.test, repo):
            out.append(f"{r.id}: test {r.test} does not exist")
        if r.always and not r.cls.startswith("LEGACY_"):
            out.append(f"{r.id}: an always row must carry a LEGACY_* class")
    return out


# ── one comparison ────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Diff:
    field: str
    card: str
    key: Any
    cls: str
    row: str = ""
    derived: Any = None
    legacy: Any = None


def _printed_text(template) -> str:
    """Every printed face, lowercased: what allowlist patterns match."""
    parts = [getattr(template, "oracle_text", "") or "",
             getattr(template, "back_face_oracle", "") or ""]
    return "\n".join(p for p in parts if p).lower()


# The host attributes that hold a host's content: reading one of them is
# reading the host. `kind`, `face`, `index`, `modes` and the slot labels
# only locate or traverse hosts, so a derivation that filters hosts by
# kind (a trigger-head view skipping a mana ability) has not read them.
_HOST_CONTENT = frozenset({
    "text", "specs", "targets", "target_alts", "trigger", "cost",
    "cost_modifiers", "cost_condition", "loyalty_cost", "chapters",
    "choose", "mode_cost", "label", "keywords", "from_zone", "flags",
    "restrictions"})
# CardEffects' summary of every host's verbs: reading it reads every host.
_ALL_HOSTS = object()


def _traced(fn: Callable[[], Any]) -> Tuple[Any, Any]:
    """(result, hosts read): `fn()` with every content read of an
    AbilityEffects recorded by identity. Reading `CardEffects.verbs`
    records `_ALL_HOSTS`."""
    from engine.effect_spec import AbilityEffects, CardEffects
    seen: set = set()
    base = object.__getattribute__

    def host_get(self, name):
        if name in _HOST_CONTENT:
            seen.add(id(self))
        return base(self, name)

    def card_get(self, name):
        if name == "verbs":
            seen.add(_ALL_HOSTS)
        return base(self, name)
    AbilityEffects.__getattribute__ = host_get
    CardEffects.__getattribute__ = card_get
    try:
        out = fn()
    finally:
        del AbilityEffects.__getattribute__
        del CardEffects.__getattribute__
    return out, seen


def _has_refusal(effects, read: Any = _ALL_HOSTS) -> bool:
    """A non-REPLACEMENT refusal in a host the derivation read (`read`:
    the identities `_traced` recorded, or `_ALL_HOSTS`)."""
    from engine.effect_spec import Stage
    every = read is _ALL_HOSTS or _ALL_HOSTS in read
    return any(s.payload.stage is not Stage.REPLACEMENT and
               (every or id(h) in read)
               for h, s in effects.unmodelled())


def classify(rec, template, effects, key, rows: Iterable[AllowRow],
             *, text: Optional[str] = None) -> Diff:
    """The class of one (record, template, key) comparison. UNMODELLED_
    CLAUSE blames only a refusal in a host the derivation read: a refusal
    elsewhere on the card cannot have hidden the field's value."""
    derived = rec.derive(effects, key, template)
    legacy = rec.legacy(template, key)
    if rec.partial:
        derived, legacy = projection(rec, derived), projection(rec, legacy)
    label = template.name
    text = _printed_text(template) if text is None else text
    for r in rows:
        if r.always and r.matches(rec.field, label, text):
            return Diff(rec.field, label, key, r.cls, r.id, derived, legacy)
    if views_equal(derived, legacy):
        cls = MASKED_GROWTH if rec.masked(effects, key, template) else AGREE
        return Diff(rec.field, label, key, cls)
    for r in rows:
        if r.matches(rec.field, label, text):
            return Diff(rec.field, label, key, r.cls, r.id, derived, legacy)
    if _empty(legacy) and not _empty(derived):
        cls = DERIVED_COVERAGE_GROWTH
    elif _empty(derived) and not _empty(legacy) and \
            _has_refusal(effects) and _has_refusal(effects, _traced(
                lambda: rec.derive(effects, key, template))[1]):
        cls = UNMODELLED_CLAUSE
    else:
        cls = UNEXPLAINED
    return Diff(rec.field, label, key, cls, "", derived, legacy)


# ── the cast_targets carrier (G15; 10.1 "A (report)") ─────────────────

def _req_shape(r) -> Tuple[Any, ...]:
    zone = getattr(r.zone, "value", r.zone)
    return (zone, tuple(sorted(r.types)), r.count_min, r.count_max)


class CastTargets:
    """The `cast_targets` pseudo-field: an instant's or sorcery's targets
    as the whole-oracle `target_solver.parse` reads them (legacy) against
    the targets its SPELL host and modes own (sub-ability targets
    excluded), each as (zone, types, count_min, count_max) in printed
    order. A report row (10.1): it is no template field, so it has no
    FieldDerivation and never switches; the tool compares it so the
    section-10 target disagreements have a carrier."""
    field = "cast_targets"
    family = "stack_mana"
    tier = "A"
    estep = "E6"
    scope = "card"
    partial = False
    compare = "eq"
    default: Any = ()

    @staticmethod
    def keys(template) -> Tuple[Any, ...]:
        from engine.cards import CardType
        types = set(getattr(template, "card_types", ()) or ())
        return (None,) if types & {CardType.INSTANT, CardType.SORCERY} \
            else ()

    @staticmethod
    def derive(effects, key=None, template=None):
        spell = effects.spell(0)
        if spell is None:
            return ()
        reqs = list(spell.targets)
        for m in effects.modes(0):
            reqs += list(m.targets)
        return tuple(_req_shape(r) for r in reqs)

    @staticmethod
    def legacy(template, key=None):
        from engine import target_solver
        return tuple(_req_shape(r) for r in
                     target_solver.parse(template.oracle_text or ""))

    @staticmethod
    def masked(effects, key=None, template=None) -> bool:
        return False


TOOL_CARRIERS = {CastTargets.field: CastTargets}


def records(fields: Optional[Iterable[str]] = None) -> List[Any]:
    """Every compared record: the DERIVATIONS plus the tool's own
    carriers, in name order (optionally only `fields`)."""
    allr = dict(_views().DERIVATIONS)
    allr.update(TOOL_CARRIERS)
    want = None if fields is None else set(fields)
    return [r for n, r in sorted(allr.items()) if want is None or n in want]


# ── a run ─────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Report:
    counts: Dict[str, Dict[str, int]]
    diffs: List[Diff]
    row_hits: Dict[str, int]
    templates: int
    full: bool
    timing: Dict[str, float] = dataclasses.field(default_factory=dict)

    def total(self, cls: str) -> int:
        return sum(c.get(cls, 0) for c in self.counts.values())


def deck_card_names() -> frozenset:
    from decks.modern_meta import MODERN_DECKS
    return frozenset(c for d in MODERN_DECKS.values()
                     for part in ("mainboard", "sideboard")
                     for c in (d.get(part) or {}))


def deck_parts() -> Tuple[frozenset, frozenset]:
    """(mainboard names, sideboard-only names) over every registered deck."""
    from decks.modern_meta import MODERN_DECKS
    mb = frozenset(c for d in MODERN_DECKS.values()
                   for c in (d.get("mainboard") or {}))
    sb = frozenset(c for d in MODERN_DECKS.values()
                   for c in (d.get("sideboard") or {})) - mb
    return mb, sb


def pool_templates(db) -> List[Any]:
    return sorted({id(t): t for t in db.cards.values()}.values(),
                  key=lambda t: t.name)


def deck_templates(db) -> List[Any]:
    out = {}
    for n in deck_card_names():
        t = db.cards.get(n)
        if t is not None:
            out[id(t)] = t
    return sorted(out.values(), key=lambda t: t.name)


def run(templates: Iterable[Any], effects: Mapping[str, Any], *,
        fields: Optional[Iterable[str]] = None,
        rows: Optional[List[AllowRow]] = None,
        full: bool = False, recs: Optional[List[Any]] = None) -> Report:
    """Compare every record (or `fields`, or the given `recs`) on every
    template; `effects` maps template name -> CardEffects (the eager
    parse)."""
    recs = records(fields) if recs is None else list(recs)
    rows = load_allowlist() if rows is None else rows
    counts: Dict[str, Dict[str, int]] = {r.field: {} for r in recs}
    diffs: List[Diff] = []
    hits = {r.id: 0 for r in rows}
    cpu: Dict[str, float] = collections.defaultdict(float)
    n = 0
    for t in templates:
        n += 1
        ce = effects.get(t.name)
        if ce is None:
            ce = t.effects
        text = None
        for rec in recs:
            t0 = time.process_time()
            for key in rec.keys(t):
                if text is None:
                    text = _printed_text(t)
                d = classify(rec, t, ce, key, rows, text=text)
                c = counts[rec.field]
                c[d.cls] = c.get(d.cls, 0) + 1
                if d.row:
                    hits[d.row] += 1
                if d.cls not in (AGREE, MASKED_GROWTH):
                    diffs.append(d)
                elif d.cls == MASKED_GROWTH:
                    diffs.append(d)
            cpu[rec.family] += time.process_time() - t0
    return Report(counts=counts, diffs=diffs, row_hits=hits, templates=n,
                  full=full, timing={f"derive:{k}": round(x, 3)
                                     for k, x in sorted(cpu.items())})


# ── switched fields, snapshots ────────────────────────────────────────

def switched_fields(path: Path = CARD_DATABASE) -> List[str]:
    """Fields `card_database` assigns from an `effect_views` call (a
    family's migration step 5); none in E0."""
    tree = ast.parse(path.read_text())
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Assign):
            continue
        for t in n.targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                    and t.value.id == "template" and _calls_views(n.value):
                out.append(t.attr)
    return sorted(set(out))


def _calls_views(node: ast.AST) -> bool:
    for c in ast.walk(node):
        if isinstance(c, ast.Call):
            f = c.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.value.id == "effect_views":
                return True
    return False


def snapshot_problems(templates: Iterable[Any],
                      snapshot_dir: Path = SNAPSHOT_DIR) -> List[str]:
    """Each frozen legacy snapshot ({card: stable legacy value}) against
    the current legacy value of its field."""
    if not snapshot_dir.is_dir():
        return []
    v = _views()
    by_name = {t.name: t for t in templates}
    out = []
    for p in sorted(snapshot_dir.glob("*.json")):
        field = p.stem
        rec = v.DERIVATIONS.get(field)
        if rec is None:
            out.append(f"snapshot {p.name}: no derivation {field}")
            continue
        snap = json.loads(p.read_text())
        for card, value in sorted(snap.items()):
            t = by_name.get(card)
            if t is None:
                continue
            got = stable(rec.legacy(t, None))
            if got != value:
                out.append(f"snapshot {field}: {card} legacy changed")
    return out


# ── baseline and check ────────────────────────────────────────────────

def baseline_of(report: Report, parity: Optional[dict] = None) -> dict:
    out = {"description": (
        "Per-field equivalence class counts (design doc 2026-09-29 "
        "section 10): UNEXPLAINED may only fall and AGREE may only rise, per "
        "field. Regenerate with `python tools/effect_spec_equivalence.py "
        "--update`."),
        "templates": report.templates,
        "fields": {f: dict(sorted(c.items()))
                   for f, c in sorted(report.counts.items())},
        "totals": {cls: report.total(cls) for cls in sorted(
            {k for c in report.counts.values() for k in c})}}
    if parity is not None:
        out["gate_parity"] = {"pairs": parity["pairs"],
                              "new_path": parity["new_path"],
                              "legacy_fallback": parity["legacy_fallback"]}
    return out


def check(baseline: Mapping[str, Any], report: Report, *,
          rows: Optional[List[AllowRow]] = None,
          templates: Iterable[Any] = (),
          effects: Optional[Mapping[str, Any]] = None,
          unsurfaced: Optional[List[dict]] = None,
          parity: Optional[dict] = None,
          closure: Optional[dict] = None,
          closure_path: Optional[Path] = None) -> List[str]:
    """Every reason `--check` exits 1 (section 10, "Tool usage"). On a
    full run an improvement is a stale baseline (UNEXPLAINED fell, AGREE
    rose, legacy fallback fell): the commit that makes it lowers the
    ceiling (``--update``), so a later regression cannot refill it.
    `parity` is the gate-parity report (`gate_parity`), checked when
    given; `closure` (a `closure_report`) is compared, on a full run, with
    the committed report at `closure_path`."""
    rows = load_allowlist() if rows is None else rows
    unsurfaced = load_unsurfaced() if unsurfaced is None else unsurfaced
    out = list(validate_allowlist(rows)) + validate_unsurfaced(unsurfaced)
    base = baseline.get("fields", {})
    stale = " -- stale baseline: lock it in with --update in this commit"
    for field, c in sorted(report.counts.items()):
        b = base.get(field, {})
        un, ag = c.get(UNEXPLAINED, 0), c.get(AGREE, 0)
        bun, bag = b.get(UNEXPLAINED, 0), b.get(AGREE, 0)
        if un > bun:
            out.append(f"{field}: UNEXPLAINED grew {bun} -> {un}")
        elif report.full and un < bun:
            out.append(f"{field}: UNEXPLAINED fell {bun} -> {un}{stale}")
        if ag < bag:
            out.append(f"{field}: AGREE fell {bag} -> {ag}")
        elif report.full and ag > bag:
            out.append(f"{field}: AGREE rose {bag} -> {ag}{stale}")
    if parity is not None:
        out += list(parity.get("failures", ()))
        pinned = baseline.get("gate_parity", {}).get("legacy_fallback")
        got = parity.get("legacy_fallback", 0)
        if report.full and pinned is not None:
            if got > pinned:
                out.append(f"gate parity: legacy fallback grew {pinned} -> "
                           f"{got}")
            elif got < pinned:
                out.append(f"gate parity: legacy fallback fell {pinned} -> "
                           f"{got}{stale}")
    if closure is not None and report.full:
        closure_path = CLOSURE_PATH if closure_path is None else closure_path
        committed = closure_path.read_text() if closure_path.is_file() \
            else ""
        if committed != closure_json(closure):
            out.append(f"closure report {closure_path.name} differs from "
                       f"the pool closure{stale}")
    if report.full:
        for r in rows:
            if report.row_hits.get(r.id, 0) == 0:
                out.append(f"allowlist row {r.id} is stale (explains "
                           f"nothing)")
    templates = list(templates)
    out += snapshot_problems(templates)
    switched = switched_fields()
    if switched:
        v = _views()
        effects = effects or {}
        for t in templates:
            for f in switched:
                rec = v.DERIVATIONS.get(f)
                if rec is None:
                    out.append(f"switched field {f} has no derivation")
                    continue
                for key in rec.keys(t):
                    ce = effects.get(t.name) or t.effects
                    if not views_equal(rec.derive(ce, key, t),
                                       rec.legacy(t, key)):
                        out.append(f"switched field {f} differs from legacy "
                                   f"on {t.name}")
    return out


# ── closure and gate parity (A38) ─────────────────────────────────────

# Section 14: the family step whose executors own each verb. A verb in no
# family (FIGHT, TRANSFORM, ATTACH, COPY, ...) has no step yet; the
# dispatcher itself sequences CREATE_TRIGGER, whose sub-host's verbs count.
VERB_STEP = {
    "DAMAGE": "E1", "LOSE_LIFE": "E1", "GAIN_LIFE": "E1", "CHOOSE": "E1",
    "LOOK": "E1",
    "DESTROY": "E2", "EXILE": "E2", "SACRIFICE": "E2", "MOVE": "E2",
    "SHUFFLE": "E2",
    "DRAW": "E3", "DISCARD": "E3", "MILL": "E3", "SCRY": "E3",
    "SURVEIL": "E3", "REVEAL": "E3", "SEARCH": "E3", "CAST_FREE": "E3",
    "CREATE_TOKEN": "E4", "PUT_COUNTERS": "E4", "REMOVE_COUNTERS": "E4",
    "MOVE_COUNTERS": "E4", "DOUBLE_COUNTERS": "E4", "PLAYER_COUNTERS": "E4",
    "KEYWORD_ACTION": "E4",
    "CONTINUOUS": "E5", "TAP": "E5", "UNTAP": "E5",
    "COUNTER": "E6", "ADD_MANA": "E6", "PAY": "E6", "END_TURN": "E6",
    "CREATE_TRIGGER": "E0",
}
STEP_ORDER = ("E0", "E1", "E2", "E3", "E4", "E5", "E6", "E7")

# Section 14's handler switch schedule: the family each legacy
# clause_resolver handler belongs to. A handler missing here fails closed
# (gate parity reports it).
HANDLER_FAMILY = {
    "x_creature_tutor": "card_flow", "team_pump": "pump_restrict",
    "combat_prevention": "pump_restrict", "hand_refill_wheel": "card_flow",
    "cast_prohibition": "pump_restrict",
    "object_restriction": "pump_restrict",
    "attack_observer": "pump_restrict",
    "group_restriction": "pump_restrict",
    "until_next_turn": "pump_restrict", "mass_mode_clause": "removal",
    "targeted_pump": "pump_restrict", "mass_reanimate": "removal",
    "energy_damage": "damage", "land_destruction": "removal",
    "direct_damage": "damage", "board_sweep": "removal",
    "targeted_removal": "removal", "library_dig": "card_flow",
    "hand_attack": "card_flow", "bounce": "removal",
    "reanimate_target": "removal", "impulse_reveal": "card_flow",
    "card_flow": "card_flow", "create_token": "tokens_counters",
}
# The clause handler that is the dispatcher's own carrier for a spell no
# legacy handler claims: its family is the landed family that runs the
# host (`effect_carrier.spell_family`), not a fixed one.
DISPATCHED_HANDLER = "dispatched"
# The planeswalker_manager branches by LoyaltyEffectKind (10.1 rows).
LOYALTY_FAMILY = {
    "DAMAGE": "damage", "GAIN_LIFE_AND_DRAW": "damage",
    "RETURN_TO_HAND": "removal", "TUCK_TARGET_INTO_LIBRARY": "removal",
    "EMBLEM_EXILE_PERMANENT": "removal",
    "DRAW_AND_UNTAP_LANDS": "pump_restrict",
}
# The ETB carriers: legacy fields an enter trigger resolves from.
ETB_CARRIERS = ("etb_targeted_removal_data", "etb_exile_returns_on_leave",
                "etb_return_land")
# The legacy apply that resolves each carrier ("module:qualname"): what
# `_is_switched` inspects, so a carrier switch is seen like any handler's.
ETB_CARRIER_APPLY = {
    "etb_targeted_removal_data":
        "engine.oracle_resolver:resolve_etb_from_oracle",
    "etb_exile_returns_on_leave":
        "engine.oracle_resolver:resolve_dies_trigger",
    "etb_return_land":
        "engine.land_manager:LandManager.apply_land_etb_static",
}
# The enter-trigger carrier (`effect_carrier.dispatch_etb`): its pairs are
# the hosts `effect_carrier.etb_plan` takes, through the enter resolver.
ETB_DISPATCH_HANDLER = "etb:dispatch"
ETB_DISPATCH_APPLY = "engine.oracle_resolver:resolve_etb_from_oracle"
SELF_CAST_HANDLER = "oracle_resolver.resolve_self_cast_trigger"
SELF_CAST_APPLY = "engine.oracle_resolver:resolve_self_cast_trigger"
# The dispatcher entry point a switched apply reaches (section 10,
# migration step 6).
DISPATCHER = "resolve_ability"


@dataclasses.dataclass(frozen=True)
class Pair:
    handler: str
    family: str
    card: str
    host: str          # "<kind>:<face>:<index>[:mode i]"
    part: str          # "mainboard" / "sideboard" / "pool"
    verbs: Tuple[str, ...]
    sub_kinds: Tuple[str, ...]
    step: Optional[str]
    strict: bool
    executable: bool
    switched: bool

    @property
    def new_path(self) -> bool:
        return self.switched and self.strict and self.executable


def _host_label(h, mode: Optional[int] = None) -> str:
    lab = f"{h.kind.name}:{h.face}:{h.index}"
    return lab if mode is None else f"{lab}:mode{mode}"


def host_verbs(h) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """(verbs, sub-ability kinds) of a host, its modes and sub-abilities."""
    from engine.effect_spec import SubAbility, iter_specs
    verbs, subs = set(), set()

    def visit(x):
        for s in iter_specs(x.specs):
            verbs.add(s.verb.name)
            if isinstance(s.payload, SubAbility):
                subs.add(s.payload.kind.name)
                visit(s.payload.host)
        for m in x.modes:
            visit(m)
    visit(h)
    return tuple(sorted(verbs)), tuple(sorted(subs))


def earliest_step(verbs: Iterable[str]) -> Optional[str]:
    """The first step at which every verb has an executor (section 14);
    None when a verb has no family yet (UNMODELLED included)."""
    steps = []
    for v in verbs:
        s = VERB_STEP.get(v)
        if s is None:
            return None
        steps.append(STEP_ORDER.index(s))
    return STEP_ORDER[max(steps)] if steps else "E0"


def _resolve_path(path: str) -> Callable:
    """"module:qualname" -> the function."""
    import importlib
    mod, _, qual = path.partition(":")
    obj: Any = importlib.import_module(mod)
    for part in qual.split("."):
        obj = getattr(obj, part)
    return obj


def _engine_function(fn: Callable) -> bool:
    return (getattr(fn, "__module__", "") or "").startswith("engine")


_CALLEES: Dict[int, Tuple[bool, Tuple[Callable, ...]]] = {}


def _callees(fn: Callable) -> Tuple[bool, Tuple[Callable, ...]]:
    """(calls the dispatcher directly, the functions it calls that resolve
    by name: module globals, function-local imports, Class.method and
    module.attr), memoised per function."""
    import importlib
    import textwrap
    key = id(fn)
    if key in _CALLEES:
        return _CALLEES[key]
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        _CALLEES[key] = (False, ())
        return _CALLEES[key]
    scope = dict(getattr(fn, "__globals__", {}) or {})
    pkg = (getattr(fn, "__module__", "") or "").rpartition(".")[0]
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            try:
                base = importlib.import_module(
                    "." * n.level + (n.module or ""), pkg) if n.level \
                    else importlib.import_module(n.module or "")
            except Exception:
                continue
            for a in n.names:
                v = getattr(base, a.name, None)
                if v is None:
                    try:
                        v = importlib.import_module(
                            f"{base.__name__}.{a.name}")
                    except Exception:
                        v = None
                if v is not None:
                    scope[a.asname or a.name] = v
    direct, out = False, []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f, target = n.func, None
        if isinstance(f, ast.Name):
            direct |= f.id == DISPATCHER
            target = scope.get(f.id)
        elif isinstance(f, ast.Attribute):
            direct |= f.attr == DISPATCHER
            if isinstance(f.value, ast.Name) and f.value.id in scope:
                target = getattr(scope[f.value.id], f.attr, None)
        target = getattr(target, "__func__", target)
        if inspect.isfunction(target):
            out.append(target)
    _CALLEES[key] = (direct, tuple(out))
    return _CALLEES[key]


def _is_switched(fn: Callable, *,
                 within: Callable[[Callable], bool] = _engine_function
                 ) -> bool:
    """A legacy apply is switched once it reaches the dispatcher (section
    10, migration step 6), directly or through any helper it calls (the
    static call graph over the functions `within` admits: engine code,
    followed through module functions, function-local imports,
    `Class.method` and `module.attr` calls; a call on an instance
    (`game.x()`) is not followed: a switch reached only through one is
    not seen);
    none is in E0."""
    fn = getattr(fn, "__func__", fn)
    seen, stack = set(), [fn]
    while stack:
        f = stack.pop()
        if id(f) in seen:
            continue
        seen.add(id(f))
        direct, callees = _callees(f)
        if direct:
            return True
        stack.extend(c for c in callees if id(c) not in seen and within(c))
    return False


def _part(name: str, mb: frozenset, sb: frozenset) -> str:
    return "mainboard" if name in mb else "sideboard" if name in sb \
        else "pool"


def _pair(handler, family, t, h, label, part, switched) -> Pair:
    from engine.effect_resolver import can_execute
    v = _views()
    verbs, subs = host_verbs(h)
    strict = v.STRICT.get(family)
    return Pair(handler=handler, family=family, card=t.name, host=label,
                part=part, verbs=verbs, sub_kinds=subs,
                step=earliest_step(verbs),
                strict=bool(strict and strict(h)),
                executable=can_execute(h, family), switched=switched)


def _clause_contexts(t, ce):
    """(override, removal_data, host, label) for every way legacy runs
    `t` through the clause resolver: the whole spell, each mode's clause,
    the kicked clause."""
    from engine.cards import CardType
    types = set(getattr(t, "card_types", ()) or ())
    spell = ce.spell(0)
    if types & {CardType.INSTANT, CardType.SORCERY} and spell is not None:
        yield None, None, spell, _host_label(spell)
    for i, m in enumerate(getattr(t, "modes", None) or ()):
        hosts = ce.modes(0)
        h = hosts[i] if i < len(hosts) else None
        if h is not None and m.get("text"):
            yield m["text"], m.get("removal"), h, _host_label(h, i)
    kc = getattr(t, "kicked_clause", None)
    if kc:
        h = _views().host_for_override(t, kc, effects=ce)
        if h is not None:
            yield kc, None, h, _host_label(h) + ":kicked"


def _spell_reaches_clause_handlers(t) -> bool:
    """Does the whole spell's resolution reach the clause handlers? A
    card-name SPELL_RESOLVE registry handler runs first and ends it, and a
    counterspell takes the per-ability path
    (`ResolutionManager._execute_spell_effects`): for either, a switched
    clause handler is not the path its host resolves on."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    return not (EFFECT_REGISTRY.has_handler(t.name, EffectTiming.SPELL_RESOLVE)
                or getattr(t, "is_counterspell", False))


def _enter_reaches_resolver(t) -> bool:
    """Does the permanent's entry reach the enter resolver? A card-name ETB
    registry handler runs instead of it (`ResolutionManager.
    _handle_permanent_etb`, `zone_transfer._fire_etb_triggers`): for such a
    card the enter-trigger carrier is not the path its hosts resolve on."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    return not EFFECT_REGISTRY.has_handler(t.name, EffectTiming.ETB)


def closure(templates: Iterable[Any], effects: Mapping[str, Any]
            ) -> List[Pair]:
    """Every (legacy handler, host) pair a legacy gate accepts. A pair is
    switched when its handler's apply reaches the dispatcher AND the host
    resolves through that handler (`_spell_reaches_clause_handlers`)."""
    from engine import clause_resolver as CR
    from engine import activated_effects, planeswalker_manager
    from engine.effect_carrier import places_legacy_targets, spell_family
    from engine.cards import CardInstance, LoyaltyEffectKind
    from engine.effect_spec import EventHint, HostKind
    v = _views()
    mb, sb = deck_parts()
    handlers = list(CR.PRE_ORACLE_HANDLERS) + list(CR.HANDLERS)
    oracle_handlers = {h.name for h in CR.HANDLERS}
    switched = {h.name: _is_switched(h.apply) for h in handlers}
    pw_switched = _is_switched(planeswalker_manager.PlaneswalkerManager._resolve)
    act_switched = _is_switched(activated_effects.resolve_activated_ability)
    etb_switched = {f: _is_switched(_resolve_path(p))
                    for f, p in ETB_CARRIER_APPLY.items()}
    self_cast_switched = _is_switched(_resolve_path(SELF_CAST_APPLY))
    etb_dispatch_switched = _is_switched(_resolve_path(ETB_DISPATCH_APPLY))
    from engine.effect_carrier import etb_plan
    out: List[Pair] = []
    for t in templates:
        ce = effects.get(t.name) or t.effects
        part = _part(t.name, mb, sb)
        card = CardInstance(template=t, owner=0, controller=0,
                            instance_id=0, zone="stack")
        whole_reached = _spell_reaches_clause_handlers(t)
        for override, removal, h, label in _clause_contexts(t, ce):
            ctx = CR._static_context(card, 0, override, removal)
            reached = override is not None or (
                whole_reached and places_legacy_targets(h, ce.front()))
            for hd in handlers:
                if hd.name in oracle_handlers and not ctx.oracle:
                    continue
                family = HANDLER_FAMILY.get(hd.name, "?")
                if hd.name == DISPATCHED_HANDLER:
                    # its gate is the landed family that runs the spell
                    # host; asked with the parse in hand (no second parse)
                    family = (spell_family(t, ce) if override is None
                              else None)
                    ok = family is not None
                else:
                    try:
                        ok = hd.gate(ctx)
                    except Exception:
                        ok = False
                if ok:
                    out.append(_pair(hd.name, family, t, h, label, part,
                                     switched[hd.name] and reached))
        # loyalty lines: kind branches, and CLAUSE through the registry
        for face, attr in ((0, "loyalty_abilities"),
                           (1, "back_face_loyalty_abilities")):
            for slot, ab in sorted((getattr(t, attr, None) or {}).items()):
                h = ce.loyalty(slot, face)
                if h is None:
                    continue
                label = _host_label(h)
                kind = ab.effect_kind.name
                if ab.effect_kind is LoyaltyEffectKind.CLAUSE and \
                        ab.clause is not None:
                    src = CardInstance(template=ab.clause, owner=0,
                                       controller=0, instance_id=0,
                                       zone="battlefield")
                    ctx = CR._static_context(src, 0, None, None)
                    for hd in handlers:
                        try:
                            ok = hd.gate(ctx)
                        except Exception:
                            ok = False
                        if ok:
                            out.append(_pair(hd.name, HANDLER_FAMILY.get(
                                hd.name, "?"), t, h, label, part,
                                switched[hd.name]))
                elif kind in LOYALTY_FAMILY:
                    out.append(_pair(f"planeswalker_manager:{kind}",
                                     LOYALTY_FAMILY[kind], t, h, label,
                                     part, pw_switched))
        # activated abilities by effect kind
        for ab in getattr(t, "activated_abilities", None) or ():
            kind = getattr(ab.effect_kind, "name", None)
            if kind is None or kind == "UNCLASSIFIED":
                continue
            rec = v.DERIVATIONS.get(f"ActivatedAbility.effect_kind[{kind}]")
            h = ce.activated(ab.index)
            if h is None:
                continue
            out.append(_pair(f"activated_effects:{kind}",
                             rec.family if rec else "?", t, h,
                             _host_label(h), part,
                             act_switched and places_legacy_targets(h)))
        # ETB carriers
        enters = [h for h in ce.front() if h.kind is HostKind.TRIGGERED
                  and h.trigger is not None
                  and EventHint.SELF_ENTERS in h.trigger.event_hints]
        for field in ETB_CARRIERS:
            if enters and getattr(t, field, None):
                rec = v.DERIVATIONS[field]
                out.append(_pair(f"etb:{field}", rec.family, t, enters[0],
                                 _host_label(enters[0]), part,
                                 etb_switched[field]))
        for h, family in etb_plan(ce.front()) or ():
            out.append(_pair(ETB_DISPATCH_HANDLER, family, t, h,
                             _host_label(h), part,
                             etb_dispatch_switched
                             and _enter_reaches_resolver(t)))
        # the spell's own cast triggers
        if "when you cast this spell" in _printed_text(t):
            for h in ce.front():
                if h.kind is HostKind.TRIGGERED and h.trigger is not None \
                        and EventHint.SELF_CAST in h.trigger.event_hints:
                    out.append(_pair(SELF_CAST_HANDLER, "removal", t, h,
                                     _host_label(h), part, self_cast_switched))
    return out


def gate_parity(pairs: Iterable[Pair],
                harness_ok: Optional[Callable[[Pair], bool]] = None) -> dict:
    """A38: a pair on the new path must be strict, executable and
    harness-identical; a handler with no family fails closed. Every pair
    not on the new path is on legacy fallback (ratchet (f))."""
    pairs = list(pairs)
    failures = []
    for p in pairs:
        if p.family == "?":
            failures.append(f"{p.handler}: no family (map it in "
                            f"HANDLER_FAMILY)")
        if p.new_path and (harness_ok is None or not harness_ok(p)):
            failures.append(f"{p.handler} {p.card} {p.host}: on the new "
                            f"path without a harness-identical run")
    new = sum(p.new_path for p in pairs)
    by_handler = collections.Counter(p.handler for p in pairs
                                     if not p.new_path)
    return {"pairs": len(pairs), "new_path": new,
            "legacy_fallback": len(pairs) - new,
            "fallback_by_handler": dict(sorted(by_handler.items())),
            "failures": sorted(set(failures))}


def _recorded_pairs(path: Optional[Path] = None) -> set:
    """Every pair the harness record accounts for: proven identical to
    its legacy apply, or an intended change with its reason."""
    path = HARNESS_RECORD_PATH if path is None else path
    if not path.is_file():
        return set()
    rec = json.loads(path.read_text())
    return ({tuple(k) for k in rec.get("pairs", ())}
            | {tuple(e["pair"]) for e in rec.get("intended", ())})


def recorded_harness_ok(path: Optional[Path] = None
                        ) -> Callable[[Pair], bool]:
    """`harness_ok` for gate parity: the pair's switched carrier was
    proven against its legacy apply by the committed harness record, or
    the record names why it differs on purpose."""
    proven = _recorded_pairs(path)
    return lambda p: (p.handler, p.card, p.host) in proven


def stale_harness_record(pairs: Iterable[Pair],
                         path: Optional[Path] = None) -> List[str]:
    """Recorded pairs that are no longer on the new path (a full pool
    closure only): the record must be refreshed in the same commit."""
    path = HARNESS_RECORD_PATH if path is None else path
    on_new = {(p.handler, p.card, p.host) for p in pairs if p.new_path}
    return [f"harness record {path.name} lists {list(k)}, which is not on "
            f"the new path: refresh it with host_resolution_equivalence.py "
            f"--switched --pool --record"
            for k in sorted(_recorded_pairs(path) - on_new)]


def closure_json(rep: Mapping[str, Any]) -> str:
    """The committed form of a closure report."""
    return json.dumps(rep, indent=1, sort_keys=True) + "\n"


def closure_report(pairs: Iterable[Pair]) -> dict:
    """Per handler: hosts by deck part, the verb sets and sub-ability
    kinds, the earliest executable step and the strict-view matches."""
    out: Dict[str, dict] = {}
    for p in pairs:
        h = out.setdefault(p.handler, {
            "family": p.family, "hosts": {"mainboard": 0, "sideboard": 0,
                                          "pool": 0},
            "strict": 0, "executable": 0, "steps": {}, "verb_sets": {},
            "sub_kinds": {}, "deck_hosts": []})
        h["hosts"][p.part] += 1
        h["strict"] += p.strict
        h["executable"] += p.executable
        step = p.step or "none"
        h["steps"][step] = h["steps"].get(step, 0) + 1
        vs = ",".join(p.verbs)
        h["verb_sets"][vs] = h["verb_sets"].get(vs, 0) + 1
        for k in p.sub_kinds:
            h["sub_kinds"][k] = h["sub_kinds"].get(k, 0) + 1
        if p.part != "pool":
            h["deck_hosts"].append({"card": p.card, "host": p.host,
                                    "part": p.part, "step": p.step,
                                    "strict": p.strict})
    for h in out.values():
        h["steps"] = dict(sorted(h["steps"].items()))
        h["verb_sets"] = dict(sorted(h["verb_sets"].items(),
                                     key=lambda kv: (-kv[1], kv[0]))[:10])
        h["deck_hosts"].sort(key=lambda d: (d["card"], d["host"]))
    return dict(sorted(out.items()))


# ── CLI ───────────────────────────────────────────────────────────────

def load_db():
    import contextlib
    import io
    from engine.card_database import CardDatabase
    with contextlib.redirect_stdout(io.StringIO()):
        return CardDatabase()


def parse_effects_of(templates: Iterable[Any]) -> Dict[str, Any]:
    """The eager parse of `templates` through `parse_template` (the
    lazy property's own call), never pinned on a template."""
    from engine.effect_grammar import parse_template
    return {t.name: parse_template(t) for t in templates}


def _print_table(report: Report) -> None:
    classes = sorted({k for c in report.counts.values() for k in c},
                     key=lambda k: (k not in AUTOMATIC_CLASSES, k))
    print(f"{report.templates:,} templates, {len(report.counts)} fields")
    print(f"{'field':<58}" + "".join(f"{c[:12]:>13}" for c in classes))
    for f, c in sorted(report.counts.items()):
        print(f"{f[:57]:<58}" + "".join(f"{c.get(k, 0):>13}"
                                        for k in classes))
    print(f"{'TOTAL':<58}" + "".join(f"{report.total(k):>13}"
                                     for k in classes))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--field", action="append")
    ap.add_argument("--class", dest="cls", action="append")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--decks", action="store_true")
    ap.add_argument("--patterns", action="store_true")
    ap.add_argument("--timing", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--closure", action="store_true")
    ap.add_argument("--gate-parity", action="store_true")
    ap.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = ap.parse_args(argv)

    timing: Dict[str, float] = {}
    t0 = time.process_time()
    db = load_db()
    timing["load_db"] = time.process_time() - t0
    templates = deck_templates(db) if args.decks else pool_templates(db)
    t0 = time.process_time()
    effects = parse_effects_of(templates)
    timing["parse"] = time.process_time() - t0
    full = not args.decks and not args.field

    parity = None
    if args.closure or args.gate_parity or args.check or args.update:
        t0 = time.process_time()
        pairs = closure(templates, effects)
        timing["closure"] = time.process_time() - t0
        parity = gate_parity(pairs, harness_ok=recorded_harness_ok())
        if full:
            parity["failures"] += stale_harness_record(pairs)
    if args.closure or args.gate_parity:
        if args.closure:
            rep = closure_report(pairs)
            if args.json:
                print(json.dumps({"closure": rep, "gate_parity": parity},
                                 indent=1, sort_keys=True))
            else:
                for name, h in rep.items():
                    print(f"{name} [{h['family']}] hosts {h['hosts']} "
                          f"strict {h['strict']} executable "
                          f"{h['executable']} steps {h['steps']}")
        print(f"gate parity: {parity['pairs']} pairs, {parity['new_path']} "
              f"on the new path, {parity['legacy_fallback']} on legacy "
              f"fallback")
        for f in parity["failures"]:
            print(f"  FAIL {f}")
        if args.timing:
            print(json.dumps({k: round(x, 2) for k, x in timing.items()}))
        if args.gate_parity and parity["failures"]:
            return 1
        if not (args.check or args.update or args.list):
            return 0

    t0 = time.process_time()
    rows = load_allowlist()
    report = run(templates, effects, fields=args.field, rows=rows,
                 full=full)
    timing["derive"] = time.process_time() - t0

    if args.list:
        for d in report.diffs:
            if args.cls and d.cls not in args.cls:
                continue
            key = "" if d.key is None else f" [{d.key}]"
            print(f"{d.cls:<26} {d.field:<48} {d.card}{key}"
                  + (f"  ({d.row})" if d.row else ""))
            if d.cls not in (MASKED_GROWTH,):
                print(f"    derived {stable(d.derived)!r:.160}")
                print(f"    legacy  {stable(d.legacy)!r:.160}")
    elif args.json:
        print(json.dumps({"counts": report.counts,
                          "row_hits": report.row_hits,
                          "templates": report.templates},
                         indent=1, sort_keys=True))
    else:
        _print_table(report)
    if args.patterns:
        for r in rows:
            print(f"{report.row_hits.get(r.id, 0):>6}  {r.cls:<28} {r.id}")
    if args.timing:
        timing.update(report.timing)
        print(json.dumps({k: round(x, 2) for k, x in timing.items()},
                         indent=1))
    if args.update:
        if not full:
            print("--update needs the full pool and every field")
            return 2
        args.baseline.write_text(json.dumps(
            baseline_of(report, parity), indent=1, sort_keys=True) + "\n")
        CLOSURE_PATH.write_text(closure_json(closure_report(pairs)))
        print(f"wrote {args.baseline} and {CLOSURE_PATH}")
        return 0
    if args.check:
        baseline = json.loads(args.baseline.read_text())
        problems = check(baseline, report, rows=rows, templates=templates,
                         effects=effects, parity=parity,
                         closure=closure_report(pairs))
        if problems:
            print("Effect-spec equivalence FAILED:")
            for p in problems:
                print(f"  {p}")
            return 1
        print(f"Effect-spec equivalence OK -- UNEXPLAINED "
              f"{report.total(UNEXPLAINED)}, AGREE {report.total(AGREE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
