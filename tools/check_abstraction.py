#!/usr/bin/env python3
"""Abstraction ratchet — block commits that add hardcoded card-name OR deck-name conditionals.

Two ratchets, sharing this script:

A. Card-name conditionals (regex-based, original detector).

   Pattern detected (in engine/ and ai/ only):
       <expr>.name == "<literal>"            # direct name check
       <expr>.name == '<literal>'
       name in (<literals>) / name in {<literals>}   # set/tuple membership

B. Deck-name gates (AST-based, Class D from the combo-deck audit
   methodology — see docs/design/2026-05-04_modern_combo_audit_methodology.md).

   Pattern detected (in engine/ and ai/ only):
       <expr>.deck_name == "<deck-name>"
       <expr>.active_deck == "<deck-name>"
       <deck-attr-expr>.name == "<deck-name>"            # heuristic: receiver named like a deck
       deck_name in ("<deck>", ...)  / active_deck in {...}
       "<deck-name>" in <expr>.deck_name                  # substring fuzzy
   The string literal must match a known Modern deck name (loaded from
   `ai/strategy_profile.py:DECK_ARCHETYPES`) — this anchors detection to
   real deck-name gates and prevents flagging unrelated `name == "x"`
   string compares.

Lines tagged with `# abstraction-allow: <reason>` are exempted (both ratchets).

Behavior (each ratchet independently):
    count > baseline → exit 1, print new offenders
    count < baseline → exit 1, prompt to lower baseline (forces explicit ratchet)
    count == baseline → exit 0

Why a ratchet, not absolute zero: there is existing legitimate technical debt
(see baseline). The contract is to never *grow* it. Reductions must be
explicit so debt-paydown is visible in git history.

Usage:
    python tools/check_abstraction.py            # check current tree
    python tools/check_abstraction.py --list     # print all current hits (both ratchets)
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = [ROOT / "engine", ROOT / "ai"]
BASELINE_FILE = ROOT / "tools" / "abstraction_baseline.json"
ALLOW_MARKER = "# abstraction-allow"

# Patterns that catch hardcoded card-name conditionals. Kept conservative on
# purpose — false positives are fine because they enter the baseline once and
# never grow. False negatives (missed antipatterns) are the real failure mode.
PATTERNS = [
    # <expr>.name == "literal"  /  <expr>.name == 'literal'
    re.compile(r'\.name\s*==\s*"[^"]+"'),
    re.compile(r"\.name\s*==\s*'[^']+'"),
    # name in (...) / name in {...} — opens a literal tuple/set on this line
    # (matches both same-line-closes and multi-line-opens; the next char being
    # a quote, or end-of-line followed by quoted lines, is what we care about)
    re.compile(r"\bname\s+in\s+[\(\{]\s*['\"]"),
    re.compile(r"\bname\s+in\s+[\(\{]\s*$"),
]


# ─── Card-name detector (regex-based, original) ────────────────────────────


def find_hits() -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for py in d.rglob("*.py"):
            try:
                lines = py.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for i, line in enumerate(lines, 1):
                if ALLOW_MARKER in line:
                    continue
                if any(p.search(line) for p in PATTERNS):
                    hits.append((py.relative_to(ROOT), i, line.rstrip()))
    return hits


# ─── Deck-name gate detector (AST-based, Class D) ───────────────────────────

# Attribute names that, when read on an arbitrary <expr>, are unambiguously
# referring to a deck-name string (so `x.deck_name == "Storm"` is a deck gate
# regardless of what `x` is).
DECK_ATTR_NAMES = {"deck_name", "active_deck"}

# Variable / attribute names whose `.name` access is heuristically a
# deck-name reference (so `deck.name == "Storm"` and `strategy.name == "Storm"`
# are deck gates). Kept tight on purpose — broadening this risks false
# positives on `card.name` etc.
DECK_RECEIVER_NAMES = {"deck", "active_deck", "strategy", "archetype", "gameplan"}


def _load_known_deck_names() -> set[str]:
    """Parse `ai/strategy_profile.py` for the DECK_ARCHETYPES dict keys.

    The dict is the canonical registry of deck names — using it (instead of
    a hardcoded list here) means new decks land detection automatically. We
    parse with AST instead of importing to avoid pulling in the runtime
    dependency graph during a lightweight CI ratchet.
    """
    path = ROOT / "ai" / "strategy_profile.py"
    if not path.exists():
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DECK_ARCHETYPES":
                    if isinstance(node.value, ast.Dict):
                        for key in node.value.keys:
                            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                names.add(key.value)
    return names


def _is_str_const(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _attr_chain_tail(node: ast.AST) -> str | None:
    """Return the trailing identifier of an attribute access (`a.b.c` → 'c'),
    or the bare name (`a` → 'a'), or None if neither shape applies."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _attr_receiver_name(node: ast.AST) -> str | None:
    """For `x.attr` return the textual name of x's tail (`a.b.attr` → 'b').

    Used to decide if a `.name` access is on a deck-receiver."""
    if isinstance(node, ast.Attribute):
        return _attr_chain_tail(node.value)
    return None


