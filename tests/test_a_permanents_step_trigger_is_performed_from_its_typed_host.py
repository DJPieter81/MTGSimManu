"""A permanent's "at the beginning of <step>" ability is performed from its
typed host, at that step, on the turn its head names (CR 603.2, 603.3b,
603.4).

As a step begins, every ability whose trigger event is that step of the
named player's turn triggers once. An intervening "if" is checked as it
triggers and again as it resolves (CR 603.4). Targets are chosen as the
ability is put on the stack (CR 603.3d), and the triggers go on the stack in
APNAP order, so the non-active player's resolve first (CR 603.3b). "That
player" is the active player.

The defect: no code performed a printed beginning-of-step ability. The
grammar typed the head (`EventHint.BEGINNING_OF`, its step phrase) and
nothing read it, so Bitterblossom never made a Faerie, Emperor of Bones
never exiled a graveyard card and Haliya, Guided by Light never drew.

Class: 1189 pool cards print 1257 beginning-of-step abilities; the closed
step table types 1218 of the heads, and this cut takes 89 cards whole.
Registered: Emperor of Bones (Grixis Reanimator x3), Haliya, Guided by
Light (Azorius Blink x2). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return _place(game, tmpl, zone, idx)


def _place(game, tmpl, zone, idx):
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _synthetic(game, oracle, idx):
    tmpl = CardTemplate(
        name="Step Probe", card_types=[CardType.ENCHANTMENT],
        mana_cost=ManaCost(), supertypes=[], subtypes=[], power=None,
        toughness=None, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text=oracle, tags=set())
    return _place(game, tmpl, "battlefield", idx)


def _step(game, step, active=0):
    from engine.turn_clock import Clock, ClockEvent, emit
    game.active_player = game.priority_player = active
    game.current_phase = Phase(step)
    emit(game, ClockEvent(Clock.STEP_BEGINS, active, step=step))


def _library(game, card_db, n, idx=0):
    for _ in range(n):
        _put(game, card_db, "Island", "library", idx)


def _tokens(game, idx):
    return [c.name for c in game.players[idx].battlefield
            if getattr(c, "is_token", False)]


# ── The typed head: a closed table of steps and turns ─────────────────

@pytest.mark.parametrize("name,step,whose", [
    ("Bitterblossom", "upkeep", "you"),
    ("Verdant Force", "upkeep", "each"),
    ("Gibbering Fiend", "upkeep", "opponent"),
    ("Overbeing of Myth", "draw", "you"),
    ("Sanctum of Stone Fangs", "main1", "you"),
    ("Emperor of Bones", "begin_combat", "you"),
    ("Haliya, Guided by Light", "end_step", "you"),
    ("Resplendent Angel", "end_step", "each"),
])
def test_a_step_head_is_typed_by_its_step_and_whose_turn(card_db, name,
                                                         step, whose):
    from engine.effect_spec import EventHint
    (head,) = [h.trigger for h in card_db.get_card(name).effects.faces[0]
               if h.trigger is not None
               and EventHint.BEGINNING_OF in h.trigger.event_hints]
    assert head.step_event is not None
    assert (head.step_event.step, head.step_event.whose) == (step, whose)
    assert step in {p.value for p in Phase}


def test_a_step_phrase_the_table_does_not_read_is_left_untyped(card_db):
    from engine.effect_spec import EventHint
    game = _game()
    curse = card_db.get_card("Curse of the Pierced Heart")
    heads = [h.trigger for h in curse.effects.faces[0]
             if h.trigger is not None
             and EventHint.BEGINNING_OF in h.trigger.event_hints]
    assert heads and all(h.step_event is None for h in heads)
    probe = _synthetic(game, "At the beginning of your second main phase, "
                       "you gain 1 life.", 0)
    (h,) = [h for h in probe.template.effects.faces[0] if h.trigger]
    assert h.trigger.step_event is None


# ── The carrier ───────────────────────────────────────────────────────

def test_an_upkeep_trigger_performs_its_instructions_at_its_controllers_upkeep(
        card_db):
    game = _game()
    _put(game, card_db, "Bitterblossom", "battlefield", 0)
    _step(game, "upkeep", active=0)
    assert game.players[0].life == 19
    assert _tokens(game, 0) == ["Faerie Rogue Token"]


def test_a_your_upkeep_head_does_not_trigger_on_an_opponents_upkeep(card_db):
    game = _game()
    _put(game, card_db, "Bitterblossom", "battlefield", 0)
    _step(game, "upkeep", active=1)
    assert game.players[0].life == 20 and _tokens(game, 0) == []


def test_an_each_opponents_upkeep_head_triggers_only_on_an_opponents_upkeep(
        card_db):
    """"Each opponent's upkeep": "that player" is the active player."""
    game = _game()
    _put(game, card_db, "Gibbering Fiend", "battlefield", 0)
    for name in ("Lightning Bolt", "Mountain", "Grizzly Bears",
                 "Ornithopter"):
        _put(game, card_db, name, "graveyard", 0)     # delirium: four types
    _step(game, "upkeep", active=0)
    assert game.players[1].life == 20
    _step(game, "upkeep", active=1)
    assert game.players[1].life == 19


def test_a_trailing_if_is_read_as_the_instruction_resolves(card_db):
    """Haliya's "draw a card if you've gained 3 or more life this turn"."""
    game = _game()
    _put(game, card_db, "Haliya, Guided by Light", "battlefield", 0)
    _library(game, card_db, 3)
    game.players[0].life_gained_this_turn = 2
    _step(game, "end_step")
    assert len(game.players[0].hand) == 0
    game.turn_number += 1
    game.players[0].life_gained_this_turn = 3
    _step(game, "end_step")
    assert len(game.players[0].hand) == 1


