"""A sweep destroys each permanent its description admits, its bound read
off what the source last had (CR 701.8a, 202.3, 608.2h, 602.2b, 702.12b).

"Destroy each nonland permanent with mana value equal to the number of
charge counters on ~" is one instruction over a typed description:
- the description admits a permanent by its card types and by a
  characteristic bound -- mana value (CR 202.3), power or toughness (CR
  208.1) -- compared with a value determined once, as the instruction
  applies (CR 608.2h);
- when the value counts the source's own counters and the source left as
  part of the cost (a sacrifice, CR 602.2b), it is read from the counters
  the source last had (CR 608.2h): a card in a graveyard has none;
- every admitted permanent is destroyed through the destroy owner unless it
  is indestructible (CR 701.8a, 702.12b), every player's unless the
  description names "you control".

The grammar reads the bound ("with <stat> equal to <quantity>"); the
description owner (`effect_conditions.permanent_described`) compares it;
the activation owner keeps the source's counters as its cost removes it
(`CardInstance.last_known`); the destroy owner destroys each
(`effect_executors.destroy_each_supported`). The auditor
`701.8a/destroyed_each` restates the rule from raw fields.

The defect (docs/diagnostics/2026-10-14_dimir_midrange_root_cause.md,
subsystem 3): the instruction did not parse ("filter.stat"), so these
activations were refused; a runner shortcut popped them instead, for free
and against one player only.

Card names are fixture carriers: 24 pool activations destroy each or all
permanents a description admits; 82 pool instructions carried a
characteristic bound the grammar refused.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
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
    return game


def _sweep(card):
    return next(a for a in card.template.activated_abilities
                if a.cost.sacrifice_self)


def _activate(game, card):
    from engine.activation import ActivationManager
    assert ActivationManager.activate(game, 0, card, _sweep(card), [])
    while not game.stack.is_empty:
        game.resolve_stack()


def _explosives(game, card_db, charges):
    ee = _add(game, card_db, "Engineered Explosives", 0)
    ee.other_counters["charge"] = charges
    for _ in range(2):
        _add(game, card_db, "Island", 0)
    return ee


# ── The grammar: a bound read off a quantity ──────────────────────────

@pytest.mark.parametrize("name, kind", [("Engineered Explosives", "charge"),
                                        ("Ratchet Bomb", "charge")])
def test_a_bound_read_off_the_sources_counters_is_typed(card_db, name, kind):
    from engine.effect_spec import AmountKind, QuantityKind, Verb, iter_specs
    t = card_db.get_card(name)
    a = next(a for a in t.activated_abilities if a.cost.sacrifice_self)
    (s,) = list(iter_specs(t.effects.activated(a.index).specs))
    assert s.verb is Verb.DESTROY and s.filter.not_types == {"land"}
    ((stat, op, n),) = s.filter.stat_bounds
    assert (stat, op, n.kind) == ("mana_value", "==", AmountKind.EQUAL_TO)
    q = n.quantity
    assert q.kind is QuantityKind.COUNTERS_ON and q.counter_kind == kind
    assert q.ref.lki, "the source left as part of the cost (CR 608.2h)"
    assert dict(s.subject.filter) == dict(s.filter.as_tuple())


@pytest.mark.parametrize("name", ["Engineered Explosives", "Ratchet Bomb",
                                  "Nevinyrral's Disk"])
def test_a_sweep_activation_takes_the_removal_family(card_db, name):
    from engine.effect_carrier import activation_family
    t = card_db.get_card(name)
    a = next(a for a in t.activated_abilities
             if "destroy" in a.effect_text.lower())
    assert activation_family(a, t.effects.activated(a.index)) == "removal"


def test_a_sweep_with_a_rider_no_owner_performs_is_refused_whole(card_db):
    """Oblivion Stone: "..., then remove all fate counters from all
    permanents" -- no executor removes counters, so nothing is performed."""
    from engine.effect_carrier import activation_family
    t = card_db.get_card("Oblivion Stone")
    a = next(a for a in t.activated_abilities if a.cost.sacrifice_self)
    assert activation_family(a, t.effects.activated(a.index)) is None


# ── The description owner: characteristic bounds ──────────────────────

