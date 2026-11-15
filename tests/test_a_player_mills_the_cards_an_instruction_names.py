"""A player mills the cards an instruction names (CR 701.17a).

"To mill a number of cards, a player puts that many cards from the top of
their library into their graveyard" -- each player the instruction names:
its controller ("mill N cards"), the player chosen for a target ("target
player mills N"), or a printed player set ("each opponent", "each player").
A player with fewer cards mills what there is (CR 701.17b). A card a
replacement effect sends elsewhere goes there instead (CR 614.1a).

The defect: the effect grammar typed MILL in 392 pool cards, but no
dispatcher family executed it. A spell whose text is a mill resolved doing
nothing (Glimpse the Unthinkable, Fractured Sanity); Thought Scour's mill
was dropped by the legacy draw clause. Self-mill enter triggers milled
through a regex branch and one card-name handler (Emry, Lurker of the
Loch), now retired.

Card names are fixture carriers: the MILL executor and the mill owner
(`GameState.mill`) serve every instruction of this shape.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF
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


def _game(card_db, library=20):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(library):
        _add(game, card_db, "Island", 1, "library")
        _add(game, card_db, "Swamp", 0, "library")
    return game


def _cast(game, card_db, name, targets):
    spell = _add(game, card_db, name, 0, "hand")
    assert game.cast_spell(0, spell, targets, free_cast=True)
    while not game.stack.is_empty:
        game.resolve_stack()
    return spell


def _sizes(game):
    return [(len(p.library), len(p.graveyard)) for p in game.players]


def test_the_player_chosen_for_a_target_mills(card_db):
    game = _game(card_db)
    _cast(game, card_db, "Glimpse the Unthinkable", [PLAYER_TARGET_OPPONENT])
    assert _sizes(game)[1] == (10, 10)
    assert _sizes(game)[0] == (20, 1), "only the spell itself"


def test_a_caster_aiming_at_itself_mills_its_own_library(card_db):
    game = _game(card_db)
    _cast(game, card_db, "Glimpse the Unthinkable", [PLAYER_TARGET_SELF])
    assert _sizes(game)[0] == (10, 11)
    assert _sizes(game)[1] == (20, 0)


def test_each_player_a_printed_set_names_mills(card_db):
    game = _game(card_db)
    _cast(game, card_db, "Chill of Foreboding", [])
    assert _sizes(game) == [(15, 6), (15, 5)]
    game = _game(card_db)
    _cast(game, card_db, "Fractured Sanity", [])
    assert _sizes(game) == [(20, 1), (6, 14)]


def test_a_player_with_fewer_cards_mills_what_there_is(card_db):
    game = _game(card_db, library=3)
    _cast(game, card_db, "Glimpse the Unthinkable", [PLAYER_TARGET_OPPONENT])
    assert _sizes(game)[1] == (0, 3)


def test_a_milled_card_a_replacement_exiles_goes_to_exile(card_db):
    """Under a "cards ... would be put into a graveyard ... exile it
    instead" replacement (CR 614.1a), a milled card is exiled."""
    game = _game(card_db)
    _add(game, card_db, "Rest in Peace", 0, "battlefield")
    _cast(game, card_db, "Glimpse the Unthinkable", [PLAYER_TARGET_OPPONENT])
    opp = game.players[1]
    assert (len(opp.library), len(opp.graveyard), len(opp.exile)) == \
        (10, 0, 10)


def test_an_enter_trigger_mill_is_performed_once(card_db):
    """'When ~ enters, mill four cards', through the enter-trigger carrier
    and no card-name handler: exactly four."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    assert not EFFECT_REGISTRY.has_handler("Emry, Lurker of the Loch",
                                           EffectTiming.ETB)
    game = _game(card_db)
    emry = _add(game, card_db, "Emry, Lurker of the Loch", 0, "hand")
    assert game.cast_spell(0, emry, [], free_cast=True)
    while not game.stack.is_empty:
        game.resolve_stack()
    assert emry.zone == "battlefield"
    assert _sizes(game)[0] == (16, 4)


# ── The auditor (CR 701.17a, restated from library sizes) ─────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _mill_findings(audit):
    return [f for f in audit.drain() if f["rule"] == "701.17a/mill"]


def test_the_audit_records_a_mill_that_moved_too_few_cards(card_db, audit,
                                                           monkeypatch):
    original = GameState.mill

    def short(self, player_idx, n, cause=""):           # the defect
        return original(self, player_idx, max(0, n - 1), cause)

    monkeypatch.setattr(GameState, "mill", short)
    game = _game(card_db)
    _cast(game, card_db, "Glimpse the Unthinkable", [PLAYER_TARGET_OPPONENT])
    assert _mill_findings(audit)


def test_the_audit_is_silent_when_the_mill_is_whole(card_db, audit):
    game = _game(card_db, library=3)
    _cast(game, card_db, "Glimpse the Unthinkable", [PLAYER_TARGET_OPPONENT])
    _add(game, card_db, "Rest in Peace", 0, "battlefield")
    _cast(game, card_db, "Fractured Sanity", [])
    assert not _mill_findings(audit)
