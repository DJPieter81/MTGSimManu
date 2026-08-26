"""A "counter unless its controller pays {N}" response must respect payability.

A rational opponent who has already sunk a spell's cost pays a counter-tax
whenever able (the payer-side EV is modelled in
`ai/ev_evaluator.project_counter_tax_payment` and the engine offers the
payment via the 1a counter-tax framework). Firing a tax counter while the
targeted spell's controller has untapped mana >= the tax therefore has an
expected neutralization of ~0: the caster burns a card plus real mana and the
spell resolves anyway.

Live bug this pins (docs/diagnostics/2026-08-26_decider_loss_root_cause.md):
the counterspell candidate loop in `ai/response.py` read
`counter_target_kind` and effective cost but never `counter_tax_amount`, so a
soft counter was scored identically to a hard counter. Replay evidence: seed
54500 game 3, control at 3 life fired two tax counters into an opponent with
four untapped lands; both taxes were paid; six mana, two cards, and two shock
life bought nothing.

Rule under test: a tax counter is withheld when the targeted spell's
controller can afford the tax, and still fired when they cannot. A hard
counter (tax 0) is unaffected. Card names below are fixture carriers only —
the mechanic is the parsed `counter_tax_amount` field vs the opponent's
untapped mana capacity.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.stack import StackItem, StackItemType
from ai.ev_player import EVPlayer

_DB = CardDatabase()


def _add(game, name, controller, zone, tapped=False):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = tapped
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game(counter_name, opp_untapped_lands, opp_tapped_lands=0):
    game = GameState(rng=random.Random(0))
    game.active_player = 1
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    me, opp = game.players[0], game.players[1]
    me.deck_name = "4/5c Control"
    opp.deck_name = "Domain Zoo"
    # Plain basics so the counter is always castable (see the sibling
    # counterspell-vs-ability test for why shocklands break the fixture).
    for _ in range(5):
        _add(game, "Island", 0, "battlefield")
    for _ in range(opp_untapped_lands):
        _add(game, "Mountain", 1, "battlefield")
    for _ in range(opp_tapped_lands):
        _add(game, "Mountain", 1, "battlefield", tapped=True)
    counter = _add(game, counter_name, 0, "hand")
    assert "counterspell" in counter.template.tags
    # Threat on the stack: a resolved-body creature spell (same threat the
    # sibling test uses to prove the counter gate opens).
    threat = _add(game, "Territorial Kavu", 1, "hand")
    threat.zone = "stack"
    game.players[1].hand.remove(threat)
    item = StackItem(item_type=StackItemType.SPELL, source=threat,
                     controller=1)
    game.stack.items.append(item)
    assert game.can_cast(0, counter), (
        "fixture premise: the counter is castable — otherwise the "
        "withhold cases pass vacuously")
    return game, item, counter


def _decide(game, item):
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control",
                  rng=random.Random(0))
    return ai.decide_response(game, item)


def test_tax_counter_withheld_when_the_controller_can_pay_the_tax():
    """Opponent has untapped mana >= the tax: expected neutralization ~0,
    the tax counter must not fire."""
    game, item, counter = _game("Mystical Dispute", opp_untapped_lands=4)
    assert counter.template.counter_tax_amount > 0, (
        "fixture premise: this is a tax counter")
    assert _decide(game, item) is None, (
        "a tax counter fired into a payable tax burns a card and mana for "
        "nothing — the opponent pays from idle mana and the spell resolves")


def test_tax_counter_fires_when_the_controller_cannot_pay():
    """Opponent tapped out: the tax cannot be paid, the soft counter is a
    hard counter and must still be offered."""
    game, item, counter = _game("Mystical Dispute",
                                opp_untapped_lands=0, opp_tapped_lands=4)
    assert counter.template.counter_tax_amount > 0
    assert _decide(game, item) is not None, (
        "with the tax unpayable the counter is fully live and the existing "
        "threat gate already approves this threat (see sibling test)")


def test_hard_counter_unaffected_by_opponent_open_mana():
    """Control case: a tax-0 counter fires regardless of opponent mana."""
    game, item, counter = _game("Counterspell", opp_untapped_lands=4)
    assert counter.template.counter_tax_amount == 0
    assert _decide(game, item) is not None, (
        "a hard counter's value does not depend on the opponent's mana")