def test_the_description_admits_by_its_bounds_and_only_with_their_values(
        card_db):
    from engine import effect_conditions as c
    from engine.effect_spec import Amount, AmountKind, CardFilter
    game = _game()
    bears = _add(game, card_db, "Grizzly Bears", 1)        # MV 2, 2/2
    vanguard = _add(game, card_db, "Elite Vanguard", 1)    # MV 1, 2/1
    two = Amount(AmountKind.LITERAL, n=2)
    f = CardFilter(not_types=frozenset({"land"}),
                   stat_bounds=(("mana_value", "==", two),))
    assert c.description_supported(f, bounds=True)
    assert not c.description_supported(f), "a consumer that reads no bound"
    values = c.bound_values(game, 0, f)
    assert c.permanent_described(bears, f, bounds=values)
    assert not c.permanent_described(vanguard, f, bounds=values)
    assert not c.permanent_described(bears, f), "no values, no admission"
    tough = CardFilter(stat_bounds=(("toughness", "<=", Amount(
        AmountKind.LITERAL, n=1)),))
    assert c.permanent_described(vanguard, tough,
                                 bounds=c.bound_values(game, 0, tough))
    assert not c.permanent_described(bears, tough,
                                     bounds=c.bound_values(game, 0, tough))


def test_a_source_that_left_is_read_from_the_counters_it_last_had(card_db):
    game = _game()
    ee = _explosives(game, card_db, charges=2)
    lki = ee.last_known()
    game.zone_mgr.move_card_to_graveyard(game, ee, cause="test")
    assert ee.counter_count("charge") == 0, "a card in a graveyard has none"
    assert lki.count("charge") == 2


# ── The activation, end to end ────────────────────────────────────────

def test_a_sweep_destroys_each_permanent_with_the_mana_value_it_counted(
        card_db):
    """Two charge counters: each nonland permanent with mana value 2 is
    destroyed, both players'; mana value 1 and 0 and lands stay."""
    game = _game()
    ee = _explosives(game, card_db, charges=2)
    stone = _add(game, card_db, "Mind Stone", 0)           # MV 2, mine
    bears = _add(game, card_db, "Grizzly Bears", 1)        # MV 2
    vanguard = _add(game, card_db, "Elite Vanguard", 1)    # MV 1
    memnite = _add(game, card_db, "Memnite", 1)            # MV 0
    forest = _add(game, card_db, "Forest", 1)
    _activate(game, ee)
    assert ee.zone == "graveyard"
    assert bears.zone == "graveyard" and stone.zone == "graveyard"
    assert vanguard.zone == memnite.zone == forest.zone == "battlefield"


def test_an_indestructible_permanent_survives_the_sweep(card_db):
    game = _game()
    ee = _explosives(game, card_db, charges=1)
    axe = _add(game, card_db, "Darksteel Axe", 1)          # MV 1, indestructible
    vanguard = _add(game, card_db, "Elite Vanguard", 1)    # MV 1
    _activate(game, ee)
    assert vanguard.zone == "graveyard" and axe.zone == "battlefield"


def test_a_sweep_with_no_counters_destroys_mana_value_zero(card_db):
    game = _game()
    ee = _explosives(game, card_db, charges=0)
    memnite = _add(game, card_db, "Memnite", 1)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    _activate(game, ee)
    assert memnite.zone == "graveyard" and bears.zone == "battlefield"


# ── The runner performs no sweep of its own ───────────────────────────

def test_the_runner_neither_adds_a_counter_nor_pops_a_sweep(card_db):
    """The runner's utility step once put a charge counter on every
    charge-counter sweep each turn -- one with no ability that puts it --
    and popped it for free against the opponent alone. A sweep is an
    activated ability its controller chooses to activate (CR 602.2)."""
    from engine.game_runner import GameRunner
    game = _game()
    ee = _explosives(game, card_db, charges=1)
    bears = [_add(game, card_db, "Grizzly Bears", 1) for _ in range(2)]
    GameRunner(card_db=card_db)._activate_utility_artifacts(game, 0)
    assert ee.zone == "battlefield" and ee.counter_count("charge") == 1
    assert all(b.zone == "battlefield" for b in bears)


# ── The auditor (CR 701.8a) ───────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "701.8a/destroyed_each"]


def test_the_audit_is_silent_when_the_sweep_destroys_what_it_admits(
        card_db, audit):
    game = _game()
    ee = _explosives(game, card_db, charges=2)
    _add(game, card_db, "Grizzly Bears", 1)
    _activate(game, ee)
    assert not _findings(audit)


def test_the_audit_records_a_sweep_that_counted_without_the_last_known_counters(
        card_db, audit, monkeypatch):
    """Broken the way the defect would be: the count read off the card in
    the graveyard (no counters), so mana value 0 is swept, not 2."""
    from engine import effect_conditions as c
    real = c.bound_values

    def _no_lki(game, controller, f, source=None, source_lki=None,
                x_value=0):
        return real(game, controller, f, source, None, x_value)
    monkeypatch.setattr(c, "bound_values", _no_lki)
    game = _game()
    ee = _explosives(game, card_db, charges=2)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    _activate(game, ee)
    assert bears.zone == "battlefield"
    assert _findings(audit)
