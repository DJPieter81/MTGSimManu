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
    Detection reads the typed field CardTemplate.stax_class parsed once at
    DB load (oracle_parser.parse_stax_class). No runtime oracle scan.
    """
    return getattr(template, 'stax_class', None)


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


# Families that derive their own horizon (how long the lock keeps biting)
# from the game state and so must not be multiplied by the turn-decay
# table: a land-type lock is worth what it makes uncastable for the rest
# of the game, whether it lands on turn 3 or turn 7.
_SELF_HORIZON_FAMILIES = frozenset({'blood_moon'})


def _lock_horizon_draws(snap, pool_size: int) -> int:
    """Draws a player still gets while the lock holds: the shorter of the
    two combat clocks (the same `combat_clock` position_value reads);
    with no clock on either side the lock holds for the rest of the
    library."""
    from ai.clock import NO_CLOCK, combat_clock
    my_clock = combat_clock(snap.my_power, snap.opp_life,
                            snap.my_evasion_power, snap.opp_toughness)
    opp_clock = combat_clock(snap.opp_power, snap.my_life,
                             snap.opp_evasion_power, snap.my_toughness)
    horizon = min(my_clock, opp_clock)
    if horizon >= NO_CLOCK:
        return pool_size
    return max(0, int(horizon))


def _dead_card_value(template, snap) -> float:
    """What a player forfeits per card the lock makes uncastable: a
    creature's clock impact (the `creature_threat_value` base term,
    template-only) or the average card's clock impact in the life units
    the stax family already uses."""
    from ai.clock import card_clock_impact, creature_clock_impact
    from ai.scoring_constants import CREATURE_VALUE_OUTER_SCALE
    if template.is_creature:
        kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
               for kw in (getattr(template, 'keywords', None) or set())}
        return (creature_clock_impact(template.power or 0,
                                      template.toughness or 0, kws, snap)
                * CREATURE_VALUE_OUTER_SCALE)
    return card_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING


def _dead_cards_under_forced_type(player, forced_color: str) -> list:
    """Nonland cards in the player's hand and library that need a colour
    the player can no longer produce.  Under the lock every nonbasic
    land makes only ``forced_color`` (CR 305.7), so a colour survives
    only through BASIC lands of that type — on the battlefield, or still
    in hand / library to be played (fetching them is gone with the
    fetch ability)."""
    from ai.mana_planner import COLOR_MAP
    from engine.cards import Supertype
    live = {forced_color}
    for zone in (player.battlefield, player.hand, player.library):
        for c in zone:
            t = c.template
            if t.is_land and Supertype.BASIC in (t.supertypes or []):
                live.update(t.produces_mana or [])
    dead = []
    for zone in (player.hand, player.library):
        for c in zone:
            t = c.template
            if t.is_land:
                continue
            mc = t.mana_cost
            need = {code for code, attr in COLOR_MAP.items()
                    if getattr(mc, attr, 0) > 0}
            if need - live:
                dead.append(t)
    return dead


def _blood_moon_lock_ev(template, me, opp, snap) -> float:
    """Forced-basic land-type family ("Nonbasic lands are Mountains").

    The lock is worth the cards it makes uncastable, for as long as the
    game lasts — for BOTH players, since the effect is symmetric:

      value = Σ_opp dead_card_value × P(seen) − Σ_me dead_card_value × P(seen)

    where a card is dead when its cost needs a colour the player can no
    longer make (`_dead_cards_under_forced_type`), and P(seen) is the
    share of the player's pool (hand ∪ library) they will hold or draw
    before the game ends (`_lock_horizon_draws`).  No coefficient, no
    cap, no turn table: a mono-coloured opponent on basics yields zero,
    a five-colour opponent on duals yields most of its deck.
    """
    forced_basic = getattr(template, 'stax_forced_basic', None)
    if forced_basic is None:
        return 0.0
    # A second copy while the effect is already in play (either side)
    # changes nothing: every card it would make dead is dead already.
    for player in (me, opp):
        for c in player.battlefield:
            if getattr(c.template, 'stax_forced_basic', None):
                return 0.0
    from engine.constants import BASIC_LAND_TYPE_COLORS
    forced_color = BASIC_LAND_TYPE_COLORS[forced_basic]
    total = 0.0
    for player, sign in ((opp, 1.0), (me, -1.0)):
        pool_size = len(player.hand) + len(player.library)
        if pool_size == 0:
            continue
        dead = _dead_cards_under_forced_type(player, forced_color)
        if not dead:
            continue
        seen = min(pool_size,
                   len(player.hand) + _lock_horizon_draws(snap, pool_size))
        p_seen = seen / pool_size
        total += sign * p_seen * sum(_dead_card_value(t, snap) for t in dead)
    return total


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
    if family in _SELF_HORIZON_FAMILIES:
        return _DISPATCH[family](template, me, opp, snap)
    decay = _turn_decay(snap.turn_number)
    if decay == 0.0:
        return 0.0
    raw_ev = _DISPATCH[family](template, me, opp, snap)
    return raw_ev * decay
