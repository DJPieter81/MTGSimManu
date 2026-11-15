"""A land-destruction activation destroys the land it targets (CR 602.2,
701.8, 701.19, 701.20).

"[Cost]: Destroy target [nonbasic] land [an opponent controls]." is an
activated ability like any other: its target is chosen as it is activated
(CR 602.2b), and on resolution the land is destroyed -- put into its
owner's graveyard (CR 701.8a), unless it is indestructible (CR 702.12b).
Its riders follow in printed order (CR 608.2c):
- "its controller may search their library for a basic land card, put it
  onto the battlefield, then shuffle";
- "each player searches their library for a basic land card, puts it onto
  the battlefield, then shuffles" (CR 101.4: in turn order);
- "you may search your library for a basic land card, ...".
A search finds what it finds; the searcher shuffles after (CR 701.19,
701.20).

The defect: the activation classifier did not read these abilities, so
they stayed UNCLASSIFIED, declared no target and were never activated --
Boros Ponza's eight Field of Ruin and Demolition Field were colorless
lands. They now resolve from their typed text on the dispatcher, in the
removal family.

Card names are fixture carriers: 10 pool activations destroy a target
land, 104 a target permanent of some type.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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


def _game(card_db, source="Field of Ruin"):
    """Player 0 with `source` and three Mountains; player 0's library holds
    Plains, player 1's Islands; player 1 controls a Forest and a Breeding
    Pool."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    src = _add(game, card_db, source, 0)
    for _ in range(3):
        _add(game, card_db, "Mountain", 0)
    for _ in range(3):
        _add(game, card_db, "Plains", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    forest = _add(game, card_db, "Forest", 1)
    pool = _add(game, card_db, "Breeding Pool", 1)
    return game, src, forest, pool


def _ability(card):
    """The source's destroy-a-land activation (it sacrifices the source)."""
    return next(a for a in card.template.activated_abilities
                if a.cost.sacrifice_self)


def _activate(game, card, target):
    from engine.activation import ActivationManager
    assert ActivationManager.activate(game, 0, card, _ability(card),
                                      [target.instance_id])
    while not game.stack.is_empty:
        game.resolve_stack()


def _names(game, idx):
    return [c.name for c in game.players[idx].battlefield]


# ── The activation, on the dispatcher ─────────────────────────────────

@pytest.mark.parametrize("name", ["Field of Ruin", "Demolition Field",
                                  "Ghost Quarter", "Tectonic Edge"])
def test_a_destroy_target_land_activation_takes_the_removal_family(card_db,
                                                                   name):
    from engine.effect_carrier import activation_family
    t = card_db.get_card(name)
    ability = next(a for a in t.activated_abilities if a.cost.sacrifice_self)
    assert activation_family(ability,
                             t.effects.activated(ability.index)) == "removal"


def test_it_is_activated_only_with_a_legal_target(card_db):
    from engine.activation import ActivationManager
    game, field, forest, pool = _game(card_db)
    assert ActivationManager.can_activate(game, 0, field, _ability(field))
    game.players[1].battlefield.remove(pool)
    assert not ActivationManager.can_activate(game, 0, field,
                                              _ability(field)), \
        "a basic is no target for 'nonbasic land'"


# ── Resolution ────────────────────────────────────────────────────────

def test_the_land_is_destroyed_and_each_player_fetches_a_basic(card_db):
    game, field, forest, pool = _game(card_db)
    _activate(game, field, pool)
    assert pool.zone == "graveyard" and field.zone == "graveyard"
    assert "Plains" in _names(game, 0) and "Island" in _names(game, 1)


def test_a_destroyed_lands_controller_may_replace_it_with_a_basic(card_db):
    game, quarter, forest, pool = _game(card_db, source="Ghost Quarter")
    _activate(game, quarter, forest)
    assert forest.zone == "graveyard"
    assert "Island" in _names(game, 1) and "Plains" not in _names(game, 0)


def test_you_may_search_too_when_the_ability_says_so(card_db):
    game, field, forest, pool = _game(card_db, source="Demolition Field")
    _activate(game, field, pool)
    assert pool.zone == "graveyard"
    assert "Island" in _names(game, 1) and "Plains" in _names(game, 0)


def test_with_its_target_gone_the_ability_does_nothing_not_even_its_riders(
        card_db):
    """CR 608.2b: an ability whose every target is illegal on resolution
    does not resolve -- no one searches."""
    from engine.activation import ActivationManager
    game, field, forest, pool = _game(card_db)
    assert ActivationManager.activate(game, 0, field, _ability(field),
                                      [pool.instance_id])
    game.zone_mgr.move_card(game, pool, "battlefield", "graveyard",
                            cause="sacrificed in response")
    while not game.stack.is_empty:
        game.resolve_stack()
    assert "Plains" not in _names(game, 0)
    assert "Island" not in _names(game, 1)


def test_an_indestructible_land_is_not_destroyed(card_db):
    """CR 702.12b; the rider is its own instruction and still happens."""
    game, quarter, forest, pool = _game(card_db, source="Ghost Quarter")
    citadel = _add(game, card_db, "Darksteel Citadel", 1)
    _activate(game, quarter, citadel)
    assert citadel.zone == "battlefield"
    assert "Island" in _names(game, 1)


# ── The AI ────────────────────────────────────────────────────────────

def test_the_ai_offers_it_against_a_nonbasic_and_aims_at_it(card_db):
    """Against an opponent whose only blue source is the nonbasic and who
    holds a blue spell, with no basic in their library to replace it."""
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game
    game, field, forest, pool = _game(card_db)
    for c in list(game.players[1].library):
        game.players[1].library.remove(c)
    for _ in range(3):
        _add(game, card_db, "Counterspell", 1, "library")
    _add(game, card_db, "Counterspell", 1, "hand")
    cands = [c for c in activation_candidates(
        game, 0, snapshot_from_game(game, 0)) if c[0] is field]
    assert cands and cands[0][2] == [pool.instance_id]
    game.players[1].battlefield.remove(pool)
    assert not [c for c in activation_candidates(
        game, 0, snapshot_from_game(game, 0)) if c[0] is field]


# ── The auditor (CR 701.8a) ───────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "701.8a/destroyed"]


def test_the_audit_records_a_destroyed_permanent_left_on_the_battlefield(
        card_db, audit, monkeypatch):
    game, field, forest, pool = _game(card_db)
    monkeypatch.setattr(game, "_permanent_destroyed",
                        lambda perm: None)                  # the defect
    _activate(game, field, pool)
    assert pool.zone == "battlefield"
    assert _findings(audit)


def test_the_audit_is_silent_when_the_land_is_destroyed(card_db, audit):
    game, field, forest, pool = _game(card_db)
    _activate(game, field, pool)
    assert not _findings(audit)
