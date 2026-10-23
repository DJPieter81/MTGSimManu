"""Cascade is a cast trigger, and its hit is cast onto the stack
(CR 702.85a, 603.3, 117.3b).

"Cascade (When you cast this spell, exile cards from the top of your
library until you exile a nonland card whose mana value is less than this
spell's. You may cast it without paying its mana cost. Put the exiled cards
on the bottom of your library in a random order.)" The trigger goes on the
stack above its spell as the spell is cast, so countering the spell -- on
cast by a lock, or later -- leaves the cascade, and a spell that fizzles has
already cascaded. The hit is cast: it goes on the stack above the cascade
spell, is a spell cast this turn, obeys every restriction on casting while
its cascade spell is still on the stack, and players may respond to it
before it resolves.

The defect: cascade ran during its spell's own resolution. A countered or
fizzled cascade spell never cascaded; the hit was cast from a hand and
resolved inline, before any player held priority, so it could never be
answered; and a mass-reanimation hit (Living End) was resolved in place by
a shortcut that never cast it at all.

Class: the 18 pool cards with the Cascade keyword. Card names are fixture
carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_runner import AICallbacks, GameRunner
from engine.game_state import GameState, Phase
from engine.stack import StackItem, StackItemType


def _game(seats=None):
    game = GameState(rng=random.Random(0),
                     callbacks=AICallbacks() if seats is not None else None)
    game.current_phase = Phase.MAIN1
    game.active_player = game.priority_player = 0
    game.turn_number = 5
    if seats is not None:
        game.callbacks.bind_players(seats)
    return game


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


def _library(game, card_db, names, idx=0):
    """The caster's library, top card first."""
    game.players[idx].library = []
    return [_put(game, card_db, n, "library", idx) for n in names]


def _cast(game, card_db, name, idx=0, targets=None):
    card = _put(game, card_db, name, "hand", idx)
    assert game.cast_spell(idx, card, targets=targets, free_cast=True)
    return card


def _drain(game):
    guard = 0
    while not game.stack.is_empty and guard < 50:
        game.resolve_stack()
        game.check_state_based_actions()
        guard += 1


def _kinds(game):
    return [(i.item_type, i.source.name) for i in game.stack.items]


# ── The trigger ────────────────────────────────────────────────────────

def test_casting_a_cascade_spell_puts_its_trigger_on_the_stack_above_it(
        card_db):
    game = _game()
    _library(game, card_db, ["Memnite"])
    agent = _cast(game, card_db, "Shardless Agent")
    assert _kinds(game) == [(StackItemType.SPELL, "Shardless Agent"),
                            (StackItemType.TRIGGERED_ABILITY, "Shardless Agent")]
    assert game.stack.top.source is agent


def test_countering_the_cascade_spell_does_not_stop_its_cascade(card_db):
    game = _game()
    _library(game, card_db, ["Memnite", "Island"])
    for _ in range(2):
        _put(game, card_db, "Island", "battlefield", 1)
    agent = _cast(game, card_db, "Shardless Agent")
    counter = _put(game, card_db, "Counterspell", "hand", 1)
    assert game.cast_spell(1, counter, targets=[agent.instance_id])
    _drain(game)
    assert agent.zone == "graveyard"
    assert "Memnite" in [c.name for c in game.players[0].battlefield]


def test_a_cascade_spell_countered_on_cast_by_a_lock_still_cascades(card_db):
    game = _game()
    _library(game, card_db, ["Memnite"])
    chalice = _put(game, card_db, "Chalice of the Void", "battlefield", 1)
    chalice.other_counters["charge"] = 3
    agent = _cast(game, card_db, "Shardless Agent")
    assert agent.zone == "graveyard"                 # countered on cast
    _drain(game)
    assert "Memnite" in [c.name for c in game.players[0].battlefield]


def test_a_fizzled_cascade_spell_still_cascaded(card_db):
    game = _game()
    _library(game, card_db, ["Memnite"])
    victim = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast(game, card_db, "Demonic Dread", targets=[victim.instance_id])
    game.zone_mgr.move_card(game, victim, "battlefield", "exile")
    _drain(game)                                      # Dread fizzles
    assert "Memnite" in [c.name for c in game.players[0].battlefield]


# ── The hit is cast onto the stack ─────────────────────────────────────

def test_the_hit_is_cast_onto_the_stack_above_the_cascade_spell(card_db):
    game = _game()
    _library(game, card_db, ["Memnite"])
    _cast(game, card_db, "Shardless Agent")
    game.resolve_stack()                              # the trigger
    assert _kinds(game) == [(StackItemType.SPELL, "Shardless Agent"),
                            (StackItemType.SPELL, "Memnite")]


def test_a_cascade_hit_is_a_cast(card_db):
    game = _game()
    _library(game, card_db, ["Memnite"])
    _cast(game, card_db, "Shardless Agent")
    game.resolve_stack()
    p = game.players[0]
    assert p.spells_cast_this_turn == 2
    assert len(p.spells_cast_this_turn_kinds) == 2
    assert game._global_storm_count == 2


