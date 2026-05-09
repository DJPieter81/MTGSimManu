"""Force-fire tutor-as-finisher-access when chain is lethal.

Storm holds the chain when lethal is on the table — surfaced from
the user-reported failure mode "Storm holds chain when lethal is on
the table; force fire when lethal is reachable" (branch
claude/fix-storm-combo-logic-AKehu, scoped to G1 vs no-disruption
decks per gameplan deck-class tag).

# Mechanic the test names

The tutor branch of `card_combo_modifier`
(`ai/combo_calc.py::card_combo_modifier`, `tutor` branch) decides
whether to FIRE the tutor (Wish) or HOLD it for more chain build-up.

A tutor card that fetches a STORM-keyword payoff (Grapeshot) and
then immediately casts it the same turn contributes **two** spells
to the storm count, not one:

  * Wish itself is cast → `storm_count` becomes `S + 1`.
  * Wish resolves, fetches Grapeshot from SB to hand.
  * Grapeshot is cast → `storm_count` becomes `S + 2` and Grapeshot
    deals `1 + (S + 1) = S + 2` damage (storm copies + original).

So the lethal condition for Wish-as-finisher-access is `S + 2 >=
opp_life`, NOT `S + 1 >= opp_life`. The trailing `return (storm +
2) / opp_life * a.combo_value` already encodes this `+2`; the
lethal-shortcut at the top of the tutor branch underscored it as
`+1`, an off-by-one that holds the tutor when Wish→Grapeshot would
exactly kill.

# Why this is a class fix, not a Wish patch

The same `+2` arithmetic applies to any tutor in any combo deck —
Burning Wish, Living Wish, Mastermind's Acquisition, Demonic Tutor,
Summoner's Pact, future printings — provided the tutor has a
`STORM`-keyword payoff in SB or library (caught by
`_tutor_has_payoff_access`, which is oracle/tag-driven). No card
names, no archetype gates. Same mechanism credits every tutor that
can close a storm chain.

# Failure mode without this fix

Trace pattern: Storm at storm=3 vs opponent at 5 life with Wish in
hand and Grapeshot in SB.

  * Wish→Grapeshot deals 1 + 4 = 5 damage = lethal.
  * Pre-fix `storm + 1 = 4 >= 5` is False → falls through to the
    `non_tutor_fuel > 0` branch → if any chain-extending card in
    hand, returns negative (HOLD). Storm passes the turn and dies
    next turn to opp's clock.
  * Post-fix `storm + 2 = 5 >= 5` is True → returns
    `a.combo_value` (FIRE). Storm closes the game.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

import pytest

from ai.combo_calc import ComboAssessment, card_combo_modifier
from ai.ev_evaluator import EVSnapshot


# ─── Mock helpers (mirror tests/test_storm_wish_fires_when_fuel_depleted.py) ──

def _make_snap(opp_life=5, my_mana=5, storm_count=3, **kwargs):
    defaults = dict(
        my_life=10, opp_life=opp_life, my_power=0, opp_power=4,
        my_toughness=0, opp_toughness=0, my_creature_count=0,
        opp_creature_count=0, my_hand_size=4, opp_hand_size=2,
        my_mana=my_mana, opp_mana=2, my_total_lands=5, opp_total_lands=4,
        turn_number=4, storm_count=storm_count, my_gy_creatures=0,
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


def _make_storm_assessment(combo_value=80.0):
    return ComboAssessment(
        resource_zone="storm", is_ready=False,
        payoff_value=0.5, combo_value=combo_value, risk_discount=0.7,
        has_payoff=True, has_enabler=False,
        payoff_names={"Grapeshot", "Empty the Warrens"},
        _role_cache={"Wish": "fillers"},
    )


def _make_storm_grapeshot_in_sb():
    """Grapeshot card with the STORM keyword for SB membership."""
    from engine.cards import Keyword as Kw
    return MockCard(
        name="Grapeshot", instance_id=999,
        template=MockTemplate(
            name="Grapeshot", cmc=2, tags={'combo'},
            keywords={Kw.STORM}),
        zone="sideboard",
    )


def _make_wish(instance_id=1):
    return MockCard(
        name="Wish", instance_id=instance_id,
        template=MockTemplate(
            name="Wish", cmc=3, is_sorcery=True,
            tags={'tutor', 'combo'}),
    )


def _make_pyretic(instance_id=2):
    return MockCard(
        name="Pyretic Ritual", instance_id=instance_id,
        template=MockTemplate(
            name="Pyretic Ritual", cmc=2,
            tags={'ritual', 'mana_source'}),
    )


# ─── Failing-test for the off-by-one in tutor lethal shortcut ─────

class TestTutorFiresWhenWishPlusFetchedPayoffIsLethal:
    """Wish + fetched Grapeshot = 2 spells. Grapeshot deals
    `storm + 2` damage. Lethal iff `storm + 2 >= opp_life`."""

    def test_wish_fires_at_exact_lethal_boundary(self):
        """storm=3, opp_life=5. Wish→Grapeshot deals exactly 5
        damage = lethal. Wish must fire even with chain-extending
        fuel still in hand (Pyretic Ritual)."""
        wish = _make_wish(instance_id=1)
        # Pyretic Ritual is real chain fuel — under the old logic
        # this would justify holding the tutor "for more chain".
        # The off-by-one means the lethal-shortcut at the top of
        # the tutor branch did not catch this case.
        pyretic = _make_pyretic(instance_id=2)
        sb_grapeshot = _make_storm_grapeshot_in_sb()

        a = _make_storm_assessment(combo_value=80.0)
        snap = _make_snap(opp_life=5, my_mana=5, storm_count=3)
        me = type('', (), {
            'spells_cast_this_turn': 3,
            'hand': [wish, pyretic],
            'library': [None] * 30,
            'graveyard': [],
            'battlefield': [],
            'sideboard': [sb_grapeshot],
        })()
        game = type('', (), {
            'players': [me, me],
            'can_cast': lambda *a: True,
        })()

        mod = card_combo_modifier(wish, a, snap, me, game, 0)
        # Pre-fix: storm + 1 = 4 < 5 → falls through; non_tutor_fuel=1
        #          (Pyretic) → -1/5 * 80 = -16 (held). BUG.
        # Post-fix: storm + 2 = 5 >= 5 → returns combo_value = 80 (fire).
        assert mod > 0, (
            f"Wish scored {mod:.1f} — tutor was held when "
            f"Wish→Grapeshot deals exactly lethal damage "
            f"({snap.storm_count + 2} = opp_life {snap.opp_life}). "
            f"The lethal-shortcut at the top of the tutor branch "
            f"must use storm + 2 (Wish + fetched payoff = 2 spells), "
            f"not storm + 1."
        )

    def test_wish_fires_when_lethal_with_overkill(self):
        """storm=4, opp_life=5. Wish→Grapeshot deals 6 damage =
        overkill lethal. Wish must fire."""
        wish = _make_wish(instance_id=1)
        pyretic = _make_pyretic(instance_id=2)
        sb_grapeshot = _make_storm_grapeshot_in_sb()

        a = _make_storm_assessment(combo_value=80.0)
        snap = _make_snap(opp_life=5, my_mana=5, storm_count=4)
        me = type('', (), {
            'spells_cast_this_turn': 4,
            'hand': [wish, pyretic],
            'library': [None] * 30,
            'graveyard': [],
            'battlefield': [],
            'sideboard': [sb_grapeshot],
        })()
        game = type('', (), {
            'players': [me, me],
            'can_cast': lambda *a: True,
        })()

        mod = card_combo_modifier(wish, a, snap, me, game, 0)
        # Pre-fix: storm + 1 = 5 >= 5 → already passes (this case
        #          works). Post-fix: storm + 2 = 6 >= 5 → still fires.
        # Anchor regression for the existing pass case.
        assert mod > 0, (
            f"Wish scored {mod:.1f} — overkill-lethal Wish→Grapeshot "
            f"({snap.storm_count + 2} > opp_life {snap.opp_life}) "
            f"must fire. Regression anchor for existing behavior."
        )


class TestTutorStillHeldWhenSubLethal:
    """Regression anchor: when Wish→Grapeshot is sub-lethal AND
    real fuel remains in hand, the tutor must STILL be held. The
    off-by-one fix is narrow — it only changes the lethal-shortcut,
    not the hold-for-fuel mechanism."""

    def test_wish_held_when_chain_sub_lethal_with_fuel(self):
        """storm=2, opp_life=15. Wish→Grapeshot deals 4 damage —
        sub-lethal, more fuel needed. Pyretic Ritual in hand is
        real chain fuel (extends the chain by adding mana for
        further casts). Wish must HOLD."""
        wish = _make_wish(instance_id=1)
        pyretic = _make_pyretic(instance_id=2)
        sb_grapeshot = _make_storm_grapeshot_in_sb()

        a = _make_storm_assessment(combo_value=80.0)
        snap = _make_snap(opp_life=15, my_mana=5, storm_count=2)
        me = type('', (), {
            'spells_cast_this_turn': 2,
            'hand': [wish, pyretic],
            'library': [None] * 30,
            'graveyard': [],
            'battlefield': [],
            'sideboard': [sb_grapeshot],
        })()
        game = type('', (), {
            'players': [me, me],
            'can_cast': lambda *a: True,
        })()

        mod = card_combo_modifier(wish, a, snap, me, game, 0)
        # Both pre-fix and post-fix: storm + 2 = 4 < 15 → sub-lethal.
        # Falls to non_tutor_fuel branch. Pyretic counts → hold.
        assert mod < 0, (
            f"Wish scored {mod:.1f} — sub-lethal tutor fired with "
            f"Pyretic Ritual still in hand. The hold-for-fuel "
            f"mechanism must still trigger when (a) chain is not "
            f"yet lethal AND (b) real chain-extending fuel remains."
        )
