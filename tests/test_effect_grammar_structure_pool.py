"""L1 paragraph structure over the whole card pool (design doc 2026-09-29,
section 3 "L1, structure", the coverage invariant, A6, A12, A32, A43; E0
step 9).

Every face's printed text in the card DB runs through
`structure.parse_face_structure` with the face facts the load driver will
build (names, card types, the MTGJSON keywords cut to CR 702, M3). It must:

* classify every face without raising, and place every L0 paragraph in a
  host or a mode;
* satisfy the L1 coverage invariant on every host and mode: each
  non-space character is in a body, consumed, pending or refusal span;
* be deterministic across a cache clear;
* agree with the legacy owners it shares a rule with: the loyalty slots
  `oracle_parser.parse_loyalty_abilities` assigns (A12, the one
  `loyalty_slot_for` rule) on every walker face, and the activation
  ordinals of `oracle_parser.parse_activated_abilities` (the ordinal rule).
  Legacy counts a variable-cost loyalty line ("[-X]:", whose bracket
  `parse_activation_cost` does not recognise) as an activated ability;
  A12 makes it a LOYALTY host, so a legacy ordinal L1 does not assign is
  allowed only for such a line, or for a line inside a level gate's
  refusal block (rule 13: a Class level line is no activated ability);
* fit its share of the whole-grammar CPU budget, and keep a face memo
  bounded far below the pool.

Measured 2026-10-01 on this branch's DB (23,204 distinct faces, 41,241
hosts; process CPU, caches cleared, card DB frozen out of the collector,
quiet 4-core box): 2.8-2.9 s for L0 + L1 together (L0 alone 0.75 s),
about 125 us per face. Before the face memo was bounded the same pass
took 3.3-3.6 s and the memo held 73 MB: it kept every FaceStructure with
its L0 output and the cyclic collector re-walked them. Bounded at
`structure.FACE_CACHE_SIZE` it holds 1.8 MB after a pool pass. L0 + L1
are part of the whole grammar, so their ceiling is a share of
`POOL_PARSE_CPU_BUDGET_S` (4.0 s) -- all of it: the subset may not exceed
the whole -- which leaves L2-L5 about 1.1 s on the eager pool path, less
than L4's leaf work (about 2.2 s, design section 12). That is why
`CardTemplate.effects` is lazy (a game parses only the faces it
touches); the eager pool budget itself is the section 12 open question.
Host shares at the same measurement: KEYWORD 26.1%, TRIGGERED 25.1%,
SPELL 13.3%, ACTIVATED 12.2%, STATIC 11.9%, MANA_ABILITY 3.6%,
REPLACEMENT 3.5%, LOYALTY 2.0%, CHAPTER 1.1%, ADDITIONAL_COST 0.6%,
UNKNOWN 0.5% (die tables, level-gate blocks, siege bullets with no choose
header), ALTERNATIVE_COST 0.2%. 0 uncovered hosts; 0 loyalty slot
disagreements; 91 legacy ordinal extras, every one a variable-cost
loyalty line or a line inside a level-gate block.
"""
from __future__ import annotations

import gc
import re
import time
import tracemalloc
from collections import Counter

import pytest

# L0 + L1 are part of the whole grammar, so their pass gets a share of the
# whole-grammar budget (POOL_PARSE_CPU_BUDGET_S), never an absolute
# ceiling above it -- the leaves' shares (test_effect_grammar_leaf_budget)
# read the same budget. See the measurement in the module docstring.
STRUCTURE_SHARE_OF_BUDGET = 1.0
# The L1 face memo after a pool pass, as a share of the grammar's memo
# budget (POOL_PARSE_MEMO_BUDGET_MB): bounded at structure.FACE_CACHE_SIZE
# faces, never the pool.
STRUCTURE_MEMO_SHARE_OF_BUDGET = 0.1
# UNKNOWN hosts are refusals of structure the model has no host for; a
# rise means a paragraph shape fell out of a typed host.
UNKNOWN_SHARE_CEILING = 0.01

_GATE_DETAILS = frozenset({"structure.level_band", "structure.class_level",
                           "structure.station_threshold"})
_MELD_RE = re.compile(r"\bmeld them into\b")


