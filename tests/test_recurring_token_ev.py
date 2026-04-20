"""C — Recurring-token EV subsystem (docs/proposals/recurring_token_ev.md).

Covers the token_maker cards that fall through the ETB-only immediate
branch: Ocelot Pride (end-step creature token), Pinnacle Emissary
(cast-trigger Drone), Voice of Victory (attack-trigger Warriors),
Bowmasters (opp-draw amass). Their token contribution is a lifetime
value — not a state-immediately-after-cast value — so it accrues to
`persistent_power` and is discounted in `position_value` by
`urgency_factor`.

All trigger rates are derived from `EVSnapshot` fields or declared
rules constants (`PERMANENT_VALUE_WINDOW = 2.0`,
`MODERN_SPELLS_PER_TURN = 1.0`, `OPP_DRAWS_PER_TURN = 1.0`).
"""
from __future__ import annotations

import pytest

from ai.clock import position_value
from ai.ev_evaluator import EVSnapshot, _project_spell
from engine.card_database import CardDatabase
from engine.cards import CardInstance


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _instance(card_db, name):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return CardInstance(
        template=tmpl,
        owner=0,
        controller=0,
        instance_id=1,
        zone="hand",
    )


def _baseline_snap() -> EVSnapshot:
    """Clean mid-game snapshot. opp_creature_count>0 so the AI models
    "opp has blockers," and opp_clock is comfortably > 1 so recurring
    triggers are credited (we're not about to die)."""
    return EVSnapshot(
        my_life=20, opp_life=20,
        my_power=3, opp_power=3,
        my_toughness=3, opp_toughness=3,
        my_creature_count=1, opp_creature_count=1,
        my_hand_size=5, opp_hand_size=5,
        my_mana=3, opp_mana=3,
        my_total_lands=3, opp_total_lands=3,
        turn_number=4,
    )


class TestPersistentPowerAccrual:
    """Recurring-trigger token makers accrue persistent_power, not
    immediate power."""

    def test_ocelot_pride_end_step_accrues_persistent(self, card_db):
        """End-step creature-token clause fires once per turn."""
        pride = _instance(card_db, "Ocelot Pride")
        snap = _baseline_snap()
        projected = _project_spell(pride, snap)

        body_power = pride.template.power or 0
        immediate_delta = projected.my_power - snap.my_power - body_power
        assert immediate_delta == 0, (
            f"Ocelot Pride's token is end-step, not ETB. Got "
            f"immediate_delta={immediate_delta}."
        )
        assert projected.persistent_power > 0, (
            f"End-step creature token must accrue persistent_power. "
            f"Got {projected.persistent_power:.2f}."
        )

    def test_pinnacle_emissary_cast_trigger_accrues_persistent(self, card_db):
        """Cast-trigger creature-token clause fires ~once per turn
        (Modern-avg spells-per-turn rules constant)."""
        emissary = _instance(card_db, "Pinnacle Emissary")
        snap = _baseline_snap()
        projected = _project_spell(emissary, snap)

        body_power = emissary.template.power or 0
        immediate_delta = projected.my_power - snap.my_power - body_power
        assert immediate_delta == 0
        assert projected.persistent_power > 0, (
            f"Pinnacle Emissary's cast-trigger Drone must accrue "
            f"persistent_power. Got {projected.persistent_power:.2f}."
        )

    def test_bowmasters_etb_immediate_and_draw_persistent(self, card_db):
        """Bowmasters has both ETB amass (immediate) AND opp-draws
        amass (persistent) — both should be credited."""
        bow = _instance(card_db, "Orcish Bowmasters")
        snap = _baseline_snap()
        projected = _project_spell(bow, snap)

        body_power = bow.template.power or 0
        immediate_delta = projected.my_power - snap.my_power - body_power
        # ETB amass Orcs 1 → +1 immediate.
        assert immediate_delta >= 1, (
            f"Bowmasters' ETB amass must credit immediately. "
            f"Got immediate_delta={immediate_delta}."
        )
        # "Whenever an opponent draws a card" → recurring amass on
        # opp's draw step. Must also accrue persistent_power.
        assert projected.persistent_power > 0, (
            f"Bowmasters' recurring opp-draw amass must accrue "
            f"persistent_power. Got {projected.persistent_power:.2f}."
        )


class TestLifeAndEnergyPersistentValuation:
    """Recurring per-permanent triggers that grant life or energy
    instead of creature tokens must still accrue persistent_power.
    Otherwise decks like Boros (Guide of Souls) lose to opponents
    who run similar triggers, since opp's evaluation correctly
    credits THEIR Guide-class engines while our own scores 0.

    Design: docs/experiments/2026-04-20_phase7_pinnacle_emissary_fix.md
    follow-up note — `_clause_token_power` only credits creature
    tokens / amass, leaving life-gain and energy-gain at 0.
    """

    def test_guide_of_souls_creature_enters_accrues_persistent(
            self, card_db):
        """Guide of Souls: 'Whenever another creature you control
        enters, you gain 1 life and get {E}.'  Recurring trigger,
        but produces no creature token — current code returns 0
        persistent_power for the clause.  After the fix, life + energy
        gain credited as a non-zero positive value."""
        guide = _instance(card_db, "Guide of Souls")
        snap = _baseline_snap()
        projected = _project_spell(guide, snap)

        body_power = guide.template.power or 0
        immediate_delta = projected.my_power - snap.my_power - body_power
        # Guide's body is the only immediate power; the trigger fires
        # only when ANOTHER creature enters, so the cast itself doesn't
        # generate a token same-turn.
        assert immediate_delta == 0, (
            f"Guide's clause is recurring (other_enters), not a self-"
            f"ETB token. Immediate column must be 0. "
            f"Got {immediate_delta}."
        )
        assert projected.persistent_power > 0.0, (
            f"Guide of Souls' 'gain 1 life and get {{E}}' clause is "
            f"a recurring per-other-creature trigger that strengthens "
            f"the deck over time. Persistent_power must be > 0; got "
            f"{projected.persistent_power:.3f}. Without this, Boros "
            f"opponents value Guide higher than Boros itself."
        )


