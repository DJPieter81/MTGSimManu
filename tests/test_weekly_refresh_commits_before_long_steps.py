"""Cheap pipeline output must be committed before expensive steps run.

Rule under test: in a scheduled data pipeline, a step that produces
durable data cheaply must persist that data before any long-running
step executes, and every long-running step must be bounded so it
cannot consume the job's whole time budget.

Incident this pins (2026-11-07): `.github/workflows/weekly.yml` had a
single terminal "Commit and push results" step placed AFTER a full
Bo3 matrix refresh. The matrix grew past GitHub's hard 6-hour job
ceiling, so the runner was killed mid-step, the commit step was
skipped as a downstream step of a non-completed one, and the
tier-1 decklist fetch — which succeeds in ~60s and is the data the
deck registry is refreshed from — was discarded every single week.
13 consecutive scheduled runs (2026-08-17 .. 2026-11-02) each burned
6h, reported `cancelled` rather than a failure, and committed nothing.
Three months of decklist staleness with no error surfaced anywhere.

The bug was structural, not a bad value: no threshold tweak fixes it,
because the commit was gated on the slow step finishing at all. These
assertions encode the ordering and bounding rules so the gate cannot
be reintroduced.
"""
from __future__ import annotations

import os

# Hard import, deliberately not pytest.importorskip: a silently skipped
# guard is the same failure class this test exists to prevent, so a
# missing dependency must break loudly. PyYAML is declared in
# requirements.txt and in the CI install step for exactly this test.
import yaml

WORKFLOW = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".github", "workflows", "weekly.yml",
)

# GitHub Actions hard-kills any single job at 6h; a job declaring a
# limit at or above that gets no graceful shutdown of its own.
GITHUB_JOB_CEILING_MINUTES = 360


def _steps():
    with open(WORKFLOW) as f:
        wf = yaml.safe_load(f)
    return wf, wf["jobs"]["weekly"]["steps"]


def _name(step) -> str:
    return step.get("name", step.get("uses", ""))


def _index_of(steps, predicate, what: str) -> int:
    for i, s in enumerate(steps):
        if predicate(_name(s)):
            return i
    raise AssertionError(f"no step found matching {what}; steps: "
                         f"{[_name(s) for s in steps]}")


def test_decklist_fetch_is_committed_before_the_matrix_runs():
    """The fetch output must be persisted by a step that runs BEFORE the
    matrix, so a matrix timeout cannot discard it."""
    _, steps = _steps()
    fetch = _index_of(steps, lambda n: "Fetch tier-1 decklists" in n,
                      "the decklist fetch")
    commit = _index_of(steps,
                       lambda n: "Commit" in n and "decklists" in n,
                       "a commit step for the decklists")
    matrix = _index_of(steps, lambda n: "matrix refresh" in n,
                       "the matrix refresh")

    assert fetch < commit, (
        "the decklist commit must come after the fetch that produces it"
    )
    assert commit < matrix, (
        "the decklist commit must run BEFORE the matrix refresh. With the "
        "commit downstream of the matrix, a matrix overrun kills the job "
        "and the fetched decklists are silently thrown away — the exact "
        "2026-08/11 failure where 13 weeks of fetches were lost."
    )


def test_long_running_steps_are_bounded_below_the_job_ceiling():
    """An unbounded multi-hour step consumes the entire job budget and
    gets hard-killed, skipping everything after it."""
    wf, steps = _steps()
    matrix = steps[_index_of(steps, lambda n: "matrix refresh" in n,
                             "the matrix refresh")]

    bound = matrix.get("timeout-minutes")
    assert bound, (
        "the matrix step must declare timeout-minutes; unbounded, it runs "
        "until GitHub's 6h job ceiling hard-kills the runner instead of "
        "failing as one named step"
    )
    assert bound < GITHUB_JOB_CEILING_MINUTES, (
        f"matrix timeout {bound}m must leave room under the "
        f"{GITHUB_JOB_CEILING_MINUTES}m job ceiling"
    )

    job_bound = wf["jobs"]["weekly"].get("timeout-minutes")
    assert job_bound, "the job must declare its own timeout-minutes"
    assert job_bound < GITHUB_JOB_CEILING_MINUTES, (
        f"job timeout {job_bound}m must be strictly under GitHub's "
        f"{GITHUB_JOB_CEILING_MINUTES}m ceiling so the job ends on its own "
        f"terms and its terminal steps still run"
    )


def test_terminal_persistence_steps_survive_an_upstream_failure():
    """Commit/upload steps after a long step must not be skipped just
    because that step failed or timed out — partial results still have
    value, and the artifact upload is the only record when it doesn't."""
    _, steps = _steps()
    matrix = _index_of(steps, lambda n: "matrix refresh" in n,
                       "the matrix refresh")

    terminal = [s for s in steps[matrix + 1:]
                if "Commit" in _name(s) or "Upload" in _name(s)]
    assert terminal, "expected commit/upload steps after the matrix"

    for step in terminal:
        cond = str(step.get("if", ""))
        assert "always()" in cond, (
            f"step {_name(step)!r} runs after the long matrix step, so it "
            f"needs `if: always()`; without it an upstream timeout skips "
            f"it and the run persists nothing at all (got if={cond!r})"
        )
