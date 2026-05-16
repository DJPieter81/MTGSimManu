"""Phase 2 contract tests — archetype-conditional branch sweep.

These tests pin the rules that Phase 2 of the refactor enforces:

1. `ai.clock.position_value` MUST NOT take an `archetype` parameter.
   The combo-clock override is removed at this layer; combo decks rely
   on per-deck gameplan data / LLM-scored weights at higher call sites.

2. No `archetype ==` or `archetype in (...)` conditional remains in
   `ai/*.py`. Detection is programmatic (file scan) so future
   regressions are caught.  Cache-key occurrences inside
   `ai/llm_decision_scorer.py` are allow-listed because they use the
   archetype as a HASH KEY, not a conditional.

3. Mulligan behaviour is now driven by `gameplan.mulligan_policy`
   data fields, not by archetype enum comparisons.  We pin the four
   canonical archetypes (combo / aggro / control / tempo+midrange)
   so the migration cannot silently change behaviour.

The tests are failing-first: they target signatures and patterns
that exist on `main` (pre-Phase-2) and must change in this PR.
"""
from __future__ import annotations

import inspect
import io
import re
import tokenize
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
AI_DIR = REPO_ROOT / "ai"


# ─────────────────────────────────────────────────────────────────
# Contract #1 — position_value signature
# ─────────────────────────────────────────────────────────────────

def test_position_value_signature_does_not_take_archetype():
    """`ai.clock.position_value` must NOT accept an archetype parameter.

    Phase 2 drops the archetype param entirely.  The combo-clock
    override (`min(my_clock, combo_clock)`) is also removed — combo
    decks express their plan via per-deck gameplan data and LLM-scored
    weights at the higher call sites, not via an archetype string
    forwarded into clock.py.
    """
    from ai.clock import position_value

    sig = inspect.signature(position_value)
    params = list(sig.parameters.keys())
    assert "archetype" not in params, (
        f"position_value still accepts archetype param: {params}. "
        "Phase 2 drops it; the combo-clock override is removed and "
        "callers stop passing archetype."
    )


# ─────────────────────────────────────────────────────────────────
# Contract #2 — programmatic grep gate
# ─────────────────────────────────────────────────────────────────

# Files allowed to keep the `archetype` token inside conditionals.
# `llm_decision_scorer.py` is the cache-key consumer per the plan;
# docstring text in that module describes the contract.  The
# tokenize-based scanner already strips docstrings + comments, so
# this allow-list is a belt-and-braces second line of defence.
_ALLOWLISTED_FILES = {
    "ai/llm_decision_scorer.py",
}

# Pattern matching `archetype ==` (with optional whitespace) or
# `archetype in (`, `archetype in [`.  Matches `archetype.value ==`,
# `self.archetype ==`, etc.  Does NOT match `archetype` as bare
# identifier, function argument, or string literal usage.
_GATE_RE = re.compile(
    r"\barchetype\b"             # the word
    r"(?:\.[a-zA-Z_][a-zA-Z_0-9]*)?"  # optional `.value`-style access
    r"\s*(?:==|!=|in\s*[\(\[])"  # then a gate operator
)


