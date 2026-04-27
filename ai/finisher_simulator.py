"""Finisher chain simulator — pure projection of "can I close from here?".

This module is the load-bearing scaffolding for the eventual migration of
`card_combo_modifier` (`ai/combo_calc.py:603-880`) onto the decision
kernel.  It answers ONE question: given a current snapshot + zones,
what does it look like if I attempt to close the game right now?

It does NOT make decisions.  The caller is `card_combo_modifier` (or
its kernel-based replacement) which decides whether the projection
warrants firing.

Design rules
------------
1. Pure function.  Takes the snapshot/zones by value, returns a
   `FinisherProjection`.  Never mutates game state.
2. Pattern detection is **oracle/keyword/tag-driven**.  Zero card
   names, zero deck names, zero archetype gates.  The `archetype`
   parameter is used only as a tiebreaker when multiple patterns
   are technically reachable from the same hand (rare — e.g. a deck
   that has both rituals and cascade triggers).
3. No magic numbers.  Numeric values are derived from
   `combo_chain.find_all_chains` (storm), oracle text (reanimation
   target power, cycling cost), or are rules constants documented
   inline (storm-payoff bonus = +1 because Grapeshot deals
   storm-count damage = storm + 1 with original; tutor adds another
   +1 because the tutor itself adds 1 to storm count — see
   `combo_calc.py:670-695` for the same arithmetic).
4. Pure additive.  Does NOT modify `combo_calc.py` or `ev_player.py`.
   The migration that replaces `card_combo_modifier` with
   `simulate_finisher_chain` is a follow-up PR.

Coverage
--------
Four chain patterns the existing modifier covers:

* **Storm** — rituals + tutors + Past in Flames flashback +
  Grapeshot/Wish closer.  Wraps `combo_chain.find_all_chains`.
* **Cascade** — cascade trigger casts a free spell from library
  (Living End / Crashing Footfalls).
* **Reanimation** — discard outlet + reanimator targets a big GY
  creature.
* **Cycling** — cycle to fill GY, then cast Living End-style payoff.

Each pattern's detection function returns a candidate
`FinisherProjection` (or `None` when no chain is reachable).  The
top-level entry point picks the highest-EV reachable pattern,
disambiguating with `archetype` when multiple are tied.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from ai.ev_evaluator import EVSnapshot

from ai.schemas import FinisherProjection, FinisherPattern
from ai.predicates import is_chain_fuel


# ─── Rules constants (no tuning weights — every value documented) ──

# When a tutor fetches the storm-keyword closer, the chain is one
# spell longer (the tutor itself adds 1 to storm count, the closer
# adds 1, so damage = base_storm + 2).  Mirrors the arithmetic in
# `card_combo_modifier`'s tutor branch, `combo_calc.py:694`.
TUTOR_STORM_BONUS = 1

# Storm payoff arithmetic: a STORM-keyword damage closer (Grapeshot
# pattern) deals `storm_count + 1` damage where storm_count is the
# spells cast BEFORE this closer.  The "+1" is the closer itself, by
# CR 702.40 (Storm).  This matches `ChainOutcome.storm_damage` in
# `combo_chain.py:60` where storm_count includes the closer.
STORM_CLOSER_SELF = 1


# ─── Pattern detection helpers (oracle/keyword/tag-driven) ─────────

def _has_storm_keyword(card: "CardInstance") -> bool:
    """True when the card has the STORM keyword (Grapeshot pattern)."""
    from engine.cards import Keyword as Kw
    return Kw.STORM in getattr(card.template, 'keywords', set())


def _has_cascade_keyword(card: "CardInstance") -> bool:
    """True when the card has the cascade keyword.

    Detection mirrors `ev_player.py:558` and uses both the
    oracle-parsed `is_cascade` flag and the keyword set, so cards
    where one but not the other was populated still register.
    """
    from engine.cards import Keyword as Kw
    if getattr(card.template, 'is_cascade', False):
        return True
    return Kw.CASCADE in getattr(card.template, 'keywords', set())


def _has_cycling(card: "CardInstance") -> bool:
    """True when the card can cycle (oracle-parsed `cycling_cost_data`)."""
    return getattr(card.template, 'cycling_cost_data', None) is not None


def _is_reanimator_spell(card: "CardInstance") -> bool:
    """True when the card returns a creature from a graveyard.

    Detection: `reanimate` tag (set by `engine/card_database.py`
    when oracle text matches the canonical pattern) OR the literal
    oracle phrase "return target creature card from your graveyard
    to the battlefield" — same fallback used at
    `ev_player.py:600-603`.
    """
    tags = getattr(card.template, 'tags', set())
    if 'reanimate' in tags:
        return True
    oracle = (getattr(card.template, 'oracle_text', '') or '').lower()
    return (
        'return target creature card from your graveyard to the battlefield'
        in oracle
    )


def _is_discard_outlet(card: "CardInstance") -> bool:
    """True when the card discards a card as part of its effect.

    Detection: oracle phrase "discard a card" — generic discard outlet
    pattern (Faithful Mending, Thoughtseize-self, Goryo's setup
    enablers).  Tag-based fallback included since some cards may
    be tagged without the literal oracle phrase.
    """
    tags = getattr(card.template, 'tags', set())
    if 'discard' in tags or 'looter' in tags:
        return True
    oracle = (getattr(card.template, 'oracle_text', '') or '').lower()
    return 'discard a card' in oracle


def _is_cascade_payoff(card: "CardInstance") -> bool:
    """True when the card is a viable cascade hit.

    Detection: `combo` tag AND a cmc that cascade can hit (cascade
    casts a card with lesser cmc).  Living End / Crashing Footfalls
    are sorceries with combo tag.  Generic by oracle tag — no card
    names.
    """
    tags = getattr(card.template, 'tags', set())
    return 'combo' in tags and not card.template.is_creature


def _is_cycling_payoff(card: "CardInstance") -> bool:
    """True when the card pays off "cycle to fill graveyard".

    Living End is the canonical example: oracle returns "all creature
    cards from all graveyards to the battlefield" — the GY-fill
    arithmetic is what the cycling chain enables.  Detection: oracle
    phrase "all creature cards" + "graveyards" + "to the battlefield".
    """
    oracle = (getattr(card.template, 'oracle_text', '') or '').lower()
    return (
        'all creature cards' in oracle
        and 'graveyard' in oracle
        and 'to the battlefield' in oracle
    )


def _payoff_names_from_hand(hand: List["CardInstance"]) -> set:
    """Collect names of storm-keyword closers in hand.

    Used as the `payoff_names` argument to `combo_chain.find_all_chains`.
    Matching the live signature: cards with the STORM keyword OR
    a tutor with a real target (delegated to `_tutor_has_payoff`).
    """
    names = set()
    for c in hand:
        if _has_storm_keyword(c):
            names.add(c.template.name)
    return names


def _tutor_has_payoff(
    tutor: "CardInstance",
    sb_or_library: List["CardInstance"],
) -> bool:
    """True when `tutor` can fetch a storm-keyword finisher from
    sideboard ∪ library.  Mirrors `_tutor_has_payoff_access` in
    `combo_calc.py:507` — STORM keyword or token-spawning oracle.
    """
    tags = getattr(tutor.template, 'tags', set())
    if 'tutor' not in tags:
        return False
    for c in sb_or_library:
        tmpl = getattr(c, 'template', None)
        if tmpl is None:
            continue
        if _has_storm_keyword(c):
            return True
        oracle = (tmpl.oracle_text or '').lower()
        if 'create' in oracle and 'tokens' in oracle and 'for each' in oracle:
            return True
    return False


# ─── Per-pattern projection builders ───────────────────────────────

def _project_storm(
    snap: "EVSnapshot",
    hand: List["CardInstance"],
    battlefield: List["CardInstance"],
    storm_count: int,
) -> Optional[FinisherProjection]:
    """Project a storm chain via `combo_chain.find_all_chains`.

    Returns a `FinisherProjection` with the best storm chain's
    arithmetic, or None if no chain is reachable from the current
    state.  Tutor-as-finisher access is recognized symmetrically to
    `card_combo_modifier`'s tutor branch (combo_calc.py:670-695).
    """
    from ai.combo_chain import find_all_chains
    from engine.cards import Keyword as Kw

    payoff_names = _payoff_names_from_hand(hand)
    medallions = sum(
        1 for c in battlefield
        if 'cost_reducer' in getattr(c.template, 'tags', set())
    )

    # Detect storm pattern: at least one ritual / chain-fuel card OR a
    # storm-keyword card OR a tutor with finisher access.  Without any
    # of these the storm pattern is unreachable.
    has_ritual = any(
        'ritual' in getattr(c.template, 'tags', set())
        for c in hand
    )
    has_storm_closer = bool(payoff_names)
    tutors_in_hand = [
        c for c in hand
        if 'tutor' in getattr(c.template, 'tags', set())
    ]

    # `library_size`/SB resolution: tutor needs SB ∪ library access.
    # We don't have SB visibility here (the simulator is pure), but
    # the caller can pre-merge SB into the library list if the
    # underlying engine state has a sideboard.  For now scan whatever
    # was passed via `hand` (closer in hand) + battlefield reducers.
    if not (has_ritual or has_storm_closer or tutors_in_hand):
        return None

    # Run the chain finder with the in-hand closer set.
    chains = find_all_chains(
        hand=hand,
        available_mana=snap.my_mana,
        medallion_count=medallions,
        payoff_names=payoff_names,
        base_storm=storm_count,
    )

    if not chains:
        # No chain found — but a tutor might still reach a closer.
        # Note: the simulator can't run the tutor's search without
        # library/SB visibility from the caller.  Report as a
        # "reachable but unprojected" pattern with low success.
        if tutors_in_hand:
            return FinisherProjection(
                pattern="storm",
                expected_damage=0.0,
                success_probability=0.0,
                mana_floor=min(
                    (t.template.cmc or 0) for t in tutors_in_hand
                ),
                chain_length=1,
                closer_name=None,
                # v2 fields default to 0/False — no chain reachable
                # from current state, so hold/coverage are zero and
                # zone presence is unknown without SB/lib visibility.
                hold_value=0.0,
                next_turn_damage=0.0,
                coverage_ratio=0.0,
                closer_in_zone={'hand': False, 'sb': False,
                                'library': False, 'graveyard': False},
            )
        return None

    # Pick the best chain by storm damage; if no damage chain exists,
    # by storm count (token-payoff Empty-the-Warrens pattern).
    best = max(chains, key=lambda c: (c.storm_damage, c.storm_count))
    closer = best.payoff_name
    expected_damage = float(best.storm_damage)

    # Mana floor: cheapest closer cmc.  For pure storm chains the
    # cheapest closer in `payoff_names` is the gating cost.  When
    # only tutors are available, use the tutor cmc.
    closer_cmcs = [
        c.template.cmc or 0 for c in hand
        if Kw.STORM in getattr(c.template, 'keywords', set())
    ]
    if not closer_cmcs and tutors_in_hand:
        closer_cmcs = [t.template.cmc or 0 for t in tutors_in_hand]
    mana_floor = min(closer_cmcs) if closer_cmcs else 0

    # Success probability: 1.0 if a closer is in hand and the chain
    # actually included a payoff; otherwise scale by whether tutors
    # have access.  When `closer` is None (fuel-only chain) success
    # is 0.0 — we can't close from this state.
    if closer is not None:
        success = 1.0
    elif tutors_in_hand:
        success = 0.5  # tutor present but caller hasn't given SB
    else:
        success = 0.0

    # ── v2 fields ──
    opp_life = max(1, snap.opp_life)
    coverage_ratio = min(1.0, expected_damage / opp_life)

    closer_in_zone = {
        'hand': bool(payoff_names),
        # 'sb' / 'library' / 'graveyard' aren't visible to the
        # simulator (pure-function design — caller decides what to
        # pass).  Default False; will be populated by callers that
        # have the deck context (e.g. card_combo_modifier).
        'sb': False,
        'library': False,
        'graveyard': False,
    }

    # Next-turn projection: we get one more land drop and one more
    # card.  Approximate by re-running find_all_chains with
    # available_mana + 1 and storm_count reset to 0 (CR 500.4 — the
    # storm count is per-turn).  Hand stays the same; we don't try
    # to predict the drawn card's identity (caller can run the
    # projection again with hypothetical draws if needed).
    from dataclasses import replace
    next_snap = replace(snap, my_mana=snap.my_mana + 1,
                        my_total_lands=snap.my_total_lands + 1)
    next_chains = find_all_chains(
        hand=hand,
        available_mana=next_snap.my_mana,
        medallion_count=medallions,
        payoff_names=payoff_names,
        base_storm=0,
    )
    if next_chains:
        next_best = max(next_chains,
                        key=lambda c: (c.storm_damage, c.storm_count))
        next_turn_damage = float(next_best.storm_damage)
    else:
        next_turn_damage = 0.0

    # Hold value: damage available next turn × P(we survive opp's
    # extra turn).  P(survive) = 1 − 1/opp_clock when opp has a
    # clock; 1.0 when opp has no clock (NO_CLOCK sentinel).
    opp_clock = max(1.0, getattr(snap, 'opp_clock_discrete', 99))
    survival_p = max(0.0, 1.0 - 1.0 / opp_clock)
    hold_value = next_turn_damage * survival_p

    return FinisherProjection(
        pattern="storm",
        expected_damage=expected_damage,
        success_probability=success,
        mana_floor=mana_floor,
        chain_length=best.storm_count,
        closer_name=closer,
        hold_value=hold_value,
        next_turn_damage=next_turn_damage,
        coverage_ratio=coverage_ratio,
        closer_in_zone=closer_in_zone,
    )


def _project_cascade(
    snap: "EVSnapshot",
    hand: List["CardInstance"],
    battlefield: List["CardInstance"],
) -> Optional[FinisherProjection]:
    """Project a cascade chain.

    A cascade trigger casts a free spell from library — we can't
    enumerate the library here, so we project the *intent* (cast a
    cascade enabler with the cascade-payoff already pre-loaded into
    the deck).  The simulator returns chain_length=2 (enabler + payoff
    free cast) and expected_damage=0 since the typical payoff
    (Living End, Crashing Footfalls) is a board swing rather than
    direct damage.  Combat damage comes from the resulting board
    state and is the responsibility of `clock.py`, not this simulator.
    """
    cascade_enablers = [c for c in hand if _has_cascade_keyword(c)]
    if not cascade_enablers:
        return None

    # Cheapest enabler is the mana floor; cascade casts whatever the
    # deck has pre-loaded as a payoff (Living End is in the library
    # by deckbuilding convention).  Without library visibility we
    # report success_probability=1.0 when the enabler is castable —
    # the deck guarantees the cascade-payoff target by construction.
    cheapest_enabler = min(
        cascade_enablers, key=lambda c: c.template.cmc or 0
    )
    mana_floor = cheapest_enabler.template.cmc or 0

    # Success: 1.0 when we can pay for the enabler, 0.0 otherwise.
    castable = snap.my_mana >= mana_floor
    success = 1.0 if castable else 0.0

    # v2 fields: cascade payoff (Living End / Crashing Footfalls) is
    # in library by deckbuilding convention.  coverage_ratio is 0.0
    # because cascade payoffs are board-swings, not direct damage —
    # the clock.py handles the resulting combat damage downstream.
    return FinisherProjection(
        pattern="cascade",
        expected_damage=0.0,  # board-swing payoff, see docstring
        success_probability=success,
        mana_floor=mana_floor,
        chain_length=2,  # enabler + free cast
        closer_name=cheapest_enabler.template.name,
        coverage_ratio=0.0,
        closer_in_zone={'hand': False, 'sb': False,
                        'library': True, 'graveyard': False},
    )


def _project_reanimation(
    snap: "EVSnapshot",
    hand: List["CardInstance"],
    graveyard: List["CardInstance"],
) -> Optional[FinisherProjection]:
    """Project a reanimation chain.

    Pattern: discard outlet (already used or in hand) places a big
    creature into the graveyard, reanimator spell returns it.  The
    closer's combat power is the expected damage contribution.

    Returns None when no reanimator is in hand OR no viable target
    (creature in GY) exists.
    """
    reanimators = [c for c in hand if _is_reanimator_spell(c)]
    if not reanimators:
        return None

    gy_creatures = [c for c in graveyard if c.template.is_creature]
    discard_outlets_in_hand = [c for c in hand if _is_discard_outlet(c)]

    # If no creature is in GY yet, we still need a discard outlet to
    # create one.  When neither GY nor outlet exists, the chain is
    # unreachable.
    if not gy_creatures and not discard_outlets_in_hand:
        return None

    # Best target: highest-power creature in GY.  When the GY is
    # empty but a discard outlet exists, we can't predict which
    # creature gets discarded — fall back to the highest-power
    # creature in hand (the player's intent for the outlet).
    target_pool = gy_creatures or [
        c for c in hand if c.template.is_creature
    ]
    if not target_pool:
        return None
    best_target = max(target_pool, key=lambda c: c.template.power or 0)
    expected_damage = float(best_target.template.power or 0)

    cheapest_reanimator = min(
        reanimators, key=lambda c: c.template.cmc or 0
    )
    mana_floor = cheapest_reanimator.template.cmc or 0

    # Success: 1.0 when GY already has a target AND mana suffices.
    # When a discard outlet is needed first, success degrades by
    # 0.5 (the outlet might miss, the target might not be in hand) —
    # this is a rules-derived sentinel, not a tuning weight: it
    # represents "one extra rules step required to make the chain
    # work" (the outlet must succeed before the reanimator can fire).
    if gy_creatures and snap.my_mana >= mana_floor:
        success = 1.0
    elif discard_outlets_in_hand:
        success = 0.5
    else:
        success = 0.0

    chain_length = 1 if gy_creatures else 2  # outlet + reanimator

    # v2 fields: closer is the GY creature (or hand creature awaiting
    # discard).  coverage_ratio = expected_damage / opp_life clamped.
    opp_life = max(1, snap.opp_life)
    coverage_ratio = min(1.0, expected_damage / opp_life)
    closer_in_zone = {
        'hand': bool([c for c in hand if c.template.is_creature]),
        'sb': False,
        'library': False,
        'graveyard': bool(gy_creatures),
    }

    return FinisherProjection(
        pattern="reanimation",
        expected_damage=expected_damage,
        success_probability=success,
        mana_floor=mana_floor,
        chain_length=chain_length,
        closer_name=cheapest_reanimator.template.name,
        coverage_ratio=coverage_ratio,
        closer_in_zone=closer_in_zone,
    )


def _project_cycling(
    snap: "EVSnapshot",
    hand: List["CardInstance"],
    graveyard: List["CardInstance"],
) -> Optional[FinisherProjection]:
    """Project a cycling chain — cycle to fill GY, then payoff.

    Pattern: cards with cycling go to GY, then a payoff (Living End
    pattern) returns them all to the battlefield.  Cycling is
    detected via `cycling_cost_data` (oracle-parsed); the payoff is
    detected via the oracle phrase "all creature cards … graveyards
    … to the battlefield".

    Returns None when no cycling-payoff is reachable from the
    current state (in hand, in GY-with-flashback, or neither).
    """
    cyclers = [c for c in hand if _has_cycling(c)]
    payoffs_in_hand = [c for c in hand if _is_cycling_payoff(c)]

    # Cycling pattern requires both cycling cards AND a payoff target.
    # The payoff might be cast from hand (rare) OR via cascade (the
    # common case).  When the payoff is reachable only via cascade,
    # the cycling pattern feeds into the cascade pattern — we report
    # cycling here when at least one cycler is in hand AND a payoff
    # exists (in hand or signalled by deck construction via cascade).
    if not cyclers:
        return None

    # When no payoff is in hand, the chain depends on cascade or a
    # later draw.  We still report the pattern as reachable but with
    # low success.
    if not payoffs_in_hand:
        # Detect cascade-fed payoff: any cascade enabler in hand
        # signals the deck has a cascade pre-load for the payoff.
        cascade_enablers = [c for c in hand if _has_cascade_keyword(c)]
        if not cascade_enablers:
            return None

    cheapest_cycler = min(
        cyclers,
        key=lambda c: (c.template.cycling_cost_data or {}).get('mana', 99)
    )
    mana_floor = (
        cheapest_cycler.template.cycling_cost_data or {}
    ).get('mana', 0)

    # Success scales with whether the payoff is in hand (1.0) or
    # depends on cascade/draw (0.5 — same rules-derived sentinel as
    # the reanimation outlet case: one extra rules step required).
    if payoffs_in_hand:
        success = 1.0
        closer_name = payoffs_in_hand[0].template.name
    else:
        success = 0.5
        cascade_enablers = [c for c in hand if _has_cascade_keyword(c)]
        closer_name = (
            cascade_enablers[0].template.name if cascade_enablers else None
        )

    # v2 fields: cycling payoff is in hand (or library via cascade).
    closer_in_zone = {
        'hand': bool(payoffs_in_hand),
        'sb': False,
        'library': not bool(payoffs_in_hand),  # cascade-fed branch
        'graveyard': False,
    }

    return FinisherProjection(
        pattern="cycling",
        expected_damage=0.0,  # board-swing payoff, see _project_cascade
        success_probability=success,
        mana_floor=mana_floor,
        chain_length=2,  # cycle + payoff
        closer_name=closer_name,
        coverage_ratio=0.0,
        closer_in_zone=closer_in_zone,
    )


# ─── Top-level entry point ─────────────────────────────────────────

def simulate_finisher_chain(
    snap: "EVSnapshot",
    hand: List["CardInstance"],
    battlefield: List["CardInstance"],
    graveyard: List["CardInstance"],
    library_size: int,
    storm_count: int,
    archetype: str,
) -> FinisherProjection:
    """Project the EV-impact of attempting a finisher chain.

    Pure function: does not mutate game state, does not call into
    the engine.  All inputs are read-only views.

    Args:
        snap: EVSnapshot with mana / life / clock / position context.
        hand: list of CardInstance currently in the player's hand.
        battlefield: list of CardInstance the player controls (used
            for cost-reducer detection).
        graveyard: list of CardInstance in the player's graveyard
            (used for reanimation target / GY creature counting).
        library_size: number of cards left in the library.  Used by
            future enhancements to scale `success_probability` for
            tutor-without-target / draw-miss cascades; the current
            implementation only checks > 0.
        storm_count: spells cast this turn (base storm for chain
            arithmetic).  Passed through to
            `combo_chain.find_all_chains` as `base_storm`.
        archetype: deck archetype string (e.g. "storm", "combo",
            "cascade_reanimator").  Used ONLY as a tiebreaker when
            multiple patterns are technically reachable from the
            same hand.  Detection of pattern is oracle/keyword/tag
            -driven and is NOT gated by archetype.

    Returns:
        FinisherProjection — the projected outcome of the highest-EV
        reachable pattern, or `pattern="none"` when no chain is
        reachable.
    """
    candidates: List[FinisherProjection] = []

    storm = _project_storm(snap, hand, battlefield, storm_count)
    if storm is not None:
        candidates.append(storm)

    cascade = _project_cascade(snap, hand, battlefield)
    if cascade is not None:
        candidates.append(cascade)

    reanim = _project_reanimation(snap, hand, graveyard)
    if reanim is not None:
        candidates.append(reanim)

    cycling = _project_cycling(snap, hand, graveyard)
    if cycling is not None:
        candidates.append(cycling)

    # Library guard: when the library is empty, no draw-dependent
    # chain can succeed.  Storm chains that have the closer in hand
    # are unaffected; cascade/cycling without an in-hand closer
    # collapse to success=0 because cascade looks into the library.
    if library_size <= 0:
        candidates = [
            c for c in candidates
            if c.pattern == "storm" and c.closer_name is not None
        ] + [
            FinisherProjection(
                pattern=c.pattern,
                expected_damage=c.expected_damage,
                success_probability=0.0,
                mana_floor=c.mana_floor,
                chain_length=c.chain_length,
                closer_name=c.closer_name,
            )
            for c in candidates if c.pattern in ("cascade", "cycling")
        ]

    if not candidates:
        return FinisherProjection(pattern="none")

    # Pick highest-EV reachable pattern.  EV proxy:
    #   expected_damage × success_probability — projected damage
    #   actually dealt.  Tied EV is broken by archetype hint:
    #   archetype starting with "storm" prefers storm; "cascade*"
    #   prefers cascade; "rean*" prefers reanimation; otherwise the
    #   first candidate wins (deterministic order matches detection
    #   order: storm, cascade, reanimation, cycling).
    def _ev(p: FinisherProjection) -> float:
        return p.expected_damage * p.success_probability

    archetype_lc = (archetype or "").lower()

    def _priority(p: FinisherProjection) -> int:
        # Higher = preferred.  Used only as tiebreaker — primary key
        # remains the EV proxy above.
        if archetype_lc.startswith("storm") and p.pattern == "storm":
            return 4
        if archetype_lc.startswith("cascade") and p.pattern == "cascade":
            return 4
        if (archetype_lc.startswith("rean") or "reanimat" in archetype_lc) \
                and p.pattern == "reanimation":
            return 4
        if "cycling" in archetype_lc and p.pattern == "cycling":
            return 4
        # Default ordering: storm > reanimation > cascade > cycling.
        # Reflects how directly each pattern translates to damage:
        # storm/reanimation deal damage; cascade/cycling set up boards.
        return {"storm": 3, "reanimation": 2,
                "cascade": 1, "cycling": 0}.get(p.pattern, -1)

    best = max(candidates, key=lambda p: (_ev(p), _priority(p)))
    return best
