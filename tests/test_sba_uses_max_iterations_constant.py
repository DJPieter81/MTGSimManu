"""S-2 rider — SBA iteration bound hardcoded (20) instead of consuming
SBA_MAX_ITERATIONS from engine.constants.

Survey: docs/diagnostics/2026-04-20_latent_bug_survey.md §S-2

Symptom: `engine/sba_manager.py:42` hardcodes `max_iterations = 20`
instead of reading `SBA_MAX_ITERATIONS` (already declared at
`engine/constants.py:19`).  Low blast radius today, but a maintenance
trap: bumping the constant will not propagate to the actual safety
valve.

These three tests all fail against the current hardcoded source:
  (1) source-level import check — the module must reference the
      constant;
  (2) source-level literal check — the function body must not hardcode
      `max_iterations = 20`;
  (3) behaviour check — monkeypatching the module-level constant must
      change the observed iteration cap.
"""
from __future__ import annotations

import inspect

import pytest

from engine import sba_manager
from engine.constants import SBA_MAX_ITERATIONS


class TestSBAUsesMaxIterationsConstant:
    """The SBA safety valve must honour engine.constants.SBA_MAX_ITERATIONS."""

    def test_module_imports_sba_max_iterations_constant(self):
        """engine.sba_manager must import SBA_MAX_ITERATIONS so the
        declared constant actually reaches the loop."""
        src = inspect.getsource(sba_manager)
        assert "SBA_MAX_ITERATIONS" in src, (
            "engine/sba_manager.py does not reference SBA_MAX_ITERATIONS.\n"
            "The loop at check_and_perform_loop() hardcodes "
            "max_iterations = 20, which means bumping the declared "
            "constant in engine/constants.py has no effect.\n"
            "Fix: add `from .constants import SBA_MAX_ITERATIONS` at the "
            "top of engine/sba_manager.py and bind max_iterations to it."
        )

    def test_loop_does_not_hardcode_literal_20(self):
        """The function body must not contain the literal
        `max_iterations = 20` — use the named constant instead."""
        src = inspect.getsource(sba_manager.SBAManager.check_and_perform_loop)
        assert "max_iterations = 20" not in src, (
            "SBAManager.check_and_perform_loop hardcodes "
            "`max_iterations = 20`.\n"
            "Replace with `max_iterations = SBA_MAX_ITERATIONS` so the "
            "safety valve is driven by engine/constants.py."
        )
        assert "SBA_MAX_ITERATIONS" in src, (
            "SBAManager.check_and_perform_loop must reference "
            "SBA_MAX_ITERATIONS (imported from engine.constants)."
        )

    def test_constant_change_propagates_to_loop_bound(self, monkeypatch):
        """Monkeypatching the module-level SBA_MAX_ITERATIONS must change
        the observed iteration cap. With the hardcoded 20, this test
        fails regardless of the monkeypatched value."""
        # Install a shrunken cap on the sba_manager module namespace.
        # Post-fix, the loop reads `SBA_MAX_ITERATIONS` (imported name)
        # via the module globals, so this patch must take effect.
        monkeypatch.setattr(sba_manager, "SBA_MAX_ITERATIONS", 3,
                            raising=False)

        call_count = {"n": 0}

        class _FakeZoneManager:
            pass

        mgr = sba_manager.SBAManager(_FakeZoneManager())

        # Force the inner check to always report "did work", so the loop
        # keeps going until the cap is reached. Each iteration increments
        # call_count; after the loop, call_count must equal the cap.
        def _always_performed(_game):
            call_count["n"] += 1
            return True

        monkeypatch.setattr(mgr, "_check_and_perform_once",
                            _always_performed)

        mgr.check_and_perform_loop(game=None)

        assert call_count["n"] == 3, (
            f"Loop ran {call_count['n']} iterations but the monkeypatched "
            f"SBA_MAX_ITERATIONS was 3.\n"
            f"The safety valve is not honouring the configurable "
            f"constant — it is reading the hardcoded literal 20 from "
            f"engine/sba_manager.py line 42.\n"
            f"Fix: `from .constants import SBA_MAX_ITERATIONS` and set "
            f"`max_iterations = SBA_MAX_ITERATIONS` in "
            f"check_and_perform_loop()."
        )
