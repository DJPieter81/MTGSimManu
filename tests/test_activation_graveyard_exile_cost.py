"""Tranche 5: "exile N cards from your graveyard" becomes a PAYABLE cost.

Tranches 1-4 parsed the line but dumped the cost item in
`ActivationCost.unpayable` as `exile`, which does more damage than
refusing the one ability: `can_activate` rules 5/6 refuse a permanent
ENTIRELY when ANY of its abilities carries an unpayable item, so one
un-chargeable half sterilises every sibling ability on the same card.

Measured DB-wide before this tranche: 11 activated abilities on 11 cards
carry an untyped, fixed-count exile-from-your-own-graveyard cost item, and
4 further abilities on those same cards were refused only because rules
5/6 sterilise a permanent whole — 15 abilities in total stop being
refused. Two of the 11 (a repeatable damage outlet and a repeatable draw
outlet) already have a resolver-supported effect kind, so they go from
refused to fully playable. A further 40 cost items name the same zone in a
shape this tranche deliberately refuses.

Rules pinned:
  * CR 601.2h — a cost is payable only if it CAN be paid: the controller's
    own graveyard must hold at least that many cards RIGHT NOW. Same shape
    as the discard rule (9e), a different zone.
  * CR 602.2b — the cards leave at ACTIVATION time, as part of the cost;
    the ability on the stack resolves independently of them.
  * CR 406 — exile is a zone, so every card moved there goes through the
    zone funnel and leaves-the-graveyard triggers/replacements see it.
  * No-free-repeatable (rule 9): the graveyard is a finite pile that only
    refills through separate game events, so each payment strictly shrinks
    it and the activation terminates — the same bound a discard cost takes
    from the hand.
  * CLOSED shape only. A type-restricted victim ("a creature card"), an
    unbounded count ("all", "any number", "one or more"), an {X}-counted
    exile, a self-excluding "N other cards", and an exile from any other
    zone all stay in `unpayable` — refused rather than approximated by a
    count.
  * Exile-SELF ("Exile this creature", tranche 2) is a different cost item
    with its own field and must keep working.

Rules-phrased; card names are fixture carriers only.
"""
from __future__ import annotations

import copy
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


def _game(n_islands=4, n_gy=0):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Eldrazi Tron"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_islands):
        _add(game, "Island")
    for _ in range(n_gy):
        _add(game, "Lightning Bolt", 0, "graveyard")
    for _ in range(8):
        _add(game, "Island", 0, "library")
    return game


def _ability(mana=0, tap=False, gy=0,
             kind=ActivationEffectKind.DRAW_N, amount=1):
    return ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(generic=mana), tap_self=tap,
                            exile_from_graveyard_cards=gy),
        effect_text="Draw a card.", effect_kind=kind, amount=amount)


def _host(game, ability, name="Wall of Omens"):
    """Attach one synthetic ability to a fixture carrier. The template is
    COPIED first: templates are shared DB objects and mutating one would
    leak into every other test in the session."""
    perm = _add(game, name)
    perm.template = copy.copy(perm.template)
    perm.template.activated_abilities = [ability]
    return perm


# ── parsing: the cost item becomes structured, not unpayable ──────────

def test_untyped_graveyard_exile_cost_parses_as_a_count():
    cost = parse_activation_cost(
        "{1}, {T}, Exile two cards from your graveyard")
    assert cost is not None
    assert cost.exile_from_graveyard_cards == 2
    assert cost.mana.cmc == 1 and cost.tap_self is True
    assert cost.unpayable == (), (
        f"an untyped fixed-count graveyard exile is tranche-5 payable, got "
        f"{cost.unpayable}")


def test_graveyard_exile_count_words_and_digits_parse_identically():
    for phrase, expected in (("Exile a card from your graveyard", 1),
                             ("Exile one card from your graveyard", 1),
                             ("Exile three cards from your graveyard", 3),
                             ("Exile 2 cards from your graveyard", 2)):
        cost = parse_activation_cost(phrase)
        assert cost is not None and cost.unpayable == (), f"{phrase!r}"
        assert cost.exile_from_graveyard_cards == expected, phrase


def test_type_restricted_graveyard_exile_cost_stays_unpayable():
    """A typed victim is a CHOICE among a heterogeneous set, which the cost
    schema does not hold — refused, never approximated as "any N cards"."""
    for phrase in ("Exile a creature card from your graveyard",
                   "Exile an instant or sorcery card from your graveyard",
                   "Exile three artifact cards from your graveyard"):
        cost = parse_activation_cost(phrase)
        assert cost is not None and cost.unpayable, (
            f"{phrase!r} must stay visible-but-refused, got {cost}")
        assert cost.exile_from_graveyard_cards == 0


