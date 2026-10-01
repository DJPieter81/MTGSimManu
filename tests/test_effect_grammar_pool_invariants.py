"""Pool-wide invariants of the clause grammar (design doc 2026-09-29, E0).

E0 changes no resolution behaviour. Besides the seeded digest, that is
proven by pool-wide unchanged-output pins for the refactors E0 makes to
existing modules (design section 0):

* the located target parse: `target_solver.parse()` is re-expressed over
  `parse_located`, the one placement owner (F3, A20), and its output is
  unchanged for every oracle text, line and sentence of the pool;
* the loyalty slot rule: it has one owner, `oracle_parser.loyalty_slot_for`
  (G1, A12), and `loyalty_abilities` / `back_face_loyalty_abilities` are
  unchanged for every planeswalker.

Each pin compares against a capture taken before its refactor (the capture
helpers and their `--capture` entry points live with the module tests:
tests/test_target_solver_located_parse.py and
tests/test_loyalty_slot_rule_owner.py). The grammar's own pool invariants
(effects populated, spans covered, witnesses, schema invariants, load
budget) join this file with `engine/effect_grammar/`.
"""
from __future__ import annotations

import json

import pytest

from tests.test_loyalty_slot_rule_owner import (
    CAPTURE_PATH as LOYALTY_CAPTURE_PATH, REPO, _ult_writers, walker_digests)
from tests.test_target_solver_located_parse import (
    CAPTURE_PATH as TARGET_PARSE_CAPTURE_PATH, _digest, output_digest,
    pool_texts)


# Pool-wide (~17k pool texts containing "target"). Measured 2026-09-30 on
# this container (quiet, 4 cores): ~1.3 s for the body, plus ~16 s when it is
# the first test of the process to load the shared card DB. 120 s bounds a
# hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_target_solver_parse_is_unchanged_by_the_located_refactor_pool_wide(card_db):
    from engine.target_solver import parse
    pinned = json.loads(TARGET_PARSE_CAPTURE_PATH.read_text())["entries"]
    checked, diffs = 0, []
    for t in pool_texts(card_db):
        if "target" not in t.lower():
            continue
        want = pinned.get(_digest(t))
        if want is None:          # a text added by a later DB refresh
            continue
        checked += 1
        if output_digest(parse(t)) != want:
            diffs.append(t)
    # The capture must still describe the pool, or the pin is vacuous.
    assert checked >= 0.95 * len(pinned), (checked, len(pinned))
    assert not diffs, f"{len(diffs)} texts changed, e.g. {diffs[:3]}"


# Pool-wide (~316 templates with a printed loyalty line, plus an AST scan of
# engine/ and ai/). Measured 2026-09-30 on this container (quiet, 4 cores):
# ~2.0 s for the body, plus ~16 s when it is the first test of the process to
# load the shared card DB. 120 s bounds a hang with room for a slower 2-core
# CI runner.
@pytest.mark.timeout(120)
def test_the_loyalty_slot_rule_has_one_owner_and_loyalty_abilities_are_unchanged(card_db):
    import inspect
    from engine import oracle_parser
    # One owner: no other engine/ai function writes the "ult" slot, and the
    # legacy parser reads its slots from the owner.
    owners, total = [], 0
    for sub in ("engine", "ai"):
        for path in sorted((REPO / sub).rglob("*.py")):
            o, n = _ult_writers(path)
            owners.extend(o)
            total += n
    assert owners == ["engine/oracle_parser.py::loyalty_slot_for"], owners
    assert total == 1
    assert "loyalty_slot_for(" in inspect.getsource(
        oracle_parser.parse_loyalty_abilities)
    # Unchanged: every walker's slots against the pre-refactor capture.
    pinned = json.loads(LOYALTY_CAPTURE_PATH.read_text())["walkers"]
    now = walker_digests(card_db)
    common = set(pinned) & set(now)
    assert len(common) >= 0.95 * len(pinned), (len(common), len(pinned))
    changed = sorted(k for k in common if pinned[k] != now[k])
    assert not changed, f"{len(changed)} walkers changed"


# ════════════════════════════════════════════════════════════════════════
# The whole grammar, L0-L5, over the pool and the witness cards
# ════════════════════════════════════════════════════════════════════════

import dataclasses  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

WITNESS_PATH = Path(__file__).resolve().parent / "fixtures" / \
    "effect_grammar_witnesses.json"


def _ref_str(r) -> str:
    """A Ref, a Selector or a requirement in the fixture's shape language:
    KIND[:index][:PART][:lki][:per_actor], CONTROLLER_OF(<operand>)."""
    from engine.effect_model import Selector
    from engine.effect_spec import Ref, RefPart
    if r is None:
        return "None"
    if isinstance(r, Selector):
        return "SELECTOR:" + r.kind.name
    if not isinstance(r, Ref):
        return type(r).__name__
    out = r.kind.name
    if r.index is not None:
        out += ":%d" % r.index
    if r.part is not RefPart.ALL:
        out += ":" + r.part.name
    if r.lki:
        out += ":lki"
    if r.per_actor:
        out += ":per_actor"
    if r.of is not None:
        out += "(%s)" % _ref_str(r.of)
    return out


def _granted_of(spec):
    from engine.effect_model import Modification
    from engine.effect_spec import Granted, TokenSpec
    p = spec.payload
    if isinstance(p, TokenSpec):
        return p.granted
    if isinstance(p, Granted):
        return p.hosts
    if isinstance(p, Modification):
        return tuple(h for k, v in p.data if k == "granted" for h in v.hosts)
    return ()


def _match_amount(exp, a, path, errs):
    if a is None:
        errs.append("%s: no amount" % path)
        return
    for k, v in exp.items():
        if k == "kind" and a.kind.name != v:
            errs.append("%s.kind %s != %s" % (path, a.kind.name, v))
        elif k == "n" and a.n != v:
            errs.append("%s.n %s != %s" % (path, a.n, v))
        elif k == "ref" and _ref_str(a.ref) != v:
            errs.append("%s.ref %s != %s" % (path, _ref_str(a.ref), v))
        elif k == "inner":
            _match_amount(v, a.inner, path + ".inner", errs)
        elif k == "quantity":
            q = a.quantity
            if q is None:
                errs.append("%s: no quantity" % path)
                continue
            if "kind" in v and q.kind.name != v["kind"]:
                errs.append("%s.quantity.kind %s != %s" % (path, q.kind.name, v["kind"]))
            if "ref" in v and _ref_str(q.ref) != v["ref"]:
                errs.append("%s.quantity.ref %s != %s" % (path, _ref_str(q.ref), v["ref"]))


def _match_specs(exp, specs, path, errs):
    if len(exp) != len(specs):
        errs.append("%s: %d specs %s != expected %d %s" % (
            path, len(specs), [s.verb.name for s in specs], len(exp),
            [e.get("verb") for e in exp]))
        return
    for i, (e, s) in enumerate(zip(exp, specs)):
        _match_spec(e, s, specs, "%s[%d]" % (path, i), errs)


