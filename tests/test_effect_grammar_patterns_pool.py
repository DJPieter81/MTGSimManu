"""L2-L4 of the clause grammar over the whole card pool (design doc
2026-09-29, section 3 "L2", "L3", "L4" and the coverage invariant, section
4 "Pattern table and class-size rule", section 12; E0 steps 10-11).

Every L1 host (modes included) of every distinct pool face -- the faces
and facts of `tests/test_effect_grammar_structure_pool.py` -- runs through
`patterns.match_host` (L2 frames, L3 clauses, L4 patterns). It must:

* never raise, and be deterministic across a cache clear (F10);
* satisfy the coverage invariant on every host: each non-space character
  is in an L1 consumed / refusal span, a frame token or refusal, or a
  clause span (`clauses.uncovered`), and every typed clause consumed every
  printed word (`patterns.unconsumed`);
* emit only specs that satisfy the schema invariants, except the ones the
  linker (L5) settles: a requirement's ``target``/``target_slot`` pair and
  the TARGET / RESULT references it binds (invariants 2 and 3);
* name, on every UNMODELLED spec, its stage and a ``<leaf>.<code>``
  detail, and the printed lemma whenever a lemma was read;
* give every pattern row a witness class of at least ten typed clauses
  (section 4 class-size rule; rows are disjoint by verb, so none is
  shadowed);
* keep the typed share at its measured floor, and its pass inside its
  pinned CPU ceiling with a bounded clause memo.

The census table (typed share by printed lemma, refusals by stage and
detail) is printed for ``pytest -s``.

Measured 2026-10-01 on this branch's DB (23,204 distinct faces, 42,663
hosts and modes, 41.9k clause specs; quiet 4-core box, caches cleared,
card DB frozen out of the collector): 76.8% of clauses typed (76.6% after
the L2-L4 review: "this way" tests on an object and "would" replacements
are refused, recipient-less instead upgrades and where-X followers are
typed). Refusals by
stage: CLAUSE 1.9k (mostly the payload leaf's unknown continuous
predicates and "no row" verbs such as attach), FILTER 1.6k, REFERENCE 1.3k
(library positions: "the top card of your library"), NO_LEMMA 1.2k,
TARGET 1.1k, CONDITION 0.8k, AMOUNT 0.8k, RECOGNIZED_UNSUPPORTED 0.5k.
L2-L4 CPU: 4.5-4.6 s at the stage-3 commit; 5.1 s for that same
commit and 5.3 s after the review fixes, measured back to back on
2026-10-01 (the review's participant-leaf subject checks cost ~0.15 s),
with `patterns.CLAUSE_CACHE_SIZE` (4096 clauses,
7-8 MB after a pool pass; unbounded the memo held 25 MB for no measurable
CPU gain). L2/L3 alone are ~1.3 s; the rest is L4, mostly leaf work.

Budget (design section 12): L0 + L1 take ~2.9 s, so the eager pool pass
of L0-L4 is ~7.5 s against `POOL_PARSE_CPU_BUDGET_S` (4.0 s). The settled
decision keeps `CardTemplate.effects` lazy, so `CardDatabase()` load time
does not carry it; the eager whole-pool budget (and its fallback, the
on-disk cache) is decided with the step-13/22 budget test. This pass is
pinned at its own regression ceiling, `PATTERNS_SHARE_OF_BUDGET` of the
budget, about 1.3x the measurement, so a regressing L2-L4 is named here.
"""
from __future__ import annotations

import gc
import time
from collections import Counter

import pytest

# L2-L4 pass ceiling as a share of POOL_PARSE_CPU_BUDGET_S (see the module
# docstring: measured 5.1-5.3 s on 2026-10-01 after the L2-L4 review, so
# the 6.8 s ceiling is ~1.3x the measurement and a regression is named
# here, not absorbed by headroom).
PATTERNS_SHARE_OF_BUDGET = 1.7
# The clause memo after a pool pass, as a share of POOL_PARSE_MEMO_BUDGET_MB
# (measured 7-8 MB at CLAUSE_CACHE_SIZE).
PATTERNS_MEMO_SHARE_OF_BUDGET = 0.3
# Typed share of every clause spec (measured 76.8%), a floor a few points
# under the measurement: a regression fails, a DB refresh does not.
TYPED_SHARE_FLOOR = 0.73
# Section 4 class-size rule.
MIN_WITNESSES_PER_ROW = 10

# Invariants the linker (L5) settles: target_slot pairing and the TARGET /
# RESULT references it binds.
_L5_RULES = ("target_slot", "ref_order")


@pytest.fixture(scope="module")
def hosts():
    from engine.effect_grammar import structure as S
    from tests._card_db_cache import shared_card_database
    from tests.test_effect_grammar_structure_pool import _faces
    db = shared_card_database()
    out = []
    for name, i, text, facts in _faces(db):
        entry = db._raw_data.get(name) or {}
        has_x = i == 0 and "{X}" in (entry.get("manaCost") or "")
        for h in S.parse_face_structure(text, facts, face=i).hosts:
            out.append((name, h, has_x))
            out.extend((name, m, has_x) for m in h.modes)
    return out


def _run(hosts):
    from engine.effect_grammar import patterns as PT
    return [PT.match_host(h, x) for _n, h, x in hosts]


