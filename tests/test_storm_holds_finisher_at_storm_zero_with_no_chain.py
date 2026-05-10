"""Storm finisher must HOLD at storm=0 when no chain is in progress.

# Mechanic the test names

A storm-keyword finisher (Grapeshot, Empty the Warrens, etc.) cast as
the FIRST spell of the turn — `me.spells_cast_this_turn == 0` — and
with no chain-extending fuel in hand deals exactly `storm + 1 = 1`
damage.  Burning the closer for one damage when the library can still
supply chain fuel on a future draw is dominated: holding for next
turn's draw at any storm growth ≥ 1 yields strictly more damage
(`(storm_next + 1)/opp_life * combo_value` ≥ `2/opp_life * combo_value`)
for the same one-card cost.

The current `ai/combo_calc.py:684-685` storm-fire branch unconditionally
returns `(storm + 1) / opp_life * combo_value` whenever no fuel is in
hand — at storm=0 this is a tiny positive number which sums with the
projection's heuristic EV (clock impact, mana burn) to push the cast
above pass_threshold.  Trace evidence: seed 50500 T9 vs Eldrazi Tron
shows hand=[Grapeshot] only, storm_count=0, opp_life=15, mana=4 — the
AI fires Grapeshot for 1 damage (score +14.0, pass_threshold=-5.0).
The closer is wasted; subsequent turns Storm has no finisher in hand
and loses to Tron's clock.

# Why this is a class fix, not a Storm patch

The same arithmetic applies to every storm-keyword payoff in any combo
deck.  Any future printing with the storm keyword (and any deck running
it — Living End sideboard tech, Grixis storm, Burning Vengeance lists)
inherits the rule.  Detection is the storm keyword + `me.library`
fuel-density — zero card names, zero deck gates.

The rule, named without naming a card:

  * If `storm == 0` (no chain has begun this turn), AND
  * `opp_life > 1` (no 1-life shortcut), AND
  * `me.library` still contains a chain-fuel card (a future draw can
    grow the chain),
  * THEN the storm-keyword finisher must HOLD.

Magnitude of the HOLD modifier follows the existing branch convention:
opportunity cost = expected future storm growth × `combo_value /
opp_life`.  At storm=0 with library fuel available, the conservative
floor is one storm of growth → magnitude `1 / opp_life * combo_value`,
matching the symmetric `total_fuel > 0` branch above.

# Failure mode without this fix

Trace pattern (real game, seed 50500 T9 Storm vs Eldrazi Tron):

  * Storm: hand=[Grapeshot], storm_count=0, opp_life=15, mana=4,
    library=20+ cards.
  * Pre-fix: storm-fire branch returns `(0+1)/15 * 80 = +5.3`.  Sums
    with projection (~+8.7) for total +14.0 → fires.  Grapeshot
    deals 1 damage.  Storm has no closer left and loses.
  * Post-fix: storm-fire branch returns `-1/15 * 80 = -5.3` (HOLD).
    Combined score drops below pass_threshold=-5.0.  Storm passes,
    keeps Grapeshot, draws another ritual/cantrip next turn, chains
    for lethal on T11.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

import pytest

from ai.combo_calc import ComboAssessment, card_combo_modifier
from ai.ev_evaluator import EVSnapshot


# ─── Mock helpers (mirror tests/test_storm_tutor_lethal_fires.py) ──

def _make_snap(opp_life=15, my_mana=4, storm_count=0, **kwargs):
    defaults = dict(
        my_life=15, opp_life=opp_life, my_power=0, opp_power=4,
        my_toughness=0, opp_toughness=0, my_creature_count=0,
        opp_creature_count=1, my_hand_size=1, opp_hand_size=2,
        my_mana=my_mana, opp_mana=8, my_total_lands=4, opp_total_lands=8,
        turn_number=9, storm_count=storm_count, my_gy_creatures=0,
        my_energy=0, my_evasion_power=0, my_lifelink_power=0,
        opp_evasion_power=0, cards_drawn_this_turn=0,
    )
    defaults.update(kwargs)
    return EVSnapshot(**defaults)


@dataclass
class MockTemplate:
    name: str = "Test"
    cmc: int = 2
    is_instant: bool = False
    is_sorcery: bool = True
    is_land: bool = False
    is_creature: bool = False
    oracle_text: str = ""
    tags: Set[str] = field(default_factory=set)
    keywords: Set[str] = field(default_factory=set)
    color_identity: Set = field(default_factory=set)
    has_flash: bool = False
    ritual_mana: Optional[tuple] = None
    domain_reduction: int = 0
    card_types: Set = field(default_factory=set)
    power: Optional[int] = None
    toughness: Optional[int] = None
    x_cost_data: Optional[dict] = None


@dataclass
class MockCard:
    name: str = "Test"
    instance_id: int = 0
    template: MockTemplate = field(default_factory=MockTemplate)
    zone: str = "hand"
    power: Optional[int] = None
    toughness: Optional[int] = None
    other_counters: dict = field(default_factory=dict)


def _make_grapeshot(instance_id=1):
    """Grapeshot — storm-keyword finisher in hand."""
    from engine.cards import Keyword as Kw
    return MockCard(
        name="Grapeshot", instance_id=instance_id,
        template=MockTemplate(
            name="Grapeshot", cmc=2, is_sorcery=True,
            tags={'combo'}, keywords={Kw.STORM}),
    )


def _make_pyretic_in_library(instance_id=100):
    """Pyretic Ritual sitting in the library — represents future
    chain-fuel that hasn't been drawn yet.  Library composition
    matters: its presence means the next draw could grow the chain."""
    return MockCard(
        name="Pyretic Ritual", instance_id=instance_id,
        template=MockTemplate(
            name="Pyretic Ritual", cmc=2,
            tags={'ritual', 'mana_source'}),
        zone="library",
    )


def _make_storm_assessment(combo_value=80.0):
    return ComboAssessment(
        resource_zone="storm", is_ready=False,
        payoff_value=0.5, combo_value=combo_value, risk_discount=0.7,
        has_payoff=True, has_enabler=False,
        payoff_names={"Grapeshot", "Empty the Warrens"},
        _role_cache={"Grapeshot": "payoff"},
    )


# ─── Failing tests for the storm-zero, no-chain leak ─────────────

class TestStormHoldsFinisherAtStormZeroWithNoChain:
    """When no chain has started this turn (storm=0) AND hand has no
    chain-extending fuel AND library still contains chain fuel, the
    storm-keyword finisher must HOLD — fire-now is dominated by
    hold-for-next-draw."""

    def test_grapeshot_held_at_storm_zero_with_library_fuel(self):
        """Storm: hand=[Grapeshot], storm=0, opp_life=15, library has
        chain fuel.  Reproduces seed 50500 T9 vs Eldrazi Tron — the
        exact leak the user surfaced.  Grapeshot must score < 0."""
        grapeshot = _make_grapeshot(instance_id=1)

        a = _make_storm_assessment(combo_value=80.0)
        snap = _make_snap(opp_life=15, my_mana=4, storm_count=0)
        me = type('', (), {
            'spells_cast_this_turn': 0,
            'hand': [grapeshot],
            # Library contains chain fuel — represents the deck still
            # having draws that can grow the chain.
            'library': [_make_pyretic_in_library(instance_id=i)
                        for i in range(100, 130)],
            'graveyard': [],
            'battlefield': [],
            'sideboard': [],
        })()
        game = type('', (), {
            'players': [me, me],
            'can_cast': lambda *a: True,
        })()

        mod = card_combo_modifier(grapeshot, a, snap, me, game, 0)
        # Pre-fix: +(0+1)/15 * 80 = +5.3 (fires).
        # Post-fix: -1/15 * 80 = -5.3 (held — symmetric to the
        #          total_fuel > 0 branch above).
        assert mod < 0, (
            f"Grapeshot scored {mod:.2f} — finisher fired at "
            f"storm_count=0 with no chain-fuel in hand and a non-empty "
            f"library.  At storm=0 the cast deals 1 damage; holding "
            f"for next turn's draw is strictly better whenever the "
            f"library can supply chain fuel.  See test docstring + "
            f"seed 50500 T9 trace."
        )

    def test_grapeshot_still_fires_when_opp_at_one_life(self):
        """Regression anchor — at opp_life=1, Grapeshot's `storm + 1
        >= opp_life` lethal-shortcut at the top of the storm branch
        must still fire.  The new HOLD rule must NOT block lethal."""
        grapeshot = _make_grapeshot(instance_id=1)

        a = _make_storm_assessment(combo_value=80.0)
        snap = _make_snap(opp_life=1, my_mana=4, storm_count=0)
        me = type('', (), {
            'spells_cast_this_turn': 0,
            'hand': [grapeshot],
            'library': [_make_pyretic_in_library(instance_id=i)
                        for i in range(100, 130)],
            'graveyard': [],
            'battlefield': [],
            'sideboard': [],
        })()
        game = type('', (), {
            'players': [me, me],
            'can_cast': lambda *a: True,
        })()

        mod = card_combo_modifier(grapeshot, a, snap, me, game, 0)
        assert mod > 0, (
            f"Grapeshot scored {mod:.2f} at opp_life=1, storm=0 — "
            f"`storm + 1 >= opp_life` lethal-shortcut must still "
            f"trigger.  The HOLD-at-storm-zero rule must defer to "
            f"lethal."
        )

    def test_grapeshot_still_fires_when_chain_in_progress(self):
        """Regression anchor — when storm > 0 (chain has started this
        turn) AND no fuel remains in hand, the finisher should still
        fire.  The HOLD-at-storm-zero rule is narrow: it only applies
        when `storm == 0` (no chain has begun)."""
        grapeshot = _make_grapeshot(instance_id=1)

        a = _make_storm_assessment(combo_value=80.0)
        # storm=4 (chain in progress, 4 spells already cast this turn)
        snap = _make_snap(opp_life=15, my_mana=4, storm_count=4)
        me = type('', (), {
            'spells_cast_this_turn': 4,
            'hand': [grapeshot],
            'library': [_make_pyretic_in_library(instance_id=i)
                        for i in range(100, 130)],
            'graveyard': [],
            'battlefield': [],
            'sideboard': [],
        })()
        game = type('', (), {
            'players': [me, me],
            'can_cast': lambda *a: True,
        })()

        mod = card_combo_modifier(grapeshot, a, snap, me, game, 0)
        # storm=4 → Grapeshot deals 5 damage.  Chain has commenced;
        # firing closes whatever has been built. Branch returns
        # +(4+1)/15 * 80 = +26.7.
        assert mod > 0, (
            f"Grapeshot scored {mod:.2f} at storm_count=4 — once the "
            f"chain has begun, the finisher should fire to close the "
            f"5-damage chain.  The HOLD rule applies only at storm=0."
        )

    def test_grapeshot_fires_when_library_has_no_fuel(self):
        """Regression anchor — if the library is empty (or contains
        no chain fuel), holding for future draws is pointless.  Fire
        the finisher rather than waste it.  This also covers the
        deck-out / library-empty edge case."""
        grapeshot = _make_grapeshot(instance_id=1)

        a = _make_storm_assessment(combo_value=80.0)
        snap = _make_snap(opp_life=15, my_mana=4, storm_count=0)
        me = type('', (), {
            'spells_cast_this_turn': 0,
            'hand': [grapeshot],
            'library': [],  # No future draws possible
            'graveyard': [],
            'battlefield': [],
            'sideboard': [],
        })()
        game = type('', (), {
            'players': [me, me],
            'can_cast': lambda *a: True,
        })()

        mod = card_combo_modifier(grapeshot, a, snap, me, game, 0)
        # No future draws can grow the chain — fire the closer for
        # whatever damage it deals.  Pre-fix and post-fix both fire
        # via `(storm + 1) / opp_life * combo_value = +5.3`.
        assert mod > 0, (
            f"Grapeshot scored {mod:.2f} — with empty library, "
            f"holding for future draws is pointless.  Fire-now "
            f"branch must trigger when no library fuel can be drawn."
        )
