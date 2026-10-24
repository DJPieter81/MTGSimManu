"""An alternative cost that exiles a card from hand (CR 118.9, 601.2f-h).

"If it's not your turn, you may exile a green card from your hand rather
than pay this spell's mana cost" (the five Forces) and "You may exile a blue
card from your hand rather than pay this spell's mana cost" (Snapback): the
cost is what the card prints -- a card of its colour (CR 105.2), under its
printed condition, if any.

The engine re-read the text at every cast; it let a card sharing the
spell's colour identity pay; it applied "if it's not your turn" to every
such spell (Snapback prints none); it exiled the cheapest candidate itself;
and on the opponent's turn it always paid this way, even with the mana cost
payable. The controller chooses whether to pay this way
(`should_exile_instead_of_paying`) and which card (`choose_exile_from_hand`).
"""
from __future__ import annotations

import copy
import random

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.mana import Color


class _Pitcher(DefaultCallbacks):
    """Exiles instead of paying when `exile` says so; picks `pick`."""

    def __init__(self, exile=True, pick=lambda candidates: candidates[0]):
        self.exile, self.pick = exile, pick
        self.asked = []

    def should_exile_instead_of_paying(self, game, player_idx, card,
                                       can_pay_mana):
        self.asked.append(can_pay_mana)
        return self.exile

    def choose_exile_from_hand(self, game, player_idx, spell, candidates):
        return self.pick(candidates)


def _game(callbacks=None, my_turn=False):
    game = GameState(rng=random.Random(0), callbacks=callbacks or _Pitcher())
    game.current_phase = Phase.MAIN1
    game.active_player = 0 if my_turn else 1
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0, template=None):
    c = CardInstance(template=template or card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


# ── The printed cost ──────────────────────────────────────────────────

def test_an_alternative_exile_cost_is_typed_with_its_colour_and_condition(
        card_db):
    negation = card_db.get_card("Force of Negation")
    assert negation.alternate_exile_color is Color.BLUE
    assert negation.alternate_exile_not_your_turn is True
    snapback = card_db.get_card("Snapback")
    assert snapback.alternate_exile_color is Color.BLUE
    assert snapback.alternate_exile_not_your_turn is False
    # Other shapes: an X-valued card, a graveyard card, a mana-free trap.
    for name in ("Sickening Shoal", "Stalwart Valkyrie", "Mindbreak Trap"):
        assert card_db.get_card(name).alternate_exile_color is None, name


# ── The engine pays it ────────────────────────────────────────────────

def test_an_alternative_exile_cost_takes_a_card_of_the_spells_colour(
        card_db):
    """A green card pays it (CR 105.2); a red card with green in its
    colour identity does not."""
    game = _game()
    _put(game, card_db, "Ornithopter", "battlefield", idx=1)   # a target
    vigor = _put(game, card_db, "Force of Vigor", "hand")
    red = copy.copy(card_db.get_card("Lightning Bolt"))
    red.color_identity = {Color.RED, Color.GREEN}
    _put(game, card_db, "", "hand", template=red)
    assert not game.can_cast(0, vigor)
    elf = _put(game, card_db, "Llanowar Elves", "hand")
    assert game.can_cast(0, vigor)
    assert game.cast_spell(0, vigor)
    assert elf.zone == "exile" and vigor.zone == "stack"


def test_an_alternative_exile_cost_holds_only_under_its_printed_condition(
        card_db):
    """"If it's not your turn" is the Forces' condition; Snapback prints
    none."""
    game = _game(my_turn=True)
    _put(game, card_db, "Ornithopter", "battlefield", idx=1)
    vigor = _put(game, card_db, "Force of Vigor", "hand")
    snapback = _put(game, card_db, "Snapback", "hand")
    _put(game, card_db, "Llanowar Elves", "hand")
    _put(game, card_db, "Opt", "hand")
    assert not game.can_cast(0, vigor)
    assert game.can_cast(0, snapback)


def test_the_controller_chooses_to_pay_the_mana_cost_instead(card_db):
    callbacks = _Pitcher(exile=False)
    game = _game(callbacks)
    forests = [_put(game, card_db, "Forest", "battlefield") for _ in range(4)]
    _put(game, card_db, "Ornithopter", "battlefield", idx=1)
    vigor = _put(game, card_db, "Force of Vigor", "hand")     # {2}{G}{G}
    elf = _put(game, card_db, "Llanowar Elves", "hand")
    assert game.cast_spell(0, vigor)
    assert callbacks.asked == [True]          # the mana cost was payable
    assert all(f.tapped for f in forests)
    assert elf in game.players[0].hand


def test_the_controller_picks_the_card_the_alternative_cost_exiles(card_db):
    game = _game(_Pitcher(pick=lambda candidates: candidates[1]))
    _put(game, card_db, "Ornithopter", "battlefield", idx=1)
    vigor = _put(game, card_db, "Force of Vigor", "hand")
    first = _put(game, card_db, "Llanowar Elves", "hand")
    second = _put(game, card_db, "Tarmogoyf", "hand")
    assert game.cast_spell(0, vigor)
    assert second.zone == "exile" and first in game.players[0].hand


def test_declining_the_card_with_no_mana_casts_nothing(card_db):
    game = _game(_Pitcher(pick=lambda candidates: None))
    _put(game, card_db, "Ornithopter", "battlefield", idx=1)
    vigor = _put(game, card_db, "Force of Vigor", "hand")
    elf = _put(game, card_db, "Llanowar Elves", "hand")
    assert not game.cast_spell(0, vigor)
    assert vigor in game.players[0].hand and elf in game.players[0].hand


# ── The AI's choice ───────────────────────────────────────────────────

def test_the_ai_pays_the_mana_cost_when_it_can_and_a_card_when_it_cannot(
        card_db):
    """A card in hand outlasts the turn; mana spent on the opponent's
    turn untaps on the next."""
    from ai.discard_advisor import exile_instead_of_paying
    game = _game(DefaultCallbacks())
    vigor = _put(game, card_db, "Force of Vigor", "hand")
    assert exile_instead_of_paying(game, 0, vigor, can_pay_mana=True) is False
    assert exile_instead_of_paying(game, 0, vigor, can_pay_mana=False) is True
