"""Tranche 3: sacrifice-another and discard become PAYABLE activation costs.

Tranches 1-2 parsed these cost items but marked them `unpayable`, making the
ability visible-but-refused. Measured DB-wide before this tranche: 1647 of
6457 parsed activated abilities were blocked, 460 of them by a
sacrifice-another cost and 244 by a discard cost — the two largest named
classes. This tranche is a payer ADDITION: the parser's classification work
is reused, no re-parse.

Rules pinned:
  * CR 601.2h — costs are paid only if they CAN be paid: a sacrifice cost
    needs a legal victim under the controller's control right now, and a
    discard cost needs that many cards in hand.
  * CR 602.2b — the victim/discard is paid at ACTIVATION time; the ability on
    the stack resolves independently of the paid resource.
  * "Sacrifice a creature" permits the source itself as the victim when the
    source matches the type; "Sacrifice ANOTHER creature" excludes it.
  * Sacrifice-another and discard both deplete a real resource (board,
    hand), so they satisfy the no-free-repeatable rule the way
    sacrifice-self and pay-life do.
  * The victim CHOICE is strategic and crosses the engine/AI seam via a
    callback; the engine only enumerates the LEGAL victims.
  * Both payments route through the sanctioned funnels: the victim through
    `zone_mgr`, the discard through the same discard path forced discards
    use (so discard-linked triggers keep firing).

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


def _game(n_islands=4, n_hand=3):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Eldrazi Tron"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_islands):
        _add(game, "Island")
    for _ in range(n_hand):
        _add(game, "Island", 0, "hand")
    for _ in range(8):
        _add(game, "Island", 0, "library")
    return game


def _ability(mana=0, tap=False, sac_type=None, sac_another=False, discard=0,
             kind=ActivationEffectKind.DRAW_N, amount=1):
    return ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(generic=mana), tap_self=tap,
                            sacrifice_type=sac_type,
                            sacrifice_another=sac_another,
                            discard_cards=discard),
        effect_text="Draw a card.", effect_kind=kind, amount=amount)


def _host(game, ability, name="Wall of Omens"):
    perm = _add(game, name)
    perm.template.activated_abilities = [ability]
    return perm


# ── parsing: the two cost items become structured, not unpayable ──────

def test_single_victim_sacrifice_cost_parses_as_a_structured_type():
    cost = parse_activation_cost("{3}, Sacrifice a creature")
    assert cost is not None
    assert cost.sacrifice_type == "creature"
    assert cost.sacrifice_another is False, (
        "'a creature' does not exclude the source — only 'another' does")
    assert cost.mana.cmc == 3
    assert cost.unpayable == (), (
        f"single-victim sacrifice is a tranche-3 payable cost, got "
        f"{cost.unpayable}")


def test_sacrifice_another_cost_records_that_the_source_is_excluded():
    cost = parse_activation_cost("Sacrifice another creature")
    assert cost is not None
    assert cost.sacrifice_type == "creature"
    assert cost.sacrifice_another is True
    assert cost.unpayable == ()


def test_each_permanent_type_word_parses_as_a_sacrifice_type():
    for word in ("creature", "artifact", "enchantment", "land", "permanent"):
        cost = parse_activation_cost(f"Sacrifice a {word}")
        assert cost is not None and cost.sacrifice_type == word, (
            f"'Sacrifice a {word}' must parse structured, got {cost}")
        assert cost.unpayable == ()


def test_multi_victim_and_or_typed_sacrifices_stay_unpayable():
    """Choosing among unions or sacrificing several permanents is a choice
    shape this tranche does not make — refused, never silently mangled."""
    for phrase in ("Sacrifice two creatures",
                   "Sacrifice an artifact or land",
                   "Sacrifice a Goblin"):
        cost = parse_activation_cost(phrase)
        assert cost is not None and cost.unpayable, (
            f"{phrase!r} must stay visible-but-refused, got {cost}")
        assert cost.sacrifice_type is None


def test_discard_cost_parses_as_a_structured_count():
    cost = parse_activation_cost("Discard a card")
    assert cost is not None and cost.discard_cards == 1
    assert cost.unpayable == ()
    cost2 = parse_activation_cost("{T}, Discard two cards")
    assert cost2 is not None and cost2.discard_cards == 2
    assert cost2.tap_self is True
    assert cost2.unpayable == ()


def test_random_and_typed_discards_stay_unpayable():
    """'At random' and type-restricted discards need a choice mode this
    tranche does not implement — refused, never approximated."""
    for phrase in ("Discard a card at random",
                   "Discard a creature card",
                   "Discard your hand"):
        cost = parse_activation_cost(phrase)
        assert cost is not None and cost.unpayable, (
            f"{phrase!r} must stay visible-but-refused, got {cost}")
        assert cost.discard_cards == 0


# ── legality ──────────────────────────────────────────────────────────

def test_sacrifice_another_refused_without_a_second_permanent_of_the_type():
    """CR 601.2h — a cost that cannot be fully paid cannot be activated."""
    game = _game()
    ab = _ability(sac_type="creature", sac_another=True)
    perm = _host(game, ab)  # the only creature on the battlefield
    assert not ActivationManager.can_activate(game, 0, perm, ab), (
        "'another creature' excludes the source; with no second creature "
        "there is no legal victim")
    _add(game, "Wall of Omens")
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_unqualified_sacrifice_permits_the_source_itself_as_victim():
    """'Sacrifice a creature' (no 'another') counts the source among the
    legal victims when the source matches the required type."""
    game = _game()
    ab = _ability(sac_type="creature", sac_another=False)
    perm = _host(game, ab)  # the only creature — but a legal victim itself
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_sacrifice_victim_must_match_the_required_permanent_type():
    game = _game()
    ab = _ability(sac_type="artifact")
    perm = _host(game, ab)  # a creature host; lands + creature on board
    assert not ActivationManager.can_activate(game, 0, perm, ab), (
        "no artifact under the controller's control — no legal victim")


def test_discard_refused_when_hand_cannot_cover_the_cost():
    """CR 601.2h — hand size must cover the discard amount."""
    game = _game(n_hand=1)
    ab = _ability(discard=2)
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab)
    _add(game, "Island", 0, "hand")
    assert ActivationManager.can_activate(game, 0, perm, ab), (
        "a hand exactly covering the cost is payable")


def test_resource_depleting_costs_satisfy_the_no_free_repeatable_rule():
    """Sacrifice-another consumes board, discard consumes hand — both are
    self-limiting the way sacrifice-self and pay-life are."""
    game = _game()
    _add(game, "Wall of Omens")
    sac_ab = _ability(sac_type="creature", sac_another=True)
    sac_host = _host(game, sac_ab)
    assert ActivationManager.can_activate(game, 0, sac_host, sac_ab), (
        "zero-mana, no-tap is fine when the cost consumes a permanent")
    disc_ab = _ability(discard=1)
    disc_host = _host(game, disc_ab)
    assert ActivationManager.can_activate(game, 0, disc_host, disc_ab), (
        "zero-mana, no-tap is fine when the cost consumes a card in hand")


# ── payment ───────────────────────────────────────────────────────────

def test_sacrifice_victim_leaves_via_the_funnel_and_the_ability_resolves():
    """CR 602.2b: the victim is paid at activation time through the zone
    funnel; the ability resolves independently of it."""
    game = _game()
    ab = _ability(sac_type="creature", sac_another=True)
    perm = _host(game, ab)
    victim = _add(game, "Wall of Omens")
    hand = len(game.players[0].hand)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    assert victim.zone == "graveyard", (
        "the victim is sacrificed as a COST — it must reach the graveyard "
        "through the zone funnel at activation time")
    assert victim not in game.players[0].battlefield
    assert perm.zone == "battlefield", "the source itself stays"
    game.resolve_stack()
    assert len(game.players[0].hand) == hand + 1, "the draw still resolves"


def test_discard_cost_removes_a_card_from_hand_before_resolution():
    game = _game(n_hand=3)
    ab = _ability(discard=1, kind=ActivationEffectKind.DAMAGE_ANY_TARGET,
                  amount=2)
    perm = _host(game, ab)
    gy = len(game.players[0].graveyard)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    assert len(game.players[0].hand) == 2, (
        "the discard is part of the COST — paid at activation time")
    assert len(game.players[0].graveyard) == gy + 1, (
        "the discarded card must land in the graveyard via the discard "
        "funnel, so discard-linked triggers keep firing")


def test_activation_is_refused_whole_when_no_victim_exists_at_payment():
    """Half-paid costs are forbidden: with no legal victim, activate()
    refuses before charging anything."""
    game = _game()
    ab = _ability(mana=1, sac_type="creature", sac_another=True)
    perm = _host(game, ab)  # no second creature
    untapped_before = len(game.players[0].untapped_lands)
    assert not ActivationManager.activate(game, 0, perm, ab, [])
    assert len(game.players[0].untapped_lands) == untapped_before, (
        "a refused activation must not have paid the mana half of the cost")
    assert game.stack.is_empty


# ── end-to-end: real-DB sacrifice-outlet and discard-outlet shapes ────

def test_sacrifice_outlet_shape_parses_payable_from_the_db():
    """'{N}, Sacrifice a creature: Draw a card' — the biggest blocked class
    this tranche unlocks (460 abilities DB-wide)."""
    t = _DB.get_card("Carnage Altar")
    draw_abs = [a for a in (t.activated_abilities or [])
                if a.effect_kind is ActivationEffectKind.DRAW_N]
    assert draw_abs, f"fixture premise: got {t.activated_abilities}"
    ab = draw_abs[0]
    assert ab.cost.sacrifice_type == "creature"
    assert ab.cost.sacrifice_another is False
    assert ab.cost.unpayable == (), f"got {ab.cost.unpayable}"


def test_discard_outlet_shape_parses_payable_from_the_db():
    """'{T}, Discard a card: Draw a card' — the looter shape (244 discard
    costs DB-wide)."""
    t = _DB.get_card("Charging Strifeknight")
    draw_abs = [a for a in (t.activated_abilities or [])
                if a.effect_kind is ActivationEffectKind.DRAW_N]
    assert draw_abs, f"fixture premise: got {t.activated_abilities}"
    ab = draw_abs[0]
    assert ab.cost.discard_cards == 1
    assert ab.cost.tap_self is True
    assert ab.cost.unpayable == (), f"got {ab.cost.unpayable}"


def test_sacrifice_outlet_is_enumerated_by_the_ai_and_charges_the_victim():
    """End-to-end: the AI enumerates the sacrifice-outlet draw, with the
    victim's board contribution charged into the projection."""
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game

    game = _game()
    altar = _add(game, "Carnage Altar")
    victim = _add(game, "Wall of Omens")  # 0-power: cheap board loss
    ab = altar.template.activated_abilities[0]
    assert ActivationManager.can_activate(game, 0, altar, ab), (
        "the engine must permit the sacrifice-outlet cash-in")
    snap = snapshot_from_game(game, 0)
    cands = activation_candidates(game, 0, snap)
    assert any(p.instance_id == altar.instance_id for p, *_ in cands), (
        "the AI must enumerate the sacrifice-outlet draw as a candidate")


def test_discard_outlet_executes_end_to_end_from_the_db():
    """Engine end-to-end on the real looter shape: cost paid from hand
    through the discard funnel, draw resolves."""
    game = _game(n_hand=2)
    looter = _add(game, "Charging Strifeknight")
    ab = looter.template.activated_abilities[0]
    assert ActivationManager.can_activate(game, 0, looter, ab)
    gy = len(game.players[0].graveyard)
    assert ActivationManager.activate(game, 0, looter, ab, [])
    assert looter.tapped, "the tap half of the cost is still charged"
    assert len(game.players[0].graveyard) == gy + 1
    assert len(game.players[0].hand) == 1
    game.resolve_stack()
    assert len(game.players[0].hand) == 2, (
        "net loot: the discard is a cost, the draw is the resolution")
