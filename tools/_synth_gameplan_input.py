"""Decklist → user-prompt formatter for the synth_gameplan agent.

Phase H of the abstraction-cleanup pass.  Pulled out of
`tools/synth_gameplan.py` to keep the CLI shim thin.  No
LLM-specific logic lives here — only decklist parsing and a stable
text rendering for the user prompt."""
from __future__ import annotations

from typing import Dict


def format_decklist_for_prompt(
    deck_name: str,
    mainboard: Dict[str, int],
    db,
) -> str:
    """Render a decklist as a text blob: each card with its quantity
    and oracle text, ready to feed to the LLM as the user prompt.

    Single-line oracle text per card keeps the prompt compact and
    deterministic regardless of MTGJSON's line-break choices."""
    lines = [f"Deck name: {deck_name}", ""]
    lines.append("Mainboard (with oracle text):")
    for name in sorted(mainboard.keys()):
        qty = mainboard[name]
        oracle = ""
        if db is not None:
            raw = db.get_raw(name)
            if raw is not None:
                # MTGJSON exposes oracle text on `text`.  Missing for
                # split / transform layouts; treat as empty rather
                # than crashing.
                oracle = raw.get("text") or ""
        oracle_clean = oracle.replace("\n", " ").strip()
        lines.append(f"- {qty}x {name}: {oracle_clean}")
    lines.append("")
    lines.append(
        "Emit a SynthesizedGameplan JSON object describing this deck's "
        "goals, role assignments, and mulligan keys."
    )
    return "\n".join(lines)
