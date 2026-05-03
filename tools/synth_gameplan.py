"""Offline gameplan synthesizer — typed-output entry point.

Phase 4 of the abstraction-cleanup pass.  Produces a `SynthesizedGameplan`
(typed pydantic model) from a decklist + deck name.  The output is the
canonical "I have a decklist, give me a gameplan JSON" surface.

Two synth backends share the same surface:

1. `synth_gameplan_rule_based(...)` — wraps the existing rule-based
   heuristic in `import_deck.generate_gameplan`.  Deterministic, no
   external dependencies.  Default backend.

2. `synth_gameplan_llm(...)` — a pydantic-ai agent that prompts a
   model with the decklist + per-card oracle text and emits a typed
   `SynthesizedGameplan` directly via pydantic-ai's structured-output
   feature.  Runs ONCE at deck-import time; the simulator never
   re-invokes the LLM.  See `_build_llm_agent()` for the agent setup.

Both backends produce the same `SynthesizedGameplan` shape so
downstream consumers (`to_json_dict` → file → `parse_gameplan`)
are agnostic.

CLI:

    # Rule-based (default, deterministic, offline)
    python -m tools.synth_gameplan "My Deck" decklist.txt > plan.json

    # LLM-driven (calls the configured model — requires an API key)
    python -m tools.synth_gameplan --llm "My Deck" decklist.txt > plan.json

Model selection: defaults to `anthropic:claude-haiku-4-5-20251001`
(small, cheap; the task is structured extraction).  Override via the
`MTG_SYNTH_MODEL` environment variable, e.g.:

    MTG_SYNTH_MODEL=anthropic:claude-sonnet-4-5 python -m tools.synth_gameplan --llm ...

Determinism: the rule-based path is fully deterministic.  The LLM path
runs ONCE at deck-import time and the resulting JSON is committed —
the simulator never re-invokes the LLM.  Reproducibility is at the
JSON level, not the LLM-call level.

Testing: `tests/test_synth_gameplan_llm_backend.py` uses pydantic-ai's
`TestModel` via `Agent.override(model=...)`, so CI never makes a real
API call and no API key is required.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

from ai.gameplan_schemas import (
    SynthesizedGameplan,
    SynthesizedGoal,
    to_json_dict,
)


DEFAULT_LLM_MODEL = "anthropic:claude-haiku-4-5-20251001"
"""Default model for the LLM backend.  Override via `MTG_SYNTH_MODEL`."""

LLM_MODEL_ENV_VAR = "MTG_SYNTH_MODEL"
"""Env var name a caller can set to override the default model."""

# Few-shot example deck names — these gameplans cover the three
# archetype shapes the synthesizer needs to handle: aggro CURVE_OUT,
# combo reanimator with mulligan_combo_paths, and combo cascade with
# fallback_goals.  Loaded at agent-build time from
# `decks/gameplans/*.json` so the LLM is anchored to real,
# checked-in examples rather than inline string blobs.
FEW_SHOT_GAMEPLANS = ("boros_energy", "goryos_vengeance", "living_end")


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


# ─── LLM backend (pydantic-ai) ────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a Magic: The Gathering deck-analysis assistant.  Your job is
to read a 60-card decklist (with each card's oracle text) and emit a
strategic gameplan as a typed JSON object matching the
`SynthesizedGameplan` schema.

Schema overview:
- `deck_name`, `archetype` (one of: aggro, midrange, control, combo,
  tempo, ramp), and a list of `goals`.
- Each `goal` has a `goal_type` (DEPLOY_ENGINE, FILL_RESOURCE, RAMP,
  EXECUTE_PAYOFF, CURVE_OUT, PUSH_DAMAGE, DISRUPT, PROTECT, INTERACT,
  GRIND_VALUE, CLOSE_GAME), a description, and `card_roles` mapping
  role buckets (enablers / payoffs / interaction / fillers /
  protection) to lists of card names.
- For combo decks you SHOULD emit `mulligan_combo_paths`: a list of
  dicts mapping role buckets → card names.  Each path is an
  alternative way to assemble the combo; the mulligan engine treats a
  hand as keepable when it has at least one card from EACH bucket of
  at least one path at the relevant virtual hand size.
- `always_early` lists cards that should be played in the first
  available turn.  `reactive_only` lists cards that must be held for
  responses, not deployed proactively.  `critical_pieces` lists cards
  whose presence in a hand strongly biases keep decisions.

Identify role buckets by MECHANIC, not by card name.  For example:
- Cards with cycling/loot/discard outlets that fill a graveyard are
  `enablers` of a reanimator goal.
- Reanimation spells (oracle text returns a creature from graveyard
  to play) are `payoffs`.
- Removal/counter spells are `interaction`.
- Cheap blink/protection effects are `protection`.

DO NOT invent card names.  Every name in your output MUST appear in
the decklist provided.

Below are three checked-in real-game examples in the same JSON shape
for reference.  Match their level of detail.
"""


