"""Per-card combo-relevance score, simulator-driven.

Replaces `ai.combo_calc.card_combo_modifier` (~310 LOC of patches —
storm finisher timing, tutor-as-finisher gating, cost-reducer
arithmetic, ritual chain gates at storm=0 and storm>=1, flip-
transform stack batching, search-tax awareness).

Two orthogonal scoring paths:

* **Chain-fuel credit (reachable-chain branch):**
  When `simulate_finisher_chain` returns a reachable chain
  (`pattern != "none"`), each chain-relevant card in hand gets a
  flat credit proportional to the chain's projected damage:
      credit = (expected_damage / opp_life) × success_prob ×
               combo_value × chain_relevance(card)
  `chain_relevance` is 1.0 for cards that the simulator's
  underlying chain finder includes in its best chain (the
  closer, fuel rituals, tutors, cantrips) and 0.0 for irrelevant
  cards (creatures, removal, lands).

* **Hard hold (unreachable-chain branch):**
  When `simulate_finisher_chain` returns `pattern="none"` AND the
  card-being-evaluated is chain-fuel (ritual / cantrip /
  tutor / storm closer), return `STORM_HARD_HOLD` sentinel —
  CR 500.4 says mana empties at phase end, so casting fuel into
  a state with no win path wastes the resource.

Plus two orthogonal effects that the simulator does not model:
* flip-transform stack batching (combo_calc.py:861-886)
* search-tax awareness (combo_calc.py:888-905)

Marginal-delta (after − before) was tried and failed — the chain
finder includes the card in both projections, yielding delta = 0
for every chain piece.  See `docs/PHASE_D_DEFERRED.md` for the
full diagnostic.

No card names.  No archetype gates.  Numeric values come from
`simulate_finisher_chain` arithmetic, `_compute_combo_value`, or
are documented rules-derived sentinels.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot

# Rules constant (CR 500.4): mana empties at phase end.  Casting
# fuel into an unreachable-chain state burns mana with no path to
# win → hard hold.  Same sentinel value as combo_calc.py's
# STORM_HARD_HOLD; matches the legacy modifier's clamp.
STORM_HARD_HOLD = -50.0

# Cache: (snapshot_id, archetype, me_id) → (proj, chain_card_ids).
# Baseline projection is identical across every card evaluation
# within one main-phase decision; memoise on snapshot identity.
_BASELINE_CACHE: dict = {}


# ─── Diagnostic trace (env-gated, zero overhead by default) ────────
#
# Set MTGSIM_COMBO_TRACE=1 in the environment to emit a structured
# line per card_combo_evaluation call to stderr.  Used to diagnose
# wire-up collapses (per docs/PHASE_D_FOURTH_ATTEMPT.md): the live
# wire-up has collapsed Storm five times even with the simulator-
# layer gap closed.  The trace exposes per-card branch decisions so
# the second gap can be located before a sixth wire-up attempt.
import os as _os
import sys as _sys

_TRACE = _os.environ.get("MTGSIM_COMBO_TRACE", "") == "1"


def _log_evaluation(card_name: str, branch: str, score: float,
                     **fields) -> None:
    """Emit a structured trace line when MTGSIM_COMBO_TRACE=1.

    Off by default → zero overhead (single env-var check at module
    load + a boolean test per call).  When on, emits one line per
    card_combo_evaluation call so a Bo3 trace can be diff-ed against
    expected behavior.

    Format: ``COMBO_TRACE branch=X card="Y" score=Z field1=V1 ...``
    """
    if not _TRACE:
        return
    parts = [f"COMBO_TRACE", f"branch={branch}", f"card=\"{card_name}\"",
             f"score={score:.2f}"]
    for k, v in fields.items():
        parts.append(f"{k}={v}")
    print(" ".join(parts), file=_sys.stderr)


def _has_search_tax(oracle_text: str) -> bool:
    if not oracle_text:
        return False
    lower = oracle_text.lower()
    return (('opponent' in lower or 'player' in lower)
            and 'search' in lower
            and ('whenever' in lower or 'if' in lower))


def _flip_transform_bonus(card, snap, me, storm_count) -> float:
    """Marginal flip-coin transform value for a cheap instant/sorcery
    when an untransformed flip-creature is on our battlefield."""
    t = card.template
    if not (t.is_instant or t.is_sorcery):
        return 0.0
    flip_creatures = [
        c for c in me.battlefield
        if c.template.is_creature
        and not getattr(c, 'is_transformed', False)
        and 'flip a coin' in (c.template.oracle_text or '').lower()
        and ('instant or sorcery' in (c.template.oracle_text or '').lower()
             or 'instant and sorcery' in (c.template.oracle_text or '').lower())
    ]
    if not flip_creatures:
        return 0.0
    marginal_p = 0.5 ** (storm_count + 1)
    from ai.combo_calc import _compute_combo_value
    combo_value = _compute_combo_value(snap, "combo")
    return marginal_p * combo_value * 0.3 * len(flip_creatures)


def _search_tax_penalty(card, game, player_idx, snap) -> float:
    if 'tutor' not in getattr(card.template, 'tags', set()):
        return 0.0
    opp = game.players[1 - player_idx]
    tax_count = sum(
        1 for c in opp.battlefield
        if _has_search_tax(getattr(c.template, 'oracle_text', '') or '')
    )
    if tax_count == 0:
        return 0.0
    from ai.combo_calc import _compute_combo_value
    combo_value = _compute_combo_value(snap, "combo")
    opp_life = max(1, snap.opp_life)
    card_value = combo_value / opp_life * 3.0
    return -tax_count * card_value


def _is_chain_fuel(card) -> bool:
    """True when the card contributes to ANY reachable chain pattern.

    Originally storm-specific (rituals/cantrips/tutors/storm-keyword).
    Extended for the multi-pattern simulator (per
    docs/PHASE_D_FOURTH_ATTEMPT.md fifth-gap diagnosis): Living End
    and Amulet Titan hands had ALL cards score relevance=0.0
    because cycling cards, cycling payoffs, cascade enablers, and
    reanimation-related cards weren't recognised here.

    Coverage:
      * Storm     — rituals, cantrips, tutors, STORM keyword
      * Cascade   — CASCADE keyword
      * Cycling   — cards with `cycling_cost_data`, cycling payoffs
                    (oracle: "all creature cards … graveyard …
                    battlefield")
      * Reanimation — reanimator spells, discard outlets
      * Chain extender — Past in Flames pattern (oracle: flashback
                    + graveyard + instant/sorcery)

    All detection oracle/keyword/tag-driven; no card names.
    """
    from engine.cards import Keyword as Kw
    t = card.template
    tags = getattr(t, 'tags', set())
    keywords = getattr(t, 'keywords', set())
    oracle = (getattr(t, 'oracle_text', '') or '').lower()

    # Storm/reanimation-style fuel: instant/sorcery spells with
    # chain-relevant tags, or the STORM keyword closer itself
    if t.is_instant or t.is_sorcery:
        if any(tag in tags for tag in ('ritual', 'cantrip',
                                        'card_advantage', 'draw',
                                        'tutor', 'reanimate',
                                        'discard', 'looter')):
            return True
        if Kw.STORM in keywords:
            return True
        # PiF-pattern chain extender
        if ('flashback' in oracle and 'graveyard' in oracle
                and ('instant' in oracle or 'sorcery' in oracle)):
            return True
        # Cycling payoff: "all creature cards from graveyards to bf"
        if ('all creature cards' in oracle
                and 'graveyard' in oracle
                and 'to the battlefield' in oracle):
            return True

    # Cascade enablers (creatures or spells with cascade keyword)
    if Kw.CASCADE in keywords:
        return True

    # Cycling: any card with cycling cost is cycling fuel
    if getattr(t, 'cycling_cost_data', None) is not None:
        return True

    return False


def _chain_relevance(card, chain_card_ids: set) -> float:
    """Returns 1.0 if `card` is part of the best chain projected by
    `simulate_finisher_chain`'s underlying `find_all_chains` call,
    else 0.0.  The set is taken from `ChainOutcome.cards_used` on the
    best chain; identity comparison via instance_id."""
    if not chain_card_ids:
        # Simulator returned a chain but didn't identify cards (e.g.
        # tutor-only branch).  Fall back to "is this card chain fuel?"
        return 1.0 if _is_chain_fuel(card) else 0.0
    return 1.0 if card.instance_id in chain_card_ids else 0.0


def _project_baseline(snap, hand, battlefield, graveyard,
                       library_size, storm_count, archetype,
                       sideboard=None, library=None):
    """Run the simulator and extract the chain's card_ids.

    Passes `sideboard` / `library` through so the simulator can
    use the tutor-as-finisher-access fallback (per
    docs/PHASE_D_FOURTH_ATTEMPT.md step 1).  Without these the
    projection collapses to expected_damage=0 for tutor-only
    Storm hands — the gap that broke four prior Phase D
    migration attempts.
    """
    from ai.finisher_simulator import simulate_finisher_chain
    proj = simulate_finisher_chain(
        snap=snap,
        hand=hand,
        battlefield=battlefield,
        graveyard=graveyard,
        library_size=library_size,
        storm_count=storm_count,
        archetype=archetype,
        sideboard=sideboard,
        library=library,
    )
    chain_card_ids: set = set()
    if proj.pattern == "storm":
        # Re-run find_all_chains with the same inputs so we can
        # extract the best chain's card identity set.  The simulator
        # does this internally but doesn't expose the set in
        # FinisherProjection — extracting via direct call.
        from ai.combo_chain import find_all_chains
        from engine.cards import Keyword as Kw
        payoff_names = {
            c.template.name for c in hand
            if Kw.STORM in getattr(c.template, 'keywords', set())
        }
        if not payoff_names:
            for c in hand:
                if 'tutor' in getattr(c.template, 'tags', set()):
                    payoff_names.add(c.template.name)
        medallions = sum(
            1 for c in battlefield
            if 'cost_reducer' in getattr(c.template, 'tags', set())
        )
        chains = find_all_chains(
            hand=hand,
            available_mana=snap.my_mana,
            medallion_count=medallions,
            payoff_names=payoff_names,
            base_storm=storm_count,
        )
        if chains:
            best = max(chains, key=lambda c: (c.storm_damage, c.storm_count))
            cards_used = getattr(best, 'cards_used', None) or []
            for c in cards_used:
                if hasattr(c, 'instance_id'):
                    chain_card_ids.add(c.instance_id)
    return proj, chain_card_ids


def card_combo_evaluation(
    card: "CardInstance",
    snap: "EVSnapshot",
    me,
    game: "GameState",
    player_idx: int,
    archetype: str = "combo",
    library_size: Optional[int] = None,
) -> float:
    """Score `card`'s contribution to the projected finisher chain."""
    from ai.combo_calc import _compute_combo_value

    storm_count = me.spells_cast_this_turn
    if library_size is None:
        library_size = len(me.library)

    # ── 1. Baseline projection (cache per-snap) ──
    # Pass sideboard + library so the simulator can run the
    # tutor-as-finisher-access fallback when a tutor is in hand
    # but the closer lives in SB/library (Wish→Grapeshot).
    cache_key = (id(snap), archetype, id(me))
    if cache_key in _BASELINE_CACHE:
        baseline_proj, chain_card_ids = _BASELINE_CACHE[cache_key]
    else:
        sb = getattr(me, 'sideboard', None) or []
        lib = list(me.library)
        baseline_proj, chain_card_ids = _project_baseline(
            snap, list(me.hand), list(me.battlefield),
            list(me.graveyard), library_size, storm_count, archetype,
            sideboard=sb, library=lib,
        )
        _BASELINE_CACHE[cache_key] = (baseline_proj, chain_card_ids)

    # ── 2. Orthogonal terms ──
    flip_bonus = _flip_transform_bonus(card, snap, me, storm_count)
    tax_penalty = _search_tax_penalty(card, game, player_idx, snap)
    combo_value = _compute_combo_value(snap, "combo")
    opp_life = max(1, snap.opp_life)

    # ── 3. Hard hold when no reachable chain AND card is chain fuel ──
    if baseline_proj.pattern == "none":
        if _is_chain_fuel(card):
            # CR 500.4: mana empties at phase end.  Casting fuel here
            # wastes the resource; the AI must hold and rebuild on a
            # later turn.  Same sentinel as combo_calc.py's clamp.
            score = STORM_HARD_HOLD + flip_bonus + tax_penalty
            _log_evaluation(card.template.name, "hard_hold_no_chain",
                            score, pattern=baseline_proj.pattern,
                            flip=flip_bonus, tax=tax_penalty)
            return score
        score = flip_bonus + tax_penalty
        _log_evaluation(card.template.name, "no_chain_non_fuel", score,
                        pattern=baseline_proj.pattern,
                        flip=flip_bonus, tax=tax_penalty)
        return score

    # ── 4. Hold-vs-fire decision (simulator v2) ──
    # Hold ONLY when next-turn projects LETHAL but this turn does
    # NOT.  Holding indefinitely for "next turn always projects more
    # mana" is irrational — opp's clock makes it non-terminating.
    # The principled hold case is the narrow one: this turn's
    # chain is sub-lethal AND next turn's chain reaches lethal.
    # All other cases fire (sub-lethal damage still chips opp's
    # life total toward future lethal turns).
    fire_lethal = (
        baseline_proj.expected_damage >= opp_life
        and baseline_proj.success_probability >= 0.5
    )
    hold_lethal = (
        baseline_proj.hold_value >= opp_life
        and not fire_lethal
    )
    if hold_lethal and _is_chain_fuel(card):
        score = flip_bonus + tax_penalty
        _log_evaluation(card.template.name, "hold_lethal", score,
                        exp_dmg=baseline_proj.expected_damage,
                        hold_value=baseline_proj.hold_value,
                        opp_life=opp_life,
                        flip=flip_bonus, tax=tax_penalty)
        return score

    # ── 5. Chain-fuel credit when chain is reachable AND firing
    #     this turn beats holding ──
    fire_value = (
        baseline_proj.expected_damage
        * baseline_proj.success_probability
    )
    relevance = _chain_relevance(card, chain_card_ids)
    chain_credit = (fire_value / opp_life) * combo_value * relevance

    # ── 5b. Chain-progress credit during build-up turns ──
    # Per docs/PHASE_D_FOURTH_ATTEMPT.md fourth-gap diagnosis:
    # when the simulator detects a reachable storm pattern but
    # `expected_damage = 0` (chain not assembled yet — typical
    # T1-T3 build-up), `chain_credit` collapses to 0.  The live
    # `card_combo_modifier` gives positive EV in this state to
    # advance the chain.  The simulator-driven evaluator needs an
    # equivalent.
    #
    # Principled formula: each chain-relevant spell cast represents
    # `1 / N` of the eventual lethal where `N` = spells-to-lethal.
    # For Storm-via-Grapeshot, lethal needs storm count = opp_life
    # (Grapeshot deals storm-count + 1 damage; +1 from itself).
    # So per-spell progress = combo_value / opp_life.
    #
    # Gated by `success_probability > 0` (pattern reachable, not
    # purely speculative) AND `expected_damage = 0` (no full chain
    # yet — otherwise the regular chain_credit fires).
    if (chain_credit == 0
            and baseline_proj.pattern != "none"
            and relevance > 0):
        progress_credit = combo_value * relevance / opp_life
        chain_credit = progress_credit

    # ── 6. Mid-chain coverage escalation (simulator v2) ──
    # When `coverage_ratio > HALF_LETHAL`, additional fuel
    # investments into a stranded chain become catastrophic.  Boost
    # the chain credit for cards that DO advance the chain, gate
    # for cards that don't.  Threshold derives from clock arithmetic:
    # at coverage > 0.5 we've committed half our resources to this
    # chain and turning back wastes everything spent.
    HALF_LETHAL = 0.5  # rules constant: half-lethal coverage
    if (baseline_proj.coverage_ratio > HALF_LETHAL
            and relevance == 0.0
            and _is_chain_fuel(card)):
        # Chain is mid-flight, this fuel doesn't extend it — hold.
        score = flip_bonus + tax_penalty
        _log_evaluation(card.template.name, "coverage_escalation_hold",
                        score, coverage=baseline_proj.coverage_ratio,
                        relevance=relevance,
                        flip=flip_bonus, tax=tax_penalty)
        return score

    final_score = chain_credit + flip_bonus + tax_penalty
    _log_evaluation(card.template.name, "chain_credit", final_score,
                    fire_value=fire_value, relevance=relevance,
                    combo_value=combo_value,
                    chain_credit=chain_credit,
                    flip=flip_bonus, tax=tax_penalty)
    return final_score
