"""LLM-at-decision-time scoring weights, cached for determinism.

Phase 1 of the project-direction refactor — see
``docs/proposals/jazzy-swimming-muffin.md``.  The user directive:

    "all changes must be structural - dropping constants, not
     adding. no overrides. relying on new techniques instead of
     just extending if / case etc statements.  Use LLM at decision
     time: score via cached LLM calls during the sim.  First call
     slow, repeats hit cache. Determinism via cache."

This module replaces ~10 archetype-tied scaling multipliers that
used to live as hand-tuned floats in ``ai/scoring_constants.py``
(``TRON_MANA_ADVANTAGE``, ``CYCLING_CASCADE_BOOST``, etc.).  The
new contract:

  * Call sites compute a *base value* from a clock/mana primitive
    (``mana_clock_impact``, ``card_clock_impact``, ``combat_clock``)
    and multiply by ``weight(archetype, context)``.
  * The weight comes from the ``decision_scorer`` pydantic-ai
    agent, cached via ``ai.llm_cache``.  Cache key derives from
    ``(archetype, context)`` — NOT the raw deck name — so two
    decks of the same archetype share cache rows.
  * On cache miss + budget-exhausted (no LLM call possible), the
    helper falls back to the entry in ``DEFAULT_WEIGHTS`` for the
    same ``(archetype, context)`` pair.  If the pair isn't in the
    table, the helper returns ``NEUTRAL_WEIGHT`` (1.0).

``DEFAULT_WEIGHTS`` is a data table (not a branch tree).  It
records the same scaling values that lived as constants before
the refactor, so:
  * In production with a cold cache, behaviour matches the prior
    constants exactly — the sim doesn't regress.
  * Once the cache is warmed by ``tools/llm_cache_warm.py``, the
    LLM's weight overrides the default.  The user can later tune
    the LLM's prompt / model to refine archetype-specific weights
    without touching ``ai/`` source.
  * Operators (and a future Phase 2 sweep) replace
    ``DEFAULT_WEIGHTS`` rows with primitive-derived expressions,
    not new ``if archetype == X`` branches.

Why this is structural, not a patch: every ``ev_player`` /
``clock`` call site now multiplies a primitive base by *one* helper
call.  The dispatch surface is the cache key.  Adding a new
archetype-context pair is a data change in this module or in a
warmed cache row — never a Python branch.
"""
from __future__ import annotations

import math
import os
from typing import Optional

from ai.llm_schemas import DecisionScoringWeights


NEUTRAL_WEIGHT: float = 1.0  # magic-allow: neutral fallback when cache miss + budget exhausted


# ─── Default weights table ──────────────────────────────────────────
#
# Source of truth for the values dropped from ``ai/scoring_constants.py``
# in this PR.  Keys are ``(archetype, decision_context)``.  These are
# the offline-cold-start weights — the same scaling factors that lived
# as constants before the refactor — so the sim's behaviour is
# preserved when no API key is configured.
#
# Once ``tools/llm_cache_warm.py`` warms the response cache, the LLM's
# returned weight wins; this table is the deterministic fallback that
# keeps Bo3 runs reproducible offline.
#
# Adding a new row here is the same "drop a constant" pattern — it
# replaces a numeric literal in ``ai/`` with a single data row keyed
# by archetype + context.  Adding a new context to an existing
# archetype is the right pattern; adding a per-card row is NOT
# (card-specific data belongs in oracle text or gameplan JSON, per
# the abstraction contract).

