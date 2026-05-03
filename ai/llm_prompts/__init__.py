"""Versioned prompt loader for LLM agents.

Phase H of the abstraction-cleanup pass.  System prompts live in
markdown files (`<task>_v<N>.md`) and few-shot examples in JSON files
(`<task>_v<N>_fewshot.json`) inside this directory.  Versions are
integer-suffixed and append-only: **bump the version, never edit a
published prompt file** — old runs must remain reproducible.

Layout::

    ai/llm_prompts/
    ├── __init__.py                          # this file
    ├── synth_gameplan_v1.md                 # PR #258 prompt body
    ├── synth_gameplan_v1_fewshot.json       # PR #258 examples
    ├── diagnose_replay_v1.md                # G-3 prompt
    ├── audit_doc_freshness_v1.md            # G-2 prompt
    └── handler_audit_v1.md                  # G-4 prompt

Loader API::

    load_prompt("synth_gameplan")        # latest version body
    load_prompt("synth_gameplan", 1)     # explicit version
    load_fewshot("synth_gameplan")       # latest version examples
    latest_version("synth_gameplan")     # 1, 2, ... for the highest .md present

Versioning rule (read this before bumping):
    A published prompt file is immutable.  If you need to change the
    prompt for an already-shipped task, copy
    `<task>_v<N>.md → <task>_v<N+1>.md`, edit the new file, and bump
    `latest_version(task)` automatically by adding the new file.  Old
    runs that pinned `prompt_version=<N>` keep working unchanged."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import List

_PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(task: str, version: int = 1) -> str:
    """Read `ai/llm_prompts/<task>_v<version>.md` and return its body.

    Raises `FileNotFoundError` if the version doesn't exist."""
    path = _PROMPTS_DIR / f"{task}_v{version}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path}.  Available versions for "
            f"`{task}`: {available_versions(task) or 'none'}"
        )
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def load_fewshot(task: str, version: int = 1) -> List[dict]:
    """Read `ai/llm_prompts/<task>_v<version>_fewshot.json` and return
    its parsed list.  Returns an empty list if the file doesn't exist
    (few-shot examples are optional)."""
    path = _PROMPTS_DIR / f"{task}_v{version}_fewshot.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(
            f"Few-shot file must be a JSON list of examples: {path}"
        )
    return data


def available_versions(task: str) -> List[int]:
    """Return the sorted list of available prompt versions for `task`,
    derived from `<task>_v<N>.md` filenames."""
    versions: List[int] = []
    for path in _PROMPTS_DIR.glob(f"{task}_v*.md"):
        stem = path.stem
        # Strip the `<task>_v` prefix, keep the integer suffix.
        suffix = stem[len(task) + 2:]
        try:
            versions.append(int(suffix))
        except ValueError:
            continue
    return sorted(versions)


def latest_version(task: str) -> int:
    """Return the highest version number available for `task`.

    Raises `FileNotFoundError` if no prompt files exist for the task —
    this catches typos in `task` early rather than letting them
    silently fall through to v1."""
    versions = available_versions(task)
    if not versions:
        raise FileNotFoundError(
            f"No prompt files for task `{task}` in {_PROMPTS_DIR}"
        )
    return versions[-1]


__all__ = [
    "load_prompt",
    "load_fewshot",
    "available_versions",
    "latest_version",
]
