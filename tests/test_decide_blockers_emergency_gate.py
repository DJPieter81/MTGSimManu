"""Phase 1 — RC-1 + RC-3 of the block strategy audit.

RC-1: emergency gate no longer fires for single-swing non-lethal damage
      (e.g. a 10/10 at life=20 is not an emergency — 20→10 leaves us a turn
      to respond).
RC-3: the emergency blocker loop caps cumulative sacrificed creature_value
      at the unblocked damage we'd take; we stop over-committing chumps
      long before arithmetic stabilizes.

Reference: audits/BLOCK_STRATEGY_AUDIT.md §Phase 1.
"""
from __future__ import annotations

import random
import pytest

from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


class TestNoEmergencyWhenSingleSwingNonlethal:
    """RC-1 contract: the emergency gate must not fire for a single non-lethal
    swing at healthy life. Non-emergency path may still find a block via
    evaluate_action — that path is governed by other phases and is out of
    scope here. The assertion is specifically on the emergency-path log
    marker and on _two_turn_lethal."""

    def test_4_4_at_life_20_emergency_gate_does_not_fire(self, card_db):
        """Life=20, Sojourner's Companion (4/4) attacks alone. No other opp
        creatures. _two_turn_lethal must be False; no BLOCK-EMERGENCY log
        entry."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 20
        game.players[1].life = 20

        _add_to_battlefield(game, card_db, "Guide of Souls", controller=0)
        attacker = _add_to_battlefield(
            game, card_db, "Sojourner's Companion", controller=1
        )

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        me = game.players[0]
        opp = game.players[1]

        assert not player._two_turn_lethal(game, me, opp, [attacker]), (
            "_two_turn_lethal must return False: 4 incoming + 0 followup "
            "< life=20. Old RC-1 heuristic would have fired emergency."
        )

        pre_log_len = len(game.log)
        player.decide_blockers(game, [attacker])
        new_entries = game.log[pre_log_len:]
        emergency_entries = [
            line for line in new_entries if "BLOCK-EMERGENCY" in line
        ]
        assert not emergency_entries, (
            f"emergency path must not fire at life=20 vs single 4/4; got "
            f"{emergency_entries}"
        )


class TestNoEmergencyAtLife20VsSingle10Power:
    """RC-1 regression: old code fired emergency whenever
    biggest_attacker_power >= life // 2 (so a 10-power attacker at life=20
    triggered emergency). New _two_turn_lethal replaces that with a
    projection. With no followup, emergency must not fire."""

    def test_10_power_alone_emergency_gate_does_not_fire(self, card_db):
        game = GameState(rng=random.Random(0))
        game.players[0].life = 20
        game.players[1].life = 20

        _add_to_battlefield(game, card_db, "Guide of Souls", controller=0)
        attacker = _add_to_battlefield(
            game, card_db, "Sojourner's Companion", controller=1
        )
        # Bump to 10-power via temp_power_mod (existing field on CardInstance)
        attacker.temp_power_mod = 6
        attacker.temp_toughness_mod = 6
        assert attacker.power == 10, (
            f"test setup: attacker power={attacker.power}, expected 10"
        )

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        me = game.players[0]
        opp = game.players[1]

        assert not player._two_turn_lethal(game, me, opp, [attacker]), (
            "_two_turn_lethal: 10 incoming + 0 followup < life=20"
        )

        pre_log_len = len(game.log)
        player.decide_blockers(game, [attacker])
        new_entries = game.log[pre_log_len:]
        emergency_entries = [
            line for line in new_entries if "BLOCK-EMERGENCY" in line
        ]
        assert not emergency_entries, (
            f"old code fired emergency via biggest_attacker >= life//2; "
            f"new code must not. Got emergency entries: {emergency_entries}"
        )


class TestTwoTurnLethalStillTriggersEmergency:
    def test_low_life_with_followup_threat_blocks(self, card_db):
        """Life=5, a 4/4 attacking + another 4/4 opp creature ready to swing
        next turn. _two_turn_lethal: 4 + 4 = 8 >= 5 → emergency fires."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 5
        game.players[1].life = 20

        blocker = _add_to_battlefield(game, card_db, "Guide of Souls",
                                       controller=0)
        attacker = _add_to_battlefield(
            game, card_db, "Sojourner's Companion", controller=1
        )
        # Second 4/4 opp creature: not attacking, untapped, not summoning sick.
        followup = _add_to_battlefield(
            game, card_db, "Sojourner's Companion", controller=1
        )
        followup.summoning_sick = False

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        blocks = player.decide_blockers(game, [attacker])

        assert attacker.instance_id in blocks, (
            f"expected emergency block at life=5 with a follow-up 4/4 "
            f"threat (2-turn lethal). Got {blocks}."
        )
        assert blocker.instance_id in blocks[attacker.instance_id], (
            f"expected Guide of Souls to chump the 4/4, got {blocks}."
        )


class TestPortfolioCapStopsOverCommit:
    def test_four_3_3_attackers_vs_five_cat_tokens_caps_blockers(self, card_db):
        """Life=14, 4 attackers each 3/3 (12 total incoming). We have 5 small
        Ajani's Pridemate-ish creatures. Taking 12 drops life to 2 — which
        IS emergency via the drop-below-5 clause. So the interesting cap
        behaviour is: even though emergency fires, sacrificed_value cap
        must stop us before we blow our whole board.

        Expectation: fewer than 4 blockers committed (we shouldn't blank
        all 4 attackers if sacrificed_value already exceeds damage)."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 14
        game.players[1].life = 20

        # Me: 5 small creatures — use repeated Memnite (1/1 no abilities) as
        # low-value chump fodder. Memnite is in ModernAtomic.
        for _ in range(5):
            _add_to_battlefield(game, card_db, "Memnite", controller=0)

        attackers = []
        for _ in range(4):
            # Sojourner's Companion default is 4/4 — we want 3/3. Use a real
            # 3/3 instead: Kraken Hatchling isn't 3/3 either. Use Watchwolf.
            atk = _add_to_battlefield(game, card_db, "Watchwolf", controller=1)
            attackers.append(atk)

        # Sanity: total_incoming = 12, life=14 → drops to 2 (emergency fires).
        incoming = sum(a.power or 0 for a in attackers)
        assert incoming == 12, f"test setup: incoming={incoming}, expected 12"

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        blocks = player.decide_blockers(game, attackers)

        # Portfolio cap: sacrificing 4 creatures worth ~3 creature_value each
        # to save 12 damage is breakeven at best; but the point is that
        # sacrificed_value (~12+) > remaining after a few blocks. Cap must
        # stop us before we commit all 5 tokens. Expect ≤ 4 blockers total.
        # In a strict reading of "cap stops before arithmetic stabilizes",
        # we expect at most 3. Assert the upper bound loosely: < 4.
        total_blockers = sum(len(v) for v in blocks.values())
        assert total_blockers < 4, (
            f"portfolio cap should keep emergency-block count under 4. "
            f"Got {total_blockers} blockers across {len(blocks)} attackers: "
            f"{blocks}."
        )
