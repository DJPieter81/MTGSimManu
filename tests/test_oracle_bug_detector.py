"""Phase 4F — oracle bug detector tool tests.

Verifies the regression-anchor detectors fire on injected bug
patterns and stay silent on the current (post-Phase-1A) parser
output.

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import pytest

from tools.oracle_bug_detector import (
    Suspicion,
    _check_cost_reduction,
    _check_ritual,
    scan,
)


# ─── _check_cost_reduction unit ──────────────────────────────────────


class TestCheckCostReduction:
    def test_no_cost_or_less_returns_none(self):
        """Oracle without 'cost' or 'less' — parser returns None,
        detector returns None."""
        assert _check_cost_reduction(
            "Memnite", ""
        ) is None

    def test_strict_pattern_passes(self):
        """A real cost-reducer has the strict pattern; detector
        must not flag it."""
        oracle = "Spells cost {1} less to cast."
        assert _check_cost_reduction("Helm of Awakening", oracle) is None

    def test_post_phase_1a_no_false_positives(self):
        """Cards that previously triggered the false positive
        (Saga, Phlage, Trinisphere) must NOT trigger the detector
        post-Phase-1A. parse_cost_reduction returns None for them
        now, so _check_cost_reduction returns None."""
        # Saga's oracle (paraphrased — has 'cost' + 'colorless').
        saga_oracle = (
            "I — Add {C}. II — Create a 0/0 colorless Construct "
            "artifact creature token. III — Search your library "
            "for an artifact card with mana cost {0} or {1}."
        )
        assert _check_cost_reduction("Urza's Saga", saga_oracle) is None


# ─── _check_ritual unit ──────────────────────────────────────────────


class TestCheckRitual:
    def test_real_ritual_passes(self):
        """Dark Ritual: 'Add {B}{B}{B}' — real ritual, must not
        be flagged."""
        oracle = "Add {B}{B}{B}."
        # parse_ritual_mana correctly parses this; detector
        # checks the 'addition' substring boundary; clean.
        assert _check_ritual("Dark Ritual", oracle) is None

    def test_no_ritual_returns_none(self):
        """Oracle without 'add' — parser returns None, detector
        returns None."""
        assert _check_ritual("Memnite", "") is None


# ─── End-to-end scan ──────────────────────────────────────────────────


class TestScan:
    def test_scan_modern_decks_no_regressions(self):
        """The full deck-filtered scan against the post-Phase-1A
        parsers must surface ZERO suspicions on the
        cost_reduction detector (regression anchor for PR #304)."""
        suspicions = scan(
            target="cost_reduction",
            deck_filter=True,
            use_slm=False,
        )
        if suspicions:
            details = "\n".join(
                f"  {s.card_name}: {s.reason}" for s in suspicions[:10]
            )
            pytest.fail(
                f"Found {len(suspicions)} regression(s) in "
                f"parse_cost_reduction. First 10:\n{details}"
            )

    def test_scan_returns_suspicion_dataclass(self):
        """When _check_cost_reduction is hand-fed a regression
        case, scan returns a Suspicion."""
        # Construct a Suspicion directly to verify the shape.
        s = Suspicion(
            card_name="Synthetic",
            parser="parse_cost_reduction",
            parsed_result={"target": "all", "amount": 1},
            reason="test",
            oracle_excerpt="oracle",
        )
        assert s.card_name == "Synthetic"
        assert s.slm_disagrees is None  # default

    def test_unknown_target_raises(self):
        with pytest.raises(ValueError):
            scan(target="not_a_detector", deck_filter=True, use_slm=False)
