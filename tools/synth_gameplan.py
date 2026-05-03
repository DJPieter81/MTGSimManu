"""Offline gameplan synthesizer — typed-output entry point (CLI shim).

Phase H consolidated the LLM infrastructure under `ai/llm_*`:

  * Schemas:    `ai.llm_schemas.SynthesizedGameplan`
  * Model:      `ai.llm_models.select_model("synth_gameplan")`
  * Prompt:     `ai.llm_prompts/synth_gameplan_v<N>.md`
  * Agent:      `ai.llm_agents.build_agent("synth_gameplan")`

This module is now a thin wrapper that exposes:
  * `synth_gameplan_rule_based(...)` — wraps `import_deck.generate_gameplan`,
    deterministic and offline.  Default backend.
  * `synth_gameplan_llm(...)` — calls the pydantic-ai agent built
    above and post-processes the result.
  * The `python -m tools.synth_gameplan` CLI.

CLI:

    # Rule-based (default, deterministic, offline)
    python -m tools.synth_gameplan "My Deck" decklist.txt > plan.json

    # LLM-driven (calls the configured model — requires an API key)
    python -m tools.synth_gameplan --llm "My Deck" decklist.txt > plan.json

Model selection:
    1. Explicit `model=` argument to `synth_gameplan_llm`.
    2. `MTG_LLM_MODEL_SYNTH_GAMEPLAN` env var.
    3. `MTG_LLM_MODEL` env var.
    4. `MTG_SYNTH_MODEL` env var (legacy; one-shot DeprecationWarning).
    5. `ai.llm_models.DEFAULT_MODELS["synth_gameplan"]`.

Determinism: rule-based path is fully deterministic.  LLM path runs
ONCE at deck-import time and the resulting JSON is committed — the
simulator never re-invokes the LLM.

Backward-compat surfaces (kept as thin shims so existing tests and
callers don't churn):
  * `DEFAULT_LLM_MODEL` — re-exports `DEFAULT_MODELS["synth_gameplan"]`.
  * `LLM_MODEL_ENV_VAR` — re-exports `LEGACY_SYNTH_ENV` ("MTG_SYNTH_MODEL").
  * `_build_llm_agent(model=...)` — calls `ai.llm_agents.build_agent`.
  * `_format_decklist_for_prompt(...)` — calls
    `tools._synth_gameplan_input.format_decklist_for_prompt`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Optional

from ai.llm_agents import build_agent
from ai.llm_models import DEFAULT_MODELS, LEGACY_SYNTH_ENV
from ai.llm_schemas import SynthesizedGameplan, SynthesizedGoal, to_json_dict
from tools._synth_gameplan_input import format_decklist_for_prompt


# ─── Backward-compat exports (PR #258 surface) ───────────────────────

DEFAULT_LLM_MODEL = DEFAULT_MODELS["synth_gameplan"]
"""Backward-compat alias for the synth_gameplan default model.  New
callers should read `ai.llm_models.DEFAULT_MODELS['synth_gameplan']`
directly."""

LLM_MODEL_ENV_VAR = LEGACY_SYNTH_ENV
"""Backward-compat alias for the legacy env-var name `MTG_SYNTH_MODEL`.
New callers should set `MTG_LLM_MODEL_SYNTH_GAMEPLAN` or
`MTG_LLM_MODEL` instead."""


# ─── Rule-based backend (unchanged) ──────────────────────────────────

def synth_gameplan_rule_based(
    deck_name: str,
    mainboard: Dict[str, int],
    archetype: Optional[str] = None,
    db=None,
) -> SynthesizedGameplan:
    """Synthesize a gameplan using the rule-based heuristic in
    `import_deck.generate_gameplan`, then validate-cast the resulting
    dict into the typed `SynthesizedGameplan`."""
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
    """Validate-cast a raw gameplan dict into a typed
    `SynthesizedGameplan`."""
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


# ─── LLM backend (delegates to ai.llm_agents) ────────────────────────

def _build_llm_agent(model: Optional[str] = None):
    """Build the synth_gameplan agent.  Thin wrapper over
    `ai.llm_agents.build_agent` kept for backward compatibility with
    PR #258's tests, which patch this symbol directly."""
    return build_agent("synth_gameplan", model=model)


def _format_decklist_for_prompt(
    deck_name: str,
    mainboard: Dict[str, int],
    db,
) -> str:
    """Backward-compat alias for the prompt formatter (now in
    `tools._synth_gameplan_input`)."""
    return format_decklist_for_prompt(deck_name, mainboard, db)


def synth_gameplan_llm(
    deck_name: str,
    mainboard: Dict[str, int],
    db=None,
    *,
    model: Optional[str] = None,
) -> SynthesizedGameplan:
    """Synthesize a gameplan using a pydantic-ai agent with structured
    output."""
    if db is None:
        from engine.card_database import CardDatabase
        db = CardDatabase()

    agent = _build_llm_agent(model=model)
    user_prompt = format_decklist_for_prompt(deck_name, mainboard, db)
    result = agent.run_sync(user_prompt)
    output = result.output
    # The model occasionally drops `deck_name` even with structured
    # output enabled; restore from the input to keep the round-trip
    # contract tight.
    if not output.deck_name:
        output = output.model_copy(update={"deck_name": deck_name})
    return output


# ─── CLI ─────────────────────────────────────────────────────────────

def _read_decklist(path: str) -> Dict[str, int]:
    from import_deck import parse_decklist
    text = Path(path).read_text()
    mainboard, _sb = parse_decklist(text)
    return mainboard


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    use_llm = False
    use_rule_based_explicit = False
    if args and args[0] == "--llm":
        use_llm = True
        args = args[1:]
    elif args and args[0] == "--rule-based":
        # Explicit flag accepted for forward-compat; rule-based is
        # already the default but the verification step in the PR
        # description uses `--rule-based` so we accept it.
        use_rule_based_explicit = True
        args = args[1:]

    if len(args) < 2:
        print(
            "Usage: python -m tools.synth_gameplan [--llm|--rule-based] "
            '"Deck Name" decklist.txt',
            file=sys.stderr,
        )
        return 2

    deck_name, decklist_path = args[0], args[1]
    mainboard = _read_decklist(decklist_path)

    if use_llm:
        plan = synth_gameplan_llm(deck_name, mainboard)
    else:
        plan = synth_gameplan_rule_based(deck_name, mainboard)
        # Reference `use_rule_based_explicit` so static-analysis tools
        # don't complain about the unused flag — the flag's role is to
        # be parsed without erroring.
        del use_rule_based_explicit

    json.dump(to_json_dict(plan), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))


__all__ = [
    "DEFAULT_LLM_MODEL",
    "LLM_MODEL_ENV_VAR",
    "synth_gameplan_rule_based",
    "synth_gameplan_llm",
    "_build_llm_agent",
    "_format_decklist_for_prompt",
    "SynthesizedGameplan",
    "SynthesizedGoal",
]
