"""X-cost board-wipe scoring-gate regression tests.

Diagnostic: docs/diagnostics/2026-05-16_wrath_enumeration_gate.md

Context: `ai/ev_player.py::EVPlayer._score_spell` lines 1163-1190 — the
v3 X-cost board-wipe gate. The gate computes a killable set from
`opp.creatures` filtered by `c.template.cmc <= effective_x`. If
`kill_count == 0` (and we aren't "desperate" via `my_life <= 10`), it
returns `min(ev, X_BOARD_WIPE_WASTE_FLOOR)` = -20. The gate has two
mechanical blind spots:

  1. **Creatures-only killable set.** Wrath of the Skies' oracle text
     destroys "each artifact, creature, AND enchantment with mana value
     ≤ X". The gate counts only `opp.creatures`, so a wipe that clears
     Springleaf Drum + Mox Opal + Cranial Plating (the artifact mana
     base + threat multiplier) reads as `kill_count == 0` and floors at
     -20 EV. Affinity is the canonical case; any wide-board artifact
     deck is exposed the same way.

  2. **`my_life <= 10` desperation lever.** A confirmed lethal-next-turn
     opp board at `my_life = 23` (e.g., Affinity with double-Plating'd
     attacker, opp_clock_discrete == 2) still trips `not desperate` and
     the gate fires. The desperation key should be a clock-derived
     query — `snap.opp_clock_discrete <= 2` — not a life threshold.

The rule each test names is mechanical, not card-specific.

Rule-phrased tests:

* `test_x_cost_wipe_kill_count_includes_artifacts_and_enchantments_not_only_creatures`
* `test_x_cost_wipe_desperation_waiver_fires_at_lethal_next_turn_above_10_life`
* `test_x_cost_wipe_negative_ev_floor_still_applies_when_kill_count_zero_and_not_desperate`
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from ai.scoring_constants import X_BOARD_WIPE_WASTE_FLOOR
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _battlefield(game, card_db, name, controller):
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


def _hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _score(game, card, deck_name="Boros Energy"):
    """Score `card` from player 0's perspective."""
    game.players[0].deck_name = deck_name
    game.players[1].deck_name = game.players[1].deck_name or "Affinity"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = max(game.turn_number, 4)
    player = EVPlayer(player_idx=0, deck_name=deck_name,
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]
    return snap, player._score_spell(card, snap, game, me, opp)