def _build_few_shot_blob() -> str:
    """Read 2-3 reference gameplan JSONs and concatenate them into a
    few-shot block appended to the system prompt.  Falls back to an
    empty blob if the files are missing (tests using TestModel still
    work because TestModel ignores the prompt text)."""
    root = Path(__file__).resolve().parent.parent
    examples = []
    for stem in FEW_SHOT_GAMEPLANS:
        path = root / "decks" / "gameplans" / f"{stem}.json"
        if path.exists():
            try:
                blob = json.loads(path.read_text())
                examples.append(
                    f"--- Example: {stem} ---\n"
                    f"{json.dumps(blob, indent=2, sort_keys=True)}"
                )
            except (json.JSONDecodeError, OSError):
                continue
    if not examples:
        return ""
    return "\n\nFew-shot examples (real checked-in gameplans):\n\n" + "\n\n".join(examples)


def _format_decklist_for_prompt(
    deck_name: str,
    mainboard: Dict[str, int],
    db,
) -> str:
    """Render a decklist as a text blob: each card with its quantity
    and oracle text, ready to feed as the user prompt."""
    lines = [f"Deck name: {deck_name}", ""]
    lines.append("Mainboard (with oracle text):")
    for name in sorted(mainboard.keys()):
        qty = mainboard[name]
        oracle = ""
        if db is not None:
            raw = db.get_raw(name)
            if raw is not None:
                # MTGJSON exposes oracle text on the `text` field.  May
                # be missing for split/transform layouts; treat as
                # empty rather than crashing.
                oracle = raw.get("text") or ""
        # Single-line oracle text per card keeps the prompt compact.
        oracle_clean = oracle.replace("\n", " ").strip()
        lines.append(f"- {qty}x {name}: {oracle_clean}")
    lines.append("")
    lines.append(
        "Emit a SynthesizedGameplan JSON object describing this deck's "
        "goals, role assignments, and mulligan keys."
    )
    return "\n".join(lines)


def _build_llm_agent(model: Optional[str] = None):
    """Build a pydantic-ai `Agent` configured for gameplan synthesis.

    The agent's `output_type` is `SynthesizedGameplan`, so the model
    is forced to emit a structured object that pydantic validates.

    `model` resolution order:
        1. explicit argument (used by `synth_gameplan_llm`'s tests)
        2. `MTG_SYNTH_MODEL` env var
        3. `DEFAULT_LLM_MODEL` constant

    `defer_model_check=True` lets us build the agent without an API
    key in the environment — tests immediately call `agent.override`
    to swap in `TestModel`, so the real-model check never runs in CI.
    """
    from pydantic_ai import Agent  # local import: optional dep

    chosen = model or os.environ.get(LLM_MODEL_ENV_VAR) or DEFAULT_LLM_MODEL
    system_prompt = _SYSTEM_PROMPT + _build_few_shot_blob()
    return Agent(
        chosen,
        output_type=SynthesizedGameplan,
        system_prompt=system_prompt,
        defer_model_check=True,
    )


def synth_gameplan_llm(
    deck_name: str,
    mainboard: Dict[str, int],
    db=None,
    *,
    model: Optional[str] = None,
) -> SynthesizedGameplan:
    """Synthesize a gameplan using a pydantic-ai agent with structured
    output.

    The model is given the decklist + per-card oracle text and emits
    a `SynthesizedGameplan` directly (validated by pydantic).  No
    card-specific knowledge lives in this code — the model reads the
    oracle text itself and assigns role buckets by mechanic.

    Args:
        deck_name: Human-readable deck name (round-trips to JSON).
        mainboard: {card_name: quantity}.
        db: Optional pre-loaded `CardDatabase`.  When None, one is
            instantiated from the default JSON path.
        model: Optional model override.  When None, uses
            `MTG_SYNTH_MODEL` env var or `DEFAULT_LLM_MODEL`.

    Returns:
        A validated `SynthesizedGameplan`.

    Raises:
        Whatever pydantic-ai raises on auth / network / validation
        failures.  The caller (CLI / import_deck) decides whether to
        fall back to the rule-based path.
    """
    if db is None:
        from engine.card_database import CardDatabase
        db = CardDatabase()

    agent = _build_llm_agent(model=model)
    user_prompt = _format_decklist_for_prompt(deck_name, mainboard, db)
    result = agent.run_sync(user_prompt)
    output = result.output
    # The model occasionally drops `deck_name` even with structured
    # output enabled; restore from the input to keep the round-trip
    # contract tight.  This is a one-line normalization, not a patch
    # of the schema.
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
    if args and args[0] == "--llm":
        use_llm = True
        args = args[1:]
    if len(args) < 2:
        print(
            "Usage: python -m tools.synth_gameplan [--llm] \"Deck Name\" decklist.txt",
            file=sys.stderr,
        )
        return 2
    deck_name, decklist_path = args[0], args[1]
    mainboard = _read_decklist(decklist_path)
    if use_llm:
        plan = synth_gameplan_llm(deck_name, mainboard)
    else:
        plan = synth_gameplan_rule_based(deck_name, mainboard)
    json.dump(to_json_dict(plan), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