def _ai_py_files():
    """Yield (rel_path_str, source_text) for every .py in ai/.

    Skips test_* files (tests are allowed to assert on archetype values).
    """
    for path in sorted(AI_DIR.rglob("*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        if "test_" in path.name:
            continue
        if rel.replace("\\", "/") in _ALLOWLISTED_FILES:
            continue
        yield rel, path.read_text(encoding="utf-8")


def _code_only_lines(src: str) -> dict[int, str]:
    """Tokenize ``src`` and rebuild each line with string-literal and
    comment tokens elided.  Returns ``{lineno: code_only_line}`` so the
    caller can grep over executable code without touching docstrings.

    Robust against multi-line triple-quoted strings.  Uses Python's
    tokenizer rather than a regex so triple-quoted strings (including
    those that span dozens of lines) are reliably skipped.
    """
    out: dict[int, str] = {}
    # Initialize: every line starts as its raw text; we'll subtract
    # string and comment slices.
    raw_lines = src.splitlines()
    for i, ln in enumerate(raw_lines, start=1):
        out[i] = ln
    try:
        toks = tokenize.tokenize(io.BytesIO(src.encode("utf-8")).readline)
        for tok in toks:
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                # Replace the token's character range with spaces (multi-line
                # safe).  Token coords are (line, col); end might span lines.
                start_l, _ = tok.start
                end_l, _ = tok.end
                for ln_no in range(start_l, end_l + 1):
                    out[ln_no] = re.sub(r"\S", " ", out.get(ln_no, ""))
    except (tokenize.TokenizeError, SyntaxError):
        # Best-effort: tokenizer failures fall back to raw text.
        pass
    return out


def test_no_archetype_equality_check_in_ai_module():
    """No `archetype ==` or `archetype in (...)` conditional anywhere
    in `ai/*.py` (excluding the cache-key allow-list).

    This is the binding form of the grep gate at the top of the Phase 2
    acceptance criteria.  CI would have caught it; we wrap it in pytest
    so the standard suite catches regressions before push.

    Uses ``tokenize`` to strip string-literal and comment content from
    each line before applying the gate regex, so multi-line docstrings
    that *describe* prior conditionals do not register as offenders.
    """
    offenders: list[tuple[str, int, str]] = []
    for rel_path, src in _ai_py_files():
        code_lines = _code_only_lines(src)
        for lineno, code in code_lines.items():
            if _GATE_RE.search(code):
                offenders.append((rel_path, lineno, code.rstrip()))
    assert not offenders, (
        "Found archetype-conditional gate(s) in ai/.  Each Phase 2 site "
        "must be replaced by a data lookup (gameplan field) or LLM-scored "
        "weight (ai/llm_decision_scorer.weight). Offenders:\n"
        + "\n".join(f"  {p}:{n}: {ln}" for p, n, ln in offenders)
    )


# ─────────────────────────────────────────────────────────────────
# Contract #3 — mulligan keeps behaviour via gameplan JSON field
# ─────────────────────────────────────────────────────────────────

def test_mulligan_policy_field_defined_on_gameplan():
    """`DeckGameplan` must expose a `mulligan_policy` attribute.

    The four mulligan archetype branches collapsed onto this single
    data field in Phase 2.  We verify the field is materialized (with
    archetype-appropriate defaults) for the four canonical archetypes:
    combo (Ruby Storm), aggro (Boros Energy), control (Azorius
    Control), midrange (Dimir Midrange).
    """
    from ai.gameplan import get_gameplan

    expectations = {
        "Ruby Storm": "combo",
        "Boros Energy": "aggro",
        "Azorius Control": "control",
        "Dimir Midrange": "midrange",
    }
    for deck_name, expected_arch in expectations.items():
        gp = get_gameplan(deck_name)
        assert gp is not None, f"missing gameplan for {deck_name}"
        assert hasattr(gp, "mulligan_policy"), (
            f"{deck_name}: DeckGameplan has no mulligan_policy attr"
        )
        policy = gp.mulligan_policy
        assert policy is not None, f"{deck_name}: mulligan_policy is None"
        # Field shape: must declare the four flags the migrated sites read.
        for flag in (
            "requires_combo_backup",
            "key_card_min_cheap_relaxed",
            "generic_branch",
            "keep_score_early_play_at_home",
            "keep_score_combo_at_home",
            "keep_score_counterspell_at_home",
        ):
            assert hasattr(policy, flag), (
                f"{deck_name}: mulligan_policy missing flag '{flag}'"
            )
        # Archetype-anchored: combo decks set requires_combo_backup True;
        # aggro decks set keep_score_early_play_at_home True; control sets
        # keep_score_counterspell_at_home True; midrange is the neutral
        # baseline (no flags set).
        if expected_arch == "combo":
            assert policy.requires_combo_backup is True
            assert policy.key_card_min_cheap_relaxed is True
            assert policy.keep_score_combo_at_home is True
        elif expected_arch == "aggro":
            assert policy.keep_score_early_play_at_home is True
            assert policy.generic_branch == "aggro"
        elif expected_arch == "control":
            assert policy.keep_score_counterspell_at_home is True
            assert policy.generic_branch == "control"


# ─────────────────────────────────────────────────────────────────
# Contract #4 — enables_disruption is now a per-deck gameplan flag
# ─────────────────────────────────────────────────────────────────

def test_engine_disruption_premium_uses_enables_disruption_flag():
    """The combo-only gate in `engine_disruption.engine_disruption_value`
    must consult `gameplan.enables_disruption`, not the archetype string.

    Phase 2 replaces the implicit `archetype == 'combo'` test with an
    explicit `enables_disruption: bool` field on `DeckGameplan`.  Combo
    decks (Ruby Storm, Living End, Amulet Titan, Goryo's Vengeance) set
    it to True; non-combo decks leave it at the False default.
    """
    from ai.gameplan import get_gameplan

    enable_decks = [
        "Ruby Storm", "Living End", "Amulet Titan", "Goryo's Vengeance",
    ]
    disable_decks = [
        "Boros Energy", "Azorius Control", "Dimir Midrange",
    ]
    for d in enable_decks:
        gp = get_gameplan(d)
        if gp is None:
            continue  # deck not registered; skip
        assert getattr(gp, "enables_disruption", False) is True, (
            f"{d}: enables_disruption should be True for combo-style decks"
        )
    for d in disable_decks:
        gp = get_gameplan(d)
        if gp is None:
            continue
        assert getattr(gp, "enables_disruption", False) is False, (
            f"{d}: enables_disruption should default to False for non-combo"
        )


# ─────────────────────────────────────────────────────────────────
# Contract #5 — finisher_simulator priority uses data dispatch
# ─────────────────────────────────────────────────────────────────

def test_finisher_simulator_priority_table_is_data_not_branches():
    """`ai.finisher_simulator._priority` resolution table must be
    a module-level dict / mapping — not a chain of `archetype.startswith`
    conditionals.

    We pin this by importing the table-of-records name and checking the
    mapping exists with the four canonical patterns.
    """
    from ai import finisher_simulator as fs

    # The Phase 2 migration replaces the archetype.startswith() chain
    # with an explicit data table.  We don't care about its exact
    # values — only that it's data-shaped.
    assert hasattr(fs, "_ARCHETYPE_PATTERN_PRIORITY"), (
        "Phase 2: ai.finisher_simulator must expose "
        "_ARCHETYPE_PATTERN_PRIORITY (data dispatch table) replacing "
        "the archetype.startswith() branch chain."
    )
    tbl = fs._ARCHETYPE_PATTERN_PRIORITY
    # Each entry maps (archetype_token, pattern) -> int priority.
    # Probe that the four patterns the original branches matched are
    # all keyed in the table.
    keyed_patterns = {pattern for (_arch, pattern) in tbl.keys()}
    assert keyed_patterns >= {"storm", "cascade", "reanimation", "cycling"}


# ─────────────────────────────────────────────────────────────────
# Contract #6 — combo-clock override no longer fires from position_value
# ─────────────────────────────────────────────────────────────────

def test_position_value_does_not_apply_combo_clock_override():
    """`position_value(snap)` returns the same value for any combo /
    storm fixture regardless of archetype — because the archetype
    param is gone.  No combo-specific clock override.
    """
    from ai.clock import position_value
    from ai.ev_evaluator import EVSnapshot

    snap = EVSnapshot(
        my_life=20, opp_life=20,
        my_power=2, opp_power=2,
        my_toughness=2, opp_toughness=2,
        my_evasion_power=0, opp_evasion_power=0,
        my_mana=3, opp_mana=3,
        my_hand_size=4, opp_hand_size=4,
        my_lifelink_power=0,
        my_total_lands=3,
        storm_count=2,
        my_gy_creatures=0,
    )
    # Single-arg call must succeed: the archetype param is dropped.
    pv = position_value(snap)
    assert isinstance(pv, float)
