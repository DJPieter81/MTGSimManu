"""CR 603.2 — ORDINAL cast triggers whose effect is NOT a token.

The ordinal-cast class ("whenever you cast your Nth spell each turn,
<effect>") is gated once per permanent in `resolve_spell_cast_trigger`.
Until now the only effect a firing ordinal reached was the shared TOKEN
branch (`cast_trigger_token`).  A non-token ordinal card
(`cast_trigger_token is None`) passed the gate and fell through every
branch doing NOTHING.

This pins the generic non-token dispatch: the effect clause is typed once
at DB load (`ordinal_cast_trigger["effect"]`) and dispatched through the
rule owners — +1/+1 counter via `add_plus_counters`, opponent damage via
`engine.damage.deal_damage`, life via `gain_life`, draw via `draw_cards`.
Shapes a simple clause executor cannot run whole (proliferate, modal,
coin-flip, copy, a second sentence) parse to None and are skipped, never
half-run.

Class: 45 ordinal cards, 9 make a token (handled elsewhere); of the 36
non-token, this generic dispatch reaches the counter (11), damage (5),
draw (2) and gain-life (1) shapes.  No registered deck carries a
non-token ordinal card, so this is rules-correctness only — no win-rate
change.

Card names are fixture carriers only; the rule under test is "a firing
ordinal trigger applies its printed non-token effect through the rule
owners".
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState
from engine.mana import ManaCost
from engine.oracle_parser import parse_ordinal_cast_trigger, parse_ordinal_effect


# ─── helpers (mirroring tests/test_ordinal_cast_trigger.py) ──────────


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
    tmpl = CardTemplate(
        name=name, card_types=card_types, mana_cost=ManaCost(generic=0),
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


def _cast(game: GameState, controller: int, spell: CardInstance) -> None:
    game.cast_spell(controller, spell, free_cast=True)


def _cast_second(game: GameState, source_tmpl: CardTemplate,
                 controller: int = 0) -> CardInstance:
    """Put the ordinal source on the battlefield and cast two free spells,
    returning the source (so the caller can read its counters)."""
    src = _on_battlefield(game, source_tmpl, controller)
    _cast(game, controller, _spell_in_hand(game, "S1", [CardType.INSTANT], controller))
    _cast(game, controller, _spell_in_hand(game, "S2", [CardType.INSTANT], controller))
    return src


def _synthetic_ordinal(oracle: str) -> CardTemplate:
    """A creature template carrying a given ordinal trigger oracle line —
    for the refuse-shape case, where no real carrier is needed."""
    t = CardTemplate(
        name="Ordinal Probe", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=1, toughness=1, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text=oracle, tags=set(),
    )
    # populate the typed field the DB loader would set
    t.ordinal_cast_trigger = parse_ordinal_cast_trigger(oracle)
    return t


# ─── parse layer ─────────────────────────────────────────────────────


class TestOrdinalEffectIsTypedFromTheClause:

    def test_a_counter_shape_is_typed(self, card_db):
        spec = card_db.get_card("Monk of the Open Hand").ordinal_cast_trigger
        assert spec["effect"] == {"kind": "counter", "amount": 1}

    def test_a_damage_to_each_opponent_shape_is_typed(self, card_db):
        spec = card_db.get_card("Devoted Duelist").ordinal_cast_trigger
        assert spec["effect"]["kind"] == "damage"
        assert spec["effect"]["amount"] == 1
        assert spec["effect"]["scope"] == "each"
        assert spec["effect"].get("gain", 0) == 0

    def test_a_damage_and_gain_shape_is_typed(self, card_db):
        spec = card_db.get_card("Cori Mountain Stalwart").ordinal_cast_trigger
        assert spec["effect"]["kind"] == "damage"
        assert spec["effect"]["amount"] == 2
        assert spec["effect"]["gain"] == 2

    def test_a_gain_shape_is_typed(self, card_db):
        spec = card_db.get_card("Doomskar Oracle").ordinal_cast_trigger
        assert spec["effect"] == {"kind": "gain", "gain": 2}

    def test_a_draw_shape_is_typed(self, card_db):
        spec = card_db.get_card("Jori En, Ruin Diver").ordinal_cast_trigger
        assert spec["effect"] == {"kind": "draw", "draw": 1}

    def test_a_token_clause_carries_no_nontoken_effect(self, card_db):
        """The token effect is owned by `cast_trigger_token`; the ordinal
        record must not also claim it as a non-token effect."""
        spec = card_db.get_card("Clarion Spirit").ordinal_cast_trigger
        assert spec.get("effect") is None

    def test_a_shape_the_executor_cannot_run_is_refused(self):
        """Proliferate / modal / coin-flip / a second sentence parse to
        None — refused whole, never half-run."""
        assert parse_ordinal_effect(", proliferate") is None
        assert parse_ordinal_effect(", choose one —") is None
        # a counter clause with a trailing second sentence is refused
        assert parse_ordinal_effect(
            ", put two +1/+1 counters on this creature. It gains menace "
            "until end of turn.") is None


# ─── dispatch layer ──────────────────────────────────────────────────


class TestOrdinalNonTokenEffectFiresOnTheNthSpell:

    def test_counter_lands_on_the_source_on_the_second_spell(self, card_db):
        game = _fresh_game()
        monk = _on_battlefield(game, card_db.get_card("Monk of the Open Hand"), 0)
        _cast(game, 0, _spell_in_hand(game, "S1", [CardType.INSTANT]))
        assert monk.plus_counters == 0, "fired on the first spell"
        _cast(game, 0, _spell_in_hand(game, "S2", [CardType.INSTANT]))
        assert monk.plus_counters == 1, "did not put the counter on the second spell"
        _cast(game, 0, _spell_in_hand(game, "S3", [CardType.INSTANT]))
        assert monk.plus_counters == 1, "over-triggered on a later spell"

    def test_damage_reaches_each_opponent_on_the_second_spell(self, card_db):
        game = _fresh_game()
        start = game.players[1].life
        _cast_second(game, card_db.get_card("Devoted Duelist"))
        assert game.players[1].life == start - 1, (
            "the ordinal damage did not reach the opponent")

    def test_damage_and_gain_both_apply(self, card_db):
        game = _fresh_game()
        opp0 = game.players[1].life
        me0 = game.players[0].life
        _cast_second(game, card_db.get_card("Cori Mountain Stalwart"))
        assert game.players[1].life == opp0 - 2
        assert game.players[0].life == me0 + 2

    def test_gain_life_applies_on_the_second_spell(self, card_db):
        game = _fresh_game()
        me0 = game.players[0].life
        _cast_second(game, card_db.get_card("Doomskar Oracle"))
        assert game.players[0].life == me0 + 2

    def test_draw_applies_on_the_second_spell(self, card_db):
        game = _fresh_game()
        # seed a library so the draw has cards to take
        for i in range(5):
            game.players[0].library.append(
                _spell_in_hand(game, f"lib{i}", [CardType.INSTANT]))
            game.players[0].hand.pop()  # keep it in library only
        lib0 = len(game.players[0].library)
        _cast_second(game, card_db.get_card("Jori En, Ruin Diver"))
        assert len(game.players[0].library) == lib0 - 1, (
            "the ordinal draw did not draw a card")

    def test_a_token_ordinal_card_is_unchanged_by_the_nontoken_branch(self, card_db):
        """Cori-Steel Cutter / Clarion Spirit still make exactly one token and
        gain no counter from the non-token branch (guarded on
        cast_trigger_token is None)."""
        game = _fresh_game()
        src = _cast_second(game, card_db.get_card("Clarion Spirit"))
        tokens = [c for c in game.players[0].battlefield
                  if getattr(c, "is_token", False)]
        assert len(tokens) == 1, "token effect disturbed"
        assert src.plus_counters == 0, "non-token branch fired on a token card"

    def test_a_refused_shape_is_a_no_op(self):
        game = _fresh_game()
        src = _cast_second(
            game, _synthetic_ordinal(
                "Whenever you cast your second spell each turn, proliferate."))
        # no crash, no counter, life unchanged
        assert src.plus_counters == 0
        assert game.players[0].life == 20 and game.players[1].life == 20