def _match_spec(e, s, siblings, path, errs):
    from engine.effect_spec import CardFilter, Verb
    if s.verb.name != e.get("verb", s.verb.name):
        errs.append("%s.verb %s != %s (%s)" % (
            path, s.verb.name, e["verb"],
            s.payload.detail if s.verb is Verb.UNMODELLED else s.raw))
        return
    for k, v in e.items():
        if k in ("verb",):
            continue
        if k == "optional" and s.optional != v:
            errs.append("%s.optional %s" % (path, s.optional))
        elif k == "flags" and not set(v) <= s.flags:
            errs.append("%s.flags %s lacks %s" % (path, sorted(s.flags), v))
        elif k == "target_slot" and s.target_slot != v:
            errs.append("%s.target_slot %s != %s" % (path, s.target_slot, v))
        elif k in ("ref", "actor", "other") and _ref_str(getattr(s, k)) != v:
            errs.append("%s.%s %s != %s" % (path, k, _ref_str(getattr(s, k)), v))
        elif k == "replaces" and list(s.replaces) != v:
            errs.append("%s.replaces %s != %s" % (path, s.replaces, v))
        elif k == "dest":
            for dk, dv in v.items():
                got = getattr(s.dest, dk, None) if s.dest else None
                if got != dv:
                    errs.append("%s.dest.%s %s != %s" % (path, dk, got, dv))
        elif k == "mod_kind" and (s.mod_kind is None or s.mod_kind.name != v):
            errs.append("%s.mod_kind %s != %s" % (path, s.mod_kind, v))
        elif k == "condition":
            c = s.condition
            if c is None:
                errs.append("%s: no condition" % path)
                continue
            if "kind" in v and c.kind.name != v["kind"]:
                errs.append("%s.condition.kind %s != %s" % (path, c.kind.name, v["kind"]))
            if "ref" in v and _ref_str(c.ref) != v["ref"]:
                errs.append("%s.condition.ref %s != %s" % (path, _ref_str(c.ref), v["ref"]))
        elif k == "amount":
            _match_amount(v, s.amount, path + ".amount", errs)
        elif k == "residue" and not set(v) <= set(s.residue):
            errs.append("%s.residue %s lacks %s" % (path, s.residue, v))
        elif k == "stage" and (s.verb is not Verb.UNMODELLED
                               or s.payload.stage.name != v):
            errs.append("%s.stage %s != %s" % (path, getattr(s.payload, "stage", None), v))
        elif k == "detail" and getattr(s.payload, "detail", None) != v:
            errs.append("%s.detail %s != %s" % (path, getattr(s.payload, "detail", None), v))
        elif k == "then":
            _match_specs(v, s.then, path + ".then", errs)
        elif k == "otherwise":
            _match_specs(v, s.otherwise, path + ".otherwise", errs)
        elif k == "sub":
            p = s.payload
            if v.get("kind") and p.kind.name != v["kind"]:
                errs.append("%s.sub.kind %s" % (path, p.kind.name))
            if v.get("timing") and (p.timing is None or p.timing.name != v["timing"]):
                errs.append("%s.sub.timing %s" % (path, p.timing))
            if "host" in v:
                _match_host(v["host"], p.host, path + ".sub.host", errs)
        elif k == "group_with":
            other = siblings[v]
            if s.group is None or other.group != s.group:
                errs.append("%s.group %s != sibling %s" % (path, s.group, other.group))
        elif k == "granted":
            hosts = _granted_of(s)
            if len(hosts) != len(v):
                errs.append("%s.granted %d hosts != %d" % (path, len(hosts), len(v)))
                continue
            for j, (gh, h) in enumerate(zip(v, hosts)):
                _match_host(gh, h, "%s.granted[%d]" % (path, j), errs)
        elif k == "cost_rule":
            got = dict(s.payload.data).get("cost_rule") if s.mod_kind else None
            got = getattr(got, "cost_rule", got)
            if got != v:
                errs.append("%s.cost_rule %r != %r" % (path, got, v))
        elif k == "filter_classes":
            f = s.filter
            if not isinstance(f, CardFilter) or not set(v) <= f.classes:
                errs.append("%s.filter.classes %s" % (path, getattr(f, "classes", None)))
        elif k == "duration" and (s.duration is None or s.duration.kind.name != v):
            errs.append("%s.duration %s != %s" % (path, s.duration, v))
        elif k == "subject_controller":
            got = dict(s.subject.filter or ()).get("controller") if s.subject else None
            if got != v:
                errs.append("%s.subject controller %s != %s" % (path, got, v))
        elif k == "filter_stat_ref":
            refs = [_ref_str(b[2].quantity.ref) for b in
                    getattr(s.filter, "stat_bounds", ()) if b[2].quantity]
            if v not in refs:
                errs.append("%s.filter stat refs %s lack %s" % (path, refs, v))


def _match_host(e, h, path, errs):
    from engine.effect_spec import iter_specs
    for k, v in e.items():
        if k == "kind" and h.kind.name != v:
            errs.append("%s.kind %s != %s" % (path, h.kind.name, v))
        elif k == "targets" and len(h.targets) != v:
            errs.append("%s.targets %d != %d" % (path, len(h.targets), v))
        elif k == "target_alts" and [list(x) for x in h.target_alts] != v:
            errs.append("%s.target_alts %s != %s" % (path, h.target_alts, v))
        elif k == "mode_groups" and [t.mode_group for t in h.targets] != v:
            errs.append("%s.mode_groups %s" % (path, [t.mode_group for t in h.targets]))
        elif k == "flags" and not set(v) <= h.flags:
            errs.append("%s.flags %s lacks %s" % (path, sorted(h.flags), v))
        elif k == "cost_items":
            items = dict(h.cost.items) if h.cost else {}
            for ck, cv in v.items():
                if items.get(ck) != cv:
                    errs.append("%s.cost.%s %s != %s" % (path, ck, items.get(ck), cv))
        elif k == "cost_modifiers" and len(h.cost_modifiers) != v:
            errs.append("%s.cost_modifiers %d != %d" % (path, len(h.cost_modifiers), v))
        elif k == "keywords" and not set(v) <= {kw.name for kw in h.keywords}:
            errs.append("%s.keywords %s lack %s" % (path, [kw.name for kw in h.keywords], v))
        elif k == "cost_condition" and (h.cost_condition is not None) != v:
            errs.append("%s.cost_condition %s" % (path, h.cost_condition))
        elif k == "from_zone" and h.from_zone != v:
            errs.append("%s.from_zone %s != %s" % (path, h.from_zone, v))
        elif k == "event_hints":
            got = [x.name for x in h.trigger.event_hints] if h.trigger else None
            if got != v:
                errs.append("%s.event_hints %s != %s" % (path, got, v))
        elif k == "intervening_if" and (
                h.trigger is None or (h.trigger.intervening_if is not None) != v):
            errs.append("%s.intervening_if missing" % path)
        elif k == "loyalty_cost":
            lc = h.loyalty_cost
            if lc is None or lc.kind.name != v["kind"] or lc.n != v["n"]:
                errs.append("%s.loyalty_cost %s != %s" % (path, lc, v))
        elif k == "loyalty_slot" and h.loyalty_slot != v:
            errs.append("%s.loyalty_slot %r != %r" % (path, h.loyalty_slot, v))
        elif k == "mode_cost" and h.mode_cost != v:
            errs.append("%s.mode_cost %r != %r" % (path, h.mode_cost, v))
        elif k == "modes":
            if len(h.modes) != len(v):
                errs.append("%s.modes %d != %d" % (path, len(h.modes), len(v)))
                continue
            for j, (me, m) in enumerate(zip(v, h.modes)):
                _match_host(me, m, "%s.modes[%d]" % (path, j), errs)
        elif k == "verbs" and [s.verb.name for s in h.specs] != v:
            errs.append("%s.verbs %s != %s" % (path, [s.verb.name for s in h.specs], v))
        elif k == "no_verbs":
            got = {s.mod_kind.name for s in iter_specs(h.specs) if s.mod_kind}
            if got & set(v):
                errs.append("%s holds %s" % (path, got & set(v)))
        elif k == "specs":
            _match_specs(v, h.specs, path + ".specs", errs)


