"""S-1a — Past in Flames as a finisher path requires graveyard fuel + mana.

Diagnostic convergence (Boros trace + patience-audit, 2026-04-24):
The storm patience gate at `ai/ev_player.py:1165-1179` greenlights ritual
chains at storm=0 when `(has_finisher OR has_pif) AND total_fuel >= min`.
But Past in Flames alone is NOT a finisher — it requires rituals/cantrips
already in the graveyard to flashback into a chain. On T3 with an empty
graveyard, PiF is uncastable as a finisher path: even if the rituals
resolve, PiF replays nothing.

Symptom: Ruby Storm at 22.8% flat WR. Storm fires speculative ritual
chains expecting PiF to bail it out, fizzles, then loses to Boros clock.

Fix: when computing has_pif as a finisher-path proxy, also require:
1. Graveyard contains >=1 flashback-castable ritual/cantrip (else PiF
   replays nothing).
2. Available mana >= PiF.cmc minus on-battlefield cost reducers (else
   PiF cannot be cast this turn).
3. Either Grapeshot in hand OR a Wish-tutor (already covered by
   `_has_finisher()` — has_pif must add a finisher access path on top
   of the GY-replay capability).

Anti-patterns rejected:
- No hardcoded card names (PiF is identified by `flashback`+`combo` tags).
- No magic numbers — PiF cost uses template.cmc, finisher detection
  re-uses existing `_has_finisher()` semantics.
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


def _setup_storm_t3(game, card_db, mountains: int) -> None:
    """Boilerplate: storm side state at storm=0 on T3, no medallions."""
    for _ in range(mountains):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    # Opp board — needs a creature so EVSnapshot has clock data, but
    # opp_life=20 (no lethal-this-turn shortcuts).
    _add(game, card_db, "Guide of Souls", controller=1, zone="battlefield")
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 3
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = 0
    game._global_storm_count = 0
    game.players[0].life = 20
    game.players[1].life = 20


class TestStormPiFFinisherRequiresGYAndMana:
    """At storm=0, a Past-in-Flames-only finisher path (no Grapeshot
    in hand, no tutor) must NOT greenlight ritual commitment unless
    PiF can actually be cast AND has at least one flashback target in
    the graveyard."""

    def test_pif_with_empty_gy_does_not_greenlight_rituals(self, card_db):
        """Hand: 2 rituals + Past in Flames. GY: empty. Mana: 4
        Mountains untapped (enough for PiF).  PiF would resolve but
        replay nothing — no finisher path exists.  Ritual EV must be
        clamped negative so the AI holds."""
        game = GameState(rng=random.Random(0))
        _setup_storm_t3(game, card_db, mountains=4)

        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
        _add(game, card_db, "Past in Flames", controller=0, zone="hand")

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(ritual, snap, game, me, opp)

        STORM_PASS_THRESHOLD = -5.0  # mirrors STORM profile pass_threshold
        assert ev < STORM_PASS_THRESHOLD, (
            f"Ritual at storm=0 with PiF-in-hand but EMPTY graveyard "
            f"scored EV={ev:.2f} >= pass_threshold ({STORM_PASS_THRESHOLD}). "
            f"PiF needs flashback fuel in GY to function as a finisher "
            f"path; with an empty GY, this ritual chain fizzles."
        )

    def test_pif_with_loaded_gy_and_grapeshot_greenlights_rituals(self, card_db):
        """Hand: Ritual + PiF + Grapeshot. GY: 3 rituals (flashback-able
        once PiF resolves). Mana: 5 Mountains (enough for PiF cost).
        This IS a real go-off line: cast ritual -> PiF -> flashback 3 GY
        rituals -> Grapeshot for storm=6+. Ritual EV must be positive."""
        game = GameState(rng=random.Random(0))
        _setup_storm_t3(game, card_db, mountains=5)

        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        _add(game, card_db, "Past in Flames", controller=0, zone="hand")
        _add(game, card_db, "Grapeshot", controller=0, zone="hand")
        # GY fuel — flashback-eligible rituals waiting for PiF.
        _add(game, card_db, "Desperate Ritual", controller=0,
             zone="graveyard")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="graveyard")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="graveyard")

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(ritual, snap, game, me, opp)

        # Note: this test guards against over-firing the fix — when the
        # PiF path is REAL, has_finisher (Grapeshot in hand) already
        # greenlights the chain.  The test value lies in confirming the
        # fix doesn't accidentally clamp this case too.
        assert ev > -5.0, (
            f"Regression: ritual with loaded GY + PiF + Grapeshot in "
            f"hand scored EV={ev:.2f} <= pass_threshold.  The fix has "
            f"over-fired — it must only clamp when PiF lacks GY fuel "
            f"or sufficient mana."
        )

    def test_pif_with_insufficient_mana_does_not_greenlight_rituals(self, card_db):
        """Hand: Ritual + PiF (no Grapeshot). GY: 2 rituals. Mana: 2
        Mountains (insufficient for PiF cost=4). Even though the GY has
        fuel, PiF cannot be cast this turn — so PiF is not a real
        finisher path. Ritual EV must be clamped negative."""
        game = GameState(rng=random.Random(0))
        _setup_storm_t3(game, card_db, mountains=2)

        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        _add(game, card_db, "Past in Flames", controller=0, zone="hand")
        _add(game, card_db, "Desperate Ritual", controller=0,
             zone="graveyard")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="graveyard")

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(ritual, snap, game, me, opp)

        STORM_PASS_THRESHOLD = -5.0
        assert ev < STORM_PASS_THRESHOLD, (
            f"Ritual at storm=0 with PiF in hand but INSUFFICIENT MANA "
            f"to cast PiF (need cmc>=4, have 2 Mountains) scored "
            f"EV={ev:.2f} >= pass_threshold ({STORM_PASS_THRESHOLD}). "
            f"Even though GY has flashback fuel, PiF cannot resolve "
            f"this turn — the chain still fizzles."
        )

    def test_grapeshot_in_hand_greenlights_regardless_of_gy(self, card_db):
        """Regression: when a real finisher (Grapeshot via storm
        keyword OR a tutor) is in hand, the existing has_finisher path
        must still greenlight rituals — the fix only constrains the
        has_pif path."""
        game = GameState(rng=random.Random(0))
        _setup_storm_t3(game, card_db, mountains=3)

        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
        _add(game, card_db, "Grapeshot", controller=0, zone="hand")
        # GY empty, no PiF — finisher path is purely Grapeshot.

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(ritual, snap, game, me, opp)

        assert ev > -5.0, (
            f"Regression: ritual with Grapeshot in hand (real finisher) "
            f"scored EV={ev:.2f} <= pass_threshold.  The fix must not "
            f"affect the has_finisher path — only the has_pif proxy."
        )