class TestXCostBoardWipeGate:
    """Mechanical regression tests for the X-cost board-wipe gate."""

    def test_x_cost_wipe_kill_count_includes_artifacts_and_enchantments_not_only_creatures(
            self, card_db):
        """An X-wipe whose oracle destroys creatures + artifacts +
        enchantments at MV ≤ X must count artifact + enchantment kills,
        not just creature kills.

        Affinity-style board: artifact mana base (Mox Opal CMC 0,
        Springleaf Drum CMC 1, Cranial Plating CMC 2) plus low-CMC
        artifact creatures (Memnite/Ornithopter CMC 0). The defender
        has 4 lands → effective X = 2. The wipe clears EVERY listed
        permanent (all MV ≤ 2). The old gate sees only `opp.creatures`
        (Memnite + Ornithopter) and falls back to a 2-creature kill —
        but a Plating-equipped Memnite case has only 1 token-class
        creature on board, which is exactly where the gate
        `kill_count == 0`/`kill_count == 1 and killable_power < 2`
        branches fire. The fix must count artifact + enchantment kills
        so the gate does not floor at -20.
        """
        game = GameState(rng=random.Random(0))
        # Defender at 23 life — NOT in the my_life<=10 desperation
        # branch; tests the kill-count-classification fix in isolation.
        game.players[0].life = 23
        # 4 white-producing lands for the defender — gives X=2 budget
        # for Wrath of the Skies (base {W}{W} = 2; total mana 4).
        for _ in range(4):
            _battlefield(game, card_db, "Plains", controller=0)
        wrath = _hand(game, card_db, "Wrath of the Skies", controller=0)
        # Opp board: ONE 1-power creature (so kill_count_creatures==1
        # AND killable_power<2 → old gate fires) + multiple non-creature
        # artifacts that the wipe DOES destroy at X=2.
        _battlefield(game, card_db, "Memnite", controller=1)         # cmc 0, 1/1
        _battlefield(game, card_db, "Mox Opal", controller=1)        # cmc 0
        _battlefield(game, card_db, "Springleaf Drum", controller=1) # cmc 1
        _battlefield(game, card_db, "Cranial Plating", controller=1) # cmc 2

        snap, ev = _score(game, wrath)

        # The wipe at X=2 destroys 4 permanents (1 creature + 3
        # non-creature artifacts), every one of which is a real loss
        # for the opponent. The gate must NOT floor at -20 here.
        assert ev > X_BOARD_WIPE_WASTE_FLOOR, (
            f"X-cost wipe scored {ev:.2f} (≤ floor {X_BOARD_WIPE_WASTE_FLOOR:.2f}). "
            f"Gate counted only opp.creatures and missed 3 non-creature "
            f"artifact kills (Mox Opal, Springleaf Drum, Cranial Plating). "
            f"Fix: lift killable set from opp.creatures to opp.battlefield "
            f"filtered by the oracle-derived target classes "
            f"({{creature, artifact, enchantment}} for Wrath-style wipes)."
        )

    def test_x_cost_wipe_desperation_waiver_fires_at_lethal_next_turn_above_10_life(
            self, card_db):
        """The desperation waiver must key off clock, not life. When
        `opp_clock_discrete <= 2` (lethal-this-or-next-turn), the gate
        must NOT floor an X-wipe at -20 even when `my_life > 10`.

        Setup: my_life = 15 (above the old 10-life threshold), opp has
        a high-power attacker — opp_clock_discrete <= 2. The wipe
        clears a single 1-power Memnite (kill_count == 1,
        killable_power == 1 < 2 → old gate fires its second branch).
        Under the life-threshold lever, gate keeps the wipe from
        firing because life > 10. Under the clock-derived lever, the
        gate yields the wipe (we're about to die — chump-removing one
        attacker is worth more than the waste floor).
        """
        game = GameState(rng=random.Random(0))
        # Above the old 10-life desperation cutoff but lethal-next-turn.
        game.players[0].life = 15
        # 4 white-producing lands for the defender — X budget = 2.
        for _ in range(4):
            _battlefield(game, card_db, "Plains", controller=0)
        wrath = _hand(game, card_db, "Wrath of the Skies", controller=0)
        # Opp board: one weak 1-power creature (kill_count=1 at X≥0,
        # killable_power=1<2 → trips the second old-gate branch) PLUS
        # one big out-of-CMC-budget attacker pushing the clock.
        _battlefield(game, card_db, "Memnite", controller=1)  # 1/1, CMC 0
        big = _battlefield(game, card_db, "Sojourner's Companion",
                            controller=1)
        # Push power so opp_clock_discrete <= 2 regardless of base P/T.
        # Use temp_power_mod (power is computed from base + counters +
        # temp_*_mod).
        big.temp_power_mod = 15  # ≥ my_life → opp_clock_discrete == 1

        snap, ev = _score(game, wrath)

        # Sanity: snapshot confirms lethal-next-turn position.
        assert snap.opp_clock_discrete <= 2, (
            f"Test setup invariant: opp_clock_discrete must be <= 2 "
            f"(got {snap.opp_clock_discrete}). Adjust the fixture so the "
            f"opponent's combat clock is lethal-next-turn at minimum."
        )
        assert snap.my_life > 10, (
            f"Test setup invariant: my_life must be > 10 (got "
            f"{snap.my_life}) so the old `my_life <= 10` lever does "
            f"NOT fire. This isolates the clock-derived waiver."
        )

        assert ev > X_BOARD_WIPE_WASTE_FLOOR, (
            f"X-cost wipe scored {ev:.2f} (≤ floor {X_BOARD_WIPE_WASTE_FLOOR:.2f}). "
            f"Gate fired the waste floor at my_life=15 with "
            f"opp_clock_discrete<=2 (lethal-next-turn). The desperation "
            f"lever must be clock-derived, not a life threshold — "
            f"replace `my_life <= DESPERATE_LIFE_THRESHOLD` with "
            f"`snap.opp_clock_discrete <= 2`."
        )

    def test_x_cost_wipe_negative_ev_floor_still_applies_when_kill_count_zero_and_not_desperate(
            self, card_db):
        """Regression anchor: the gate must keep flooring at -20 when
        the wipe truly accomplishes nothing AND we are not desperate.
        Mid-game, opponent has only an out-of-X-budget threat, my_life
        is comfortable, opp_clock is comfortable — the wipe is pure
        waste and the gate must hold the line. The fix from tests 1+2
        must not over-correct into "always fire the wipe".
        """
        game = GameState(rng=random.Random(0))
        # Comfortable life — well above any desperation threshold.
        game.players[0].life = 20
        # Only 3 lands → X budget = 1 after {W}{W} base.
        for _ in range(3):
            _battlefield(game, card_db, "Plains", controller=0)
        wrath = _hand(game, card_db, "Wrath of the Skies", controller=0)
        # Opp board: a single CMC-4 creature (out of X=1 reach), and
        # NO non-creature permanents of MV ≤ 1. Wipe destroys nothing.
        big = _battlefield(game, card_db, "Frogmite", controller=1)
        # Opp power stays low so we are NOT in clock-derived desperation
        # (opp_clock_discrete must be > 2). Frogmite is 2/2 → 20/2 = 10
        # turns, well above the 2-turn threshold.

        snap, ev = _score(game, wrath)

        # Sanity invariants.
        assert snap.my_life > 10
        assert snap.opp_clock_discrete > 2, (
            f"Test setup invariant: opp_clock_discrete must be > 2 "
            f"(got {snap.opp_clock_discrete}) so neither lever fires. "
            f"Adjust opp.power to lower the clock pressure."
        )

        assert ev <= X_BOARD_WIPE_WASTE_FLOOR, (
            f"Regression: X-cost wipe scored {ev:.2f} (> floor "
            f"{X_BOARD_WIPE_WASTE_FLOOR:.2f}) in a true-waste scenario "
            f"(0 kills, comfortable life, comfortable clock). The gate "
            f"must still floor under these conditions — the fix for "
            f"the other two tests must NOT remove the floor entirely."
        )
