"""Compatibility re-export of the synth-gameplan schemas.

The schemas now live in `ai.llm_schemas` (Phase H consolidated every
LLM-driven tool's schema there).  This module continues to import the
same names so existing callers keep working without churn:

    from ai.gameplan_schemas import SynthesizedGameplan, SynthesizedGoal, to_json_dict

New code should import from `ai.llm_schemas` directly.  This shim is
kept indefinitely — it costs nothing and avoids rewrite churn for any
downstream tool that pinned to the old import path."""
from ai.llm_schemas import (
    GoalTypeStr,
    SynthesizedGameplan,
    SynthesizedGoal,
    to_json_dict,
)

__all__ = [
    "GoalTypeStr",
    "SynthesizedGameplan",
    "SynthesizedGoal",
    "to_json_dict",
]
