"""A permanent that exiles itself and returns transformed is a new object
showing its back face (CR 400.7, 712.8a, 306.5b).

"Exile ~, then return ~ to the battlefield transformed under its owner's
control": the permanent leaves the battlefield and a new object enters with
its back face up -- summoning sick, untapped, with none of the old object's
counters or damage; a planeswalker back face enters with the loyalty that
face prints (CR 306.5b). The return finds only the card its own exile moved
(CR 400.7): a permanent that has already left the battlefield is not exiled
and does not come back. Off the battlefield a double-faced card has only its
front face (CR 712.8a).

The draw carrier refused the shape, so a "when you draw your third card in a
turn" flip never happened (`603.2/draw_trigger_unresolved` in 22 of 25 rows
of the rank-1 arm); the legacy flips moved the card around the zone funnel,
so a flipped permanent never left the battlefield (no revolt, counters kept)
and a dead back face stayed a back face in the graveyard.

Card names are fixture carriers: 53 pool cards print the shape.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.game_state import GameState, Phase

TAMIYO = "Tamiyo, Inquisitive Student // Tamiyo, Seasoned Scholar"
FABLE = "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki"


def _game(active=0):
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = active
    g.turn_number = 5
    return g


def _put(game, card_db, name, zone, owner=0, controller=None):
    controller = owner if controller is None else controller
    c = CardInstance(template=card_db.get_card(name), owner=owner,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        game.players[controller].battlefield.append(c)
    else:
        getattr(game.players[owner], zone).append(c)
    return c


def _draw(game, card_db, idx, n=1):
    for _ in range(n):
        _put(game, card_db, "Island", "library", idx)
    game.draw_cards(idx, n)


def _flip_host(card_db):
    from engine.effect_spec import EventHint, Verb
    t = card_db.get_card(TAMIYO)
    return next(h for h in t.effects.front()
                if h.trigger is not None
                and EventHint.DRAW in h.trigger.event_hints
                and h.specs[0].verb is Verb.EXILE)


# ── The shape ──────────────────────────────────────────────────────────

def test_the_exile_and_return_transformed_shape_is_executable(card_db):
    from engine import effect_resolver as er
    from engine.effect_carrier import DRAW_FAMILY
    from engine.effect_views import STRICT
    h = _flip_host(card_db)
    assert STRICT[DRAW_FAMILY](h)
    assert er.can_execute(h, DRAW_FAMILY)


# ── The engine ─────────────────────────────────────────────────────────

def test_the_third_draw_returns_the_permanent_as_a_new_back_face_object(card_db):
    game = _game()
    tamiyo = _put(game, card_db, TAMIYO, "battlefield")
    seq = tamiyo.battlefield_entry_seq
    _draw(game, card_db, 0, 2)
    assert not tamiyo.is_transformed
    _draw(game, card_db, 0)
    assert tamiyo.zone == "battlefield" and tamiyo in game.players[0].battlefield
    assert tamiyo.is_transformed
    assert CardType.PLANESWALKER in tamiyo.effective_card_types
    assert tamiyo.loyalty_counters == card_db.get_card(TAMIYO).back_face_loyalty
    assert tamiyo.battlefield_entry_seq == seq + 1      # a new object
    assert tamiyo.summoning_sick


def test_it_returns_under_its_owners_control(card_db):
    game = _game()
    tamiyo = _put(game, card_db, TAMIYO, "battlefield", owner=1, controller=0)
    _draw(game, card_db, 0, 3)
    assert tamiyo.is_transformed and tamiyo.controller == 1
    assert tamiyo in game.players[1].battlefield
    assert tamiyo not in game.players[0].battlefield


def test_a_permanent_that_already_left_is_not_exiled_and_does_not_return(card_db):
    """CR 400.7: the source bounced before its trigger resolved is a new
    object in its owner's hand; neither instruction finds it."""
    from engine import effect_resolver as er
    from engine.effect_carrier import DRAW_FAMILY
    game = _game()
    tamiyo = _put(game, card_db, TAMIYO, "battlefield")
    handle = er.handle_of(tamiyo)
    game.zone_mgr.move_card(game, tamiyo, "battlefield", "hand")
    er.resolve_ability(game, handle, 0, _flip_host(card_db), (),
                       family=DRAW_FAMILY, event=er.TriggerEvent(player=0),
                       source_object=tamiyo)
    assert tamiyo.zone == "hand" and not tamiyo.is_transformed
    assert tamiyo not in game.players[0].battlefield