def test_a_mass_reanimation_hit_is_cast_not_resolved_in_place(card_db):
    game = _game()
    _library(game, card_db, ["Living End"])
    _put(game, card_db, "Memnite", "graveyard", 0)
    _cast(game, card_db, "Shardless Agent")
    game.resolve_stack()                              # the trigger
    assert _kinds(game)[-1] == (StackItemType.SPELL, "Living End")
    assert "Memnite" not in [c.name for c in game.players[0].battlefield]
    _drain(game)
    names = [c.name for c in game.players[0].battlefield]
    assert "Memnite" in names and "Shardless Agent" in names


def test_a_cascaded_spell_can_be_countered(card_db):
    game = _game()
    _library(game, card_db, ["Living End"])
    _put(game, card_db, "Memnite", "graveyard", 0)
    for _ in range(2):
        _put(game, card_db, "Island", "battlefield", 1)
    agent = _cast(game, card_db, "Shardless Agent")
    game.resolve_stack()                              # the trigger
    living_end = game.stack.top.source
    counter = _put(game, card_db, "Counterspell", "hand", 1)
    assert game.cast_spell(1, counter, targets=[living_end.instance_id])
    _drain(game)
    assert living_end.zone == "graveyard"
    names = [c.name for c in game.players[0].battlefield]
    assert "Memnite" not in names and "Shardless Agent" in names
    assert agent.zone == "battlefield"


def test_a_cascaded_spell_is_cast_from_exile_so_a_cast_from_hand_rider_does_not_apply(
        card_db):
    game = _game()
    _library(game, card_db, ["Ephemerate"])
    _put(game, card_db, "Memnite", "battlefield", 0)
    _cast(game, card_db, "Shardless Agent")
    _drain(game)
    ephemerate = next(c for c in game.players[0].graveyard
                      + game.players[0].exile if c.name == "Ephemerate")
    assert ephemerate.zone == "graveyard"            # no rebound from exile


def test_a_hit_cast_while_its_cascade_spell_is_on_the_stack_obeys_a_sorcery_timing_restriction(
        card_db):
    game = _game()
    _library(game, card_db, ["Memnite", "Island"])
    _put(game, card_db, "Teferi, Time Raveler", "battlefield", 1)
    game.continuous_effects.recalculate(game)
    _cast(game, card_db, "Shardless Agent")          # at sorcery timing
    _drain(game)
    p = game.players[0]
    assert "Memnite" not in [c.name for c in p.battlefield]
    assert p.library[-1].name == "Memnite"           # not cast: to the bottom


def test_a_lock_counters_a_cascade_hit_of_its_mana_value(card_db):
    game = _game()
    _library(game, card_db, ["Living End"])
    _put(game, card_db, "Memnite", "graveyard", 0)
    chalice = _put(game, card_db, "Chalice of the Void", "battlefield", 1)
    chalice.other_counters["charge"] = 0
    _cast(game, card_db, "Shardless Agent")
    _drain(game)
    p = game.players[0]
    assert "Memnite" not in [c.name for c in p.battlefield]
    assert any(c.name == "Living End" for c in p.graveyard)


def test_the_hit_is_found_at_the_spells_mana_value_after_the_spell_left_the_stack(
        card_db):
    game = _game()
    _library(game, card_db, ["Kolaghan's Command", "Memnite"])
    for _ in range(2):
        _put(game, card_db, "Island", "battlefield", 1)
    agent = _cast(game, card_db, "Shardless Agent")    # mana value 3
    counter = _put(game, card_db, "Counterspell", "hand", 1)
    assert game.cast_spell(1, counter, targets=[agent.instance_id])
    _drain(game)
    p = game.players[0]
    assert "Memnite" in [c.name for c in p.battlefield]
    assert p.library[-1].name == "Kolaghan's Command"  # 3 is not less than 3


def test_the_cards_not_cast_go_to_the_bottom(card_db):
    game = _game()
    _library(game, card_db, ["Mountain", "Island", "Memnite", "Forest"])
    _cast(game, card_db, "Shardless Agent")
    _drain(game)
    lib = [c.name for c in game.players[0].library]
    assert lib[0] == "Forest"
    assert sorted(lib[1:]) == ["Island", "Mountain"]
    assert not any(c.name in ("Mountain", "Island")
                   for c in game.players[0].exile + game.players[0].hand)


# ── A trigger and its spell share one card ─────────────────────────────

def _spell_beneath_its_trigger(game, card_db, name, idx):
    card = CardInstance(template=card_db.get_card(name), owner=idx,
                        controller=idx, instance_id=game.next_instance_id(),
                        zone="stack")
    card._game_state = game
    spell = StackItem(item_type=StackItemType.SPELL, source=card,
                      controller=idx)
    game.stack.push(spell)
    game.stack.push(StackItem(item_type=StackItemType.TRIGGERED_ABILITY,
                              source=card, controller=idx,
                              effect=lambda *a: None))
    return card, spell


