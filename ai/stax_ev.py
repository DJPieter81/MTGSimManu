"""Oracle-driven EV valuation for stax permanents.

Scores the expected disruption value of lock pieces (Chalice of the Void,
Blood Moon, Ethersworn Canonist / Rule of Law, Torpor Orb, etc.) based on
opponent deck composition.

Why this exists
---------------
Before this module, `_score_spell` in ev_player.py had no positive EV signal
for stax permanents — only a redundancy penalty for duplicate Chalices.
As a result, the AI treated Chalice as a generic 2-mana artifact. That's
wrong: a well-timed Chalice @ X=1 on the play vs Boros Energy locks ~12
one-drops for multiple turns.

Design
------
Pure function. Oracle-text pattern matching — no hardcoded card names.
The same dispatch covers Chalice, Blood Moon, Ethersworn Canonist, Rule
of Law, Torpor Orb, Cursed Totem. Any new stax card whose oracle matches
an existing pattern is valued automatically.

All value formulas are intentionally conservative. Tests validate sign
and rough magnitude, not precise calibration.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Callable

from ai.scoring_constants import (
    BLOOD_MOON_DISRUPTION_CAP,
    BLOOD_MOON_DISRUPTION_COEFFICIENT,
    CANONIST_DENSITY_FLOOR,
    CANONIST_DISRUPTION_COEFFICIENT,
    CANONIST_DISRUPTION_TURN_COUNT,
    CHALICE_PRACTICAL_X_CEIL,
    CLOCK_IMPACT_LIFE_SCALING,
    STAX_LOCK_DECAY_BURNOUT_TURN,
    STAX_TURN_DECAY_PER_TURN,
    TORPOR_ORB_ETB_DENSITY_FLOOR,
    TORPOR_ORB_PER_ETB_VALUE,
)

if TYPE_CHECKING:
    from engine.cards import CardTemplate
    from engine.game_state import PlayerState
    from ai.ev_evaluator import EVSnapshot


# ──────────────────────────────────────────────────────────────────────
# Expected lifetime constants (turns in play before removal)
# ──────────────────────────────────────────────────────────────────────
# A perfect model would derive these from opponent's removal density.
# These conservative defaults differentiate by card type. Calibrated
# against matchup sims: 3.0 overvalued Chalice and caused the AI to
# tap out on T2 vs aggro. 2.5 is closer to real Modern artifact-hate
# pressure (Wear // Tear SB, Haywire Mite in Affinity, etc.).
ARTIFACT_EXPECTED_LIFETIME = 2.5
ENCHANTMENT_EXPECTED_LIFETIME = 2.5
CREATURE_EXPECTED_LIFETIME = 2.0

# Universal discount: opp plays around lock pieces (holds spells, casts
# non-locked modes, dies with cards in hand). Only ~50% of the theoretical
# lock materialises.
REALISM_DISCOUNT = 0.5

# Cap on the "net spells locked" count. A Chalice that theoretically
# locks 14 one-drops in opp's library doesn't actually lock 14 — opp
# draws ~15 cards total over the lock's lifetime, many are higher CMC,
# and some are lands. Empirically, ~6 is the ceiling for real in-game
# lock count. Uncapped values caused the AI to over-prioritise Chalice
# over mana-efficient interaction (tap-out-on-T2-vs-Boros bug).
MAX_NET_LOCK = 6


def _turn_decay(turn_number: int) -> float:
    """Decay factor for stax lock value as the game progresses.

    Chalice's lock stops *future* casts of a given CMC. On T1-T2 most of
    opp's low-CMC spells are still in hand/library, so the lock bites.
    By T5+ opp has already resolved their one-drops and two-drops; a
    Chalice cast then only catches topdecks, which is marginal value.

    Observed trace (v1 vs Boros, post-overlay): casting Chalice on T5
    stole the Wrath slot from the sweeper plan and cost tempo. The
    overlay should not crowd out mid-game interaction.

    Curve: 1.0 on T1, 0.75 on T2, 0.5 on T3, 0.25 on T4, 0.0 from T5.
    """
    if turn_number <= 1:
        return 1.0
    if turn_number >= STAX_LOCK_DECAY_BURNOUT_TURN:
        return 0.0
    return max(0.0, 1.0 - STAX_TURN_DECAY_PER_TURN * (turn_number - 1))


# ──────────────────────────────────────────────────────────────────────
# Classification (oracle-driven dispatch)
# ──────────────────────────────────────────────────────────────────────

def classify_stax(template: 'CardTemplate') -> Optional[str]:
    """Return stax family name, or None if template isn't a stax permanent.

    Returns one of: 'chalice', 'blood_moon', 'canonist', 'torpor_orb', None.
    """
    oracle = (template.oracle_text or '').lower()

    # Chalice family: triggered ability counters spells with mana value
    # equal to the permanent's charge counters.
    # Real oracle: "Whenever a player casts a spell with mana value equal
    # to the number of charge counters on this artifact, counter that spell."
    if ('charge counter' in oracle
            and 'mana value' in oracle
            and ('counter that spell' in oracle or 'counter it' in oracle)):
        return 'chalice'

    # Blood Moon family: nonbasic lands become basics of one type.
    # Oracle: "Nonbasic lands are Mountains." (or Islands, Plains, etc.)
    if 'nonbasic lands are' in oracle:
        for basic in ('mountain', 'island', 'plains', 'swamp', 'forest'):
            if basic in oracle:
                return 'blood_moon'

    # Canonist / Rule of Law family: hard limit on spells per turn.
    # Canonist real oracle: "Each player who has cast a nonartifact spell
    # this turn can't cast additional nonartifact spells."
    # Rule of Law real oracle: "Each player can't cast more than one
    # spell each turn."
    # Both are one-spell-per-turn effects; unify via two patterns.
    if (("can't cast more than one" in oracle and 'each turn' in oracle)
            or ("can't cast additional" in oracle)):
        return 'canonist'

    # Torpor Orb / Cursed Totem family: ETB triggers don't trigger.
    # Oracle: "Creatures entering the battlefield don't cause abilities to trigger."
    if ('entering' in oracle
            and 'abilities' in oracle
            and ("don't cause" in oracle or "don't trigger" in oracle)):
        return 'torpor_orb'

    return None


# ──────────────────────────────────────────────────────────────────────
# Per-family valuators
# ──────────────────────────────────────────────────────────────────────

def _count_nonland_cmcs(zone) -> dict[int, int]:
    """Count CMCs of non-land cards in a zone."""
    out: dict[int, int] = {}
    for c in zone:
        if c.template.is_land:
            continue
        cmc = c.template.cmc or 0
        out[cmc] = out.get(cmc, 0) + 1
    return out


def _chalice_lock_ev(template, me, opp, snap) -> float:
    """Chalice-of-the-Void family: counter spells of CMC = X.

    Picks best X by max net lock (opp_at_X − my_at_X); mirrors the X-choice
    logic already in engine/game_state.py:1557 so the AI's valuation and
    the engine's actual X-selection agree.

    Value = net_spells_locked × card_clock_impact × lifetime × realism.
    """
    from ai.clock import card_clock_impact

    # Opp library is full-information in the sim (same assumption the
    # engine's X-chooser uses at game_state.py:1557).
    opp_cmcs = _count_nonland_cmcs(opp.library)
    # Our side: library + hand, minus this card itself if it's in hand.
    my_cmcs: dict[int, int] = {}
    for zone in (me.library, me.hand):
        cmcs = _count_nonland_cmcs(zone)
        for cmc, n in cmcs.items():
            my_cmcs[cmc] = my_cmcs.get(cmc, 0) + n

    # Practical X range: we can cast Chalice at X=0 freely, X=1 on T1 with
    # untapped land, X=2 on T2, X=3 on T3. Cap at 3 — higher X is rare.
    best_net = 0
    candidate_cmcs = set(opp_cmcs) | set(my_cmcs)
    for x in candidate_cmcs:
        if x > CHALICE_PRACTICAL_X_CEIL:
            continue
        net = opp_cmcs.get(x, 0) - my_cmcs.get(x, 0)
        if net > best_net:
            best_net = net

    if best_net <= 0:
        return 0.0

    # Cap at MAX_NET_LOCK. Uncapped values were 2-3x too high in practice
    # (a library with 14 one-drops doesn't translate to 14 locked spells).
    best_net = min(best_net, MAX_NET_LOCK)

    impact = card_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING
    return best_net * impact * ARTIFACT_EXPECTED_LIFETIME * REALISM_DISCOUNT


def _blood_moon_lock_ev(template, me, opp, snap) -> float:
    """Blood Moon family: nonbasic lands become forced basic type.

    Value depends on opp's mana base (nonbasic count) and color palette
    (do they still get their colors through the forced basic type?).
    """
    from ai.clock import card_clock_impact

    oracle = (template.oracle_text or '').lower()
    forced_basic = None
    for basic in ('mountain', 'island', 'plains', 'swamp', 'forest'):
        # "are Mountains" / "are Islands" / etc.
        if f'are {basic}s' in oracle or f'are {basic}.' in oracle:
            forced_basic = basic
            break
    if forced_basic is None:
        return 0.0

    # Count opp's nonbasic lands across all zones.
    # "Basic" is a supertype on the CardTemplate, not a string field.
    from engine.cards import Supertype
    nonbasic_count = 0
    for zone in (opp.library, opp.hand, opp.battlefield):
        for c in zone:
            if c.template.is_land and Supertype.BASIC not in c.template.supertypes:
                nonbasic_count += 1
    if nonbasic_count == 0:
        return 0.0

    # Approximate opp's color requirements from mana costs of their spells.
    colors_used: set[str] = set()
    for zone in (opp.library, opp.hand):
        for c in zone:
            mc = c.template.mana_cost
            if getattr(mc, 'white', 0) > 0: colors_used.add('W')
            if getattr(mc, 'blue', 0) > 0:  colors_used.add('U')
            if getattr(mc, 'black', 0) > 0: colors_used.add('B')
            if getattr(mc, 'red', 0) > 0:   colors_used.add('R')
            if getattr(mc, 'green', 0) > 0: colors_used.add('G')

    forced_color = {'mountain': 'R', 'island': 'U', 'plains': 'W',
                    'swamp': 'B', 'forest': 'G'}[forced_basic]

    # If opp plays only the forced color, Blood Moon does nothing.
    other_colors = len(colors_used - {forced_color})
    if other_colors == 0:
        return 0.0

    # Disruption scales with nonbasic count × missing colors.
    # Coefficient keeps magnitudes in the same range as Chalice; cap
    # avoids dominating all other considerations.
    disruption = min(
        nonbasic_count * other_colors * BLOOD_MOON_DISRUPTION_COEFFICIENT,
        BLOOD_MOON_DISRUPTION_CAP,
    )
    impact = card_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING
    return disruption * impact * ENCHANTMENT_EXPECTED_LIFETIME * REALISM_DISCOUNT


def _canonist_lock_ev(template, me, opp, snap) -> float:
    """Can't-cast-more-than-one effects (Canonist, Rule of Law).

    Bites decks that chain low-CMC spells (Storm, Affinity, Prowess).
    Barely affects control mirrors.
    """
    from ai.clock import card_clock_impact

    low_cmc = 0
    total_nonland = 0
    for c in opp.library:
        if c.template.is_land:
            continue
        total_nonland += 1
        if (c.template.cmc or 0) <= 2:
            low_cmc += 1
    if total_nonland == 0:
        return 0.0
    density = low_cmc / total_nonland
    if density < CANONIST_DENSITY_FLOOR:
        return 0.0  # not enough low-CMC density for the lock to bite

    # Disruption ≈ 1 extra spell/turn × lifetime × density.
    from engine.cards import CardType
    is_creature = CardType.CREATURE in template.card_types
    lifetime = CREATURE_EXPECTED_LIFETIME if is_creature else ENCHANTMENT_EXPECTED_LIFETIME
    # Spell-limiting turns × density.
    disruption = density * CANONIST_DISRUPTION_TURN_COUNT
    impact = card_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING
    # Slightly lower coefficient — Canonist's lock is per-turn-skippable.
    return disruption * impact * lifetime * CANONIST_DISRUPTION_COEFFICIENT


def _torpor_orb_lock_ev(template, me, opp, snap) -> float:
    """Torpor Orb / Cursed Totem: ETB abilities don't trigger.

    Value = count of opp's etb_value tagged creatures in library.
    """
    from ai.clock import card_clock_impact
    from engine.cards import CardType

    etb_count = 0
    for c in opp.library:
        tags = getattr(c.template, 'tags', set())
        if CardType.CREATURE in c.template.card_types and 'etb_value' in tags:
            etb_count += 1
    if etb_count < TORPOR_ORB_ETB_DENSITY_FLOOR:
        return 0.0

    # Each disrupted ETB worth a fraction of a card (not all ETBs are huge).
    impact = card_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING
    return (etb_count * TORPOR_ORB_PER_ETB_VALUE * impact
            * ARTIFACT_EXPECTED_LIFETIME * REALISM_DISCOUNT)


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────

_DISPATCH: dict[str, Callable] = {
    'chalice': _chalice_lock_ev,
    'blood_moon': _blood_moon_lock_ev,
    'canonist': _canonist_lock_ev,
    'torpor_orb': _torpor_orb_lock_ev,
}


def stax_lock_ev(template: 'CardTemplate',
                 me: 'PlayerState',
                 opp: 'PlayerState',
                 snap: 'EVSnapshot') -> float:
    """Return additional EV for casting `template` as a stax lock piece.

    Returns 0.0 if:
    - `template` isn't a recognised stax card
    - the lock is symmetric or useless against this opponent
    - opponent's library is empty (defensive)
    - the game is too late for the lock to bite (turn 5+)
    """
    family = classify_stax(template)
    if family is None:
        return 0.0
    decay = _turn_decay(snap.turn_number)
    if decay == 0.0:
        return 0.0
    raw_ev = _DISPATCH[family](template, me, opp, snap)
    return raw_ev * decay