def test_an_exile_and_return_flip_leaves_the_battlefield(card_db):
    """The flip routes through the zone funnel: the permanent left the
    battlefield (revolt, CR 702.139) and the new object keeps none of the
    old one's counters."""
    from engine.oracle_resolver import _transform_permanent
    game = _game()
    fable = _put(game, card_db, FABLE, "battlefield")
    fable.add_plus_counters(1, game)
    _transform_permanent(game, fable, 0, returns_as_new_object=True)
    assert fable.is_transformed and fable.zone == "battlefield"
    assert fable.plus_counters == 0
    assert game.players[0].permanents_left_battlefield_this_turn == 1


def test_a_double_faced_card_off_the_battlefield_shows_its_front_face(card_db):
    from engine.oracle_resolver import _transform_permanent
    game = _game()
    fable = _put(game, card_db, FABLE, "battlefield")
    _transform_permanent(game, fable, 0, returns_as_new_object=True)
    assert CardType.CREATURE in fable.effective_card_types
    game._creature_dies(fable)
    assert fable.zone == "graveyard" and not fable.is_transformed
    assert fable.effective_card_types == card_db.get_card(FABLE).card_types


# ── The face it shows is the permanent (CR 712.8e) ────────────────────

def test_a_back_face_has_only_its_back_faces_attack_triggers(card_db):
    """The front face's chapter I prints a token with a quoted "whenever
    this creature attacks"; the back face prints no attack trigger, so the
    back face attacking makes nothing."""
    from engine.oracle_resolver import _transform_permanent
    game = _game()
    fable = _put(game, card_db, FABLE, "battlefield")
    _transform_permanent(game, fable, 0, returns_as_new_object=True)
    fable.summoning_sick = False
    before = list(game.players[0].battlefield)
    game.trigger_attack(fable, 0)
    assert game.players[0].battlefield == before


def test_a_saga_returned_transformed_is_no_longer_a_saga(card_db):
    """A back face that is a creature gets no lore counters and no
    chapters (CR 714): the flip happens once."""
    from engine.game_runner import GameRunner
    from engine.oracle_resolver import _transform_permanent
    from engine.saga import LORE_COUNTER, is_saga
    game = _game()
    fable = _put(game, card_db, FABLE, "battlefield")
    _transform_permanent(game, fable, 0, returns_as_new_object=True)
    assert not is_saga(fable)
    seq = fable.battlefield_entry_seq
    for _ in range(3):
        GameRunner._process_saga_chapters(None, game, 0)
    assert fable.battlefield_entry_seq == seq
    assert not fable.other_counters.get(LORE_COUNTER)


def test_a_creature_sweep_reads_the_face_a_permanent_shows(card_db):
    """A flipped back face that is a planeswalker is not a creature: the
    mass creature exile (Living End's battlefield half) leaves it."""
    game = _game()
    tamiyo = _put(game, card_db, TAMIYO, "battlefield")
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    _draw(game, card_db, 0, 3)
    assert tamiyo.is_transformed
    game._resolve_living_end(0)
    assert tamiyo.zone == "battlefield"
    assert bears.zone != "battlefield"


# ── Auditors (CR 306.5b, 712.8a) ───────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_back_face_entering_with_front_face_loyalty(
        card_db, audit, monkeypatch):
    real = CardInstance.enter_battlefield

    def front_face_loyalty(self):                       # the defect
        real(self)
        if self.is_transformed:
            self.loyalty_counters = self.template.loyalty or 0
    monkeypatch.setattr(CardInstance, "enter_battlefield", front_face_loyalty)
    game = _game()
    _put(game, card_db, TAMIYO, "battlefield")
    _draw(game, card_db, 0, 3)
    assert "306.5b/entry_loyalty" in _rules(audit)


def test_the_audit_records_a_back_face_left_in_another_zone(
        card_db, audit, monkeypatch):
    from engine.oracle_resolver import _transform_permanent
    from engine.zone_manager import ZoneManager
    real = ZoneManager._cleanup_leaving_battlefield

    def keeps_its_face(self, card):                     # the defect
        face = card.is_transformed
        real(self, card)
        card.is_transformed = face
    monkeypatch.setattr(ZoneManager, "_cleanup_leaving_battlefield",
                        keeps_its_face)
    game = _game()
    fable = _put(game, card_db, FABLE, "battlefield")
    _transform_permanent(game, fable, 0, returns_as_new_object=True)
    game._creature_dies(fable)
    assert "712.8a/front_face_off_battlefield" in _rules(audit)


def test_the_audits_are_silent_when_the_rules_hold(card_db, audit):
    from engine.oracle_resolver import _transform_permanent
    game = _game()
    _put(game, card_db, TAMIYO, "battlefield")
    _draw(game, card_db, 0, 3)
    fable = _put(game, card_db, FABLE, "battlefield")
    _transform_permanent(game, fable, 0, returns_as_new_object=True)
    game._creature_dies(fable)
    rules = _rules(audit)
    assert "306.5b/entry_loyalty" not in rules
    assert "712.8a/front_face_off_battlefield" not in rules
