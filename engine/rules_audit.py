"""Rules auditor — opt-in, CR-phrased runtime invariants that record a
finding when the engine breaks a rule, everywhere, in every game.

Why this exists: every rules gap found in 2026-09 (X counters never
placed, first-strike creatures dealing no damage, domain surviving Blood
Moon, spells resolving into no legal target, hexproof ignored by ETB
handlers) had been silently wrong across whole matrix runs until a
human-read replay happened to land on it. The engine had no way of
noticing. This module gives every rule a sentence and a place to say
"that just did not hold", so a census over a matrix run — not the next
replay — ranks what the engine gets wrong.

Contract:
- Never behaviour-changing. With ``MTG_RULES_AUDIT`` unset every call is a
  cheap early return and the game is byte-identical to a run without it
  (the WR anchor runs with it unset and must not move).
- ``check(rule_id, ok, detail)`` records a finding when ``ok`` is False;
  ``census(rule_id, key)`` records a fact (not a violation) once per key.
- Findings carry the seed / decks the runner set with ``set_context`` and
  the game's display turn; the runner drains them per game into
  ``GameResult.audit_findings`` and writes one JSONL per run.
- Rule ids are CR section numbers with a short slug (``"510.2/blocker_dealt"``)
  so ``tools/rules_audit_report.py`` can rank by rule.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

_ENV_FLAG = "MTG_RULES_AUDIT"

_findings: List[Dict[str, Any]] = []
_census_seen: set = set()
_context: Dict[str, Any] = {}


def enabled() -> bool:
    """True when the auditor records. Read per call so a test's
    monkeypatched environment takes effect without an import dance."""
    return bool(os.environ.get(_ENV_FLAG))


def set_context(seed: Optional[int] = None, deck1: str = "", deck2: str = "",
                **extra: Any) -> None:
    """Runner-side: stamp every finding of the next game with its seed and
    deck pair (plus anything else the runner wants recorded)."""
    _context.clear()
    _context.update({"seed": seed, "deck1": deck1, "deck2": deck2})
    _context.update(extra)


def _turn(game) -> Optional[int]:
    try:
        return int(getattr(game, "display_turn", None) or getattr(game, "turn_number", 0))
    except Exception:
        return None


def check(rule_id: str, ok: bool, detail: str = "", game=None) -> bool:
    """Record a finding when ``ok`` is False. Returns ``ok`` unchanged so a
    call can sit inline in an expression. A no-op unless enabled."""
    if ok or not enabled():
        return ok
    _findings.append({
        "kind": "violation",
        "rule": rule_id,
        "turn": _turn(game),
        "detail": detail,
        **_context,
    })
    return ok


def census(rule_id: str, key: str, detail: str = "", game=None) -> None:
    """Record a fact once per (rule, key) — e.g. a keyword word the engine
    has no model for. Not a violation; ranked separately by the report."""
    if not enabled():
        return
    if (rule_id, key) in _census_seen:
        return
    _census_seen.add((rule_id, key))
    _findings.append({
        "kind": "census",
        "rule": rule_id,
        "key": key,
        "turn": _turn(game),
        "detail": detail,
        **_context,
    })


def drain() -> List[Dict[str, Any]]:
    """Return the findings recorded so far and clear them (the runner
    calls this once per game)."""
    out = list(_findings)
    _findings.clear()
    return out


def reset() -> None:
    """Clear findings, census memory and context (tests; run start)."""
    _findings.clear()
    _census_seen.clear()
    _context.clear()


def write_jsonl(findings: List[Dict[str, Any]], path: str) -> int:
    """Append findings to a JSONL file; returns the number written."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        for row in findings:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    return len(findings)
