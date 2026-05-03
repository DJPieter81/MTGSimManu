"""Thraben Charm — modal damage mode is the missing oracle mode.

Oracle text (https://scryfall.com):

    Choose one —
    • Thraben Charm deals damage equal to twice the number of creatures
      you control to target creature.
    • Destroy target enchantment.
    • Exile any number of target players' graveyards.

The engine implementation in ``engine/card_effects.py`` previously
omitted the damage mode entirely and substituted a fake "all creatures
get -1/-1" mode that does not appear on the card.  This made Thraben
Charm useless against artifact-dense boards (Affinity, Pinnacle
Affinity, Eldrazi Tron) where there is no opponent enchantment to
remove and the only winning play is to kill an equipped 1-toughness
creature like Memnite (becomes 9/1 with Cranial Plating but the
toughness stays at 1).

Each test names the **rule** under test, not Thraben Charm specifically;
the same modal-spell pattern (damage / destroy-permanent / hate)
recurs across `Boros Charm`, `Izzet Charm`, etc.
"""
from __future__ import annotations

from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardType
from engine.game_state import GameState
import pytest
import random


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


def _attach_equipment(equipment, creature):
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


def _resolve_thraben_charm(game, controller, target_id=None):
    """Resolve Thraben Charm with the engine effect registry, mimicking
    the cast flow that supplies a target id."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    targets = [target_id] if target_id is not None else None
    ok = EFFECT_REGISTRY.execute(
        "Thraben Charm", EffectTiming.SPELL_RESOLVE,
        game, None, controller, targets=targets, item=None)
    assert ok, "Thraben Charm effect not registered"


class TestThrabenCharmDamageMode:
    """Damage mode = 2 * (controller's creatures) damage to chosen target."""

    def test_damage_mode_kills_one_toughness_creature_with_three_creatures(
            self, card_db):
        """Rule: with 3 creatures, damage mode deals 6 to a target.  A
        1-toughness creature dies regardless of attached power-pump
        equipment (Cranial Plating bumps power, not toughness)."""
        game = GameState(rng=random.Random(42))

        # Caster (P1) has 3 creatures
        for _ in range(3):
            _add_to_battlefield(game, card_db, "Ocelot Pride", 0)

        # Opp (P2) has Memnite (1/1) equipped with Cranial Plating
        memnite = _add_to_battlefield(game, card_db, "Memnite", 1)
        plating = _add_to_battlefield(game, card_db, "Cranial Plating", 1)
        _attach_equipment(plating, memnite)

        # No opp enchantment, < 10 graveyard CMC, < 2 1-toughness opp tokens
        # so the buggy "destroy enchantment" / "exile graveyard" / fake
        # "-1/-1" branches all skip and damage mode must be chosen.
        assert memnite in game.players[1].battlefield

        _resolve_thraben_charm(game, controller=0, target_id=memnite.instance_id)

        # The rule under test: 2 * 3 = 6 damage to Memnite (toughness 1) → dies.
        assert memnite not in game.players[1].battlefield, (
            "damage mode missing: Thraben Charm with 3 creatures must kill "
            "a 1-toughness target")
        assert memnite in game.players[1].graveyard

    def test_damage_mode_does_not_kill_high_toughness_target(self, card_db):
        """Rule: damage scales linearly; a target with toughness > 2N
        survives even when targeted."""
        game = GameState(rng=random.Random(42))

        for _ in range(2):
            _add_to_battlefield(game, card_db, "Ocelot Pride", 0)
        # 2 creatures → 4 damage

        # Construct Token analogue: pick a 5/5 creature so 4 dmg < toughness.
        big = _add_to_battlefield(game, card_db, "Frogmite", 1)  # 2/2
        # Boost toughness via plating-equivalent? Just pick something with
        # natural high toughness — Sojourner's Companion is 4/4 base.
        big2 = _add_to_battlefield(game, card_db, "Sojourner's Companion", 1)
        # Sojourner's Companion is 4/4; 4 dmg lethal exactly → dies.
        # Use a 5+ toughness target instead.
        # Frogmite (2/2): 4 dmg lethal → dies. Need a creature with
        # toughness > 4. Try Archon of Cruelty (6/6).
        archon = _add_to_battlefield(
            game, card_db, "Archon of Cruelty", 1)

        _resolve_thraben_charm(game, controller=0, target_id=archon.instance_id)
        assert archon in game.players[1].battlefield, (
            "4 damage must not kill 6-toughness Archon")

    def test_damage_mode_picks_highest_threat_killable_target_when_no_target_supplied(
            self, card_db):
        """Rule: when caller does not supply a target, the engine picks
        the most threatening *killable* creature.  Killable means
        2N >= effective_toughness."""
        game = GameState(rng=random.Random(42))

        for _ in range(3):
            _add_to_battlefield(game, card_db, "Ocelot Pride", 0)
        # 6 damage available

        # Two opp creatures: a low-threat 1/1 and a high-threat
        # 1-toughness pumped attacker.  Damage mode kills both, but
        # the pumped attacker has higher threat value → must be chosen.
        memnite = _add_to_battlefield(game, card_db, "Memnite", 1)
        plating = _add_to_battlefield(game, card_db, "Cranial Plating", 1)
        _attach_equipment(plating, memnite)
        ornithopter = _add_to_battlefield(game, card_db, "Ornithopter", 1)

        _resolve_thraben_charm(game, controller=0, target_id=None)

        # Higher-threat target (Memnite with Plating ≈ 9/1) goes; the
        # low-threat 0/2 Ornithopter should not be the auto-pick.
        assert memnite in game.players[1].graveyard, (
            "auto-target picked the lower-threat creature when a "
            "higher-threat killable creature was available")


class TestThrabenCharmEnchantmentMode:
    """Enchantment mode is the existing behaviour and must still work
    when no killable creature target is more valuable."""

    def test_enchantment_mode_destroys_target_enchantment_when_no_creature_target(
            self, card_db):
        """Regression: the previous default-mode (destroy enchantment)
        must still fire when caller targets an enchantment and damage
        mode would not provide more value."""
        game = GameState(rng=random.Random(42))

        # Caster has 0 creatures → damage mode does 0 damage and is
        # never preferred.
        leyline = _add_to_battlefield(
            game, card_db, "Leyline of Sanctity", 1)
        # Verify it's actually classified as an enchantment in the DB.
        assert CardType.ENCHANTMENT in leyline.template.card_types

        _resolve_thraben_charm(
            game, controller=0, target_id=leyline.instance_id)

        assert leyline not in game.players[1].battlefield, (
            "enchantment mode regression — Leyline of Sanctity should be "
            "destroyed when targeted")