def test_unbounded_or_x_counted_graveyard_exile_cost_stays_unpayable():
    """"All", "any number", "one or more" and an {X}-bound count are not
    fixed counts: nothing here binds X and nothing bounds the pile, so the
    payer has no number to charge."""
    for phrase in ("Exile all cards from your graveyard",
                   "Exile any number of cards from your graveyard",
                   "Exile one or more cards from your graveyard",
                   "Exile X cards from your graveyard"):
        cost = parse_activation_cost(phrase)
        assert cost is not None and cost.unpayable, (
            f"{phrase!r} must stay visible-but-refused, got {cost}")
        assert cost.exile_from_graveyard_cards == 0


def test_self_excluding_graveyard_exile_cost_stays_unpayable():
    """"N OTHER cards" excludes the source from the pile it draws from —
    a self-exclusion the payer does not model."""
    cost = parse_activation_cost(
        "Exile seven other cards from your graveyard")
    assert cost is not None and cost.unpayable
    assert cost.exile_from_graveyard_cards == 0


def test_exile_from_another_zone_stays_unpayable():
    """The zone is pinned. An exile paid from the hand, or from a
    graveyard that is not the controller's own, is a different cost."""
    for phrase in ("Exile a card from your hand",
                   "Exile a card from a graveyard",
                   "Exile the top card of your library"):
        cost = parse_activation_cost(phrase)
        assert cost is not None and cost.unpayable, (
            f"{phrase!r} must stay visible-but-refused, got {cost}")
        assert cost.exile_from_graveyard_cards == 0


def test_exile_self_cost_is_unaffected_by_the_graveyard_exile_shape():
    """Tranche 2's exile-SELF item keeps its own structured field: the two
    shapes share the verb and nothing else."""
    for phrase in ("Exile this creature", "Exile this artifact"):
        cost = parse_activation_cost(phrase)
        assert cost is not None
        assert cost.exile_self is True, phrase
        assert cost.exile_from_graveyard_cards == 0, phrase
        assert cost.unpayable == (), phrase
    # And the graveyard-exile shape never sets exile_self.
    gy = parse_activation_cost("Exile three cards from your graveyard")
    assert gy.exile_self is False and gy.exile_from_graveyard_cards == 3


# ── legality ──────────────────────────────────────────────────────────

def test_graveyard_exile_refused_when_the_graveyard_is_short():
    """CR 601.2h — you cannot pay from a zone that lacks the cards."""
    game = _game(n_gy=1)
    ab = _ability(gy=2)
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab)
    _add(game, "Lightning Bolt", 0, "graveyard")
    assert ActivationManager.can_activate(game, 0, perm, ab), (
        "two cards exactly cover a two-card cost")


def test_graveyard_exile_reads_the_activators_own_graveyard():
    """The cost says "your graveyard": the opponent's pile cannot pay it."""
    game = _game()
    for _ in range(3):
        _add(game, "Lightning Bolt", 1, "graveyard")
    ab = _ability(gy=1)
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab)


def test_exiling_from_your_graveyard_satisfies_the_no_free_repeatable_rule():
    """A finite pile that only refills through separate game events is a
    depleting resource, so a zero-mana, no-tap ability charging it
    terminates the way a discard cost does."""
    game = _game(n_gy=2)
    ab = _ability(gy=1)
    perm = _host(game, ab)
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_one_unpayable_cost_item_no_longer_sterilises_sibling_abilities():
    """Rules 5/6 refuse a permanent whole when ANY of its abilities carries
    an unpayable cost item. Graduating this cost item is therefore not just
    "one more ability": every SIBLING ability on the same card stops being
    refused with it."""
    game = _game(n_gy=3)
    payable = ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(generic=1)),
        effect_text="Draw a card.",
        effect_kind=ActivationEffectKind.DRAW_N, amount=1)
    gy_sibling = ActivatedAbility(
        index=1,
        cost=ActivationCost(exile_from_graveyard_cards=3),
        effect_text="Draw a card.",
        effect_kind=ActivationEffectKind.DRAW_N, amount=1)
    perm = _host(game, payable)
    perm.template.activated_abilities = [payable, gy_sibling]
    assert gy_sibling.cost.unpayable == (), (
        "premise: the sibling's cost item is the one this tranche pays")
    assert ActivationManager.can_activate(game, 0, perm, payable), (
        "the sibling no longer carries an unpayable item, so rule 6 stops "
        "refusing the whole permanent")


# ── payment ───────────────────────────────────────────────────────────

def test_paid_graveyard_cards_reach_exile_at_activation_time():
    """CR 602.2b + CR 406 — the cards leave the graveyard for exile as the
    cost is paid, through the zone funnel, before the ability resolves."""
    game = _game(n_gy=3)
    ab = _ability(mana=1, gy=2)
    perm = _host(game, ab)
    gy_before = list(game.players[0].graveyard)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    assert len(game.players[0].graveyard) == 1
    paid = [c for c in gy_before if c not in game.players[0].graveyard]
    assert len(paid) == 2
    for card in paid:
        assert card.zone == "exile", (
            "the cost must move the card through the zone funnel, not "
            "merely drop it out of the graveyard list")
        assert card in game.players[0].exile


