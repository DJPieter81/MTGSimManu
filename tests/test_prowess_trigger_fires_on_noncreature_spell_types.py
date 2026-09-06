"""CR 702.107 — prowess fires on ANY noncreature spell.

# Mechanic under test

"Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn."

The trigger condition is spelled out in CR 702.107a: the spell cast must be a
noncreature spell.  That means ALL of the following spell types trigger prowess:

  - Instant
  - Sorcery
  - Artifact (non-creature)
  - Enchantment
  - Planeswalker

And none of these trigger prowess:

  - Creature spells (CardType.CREATURE in spell.template.card_types)
  - Artifact creature spells (both ARTIFACT and CREATURE)

Additional rules verified here:

  CR 702.107 stacking: each separate noncreature spell cast in a turn
  gives +1/+1 independently — two spells in the same turn give +2/+2 total.

  CR 702.107 duration: "+1/+1 until end of turn" means the bonus wears off
  during the cleanup step when `cleanup_damage()` resets `temp_power_mod`.

Implementation in engine/cast_manager.py (CastManager.cast_spell):
  The trigger guard is ``if not template.is_creature:`` where ``template``
  is the spell being cast.  This correctly covers all noncreature types
  without gating on spell subtype.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType, Keyword
from engine.game_state import GameState
from engine.mana import ManaCost


# ─── helpers ─────────────────────────────────────────────────────────


def _fresh_game() -> GameState:
    return GameState(rng=random.Random(0))


def _prowess_creature_template(name: str = "Test Prowess Creature") -> CardTemplate:
    return CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[],
        power=1, toughness=1, loyalty=None,
        keywords={Keyword.PROWESS},
        abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text=(
            "Prowess (Whenever you cast a noncreature spell, "
            "this creature gets +1/+1 until end of turn.)"
        ),
        tags=set(),
    )


def _prowess_creature_on_battlefield(
    game: GameState, controller: int = 0
) -> CardInstance:
    """Put a creature with the PROWESS keyword onto the battlefield."""
    tmpl = _prowess_creature_template()
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _spell_in_hand(
    game: GameState,
    name: str,
    card_types: list,
    controller: int = 0,
    power: int | None = None,
    toughness: int | None = None,
) -> CardInstance:
    """Create a spell of the given types and put it in the controller's hand."""
    tmpl = CardTemplate(
        name=name,
        card_types=card_types,
        mana_cost=ManaCost(generic=0),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


# ─── tests ───────────────────────────────────────────────────────────


class TestProwessTriggerFiresOnNoncreatureSpellTypes:

    def test_prowess_triggers_on_artifact_spell(self):
        """CR 702.107: casting a noncreature artifact spell triggers prowess.

        Mechanic: the trigger guard is ``not template.is_creature``, which is
        True for any spell whose card_types do not include CardType.CREATURE.
        Artifact-only spells (e.g. Mishra's Bauble, Springleaf Drum) satisfy
        this condition and must give the prowess creature +1/+1.
        """
        game = _fresh_game()
        creature = _prowess_creature_on_battlefield(game)
        artifact_spell = _spell_in_hand(game, "Test Artifact", [CardType.ARTIFACT])
        assert not artifact_spell.template.is_creature  # pre-condition

        before_power = creature.temp_power_mod
        before_tough = creature.temp_toughness_mod

        game.cast_spell(0, artifact_spell, free_cast=True)

        assert creature.temp_power_mod == before_power + 1, (
            "prowess creature must gain +1 power when a noncreature artifact is cast"
        )
        assert creature.temp_toughness_mod == before_tough + 1, (
            "prowess creature must gain +1 toughness when a noncreature artifact is cast"
        )

    def test_prowess_triggers_on_enchantment_spell(self):
        """CR 702.107: casting a noncreature enchantment spell triggers prowess.

        Enchantments are noncreature permanents; their spell on the stack has
        CardType.ENCHANTMENT and not CardType.CREATURE, so ``not is_creature``
        is True and prowess must fire.
        """
        game = _fresh_game()
        creature = _prowess_creature_on_battlefield(game)
        enchantment_spell = _spell_in_hand(
            game, "Test Enchantment", [CardType.ENCHANTMENT]
        )
        assert not enchantment_spell.template.is_creature  # pre-condition

        game.cast_spell(0, enchantment_spell, free_cast=True)

        assert creature.temp_power_mod == 1
        assert creature.temp_toughness_mod == 1

    def test_prowess_triggers_on_instant_spell(self):
        """CR 702.107: casting an instant spell triggers prowess."""
        game = _fresh_game()
        creature = _prowess_creature_on_battlefield(game)
        instant_spell = _spell_in_hand(game, "Test Instant", [CardType.INSTANT])

        game.cast_spell(0, instant_spell, free_cast=True)

        assert creature.temp_power_mod == 1
        assert creature.temp_toughness_mod == 1

    def test_prowess_triggers_on_sorcery_spell(self):
        """CR 702.107: casting a sorcery spell triggers prowess."""
        game = _fresh_game()
        creature = _prowess_creature_on_battlefield(game)
        sorcery_spell = _spell_in_hand(game, "Test Sorcery", [CardType.SORCERY])

        game.cast_spell(0, sorcery_spell, free_cast=True)

        assert creature.temp_power_mod == 1
        assert creature.temp_toughness_mod == 1

    def test_prowess_does_not_trigger_on_creature_spell(self):
        """CR 702.107: creature spells do NOT trigger prowess.

        The trigger text is 'whenever you cast a NONCREATURE spell'.  Casting
        a creature spell must leave temp_power_mod unchanged at 0.
        """
        game = _fresh_game()
        creature = _prowess_creature_on_battlefield(game)
        creature_spell = _spell_in_hand(
            game, "Test Creature Spell", [CardType.CREATURE],
            power=2, toughness=2
        )
        assert creature_spell.template.is_creature  # pre-condition

        game.cast_spell(0, creature_spell, free_cast=True)

        assert creature.temp_power_mod == 0, (
            "prowess must NOT trigger when a creature spell is cast"
        )
        assert creature.temp_toughness_mod == 0

    def test_prowess_does_not_trigger_on_artifact_creature_spell(self):
        """CR 702.107: artifact creature spells (e.g. Ornithopter) do NOT trigger prowess.

        A spell with both CardType.ARTIFACT and CardType.CREATURE is a creature
        spell — ``template.is_creature`` checks only for CardType.CREATURE in
        card_types, so the presence of ARTIFACT does not override the check.
        """
        game = _fresh_game()
        creature = _prowess_creature_on_battlefield(game)
        artifact_creature = _spell_in_hand(
            game, "Test Artifact Creature", [CardType.ARTIFACT, CardType.CREATURE],
            power=0, toughness=2
        )
        assert artifact_creature.template.is_creature  # pre-condition

        game.cast_spell(0, artifact_creature, free_cast=True)

        assert creature.temp_power_mod == 0, (
            "artifact creatures are creature spells and must NOT trigger prowess"
        )

    def test_prowess_stacks_once_per_spell_in_same_turn(self):
        """CR 702.107: each noncreature spell cast gives a separate +1/+1 trigger.

        Two noncreature spells in the same turn → +2/+2 accumulated on the
        prowess creature, not +1/+1 (one-time cap).  The mechanic is additive:
        temp_power_mod accumulates per-spell-cast, so the second cast increments
        from 1 to 2.
        """
        game = _fresh_game()
        creature = _prowess_creature_on_battlefield(game)
        spell_a = _spell_in_hand(game, "Test Spell A", [CardType.INSTANT])
        spell_b = _spell_in_hand(game, "Test Spell B", [CardType.SORCERY])

        game.cast_spell(0, spell_a, free_cast=True)
        assert creature.temp_power_mod == 1, "first spell → +1"

        game.cast_spell(0, spell_b, free_cast=True)
        assert creature.temp_power_mod == 2, (
            "second noncreature spell must give a second +1 (stacks to +2, not capped at +1)"
        )
        assert creature.temp_toughness_mod == 2

    def test_prowess_bonus_resets_at_cleanup(self):
        """CR 702.107: +1/+1 is 'until end of turn'; cleanup_damage resets it.

        cleanup_damage() is called for every creature at the cleanup step
        (CR 514), setting temp_power_mod and temp_toughness_mod back to 0.
        """
        game = _fresh_game()
        creature = _prowess_creature_on_battlefield(game)
        spell = _spell_in_hand(game, "Test Spell", [CardType.INSTANT])

        game.cast_spell(0, spell, free_cast=True)
        assert creature.temp_power_mod == 1  # prowess fired

        # Simulate end-of-turn cleanup for this creature
        creature.cleanup_damage()

        assert creature.temp_power_mod == 0, (
            "prowess bonus must be removed by cleanup_damage (until end of turn)"
        )
        assert creature.temp_toughness_mod == 0

    def test_prowess_does_not_trigger_for_opponent_spells(self):
        """CR 702.107: prowess only fires for spells cast by the creature's controller.

        The trigger checks ``player.creatures`` — the list of creatures the
        CASTER controls.  When opponent (player 1) casts a spell, the loop
        in cast_spell runs over player 1's creatures, not player 0's prowess
        creature.
        """
        game = _fresh_game()
        # Player 0 controls the prowess creature
        creature = _prowess_creature_on_battlefield(game, controller=0)
        # Player 1 casts a noncreature spell
        opp_spell = _spell_in_hand(
            game, "Opponent Instant", [CardType.INSTANT], controller=1
        )

        game.cast_spell(1, opp_spell, free_cast=True)

        assert creature.temp_power_mod == 0, (
            "prowess on player 0's creature must not trigger when player 1 casts a spell"
        )


def _unrelated_pump_creature_on_battlefield(
    game: GameState, controller: int = 0
) -> CardInstance:
    """A creature shaped like Dragon's Rage Channeler: a real 'whenever you
    cast a noncreature spell' trigger whose OWN effect has no +N/+M (it only
    surveils), plus an unrelated static elsewhere in the oracle text that
    happens to contain a '+N/+N' pattern (a delirium-style condition, here
    literally modelled on DRC's own printed text)."""
    tmpl = CardTemplate(
        name="Test Delirium Surveiller",
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[],
        power=1, toughness=1, loyalty=None,
        keywords=set(),
        abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text=(
            "Whenever you cast a noncreature spell, surveil 1. "
            "Delirium — As long as there are four or more card types "
            "among cards in your graveyard, this creature gets +2/+2, "
            "has flying, and attacks each combat if able."
        ),
        tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


class TestProwessLikeDetectorAnchorsToTheCastTriggerClause:
    """The generic 'prowess-like trigger' detector in CastManager.cast_spell
    gates on the SUBSTRING 'noncreature spell' appearing anywhere in a
    creature's oracle text, then searches the ENTIRE oracle text for a
    '+N/+N' pattern to decide the pump amount. A creature whose real
    'whenever you cast a noncreature spell' trigger has no P/T effect of its
    own (e.g. it only surveils), but which separately prints an unrelated
    '+N/+N' static (delirium, oil counters, an anthem) elsewhere in its
    text, must NOT receive that unrelated bonus on every spell cast — the
    pump has to be textually anchored to the actual cast-trigger clause, not
    merely co-present anywhere in the oracle text blob.

    Modern class: at least 11 real cards hit this exact shape, including
    Dragon's Rage Channeler (Izzet Prowess) — see the 2026-08-31 replay
    audit doc for the enumeration and the replay evidence.
    """

    def test_unrelated_pump_text_elsewhere_in_oracle_is_not_applied_per_cast(self):
        """CR 702.107 does not apply here at all: this creature has no
        printed Prowess keyword and no 'whenever you cast ... gets +N/+N'
        clause. Its only cast-triggered effect is a surveil with no P/T
        component, so temp_power_mod must stay at 0 after a noncreature
        spell resolves — the delirium '+2/+2' text belongs to a static
        condition, not a per-cast trigger, and (being false here — the
        creature controls no graveyard cards of any type) is not even
        active as a static.
        """
        game = _fresh_game()
        creature = _unrelated_pump_creature_on_battlefield(game)
        spell = _spell_in_hand(game, "Test Instant", [CardType.INSTANT])

        game.cast_spell(0, spell, free_cast=True)

        assert creature.temp_power_mod == 0, (
            "an unrelated static '+N/+N' clause elsewhere in the oracle text "
            "(e.g. a delirium condition) must not be re-applied as a "
            "per-spell-cast pump just because the text also contains "
            "'noncreature spell' from a DIFFERENT, unrelated trigger"
        )
        assert creature.temp_toughness_mod == 0

    def test_unrelated_pump_text_does_not_stack_across_multiple_casts(self):
        """Same shape as above, but casting THREE noncreature spells in one
        turn must not accumulate +2/+2 x 3 = +6/+6 the way the un-anchored
        regex does today (this is the exact mechanism that produced a 7/7
        Dragon's Rage Channeler from a 1/1 base off of 3 unrelated spell
        casts in a live Bo3 replay — see the audit doc)."""
        game = _fresh_game()
        creature = _unrelated_pump_creature_on_battlefield(game)
        spell_a = _spell_in_hand(game, "Test Spell A", [CardType.INSTANT])
        spell_b = _spell_in_hand(game, "Test Spell B", [CardType.SORCERY])
        spell_c = _spell_in_hand(game, "Test Spell C", [CardType.ARTIFACT])

        game.cast_spell(0, spell_a, free_cast=True)
        game.cast_spell(0, spell_b, free_cast=True)
        game.cast_spell(0, spell_c, free_cast=True)

        assert creature.temp_power_mod == 0, (
            "three unrelated-trigger casts must not accumulate the "
            "delirium clause's +2/+2 into a +6/+6 temp bonus"
        )
        assert creature.temp_toughness_mod == 0
