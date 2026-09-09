"""A spell that a lock permanent on the opponent's battlefield will counter
on cast has resolution probability ZERO — that is a rule, not a belief.

`compute_play_ev`'s single interaction-probability call site read only the
Bayesian hidden-hand model (`bhi.get_interaction_probability`).  Nothing in
`ai/` looked at the opponent's BATTLEFIELD, so a spell whose mana value
matched an opposing Chalice of the Void's charge counters scored byte-
identically to the same spell with no Chalice in play.

Observed 2026-09-08 (Izzet Prowess vs Azorius Control (WST), seed 50000):
Prowess fed five cards into a Chalice on 1 in game 1 and nine casts in game
2 — Bolt, Swiftspear, Dragon's Rage Channeler, Preordain, Lava Dart, all
countered on cast — while holding the one card in hand that dodged it.

Rules:
  1. The engine owns the question "which opposing lock counters this cast"
     (`CastManager.lock_that_counters`, typed `stax_class`, no oracle scan);
     the same primitive that counters the spell in `cast_spell` answers the
     AI, so legality and valuation cannot disagree.
  2. A known lock makes P(countered) = 1.0 at the AI's single call site and
     the cast loses its card for nothing, so a lock-dodging castable spell
     outranks any locked one.

Class: every lock permanent the `stax_class` parser recognises (the Chalice
family today), against every spell in the pool.  Card names below are
fixture carriers for the shapes (a charge-counter lock; a mana-value-1
spell; a mana-value-2 spell).
"""
from __future__ import annotations

import random

from ai.ev_evaluator import compute_play_ev, estimate_pass_ev, snapshot_from_game
from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def _board(card_db, *, chalice_x):
    """Me (0): two Mountains untapped, hand = a mana-value-1 burn spell and
    a mana-value-2 artifact.  Opponent (1): a charge-counter lock at X, or
    none when chalice_x is None."""
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(2):
        _add(game, card_db, "Mountain", 0, "battlefield")
    one_drop = _add(game, card_db, "Lightning Bolt", 0, "hand")
    two_drop = _add(game, card_db, "Cori-Steel Cutter", 0, "hand")
    lock = None
    if chalice_x is not None:
        lock = _add(game, card_db, "Chalice of the Void", 1, "battlefield")
        lock.other_counters["charge"] = chalice_x
    return game, one_drop, two_drop, lock


# ─── Rule 1: the engine names the lock, from the typed field ──────────


def test_engine_names_the_opposing_lock_that_counters_a_matching_mana_value(card_db):
    game, one_drop, two_drop, lock = _board(card_db, chalice_x=1)
    assert CastManager.lock_that_counters(game, 0, one_drop.template) is lock
    assert CastManager.lock_that_counters(game, 0, two_drop.template) is None

    game, one_drop, two_drop, lock = _board(card_db, chalice_x=2)
    assert CastManager.lock_that_counters(game, 0, one_drop.template) is None
    assert CastManager.lock_that_counters(game, 0, two_drop.template) is lock


def test_no_lock_on_the_battlefield_names_nothing(card_db):
    game, one_drop, two_drop, _ = _board(card_db, chalice_x=None)
    assert CastManager.lock_that_counters(game, 0, one_drop.template) is None


def test_the_engine_counters_the_cast_through_the_same_primitive(card_db):
    """`cast_spell` and the AI must read one predicate: a matching cast is
    countered on cast, a non-matching one resolves onto the stack."""
    game, one_drop, two_drop, lock = _board(card_db, chalice_x=1)
    assert CastManager.cast_spell(game, 0, one_drop, [])
    assert one_drop in game.players[0].graveyard, "MV-1 spell countered on cast"
    assert game.stack.is_empty


# ─── Rule 2: the AI treats a known lock as certainty, not belief ──────


def test_spell_whose_mana_value_matches_an_opposing_lock_scores_as_countered(card_db):
    game, one_drop, two_drop, lock = _board(card_db, chalice_x=1)
    snap = snapshot_from_game(game, 0)
    ev, info = compute_play_ev(one_drop, snap, "aggro", game=game, player_idx=0,
                               detailed=True)
    assert info["counter_pct"] == 1.0, (
        f"a Bolt into a Chalice on 1 is countered by rule; counter_pct={info['counter_pct']}")
    assert ev < estimate_pass_ev(snap, "aggro"), (
        f"a cast countered on cast wastes the same mana as passing AND the "
        f"card, so it must score below passing; ev={ev:.3f}")


