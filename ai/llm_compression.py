"""Per-task input compression for LLM prompts.

Replaces raw, verbose inputs (oracle text dumps, full replay logs,
surrounding handler context) with token-efficient feature vectors or
extracted key events. Token reduction targets:

- compress_decklist:  ~12k → ~3k tokens (4× reduction)
- compress_replay:    ~10k → ~800 tokens (12× reduction)
- compress_handler:   surrounding-file → handler-only (50× reduction)

Each compressor has the same contract:
- Input: the raw object the LLM tool would otherwise consume
- Output: a string that fits in the per-task token budget (see
  ai/llm_budgets.py — forthcoming).

Design constraints
------------------
* No card-name conditionals — every signal is derived from oracle
  text or `ai.card_features.CardFeatures` flags.
* No scoring math; this module formats text only.  All numeric
  literals here are infra knobs (line caps, excerpt char limits) —
  they are *display* parameters, not rule constants.  The module is
  excluded from `tools/check_magic_numbers.py` for that reason.
* Deterministic — the same input always produces the same output.
  No timestamps, no random ordering.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ai.card_features import CardFeatures, extract_features_for_deck

# ─── Module-level constants (display knobs, not scoring magic) ───────

# Mechanic-flag attributes surfaced as compact `flags=...` tokens in the
# decklist compression.  Mirrors the boolean fields on `CardFeatures` —
# kept as a tuple so the order is stable across runs (Python sets are
# insertion-ordered but tuples make the intent explicit).
_FEATURE_FLAG_ATTRS: tuple[str, ...] = (
    "is_ramp",
    "is_removal",
    "is_card_draw",
    "is_counterspell",
    "is_discard",
    "is_tutor",
    "is_recursion",
    "is_reanimator",
    "is_sweeper",
    "is_combo_payoff",
    "is_combo_enabler",
    "is_modal",
    "is_instant_speed",
)

# Maximum char count for the per-card oracle excerpt that anchors the
# feature vector.  The CardFeatures.first_two_oracle_lines field is
# already capped at two lines; this caps the *character* width of that
# excerpt so a single Saga / Adventure card can't blow the line budget.
# Tuned to land the headline 4× token reduction:
#   * the feature-flag list already encodes "this is removal", "this is
#     a counterspell", etc., so the excerpt is a sanity-check anchor,
#     not the primary signal.
#   * 60 chars is roughly one short sentence — enough for the LLM to
#     spot a misclassified flag without re-reading the full oracle.
_ORACLE_EXCERPT_CHAR_CAP: int = 60

# Default ceiling on key events kept by `compress_replay`.  Bo3 logs
# regularly run 1000+ lines; ~200 key events is enough for an LLM to
# reconstruct the sequence of decisions without re-reading the entire
# log.  Caller can override via the `max_lines` keyword argument.
_DEFAULT_REPLAY_MAX_LINES: int = 200

# Replay-log key-event regexes.  Matched against each line; if any
# pattern hits, the line is kept.  Patterns intentionally mirror the
# emoji + box-drawing conventions documented in CLAUDE.md "Replay
# Viewer — Pipeline" so the compressor stays aligned with whatever
# `build_replay.py` parses.
_REPLAY_KEEP_PATTERNS: tuple[str, ...] = (
    r"╔══ TURN ",                         # turn boundaries
    r"BREAKDOWN:",                         # per-attacker damage
    r"LETHAL",                              # lethal callouts
    r"☠",                                   # lethal emoji marker
    r"🛡 BLOCK:",                           # normal blocks
    r"🚨 BLOCK-EMRG:",                      # emergency blocks
    r"\[BLOCK-EMERGENCY\]",                # legacy block marker (no emoji)
    r"mulligan(?:s)? to \d",               # mulligan decisions
    r"cast .* paid \d",                    # casts (with mana paid breakdown)
    r"^=========+$",                       # game/match separator bars
    r"GAME \d (?:WIN|LOSS|RESULT)",        # game-result tokens
    r"MATCH RESULT",                        # match-result line
    r"wins game",                          # win event
    r"loses(?:\s|:)",                      # loss event ("P1 loses: life ...")
    r"wins Game \d",                       # "Affinity wins Game 1 ..."
    r"\bWINS\b",                            # explicit WINS token
    r"\bLOSES\b",                           # explicit LOSES token
)

# Pre-compile once for speed and to surface bad patterns at import time.
_REPLAY_KEEP_RE: re.Pattern[str] = re.compile("|".join(_REPLAY_KEEP_PATTERNS))


# ─── Decklist compression for synth_gameplan ─────────────────────────


def compress_decklist(
    deck_name: str,
    mainboard: dict[str, int],
    db: Any,
    *,
    include_oracle_excerpt: bool = True,
) -> str:
    """Render decklist as feature-vector + first-2-oracle-lines per card.

    Replaces the existing
    `tools._synth_gameplan_input.format_decklist_for_prompt`'s
    full-oracle-dump approach.  Uses
    `ai.card_features.extract_features_for_deck` (PR #272) for the
    deterministic feature surface.

    Output format per card::

        - 4× Lightning Bolt {1}|removal,instant_speed | Instant
          "Lightning Bolt deals 3 damage to any target."

    The feature flags replace the LLM's need to re-derive
    removal/ramp/cantrip classification from oracle text.  Only the
    first two oracle lines are included as a sanity-check anchor (most
    mechanics live in the first two lines).
    """
    features = extract_features_for_deck(mainboard, db)

    lines: list[str] = [f"Deck name: {deck_name}", ""]
    lines.append(f"Mainboard ({sum(mainboard.values())} cards):")

    for name in sorted(mainboard.keys()):
        qty = mainboard[name]
        f = features.get(name)
        if f is None:
            # Defensive fallback — extract_features_for_deck always
            # returns an entry per name (it stubs DB misses), but keep
            # the safety net so a future refactor that drops the stub
            # still produces a parseable line.
            lines.append(f"- {qty}× {name}  (NO FEATURES — DB miss)")
            continue

        # Compact one-line feature signature.  Flags omitted entirely
        # when none fire — vanilla creatures get just `{cmc}` after the
        # name to keep the line short.
        flags: list[str] = []
        for attr in _FEATURE_FLAG_ATTRS:
            if getattr(f, attr, False):
                # Strip the "is_" prefix so the flag list reads as a
                # bag of mechanic tags rather than predicate names.
                flags.append(attr.replace("is_", ""))

        type_line = "/".join(f.types) if f.types else "?"
        cost = f"{{{f.cmc}}}"
        if flags:
            cost = f"{cost}|{','.join(flags)}"
        lines.append(f"- {qty}× {name} {cost} | {type_line}")

        if include_oracle_excerpt and f.first_two_oracle_lines:
            excerpt = f.first_two_oracle_lines.replace("\n", " ")
            if len(excerpt) > _ORACLE_EXCERPT_CHAR_CAP:
                excerpt = excerpt[:_ORACLE_EXCERPT_CHAR_CAP]
            lines.append(f'    "{excerpt}"')

    lines.append("")
    lines.append(
        "Emit a SynthesizedGameplan JSON object describing this deck's "
        "goals, role assignments, and mulligan keys."
    )
    return "\n".join(lines)


# ─── Replay log compression for diagnose_replay ──────────────────────


def compress_replay(
    log_path: Path,
    *,
    max_lines: int = _DEFAULT_REPLAY_MAX_LINES,
) -> str:
    """Strip a 1000+-line Bo3 log down to ~max_lines key-event lines.

    Keeps:
      * TURN N headers (boundaries)
      * BREAKDOWN: lines (per-attacker damage)
      * LETHAL callouts
      * BLOCK / BLOCK-EMRG decisions
      * mulligan to <N>
      * cast (with mana paid 0/X)
      * final game-result line(s)

    Drops:
      * per-card oracle-text repetition
      * phase markers between turns
      * whitespace-only lines
      * mana-pool / hand-content dumps
    """
    lines_kept: list[str] = []
    truncated = False
    for line in log_path.read_text().splitlines():
        if _REPLAY_KEEP_RE.search(line):
            lines_kept.append(line)
        if len(lines_kept) >= max_lines:
            truncated = True
            break
    if truncated:
        lines_kept.append(f"... [truncated at {max_lines} key events]")
    return "\n".join(lines_kept)


# ─── Handler + oracle compression for handler_audit ──────────────────


def compress_handler(
    handler_src: str,
    oracle_text: str,
    card_name: str,
    *,
    handler_context_lines: int = 0,
) -> str:
    """Format a handler-vs-oracle pair for the `handler_audit` LLM tool.

    Provides ONLY the handler function body and the oracle text —
    strips surrounding file (imports, other handlers, decorator
    boilerplate).  `handler_context_lines` is reserved for a future
    "include N lines around the handler" mode; it is currently unused
    but accepted so callers can opt-in without a signature change.
    """
    # Reference the keyword to keep static analysers quiet — the param
    # is part of the documented surface, just not yet wired up.
    del handler_context_lines

    lines = [
        f"# Card: {card_name}",
        "",
        "## Oracle text (from MTGJSON)",
        oracle_text.strip(),
        "",
        "## Engine handler",
        "```python",
        handler_src.strip(),
        "```",
        "",
        "Compare the printed modes vs the implemented modes. "
        "Emit a HandlerGapReport.",
    ]
    return "\n".join(lines)


__all__ = [
    "compress_decklist",
    "compress_replay",
    "compress_handler",
]