def _select_host(effects, sel, face):
    from engine.effect_spec import HostKind
    hosts = effects.faces[face] if face < len(effects.faces) else ()
    kind = HostKind[sel["kind"]]
    cands = [h for h in hosts if h.kind is kind]
    if "keyword" in sel:
        cands = [h for h in cands if sel["keyword"] in {k.name for k in h.keywords}]
    if "contains" in sel:
        cands = [h for h in cands if sel["contains"] in h.text]
    if "slot" in sel:
        cands = [h for h in cands if h.loyalty_slot == sel["slot"]]
    nth = sel.get("nth", 0)
    return cands[nth] if nth < len(cands) else None


def _witness_rows():
    rows = json.loads(WITNESS_PATH.read_text())["rows"]
    out = []
    for r in rows:
        marks = ()
        if r.get("known_gap"):
            marks = (pytest.mark.xfail(strict=True, reason=r["known_gap"]),)
        out.append(pytest.param(r, marks=marks, id="%s|%s|%s" % (
            r["card"], r["class"], json.dumps(r["host"], sort_keys=True))))
    return out


def _template_effects(db, template):
    """The lazy per-template path a game uses (step 13)."""
    from engine.effect_grammar import parse_template
    return parse_template(template)


def _template(db, card):
    t = db.cards.get(card)
    if t is None:
        t = next(v for k, v in db.cards.items() if k.split(" // ")[0] == card)
    return t


# Pool-wide (~22.7k templates, facts only, no parse) plus a full parse of
# every registered-deck card both ways. Measured 2026-10-01: ~1.5 s body,
# plus ~16 s when first in the process to load the card DB. 300 s bounds a
# hang on a slower 2-core runner.
@pytest.mark.timeout(300)
def test_the_lazy_template_parse_reads_the_same_facts_as_the_eager_pool_path(card_db):
    """Section 3: a face's facts -- its CR 702 keywords included -- come
    from one source, so `parse_template(t)` (the lazy path a game uses)
    and the eager pool path agree. The keywords are the raw MTGJSON list
    the database loaded, never the typed engine enum."""
    from engine.effect_grammar import parse_template, template_facts
    from engine.effect_spec import canonical
    raw = card_db._raw_data
    diff = [t.name for t in {id(v): v for v in card_db.cards.values()}.values()
            if template_facts(t) != template_facts(
                t, 0, (raw.get(t.name) or {}).get("keywords") or ())]
    assert not diff, (len(diff), diff[:5])
    from decks.modern_meta import MODERN_DECKS
    names = sorted({c for d in MODERN_DECKS.values()
                    for part in ("mainboard", "sideboard")
                    for c in (d.get(part) or {})})
    checked = 0
    for name in names:
        t = card_db.cards.get(name)
        if t is None:
            continue
        kws = (raw.get(t.name) or {}).get("keywords") or ()
        facts = [template_facts(t, 0, kws)]
        if getattr(t, "back_face_oracle", ""):
            facts.append(template_facts(t, 1))
        assert canonical(parse_template(t)) == \
            canonical(parse_template(t, facts)), name
        checked += 1
    assert checked >= 200, checked


@pytest.mark.parametrize("row", _witness_rows())
def test_registered_deck_witness_cards_parse_to_their_expected_spec_chains(card_db, row):
    """Design section 18.1: each registered-deck witness card parses to the
    spec chain the design doc states for its defect class. A known gap is
    a strict xfail: it names the earlier-layer refusal, and the row passes
    -- and must drop its known_gap -- the day that refusal is fixed."""
    from engine.effect_spec import validate_card_effects
    effects = _template_effects(card_db, _template(card_db, row["card"]))
    assert validate_card_effects(effects) is None
    host = _select_host(effects, row["host"], row.get("face", 0))
    assert host is not None, "no host %s on face %s" % (row["host"],
                                                       row.get("face", 0))
    errs = []
    _match_host(row["expect"], host, row["card"], errs)
    assert not errs, "\n".join(errs)


def test_the_witness_fixture_names_only_pool_cards_and_every_defect_class(card_db):
    """The fixture is data about real cards: every row's card is in the
    pool, and every round-2 defect class of section 18.1 has a row."""
    rows = json.loads(WITNESS_PATH.read_text())["rows"]
    for r in rows:
        assert _template(card_db, r["card"]) is not None, r["card"]
    classes = " ".join(r["class"] for r in rows)
    for amendment in ("A13", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8",
                      "A9", "A10", "A11", "A12", "A14", "A15", "A16", "A17",
                      "A18", "A19", "A20", "A21", "A22", "A23", "A24", "A25",
                      "A26", "A27", "A28", "A29", "A30", "A33", "A35", "A36",
                      "A39", "A41"):
        assert amendment in classes, amendment


# ── The pool ────────────────────────────────────────────────────────────

# Design section 12, decided in the L5 review (2026-10-01): the 4.0 s
# POOL_PARSE_CPU_BUDGET_S stays the design budget the leaf and L0-L1 shares
# are scaled from, and two budgets gate the whole grammar:
#
# * the EAGER whole-pool L0-L5 pass (`parse_pool`, the tools' path; games
#   never run it) is revised to its measurement: 19.8 s process CPU on a
#   quiet 4-core box (card DB frozen out of the collector, caches cleared,
#   best of two), gated at 30 s -- about 1.5x, sized so a slower CI core
#   still passes while a layer that regresses by half is named;
# * the LAZY per-template path (`parse_template`, what a game calls through
#   CardTemplate.effects) is gated over the cards a game can touch: every
#   registered-deck card parsed cold, measured 0.30-0.39 s for 359
#   templates (mean ~1 ms, max 4-6 ms), against 1.0 s total and 50 ms for
#   any one template (single-template times are noisy: a collector pass
#   can land in any one of them).
POOL_L0_L5_EAGER_CPU_BUDGET_S = 30.0
# The module memos (every lru_cache of L0-L5 and the leaves) held after one
# eager pool pass with its results dropped, by tracemalloc. Revised in the
# E0 integration review (2026-10-01) from the 40 MB design budget to its
# measurement: 51-52 MB (sub/filter 8.6, keywords 7.9, patterns 6.1,
# effect_spec 4.3, lexicon 4.2, normalize 4.1, sub/target 3.8,
# sub/condition 2.9, the rest under 1.5 each), gated at about 1.3x so a
# memo that grows by a third is named. Every memo is bounded, so a game
# (which parses only the cards it touches) holds less; `clear_caches`
# drops them all.
POOL_L0_L5_MEMO_BUDGET_MB = 68.0
DECK_CARDS_LAZY_CPU_BUDGET_S = 1.0
TEMPLATE_LAZY_CPU_MAX_S = 0.05

# Typed share (non-UNMODELLED specs, sub-ability hosts included) by host
# kind, measured 2026-10-01 over the whole pool after L5. A floor a few
# points below each: a fall names the host kind whose clauses regressed.
TYPED_SHARE_FLOORS = {
    "SPELL": 0.70, "MODE": 0.74, "ACTIVATED": 0.76, "MANA_ABILITY": 0.92,
    "LOYALTY": 0.72, "TRIGGERED": 0.74, "CHAPTER": 0.70, "STATIC": 0.57,
}


