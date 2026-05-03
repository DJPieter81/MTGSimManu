"""Real-model eval tests — gated behind `pytest --run-eval`.

By default these tests are skipped (see `tests/conftest.py` —
`pytest_collection_modifyitems` strips them unless the flag is
passed).  Run with:

    MTG_LLM_MODEL=anthropic:claude-sonnet-4-6 \\
        pytest tests/eval/ --run-eval -q

Each task's report's pass-rate must clear a per-task floor.  When a
foundation-model upgrade lands, run this suite to confirm the new
model still scores acceptably on the seed corpus.  When it doesn't,
either bump the model back or extend the prompt/few-shots and bump
`<task>_v<N>.md` to a new version."""
from __future__ import annotations

import pytest

from tests.eval.llm_eval import run_eval


# Per-task floor — the fraction of golden pairs that must pass.
# Conservative numbers; tighten as more goldens accumulate.
_PASS_RATE_FLOOR = {
    "synth_gameplan":      0.5,
    "diagnose_replay":     0.5,
    "handler_audit":       0.5,
}


@pytest.mark.llm_eval_real_model
@pytest.mark.parametrize("task", list(_PASS_RATE_FLOOR.keys()))
def test_real_model_pass_rate_floor(task):
    """Run the eval and assert pass-rate clears the per-task floor."""
    report = run_eval(task)
    assert report.pass_rate >= _PASS_RATE_FLOOR[task], (
        f"{task} eval pass rate {report.pass_rate:.2f} < "
        f"floor {_PASS_RATE_FLOOR[task]:.2f}; failing pairs: "
        f"{[r.pair_id for r in report.pair_results if not r.passed]}"
    )
