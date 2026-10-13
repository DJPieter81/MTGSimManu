"""A card chosen in a graveyard and exiled leaves that graveyard (CR
701.13a, 400.7), and the instructions beside the exile are performed in
printed order, reading the card it exiled (CR 608.2c).

"Exile target card from a graveyard", alone or with a rider ("Draw a
card.", "Create a 2/2 black Zombie creature token.", "If it was a creature
card, you gain 3 life. Otherwise, you draw a card.") moves the chosen
card -- still a legal target on resolution (CR 608.2b) -- from its owner's
graveyard to exile through the zone funnel; a branch on "it" reads the
card the exile moved.

The defect: no executor and no legacy handler exiled a graveyard target.
Every spell of the class left its target where it was and performed only
its riders (Cling to Dust always drew; Shadowfeed gained 6 life), and
enter triggers of the class (Soul-Guide Lantern) did nothing. Found
replaying Grixis Reanimator vs Azorius Blink s53000: Cling to Dust was
cast at a creature card that stayed in the graveyard while its caster drew.

Card names are fixture carriers: 116 pool cards exile a target card from a
graveyard.
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


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(6):
        _add(game, card_db, "Swamp", 0, "battlefield")
        _add(game, card_db, "Plains", 0, "battlefield")
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    return game


def _cast(game, card_db, spell, target):
    card = _add(game, card_db, spell, 0, "hand")
    assert game.cast_spell(0, card, [target.instance_id])
    while not game.stack.is_empty:
        game.resolve_stack()
    return card


def test_a_spell_exiles_the_card_chosen_in_a_graveyard(card_db):
    game = _game(card_db)
    target = _add(game, card_db, "Lightning Bolt", 1, "graveyard")
    _cast(game, card_db, "Purify the Grave", target)
    assert target.zone == "exile"
    assert target not in game.players[1].graveyard
    assert target in game.players[1].exile


def test_a_draw_beside_the_exile_is_performed_too(card_db):
    game = _game(card_db)
    target = _add(game, card_db, "Grizzly Bears", 1, "graveyard")
    library = len(game.players[0].library)
    _cast(game, card_db, "Cremate", target)
    assert target.zone == "exile"
    assert library - len(game.players[0].library) == 1


@pytest.mark.parametrize("exiled, life, drew", [
    ("Grizzly Bears", 23, 0),     # a creature card: gain 3, no draw
    ("Lightning Bolt", 20, 1),    # otherwise: draw, no life
])
def test_the_branch_reads_the_card_the_exile_moved(card_db, exiled, life,
                                                   drew):
    game = _game(card_db)
    target = _add(game, card_db, exiled, 1, "graveyard")
    library = len(game.players[0].library)
    _cast(game, card_db, "Cling to Dust", target)
    assert target.zone == "exile"
    assert game.players[0].life == life
    assert library - len(game.players[0].library) == drew


def test_life_beside_the_exile_is_gained_once(card_db):
    game = _game(card_db)
    target = _add(game, card_db, "Grizzly Bears", 1, "graveyard")
    _cast(game, card_db, "Shadowfeed", target)
    assert target.zone == "exile"
    assert game.players[0].life == 23


def test_a_token_beside_the_exile_is_created(card_db):
    game = _game(card_db)
    target = _add(game, card_db, "Grizzly Bears", 1, "graveyard")
    _cast(game, card_db, "Vile Rebirth", target)
    assert target.zone == "exile"
    zombies = [c for c in game.players[0].battlefield
               if getattr(c, "is_token", False)]
    assert [(c.power, c.toughness) for c in zombies] == [(2, 2)]


def test_a_target_gone_from_its_graveyard_is_not_affected(card_db):
    """CR 608.2b: a target no longer in the graveyard it was chosen in is
    illegal; the spell with no legal target left does nothing at all."""
    game = _game(card_db)
    target = _add(game, card_db, "Grizzly Bears", 1, "graveyard")
    cremate = _add(game, card_db, "Cremate", 0, "hand")
    assert game.cast_spell(0, cremate, [target.instance_id])
    game.zone_mgr.move_card(game, target, "graveyard", "hand", cause="test")
    library = len(game.players[0].library)
    while not game.stack.is_empty:
        game.resolve_stack()
    assert target.zone == "hand"
    assert len(game.players[0].library) == library      # no draw


def test_an_enter_trigger_exiles_a_card_from_a_graveyard(card_db):
    """An enter trigger's graveyard target is its controller's pick (A36):
    the card it picks leaves the graveyard."""
    game = _game(card_db)
    theirs = _add(game, card_db, "Grizzly Bears", 1, "graveyard")
    lantern = _add(game, card_db, "Soul-Guide Lantern", 0, "hand")
    assert game.cast_spell(0, lantern, [])
    while not game.stack.is_empty:
        game.resolve_stack()
    assert theirs.zone == "exile"


# ── The controller's pick (AI) ────────────────────────────────────────

def _exile_spec(card_db, name="Soul-Guide Lantern"):
    from engine.effect_spec import Verb, iter_specs
    eff = card_db.get_card(name).effects
    return next(s for face in eff.faces for h in face
                for s in iter_specs(h.specs)
                if s.verb is Verb.EXILE and s.target is not None
                and s.target.zone == "graveyard")


class _Ctx:
    controller = 0


def test_the_ai_takes_the_opponents_card_worth_most_to_its_owner(card_db):
    """The opponents' cards whose removal is worth something to them,
    best first (`ai.graveyard_removal`): a keystone of their published
    plan over a spent land, and never one of the caster's own cards while
    an opponent's is there."""
    from ai.resolution_choices import pick_cards
    game = _game(card_db)
    game.players[1].deck_name = "Living End"
    mine = _add(game, card_db, "Griselbrand", 0, "graveyard")
    land = _add(game, card_db, "Misty Rainforest", 1, "graveyard")
    keystone = _add(game, card_db, "Living End", 1, "graveyard")
    spec = _exile_spec(card_db)
    assert pick_cards(game, _Ctx(), spec, [mine, land, keystone], 1) == [
        keystone]
    assert pick_cards(game, _Ctx(), spec, [mine, land], 1) == [land]


