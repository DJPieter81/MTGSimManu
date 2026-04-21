"""Phase 2 — RC-2 of the block strategy audit.

When an attacker's damage is dominated by attached equipment or auras,
chumping it this turn just means the plating rebinds to a different
creature next turn. Blocking is futile; accept the damage, preserve the
blocker, remove the equipment if possible.

Reference: audits/BLOCK_STRATEGY_AUDIT.md §Phase 2.
"""
from __future__ import annotations

import random
import pytest

from ai.ev_player import EVPlayer, _EQUIP_BONUS_RE
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _attach_equipment(equipment, creature):
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


class TestNoChumpIntoPlatedAttackerWithoutAnswer:
    def test_plated_frogmite_not_chumped_at_healthy_life(self, card_db):
        """Opp: Frogmite + Cranial Plating equipped + 4 artifacts total
        (Plating, Frogmite, Memnite x2). Plating grants +4/+0.
        Me: life=12 (just in emergency range via drop-below-5), one
        Guide of Souls as chump. Empty hand.

        Expected: NO emergency block against the plated attacker —
        plating rebinds regardless."""
        game = GameState(rng=random.Random(0))
        # life=12, 6 damage drops to 6, just misses drop-below-5 threshold;
        # but a 6-power attacker vs life=12 used to trigger RC-1's old
        # biggest_attacker >= life//2 clause. Need to trigger emergency
        # explicitly for this test: set life=8, incoming=6 → drop to 2
        # (emergency fires via drop-below-5). That's the real scenario
        # where we were chumping into a plated attacker.
        game.players[0].life = 8
        game.players[1].life = 20

        guide = _add_to_battlefield(game, card_db, "Guide of Souls",
                                     controller=0)
        frogmite = _add_to_battlefield(game, card_db, "Frogmite", controller=1)
        plating = _add_to_battlefield(game, card_db, "Cranial Plating",
                                       controller=1)
        _add_to_battlefield(game, card_db, "Memnite", controller=1)
        _add_to_battlefield(game, card_db, "Memnite", controller=1)
        _attach_equipment(plating, frogmite)

        # Sanity — plating bonus is ≥ 3 on the 4-artifact board
        assert frogmite.power >= 5, (
            f"test setup: Frogmite power={frogmite.power}, expected ≥5 "
            f"with Plating + 4 artifacts"
        )

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        opp = game.players[1]
        bonus = player._attacker_equipment_bonus(game, opp, frogmite)
        assert bonus >= 3, (
            f"_attacker_equipment_bonus must detect plating on Frogmite, "
            f"got {bonus}"
        )

        blocks = player.decide_blockers(game, [frogmite])
        # No chump into the plated attacker (no answer in hand).
        # Guide may or may not be assigned; the specific assertion is that
        # Frogmite is not blocked.
        assert frogmite.instance_id not in blocks, (
            f"expected NO chump into plated Frogmite (no answer in hand). "
            f"Got {blocks}."
        )


class TestChumpPlatedAttackerIfWearTearInHand:
    def test_plated_frogmite_chumped_when_answer_in_hand(self, card_db):
        """Same plating setup, but we have Nature's Claim in hand (destroys
        target artifact/enchantment). Equipment is breakable → chump is
        worthwhile because the plating goes away next turn."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 8
        game.players[1].life = 20

        guide = _add_to_battlefield(game, card_db, "Guide of Souls",
                                     controller=0)
        frogmite = _add_to_battlefield(game, card_db, "Frogmite", controller=1)
        plating = _add_to_battlefield(game, card_db, "Cranial Plating",
                                       controller=1)
        _add_to_battlefield(game, card_db, "Memnite", controller=1)
        _add_to_battlefield(game, card_db, "Memnite", controller=1)
        _attach_equipment(plating, frogmite)

        # Nature's Claim: "Destroy target artifact or enchantment."
        # Has tag 'removal' and oracle contains 'destroy target artifact'.
        _add_to_hand(game, card_db, "Nature's Claim", controller=0)

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        me = game.players[0]
        assert player._equipment_breakable(game, me), (
            "Nature's Claim in hand must be detected as equipment-breakable"
        )

        blocks = player.decide_blockers(game, [frogmite])
        assert frogmite.instance_id in blocks, (
            f"expected chump block against plated Frogmite (answer in hand "
            f"makes chumping worthwhile). Got {blocks}."
        )


class TestChumpPlatedAttackerIfBlockingPreventsLethal:
    def test_plated_attacker_chumped_when_lethal_incoming(self, card_db):
        """Same plating setup, but me at life=5 and the plated attacker
        represents lethal this turn (5 power ≥ life). Projection yields
        to survival — chump regardless of plating."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 5
        game.players[1].life = 20

        guide = _add_to_battlefield(game, card_db, "Guide of Souls",
                                     controller=0)
        frogmite = _add_to_battlefield(game, card_db, "Frogmite", controller=1)
        plating = _add_to_battlefield(game, card_db, "Cranial Plating",
                                       controller=1)
        _add_to_battlefield(game, card_db, "Memnite", controller=1)
        _add_to_battlefield(game, card_db, "Memnite", controller=1)
        _attach_equipment(plating, frogmite)

        assert frogmite.power >= 5, "setup: plated Frogmite must be ≥ 5 power"

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        blocks = player.decide_blockers(game, [frogmite])
        assert frogmite.instance_id in blocks, (
            f"at life=5, lethal plated attacker MUST be chumped even without "
            f"a removal answer (survival beats projection). Got {blocks}."
        )