def _pool_canonical_digest() -> str:
    """sha256 over the canonical form of every template's effects, in name
    order (run in a subprocess by the hash-seed test)."""
    from engine.effect_grammar import parse_pool
    from engine.effect_spec import canonical
    from tests._card_db_cache import shared_card_database
    effects = parse_pool(shared_card_database())
    h = hashlib.sha256()
    for name in sorted(effects):
        h.update(name.encode())
        h.update(canonical(effects[name]).encode())
    return h.hexdigest()


@pytest.fixture(scope="module")
def pool_effects(card_db):
    """Every template's CardEffects through the eager pool path, with the
    process CPU it took (caches cleared, DB frozen out of the collector)."""
    import gc
    import time

    import engine.effect_grammar as grammar
    grammar.clear_caches()
    gc.collect()
    gc.freeze()
    try:
        t0 = time.process_time()
        effects = grammar.parse_pool(card_db)
        cpu = time.process_time() - t0
    finally:
        gc.unfreeze()
    grammar.clear_caches()
    return effects, cpu


# Measured 2026-10-01: ~36 s wall when first in the process (16 s DB load,
# ~20 s for the eager L0-L5 pass and the walks below). 600 s bounds a hang
# with room for a slower 2-core CI runner.
@pytest.mark.timeout(600)
def test_every_spec_satisfies_the_schema_invariants(card_db, pool_effects):
    """L5 step 11: every template parses without an exception, and every
    spec of every host -- sub-ability and granted hosts with their creating
    hosts in view -- passes validate_spec."""
    from engine.effect_spec import CardEffects, validate_card_effects
    effects, _cpu = pool_effects
    assert len(effects) >= 0.95 * len({id(v) for v in card_db.cards.values()})
    bad = []
    for name, ce in effects.items():
        assert isinstance(ce, CardEffects), name
        rule = validate_card_effects(ce)
        if rule is not None:
            bad.append((name, rule))
    assert not bad, bad[:10]


@pytest.mark.timeout(600)
def test_every_card_effects_value_is_hashable_and_holds_no_mutable_cost(pool_effects):
    """Invariant 8: every CardEffects of the pool -- not a sample -- is
    hashable and reaches no mutable object (a cost is a frozen
    CostSnapshot, never an ActivationCost)."""
    from engine.effect_spec import find_mutable
    effects, _cpu = pool_effects
    bad = []
    for name, ce in effects.items():
        where = find_mutable(ce)
        if where is not None:
            bad.append((name, where))
            continue
        try:
            hash(ce)
        except TypeError as e:
            bad.append((name, str(e)))
    assert not bad, (len(bad), bad[:5])


@pytest.mark.timeout(600)
def test_sub_ability_targets_are_never_in_the_parents_targets(pool_effects):
    """A30, CR 603.12: a sub-ability's requirements are chosen when it
    triggers -- they are its own host's, never its creator's."""
    from engine.effect_spec import RefKind, SubAbility, _refs, iter_specs
    effects, _cpu = pool_effects
    checked = 0
    for name, ce in effects.items():
        for h in ce.walk():
            for s in iter_specs(h.specs):
                if isinstance(s.payload, SubAbility):
                    sub = s.payload.host
                    checked += 1
                    # Every target the sub host's specs name -- a slot or
                    # a TARGET ref -- indexes the sub host's own targets;
                    # a creator's target is read as its spec's RESULT
                    # (A34), never as a TARGET ref of the sub host.
                    for t in iter_specs(sub.specs):
                        if t.target_slot is not None:
                            assert t.target_slot < len(sub.targets), name
                            assert t.target is sub.targets[t.target_slot], name
                        for r in _refs(t):
                            if r.kind is RefKind.TARGET:
                                assert r.index is not None and \
                                    r.index < len(sub.targets), name
                    assert not any(t is p for t in sub.targets
                                   for p in h.targets), name
    assert checked >= 100


@pytest.mark.timeout(600)
def test_no_actor_is_the_result_of_a_refused_clause(pool_effects):
    """Section 7 "That player": who performs a clause is a player mention,
    a selector or a chosen player -- never the unknown result of a refused
    (UNMODELLED) clause, which would make an unread object the actor."""
    from engine.effect_spec import Ref, RefKind, Verb, iter_specs
    effects, _cpu = pool_effects
    bad = []
    for name, ce in effects.items():
        for h in ce.walk():
            by = {s.seq: s for s in iter_specs(h.specs)}
            for s in iter_specs(h.specs):
                a = s.actor
                if isinstance(a, Ref) and a.kind is RefKind.RESULT and \
                        a.index in by and by[a.index].verb is Verb.UNMODELLED:
                    bad.append((name, s.raw))
    assert not bad, (len(bad), bad[:5])


@pytest.mark.timeout(600)
def test_no_spec_replaces_a_refused_spec_and_no_replacement_refusal_replaces(pool_effects):
    """A15, section 7: an instead clause replaces a known spec; a refused
    antecedent leaves the replaced action unknown, and a REPLACEMENT
    refusal ("if ... would ...", CR 614) is no instead sibling."""
    from engine.effect_spec import Stage, Verb, iter_specs
    effects, _cpu = pool_effects
    bad = []
    for name, ce in effects.items():
        for h in ce.walk():
            by = {s.seq: s for s in iter_specs(h.specs)}
            for s in iter_specs(h.specs):
                if not s.replaces:
                    continue
                if s.verb is Verb.UNMODELLED and \
                        s.payload.stage is Stage.REPLACEMENT:
                    bad.append((name, "replacement", s.raw))
                elif any(k in by and by[k].verb is Verb.UNMODELLED
                         for k in s.replaces):
                    bad.append((name, "refused antecedent", s.raw))
    assert not bad, (len(bad), bad[:5])


@pytest.mark.timeout(600)
def test_the_typed_share_by_host_kind_holds_its_floor(pool_effects):
    """The census by host kind after L5: the share of specs that are typed
    (not UNMODELLED) per host kind, each at or above its floor; a linking
    refusal is UNMODELLED(REFERENCE / DELAY / INVALID) with a link.* or
    schema detail, never a silent default."""
    import collections

    from engine.effect_spec import Stage, Verb, iter_specs
    effects, _cpu = pool_effects
    tot, typed, link = collections.Counter(), collections.Counter(), 0
    for ce in effects.values():
        for h in ce.walk():
            for s in iter_specs(h.specs):
                tot[h.kind.name] += 1
                if s.verb is not Verb.UNMODELLED:
                    typed[h.kind.name] += 1
                elif s.payload.detail.startswith("link."):
                    link += 1
                    assert s.payload.stage in (Stage.REFERENCE, Stage.DELAY)
    shares = {k: typed[k] / tot[k] for k in tot}
    low = {k: round(shares[k], 3) for k, floor in TYPED_SHARE_FLOORS.items()
           if k in shares and shares[k] < floor}
    assert not low, (low, {k: round(v, 3) for k, v in shares.items()})
    # Linking refusals stay a small fraction of all specs.
    assert link <= 0.02 * sum(tot.values()), link


