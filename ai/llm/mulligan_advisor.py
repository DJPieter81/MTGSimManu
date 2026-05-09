"""SLM-driven mulligan advisor — Phase 4C mulligan caller.

Third concrete caller of ``LLMPolicy``, parallel to
``oracle_parse`` and ``sideboard_advisor``. Replaces the
archetype-specific keep/mull rules in ``ai/mulligan.py`` for
corner cases the heuristic flags as low-confidence.

The full mulligan decision in ``ai/mulligan.py`` is generally
strong — especially for canonical archetypes (Affinity always
keeps an artifact-heavy hand; Storm always keeps a hand with a
ritual + cantrip). The advisor's value is in:
  - **Edge cases**: hands the heuristic returns "keep" with thin
    rationale (e.g. Affinity 1-land + 4 zero-cost artifacts —
    keep is correct but the heuristic's confidence is low).
  - **Pre-mulligan-to-six bottoming**: the LLM understands which
    cards to ship to the bottom given the full hand context.

Reference:
- docs/research/2026-05_phase_4c_slm_scoping.md
- ai/mulligan.py — current heuristic
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from ai.llm.policy import LLMPolicy


# Schema ID — bumped when the MulliganDecision shape changes.
MULLIGAN_DECISION_SCHEMA_ID = "mulligan_decision_v1"


@dataclass
class MulliganDecision:
    """Structured mulligan recommendation with provenance.

    Attributes:
      keep: True to keep the hand, False to mulligan.
      confidence: model self-reported confidence in the decision
        (0.0–1.0). Heuristic callers can use this to gate
        SLM-vs-fallback logic.
      reasoning: short free-text rationale (one sentence). Useful
        in replays for explainability.
      bottom: when keeping at <7 cards (post-Vancouver-mulligan),
        ordered list of cards the player should put on the bottom.
        Empty when not applicable.
    """

    keep: bool = True
    confidence: float = 0.0
    reasoning: Optional[str] = None
    bottom: List[str] = field(default_factory=list)


def _build_prompt(
    deck_name: str,
    hand: List[str],
    on_play: bool,
    bottom_count: int = 0,
) -> str:
    """Format the mulligan decision into a structured-output prompt."""
    hand_lines = "\n".join(f"  - {card}" for card in sorted(hand))
    play_str = "on the play" if on_play else "on the draw"
    bottom_str = (
        f"\nIf keeping, you must put {bottom_count} card(s) on "
        f"the bottom of your library."
        if bottom_count > 0 else ""
    )
    return f"""You are an expert Magic: The Gathering coach
deciding whether to keep or mulligan an opening hand.

Deck: {deck_name}
You are: {play_str}
Hand ({len(hand)} cards):
{hand_lines}{bottom_str}

Output ONLY a JSON object matching this schema (no prose):

  {{
    "keep": <true or false>,
    "confidence": <float 0.0–1.0>,
    "reasoning": "<1 sentence rationale>",
    "bottom": [<card name>, ...]  // exactly {bottom_count} entries if keeping; [] otherwise
  }}

JSON:"""


def _strip_code_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _parse_response(raw: str) -> MulliganDecision:
    cleaned = _strip_code_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Model output is not valid JSON: {e}. Raw: {raw[:300]}"
        )
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected JSON object, got {type(data).__name__}."
        )

    keep_raw = data.get("keep", True)
    keep = bool(keep_raw)

    confidence_raw = data.get("confidence", 0.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0.0

    reasoning_raw = data.get("reasoning")
    reasoning = (
        reasoning_raw if isinstance(reasoning_raw, str) else None
    )

    bottom_raw = data.get("bottom", [])
    if isinstance(bottom_raw, list):
        bottom = [str(c) for c in bottom_raw if isinstance(c, str)]
    else:
        bottom = []

    return MulliganDecision(
        keep=keep,
        confidence=confidence,
        reasoning=reasoning,
        bottom=bottom,
    )


def advise_mulligan(
    deck_name: str,
    hand: List[str],
    policy: LLMPolicy,
    on_play: bool = True,
    bottom_count: int = 0,
) -> MulliganDecision:
    """Generate a keep/mull decision (and bottoming if applicable).

    Cache-aware: same (deck, hand, play_state, bottom_count) →
    same decision. Mulligans are deterministic per the model's
    greedy decode + the cache layer.

    Args:
      deck_name: e.g. "Boros Energy".
      hand: list of card names in the opening hand.
      policy: configured ``LLMPolicy``.
      on_play: True if going first.
      bottom_count: number of cards to send to the bottom (0 for
        a pre-Vancouver hand of 7; raise to N when keeping at
        7-N cards post-mulligan).

    Returns: ``MulliganDecision``. Falls back to keep=True with
    confidence=0.0 if the model's output is unparseable
    (caller should treat low confidence as fallback signal).
    """
    prompt = _build_prompt(deck_name, hand, on_play, bottom_count)
    response = policy.generate(
        prompt=prompt,
        schema_id=MULLIGAN_DECISION_SCHEMA_ID,
        parser=_parse_response,
        max_tokens=200,
    )
    return response.parsed


def to_dict(decision: MulliganDecision) -> dict:
    """Convenience: serialize a MulliganDecision to a plain dict."""
    return asdict(decision)
