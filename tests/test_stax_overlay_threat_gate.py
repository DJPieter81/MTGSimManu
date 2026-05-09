"""P1-1 — wire ai/stax_ev.py into _score_spell behind a holdback gate.

Rule (per docs/proposals/2026-05-03_p0_p1_backlog.md):
    Stax pieces (Chalice, Blood Moon, Canonist, Torpor Orb) get a positive
    EV overlay from `stax_lock_ev`. The overlay is GATED so that it only
    applies when tapping out for the stax piece would NOT forfeit a held
    instant-speed response — i.e. when `_holdback_penalty` returns 0.
    Otherwise the AI tap-outs for Chalice on T2 and loses the held
    Counterspell to the opponent's incoming threat (the WST regression
    that caused the previous wiring to be reverted).

Failing-first tests (Option C):
1. `test_stax_overlay_added_when_holdback_zero` — when no held response
   is present, the score of a stax permanent rises by `stax_lock_ev` vs
   the same scenario with the overlay disabled.
2. `test_stax_overlay_silenced_when_holdback_fires` — when the holdback
   penalty is non-zero (a held response would be sacrificed), the
   overlay must NOT be applied; the open-vs-closed-gate score delta
   exceeds the holdback magnitude by at least the stax bonus.

Both tests isolate the wiring via monkeypatch so the assertions are
independent of `compute_play_ev`'s base scoring profile. No card-name
conditionals in the test logic; cards are picked because they exemplify
the rule (Chalice = stax permanent with Memnite-rich opponent → positive
`stax_lock_ev`).
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone, summoning_sick=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = summoning_sick
    if zone == "library":
        game.players[controller].library.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


def _build_t2_scenario(card_db):
    """Player 0 (control) on T2 with 2 Islands, Chalice in hand.
    Player 1 (aggro) deploying one-drops with library full of one-drops
    so Chalice@X=1 has a non-trivial lock target — `stax_lock_ev` is
    positive in this snapshot."""
    game = GameState(rng=random.Random(0))
    for _ in range(2):
        _add(game, card_db, "Island", controller=0, zone="battlefield")
    chalice = _add(game, card_db, "Chalice of the Void",
                   controller=0, zone="hand")

    _add(game, card_db, "Memnite", controller=1, zone="battlefield")
    for _ in range(4):
        _add(game, card_db, "Memnite", controller=1, zone="hand")
    for _ in range(20):
        _add(game, card_db, "Memnite", controller=1, zone="library")

    game.players[0].deck_name = "Azorius Control"
    game.players[1].deck_name = "Affinity"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 2
    return game, chalice


def _scenario_pieces(card_db):
    game, chalice = _build_t2_scenario(card_db)
    player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me, opp = game.players[0], game.players[1]
    return game, chalice, player, snap, me, opp


def test_stax_overlay_added_when_holdback_zero(card_db, monkeypatch):
    """When the holdback penalty is 0 (no held instant-speed response
    would be lost), `_score_spell` MUST add the stax overlay. Verify
    by comparing against the same scenario with `stax_lock_ev`
    monkeypatched to 0 — the score delta must equal the real bonus.

    Pre-wiring: `_score_spell` never calls `stax_lock_ev`, so the
    delta is 0 and this test fails.
    """
    from ai import stax_ev as stax_mod
    real_stax_lock_ev = stax_mod.stax_lock_ev

    game, chalice, player, snap, me, opp = _scenario_pieces(card_db)
    expected_bonus = real_stax_lock_ev(chalice.template, me, opp, snap)
    assert expected_bonus > 0.0, (
        "test setup must produce a positive stax bonus for the rule "
        "to be discriminable"
    )

    # Score the spell normally (overlay path active, if wired).
    score_with_overlay = player._score_spell(chalice, snap, game, me, opp)

    # Now disable the overlay and re-score in the SAME scenario.
    monkeypatch.setattr(stax_mod, "stax_lock_ev", lambda *a, **kw: 0.0)
    # If `ai.ev_player` does `from ai.stax_ev import stax_lock_ev`,
    # patch the rebound name there too.
    import ai.ev_player as evp_mod
    if hasattr(evp_mod, "stax_lock_ev"):
        monkeypatch.setattr(evp_mod, "stax_lock_ev",
                            lambda *a, **kw: 0.0)
    score_without_overlay = player._score_spell(chalice, snap, game, me, opp)

    delta = score_with_overlay - score_without_overlay
    assert delta == pytest.approx(expected_bonus, abs=0.05), (
        f"With the holdback gate open, _score_spell must add the stax "
        f"bonus to the score. Expected delta ≈ {expected_bonus:.3f}, "
        f"got {delta:.3f}."
    )


def test_stax_overlay_silenced_when_holdback_fires(card_db, monkeypatch):
    """When `_holdback_penalty` returns a non-zero (negative) value —
    i.e. a held instant-speed response would be sacrificed — the stax
    overlay must NOT be applied. Verify by forcing holdback to a fixed
    -1.0 and comparing against the natural (open-gate) score:

        delta = score_open - score_closed
              = (base + stax_bonus + 0) - (base + 0 + (-1.0))
              = stax_bonus + 1.0

    Pre-wiring: `score_open` and `score_closed` differ only by the
    forced holdback (no stax bonus on either side), so delta = 1.0
    and this test fails.
    """
    from ai import stax_ev as stax_mod
    real_stax_lock_ev = stax_mod.stax_lock_ev

    game, chalice, player, snap, me, opp = _scenario_pieces(card_db)
    expected_bonus = real_stax_lock_ev(chalice.template, me, opp, snap)
    assert expected_bonus > 0.5, (
        "test setup needs a stax bonus comfortably above the forced "
        "holdback magnitude (-1.0) to give a clean assertion margin"
    )

    # Score with natural (open) holdback — should include stax overlay.
    score_open = player._score_spell(chalice, snap, game, me, opp)

    # Force `_holdback_penalty` to fire on the next call.
    monkeypatch.setattr(EVPlayer, "_holdback_penalty",
                        lambda self, *a, **kw: -1.0)
    score_closed = player._score_spell(chalice, snap, game, me, opp)

    delta = score_open - score_closed
    # Expected post-wiring: delta ≈ stax_bonus + 1.0.
    # Pre-wiring (no overlay): delta = 1.0 → fails the bound below.
    assert delta > 1.0 + 0.5 * expected_bonus, (
        f"With holdback firing, stax overlay must be silenced. "
        f"Open-gate − closed-gate delta should be ≈ "
        f"stax_bonus + holdback_magnitude = {expected_bonus + 1.0:.3f}. "
        f"Pre-wiring delta is 1.0 (forced holdback only) and fails. "
        f"Got delta = {delta:.3f}."
    )