def _faces(db):
    """(name, face index, printed text, Facts) for every distinct face."""
    from engine.effect_grammar import normalize as N
    from engine.effect_grammar.keywords import keywords702
    out, seen = [], set()
    for t in {id(v): v for v in db.cards.values()}.values():
        legendary = any(getattr(s, "value", s) == "legendary"
                        for s in t.supertypes)
        entry = db._raw_data.get(t.name) or {}
        faces = ((0, t.oracle_text or "", t.card_types, t.subtypes,
                  entry.get("keywords") or ()),
                 (1, getattr(t, "back_face_oracle", "") or "",
                  t.back_face_types, t.back_face_subtypes, ()))
        for i, text, types, subs, kws in faces:
            if not text:
                continue
            tc = frozenset(getattr(c, "value", str(c)) for c in types)
            character = "planeswalker" in tc or (legendary and "creature" in tc)
            facts = N.Facts(
                names=N.self_names(t.name, is_legendary=legendary,
                                   is_character=character,
                                   subtypes=tuple(subs) if "creature" in tc else (),
                                   meld=" // " in t.name
                                   and bool(_MELD_RE.search(text))),
                type_class=tc,
                is_spell=bool({"instant", "sorcery"} & tc),
                is_legendary=legendary,
                is_planeswalker="planeswalker" in tc,
                keywords702=keywords702(kws))
            key = (text, facts, i)
            if key not in seen:
                seen.add(key)
                out.append((t.name, i, text, facts))
    return out


@pytest.fixture(scope="module")
def faces():
    from tests._card_db_cache import shared_card_database
    return _faces(shared_card_database())


def _parse_all(faces):
    from engine.effect_grammar import structure as S
    return [S.parse_face_structure(text, facts, face=i)
            for _n, i, text, facts in faces]


@pytest.mark.timeout(300)  # measured: 38 s wall incl. the ~16 s DB load
def test_every_pool_face_classifies_covered_deterministic_and_within_its_cpu_ceiling(faces):
    import engine.effect_grammar as grammar
    from engine.effect_grammar import structure as S
    from engine.effect_spec import HostKind

    from tests.test_effect_grammar_normalize_pool import \
        POOL_PARSE_CPU_BUDGET_S

    grammar.clear_caches()
    # As for L0: the card DB is frozen out of the cyclic collector so a
    # full collection over its heap (the load pays it once) is not
    # charged to the layer, and the timed pass keeps no results (the
    # bounded memo keeps FACE_CACHE_SIZE faces), so the collector does not
    # re-walk 23k parses the test holds for its checks.
    gc.collect()
    gc.freeze()
    try:
        t0 = time.process_time()
        for _n, i, text, facts in faces:
            S.parse_face_structure(text, facts, face=i)
        cpu = time.process_time() - t0
    finally:
        gc.unfreeze()
    grammar.clear_caches()
    first = _parse_all(faces)

    kinds, uncovered, missing_paragraphs = Counter(), [], []
    for (name, _i, _text, _f), fs in zip(faces, first):
        placed = set()
        for h in fs.hosts:
            kinds[h.kind] += 1
            placed.update(h.paragraphs)
            for x in (h, *h.modes):
                placed.update(x.paragraphs)
                if S.uncovered(x):
                    uncovered.append((name, x.kind.name, S.uncovered(x)))
        n = len(fs.normalized.paragraphs)
        if placed != set(range(n)):
            missing_paragraphs.append((name, sorted(set(range(n)) - placed)))
    total = sum(kinds.values())
    print("\nL1 pool: %d faces, %d hosts, %.2f s CPU (%.0f us/face)"
          % (len(faces), total, cpu, 1e6 * cpu / max(len(faces), 1)))
    for k, v in kinds.most_common():
        print("  %-17s %6d  %5.1f%%" % (k.name, v, 100.0 * v / total))

    assert uncovered == [], uncovered[:10]
    assert missing_paragraphs == [], missing_paragraphs[:10]
    # Every host kind L1 can produce shows up in the pool (GRANTED hosts are
    # L5's; MODE hosts live under their modal host).
    assert set(kinds) == set(HostKind) - {HostKind.GRANTED, HostKind.MODE}
    assert kinds[HostKind.UNKNOWN] / total <= UNKNOWN_SHARE_CEILING
    assert cpu <= POOL_PARSE_CPU_BUDGET_S * STRUCTURE_SHARE_OF_BUDGET, cpu

    grammar.clear_caches()
    assert _parse_all(faces) == first


