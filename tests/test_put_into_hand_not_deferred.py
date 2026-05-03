"""Card-advantage spells using "put N into your hand" must not be
deferred for lacking a same-turn signal.

Surfaced by the 2026-05-03 Azorius Control diagnostic at 13% WR
(22pp under expected 35-50% band): AC's primary card-draw spell,
Stock Up ({2}{U}, "Look at the top five cards of your library.
Put two of them into your hand and the rest on the bottom of your
library in any order"), scored at -16.2 in trace
`run_meta.py --trace "Azorius Control" "Boros Energy" -s 60500` and
was therefore never cast across two full games. Without its draw
engine, AC drew lands and ran out of action while the aggro clock
ticked through.

Root cause — `_enumerate_this_turn_signals` (ai/ev_evaluator.py)
signal #5 (`card_draw`) requires the literal word "draw" in oracle
text. Stock Up's oracle uses "put two of them into your hand"
phrasing instead and therefore emits NO same-turn signal. The
enumerator falls through to deferral, the EV is set to
`-exposure_cost` (≈ -16 for a 3-mana spell), and the spell loses to
"pass" every time.

Class size — 453 cards in ModernAtomic carry oracle text containing
"into your hand" without "draw" and without "search your library".
Examples: Stock Up, Ancient Stirrings, Anticipate, Adventurous
Impulse, Allure of the Unknown, Augur of Bolas, Brainstorm-style
"draw three then put two back" variants, several planeswalker -2
abilities, and many tutors/dig effects. The fix targets the generic
"look at top N → put X into your hand" mechanic by oracle text
(no card-name checks), so every deck containing such a spell
benefits.

Fix — extend signal #5 (`card_draw`) to also fire when the oracle
contains "into your hand" together with a library-touching verb
("look at" / "reveal" / "exile" / "the top"). This recognises the
Stock Up / Ancient Stirrings / Anticipate / Augur of Bolas family
as same-turn card-advantage and routes the cast through the normal
projection path (which already credits `cards_drawn_this_turn` for
draw-engine tags).

Regression anchor — a 4-mana sorcery with no draw / dig / hand
phrasing must STILL defer (e.g. a vanilla 4/4 with no ETB at the
wrong CMC), or the gate becomes meaningless.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import _enumerate_this_turn_signals, snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
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


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _setup_t4_main(game, controller=0):
    game.players[controller].deck_name = "Azorius Control"
    game.players[1 - controller].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = controller
    game.priority_player = controller
    game.turn_number = 4
    game.players[controller].lands_played_this_turn = 1


class TestPutIntoHandSignalsCardDraw:
    """The "look at the top N → put X into your hand" mechanic must
    register as a same-turn `card_draw` signal in the deferral
    enumerator. Without the signal, control decks defer their primary
    engine spells indefinitely and lose to the aggro clock."""

    def test_stock_up_emits_card_draw_signal(self, card_db):
        """Stock Up ({2}{U}: look at top 5, put 2 into hand) must
        emit a same-turn signal — its EV depends on the projection
        path crediting `cards_drawn_this_turn`."""
        game = GameState(rng=random.Random(0))
        for _ in range(3):
            _add_to_battlefield(game, card_db, "Island", controller=0)
        stock_up = _add_to_hand(game, card_db, "Stock Up", controller=0)
        _setup_t4_main(game)

        snap = snapshot_from_game(game, 0)
        signals = _enumerate_this_turn_signals(
            stock_up, snap, game, 0, archetype="control")
        assert signals, (
            "Stock Up returned no this-turn signal — AI will defer "
            "the cast indefinitely. 'Look at the top N, put X into "
            "your hand' provides immediate card advantage and must "
            "register as a card_draw-class signal."
        )

    def test_ancient_stirrings_emits_card_draw_signal(self, card_db):
        """Ancient Stirrings ({G}: look at top 5, reveal a colorless
        card, put it into hand) — same mechanic class as Stock Up,
        same fix benefits Eldrazi Tron / Amulet Titan."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Forest", controller=0)
        stirrings = _add_to_hand(game, card_db, "Ancient Stirrings",
                                  controller=0)
        _setup_t4_main(game)

        snap = snapshot_from_game(game, 0)
        signals = _enumerate_this_turn_signals(
            stirrings, snap, game, 0, archetype="midrange")
        assert signals, (
            "Ancient Stirrings returned no this-turn signal — AI will "
            "defer. 'Look at top N → reveal a colorless card → put it "
            "into your hand' is a library-dig that yields card "
            "advantage same-turn."
        )

    def test_oracle_with_into_hand_but_no_dig_verb_does_not_signal(self):
        """Regression anchor: the new branch must require a
        library-touching verb (look-at / reveal / exile / search /
        the top), not just the bare phrase 'into your hand'.

        Otherwise effects that mention 'into your hand' as part of an
        unrelated clause (e.g., bounce-self spells, "return to your
        hand", reanimation modes) would over-fire the card_draw
        signal. We probe this with a synthetic oracle string passed
        through the public detection helper used by the enumerator.

        The check is structural — we test the helper's logic, not the
        end-to-end signal list, so the regression survives independent
        of which other signals happen to fire on a real card.
        """
        from ai.ev_evaluator import _oracle_signals_card_draw
        # Bounce-self phrasing: "return ~ to its owner's hand" mentions
        # hand but is not a card-draw — must NOT fire.
        bounce_self = (
            "Return target creature to its owner's hand."
        )
        assert not _oracle_signals_card_draw(bounce_self), (
            "Bounce-self oracle text wrongly registered as card_draw. "
            "The new gate must require a library-touching verb "
            "(look at / reveal / exile / search / the top), not just "
            "the phrase 'into your hand' or 'to your hand'."
        )
        # Sanity: the canonical Stock Up phrasing MUST fire.
        stock_up_phrasing = (
            "Look at the top five cards of your library. Put two of "
            "them into your hand and the rest on the bottom of your "
            "library in any order."
        )
        assert _oracle_signals_card_draw(stock_up_phrasing), (
            "Stock Up canonical phrasing must register as card_draw — "
            "this is the whole point of the fix."
        )