@pytest.mark.timeout(600)
def test_effect_parse_fits_the_load_budget(pool_effects):
    """Section 12: the whole-pool eager L0-L5 pass (the tools' path) in
    process CPU against its revised budget, POOL_L0_L5_EAGER_CPU_BUDGET_S
    (measured 2026-10-01 at 19.8 s: L0-L4 about 9 s, L5 about 5 s of which
    about half is validate_spec over every spec, plus collector time)."""
    _effects, cpu = pool_effects
    assert cpu <= POOL_L0_L5_EAGER_CPU_BUDGET_S, cpu


# One eager pool pass under tracemalloc. Measured 2026-10-01: ~140 s wall
# (tracemalloc roughly sextuples the pass), plus ~16 s when first in the
# process to load the card DB. 900 s bounds a hang on a slower runner.
@pytest.mark.timeout(900)
def test_the_whole_grammar_memos_after_a_pool_pass_fit_their_budget(card_db):
    """Section 12: after one eager L0-L5 pool pass, with its results
    dropped, the grammar's module memos hold at most
    POOL_L0_L5_MEMO_BUDGET_MB (tracemalloc), and `clear_caches` returns
    them -- every memo is bounded, none keeps the pool."""
    import gc
    import tracemalloc

    import engine.effect_grammar as grammar
    grammar.clear_caches()
    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.take_snapshot()
        grammar.parse_pool(card_db)
        gc.collect()
        held = sum(d.size_diff for d in
                   tracemalloc.take_snapshot().compare_to(base, "filename"))
        grammar.clear_caches()
        gc.collect()
        left = sum(d.size_diff for d in
                   tracemalloc.take_snapshot().compare_to(base, "filename"))
    finally:
        tracemalloc.stop()
        grammar.clear_caches()
    print("\nwhole-grammar memos after a pool pass: %.1f MB (%.1f MB after "
          "clear_caches)" % (held / 1e6, left / 1e6))
    assert held <= POOL_L0_L5_MEMO_BUDGET_MB * 1e6, held / 1e6
    assert left <= 0.05 * POOL_L0_L5_MEMO_BUDGET_MB * 1e6, left / 1e6


# Registered-deck templates (~360), each parsed cold. Measured 2026-10-01:
# ~0.4 s body, plus ~16 s when first in the process to load the card DB.
# 300 s bounds a hang on a slower 2-core runner.
@pytest.mark.timeout(300)
def test_the_lazy_per_template_parse_of_every_card_a_game_can_touch_fits_its_budget(card_db):
    """Section 12: a game parses lazily, per template (CardTemplate.effects),
    so `CardDatabase()` load time is unchanged. The cards a game can touch
    are the registered decks' main and sideboards: parsed cold, in process
    CPU, they fit DECK_CARDS_LAZY_CPU_BUDGET_S, and no one template takes
    more than TEMPLATE_LAZY_CPU_MAX_S."""
    import gc
    import time

    import engine.effect_grammar as grammar
    from decks.modern_meta import MODERN_DECKS
    names = sorted({c for d in MODERN_DECKS.values()
                    for part in ("mainboard", "sideboard")
                    for c in (d.get(part) or {})})
    templates = [card_db.cards[n] for n in names if n in card_db.cards]
    assert len(templates) >= 200, len(templates)
    grammar.clear_caches()
    gc.collect()
    gc.freeze()
    try:
        per = []
        t0 = time.process_time()
        for t in templates:
            a = time.process_time()
            grammar.parse_template(t)
            per.append(time.process_time() - a)
        total = time.process_time() - t0
    finally:
        gc.unfreeze()
        grammar.clear_caches()
    assert total <= DECK_CARDS_LAZY_CPU_BUDGET_S, total
    worst = max(per)
    assert worst <= TEMPLATE_LAZY_CPU_MAX_S, (
        worst, templates[per.index(worst)].name)


# Two subprocesses, run in parallel, each loading the card DB (~16 s) and
# parsing the pool (~20 s): measured ~45 s wall on this container. 900 s
# bounds a hang on a slower 2-core runner.
@pytest.mark.timeout(900)
def test_effect_parse_is_independent_of_the_hash_seed():
    """F10, section 12: the canonical form of every template's effects is
    byte-identical under PYTHONHASHSEED 0 and 1 (no set or dict order
    reaches the output)."""
    repo = Path(__file__).resolve().parent.parent
    code = ("from tests.test_effect_grammar_pool_invariants import "
            "_pool_canonical_digest as d; print(d())")
    procs = []
    for seed in ("0", "1"):
        env = dict(os.environ, PYTHONHASHSEED=seed,
                   MTG_LLM_DECISION_SCORER_OFFLINE="1")
        procs.append(subprocess.Popen([sys.executable, "-c", code], cwd=repo,
                                      env=env, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True))
    outs = [p.communicate() for p in procs]
    digests = [o.strip().splitlines()[-1] if o.strip() else "" for o, _e in outs]
    assert all(p.returncode == 0 for p in procs), [e[-2000:] for _o, e in outs]
    assert digests[0] and digests[0] == digests[1], digests


# ════════════════════════════════════════════════════════════════════════
# CardTemplate.effects: lazy, memoised, never parsed at load (step 13)
# ════════════════════════════════════════════════════════════════════════
#
# Decided 2026-10-01 (section 12): the whole-pool parse costs far more than
# the load budget, so `CardTemplate.effects` parses on first access, per
# template, through the same `parse_template(t)` the eager tools' path
# (`parse_pool`) calls, and memoises. `CardDatabase()` parses nothing.

def _deck_card_names():
    from decks.modern_meta import MODERN_DECKS
    return sorted({c for d in MODERN_DECKS.values()
                   for part in ("mainboard", "sideboard")
                   for c in (d.get(part) or {})})


def _effects_sample(card_db):
    """Every registered-deck template plus every 97th pool template."""
    pool = sorted({id(v): v for v in card_db.cards.values()}.values(),
                  key=lambda t: t.name)
    picked = {t.name: t for t in pool[::97]}
    for n in _deck_card_names():
        t = card_db.cards.get(n)
        if t is not None:
            picked[t.name] = t
    return [picked[k] for k in sorted(picked)]


def _counting_template_parses(monkeypatch):
    """Count every template parse and every face parse (L0-L5)."""
    import engine.effect_grammar as grammar
    from engine.effect_grammar import link
    calls = {"template": 0, "face": 0}
    real_t, real_f = grammar.parse_template, link.parse_face_hosts

    def t(*a, **kw):
        calls["template"] += 1
        return real_t(*a, **kw)

    def f(*a, **kw):
        calls["face"] += 1
        return real_f(*a, **kw)
    monkeypatch.setattr(grammar, "parse_template", t)
    monkeypatch.setattr(link, "parse_face_hosts", f)
    return calls


def test_no_template_parses_its_effects_while_the_database_loads(tmp_path, monkeypatch):
    """CardDatabase load builds no effects: every template's `_effects` is
    still unset after the load and no face was parsed, so the load's CPU is
    unchanged by the grammar."""
    from tests._mini_card_db import load_mini_db
    calls = _counting_template_parses(monkeypatch)
    db = load_mini_db(tmp_path)
    assert len(db.cards) >= 4
    assert calls == {"template": 0, "face": 0}
    for t in db.cards.values():
        assert t._effects is None, t.name
        for ab in list((t.loyalty_abilities or {}).values()) + \
                list((t.back_face_loyalty_abilities or {}).values()):
            assert ab.clause is None or ab.clause._effects is None


