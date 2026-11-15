"""A land card is put from its owner's hand onto the battlefield (CR 305.4).

"You may put a land card from your hand onto the battlefield [tapped]" --
on an enter trigger (Spelunking, Arboreal Grazer), an activation
(Sakura-Tribe Scout) or a spell (Growth Spiral) -- moves a land of its
controller's choice from their hand to the battlefield. It is not a land
played (CR 305.4): it counts against no land drop. It enters through the
land-entry owner, so an untap-on-enter watcher (Amulet of Vigor) and a
"lands you control enter untapped" static see it, and landfall triggers.
"If you put a Cave onto the battlefield this way" reads the land it put.

The defect: no executor put a land from hand. Spelunking's enter trigger
(Amulet Titan main deck x4) neither drew nor put a land; Sakura-Tribe
Scout's activation (x2) did nothing; only Arboreal Grazer worked, through
a card-name registry handler. Found replaying Amulet Titan vs Boros Energy
s50000 (Amulet Titan at 25% of the field, band 45-60).

Card names are fixture carriers: 33 pool cards put a land card from a
hand onto the battlefield.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, hand_lands=("Forest",)):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(4):
        _add(game, card_db, "Forest", 0, "battlefield")
        _add(game, card_db, "Island", 0, "library")
    for _ in range(2):
        _add(game, card_db, "Island", 0, "battlefield")
    for name in hand_lands:
        _add(game, card_db, name, 0, "hand")
    return game


def _lands(game):
    return [c for c in game.players[0].battlefield if c.template.is_land]


def _cast(game, card_db, name):
    card = _add(game, card_db, name, 0, "hand")
    assert game.cast_spell(0, card, [])
    while not game.stack.is_empty:
        game.resolve_stack()
    return card


def test_an_enter_trigger_draws_then_puts_a_land_from_hand(card_db):
    game = _game(card_db)
    library = len(game.players[0].library)
    _cast(game, card_db, "Spelunking")
    assert library - len(game.players[0].library) == 1          # drew
    assert len(_lands(game)) == 7                                # put one
    assert not any(c.template.is_land and c.name == "Forest"
                   for c in game.players[0].hand)


def test_a_put_land_is_not_a_land_played(card_db):
    game = _game(card_db)
    _cast(game, card_db, "Growth Spiral")
    assert len(_lands(game)) == 7
    assert game.players[0].lands_played_this_turn == 0


def test_a_cave_put_this_way_gains_its_controller_4_life(card_db):
    game = _game(card_db, hand_lands=("Echoing Deeps",))
    _cast(game, card_db, "Spelunking")
    assert any(c.name == "Echoing Deeps" for c in _lands(game))
    assert game.players[0].life == 24


def test_a_land_that_is_no_cave_gains_nothing(card_db):
    game = _game(card_db)
    _cast(game, card_db, "Spelunking")
    assert game.players[0].life == 20


def test_an_activation_puts_a_land_from_hand(card_db):
    from engine.activated_effects import resolve_activated_ability
    game = _game(card_db)
    scout = _add(game, card_db, "Sakura-Tribe Scout", 0, "battlefield")
    ability = scout.template.activated_abilities[0]
    assert resolve_activated_ability(game, scout, 0, [], ability=ability)
    assert len(_lands(game)) == 7


def test_a_land_put_tapped_meets_the_untap_on_enter_watcher(card_db):
    """Arboreal Grazer puts its land tapped; Amulet of Vigor untaps it
    (CR 603.2: it entered tapped, so the watcher triggers)."""
    game = _game(card_db)
    _add(game, card_db, "Amulet of Vigor", 0, "battlefield")
    _cast(game, card_db, "Arboreal Grazer")
    put = [c for c in _lands(game) if c.name == "Forest"][-1]
    assert len(_lands(game)) == 7
    assert not put.tapped


def test_a_land_put_tapped_stays_tapped_without_a_watcher(card_db):
    game = _game(card_db)
    _cast(game, card_db, "Arboreal Grazer")
    assert len(_lands(game)) == 7
    assert [c.tapped for c in _lands(game)][-1] is True


def test_nothing_is_put_when_the_hand_holds_no_land(card_db):
    game = _game(card_db, hand_lands=())
    for card in list(game.players[0].library):     # draws no land either
        game.players[0].library.remove(card)
    _add(game, card_db, "Grizzly Bears", 0, "library")
    _cast(game, card_db, "Growth Spiral")
    assert len(_lands(game)) == 6


# ── The controller's choice (AI) ──────────────────────────────────────

def test_the_ai_puts_the_land_its_land_valuation_prefers(card_db):
    """The land the AI's one land valuation delivers
    (`activation_ev._choose_land_delivery`), never a second ranking."""
    from ai.activation_ev import _choose_land_delivery
    from ai.resolution_choices import pick_cards
    from engine.effect_spec import Verb, iter_specs
    game = _game(card_db, hand_lands=("Forest", "Simic Growth Chamber"))
    game.players[0].deck_name = "Amulet Titan"
    _add(game, card_db, "Amulet of Vigor", 0, "battlefield")
    spec = next(s for s in iter_specs(
        card_db.get_card("Growth Spiral").effects.spell(0).specs)
        if s.verb is Verb.MOVE)

    class _Ctx:
        controller = 0
    pool = [c for c in game.players[0].hand if c.template.is_land]
    assert pick_cards(game, _Ctx(), spec, pool, 1) == [
        _choose_land_delivery(game, 0, pool)]


# ── The auditor (CR 305.4) ────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "305.4/land_put"]


def test_the_audit_records_a_land_put_counted_as_a_land_played(
        card_db, audit, monkeypatch):
    from engine.land_manager import LandManager
    real = LandManager.enter_land

    def counted(game, card, player_idx, from_zone, tapped, cause):  # the defect
        real(game, card, player_idx, from_zone, tapped, cause)
        game.players[player_idx].lands_played_this_turn += 1
    monkeypatch.setattr(LandManager, "enter_land", staticmethod(counted))
    game = _game(card_db)
    _cast(game, card_db, "Growth Spiral")
    assert _findings(audit)


def test_the_audit_is_silent_when_the_land_is_put(card_db, audit):
    game = _game(card_db)
    _cast(game, card_db, "Growth Spiral")
    assert not _findings(audit)


def test_the_audit_is_silent_when_the_put_lands_own_trigger_returns_it(
        card_db, audit):
    """A bounce land put with no other land returns itself ("return a land
    you control to its owner's hand"): the put happened, and the land's own
    trigger is a later event (CR 603.2). Living End vs Amulet Titan s57500,
    T4: Living End returned Arboreal Grazer to a board with no land."""
    from engine.activated_effects import resolve_activated_ability
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    _add(game, card_db, "Island", 0, "library")
    chamber = _add(game, card_db, "Simic Growth Chamber", 0, "hand")
    scout = _add(game, card_db, "Sakura-Tribe Scout", 0, "battlefield")
    ability = scout.template.activated_abilities[0]
    assert resolve_activated_ability(game, scout, 0, [], ability=ability)
    assert chamber.zone == "hand"                  # it returned itself
    assert not _findings(audit)