def test_an_intervening_if_is_checked_as_the_step_begins(card_db):
    game = _game()
    _put(game, card_db, "Griffin Aerie", "battlefield", 0)
    game.players[0].life_gained_this_turn = 2
    _step(game, "end_step")
    assert _tokens(game, 0) == []
    game.turn_number += 1
    game.players[0].life_gained_this_turn = 3
    _step(game, "end_step")
    assert _tokens(game, 0) == ["Griffin Token"]


def test_an_intervening_if_false_as_it_resolves_does_nothing(card_db):
    """The non-active player's trigger resolves first (CR 603.3b) and kills
    the active player's only creature; the active player's "if you control
    a creature" no longer holds as its trigger resolves (CR 603.4)."""
    game = _game()
    _synthetic(game, "At the beginning of your upkeep, if you control a "
               "creature, you gain 3 life.", 0)
    _synthetic(game, "At the beginning of each opponent's upkeep, this "
               "enchantment deals 3 damage to target creature.", 1)
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 0)
    _step(game, "upkeep", active=0)
    assert bears.zone == "graveyard"
    assert game.players[0].life == 20


def test_the_non_active_players_step_triggers_resolve_first(card_db,
                                                           monkeypatch):
    from engine import effect_resolver as er
    real = er.resolve_ability
    order = []

    def spy(game, source, controller, *a, **kw):
        order.append(controller)
        return real(game, source, controller, *a, **kw)
    monkeypatch.setattr(er, "resolve_ability", spy)
    game = _game()
    _put(game, card_db, "Verdant Force", "battlefield", 0)
    _put(game, card_db, "Verdant Force", "battlefield", 1)
    _step(game, "upkeep", active=0)
    assert order == [1, 0]
    assert _tokens(game, 0) == _tokens(game, 1) == ["Saproling Token"]


def test_a_life_history_condition_reads_the_turns_tally(card_db):
    """Resplendent Angel's "each end step, if you gained 5 or more life this
    turn": on an opponent's turn too, and the tally is the turn's."""
    from engine.turn_clock import Clock, ClockEvent, emit
    game = _game()
    _put(game, card_db, "Resplendent Angel", "battlefield", 0)
    game.players[0].life_gained_this_turn = 5
    _step(game, "end_step", active=1)
    assert _tokens(game, 0) == ["Angel Token"]
    game.turn_number += 1
    emit(game, ClockEvent(Clock.TURN_BEGINS, 0))      # the tallies reset
    _step(game, "end_step", active=0)
    assert _tokens(game, 0) == ["Angel Token"]


def test_a_beginning_of_combat_trigger_exiles_the_graveyard_card_it_targeted(
        card_db):
    game = _game()
    _put(game, card_db, "Emperor of Bones", "battlefield", 0)
    bears = _put(game, card_db, "Grizzly Bears", "graveyard", 1)
    _step(game, "begin_combat", active=1)
    assert bears.zone == "graveyard"
    _step(game, "begin_combat", active=0)
    assert bears.zone == "exile"


def test_a_step_trigger_fires_only_at_its_step(card_db):
    """Phyrexian Arena over a whole turn's steps: one card, one life."""
    game = _game()
    _put(game, card_db, "Phyrexian Arena", "battlefield", 0)
    _library(game, card_db, 5)
    for step in ("upkeep", "draw", "main1", "begin_combat", "end_step"):
        _step(game, step, active=0)
    assert len(game.players[0].hand) == 1 and game.players[0].life == 19


def test_a_step_trigger_triggers_from_the_face_shown(card_db):
    """Unholy Fiend's "at the beginning of your end step, you lose 1 life"
    is the back face's: it triggers only while that face is up."""
    game = _game()
    youth = _put(game, card_db, "Cloistered Youth // Unholy Fiend",
                 "battlefield", 0)
    _step(game, "end_step")
    assert game.players[0].life == 20
    youth.is_transformed = True
    game.turn_number += 1
    _step(game, "end_step")
    assert game.players[0].life == 19


def test_a_card_the_carrier_cannot_take_whole_performs_none_of_them(card_db):
    """Ocelot Pride's rider (token copies under the city's blessing) is not
    modelled, so its end-step token is not made either."""
    game = _game()
    _put(game, card_db, "Ocelot Pride", "battlefield", 0)
    game.players[0].life_gained_this_turn = 1
    _step(game, "end_step")
    assert _tokens(game, 0) == []


