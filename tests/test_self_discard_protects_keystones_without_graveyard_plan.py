"""Self-discard must not bin declared keystones of a no-graveyard deck.

`choose_discard`'s self-discard scorer has a generic fallback that
treats EVERY high-CMC creature as a reanimation target worth binning.
For a deck whose gameplan declares that creature as a keystone
(critical_pieces / mulligan_keys) and declares NO graveyard
FILL_RESOURCE plan, the fallback throws away the deck's win condition:
replay `--bo3 "Amulet Titan" "Boros Energy" -s 50000` T5 discarded BOTH
copies of the deck's 6-drop payoff to hand size while spare lands were
available (diagnostic
docs/diagnostics/2026-07-06_amulet_titan_root_cause.md).

Rule: the big-creature "good to bin" bonus applies only when the deck
declares a graveyard plan (the existing GV-1 shape) or the creature is
not a declared keystone. Reanimator decks keep the current behavior —
binning the declared payoff IS their plan.
"""
from __future__ import annotations

import random

from ai.discard_advisor import choose_discard
from engine.cards import CardInstance
from engine.game_state import GameState


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
    getattr(game.players[controller], zone if zone != "battlefield"
            else "battlefield").append(card)
    return card


def _hand_with_payoff_and_spare_lands(card_db, deck_name, payoff):
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = deck_name
    for _ in range(5):
        _add(game, card_db, "Forest", controller=0, zone="battlefield")
    hand = [
        _add(game, card_db, payoff, controller=0, zone="hand"),
        _add(game, card_db, "Forest", controller=0, zone="hand"),
        _add(game, card_db, "Forest", controller=0, zone="hand"),
    ]
    return game, hand


def test_keystone_payoff_kept_when_deck_has_no_graveyard_plan(card_db):
    """Amulet-shaped deck: the declared 6-drop keystone must not be
    the discard pick while spare lands are in hand."""
    game, hand = _hand_with_payoff_and_spare_lands(
        card_db, "Amulet Titan", "Primeval Titan")
    pick = choose_discard(game, 0, hand, self_discard=True)
    assert pick is not None and pick.name != "Primeval Titan", (
        "self-discard binned the deck's declared keystone payoff while "
        "spare lands were available — the big-creature fallback ignores "
        "gameplan keystones on no-graveyard-plan decks"
    )
    assert pick.template.is_land


def test_reanimator_still_bins_its_declared_payoff(card_db):
    """Negative pin: a deck declaring the graveyard FILL_RESOURCE plan
    keeps the current behavior — binning the fat payoff IS the plan."""
    game, hand = _hand_with_payoff_and_spare_lands(
        card_db, "Goryo's Vengeance", "Griselbrand")
    pick = choose_discard(game, 0, hand, self_discard=True)
    assert pick is not None and pick.name == "Griselbrand", (
        f"reanimator self-discard picked {pick.name if pick else None}; "
        "the GV-1 reanimation-fuel behavior must be unchanged"
    )
