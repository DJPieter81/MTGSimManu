"""A description that names "a counter" without a kind admits a permanent
with a counter of any kind on it (CR 122.1): "a creature with a counter on
it", "creatures with no counters on them".

The defect: the grammar types the kindless counter as the wildcard kind,
and the description owner (`effect_conditions.permanent_described`) asked
the permanent how many counters of the kind "*" it had -- none, ever. Only
an object's own static condition ("as long as ~ has two or more counters on
it") read the wildcard. So "if you control a creature with a counter on
it" never held, and "creatures with no counters on them" admitted every
creature.

Class: 19 pool cards print such a description. No registered card. Card
names are fixture carriers.
"""
from __future__ import annotations

import dataclasses
import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _put(game, card_db, name, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[idx].battlefield.append(c)
    return c


def _condition(card_db):
    """Delta Bloodflies' "if you control a creature with a counter on it"."""
    (head,) = [h.trigger for h in
               card_db.get_card("Delta Bloodflies").effects.faces[0]
               if h.trigger is not None]
    return head.intervening_if


def test_a_counter_of_any_kind_is_a_counter(card_db):
    from engine.effect_conditions import permanent_described
    game = _game()
    f = _condition(card_db).filter
    assert f.counters == (("*", True),)
    plus, minus, oil, bare = (_put(game, card_db, "Grizzly Bears")
                              for _ in range(4))
    plus.add_plus_counters(1, game)
    minus.minus_counters = 1
    oil.other_counters["oil"] = 1
    assert [permanent_described(c, f) for c in (plus, minus, oil, bare)] \
        == [True, True, True, False]


def test_a_description_of_no_counters_admits_only_a_permanent_with_none(
        card_db):
    from engine.effect_conditions import permanent_described
    game = _game()
    f = dataclasses.replace(_condition(card_db).filter,
                            counters=(("*", False),))
    bare, oil = (_put(game, card_db, "Grizzly Bears") for _ in range(2))
    oil.other_counters["oil"] = 2
    assert permanent_described(bare, f) and not permanent_described(oil, f)


def test_a_count_of_creatures_with_a_counter_reads_every_kind(card_db):
    from engine.effect_conditions import state_condition_holds
    game = _game()
    cond = _condition(card_db)
    flies = _put(game, card_db, "Delta Bloodflies")
    assert not state_condition_holds(game, 0, cond, flies)
    _put(game, card_db, "Grizzly Bears").other_counters["shield"] = 1
    assert state_condition_holds(game, 0, cond, flies)


def test_a_kinded_description_still_reads_its_kind(card_db):
    """"A +1/+1 counter" is that kind, not any counter."""
    from engine.effect_conditions import permanent_described
    game = _game()
    f = dataclasses.replace(_condition(card_db).filter,
                            counters=(("+1/+1", True),))
    oil, plus = (_put(game, card_db, "Grizzly Bears") for _ in range(2))
    oil.other_counters["oil"] = 1
    plus.add_plus_counters(1, game)
    assert not permanent_described(oil, f) and permanent_described(plus, f)


# ── Auditor (CR 122.1), restated from the raw counter fields ──────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_counter_of_another_kind_left_uncounted(
        card_db, audit, monkeypatch):
    from engine import effect_conditions
    monkeypatch.setattr(effect_conditions, "counters_on",
                        lambda perm, kind: perm.counter_count(kind))  # defect
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears")
    bears.other_counters["oil"] = 1
    effect_conditions.permanent_described(bears, _condition(card_db).filter)
    assert "122.1/any_counter_counted" in _rules(audit)


def test_the_audit_is_silent_when_every_kind_is_counted(card_db, audit):
    from engine.effect_conditions import permanent_described
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears")
    bears.other_counters["oil"] = 1
    bears.add_plus_counters(2, game)
    permanent_described(bears, _condition(card_db).filter)
    assert "122.1/any_counter_counted" not in _rules(audit)