def test_a_stack_effect_on_a_creature_spell_takes_the_spell_not_its_trigger(
        card_db):
    game = _game()
    card, spell = _spell_beneath_its_trigger(game, card_db,
                                             "Shardless Agent", 1)
    subtlety = _put(game, card_db, "Subtlety", "battlefield", 0)
    from engine.card_effects import subtlety_etb
    subtlety_etb(game, subtlety, 0)
    assert spell not in game.stack.items
    assert [i.item_type for i in game.stack.items] == [
        StackItemType.TRIGGERED_ABILITY]
    assert game.players[1].library[0] is card


def test_a_stack_exile_takes_the_topmost_spell_not_an_ability(card_db):
    game = _game()
    card, spell = _spell_beneath_its_trigger(game, card_db,
                                             "Shardless Agent", 1)
    queller = _put(game, card_db, "Spell Queller", "battlefield", 0)
    from engine.card_effects import spell_queller_etb
    spell_queller_etb(game, queller, 0)
    assert spell not in game.stack.items
    assert [i.item_type for i in game.stack.items] == [
        StackItemType.TRIGGERED_ABILITY]
    assert card.zone == "exile"


def test_a_counter_without_a_chosen_target_counters_the_topmost_spell(
        card_db):
    game = _game()
    card, spell = _spell_beneath_its_trigger(game, card_db,
                                             "Shardless Agent", 1)
    counter = CardInstance(template=card_db.get_card("Counterspell"),
                           owner=0, controller=0,
                           instance_id=game.next_instance_id(), zone="stack")
    counter._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=counter,
                     controller=0, targets=[])
    game._execute_spell_effects(item)
    assert spell not in game.stack.items
    assert card.zone == "graveyard"


# ── With priority between resolutions (unit PB) ────────────────────────

class _Seat:
    def __init__(self, idx):
        self.player_idx = idx
        self.offers = []

    def decide_response(self, game, item):
        self.offers.append(item.source.name)
        return None


def test_the_opponent_is_asked_about_the_cascade_hit_and_then_the_cascade_spell(
        card_db):
    seats = [_Seat(0), _Seat(1)]
    game = _game(seats)
    _library(game, card_db, ["Memnite"])
    _cast(game, card_db, "Shardless Agent")
    GameRunner(card_db)._resolve_stack_loop(game)
    assert seats[1].offers == ["Memnite", "Shardless Agent"]


# ── The AI prices the spell on the stack without its trigger ───────────

def test_a_spell_on_the_stack_is_priced_without_the_cascade_its_trigger_carries(
        card_db):
    from ai.response import ResponseDecider

    def threat(library):
        game = _game()
        _library(game, card_db, library)
        for _ in range(4):
            _put(game, card_db, "Memnite", "graveyard", 0)
        _cast(game, card_db, "Shardless Agent")
        spell = next(i for i in game.stack.items
                     if i.item_type is StackItemType.SPELL)
        return ResponseDecider(1).evaluate_stack_threat(game, spell)

    assert threat(["Living End"] * 3) == pytest.approx(
        threat(["Island"] * 3))


# ── Auditors (CR 702.85a) ──────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_cascade_spell_cast_without_its_trigger(
        card_db, audit, monkeypatch):
    from engine.cast_manager import CastManager
    monkeypatch.setattr(CastManager, "put_cast_triggers",
                        staticmethod(lambda game, item: None))   # the defect
    game = _game()
    _library(game, card_db, ["Memnite"])
    _cast(game, card_db, "Shardless Agent")
    assert "702.85a/cascade_triggers_on_cast" in _rules(audit)


def test_the_audit_records_a_hit_resolved_inside_its_trigger(
        card_db, audit, monkeypatch):
    real_cast = GameState.cast_spell

    def cast_and_drain(self, *a, **k):                           # the defect
        ok = real_cast(self, *a, **k)
        while ok and self.stack.items:
            self.resolve_stack()
        return ok

    game = _game()
    _library(game, card_db, ["Memnite"])
    _put(game, card_db, "Island", "library", 0)
    agent = _cast(game, card_db, "Shardless Agent")
    monkeypatch.setattr(GameState, "cast_spell", cast_and_drain)
    game.resolve_stack()                                         # the trigger
    assert agent.zone == "battlefield"                           # drained
    assert "702.85a/cascade_hit_cast" in _rules(audit)


def test_the_audits_are_silent_on_the_cascade_path(card_db, audit):
    game = _game()
    _library(game, card_db, ["Mountain", "Memnite", "Island"])
    _cast(game, card_db, "Shardless Agent")
    _drain(game)
    rules = _rules(audit)
    assert "702.85a/cascade_triggers_on_cast" not in rules
    assert "702.85a/cascade_hit_cast" not in rules