class TestEquipmentBonusDetectionOracleDriven:
    def test_regex_finds_plating_bonus_no_hardcoded_names(self, card_db):
        """Regression: detection is oracle-regex-based; it must find the
        bonus on any equipment granting '+X/+Y'. Use Colossus Hammer
        (not named in the code) on a Memnite, and Cranial Plating on a
        Frogmite with several artifacts."""
        game = GameState(rng=random.Random(0))

        # Case 1: Colossus Hammer on Memnite — "+10/+10"
        memnite = _add_to_battlefield(game, card_db, "Memnite", controller=1)
        hammer = _add_to_battlefield(game, card_db, "Colossus Hammer",
                                      controller=1)
        _attach_equipment(hammer, memnite)

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        opp = game.players[1]
        hammer_bonus = player._attacker_equipment_bonus(game, opp, memnite)
        assert hammer_bonus >= 10, (
            f"Colossus Hammer grants +10/+10; regex must detect. Got "
            f"{hammer_bonus} on Memnite."
        )

        # Case 2: plating + 4 artifacts scaling
        game2 = GameState(rng=random.Random(0))
        frogmite = _add_to_battlefield(game2, card_db, "Frogmite",
                                        controller=1)
        plating = _add_to_battlefield(game2, card_db, "Cranial Plating",
                                       controller=1)
        _add_to_battlefield(game2, card_db, "Memnite", controller=1)
        _add_to_battlefield(game2, card_db, "Memnite", controller=1)
        _attach_equipment(plating, frogmite)
        opp2 = game2.players[1]
        plating_bonus = player._attacker_equipment_bonus(game2, opp2, frogmite)
        # 4 artifacts (Frogmite, Plating, Memnite, Memnite) × +1 = +4
        assert plating_bonus >= 3, (
            f"Cranial Plating 'for each artifact' scaling not detected "
            f"correctly; got {plating_bonus}, expected ≥3."
        )

        # Regex itself is a simple sanity
        m = _EQUIP_BONUS_RE.search(
            "equipped creature gets +2/+2 and has trample."
        )
        assert m is not None
        assert m.group(2) == "2" and m.group(3) == "2"


class TestIntrinsicScalingDetection:
    """Construct Tokens from Urza's Saga scale via their OWN oracle text
    ('this creature gets +1/+1 for each artifact you control'), not via
    equipment. The projection must include these — chumping one still
    means the saga keeps spawning more with the same scaling."""

    def test_intrinsic_artifact_scaling_detected_via_oracle(self, card_db):
        """Regression: extend _attacker_equipment_bonus to pick up
        intrinsic '+X/+Y for each artifact you control' on the attacker's
        own template. Uses a real card with this pattern."""
        # Urza's Saga doesn't exist as a battlefield card in our mini test
        # context and Construct Token is a dynamically-created template.
        # Use Steel Overseer-adjacent? No — pick a card whose oracle has the
        # intrinsic pattern. Shambling Suit matches:
        #   "Shambling Suit's power is equal to the number of other
        #    artifacts and/or creatures you control."
        # Not the exact pattern. Try Tempered Steel or Etched Champion.
        # Simplest: directly construct a CardInstance with a synthetic
        # template using an oracle snippet containing the pattern.
        from engine.cards import CardTemplate, CardType

        tmpl = CardTemplate(
            name="Synthetic Construct",
            card_types=[CardType.ARTIFACT, CardType.CREATURE],
            mana_cost=None,
            supertypes=[], subtypes=["Construct"],
            power=0, toughness=0, loyalty=None,
            keywords=set(), abilities=[],
            color_identity=set(), produces_mana=[],
            enters_tapped=False,
            oracle_text=(
                "This creature gets +1/+1 for each artifact you control."
            ),
            tags=set(),
        )
        game = GameState(rng=random.Random(0))
        from engine.cards import CardInstance
        attacker = CardInstance(
            template=tmpl, owner=1, controller=1,
            instance_id=game.next_instance_id(),
            zone="battlefield",
        )
        attacker._game_state = game
        game.players[1].battlefield.append(attacker)
        # Add four artifacts on opp's board
        for _ in range(4):
            _add_to_battlefield(game, card_db, "Memnite", controller=1)

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        opp = game.players[1]
        bonus = player._attacker_equipment_bonus(game, opp, attacker)
        # 4 artifacts (Memnite x4) + the synthetic construct itself = 5
        assert bonus >= 4, (
            f"intrinsic '+1/+1 for each artifact you control' scaling must "
            f"be detected on attacker's own oracle. Got {bonus}, expected ≥4."
        )
