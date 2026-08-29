"""What graveyard hate is WORTH: the fuel it removes from its owner.

The engine half (`ActivationEffectKind.EXILE_FROM_GRAVEYARD`) makes these
activations legal. Legality alone would make them worse than useless: an
"exile all graveyards" line with an empty board on both sides is an
engine-legal activation that sacrifices the permanent and accomplishes
nothing, and a symmetric one can cost its controller more than it takes
from the opponent.

The value of exiling a graveyard is not its card COUNT. It is how much of
that graveyard is still a RESOURCE to its owner, which is a different
number entirely — a Boros Energy graveyard of spent burn spells is worth
nothing, and a two-card graveyard holding an escape threat is worth a
great deal. `ai.predicates.graveyard_fuel` answers that from parse-once
typed fields only, in three tiers:

  * cards castable from the graveyard by their own printed permission
    (flashback / escape / unearth) — live unconditionally, they need no
    other card;
  * creature cards, once their owner has REVEALED a graveyard-to-
    battlefield recursion source (public information: a permanent on
    their battlefield or a spell already in their graveyard);
  * every card, once their owner has revealed a consumer of graveyard
    SIZE (delve, delirium, a graveyard-scaling body).

The removed fuel is then priced at `card_clock_impact` — the exact term
`position_value` uses to price one card of advantage — so a hate
activation competes with a cast on one scale, with no new magnitude
invented for it.

Rules pinned here. Card names in bodies are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.activation_ev import activation_candidates
from ai.ev_evaluator import snapshot_from_game
from ai.predicates import graveyard_fuel
from engine.card_database import CardDatabase
from engine.cards import ActivationEffectKind, CardInstance
from engine.game_state import GameState, Phase

_DB = CardDatabase()


def _add(game, name, controller=0, zone="battlefield"):
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
    game.turn_number = 5
    for _ in range(4):
        _add(game, "Swamp")
        _add(game, "Swamp", 1)
    return game


def _snap(game):
    return snapshot_from_game(game, 0)


def _hate(game, cands, perm):
    return [c for c in cands if c[0].instance_id == perm.instance_id]


# ── the fuel primitive ────────────────────────────────────────────────

def test_spent_cards_with_no_route_back_are_not_fuel():
    """A graveyard of burnt-out spells is worth nothing to its owner, so
    exiling it removes nothing."""
    game = _game()
    for _ in range(4):
        _add(game, "Lightning Bolt", 1, "graveyard")
    assert graveyard_fuel(game, 1) == []


def test_cards_castable_from_the_graveyard_are_fuel_unconditionally():
    """Flashback and escape need no second card — the graveyard IS the
    hand for them."""
    game = _game()
    fb = _add(game, "Lingering Souls", 1, "graveyard")
    esc = _add(game, "Kroxa, Titan of Death's Hunger", 1, "graveyard")
    _add(game, "Lightning Bolt", 1, "graveyard")
    fuel = graveyard_fuel(game, 1)
    assert set(fuel) == {fb, esc}


def test_creature_cards_become_fuel_once_recursion_is_revealed():
    """A creature in the graveyard is a reanimation target only when its
    owner has actually shown a way to bring it back."""
    game = _game()
    body = _add(game, "Griselbrand", 1, "graveyard")
    assert graveyard_fuel(game, 1) == []
    # A resolved reanimation spell in their graveyard is public evidence.
    _add(game, "Goryo's Vengeance", 1, "graveyard")
    assert body in graveyard_fuel(game, 1)


def test_whole_graveyard_is_fuel_once_a_size_consumer_is_revealed():
    """Delve / delirium / graveyard-scaling bodies make every card in the
    graveyard count, type irrelevant."""
    game = _game()
    for _ in range(3):
        _add(game, "Lightning Bolt", 1, "graveyard")
    assert graveyard_fuel(game, 1) == []
    _add(game, "Tarmogoyf", 1)          # power scales with the graveyard
    assert len(graveyard_fuel(game, 1)) == 3


# ── the activation decision ───────────────────────────────────────────

def test_hate_activation_is_not_offered_against_an_empty_graveyard():
    """The whole point: a permanent that sacrifices itself for nothing
    must never be enumerated."""
    game = _game()
    crypt = _add(game, "Tormod's Crypt")
    assert not _hate(game, activation_candidates(game, 0, _snap(game)),
                     crypt)


def test_hate_activation_is_not_offered_against_a_graveyard_of_dead_cards():
    """A full graveyard with no route back is the same as an empty one."""
    game = _game()
    crypt = _add(game, "Tormod's Crypt")
    for _ in range(6):
        _add(game, "Lightning Bolt", 1, "graveyard")
    assert not _hate(game, activation_candidates(game, 0, _snap(game)),
                     crypt)


def test_hate_activation_is_offered_when_the_graveyard_holds_live_fuel():
    game = _game()
    crypt = _add(game, "Tormod's Crypt")
    _add(game, "Lingering Souls", 1, "graveyard")
    _add(game, "Kroxa, Titan of Death's Hunger", 1, "graveyard")
    picked = _hate(game, activation_candidates(game, 0, _snap(game)), crypt)
    assert picked, "live fuel in the opposing graveyard must be worth hitting"
    assert picked[0][3] > 0.0


def test_more_fuel_removed_is_worth_more():
    """EV scales with the fuel removed, not with an invented magnitude."""
    small = _game()
    c1 = _add(small, "Tormod's Crypt")
    _add(small, "Lingering Souls", 1, "graveyard")
    ev_small = _hate(small, activation_candidates(small, 0, _snap(small)),
                     c1)[0][3]

    big = _game()
    c2 = _add(big, "Tormod's Crypt")
    for _ in range(4):
        _add(big, "Lingering Souls", 1, "graveyard")
    ev_big = _hate(big, activation_candidates(big, 0, _snap(big)), c2)[0][3]
    assert ev_big > ev_small


def _host_symmetric_exile(game, name="Tormod\'s Crypt"):
    """Put an "Exile all graveyards" line on a real permanent with a
    payable cost. The symmetric shape is a printed one (Relic of
    Progenitus, Sentinel Totem, Crook of Condemnation, Scavenger
    Grounds); those cards charge cost items outside this tranche, so the
    AI rule is exercised through a constructed carrier rather than left
    untested until the cost lands."""
    from engine.cards import ActivatedAbility, ActivationCost
    from engine.mana import ManaCost

    ability = ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(), tap_self=True),
        effect_text="Exile all graveyards",
        effect_kind=ActivationEffectKind.EXILE_FROM_GRAVEYARD,
        graveyard_exile_data={'scope': 'all', 'count': 0, 'up_to': False,
                              'types': [], 'owner': 'any',
                              'single_graveyard': False})
    perm = _add(game, name)
    perm.template = perm.template.__class__(**{
        **{f: getattr(perm.template, f)
           for f in perm.template.__dataclass_fields__},
        'activated_abilities': [ability]})
    return perm


def test_symmetric_exile_is_declined_when_it_burns_more_of_our_own_fuel():
    """"Exile all graveyards" takes the activator's graveyard too. When
    ours is the more valuable one the activation is a net loss."""
    game = _game()
    totem = _host_symmetric_exile(game)
    for _ in range(4):
        _add(game, "Lingering Souls", 0, "graveyard")   # OUR fuel
    _add(game, "Lingering Souls", 1, "graveyard")       # theirs
    assert not _hate(game, activation_candidates(game, 0, _snap(game)),
                     totem)


def test_symmetric_exile_is_taken_when_their_graveyard_is_the_valuable_one():
    """The mirror of the rule above — the net, not the raw count, is what
    the decision reads."""
    game = _game()
    totem = _host_symmetric_exile(game)
    _add(game, "Lingering Souls", 0, "graveyard")       # ours
    for _ in range(4):
        _add(game, "Lingering Souls", 1, "graveyard")   # theirs
    picked = _hate(game, activation_candidates(game, 0, _snap(game)), totem)
    assert picked and picked[0][3] > 0.0


def test_card_scope_targets_only_the_opponents_fuel():
    """A targeted exile never spends itself on our own graveyard, and
    never on a dead card when a live one is available."""
    game = _game()
    wretch = _add(game, "Withered Wretch")
    mine = _add(game, "Lingering Souls", 0, "graveyard")
    dead = _add(game, "Lightning Bolt", 1, "graveyard")
    live = _add(game, "Lingering Souls", 1, "graveyard")
    picked = _hate(game, activation_candidates(game, 0, _snap(game)), wretch)
    assert picked, "a live card in the opposing graveyard is worth exiling"
    targets = picked[0][2]
    assert targets == [live.instance_id], targets
    assert mine.instance_id not in targets
    assert dead.instance_id not in targets
