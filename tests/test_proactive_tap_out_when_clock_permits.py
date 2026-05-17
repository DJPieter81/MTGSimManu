"""M3 — proactive tap-out when the clock permits (5-panel audit, control
panel + combo cross-pattern #2).

Diagnosis (`docs/history/audits/2026-05-16_5panel_bo3_audit.md`):
`ai/ev_player.py:620` uses a binary `pass_threshold` gate — if the best
candidate's EV is below `profile.pass_threshold` the AI passes.  In the
trace evidence Azorius repeatedly enumerated a single low-EV play at
≈ -13.9 EV for 4 consecutive turns while the opponent (Storm) assembled
2× Medallions + Wish.  The opp board was empty, opp life 18, opp had no
deployed pressure — there was literally nothing to hold the mana for —
yet the binary gate kept Azorius passing because the EV was below
threshold.

Fix (structural): delete the `pass_threshold` constant + binary gate.
Replace with `play_value = ev - holdback_cost(snap)` where
`holdback_cost` is *signed*:

- POSITIVE (=> EV penalty) when the open mana has a defensive use
  (held counter/removal, opp can deploy a counterable spell next turn).
  This is the existing `_holdback_penalty` behaviour, unchanged.

- NEGATIVE (=> EV bonus) when no defensive use exists (opp tapped out,
  no held interaction, no follow-up threat coming).  Holding mana for
  *nothing* is strictly worse than spending it on a marginally-negative
  play that still develops the board — the AI must proactively tap out.

The test mechanism is named at the rule level — "tap-out when clock
permits" — not the card.  The same fix lifts every control deck that
hits the same binary-gate trap (Azorius, 4/5c, Jeskai).

Three tests, mapped 1:1 to the M3 brief:

1. `test_planeswalker_cast_at_negative_ev_when_opp_clock_permits` —
   the positive case.  Opp empty + tapped out + no held interaction →
   `_holdback_penalty` returns a bonus.  A spell whose direct EV is
   modestly negative (planeswalker projection without immediate value)
   passes the gate because `play_value = ev + bonus > 0`.

2. `test_pass_holds_when_counter_in_hand_and_opp_can_cast` — the
   regression-safety case.  Same setup BUT held Counterspell + opp has
   spells in hand → `_holdback_penalty` returns a negative number (the
   existing penalty path).  Play value drops; AI passes correctly.

3. `test_pass_threshold_constant_deleted` — structural assertion that
   the `pass_threshold` constant is gone from `ai.scoring_constants`
   AND the field is gone from `ai.strategy_profile.StrategyProfile`.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from ai.strategy_profile import StrategyProfile
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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


class TestHoldbackCostIsSigned:
    """`_holdback_penalty` must return a SIGNED value:

    - <0 (penalty) when defensive use exists (existing behaviour)
    - >0 (bonus)  when no defensive use exists  (new in M3)

    These two tests exercise both branches *directly* — by calling
    `_holdback_penalty` and reading its return value — so the property
    is pinned at the holdback subsystem boundary, not at the
    `_score_spell` aggregation level (which can mask sign changes
    behind other overlays).
    """

    def test_holdback_returns_bonus_when_no_defensive_use(self, card_db):
        """Azorius at T3.  Hand has Teferi (planeswalker, no defensive
        instants).  Opp board empty, opp life 18, opp hand 0 (tapped
        out, no follow-up threats).

        There is literally no defensive use for the open mana — no
        held counter, no opp threat to interact with.  `_holdback_penalty`
        must return a *positive* (=> bonus) value so a marginally
        negative-EV development play (Teferi at its projection floor)
        clears the gate.

        Pre-M3: the function returns 0.0 in this branch — AI passes
        because the binary `pass_threshold` gate fires regardless.
        Post-M3: the function returns a positive bonus derived from
        existing primitives (no new magic constants)."""
        game = GameState(rng=random.Random(0))

        # Azorius — 3 Plains + 1 Island, enough to cast 3-CMC Teferi.
        for _ in range(3):
            _add(game, card_db, "Plains", controller=0, zone="battlefield")
        _add(game, card_db, "Island", controller=0, zone="battlefield")

        # Teferi, Time Raveler (1WU planeswalker) — 4-of in real Azorius lists.
        # No defensive instants in hand.
        teferi = _add(game, card_db, "Teferi, Time Raveler", controller=0,
                      zone="hand")

        # Opp empty board, empty hand — explicitly no threats and no
        # mana to follow up.
        game.players[1].life = 18
        game.players[0].life = 20

        game.players[0].deck_name = "Azorius Control"
        game.players[1].deck_name = "Ruby Storm"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 3

        player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me, opp = game.players[0], game.players[1]

        holdback = player._holdback_penalty(
            me, opp, snap, cost=teferi.template.cmc or 3,
            exclude_instance_id=teferi.instance_id,
        )

        # Pin the SIGN, not the magnitude.  Pre-M3 returns 0.0 (binary
        # gate handles passing); post-M3 returns a positive bonus.
        # Rule encoded: when there is no defensive use, holding mana is
        # strictly worse than spending it — bonus must be positive.
        assert holdback > 0.0, (
            f"Holdback returned {holdback:.2f}; M3 requires a POSITIVE "
            f"bonus when (a) no held instant-speed interaction, (b) opp "
            f"hand empty / no threats, (c) opp tapped out — there is "
            f"nothing to hold mana for, so tapping out for a marginal "
            f"play is strictly better than passing.  The pass_threshold "
            f"binary gate must NOT be the mechanism that handles this."
        )

    def test_pass_holds_when_counter_in_hand_and_opp_can_cast(self, card_db):
        """Regression-safety mirror — same Teferi setup BUT now we hold
        a Counterspell AND the opponent has cards in hand they could
        deploy as a real threat.

        The existing `_holdback_penalty` (positive branch) must still
        fire: defensive use exists, tap-out forfeits response capacity,
        bonus must be negative."""
        game = GameState(rng=random.Random(0))

        # Same Azorius mana base as the bonus test.
        for _ in range(3):
            _add(game, card_db, "Plains", controller=0, zone="battlefield")
        _add(game, card_db, "Island", controller=0, zone="battlefield")

        # Held interaction — a CMC-2 Counterspell.  This is the
        # defensive use that must gate the tap-out.
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

        teferi = _add(game, card_db, "Teferi, Time Raveler", controller=0,
                      zone="hand")

        # Opp has board + hand → the held Counterspell has live targets.
        _add(game, card_db, "Memnite", controller=1, zone="battlefield")
        _add(game, card_db, "Ornithopter", controller=1, zone="battlefield")
        for _ in range(3):
            _add(game, card_db, "Memnite", controller=1, zone="hand")

        game.players[0].deck_name = "Azorius Control"
        game.players[1].deck_name = "Affinity"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 3

        player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me, opp = game.players[0], game.players[1]

        holdback = player._holdback_penalty(
            me, opp, snap, cost=teferi.template.cmc or 3,
            exclude_instance_id=teferi.instance_id,
        )

        # The existing penalty path must remain negative — defensive
        # use is live, tap-out forfeits capacity.  Magnitude is pinned
        # by `test_holdback_coefficient_calibration`; here we only
        # assert the SIGN to confirm the negative branch is intact.
        assert holdback < 0.0, (
            f"Holdback returned {holdback:.2f}; with held Counterspell "
            f"and an active creature opponent the existing penalty must "
            f"still fire (negative).  M3 must NOT regress the defensive "
            f"branch when extending the signed-cost model."
        )


class TestPassThresholdConstantDeleted:
    """Structural-only assertion.  The `pass_threshold` constant must
    be physically absent from both the scoring-constants module and
    the StrategyProfile dataclass.  This is the M3 net-deletion
    guard — if a future refactor re-introduces the constant, this
    test fails before the binary gate creeps back into use."""

    def test_pass_threshold_not_in_scoring_constants(self):
        """`from ai.scoring_constants import PASS_THRESHOLD` must
        raise ImportError.  M3 deletes the constant outright; any
        reference is a regression."""
        with pytest.raises(ImportError):
            from ai.scoring_constants import PASS_THRESHOLD  # noqa: F401

    def test_pass_threshold_not_in_strategy_profile(self):
        """`StrategyProfile().pass_threshold` must raise AttributeError —
        the dataclass field is deleted.  Per-archetype EV gating now
        derives from signed `holdback_cost`, not a per-profile cutoff."""
        profile = StrategyProfile()
        with pytest.raises(AttributeError):
            _ = profile.pass_threshold
