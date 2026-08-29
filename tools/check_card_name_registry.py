"""Ratchet: the card-name-keyed registries may only shrink.

CLAUDE.md's abstraction contract bans new `card.name == "X"` gates in
`engine/` and `ai/`, and `tools/check_abstraction.py` enforces that at 0.
But the contract also records a blind spot in that enforcement:

    Note: `EFFECT_REGISTRY.register("Card Name", ...)` and
    `TAG_OVERRIDES`/`ABILITY_OVERRIDES` dict keys are **invisible to the
    ratchet's regex** ... that blindness is not permission.

This ratchet closes the blindness.  Every card-name-keyed registry entry is
per-card knowledge living in `.py` source — exactly what the contract says
belongs in oracle text, MTGJSON, or `decks/gameplans/*.json` instead.  The
entries that exist are technical debt from before the mechanic-cluster
consolidations; the ones that remain are allowed to stay, but the count must
never grow.

The rule is a strict ratchet in one direction:

    count >  baseline  -> regression: a new per-card handler was added.
                          Build the mechanic class instead (parse the shape
                          once into a typed CardTemplate field and dispatch
                          off that), or refuse the variant outright.
    count <  baseline  -> an improvement was made but not recorded.  Lower
                          the baseline in the same commit (this is the
                          intended direction of travel).
    count == baseline  -> pass.

Precedent for the fix direction: `green_suns_zenith_resolve` was deleted when
the X-creature-tutor shape got a generic resolver driven by
`CardTemplate.x_creature_tutor_data`, taking this count from 97 to 96.

Usage:
    python tools/check_card_name_registry.py [--list] [--baseline PATH]
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent.parent
DEFAULT_BASELINE = ROOT / "tools" / "card_name_registry_baseline.json"

# Files scanned for card-name-keyed registrations.  Each pattern captures the
# card name so `--list` can show exactly what is registered.
_PATTERNS = {
    "engine/card_effects.py": [
        re.compile(r"""EFFECT_REGISTRY\.register\(\s*["']([^"']+)["']"""),
    ],
}


def _scan() -> dict[str, list[str]]:
    """Return {relative_path: [card names registered]}."""
    found: dict[str, list[str]] = {}
    for rel, patterns in _PATTERNS.items():
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        names: list[str] = []
        for pat in patterns:
            names.extend(pat.findall(text))
        if names:
            found[rel] = names
    return found


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    list_mode = "--list" in args

    baseline_path = DEFAULT_BASELINE
    for i, arg in enumerate(args):
        if arg == "--baseline" and i + 1 < len(args):
            baseline_path = pathlib.Path(args[i + 1])

    found = _scan()
    total = sum(len(v) for v in found.values())

    if list_mode:
        for rel, names in sorted(found.items()):
            print(f"{rel}: {len(names)} card-name registrations")
            for name in sorted(names):
                print(f"    {name}")

    with baseline_path.open() as f:
        baseline: dict = json.load(f)
    allowed = int(baseline["total"])

    if total > allowed:
        print(
            f"FAIL: card-name registry grew — {total} entries "
            f"(baseline {allowed}).\n"
            f"A new per-card handler was added.  The contract wants the "
            f"MECHANIC instead: parse the shape once into a typed "
            f"CardTemplate field and dispatch generically off it, or refuse "
            f"the variant (leave it unclassified) rather than half-executing "
            f"it.  See tools/check_card_name_registry.py's docstring.\n"
            f"To see every entry: python tools/check_card_name_registry.py --list"
        )
        return 1

    if total < allowed:
        print(
            f"FAIL: baseline is stale — {total} entries, baseline says "
            f"{allowed}.\nYou removed per-card handlers (good, that is the "
            f"intended direction).  Record it: set \"total\" to {total} in "
            f"{baseline_path.name} in this same commit."
        )
        return 1

    print(f"Card-name-registry ratchet OK — total = {total} (baseline = {allowed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
