"""S-1b retry — Wish-as-finisher must validate sideboard contents.

Diagnostic: Iter 4 left Ruby Storm at 19.4% WR.  Audit traced the
remaining loss path to the storm patience gate
`ai/ev_player.py:_combo_modifier::_has_finisher` returning True
whenever ANY tutor (Wish, Burning Wish) sat in hand — without
checking that the sideboard / library actually contained a real
finisher to tutor for.

After Wish has been cast once and the lone sideboard Grapeshot has
been pulled, the remaining SB cards are utility (Orim's Chant,
Prismatic Ending, Wear // Tear, etc.).  A second Wish in hand still
flagged "has finisher" — so the AI happily committed a 4-ritual
chain expecting a kill, then resolved Wish into a non-lethal utility
spell and passed turn with the chain wasted.

Fix (this PR): split the predicate into two checks —

1. Direct STORM-keyword finisher in hand → True (fast path).
2. Tutor in hand → only True if SB ∪ library contains either
   - a STORM-keyword card (Grapeshot, Empty the Warrens,
     Galvanic Relay), OR
   - a token-spawning finisher whose oracle text matches
     "create … tokens" + "for each" (catches printed Empty-style
     finishers without the STORM keyword).

ZERO hardcoded card names — the predicate only inspects the keyword
set, the tag set, and oracle text.

The test exercises the production code via `EVPlayer._score_spell`
on a Desperate Ritual at storm=0 with the patience gate active.

Pre-fix: scenarios 1, 2, 4 greenlight; scenario 3 ALSO greenlights
(buggy — tutor-in-hand alone passed `_has_finisher()`).
Post-fix: scenarios 1, 2, 4 greenlight; scenario 3 clamps below
the STORM pass_threshold.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


STORM_PASS_THRESHOLD = -5.0  # mirrors STORM profile pass_threshold


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


def _build_storm_game(card_db, hand_names, sideboard_names,
                      library_names=()):
    """Storm side: 2 Mountains + 2 Medallions on battlefield, T4,
    storm=0, opp life 20.  Hand / sideboard / library populated
    from the name lists.  Returns (game, ritual_card_in_hand).

    The hand is expected to contain at least one ritual; we return
    the first one to use as the scoring probe."""
    game = GameState(rng=random.Random(0))
    _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    _add(game, card_db, "Ruby Medallion", controller=0,
         zone="battlefield")
    _add(game, card_db, "Ruby Medallion", controller=0,
         zone="battlefield")
    ritual = None
    for n in hand_names:
        c = _add(game, card_db, n, controller=0, zone="hand")
        if ritual is None and 'ritual' in getattr(c.template, 'tags', set()):
            ritual = c
    for n in sideboard_names:
        _add(game, card_db, n, controller=0, zone="sideboard")
    for n in library_names:
        _add(game, card_db, n, controller=0, zone="library")
    _add(game, card_db, "Guide of Souls", controller=1,
         zone="battlefield")
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = 0
    game._global_storm_count = 0
    game.players[0].life = 20
    game.players[1].life = 20
    assert ritual is not None, "test scenario must include a ritual"
    return game, ritual


def _score_ritual(game, ritual):
    """Run EVPlayer._score_spell against the ritual and return EV."""
    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]
    return player._score_spell(ritual, snap, game, me, opp)


class TestWishValidatesSideboard:
    """The patience-gate `_has_finisher` predicate must verify a
    tutor in hand can actually fetch a real finisher from the
    sideboard or library.  Otherwise it greenlights ritual chains
    that close into a non-finisher utility spell."""

    def test_wish_with_grapeshot_in_sb_greenlights_ritual(self, card_db):
        """Hand: Desperate Ritual + Wish.  SB: Grapeshot.
        Expected: ritual EV above pass_threshold (Wish→Grapeshot is
        a real finisher path)."""
        game, ritual = _build_storm_game(
            card_db,
            hand_names=["Desperate Ritual", "Wish"],
            sideboard_names=["Grapeshot"],
        )
        ev = _score_ritual(game, ritual)
        assert ev > STORM_PASS_THRESHOLD, (
            f"Ritual EV={ev:.2f} ≤ pass_threshold ({STORM_PASS_THRESHOLD}) "
            f"with Wish+Grapeshot-in-SB.  The patience gate must "
            f"recognise Wish→Grapeshot as a real finisher path."
        )

    def test_wish_with_empty_warrens_in_sb_greenlights_ritual(self, card_db):
        """Hand: Desperate Ritual + Wish.  SB: Empty the Warrens.
        Expected: ritual EV above pass_threshold (Wish→Empty is a
        valid storm finisher; Empty has STORM keyword in this DB)."""
        game, ritual = _build_storm_game(
            card_db,
            hand_names=["Desperate Ritual", "Wish"],
            sideboard_names=["Empty the Warrens"],
        )
        ev = _score_ritual(game, ritual)
        assert ev > STORM_PASS_THRESHOLD, (
            f"Ritual EV={ev:.2f} ≤ pass_threshold ({STORM_PASS_THRESHOLD}) "
            f"with Wish+Empty-the-Warrens-in-SB.  Empty is a valid "
            f"storm finisher; the patience gate must recognise it."
        )

    def test_wish_with_only_utility_in_sb_holds_ritual(self, card_db):
        """Hand: Desperate Ritual + Wish.  SB: Vexing Shusher only
        (no finisher).  Expected: ritual EV CLAMPED below
        pass_threshold — Wish has nothing real to fetch.

        This is the bug Iter 4 left behind: prior code returned True
        from `_has_finisher` as soon as a tutor sat in hand,
        regardless of what the sideboard actually contained.  The
        fix demands SB ∪ library contain a usable finisher."""
        game, ritual = _build_storm_game(
            card_db,
            hand_names=["Desperate Ritual", "Wish"],
            sideboard_names=["Vexing Shusher"],
        )
        ev = _score_ritual(game, ritual)
        assert ev < STORM_PASS_THRESHOLD, (
            f"Ritual EV={ev:.2f} > pass_threshold "
            f"({STORM_PASS_THRESHOLD}) with Wish-in-hand but no "
            f"real finisher in SB/library.  The previous "
            f"`_has_finisher` predicate returned True purely on "
            f"the 'tutor' tag — this caused Storm to commit ritual "
            f"chains that closed into a non-lethal utility spell. "
            f"Fix: in ai/ev_player.py _combo_modifier, validate "
            f"SB ∪ library contains a STORM-keyword card or a "
            f"token-spawning finisher (oracle: 'create … tokens' "
            f"+ 'for each') before granting the tutor a finisher "
            f"credit."
        )

    def test_grapeshot_in_hand_directly_greenlights_ritual(self, card_db):
        """Regression anchor: hand has Desperate Ritual + Grapeshot.
        Expected: ritual EV above pass_threshold via the
        STORM-keyword fast path (no SB lookup needed)."""
        game, ritual = _build_storm_game(
            card_db,
            hand_names=["Desperate Ritual", "Grapeshot"],
            sideboard_names=[],
        )
        ev = _score_ritual(game, ritual)
        assert ev > STORM_PASS_THRESHOLD, (
            f"Regression: ritual EV={ev:.2f} ≤ pass_threshold "
            f"({STORM_PASS_THRESHOLD}) with Grapeshot-in-hand.  The "
            f"STORM-keyword fast path must always greenlight."
        )