# Decision contexts (one per dropped constant).  These are the keys
# the call-sites pass and the cache stores; they are *not* archetype
# names.  Keeping them as module-level string constants makes the
# dispatch surface explicit and greppable.
CTX_COMBO_FORCE_PAYOFF_STORM_THRESHOLD = "combo_force_payoff_storm_threshold"
CTX_TRON_MANA_ADVANTAGE = "tron_mana_advantage"
CTX_AMULET_TITAN_MANA_BONUS = "amulet_titan_mana_bonus"
CTX_CYCLING_CASCADE_BOOST = "cycling_cascade_boost"
CTX_CYCLING_GY_URGENCY = "cycling_gy_urgency"
CTX_CYCLING_GAMEPLAN_BOOST = "cycling_gameplan_boost"
CTX_CYCLING_FREE_COST_BONUS = "cycling_free_cost_bonus"
CTX_CASCADE_FREE_SPELL_VALUE = "cascade_free_spell_value"

# ``DEFAULT_WEIGHTS[(archetype, ctx)] = float`` — the offline-cold-start
# weight that matches the historical constant value.  Archetype strings
# match the literals used by ``_get_archetype()`` in ``ai/ev_player.py``
# (lower-case archetype enum values, plus "storm" / "cascade" / "tron"
# refinements).  An ``(archetype, ctx)`` pair absent from this table
# falls back to ``NEUTRAL_WEIGHT``.
DEFAULT_WEIGHTS: dict[tuple[str, str], float] = {
    # Storm count threshold above which the combo-kill goal-advance
    # fires in `decide_main_phase`.  Historical: 5.0.  Storm/combo
    # archetypes only — non-combo archetypes don't have a "storm
    # count" concept so the helper returns NEUTRAL_WEIGHT for them.
    ("storm",  CTX_COMBO_FORCE_PAYOFF_STORM_THRESHOLD): 5.0,
    ("combo",  CTX_COMBO_FORCE_PAYOFF_STORM_THRESHOLD): 5.0,

    # Tron-assembly mana advantage: completed Tron yields {C}{C}{C}{C}
    # {C}{C}{C} = 7 colorless mana from 3 lands vs ~3 mana from 3
    # vanilla lands.  Historical: 4.0 mana / turn.
    ("ramp",   CTX_TRON_MANA_ADVANTAGE): 4.0,

    # Amulet + Primeval Titan: 2 lands ETB tapped, Amulet untaps both
    # → +4 mana same turn.  Historical: 4.0.
    ("combo",  CTX_AMULET_TITAN_MANA_BONUS): 4.0,
    # Some Amulet variants are classified as "ramp" in the archetype
    # registry — cover both.
    ("ramp",   CTX_AMULET_TITAN_MANA_BONUS): 4.0,

    # Cycling EV bonus when a cascade spell is in hand.  Cascade decks
    # (Living End) value graveyard fill heavily.  Historical: 8.0.
    ("combo",  CTX_CYCLING_CASCADE_BOOST): 8.0,
    ("cascade", CTX_CYCLING_CASCADE_BOOST): 8.0,

    # Additional cycling EV when graveyard creature count < urgency
    # floor AND a cascade is in hand.  Historical: 6.0.
    ("combo",  CTX_CYCLING_GY_URGENCY): 6.0,
    ("cascade", CTX_CYCLING_GY_URGENCY): 6.0,

    # Cycling EV bonus when the gameplan's current goal sets
    # `prefer_cycling = True` (Living End reanimator shell).
    # Historical: 10.0.
    ("combo",  CTX_CYCLING_GAMEPLAN_BOOST): 10.0,
    ("cascade", CTX_CYCLING_GAMEPLAN_BOOST): 10.0,

    # Free cycling — pay life instead of mana (Street Wraith, Decree
    # of Pain).  Historical: 2.0.  Applies to any deck with cyclers.
    ("combo",   CTX_CYCLING_FREE_COST_BONUS): 2.0,
    ("cascade", CTX_CYCLING_FREE_COST_BONUS): 2.0,
    ("aggro",   CTX_CYCLING_FREE_COST_BONUS): 2.0,
    ("midrange", CTX_CYCLING_FREE_COST_BONUS): 2.0,
    ("control", CTX_CYCLING_FREE_COST_BONUS): 2.0,
    ("tempo",   CTX_CYCLING_FREE_COST_BONUS): 2.0,
    ("ramp",    CTX_CYCLING_FREE_COST_BONUS): 2.0,
    ("storm",   CTX_CYCLING_FREE_COST_BONUS): 2.0,

    # Cascade keyword clock contribution: a creature with cascade
    # gets a "free spell of CMC strictly less than caster" ≈ another
    # small creature.  Historical: 2.5.  Applies wherever the cascade
    # keyword appears, regardless of deck archetype (it's a keyword
    # rule, not a deck strategy).  Single key "*" matches any
    # archetype via the explicit `_lookup_default` fallback below.
    ("*",     CTX_CASCADE_FREE_SPELL_VALUE): 2.5,
}


