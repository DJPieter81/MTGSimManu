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
def test_every_template_parses_without_exception_and_every_spec_satisfies_the_schema_invariants(card_db, pool_effects):
    """L5 step 11: every spec of every host -- sub-ability and granted
    hosts with their creating hosts in view -- passes validate_spec, and
    every CardEffects value is hashable with no mutable object reachable
    (invariant 8)."""
    from engine.effect_spec import (CardEffects, find_mutable,
                                    validate_card_effects)
    effects, _cpu = pool_effects
    assert len(effects) >= 0.95 * len({id(v) for v in card_db.cards.values()})
    bad = []
    for name, ce in effects.items():
        assert isinstance(ce, CardEffects), name
        rule = validate_card_effects(ce)
        if rule is not None:
            bad.append((name, rule))
    assert not bad, bad[:10]
    some = list(effects.values())[::97]
    assert all(find_mutable(ce) is None and hash(ce) is not None for ce in some)


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
