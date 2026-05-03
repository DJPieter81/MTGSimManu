"""Offline gameplan synthesizer — typed-output entry point.

Phase 4 of the abstraction-cleanup pass.  Produces a `SynthesizedGameplan`
(typed pydantic model) from a decklist + deck name.  The output is the
canonical "I have a decklist, give me a gameplan JSON" surface.

Two synth backends are designed for this surface:

1. `synth_gameplan_rule_based(...)` — wraps the existing rule-based
   heuristic in `import_deck.generate_gameplan`.  Deterministic, no
   external dependencies.  Shipped in this PR.

2. `synth_gameplan_llm(...)` — a pydantic-ai agent that prompts a
   model with the decklist + oracle text and emits a typed
   `SynthesizedGameplan` directly.  Skeleton placeholder here;
   real implementation is a follow-up PR once the schema surface
   has been validated by the rule-based path.

Both backends produce the same `SynthesizedGameplan` shape so
downstream consumers (`to_json_dict` → file → `parse_gameplan`)
are agnostic.

CLI:

    python -m tools.synth_gameplan "My Deck" decklist.txt > plan.json

Determinism: the rule-based path is fully deterministic.  The LLM path
runs ONCE at deck-import time and the resulting JSON is committed —
the simulator never re-invokes the LLM.  Reproducibility is at the
JSON level, not the LLM-call level.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Optional

from ai.gameplan_schemas import (
    SynthesizedGameplan,
    SynthesizedGoal,
    to_json_dict,
)


def synth_gameplan_rule_based(
    deck_name: str,
    mainboard: Dict[str, int],
    archetype: Optional[str] = None,
    db=None,
) -> SynthesizedGameplan:
    """Synthesize a gameplan from a decklist using the rule-based
    heuristic in `import_deck.generate_gameplan`, then validate-cast
    the resulting dict into the typed `SynthesizedGameplan`.

    Returning a typed model (rather than a raw dict) lets downstream
    callers inspect, mutate, and re-serialize without losing the
    schema contract — and matches the surface the LLM backend will
    produce.
    """
    # Local imports keep the module loadable even when the engine /
    # card database is not available (e.g. for schema-only tests).
    from import_deck import generate_gameplan, detect_archetype

    if db is None:
        from engine.card_database import CardDatabase
        db = CardDatabase()

    if archetype is None:
        archetype = detect_archetype(mainboard, db=db)

    raw = generate_gameplan(deck_name, mainboard, archetype, db=db)
    return _coerce_dict_to_synthesized(raw)


def _coerce_dict_to_synthesized(raw: dict) -> SynthesizedGameplan:
    """Validate-cast a raw gameplan dict (from the rule-based heuristic)
    into a typed `SynthesizedGameplan`.

    The heuristic emits Python lists for every card-list field, which
    is the same shape pydantic expects, so this is a single
    `model_validate` call.  Validation errors surface as
    `pydantic.ValidationError` to the caller.
    """
    # Coerce the legacy-shape "card_roles" values from sets-or-lists to
    # lists, and convert any nested sets in mulligan_combo_sets the
    # same way.  The rule-based path emits lists already, but defensive
    # coercion makes the function safe to feed dicts loaded from JSON
    # too.
    goals = []
    for g in raw.get("goals", []):
        roles = {k: sorted(v) if isinstance(v, set) else v
                 for k, v in g.get("card_roles", {}).items()}
        goals.append({**g, "card_roles": roles})

    fallback_goals = None
    if raw.get("fallback_goals"):
        fallback_goals = []
        for g in raw["fallback_goals"]:
            roles = {k: sorted(v) if isinstance(v, set) else v
                     for k, v in g.get("card_roles", {}).items()}
            fallback_goals.append({**g, "card_roles": roles})

    payload = {
        "deck_name": raw["deck_name"],
        "archetype": raw.get("archetype", "midrange"),
        "archetype_subtype": raw.get("archetype_subtype"),
        "goals": goals,
    }
    if fallback_goals is not None:
        payload["fallback_goals"] = fallback_goals

    # Optional fields — only carry through when present in raw, so the
    # output JSON stays minimal and the loader's defaults apply.
    for key in (
        "mulligan_min_lands", "mulligan_max_lands",
        "mulligan_require_creature_cmc", "mulligan_effective_cmc",
        "mulligan_keys", "mulligan_combo_sets", "mulligan_combo_paths",
        "mulligan_cmc_profile",
        "always_early", "reactive_only", "critical_pieces",
        "land_priorities", "combo_readiness_check",
    ):
        if key in raw:
            payload[key] = raw[key]

    return SynthesizedGameplan.model_validate(payload)


# ─── LLM backend skeleton — implementation is a follow-up PR ─────────

def synth_gameplan_llm(
    deck_name: str,
    mainboard: Dict[str, int],
    db=None,
) -> SynthesizedGameplan:
    """Placeholder for the pydantic-ai backend.

    Will accept the same inputs as the rule-based variant, prompt a
    model with the decklist + per-card oracle text + 3 example
    gameplans (few-shot), and emit a typed `SynthesizedGameplan`
    directly via pydantic-ai's structured-output feature.

    Until the follow-up PR lands, this raises NotImplementedError.
    The schema surface above is the concrete output contract the
    eventual implementation must satisfy.
    """
    raise NotImplementedError(
        "synth_gameplan_llm is a Phase-4 follow-up PR. "
        "Use synth_gameplan_rule_based for now — same output shape."
    )


# ─── CLI ─────────────────────────────────────────────────────────────

def _read_decklist(path: str) -> Dict[str, int]:
    from import_deck import parse_decklist
    text = Path(path).read_text()
    mainboard, _sb = parse_decklist(text)
    return mainboard


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("Usage: python -m tools.synth_gameplan \"Deck Name\" decklist.txt",
              file=sys.stderr)
        return 2
    deck_name, decklist_path = argv[1], argv[2]
    mainboard = _read_decklist(decklist_path)
    plan = synth_gameplan_rule_based(deck_name, mainboard)
    json.dump(to_json_dict(plan), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
