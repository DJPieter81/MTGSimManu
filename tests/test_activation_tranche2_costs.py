"""Tranche 2: sacrifice-self and pay-life become PAYABLE activation costs.

Tranche 1 parsed these cost items but marked them `unpayable`, making the
ability visible-but-refused. Measured against the 25 registered decks (after
the pattern repair in 7246c14): 48 abilities were blocked, and 32 of them by
exactly these two cost items — the horizon-land shape ("{1}, {T}, Sacrifice
this land: Draw a card") and the pay-life draw engine ("Pay 7 life: Draw seven
cards"). This tranche is a payer ADDITION: the parser's classification work is
reused, no re-parse.

Rules pinned:
  * CR 118.4 — life can be paid only if the life total is at least the amount.
    Paying down to exactly 0 is legal (the SBA loss is a separate event).
  * A sacrifice-self cost is inherently self-limiting (the source leaves), so
    it satisfies the "no free repeatable ability" rule the same way a tap cost
    does.
  * Paying a cost for an effect the resolver cannot execute is strictly worse
    than refusing — so an ability whose effect kind is unsupported is refused
    UP FRONT, before any cost is charged. (Previously the cost would be paid
    and resolution would record an unhandled effect: cost wasted.)
  * The sacrifice happens as a COST (CR 602.2b): the permanent is gone before
    resolution, and the ability still resolves.

Rules-phrased; card names are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.activation import ActivationManager
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import parse_activation_cost

_DB = CardDatabase()


def _add(game, name, controller=0, zone="battlefield"):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game(n_islands=4):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Eldrazi Tron"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_islands):
        _add(game, "Island")
    for _ in range(8):
        c = _add(game, "Island", 0, "library")
    return game


def _ability(mana=0, tap=False, life=0, sac=False,
             kind=ActivationEffectKind.DRAW_N, amount=1):
    return ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(generic=mana), tap_self=tap,
                            life=life, sacrifice_self=sac),
        effect_text="Draw a card.", effect_kind=kind, amount=amount)


def _host(game, ability, name="Wall of Omens"):
    perm = _add(game, name)
    perm.template.activated_abilities = [ability]
    return perm


# ── parsing: the two cost items become structured, not unpayable ──────

def test_pay_life_cost_parses_as_payable():
    cost = parse_activation_cost("Pay 2 life")
    assert cost is not None
    assert cost.life == 2, f"expected life=2, got {cost.life}"
    assert "pay_life" not in cost.unpayable and cost.unpayable == (), (
        f"pay-life is a tranche-2 payable cost, got unpayable={cost.unpayable}")


def test_sacrifice_self_cost_parses_as_payable():
    cost = parse_activation_cost("{1}, {T}, Sacrifice this land")
    assert cost is not None
    assert cost.sacrifice_self is True
    assert cost.tap_self is True
    assert cost.mana.cmc == 1
    assert cost.unpayable == (), (
        f"sacrifice-self is a tranche-2 payable cost, got {cost.unpayable}")


def test_multi_victim_sacrifice_is_still_unpayable():
    """Single-victim sacrifice graduated in tranche 3; sacrificing SEVERAL
    permanents needs a choice shape no tranche makes yet — it must stay
    refused, not silently sacrifice something."""
    cost = parse_activation_cost("Sacrifice two creatures")
    assert cost is not None and cost.unpayable, (
        "multi-victim sacrifice requires choosing several victims; it stays "
        "unpayable")


# ── legality ──────────────────────────────────────────────────────────

def test_pay_life_refused_when_life_is_insufficient():
    """CR 118.4 — cannot pay more life than you have."""
    game = _game()
    ab = _ability(life=7)
    perm = _host(game, ab)
    game.players[0].life = 6
    assert not ActivationManager.can_activate(game, 0, perm, ab)
    game.players[0].life = 7
    assert ActivationManager.can_activate(game, 0, perm, ab), (
        "paying down to exactly 0 is rules-legal; suicide-avoidance is the "
        "AI's job, not the engine's")


def test_sacrifice_self_satisfies_the_no_free_repeatable_rule():
    """A sacrifice-self cost is self-limiting — the source leaves play."""
    game = _game()
    ab = _ability(mana=0, tap=False, sac=True)
    perm = _host(game, ab)
    assert ActivationManager.can_activate(game, 0, perm, ab), (
        "zero-mana, no-tap is fine when the cost consumes the source itself")


def test_unsupported_effect_kind_is_refused_before_any_cost_is_paid():
    """Paying a cost for a no-op resolution is strictly worse than refusing."""
    game = _game()
    ab = _ability(mana=1, kind=ActivationEffectKind.UNCLASSIFIED)
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab), (
        "an effect the resolver cannot execute must be refused up front — "
        "otherwise the cost is charged and nothing happens")


# ── payment ───────────────────────────────────────────────────────────

def test_pay_life_deducts_life_and_the_ability_resolves():
    game = _game()
    ab = _ability(life=3)
    perm = _host(game, ab)
    hand = len(game.players[0].hand)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    assert game.players[0].life == 20 - 3, (
        f"life must be paid as a cost, got {game.players[0].life}")
    game.resolve_stack()
    assert len(game.players[0].hand) == hand + 1, "the draw still resolves"


def test_sacrifice_self_pays_by_leaving_the_battlefield_yet_still_resolves():
    """CR 602.2b: costs are paid on activation; the ability is independent of
    its source and resolves even though the source is gone."""
    game = _game()
    ab = _ability(mana=1, tap=True, sac=True)
    perm = _host(game, ab)
    hand = len(game.players[0].hand)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    assert perm.zone != "battlefield", (
        "the sacrifice is part of the COST — the permanent leaves at "
        "activation time, not at resolution")
    assert perm not in game.players[0].battlefield
    game.resolve_stack()
    assert len(game.players[0].hand) == hand + 1, (
        "the ability resolves independently of its departed source")


# ── end-to-end: the horizon-land shape is enumerable ──────────────────

def test_horizon_land_shape_is_enumerated_and_executes():
    """'{1}, {T}, Sacrifice this land: Draw a card' — the registered-deck
    shape this tranche unlocks (32 of the 48 blocked abilities)."""
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game

    game = _game()
    land = _add(game, "Fiery Islet")
    draw_abs = [a for a in (land.template.activated_abilities or [])
                if a.effect_kind is ActivationEffectKind.DRAW_N]
    assert draw_abs, (
        "fixture premise: the horizon land's draw line must parse as DRAW_N "
        f"with a payable cost; got {land.template.activated_abilities}")
    ab = draw_abs[0]
    assert ab.cost.sacrifice_self and ab.cost.unpayable == (), (
        f"fixture premise: cost fully payable, got {ab.cost}")

    assert ActivationManager.can_activate(game, 0, land, ab), (
        "the engine must permit the horizon-land cash-in")
    snap = snapshot_from_game(game, 0)
    cands = activation_candidates(game, 0, snap)
    assert any(p.instance_id == land.instance_id for p, *_ in cands), (
        "the AI must enumerate the horizon-land draw as a candidate play")


def test_flooded_board_chooses_the_horizon_land_cash_in():
    """Sign regression: the holdback adjustment is ADDED, never subtracted.

    `_holdback_penalty` returns a signed value — negative when open mana has a
    defensive use, POSITIVE when holding mana serves nothing. Subtracting it
    (as an earlier revision did) inverts both branches: on a flooded board
    with an empty hand, the "spend it" bonus became a killing penalty and no
    activation ever survived scoring. This pins the behaviour, not the sign
    itself: eight lands, empty hand, nothing else to do — cashing the horizon
    land for a card must be the chosen play.
    """
    from ai.ev_player import EVPlayer

    game = _game(n_islands=0)
    p = game.players[0]
    p.deck_name = "Boros Energy"
    for _ in range(7):
        _add(game, "Mountain")
    islet = _add(game, "Fiery Islet")
    for _ in range(10):
        _add(game, "Mountain", 0, "library")
    ai = EVPlayer(player_idx=0, deck_name="Boros Energy",
                  rng=random.Random(0))
    decision = ai.decide_main_phase(game)
    assert decision is not None, (
        "with a flooded board, an empty hand and a cashable horizon land, "
        "PASS is strictly worse than drawing a card")
    action, card, _t = decision
    assert action == "activate" and card.instance_id == islet.instance_id, (
        f"expected the horizon-land cash-in, got {action} {card.name}")
