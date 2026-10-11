"""A type an effect adds is a characteristic of the object while the effect
lasts (CR 205.1b, 613.1d, 611.2a/c).

"Until end of turn, target creature you control becomes an Avatar in
addition to its other types and gains flying, first strike, lifelink, and
hexproof": the chosen object has the added subtype and keywords until the
cleanup step (CR 514.2), and only that object -- a creature that leaves and
returns is a new object with neither (CR 400.7). "Target permanent becomes
an artifact in addition to its other types until end of turn" makes a land
an artifact for every rule that reads artifacts.

The legacy spell parser resolved Enter the Avatar State to nothing
(`unhandled/spell` in 22 of 25 rows of the gh-post arm) because nothing
could add a type to an object; the typed host -- CONTINUOUS ADD_TYPES and
ADD_KEYWORDS on the target, this turn -- had no executor.

Card names are fixture carriers: 27 pool cards add a type to an object;
277 pool spells change a target's characteristics until end of turn.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType, Keyword
from engine.game_state import GameState, Phase


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


def _cast_avatar_state(game, card_db, target):
    _put(game, card_db, "Plains", "battlefield")
    spell = _put(game, card_db, "Enter the Avatar State", "hand")
    assert game.cast_spell(0, spell, targets=[target.instance_id])
    game.resolve_stack()
    return spell


def test_the_target_has_the_added_subtype_and_keywords_this_turn(card_db):
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    _cast_avatar_state(game, card_db, bears)
    assert "Avatar" in bears.effective_subtypes
    assert "Bear" in bears.effective_subtypes
    assert {Keyword.FLYING, Keyword.FIRST_STRIKE, Keyword.LIFELINK,
            Keyword.HEXPROOF} <= bears.keywords


def test_the_added_type_and_keywords_end_at_cleanup(card_db):
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    _cast_avatar_state(game, card_db, bears)
    game.cleanup_step()               # CR 514.2: they end simultaneously
    assert "Avatar" not in bears.effective_subtypes
    assert Keyword.FLYING not in bears.keywords


def test_a_new_object_carries_no_added_type(card_db):
    """CR 400.7: blinked or bounced, the creature is a new object the
    effect never named."""
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    _cast_avatar_state(game, card_db, bears)
    game.zone_mgr.move_card(game, bears, "battlefield", "hand")
    game.zone_mgr.move_card(game, bears, "hand", "battlefield")
    game.continuous_effects.recalculate(game)
    assert "Avatar" not in bears.effective_subtypes
    assert Keyword.HEXPROOF not in bears.keywords


def test_an_added_card_type_is_read_by_every_rule(card_db):
    """An activated ability: the land is an artifact until end of turn."""
    from engine.activation import ActivationManager
    game = _game()
    coating = _put(game, card_db, "Liquimetal Coating", "battlefield")
    land = _put(game, card_db, "Forest", "battlefield", 1)
    ability = coating.template.activated_abilities[0]
    assert ActivationManager.activate(game, 0, coating, ability,
                                      targets=[land.instance_id])
    game.resolve_stack()
    assert CardType.ARTIFACT in land.effective_card_types
    assert CardType.LAND in land.effective_card_types


def test_a_pump_spell_a_legacy_handler_claims_stays_on_its_handler(card_db):
    """The typed host is taken only when no legacy handler claims the
    spell ("dispatched" is the registry's last handler)."""
    from engine.effect_carrier import spell_family
    assert spell_family(card_db.get_card("Enter the Avatar State")) == \
        "type_change"
    # A keyword-and-P/T pump stays with its legacy applier.
    assert spell_family(card_db.get_card("Giant Growth")) is None
    from engine.clause_resolver import HANDLERS
    assert HANDLERS[-1].name == "dispatched"


# ── Auditor (CR 400.7) ─────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_a_new_object_entering_with_an_added_type(
        card_db, audit, monkeypatch):
    from engine.zone_manager import ZoneManager
    real = ZoneManager._cleanup_leaving_battlefield

    def keeps_added_types(self, card):                  # the defect
        added = set(card.cem_subtypes_added)
        real(self, card)
        card.cem_subtypes_added = added
    monkeypatch.setattr(ZoneManager, "_cleanup_leaving_battlefield",
                        keeps_added_types)
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    _cast_avatar_state(game, card_db, bears)
    game.zone_mgr.move_card(game, bears, "battlefield", "hand")
    game.zone_mgr.move_card(game, bears, "hand", "battlefield")
    assert "400.7/new_object_added_types" in [f["rule"] for f in audit.drain()]


def test_the_audit_is_silent_for_a_new_object(card_db, audit):
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    _cast_avatar_state(game, card_db, bears)
    game.zone_mgr.move_card(game, bears, "battlefield", "hand")
    game.zone_mgr.move_card(game, bears, "hand", "battlefield")
    assert "400.7/new_object_added_types" not in [
        f["rule"] for f in audit.drain()]
