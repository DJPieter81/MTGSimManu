"""Engine rules are read from card text, never from classifier tags.

An object's abilities are what its rules text says (CR 113.1): a triggered
ability triggers only on the event its text names (CR 603.1) and a static
restriction applies only as printed (CR 604.1). The oracle classifier
(`ai/oracle_classifier`) is a heuristic -- possibly model-made -- label: it
may guide AI scoring, but an engine rule that reads it lets one wrong label
become a game rule (the rejected Jev tag-cache A/B: a false on-draw
life-gain tag gave its controller life on every draw).

The engine modules that still read a tag are pinned here. The set may only
shrink: each unit that moves a rule onto the card's parsed text removes its
module, and an addition fails.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The tag-gated engine rules still on their way to the card's text.
STILL_READING_TAGS = {
    "engine/oracle_resolver.py",      # ETB surveil / ETB regrowth (unit E)
    "engine/zone_transfer.py",        # on-draw triggers (unit D)
    "engine/clause_resolver.py",      # impulse draw (unit I)
}


def _reads_classifier(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                node.module.endswith("oracle_classifier"):
            return True
        if isinstance(node, ast.Import) and any(
                a.name.endswith("oracle_classifier") for a in node.names):
            return True
    return False


def test_only_the_pinned_engine_modules_read_classifier_tags():
    readers = {str(p.relative_to(REPO))
               for p in sorted((REPO / "engine").rglob("*.py"))
               if _reads_classifier(p)}
    assert readers <= STILL_READING_TAGS, (
        f"new engine reader(s) of classifier tags: "
        f"{sorted(readers - STILL_READING_TAGS)} -- read the card's text")
    assert readers == STILL_READING_TAGS, (
        f"stale pin: {sorted(STILL_READING_TAGS - readers)} no longer read "
        f"tags -- remove them from STILL_READING_TAGS")
