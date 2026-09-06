"""CR 603.2 — ORDINAL cast triggers: "whenever you cast your Nth spell each turn".

# Mechanic under test

A cast trigger whose condition is not "a spell of type X" but "the Nth
spell you cast this turn".  The condition is a per-turn ORDINAL over the
controller's own spell count:

    "Whenever you cast your second spell each turn, <effect>"
    "Whenever a player casts their second spell each turn, <effect>"
    "Whenever an opponent casts their first spell each turn, <effect>"

Rules the class must obey (CR 603.2, CR 500.8):

  1. It fires on the Nth spell REGARDLESS of that spell's card type — a
     creature spell is as good as an instant.
  2. It fires EXACTLY ONCE per turn: spells N+1, N+2 … do not re-fire it.
  3. The counter's reset scope is the TURN, not the game — every turn each
     player starts a fresh count, so the trigger fires again next turn.
  4. A "you cast" ordinal counts only its CONTROLLER's spells; spells cast
     by the other player neither advance nor satisfy it.

# Class size (measured against ModernAtomic, 22,506 cards)

  45 cards carry an ordinal cast TRIGGER (37 "you", 4 "a player", 4 "an
  opponent"); 9 of those create a token.  4 more carry the ordinal as a
  static COST REDUCTION ("the second spell you cast each turn costs {1}
  less") — a different mechanic, deliberately not handled here.  Distinct
  and untouched by this class: 25 magecraft cards and 757 plain
  "whenever you cast a/an/another <type> spell" triggers.

# The bug this pins

`parse_cast_trigger_token`'s regex cannot see an ordinal condition, so on
a card whose token has prowess it matched the TOKEN'S REMINDER TEXT
("(Whenever you cast a noncreature spell, the token gets +1/+1 …)")
instead — the right effect off the wrong sentence.  Symptoms: the trigger
fired on every noncreature spell (over-trigger) and never on a creature
spell (under-trigger), and the "then attach this Equipment to it" rider
never ran at all.

Card names below are fixture carriers only.  The rule under test is
"an ordinal cast trigger fires only on the Nth spell of a turn".
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState
from engine.mana import ManaCost
from engine.oracle_parser import (parse_cast_trigger_token,
                                  parse_ordinal_cast_trigger)


# ─── helpers ─────────────────────────────────────────────────────────


def _fresh_game() -> GameState:
    return GameState(rng=random.Random(0))


def _on_battlefield(game: GameState, tmpl: CardTemplate,
                    controller: int = 0) -> CardInstance:
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _spell_in_hand(game: GameState, name: str, card_types: list,
                   controller: int = 0) -> CardInstance:
    """A free-to-cast spell of the given types in `controller`'s hand."""
    tmpl = CardTemplate(
        name=name,
        card_types=card_types,
        mana_cost=ManaCost(generic=0),
        supertypes=[], subtypes=[],
        power=1 if CardType.CREATURE in card_types else None,
        toughness=1 if CardType.CREATURE in card_types else None,
        loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _tokens(game: GameState, controller: int):
    return [c for c in game.players[controller].battlefield
            if getattr(c, "is_token", False)]


def _cast(game: GameState, controller: int, spell: CardInstance) -> None:
    """Cast through the REAL entry point (CastManager), which is what
    increments the per-turn spell counter and runs cast triggers.  A probe
    that calls `resolve_spell_cast_trigger` directly bypasses the counter
    and cannot see an ordinal condition at all."""
    game.cast_spell(controller, spell, free_cast=True)


# ─── fixtures: the two carrier shapes ────────────────────────────────


@pytest.fixture
def ordinal_token_equipment(card_db):
    """Equipment whose ordinal trigger makes a token AND attaches itself."""
    return card_db.get_card("Cori-Steel Cutter")


@pytest.fixture
def ordinal_token_creature(card_db):
    """Creature carrying the same ordinal trigger with no attach rider —
    proves the dispatch is not equipment-shaped."""
    return card_db.get_card("Clarion Spirit")


# ─── parse layer ─────────────────────────────────────────────────────


class TestOrdinalCastTriggerParsedOnce:

    def test_ordinal_and_reset_scope_are_typed_fields(self,
                                                      ordinal_token_equipment):
        """The ordinal (which spell) and its reset scope (per turn, not per
        game) are parsed once at DB load into a typed field."""
        spec = ordinal_token_equipment.ordinal_cast_trigger
        assert spec is not None, "ordinal cast trigger not parsed at DB load"
        assert spec["ordinal"] == 2
        assert spec["reset"] == "turn", (
            "an ordinal cast trigger's counter resets each TURN (CR 500.8), "
            "not once per game")
        assert spec["caster_scope"] == "you"

    def test_ordinal_condition_is_not_read_off_token_reminder_text(
            self, ordinal_token_equipment):
        """The trigger's spell condition must come from the card's own rules
        text.  Reminder text in parentheses describes the TOKEN's ability, so
        a token-with-prowess card must not be classified as a
        "whenever you cast a noncreature spell" trigger."""
        spec = parse_cast_trigger_token(ordinal_token_equipment.oracle_text)
        assert spec is not None, "the ordinal trigger still creates a token"
        assert "noncreature" not in spec["spell_types"], (
            "spell-type condition was matched off the token's prowess "
            "reminder text instead of the card's own trigger")

    def test_ordinal_trigger_qualifies_on_any_spell_type(
            self, ordinal_token_equipment):
        """An ordinal condition names no card type — every spell type
        qualifies, and the ordinal alone decides."""
        spec = parse_cast_trigger_token(ordinal_token_equipment.oracle_text)
        assert spec["spell_types"] == frozenset({"any"})

    def test_plain_and_magecraft_cast_triggers_are_a_different_shape(self):
        """Boundary: the ordinal parse must not claim plain
        "whenever you cast a <type> spell" or magecraft text."""
        assert parse_ordinal_cast_trigger(
            "Whenever you cast a noncreature spell, create a 1/1 white "
            "Monk creature token with prowess.") is None
        assert parse_ordinal_cast_trigger(
            "Magecraft — Whenever you cast or copy an instant or sorcery "
            "spell, this creature gets +1/+1 until end of turn.") is None

    def test_ordinal_cost_reduction_is_not_a_trigger(self):
        """Boundary: "the second spell you cast each turn costs {1} less"
        is a static cost effect, not a triggered ability."""
        assert parse_ordinal_cast_trigger(
            "The second spell you cast each turn costs {1} less to cast."
        ) is None


# ─── dispatch layer ──────────────────────────────────────────────────


class TestOrdinalCastTriggerFiresOnNthSpell:

    def test_fires_exactly_once_per_turn_on_the_nth_spell(
            self, ordinal_token_equipment):
        """CR 603.2: the trigger condition is met by the Nth spell only.
        Spells 1, 3 and 4 of the same turn do not meet it."""
        game = _fresh_game()
        _on_battlefield(game, ordinal_token_equipment, 0)

        _cast(game, 0, _spell_in_hand(game, "Spell 1", [CardType.INSTANT]))
        assert len(_tokens(game, 0)) == 0, "fired on the FIRST spell"

        _cast(game, 0, _spell_in_hand(game, "Spell 2", [CardType.INSTANT]))
        assert len(_tokens(game, 0)) == 1, "did not fire on the SECOND spell"

        _cast(game, 0, _spell_in_hand(game, "Spell 3", [CardType.INSTANT]))
        _cast(game, 0, _spell_in_hand(game, "Spell 4", [CardType.SORCERY]))
        assert len(_tokens(game, 0)) == 1, (
            "over-triggered: an ordinal trigger fires once per turn, not on "
            "every subsequent spell")

    def test_fires_when_the_nth_spell_is_a_creature_spell(
            self, ordinal_token_equipment):
        """An ordinal condition names no card type, so a creature spell in
        the Nth slot triggers it exactly like an instant would."""
        game = _fresh_game()
        _on_battlefield(game, ordinal_token_equipment, 0)

        _cast(game, 0, _spell_in_hand(game, "Spell 1", [CardType.INSTANT]))
        creature = _spell_in_hand(game, "Spell 2", [CardType.CREATURE])
        assert creature.template.is_creature  # pre-condition
        _cast(game, 0, creature)

        assert len(_tokens(game, 0)) == 1, (
            "under-triggered: a creature spell in the Nth slot must satisfy "
            "an ordinal cast trigger")

    def test_counter_resets_each_turn(self, ordinal_token_equipment):
        """CR 500.8: "each turn" means the count restarts every turn, so the
        trigger fires again on next turn's Nth spell."""
        game = _fresh_game()
        _on_battlefield(game, ordinal_token_equipment, 0)

        _cast(game, 0, _spell_in_hand(game, "T1 spell 1", [CardType.INSTANT]))
        _cast(game, 0, _spell_in_hand(game, "T1 spell 2", [CardType.INSTANT]))
        assert len(_tokens(game, 0)) == 1

        game.players[0].reset_turn_tracking()

        _cast(game, 0, _spell_in_hand(game, "T2 spell 1", [CardType.INSTANT]))
        assert len(_tokens(game, 0)) == 1, "re-fired on the new turn's FIRST spell"
        _cast(game, 0, _spell_in_hand(game, "T2 spell 2", [CardType.INSTANT]))
        assert len(_tokens(game, 0)) == 2, (
            "the per-turn spell counter did not reset — the trigger must fire "
            "again on the next turn's Nth spell")

    def test_counts_only_the_controllers_own_spells(self,
                                                    ordinal_token_equipment):
        """A "whenever YOU cast" ordinal counts its controller's spells
        only; the opponent's spells neither advance nor satisfy it."""
        game = _fresh_game()
        _on_battlefield(game, ordinal_token_equipment, 0)

        _cast(game, 1, _spell_in_hand(game, "Opp 1", [CardType.INSTANT], 1))
        _cast(game, 1, _spell_in_hand(game, "Opp 2", [CardType.INSTANT], 1))
        assert len(_tokens(game, 0)) == 0, "opponent's spells fired a 'you cast' trigger"
        assert len(_tokens(game, 1)) == 0

        _cast(game, 0, _spell_in_hand(game, "Mine 1", [CardType.INSTANT]))
        assert len(_tokens(game, 0)) == 0, (
            "opponent's spells advanced the controller's own ordinal count")
        _cast(game, 0, _spell_in_hand(game, "Mine 2", [CardType.INSTANT]))
        assert len(_tokens(game, 0)) == 1

    def test_token_characteristics_come_from_the_triggers_own_clause(
            self, ordinal_token_equipment):
        """The token's printed characteristics are read from the trigger's
        own sentence, so a card with several abilities cannot contribute a
        stray token spec — and the clause keeps the capitalisation the
        subtype parse needs."""
        from engine.cards import Keyword

        game = _fresh_game()
        _on_battlefield(game, ordinal_token_equipment, 0)
        _cast(game, 0, _spell_in_hand(game, "Spell 1", [CardType.INSTANT]))
        _cast(game, 0, _spell_in_hand(game, "Spell 2", [CardType.INSTANT]))

        token = _tokens(game, 0)[0]
        assert "Monk" in token.name, f"token subtype lost: {token.name!r}"
        assert (token.template.power, token.template.toughness) == (1, 1)
        assert Keyword.PROWESS in token.template.keywords

    def test_dispatch_is_not_equipment_shaped(self, ordinal_token_creature):
        """The same ordinal dispatch serves a creature with no equip cost
        and no attach rider — proof the mechanic is the ordinal, not the
        card type of its source."""
        game = _fresh_game()
        _on_battlefield(game, ordinal_token_creature, 0)

        _cast(game, 0, _spell_in_hand(game, "Spell 1", [CardType.SORCERY]))
        assert len(_tokens(game, 0)) == 0
        _cast(game, 0, _spell_in_hand(game, "Spell 2", [CardType.CREATURE]))
        assert len(_tokens(game, 0)) == 1


class TestPlainCastTriggersAreUndisturbed:
    """Boundary guard: the 757-card plain-cast class keeps firing on EVERY
    qualifying spell — an ordinal gate must not leak onto it."""

    def test_plain_noncreature_cast_trigger_fires_on_each_spell(self, card_db):
        game = _fresh_game()
        _on_battlefield(game, card_db.get_card("Monastery Mentor"), 0)

        _cast(game, 0, _spell_in_hand(game, "Spell 1", [CardType.INSTANT]))
        _cast(game, 0, _spell_in_hand(game, "Spell 2", [CardType.SORCERY]))
        assert len(_tokens(game, 0)) == 2, (
            "a plain cast trigger must fire on every qualifying spell")

    def test_plain_instant_or_sorcery_trigger_fires_on_each_spell(self, card_db):
        game = _fresh_game()
        _on_battlefield(game, card_db.get_card("Young Pyromancer"), 0)

        _cast(game, 0, _spell_in_hand(game, "Spell 1", [CardType.INSTANT]))
        _cast(game, 0, _spell_in_hand(game, "Spell 2", [CardType.SORCERY]))
        assert len(_tokens(game, 0)) == 2


# ─── attach rider ────────────────────────────────────────────────────


class TestOrdinalTriggerAttachRider:
    """"… create a token, then attach this Equipment to it."

    43 cards in the pool carry an attach-to-the-creature-just-made rider
    (15 of them in the "create a token, then attach" shape).  The rider is
    part of the trigger's effect: no equip cost is paid and no equip
    activation is needed."""

    def test_source_equipment_attaches_to_the_created_token(
            self, ordinal_token_equipment):
        game = _fresh_game()
        equipment = _on_battlefield(game, ordinal_token_equipment, 0)

        _cast(game, 0, _spell_in_hand(game, "Spell 1", [CardType.INSTANT]))
        _cast(game, 0, _spell_in_hand(game, "Spell 2", [CardType.INSTANT]))

        tokens = _tokens(game, 0)
        assert len(tokens) == 1
        assert f"equipped_{equipment.instance_id}" in tokens[0].instance_tags, (
            "the attach rider never ran — the token is not equipped")

    def test_attached_equipment_is_not_left_marked_unattached(
            self, ordinal_token_equipment):
        """`equipment_unattached` is the flag the AI's equip planner reads.
        An Equipment that just attached itself must not still advertise
        itself as needing an equip activation."""
        game = _fresh_game()
        equipment = _on_battlefield(game, ordinal_token_equipment, 0)

        _cast(game, 0, _spell_in_hand(game, "Spell 1", [CardType.INSTANT]))
        _cast(game, 0, _spell_in_hand(game, "Spell 2", [CardType.INSTANT]))

        assert "equipment_attached" in equipment.instance_tags
        assert "equipment_unattached" not in equipment.instance_tags

    def test_attach_rider_moves_the_equipment_off_its_old_creature(
            self, ordinal_token_equipment):
        """CR 301.5c: an Equipment is attached to at most one creature, so
        firing the trigger again moves it to the new token."""
        game = _fresh_game()
        equipment = _on_battlefield(game, ordinal_token_equipment, 0)

        _cast(game, 0, _spell_in_hand(game, "T1 spell 1", [CardType.INSTANT]))
        _cast(game, 0, _spell_in_hand(game, "T1 spell 2", [CardType.INSTANT]))
        first_token = _tokens(game, 0)[0]

        game.players[0].reset_turn_tracking()
        _cast(game, 0, _spell_in_hand(game, "T2 spell 1", [CardType.INSTANT]))
        _cast(game, 0, _spell_in_hand(game, "T2 spell 2", [CardType.INSTANT]))

        tag = f"equipped_{equipment.instance_id}"
        second_token = [t for t in _tokens(game, 0)
                        if t.instance_id != first_token.instance_id][0]
        assert tag in second_token.instance_tags
        assert tag not in first_token.instance_tags, (
            "the Equipment stayed attached to two creatures at once")

    def test_trigger_without_attach_rider_leaves_equipment_alone(self, card_db):
        """A token-making ordinal trigger with no attach clause must not
        attach anything — the rider is parsed, not assumed."""
        game = _fresh_game()
        source = _on_battlefield(game, card_db.get_card("Clarion Spirit"), 0)
        assert source.template.cast_trigger_token.get("attach_source") is False

        _cast(game, 0, _spell_in_hand(game, "Spell 1", [CardType.INSTANT]))
        _cast(game, 0, _spell_in_hand(game, "Spell 2", [CardType.INSTANT]))

        token = _tokens(game, 0)[0]
        assert f"equipped_{source.instance_id}" not in token.instance_tags
