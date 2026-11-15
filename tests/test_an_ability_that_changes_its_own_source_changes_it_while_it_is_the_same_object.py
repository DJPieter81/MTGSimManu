"""An ability that changes its own source's characteristics until end of
turn changes its source, while it is still the object the ability came
from (CR 611.2a, 611.2c, 613.4c, 400.7).

"Whenever you cast a noncreature spell, this creature gets +2/+0 until end
of turn": as the trigger resolves, its source gets +2/+0 until the turn
ends, once for each spell that triggers it. A source that has left the
battlefield, or left and come back as a new object, is not changed (CR
400.7).

The defect: no carrier performed a self change. The attack and cast
carriers' trigger body admitted a characteristic change only on a target,
so every self-change trigger stayed on a legacy pass or did nothing. For
cast triggers that pass is the cast manager's oracle scan: it pumps a
creature whose text has a "whenever you cast ... noncreature spell /
instant or sorcery" clause on EVERY noncreature spell (an artifact spell
pumps an instant-or-sorcery head), and it reads only "+N/+M", so a keyword
grant did nothing. An attack trigger's self change did nothing.

Class: 418 pool cards print a self characteristic change in a triggered
ability (52 cast heads, 88 attack heads). Registered: Slickshot Show-Off
(Izzet Prowess x4). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType, Keyword
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


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
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 3
    for _ in range(6):
        _add(game, card_db, "Mountain", 0)
        _add(game, card_db, "Island", 1)
    for p in (0, 1):
        for _ in range(6):
            _add(game, card_db, "Swamp", p, "library")
    return game


def _cast(game, card_db, name, controller=0, targets=None):
    spell = _add(game, card_db, name, controller, "hand")
    assert game.cast_spell(controller, spell, targets or [])
    return spell


def _creature(game, oracle, controller=0, power=1, toughness=1):
    """A synthetic creature printing `oracle`."""
    tmpl = CardTemplate(
        name="Self Probe", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(), supertypes=[], subtypes=[], power=power,
        toughness=toughness, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text=oracle, tags=set())
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(),
                        zone="battlefield")
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _pt(card):
    return (card.power, card.toughness)


# ── The cast carrier ───────────────────────────────────────────────────

def test_a_cast_trigger_changes_its_own_source_once_for_each_spell(card_db):
    game = _game(card_db)
    show = _add(game, card_db, "Slickshot Show-Off", 0)
    base = _pt(show)
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert _pt(show) == (base[0] + 2, base[1])
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert _pt(show) == (base[0] + 4, base[1])


def test_the_change_ends_with_the_turn(card_db):
    game = _game(card_db)
    show = _add(game, card_db, "Slickshot Show-Off", 0)
    base = _pt(show)
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    game.resolve_stack()
    game.cleanup_step()          # CR 514.2
    assert _pt(show) == base


def test_a_spell_its_head_does_not_name_does_not_change_it(card_db):
    """An instant-or-sorcery head is not met by an artifact spell, though
    that spell is noncreature."""
    game = _game(card_db)
    probe = _creature(game, "Whenever you cast an instant or sorcery spell, "
                      "this creature gets +1/+1 until end of turn.")
    _cast(game, card_db, "Welding Jar")
    assert _pt(probe) == (1, 1)
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert _pt(probe) == (2, 2)


def test_a_cast_trigger_grants_its_own_source_a_keyword(card_db):
    game = _game(card_db)
    probe = _creature(game, "Whenever you cast a noncreature spell, this "
                      "creature gains flying until end of turn.")
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert Keyword.FLYING in probe.keywords
    game.resolve_stack()
    game.cleanup_step()          # CR 514.2
    assert Keyword.FLYING not in probe.keywords


# ── The attack carrier ─────────────────────────────────────────────────

def test_an_attack_trigger_changes_its_own_source(card_db):
    from engine.combat_manager import CombatManager
    game = _game(card_db)
    probe = _creature(game, "Whenever this creature attacks, this creature "
                      "gets +2/+0 and gains first strike until end of "
                      "turn.", power=2, toughness=2)
    game.current_phase = Phase.DECLARE_ATTACKERS
    CombatManager().declare_attackers(game, [probe], 0)
    assert _pt(probe) == (4, 2)
    assert Keyword.FIRST_STRIKE in probe.keywords


# ── The executor: the source must still be the same object ─────────────

def _self_host(card):
    (h,) = [h for h in card.template.effects.front()
            if h.trigger is not None]
    return h


def test_a_source_that_left_and_came_back_is_a_new_object_it_skips(card_db):
    """CR 400.7: the ability's source is the object it came from; once
    that object left the battlefield, the card that returned is another
    object, and "this creature" no longer names it."""
    from engine import effect_resolver as er
    game = _game(card_db)
    probe = _creature(game, "Whenever you cast a noncreature spell, this "
                      "creature gets +2/+0 until end of turn.")
    host = _self_host(probe)
    stale = er.handle_of(probe)
    game.zone_mgr.move_card(game, probe, "battlefield", "exile")
    game.zone_mgr.move_card(game, probe, "exile", "battlefield")
    er.resolve_ability(game, stale, 0, host, (), family="cast_trigger",
                       source_object=probe)
    assert _pt(probe) == (1, 1)
    er.resolve_ability(game, er.handle_of(probe), 0, host, (),
                       family="cast_trigger", source_object=probe)
    assert _pt(probe) == (3, 1)


def test_the_change_is_typed_for_the_trigger_carriers_only(card_db):
    """The self change runs in the attack and cast trigger families, whose
    hosts the harness proves; elsewhere it stays on its legacy pass."""
    from engine.effect_executors import (FAMILY_ATTACK_TRIGGER,
                                         FAMILY_CAST_TRIGGER,
                                         characteristic_supported,
                                         execute_continuous)
    game = _game(card_db)
    probe = _creature(game, "Whenever you cast a noncreature spell, this "
                      "creature gets +2/+0 until end of turn.")
    (s,) = _self_host(probe).specs
    assert characteristic_supported(s)
    assert execute_continuous.allowed_families(s) == frozenset(
        {FAMILY_ATTACK_TRIGGER, FAMILY_CAST_TRIGGER})


# ── Auditor (CR 400.7, 611.2c) ─────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_change_made_to_a_new_object(card_db, audit,
                                                          monkeypatch):
    from engine import effect_executors, effect_resolver as er
    monkeypatch.setattr(effect_executors, "_source_on_battlefield",
                        lambda ctx: effect_executors._source_object(ctx))
    game = _game(card_db)
    probe = _creature(game, "Whenever you cast a noncreature spell, this "
                      "creature gets +2/+0 until end of turn.")
    host = _self_host(probe)
    stale = er.handle_of(probe)
    game.zone_mgr.move_card(game, probe, "battlefield", "exile")
    game.zone_mgr.move_card(game, probe, "exile", "battlefield")
    er.resolve_ability(game, stale, 0, host, (), family="cast_trigger",
                       source_object=probe)
    assert "611.2c/own_source_change" in _rules(audit)


def test_the_audit_is_silent_when_the_source_is_changed(card_db, audit):
    game = _game(card_db)
    _add(game, card_db, "Slickshot Show-Off", 0)
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert "611.2c/own_source_change" not in _rules(audit)
