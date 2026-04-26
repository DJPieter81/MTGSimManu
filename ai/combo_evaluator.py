"""Per-card combo-relevance score, simulator-driven.

Replaces `ai.combo_calc.card_combo_modifier` (~310 LOC of patches —
storm finisher timing, tutor-as-finisher gating, cost-reducer
arithmetic, ritual chain gates at storm=0 and storm>=1, flip-
transform stack batching, search-tax awareness) with a uniform
projection-delta:

  ΔEV(card) = (EV_after − EV_before) × combo_value

where `EV_*` is `simulate_finisher_chain(...)`'s `expected_damage ×
success_probability / opp_life` from the relevant zone.  All chain
arithmetic — storm count, mana production, cost reducers, tutor
finisher access, GY-fueled PiF flashback — is encapsulated in
`simulate_finisher_chain`.  This module computes the per-card
delta and stitches in two orthogonal effects (flip-transform
batching, search-tax) that the simulator does not model.

No card names.  No archetype gates.  No magic numbers — values
derive from `simulate_finisher_chain`, `_compute_combo_value`,
or are documented rules constants.
"""
from __future__ import annotations
from dataclasses import replace
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot
    from ai.gameplan import GoalEngine


# Cache: (snapshot_id, archetype) → FinisherProjection.  Baseline
# projection is identical across every card evaluation within one
# main-phase decision, so memoise on the snapshot's identity.
_BASELINE_CACHE: dict = {}


def _has_search_tax(oracle_text: str) -> bool:
    """Whenever-an-opponent/player-searches punisher detection."""
    if not oracle_text:
        return False
    lower = oracle_text.lower()
    return (('opponent' in lower or 'player' in lower)
            and 'search' in lower
            and ('whenever' in lower or 'if' in lower))


def _flip_transform_bonus(card, snap, me, storm_count) -> float:
    """Marginal flip-coin transform value for a cheap instant/sorcery
    when an untransformed flip-creature is on our battlefield.

    P(transform on THIS spell) = 0.5^(storm+1) (the marginal
    probability of the FIRST successful flip on this cast given
    storm prior tries).  Value scales with combo_value (the win-
    fraction this transform unlocks).  Same arithmetic as the
    legacy `card_combo_modifier` flip-transform branch.
    """
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
    # Marginal probability of flipping THIS cast (vs storm prior).
    marginal_p = 0.5 ** (storm_count + 1)
    # Combo-value × 0.3 — the transform unlocks ~30% of a combo win
    # (planeswalker flip is potent but not lethal alone).  Same
    # constant as combo_calc.py:885; rules-derived sentinel, not a
    # tuning weight.
    from ai.combo_calc import _compute_combo_value
    combo_value = _compute_combo_value(snap, "combo")
    return marginal_p * combo_value * 0.3 * len(flip_creatures)


def _search_tax_penalty(card, game, player_idx, snap) -> float:
    """Tutor penalty when opp has 'whenever a player searches'
    permanents (Aven Mindcensor pattern).  Each tax permanent draws
    opp a card (or worse) — penalty scales with card value × count.
    """
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
    # card_value = combo_value / opp_life × 3.0 — same scaling as the
    # legacy `card_combo_modifier` search-tax branch.  3.0 is the
    # rules-derived "average impact of a card on the chain", not a
    # tuning weight.
    card_value = combo_value / opp_life * 3.0
    return -tax_count * card_value


def _project(snap, hand, battlefield, graveyard, library_size,
              storm_count, archetype):
    """Wrapper that calls `simulate_finisher_chain` and converts the
    projection into a single EV scalar normalised by opp_life.
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
    )
    if proj.pattern == "none":
        return 0.0, proj
    opp_life = max(1, snap.opp_life)
    ev = proj.expected_damage * proj.success_probability / opp_life
    return ev, proj


def card_combo_evaluation(
    card: "CardInstance",
    snap: "EVSnapshot",
    me,
    game: "GameState",
    player_idx: int,
    archetype: str = "combo",
    library_size: Optional[int] = None,
) -> float:
    """Score `card`'s contribution to the projected finisher chain.

    Workflow:
      1.  Run baseline `simulate_finisher_chain` on the current
          state (cached per-snapshot).
      2.  If no chain is reachable, return orthogonal effects only
          (flip-transform / search-tax).
      3.  Project post-cast state (hand minus this card, GY plus
          this card if instant/sorcery, battlefield plus this card
          if a permanent, mana minus this card's CMC, storm + 1).
      4.  Return (after_ev − before_ev) × combo_value plus the
          orthogonal flip-transform / search-tax terms.

    All chain-relevance scoring is `simulate_finisher_chain`-driven;
    the per-card delta is the only thing this function adds.
    """
    from ai.combo_calc import _compute_combo_value

    storm_count = me.spells_cast_this_turn
    if library_size is None:
        library_size = len(me.library)

    # ── 1. Baseline projection (cache per-snap) ──
    cache_key = (id(snap), archetype, id(me))
    if cache_key in _BASELINE_CACHE:
        baseline_ev, baseline_proj = _BASELINE_CACHE[cache_key]
    else:
        baseline_ev, baseline_proj = _project(
            snap, list(me.hand), list(me.battlefield),
            list(me.graveyard), library_size, storm_count, archetype,
        )
        _BASELINE_CACHE[cache_key] = (baseline_ev, baseline_proj)

    # ── 2. Orthogonal terms ──
    flip_bonus = _flip_transform_bonus(card, snap, me, storm_count)
    tax_penalty = _search_tax_penalty(card, game, player_idx, snap)

    # ── 3. If no chain reachable, only orthogonal terms apply ──
    if baseline_proj.pattern == "none":
        return flip_bonus + tax_penalty

    # ── 4. Post-cast projection ──
    from engine.cards import CardType
    t = card.template
    is_spell = bool(t.is_instant or t.is_sorcery)
    types = t.card_types or []
    is_permanent = (
        t.is_creature
        or CardType.ARTIFACT in types
        or CardType.ENCHANTMENT in types
        or CardType.PLANESWALKER in types
    )

    new_hand = [c for c in me.hand if c.instance_id != card.instance_id]
    new_gy = list(me.graveyard) + ([card] if is_spell else [])
    new_bf = list(me.battlefield) + ([card] if (is_permanent and not is_spell) else [])
    new_mana = max(0, snap.my_mana - (t.cmc or 0))
    # Snapshot delta — only mana changes for the projection (other
    # snapshot fields don't affect chain math).
    new_snap = replace(snap, my_mana=new_mana)
    new_storm = storm_count + 1

    after_ev, after_proj = _project(
        new_snap, new_hand, new_bf, new_gy, library_size,
        new_storm, archetype,
    )

    # ── 5. Marginal value × combo_value scale ──
    delta_ev = after_ev - baseline_ev
    combo_value = _compute_combo_value(snap, "combo")
    chain_score = delta_ev * combo_value

    # ── 5b. Wasted-cast penalty ──
    # When the post-cast state STILL has no chain reachable AND we
    # just spent mana, the cast wasted resources without advancing
    # the win condition.  Penalty = mana_spent / opp_life ×
    # combo_value, mirroring the "rituals empty at phase end"
    # arithmetic from the legacy modifier.  Engine-rules-derived
    # (CR 500.4 mana pool empties); not a tuning weight.
    if after_proj.pattern == "none" and (t.cmc or 0) > 0:
        opp_life = max(1, snap.opp_life)
        wasted_penalty = (t.cmc or 0) / opp_life * combo_value
        chain_score -= wasted_penalty

    return chain_score + flip_bonus + tax_penalty
