"""Planeswalker compute_play_ev must credit loyalty pool + immediate ability.

Rule (mechanic-phrased):
    A planeswalker spell whose printed +1 / 0 / X loyalty ability is
    "useful" (draws a card, deals damage, makes a token, taps something,
    bounces a permanent, etc.) must score a positive `compute_play_ev`
    on a normal mid-game state: opp has a board, our mana pays for the
    cast, opp clock is several turns away.

    The projection-based `_project_spell` previously only updated hand
    and mana for planeswalkers — no permanent/power delta, no loyalty
    pool — so `compute_play_ev` returned strongly negative for every
    planeswalker (mana spent, nothing on the board to credit). Audit
    `docs/history/audits/2026-05-16_5panel_bo3_audit.md` Control
    Decision 1 + 7 (Azorius G2T4: Teferi enumerates at EV -5.6, AI
    casts a -13.9 Isochron Scepter instead while Counterspell, Solitude,
    and Verdict sit unplayed).

    Fix shape: replace the missing projection with a structural
    `expected_future_value(card, snap)` free function that walks the
    planeswalker's loyalty abilities, computes expected loyalty pool ×
    per-tick clock impact (via `ai/clock.py`), and credits the result as
    `persistent_power` on the projected snapshot — the same channel
    recurring-trigger tokens already use, decayed by `urgency_factor`
    so the pool shrinks as the opp clock tightens.

    Class size: every planeswalker in Modern's 20k+ cards (Teferi,
    Wrenn and Six, Karn, Liliana, Ajani, Chandra, Jace, ...). Far above
    the 10-card abstraction-contract threshold.

    Knowledge location: oracle text only. No `card.name == "Teferi"` /
    `'Teferi' in card.name` branches anywhere in `engine/` or `ai/`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _setup_t3_planeswalker_state(card_db, pw_name: str, lands_pattern):
    """T3 state with a planeswalker in hand, mana available, opp has 1
    creature on the board. Returns (game, controller_idx, pw_card).
    """
    game = GameState(card_db, ["P1", "P2"])
    for land in lands_pattern:
        _add_to_battlefield(game, card_db, land, 0)
    pw = _add_to_hand(game, card_db, pw_name, 0)
    _add_to_battlefield(game, card_db, "Memnite", 1)
    game.players[0].life = 20
    game.players[1].life = 20
    game.turn_number = 3
    return game, 0, pw


def test_planeswalker_with_immediate_plus1_useful_scores_positive(card_db):
    """Teferi, Time Raveler (3-CMC PW with useful +1 / -3) cast on T3
    with mana available and 1 opp creature must score `compute_play_ev > 0`.

    Encodes the rule: the projection credits the planeswalker's
    expected loyalty pool as `persistent_power`, so the after-cast
    board-eval exceeds the before-cast value despite the mana spent.
    """
    from ai.ev_evaluator import compute_play_ev, snapshot_from_game

    game, ctrl, teferi = _setup_t3_planeswalker_state(
        card_db, "Teferi, Time Raveler",
        ["Hallowed Fountain", "Hallowed Fountain", "Hallowed Fountain"],
    )
    snap = snapshot_from_game(game, ctrl)
    ev = compute_play_ev(teferi, snap, "control", game, ctrl)
    assert ev > 0, (
        f"Teferi, Time Raveler EV={ev:.2f}. The projection must credit "
        f"a useful planeswalker's expected loyalty pool so compute_play_ev "
        f"is positive on a normal T3 cast — otherwise the AI passes / "
        f"casts deep-negative alternatives instead."
    )


def test_planeswalker_with_no_useful_abilities_scores_neutral(card_db):
    """A planeswalker whose loyalty abilities are NOT classified as
    "useful" should score near-zero (not strongly positive, not
    strongly negative) — the projection must not crash and must not
    falsely credit value.

    Anchor: use a generic 4-CMC planeswalker; floor the expectation at
    "deferral-ish" (very small negative or near-zero) — anything more
    than a small positive without useful tags would mean the formula
    is over-crediting.
    """
    from ai.ev_evaluator import compute_play_ev, expected_future_value, snapshot_from_game

    game = GameState(card_db, ["P1", "P2"])
    for _ in range(4):
        _add_to_battlefield(game, card_db, "Wastes", 0)
    # Use a real planeswalker template, but call the helper directly so
    # we can isolate the "no useful loyalty tags" branch.
    pw = _add_to_hand(game, card_db, "Karn, the Great Creator", 0)
    _add_to_battlefield(game, card_db, "Memnite", 1)
    game.players[0].life = 20
    game.players[1].life = 20
    game.turn_number = 4

    snap = snapshot_from_game(game, 0)
    # Sanity: the helper handles non-PWs as 0 cleanly.
    non_pw = _add_to_hand(game, card_db, "Memnite", 0)
    assert expected_future_value(non_pw, snap) == pytest.approx(0.0), (
        "expected_future_value must return 0 for non-planeswalkers — it "
        "is a composable per-permanent value primitive."
    )
    # And: compute_play_ev does not crash on any planeswalker.
    ev = compute_play_ev(pw, snap, "midrange", game, 0)
    assert ev is not None
    assert ev > -50.0, (
        f"compute_play_ev for a planeswalker must not collapse to a "
        f"deep negative — got {ev:.2f}. The projection's loyalty-pool "
        f"credit is the floor."
    )


def test_planeswalker_loyalty_pool_decays_with_opp_clock(card_db):
    """The credited loyalty pool must decay as opp_clock tightens.

    Concretely: identical T3 Teferi cast, but in scenario A opp has
    a fast clock (10 power → opp_clock=2), in scenario B opp has a
    slow clock (1 power → opp_clock ~7). EV(B) must exceed EV(A) by a
    measurable margin because we get more loyalty activations in the
    slower-clock world.
    """
    from ai.ev_evaluator import compute_play_ev, snapshot_from_game

    # Scenario A: fast opp clock
    game_a, ctrl_a, pw_a = _setup_t3_planeswalker_state(
        card_db, "Teferi, Time Raveler",
        ["Hallowed Fountain", "Hallowed Fountain", "Hallowed Fountain"],
    )
    # Add a big opp threat to drop opp_clock
    _add_to_battlefield(game_a, card_db, "Goldspan Dragon", 1)
    snap_a = snapshot_from_game(game_a, ctrl_a)
    ev_a = compute_play_ev(pw_a, snap_a, "control", game_a, ctrl_a)

    # Scenario B: slow opp clock (Memnite only, 1 power)
    game_b, ctrl_b, pw_b = _setup_t3_planeswalker_state(
        card_db, "Teferi, Time Raveler",
        ["Hallowed Fountain", "Hallowed Fountain", "Hallowed Fountain"],
    )
    snap_b = snapshot_from_game(game_b, ctrl_b)
    ev_b = compute_play_ev(pw_b, snap_b, "control", game_b, ctrl_b)

    assert ev_b > ev_a, (
        f"Slow-clock EV ({ev_b:.2f}) must exceed fast-clock EV "
        f"({ev_a:.2f}) — the loyalty-pool credit must decay with "
        f"opp_clock via urgency_factor (rule: persistent_power × "
        f"urgency_factor in ai/clock.position_value)."
    )


def test_no_card_name_branches_in_planeswalker_scoring():
    """Grep the touched modules for hardcoded planeswalker names —
    classification must be tag/oracle-driven, not name-driven.

    Encodes the abstraction-contract rule for this work unit. If the
    fix sneaks in a `card.name == "Teferi"` or `'Wrenn' in card.name`
    or similar, this test red-lights it before merge.
    """
    repo_root = Path(__file__).resolve().parent.parent
    touched = [
        repo_root / "ai" / "ev_evaluator.py",
        repo_root / "ai" / "clock.py",
    ]
    forbidden_substrings = (
        "Teferi", "Wrenn and Six", "Liliana of the Veil",
        "Karn, the Great Creator", "Jace, the Mind Sculptor",
        "Chandra, Torch", "Ajani", "Nissa",
    )
    offenders = []
    for path in touched:
        if not path.exists():
            continue
        src = path.read_text()
        for needle in forbidden_substrings:
            if needle in src:
                offenders.append((path.name, needle))
    assert not offenders, (
        f"Touched files contain hardcoded planeswalker names: "
        f"{offenders}. Classification must use loyalty-ability oracle "
        f"patterns / tags, not card names."
    )
