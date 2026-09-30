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
                             seq=3, raw="exile it"),),
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


def _required_args(cls) -> dict:
    """Placeholders for a schema class's fields without a default (the
    payload classes' only such field is a name string)."""
    return {f.name: "x" for f in dataclasses.fields(cls)
            if f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING}


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
    for host in ce.walk(include_sub=False):
        for spec in es.iter_specs(host.specs):
            assert es.validate_spec(spec, host) is None, spec
    # The delayed exile names its parent's result: valid only with the
    # parent in view (invariant 3's sub-ability exception).
    spell = ce.spell()
    sub = spell.specs[2].payload.host
    (exile,) = sub.specs
    assert es.validate_spec(exile, sub, parents=(spell,)) is None
    assert es.validate_spec(exile, sub) == "ref_order:cross_host"
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
    player = TargetRequirement(zone="any", types=frozenset({"player"}))
    sel = es.CardFilter(types=frozenset({"creature"})).as_selector()
    cases = [
        ("principal", es.EffectSpec(verb=V.DESTROY, target=t0, target_slot=0,
                                    ref=R(es.RefKind.SELF), raw="x"), (t0,)),
        ("principal", es.EffectSpec(verb=V.DRAW, subject=sel, raw="x"), ()),
        ("principal", es.EffectSpec(verb=V.LOSE_LIFE, target=player,
                                    target_slot=0, raw="x"), (player,)),
        ("ref_order", es.EffectSpec(verb=V.MOVE, ref=R(es.RefKind.RESULT, index=1),
                                    seq=5, raw="x"), ()),
        ("ref_order", es.EffectSpec(verb=V.TAP, ref=R(es.RefKind.TARGET, index=0),
                                    raw="x"), ()),
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


def test_an_actor_only_verb_has_no_principal_and_a_targeted_player_acts_through_an_actor_ref():
    """Invariant 1: "target player draws two cards" has one encoding --
    DRAW(actor=Ref(TARGET, 0)) with the requirement only in the host's
    targets (CR 115.1) -- never a player requirement in the target slot."""
    from engine import effect_spec as es
    from engine.target_solver import TargetRequirement
    p = TargetRequirement(zone="any", types=frozenset({"player"}))
    two = es.Amount(es.AmountKind.LITERAL, n=2)
    actor = es.Ref(es.RefKind.TARGET, index=0)
    for verb in (es.Verb.DRAW, es.Verb.LOSE_LIFE, es.Verb.MILL,
                 es.Verb.SEARCH, es.Verb.ADD_MANA):
        good = es.EffectSpec(verb=verb, actor=actor, amount=two, raw="x")
        assert es.validate_spec(good, _host_with(good, targets=(p,))) is None, verb
        for bad in (es.EffectSpec(verb=verb, target=p, target_slot=0,
                                  amount=two, raw="x"),
                    es.EffectSpec(verb=verb, target=p, target_slot=0,
                                  actor=actor, amount=two, raw="x"),
                    es.EffectSpec(verb=verb, ref=es.Ref(es.RefKind.SELF),
                                  amount=two, raw="x")):
            got = es.validate_spec(bad, _host_with(bad, targets=(p,)))
            assert got == "principal:actor_only", (verb, got)
    # A verb that also takes an object keeps the object as its one
    # principal while a targeted player acts ("target player sacrifices a
    # creature" has no principal; "discard it" has a ref).
    sac = es.EffectSpec(verb=es.Verb.SACRIFICE, actor=actor,
                        filter=es.CardFilter(types=frozenset({"creature"})),
                        amount=es.Amount(es.AmountKind.LITERAL, n=1), raw="x")
    assert es.validate_spec(sac, _host_with(sac, targets=(p,))) is None
    dis = es.EffectSpec(verb=es.Verb.DISCARD, actor=actor,
                        ref=es.Ref(es.RefKind.SELF), raw="x")
    assert es.validate_spec(dis, _host_with(dis, targets=(p,))) is None


def _host(kind="SPELL", index=0, specs=(), targets=(), modes=()):
    from engine import effect_spec as es
    return es.AbilityEffects(kind=es.HostKind[kind], face=0, index=index,
                             specs=tuple(specs), targets=tuple(targets),
                             modes=tuple(modes))


def _delayed(host, seq):
    from engine import effect_spec as es
    from engine.delayed_triggers import DelayedTriggerTiming
    return es.EffectSpec(verb=es.Verb.CREATE_TRIGGER, seq=seq, raw="later",
                         payload=es.SubAbility(es.SubAbilityKind.DELAYED,
                                               DelayedTriggerTiming.NEXT_END_STEP,
                                               host))


def test_a_reference_never_crosses_hosts_except_from_a_sub_ability_to_the_hosts_that_created_it():
    """Invariant 3: a RESULT index or a replaces seq names an earlier spec of
    the SAME host; a sub-ability host may also name specs of the hosts that
    created it (captured in its snapshot, A34), transitively, and a mode
    shares its modal host's creators. A TARGET index names one of the host's
    own targets. Another ability's specs, a granting ability's specs, and a
    seq no spec has are all outside."""
    from engine import effect_spec as es
    from engine.target_solver import TargetRequirement
    V, R, K = es.Verb, es.Ref, es.RefKind
    t0 = TargetRequirement(zone="battlefield", types=frozenset({"creature"}))

    def card(*hosts):
        return es.CardEffects.of((hosts,))

    def check(*hosts):
        got = es.validate_card_effects(card(*hosts))
        return got.split(" @")[0] if got else None

    destroy = es.EffectSpec(verb=V.DESTROY, target=t0, target_slot=0, seq=0, raw="x")
    # A RESULT index no spec of the host carries (the reviewer's probe).
    dangling = es.EffectSpec(verb=V.MOVE, ref=R(K.RESULT, index=1), seq=5,
                             dest=es.Destination("hand"), raw="x")
    assert check(_host(specs=(destroy, dangling), targets=(t0,))) == "ref_order:cross_host"
    # A replaces seq naming no sibling.
    instead = es.EffectSpec(verb=V.EXILE, target=t0, target_slot=0,
                            replaces=(3,), seq=6, raw="x")
    assert check(_host(specs=(destroy, instead), targets=(t0,))) == "ref_order:cross_host"
    # Another ability's spec, even with a lower seq.
    other_ability = _host(index=1, specs=(
        es.EffectSpec(verb=V.TAP, ref=R(K.RESULT, index=0), seq=2, raw="x"),))
    assert check(_host(specs=(destroy,), targets=(t0,)), other_ability) == "ref_order:cross_host"
    # A same-host RESULT and a same-host TARGET ref are fine.
    follow = es.EffectSpec(verb=V.TAP, ref=R(K.RESULT, index=0), seq=1, raw="x")
    tgt = es.EffectSpec(verb=V.UNTAP, ref=R(K.TARGET, index=0), seq=2, raw="x")
    assert check(_host(specs=(destroy, follow, tgt), targets=(t0,))) is None

    # Sub-abilities: the delayed host may name its creator's result, and a
    # sub-ability of it may name its grandparent's; a mode of a modal
    # sub-ability host shares those creators.
    inner = _host(kind="TRIGGERED", specs=(
        es.EffectSpec(verb=V.EXILE, ref=R(K.RESULT, index=0), seq=4, raw="x"),))
    middle = _host(kind="TRIGGERED", specs=(
        es.EffectSpec(verb=V.TAP, ref=R(K.RESULT, index=0), seq=2, raw="x"),
        _delayed(inner, 3)))
    assert check(_host(specs=(destroy, _delayed(middle, 1)), targets=(t0,))) is None
    mode = _host(kind="MODE", specs=(
        es.EffectSpec(verb=V.TAP, ref=R(K.RESULT, index=0), seq=3, raw="x"),))
    modal_sub = _host(kind="TRIGGERED", modes=(mode,))
    assert check(_host(specs=(destroy, _delayed(modal_sub, 2)), targets=(t0,))) is None
    # ...but it may not replace its creator's spec, nor use its targets.
    replacing = _host(kind="TRIGGERED", specs=(
        es.EffectSpec(verb=V.TAP, ref=R(K.SELF), replaces=(0,), seq=2, raw="x"),))
    assert check(_host(specs=(destroy, _delayed(replacing, 1)), targets=(t0,))) == "ref_order:cross_host"
    parent_target = _host(kind="TRIGGERED", specs=(
        es.EffectSpec(verb=V.TAP, ref=R(K.TARGET, index=0), seq=2, raw="x"),))
    assert check(_host(specs=(destroy, _delayed(parent_target, 1)), targets=(t0,))) == "ref_order:cross_host"

    # A granted ability is its own ability: it never names the granter's
    # specs, whether an emblem or a token carries it.
    granted = _host(kind="GRANTED", specs=(
        es.EffectSpec(verb=V.TAP, ref=R(K.RESULT, index=0), seq=2, raw="x"),))
    emblem = es.EffectSpec(verb=V.CREATE_EMBLEM, seq=1, raw="x",
                           payload=es.Granted(hosts=(granted,)))
    assert check(_host(specs=(destroy, emblem), targets=(t0,))) == "ref_order:cross_host"
    token = es.EffectSpec(verb=V.CREATE_TOKEN, seq=1, raw="x",
                          payload=es.TokenSpec(granted=(granted,)))
    assert check(_host(specs=(destroy, token), targets=(t0,))) == "ref_order:cross_host"

    # Without a host nothing can be resolved, so nothing is assumed.
    assert es.validate_spec(follow) == "ref_order:no_host"
    assert es.validate_spec(tgt) == "ref_order:no_host"
    assert es.validate_spec(dataclasses.replace(
        tgt, ref=R(K.TARGET)), _host(targets=(t0,))) == "ref_order:target"


def test_a_violation_lowers_the_spec_to_unmodelled_invalid():
    from engine import effect_spec as es
    for rule, spec, host in _violations():
        low = es.enforce_invariants(spec, host)
        assert low.verb is es.Verb.UNMODELLED
        assert low.payload.stage is es.Stage.INVALID
        assert low.payload.detail.split(":")[0] == rule
        assert low.raw and low.seq == spec.seq and low.span == spec.span
        assert es.validate_spec(low, host) is None


def test_a_lowered_spec_satisfies_every_invariant_even_when_a_carried_field_is_malformed():
    """The lowering keeps seq, span and raw only when they are well-formed;
    a violation IN one of them must not survive into the UNMODELLED(INVALID)
    spec (which would again be invalid and unhashable)."""
    from engine import effect_spec as es
    D = es.Verb.DRAW
    sel = es.CardFilter().as_selector()      # a principal DRAW may not have
    cases = [
        # the violation is in the carried field itself
        (es.EffectSpec(verb=D, span=[0, 3], raw="draw"), "immutable"),
        (es.EffectSpec(verb=D, raw=["draw"]), "immutable"),
        (es.EffectSpec(verb=D, seq=[1], raw="draw"), "immutable"),
        (es.EffectSpec(verb=D, seq="1", replaces=(0,), raw="draw"), None),
        # the violation is elsewhere; the carried field is still malformed
        (es.EffectSpec(verb=D, subject=sel, span=(0, 3, 4), raw="draw"), "principal"),
        (es.EffectSpec(verb=D, subject=sel, span=("a", "b"), raw="draw"), "principal"),
        (es.EffectSpec(verb=D, subject=sel, raw=b"draw"), "principal"),
        (es.EffectSpec(verb=D, subject=sel, raw=""), "principal"),
    ]
    for spec, rule in cases:
        got = es.validate_spec(spec)
        assert got is not None, spec
        if rule is not None:
            assert got.split(":")[0] == rule, (got, spec)
        low = es.enforce_invariants(spec)
        assert es.validate_spec(low) is None, (low, spec)
        hash(low)
        assert isinstance(low.raw, str) and low.raw
        assert isinstance(low.seq, int)
        assert isinstance(low.span, tuple) and len(low.span) == 2
    kept = es.enforce_invariants(es.EffectSpec(verb=D, span=[2, 9], seq=4, raw="draw two"))
    assert (kept.span, kept.seq, kept.raw) == ((2, 9), 4, "draw two")
    assert es.validate_spec(es.enforce_invariants(None)) is None


def test_validate_spec_never_raises():
    from engine import effect_spec as es
    junk = [None, 0, "spec", object(), es.EffectSpec(verb=es.Verb.DRAW),
            es.EffectSpec(verb=None), es.EffectSpec(verb=es.Verb.DESTROY,
                                                    target_slot=0)]
    junk.append(es.EffectSpec(verb=es.Verb.TAP, seq=3,
                              ref=es.Ref(es.RefKind.RESULT, index=1)))
    for spec in junk:
        for host in (None, 1, _host_with()):
            for parents in ((), None, (1, None), "ab", (_host_with(),)):
                got = es.validate_spec(spec, host, parents)
                assert got is None or isinstance(got, str)


def test_the_card_effects_hash_invariant_is_checked_whole():
    from engine import effect_spec as es
    assert es.validate_card_effects(_sample()) is None
    assert es.validate_card_effects(object()) is not None


def test_every_spec_a_payload_nests_is_walked_validated_and_indexed():
    """Specs inside a token's copy exceptions (and a keyword action's
    expansion) are specs of the same host: validate_card_effects checks
    them, unmodelled() reports them and the verbs index includes them."""
    from engine import effect_spec as es
    V = es.Verb
    um = es.EffectSpec(verb=V.UNMODELLED, raw="except it's a spirit",
                       payload=es.Unmodelled(es.Stage.CLAUSE, "except"))
    bad = es.EffectSpec(verb=V.CONTINUOUS, ref=es.Ref(es.RefKind.SELF), raw="x")
    for carrier in (es.TokenSpec(copy_except=(um,)),
                    es.KeywordAction("amass", expansion=(um,))):
        verb = V.CREATE_TOKEN if isinstance(carrier, es.TokenSpec) else V.KEYWORD_ACTION
        host = _host(specs=(es.EffectSpec(verb=verb, payload=carrier, raw="x"),))
        ce = es.CardEffects.of(((host,),))
        assert [s for _, s in ce.unmodelled()] == [um], carrier
        assert {verb, V.UNMODELLED} <= ce.verbs, carrier
        assert es.validate_card_effects(ce) is None
        broken = dataclasses.replace(carrier, **{
            "copy_except" if isinstance(carrier, es.TokenSpec) else "expansion": (bad,)})
        host = _host(specs=(es.EffectSpec(verb=verb, payload=broken, raw="x"),))
        got = es.validate_card_effects(es.CardEffects.of(((host,),)))
        assert got is not None and got.startswith("payload:continuous"), (carrier, got)


def test_every_schema_field_that_holds_effect_specs_is_walked():
    """A spec-bearing payload field that iter_specs forgets would hide its
    specs from validation and the census: every EffectSpec-typed field of a
    payload class is declared in PAYLOAD_SPEC_FIELDS (EffectSpec's own
    branches and AbilityEffects.specs are walked directly)."""
    from engine import effect_spec as es
    walked_directly = {(es.EffectSpec, "then"), (es.EffectSpec, "otherwise"),
                       (es.EffectSpec, "alternatives"), (es.AbilityEffects, "specs")}
    spec_fields = {(c, f.name) for c in _schema_classes()
                   for f in dataclasses.fields(c)
                   if "EffectSpec" in str(f.type)}
    declared = {(c, name) for c, name in es.PAYLOAD_SPEC_FIELDS.items()}
    assert spec_fields == walked_directly | declared
    marker = es.EffectSpec(verb=es.Verb.DRAW, raw="marker")
    for cls, name in declared:
        carrier = cls(**{**_required_args(cls), name: (marker,)})
        outer = es.EffectSpec(verb=es.Verb.CREATE_TOKEN, payload=carrier, raw="x")
        assert marker in list(es.iter_specs((outer,))), cls


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


def test_every_residue_code_resolves_through_the_declared_mapping():
    """RESIDUE_CODES is the documented Mapping code -> polarity (invariant
    7: every residue code is IN it), so a parameterised code resolves
    through `in`, `[]` and `.get` like an exact one; a non-code does not."""
    from engine import effect_spec as es
    from collections.abc import Mapping
    codes = es.RESIDUE_CODES
    assert isinstance(codes, Mapping)
    for code, pol in {"target.keyword:flying": es.WIDENING,
                      "target.state:tapped": es.WIDENING,
                      "target.color:red": es.WIDENING,
                      "target.stat:power": es.WIDENING,
                      "target.union:artifact": es.NARROWING,
                      "target.colored": es.WIDENING,
                      "target.unparsed": es.UNPARSED}.items():
        assert code in codes, code
        assert codes[code] == pol == codes.get(code), code
    for bad in ("target.keyword:", "target.made_up", "", "target.scope:you",
                "keyword:flying", None, 7, ("target.colored",)):
        assert bad not in codes, bad
        assert codes.get(bad) is None, bad
        with pytest.raises(KeyError):
            codes[bad]
    # Iteration yields the declared entries; each is itself a member.
    assert len(list(codes)) == len(codes) == 17
    assert all(k in codes for k in codes)
    with pytest.raises(TypeError):
        codes["target.made_up"] = es.WIDENING     # read-only
    # Invariant 7 reads the same mapping.
    from engine.target_solver import TargetRequirement
    t0 = TargetRequirement(zone="battlefield", types=frozenset({"creature"}))
    ok = es.EffectSpec(verb=es.Verb.DESTROY, target=t0, target_slot=0,
                       residue=("target.keyword:flying", "target.union:artifact"),
                       raw="x")
    assert es.validate_spec(ok, _host_with(ok, targets=(t0,))) is None


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
