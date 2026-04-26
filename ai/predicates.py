"""Single-source-of-truth predicates for card evaluation.

Every site in `ai/` that asks "is this card a ritual?" / "is this
card chain-extending fuel?" / "how many lands are in this collection?"
should call into this module rather than re-implementing the check
inline.

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