def test_a_locked_creature_spell_never_resolves_so_it_is_not_scored_as_a_body(card_db):
    """The hidden-hand model for creatures is 'resolves, then maybe gets
    removed'; a battlefield lock counters the cast, so nothing resolves."""
    game, one_drop, two_drop, lock = _board(card_db, chalice_x=1)
    swiftspear = _add(game, card_db, "Monastery Swiftspear", 0, "hand")   # MV 1
    slickshot = _add(game, card_db, "Slickshot Show-Off", 0, "hand")      # MV 2
    snap = snapshot_from_game(game, 0)
    ev_locked, info = compute_play_ev(swiftspear, snap, "aggro", game=game,
                                      player_idx=0, detailed=True)
    ev_free = compute_play_ev(slickshot, snap, "aggro", game=game, player_idx=0)
    assert info["counter_pct"] == 1.0 and info["removal_pct"] == 0.0
    assert ev_locked < estimate_pass_ev(snap, "aggro") < ev_free


def test_lock_dodging_castable_spell_outranks_locked_spells(card_db):
    game, one_drop, two_drop, lock = _board(card_db, chalice_x=1)
    snap = snapshot_from_game(game, 0)
    ev_locked = compute_play_ev(one_drop, snap, "aggro", game=game, player_idx=0)
    ev_free = compute_play_ev(two_drop, snap, "aggro", game=game, player_idx=0)
    assert ev_free > ev_locked, (
        f"the MV-2 card that dodges the lock ({ev_free:.2f}) must outrank the "
        f"MV-1 card the lock counters ({ev_locked:.2f})")


def test_without_a_lock_the_hidden_hand_model_alone_decides(card_db):
    """Guard: no lock on the battlefield → the call site behaves as before
    (no BHI here, so no interaction at all)."""
    game, one_drop, two_drop, _ = _board(card_db, chalice_x=None)
    snap = snapshot_from_game(game, 0)
    ev, info = compute_play_ev(one_drop, snap, "aggro", game=game, player_idx=0,
                               detailed=True)
    assert info["counter_pct"] == 0.0


# ─── Rule 3: a cast fixed by a battlefield lock to be a no-op is never a
# candidate — on any cast path (main phase, response window, instant window)


def test_a_locked_spell_is_not_a_main_phase_candidate(card_db):
    """With only a locked spell castable, the main phase passes rather than
    feeding it to the lock (the play gate is a fixed floor, so a merely
    negative EV would still be executed)."""
    from ai.ev_player import EVPlayer
    game, one_drop, two_drop, lock = _board(card_db, chalice_x=1)
    game.players[0].hand.remove(two_drop)
    ai = EVPlayer(player_idx=0, deck_name="Izzet Prowess", rng=random.Random(0))
    decision = ai.decide_main_phase(game)
    assert decision is None or decision[1] is not one_drop, (
        f"the main phase offered the locked spell: {decision}")


def test_a_locked_instant_is_not_a_response_candidate(card_db):
    """A burn spell the lock would counter is not a response to anything."""
    from ai.response import ResponseDecider
    from ai.turn_planner import TurnPlanner
    from engine.stack import StackItem, StackItemType
    game, one_drop, two_drop, lock = _board(card_db, chalice_x=1)
    game.active_player = 1
    # Opponent casts a creature; our only instant is the locked Bolt.
    game.players[0].hand.remove(two_drop)
    tmpl = card_db.get_card("Grizzly Bears")
    spell = CardInstance(template=tmpl, owner=1, controller=1,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=spell, controller=1, targets=[])
    game.stack.push(item)
    decider = ResponseDecider(0, TurnPlanner(), strategic_logger=None)
    assert one_drop not in decider._enumerate_response_candidates(game, item)
    assert decider.decide_response(game, item) is None


def test_the_instant_window_skips_spells_a_lock_would_counter(card_db):
    """The engine's begin-combat / end-step window casts nothing a lock on
    the active player's battlefield would counter on cast."""
    from ai.ev_player import EVPlayer
    from engine.game_runner import GameRunner
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 1
    _add(game, card_db, "Sojourner's Companion", 1, "battlefield")  # a 4/4 worth removing
    lock = _add(game, card_db, "Chalice of the Void", 1, "battlefield")
    lock.other_counters["charge"] = 1
    _add(game, card_db, "Mountain", 0, "battlefield")
    bolt = _add(game, card_db, "Lightning Bolt", 0, "hand")
    responder = EVPlayer(player_idx=0, deck_name="Izzet Prowess", rng=random.Random(0))
    active = EVPlayer(player_idx=1, deck_name="Azorius Control", rng=random.Random(0))
    GameRunner(card_db=card_db)._cast_instant_removal(
        game, responder, active, context="pre_combat", max_instants=3)
    assert bolt in game.players[0].hand, "the locked Bolt must not be cast into the Chalice"