class DeckGateVisitor(ast.NodeVisitor):
    """Walks an AST and records hardcoded deck-name gates.

    We treat the file's text via `source_lines` to honor the
    `# abstraction-allow:` marker.
    """

    def __init__(self, source_lines: list[str], known_decks: set[str]):
        self.source_lines = source_lines
        self.known_decks = known_decks
        self.hits: list[tuple[int, str]] = []

    def _is_exempt(self, lineno: int) -> bool:
        idx = lineno - 1
        if 0 <= idx < len(self.source_lines):
            return ALLOW_MARKER in self.source_lines[idx]
        return False

    def _record(self, lineno: int):
        if self._is_exempt(lineno):
            return
        line = ""
        idx = lineno - 1
        if 0 <= idx < len(self.source_lines):
            line = self.source_lines[idx].rstrip()
        self.hits.append((lineno, line))

    def _is_deck_attribute(self, node: ast.AST) -> bool:
        """`<expr>.deck_name` / `<expr>.active_deck` — unconditionally a deck ref."""
        return isinstance(node, ast.Attribute) and node.attr in DECK_ATTR_NAMES

    def _is_deck_name_attribute(self, node: ast.AST) -> bool:
        """`<deck-receiver>.name` — heuristic deck ref, only when the receiver
        is named like a deck (deck/strategy/archetype/...).
        """
        if not isinstance(node, ast.Attribute) or node.attr != "name":
            return False
        receiver = _attr_receiver_name(node)
        return receiver in DECK_RECEIVER_NAMES

    def _is_deck_name_bareid(self, node: ast.AST) -> bool:
        """Bare `deck_name` / `active_deck` identifier."""
        return isinstance(node, ast.Name) and node.id in DECK_ATTR_NAMES

    def _looks_like_deck_ref(self, node: ast.AST) -> bool:
        return (
            self._is_deck_attribute(node)
            or self._is_deck_name_attribute(node)
            or self._is_deck_name_bareid(node)
        )

    def _matches_known_deck(self, value: object) -> bool:
        return isinstance(value, str) and value in self.known_decks

    def visit_Compare(self, node: ast.Compare):
        # Handle a single op (most idiomatic case). Chained compares are
        # very rare on string-equality so we only inspect the first op.
        if not node.ops or not node.comparators:
            self.generic_visit(node)
            return
        op = node.ops[0]
        right = node.comparators[0]
        left = node.left

        # Pattern 1: <expr>.deck_name == "X" / <bare deck_name> == "X" / <deck-receiver>.name == "X"
        if isinstance(op, ast.Eq):
            for target, literal in ((left, right), (right, left)):
                if self._looks_like_deck_ref(target) and _is_str_const(literal):
                    val = literal.value  # type: ignore[attr-defined]
                    # Always flag deck_name/active_deck attr/bare comparisons.
                    # For .name on deck-receivers, only flag when the literal
                    # is a known deck name (avoids false positives on
                    # `archetype.name == "AGGRO"` enum compares).
                    if self._is_deck_attribute(target) or self._is_deck_name_bareid(target):
                        self._record(node.lineno)
                        break
                    if self._is_deck_name_attribute(target) and self._matches_known_deck(val):
                        self._record(node.lineno)
                        break

        # Pattern 2: <deck-ref> in (...) / <deck-ref> in {...}
        if isinstance(op, ast.In):
            if self._looks_like_deck_ref(left) and isinstance(
                right, (ast.Tuple, ast.Set, ast.List)
            ):
                # Flag if any element is a string literal — a tuple of
                # deck-name strings is the antipattern.
                if any(_is_str_const(elt) for elt in right.elts):
                    self._record(node.lineno)

            # Pattern 3: "<deck-name>" in <expr>.deck_name  (substring fuzzy)
            if _is_str_const(left) and self._looks_like_deck_ref(right):
                if self._matches_known_deck(left.value):  # type: ignore[attr-defined]
                    self._record(node.lineno)

        self.generic_visit(node)


