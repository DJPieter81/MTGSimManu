"""The clause-grammar census tool (design doc 2026-09-29, section 9).

`tools/effect_census.py` walks every spec of every host (sub-ability hosts
included, granted hosts not) and counts the grammar's refusals by (stage,
lemma, detail), its residue codes by code and polarity, the typed share by
host kind and on registered-deck cards, and the section-9 report rows
(may_scope nestings, sub-ability shapes, keyword-line classifications,
cost_modifiers absorptions). `--update` pins the baseline and generates the
census doc; `--check` fails when a typed share falls or a refusal total
grows, and on an improvement not yet locked in (a stale baseline).

The unit tests build synthetic CardEffects (no card DB); the pool test reads
the shared card DB through the eager parse without pinning anything on a
template.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"


def _tool():
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import effect_census
    return effect_census


# ── synthetic effects ─────────────────────────────────────────────────

def _spec(verb, seq=0, **kw):
    from engine.effect_spec import EffectSpec
    return EffectSpec(verb=verb, seq=seq, raw=kw.pop("raw", verb.value), **kw)


def _refusal(stage, lemma, detail, seq=0):
    from engine.effect_spec import Unmodelled, Verb
    return _spec(Verb.UNMODELLED, seq, payload=Unmodelled(stage, lemma,
                                                          detail))


def _host(kind, specs, *, index=0, text="", **kw):
    from engine.effect_spec import AbilityEffects
    return AbilityEffects(kind=kind, face=0, index=index, specs=tuple(specs),
                          text=text, **kw)


def _card(*hosts):
    from engine.effect_spec import CardEffects
    return CardEffects.of((tuple(hosts),))


def _effects():
    """Two cards: a spell with one typed and one refused clause and two
    residue codes; a permanent with a refused clause inside a reflexive
    sub-ability, a may-scoped search, a keyword line and a cost
    modifier."""
    from engine.effect_model import ModKind, Modification
    from engine.effect_spec import (Condition, ConditionKind, HostKind,
                                    Stage, SubAbility, SubAbilityKind,
                                    TriggerHead, Verb)
    spell = _host(HostKind.SPELL, [
        _spec(Verb.DESTROY, 0, residue=("target.scope:opponent",
                                        "target.union:creature")),
        _refusal(Stage.TARGET, "exile", "target.unparsed", 1)])
    sub = _host(HostKind.TRIGGERED, [
        _refusal(Stage.FILTER, "destroy", "filter.unparsed:with")],
        trigger=TriggerHead(intervening_if=Condition(ConditionKind.STATE,
                                                     "x")))
    trig = _host(HostKind.TRIGGERED, [
        _spec(Verb.CREATE_TRIGGER, 0, payload=SubAbility(
            SubAbilityKind.REFLEXIVE, None, sub)),
        _spec(Verb.SEARCH, 1, optional=True, then=(
            _spec(Verb.MOVE, 2, flags=frozenset({"may_scope"})),
            _spec(Verb.SHUFFLE, 3, flags=frozenset({"may_scope"}))))],
        index=1, cost_modifiers=(Modification(ModKind.COST_DELTA),))
    kw = _host(HostKind.KEYWORD, [], index=2, text="flying")
    fell = _host(HostKind.SPELL, [_spec(Verb.DRAW)], index=3,
                 text="flashback {2}")
    return {"Synthetic Spell": _card(spell),
            "Synthetic Permanent": _card(trig, kw, fell)}


def _census(**kw):
    return _tool().census(_effects(), **kw)


# ── what the census counts ────────────────────────────────────────────

def test_every_refused_clause_is_counted_by_stage_lemma_and_detail_sub_ability_hosts_included():
    c = _census()
    assert c["unmodelled"] == 2
    assert c["unmodelled_by_stage"] == {"FILTER": 1, "TARGET": 1}
    assert sorted(c["unmodelled_rows"]) == [
        ["FILTER", "destroy", "filter.unparsed:with", 1],
        ["TARGET", "exile", "target.unparsed", 1]]


def test_residue_codes_are_counted_by_code_and_by_their_polarity():
    c = _census()
    assert c["residue_by_code"] == {"target.scope:opponent": 1,
                                    "target.union:creature": 1}
    assert c["residue_by_polarity"] == {"NARROWING": 1, "WIDENING": 1}


def test_the_typed_share_is_reported_per_host_kind_and_for_registered_deck_cards():
    c = _census(deck_names={"Synthetic Spell"})
    # SPELL: destroy (typed), refusal, draw (typed); TRIGGERED: create
    # trigger, search, move, shuffle (typed) and the sub-host refusal.
    assert c["by_host_kind"]["SPELL"] == {"specs": 3, "typed": 2,
                                          "share": round(2 / 3, 4)}
    assert c["by_host_kind"]["TRIGGERED"]["specs"] == 5
    assert c["by_host_kind"]["TRIGGERED"]["typed"] == 4
    assert c["typed_share"] == round(6 / 8, 4)
    assert c["deck"]["cards"] == 1
    assert c["deck"]["typed_share"] == 0.5
    assert c["deck"]["unmodelled_by_stage"] == {"TARGET": 1}
    assert c["deck"]["residue_by_code"] == {"target.scope:opponent": 1,
                                            "target.union:creature": 1}


def test_the_census_reports_may_scope_nestings_sub_ability_shapes_keyword_lines_and_cost_modifiers():
    c = _census(keywords_of=lambda name, face: (
        ("flying", "flashback") if name == "Synthetic Permanent" else ()))
    assert c["may_scope"] == [["SEARCH? > MOVE, SHUFFLE", 1]]
    assert c["sub_abilities"] == [["REFLEXIVE", "-", "intervening_if", 1]]
    # A keyword line that fell to SPELL is visible next to the KEYWORD one.
    assert sorted(c["keyword_lines"]) == [["flashback", "SPELL", 1],
                                          ["flying", "KEYWORD", 1]]
    assert c["cost_modifiers"] == [["TRIGGERED", "COST_DELTA", 1]]


def _removal_cards():
    """One deck card: a typed DESTROY (with a residue code), a refused
    clause whose lemma is a zone verb ('exile') and a DRAW (card flow);
    one pool card with a typed SACRIFICE."""
    from engine.effect_spec import HostKind, Stage, Verb
    deck = _card(_host(HostKind.SPELL, [
        _spec(Verb.DESTROY, 0, residue=("target.scope:opponent",)),
        _refusal(Stage.TARGET, "exile", "target.unparsed", 1),
        _spec(Verb.DRAW, 2)]))
    pool = _card(_host(HostKind.SPELL, [_spec(Verb.SACRIFICE)]))
    return {"Deck Card": deck, "Pool Card": pool}


def test_the_census_measures_the_e2_gate_over_registered_deck_removal_family_clauses(monkeypatch):
    # Section 17 criterion 3: E2 starts once 85% of registered-deck
    # removal-family (zone-verb) clauses are typed and executable or
    # tolerable. A refused clause counts by its lemma's verb family.
    from engine import effect_resolver
    t = _tool()
    c = t.census(_removal_cards(), deck_names={"Deck Card"})
    g = c["deck"]["removal_gate"]
    assert (g["clauses"], g["typed"], g["ready"]) == (2, 1, 0), g
    assert g["typed_share"] == 0.5 and g["ready_share"] == 0.0
    assert g["gate"] == t.E2_GATE_SHARE and g["met"] is False
    # a typed clause whose residue the family's legacy apply tolerates is
    # ready even before an executor lands
    monkeypatch.setitem(effect_resolver.LEGACY_RESIDUE_TOLERATED, "removal",
                        frozenset({"target.scope:opponent"}))
    g = t.census(_removal_cards(), deck_names={"Deck Card"})["deck"][
        "removal_gate"]
    assert g["ready"] == 1 and g["ready_share"] == 0.5


def test_the_e2_gate_shares_are_pinned_and_rendered():
    t = _tool()
    base = t.baseline_of(t.census(_removal_cards(), deck_names={"Deck Card"}))
    assert base["pinned"]["deck_removal_typed_share"] == 0.5
    assert base["pinned"]["deck_removal_ready_share"] == 0.0
    base["session"] = "2026-10-02"
    doc = t.render_markdown(base)
    assert "## E2 gate: registered-deck removal-family clauses" in doc
    assert "| 2 | 1 | 50.0% | 0 | 0.0% | 85.0% | no |" in doc


# ── baseline and check ────────────────────────────────────────────────

def test_check_fails_when_a_typed_share_falls_or_a_refusal_total_grows_and_passes_on_improvement():
    from engine.effect_spec import HostKind, Stage, Verb
    t = _tool()
    base = t.baseline_of(_census())
    assert t.compare(base, _census()) == ([], [])
    worse = _effects()
    worse["Another"] = _card(_host(HostKind.SPELL, [
        _refusal(Stage.TARGET, "exile", "target.unparsed"),
        _spec(Verb.DESTROY, 1, residue=("target.colored",))]))
    bad, _good = t.compare(base, t.census(worse))
    assert any("typed share fell" in b for b in bad), bad
    assert any("UNMODELLED stage TARGET" in b for b in bad), bad
    assert any("residue code target.colored" in b for b in bad), bad
    better = _effects()
    better["Another"] = _card(_host(HostKind.SPELL, [_spec(Verb.DRAW)]))
    bad, good = t.compare(base, t.census(better))
    assert not bad and any("rose" in g for g in good)


def test_an_improvement_is_a_stale_baseline_that_check_fails_until_it_is_locked_in():
    # The ceiling may only shrink, and the commit that shrinks it lowers
    # the baseline, so a later regression cannot silently refill it.
    from engine.effect_spec import HostKind, Verb
    t = _tool()
    base = t.baseline_of(_census())
    assert t.check(base, _census()) == []
    better = _effects()
    better["Another"] = _card(_host(HostKind.SPELL, [_spec(Verb.DRAW)]))
    probs = t.check(base, t.census(better))
    assert probs and all("stale" in p for p in probs), probs
    assert t.check(t.baseline_of(t.census(better)), t.census(better)) == []


def test_the_generated_census_doc_carries_frontmatter_and_names_the_design():
    t = _tool()
    base = t.baseline_of(_census(deck_names={"Synthetic Spell"}))
    base["session"] = "2026-10-02"
    doc = t.render_markdown(base)
    head = doc.split("---")[1]
    for key in ("title:", "status: active", "priority:", "session: 2026-10-02",
                "depends_on:", "tags:", "summary:"):
        assert key in head, key
    assert "2026-09-29_clause_and_trigger_grammar.md" in doc
    assert "| TARGET | exile | target.unparsed | 1 |" in doc


def test_the_committed_census_doc_is_generated_from_the_committed_baseline():
    """No pool needed: the doc renders from the baseline alone, so the
    committed pair cannot drift apart."""
    t = _tool()
    base = json.loads(t.BASELINE_PATH.read_text())
    assert t.DOC_PATH.read_text() == t.render_markdown(base)


def test_the_census_doc_passes_doc_hygiene_and_the_design_doc_links_it():
    import subprocess
    r = subprocess.run([sys.executable, str(TOOLS / "check_doc_hygiene.py")],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    design = (REPO / "docs/design/2026-09-29_clause_and_trigger_grammar.md"
              ).read_text().split("---")[1]
    assert "docs/design/effect_grammar_census.md" in design


# Measured 2026-10-02 (4-core box, under a concurrent 4-worker matrix
# run): ~18 s eager pool parse + ~1 s census walk, plus ~18 s when first in
# the process to load the shared card DB. 600 s bounds a hang on a slower
# 2-core CI runner.
@pytest.mark.timeout(600)
def test_the_pool_census_holds_its_committed_baseline(card_db):
    t = _tool()
    base = json.loads(t.BASELINE_PATH.read_text())
    assert t.check(base, t.pool_census(card_db)) == []
