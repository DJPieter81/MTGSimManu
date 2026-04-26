"""F2.1 — tutor hold-penalty must only count chain-extending fuel.

Surfaced by the 2026-04-26 Storm pro-player audit (F2.1 + F3.1 — both
A2 and A3 independently flagged the same root cause):
docs/history/audits/2026-04-26_storm_pro_audit.md.

Trace evidence: replays/audit_storm_vs_dimir_midrange_s60500.txt
T10 (Storm at 1 life, mana=5, storm=8, hand=[Ral, Wish, Wish, Ral]):
the AI casts Past in Flames flashback for 0 damage instead of Wish
→ Grapeshot for 10 damage and dies the next turn.

Root cause in `ai/combo_calc.py:660-668` (tutor-as-finisher-access
branch): `non_tutor_fuel` counts EVERY non-land non-storm non-tutor
card in hand. This wrongly includes creatures (Ral, Monsoon Mage)
that don't contribute to the chain — they consume mana without
producing more, so holding the tutor "for them" strands the chain.

Fix: only count cards tagged as `ritual` / `cantrip` / `draw` /
`card_advantage` as chain-extending fuel. Cards without those tags
add 1 to storm count if cast but don't enable more spells, so they
shouldn't justify holding the tutor.

Generic by construction — same predicate would distinguish "fuel
that grows the chain" from "filler that shows up in hand" for any
combo-tutor pattern (Burning Wish, Living Wish, Demonic Tutor,
Summoner's Pact).

Regression anchor: tutor with rituals/cantrips in hand must STILL
be held (the fix doesn't break the canonical "build chain first"
case).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

import pytest

from ai.combo_calc import ComboAssessment, card_combo_modifier
from ai.ev_evaluator import EVSnapshot


# ─── Mock helpers (mirror tests/test_combo_calc.py) ────────────────

def _make_snap(opp_life=17, my_mana=5, storm_count=8, **kwargs):
    defaults = dict(
        my_life=1, opp_life=opp_life, my_power=0, opp_power=4,
        my_toughness=0, opp_toughness=0, my_creature_count=0,
        opp_creature_count=4, my_hand_size=4, opp_hand_size=3,
        my_mana=my_mana, opp_mana=2, my_total_lands=5, opp_total_lands=4,
        turn_number=10, storm_count=storm_count, my_gy_creatures=0,
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


def _make_storm_assessment(combo_value=80.0, opp_life=17):
    return ComboAssessment(
        resource_zone="storm", is_ready=False,
        payoff_value=0.5, combo_value=combo_value, risk_discount=0.7,
        has_payoff=True, has_enabler=False,
        payoff_names={"Grapeshot", "Empty the Warrens"},
        _role_cache={"Wish": "fillers"},
    )


def _make_storm_grapeshot_in_sb():
    """Build a Grapeshot card with the STORM keyword for SB membership."""
    from engine.cards import Keyword as Kw
    return MockCard(
        name="Grapeshot", instance_id=999,
        template=MockTemplate(
            name="Grapeshot", cmc=2, tags={'combo'},
            keywords={Kw.STORM}),
        zone="sideboard",
    )


# ─── F2.1: tutor must fire when only filler creatures remain ─────

class TestTutorFiresWhenFuelDepleted:
    """F2.1 reproduction — when the only non-tutor, non-storm cards
    in hand are creatures (no ritual/cantrip/draw tags), the tutor
    should fire as the closer rather than be held for filler."""

    def test_wish_fires_when_only_creatures_remain(self):
        """Trace state: hand=[Wish, Ral, Ral, Wish], mana=5, storm=8.
        Rals are creatures (no ritual/cantrip/draw tag) — they don't
        extend the chain. Wish must fire."""
        wish = MockCard(
            name="Wish", instance_id=1,
            template=MockTemplate(
                name="Wish", cmc=3, is_sorcery=True,
                tags={'tutor', 'combo'}),
        )
        wish2 = MockCard(
            name="Wish", instance_id=2,
            template=MockTemplate(
                name="Wish", cmc=3, is_sorcery=True,
                tags={'tutor', 'combo'}),
        )
        # Ral is a creature with mana_source + cost_reducer tags but
        # NO ritual/cantrip/draw/card_advantage tag — it doesn't grow
        # the chain when cast (consumes 2 mana, adds 1 storm).
        ral1 = MockCard(
            name="Ral, Monsoon Mage // Ral, Leyline Prodigy",
            instance_id=3,
            template=MockTemplate(
                name="Ral, Monsoon Mage // Ral, Leyline Prodigy",
                cmc=2, is_creature=True, is_sorcery=False,
                tags={'cost_reducer', 'creature', 'mana_source',
                      'early_play'}),
        )
        ral2 = MockCard(
            name="Ral, Monsoon Mage // Ral, Leyline Prodigy",
            instance_id=4,
            template=MockTemplate(
                name="Ral, Monsoon Mage // Ral, Leyline Prodigy",
                cmc=2, is_creature=True, is_sorcery=False,
                tags={'cost_reducer', 'creature', 'mana_source',
                      'early_play'}),
        )
        sb_grapeshot = _make_storm_grapeshot_in_sb()

        a = _make_storm_assessment(combo_value=80.0, opp_life=17)
        snap = _make_snap(opp_life=17, my_mana=5, storm_count=8)
        me = type('', (), {
            'spells_cast_this_turn': 8,
            'hand': [wish, wish2, ral1, ral2],
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
        # Pre-fix: -2/17 * 80 = -9.41 (held, Rals counted as fuel)
        # Post-fix: +(8+2)/17 * 80 = +47.06 (fires, Rals not counted)
        assert mod > 0, (
            f"Wish scored {mod:.1f} — tutor was held. With only "
            f"creatures (Ral) remaining as 'fuel', the tutor should "
            f"FIRE because creatures don't extend the chain (consume "
            f"mana without producing it, no draw, no dig). "
            f"Counting only ritual/cantrip/draw-tagged fuel would "
            f"give non_tutor_fuel=0 → fire branch."
        )


class TestTutorStillHeldWithRealFuel:
    """Regression anchor: tutor with rituals/cantrips in hand must
    STILL be held. The fix narrows what counts as fuel; it doesn't
    eliminate the hold-for-fuel mechanism for legitimate fuel."""

    def test_wish_held_when_ritual_and_cantrip_in_hand(self):
        """Hand=[Wish, Pyretic Ritual, Reckless Impulse], mana=4, storm=2.
        Pyretic (ritual) + Reckless (cantrip) are both real chain fuel —
        Wish should be HELD with negative modifier."""
        wish = MockCard(
            name="Wish", instance_id=1,
            template=MockTemplate(
                name="Wish", cmc=3, is_sorcery=True,
                tags={'tutor', 'combo'}),
        )
        pyretic = MockCard(
            name="Pyretic Ritual", instance_id=2,
            template=MockTemplate(
                name="Pyretic Ritual", cmc=2,
                tags={'ritual', 'mana_source'}),
        )
        reckless = MockCard(
            name="Reckless Impulse", instance_id=3,
            template=MockTemplate(
                name="Reckless Impulse", cmc=2,
                tags={'cantrip', 'card_advantage'}),
        )
        sb_grapeshot = _make_storm_grapeshot_in_sb()

        a = _make_storm_assessment(combo_value=80.0, opp_life=20)
        snap = _make_snap(opp_life=20, my_mana=4, storm_count=2)
        me = type('', (), {
            'spells_cast_this_turn': 2,
            'hand': [wish, pyretic, reckless],
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
        # Pre-fix: -2/20 * 80 = -8.0 (held, both ritual + cantrip count)
        # Post-fix: same — both still count as chain-extending fuel
        assert mod < 0, (
            f"Wish scored {mod:.1f} — tutor fired with real fuel "
            f"(ritual + cantrip) still in hand. The hold-for-fuel "
            f"mechanism must still trigger when legitimate chain-"
            f"extending fuel exists."
        )

    def test_wish_held_when_other_tutor_and_cantrip_in_hand(self):
        """Hand=[Wish, Wish, Reckless Impulse], mana=4, storm=2.
        Other Wish doesn't count (already excluded by 'tutor' filter
        — pre-existing behaviour). Reckless counts as cantrip fuel."""
        wish1 = MockCard(
            name="Wish", instance_id=1,
            template=MockTemplate(
                name="Wish", cmc=3, is_sorcery=True,
                tags={'tutor', 'combo'}),
        )
        wish2 = MockCard(
            name="Wish", instance_id=2,
            template=MockTemplate(
                name="Wish", cmc=3, is_sorcery=True,
                tags={'tutor', 'combo'}),
        )
        reckless = MockCard(
            name="Reckless Impulse", instance_id=3,
            template=MockTemplate(
                name="Reckless Impulse", cmc=2,
                tags={'cantrip', 'card_advantage'}),
        )
        sb_grapeshot = _make_storm_grapeshot_in_sb()

        a = _make_storm_assessment(combo_value=80.0, opp_life=20)
        snap = _make_snap(opp_life=20, my_mana=4, storm_count=2)
        me = type('', (), {
            'spells_cast_this_turn': 2,
            'hand': [wish1, wish2, reckless],
            'library': [None] * 30,
            'graveyard': [],
            'battlefield': [],
            'sideboard': [sb_grapeshot],
        })()
        game = type('', (), {
            'players': [me, me],
            'can_cast': lambda *a: True,
        })()

        mod = card_combo_modifier(wish1, a, snap, me, game, 0)
        # 1 cantrip (Reckless) → -1/20 * 80 = -4.0 (held)
        assert mod < 0, (
            f"Wish scored {mod:.1f} — tutor fired despite a cantrip "
            f"in hand. Reckless Impulse is real chain fuel and "
            f"should keep the tutor held."
        )
