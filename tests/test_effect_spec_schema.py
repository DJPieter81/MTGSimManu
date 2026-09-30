"""The typed effect model's schema (engine/effect_spec.py).

Design doc 2026-09-29, section 2 (A31 frozen costs, A21 residue polarity,
F10 canonical form). The schema is data only: every class is a frozen,
slotted dataclass, so a parsed CardEffects is hashable, picklable and can be
shared across the parse memo, and no mutable object (in particular no
mutable ActivationCost) is reachable from it. `canonical()` is the
hash-seed-independent text form the determinism tests compare.
`validate_spec` checks the eight schema invariants, reports the violated
rule by name and never raises; a violating spec is lowered to
UNMODELLED(INVALID). The module parses nothing and imports no game state.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


# ── fixtures ────────────────────────────────────────────────────────────

def _sample():
    """A small but structurally rich CardEffects: two faces, a modal host,
    a loyalty host, an activated host with a frozen cost, and a delayed
    sub-ability whose host has its own target."""
    from engine import effect_spec as es
    from engine.delayed_triggers import DelayedTriggerTiming
    from engine.effect_model import THIS_TURN, Modification, ModKind
    from engine.target_solver import TargetRequirement

    creature = TargetRequirement(zone="battlefield",
                                 types=frozenset({"creature", "planeswalker"}),
                                 raw_phrase="target creature or planeswalker")
    exile_later = es.AbilityEffects(
        kind=es.HostKind.TRIGGERED, face=0, index=0, text="exile it",
        specs=(es.EffectSpec(verb=es.Verb.EXILE,
                             ref=es.Ref(es.RefKind.RESULT, index=0),
                             seq=1, raw="exile it"),),
        trigger=es.TriggerHead(event_hints=(es.EventHint.DELAYED,),
                               raw="at the beginning of the next end step"))
    spell = es.AbilityEffects(
        kind=es.HostKind.SPELL, face=0, index=0,
        text="~ deals 3 damage to target creature or planeswalker.",
        targets=(creature,),
        specs=(
            es.EffectSpec(verb=es.Verb.DAMAGE, target=creature, target_slot=0,
                          other=es.Ref(es.RefKind.SELF),
                          amount=es.Amount(es.AmountKind.LITERAL, n=3),
                          residue=("target.colored",), seq=0,
                          span=(0, 52), raw="~ deals 3 damage"),
            es.EffectSpec(verb=es.Verb.CONTINUOUS,
                          ref=es.Ref(es.RefKind.RESULT, index=0),
                          payload=Modification(ModKind.MODIFY_PT,
                                               data=(("power", 1),)),
                          duration=THIS_TURN, seq=1, raw="gets +1/+0"),
            es.EffectSpec(verb=es.Verb.CREATE_TRIGGER,
                          payload=es.SubAbility(
                              kind=es.SubAbilityKind.DELAYED,
                              timing=DelayedTriggerTiming.NEXT_END_STEP,
                              host=exile_later),
                          seq=2, raw="exile it at the beginning of the next end step"),
        ))
    mode_a = es.AbilityEffects(kind=es.HostKind.MODE, face=0, index=1,
                               mode_index=0, text="draw a card",
                               specs=(es.EffectSpec(verb=es.Verb.DRAW,
                                                    actor=es.Ref(es.RefKind.SELF),
                                                    amount=es.Amount(es.AmountKind.LITERAL, n=1),
                                                    raw="draw a card"),))
    modal = es.AbilityEffects(kind=es.HostKind.TRIGGERED, face=0, index=1,
                              choose=(1, 1), modes=(mode_a,),
                              trigger=es.TriggerHead(
                                  event_hints=(es.EventHint.SELF_ENTERS,
                                               es.EventHint.SELF_ATTACKS),
                                  raw="whenever ~ enters or attacks"))
    cost = es.freeze_cost(_activation_cost())
    act = es.AbilityEffects(kind=es.HostKind.ACTIVATED, face=0, index=2,
                            activation_index=0, cost=cost,
                            specs=(es.EffectSpec(verb=es.Verb.UNMODELLED,
                                                 payload=es.Unmodelled(es.Stage.CLAUSE, "fling"),
                                                 raw="fling it"),))
    loyal = es.AbilityEffects(kind=es.HostKind.LOYALTY, face=1, index=0,
                              loyalty_cost=es.Amount(es.AmountKind.X, n=-1),
                              loyalty_slot="minus")
    return es.CardEffects.of(((spell, modal, act), (loyal,)))


def _activation_cost():
    from engine.cards import ActivationCost
    from engine.mana import ManaCost
    return ActivationCost(
        mana=ManaCost(generic=1, black=1, phyrexian={"B": 1},
                      hybrid=[("R", "G"), ("W", "2")]),
        tap_self=True, sacrifice_type="creature", unpayable=("pay 2 life",))


# ── frozen, slotted, hashable ──────────────────────────────────────────

def _schema_classes():
    from engine import effect_spec as es
    return [v for v in vars(es).values()
            if isinstance(v, type) and dataclasses.is_dataclass(v)
            and v.__module__ == es.__name__]


def test_every_schema_class_is_a_frozen_slotted_dataclass():
    classes = _schema_classes()
    names = {c.__name__ for c in classes}
    assert {"Ref", "CardFilter", "Amount", "Quantity", "CostSnapshot",
            "Condition", "Destination", "CounterSpec", "ManaSpec",
            "KeywordSpec", "KeywordAction", "Granted", "TokenSpec",
            "SubAbility", "Unmodelled", "EffectSpec", "TriggerHead",
            "AbilityEffects", "CardEffects"} <= names
    for c in classes:
        assert c.__dataclass_params__.frozen, c
        assert "__slots__" in vars(c), c


def test_card_effects_are_hashable_equal_by_value_and_picklable():
    a, b = _sample(), _sample()
    assert a == b and hash(a) == hash(b)
    assert pickle.loads(pickle.dumps(a)) == a
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.verbs = frozenset()


def test_the_schema_holds_no_mutable_cost_and_thaw_hands_out_fresh_objects():
    from engine import effect_spec as es
    cost = _activation_cost()
    snap = es.freeze_cost(cost)
    hash(snap)
    one, two = snap.thaw(), snap.thaw()
    assert one == cost and two == cost and one is not two
    one.mana.phyrexian["B"] = 9
    one.mana.hybrid.append(("U",))
    assert snap.thaw() == cost        # the snapshot is untouched
    assert es.find_mutable(_sample()) is None
    assert es.find_mutable(cost) is not None


def test_a_cost_snapshot_round_trips_every_parsed_activation_cost():
    from engine import effect_spec as es
    from tests._card_db_cache import shared_card_database
    db = shared_card_database()
    n = 0
    for t in {id(v): v for v in db.cards.values()}.values():
        for ab in getattr(t, "activated_abilities", None) or ():
            snap = es.freeze_cost(ab.cost)
            hash(snap)
            assert snap.thaw() == ab.cost, t.name
            assert es.freeze_cost(snap.thaw()) == snap
            n += 1
    assert n > 1000


# ── canonical form ─────────────────────────────────────────────────────

def test_canonical_sorts_sets_and_names_enums():
    from engine import effect_spec as es
    s = es.canonical(frozenset({"b", "a", "c"}))
    assert s.index("'a'") < s.index("'b'") < s.index("'c'")
    assert "Verb.DESTROY" in es.canonical(es.Verb.DESTROY)
    assert es.canonical(_sample()) == es.canonical(_sample())


_HASHSEED_SCRIPT = (
    "import hashlib, sys; sys.path.insert(0, %r);"
    "from tests.test_effect_spec_schema import _sample;"
    "from engine.effect_spec import canonical;"
    "print(hashlib.sha256(canonical(_sample()).encode()).hexdigest())"
)


def test_canonical_is_independent_of_the_hash_seed():
    outs = []
    for seed in ("0", "1"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outs.append(subprocess.run(
            [sys.executable, "-c", _HASHSEED_SCRIPT % str(REPO)],
            capture_output=True, text=True, env=env, cwd=REPO,
            check=True).stdout.strip())
    assert outs[0] == outs[1] and len(outs[0]) == 64


# ── invariants ─────────────────────────────────────────────────────────

def _host_with(*specs, targets=()):
    from engine import effect_spec as es
    return es.AbilityEffects(kind=es.HostKind.SPELL, face=0, index=0,
                             specs=tuple(specs), targets=tuple(targets))


def test_every_well_formed_sample_spec_satisfies_the_invariants():
    from engine import effect_spec as es
    ce = _sample()
    for host in ce.walk(include_sub=True):
        for spec in host.specs:
            assert es.validate_spec(spec, host) is None, spec
    assert es.validate_card_effects(ce) is None


def _violations():
    """(rule, spec, host) for each of the eight invariants."""
    from engine import effect_spec as es
    from engine.effect_model import THIS_TURN, Modification, ModKind
    from engine.cards import ActivationCost
    from engine.target_solver import TargetRequirement
    V, R = es.Verb, es.Ref
    t0 = TargetRequirement(zone="battlefield", types=frozenset({"creature"}))
    t_copy = TargetRequirement(zone="battlefield", types=frozenset({"creature"}))
    sel = es.CardFilter(types=frozenset({"creature"})).as_selector()
    cases = [
        ("principal", es.EffectSpec(verb=V.DESTROY, target=t0, target_slot=0,
                                    ref=R(es.RefKind.SELF), raw="x"), (t0,)),
        ("principal", es.EffectSpec(verb=V.DRAW, subject=sel, raw="x"), ()),
        ("target_slot", es.EffectSpec(verb=V.DESTROY, target=t0, raw="x"), (t0,)),
        ("target_slot", es.EffectSpec(verb=V.DESTROY, target=t_copy,
                                      target_slot=0, raw="x"), (t0,)),
        ("target_slot", es.EffectSpec(verb=V.DESTROY, target=t0,
                                      target_slot=3, raw="x"), (t0,)),
        ("ref_order", es.EffectSpec(verb=V.EXILE, ref=R(es.RefKind.RESULT, index=2),
                                    seq=1, raw="x"), ()),
        ("ref_order", es.EffectSpec(verb=V.DESTROY, replaces=(4,), seq=2,
                                    raw="x"), ()),
        ("unmodelled", es.EffectSpec(verb=V.UNMODELLED, raw="x"), ()),
        ("unmodelled", es.EffectSpec(verb=V.UNMODELLED,
                                     payload=es.Unmodelled(es.Stage.CLAUSE)), ()),
        ("payload", es.EffectSpec(verb=V.CONTINUOUS, ref=R(es.RefKind.SELF),
                                  raw="x"), ()),
        ("payload", es.EffectSpec(verb=V.CREATE_TRIGGER, raw="x"), ()),
        ("duration", es.EffectSpec(verb=V.DESTROY, ref=R(es.RefKind.SELF),
                                   duration=THIS_TURN, raw="x"), ()),
        ("duration", es.EffectSpec(verb=V.EXILE, ref=R(es.RefKind.SELF),
                                   duration=THIS_TURN, raw="x"), ()),
        ("residue", es.EffectSpec(verb=V.DESTROY, target=t0, target_slot=0,
                                  residue=("target.made_up",), raw="x"), (t0,)),
        ("residue", es.EffectSpec(verb=V.DESTROY, target=t0, target_slot=0,
                                  residue=("target.keyword:",), raw="x"), (t0,)),
        ("immutable", es.EffectSpec(verb=V.PAY, payload=ActivationCost(),
                                    raw="x"), ()),
    ]
    return [(rule, spec, _host_with(spec, targets=tg)) for rule, spec, tg in cases]


def test_each_schema_invariant_names_its_violation():
    from engine import effect_spec as es
    seen = set()
    for rule, spec, host in _violations():
        got = es.validate_spec(spec, host)
        assert got is not None and got.split(":")[0] == rule, (rule, got, spec)
        seen.add(rule)
    assert seen == set(es.SCHEMA_INVARIANTS)
    assert len(es.SCHEMA_INVARIANTS) == 8


def test_a_player_target_may_designate_the_actor_of_an_actor_only_verb():
    from engine import effect_spec as es
    from engine.target_solver import TargetRequirement
    p = TargetRequirement(zone="any", types=frozenset({"player"}))
    spec = es.EffectSpec(verb=es.Verb.DRAW, target=p, target_slot=0,
                         actor=es.Ref(es.RefKind.TARGET, index=0),
                         amount=es.Amount(es.AmountKind.LITERAL, n=2), raw="x")
    assert es.validate_spec(spec, _host_with(spec, targets=(p,))) is None
    c = TargetRequirement(zone="battlefield", types=frozenset({"creature"}))
    bad = dataclasses.replace(spec, target=c)
    assert es.validate_spec(bad, _host_with(bad, targets=(c,))).startswith("principal")


def test_a_violation_lowers_the_spec_to_unmodelled_invalid():
    from engine import effect_spec as es
    for rule, spec, host in _violations():
        low = es.enforce_invariants(spec, host)
        assert low.verb is es.Verb.UNMODELLED
        assert low.payload.stage is es.Stage.INVALID
        assert low.payload.detail.split(":")[0] == rule
        assert low.raw and low.seq == spec.seq and low.span == spec.span
        assert es.validate_spec(low, host) is None


def test_validate_spec_never_raises():
    from engine import effect_spec as es
    junk = [None, 0, "spec", object(), es.EffectSpec(verb=es.Verb.DRAW),
            es.EffectSpec(verb=None), es.EffectSpec(verb=es.Verb.DESTROY,
                                                    target_slot=0)]
    for spec in junk:
        for host in (None, 1, _host_with()):
            got = es.validate_spec(spec, host)
            assert got is None or isinstance(got, str)


def test_the_card_effects_hash_invariant_is_checked_whole():
    from engine import effect_spec as es
    assert es.validate_card_effects(_sample()) is None
    assert es.validate_card_effects(object()) is not None


# ── residue polarity ───────────────────────────────────────────────────

def test_residue_codes_declare_a_polarity():
    from engine import effect_spec as es
    assert set(es.RESIDUE_CODES.values()) <= {es.WIDENING, es.NARROWING, es.UNPARSED}
    table = {
        "target.scope:opponent": es.WIDENING, "target.scope:not_you": es.WIDENING,
        "target.exclude_source": es.WIDENING, "target.keyword:flying": es.WIDENING,
        "target.state:tapped": es.WIDENING, "target.color:red": es.WIDENING,
        "target.colored": es.WIDENING, "target.nontoken": es.WIDENING,
        "target.historic": es.WIDENING, "target.stat:power": es.WIDENING,
        "target.single_graveyard": es.WIDENING,
        "target.dependent_controller": es.WIDENING,
        "target.total_mv": es.WIDENING, "target.conjunctive_types": es.WIDENING,
        "target.union:artifact": es.NARROWING,
        "target.zone_union": es.UNPARSED, "target.unparsed": es.UNPARSED,
    }
    for code, pol in table.items():
        assert es.residue_polarity(code) == pol, code
    for bad in ("target.keyword:", "target.made_up", "", "target.scope:you"):
        assert es.residue_polarity(bad) is None, bad


# ── accessors ──────────────────────────────────────────────────────────

def test_empty_effects_answers_every_accessor_with_nothing():
    from engine import effect_spec as es
    e = es.EMPTY_EFFECTS
    assert e.front() == () and e.spell() is None and e.modes() == ()
    assert e.loyalty("plus") is None and e.activated(0) is None
    assert list(e.walk()) == [] and e.unmodelled() == ()
    assert e.verbs == frozenset() and hash(e) == hash(es.CardEffects())


def test_accessors_find_hosts_by_kind_slot_and_activation_index():
    from engine import effect_spec as es
    ce = _sample()
    assert ce.spell().kind is es.HostKind.SPELL
    assert [m.mode_index for m in ce.modes()] == [0]
    assert ce.activated(0).cost is not None and ce.activated(1) is None
    assert ce.loyalty("minus", face=1).loyalty_cost.n == -1
    assert ce.loyalty("minus") is None
    assert es.Verb.DAMAGE in ce.verbs and es.Verb.EXILE in ce.verbs
    (host, spec), = ce.unmodelled()
    assert host.kind is es.HostKind.ACTIVATED and spec.payload.lemma == "fling"


def test_walk_is_pre_order_and_includes_sub_ability_hosts_on_request():
    from engine import effect_spec as es
    ce = _sample()
    kinds = [h.kind for h in ce.walk()]
    assert kinds == [es.HostKind.SPELL, es.HostKind.TRIGGERED,
                     es.HostKind.TRIGGERED, es.HostKind.MODE,
                     es.HostKind.ACTIVATED, es.HostKind.LOYALTY]
    assert len(list(ce.walk(include_sub=False))) == 5


def test_with_face_replaces_one_face_and_recomputes_verbs():
    from engine import effect_spec as es
    ce = _sample()
    ce2 = ce.with_face(0, ())
    assert ce2.front() == () and ce2.faces[1] == ce.faces[1]
    assert es.Verb.DAMAGE not in ce2.verbs
    ce3 = es.EMPTY_EFFECTS.with_face(1, ce.faces[1])
    assert ce3.faces == ((), ce.faces[1])


def test_a_card_filter_becomes_a_filter_selector_of_its_set_entries():
    from engine import effect_spec as es
    from engine.effect_model import SelectorKind, is_supported_filter_entry
    f = es.CardFilter(controller="opponents",
                      without_keywords=frozenset({"flying"}), raw="…")
    sel = f.as_selector()
    assert sel.kind is SelectorKind.FILTER and sel.player is None
    assert dict(sel.filter) == {"controller": "opponents",
                                "without_keyword": "flying"}
    assert all(is_supported_filter_entry(k, v) for k, v in sel.filter)
    two = es.CardFilter(without_keywords=frozenset({"flying", "reach"}))
    assert not any(is_supported_filter_entry(k, v) for k, v in two.as_tuple())


# ── no game state ──────────────────────────────────────────────────────

def test_the_schema_imports_no_game_state_and_parses_nothing():
    code = ("import sys; import engine.effect_spec;"
            "bad=[m for m in sys.modules if m.startswith('ai') or m in ("
            "'engine.game_state','engine.cards','engine.card_database',"
            "'engine.oracle_parser','engine.player_state','engine.mana')];"
            "print(bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=REPO, check=True).stdout.strip()
    assert out == "[]"
    src = (REPO / "engine" / "effect_spec.py").read_text()
    assert "import re" not in src and "re.compile" not in src
