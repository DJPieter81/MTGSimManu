"""The per-host resolution harness (design doc 2026-09-29, section 10, A42).

`tools/host_resolution_equivalence.py` resolves one host on deep copies of
six fixed synthetic boards, per seed, through two sides and compares the
canonical game-state digest and the game-log bytes. Each legacy apply gets
the targets a deterministic rule chooses on that board from the host's
requirements. In E0 no executor exists, so both sides are the legacy apply:
the harness proves itself deterministic on every registered-deck mainboard
and sideboard SPELL, MODE, ACTIVATED and executable LOYALTY host (exit
criterion 5; triggered and static hosts have no single legacy apply yet and
are reported skipped), and pins the hosts whose resolution changes no state
on any board.

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
    seen = {"SPELL": 0, "MODE": 0, "ACTIVATED": 0, "LOYALTY": 0, "ETB": 0,
            "DRAW": 0}
    for t in h.deck_templates(card_db):
        ce = parse_template(t)
        ok, skipped = h.host_cases(t, ce)
        # a head naming both an enter and a draw event has a case for each
        assert len({c.host for c in ok}) + len(skipped) == sum(
            1 for _ in ce.walk(include_sub=False))
        for c in ok:
            seen[c.kind] += 1
    assert all(n > 0 for n in seen.values()), seen


def _synthetic(name, types, text):
    from engine.cards import CardTemplate, CardType, ManaCost
    return CardTemplate(name=name, card_types=[CardType(t) for t in types],
                        mana_cost=ManaCost(), oracle_text=text)


def test_legacy_targets_are_chosen_on_each_board_from_the_hosts_requirements(boards):
    from engine.constants import PLAYER_TARGET_OPPONENT
    h = _h()
    _pool, built = boards
    kill = _synthetic("Synthetic Kill", ["instant"],
                      "Destroy target creature.")
    case = h.HostCase(kill.name, "SPELL:0:0", "SPELL")
    opp = _copy(built["opponent_creatures"])
    card = h.legacy_place(opp, kill, case)
    ids = h.legacy_targets(opp, kill, case, card)
    assert len(ids) == 1
    target = next(c for c in opp.players[1].battlefield
                  if c.instance_id == ids[0])
    assert "creature" in h._types(target.template)
    # the same rule picks the same object on every copy of the board
    again = _copy(built["opponent_creatures"])
    assert h.legacy_targets(again, kill, case,
                            h.legacy_place(again, kill, case)) == ids
    # with only the controller's creatures, the pick falls back to them
    own = _copy(built["own_creatures"])
    own_ids = h.legacy_targets(own, kill, case, h.legacy_place(own, kill, case))
    assert own_ids and all(any(c.instance_id == i for c in
                               own.players[0].battlefield) for i in own_ids)
    # no legal object: no target
    empty = _copy(built["empty"])
    assert h.legacy_targets(empty, kill, case,
                            h.legacy_place(empty, kill, case)) == []
    # "any target" is the opponent's face
    bolt = _synthetic("Synthetic Bolt", ["instant"],
                      "Synthetic Bolt deals 3 damage to any target.")
    bcase = h.HostCase(bolt.name, "SPELL:0:0", "SPELL")
    e = _copy(built["empty"])
    assert h.legacy_targets(e, bolt, bcase, h.legacy_place(e, bolt, bcase)) \
        == [PLAYER_TARGET_OPPONENT]


def test_a_targeted_spell_resolved_with_its_chosen_target_changes_the_board(card_db, boards):
    h = _h()
    _pool, built = boards
    # "Target creature gets -5/-5 until end of turn." resolves on the
    # target it is handed and picks none itself
    kill = card_db.cards["Dismember"]
    case = h.HostCase(kill.name, "SPELL:0:0", "SPELL")
    base = built["opponent_creatures"]
    assert h.resolve_once(base, kill, case, 0).digest != \
        h.placed_digest(base, kill, case)
    # nothing to target: the resolution changes nothing, and says so
    assert h.resolve_once(built["empty"], kill, case, 0).digest == \
        h.placed_digest(built["empty"], kill, case)


NOOP_FIXTURE = REPO / "tests" / "fixtures" / "host_harness_noop_hosts.json"


# The E0 self-check over every registered-deck MB and SB host with a
# legacy apply: 239 hosts (enter triggers since unit E, draw triggers
# since unit D) x 6
# boards x 2 seeds x 2 sides, plus one placed-only copy per (host, board)
# for the no-op report. Measured 2026-10-09: ~95 s on a quiet 4-core box
# (2026-10-02, 175 hosts: ~53 s CPU), plus ~18 s when first in the process
# to load the shared card DB. 900 s bounds a hang on a 2-core CI runner.
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
    import json
    pinned = json.loads(NOOP_FIXTURE.read_text())
    assert rep["hosts"] == pinned["hosts"], rep["hosts"]
    assert rep["boards"] == list(h.BOARDS) and rep["seeds"] == list(h.SEEDS)
    # "deterministic" is claimed only for resolutions that do something:
    # the hosts that change no state on any board are a pinned set, so a
    # harness that stops choosing targets (or a host that starts doing
    # nothing) fails here instead of passing vacuously.
    assert rep["noop_hosts"] == pinned["noop_hosts"], sorted(
        set(map(tuple, rep["noop_hosts"])) ^ set(map(tuple,
                                                     pinned["noop_hosts"])))
    assert rep["state_changing_hosts"] == rep["hosts"] - len(
        rep["noop_hosts"])
    assert rep["state_changing_hosts"] >= pinned["state_changing_floor"]
    # the hosts with no single legacy apply are reported by kind
    assert set(rep["skipped_by_kind"]) >= {"TRIGGERED", "STATIC"}


# Each registered-deck pair on the new path, on every board and seed, both
# ways. Measured 2026-10-08: ~2 s CPU for the 7 pairs, plus the closure's
# deck parse and, when first in the process, ~18 s for the shared card DB.
@pytest.mark.timeout(900)
def test_every_registered_deck_host_on_the_new_path_resolves_as_its_legacy_apply_or_records_why_not(card_db):
    """A38: a switched carrier takes the dispatcher for a strict,
    executable host. Through it and through its legacy apply
    (`legacy_only`), every board and seed gives the same state digest, log
    bytes and result -- or the pair is a recorded intended change with its
    reason -- and the dispatcher was entered, so the proof is about the
    new path. Every pair is in the record gate parity reads."""
    h = _h()
    rep = h.switched_check(card_db, h.deck_templates(card_db))
    assert rep["undispatched"] == [] and rep["no_case"] == []
    intended = h.load_intended_changes()
    diverging = {tuple(k) for k in rep["diverging"]}
    assert diverging <= set(intended)
    assert all(intended[k] for k in diverging)
    proven = {tuple(k) for k in rep["proven"]}
    assert proven and len(proven) + len(diverging) == rep["pairs"]
    assert proven <= h.load_switched_record()