def test_loyalty_hosts_take_the_slots_the_legacy_owner_assigns_on_every_walker_face(faces):
    """A12: the grammar passes the printed superset (X lines included) to
    `loyalty_slot_for`; `parse_loyalty_abilities` passes fixed lines only.
    Every fixed line gets the same slot and cost from both."""
    from engine.effect_grammar import structure as S
    from engine.effect_spec import HostKind
    from engine.oracle_parser import parse_loyalty_abilities
    bad = []
    for name, i, text, facts in faces:
        if not facts.is_planeswalker:
            continue
        fs = S.parse_face_structure(text, facts, face=i)
        legacy = {k: v.cost for k, v in parse_loyalty_abilities(text).items()}
        mine = {h.loyalty_slot: h.loyalty_cost.n for h in fs.hosts
                if h.kind is HostKind.LOYALTY and h.loyalty_slot}
        if legacy != mine:
            bad.append((name, legacy, mine))
    assert bad == [], bad[:10]


def test_activation_ordinals_follow_the_legacy_ordinal_rule_on_every_face(faces):
    from engine.effect_grammar import structure as S
    from engine.effect_spec import AmountKind, HostKind
    from engine.oracle_parser import parse_activated_abilities
    bad, extras = [], 0
    for name, i, text, facts in faces:
        fs = S.parse_face_structure(text, facts, face=i)
        mine = sorted(h.activation_index for h in fs.hosts
                      if h.kind in (HostKind.ACTIVATED, HostKind.MANA_ABILITY)
                      and h.activation_index is not None)
        n = len(parse_activated_abilities(text))
        x_lines = sum(1 for h in fs.hosts if h.kind is HostKind.LOYALTY
                      and h.loyalty_cost is not None
                      and h.loyalty_cost.kind is AmountKind.X)
        # Rule 13: a level gate's block is one refusal, so a Class level
        # line (and any activated line it gates) takes no ordinal here.
        gated = sum(line.count(":") > 0 and "⟨q" not in line
                    for h in fs.hosts if h.kind is HostKind.UNKNOWN
                    and any(u.detail in _GATE_DETAILS for u, _ in h.unmodelled)
                    for line in h.text.split("\n"))
        if len(mine) != len(set(mine)) or not set(mine) <= set(range(n)) \
                or n - len(mine) > x_lines + gated:
            bad.append((name, i, mine, n))
        extras += n - len(mine)
    print("\nlegacy ordinals on X loyalty lines and level-gated lines: %d" % extras)
    assert bad == [], bad[:10]


@pytest.mark.timeout(300)  # measured: about 15 s under tracemalloc
def test_the_face_memo_is_bounded_below_the_pool_and_fits_its_memory_share(faces):
    """Design section 12: the grammar's memos hold at most
    POOL_PARSE_MEMO_BUDGET_MB after a pool pass. The L1 face memo keeps
    whole face parses (hosts and L0 output), so it is bounded far below
    the pool: after a full pass, with every leaf memo cleared and the
    results dropped, what the face memo still holds fits its share."""
    import engine.effect_grammar as grammar
    from engine.effect_grammar import keywords, lexicon, normalize, sub
    from engine.effect_grammar import structure as S
    from tests.test_effect_grammar_normalize_pool import \
        POOL_PARSE_MEMO_BUDGET_MB

    assert S.parse_face_structure.cache_info().maxsize == S.FACE_CACHE_SIZE
    assert S.FACE_CACHE_SIZE < len(faces) // 10
    grammar.clear_caches()
    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        for _n, i, text, facts in faces:
            S.parse_face_structure(text, facts, face=i)
        sub.clear_caches()
        for leaf in (normalize, keywords, lexicon):
            leaf.clear_caches()
        S._cost.cache_clear()
        gc.collect()
        held = tracemalloc.get_traced_memory()[0] - base
        kept = S.parse_face_structure.cache_info().currsize
    finally:
        tracemalloc.stop()
        grammar.clear_caches()
    assert kept == S.FACE_CACHE_SIZE
    print("\nL1 face memo after a pool pass: %.1f MB" % (held / 1e6))
    assert held <= POOL_PARSE_MEMO_BUDGET_MB * 1e6 * \
        STRUCTURE_MEMO_SHARE_OF_BUDGET, held