def test_the_runner_begins_each_step_once_a_turn(card_db, monkeypatch):
    """A real game: every step the carrier is asked about begins once per
    turn, in turn order, and a skipped draw step (the first turn on the
    play, CR 103.8a) triggers nothing."""
    import engine.effect_carrier as ec
    from decks.modern_meta import MODERN_DECKS
    from engine.game_runner import GameRunner
    seen = []
    real = ec.dispatch_step_triggers

    def spy(game, step):
        seen.append((game.turn_number, step))
        return real(game, step)
    monkeypatch.setattr(ec, "dispatch_step_triggers", spy)
    runner = GameRunner(card_db)
    a, b = MODERN_DECKS["Boros Energy"], MODERN_DECKS["Domain Zoo"]
    random.seed(50000)
    runner.rng.seed(50000)
    runner.run_game("Boros Energy", a["mainboard"], "Domain Zoo",
                    b["mainboard"])
    assert seen and len(seen) == len(set(seen))
    order = ["upkeep", "draw", "main1", "begin_combat", "end_step"]
    turns = {}
    for turn, step in seen:
        turns.setdefault(turn, []).append(step)
    for steps in turns.values():
        assert steps == sorted(steps, key=order.index)
    assert "draw" not in turns[min(turns)]


# ── AI: where a graveyard exile is aimed ──────────────────────────────

def test_a_graveyard_exile_trigger_aims_where_the_removal_owner_does(card_db):
    from ai.graveyard_removal import pick_removals
    from engine.game_runner import AICallbacks
    game = _game()
    game.callbacks = AICallbacks()
    _put(game, card_db, "Emperor of Bones", "battlefield", 0)
    pool = [_put(game, card_db, "Mountain", "graveyard", 1),
            _put(game, card_db, "Faithless Looting", "graveyard", 1)]
    want = pick_removals(game, 0, list(pool), 1, 0)
    _step(game, "begin_combat", active=0)
    assert [c for c in pool if c.zone == "exile"] == want


# ── Auditor (CR 603.2, 603.4), restated from raw fields ───────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_census_records_a_step_trigger_the_carrier_cannot_take(card_db,
                                                                   audit):
    game = _game()
    _put(game, card_db, "Ocelot Pride", "battlefield", 0)
    _step(game, "end_step")
    assert "603.2/step_trigger_unresolved" in _rules(audit)


def test_the_census_is_silent_for_a_step_trigger_it_takes(card_db, audit):
    game = _game()
    _put(game, card_db, "Bitterblossom", "battlefield", 0)
    _step(game, "upkeep")
    assert "603.2/step_trigger_unresolved" not in _rules(audit)


def test_the_audit_records_an_intervening_if_that_did_not_hold(card_db, audit,
                                                               monkeypatch):
    from engine import effect_conditions
    monkeypatch.setattr(effect_conditions, "history_condition_holds",
                        lambda *a, **k: True)                  # the defect
    game = _game()
    _put(game, card_db, "Griffin Aerie", "battlefield", 0)
    _step(game, "end_step")
    assert "603.4/step_trigger_intervening_if" in _rules(audit)


def test_the_audit_is_silent_when_the_intervening_if_is_read(card_db, audit):
    game = _game()
    _put(game, card_db, "Griffin Aerie", "battlefield", 0)
    for gained in (2, 3):
        game.turn_number += 1
        game.players[0].life_gained_this_turn = gained
        _step(game, "end_step")
    assert "603.4/step_trigger_intervening_if" not in _rules(audit)


def test_the_audit_records_a_step_begun_twice(card_db, audit):
    game = _game()
    _put(game, card_db, "Bitterblossom", "battlefield", 0)
    _step(game, "upkeep")
    _step(game, "upkeep")                                      # the defect
    assert "603.2/step_trigger_once" in _rules(audit)


def test_the_audit_records_a_trigger_performed_on_a_turn_it_does_not_name(
        card_db, audit, monkeypatch):
    from engine import effect_carrier
    monkeypatch.setattr(effect_carrier, "step_matches",
                        lambda *a, **k: True)                  # the defect
    game = _game()
    _put(game, card_db, "Bitterblossom", "battlefield", 0)
    _step(game, "upkeep", active=1)
    assert "603.2/step_trigger_once" in _rules(audit)


def test_the_audit_is_silent_over_a_turns_steps(card_db, audit):
    game = _game()
    _put(game, card_db, "Bitterblossom", "battlefield", 0)
    _put(game, card_db, "Phyrexian Arena", "battlefield", 1)
    _library(game, card_db, 3, idx=1)
    for active in (0, 1):
        game.turn_number += 1
        for step in ("upkeep", "draw", "main1", "begin_combat", "end_step"):
            _step(game, step, active=active)
    rules = _rules(audit)
    assert "603.2/step_trigger_once" not in rules
    assert "603.2/step_trigger_unresolved" not in rules
