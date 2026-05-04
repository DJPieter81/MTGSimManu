"""Regression tests for canonical Bo3 match format default.

Per the 2026-05-04 directive ("many people sideboard against artifacts.
so we should rely on g1 stats, should always be bo3. we should note this
throughout"), `run_meta.run_meta_matrix`, `run_meta.run_matchup`, and
`run_meta.run_field` MUST default to Bo3 with sideboarding.

Real-world Modern is Bo3 with sideboarding; Bo1 evaluation systematically
over-rewards decks whose worst matchups are answered by sideboard hate
(canonical case: Affinity — opponents carry 2-3 artifact destroyers in SB,
near-zero in MB). Treating Bo1 as the default produces tournament-irrelevant
WR estimates and motivated several false-positive AI-scoring "fixes" (e.g.
Phase K's PR #288 mainboard hate edits) that were really responding to a
Bo1-framing artifact.

These tests ratchet the default — flipping any of these to Bo1 (`bo3=False`)
must be a deliberate, reviewed change that updates this test alongside.
"""
import inspect


def test_run_matrix_defaults_to_bo3():
    """Per the 2026-05-04 directive, matrix default is Bo3, not Bo1."""
    from run_meta import run_meta_matrix
    sig = inspect.signature(run_meta_matrix)
    assert 'bo3' in sig.parameters, (
        "run_meta_matrix must accept a `bo3` parameter; the canonical "
        "match format is named explicitly so callers self-document."
    )
    assert sig.parameters['bo3'].default is True, (
        "Matrix default must be Bo3 per CLAUDE.md match-format rule. "
        "Real-world Modern is Bo3; Bo1 over-rewards decks whose worst "
        "matchups are answered by sideboard hate."
    )


def test_run_matchup_defaults_to_bo3():
    """Same rule for individual matchups via run_matchup."""
    from run_meta import run_matchup
    sig = inspect.signature(run_matchup)
    assert 'bo3' in sig.parameters, (
        "run_matchup must accept a `bo3` parameter."
    )
    assert sig.parameters['bo3'].default is True, (
        "run_matchup default must be Bo3 per the 2026-05-04 canonical "
        "match-format directive."
    )


def test_run_field_defaults_to_bo3():
    """Same rule for one-deck-vs-field sweeps via run_field."""
    from run_meta import run_field
    sig = inspect.signature(run_field)
    assert 'bo3' in sig.parameters, (
        "run_field must accept a `bo3` parameter."
    )
    assert sig.parameters['bo3'].default is True, (
        "run_field default must be Bo3 per the 2026-05-04 canonical "
        "match-format directive."
    )


def test_run_sigma_defaults_to_bo3():
    """run_sigma (variance estimator) also defaults to Bo3."""
    from run_meta import run_sigma
    sig = inspect.signature(run_sigma)
    assert 'bo3' in sig.parameters, (
        "run_sigma must accept a `bo3` parameter."
    )
    assert sig.parameters['bo3'].default is True, (
        "run_sigma default must be Bo3 per the 2026-05-04 canonical "
        "match-format directive — variance estimates should use the "
        "same format as primary matrix runs."
    )
