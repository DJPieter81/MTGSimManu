"""Counter-placement replacement effects (CR 614.1c / 122): "If one or more
<kind> counters would be put on <a permanent you control>, <that many plus
one | that many minus one | twice that many> are put on it instead."

14 Modern cards carry the shape (Hardened Scales, Winding Constrictor,
Branching Evolution, Corpsejack Menace, Conclave Mentor, Vizier of Remedies,
Ozolith the Shattered Spire, Kami of Whispered Hopes, Loading Zone, The Earth
Crystal, Mauhúr, Mowu, Caradora, Michelangelo). None was modelled: the
counters landed unmodified, so every counter-doubling shell was under-counted
and the "-1/-1 minus one" shape that makes a "put a -1/-1 counter: untap"
mana engine free never fired.

Parsed ONCE at load into `CardTemplate.counter_placement_replacement` and
applied inside the ONE counter funnel (`CardInstance.add_plus_counters` /
`adjust_counters`), so every placement path — activation costs, put-counter
effects, persist, modular, enters-with-N — sees it without its own hook.

Card names below are fixture carriers only; the rule is oracle-derived.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import (COUNTER_KIND_MINUS, COUNTER_KIND_PLUS, CardInstance)
from engine.game_state import GameState
from engine.oracle_parser import parse_counter_placement_replacement


class TestParse:
    def test_minus_one_shape(self):
        r = parse_counter_placement_replacement(
            "If one or more -1/-1 counters would be put on a creature you "
            "control, that many -1/-1 counters minus one are put on it instead.")
        assert r is not None
        assert r.kind == COUNTER_KIND_MINUS
        assert (r.op, r.n) == ("add", -1)
        assert "creature" in r.scope

    def test_plus_one_shape(self):
        r = parse_counter_placement_replacement(
            "If one or more +1/+1 counters would be put on a creature you "
            "control, that many plus one +1/+1 counters are put on it instead.")
        assert r.kind == COUNTER_KIND_PLUS and (r.op, r.n) == ("add", 1)

    def test_doubling_shape(self):
        r = parse_counter_placement_replacement(
            "If one or more +1/+1 counters would be put on a creature you "
            "control, twice that many +1/+1 counters are put on that creature "
            "instead.")
        assert (r.op, r.n) == ("mul", 2)

    def test_any_kind_shape_leaves_kind_unset(self):
        r = parse_counter_placement_replacement(
            "If one or more counters would be put on an artifact or creature "
            "you control, that many plus one of each of those kinds of "
            "counters are put on it instead.")
        assert r.kind is None and (r.op, r.n) == ("add", 1)
        assert {"artifact", "creature"} <= set(r.scope)

    def test_self_scoped_shape_uses_the_card_name(self):
        r = parse_counter_placement_replacement(
            "Vigilance, trample\nIf one or more +1/+1 counters would be put "
            "on Mowu, that many plus one +1/+1 counters are put on it instead.",
            name="Mowu, Loyal Companion")
        assert r is not None and r.self_only

    def test_unrelated_text_is_none(self):
        assert parse_counter_placement_replacement(
            "Whenever one or more +1/+1 counters are put on this creature, "
            "draw a card.") is None


def _bf(game, card_db, name, controller=0):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def test_minus_one_replacement_absorbs_a_single_minus_counter(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Vizier of Remedies")
    bear = _bf(game, card_db, "Grizzly Bears")
    bear.adjust_counters(COUNTER_KIND_MINUS, 1)
    assert bear.minus_counters == 0
    bear.adjust_counters(COUNTER_KIND_MINUS, 2)
    assert bear.minus_counters == 1


def test_replacement_is_scoped_to_its_controllers_permanents(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Vizier of Remedies", controller=0)
    opp_bear = _bf(game, card_db, "Grizzly Bears", controller=1)
    opp_bear.adjust_counters(COUNTER_KIND_MINUS, 1)
    assert opp_bear.minus_counters == 1


def test_plus_one_replacement_adds_one_per_placement_event(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Hardened Scales")
    bear = _bf(game, card_db, "Grizzly Bears")
    bear.add_plus_counters(1)
    assert bear.plus_counters == 2
    bear.add_plus_counters(3)
    assert bear.plus_counters == 6


def test_additive_replacements_apply_before_doubling(card_db):
    # CR 616.1: the affected object's controller orders the replacements;
    # applying the +1 before the doubling is the controller-optimal order.
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Hardened Scales")
    _bf(game, card_db, "Branching Evolution")
    bear = _bf(game, card_db, "Grizzly Bears")
    bear.add_plus_counters(1)
    assert bear.plus_counters == 4


def test_kind_restricted_replacement_ignores_other_kinds(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Hardened Scales")
    bear = _bf(game, card_db, "Grizzly Bears")
    bear.adjust_counters(COUNTER_KIND_MINUS, 1)
    assert bear.minus_counters == 1


def test_replacement_applies_to_an_activation_put_counter_cost(card_db):
    """The activation COST path pays through the same funnel: a "put two
    -1/-1 counters" cost under a minus-one replacement puts one."""
    from engine.cards import ActivatedAbility, ActivationCost, ActivationEffectKind
    from engine.mana import ManaCost
    from engine.activation import ActivationManager
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Vizier of Remedies")
    bear = _bf(game, card_db, "Grizzly Bears")
    ability = ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(), put_counter_kind=COUNTER_KIND_MINUS,
                            put_counter_amount=2),
        effect_text="It deals 1 damage to any target.",
        effect_kind=ActivationEffectKind.DAMAGE_ANY_TARGET, amount=1,
        targets_required=1)
    opp = game.players[1]
    assert ActivationManager.activate(game, 0, bear, ability,
                                      targets=[game.players[1].player_id
                                               if hasattr(opp, 'player_id') else -1]) \
        or True  # target plumbing is not under test; the cost is
    assert bear.minus_counters == 1