def test_graveyard_exile_payment_takes_cards_in_zone_order():
    """WHICH cards pay is one definition, in the engine, with no
    valuation: the graveyard is an ordered pile and the head of it pays."""
    game = _game(n_gy=3)
    gy = list(game.players[0].graveyard)
    assert ActivationManager.graveyard_exile_payment(game, 0, 2) == gy[:2]
    assert ActivationManager.graveyard_exile_payment(game, 0, 0) == []


def test_activation_is_refused_whole_when_the_graveyard_is_short():
    """Half-paid costs are forbidden: with the graveyard short, activate()
    refuses before charging the mana half."""
    game = _game(n_gy=1)
    ab = _ability(mana=1, gy=2)
    perm = _host(game, ab)
    untapped_before = len(game.players[0].untapped_lands)
    assert not ActivationManager.activate(game, 0, perm, ab, [])
    assert len(game.players[0].untapped_lands) == untapped_before
    assert len(game.players[0].graveyard) == 1
    assert game.stack.is_empty


def test_the_graveyard_is_what_bounds_a_repeated_activation():
    """The whole point of rule 9: the pile empties, so the loop stops."""
    game = _game(n_gy=3)
    ab = _ability(gy=1)
    perm = _host(game, ab)
    fired = 0
    while ActivationManager.can_activate(game, 0, perm, ab) and fired < 20:
        assert ActivationManager.activate(game, 0, perm, ab, [])
        game.resolve_stack()
        fired += 1
    assert fired == 3, (
        f"exactly as many activations as graveyard cards — the cost is the "
        f"bound (fired {fired})")
    assert game.players[0].graveyard == []


# ── end-to-end on real DB shapes ──────────────────────────────────────

def test_graveyard_fuelled_damage_outlet_parses_payable_from_the_db():
    """"{R}, {T}, Exile two cards from your graveyard: deal 2 damage to any
    target" — a repeatable outlet that was refused whole before this
    tranche."""
    t = _DB.get_card("Grim Lavamancer")
    dmg = [a for a in (t.activated_abilities or [])
           if a.effect_kind is ActivationEffectKind.DAMAGE_ANY_TARGET]
    assert dmg, f"fixture premise: got {t.activated_abilities}"
    ab = dmg[0]
    assert ab.cost.exile_from_graveyard_cards == 2
    assert ab.cost.tap_self is True
    assert ab.cost.unpayable == (), f"got {ab.cost.unpayable}"


def test_graveyard_fuelled_draw_outlet_parses_payable_from_the_db():
    """The same cost item on a draw effect — the class is a cost shape, not
    one effect."""
    t = _DB.get_card("Immortal Coil")
    draws = [a for a in (t.activated_abilities or [])
             if a.effect_kind is ActivationEffectKind.DRAW_N]
    assert draws, f"fixture premise: got {t.activated_abilities}"
    assert draws[0].cost.exile_from_graveyard_cards == 2
    assert draws[0].cost.unpayable == ()


def test_graveyard_fuelled_damage_outlet_runs_end_to_end_from_the_db():
    """Engine end-to-end: the graveyard cards are charged, the damage
    resolves, and the activation is refused once the pile runs short."""
    game = _game(n_gy=3)
    _add(game, "Mountain")
    lavamancer = _add(game, "Grim Lavamancer")
    ab = [a for a in lavamancer.template.activated_abilities
          if a.effect_kind is ActivationEffectKind.DAMAGE_ANY_TARGET][0]
    opp_life = game.players[1].life
    assert ActivationManager.can_activate(game, 0, lavamancer, ab)
    assert ActivationManager.activate(game, 0, lavamancer, ab, [])
    game.resolve_stack()
    assert game.players[1].life < opp_life
    assert len(game.players[0].graveyard) == 1, (
        "two graveyard cards paid the cost")
    lavamancer.tapped = False  # untap so only the graveyard cost can refuse
    assert not ActivationManager.can_activate(game, 0, lavamancer, ab), (
        "one card left cannot cover a two-card cost")


def test_a_card_whose_only_unpayable_item_was_this_one_becomes_activatable():
    """The sibling-sterilisation rule, on a real DB shape: a permanent
    carrying an untyped graveyard-exile ability alongside another ability
    must no longer be refused by rule 6."""
    t = _DB.get_card("Psychic Frog")
    abilities = t.activated_abilities or []
    assert len(abilities) == 2, f"fixture premise: got {abilities}"
    assert all(a.cost.unpayable == () for a in abilities), (
        f"no ability on this permanent may carry an unpayable cost item any "
        f"more, or rule 6 refuses all of them: "
        f"{[a.cost.unpayable for a in abilities]}")