def test_the_ai_takes_its_own_card_only_for_a_required_target(card_db):
    from ai.resolution_choices import pick_cards
    game = _game(card_db)
    mine = _add(game, card_db, "Lightning Bolt", 0, "graveyard")
    spec = _exile_spec(card_db)
    assert spec.target.count_min == 1
    assert pick_cards(game, _Ctx(), spec, [mine], 1) == [mine]
    optional = _exile_spec(card_db, "Emperor of Bones")
    assert optional.target.count_min == 0
    assert pick_cards(game, _Ctx(), optional, [mine], 1) == []


# ── The auditor (CR 701.13a) ──────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain()
            if f["rule"] == "701.13/graveyard_target_exiled"]


def test_the_audit_records_a_chosen_card_left_in_its_graveyard(
        card_db, audit, monkeypatch):
    from engine.zone_manager import ZoneManager
    real = ZoneManager.move_card

    def stuck(self, game, card, from_zone, to_zone, *a, **k):   # the defect
        if from_zone == "graveyard" and to_zone == "exile":
            return False
        return real(self, game, card, from_zone, to_zone, *a, **k)
    monkeypatch.setattr(ZoneManager, "move_card", stuck)
    game = _game(card_db)
    target = _add(game, card_db, "Lightning Bolt", 1, "graveyard")
    _cast(game, card_db, "Purify the Grave", target)
    assert _findings(audit)


def test_the_audit_is_silent_when_the_chosen_card_is_exiled(card_db, audit):
    game = _game(card_db)
    target = _add(game, card_db, "Lightning Bolt", 1, "graveyard")
    _cast(game, card_db, "Purify the Grave", target)
    assert not _findings(audit)
