"""Kicker as an optional ADDITIONAL cost (CR 702.33) — the payment machinery.

Kicker/multikicker is parsed once (kicker_cost / multikicker / kicked_clause),
the caster MAY pay the kicker on top of the base as the spell is cast (the
AI decides via should_kick; the engine clamps to affordable mana), and the
StackItem records kick_count. This file pins the parse + payment; the kicked
EFFECTS are K1 commit 3.

Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_kicker, parse_kicked_clause


# ─── parse layer ─────────────────────────────────────────────────────


def test_registered_kicker_cards_are_typed(card_db):
    oc = card_db.get_card("Orim's Chant")
    assert oc.kicker_cost is not None and oc.kicker_cost.to_dict()["W"] == 1
    assert oc.multikicker is False
    assert "can't attack" in (oc.kicked_clause or "")
    sm = card_db.get_card("Sowing Mycospawn")
    assert sm.kicker_cost.cmc == 2 and "exile target land" in (sm.kicked_clause or "")
    cs = card_db.get_card("Consult the Star Charts")
    assert cs.kicker_cost.cmc == 2 and "two" in (cs.kicked_clause or "")


def test_multikicker_is_flagged(card_db):
    cs = card_db.get_card("Comet Storm")
    if cs is not None:  # fixture carrier; skip if absent from this DB
        assert cs.multikicker is True


def test_and_or_kicker_is_refused():
    # "and/or" / two-cost kicker is out of v1 scope → not typed.
    assert parse_kicker("Kicker {B} and/or {R}\nBase text.") is None


def test_kicked_clause_extraction():
    assert parse_kicked_clause(
        "Target player can't cast spells this turn. If this spell was "
        "kicked, creatures can't attack this turn.") == "creatures can't attack this turn"
    assert parse_kicked_clause("When you cast this spell, if it was kicked, "
                               "exile target land.") == "exile target land"
    assert parse_kicked_clause("Draw a card.") is None


# ─── payment layer ───────────────────────────────────────────────────


def _kicker_instant(game, base_green, kicker_green):
    t = CardTemplate(
        name="Kick Probe", card_types=[CardType.INSTANT],
        mana_cost=ManaCost(green=base_green), supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="Kicker {G}\nDraw a card.", tags=set())
    t.kicker_cost = ManaCost(green=kicker_green)
    t.multikicker = False
    t.kicked_clause = "draw a card"
    c = CardInstance(template=t, owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone="hand")
    c._game_state = game
    game.players[0].hand.append(c)
    return c


def _game_with_forests(card_db, n):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(n):
        f = CardInstance(template=card_db.get_card("Forest"), owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="battlefield")
        f._game_state = game; f.tapped = False
        game.players[0].battlefield.append(f)
    return game


def _tapped(game):
    return sum(1 for c in game.players[0].battlefield
               if c.template.is_land and getattr(c, "tapped", False))


def test_a_kicked_cast_pays_base_plus_kicker_and_flags_the_stack_item(card_db):
    game = _game_with_forests(card_db, 2)
    spell = _kicker_instant(game, base_green=1, kicker_green=1)
    game.callbacks.should_kick = lambda g, p, c: 1  # force the kick
    game.cast_spell(0, spell)
    assert _tapped(game) == 2, "base {G} + kicker {G} = two Forests tapped"
    item = game.stack.items[-1] if game.stack.items else None
    assert item is not None and item.kick_count == 1


def test_kicker_is_not_paid_when_leftover_covers_only_the_base(card_db):
    game = _game_with_forests(card_db, 1)  # one Forest: base only
    spell = _kicker_instant(game, base_green=1, kicker_green=1)
    game.callbacks.should_kick = lambda g, p, c: 1  # AI wants to, engine clamps
    game.cast_spell(0, spell)
    assert _tapped(game) == 1, "only the base was affordable"
    item = game.stack.items[-1] if game.stack.items else None
    assert item is not None and item.kick_count == 0


def test_an_unkicked_cast_pays_the_base_only(card_db):
    game = _game_with_forests(card_db, 2)
    spell = _kicker_instant(game, base_green=1, kicker_green=1)
    game.callbacks.should_kick = lambda g, p, c: 0
    game.cast_spell(0, spell)
    assert _tapped(game) == 1
    item = game.stack.items[-1] if game.stack.items else None
    assert item is not None and item.kick_count == 0