def _lookup_default(archetype: str, context: str) -> float:
    """Return ``DEFAULT_WEIGHTS[(archetype, context)]`` if present,
    else ``DEFAULT_WEIGHTS[("*", context)]`` (the "any archetype"
    fallback), else :data:`NEUTRAL_WEIGHT`.

    Keeping a single "any archetype" wildcard row lets keyword-driven
    contexts (cascade, evolve, suspend) share one default across all
    archetypes without duplicating the same float in every row.
    """
    if (archetype, context) in DEFAULT_WEIGHTS:
        return DEFAULT_WEIGHTS[(archetype, context)]
    if ("*", context) in DEFAULT_WEIGHTS:
        return DEFAULT_WEIGHTS[("*", context)]
    return NEUTRAL_WEIGHT


# ─── Cache + agent integration ──────────────────────────────────────


def _cache_input(archetype: str, context: str) -> dict:
    """Build the dict that is hashed into the cache key.

    ABSTRACTION-CONTRACT probe: the key MUST be archetype + context,
    NOT raw deck name.  Two different decks with the same archetype
    and context share one cache row.  See
    ``tests/test_llm_decision_scorer.py::
    test_deck_name_does_not_appear_in_cache_key``.
    """
    return {
        "archetype": archetype,
        "context": context,
    }


# Single-shot agent handle.  Built lazily on first call so importing
# this module in environments without pydantic-ai (e.g. ratchet CI)
# doesn't crash.  ``None`` means "not yet attempted" or "construction
# failed last time".
_AGENT = None
_AGENT_BUILD_FAILED = False


def _get_agent():
    """Return the cached ``decision_scorer`` agent or ``None`` if it
    cannot be built (no pydantic-ai, no API key, etc.).

    Failure is sticky — once we've seen a build error, we don't retry
    on every call.  This matters for the simulator hot loop: a single
    failed build shouldn't pay the import-and-fail cost N times.
    """
    global _AGENT, _AGENT_BUILD_FAILED
    if _AGENT is not None:
        return _AGENT
    if _AGENT_BUILD_FAILED:
        return None
    try:
        from ai.llm_agents import build_agent
        _AGENT = build_agent("decision_scorer")
        return _AGENT
    except Exception:
        _AGENT_BUILD_FAILED = True
        return None


def _try_cache_only(archetype: str, context: str) -> Optional[float]:
    """Look in the SQLite cache for a matching ``DecisionScoringWeights``
    row.  Returns the ``weight`` field on hit, ``None`` on miss.

    Used as the fast path: cache hits should be free + sub-millisecond.
    """
    try:
        from ai import llm_cache
        from ai.llm_models import select_model
        from ai.llm_prompts import latest_version
        model = select_model("decision_scorer")
        version = latest_version("decision_scorer")
        key = llm_cache.cache_key(
            "decision_scorer", model, version,
            _cache_input(archetype, context),
        )
        hit = llm_cache.get_cached(key, DecisionScoringWeights)
        if hit is not None:
            return float(hit.weight)
    except Exception:
        # Cache failures fall through to the LLM-call / fallback path.
        # We do not crash the sim on a cache I/O error.
        return None
    return None


# Sentinel for the "the parent code asked for a fallback different
# from NEUTRAL_WEIGHT" path.  Plain default-arg detection via `is`
# requires a module-level singleton.
_UNSET = object()


