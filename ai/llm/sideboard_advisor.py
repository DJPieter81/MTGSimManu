"""SLM-driven sideboard plan advisor — Phase 4C SB advisor.

Second concrete caller of ``LLMPolicy``, parallel to
``ai/llm/oracle_parse.py``. Generates structured sideboard swap
plans from a (my deck, my SB, opponent deck) tuple, replacing the
keyword-categorization tree in ``engine/sideboard_manager.py``
with a learned model.

The Phase 2A categorization fix (PR #304) addressed the worst false
positives (Damping Sphere, Pithing-as-destruction, Teferi as
artifact-hate), but the underlying matcher is still string-based and
context-blind: it doesn't know that:
  - vs Storm, you board out creature removal but keep 1 Bolt
    against Ral the Shockmaster.
  - vs Affinity, the swap budget exceeds the total artifact-hate
    count in many decks (Boros = 3, Izzet Prowess = 1) so the
    matcher caps short of optimal.
  - vs Living End, Force of Negation is the priority counter even
    if Mystical Dispute is in the SB.

A learned advisor trained on tournament SB guides absorbs all this
context. Cache pre-warm at session start (~256 swap plans for the
16x16 matrix) keeps the matrix-sim hot loop fast.

Reference:
- docs/research/2026-05_phase_4c_slm_scoping.md
- engine/sideboard_manager.py (current matcher being replaced)
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from ai.llm.policy import LLMPolicy


# Schema ID — bumped when the SideboardPlan shape changes.
SIDEBOARD_PLAN_SCHEMA_ID = "sideboard_plan_v1"


@dataclass
class SwapDirective:
    """A single sideboard swap: ±N copies of a card."""

    card: str
    """Card name. Must be present in either MB (for cuts) or SB
    (for adds), depending on direction."""

    delta: int
    """Net change in mainboard count. Positive = bring in from SB.
    Negative = send to SB."""


@dataclass
class SideboardPlan:
    """Structured sideboard plan: a list of swap directives plus
    summary metadata.

    Conventions:
      - Net delta MUST balance: sum(adds) == -sum(cuts).
      - At most ``max_swaps`` swaps per direction.
      - A swap of ``delta=0`` is invalid (filtered at parse time).
    """

    swaps: List[SwapDirective] = field(default_factory=list)
    """All non-zero deltas. Adds and cuts mixed; caller filters by
    sign if needed."""

    notes: Optional[str] = None
    """Free-form rationale from the model. Optional."""

    @property
    def adds(self) -> List[SwapDirective]:
        return [s for s in self.swaps if s.delta > 0]

    @property
    def cuts(self) -> List[SwapDirective]:
        return [s for s in self.swaps if s.delta < 0]

    def is_balanced(self) -> bool:
        """True if total adds equal total cuts in magnitude."""
        adds = sum(s.delta for s in self.adds)
        cuts = -sum(s.delta for s in self.cuts)
        return adds == cuts


def _build_prompt(
    my_deck: str,
    my_sideboard: Dict[str, int],
    opponent_deck: str,
    max_swaps: int = 7,
) -> str:
    """Format the matchup into a structured-output prompt.

    The model sees the player deck name, opponent deck name, and
    the available sideboard cards (with counts). It must return
    a balanced swap plan.
    """
    sb_lines = "\n".join(
        f"  - {count}x {name}"
        for name, count in sorted(my_sideboard.items())
    )
    return f"""You are an expert Magic: The Gathering coach building
a sideboard plan.

Player deck: {my_deck}
Opponent deck: {opponent_deck}
Available sideboard ({sum(my_sideboard.values())} cards):
{sb_lines}

Build a balanced sideboard plan: equal cards in and out, at most
{max_swaps} per side. Output ONLY a JSON object matching this
schema (no prose):

  {{
    "swaps": [
      {{"card": "<exact card name>", "delta": <positive integer to bring in, negative to cut>}},
      ...
    ],
    "notes": "<optional 1-sentence rationale>"
  }}

JSON:"""


def _strip_code_fences(raw: str) -> str:
    """Mirror oracle_parse._strip_code_fences — accept ```json
    and ``` fences from the model."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _parse_response(raw: str) -> SideboardPlan:
    """Convert the model's raw text into a SideboardPlan.

    Robust to the same model quirks as ``oracle_parse``: code
    fences, whitespace, missing fields, non-string entries.

    Drops ``delta=0`` entries silently (no-op swaps clutter the
    plan without changing it).
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

    raw_swaps = data.get("swaps", [])
    if not isinstance(raw_swaps, list):
        raw_swaps = []

    swaps: List[SwapDirective] = []
    for item in raw_swaps:
        if not isinstance(item, dict):
            continue
        card = item.get("card")
        delta = item.get("delta")
        if not isinstance(card, str) or not card.strip():
            continue
        try:
            delta_int = int(delta)
        except (TypeError, ValueError):
            continue
        if delta_int == 0:
            continue
        swaps.append(SwapDirective(card=card.strip(), delta=delta_int))

    notes_raw = data.get("notes")
    notes = notes_raw if isinstance(notes_raw, str) else None

    return SideboardPlan(swaps=swaps, notes=notes)


def advise_sideboard(
    my_deck: str,
    my_sideboard: Dict[str, int],
    opponent_deck: str,
    policy: LLMPolicy,
    max_swaps: int = 7,
) -> SideboardPlan:
    """Generate a sideboard plan for a (my deck, my SB, opp deck)
    tuple. Cache-aware: same matchup → same plan.

    Args:
      my_deck: name of the player's deck (e.g. "Boros Energy").
      my_sideboard: card-name → count of cards available in the SB.
      opponent_deck: name of the opponent's deck.
      policy: configured ``LLMPolicy`` with a backend.
      max_swaps: prompt hint for the model — does NOT enforce a hard
        cap on the parsed result. Callers can post-filter.

    Returns: ``SideboardPlan`` with a list of directives. Plans
    are not guaranteed balanced — callers can check
    ``plan.is_balanced()`` and reject / patch if needed.
    """
    prompt = _build_prompt(my_deck, my_sideboard, opponent_deck,
                            max_swaps=max_swaps)
    response = policy.generate(
        prompt=prompt,
        schema_id=SIDEBOARD_PLAN_SCHEMA_ID,
        parser=_parse_response,
        max_tokens=400,
    )
    return response.parsed


def to_dict(plan: SideboardPlan) -> dict:
    """Convenience: serialize a SideboardPlan to a plain dict for
    JSON dumping (e.g. when building the canonical-plan corpus
    for the acceptance gate)."""
    return asdict(plan)
