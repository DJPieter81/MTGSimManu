"""A graveyard is unsafe only when something can actually EMPTY it.

`ai.discard_advisor` refuses to bin plan resources into the graveyard
when the opponent has "graveyard hate" on the battlefield — correct in
principle: a graveyard that is about to be exiled cannot hold a
reanimation target, so binning loses the card instead of relocating it.

The predicate it asked was `CardTemplate.has_graveyard_hate`, the same
deliberately BROAD sideboard-advice field that the cast-prevention gate
was misusing: it matches any oracle mentioning "exile … graveyard",
which is 446 Modern permanents. Murktide Regent (delve), Psychic Frog
(an exile-cards-as-a-cost discard outlet), Tarmogoyf-style bodies and
Seasoned Pyromancer all satisfy it while posing no threat whatsoever to
a graveyard. A reanimator deck therefore stopped binning its own payoffs
across the most common creatures in the format.

The threat is a MECHANISM, and after the graveyard-exile classification
it is nameable: an opposing permanent threatens the graveyard when it
carries an activated ability whose parsed effect kind is
EXILE_FROM_GRAVEYARD, or when it carries the continuous "cards that
would go to a graveyard are exiled instead" replacement (Leyline of the
Void / Rest in Peace family), or when it bans casting from graveyards
outright. A card that merely CONSUMES its own graveyard is not a threat
to ours.

Card names in test bodies are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.discard_advisor import _graveyard_is_safe
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase

_DB = CardDatabase()


def _add(game, name, controller=1, zone="battlefield"):
    tmpl = _DB.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 4
    return game


def test_an_empty_opposing_board_leaves_the_graveyard_safe():
    assert _graveyard_is_safe(_game(), 0) is True


def test_a_permanent_that_can_exile_our_graveyard_makes_it_unsafe():
    """The activated graveyard-exile class IS the threat."""
    for name in ("Tormod's Crypt", "Nihil Spellbomb", "Withered Wretch",
                 "Soul-Guide Lantern"):
        game = _game()
        _add(game, name)
        assert _graveyard_is_safe(game, 0) is False, name


def test_a_permanent_that_exiles_cards_on_the_way_to_the_graveyard_is_a_threat():
    """The continuous replacement family never lets the card arrive."""
    for name in ("Leyline of the Void", "Rest in Peace"):
        game = _game()
        _add(game, name)
        assert _graveyard_is_safe(game, 0) is False, name


def test_a_permanent_that_bans_graveyard_casting_is_a_threat():
    game = _game()
    _add(game, "Grafdigger's Cage")
    assert _graveyard_is_safe(game, 0) is False


def test_a_creature_that_merely_consumes_its_own_graveyard_is_not_a_threat():
    """Delve, escape, self-exile discard outlets and graveyard-scaling
    bodies all mention exiling a graveyard and threaten nothing of ours.
    These are among the most-played creatures in the format, so treating
    them as hate switched the binning plan off almost permanently."""
    for name in ("Murktide Regent", "Psychic Frog", "Tarmogoyf",
                 "Seasoned Pyromancer", "Territorial Kavu"):
        game = _game()
        _add(game, name)
        assert _graveyard_is_safe(game, 0) is True, name


def test_our_own_hate_permanent_does_not_make_our_graveyard_unsafe():
    """The question is what the OPPONENT can do to us."""
    game = _game()
    _add(game, "Tormod's Crypt", controller=0)
    assert _graveyard_is_safe(game, 0) is True


def test_the_threat_predicate_is_far_narrower_than_the_advice_predicate():
    """DB-wide: the mechanism test must not be a rename of the broad one."""
    from ai.discard_advisor import _threatens_graveyards

    narrow = {n for n, t in _DB.cards.items() if _threatens_graveyards(t)}
    broad = {n for n, t in _DB.cards.items() if t.has_graveyard_hate}
    assert narrow, "the predicate must still catch real hate"
    assert len(narrow) < len(broad) / 4, (len(narrow), len(broad))
