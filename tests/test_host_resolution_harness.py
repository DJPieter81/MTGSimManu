"""The per-host resolution harness (design doc 2026-09-29, section 10, A42).

`tools/host_resolution_equivalence.py` resolves one host on deep copies of
six fixed synthetic boards, per seed, through two sides and compares the
canonical game-state digest and the game-log bytes. In E0 no executor
exists, so both sides are the legacy apply: the harness proves itself
deterministic on every registered-deck mainboard and sideboard host (exit
criterion 5).

The boards are built from the shared card DB by characteristics; the
harness never mutates a DB template (its deep copies share templates
read-only).
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _h():
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import host_resolution_equivalence
    return host_resolution_equivalence


@pytest.fixture(scope="module")
def boards(card_db):
    h = _h()
    pool = h.BoardPool(card_db)
    return pool, {b: h.build_board(pool, b) for b in h.BOARDS}


def _copy(game):
    return copy.deepcopy(game, _h()._memo_for(game))


def test_the_six_boards_are_the_same_on_every_build_and_differ_from_each_other(card_db, boards):
    h = _h()
    pool, built = boards
    assert tuple(built) == ("empty", "opponent_creatures", "own_creatures",
                            "lands", "stack", "graveyards")
    again = {b: h.state_digest(h.build_board(h.BoardPool(card_db), b))
             for b in h.BOARDS}
    assert again == {b: h.state_digest(g) for b, g in built.items()}
    assert len(set(again.values())) == len(h.BOARDS)
    # the creature board holds mixed mana values and a keyworded creature
    mvs = {h._mv(t) for t in pool.creatures}
    assert len(mvs) >= 3 and any(t.keywords for t in pool.creatures)
    assert built["stack"].stack.items


def test_the_state_digest_changes_with_every_component_it_covers(boards):
    from engine.continuous_effects import ContinuousEffect, Layer
    from engine.delayed_triggers import DelayedTrigger, DelayedTriggerTiming
    h = _h()
    _pool, built = boards
    base = built["own_creatures"]
    d0 = h.state_digest(base)

    def changed(mutate):
        g = _copy(base)
        assert h.state_digest(g) == d0      # a copy digests the same
        mutate(g)
        return h.state_digest(g) != d0

    me = 0
    assert changed(lambda g: setattr(g.players[1], "life", 17))
    assert changed(lambda g: setattr(g.players[me], "poison_counters", 1))
    assert changed(lambda g: setattr(g.players[me], "energy_counters", 2))
    assert changed(lambda g: g.players[me].library.reverse())
    assert changed(lambda g: g.players[me].hand.pop())
    assert changed(lambda g: setattr(g.players[me].battlefield[-1],
                                     "plus_counters", 1))
    assert changed(lambda g: setattr(g.players[me].battlefield[-1],
                                     "damage_marked", 2))
    assert changed(lambda g: setattr(g.players[me].battlefield[-1],
                                     "tapped", True))
    assert changed(lambda g: g.continuous_effects._effects.append(
        ContinuousEffect(source_id=1, source_name="x",
                         layer=list(Layer)[0], description="d")))
    assert changed(lambda g: g.register_delayed_trigger(DelayedTrigger(
        timing=list(DelayedTriggerTiming)[0], controller=0,
        effect=lambda game: None, description="later", created_turn=1)))


def test_the_digest_holds_no_object_address():
    """Callables canonicalise by qualified name, so two equal states hold
    equal digests even though their closures are distinct objects."""
    h = _h()

    def f():
        return None
    a = h._canon({"effect": f, "items": {3, 1, 2}}, set())
    b = h._canon({"items": {2, 3, 1}, "effect": (lambda: None)}, set())
    assert a[0] == ["effect", "fn:test_the_digest_holds_no_object_address."
                    "<locals>.f"]
    assert a[1] == b[1] == ["items", [1, 2, 3]]
    assert "0x" not in repr(a)


def test_a_side_whose_resolution_is_not_a_function_of_the_board_and_seed_diverges(card_db, boards):
    h = _h()
    _pool, built = boards
    counter = {"n": 0}

    def unstable(game, template, case):
        counter["n"] += 1
        game.log.append(f"resolution {counter['n']}")
        game.players[1].life -= counter["n"]

    t = h.deck_templates(card_db)[0]
    case = h.HostCase(t.name, "SPELL:0:0", "SPELL")
    out = h.compare_host({"empty": built["empty"]}, t, case, seeds=(0,),
                         left=unstable, right=unstable)
    assert {d.what for d in out} == {"digest", "log"}
    # the same deterministic side never diverges
    same = h.compare_host({"empty": built["empty"]}, t, case, seeds=(0,),
                          left=lambda g, tt, c: g.log.append("x"),
                          right=lambda g, tt, c: g.log.append("x"))
    assert same == []


def test_every_host_of_a_card_is_either_resolvable_through_a_legacy_apply_or_reported_skipped(card_db):
    from engine.effect_grammar import parse_template
    h = _h()
    seen = {"SPELL": 0, "MODE": 0, "ACTIVATED": 0, "LOYALTY": 0}
    for t in h.deck_templates(card_db):
        ce = parse_template(t)
        ok, skipped = h.host_cases(t, ce)
        assert len(ok) + len(skipped) == sum(1 for _ in ce.walk(
            include_sub=False))
        for c in ok:
            seen[c.kind] += 1
    assert all(n > 0 for n in seen.values()), seen


# The E0 self-check over every registered-deck MB and SB host: 234 hosts x
# 6 boards x 2 seeds x 2 sides. Measured 2026-10-02: ~59 s CPU (4-core box
# under a concurrent 4-worker matrix run), plus ~18 s when first in the
# process to load the shared card DB. 900 s bounds a hang on a 2-core CI
# runner.
@pytest.mark.timeout(900)
def test_the_legacy_self_check_is_deterministic_on_every_registered_deck_host(card_db):
    h = _h()
    templates = h.deck_templates(card_db)

    def snapshot():
        return {t.name: repr([(k, v) for k, v in sorted(vars(t).items())
                              if not k.startswith("_effects")])
                for t in templates}
    before = snapshot()
    rep = h.self_check(card_db, templates)
    assert rep["divergences"] == []
    # the deep copies share the DB templates read-only
    assert snapshot() == before
    assert rep["hosts"] >= 200, rep["hosts"]
    assert rep["boards"] == list(h.BOARDS) and rep["seeds"] == list(h.SEEDS)
