"""Bug 3 — `token_maker` tag conflates creature tokens with treasure tokens.

`_project_spell` in ai.ev_evaluator historically added +2 projected
power whenever a card carried the `token_maker` tag. That tag is
assigned whenever the oracle text contains any create-token clause,
regardless of what the token is:

    Ajani, Nacatl Pariah  — ETB creates a 2/1 Cat (creature token, guaranteed)
    Ragavan              — on combat damage, create a Treasure (mana token, conditional)

Both produced +2 projected power, so Ragavan gained a ~+2.4 EV swing
over Ajani even though its token is a mana rock that never attacks.

Invariant candidate (card-parity): two cards with equivalent
oracle-text clauses must produce equivalent projection bonuses. An
ETB creature-token clause is one class of effect; a
combat-damage-create-treasure clause is a different class and must
not share projection math.

The fix replaces the binary tag check with oracle-driven branching.
"""
from __future__ import annotations

import pytest

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
    """Clean mid-game snapshot so projection deltas are pure bonuses."""
    return EVSnapshot(
        my_life=20, opp_life=20,
        my_power=0, opp_power=0,
        my_toughness=0, opp_toughness=0,
        my_creature_count=0, opp_creature_count=1,  # opp has a blocker
        my_hand_size=5, opp_hand_size=5,
        my_mana=3, opp_mana=3,
        my_total_lands=3, opp_total_lands=3,
        turn_number=3,
    )


class TestTokenMakerProjection:
    """Ajani's guaranteed ETB creature token must project power;
    Ragavan's conditional combat-damage treasure token must not."""

    def test_ajani_etb_creature_token_projects_power(self, card_db):
        """Ajani's ETB clause guarantees a 2/1 creature on resolution —
        its projected power contribution must cover that token."""
        ajani = _instance(card_db, "Ajani, Nacatl Pariah")
        snap = _baseline_snap()
        projected = _project_spell(ajani, snap)

        ajani_base_power = ajani.template.power or 0
        token_delta = projected.my_power - snap.my_power - ajani_base_power
        # Token is a 2/1 Cat Warrior → +2 power is the expected bonus.
        assert token_delta >= 2, (
            f"Ajani's guaranteed ETB creature token did not contribute "
            f"projected power. token_delta={token_delta} (expected ≥ 2)."
        )
        # And the token is a real creature on the board.
        count_delta = (projected.my_creature_count
                       - snap.my_creature_count)
        assert count_delta >= 2, (
            f"Ajani himself + Cat token = +2 creatures, got "
            f"{count_delta}."
        )

    def test_ragavan_treasure_token_does_not_project_power(self, card_db):
        """Ragavan's treasure-token clause is combat-damage conditional
        AND the token is a mana rock (not a creature). It must not add
        projected power."""
        ragavan = _instance(card_db, "Ragavan, Nimble Pilferer")
        snap = _baseline_snap()
        projected = _project_spell(ragavan, snap)

        ragavan_base_power = ragavan.template.power or 0
        token_delta = projected.my_power - snap.my_power - ragavan_base_power
        assert token_delta == 0, (
            f"Ragavan's treasure-token clause added +{token_delta} power "
            f"to projection. Treasures are mana, not creatures — they "
            f"contribute to mana-clock impact, not combat power."
        )
        # Ragavan alone = +1 creature, not +2 (no creature token).
        count_delta = (projected.my_creature_count
                       - snap.my_creature_count)
        assert count_delta == 1, (
            f"Ragavan + treasure = +1 creature (Ragavan only), got "
            f"{count_delta}. Treasure tokens are not creatures."
        )

    def test_ragavan_outranks_ajani_does_not_hold_on_power(self, card_db):
        """Sanity cross-check: after the fix, Ajani's projected power
        contribution should exceed Ragavan's from the token bonus alone.

        Pre-fix both added +2; post-fix only Ajani adds +2."""
        ajani = _instance(card_db, "Ajani, Nacatl Pariah")
        ragavan = _instance(card_db, "Ragavan, Nimble Pilferer")
        snap = _baseline_snap()

        ajani_projected = _project_spell(ajani, snap)
        ragavan_projected = _project_spell(ragavan, snap)

        ajani_bonus = (ajani_projected.my_power - snap.my_power
                       - (ajani.template.power or 0))
        ragavan_bonus = (ragavan_projected.my_power - snap.my_power
                         - (ragavan.template.power or 0))
        assert ajani_bonus > ragavan_bonus, (
            f"Token-projection bonus ordering wrong: "
            f"ajani={ajani_bonus}, ragavan={ragavan_bonus}. "
            f"Ajani's guaranteed creature token should exceed Ragavan's "
            f"treasure token in power projection."
        )
