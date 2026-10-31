"""A permanent's cast-triggered counter counters the spell its text names,
cast by any player its text names, under the condition it prints (CR 603.2,
701.6, 202.3e).

"Whenever a player casts a spell with mana value equal to the number of
charge counters on this artifact, counter that spell" covers every player --
the artifact's controller included -- and reads the spell's mana value on
the stack, its X counted (CR 202.3e). "Whenever a player casts a spell, if
no mana was spent to cast it, counter that spell" counters a spell cast
without mana: a free cast, a spell whose cost is 0 or was reduced to 0, an
alternative cost paid with no mana. "If no colored mana was spent" admits
colourless mana. "An opponent" excludes the permanent's controller, and
"this ability triggers only once each turn" counters only the turn's first
spell it names.

The defect: the engine's one lock predicate read only the opponent's
Chalice-family permanents, at the spell's printed mana value, so a player's
own Chalice never countered their own spell and an X spell was read at
X = 0; and the no-mana counters (Vexing Bauble, Lavinia, Boromir) were
performed by nothing, so a free cast resolved under them.

Class: 11 pool cards print a cast-triggered counter. Typed and performed:
Chalice of the Void, Vexing Bauble, Lavinia, Boromir, Void Mirror,
Jin-Gitaxias. Refused: "counter it unless ..." (Chancellor of the Annex,
Blood Funnel) and a counter with a rider (Dovescape). Registered: Chalice of
the Void (x24), Vexing Bauble (x11). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase


def _game(active=0):
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = active
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
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


def _chalice(game, card_db, idx, charge):
    chalice = _put(game, card_db, "Chalice of the Void", "battlefield", idx)
    chalice.other_counters["charge"] = charge
    return chalice


def _countered(game, card):
    return card.zone != "stack" and not any(
        i.source is card for i in game.stack.items)


def _bolt(game, card_db, idx=0, free=False):
    if not free:
        _put(game, card_db, "Mountain", "battlefield", idx)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", idx)
    assert game.cast_spell(idx, bolt, targets=[PLAYER_TARGET_OPPONENT],
                           free_cast=free)
    return bolt


# ── A Chalice covers every player, at the mana value on the stack ─────

def test_a_players_own_chalice_counters_their_spell_of_its_mana_value(card_db):
    game = _game()
    _chalice(game, card_db, 0, 1)
    bolt = _bolt(game, card_db, 0)
    assert _countered(game, bolt)


def test_an_opponents_chalice_counters_a_spell_of_its_mana_value(card_db):
    game = _game()
    _chalice(game, card_db, 1, 1)
    bolt = _bolt(game, card_db, 0)
    assert _countered(game, bolt)


def test_a_chalice_spares_a_spell_of_another_mana_value(card_db):
    game = _game()
    _chalice(game, card_db, 1, 2)
    bolt = _bolt(game, card_db, 0)
    assert not _countered(game, bolt)


def test_a_chalice_reads_an_x_spells_mana_value_on_the_stack(card_db):
    game = _game()
    _chalice(game, card_db, 1, 2)
    for _ in range(2):
        _put(game, card_db, "Wastes", "battlefield", 0)
    ballista = _put(game, card_db, "Walking Ballista", "hand", 0)  # {X}{X}
    assert game.cast_spell(0, ballista)
    assert _countered(game, ballista)                    # X = 1: mana value 2


# ── No mana spent / no coloured mana spent ────────────────────────────

def test_a_spell_cast_without_mana_is_countered_by_a_no_mana_counter(card_db):
    game = _game()
    _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    bolt = _bolt(game, card_db, 0, free=True)
    assert _countered(game, bolt)


def test_a_spell_paid_with_mana_is_not_countered_by_a_no_mana_counter(card_db):
    game = _game()
    _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    bolt = _bolt(game, card_db, 0)
    assert not _countered(game, bolt)


def test_a_spell_whose_cost_is_zero_spends_no_mana(card_db):
    game = _game()
    _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    memnite = _put(game, card_db, "Memnite", "hand", 0)
    assert game.cast_spell(0, memnite)
    assert _countered(game, memnite)


def test_colourless_mana_is_no_coloured_mana(card_db):
    game = _game()
    _put(game, card_db, "Void Mirror", "battlefield", 1)
    for _ in range(2):
        _put(game, card_db, "Wastes", "battlefield", 0)
    stone = _put(game, card_db, "Mind Stone", "hand", 0)
    assert game.cast_spell(0, stone)
    assert _countered(game, stone)
    game2 = _game()
    _put(game2, card_db, "Void Mirror", "battlefield", 1)
    bolt = _bolt(game2, card_db, 0)                      # paid with {R}
    assert not _countered(game2, bolt)


# ── Who it covers ─────────────────────────────────────────────────────

def test_an_opponent_counter_spares_its_controllers_spells(card_db):
    game = _game()
    _put(game, card_db, "Lavinia, Azorius Renegade", "battlefield", 0)
    own = _bolt(game, card_db, 0, free=True)
    assert not _countered(game, own)
    game2 = _game(active=1)
    _put(game2, card_db, "Boromir, Warden of the Tower", "battlefield", 0)
    theirs = _bolt(game2, card_db, 1, free=True)
    assert _countered(game2, theirs)


def test_a_once_each_turn_counter_counters_only_the_turns_first_spell(card_db):
    game = _game(active=1)
    _put(game, card_db, "Jin-Gitaxias, Progress Tyrant", "battlefield", 0)
    first = _bolt(game, card_db, 1)
    second = _bolt(game, card_db, 1)
    assert _countered(game, first) and not _countered(game, second)
    game.turn_number += 1
    third = _bolt(game, card_db, 1)
    assert _countered(game, third)


# ── Typed at load; refused shapes ─────────────────────────────────────

def test_the_cast_counter_is_typed_at_load(card_db):
    assert card_db.get_card("Chalice of the Void").cast_counter == (
        "any", "mana_value_equals_charge", None, False)
    assert card_db.get_card("Vexing Bauble").cast_counter == (
        "any", "spell", "no_mana_spent", False)
    assert card_db.get_card("Lavinia, Azorius Renegade").cast_counter == (
        "opponent", "spell", "no_mana_spent", False)
    assert card_db.get_card("Void Mirror").cast_counter == (
        "any", "spell", "no_colored_mana_spent", False)
    assert card_db.get_card("Jin-Gitaxias, Progress Tyrant").cast_counter == (
        "opponent", "artifact_instant_sorcery", None, True)


@pytest.mark.parametrize("name", ["Chancellor of the Annex", "Blood Funnel",
                                  "Dovescape"])
def test_a_counter_with_an_unless_or_a_rider_is_refused(card_db, name):
    assert card_db.get_card(name).cast_counter is None


# ── The AI reads the same predicate ───────────────────────────────────

def test_the_ai_reads_its_own_chalice_as_a_certain_counter(card_db):
    from engine.cast_manager import CastManager
    game = _game()
    chalice = _chalice(game, card_db, 0, 1)
    assert CastManager.lock_that_counters(
        game, 0, card_db.get_card("Lightning Bolt")) is chalice


def test_an_x_spell_is_read_at_the_largest_x_its_caster_can_pay(card_db):
    """Its mana value on the stack is chosen with X as it is cast (CR
    202.3e, 601.2b): before the cast the AI reads it at the largest X the
    caster can pay now -- a certain counter only when that X cannot clear
    the lock."""
    from engine.cast_manager import CastManager
    game = _game()
    chalice = _chalice(game, card_db, 1, 1)
    march = card_db.get_card("March of Otherworldly Light")      # {X}{W}
    _put(game, card_db, "Plains", "battlefield", 0)              # X = 0 only
    assert CastManager.lock_that_counters(game, 0, march) is chalice
    for _ in range(5):
        _put(game, card_db, "Plains", "battlefield", 0)          # X up to 5
    assert CastManager.lock_that_counters(game, 0, march) is None


# ── Mana spent is recorded per cast ───────────────────────────────────

def test_a_cast_records_the_mana_spent_on_it(card_db):
    game = _game()
    _put(game, card_db, "Mountain", "battlefield", 0)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", 0)
    assert game.cast_spell(0, bolt, targets=[PLAYER_TARGET_OPPONENT])
    assert game.stack.top.mana_spent == 1
    free = _put(game, card_db, "Lightning Bolt", "hand", 0)
    assert game.cast_spell(0, free, targets=[PLAYER_TARGET_OPPONENT],
                           free_cast=True)
    assert game.stack.top.mana_spent == 0


# ── Auditor (CR 603.2) ────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_spell_left_on_the_stack_under_its_counter(
        card_db, audit, monkeypatch):
    from engine.cast_manager import CastManager
    monkeypatch.setattr(CastManager, "cast_counters",
                        staticmethod(lambda *a, **k: []))     # the defect
    game = _game()
    _chalice(game, card_db, 0, 1)
    _bolt(game, card_db, 0)
    assert "603.2/cast_counter" in _rules(audit)


def test_the_audit_is_silent_when_the_counter_counters(card_db, audit):
    game = _game()
    _chalice(game, card_db, 0, 1)
    _bolt(game, card_db, 0)
    _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    _bolt(game, card_db, 0)                                # paid: spared
    assert "603.2/cast_counter" not in _rules(audit)