# Pool-wide. Measured 2026-10-01 on this container (quiet, 4 cores): ~25 s
# for the L1 fixture, the timed pass and the checks, plus ~16 s when it is
# the first test of the process to load the shared card DB. 300 s bounds
# a hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(300)
def test_every_pool_host_frames_splits_and_matches_covered_and_deterministic(hosts):
    import engine.effect_grammar as grammar
    from engine.effect_grammar import clauses as CL
    from engine.effect_grammar import patterns as PT
    from engine.effect_spec import Verb, canonical, validate_spec

    grammar.clear_caches()
    first = _run(hosts)
    grammar.clear_caches()
    second = _run(hosts)
    assert [canonical(tuple(cm.spec for fm in r for cm in fm.clauses))
            for r in first] == \
        [canonical(tuple(cm.spec for fm in r for cm in fm.clauses))
         for r in second]

    typed, total = Counter(), Counter()
    stages, details = Counter(), Counter()
    rows = Counter()
    uncovered, unconsumed, invalid, unnamed = [], [], [], []
    for (name, h, _x), fms in zip(hosts, first):
        u = CL.uncovered(h, tuple(fm.frame for fm in fms))
        if u:
            uncovered.append((name, h.text, u))
        for fm in fms:
            for cm in fm.clauses:
                s = cm.spec
                lemma = cm.lemma or "-"
                total[lemma] += 1
                rule = validate_spec(s)
                if rule is not None and not rule.startswith(_L5_RULES):
                    invalid.append((name, s.raw, rule))
                if s.verb is Verb.UNMODELLED:
                    um = s.payload
                    stages[um.stage.name] += 1
                    details[(um.stage.name, um.detail)] += 1
                    if "." not in um.detail or (cm.lemma and not um.lemma):
                        unnamed.append((name, s.raw, um))
                    continue
                typed[lemma] += 1
                rows[cm.row] += 1
                text = h.text[s.span[0]:s.span[1]]
                if text != s.raw:
                    invalid.append((name, s.raw, "span"))
                local = cm._replace(consumed=tuple(
                    (k, (a - s.span[0], b - s.span[0]))
                    for k, (a, b) in cm.consumed))
                left = PT.unconsumed(local, text)
                if left:
                    unconsumed.append((name, text, left))
    n = sum(total.values())
    share = sum(typed.values()) / n
    print("\nL2-L4 pool census: %d clause specs, %.1f%% typed" % (n, 100 * share))
    print("rows: " + ", ".join("%s %d" % kv for kv in rows.most_common()))
    print("refusals by stage: " + ", ".join(
        "%s %d" % kv for kv in stages.most_common()))
    print("top refusal details:")
    for (st, d), k in details.most_common(25):
        print("  %-24s %-44s %5d" % (st, d, k))
    print("typed share by printed lemma:")
    for lemma, k in total.most_common(30):
        print("  %-16s %5d  %5.1f%%" % (lemma, k, 100 * typed[lemma] / k))

    assert not uncovered, uncovered[:10]
    assert not unconsumed, unconsumed[:10]
    assert not invalid, invalid[:10]
    assert not unnamed, unnamed[:10]
    thin = {r: k for r, k in rows.items() if k < MIN_WITNESSES_PER_ROW}
    from engine.effect_grammar.patterns import ROWS
    missing = {row for row, _fn in ROWS.values()} - set(rows)
    assert not thin and not missing, (thin, missing)
    assert share >= TYPED_SHARE_FLOOR, share


@pytest.mark.timeout(300)   # measured: ~15 s for two timed passes and a memo pass
def test_the_l2_to_l4_pool_pass_fits_its_cpu_ceiling_with_a_bounded_clause_memo(hosts):
    import tracemalloc

    import engine.effect_grammar as grammar
    from engine.effect_grammar import patterns as PT
    from tests.test_effect_grammar_normalize_pool import (
        POOL_PARSE_CPU_BUDGET_S, POOL_PARSE_MEMO_BUDGET_MB)

    gc.collect()
    gc.freeze()
    try:
        best = None
        for _ in range(2):
            grammar.clear_caches()
            t0 = time.process_time()
            for _n, h, x in hosts:
                PT.match_host(h, x)
            took = time.process_time() - t0
            best = took if best is None else min(best, took)
    finally:
        gc.unfreeze()
    print("\nL2-L4 pool CPU: %.2f s over %d hosts (ceiling %.1f s)" % (
        best, len(hosts), POOL_PARSE_CPU_BUDGET_S * PATTERNS_SHARE_OF_BUDGET))
    assert best <= POOL_PARSE_CPU_BUDGET_S * PATTERNS_SHARE_OF_BUDGET, best

    grammar.clear_caches()
    for _n, h, x in hosts:          # warm the leaves' memos first
        PT.match_host(h, x)
    PT.clear_caches()
    gc.collect()
    tracemalloc.start()
    try:
        for _n, h, x in hosts:
            PT.match_host(h, x)
        with_memo = tracemalloc.get_traced_memory()[0]
        PT.clear_caches()
        gc.collect()
        memo_mb = (with_memo - tracemalloc.get_traced_memory()[0]) / 1e6
    finally:
        tracemalloc.stop()
        grammar.clear_caches()
    info = PT.match_clause.cache_info()
    assert info.maxsize == PT.CLAUSE_CACHE_SIZE
    print("clause memo after a pool pass: %.1f MB" % memo_mb)
    assert memo_mb <= POOL_PARSE_MEMO_BUDGET_MB * PATTERNS_MEMO_SHARE_OF_BUDGET, memo_mb
