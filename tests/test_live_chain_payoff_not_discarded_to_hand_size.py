"""A live chain payoff is not discarded to hand size as flashback fuel.

# Mechanic the test names (engine rule, not a card)

When discarding down to hand size, the self-discard scorer treats a
flashback-tagged card as "happy in the graveyard" and bins it — you can
flash it back later, so binning loses nothing. That reasoning is WRONG
for a card that GRANTS flashback to the whole graveyard (the
Past-in-Flames pattern): its value comes from being CAST FROM HAND to
replay the yard as a chain payoff. Binning it discards a live line
piece rather than fuel — the "execute the lethal line, do not discard
its pieces" failure surfaced in Storm vs Azorius Control (WST) s55501
T4, where the graveyard-replay payoff was Wished into hand and then
discarded to hand size while spare lands were available.

Rule, phrased without naming a card: a card that grants flashback to
the graveyard is a payoff to cast, not fuel to bin — it must NOT
receive the flashback-target discard bonus, so it outranks true excess
(a spare land) for retention.

# Why this is a class fix

Detection is the typed field `grants_flashback_to_gy_spells`, populated
at DB load — no runtime oracle parse, no card names. The class is every
card that hands the graveyard back to its controller (Past in Flames
and any future reprint of the pattern). Past in Flames below is only a
fixture carrier.
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


def test_graveyard_replay_payoff_retained_over_excess_land(card_db):
    """A card that grants flashback to the graveyard is a payoff cast
    from hand; discarding to hand size must bin a spare land instead."""
    # Sanity: the fixture card really carries the typed flag the rule
    # keys on — otherwise the test would pass vacuously.
    pif_tmpl = card_db.get_card("Past in Flames")
    assert getattr(pif_tmpl, "grants_flashback_to_gy_spells", False), (
        "fixture invariant broken: Past in Flames should carry "
        "grants_flashback_to_gy_spells=True at DB load"
    )

    game = GameState(rng=random.Random(0))
    # Four lands already on the battlefield → the extra Mountain in hand
    # is genuine excess.
    for _ in range(4):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    hand = [
        _add(game, card_db, "Past in Flames", controller=0, zone="hand"),
        _add(game, card_db, "Mountain", controller=0, zone="hand"),
        _add(game, card_db, "Mountain", controller=0, zone="hand"),
    ]

    pick = choose_discard(game, 0, hand, self_discard=True)
    assert pick is not None and pick.name != "Past in Flames", (
        f"self-discard binned {pick.name if pick else None} — a "
        f"graveyard-replay chain payoff was discarded to hand size as "
        f"flashback fuel while a spare land was available. It is a "
        f"payoff to cast, not fuel to bin."
    )
    assert pick.template.is_land


def test_plain_flashback_card_still_binned_over_nonfuel(card_db):
    """Regression anchor: a plain flashback card (does NOT grant
    flashback to the graveyard) is still happy in the yard — the fix
    only exempts graveyard-replay payoffs, not ordinary flashback."""
    faithful = card_db.get_card("Faithful Mending")
    if faithful is None or 'flashback' not in getattr(faithful, 'tags', set()):
        import pytest
        pytest.skip("Faithful Mending unavailable or not flashback-tagged")
    assert not getattr(faithful, "grants_flashback_to_gy_spells", False)

    game = GameState(rng=random.Random(0))
    hand = [
        _add(game, card_db, "Faithful Mending", controller=0, zone="hand"),
        # A non-fuel companion the scorer has no reason to bin.
        _add(game, card_db, "Ral, Monsoon Mage // Ral, Leyline Prodigy",
             controller=0, zone="hand"),
    ]
    pick = choose_discard(game, 0, hand, self_discard=True)
    assert pick is not None and pick.name == "Faithful Mending", (
        f"self-discard picked {pick.name if pick else None}; a plain "
        f"flashback card should still be the preferred bin (it wants to "
        f"be in the graveyard) — the exemption is only for cards that "
        f"grant flashback to the whole graveyard."
    )