def test_a_template_parses_its_effects_on_first_access_and_memoises(monkeypatch):
    from engine.cards import CardTemplate, CardType
    from engine.effect_spec import EMPTY_EFFECTS, HostKind, Verb
    from engine.mana import ManaCost
    calls = _counting_template_parses(monkeypatch)
    t = CardTemplate(name="Fixture Insight", card_types=[CardType.SORCERY],
                     mana_cost=ManaCost(generic=2),
                     oracle_text="Draw two cards.")
    assert calls["template"] == 0 and t._effects is None
    first = t.effects
    assert calls["template"] == 1
    assert first.spell().kind is HostKind.SPELL
    assert Verb.DRAW in first.verbs
    assert t.effects is first and calls["template"] == 1     # memoised
    # The memo is the effects of the printed text it was parsed from: a
    # template whose text is replaced (tests copy and re-print templates)
    # parses again instead of serving the old text's effects.
    t.oracle_text = "Draw a card."
    assert t.effects is not first and calls["template"] == 2
    # set_effects pins a value for the current text.
    t.set_effects(EMPTY_EFFECTS)
    assert t.effects is EMPTY_EFFECTS and calls["template"] == 2


def test_a_template_whose_facts_change_after_its_first_effects_read_parses_again(monkeypatch):
    """The memo is keyed on the complete parse input -- both face texts and
    every face's facts (types, supertypes, subtypes, X cost, printed
    keywords) -- so a template whose type changes after its first read
    parses again instead of serving effects parsed under the old facts."""
    from engine.cards import CardTemplate, CardType
    from engine.effect_spec import HostKind
    from engine.mana import ManaCost
    calls = _counting_template_parses(monkeypatch)
    t = CardTemplate(name="Fixture Shape", card_types=[CardType.CREATURE],
                     mana_cost=ManaCost(generic=2), oracle_text="Draw a card.")
    assert not any(h.kind is HostKind.SPELL for h in t.effects.faces[0])
    assert calls["template"] == 1
    t.card_types = [CardType.SORCERY]
    assert t.effects.spell() is not None and calls["template"] == 2
    t.printed_keywords = ["Flying"]
    t.effects
    assert calls["template"] == 3
    t.effects
    assert calls["template"] == 3                             # memoised


# Every registered-deck template (~360) plus every 97th pool template
# (~235), each parsed twice. Measured 2026-10-01: ~1.5 s body, plus ~16 s
# when first in the process to load the card DB. 300 s bounds a hang on a
# slower 2-core runner.
@pytest.mark.timeout(300)
def test_the_lazy_effects_property_and_the_eager_pool_path_give_identical_specs(card_db):
    import engine.effect_grammar as grammar
    from engine.effect_spec import canonical
    sample = _effects_sample(card_db)
    assert len(sample) >= 400, len(sample)
    grammar.clear_caches()
    for t in sample:
        t.set_effects(None)
    lazy = {t.name: canonical(t.effects) for t in sample}
    grammar.clear_caches()

    class _Subset:
        cards = {t.name: t for t in sample}
    eager = grammar.parse_pool(_Subset)
    assert sorted(eager) == sorted(lazy)
    diff = [n for n in lazy if canonical(eager[n]) != lazy[n]]
    assert not diff, (len(diff), diff[:5])
    # The tools' populate mode writes the same value the property computes.
    for t in sample:
        t.set_effects(None)
    grammar.parse_pool(_Subset, populate=True)
    assert all(t._effects is not None and canonical(t._effects) == lazy[t.name]
               for t in sample)


def _rules_text(text):
    import re
    return re.sub(r"\([^)]*\)", "", text or "").strip()


@pytest.mark.timeout(300)
def test_every_template_with_oracle_text_has_effects_after_load(card_db):
    """Lazily: every template's first `effects` access returns a validated
    CardEffects with a host for every printed face."""
    from engine.effect_spec import CardEffects, validate_card_effects
    for t in _effects_sample(card_db):
        ce = t.effects
        assert isinstance(ce, CardEffects), t.name
        assert validate_card_effects(ce) is None, t.name
        # A face whose printed text is only reminder text (CR 207.2) has
        # no ability to host.
        if _rules_text(t.oracle_text):
            assert ce.faces and ce.faces[0], t.name
        if _rules_text(getattr(t, "back_face_oracle", "")):
            assert len(ce.faces) == 2 and ce.faces[1], t.name


def test_synthetic_templates_get_the_same_effects_as_loaded_ones(card_db):
    """A template built directly (tests, tokens) parses through the same
    lazy path and facts as a loaded one: equal printed fields give equal
    effects."""
    import dataclasses
    from engine.cards import CardTemplate
    from engine.effect_spec import canonical
    checked = 0
    for name in _deck_card_names():
        t = card_db.cards.get(name)
        if t is None or getattr(t, "back_face_oracle", ""):
            continue
        synthetic = CardTemplate(
            name=t.name, card_types=list(t.card_types),
            mana_cost=t.mana_cost, supertypes=list(t.supertypes),
            subtypes=list(t.subtypes), oracle_text=t.oracle_text,
            printed_keywords=t.printed_keywords, layout=t.layout)
        assert synthetic._effects is None
        assert canonical(synthetic.effects) == canonical(t.effects), name
        # A copied template (dataclasses.replace) carries the memo while
        # its text is unchanged.
        assert dataclasses.replace(t).effects is t.effects
        checked += 1
    assert checked >= 200, checked


@pytest.mark.timeout(300)
def test_a_meld_cards_melded_permanent_name_is_not_a_self_reference(card_db):
    """CR 712.4: a meld card's "A // B" second half names the melded
    permanent, a different object, so the production face facts
    (`template_facts`, read by the lazy and the eager path alike) never
    make it a self-name; the face's own half still is. The layout fact is
    the card's printed MTGJSON layout (`CardTemplate.layout`)."""
    from engine.effect_grammar import parse_template, template_facts
    melds = [t for t in {id(v): v for v in card_db.cards.values()}.values()
             if t.layout == "meld" and " // " in t.name]
    assert len(melds) >= 8, len(melds)
    linked = 0
    for t in melds:
        own, melded = t.name.split(" // ", 1)
        names = template_facts(t).names
        assert own in names and melded not in names, (t.name, names)
        text = (t.oracle_text or "").lower()
        if "meld them into" in text:
            texts = [h.text for h in parse_template(t).faces[0]]
            assert any("meld them into " + melded.lower() in x
                       for x in texts), (t.name, texts)
            assert not any("meld them into ~" in x for x in texts), t.name
            linked += 1
    assert linked >= 1
    # A non-meld split name keeps both halves as self-names.
    split = next(t for t in card_db.cards.values()
                 if " // " in t.name and t.layout in ("split", "adventure"))
    names = template_facts(split).names
    assert all(h in names for h in split.name.split(" // ")), split.name


# ════════════════════════════════════════════════════════════════════════
# The linked pool against L1-L4 and the legacy owners (steps 12-13)
# ════════════════════════════════════════════════════════════════════════

def _l1_and_linked(card_db, effects):
    """(name, face facts, L1 host, linked host) for every host and mode of
    every face, the linked host read from the eager pool parse."""
    from engine.effect_grammar import _face_texts, template_facts
    from engine.effect_grammar import structure as S
    for t in {id(v): v for v in card_db.cards.values()}.values():
        ce = effects.get(t.name)
        if ce is None:
            continue
        for i, text in enumerate(_face_texts(t)):
            if not text:
                continue
            facts = template_facts(t, i)
            fs = S.parse_face_structure(text, facts, face=i)
            linked = ce.faces[i] if i < len(ce.faces) else ()
            assert len(linked) == len(fs.hosts), (t.name, i)
            for l1, h in zip(fs.hosts, linked):
                yield t.name, facts, l1, h
                assert len(l1.modes) == len(h.modes), (t.name, i)
                for ml, mh in zip(l1.modes, h.modes):
                    yield t.name, facts, ml, mh