def find_deck_gate_hits() -> list[tuple[Path, int, str]]:
    known_decks = _load_known_deck_names()
    hits: list[tuple[Path, int, str]] = []
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for py in d.rglob("*.py"):
            try:
                src = py.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            try:
                tree = ast.parse(src, filename=str(py))
            except SyntaxError:
                continue
            visitor = DeckGateVisitor(src.splitlines(), known_decks)
            visitor.visit(tree)
            for lineno, line in visitor.hits:
                hits.append((py.relative_to(ROOT), lineno, line))
    return hits


# ─── Baseline I/O ──────────────────────────────────────────────────────────


def load_baseline() -> int:
    if not BASELINE_FILE.exists():
        return 0
    return int(json.loads(BASELINE_FILE.read_text())["hardcoded_name_count"])


def load_deck_gate_baseline() -> int:
    if not BASELINE_FILE.exists():
        return 0
    data = json.loads(BASELINE_FILE.read_text())
    return int(data.get("deck_gate_count", 0))


# ─── Main ──────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    hits = find_hits()
    count = len(hits)

    deck_hits = find_deck_gate_hits()
    deck_count = len(deck_hits)

    if "--list" in argv:
        print("=== Card-name hits ===")
        for path, lineno, line in hits:
            print(f"{path}:{lineno}: {line}")
        print(f"  Total card-name: {count}")
        print()
        print("=== Deck-name-gate hits ===")
        for path, lineno, line in deck_hits:
            print(f"{path}:{lineno}: {line}")
        print(f"  Total deck-gate: {deck_count}")
        return 0

    baseline = load_baseline()
    deck_baseline = load_deck_gate_baseline()
    rc = 0

    if count > baseline:
        print(
            f"ABSTRACTION CONTRACT VIOLATION: hardcoded card-name conditionals "
            f"increased from {baseline} → {count}.",
            file=sys.stderr,
        )
        print("\nCurrent hits:", file=sys.stderr)
        for path, lineno, line in hits:
            print(f"  {path}:{lineno}: {line}", file=sys.stderr)
        print(
            "\nFix options:\n"
            "  1. Replace the name check with an oracle-text or template-field check.\n"
            "  2. Move card-specific knowledge into decks/gameplans/*.json.\n"
            "  3. If genuinely unavoidable (e.g. enum check), tag the line:\n"
            f"       {ALLOW_MARKER}: <reason>",
            file=sys.stderr,
        )
        rc = 1
    elif count < baseline:
        print(
            f"Abstraction count dropped from {baseline} → {count}. "
            f"Lower the baseline explicitly:",
            file=sys.stderr,
        )
        print(
            f'  set "hardcoded_name_count": {count} in '
            f"{BASELINE_FILE.relative_to(ROOT)}",
            file=sys.stderr,
        )
        rc = 1

    if deck_count > deck_baseline:
        print(
            f"ABSTRACTION CONTRACT VIOLATION: hardcoded deck-name gates "
            f"increased from {deck_baseline} → {deck_count}.",
            file=sys.stderr,
        )
        print("\nCurrent deck-gate hits:", file=sys.stderr)
        for path, lineno, line in deck_hits:
            print(f"  {path}:{lineno}: {line}", file=sys.stderr)
        print(
            "\nFix options:\n"
            "  1. Lift the gate to an archetype tag or oracle predicate.\n"
            "  2. Move per-deck tuning into decks/gameplans/*.json or\n"
            "     ai/strategy_profile.py (per-archetype config).\n"
            "  3. If genuinely unavoidable (rare), tag the line:\n"
            f"       {ALLOW_MARKER}: <reason>\n"
            "See docs/design/2026-05-04_modern_combo_audit_methodology.md (Class D).",
            file=sys.stderr,
        )
        rc = 1
    elif deck_count < deck_baseline:
        print(
            f"Deck-gate count dropped from {deck_baseline} → {deck_count}. "
            f"Lower the baseline explicitly:",
            file=sys.stderr,
        )
        print(
            f'  set "deck_gate_count": {deck_count} in '
            f"{BASELINE_FILE.relative_to(ROOT)}",
            file=sys.stderr,
        )
        rc = 1

    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
