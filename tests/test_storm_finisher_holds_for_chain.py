"""Phase 9a — Storm finisher holds when next-turn chain is bigger.

Phase 6 follow-up from
docs/experiments/2026-04-20_phase6_matrix_validation.md.

Observed in `python run_meta.py --verbose "Ruby Storm" "Dimir
Midrange" -s 50000` T3: Storm cast 4 spells (Ritual + Medallion +
Wrenn's Resolve + Grapeshot) with storm_count=4 → only 4 damage
(opponent at 20 → 16). The actual best play was to HOLD Grapeshot
into T4: untap 4 lands → cast Past in Flames (already in hand) →
flashback rituals + cantrips from GY → much larger storm count for
lethal-or-near-lethal Grapeshot.

Diagnosis: `_combo_modifier` Grapeshot finisher gate at
`ai/ev_player.py:884-918` counts `fuel_available` only over cards
the player can CAST RIGHT NOW (`game.can_cast` filter).  At the
firing point Storm had 0-1 mana left, so fuel_available
under-counted Past in Flames + other GY/hand fuel that would be
playable next turn after untap. Penalty for missing lethal stayed
small (~-2.5), Grapeshot's other-signal bonuses pushed EV positive,
and Storm fired prematurely.

Fix: count cards in hand + flashback-eligible GY without the
`can_cast` filter. Next-turn-mana fuel is the right horizon; the
existing `storm_chain_continuation_p` discount already models chain
reliability.
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


class TestStormFinisherHoldsForChain:
    """Grapeshot must NOT fire when storm < lethal AND a Past in
    Flames in hand promises a much bigger chain next turn."""

    def test_grapeshot_held_when_pif_in_hand_and_storm_short(
            self, card_db):
        """Reproduces seed 50000 T3: storm=3, hand has Grapeshot +
        2 Past in Flames + March of Reckless Joy + 1 mana floating.
        Casting Grapeshot now = 4 damage. Holding for T4 PiF chain
        promises 10+ damage. AI must defer Grapeshot."""
        game = GameState(rng=random.Random(0))
        # Storm side: 1 untapped mana (Sacred Foundry); Mountain x2
        # tapped (already used for the rituals this turn); Ruby
        # Medallion in play (cost reducer = combo_continuation signal).
        _add(game, card_db, "Sacred Foundry", controller=0,
             zone="battlefield")
        m1 = _add(game, card_db, "Mountain", controller=0,
                  zone="battlefield")
        m1.tapped = True
        m2 = _add(game, card_db, "Mountain", controller=0,
                  zone="battlefield")
        m2.tapped = True
        _add(game, card_db, "Ruby Medallion", controller=0,
             zone="battlefield")

        # Hand: Grapeshot (the cast in question), 2x PiF (the chain
        # extender), March of Reckless Joy (an impulse-draw cantrip).
        grapeshot = _add(game, card_db, "Grapeshot", controller=0,
                         zone="hand")
        _add(game, card_db, "Past in Flames", controller=0, zone="hand")
        _add(game, card_db, "Past in Flames", controller=0, zone="hand")
        _add(game, card_db, "March of Reckless Joy", controller=0,
             zone="hand")

        # GY: rituals + cantrips already cast this turn (PiF would
        # flashback these next turn).
        _add(game, card_db, "Desperate Ritual", controller=0,
             zone="graveyard")
        _add(game, card_db, "Reckless Impulse", controller=0,
             zone="graveyard")
        _add(game, card_db, "Wrenn's Resolve", controller=0,
             zone="graveyard")

        # Opp board reflects Dimir T3: Bowmasters + Orc Army + Frog.
        # Storm at 12 life, opp at 20 (we haven't dealt damage yet).
        game.players[0].life = 12
        game.players[1].life = 20
        _add(game, card_db, "Orcish Bowmasters", controller=1,
             zone="battlefield")
        _add(game, card_db, "Psychic Frog", controller=1,
             zone="battlefield")

        game.players[0].deck_name = "Ruby Storm"
        game.players[1].deck_name = "Dimir Midrange"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 3
        game.players[0].lands_played_this_turn = 1
        # Three spells cast this turn (Ritual + Medallion + Wrenn's).
        game.players[0].spells_cast_this_turn = 3
        game._global_storm_count = 3

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(grapeshot, snap, game, me, opp)

        # Storm pass_threshold is -5.0.  Grapeshot at storm=3 deals 4
        # damage (1 + 3 copies); opp at 20 - 4 = 16.  Holding for T4
        # PiF chain is strictly better, so Grapeshot's score must be
        # below pass_threshold so the AI defers.
        STORM_PASS_THRESHOLD = -5.0
        assert ev < STORM_PASS_THRESHOLD, (
            f"Grapeshot scored EV={ev:.2f} above STORM pass_threshold "
            f"({STORM_PASS_THRESHOLD}); AI will fire for 4 damage "
            f"and lose the PiF chain.  The fuel-counting branch in "
            f"_combo_modifier should count PiF + GY flashback fuel "
            f"that's playable NEXT TURN, not just what's castable "
            f"with current 1-mana floor."
        )

    def test_grapeshot_fires_for_lethal(self, card_db):
        """Regression anchor — Grapeshot at storm count >= opp_life
        is lethal; must STILL fire."""
        game = GameState(rng=random.Random(0))
        for _ in range(6):
            _add(game, card_db, "Mountain", controller=0,
                 zone="battlefield")
        grapeshot = _add(game, card_db, "Grapeshot", controller=0,
                         zone="hand")
        # Push opp_life low so storm=3 lethals.
        game.players[1].life = 3
        # Three spells already cast this turn → storm_count=3,
        # Grapeshot copies 3 times for 4 damage = lethal vs 3 life.
        game.players[0].spells_cast_this_turn = 3
        game._global_storm_count = 3

        game.players[0].deck_name = "Ruby Storm"
        game.players[1].deck_name = "Dimir Midrange"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 3
        game.players[0].lands_played_this_turn = 1

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(grapeshot, snap, game, me, opp)
        assert ev > 0.0, (
            f"Lethal Grapeshot scored EV={ev:.2f} — must remain a "
            f"strong positive cast (lethal = +100 win swing in the "
            f"finisher gate)."
        )
