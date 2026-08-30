"""Loyalty abilities resolve their printed effect, or cost nothing.

The rule these tests pin, phrased on the mechanic (CR 606 + the
`ActivationManager.can_activate` rule-9b invariant):

    A planeswalker loyalty ability's effect resolves from its printed
    oracle text through the same oracle-driven effect resolver every
    other effect surface uses; an effect the resolver cannot execute is
    REFUSED BEFORE the loyalty is paid, and is not offered to the AI as
    a choice.

Before this file, `engine/planeswalker_manager.py::activate_planeswalker`
deducted loyalty FIRST and then dispatched through a hand-written chain
of substring tests against invented vocabulary ("bounce", "brainstorm",
"cast sorceries as flash" — phrases that occur on zero cards in the
22,470-card pool).  83% of parsed loyalty abilities matched no branch:
the cost was paid and nothing happened.  Root cause and the measured
A/B live in
`docs/diagnostics/2026-08-30_azorius_planeswalker_loyalty_noop_root_cause.md`.

No card names in the assertions — the fixtures build synthetic
planeswalker templates from printed-shape oracle text, so the tests
describe the mechanic class (every "[-N]: Return target ... to its
owner's hand" walker) rather than any one card.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import (CardInstance, CardTemplate, CardType,
                          LoyaltyEffectKind)
from engine.mana import ManaCost
from engine.game_state import GameState, Phase
from engine.planeswalker_manager import PlaneswalkerManager


# ── fixtures — synthetic templates, printed oracle shapes ────────────

def _walker_template(name: str, oracle: str, loyalty: int = 4) -> CardTemplate:
    """A minimal planeswalker template carrying printed loyalty text."""
    return CardTemplate(
        name=name,
        mana_cost=ManaCost(generic=2, blue=1),
        card_types=[CardType.PLANESWALKER],
        oracle_text=oracle,
        loyalty=loyalty,
    )


def _creature_template(name: str, power: int = 4,
                       toughness: int = 4) -> CardTemplate:
    return CardTemplate(
        name=name,
        mana_cost=ManaCost(generic=4),
        card_types=[CardType.CREATURE],
        power=power,
        toughness=toughness,
        oracle_text="",
    )


def _put(game: GameState, template: CardTemplate, controller: int,
         zone: str) -> CardInstance:
    card = CardInstance(
        template=template, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "graveyard":
        game.players[controller].graveyard.append(card)
    else:
        game.players[controller].library.append(card)
    return card


def _fresh_game() -> GameState:
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "walker_side"
    game.players[1].deck_name = "board_side"
    game.turn_number = 6
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


# ── 1. the effect actually happens ───────────────────────────────────

def test_loyalty_ability_returning_a_permanent_moves_it_to_owners_hand():
    """"[-N]: Return up to one target <types> to its owner's hand."

    The permanent leaves the battlefield and arrives in its owner's
    hand, and the printed draw rider draws.  Pre-fix this ability
    matched no dispatch branch: the loyalty was spent, the permanent
    stayed on the battlefield and no card was drawn.
    """
    game = _fresh_game()
    walker = _put(game, _walker_template(
        "Bounce Walker",
        "[+1]: Until your next turn, you may cast sorcery spells as "
        "though they had flash.\n"
        "[-3]: Return up to one target artifact, creature, or "
        "enchantment to its owner's hand. Draw a card."),
        0, "battlefield")
    walker.loyalty_counters = 4

    threat = _put(game, _creature_template("Big Body"), 1, "battlefield")
    # A library to draw from — the printed rider must be able to resolve.
    for i in range(3):
        _put(game, _creature_template(f"Filler {i}"), 0, "library")
    hand_before = len(game.players[0].hand)

    assert PlaneswalkerManager.activate_planeswalker(
        game, 0, walker, "minus") is True

    assert threat not in game.players[1].battlefield, (
        "the returned permanent must leave the battlefield")
    assert threat in game.players[1].hand, (
        "the returned permanent must arrive in its OWNER's hand")
    assert threat.zone == "hand"
    assert walker.loyalty_counters == 1, "the printed loyalty cost is paid"
    assert len(game.players[0].hand) == hand_before + 1, (
        "the printed draw rider must resolve too")


def test_loyalty_ability_returning_a_card_from_graveyard_reaches_hand():
    """The same mechanic in the graveyard-source direction:
    "[+N]: Return up to one target <type> card from your graveyard to
    your hand."  One rule, both printed source zones."""
    game = _fresh_game()
    walker = _put(game, _walker_template(
        "Recursion Walker",
        "[+1]: Return up to one target creature card from your "
        "graveyard to your hand.\n"
        "[-1]: This planeswalker deals 1 damage to any target."),
        0, "battlefield")
    walker.loyalty_counters = 2

    buried = _put(game, _creature_template("Buried Body"), 0, "graveyard")

    assert PlaneswalkerManager.activate_planeswalker(
        game, 0, walker, "plus") is True

    assert buried in game.players[0].hand
    assert buried not in game.players[0].graveyard
    assert walker.loyalty_counters == 3


# ── 2. refuse BEFORE paying ──────────────────────────────────────────

