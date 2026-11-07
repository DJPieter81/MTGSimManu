"""A storm copy is put onto the stack and resolves on its own.

CR 702.40a: storm is a triggered ability, "When you cast this spell, copy
it for each other spell that was cast before it this turn. If the spell
has targets, you may choose new targets for the copies." The trigger goes
on the stack above the spell (CR 603.3). When it resolves, the copies are
put onto the stack above the spell (CR 707.10), all of them at once, so
their targets are chosen together (CR 707.10c). Each copy is a stack
object of its own:
- it resolves alone, and state-based actions are checked before the next
  one does (CR 117.3b, 704.3);
- a copy whose every target has become illegal does not resolve (CR
  608.2b);
- a copy is not cast (CR 707.12), so it adds nothing to the storm count;
- once resolved it ceases to exist (CR 704.5e, 707.10a).
The trigger copies the spell even when the spell has left the stack (CR
702.40a, 707.10: last known information).

The defect: the copies ran inside the original's own resolution, after
its effect, with no state-based check between them. A planeswalker whose
loyalty had already reached 0 stayed on the battlefield, and the AI aimed
each copy anew, so it saw the walker as "killed by one more". Ruby Storm
vs Boros Energy s50000, game 2, turn 5: 8 of 9 Grapeshot damage went into
a 3-loyalty Ajani, and Boros survived at 9 life. 18 pool cards print
storm. Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase
from engine.stack import StackItemType

WALKER = "Teferi, Hero of Dominaria"


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


def _game(card_db, opp_life=20):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(4):
        _add(game, card_db, "Mountain", 0)
    for _ in range(3):
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    game.players[1].life = opp_life
    return game


def _cast(game, card_db, prior_spells, target=PLAYER_TARGET_OPPONENT,
          name="Grapeshot"):
    spell = _add(game, card_db, name, 0, "hand")
    game._global_storm_count = prior_spells
    assert game.cast_spell(0, spell, [target] if target is not None else None)
    return spell


def _drain(game):
    while not game.stack.is_empty:
        game.resolve_stack()


def _copies_on_stack(game):
    return [i for i in game.stack.items
            if getattr(i.source, "_is_spell_copy", False)]


# ── The trigger and its copies are stack objects ─────────────────────────

def test_storm_is_a_trigger_above_the_spell(card_db):
    game = _game(card_db)
    shot = _cast(game, card_db, prior_spells=3)
    top = game.stack.items[-1]
    assert top.item_type is StackItemType.TRIGGERED_ABILITY
    assert top.source is shot
    assert game.stack.items[-2].source is shot


def test_the_trigger_puts_the_copies_on_the_stack_above_the_spell(card_db):
    game = _game(card_db)
    shot = _cast(game, card_db, prior_spells=3)
    game.resolve_stack()                                 # the trigger
    assert len(_copies_on_stack(game)) == 3
    assert game.stack.items[0].source is shot            # still beneath
    assert game.players[1].life == 20                    # nothing resolved


def test_each_copy_resolves_on_its_own(card_db):
    game = _game(card_db)
    _cast(game, card_db, prior_spells=3)
    game.resolve_stack()
    game.resolve_stack()                                 # one copy
    assert game.players[1].life == 19
    assert len(_copies_on_stack(game)) == 2


def test_a_copy_ceases_to_exist_and_is_not_cast(card_db):
    game = _game(card_db)
    shot = _cast(game, card_db, prior_spells=3)
    _drain(game)
    assert game.players[1].life == 16
    assert [c for c in game.players[0].graveyard
            if c.name == shot.name] == [shot]
    assert game._global_storm_count == 4


def test_state_based_actions_are_checked_between_copies(card_db):
    """Every copy kept the walker as its target: the copies after its
    loyalty is gone find it in the graveyard and do not resolve."""
    game = _game(card_db)
    walker = _add(game, card_db, WALKER, 1)
    loyalty = walker.loyalty_counters
    _cast(game, card_db, prior_spells=loyalty + 2, target=walker.instance_id)
    _drain(game)
    assert walker.zone == "graveyard"
    assert any("fizzles" in line and "Grapeshot" in line for line in game.log)


def test_the_spell_is_copied_even_after_it_has_left_the_stack(card_db):
    game = _game(card_db)
    shot = _cast(game, card_db, prior_spells=2)
    original = game.stack.items[0]
    game.stack.items.remove(original)                    # countered
    game.zone_mgr.move_card_from_stack(game, shot, "graveyard",
                                       cause="countered")
    _drain(game)
    assert game.players[1].life == 18


def test_copies_of_a_spell_without_targets_resolve_too(card_db):
    game = _game(card_db)
    _cast(game, card_db, prior_spells=2, target=None,
          name="Empty the Warrens")
    _drain(game)
    goblins = [c for c in game.players[0].battlefield
               if getattr(c, "is_token", False)]
    assert len(goblins) == 6


# ── Targets for the copies are chosen together (CR 707.10c) ──────────────

def test_the_copies_targets_are_chosen_once_for_all_of_them(card_db):
    game = _game(card_db)
    asked = []

    def choose(g, p, item, copies):
        asked.append(copies)
        return None
    game.callbacks.choose_copy_targets = choose
    _cast(game, card_db, prior_spells=4)
    _drain(game)
    assert asked == [4]


def test_the_ai_spends_no_more_copies_on_a_permanent_than_destroy_it(
        card_db):
    """The replayed line: one damage per copy, a walker and the face. The
    walker takes its loyalty in copies or none, and the rest go to the
    face."""
    from engine.game_runner import AICallbacks
    game = _game(card_db)
    game.players[0].deck_name = "Ruby Storm"
    game.callbacks = AICallbacks()
    walker = _add(game, card_db, WALKER, 1)
    walker.loyalty_counters = loyalty = 3          # the replay's Ajani
    prior = loyalty + 5
    _cast(game, card_db, prior_spells=prior)
    _drain(game)
    dealt = 20 - game.players[1].life
    if walker.zone == "graveyard":
        assert dealt == prior + 1 - loyalty
    else:
        assert dealt == prior + 1


# ── The auditor (CR 707.10, 704.3) ───────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f for f in audit.drain()
            if f["rule"] == "707.10/copy_resolves_on_its_own"
            and f["kind"] == "violation"]


def test_the_auditor_sees_a_copy_resolved_inside_another_resolution(
        card_db, audit, monkeypatch):
    """Re-create the defect: the copies performed as the trigger
    resolves, never put on the stack."""
    from engine.cast_manager import CastManager
    monkeypatch.setattr(CastManager, "_put_copy", staticmethod(
        lambda game, copy: game._execute_spell_effects(copy)))
    game = _game(card_db)
    _cast(game, card_db, prior_spells=2)
    _drain(game)
    assert _violations(audit)


def test_the_auditor_is_silent_when_each_copy_resolves_on_its_own(
        card_db, audit):
    game = _game(card_db)
    _cast(game, card_db, prior_spells=2)
    _drain(game)
    assert not _violations(audit)
