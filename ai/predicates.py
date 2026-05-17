"""Single-source-of-truth predicates for card evaluation.

Every site in `ai/` that asks "is this card a ritual?" / "is this
card chain-extending fuel?" / "how many lands are in this collection?"
/ "what is this card's first-turn value?" should call into this
module rather than re-implementing the check inline.

Why this exists
---------------
The 2026-04-26 Storm pro-player audit found the SAME fuel-counting
bug copy-pasted in two adjacent functions of `ai/combo_calc.py`
(F2.1 + F2.1b).  Both branches had `count_every_non_storm_spell()`
when they meant `count_chain_extending_spells()`.  Fixing one
branch alone regressed Storm WR by 0.8pp; fixing both together
lifted it +4.0pp.

That class of bug — predicate copy-pasted, drifts independently —
is exactly what this module prevents.  Centralizing the predicates
ensures every call site agrees on what counts as "fuel" / "ritual"
/ "draw engine", and a future bug fixed in one place is fixed
everywhere.

Design rules
------------
1. Each predicate is a pure function of its arguments.  No side
   effects, no game-state mutation.
2. Tag-membership predicates use frozenset constants so the
   "what tags qualify" definition lives in one place.
3. Negative cases are tested explicitly (see
   `tests/test_ai_predicates.py`) — predicates designed only for
   the positive case were the source of multiple audit findings
   (F4.1 "always pay shock", F5.1 "no signal for cost reducer").
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from engine.cards import CardInstance


# ─── Tag set constants ────────────────────────────────────────────

# Spells that EXTEND a combo chain — either by producing more mana
# than they cost (rituals) or by drawing/digging for more spells
# (cantrips, draw, card_advantage).  Cards without one of these
# tags add 1 to storm count if cast but don't enable additional
# spells.  See F2.1+F2.1b in 2026-04-26 audit.
CHAIN_FUEL_TAGS: frozenset[str] = frozenset({
    'ritual', 'cantrip', 'draw', 'card_advantage',
})

# Spells that produce card advantage (draw, dig, exile-and-may-play).
# Subset of CHAIN_FUEL_TAGS — excludes pure mana-positive rituals.
DRAW_ENGINE_TAGS: frozenset[str] = frozenset({
    'cantrip', 'draw', 'card_advantage',
})


# ─── Card-level predicates ────────────────────────────────────────

def is_chain_fuel(card: "CardInstance") -> bool:
    """Card extends a spell chain (mana-positive OR card-positive).

    Returns True when the card has at least one of `CHAIN_FUEL_TAGS`
    set on its template.  Used by the storm-finisher and
    tutor-as-finisher branches in `ai/combo_calc.py` to decide
    whether to hold a finisher for more chain growth or fire it
    now.
    """
    return bool(CHAIN_FUEL_TAGS & getattr(card.template, 'tags', set()))


def is_ritual(card: "CardInstance") -> bool:
    """Card is tagged 'ritual' (mana production).

    Rituals cost N mana and produce M > N mana on resolution
    (Pyretic Ritual: pay 1R, get RRR; Manamorphose: pay 1R, get
    2 of any color).  Detection is tag-driven; the tag is set in
    `engine/card_database.py` based on oracle text patterns.
    """
    return 'ritual' in getattr(card.template, 'tags', set())


def is_draw_engine(card: "CardInstance") -> bool:
    """Card produces card advantage (cantrip / draw / card_advantage).

    Reckless Impulse, Wrenn's Resolve, Glimpse the Impossible,
    Past in Flames, etc.  Excludes pure mana rituals (which are
    chain fuel via `is_chain_fuel` but don't draw cards).
    """
    return bool(DRAW_ENGINE_TAGS & getattr(card.template, 'tags', set()))


def is_storm_payoff(card: "CardInstance") -> bool:
    """Card is a chain-payoff finisher — its effect scales with the
    storm count or its damage/token output ends the chain.

    Detection is tag-driven (`storm_payoff` is set by
    `engine/card_database.py` for every card whose oracle has the
    storm effect type).  This is the chain BOTTLENECK predicate:
    countering one removes the chain's payoff path; holding a
    counter for it is the correct play when chain fuel is on the
    stack with a payoff coming.

    Generic by construction — no card names.  Class size: every
    `storm_payoff`-tagged card in the catalog (Grapeshot, Empty the
    Warrens, Brain Freeze, Tendrils of Agony, and any future Modern
    storm finisher).
    """
    return 'storm_payoff' in getattr(card.template, 'tags', set())


def is_chain_payoff_accessor(card: "CardInstance") -> bool:
    """Card grants access to a chain payoff — direct STORM_PAYOFF or
    a tutor / flashback-combo card that surfaces a payoff from the
    sideboard / graveyard / library.

    A tutor (`Wish`, `Burning Wish`) reaches into SB / library; a
    flashback-combo card (`Past in Flames`) reanimates the chain
    out of the graveyard.  Both are payoff-access bottlenecks — the
    counter that lands on one shuts the chain down regardless of
    fuel density.

    Used by the chain-aware counter triage in `ai/response.py` to
    distinguish a payoff-access cast (counter it) from chain-fuel
    cast (hold).
    """
    tags = getattr(card.template, 'tags', set())
    return ('storm_payoff' in tags
            or 'tutor' in tags
            or ('flashback' in tags and 'combo' in tags))


# ─── Collection-level counts ──────────────────────────────────────

def count_lands(cards: "Iterable[CardInstance]") -> int:
    """Count land cards in a collection (any iterable of CardInstance)."""
    return sum(1 for c in cards if c.template.is_land)


def count_gy_creatures(graveyard: "Iterable[CardInstance]") -> int:
    """Count creature cards in a graveyard slice.

    Used by snapshot computation, gameplan goal evaluation, and
    reanimator readiness checks. Same identical formula was
    duplicated in 3 sites prior to centralization.
    """
    return sum(1 for c in graveyard if c.template.is_creature)


# ─── First-turn value (mulligan land-slack replacement) ─────────────
# Tag families consulted by ``first_turn_value``.  Each frozenset is
# a set of real classifier tags (set in ``engine/card_database.py``
# from oracle text) that signal a particular kind of T1-T2 impact:
# combo payoff, forced discard, cost reduction, interaction, or
# efficient early threats.  ``CHAIN_FUEL_TAGS`` (imported above)
# covers ritual / cantrip / draw / card_advantage.
_PAYOFF_TAGS: frozenset[str] = frozenset({'storm_payoff'})
_DISRUPTION_TAGS: frozenset[str] = frozenset({'discard'})
_REDUCER_TAGS: frozenset[str] = frozenset({'cost_reducer'})
_INTERACTION_TAGS: frozenset[str] = frozenset({'removal', 'counterspell'})
_EARLY_THREAT_TAGS: frozenset[str] = frozenset({'early_play', 'efficient_threat'})


_EARLY_TAG_FAMILIES: tuple[frozenset[str], ...] = (
    CHAIN_FUEL_TAGS, _PAYOFF_TAGS, _DISRUPTION_TAGS,
    _REDUCER_TAGS, _INTERACTION_TAGS, _EARLY_THREAT_TAGS,
)
_ANY_EARLY_TAG: frozenset[str] = frozenset().union(*_EARLY_TAG_FAMILIES)


def first_turn_value(card: "CardInstance",
                     hand_context: dict | None = None) -> int:
    """Per-card T1-T2 value in {0, 1, 2}, derived from template tags
    and primitives (CMC, creature stats). Replaces the flat
    ``mulligan_max_lands + 2`` slack in ``ai/mulligan.py``.

    Banding:
      * 0 — land, OR CMC>2 with no early-impact tag, OR cheap non-
        creature with no early-impact tag (inert in the early game).
      * 2 — 1-drop creature with positive power (prototypical T1
        play that also enables T2 attacks).
      * 1 — every other castable-on-T1-or-T2 play.

    ``hand_context`` is reserved for future cross-card adjustments.
    """
    _ = hand_context  # reserved for hand-aware adjustments
    tmpl = card.template
    if tmpl.is_land:
        return 0
    tags = getattr(tmpl, 'tags', set()) or set()
    cmc = tmpl.cmc or 0
    early_tag_hit = bool(tags & _ANY_EARLY_TAG)
    if cmc > 2 and not early_tag_hit:
        return 0
    if cmc <= 2 and not tmpl.is_creature and not early_tag_hit:
        return 0
    if cmc <= 1 and tmpl.is_creature and (tmpl.power or 0) > 0:
        return 2
    return 1


def hand_first_turn_value(cards: "Iterable[CardInstance]") -> int:
    """Sum of ``first_turn_value`` across a hand slice — used by
    the mulligan land-slack predicate."""
    return sum(first_turn_value(c) for c in cards)
