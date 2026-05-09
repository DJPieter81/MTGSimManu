"""SLM-driven oracle parser — Phase 4C Week 3.

The first concrete caller of ``LLMPolicy``. Replaces the regex-
based parsing in ``engine/oracle_parser.py`` for cards flagged
"needs review" — typically those whose oracle text uses
conjunctions, gates, or non-standard phrasing that the regex
misses (the Nettlecyst "and/or enchantment" bug class, or the
parse_cost_reduction false-positive Phase 1A fixed).

Output schema is intentionally narrow for Week 3: a single
``OracleEffect`` capturing the most-impactful primary effect
(draw N, deal N damage, destroy target X, etc.) plus structural
flags (is_cost_reduction, is_tutor, is_counter). Future weeks
expand the schema; the cache layer (``LLMPolicy.cache_dir``)
namespaces by ``schema_id`` so old cached entries stay valid for
old schema versions.

Reference:
- docs/research/2026-05_phase_4c_slm_scoping.md
- docs/research/2026-05_mtg_ai_landscape.md §5
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from ai.llm.policy import LLMPolicy


# Schema ID — bump when the OracleEffect dataclass changes shape.
# Cache entries from a previous schema version stay valid for old
# runs (different schema_id = different cache namespace).
ORACLE_EFFECT_SCHEMA_ID = "oracle_effect_v1"


@dataclass
class OracleEffect:
    """Compact structured representation of an oracle text's primary
    mechanical effect.

    Multi-effect cards (e.g. "Draw 2 cards. Then deal 2 damage to
    any target.") are summarized to the highest-impact effect.
    Future schema versions may carry an ``effects`` list; for Week
    3 we keep a single primary effect plus structural flags.
    """

    primary_effect: str = "unknown"
    """Free-form effect category. The Week-3 vocabulary (extensible):
       - "draw"        : draw N cards
       - "damage"      : deal N damage to any target
       - "destroy"     : destroy target X
       - "exile"       : exile target X
       - "counter"     : counter target spell
       - "tutor"       : search library for X
       - "discard"     : opponent discards N
       - "reanimate"   : return creature from graveyard
       - "buff"        : +N/+M to target/until end of turn
       - "ramp"        : add mana / search for land
       - "cost_reduce" : spells cost N less
       - "lock"        : prevent activated abilities
       - "passive"     : static effect (Construct token, etc.)
       - "unknown"     : fallback
    """

    amount: Optional[int] = None
    """Numeric amount (cards drawn, damage dealt, life gained, +N
    counters, etc.). None when not applicable."""

    target: Optional[str] = None
    """Target type: "creature", "artifact", "enchantment",
    "permanent", "spell", "player", "any". None for self / no
    target."""

    flags: List[str] = field(default_factory=list)
    """Structural flags the AI scoring layer reads:
       - "is_cost_reduction"  : reduces casting cost
       - "is_tutor"           : library-search effect
       - "is_counter"         : counterspell
       - "is_etb"             : enters-the-battlefield trigger
       - "is_recurring"       : fires on each upkeep / attack / etc.
       - "is_alternative_cost" : flashback / escape / warp / overload
    """


def _build_prompt(oracle_text: str) -> str:
    """Format the oracle text into a structured-output prompt.

    The prompt instructs the model to emit JSON only. The parser
    is robust to leading/trailing whitespace and ```json fences
    that some models add by reflex.
    """
    return f"""You are an expert Magic: The Gathering oracle parser.

Parse the following oracle text into structured JSON. Output
ONLY a JSON object matching this schema (no prose, no
explanation):

  {{
    "primary_effect": <one of: draw, damage, destroy, exile,
                      counter, tutor, discard, reanimate, buff,
                      ramp, cost_reduce, lock, passive, unknown>,
    "amount": <integer or null>,
    "target": <one of: creature, artifact, enchantment, permanent,
              spell, player, any, or null>,
    "flags": <list of: is_cost_reduction, is_tutor, is_counter,
              is_etb, is_recurring, is_alternative_cost>
  }}

Oracle text:
{oracle_text}

JSON:"""


def _strip_code_fences(raw: str) -> str:
    """Remove ```json...``` or ``` ``` fences if present."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Strip the opening fence (with or without language tag).
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        # Strip the closing fence.
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _parse_json_response(raw: str) -> OracleEffect:
    """Convert the model's raw text into an OracleEffect.

    Robust to:
      - ```json ... ``` fences
      - leading/trailing whitespace
      - missing fields (defaults applied)
      - unknown enum values (mapped to "unknown" / null)

    Raises ``ValueError`` only when the response can't be parsed
    as JSON at all. The ``LLMPolicy`` layer surfaces this as a
    clean error.
    """
    cleaned = _strip_code_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Model output is not valid JSON: {e}. Raw: {raw[:300]}"
        )
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected a JSON object, got {type(data).__name__}. "
            f"Raw: {raw[:300]}"
        )

    primary = data.get("primary_effect", "unknown")
    if not isinstance(primary, str):
        primary = "unknown"

    amount_raw = data.get("amount")
    amount: Optional[int]
    if amount_raw is None:
        amount = None
    else:
        try:
            amount = int(amount_raw)
        except (TypeError, ValueError):
            amount = None

    target_raw = data.get("target")
    target: Optional[str] = (
        target_raw if isinstance(target_raw, str) else None
    )

    flags_raw = data.get("flags", [])
    if isinstance(flags_raw, list):
        flags = [str(f) for f in flags_raw if isinstance(f, str)]
    else:
        flags = []

    return OracleEffect(
        primary_effect=primary,
        amount=amount,
        target=target,
        flags=flags,
    )


def parse_oracle(oracle_text: str, policy: LLMPolicy) -> OracleEffect:
    """Parse a card's oracle text into a structured OracleEffect.

    Cache-aware: same oracle text → same effect (deterministic
    replay). The first call for a given text invokes the model;
    subsequent calls hit the cache.

    Args:
      oracle_text: the card's oracle text.
      policy: configured LLMPolicy with a backend (stub or
        llama-cpp). Backend determines the cache namespace.

    Returns: ``OracleEffect``. Never None — falls back to
    ``primary_effect="unknown"`` if the model output is
    unparseable (logged via the LLMPolicy ValueError path; the
    caller decides whether to fall back to the regex parser).
    """
    prompt = _build_prompt(oracle_text)
    response = policy.generate(
        prompt=prompt,
        schema_id=ORACLE_EFFECT_SCHEMA_ID,
        parser=_parse_json_response,
        max_tokens=200,
    )
    return response.parsed


def to_dict(effect: OracleEffect) -> dict:
    """Convenience: serialize an OracleEffect to a plain dict for
    JSON dumping (e.g. when building the labeled corpus for the
    acceptance gate)."""
    return asdict(effect)
