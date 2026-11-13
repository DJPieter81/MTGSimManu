"""A modal double-faced card whose back face is a land can be played as the
turn's land with that face up (CR 712, 305.1, 712.8a).

"Sink into Stupor // Soporific Springs": in hand it is an instant (only its
front face exists off the battlefield and stack); its controller may cast
the front face, or play the back face as the turn's land play -- the
permanent is then Soporific Springs, a land that taps for {U} and enters
tapped unless its controller pays 3 life. Leaving the battlefield it is
the instant again.

The engine never offered the land face: the decks that count these cards
among their lands (13 registered copies in six main decks) played them as
spells or not at all -- Azorius Control never played its Sink into Stupor in
a 24-game scan.

Card names are fixture carriers: 50 pool modal double-faced cards have a
land back face.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.game_state import GameState, Phase

SINK = "Sink into Stupor // Soporific Springs"


def _game(active=0):
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = active
    g.turn_number = 5
    return g


def _put(game, card_db, name, zone, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


# ── The land face, typed at load ───────────────────────────────────────

def test_the_land_face_is_typed_at_load(card_db):
    t = card_db.get_card(SINK)
    face = t.playable_land_face
    assert face is t.back_face_template and face.is_land
    assert face.name == "Soporific Springs" and face.produces_mana == ["U"]
    assert face.untap_life_cost == 3
    assert card_db.get_card("Island").playable_land_face is \
        card_db.get_card("Island")
    assert card_db.get_card("Lightning Bolt").playable_land_face is None


# ── The engine ─────────────────────────────────────────────────────────

def test_the_land_face_is_played_as_the_turns_land(card_db):
    game = _game()
    sink = _put(game, card_db, SINK, "hand")
    assert sink in game.get_legal_plays(0)
    game.play_land(0, sink)
    p = game.players[0]
    assert sink.zone == "battlefield" and sink in p.battlefield
    assert sink.name == "Soporific Springs"
    assert CardType.LAND in sink.effective_card_types
    assert sink.template.produces_mana == ["U"]
    assert p.lands_played_this_turn == 1


def test_it_enters_tapped_unless_its_life_is_paid(card_db):
    game = _game()
    sink = _put(game, card_db, SINK, "hand")
    life = game.players[0].life
    game.play_land(0, sink)
    paid = game.players[0].life == life - 3
    assert sink.tapped != paid


def test_off_the_battlefield_it_is_its_front_face_again(card_db):
    game = _game()
    sink = _put(game, card_db, SINK, "hand")
    game.play_land(0, sink)
    game.zone_mgr.move_card(game, sink, "battlefield", "hand")
    assert sink.template is card_db.get_card(SINK)
    assert sink.template.card_types == [CardType.INSTANT]


def test_it_is_the_turns_land_play(card_db):
    game = _game()
    _put(game, card_db, "Island", "hand")
    sink = _put(game, card_db, SINK, "hand")
    island = game.players[0].hand[0]
    game.play_land(0, island)
    game.play_land(0, sink)
    assert sink.zone == "hand"


def test_a_card_with_no_land_face_is_no_land_play(card_db):
    game = _game()
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    game.play_land(0, bolt)
    assert bolt.zone == "hand"
    assert game.players[0].lands_played_this_turn == 0


# ── The AI ─────────────────────────────────────────────────────────────

def _ai(deck="Azorius Control"):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name=deck, rng=random.Random(0))


def test_the_ai_plays_the_land_face_when_it_has_no_other_land(card_db):
    game = _game()
    sink = _put(game, card_db, SINK, "hand")
    _put(game, card_db, "Supreme Verdict", "hand")
    decision = _ai().decide_main_phase(game)
    assert decision is not None and decision[0] == "play_land"
    assert decision[1] is sink


def test_the_ai_keeps_the_spell_face_when_a_true_land_gives_the_same_drop(card_db):
    """A true land and the land face give the same land drop; the modal
    card also keeps its spell, so the true land is played."""
    game = _game()
    _put(game, card_db, SINK, "hand")
    island = _put(game, card_db, "Island", "hand")
    decision = _ai().decide_main_phase(game)
    assert decision is not None and decision[0] == "play_land"
    assert decision[1] is island


def test_a_land_face_counts_as_a_land_for_the_keep(card_db):
    from ai.mulligan import MulliganDecider
    from ai.strategy_profile import ArchetypeStrategy
    game = _game()
    hand = [_put(game, card_db, n, "hand") for n in
            (SINK, SINK, "Supreme Verdict", "Teferi, Time Raveler",
             "Solitude", "Prismatic Ending", "Spell Snare")]
    decider = MulliganDecider(ArchetypeStrategy.CONTROL)
    decider.decide(hand, 7)
    assert "0 lands" not in decider.last_reason


def test_a_land_face_counts_toward_the_kept_hands_land_floor(card_db):
    """The bottom choice counts land options as the keep does: with one
    true land and one modal land face, neither is bottomed below the
    floor."""
    game = _game()
    hand = [_put(game, card_db, n, "hand") for n in
            ("Otawara, Soaring City", SINK, "Supreme Verdict",
             "Teferi, Time Raveler", "Solitude", "Prismatic Ending",
             "Spell Snare")]
    decider = _ai()._mulligan_decider          # the deck's gameplan scores
    for count in (1, 2):
        bottom = decider.choose_cards_to_bottom(hand, count)
        assert all(c.name != SINK for c in bottom), (count, bottom)


# ── Auditor (CR 712.8a) ────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_a_land_face_left_in_another_zone(
        card_db, audit, monkeypatch):
    from engine.zone_manager import ZoneManager
    real = ZoneManager._cleanup_leaving_battlefield

    def keeps_its_face(self, card):                     # the defect
        face, front = card.template, getattr(card, "_front_template", None)
        real(self, card)
        card.template, card._front_template = face, front
    monkeypatch.setattr(ZoneManager, "_cleanup_leaving_battlefield",
                        keeps_its_face)
    game = _game()
    sink = _put(game, card_db, SINK, "hand")
    game.play_land(0, sink)
    game.zone_mgr.move_card(game, sink, "battlefield", "hand")
    assert "712.8a/front_face_off_battlefield" in [
        f["rule"] for f in audit.drain()]


def test_the_audit_is_silent_when_the_front_face_returns(card_db, audit):
    game = _game()
    sink = _put(game, card_db, SINK, "hand")
    game.play_land(0, sink)
    game.zone_mgr.move_card(game, sink, "battlefield", "hand")
    assert "712.8a/front_face_off_battlefield" not in [
        f["rule"] for f in audit.drain()]
