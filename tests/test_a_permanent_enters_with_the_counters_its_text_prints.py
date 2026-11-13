"""A permanent enters with the counters its own text says it enters with.

CR 614.1c: "<this permanent> enters with N <kind> counters on it" is a
replacement effect -- the permanent never exists on the battlefield without
them. CR 122.6: those counters are put on it as it enters, whatever route
it takes (cast, reanimated, returned, put onto the battlefield). CR 708.2a:
a face-down permanent has no text, so no such counters.

The grammar typed the sentence as a REPLACEMENT host and refused it, and no
code placed the counters: Moonshadow ("enters with six -1/-1 counters", a
1/1 that grows) entered as a 7/7 menace for one mana. The class is the 153
pool cards whose sentence carries fixed counts (+1/+1, -1/-1, oil, charge,
shield, ...). X counts stay with the X-cost path; "for each" counts, choices
and keyword counters (CR 122.1b, which the engine does not model) are
refused. Keyword reminder text ("Modular 1 (This creature enters with a
+1/+1 counter on it.)") is not a sentence of the card and adds nothing.

Owner: `CardInstance.enter_battlefield`, beside the CR 306.5b loyalty rule;
the counts come from the entry carrier (`effect_carrier.entry_counters`, over
`effect_views.entry_counters`), which reads the typed host with the grammar's
counter parser. Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine import rules_audit
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def _rules(findings):
    return sorted({f["rule"] for f in findings if f["kind"] == "violation"})


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, name
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    getattr(game.players[controller], zone).append(card)
    return card


def _cast(game, card_db, name, lands):
    for land in lands:
        _add(game, card_db, land, 0, "battlefield").enter_battlefield()
    card = _add(game, card_db, name, 0, "hand")
    assert game.cast_spell(0, card, [])
    game.resolve_stack()
    assert card.zone == "battlefield"
    return card


def test_a_creature_cast_enters_with_its_minus_counters(card_db):
    game = _game()
    shadow = _cast(game, card_db, "Moonshadow", ["Swamp"])
    assert shadow.counter_count("-1/-1") == 6
    assert (shadow.power, shadow.toughness) == (1, 1)


def test_the_counters_come_on_every_route_onto_the_battlefield(card_db):
    game = _game()
    shadow = _add(game, card_db, "Moonshadow", 0, "graveyard")
    game.zone_mgr.move_card(game, shadow, "graveyard", "battlefield",
                            cause="reanimate")
    assert shadow.zone == "battlefield"
    assert shadow.counter_count("-1/-1") == 6


@pytest.mark.parametrize("name, kind, n", [
    ("Clockwork Beetle", "+1/+1", 2),
    ("Bladed Ambassador", "oil", 1),
    ("Bristlebane Battler", "-1/-1", 5),
])
def test_each_counter_kind_is_put_on_as_printed(card_db, name, kind, n):
    game = _game()
    card = _add(game, card_db, name, 0, "graveyard")
    game.zone_mgr.move_card(game, card, "graveyard", "battlefield",
                            cause="put")
    assert card.counter_count(kind) == n


def test_keyword_reminder_text_adds_no_counters(card_db):
    """Modular 1's reminder ("enters with a +1/+1 counter") is the
    keyword's, which places its one counter on a cast; it is not a
    sentence of the card."""
    game = _game()
    ravager = _add(game, card_db, "Arcbound Ravager", 0, "graveyard")
    game.zone_mgr.move_card(game, ravager, "graveyard", "battlefield",
                            cause="put")
    assert ravager.counter_count("+1/+1") == 0


def test_an_x_count_is_not_read_as_a_fixed_one(card_db):
    game = _game()
    cornucopia = _add(game, card_db, "Astral Cornucopia", 0, "graveyard")
    game.zone_mgr.move_card(game, cornucopia, "graveyard", "battlefield",
                            cause="put")
    assert cornucopia.counter_count("charge") == 0


def test_a_keyword_counter_shape_is_refused():
    """CR 122.1b keyword counters are not modelled: refused, not stored
    as an inert counter."""
    from engine.effect_grammar import parse_text_effects
    from engine.effect_carrier import entry_counters
    effects = parse_text_effects("Menace\nThis creature enters with a menace "
                                 "counter on it.")
    assert entry_counters(effects) == ()


def test_a_face_down_permanent_enters_with_no_counters(card_db):
    game = _game()
    shadow = _add(game, card_db, "Moonshadow", 0, "graveyard")
    game.zone_mgr.move_card(game, shadow, "graveyard", "battlefield",
                            cause="manifest", face_down=True)
    assert shadow.counter_count("-1/-1") == 0
    assert (shadow.power, shadow.toughness) == (2, 2)


def test_the_auditor_sees_a_permanent_enter_without_its_counters(
        audit, monkeypatch, card_db):
    """Re-create the defect: the entry places nothing."""
    game = _game()
    monkeypatch.setattr(CardInstance, "_put_entry_counters",
                        lambda self: None)
    _cast(game, card_db, "Moonshadow", ["Swamp"])
    assert "614.1c/entry_counters" in _rules(audit.drain())


def test_the_auditor_is_silent_when_the_counters_are_there(audit, card_db):
    game = _game()
    _cast(game, card_db, "Moonshadow", ["Swamp"])
    assert "614.1c/entry_counters" not in _rules(audit.drain())
