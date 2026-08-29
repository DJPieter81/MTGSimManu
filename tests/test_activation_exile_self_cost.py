"""Exile-this-permanent as an activation COST (CR 602.2b).

The sibling of the already-payable `sacrifice this <thing>` cost item:
the source leaves the battlefield as part of paying, so the ability is
inherently self-limiting and satisfies the no-free-repeatable rule the
same way. The only difference is the destination zone — exile rather
than the graveyard — which matters because the permanent does not come
back through graveyard recursion.

DB-wide sizing: 24 activated abilities in the Modern pool charge it
("Exile this artifact" 13, "Exile this creature" 6, "Exile this
enchantment" 5).

The second, larger effect is on SIBLING abilities. `can_activate` refuses
a permanent outright when ANY of its abilities has an unchargeable cost
item — a partially-usable engine is worse than an unusable one. So an
exile cost on one line was disabling every OTHER, fully-classified line
on the same permanent.

Rules pinned:
  * `exile this <permanent type>` parses into a structured cost field,
    not onto the unpayable escape hatch;
  * a cost that exiles the source depletes a resource, so it is a legal
    terminator for a repeatable ability;
  * payment routes the source through the zone funnel into EXILE, and it
    happens LAST — after every step that could still refuse — so nothing
    can leave the cost half-paid;
  * an exile cost on one line no longer disables its siblings;
  * costs that exile something OTHER than the source ("Exile a creature
    card from your graveyard", "Exile three cards from your graveyard")
    are a different mechanic and stay unpayable.

Card names in test bodies are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.activation import ActivationManager
from engine.card_database import CardDatabase
from engine.cards import (ActivationEffectKind, CardInstance)
from engine.game_state import GameState, Phase
from engine.oracle_parser import (parse_activated_abilities,
                                  parse_activation_cost)

_DB = CardDatabase()


# ── cost parsing ──────────────────────────────────────────────────────

def test_exile_this_permanent_parses_as_a_structured_cost_item():
    for text in ("{T}, Exile this artifact", "Exile this creature",
                 "{1}, Exile this enchantment", "{T}, Exile this land"):
        cost = parse_activation_cost(text)
        assert cost is not None, text
        assert cost.exile_self is True, text
        assert cost.unpayable == (), text


def test_exiling_something_other_than_the_source_stays_unpayable():
    """A cost that exiles cards from a graveyard, or another permanent, is
    a different mechanic — refused rather than approximated."""
    for text in ("Exile a creature card from your graveyard",
                 "Exile three cards from your graveyard",
                 "{2}, Exile two other creature cards from your graveyard"):
        cost = parse_activation_cost(text)
        assert cost is not None, text
        assert cost.exile_self is False, text
        assert 'exile' in cost.unpayable, text


def test_exile_self_and_sacrifice_self_are_distinct_cost_items():
    """Same self-limiting shape, different destination zone."""
    sac = parse_activation_cost("{T}, Sacrifice this artifact")
    exi = parse_activation_cost("{T}, Exile this artifact")
    assert sac.sacrifice_self and not sac.exile_self
    assert exi.exile_self and not exi.sacrifice_self


# ── legality ──────────────────────────────────────────────────────────

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
    return game


def test_an_exile_cost_terminates_a_repeatable_ability():
    """The no-free-repeatable rule: the source leaves, so the loop ends."""
    ab = parse_activated_abilities(
        "Exile this artifact: Draw a card.")[0]
    assert ab.cost.exile_self
    assert ActivationManager._cost_depletes_a_resource(None, ab) is True


def test_an_exile_cost_no_longer_disables_its_sibling_abilities():
    """The blast radius: a permanent whose OTHER line is fully classified
    was refused outright because this line could not be charged."""
    game = _game()
    perm = _add(game, "Crook of Condemnation")
    _add(game, "Swamp", 1, "graveyard")
    abilities = perm.template.activated_abilities
    assert len(abilities) == 2
    classified = [a for a in abilities
                  if a.effect_kind
                  is ActivationEffectKind.EXILE_FROM_GRAVEYARD]
    assert len(classified) == 2, [a.effect_kind for a in abilities]
    assert all(a.cost.unpayable == () for a in abilities)
    assert ActivationManager.can_activate(game, 0, perm, classified[0])


# ── payment ───────────────────────────────────────────────────────────

def test_paying_an_exile_cost_moves_the_source_to_exile_not_the_graveyard():
    game = _game()
    perm = _add(game, "Sentinel Totem")
    _add(game, "Swamp", 1, "graveyard")
    ab = [a for a in perm.template.activated_abilities
          if a.cost.exile_self][0]
    assert ActivationManager.activate(game, 0, perm, ab) is True
    assert perm.zone == "exile"
    assert perm in game.players[0].exile
    assert perm not in game.players[0].graveyard
    assert perm not in game.players[0].battlefield


def test_the_ability_still_resolves_after_its_source_is_exiled():
    """CR 602.2b — the cost is paid on activation; the ability on the
    stack resolves independently of the source."""
    game = _game()
    perm = _add(game, "Sentinel Totem")
    for _ in range(3):
        _add(game, "Swamp", 1, "graveyard")
    ab = [a for a in perm.template.activated_abilities
          if a.cost.exile_self][0]
    assert ActivationManager.activate(game, 0, perm, ab) is True
    game.resolve_stack()
    assert game.players[1].graveyard == []


def test_real_pool_cards_carry_the_cost_as_a_class():
    """DB-wide sizing check: the cost item is a class, not a card."""
    hits = [n for n, t in _DB.cards.items()
            if any(a.cost.exile_self
                   for a in (t.activated_abilities or []))]
    assert len(hits) >= 20, len(hits)
    for name in ("Sentinel Totem", "Crook of Condemnation",
                 "Brittle Effigy"):
        assert name in hits, name


# ── AI cost projection ────────────────────────────────────────────────

def test_the_projection_charges_an_exiled_source_like_a_sacrificed_one():
    """The permanent is gone either way. Charging only the sacrifice half
    would make "Exile this artifact: ..." score as free."""
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game
    from engine.cards import ActivatedAbility, ActivationCost
    from engine.mana import ManaCost

    def _land_eater(game, exile):
        cost = ActivationCost(mana=ManaCost(), tap_self=True,
                              sacrifice_self=not exile, exile_self=exile)
        ability = ActivatedAbility(
            index=0, cost=cost, effect_text="Draw a card",
            effect_kind=ActivationEffectKind.DRAW_N, amount=1)
        perm = _add(game, "Swamp")
        perm.template = perm.template.__class__(**{
            **{f: getattr(perm.template, f)
               for f in perm.template.__dataclass_fields__},
            'activated_abilities': [ability]})
        return perm

    evs = []
    for exile in (False, True):
        game = _game()
        perm = _land_eater(game, exile)
        snap = snapshot_from_game(game, 0)
        cands = [c for c in activation_candidates(game, 0, snap)
                 if c[0].instance_id == perm.instance_id]
        assert cands, f"exile={exile}"
        evs.append(cands[0][3])
    assert evs[0] == evs[1], (
        "exiling the source and sacrificing it are the same board loss")