def test_loyalty_ability_the_resolver_cannot_execute_costs_no_loyalty():
    """An effect kind the resolver cannot execute must be refused
    BEFORE any loyalty is charged — the same invariant
    `ActivationManager.can_activate` rule 9b enforces for activated
    abilities.  Paying a cost for a no-op is strictly worse than
    refusing."""
    game = _fresh_game()
    walker = _put(game, _walker_template(
        "Unimplemented Walker",
        "[+1]: Until your next turn, you may cast sorcery spells as "
        "though they had flash.\n"
        "[-2]: Target opponent chooses a permanent they control and "
        "returns it to its owner's hand. Then they shuffle each "
        "nonland permanent they control into its owner's library."),
        0, "battlefield")
    walker.loyalty_counters = 4

    for slot in ("plus", "minus"):
        loyalty_before = walker.loyalty_counters
        assert PlaneswalkerManager.activate_planeswalker(
            game, 0, walker, slot) is False, (
            f"the {slot} ability's effect is not executable and must be "
            f"refused")
        assert walker.loyalty_counters == loyalty_before, (
            f"refusing the {slot} ability must not charge loyalty")


def test_refused_loyalty_ability_is_not_offered_to_the_chooser(card_db,
                                                              game_runner):
    """Refusal is not merely a resolution-time failure: an ability the
    resolver cannot execute must never be OFFERED, so the AI cannot
    pick it over a line that does something.  The walker below has one
    executable line and one dead one; the dead one is the AI's
    top-scoring choice by description, so a chooser that still sees it
    would spend the walker for nothing."""
    from ai.ev_player import EVPlayer

    game = _fresh_game()
    walker = _put(game, _walker_template(
        "Mixed Walker",
        # Dead line: an emblem shape the resolver does not execute.
        # Reads as pure card advantage to the description-driven
        # chooser, which is exactly why it must not be offered.
        "[+1]: You get an emblem with \"At the beginning of your "
        "upkeep, draw a card.\"\n"
        # Live line: a printed return-to-hand.
        "[-2]: Return target creature to its owner's hand."),
        0, "battlefield")
    walker.loyalty_counters = 3

    threat = _put(game, _creature_template("Big Body"), 1, "battlefield")

    ai = EVPlayer(0, card_db)
    ai._pw_activated_this_turn = set()
    game_runner._activate_planeswalkers(game, ai)

    assert threat in game.players[1].hand, (
        "the only executable line must be the one the AI is offered")
    assert walker.loyalty_counters == 1


def test_an_unaffordable_executable_line_does_not_fall_back_to_a_dead_one(
        card_db, game_runner):
    """The narrowing must survive the chooser's own fallback.

    When the only executable line is a minus the walker cannot yet
    afford, the ranking layer has nothing to rank and falls back to a
    fixed slot name — which is the DEAD line.  The engine must reject
    that answer rather than spend the walker's one activation per turn
    on a refusal.  Caught live: 71 unexecutable activations were still
    being offered across 20 real games after the menu was narrowed.
    """
    from ai.ev_player import EVPlayer

    game = _fresh_game()
    walker = _put(game, _walker_template(
        "Unaffordable Walker",
        # Dead line, loyalty-positive — the fallback slot.
        "[+1]: Until your next turn, you may cast sorcery spells as "
        "though they had flash.\n"
        # Live line, but it costs more loyalty than the walker has.
        "[-3]: Return target creature to its owner's hand."),
        0, "battlefield")
    walker.loyalty_counters = 1  # cannot pay the -3

    _put(game, _creature_template("Big Body"), 1, "battlefield")

    ai = EVPlayer(0, card_db)
    ai._pw_activated_this_turn = set()
    game_runner._activate_planeswalkers(game, ai)

    assert walker.loyalty_counters == 1, (
        "an unaffordable executable line must not fall back to the dead "
        "one; nothing should have been activated")


def test_no_loyalty_line_executable_means_the_walker_is_left_alone(
        card_db, game_runner):
    """When NO line is executable the walker must not be activated at
    all — no loyalty spent, no SBA death for zero effect."""
    from ai.ev_player import EVPlayer

    game = _fresh_game()
    walker = _put(game, _walker_template(
        "Inert Walker",
        "[+1]: Until your next turn, you may cast sorcery spells as "
        "though they had flash.\n"
        "[-3]: Target opponent chooses a permanent they control and "
        "returns it to its owner's hand."),
        0, "battlefield")
    walker.loyalty_counters = 4

    ai = EVPlayer(0, card_db)
    ai._pw_activated_this_turn = set()
    game_runner._activate_planeswalkers(game, ai)

    assert walker.loyalty_counters == 4, (
        "no executable line means no activation and no loyalty spent")


# ── 3. classification is printed-text driven ─────────────────────────

def test_unsupported_rider_refuses_the_whole_loyalty_ability():
    """A return-to-hand whose printed text carries a rider the
    resolver does not implement is refused entirely rather than
    half-executed — the same "unsupported riders refuse the whole
    card" discipline `parse_x_creature_tutor` applies."""
    from engine.oracle_parser import parse_loyalty_abilities

    parsed = parse_loyalty_abilities(
        "[-3]: Return target nonland permanent to its owner's hand, "
        "then that player exiles a card from their hand.", 5)
    assert parsed["minus"].effect_kind is LoyaltyEffectKind.UNCLASSIFIED, (
        "the trailing exile clause is not implemented, so the whole "
        "ability must be refused")


def test_loyalty_abilities_are_classified_from_printed_oracle_text():
    """The classification comes from printed oracle text, so a
    planeswalker the engine has never seen classifies correctly with
    no registry entry of any kind."""
    from engine.oracle_parser import parse_loyalty_abilities

    parsed = parse_loyalty_abilities(
        "[+1]: Return up to one target land card from your graveyard "
        "to your hand.\n"
        "[-1]: This planeswalker deals 1 damage to any target.", 3)

    assert parsed["plus"].effect_kind is LoyaltyEffectKind.RETURN_TO_HAND
    assert parsed["plus"].cost == 1
    assert parsed["plus"].target is not None
    assert parsed["plus"].target.zone == "graveyard"
    assert parsed["minus"].effect_kind is LoyaltyEffectKind.DAMAGE