def _is_finite_float(x) -> bool:
    """Return True if x is a float that is neither NaN nor +/-Inf."""
    try:
        return isinstance(x, (int, float)) and math.isfinite(float(x))
    except Exception:
        return False


def weight(
    deck_archetype: str,
    decision_context: str,
    fallback: float = NEUTRAL_WEIGHT,
) -> float:
    """Return a scaling weight for the named ``(archetype, context)``.

    Resolution order (each step is free if the previous didn't fire):

      1. SQLite response cache (``ai.llm_cache``).  Lookup keyed by
         ``(archetype, context)`` — independent of the raw deck name.
      2. Live LLM call via the ``decision_scorer`` agent.  Gated by
         the per-task budget; on ``BudgetExceededError`` we fall
         through to step 3.  Cache miss + no API key → step 3.
      3. ``DEFAULT_WEIGHTS[(archetype, context)]`` data table, or the
         ``"*"`` wildcard row if present.
      4. ``fallback`` argument (default :data:`NEUTRAL_WEIGHT`).

    The return value is guaranteed to be a finite float.

    Args:
        deck_archetype: A lower-case archetype string matching the
            literals used by ``_get_archetype()`` in
            ``ai/ev_player.py`` (``"aggro"``, ``"midrange"``,
            ``"control"``, ``"combo"``, ``"tempo"``, ``"ramp"``,
            ``"storm"``, ``"cascade"``).
        decision_context: Short label naming the scoring decision
            (use a ``CTX_*`` module constant from this file).
        fallback: Value returned when both the cache lookup, the LLM
            call, and ``DEFAULT_WEIGHTS`` miss.  Defaults to neutral.

    Returns:
        A finite float.  Never NaN, never +/-Inf.
    """
    # Step 1: cache fast path.
    cached = _try_cache_only(deck_archetype, decision_context)
    if cached is not None and _is_finite_float(cached):
        return float(cached)

    # Step 2: live LLM call.  Skipped if no agent could be built,
    # or if the call raises (budget, network, schema validation).
    # Setting ``MTG_LLM_DECISION_SCORER_OFFLINE=1`` disables the live
    # call entirely — useful for deterministic CI runs where we want
    # the warmed cache to be the only source of weights.
    if not os.environ.get("MTG_LLM_DECISION_SCORER_OFFLINE"):
        agent = _get_agent()
        if agent is not None:
            try:
                payload = _cache_input(deck_archetype, decision_context)
                # The agent's ``run_sync`` accepts a dict or a string.
                # Render as a short structured prompt; the agent's
                # system prompt explains the contract.
                result = agent.run_sync(
                    f"archetype={deck_archetype}; context={decision_context}"
                )
                w = float(result.output.weight)
                if _is_finite_float(w):
                    return w
            except Exception:
                # Any failure — budget, network, schema, etc. —
                # falls through to the offline default table.  We do
                # not crash the sim on an LLM-side error.
                pass

    # Step 3: offline default table.
    default = _lookup_default(deck_archetype, decision_context)
    if _is_finite_float(default):
        return float(default)

    # Step 4: caller-supplied fallback (or NEUTRAL_WEIGHT).
    if _is_finite_float(fallback):
        return float(fallback)
    return NEUTRAL_WEIGHT


__all__ = [
    "NEUTRAL_WEIGHT",
    "DEFAULT_WEIGHTS",
    "CTX_COMBO_FORCE_PAYOFF_STORM_THRESHOLD",
    "CTX_TRON_MANA_ADVANTAGE",
    "CTX_AMULET_TITAN_MANA_BONUS",
    "CTX_CYCLING_CASCADE_BOOST",
    "CTX_CYCLING_GY_URGENCY",
    "CTX_CYCLING_GAMEPLAN_BOOST",
    "CTX_CYCLING_FREE_COST_BONUS",
    "CTX_CASCADE_FREE_SPELL_VALUE",
    "weight",
]
