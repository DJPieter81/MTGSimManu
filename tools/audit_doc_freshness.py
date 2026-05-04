"""LLM-driven sweep over docs/ to surface stale-but-active documents.

For each doc with ``status: active`` in its frontmatter, the tool:

1. Reads the doc body + frontmatter
2. Reads adjacent context: git mtime, sister-doc list (same domain),
   any newer docs that might supersede it
3. Calls the ``audit_doc_freshness`` LLM agent with a
   :class:`DocFreshnessReport` schema
4. Aggregates per-doc reports + emits an actionable summary

Output: prints / writes a markdown report listing recommended
frontmatter flips (``status: superseded`` / ``archived`` /
``active``-keep) with reasons.

Default model: ``anthropic:claude-haiku-4-5`` (per
``ai.llm_models.DEFAULT_MODELS``).

CLI:

    python -m tools.audit_doc_freshness                    # all active docs
    python -m tools.audit_doc_freshness --since 14d        # only docs untouched 14+ days
    python -m tools.audit_doc_freshness --domain affinity  # filter by tag/title substring
    python -m tools.audit_doc_freshness --json             # JSON output instead of markdown
    python -m tools.audit_doc_freshness --apply            # write the frontmatter changes (USE WITH CARE)

Cost: a full sweep of ~17 active docs costs ~$0.01-$0.05 at current
Haiku 4.5 rates.  Subsequent runs hit the SQLite cache (PR #266) and
are effectively free.

This is a tool, not engine/AI scoring code, so the magic-numbers and
abstraction-contract ratchets don't apply.  The LLM is allowed to
emit card-name knowledge — that's the whole point of an LLM-driven
audit.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ai.llm_agents import build_agent
from ai.llm_schemas import DocFreshnessReport


DOCS_ROOT = Path("docs")
"""Root of the docs tree we audit.  Module-level so tests can
monkeypatch it onto a tmp directory."""

# Body-truncation cap on the prompt.  Most docs fit comfortably; the
# few that don't get a "[truncated]" marker so the model knows it
# isn't seeing the whole text.  Picked to balance token cost against
# losing context — a doc whose status hinges on its 5000th character
# is rare enough that a manual review is appropriate.
PROMPT_BODY_CHAR_LIMIT = 4000

# How many sibling-doc lines to include in the prompt.  A larger list
# bloats the prompt without adding signal — five is enough to surface
# the most likely supersedence candidate.
MAX_SIBLINGS_IN_PROMPT = 5

# How many keywords to derive from a doc's tags/title for the
# sister-doc search.  Same rationale as MAX_SIBLINGS_IN_PROMPT.
MAX_KEYWORDS = 5


# ─── Frontmatter parsing ─────────────────────────────────────────────


def parse_frontmatter(doc_path: Path) -> dict:
    """Return the doc's YAML-ish frontmatter as a flat ``dict``.

    Handles only the simple ``key: value`` lines used in this repo's
    docs (no nested mappings or lists are decoded — those values come
    back as the raw post-colon string).  Returns ``{}`` for files with
    no frontmatter or with unbalanced delimiters.
    """
    try:
        text = doc_path.read_text()
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    body = text[4:end]
    out: dict[str, str] = {}
    for line in body.splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            out[k.strip()] = v.strip()
    return out


# ─── Git-driven freshness ────────────────────────────────────────────


def doc_age_days(doc_path: Path) -> int:
    """Days since the last git commit touching this doc.

    Returns ``0`` for untracked files or when ``git`` is unavailable —
    these are functionally "just landed" and shouldn't be flagged for
    staleness.
    """
    try:
        result = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI", "--", str(doc_path)],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if not result:
            return 0
        last = datetime.fromisoformat(result.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - last).days
    except Exception:
        return 0


def find_newer_sibling_docs(
    doc_path: Path, *, same_domain_keywords: list[str]
) -> list[Path]:
    """Return docs that are NEWER than ``doc_path`` AND share at least
    one of ``same_domain_keywords`` with the candidate's title or tags.

    Keyword matching is case-insensitive substring — generous on
    purpose, since the LLM gets to read the surfaced doc paths and
    can decide which (if any) actually supersedes the candidate.
    """
    candidates: list[Path] = []
    target_age = doc_age_days(doc_path)
    for p in DOCS_ROOT.rglob("*.md"):
        if p == doc_path:
            continue
        if doc_age_days(p) >= target_age:
            continue  # not strictly newer
        fm = parse_frontmatter(p)
        haystack = f"{fm.get('title', '')} {fm.get('tags', '')}".lower()
        if any(kw.lower() in haystack for kw in same_domain_keywords):
            candidates.append(p)
    return candidates


# ─── Prompt construction ─────────────────────────────────────────────


def _keywords_from_frontmatter(fm: dict, fallback_title: str) -> list[str]:
    """Derive a small list of search keywords from a doc's
    frontmatter.  Prefers the explicit ``tags`` list; falls back to
    title nouns when no tags are present.
    """
    tags_str = fm.get("tags", "")
    keywords = [t.strip().strip("[]'\"") for t in tags_str.split(",") if t.strip()]
    if not keywords:
        title = fm.get("title", fallback_title)
        keywords = [w for w in title.split() if len(w) > 3]
    return keywords[:MAX_KEYWORDS]


def build_freshness_input(doc_path: Path) -> str:
    """Construct the prompt payload for one doc.

    The prompt block carries: frontmatter status/priority, days since
    last commit, the (optionally truncated) body, and the list of
    newer sibling docs in the same domain.  Each section is a
    Markdown subheading so the model can lock onto the structure.
    """
    fm = parse_frontmatter(doc_path)
    age = doc_age_days(doc_path)
    title = fm.get("title", doc_path.name)
    keywords = _keywords_from_frontmatter(fm, fallback_title=title)
    siblings = find_newer_sibling_docs(doc_path, same_domain_keywords=keywords)

    try:
        body = doc_path.read_text()
    except (OSError, UnicodeDecodeError):
        body = "<unreadable file>"
    if len(body) > PROMPT_BODY_CHAR_LIMIT:
        body = body[:PROMPT_BODY_CHAR_LIMIT] + "\n... [truncated]"

    parts: list[str] = [
        f"# Doc to assess: {doc_path}",
        f"Frontmatter status: {fm.get('status', '<missing>')}",
        f"Frontmatter priority: {fm.get('priority', '<missing>')}",
        f"Days since last commit: {age}",
        "",
    ]
    if siblings:
        parts.append("## Newer sibling docs (same domain)")
        for s in siblings[:MAX_SIBLINGS_IN_PROMPT]:
            sf = parse_frontmatter(s)
            parts.append(
                f"- {s} (last modified {doc_age_days(s)}d ago) — "
                f"{sf.get('title', '')}"
            )
        parts.append("")
    parts.append("## This doc's body")
    parts.append(body)
    parts.append("")
    parts.append(
        "Emit a DocFreshnessReport.  `should_change_to` is None if the doc "
        "is genuinely still active.  Cite specific evidence (newer doc path, "
        "age, frontmatter signal) in `reason`."
    )
    return "\n".join(parts)


# ─── Per-doc assessment ──────────────────────────────────────────────


def assess_doc(doc_path: Path, agent: Any) -> DocFreshnessReport:
    """Run the LLM agent on one doc and return its
    :class:`DocFreshnessReport`."""
    prompt = build_freshness_input(doc_path)
    result = agent.run_sync(prompt)
    return result.output


def list_active_docs() -> list[Path]:
    """Return all docs under :data:`DOCS_ROOT` whose frontmatter
    status is ``active``, in path-sorted order."""
    out: list[Path] = []
    for p in DOCS_ROOT.rglob("*.md"):
        if parse_frontmatter(p).get("status") == "active":
            out.append(p)
    return sorted(out)


# ─── Frontmatter rewrite (--apply) ───────────────────────────────────


def apply_frontmatter_change(
    doc_path: Path, report: DocFreshnessReport
) -> None:
    """Edit the frontmatter to flip ``status`` and (optionally) add a
    ``superseded_by`` pointer.

    Only the FIRST ``status: <word>`` line is rewritten — frontmatter
    blocks are at the top of the file, so this is safe.  When a
    ``replacement_doc`` is provided AND the existing frontmatter
    doesn't already mention ``superseded_by``, a new line is appended
    immediately after the status line so the relationship is
    machine-readable.
    """
    if report.should_change_to is None:
        return  # nothing to do
    text = doc_path.read_text()
    new_text = re.sub(
        r"^status: \w+$",
        f"status: {report.should_change_to}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if (
        report.replacement_doc
        and "superseded_by" not in new_text[:500]
    ):
        new_text = re.sub(
            r"^(status: \w+)$",
            f"\\1\nsuperseded_by:\n  - {report.replacement_doc}",
            new_text,
            count=1,
            flags=re.MULTILINE,
        )
    doc_path.write_text(new_text)


# ─── CLI ─────────────────────────────────────────────────────────────


def _filter_docs(
    docs: Iterable[Path],
    *,
    since_days: Optional[int],
    domain: Optional[str],
) -> list[Path]:
    """Apply the ``--since`` and ``--domain`` CLI filters to a doc
    list.  Pulled out as a free function so the CLI tests can exercise
    it without touching the real ``docs/`` tree."""
    out: list[Path] = []
    for d in docs:
        if since_days is not None and doc_age_days(d) < since_days:
            continue
        if domain is not None:
            fm = parse_frontmatter(d)
            haystack = f"{fm.get('title', '')} {fm.get('tags', '')}".lower()
            if domain.lower() not in haystack:
                continue
        out.append(d)
    return out


def _parse_since(value: Optional[str]) -> Optional[int]:
    """Parse a ``--since`` argument like ``14`` or ``14d`` into days."""
    if value is None:
        return None
    return int(value.rstrip("dD"))


def _print_markdown_summary(reports: list[tuple[Path, DocFreshnessReport]]) -> None:
    flip = [(d, r) for d, r in reports if r.should_change_to]
    keep = [(d, r) for d, r in reports if not r.should_change_to]
    print(f"\n## {len(flip)} docs recommended for frontmatter flip\n")
    for d, r in flip:
        print(f"- **{d}**: {r.current_status} -> {r.should_change_to}")
        if r.replacement_doc:
            print(f"  - replaced_by: {r.replacement_doc}")
        print(f"  - reason: {r.reason}")
        print()
    print(f"\n## {len(keep)} docs recommended to remain active\n")
    for d, r in keep[:10]:
        print(f"- {d}: {r.reason}")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--since",
        default=None,
        help="Only docs untouched for >= N days (e.g. 14d).",
    )
    p.add_argument(
        "--domain",
        default=None,
        help="Substring filter on title/tags.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of the markdown summary.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write frontmatter changes to disk (USE WITH CARE).",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Override the model identifier (default: per ai.llm_models).",
    )
    args = p.parse_args(argv)

    since_days = _parse_since(args.since)
    docs = _filter_docs(
        list_active_docs(), since_days=since_days, domain=args.domain
    )

    print(f"Assessing {len(docs)} active docs...", flush=True)
    agent = build_agent("audit_doc_freshness", model=args.model)

    reports: list[tuple[Path, DocFreshnessReport]] = []
    for d in docs:
        try:
            r = assess_doc(d, agent)
            reports.append((d, r))
        except Exception as e:  # pragma: no cover - defensive logging only
            print(f"FAILED {d}: {e}", flush=True)

    if args.json:
        json.dump(
            [
                {"path": str(d), "report": r.model_dump()}
                for d, r in reports
            ],
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        _print_markdown_summary(reports)

    if args.apply:
        flip = [(d, r) for d, r in reports if r.should_change_to]
        if not flip:
            print("\nNo flips to apply.")
            return 0
        print(f"\nApplying {len(flip)} frontmatter flips...")
        for d, r in flip:
            apply_frontmatter_change(d, r)
        print("Done.  Review with `git diff docs/`.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DOCS_ROOT",
    "parse_frontmatter",
    "doc_age_days",
    "find_newer_sibling_docs",
    "build_freshness_input",
    "assess_doc",
    "list_active_docs",
    "apply_frontmatter_change",
    "main",
]
