"""The schema invariant checks are memoised per distinct frozen spec, and
the eager pool pass is cut without changing its output.

Design doc 2026-09-29, section 12 (load time: the eager pool path's CPU).
`validate_spec` runs over every spec the grammar freezes; the host-free
half of a spec's checks (invariants 1, 2 unpaired, 4-8 and the walk that
finds its own Refs) is a function of the spec's value alone, so it is
remembered per distinct clean, hashable spec, while the checks that read
the owning host (target slots, ref order) run on every call. A value with
a mutable object reachable is never remembered. The memos are bounded and
cleared with the grammar's caches. `effect_spec.replace` builds the same
frozen value `dataclasses.replace` builds. The eager pool pass defers full
collections and restores the collector thresholds when it ends.
"""
from __future__ import annotations

import dataclasses
import gc

import pytest


def _spec(seq=0, **kw):
    from engine import effect_spec as es
    return es.EffectSpec(verb=es.Verb.DRAW, actor=es.Ref(es.RefKind.SELF),
                         amount=es.Amount(es.AmountKind.LITERAL, n=1),
                         seq=seq, raw="draw a card", **kw)


def _host(*specs, targets=()):
    from engine import effect_spec as es
    return es.AbilityEffects(kind=es.HostKind.SPELL, face=0, index=0,
                             specs=tuple(specs), targets=tuple(targets))