# One L1 + L2-L4 pass over the pool beside the eager linked parse. Measured
# 2026-10-01: ~15 s body after the pool_effects fixture, plus the fixture
# (~36 s) when first. 600 s bounds a hang on a slower 2-core runner.
@pytest.mark.timeout(600)
def test_every_effect_text_span_is_covered_by_a_spec_or_a_consumed_frame_token(card_db, pool_effects):
    """The coverage invariant after L5 (section 3): L1-L4 pin that every
    non-space character of a host is in a consumed span, a frame token or a
    clause span (`structure.uncovered`, `clauses.uncovered`); linking must
    drop none of them, so every L4 clause span and every L1 refusal span
    lies inside a spec span of the linked host or of a sub-ability host it
    created (a branch, an instead sibling and a lowered spec keep their
    span)."""
    from engine.effect_grammar import patterns as PT
    from engine.effect_spec import _walk_hosts, iter_specs
    effects, _cpu = pool_effects
    lost, n = [], 0
    for name, facts, l1, h in _l1_and_linked(card_db, effects):
        spans = [s.span for hh, _c in _walk_hosts(((h,),), False, True)
                 if hh is h or hh.mode_index < 0
                 for s in iter_specs(hh.specs)]
        need = [cm.spec.span for fm in PT.match_host(l1, has_x=facts.has_x_cost)
                for cm in fm.clauses]
        need += [sp for _um, sp in l1.unmodelled]
        for a, b in need:
            n += 1
            if not any(x <= a and b <= y for x, y in spans):
                lost.append((name, l1.text[a:b]))
    assert n > 40000, n
    assert not lost, (len(lost), lost[:10])


_COST_LINE_KINDS = ("KEYWORD", "ALTERNATIVE_COST", "ADDITIONAL_COST")


@pytest.mark.timeout(300)
def test_no_registered_deck_spell_merges_a_keyword_or_cost_line_into_its_resolution_host(card_db):
    """F1, CR 113.3a / 118.9 / 601.2f: an instant's or sorcery's spell
    ability is its resolution text only. A keyword line, an alternative
    cost and an additional cost are hosts of their own, so no SPELL host of
    a registered-deck card shares a paragraph with one, carries keywords,
    or has an activation cost."""
    from engine.effect_spec import HostKind
    bad, spells = [], 0
    for name in _deck_card_names():
        t = card_db.cards.get(name)
        if t is None:
            continue
        for face in t.effects.faces:
            lines = {p for h in face if h.kind.name in _COST_LINE_KINDS
                     for p in h.paragraphs}
            for h in face:
                if h.kind is not HostKind.SPELL:
                    continue
                spells += 1
                if set(h.paragraphs) & lines or h.keywords or h.cost:
                    bad.append((name, h.paragraphs, sorted(lines), h.text))
    assert spells >= 50, spells
    assert not bad, bad[:10]


@pytest.mark.timeout(600)
def test_every_reference_points_backwards_within_its_ability_or_to_its_parent_from_a_sub_ability(pool_effects):
    """Section 7, invariant 3: a RESULT reference names an earlier spec of
    its own host or, from a sub-ability host, of a host that created it (a
    granted ability is an ability of its own and reads no creator); a
    TARGET reference names one of its own host's requirements; an instead
    sibling replaces earlier specs of its own host."""
    from engine.effect_spec import (Granted, RefKind, SubAbility, TokenSpec,
                                    _refs, _walk_hosts, iter_specs)
    effects, _cpu = pool_effects
    skip = (SubAbility, Granted, TokenSpec)
    fields = ("target", "subject", "ref", "other", "actor", "filter",
              "amount", "dest", "payload", "condition")
    bad, checked = [], 0
    for name, ce in effects.items():
        for h, creators in _walk_hosts(ce.faces, True, True):
            own = {s.seq for s in iter_specs(h.specs)}
            reach = own | {s.seq for c in creators for s in iter_specs(c.specs)}
            for s in iter_specs(h.specs):
                for f in fields:
                    v = getattr(s, f)
                    if v is None or isinstance(v, skip):
                        continue
                    for r in _refs(v):
                        if r.kind is RefKind.RESULT:
                            checked += 1
                            if not (r.index < s.seq and r.index in reach):
                                bad.append((name, s.raw, "result"))
                        elif r.kind is RefKind.TARGET:
                            checked += 1
                            if not 0 <= r.index < len(h.targets):
                                bad.append((name, s.raw, "target"))
                if any(not (k < s.seq and k in own) for k in s.replaces):
                    bad.append((name, s.raw, "replaces"))
    assert checked > 4000, checked
    assert not bad, (len(bad), bad[:10])


@pytest.mark.timeout(600)
def test_mode_hosts_align_with_template_modes_by_index(card_db, pool_effects):
    """CR 700.2: where the legacy modal parse (`CardTemplate.modes`) and the
    grammar both read a card's modes, they read the same number and mode
    host i is legacy mode i (mode_index i, a non-empty text each; the
    texts differ in case and self-forms only). A disagreement is one of two
    classified kinds and nothing else: the grammar reads modes legacy has
    no model for (a spree spell's mode costs, a modal triggered ability),
    or the grammar refuses a header legacy reads (every bullet an explicit
    structure.orphan_mode refusal, never a silent drop)."""
    from engine.effect_spec import HostKind, Verb
    effects, _cpu = pool_effects
    ok, reads_more, refused, bad = 0, 0, 0, []
    for t in {id(v): v for v in card_db.cards.values()}.values():
        ce = effects.get(t.name)
        legacy = t.modes or []
        mine = ce.modes(0) if ce is not None else ()
        if not legacy and not mine:
            continue
        if len(legacy) == len(mine):
            for i, (lm, m) in enumerate(zip(legacy, mine)):
                if m.mode_index != i or not m.text.strip("• ") or \
                        not (lm.get("text") or "").strip():
                    bad.append((t.name, i, lm.get("text"), m.text))
            ok += 1
        elif not legacy:
            host = next(h for h in ce.faces[0] if h.modes)
            if host.kind is HostKind.TRIGGERED or any(m.mode_cost for m in mine):
                reads_more += 1
            else:
                bad.append((t.name, "grammar-only modes", host.kind.name))
        elif not mine:
            orphans = [h for h in ce.faces[0] if h.kind is HostKind.UNKNOWN
                       and h.specs and all(
                           s.verb is Verb.UNMODELLED
                           and s.payload.detail == "structure.orphan_mode"
                           for s in h.specs)]
            if len(orphans) == len(legacy):
                refused += 1
            else:
                bad.append((t.name, "legacy-only modes", len(legacy)))
        else:
            bad.append((t.name, len(legacy), len(mine)))
    print("\nmodes: %d aligned, %d grammar-only (spree / modal trigger), "
          "%d refused headers" % (ok, reads_more, refused))
    assert ok >= 500, ok
    assert not bad, (len(bad), bad[:10])


