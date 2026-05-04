"""Tests for ``tools.audit_doc_freshness``.

CI never makes a real API call.  We use ``pydantic_ai.models.test.TestModel``
as the deterministic mock model and monkeypatch
``tools.audit_doc_freshness.DOCS_ROOT`` onto a tmp directory so the
real ``docs/`` tree is never inspected (and never written to).

The ``--apply`` codepath is exercised through
:func:`apply_frontmatter_change` directly, with hand-built
:class:`DocFreshnessReport` instances — never via a real LLM call.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from ai import llm_cache, llm_metrics
from ai.llm_agents import build_agent
from ai.llm_schemas import DocFreshnessReport
from tools import audit_doc_freshness as tool


# ─── Shared fixtures ─────────────────────────────────────────────────


@pytest.fixture
def tmp_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a fresh tmp ``docs/`` and point the tool at it.  Also
    isolates the LLM cache + metrics file so cross-test state never
    leaks into freshness assertions."""
    root = tmp_path / "docs"
    root.mkdir()
    monkeypatch.setattr(tool, "DOCS_ROOT", root)

    cache_dir = tmp_path / "cache_llm"
    cache_db = cache_dir / "responses.sqlite"
    monkeypatch.setattr(llm_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(llm_cache, "CACHE_DB", cache_db)
    monkeypatch.setattr(llm_metrics, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(llm_metrics, "METRICS_FILE", tmp_path / "calls.jsonl")

    return root


def _write_doc(
    root: Path,
    name: str,
    *,
    status: str = "active",
    priority: str = "primary",
    title: str = "Sample doc",
    tags: str = "[audit, methodology]",
    body: str = "Some body text.",
) -> Path:
    p = root / name
    fm = (
        "---\n"
        f"title: {title}\n"
        f"status: {status}\n"
        f"priority: {priority}\n"
        f"tags: {tags}\n"
        "summary: >\n"
        "  Test doc.\n"
        "---\n\n"
    )
    p.write_text(fm + body)
    return p


# ─── parse_frontmatter ───────────────────────────────────────────────


def test_parse_frontmatter_extracts_status(tmp_docs: Path) -> None:
    """A canonical doc returns ``status`` from its frontmatter."""
    p = _write_doc(tmp_docs, "active.md", status="active")
    fm = tool.parse_frontmatter(p)
    assert fm["status"] == "active"
    assert fm["priority"] == "primary"


def test_parse_frontmatter_handles_missing_frontmatter(tmp_docs: Path) -> None:
    """Files without a frontmatter block return an empty dict — never
    raise."""
    p = tmp_docs / "no_fm.md"
    p.write_text("# Just a heading\n\nNo frontmatter here.")
    assert tool.parse_frontmatter(p) == {}


def test_parse_frontmatter_handles_unbalanced_delimiters(tmp_docs: Path) -> None:
    """A doc that opens with ``---`` but never closes returns ``{}``
    rather than crashing — defensive parser contract."""
    p = tmp_docs / "broken_fm.md"
    p.write_text("---\nstatus: active\n# no closing delimiter\n")
    assert tool.parse_frontmatter(p) == {}


# ─── doc_age_days ────────────────────────────────────────────────────


def test_doc_age_days_returns_int_for_tracked_file() -> None:
    """Sanity: any path returns a non-negative int (untracked → 0).

    We explicitly test on a brand-new tmp file (never committed) so
    the result must be 0, never raise."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".md") as f:
        age = tool.doc_age_days(Path(f.name))
        assert isinstance(age, int)
        assert age >= 0


# ─── find_newer_sibling_docs ─────────────────────────────────────────


def test_find_newer_sibling_docs_filters_by_keyword(
    tmp_docs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two docs share a tag 'combo' — the newer one is returned, the
    older candidate is not.  An off-domain doc never appears."""
    older = _write_doc(
        tmp_docs, "older_combo.md",
        title="Combo audit baseline",
        tags="[combo, audit]",
    )
    newer = _write_doc(
        tmp_docs, "newer_combo.md",
        title="Combo audit refresh",
        tags="[combo, audit]",
    )
    off_domain = _write_doc(
        tmp_docs, "tooling.md",
        title="Tooling note",
        tags="[ci, tooling]",
    )

    # Stub git ages so this test is independent of the real git history.
    ages = {older: 30, newer: 5, off_domain: 1}
    monkeypatch.setattr(tool, "doc_age_days", lambda p: ages.get(p, 0))

    siblings = tool.find_newer_sibling_docs(
        older, same_domain_keywords=["combo"]
    )
    assert newer in siblings
    assert off_domain not in siblings
    assert older not in siblings


# ─── build_freshness_input ───────────────────────────────────────────


def test_build_freshness_input_includes_age_and_status(
    tmp_docs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt must surface ``status``, ``priority``, and the days
    since last commit so the LLM can reason about freshness."""
    p = _write_doc(tmp_docs, "active.md", status="active", priority="primary")
    monkeypatch.setattr(tool, "doc_age_days", lambda _p: 42)

    prompt = tool.build_freshness_input(p)

    assert "Frontmatter status: active" in prompt
    assert "Frontmatter priority: primary" in prompt
    assert "Days since last commit: 42" in prompt
    assert "DocFreshnessReport" in prompt


def test_build_freshness_input_includes_sibling_doc_list_when_present(
    tmp_docs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a newer sibling shares a domain keyword, the prompt must
    include a 'Newer sibling docs' section listing it.  Without
    siblings, the section is omitted."""
    older = _write_doc(
        tmp_docs, "older_combo.md",
        title="Combo audit baseline",
        tags="[combo, audit]",
    )
    newer = _write_doc(
        tmp_docs, "newer_combo.md",
        title="Combo audit refresh",
        tags="[combo, audit]",
    )
    ages = {older: 30, newer: 5}
    monkeypatch.setattr(tool, "doc_age_days", lambda p: ages.get(p, 0))

    prompt = tool.build_freshness_input(older)
    assert "## Newer sibling docs" in prompt
    assert str(newer) in prompt


def test_build_freshness_input_truncates_long_bodies(
    tmp_docs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bodies longer than the cap are truncated with a marker so the
    LLM knows it isn't seeing the full text."""
    big_body = "filler line\n" * 500  # ~6000 chars, well over the cap
    p = _write_doc(tmp_docs, "big.md", body=big_body)
    monkeypatch.setattr(tool, "doc_age_days", lambda _p: 1)

    prompt = tool.build_freshness_input(p)
    assert "... [truncated]" in prompt


# ─── assess_doc with TestModel ──────────────────────────────────────


def _mock_freshness_payload(
    doc_path: str,
    *,
    should_change_to: str | None = None,
    replacement: str | None = None,
) -> dict:
    """Build a deterministic ``DocFreshnessReport`` payload for
    TestModel to echo back."""
    payload: dict[str, Any] = {
        "doc_path": doc_path,
        "current_status": "active",
        "should_change_to": should_change_to,
        "replacement_doc": replacement,
        "reason": "test fixture; no real evidence cited",
    }
    return payload


def test_assess_doc_returns_DocFreshnessReport_via_TestModel(
    tmp_docs: Path,
) -> None:
    """End-to-end: build the real agent, override the model with a
    TestModel that emits a fixed DocFreshnessReport payload, run
    ``assess_doc`` on a tmp file, and assert the typed result."""
    p = _write_doc(tmp_docs, "active.md")
    payload = _mock_freshness_payload(
        str(p), should_change_to="superseded", replacement="docs/new.md"
    )

    agent = build_agent("audit_doc_freshness", instrument=False, use_cache=False)
    with agent.override(model=TestModel(custom_output_args=payload)):
        report = tool.assess_doc(p, agent)

    assert isinstance(report, DocFreshnessReport)
    assert report.doc_path == str(p)
    assert report.current_status == "active"
    assert report.should_change_to == "superseded"
    assert report.replacement_doc == "docs/new.md"


# ─── apply_frontmatter_change ────────────────────────────────────────


def test_apply_frontmatter_change_flips_status_in_place(tmp_docs: Path) -> None:
    """The function rewrites the first ``status:`` line to the new
    value and adds a ``superseded_by`` block when a replacement is
    provided.  The body of the doc is untouched."""
    p = _write_doc(tmp_docs, "to_flip.md", status="active")
    report = DocFreshnessReport(
        doc_path=str(p),
        current_status="active",
        should_change_to="superseded",
        replacement_doc="docs/newer.md",
        reason="newer doc supersedes this one",
    )

    tool.apply_frontmatter_change(p, report)

    new_text = p.read_text()
    assert "status: superseded" in new_text
    assert "status: active" not in new_text
    assert "superseded_by:" in new_text
    assert "  - docs/newer.md" in new_text
    # Body intact.
    assert "Some body text." in new_text


def test_apply_frontmatter_change_no_op_when_should_change_to_none(
    tmp_docs: Path,
) -> None:
    """If ``should_change_to`` is None, the file is left unchanged."""
    p = _write_doc(tmp_docs, "keep.md", status="active")
    original = p.read_text()
    report = DocFreshnessReport(
        doc_path=str(p),
        current_status="active",
        should_change_to=None,
        replacement_doc=None,
        reason="still relevant",
    )

    tool.apply_frontmatter_change(p, report)
    assert p.read_text() == original


def test_apply_frontmatter_change_skips_superseded_by_when_already_present(
    tmp_docs: Path,
) -> None:
    """If the frontmatter already mentions ``superseded_by``, we don't
    add a second one — the original convention is preserved."""
    p = tmp_docs / "with_superseded.md"
    p.write_text(
        "---\n"
        "title: Doc\n"
        "status: active\n"
        "superseded_by:\n"
        "  - docs/existing.md\n"
        "---\n\n"
        "Body.\n"
    )
    report = DocFreshnessReport(
        doc_path=str(p),
        current_status="active",
        should_change_to="superseded",
        replacement_doc="docs/different.md",
        reason="x",
    )

    tool.apply_frontmatter_change(p, report)
    text = p.read_text()
    # Status flipped, but no second superseded_by block added.
    assert "status: superseded" in text
    assert text.count("superseded_by:") == 1


# ─── CLI filter helpers ──────────────────────────────────────────────


def test_main_filters_by_since(tmp_docs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``_filter_docs(since_days=14)`` keeps only docs >= 14d old."""
    fresh = _write_doc(tmp_docs, "fresh.md")
    stale = _write_doc(tmp_docs, "stale.md")
    ages = {fresh: 1, stale: 30}
    monkeypatch.setattr(tool, "doc_age_days", lambda p: ages.get(p, 0))

    kept = tool._filter_docs([fresh, stale], since_days=14, domain=None)
    assert kept == [stale]


def test_main_filters_by_domain(tmp_docs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``_filter_docs(domain='combo')`` keeps only docs whose title or
    tags contain the substring."""
    monkeypatch.setattr(tool, "doc_age_days", lambda _p: 1)
    combo = _write_doc(
        tmp_docs, "combo.md", title="Combo audit", tags="[combo]"
    )
    other = _write_doc(
        tmp_docs, "other.md", title="Tooling note", tags="[tooling]"
    )

    kept = tool._filter_docs([combo, other], since_days=None, domain="combo")
    assert kept == [combo]


def test_parse_since_accepts_d_suffix() -> None:
    """``--since 14d`` and ``--since 14`` both decode to ``14``."""
    assert tool._parse_since("14d") == 14
    assert tool._parse_since("14") == 14
    assert tool._parse_since(None) is None


# ─── list_active_docs ────────────────────────────────────────────────


def test_list_active_docs_returns_only_active(
    tmp_docs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only docs with ``status: active`` are returned; ``superseded``,
    ``falsified``, and ``archived`` are excluded."""
    a = _write_doc(tmp_docs, "a_active.md", status="active")
    _ = _write_doc(tmp_docs, "b_super.md", status="superseded")
    _ = _write_doc(tmp_docs, "c_archived.md", status="archived")
    d = _write_doc(tmp_docs, "d_active.md", status="active")

    listed = tool.list_active_docs()
    assert set(listed) == {a, d}
