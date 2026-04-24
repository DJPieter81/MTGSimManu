"""P0-Ruby-Storm — mid-chain ritual without finisher access must be held.

Diagnostic: docs/diagnostics/2026-04-21_ruby_storm_underperformance.md

Observed in `replays/ruby_storm_vs_boros_energy_s60130.txt` Game 1
T4: Storm had two Ruby Medallions + Past in Flames resolved, cast a
6-ritual chain floating ~12 mana, and PASSED TURN without a
finisher. Grapeshot was never drawn, Wish wasn't in hand, and PiF's
flashback had nothing in graveyard to revive. The entire ritual
chain was flushed at phase end (CR 500.4) — wasted.

Diagnosis: `ai/ev_player.py:_combo_modifier` has a mid-chain gate
at `storm >= 1 and 'ritual' in tags` that penalises rituals when
`_has_finisher()` returns False and `_has_flashback_combo()` returns
False. But the penalty `(storm + 2) / opp_life * 5.0` is far too
small: at storm=1 opp_life=20, penalty is 0.75 — easily dwarfed by
the ritual's base EV (~5-10 from combo_continuation credit). The AI
keeps casting rituals.

Fix: when no finisher path is accessible mid-chain AND we're not in
am_dead_next territory, clamp the ritual's score below
pass_threshold (like the Scapeshift sub-4-lands pattern at
ai/ev_player.py:478). This converts the soft deterrent into a hard
hold.

Regression anchor: a ritual cast with finisher IN HAND (Grapeshot /
Wish) must still score positively — the fix targets only the
no-path case.
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
    getattr(game.players[controller], 'library' if zone == 'library'
            else zone).append(card)
    return card


class TestStormRitualHeldWithoutFinisher:
    """A mid-chain ritual must be held when no finisher / PiF-flashback
    path exists to kill this turn."""

    def test_desperate_ritual_below_threshold_no_finisher(self, card_db):
        """s60130 T4 reproduction (late-chain state): storm=4 (several
        rituals + draws already cast), hand has Desperate Ritual + a
        second Ritual but NO Grapeshot, NO Wish, NO PiF, NO remaining
        draws. The draws have been exhausted already this turn and
        didn't find a finisher. Casting another ritual just burns a
        card — mana empties at phase end (CR 500.4). EV must be
        below STORM pass_threshold so the AI holds and passes."""
        game = GameState(rng=random.Random(0))
        # Storm side: 2 Mountains untapped + 2 Medallions on battlefield.
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
        _add(game, card_db, "Ruby Medallion", controller=0,
             zone="battlefield")
        _add(game, card_db, "Ruby Medallion", controller=0,
             zone="battlefield")

        # Hand: two rituals only — no finisher, no PiF, no draws left.
        # The AI has already cast its draw spells this turn and didn't
        # hit Grapeshot.  Continuing the chain contributes only mana
        # that will empty at phase end.
        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")

        # Graveyard empty of flashback-eligible combo pieces — PiF
        # wouldn't find any Grapeshots to revive even if it were
        # resolved.  (No PiF in hand either, but this makes the point
        # that even with PiF the flashback path wouldn't kill.)

        # Opp board: Boros curve-out.  opp_life=20, so no lethal.
        _add(game, card_db, "Guide of Souls", controller=1,
             zone="battlefield")
        _add(game, card_db, "Voice of Victory", controller=1,
             zone="battlefield")

        game.players[0].deck_name = "Ruby Storm"
        game.players[1].deck_name = "Boros Energy"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 4
        game.players[0].lands_played_this_turn = 1
        # Four spells already cast this turn (2 rituals + 2 draws) —
        # late-chain state per the s60130 T4 replay.
        game.players[0].spells_cast_this_turn = 4
        game._global_storm_count = 4
        game.players[0].life = 11  # mid-game pressure
        game.players[1].life = 20

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(ritual, snap, game, me, opp)

        STORM_PASS_THRESHOLD = -5.0
        assert ev < STORM_PASS_THRESHOLD, (
            f"Desperate Ritual with no finisher-in-hand and no live "
            f"PiF-flashback target scored EV={ev:.2f}, above the STORM "
            f"pass_threshold ({STORM_PASS_THRESHOLD}).  The AI will "
            f"cast it anyway and continue building a chain it can't "
            f"close this turn.  Observed in "
            f"replays/ruby_storm_vs_boros_energy_s60130.txt T4: 6 "
            f"rituals burned for 0 damage.\n"
            f"Fix: in ai/ev_player.py _combo_modifier, when storm >= 1 "
            f"and 'ritual' in tags and neither _has_finisher() nor "
            f"_has_flashback_combo() AND snap.am_dead_next is False "
            f"AND opp_clock_discrete > 2, clamp the score below "
            f"pass_threshold (mirror the Scapeshift sub-4-lands gate "
            f"pattern at ai/ev_player.py:478)."
        )

    def test_ritual_with_grapeshot_in_hand_scores_positive(self, card_db):
        """Regression anchor: same scenario but with Grapeshot added
        to hand. Ritual EV must stay positive so the chain continues."""
        game = GameState(rng=random.Random(0))
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
        _add(game, card_db, "Ruby Medallion", controller=0,
             zone="battlefield")
        _add(game, card_db, "Ruby Medallion", controller=0,
             zone="battlefield")

        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
        _add(game, card_db, "Grapeshot", controller=0, zone="hand")

        _add(game, card_db, "Guide of Souls", controller=1,
             zone="battlefield")

        game.players[0].deck_name = "Ruby Storm"
        game.players[1].deck_name = "Boros Energy"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 4
        game.players[0].lands_played_this_turn = 1
        game.players[0].spells_cast_this_turn = 1
        game._global_storm_count = 1
        game.players[0].life = 20
        game.players[1].life = 20

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(ritual, snap, game, me, opp)

        assert ev > -5.0, (
            f"Regression: ritual-with-Grapeshot-in-hand scored "
            f"EV={ev:.2f} ≤ pass_threshold.  The finisher-access "
            f"gate fix has over-fired — it must only clamp when NO "
            f"finisher is in hand."
        )
