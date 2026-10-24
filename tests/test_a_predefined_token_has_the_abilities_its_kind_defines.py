"""A predefined token is the object CR 111.10 defines for its kind: a
colorless artifact token with that kind as its subtype and the ability its
rule prints, owned and controlled by the player who created it (CR 111.2).

* CR 111.10a: a Treasure is a colorless Treasure artifact token with "{T},
  Sacrifice this token: Add one mana of any color."
* CR 111.10b: a Food is a colorless Food artifact token with "{2}, {T},
  Sacrifice this token: You gain 3 life."
* CR 111.10f: a Clue is a colorless Clue artifact token with "{2},
  Sacrifice this token: Draw a card."

The defect: the token owner built each of them from a bare table row -- an
artifact with no subtype, no text and no ability, tagged as a creature. A
Treasure made no mana and a Clue drew no card, so every Treasure Ragavan
made when it connected did nothing.

Class: 207 pool cards create a Treasure, 98 a Food and 90 a Clue (the Clue
count includes investigate's reminder text). Registered: Ragavan, Nimble
Pilferer (Boros Energy, Jeskai Blink, Domain Zoo x4), Fable of the
Mirror-Breaker (Jeskai Blink x4), Beza, the Bounding Spring (Azorius Control
SB) and Generous Ent's Food (Living End x4, typed but not activatable: no
executor gains life on activation). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.activation import ActivationManager
from engine.cards import ActivationEffectKind, CardInstance, CardType
from engine.game_state import GameState, Phase
from engine.mana import ManaCost

KINDS = (("treasure", "Treasure"), ("food", "Food"), ("clue", "Clue"))


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    if zone != "stack":
        getattr(game.players[idx], zone).append(c)
    return c


def _library(game, card_db, n, idx=0):
    for _ in range(n):
        _put(game, card_db, "Island", "library", idx)


@pytest.mark.parametrize("kind,subtype", KINDS)
def test_a_predefined_token_is_the_colorless_artifact_its_kind_defines(kind,
                                                                       subtype):
    game = _game()
    [token] = game.create_token(1, kind)
    t = token.template
    assert token.is_token and token.owner == token.controller == 1
    assert t.name == f"{subtype} Token"
    assert t.card_types == [CardType.ARTIFACT] and not t.is_creature
    assert t.subtypes == [subtype]
    assert t.colors == set()
    assert "creature" not in t.tags
    # its text is the ability its rule prints, typed through the parsers
    # every card's text goes through
    assert t.oracle_text and t.activated_abilities
    assert token in game.players[1].battlefield


def test_a_clue_is_sacrificed_for_two_to_draw_a_card(card_db):
    game = _game()
    lands = [_put(game, card_db, "Island", "battlefield") for _ in range(2)]
    _library(game, card_db, 3)
    [clue] = game.create_token(0, "clue")
    [ability] = clue.template.activated_abilities
    assert ability.effect_kind is ActivationEffectKind.DRAW_N
    assert ability.amount == 1
    assert ability.cost.mana.generic == 2 and ability.cost.sacrifice_self
    assert not ability.cost.tap_self
    # a noncreature artifact's ability is activated the turn it arrives
    assert ActivationManager.can_activate(game, 0, clue, ability)
    hand = len(game.players[0].hand)
    assert ActivationManager.activate(game, 0, clue, ability, [])
    game.resolve_stack()
    assert len(game.players[0].hand) == hand + 1
    assert clue not in game.players[0].battlefield
    assert all(land.tapped for land in lands)


def test_a_treasure_pays_a_shortfall_and_is_sacrificed(card_db):
    game = _game()
    island = _put(game, card_db, "Island", "battlefield")
    [treasure] = game.create_token(0, "treasure")
    assert treasure.template.sacrifice_mana_units == [["W", "U", "B", "R", "G"]]
    [ability] = treasure.template.activated_abilities
    assert ability.is_mana_ability and ability.cost.sacrifice_self
    # the land pays what it can; the Treasure is sacrificed for the rest
    assert game.tap_lands_for_mana(0, ManaCost(generic=2))
    assert island.tapped
    assert treasure not in game.players[0].battlefield


def test_a_treasure_is_kept_when_the_lands_pay_the_cost(card_db):
    game = _game()
    for _ in range(2):
        _put(game, card_db, "Island", "battlefield")
    [treasure] = game.create_token(0, "treasure")
    assert game.tap_lands_for_mana(0, ManaCost(generic=2))
    assert treasure in game.players[0].battlefield


def test_a_food_ability_is_typed_but_never_activated(card_db):
    game = _game()
    for _ in range(2):
        _put(game, card_db, "Island", "battlefield")
    [food] = game.create_token(0, "food")
    [ability] = food.template.activated_abilities
    assert ability.cost.mana.generic == 2
    assert ability.cost.tap_self and ability.cost.sacrifice_self
    # no executor gains life on activation: refused, never half-performed
    assert ability.effect_kind is ActivationEffectKind.UNCLASSIFIED
    assert not ActivationManager.can_activate(game, 0, food, ability)
    assert food.template.sacrifice_mana_units == []


def test_a_predefined_tokens_entering_performs_none_of_its_abilities(card_db):
    game = _game()
    _library(game, card_db, 3)
    me = game.players[0]
    before = (len(me.hand), me.life, me.mana_pool.total(), len(me.library))
    for kind, _subtype in KINDS:
        game.create_token(0, kind)
    assert (len(me.hand), me.life, me.mana_pool.total(),
            len(me.library)) == before
    assert len(me.battlefield) == len(KINDS)


def _enter(game, card_db, name, idx=0):
    """`name` enters under `idx`'s control: its enter triggers resolve
    through the entry path every permanent takes."""
    card = _put(game, card_db, name, "battlefield", idx)
    game._handle_permanent_etb(card, idx)
    return card


def test_an_enter_trigger_that_creates_a_treasure_makes_one_that_pays_mana(
        card_db):
    game = _game()
    _enter(game, card_db, "Prosperous Innkeeper")
    [treasure] = [c for c in game.players[0].battlefield if c.is_token]
    assert treasure.template.subtypes == ["Treasure"]
    assert game.tap_lands_for_mana(0, ManaCost(generic=1))
    assert treasure not in game.players[0].battlefield


# ── Auditor (CR 111.10, 111.2), restated from the raw fields ─────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_predefined_token_built_without_its_ability(
        audit, monkeypatch):
    from engine import permanent_effects
    real = permanent_effects.predefined_token

    def bare(kind):                                          # the defect
        built = real(kind)
        return built and dict(built, activated_abilities=(),
                              sacrifice_mana_units=())
    monkeypatch.setattr(permanent_effects, "predefined_token", bare)
    _game().create_token(0, "treasure")
    assert "111.10/predefined_token" in _rules(audit)


def test_the_audit_is_silent_for_each_predefined_kind(audit):
    game = _game()
    for idx, (kind, _subtype) in enumerate(KINDS):
        game.create_token(idx % 2, kind, count=2)
    assert "111.10/predefined_token" not in _rules(audit)


def test_the_token_audit_records_a_predefined_token_under_the_wrong_player(
        card_db, audit, monkeypatch):
    from engine import permanent_effects
    real = permanent_effects.PermanentEffects._put_tokens

    def handed_over(game, controller, count, built):         # the defect
        tokens = real(game, controller, count, built)
        for t in tokens:
            t.controller = 1 - controller
        return tokens
    monkeypatch.setattr(permanent_effects.PermanentEffects, "_put_tokens",
                        staticmethod(handed_over))
    _enter(_game(), card_db, "Prosperous Innkeeper")
    assert "111.3/token_characteristics" in _rules(audit)


def test_the_token_audit_is_silent_for_a_predefined_token_its_creator_made(
        card_db, audit):
    _enter(_game(), card_db, "Prosperous Innkeeper")
    _enter(_game(), card_db, "Gilded Goose", idx=1)
    assert "111.3/token_characteristics" not in _rules(audit)