class TestRagavanRegressionAnchor:
    """Ragavan's Treasure (combat-damage, non-creature) must still
    produce 0 across both immediate and persistent columns."""

    def test_ragavan_treasure_is_zero_in_both_columns(self, card_db):
        ragavan = _instance(card_db, "Ragavan, Nimble Pilferer")
        snap = _baseline_snap()
        projected = _project_spell(ragavan, snap)

        body_power = ragavan.template.power or 0
        immediate_delta = projected.my_power - snap.my_power - body_power
        assert immediate_delta == 0, (
            f"Ragavan's Treasure is not a creature token; immediate "
            f"column must be 0. Got {immediate_delta}."
        )
        assert projected.persistent_power == 0.0, (
            f"Ragavan's Treasure must not accrue persistent_power "
            f"(non-creature token, zero power contribution). Got "
            f"{projected.persistent_power:.2f}."
        )


class TestUrgencyGating:
    """Persistent power is discounted by urgency_factor. When we're
    dying fast, recurring tokens don't matter — we never realise them."""

    def test_urgency_gates_position_value(self, card_db):
        """Same persistent_power under two different urgency factors
        must score differently in position_value. Fast opp clock =
        less credit."""
        pride = _instance(card_db, "Ocelot Pride")

        # High-urgency snapshot: opp_power huge → opp_clock ≈ 1 → urgency ≈ 0
        high_urgency_snap = EVSnapshot(
            my_life=6, opp_life=20,
            my_power=3, opp_power=20,  # we die next turn
            my_toughness=3, opp_toughness=3,
            my_creature_count=1, opp_creature_count=4,
            my_hand_size=5, opp_hand_size=5,
            my_mana=3, opp_mana=3,
            my_total_lands=3, opp_total_lands=3,
            turn_number=5,
        )
        # Low-urgency snapshot: opp has no clock, we have all the time
        low_urgency_snap = EVSnapshot(
            my_life=20, opp_life=20,
            my_power=3, opp_power=1,  # opp clock is slow
            my_toughness=3, opp_toughness=3,
            my_creature_count=1, opp_creature_count=1,
            my_hand_size=5, opp_hand_size=5,
            my_mana=3, opp_mana=3,
            my_total_lands=3, opp_total_lands=3,
            turn_number=5,
        )

        high_proj = _project_spell(pride, high_urgency_snap)
        low_proj = _project_spell(pride, low_urgency_snap)

        # Both projections should accrue the same raw persistent_power
        # (the oracle says so). But when scored via position_value,
        # the high-urgency snapshot should credit less of it.
        assert high_proj.persistent_power == low_proj.persistent_power, (
            "Raw persistent_power must be the same — it's a property "
            "of the card's oracle, not the snapshot."
        )

        # Contribution of persistent_power to position_value should be
        # smaller under high urgency.
        high_score = position_value(high_proj)
        high_no_persist = EVSnapshot(**{
            **high_proj.__dict__, "persistent_power": 0.0
        })
        high_delta = high_score - position_value(high_no_persist)

        low_score = position_value(low_proj)
        low_no_persist = EVSnapshot(**{
            **low_proj.__dict__, "persistent_power": 0.0
        })
        low_delta = low_score - position_value(low_no_persist)

        assert low_delta > high_delta, (
            f"Persistent-power contribution should be larger when "
            f"opponent's clock is slow. high_delta={high_delta:.3f}, "
            f"low_delta={low_delta:.3f}."
        )


class TestTriggerRateDerivation:
    """Regression anchors for the trigger-rate formulas. These assert
    the principled derivation, not specific magic numbers."""

    def test_attack_trigger_zero_when_we_have_no_power(self, card_db):
        """Attack-trigger rate reads snap.my_power > 0. A player with
        zero board power is not attacking — no persistent credit."""
        voice = _instance(card_db, "Voice of Victory")
        snap = EVSnapshot(
            my_life=20, opp_life=20,
            my_power=0,  # ← no attacker
            opp_power=3,
            my_toughness=0, opp_toughness=3,
            my_creature_count=0, opp_creature_count=1,
            my_hand_size=5, opp_hand_size=5,
            my_mana=3, opp_mana=3,
            my_total_lands=3, opp_total_lands=3,
            turn_number=3,
        )
        projected = _project_spell(voice, snap)
        # Voice has mobilize (attack trigger) — should NOT credit
        # persistent_power when we have no board power yet.
        # (Voice itself is the attacker in the real game, but the
        # snapshot is pre-cast. Conservative rate is fine here.)
        assert projected.persistent_power == 0.0, (
            f"Attack-trigger rate should be 0 when my_power == 0. "
            f"Got persistent_power={projected.persistent_power:.2f}."
        )

    def test_attack_trigger_fires_when_we_have_power_and_survive(
            self, card_db):
        """With my_power > 0 and opp_clock > 1, attack trigger fires."""
        voice = _instance(card_db, "Voice of Victory")
        snap = _baseline_snap()  # my_power=3, opp_power=3 → both slow
        projected = _project_spell(voice, snap)
        assert projected.persistent_power > 0, (
            f"Voice of Victory's mobilize trigger should accrue "
            f"persistent_power under the baseline snap. "
            f"Got {projected.persistent_power:.2f}."
        )