@pytest.mark.timeout(600)
def test_back_face_loyalty_hosts_align_with_back_face_loyalty_abilities(card_db, pool_effects):
    """A12: a transforming walker's back-face loyalty abilities and the
    face-1 LOYALTY hosts are one set of slots with the same signed costs
    (one `loyalty_slot_for` owner), and each back-face clause template's
    effects are the face-1 host of its slot."""
    from engine.effect_spec import HostKind
    effects, _cpu = pool_effects
    checked, bad = 0, []
    for t in {id(v): v for v in card_db.cards.values()}.values():
        back = t.back_face_loyalty_abilities
        if not back:
            continue
        ce = effects[t.name]
        hosts = {h.loyalty_slot: h for h in (ce.faces[1] if len(ce.faces) > 1
                                             else ())
                 if h.kind is HostKind.LOYALTY and h.loyalty_slot}
        if sorted(hosts) != sorted(back):
            bad.append((t.name, sorted(back), sorted(hosts)))
            continue
        for slot, ab in back.items():
            lc = hosts[slot].loyalty_cost
            if lc is None or lc.n != ab.cost:
                bad.append((t.name, slot, ab.cost, lc))
        checked += 1
    assert checked >= 10, checked
    assert not bad, bad[:10]


# The step-18 equivalence allowlist's seed for activation costs: every pool
# ability whose grammar host cost differs from the legacy
# `ActivatedAbility.cost`, each classified. Regenerate with
#   python -c "from tests.test_effect_grammar_pool_invariants import \
#              write_cost_divergences as w; w()"
COST_DIVERGENCE_PATH = Path(__file__).resolve().parent / "fixtures" / \
    "effect_grammar_activation_cost_divergences.json"


def _cost_default(v) -> bool:
    if isinstance(v, tuple) and v and isinstance(v[0], tuple):
        return all(_cost_default(x[1]) for x in v)       # a mana snapshot
    return not v


def _cost_class(legacy, mine) -> str:
    """'grammar_reads_more' when the host cost is the legacy cost with
    more of the printed cost read: legacy gave up on part of it (a
    non-empty `unpayable`), the host's `unpayable` is a strict subset of
    legacy's, and every other field that differs is at its default in
    legacy. Anything else is 'regression'."""
    a, b = dict(legacy.items), dict(mine.items)
    lu, mu = set(a.get("unpayable") or ()), set(b.get("unpayable") or ())
    if not lu or not mu < lu:
        return "regression"
    for k in set(a) | set(b):
        if k != "unpayable" and a.get(k) != b.get(k) and \
                not _cost_default(a.get(k)):
            return "regression"
    return "grammar_reads_more"


def _cost_divergences(card_db, effects):
    """{"<card>|<index>": class} for every face-0 activated ability whose
    host cost differs from its legacy cost (the ordinal half is pinned by
    test_activation_ordinals_follow_the_legacy_ordinal_rule_on_every_face)."""
    from engine.effect_spec import freeze_cost
    out, ok = {}, 0
    for t in {id(v): v for v in card_db.cards.values()}.values():
        ce = effects.get(t.name)
        for ab in t.activated_abilities or ():
            h = ce.activated(ab.index) if ce is not None else None
            if h is None:
                continue
            legacy = freeze_cost(ab.cost)
            if legacy == h.cost:
                ok += 1
                continue
            out["%s|%d" % (t.name, ab.index)] = (
                _cost_class(legacy, h.cost) if legacy and h.cost
                else "regression")
    return out, ok


def write_cost_divergences():
    import engine.effect_grammar as grammar
    from tests._card_db_cache import shared_card_database
    db = shared_card_database()
    rows, _ok = _cost_divergences(db, grammar.parse_pool(db))
    COST_DIVERGENCE_PATH.write_text(json.dumps({
        "doc": "Activation costs where the grammar host (A7: "
               "parse_activation_cost over the printed head) differs from "
               "the legacy ActivatedAbility.cost, each classified; the seed "
               "of the step-18 equivalence allowlist. Only "
               "grammar_reads_more rows may appear.",
        "rows": [{"key": k, "class": v} for k, v in sorted(rows.items())]},
        indent=1, ensure_ascii=False) + "\n")


@pytest.mark.timeout(600)
def test_activated_hosts_align_with_parsed_activated_abilities_by_index_and_cost(card_db, pool_effects):
    """A7, section 3 L1 rule 9: an activated host's cost is
    freeze_cost(parse_activation_cost(printed head)), the rule legacy
    `ActivatedAbility.cost` follows, so host i's cost equals legacy
    ability i's -- except where the grammar reads more of the printed cost
    than legacy did. Every divergence is classified and listed in the
    allowlist seed: a regression fails, a new divergence fails, and a
    stale row (a divergence that closed) fails until it is removed."""
    effects, _cpu = pool_effects
    found, ok = _cost_divergences(card_db, effects)
    pinned = {r["key"]: r["class"] for r in
              json.loads(COST_DIVERGENCE_PATH.read_text())["rows"]}
    regressions = sorted(k for k, v in found.items() if v != "grammar_reads_more")
    assert not regressions, regressions[:10]
    assert set(pinned.values()) <= {"grammar_reads_more"}
    new = sorted(set(found) - set(pinned))
    stale = sorted(set(pinned) - set(found))
    assert not new and not stale, (new[:10], stale[:10])
    assert ok >= 6000, ok


def test_identical_ability_text_parses_once_to_shared_frozen_specs():
    """A32, section 12: a face parse is a pure function of (text, facts,
    face), so two templates printing the same face share ONE parse -- the
    same host objects, not equal copies -- and that parse is frozen: no
    host or spec can be changed by a reader, and every value is hashable."""
    import dataclasses
    from types import SimpleNamespace

    from engine.cards import CardType
    from engine.effect_grammar import parse_template
    from engine.effect_spec import find_mutable

    def template():
        return SimpleNamespace(
            name="Some Relic", layout="normal",
            oracle_text="{2}, {T}, Sacrifice this artifact: Draw a card.\n"
                        "When this artifact enters, scry 1.",
            card_types=[CardType.ARTIFACT], subtypes=[], supertypes=[],
            mana_cost=None, printed_keywords=())
    a, b = parse_template(template()), parse_template(template())
    assert a == b and all(x is y for x, y in zip(a.faces[0], b.faces[0]))
    host = a.faces[0][0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        host.text = ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        host.specs[0].verb = None
    assert find_mutable(a) is None and hash(a) == hash(b)


def test_effect_grammar_holds_no_card_names(card_db):
    """Knowledge location (CLAUDE.md): card-specific knowledge lives in
    oracle text and the pool data, never in grammar source. No string
    literal of any engine/effect_grammar module (docstrings and `__all__`
    identifiers aside) is a pool card or face name, or contains a
    multi-word one."""
    import ast
    names = set()
    for n in card_db._raw_data:
        names.add(n)
        names.update(n.split(" // "))
    multi = sorted((n for n in names if " " in n), key=len)
    root = Path(__file__).resolve().parent.parent / "engine" / "effect_grammar"
    files = sorted(root.rglob("*.py"))
    assert len(files) >= 18, files
    hits = []
    for p in files:
        tree = ast.parse(p.read_text())
        skip = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "__all__"
                    for t in n.targets):
                skip.update(id(c) for c in ast.walk(n.value))
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Constant) and isinstance(n.value, str)) \
                    or id(n) in skip:
                continue
            v = n.value
            if v in names or any(m in v for m in multi if len(m) <= len(v)):
                hits.append((p.name, n.lineno, v[:60]))
    assert not hits, hits[:10]
