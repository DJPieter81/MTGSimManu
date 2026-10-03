"""Per-leaf CPU ceilings of the clause grammar over the pool (design doc
2026-09-29, section 12 "Budget"; E0 integration review).

The whole grammar (L0-L5) gets ``POOL_PARSE_CPU_BUDGET_S`` of process CPU
for the pool, and L0 is held to its share in
tests/test_effect_grammar_normalize_pool.py. Each leaf below gets a pinned
ceiling -- a share of the same budget -- over the slots its own pool test
reads, caches cleared first, so a leaf that regresses is named by its own
test instead of surfacing as an unexplained overrun once L4 lands.

The ceilings are regression guards, not an allocation: measured on a quiet
4-core box (2026-10-01, best of two passes, caches cleared) the leaves
alone take ~2.1 s and L0 ~0.7 s, ~2.9 s before any L1-L5 work. The budget was
raised from 3.0 s to 4.0 s (section 12, 2026-10-01) to leave the spine
room; the shares were rescaled by 3/4 so every leaf keeps its absolute
ceiling. Each
ceiling is about twice the measurement, so a slower CI core does not trip
it but a doubling of a leaf does.
"""
from __future__ import annotations

import gc
import time

import pytest

from tests.test_effect_grammar_normalize_pool import POOL_PARSE_CPU_BUDGET_S

# leaf -> ceiling as a share of POOL_PARSE_CPU_BUDGET_S, with the measured
# CPU (s) it guards (2026-10-01, quiet 4-core box, best of two passes).
LEAF_SHARE_OF_BUDGET = {
    "amount": 0.0525,          # 0.10 s, 3,226 slots
    "condition": 0.1425,       # 0.28 s, 3,727 slots
    "filter": 0.0225,          # 0.03 s, 1,178 slots
    "participant": 0.0525,     # 0.10 s, 3,344 slots
    "payload": 0.06,         # 0.12 s, 5,471 slots
    "quantity": 0.03,        # 0.06 s, 1,191 slots
    "target": 0.2025,          # 0.40 s, 8,022 slots
    "duration": 0.1275,        # 0.26 s, 52,612 sentences (duration + delay)
    "keywords": 0.06,        # 0.12 s, 44,114 paragraphs
    "lexicon": 0.2025,         # 0.41 s, find_verb over 52,612 sentences + loyalty
    "dest": 0.15,            # 0.27 s, the destination pool pass
}


def _jobs(card_db):
    """leaf -> (items, call) over the slots each leaf's pool test reads."""
    import tests.test_effect_grammar_amount_pool as tam
    import tests.test_effect_grammar_condition_pool as tcm
    import tests.test_effect_grammar_dest_pool as tdm
    import tests.test_effect_grammar_filter_pool as tfm
    import tests.test_effect_grammar_lexicon_pool as tlm
    import tests.test_effect_grammar_participant_pool as tpm
    import tests.test_effect_grammar_payload_pool as tym
    import tests.test_effect_grammar_quantity_pool as tqm
    import tests.test_effect_grammar_target_pool as ttm
    from engine.effect_grammar import keywords as K
    from engine.effect_grammar import lexicon as L
    from engine.effect_grammar.sub import amount as A
    from engine.effect_grammar.sub import condition as C
    from engine.effect_grammar.sub import duration as DU
    from engine.effect_grammar.sub import filter as F
    from engine.effect_grammar.sub import participant as PA
    from engine.effect_grammar.sub import payload as P
    from engine.effect_grammar.sub import quantity as Q
    from engine.effect_grammar.sub import target as T

    hosts = tlm._l0_hosts(tlm._faces(card_db))
    sentences = [(h, m.span()) for h in hosts
                 for m in tlm._SENT_RE.finditer(h) if m.group(0).strip()]
    amount = {"count": A.parse_amount, "scaler": A.parse_scaler,
              "where_x": A.parse_where_x, "leading": A.parse_leading_for_each}

    def payload(fam, verb, lemma, h, s):
        if fam == "cost_modifier":
            return P.parse_cost_modifier(h, s)
        return P.parse_payload(tym._Entry(verb, lemma), h, s, None)

    def lexicon(h, s):
        L.find_verb(h, s)
        L.parse_loyalty_cost(h)

    return {
        "amount": (list(tam._slots(card_db)),
                   lambda fr, h, s, kw: amount[fr](h, s, lemma="x", **kw)),
        "condition": (list(tcm._slots(card_db)),
                      lambda fr, h, s: C.parse_condition(h, s, lemma="x")),
        "filter": (list(tfm._slots(card_db)),
                   lambda fam, l, z, h, s: F.parse_filter(h, s, lemma=l, zone=z)),
        "participant": (list(tpm._slots(card_db)),
                        lambda pos, h, s: (PA.parse_chooser if pos == "chooser"
                                           else PA.parse_participant)(
                                               h, s, lemma="x")),
        "payload": (list(tym._slots(card_db)), payload),
        "quantity": (list(tqm._slots(card_db)),
                     lambda fr, h, s: Q.parse_quantity(h, s, lemma="x")),
        "target": (list(ttm._slots(ttm._paragraphs(card_db))),
                   lambda h, s: T.parse_target(h, s, lemma="x")),
        "duration": (sentences, lambda h, s: (DU.parse_duration(h, s),
                                              DU.parse_delay(h, s))),
        "keywords": ([(p,) for p in hosts], K.parse_keyword_line),
        "lexicon": (sentences, lexicon),
        "dest": ([(tdm._pool_sentences(card_db),)], tdm._run),
    }


def _cpu(items, call) -> float:
    import engine.effect_grammar as grammar
    best = None
    for _ in range(2):
        grammar.clear_caches()
        t0 = time.process_time()
        for it in items:
            call(*it)
        took = time.process_time() - t0
        best = took if best is None else min(best, took)
    grammar.clear_caches()
    return best


# Pool-wide over every leaf. Measured 2026-10-01 on this container (quiet,
# 4 cores): ~12 s to gather the slots and run two passes, plus ~16 s when
# it is the first test of the process to load the shared card DB.
# 180 s bounds a hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(180)
def test_every_leaf_fits_its_pinned_share_of_the_pool_load_budget(card_db):
    jobs = _jobs(card_db)
    assert set(jobs) == set(LEAF_SHARE_OF_BUDGET)
    gc.collect()
    gc.freeze()     # keep full collections over the DB heap out of the pass
    try:
        took = {leaf: _cpu(*job) for leaf, job in jobs.items()}
    finally:
        gc.unfreeze()
    print("\nleaf pool CPU (s): " + ", ".join(
        "%s %.3f" % kv for kv in sorted(took.items())))
    print("leaves total: %.2f s of %.1f s" % (sum(took.values()),
                                              POOL_PARSE_CPU_BUDGET_S))
    over = {leaf: round(t, 3) for leaf, t in took.items()
            if t > POOL_PARSE_CPU_BUDGET_S * LEAF_SHARE_OF_BUDGET[leaf]}
    assert not over, over
