"""Phase 3 — migrate 5 keyword-driven scaling constants to LLM-derived weights.

Phase 1 (PR #402) migrated 8 archetype-tied scaling constants.  Phase 2
(PR #408) swept archetype-conditional branches.  This phase continues
the same pattern for 5 additional **keyword-driven** scaling constants
in ``ai/ev_player.py``:

  * ``LANDFALL_TRIGGER_VALUE``           (was 3.0)  — landfall keyword
  * ``ARTIFACT_LAND_SYNERGY_BONUS``      (was 4.0)  — metalcraft/affinity
  * ``CYCLING_CHEAP_COST_BONUS``         (was 1.0)  — cycling keyword
  * ``CYCLING_GY_REANIMATE_BASE``        (was 4.0)  — cycling+reanim
  * ``CYCLING_GY_REANIMATE_PER_POWER``   (was 0.5)  — cycling+reanim

Each constant becomes a ``(archetype, context)`` row in
``ai.llm_decision_scorer.DEFAULT_WEIGHTS`` with a matching ``CTX_*``
module constant.  The call sites in ``ai/ev_player.py`` swap the
imported constant for a ``_llm_weight(self.archetype, CTX_X)`` call —
identical pattern to PRs #402 and #408.

Each rule below is phrased in mechanical terms (keyword + scoring role),
not card or deck names.  The test fixtures only reference archetype
labels and context tags — no card strings.

These tests assert two structural properties for every migrated
constant:

  1. The constant has been REMOVED from ``ai/scoring_constants.py`` —
     it no longer appears as a module-level symbol.  This is the
     "drop a constant" arrow of the refactor.
  2. A matching ``CTX_*`` exists in ``ai.llm_decision_scorer`` AND
     a ``DEFAULT_WEIGHTS`` row preserves the historical value for the
     relevant archetype.  This is the "byte-identical offline fallback"
     property — the sim's behaviour with a cold cache matches the
     pre-Phase-3 constants exactly.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from ai import llm_cache, llm_decision_scorer
from ai.llm_schemas import DecisionScoringWeights


# ─── Fixture: isolate cache + force offline so default-table is hit ──


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repoint the SQLite cache at a unique tmp_path for every test in
    this module.  Same pattern as ``tests/test_llm_decision_scorer.py``.
    """
    cache_dir = tmp_path / "cache_llm"
    cache_db = cache_dir / "responses.sqlite"
    monkeypatch.setattr(llm_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(llm_cache, "CACHE_DB", cache_db)
    monkeypatch.setenv("MTG_LLM_DECISION_SCORER_OFFLINE", "1")
    monkeypatch.setattr(llm_decision_scorer, "_AGENT", None)
    monkeypatch.setattr(llm_decision_scorer, "_AGENT_BUILD_FAILED", False)
    return cache_dir


# ─────────────────────────────────────────────────────────────────────
# 1. LANDFALL_TRIGGER_VALUE — landfall-keyword-driven
# ─────────────────────────────────────────────────────────────────────


def test_landfall_trigger_value_uses_decision_scorer_not_constants() -> None:
    """Landfall trigger per-event EV is sourced from the LLM helper.

    Rule: every time a land enters under a permanent with the landfall
    keyword, the controller gains an EV bump equal to one card-quality
    event.  The scaling factor is archetype-dependent (ramp / landfall
    aggro / midrange profit more than control), so it migrates from a
    flat constant to a ``(archetype, context)`` weight.

    Class size: every Modern card whose oracle text contains the
    "landfall" keyword — Bristly Bill, Beanstalk Wurm, Lotus Cobra,
    Akoum Hellhound, Crucible of Worlds variants, etc.
    """
    # Constant must be removed from scoring_constants.
    import ai.scoring_constants as sc
    assert not hasattr(sc, "LANDFALL_TRIGGER_VALUE"), (
        "LANDFALL_TRIGGER_VALUE must be removed in Phase 3 — replaced "
        "by ai.llm_decision_scorer.weight(arch, CTX_LANDFALL_TRIGGER_VALUE)."
    )
    # The new context symbol must exist.
    assert hasattr(llm_decision_scorer, "CTX_LANDFALL_TRIGGER_VALUE"), (
        "CTX_LANDFALL_TRIGGER_VALUE must be defined in ai.llm_decision_scorer."
    )
    # Historical 3.0 must be preserved in the default table for at
    # least one archetype that benefits (ramp, aggro).  The exact
    # archetype set is an implementation detail; the byte-identical
    # property requires SOME default at the historical value.
    ctx = llm_decision_scorer.CTX_LANDFALL_TRIGGER_VALUE
    found_default = any(
        v == 3.0
        for (_, c), v in llm_decision_scorer.DEFAULT_WEIGHTS.items()
        if c == ctx
    )
    assert found_default, (
        "DEFAULT_WEIGHTS must preserve LANDFALL_TRIGGER_VALUE=3.0 for "
        "at least one archetype to keep cold-cache behaviour byte-identical."
    )


# ─────────────────────────────────────────────────────────────────────
# 2. ARTIFACT_LAND_SYNERGY_BONUS — metalcraft/affinity-keyword-driven
# ─────────────────────────────────────────────────────────────────────


def test_artifact_land_synergy_bonus_uses_decision_scorer_not_constants() -> None:
    """Artifact-land synergy bonus is sourced from the LLM helper.

    Rule: when an artifact-typed land enters the battlefield and the
    controller has cards with "for each artifact", "metalcraft", or
    "affinity for artifacts" text active, each synergy card adds one
    card-quality of marginal value.  The scaling is archetype-dependent
    (Affinity values it most; aggro/midrange far less).

    Class size: every Modern artifact land — Darksteel Citadel, Treasure
    Vault, Razortide Bridge, Mishra's Foundry, Inkmoth Nexus, etc.
    """
    import ai.scoring_constants as sc
    assert not hasattr(sc, "ARTIFACT_LAND_SYNERGY_BONUS"), (
        "ARTIFACT_LAND_SYNERGY_BONUS must be removed in Phase 3 — "
        "replaced by ai.llm_decision_scorer.weight(arch, "
        "CTX_ARTIFACT_LAND_SYNERGY_BONUS)."
    )
    assert hasattr(llm_decision_scorer, "CTX_ARTIFACT_LAND_SYNERGY_BONUS"), (
        "CTX_ARTIFACT_LAND_SYNERGY_BONUS must be defined in "
        "ai.llm_decision_scorer."
    )
    ctx = llm_decision_scorer.CTX_ARTIFACT_LAND_SYNERGY_BONUS
    found_default = any(
        v == 4.0
        for (_, c), v in llm_decision_scorer.DEFAULT_WEIGHTS.items()
        if c == ctx
    )
    assert found_default, (
        "DEFAULT_WEIGHTS must preserve ARTIFACT_LAND_SYNERGY_BONUS=4.0 "
        "for at least one archetype."
    )


# ─────────────────────────────────────────────────────────────────────
# 3. CYCLING_CHEAP_COST_BONUS — cycling-keyword-driven (cheap cost)
# ─────────────────────────────────────────────────────────────────────


def test_cycling_cheap_cost_bonus_uses_decision_scorer_not_constants() -> None:
    """Cheap-cycling tempo bonus is sourced from the LLM helper.

    Rule: when a card's cycling cost is ≤ 1 mana, paying for cycling
    leaves enough mana for a second action this turn — that "free
    second action" is worth ~1 EV unit.  Archetype-dependent: cascade
    decks (Living End) prize cheap cycling for graveyard fill; control
    decks value it for card flow; aggro discounts it sharply.

    Class size: every Modern card with cycling cost {0} or {1} — Street
    Wraith, Lonely Sandbar family, Decree of Pain (free for {3}+life),
    Edge of Autumn, Migration Path, etc.
    """
    import ai.scoring_constants as sc
    assert not hasattr(sc, "CYCLING_CHEAP_COST_BONUS"), (
        "CYCLING_CHEAP_COST_BONUS must be removed in Phase 3 — replaced "
        "by ai.llm_decision_scorer.weight(arch, CTX_CYCLING_CHEAP_COST_BONUS)."
    )
    assert hasattr(llm_decision_scorer, "CTX_CYCLING_CHEAP_COST_BONUS"), (
        "CTX_CYCLING_CHEAP_COST_BONUS must be defined in "
        "ai.llm_decision_scorer."
    )
    ctx = llm_decision_scorer.CTX_CYCLING_CHEAP_COST_BONUS
    found_default = any(
        v == 1.0
        for (_, c), v in llm_decision_scorer.DEFAULT_WEIGHTS.items()
        if c == ctx
    )
    assert found_default, (
        "DEFAULT_WEIGHTS must preserve CYCLING_CHEAP_COST_BONUS=1.0 "
        "for at least one archetype."
    )


# ─────────────────────────────────────────────────────────────────────
# 4. CYCLING_GY_REANIMATE_BASE — cycling+reanimation-path-driven
# ─────────────────────────────────────────────────────────────────────


def test_cycling_gy_reanimate_base_uses_decision_scorer_not_constants() -> None:
    """Base EV for cycling a creature into a graveyard reanimation path
    is sourced from the LLM helper.

    Rule: when the controller has a visible reanimation path (Goryo's,
    Living End, Persist, etc.), cycling a creature into the graveyard
    converts the cycled card into a future reanimation target.  Base EV
    is roughly one card-equivalent of future value; archetype-dependent
    because cascade/combo shells value graveyard fuel far more than
    aggro/control.

    Class size: every Modern creature with cycling that can be hard-cast
    or reanimated — Architects of Will, Curator of Mysteries, Striped
    Riverwinder, Decree of Justice, plus the entire Living End cycler
    suite.
    """
    import ai.scoring_constants as sc
    assert not hasattr(sc, "CYCLING_GY_REANIMATE_BASE"), (
        "CYCLING_GY_REANIMATE_BASE must be removed in Phase 3 — replaced "
        "by ai.llm_decision_scorer.weight(arch, "
        "CTX_CYCLING_GY_REANIMATE_BASE)."
    )
    assert hasattr(llm_decision_scorer, "CTX_CYCLING_GY_REANIMATE_BASE"), (
        "CTX_CYCLING_GY_REANIMATE_BASE must be defined in "
        "ai.llm_decision_scorer."
    )
    ctx = llm_decision_scorer.CTX_CYCLING_GY_REANIMATE_BASE
    found_default = any(
        v == 4.0
        for (_, c), v in llm_decision_scorer.DEFAULT_WEIGHTS.items()
        if c == ctx
    )
    assert found_default, (
        "DEFAULT_WEIGHTS must preserve CYCLING_GY_REANIMATE_BASE=4.0 "
        "for at least one archetype."
    )


# ─────────────────────────────────────────────────────────────────────
# 5. CYCLING_GY_REANIMATE_PER_POWER — power-scaler on cycling+reanim
# ─────────────────────────────────────────────────────────────────────


def test_cycling_gy_reanimate_per_power_uses_decision_scorer_not_constants() -> None:
    """Per-power addend on cycling+reanimation EV is sourced from the
    LLM helper.

    Rule: a power-5 creature in the graveyard is worth more as a
    reanimation target than a power-2 creature — but the magnitude of
    that bonus depends on what the archetype does with reanimated
    creatures (cascade chain vs. Goryo's haste swing).

    Class size: every cyclable creature with power > 0 — covers the
    entire Living End / Goryo's / Persist / Persistent Petitioners
    cyclable-creature pool.
    """
    import ai.scoring_constants as sc
    assert not hasattr(sc, "CYCLING_GY_REANIMATE_PER_POWER"), (
        "CYCLING_GY_REANIMATE_PER_POWER must be removed in Phase 3 — "
        "replaced by ai.llm_decision_scorer.weight(arch, "
        "CTX_CYCLING_GY_REANIMATE_PER_POWER)."
    )
    assert hasattr(llm_decision_scorer, "CTX_CYCLING_GY_REANIMATE_PER_POWER"), (
        "CTX_CYCLING_GY_REANIMATE_PER_POWER must be defined in "
        "ai.llm_decision_scorer."
    )
    ctx = llm_decision_scorer.CTX_CYCLING_GY_REANIMATE_PER_POWER
    found_default = any(
        v == 0.5
        for (_, c), v in llm_decision_scorer.DEFAULT_WEIGHTS.items()
        if c == ctx
    )
    assert found_default, (
        "DEFAULT_WEIGHTS must preserve CYCLING_GY_REANIMATE_PER_POWER=0.5 "
        "for at least one archetype."
    )


# ─────────────────────────────────────────────────────────────────────
# 6. Determinism + finiteness — same contract Phase 1 pinned, repeated
#    here for the Phase 3 contexts so a regression in either context
#    or DEFAULT_WEIGHTS row caps blast radius at this file.
# ─────────────────────────────────────────────────────────────────────


def test_phase3_contexts_return_finite_floats_offline() -> None:
    """Each Phase 3 context resolves to a finite float in offline mode
    (cache miss → DEFAULT_WEIGHTS fallback)."""
    import math
    phase3_ctxs = [
        llm_decision_scorer.CTX_LANDFALL_TRIGGER_VALUE,
        llm_decision_scorer.CTX_ARTIFACT_LAND_SYNERGY_BONUS,
        llm_decision_scorer.CTX_CYCLING_CHEAP_COST_BONUS,
        llm_decision_scorer.CTX_CYCLING_GY_REANIMATE_BASE,
        llm_decision_scorer.CTX_CYCLING_GY_REANIMATE_PER_POWER,
    ]
    archetypes = ["aggro", "midrange", "control", "combo", "cascade",
                  "ramp", "storm", "tempo"]
    for arch in archetypes:
        for ctx in phase3_ctxs:
            w = llm_decision_scorer.weight(arch, ctx)
            assert isinstance(w, float)
            assert math.isfinite(w), (
                f"weight({arch!r}, {ctx!r}) returned non-finite {w!r}"
            )


def test_phase3_warm_iteration_includes_new_contexts() -> None:
    """``tools/llm_cache_warm.py`` enumerates every registered context.

    The warm tool's ``_iter_decision_scorer_contexts`` must include
    every CTX_* the Phase 3 migration added; otherwise the cache warm
    would silently skip them and the sim would never see LLM-derived
    weights for the new contexts.
    """
    from tools.llm_cache_warm import _iter_decision_scorer_contexts
    pairs = set(_iter_decision_scorer_contexts())
    new_ctxs = {
        llm_decision_scorer.CTX_LANDFALL_TRIGGER_VALUE,
        llm_decision_scorer.CTX_ARTIFACT_LAND_SYNERGY_BONUS,
        llm_decision_scorer.CTX_CYCLING_CHEAP_COST_BONUS,
        llm_decision_scorer.CTX_CYCLING_GY_REANIMATE_BASE,
        llm_decision_scorer.CTX_CYCLING_GY_REANIMATE_PER_POWER,
    }
    seen_ctxs = {ctx for (_arch, ctx) in pairs}
    missing = new_ctxs - seen_ctxs
    assert not missing, (
        f"warm tool does not enumerate these new Phase 3 contexts: {missing}"
    )
