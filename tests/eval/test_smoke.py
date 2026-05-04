"""In-tree shim for the eval-harness smoke tests.

Mirrors `tests/test_eval_harness_smoke.py` so the documented
verification command `pytest tests/eval/ -q` finds the smoke
suite.  Real-model eval tests live in this directory too (gated
behind `--run-eval` via the `llm_eval_real_model` marker)."""
from tests.test_eval_harness_smoke import (  # noqa: F401  (re-exports)
    test_default_threshold_table_has_every_task,
    test_diagnose_replay_goldens_load,
    test_handler_audit_goldens_load,
    test_pair_result_dataclass_shape,
    test_run_eval_passes_when_test_model_echoes_expected,
    test_run_eval_threshold_gates_pass_fail,
    test_synth_gameplan_goldens_load,
)
