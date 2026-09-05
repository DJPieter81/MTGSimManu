"""Cycling a creature earns the "future reanimation target" credit only
when something in the deck can actually return THAT card.

# Mechanic the tests name

`_score_cycling` credited every cycled creature as reanimation equity
whenever the deck had any oracle text returning a creature from a
graveyard to the battlefield.  A self-returning creature ("you may
return this card from your graveyard to the battlefield" — the
Vengevine / Phoenix shape) satisfied that scan, so a deck built around
one credited cycling its OWN payoff at +6 (Hollow One vs Domain Zoo
s50000: "cycle: Hollow One" +7.8 against +1.0 for a two-drop; the
payoff was cycled on turn 5 and never cast).  The rule: the credit is
the value of a returner in the deck that can take this card — a
targeted returner by its parsed graveyard requirement (legendary /
any / mana-value ceiling), an untargeted mass return by its scope; a
returner that only returns itself is not a path for anything else.

Class: every cycling creature in every deck with a self-returning
creature; the shared predicate is the one the self-discard-outlet line
already uses.  Card names below are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = False
        game.players[controller].battlefield.append(c)
    elif zone == "library":
        game.players[controller].library.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _cycle_ev(card_db, cycler_name, companion_names, deck_name="Hollow One"):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(3):
        _put(game, card_db, "Mountain", 0, "battlefield")
    cycler = _put(game, card_db, cycler_name, 0, "hand")
    for n in companion_names:
        _put(game, card_db, n, 0, "hand")
    for _ in range(6):
        _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "library")
    ai = EVPlayer(player_idx=0, deck_name=deck_name, rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    return ai._score_cycling(cycler, snap, game, game.players[0],
                             game.players[1])


# ── The predicate ────────────────────────────────────────────────────


def test_a_self_returning_creature_is_not_a_returner_for_other_cards(card_db):
    from ai.card_classes import deck_can_return
    hollow = card_db.get_card("Hollow One")
    vengevine = card_db.get_card("Vengevine")
    assert not deck_can_return(hollow, [vengevine])


def test_a_targeted_returner_counts_only_for_cards_its_requirement_admits(card_db):
    from ai.card_classes import deck_can_return
    goryos = card_db.get_card("Goryo's Vengeance")      # legendary only
    rites = card_db.get_card("Unburial Rites")           # any creature
    hollow = card_db.get_card("Hollow One")               # not legendary
    griselbrand = card_db.get_card("Griselbrand")
    assert not deck_can_return(hollow, [goryos])
    assert deck_can_return(griselbrand, [goryos])
    assert deck_can_return(hollow, [rites])


def test_an_untargeted_mass_return_counts_for_any_creature(card_db):
    from ai.card_classes import deck_can_return
    living_end = card_db.get_card("Living End")
    hollow = card_db.get_card("Hollow One")
    assert deck_can_return(hollow, [living_end])
    # Not for a noncreature.
    assert not deck_can_return(card_db.get_card("Lightning Bolt"), [living_end])


def test_reminder_text_never_supplies_a_target_zone(card_db):
    """A flashback spell whose real target is a battlefield creature
    parses that target on the battlefield; the reminder "(You may cast
    this card from your graveyard …)" is not a graveyard-target hint —
    and so the spell is not a returner for anything."""
    from ai.card_classes import deck_can_return
    from engine.target_solver import parse
    offense = card_db.get_card("Practiced Offense")
    reqs = parse(offense.oracle_text)
    assert reqs and all(r.zone != "graveyard" for r in reqs)
    assert not deck_can_return(card_db.get_card("Hollow One"), [offense])


# ── The cycling scorer ───────────────────────────────────────────────


def test_cycling_a_creature_charges_the_body_it_throws_away_by_castability(card_db):
    """Cycling spends the card.  The same 4/4 costs more to cycle when
    the mana to deploy it is there than when it is not."""
    from ai.ev_evaluator import EVSnapshot
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(5):
        _put(game, card_db, "Mountain", 0, "battlefield")
    hollow = _put(game, card_db, "Hollow One", 0, "hand")
    ai = EVPlayer(player_idx=0, deck_name="Hollow One", rng=random.Random(0))
    me, opp = game.players[0], game.players[1]
    rich = snapshot_from_game(game, 0)                      # 5 mana: castable
    poor = rich.model_copy(update={"my_mana": 1})           # 1 mana: not
    assert ai._score_cycling(hollow, rich, game, me, opp) < \
        ai._score_cycling(hollow, poor, game, me, opp)


def test_cycling_a_creature_earns_no_reanimation_credit_from_a_self_returner(card_db):
    with_self_returner = _cycle_ev(card_db, "Hollow One", ["Vengevine"])
    alone = _cycle_ev(card_db, "Hollow One", [])
    assert with_self_returner == alone


def test_cycling_a_creature_earns_the_credit_from_a_returner_that_can_take_it(card_db):
    with_rites = _cycle_ev(card_db, "Hollow One", ["Unburial Rites"])
    alone = _cycle_ev(card_db, "Hollow One", [])
    assert with_rites > alone
    # A legendary-only returner does nothing for a non-legendary body.
    with_goryos = _cycle_ev(card_db, "Hollow One", ["Goryo's Vengeance"])
    assert with_goryos == alone
