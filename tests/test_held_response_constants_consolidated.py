"""Deletion-anchor for W2-2 audit consolidation.

The raw constants `HELD_RESPONSE_VALUE_PER_CMC` and
`HELD_RESPONSE_VALUE_PER_CMC_ARTIFACT_RAMP` were collapsed into the
body of `held_response_value_per_cmc(p_artifact_threat)` because every
caller went through the function anyway. This test pins the deletion
and the numerical contract at the two endpoint inputs.
"""
from __future__ import annotations

import math

import pytest

from ai.scoring_constants import held_response_value_per_cmc


def test_per_cmc_constant_deleted():
    with pytest.raises(ImportError):
        from ai.scoring_constants import HELD_RESPONSE_VALUE_PER_CMC  # noqa: F401


def test_per_cmc_artifact_ramp_constant_deleted():
    with pytest.raises(ImportError):
        from ai.scoring_constants import HELD_RESPONSE_VALUE_PER_CMC_ARTIFACT_RAMP  # noqa: F401


def test_function_still_returns_documented_values():
    # Pre-deletion: BASE=4.0 (floor), RAMP=4.0; f(p) = max(4.0, 2.0 + p*4.0)
    assert math.isclose(held_response_value_per_cmc(0.0), 4.0, abs_tol=1e-9)
    assert math.isclose(held_response_value_per_cmc(1.0), 6.0, abs_tol=1e-9)
