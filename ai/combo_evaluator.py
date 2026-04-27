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
    """True when the card is something a storm chain wants — rituals,
    cantrips, draw-engines, tutors, or storm closers themselves.

    Mirror of `ai.predicates.is_chain_fuel` extended to include the
    closer (storm-keyword card) and tutors.
    """
    from engine.cards import Keyword as Kw
    t = card.template
    if not (t.is_instant or t.is_sorcery):
        return False
    tags = getattr(t, 'tags', set())
    if any(tag in tags for tag in ('ritual', 'cantrip',
                                    'card_advantage', 'draw', 'tutor')):
        return True
    keywords = getattr(t, 'keywords', set())
    if Kw.STORM in keywords:
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
                       library_size, storm_count, archetype):
    """Run the simulator and extract the chain's card_ids."""
    from ai.finisher_simulator import simulate_finisher_chain
    proj = simulate_finisher_chain(
        snap=snap,
        hand=hand,
        battlefield=battlefield,
        graveyard=graveyard,
        library_size=library_size,
        storm_count=storm_count,
        archetype=archetype,
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
    cache_key = (id(snap), archetype, id(me))
    if cache_key in _BASELINE_CACHE:
        baseline_proj, chain_card_ids = _BASELINE_CACHE[cache_key]
    else:
        baseline_proj, chain_card_ids = _project_baseline(
            snap, list(me.hand), list(me.battlefield),
            list(me.graveyard), library_size, storm_count, archetype,
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
            return STORM_HARD_HOLD + flip_bonus + tax_penalty
        return flip_bonus + tax_penalty

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
        return flip_bonus + tax_penalty

    # ── 5. Chain-fuel credit when chain is reachable AND firing
    #     this turn beats holding ──
    relevance = _chain_relevance(card, chain_card_ids)
    chain_credit = (fire_value / opp_life) * combo_value * relevance

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
        return flip_bonus + tax_penalty

    return chain_credit + flip_bonus + tax_penalty
