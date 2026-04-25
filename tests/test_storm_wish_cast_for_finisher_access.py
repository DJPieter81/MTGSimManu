"""Storm vs Boros s50500 reproduction — Wish must be cast when it
is the only finisher-access path.

Diagnosis (2026-04-25):

 1. `_enumerate_this_turn_signals` (ai/ev_evaluator.py:626) recognises
    `search your library` as a tutor signal but NOT `play a card you
    own from outside the game` (Wish-pattern).  Wish therefore returns
    an empty signal list → `compute_play_ev` sets `deferral=True` →
    `decide_main_phase` (ai/ev_player.py:417-420) filters Wish out of
    the candidate list BEFORE EV is even compared.  The play is never
    selectable, irrespective of how high its EV would have been.

 2. Even if Wish makes it past the deferral gate, the new
    `card_combo_modifier` (ai/combo_calc.py) only credits direct
    STORM-keyword spells with finisher EV — a tutor whose target deck
    contains a payoff still scores at the projection's vanilla
    baseline (~−0.2 EV).  In a multi-cast turn the cantrip tiebreaker
    will then pick a vanilla cantrip over Wish.

The verbose game `Ruby Storm vs Boros Energy -s 50500` showed Storm
casting 16+ spells across T6+T7 with Wish in hand the entire time
and a Grapeshot in sideboard, never casting Wish, finally losing
on damage T7.

Fix surface:

 A. Extend `_enumerate_this_turn_signals` tutor branch to recognise
    `play a card ... from outside the game` (Wish-pattern) AND `from
    your sideboard`.  Generic: any oracle phrasing that pulls a card
    into the chain this turn.  Captures Wish, Burning Wish, Living
    Wish, Glittering Wish — and any future card that uses the same
    template wording.

 B. In `card_combo_modifier`, score a tutor-tagged card with a
    payoff in SB/library symmetrically to the STORM-keyword finisher
    branch — a tutor IS the finisher, one cast away.  Mirrors the
    existing storm-vs-fuel arithmetic.  No card-name hardcoding —
    detection is `'tutor' in tags` AND `_has_storm_finisher`-style
    SB validation.

Generic by construction: every condition is oracle text or tag
based.  Same mechanism credits any tutor-tagged spell whose target
deck contains a STORM-keyword card or token-spawning finisher.

Pre-fix:
 - `decide_main_phase` returns a non-Wish play even when Wish is
   the highest-scored cast (deferral gate filters Wish out).
Post-fix:
 - `decide_main_phase` returns Wish as the play when it is the only
   finisher-access path AND no other above-threshold non-deferred
   plays outscore it.
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


def _build_storm_chain_game(card_db, hand_names, sideboard_names,
                            storm_count=4, mountains=4, medallions=1):
    """Storm side mid-chain.  `mountains` untapped Mountains and
    `medallions` Ruby Medallions on battlefield, opp on 19 life with
    a vanilla blocker so combat is not lethal.  Hand / SB populated
    from the name lists.  Returns (game, named_card_dict).

    `storm_count` simulates spells already cast this turn.
    """
    game = GameState(rng=random.Random(0))
    for _ in range(mountains):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    for _ in range(medallions):
        _add(game, card_db, "Ruby Medallion", controller=0,
             zone="battlefield")
    cards = {}
    for n in hand_names:
        c = _add(game, card_db, n, controller=0, zone="hand")
        cards.setdefault(n, c)
    for n in sideboard_names:
        _add(game, card_db, n, controller=0, zone="sideboard")
    _add(game, card_db, "Guide of Souls", controller=1,
         zone="battlefield")
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 6
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = storm_count
    game._global_storm_count = storm_count
    game.players[0].life = 14
    game.players[1].life = 19
    return game, cards


def _decide(game):
    """Run EVPlayer.decide_main_phase against the assembled game and
    return the chosen (action, card_name, targets) — or None if the
    AI passes."""
    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    chosen = player.decide_main_phase(game)
    if chosen is None:
        return None
    action, card, targets = chosen
    return (action, card.name, targets)


def _signals(game, card):
    """Probe `_enumerate_this_turn_signals` directly so the deferral
    failure mode is visible when the integration test fails."""
    from ai.ev_evaluator import _enumerate_this_turn_signals
    snap = snapshot_from_game(game, 0)
    return _enumerate_this_turn_signals(card, snap, game, 0, "storm")


class TestWishCastForFinisherAccess:
    """A tutor card whose target deck contains a real payoff must
    pass the deferral gate AND be selected by `decide_main_phase`.
    Without this, Storm chains assemble but never close into the
    finisher."""

    def test_wish_emits_a_this_turn_signal(self, card_db):
        """Diagnostic predicate: a Wish-pattern tutor (`play a card
        you own from outside the game`) must emit at least one
        same-turn signal so it is not deferred.  Pre-fix:
        `_enumerate_this_turn_signals` only matches `search your
        library` and returns []; Wish gets `deferral=True` and is
        filtered out of `decide_main_phase` before EV is compared."""
        game, cards = _build_storm_chain_game(
            card_db,
            hand_names=["Wish"],
            sideboard_names=["Grapeshot"],
            storm_count=4,
        )
        sig = _signals(game, cards["Wish"])
        assert sig, (
            f"Wish emitted no this-turn signals; the deferral gate "
            f"will filter it out of `decide_main_phase`.  "
            f"Extend `_enumerate_this_turn_signals` to recognise "
            f"`play a card ... from outside the game` (Wish pattern) "
            f"as a tutor-style signal — same generic predicate as "
            f"the existing `search your library` branch."
        )

    def test_wish_chosen_when_only_finisher_access_path(self, card_db):
        """End-to-end: Storm mid-chain (storm=4), Wish in hand as the
        only spell, SB has Grapeshot.  `decide_main_phase` must
        return Wish — not None (pass).

        Pre-fix: the deferral gate filters Wish out → no candidate
        survives → `decide_main_phase` returns None.
        Post-fix: Wish emits a tutor signal, clears pass_threshold,
        and is the only available cast → it is selected.
        """
        game, cards = _build_storm_chain_game(
            card_db,
            hand_names=["Wish"],
            sideboard_names=["Grapeshot"],
            storm_count=4,
        )
        chosen = _decide(game)
        assert chosen is not None and chosen[1] == "Wish", (
            f"`decide_main_phase` returned {chosen!r}; expected "
            f"('cast_spell', 'Wish', ...).  Wish is the only "
            f"finisher-access path on a built-up chain and must be "
            f"selected."
        )

    def test_wish_chosen_as_closer_after_fuel_exhausted(self, card_db):
        """Sequencing test: storm=6 (chain in progress), hand has
        Wish plus only land (Mountain) — no non-tutor fuel left to
        chain.  Wish IS the closer: it must fire now to fetch the
        SB finisher.

        This mirrors the STORM-keyword branch behaviour: hold the
        finisher while fuel remains in hand, fire it once fuel is
        exhausted.  A tutor with payoff access follows the same
        rule — chain fuel first, fire the tutor last as the closer."""
        game, cards = _build_storm_chain_game(
            card_db,
            hand_names=["Wish", "Mountain"],
            sideboard_names=["Grapeshot"],
            storm_count=6,
        )
        chosen = _decide(game)
        assert chosen is not None and chosen[1] == "Wish", (
            f"`decide_main_phase` returned {chosen!r}; with storm=6 "
            f"and no non-tutor fuel left in hand, Wish is the "
            f"closer and must fire now.  The combo modifier's "
            f"tutor branch must return positive EV "
            f"((storm + 2) / opp_life × combo_value) when "
            f"non-tutor fuel is exhausted, mirroring the STORM-"
            f"keyword finisher branch's no-fuel arithmetic."
        )

    def test_wish_not_chosen_when_sb_lacks_finisher(self, card_db):
        """Regression anchor: when SB contains only utility (no
        STORM-keyword card, no token-spawning finisher), Wish must
        NOT outrank a cantrip — there is no payoff to credit it for.

        This guards against a fix that gives every tutor a positive
        EV regardless of SB contents.  Both the signal extension and
        the combo-modifier bonus must validate SB ∪ library actually
        contains a real payoff (mirrors `_has_storm_finisher`)."""
        game, cards = _build_storm_chain_game(
            card_db,
            hand_names=["Wish", "Reckless Impulse"],
            sideboard_names=["Vexing Shusher"],
            storm_count=4,
        )
        chosen = _decide(game)
        # Either Reckless wins or Wish ties + tiebreaker picks
        # cantrip — both acceptable.  What is NOT acceptable is Wish
        # being chosen when there is no finisher to fetch.
        assert chosen is None or chosen[1] != "Wish", (
            f"`decide_main_phase` returned {chosen!r}; with no "
            f"finisher in SB ∪ library, Wish must NOT outrank a "
            f"cantrip.  Pre-merge check: validate the bonus is "
            f"gated on `_has_storm_finisher`-style SB inspection, "
            f"not granted unconditionally to every tutor."
        )
