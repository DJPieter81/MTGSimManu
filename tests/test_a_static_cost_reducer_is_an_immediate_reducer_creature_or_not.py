"""A permanent whose static ability reduces its controller's spell costs
reduces them from the moment it is on the battlefield, creature or not
(AI keep, from CR 302.6 and 601.2f).

Summoning sickness stops a creature attacking and its {T} abilities
(CR 302.6); it never stops a static ability. So the keep's storm-chain
backup gate, which waives the ritual + cantrip + finisher backup for a hand
holding a cost reducer, counts a creature with a typed static spell-cost
reduction (`CardTemplate.cost_reduction_rules`, unit CR) exactly as it
counts an artifact.

The defect (docs/diagnostics/2026-10-13_ruby_storm_ritual_hold_root_cause.md,
G2): the gate counted only non-creature reducers. Its comment said a
creature reducer must "be cast AND flipped/attacked". Ruby Storm sent back
a two-land seven holding Ral, Monsoon Mage ("no cost reducer") and kept a
one-land six.

Card names are fixture carriers; the policy is the deck's own gameplan.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance

DECK = "Ruby Storm"
NO_REDUCER = "no cost reducer and no ritual+cantrip+finisher backup"


def _hand(card_db, names):
    return [CardInstance(template=card_db.get_card(n), owner=0, controller=0,
                         instance_id=i + 1, zone="hand")
            for i, n in enumerate(names)]


def _decider():
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name=DECK,
                    rng=random.Random(0))._mulligan_decider


def test_a_creature_with_a_static_spell_cost_reduction_waives_the_backup(
        card_db):
    """The replay's G2 seven: two lands, Ral, a ritual, three spells."""
    d = _decider()
    d.decide(_hand(card_db, [
        "Arid Mesa", "Sacred Foundry",
        "Ral, Monsoon Mage // Ral, Leyline Prodigy", "Desperate Ritual",
        "Valakut Awakening // Valakut Stoneforge", "Hex Magic",
        "Hex Magic"]), 7)
    assert d.last_reason != NO_REDUCER


def test_a_hand_with_no_reducer_still_needs_its_backup(card_db):
    """The same seven with a vanilla creature in Ral's place."""
    d = _decider()
    keep = d.decide(_hand(card_db, [
        "Arid Mesa", "Sacred Foundry", "Grizzly Bears", "Desperate Ritual",
        "Valakut Awakening // Valakut Stoneforge", "Hex Magic",
        "Hex Magic"]), 7)
    assert not keep and d.last_reason == NO_REDUCER
