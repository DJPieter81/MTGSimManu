"""An enter trigger creates the token its creator prints (CR 111.3, 603.6a);
a dies observer fires whatever the dying creature prints (CR 603.2, 603.10a).

"When <this> enters, create <N> <token>": the token has exactly the
characteristics its creator defines -- power, toughness, colour, card types,
subtypes and keywords (CR 111.3).

"Whenever one or more other <subtype>s you control die, <effect>" is the
observer's ability. It triggers on the death of any creature of that subtype
its controller controls (CR 603.2), whatever that creature's own text is.
A vanilla token has none.

The engine's rule:
- The enter-trigger carrier (`effect_carrier.dispatch_etb`) takes an enter
  trigger in the tokens-and-counters family's closed shape, as it takes the
  card-flow family. The token comes from the one token owner
  (`GameState.create_token` with the typed `TokenSpec`).
- The dies observers (`CardTemplate.creature_dies_observer`, fanned out by
  `PermanentEffects._fire_creature_dies_observers` on every death) include
  "you may exile <this>, then return it to the battlefield transformed".
  A permanent that has transformed is a new object (CR 400.7) whose face
  prints no such trigger, so a batch of deaths transforms it once.

The defects:
- Ajani's enter trigger was a name-keyed handler that made a "Cat Token"
  with no subtypes and no colour.
- The subtype-death transform sat in the dying creature's own resolver
  (`resolve_dies_trigger`) behind a gate on that creature's text, so a
  vanilla token's death never reached it. Since unit TK, Ajani never
  transformed.

Card names are fixture carriers: each is one member of its class.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType, Color
from engine.game_state import GameState, Phase

AJANI = "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger"


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for p in (0, 1):
        for _ in range(6):
            _add(game, card_db, "Mountain", p)
            _add(game, card_db, "Plains", p)
    return game


def _cast(game, card_db, name, controller=0):
    card = _add(game, card_db, name, controller, "hand")
    active = game.active_player
    game.active_player = controller          # a creature is cast on its
    assert game.cast_spell(controller, card, [])   # controller's turn
    game.resolve_stack()
    game.active_player = active
    return card


def _tokens(game, controller=0):
    return [c for c in game.players[controller].battlefield
            if getattr(c, "is_token", False)]


# ── The enter trigger's token ─────────────────────────────────────────

@pytest.mark.parametrize("name, n, subtypes, colors, power, toughness", [
    (AJANI, 1, {"Cat", "Warrior"}, {Color.WHITE}, 2, 1),
    ("Siege-Gang Commander", 3, {"Goblin"}, {Color.RED}, 1, 1),
])
def test_an_enter_trigger_creates_the_token_its_creator_prints(
        card_db, name, n, subtypes, colors, power, toughness):
    game = _game(card_db)
    _cast(game, card_db, name)
    tokens = _tokens(game)
    assert len(tokens) == n
    for t in tokens:
        assert set(t.template.subtypes) == subtypes
        assert set(t.template.colors) == colors
        assert CardType.CREATURE in t.template.card_types
        assert (t.power, t.toughness) == (power, toughness)


# ── The dies observer ──────────────────────────────────────────────────

def _ajani_with_token(card_db):
    game = _game(card_db)
    ajani = _cast(game, card_db, AJANI)
    (cat,) = _tokens(game)
    return game, ajani, cat


def test_a_vanilla_token_of_the_subtype_triggers_the_observer(card_db):
    game, ajani, cat = _ajani_with_token(card_db)
    assert not (cat.template.oracle_text or "")
    game._creature_dies(cat)
    game.resolve_stack()
    assert ajani.zone == "battlefield" and ajani.is_transformed
    assert ajani.loyalty_counters == ajani.template.back_face_loyalty


def test_a_creature_of_another_subtype_does_not_trigger_it(card_db):
    game = _game(card_db)
    ajani = _cast(game, card_db, AJANI)
    goblin = _add(game, card_db, "Goblin Guide", 0)
    game._creature_dies(goblin)
    game.resolve_stack()
    assert not ajani.is_transformed


def test_an_opponents_creature_of_the_subtype_does_not_trigger_it(card_db):
    game = _game(card_db)
    ajani = _cast(game, card_db, AJANI)
    theirs = _cast(game, card_db, AJANI, controller=1)
    for t in _tokens(game, 1):
        game._creature_dies(t)
    game.resolve_stack()
    assert not ajani.is_transformed
    assert theirs.is_transformed


def test_a_batch_of_deaths_transforms_the_observer_once(card_db):
    game, ajani, cat = _ajani_with_token(card_db)
    second = _cast(game, card_db, AJANI)
    for c in [cat] + [t for t in _tokens(game) if t is not cat]:
        game._creature_dies(c)
    game.resolve_stack()
    assert ajani.is_transformed and ajani.zone == "battlefield"
    assert second.is_transformed


# ── The auditor (CR 603.2) ────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "603.2/dies_observer"]


def test_the_audit_records_an_observer_a_matching_death_did_not_fire(
        card_db, audit, monkeypatch):
    from engine import permanent_effects
    game, ajani, cat = _ajani_with_token(card_db)
    monkeypatch.setattr(permanent_effects, "observer_matches",
                        lambda spec, creature: False)        # the defect
    game._creature_dies(cat)
    assert _findings(audit)


def test_the_audit_is_silent_when_the_observer_fires(card_db, audit):
    game, ajani, cat = _ajani_with_token(card_db)
    game._creature_dies(cat)
    assert not _findings(audit)
