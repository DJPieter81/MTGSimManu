"""Storm must not burn through enablers when no finisher is reachable.

Reference: docs/diagnostics/2026-04-28_storm_wasted_enablers.md
(Verbose seed 50000 — Ruby Storm vs Dimir Midrange, T4)

Pre-fix behaviour (the bug):

  Storm mid-chain on T4 with no Grapeshot in hand, no Wish in hand,
  no Empty the Warrens in hand, and an empty library-top to dig with
  (all draws used).  Hand contains: 2× Past in Flames + a few
  rituals.  AI casts Past in Flames → grants flashback to graveyard
  → casts more rituals from graveyard → casts Past in Flames AGAIN
  for no incremental value (graveyard already had flashback) →
  burns the entire engine without dealing any damage.

Root cause: `compute_play_ev` rewards each cast by goal-priority +
combo-chain bonus.  The combo-chain bonus correctly returns
`can_kill=False, damage=0` when no finisher is reachable, so the
chain bonus contributes 0.  But the GOAL priority for Past in Flames
in `goryos_vengeance.json`-equivalent gameplan is 24.0 — high — and
gets paid out independently of whether the cast actually advances
toward a payoff.

The right rule (rule, not card-name): when archetype is combo/storm
AND no payoff is reachable this turn (no finisher in hand AND no
tutor-for-finisher in hand AND remaining-this-turn draws are
exhausted), enabler-tagged spells must score below the pass-action.
Casting them depletes resources for zero EV.

Class-size: every combo deck where the engine can run independent of
the payoff (Storm, Living End loop variations, Niv-Mizzet, Goryo's
post-resolution chain).  Detection is `'ritual' in tags` /
`'cantrip' in tags` / `flashback grant` mechanic — zero card names.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _build_storm_no_finisher_state(card_db, hand_names,
                                    storm_count=6, mountains=4,
                                    gy_names=None):
    """Storm mid-chain with hand_names in hand (NO finisher, NO Wish,
    NO sideboard finisher), mountains untapped, opp on full life.

    `gy_names` populates graveyard — defaults to a realistic
    mid-chain state with 4 spells already cast (so `gy_fuel > 0`
    and the flashback-combo signal can fire if not gated)."""
    game = GameState(rng=random.Random(0))
    for _ in range(mountains):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    cards = {}
    for n in hand_names:
        c = _add(game, card_db, n, controller=0, zone="hand")
        cards.setdefault(n, c)
    for n in (gy_names or ["Pyretic Ritual", "Manamorphose",
                            "Reckless Impulse", "Desperate Ritual"]):
        _add(game, card_db, n, controller=0, zone="graveyard")
    # Opponent: vanilla blocker so face damage from any source matters
    _add(game, card_db, "Guide of Souls", controller=1,
         zone="battlefield")
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = storm_count
    game._global_storm_count = storm_count
    game.players[0].life = 12
    game.players[1].life = 20
    return game, cards


def _decide(game):
    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    chosen = player.decide_main_phase(game)
    if chosen is None:
        return ("PASS", None, None)
    action, card, targets = chosen
    return (action, card.name, targets)


class TestStormPassesWhenNoFinisherReachable:
    """Storm must pass priority — not burn enablers — when no payoff
    is reachable this turn."""

    def test_pass_when_only_past_in_flames_left_no_finisher(
            self, card_db):
        """Hand: Past in Flames, Pyretic Ritual.
        Battlefield: 4 Mountains untapped (4R available).
        No Grapeshot, no Wish, no Empty the Warrens anywhere.
        Library: presume empty for the 'no draw' case (we don't model
        same-turn draws unless the hand has a cantrip).

        Casting PiF + flashback-cast the ritual gains storm count,
        but storm count without a finisher is wasted — it doesn't
        deal damage, doesn't draw cards, doesn't deploy threats.
        AI must PASS.

        Pre-fix: AI casts PiF (goal priority 24.0 dominates).  Today
        this assertion fails red.

        The same predicate must catch a hand of {2× PiF, rituals,
        nothing else} as in verbose seed 50000."""
        game, cards = _build_storm_no_finisher_state(
            card_db,
            hand_names=["Past in Flames", "Pyretic Ritual"],
            storm_count=6,
        )
        action, name, _ = _decide(game)
        assert action == "PASS" or name not in ("Past in Flames",
                                                "Pyretic Ritual"), (
            f"Storm cast '{name}' (action={action}) when no finisher "
            f"is reachable.  Hand: {[c.name for c in game.players[0].hand]}; "
            f"battlefield: {[c.name for c in game.players[0].battlefield]}; "
            f"opp life: {game.players[1].life}.  No Grapeshot / Wish / "
            f"Empty the Warrens anywhere — casting Past in Flames or a "
            f"ritual gains storm count for no payoff.  EV must score "
            f"these below the pass-action when payoff is unreachable.  "
            f"See docs/diagnostics/2026-04-28_storm_wasted_enablers.md"
        )

    def test_pass_when_only_rituals_no_finisher(self, card_db):
        """Pure-ritual no-finisher state: 3 rituals in hand, no PiF,
        no payoff.  Casting rituals just spends mana for storm count
        that goes nowhere.  Must PASS."""
        game, cards = _build_storm_no_finisher_state(
            card_db,
            hand_names=["Pyretic Ritual", "Desperate Ritual",
                        "Pyretic Ritual"],
            storm_count=4,
        )
        action, name, _ = _decide(game)
        ritual_names = {"Pyretic Ritual", "Desperate Ritual",
                        "Manamorphose"}
        assert action == "PASS" or name not in ritual_names, (
            f"Storm cast ritual '{name}' (action={action}) when no "
            f"finisher reachable.  Pure-ritual hands without a "
            f"payoff must pass — storm count alone wins zero games."
        )

    def test_casts_finisher_when_reachable(self, card_db):
        """Regression: Storm WITH a finisher in hand must still cast
        it when the chain can lethal.  Don't over-tighten the
        no-finisher predicate."""
        game, cards = _build_storm_no_finisher_state(
            card_db,
            hand_names=["Grapeshot", "Pyretic Ritual",
                        "Pyretic Ritual", "Desperate Ritual",
                        "Manamorphose"],
            storm_count=15,  # 15 prior casts → Grapeshot deals 16
            mountains=4,
        )
        # Drop opp life to a value the chain can finish to verify
        # the cast-finisher branch fires.
        game.players[1].life = 5
        action, name, _ = _decide(game)
        ritual_names = {"Pyretic Ritual", "Desperate Ritual",
                        "Manamorphose"}
        assert name in ritual_names | {"Grapeshot"}, (
            f"Storm passed or cast something off-line ('{name}') "
            f"when Grapeshot in hand at storm=15 vs opp@5 — chain "
            f"is lethal.  The no-finisher predicate must NOT block "
            f"this.  Casting any ritual or Grapeshot itself is fine; "
            f"passing is the regression."
        )