def _count(monkeypatch, module, name):
    calls = []
    real = getattr(module, name)

    def counted(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(module, name, counted)
    return calls


def test_each_distinct_frozen_spec_is_walked_for_its_host_free_invariants_once(monkeypatch):
    from engine import effect_spec as es
    es.clear_caches()
    walks = _count(monkeypatch, es, "find_mutable")
    first, equal = _spec(), _spec()
    assert first == equal and first is not equal
    assert es.validate_spec(first, _host(first)) is None
    assert len(walks) == 1
    # The same value again -- the same object or an equal one, in this host
    # or another -- is not walked again.
    assert es.validate_spec(first, _host(first)) is None
    assert es.validate_spec(equal, _host(equal)) is None
    assert len(walks) == 1
    # A different value is.
    assert es.validate_spec(_spec(seq=1), _host(_spec(seq=1))) is None
    assert len(walks) == 2


def test_the_host_checks_of_a_remembered_spec_run_against_each_host():
    from engine import effect_spec as es
    from engine.target_solver import TargetRequirement
    es.clear_caches()
    req = TargetRequirement(zone="battlefield", types=frozenset({"creature"}),
                            raw_phrase="target creature")
    other = TargetRequirement(zone="battlefield", types=frozenset({"creature"}),
                              raw_phrase="target creature")
    spec = es.EffectSpec(verb=es.Verb.DESTROY, target=req, target_slot=0,
                         seq=0, raw="destroy target creature")
    assert es.validate_spec(spec, _host(spec, targets=(req,))) is None
    # Invariant 2 reads the owning host: the requirement must BE its target.
    assert es.validate_spec(spec, _host(spec, targets=(other,))) == \
        "target_slot:not_host_target"
    assert es.validate_spec(spec, _host(spec)) == "target_slot:out_of_range"
    assert es.validate_spec(spec, None) == "target_slot:no_host"
    # Invariant 3 reads the owning host: a RESULT ref names one of its specs.
    tap = es.EffectSpec(verb=es.Verb.TAP, ref=es.Ref(es.RefKind.RESULT, index=0),
                        seq=1, raw="tap it")
    assert es.validate_spec(tap, _host(_spec(), tap)) is None
    assert es.validate_spec(tap, _host(tap)) == "ref_order:cross_host"
    assert es.validate_spec(tap, _host(tap), (_host(_spec()),)) is None
    assert es.validate_spec(tap, None) == "ref_order:no_host"


def test_an_equal_spec_whose_reference_index_is_not_an_int_is_still_refused():
    """Invariant 3 reads a Ref index's type, which value equality does not
    see (0.0 == 0): a spec equal to a remembered one, but not that object,
    has its own Refs read again."""
    from engine import effect_spec as es
    es.clear_caches()
    tap = es.EffectSpec(verb=es.Verb.TAP, ref=es.Ref(es.RefKind.RESULT, index=0),
                        seq=1, raw="tap it")
    odd = es.EffectSpec(verb=es.Verb.TAP, ref=es.Ref(es.RefKind.RESULT, index=0.0),
                        seq=1, raw="tap it")
    assert tap == odd and hash(tap) == hash(odd)
    assert es.validate_spec(tap, _host(_spec(), tap)) is None
    assert es.validate_spec(odd, _host(_spec(), odd)) == "ref_order:result"
    assert es.validate_spec(tap, _host(_spec(), tap)) is None


def test_a_cyclic_value_is_reported_on_every_walk():
    """A frozen value re-pointed into a cycle (object.__setattr__) that
    reaches a mutable object is reported whichever part is walked first."""
    from engine import effect_spec as es
    es.clear_caches()
    inner = es.EffectSpec(verb=es.Verb.UNMODELLED,
                          payload=es.Unmodelled(es.Stage.CLAUSE, "x"), raw="x")
    outer = es.EffectSpec(verb=es.Verb.UNMODELLED,
                          payload=es.Unmodelled(es.Stage.CLAUSE, "y"), raw="y",
                          then=(inner,))
    object.__setattr__(inner, "then", (outer,))
    object.__setattr__(outer, "otherwise", ([],))
    assert es.find_mutable(outer) == ".otherwise[0]"
    assert es.find_mutable(inner) == ".then[0].otherwise[0]"


def test_a_spec_with_a_mutable_object_reachable_is_never_remembered(monkeypatch):
    from engine import effect_spec as es
    es.clear_caches()

    @dataclasses.dataclass
    class Loose:                       # a non-frozen dataclass: mutable
        n: int = 0

    spec = es.EffectSpec(verb=es.Verb.UNMODELLED,
                         payload=es.Unmodelled(es.Stage.CLAUSE, "x"),
                         amount=Loose(), raw="x")
    walks = _count(monkeypatch, es, "find_mutable")
    for _ in range(2):
        got = es.validate_spec(spec)
        assert got is not None and got.startswith("immutable:"), got
    assert len(walks) == 2
    assert all(v[0] is not spec for v in es._VERDICTS.values())


def test_the_validation_memos_are_bounded_and_cleared_with_the_grammar_caches():
    from engine import effect_spec as es
    import engine.effect_grammar as grammar
    es.clear_caches()
    bound = es.VALIDATION_MEMO_SIZE
    for seq in range(bound + 10):
        s = _spec(seq=seq)
        assert es.validate_spec(s, _host(s)) is None
    assert 0 < len(es._VERDICTS) <= bound
    assert len(es._CLEAN) <= bound
    grammar.clear_caches()
    assert not es._VERDICTS and not es._CLEAN


def test_replace_builds_the_value_dataclasses_replace_builds_for_every_schema_class():
    from engine import effect_spec as es
    from tests.test_effect_spec_schema import _required_args, _sample
    ce = _sample()
    values = [ce] + [h for h in ce.walk(include_sub=True)]
    values += [s for h in ce.walk(include_sub=True) for s in es.iter_specs(h.specs)]
    for cls in (c for c in vars(es).values()
                if isinstance(c, type) and dataclasses.is_dataclass(c)
                and c.__module__ == es.__name__):
        values.append(cls(**_required_args(cls)))
        # Every schema class is copied slot by slot (the fast path).
        assert es._replacer(cls) is not None, cls
    for v in values:
        for f in dataclasses.fields(v):
            old = getattr(v, f.name)
            new = es.replace(v, **{f.name: old})
            assert new == v and new is not v and type(new) is type(v)
            assert hash(new) == hash(v)
        assert es.replace(v) == dataclasses.replace(v)
    spec = _spec()
    changed = es.replace(spec, seq=5, raw="draw two cards")
    assert changed == dataclasses.replace(spec, seq=5, raw="draw two cards")
    with pytest.raises(dataclasses.FrozenInstanceError):
        changed.seq = 6                # still frozen
    with pytest.raises(TypeError):
        es.replace(spec, no_such_field=1)


def test_replace_of_a_class_with_a_post_init_or_an_init_var_goes_through_its_init():
    from engine import effect_spec as es

    @dataclasses.dataclass(frozen=True, slots=True)
    class Checked:
        n: int = 0

        def __post_init__(self):
            if self.n < 0:
                raise ValueError("negative")

    assert es.replace(Checked(1), n=2) == Checked(2)
    with pytest.raises(ValueError):
        es.replace(Checked(1), n=-1)

    @dataclasses.dataclass(frozen=True, slots=True)
    class WithInitVar:
        scale: dataclasses.InitVar[int]
        n: int = 0

    # dataclasses.replace requires an InitVar without a default to be
    # given; so does replace, which hands such a class to it.
    assert es._replacer(WithInitVar) is None
    assert es.replace(WithInitVar(3, n=1), n=2, scale=3) == WithInitVar(3, n=2)
    with pytest.raises(ValueError):
        es.replace(WithInitVar(3, n=1), n=2)


class _Pool:
    def __init__(self, templates):
        self.cards = {t.name: t for t in templates}


def test_the_eager_pool_pass_defers_full_collections_and_restores_the_thresholds(monkeypatch):
    import engine.effect_grammar as grammar
    from engine.cards import CardTemplate, CardType
    from engine.mana import ManaCost
    seen = []
    real = grammar.parse_template

    def recording(t, facts=None):
        seen.append(gc.get_threshold())
        return real(t, facts)
    monkeypatch.setattr(grammar, "parse_template", recording)
    saved = gc.get_threshold()
    pool = _Pool([CardTemplate(name="Spark Test", card_types=[CardType.INSTANT],
                               mana_cost=ManaCost(generic=1),
                               oracle_text="Draw a card.")])
    out = grammar.parse_pool(pool)
    assert set(out) == {"Spark Test"}
    assert seen and all(t[:2] == saved[:2] for t in seen)
    assert all(t[2] >= grammar._NO_FULL_COLLECTION_THRESHOLD for t in seen)
    assert gc.get_threshold() == saved

    def failing(t, facts=None):
        raise RuntimeError("parse failed")
    monkeypatch.setattr(grammar, "parse_template", failing)
    with pytest.raises(RuntimeError):
        grammar.parse_pool(pool)
    assert gc.get_threshold() == saved
