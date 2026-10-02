"""Resolution sequencing for the typed effect model (design doc 2026-09-29).

E0 scope (A35): the resolution-time choices a resolving ability can ask of
its controller -- whether to perform an optional effect, how much of a
variable amount, which cards out of a pool, how to divide a total among
slots -- are declared on the engine->AI callback protocol, one channel per
KIND of choice. In E0 nothing calls them: the dispatcher has no callers,
and every legacy resolution path keeps its own choice code until its family
switches. The engine never scores; the AI answers.

The dispatcher tests (`resolve_ability`, `can_execute`, APNAP choices,
sub-abilities) join this file with `engine/effect_resolver.py`.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

RESOLUTION_CHOICES = {
    "choose_optional_effect": ["self", "ctx", "spec"],
    "choose_amount": ["self", "ctx", "spec", "lo", "hi", "remaining_specs"],
    "choose_cards": ["self", "ctx", "spec", "pool", "n"],
    "choose_division": ["self", "ctx", "spec", "slots", "total"],
}


def test_resolution_choice_callbacks_are_declared_on_the_protocol_and_defaults():
    from engine.callbacks import DefaultCallbacks, GameCallbacks
    for cls in (GameCallbacks, DefaultCallbacks):
        for name, params in RESOLUTION_CHOICES.items():
            fn = getattr(cls, name)
            assert list(inspect.signature(fn).parameters) == params, (cls, name)


def test_an_unanswered_resolution_choice_raises_rather_than_guessing():
    from engine.callbacks import DefaultCallbacks
    from engine.game_runner import AICallbacks
    for impl in (DefaultCallbacks(), AICallbacks()):
        with pytest.raises(NotImplementedError):
            impl.choose_optional_effect(None, None)
        with pytest.raises(NotImplementedError):
            impl.choose_amount(None, None, 0, 1, ())
        with pytest.raises(NotImplementedError):
            impl.choose_cards(None, None, (), 1)
        with pytest.raises(NotImplementedError):
            impl.choose_division(None, None, (), 2)


def _calls_to(names, roots=("engine", "ai"), exclude=()):
    hits = []
    for root in roots:
        for path in sorted((REPO / root).rglob("*.py")):
            if str(path.relative_to(REPO)) in exclude:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                name = (f.attr if isinstance(f, ast.Attribute)
                        else f.id if isinstance(f, ast.Name) else None)
                if name in names:
                    hits.append(f"{path.relative_to(REPO)}:{node.lineno} {name}")
                # getattr(obj, "choose_amount") is a call too.
                if (isinstance(f, ast.Name) and f.id == "getattr"
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value in names):
                    hits.append(f"{path.relative_to(REPO)}:{node.lineno} getattr")
    return hits


# The dispatcher (section 11) is the one caller the callbacks were declared
# for; it has no caller of its own in E0 (pinned by
# test_no_engine_or_ai_module_imports_the_dispatcher_in_e0), so play code
# still reaches no resolution-choice callback.
_CALLERLESS_DISPATCHER = ("engine/effect_resolver.py",)


def test_resolution_choice_callbacks_are_declared_but_not_called_by_play_code():
    assert _calls_to(set(RESOLUTION_CHOICES),
                     exclude=_CALLERLESS_DISPATCHER) == []


# ═══════════════════════════════════════════════════════════════════════
# The dispatcher skeleton (design doc section 11): engine/effect_resolver.
#
# Every test below builds typed specs by hand -- no card DB, no oracle
# text -- and a fake game that only records what the dispatcher asks of
# its owners. Executors and condition evaluators are registered per test
# with monkeypatch; the shipped tables are empty in E0.
# ═══════════════════════════════════════════════════════════════════════

from types import SimpleNamespace

from engine.delayed_triggers import DelayedTrigger, DelayedTriggerTiming
from engine.effect_model import (ANY_KEYWORD, Duration, DurationKind,
                                 ModKind, Modification, Selector,
                                 SelectorKind)
from engine.effect_spec import (AbilityEffects, CardFilter, Condition,
                                ConditionKind, EffectSpec, Granted,
                                HostKind, Ref, RefKind, Stage, SubAbility,
                                SubAbilityKind, TriggerHead, Unmodelled,
                                Verb)

RESOLVER = REPO / "engine" / "effect_resolver.py"


class _Callbacks:
    """Answers the optional-effect choice from a script; every other
    resolution choice is unanswered (the protocol default)."""

    def __init__(self, accept=True):
        self.accept = accept
        self.asked = []

    def choose_optional_effect(self, ctx, spec):
        self.asked.append(spec.seq)
        return self.accept


def _game(active=0, accept=True, n_players=2):
    registered = []
    return SimpleNamespace(
        players=[SimpleNamespace(index=i) for i in range(n_players)],
        active_player=active, turn_number=7,
        callbacks=_Callbacks(accept),
        registered=registered,
        register_delayed_trigger=registered.append)


def _spec(verb=Verb.DRAW, seq=0, **kw):
    kw.setdefault("raw", f"<{verb.value} {seq}>")
    return EffectSpec(verb=verb, seq=seq, **kw)


def _host(*specs, kind=HostKind.SPELL, **kw):
    return AbilityEffects(kind=kind, face=0, index=0, specs=tuple(specs), **kw)


def _src():
    from engine.effect_resolver import Handle
    return Handle(instance_id=1, zone="stack", entry_seq=0)


class _Recorder:
    """An executor that records each call and performs (or not)."""

    def __init__(self, performed=True, result=None):
        self.calls = []
        self.performed = performed
        self.result = result

    def __call__(self, ctx, spec, actors):
        from engine.effect_resolver import Outcome
        self.calls.append((spec.seq, tuple(actors)))
        res = self.result(ctx, spec, actors) if callable(self.result) else (
            self.result if self.result is not None else {})
        return Outcome(self.performed, res)


@pytest.fixture
def er():
    import engine.effect_resolver as er
    return er


@pytest.fixture
def run(er, monkeypatch):
    """Register executors / evaluators for one test and resolve."""
    def register(verb, executor):
        monkeypatch.setitem(er.EXECUTORS, verb, executor)
        return executor

    def evaluator(kind, fn):
        monkeypatch.setitem(er.CONDITION_EVALUATORS, kind, fn)
        return fn
    return SimpleNamespace(register=register, evaluator=evaluator)


# ── E0 ships the skeleton with nothing in it, and nothing calls it ─────

def test_dispatcher_tables_are_empty_in_e0(er):
    assert er.EXECUTORS == {}
    assert er.EXECUTOR_FILTER_KEYS == {}
    assert er.LEGACY_RESIDUE_TOLERATED == {}
    assert er.CONDITION_EVALUATORS == {}


def test_no_engine_or_ai_module_imports_the_dispatcher_in_e0():
    """E0 is data + tooling: the dispatcher exists with no caller, so no
    resolution path can reach it until a family switches (section 11)."""
    hits = []
    for root in ("engine", "ai"):
        for path in sorted((REPO / root).rglob("*.py")):
            if path == RESOLVER:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""] + [
                        f"{node.module or ''}.{a.name}" for a in node.names]
                if any(m.split(".")[-1] == "effect_resolver"
                       or m.endswith(".effect_resolver") for m in mods):
                    hits.append(f"{path.relative_to(REPO)}:{node.lineno}")
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ("__import__", "import_module")
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and "effect_resolver" in str(node.args[0].value)):
                    hits.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert hits == []


def test_dispatcher_reads_no_oracle_text_and_writes_no_game_state():
    """It sequences typed specs only: no oracle read, no regex, no
    grammar or target parse at resolution (parse-once), and every
    attribute/item write lands on its own Resolution, never on a game
    object (owners write state: check_single_owner / check_zone_mutation)."""
    tree = ast.parse(RESOLVER.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("oracle_text", "text_lower"), node.lineno
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""])
            for m in mods:
                assert m not in ("re", "regex"), node.lineno
                assert "effect_grammar" not in m, node.lineno
                assert "oracle" not in m, node.lineno
                assert "target_solver" not in m, node.lineno
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                while isinstance(t, (ast.Attribute, ast.Subscript)):
                    root = t.value
                    if not isinstance(root, (ast.Attribute, ast.Subscript)):
                        assert isinstance(root, ast.Name) and root.id in (
                            "ctx", "self"), (node.lineno, ast.dump(t))
                    t = root


# ── can_execute: fail closed (section 11) ──────────────────────────────

def test_an_ability_with_a_verb_no_executor_owns_is_not_executable(er, run):
    host = _host(_spec(Verb.DRAW, 0), _spec(Verb.DISCARD, 1))
    run.register(Verb.DRAW, _Recorder())
    assert er.can_execute(host) is False
    run.register(Verb.DISCARD, _Recorder())
    assert er.can_execute(host) is True


def test_an_unmodelled_clause_is_never_executable_even_if_registered(er, run):
    um = _spec(Verb.UNMODELLED, 0, payload=Unmodelled(Stage.CLAUSE, "x"))
    run.register(Verb.UNMODELLED, _Recorder())
    assert er.can_execute(_host(um)) is False


def test_a_reference_to_an_unmodelled_clause_is_not_executable(er, run):
    """A delayed sub-ability acting on the RESULT of an unmodelled parent
    clause has nothing to act on."""
    um = _spec(Verb.UNMODELLED, 0, payload=Unmodelled(Stage.CLAUSE, "x"))
    sub = _host(_spec(Verb.EXILE, 2, ref=Ref(RefKind.RESULT, index=0)),
                kind=HostKind.TRIGGERED)
    trig = _spec(Verb.CREATE_TRIGGER, 1, payload=SubAbility(
        SubAbilityKind.DELAYED, DelayedTriggerTiming.NEXT_END_STEP, sub))
    run.register(Verb.EXILE, _Recorder())
    assert er.can_execute(sub) is True
    assert er._refs_unmodelled(sub.specs[0], {0: um}) is True
    assert er.can_execute(_host(um, trig)) is False


def test_unparsed_target_residue_is_never_tolerated(er, run, monkeypatch):
    run.register(Verb.DESTROY, _Recorder())
    s = _spec(Verb.DESTROY, 0, residue=("target.unparsed",))
    monkeypatch.setitem(er.LEGACY_RESIDUE_TOLERATED, "removal",
                        frozenset({"target.unparsed"}))
    assert er.can_execute(_host(s), "removal") is False


def test_widening_residue_is_tolerated_only_for_the_family_that_lists_it(
        er, run, monkeypatch):
    run.register(Verb.DESTROY, _Recorder())
    s = _spec(Verb.DESTROY, 0, residue=("target.keyword:flying",))
    assert er.can_execute(_host(s), "removal") is False
    monkeypatch.setitem(er.LEGACY_RESIDUE_TOLERATED, "removal",
                        frozenset({"target.keyword:flying"}))
    assert er.can_execute(_host(s), "removal") is True
    assert er.can_execute(_host(s), "damage") is False
    assert er.can_execute(_host(s)) is False


def _filter_spec(*entries):
    return _spec(Verb.TAP, 0, subject=Selector(SelectorKind.FILTER,
                                               filter=tuple(entries)))


def test_a_filter_subject_executes_only_entries_something_evaluates(
        er, run, monkeypatch):
    """F5/A22: value-typed support -- the entries covers_object evaluates,
    or the ones the verb's executor hands to an owner that does."""
    run.register(Verb.TAP, _Recorder())
    assert er.can_execute(_host(_filter_spec(("controller", "opponents"))))
    assert er.can_execute(_host(_filter_spec(("without_keyword", "flying"))))
    # a printed keyword spelling is not a cards.Keyword value
    assert not er.can_execute(_host(_filter_spec(("without_keyword", "first strike"))))
    assert not er.can_execute(_host(_filter_spec(("without_keyword", ANY_KEYWORD))))
    you = ("controller", "you")
    assert not er.can_execute(_host(_filter_spec(you)))
    monkeypatch.setitem(er.EXECUTOR_FILTER_KEYS, Verb.TAP, frozenset({you}))
    assert er.can_execute(_host(_filter_spec(you)))
    monkeypatch.setitem(er.EXECUTOR_FILTER_KEYS, Verb.UNTAP, frozenset({you}))
    monkeypatch.delitem(er.EXECUTOR_FILTER_KEYS, Verb.TAP)
    assert not er.can_execute(_host(_filter_spec(you)))


def test_an_untargeted_card_filter_is_held_to_the_same_support(er, run, monkeypatch):
    run.register(Verb.SEARCH, _Recorder())
    s = _spec(Verb.SEARCH, 0, filter=CardFilter(zone="library",
                                                 supertypes=frozenset({"basic"})))
    assert er.can_execute(_host(s)) is False
    monkeypatch.setitem(er.EXECUTOR_FILTER_KEYS, Verb.SEARCH, frozenset(
        {("zone", "library"), ("supertypes", ("basic",))}))
    assert er.can_execute(_host(s)) is True


def test_a_per_member_condition_needs_an_executor_that_evaluates_members(er, run):
    """A23: 'creatures ... if it ...' binds to Ref(MEMBER), which only the
    executor can evaluate, member by member."""
    cond = Condition(ConditionKind.OBJECT, pred="tapped", ref=Ref(RefKind.MEMBER))
    s = _spec(Verb.UNTAP, 0, subject=Selector(SelectorKind.FILTER,
                                              filter=(("controller", "opponents"),)),
              condition=cond)
    run.evaluator(ConditionKind.OBJECT, lambda ctx, c: True)
    plain = run.register(Verb.UNTAP, _Recorder())
    assert er.can_execute(_host(s)) is False
    plain.evaluates_members = True
    assert er.can_execute(_host(s)) is True


def test_a_condition_no_evaluator_owns_is_not_executable(er, run):
    run.register(Verb.DRAW, _Recorder())
    kicked = Condition(ConditionKind.CAST_FACT, pred="kicked")
    s = _spec(Verb.DRAW, 0, condition=Condition(ConditionKind.NOT, children=(kicked,)))
    assert er.can_execute(_host(s)) is False
    run.evaluator(ConditionKind.CAST_FACT, lambda ctx, c: c.pred in ctx.cast_facts)
    assert er.can_execute(_host(s)) is True


def test_an_unapplied_modification_kind_is_not_executable(er, run):
    run.register(Verb.CONTINUOUS, _Recorder())
    dur = Duration(DurationKind.THIS_TURN)
    ok = _spec(Verb.CONTINUOUS, 0, ref=Ref(RefKind.SELF), duration=dur,
               payload=Modification(ModKind.MODIFY_PT, data=(("power", 1),)))
    grant = _spec(Verb.CONTINUOUS, 0, ref=Ref(RefKind.SELF), duration=dur,
                  payload=Modification(ModKind.GRANT_ABILITY))
    assert er.can_execute(_host(ok)) is True
    assert er.can_execute(_host(grant)) is False


def test_a_sub_ability_is_executable_only_if_its_own_host_is(er, run):
    sub = _host(_spec(Verb.EXILE, 1, ref=Ref(RefKind.RESULT, index=0)),
                kind=HostKind.TRIGGERED)
    trig = _spec(Verb.CREATE_TRIGGER, 1, payload=SubAbility(
        SubAbilityKind.DELAYED, DelayedTriggerTiming.NEXT_END_STEP, sub))
    host = _host(_spec(Verb.MOVE, 0), trig)
    run.register(Verb.MOVE, _Recorder())
    assert er.can_execute(host) is False
    run.register(Verb.EXILE, _Recorder())
    assert er.can_execute(host) is True
    untimed = _spec(Verb.CREATE_TRIGGER, 1, payload=SubAbility(
        SubAbilityKind.DELAYED, None, sub))
    assert er.can_execute(_host(_spec(Verb.MOVE, 0), untimed)) is False


def test_a_granted_ability_is_not_resolved_by_its_granting_spell(er, run):
    """A granted ability (CR 113.10) resolves when IT is activated or
    triggers; its unmodelled text does not block the granting effect."""
    um = _spec(Verb.UNMODELLED, 0, payload=Unmodelled(Stage.CLAUSE, "x"))
    granted = _host(um, kind=HostKind.GRANTED)
    s = _spec(Verb.ATTACH, 0, ref=Ref(RefKind.SELF), payload=Granted((granted,)))
    run.register(Verb.ATTACH, _Recorder())
    assert er.can_execute(_host(s)) is True


def test_a_mode_is_held_to_the_same_checks(er, run):
    modal = _host(modes=(_host(_spec(Verb.DRAW, 0), kind=HostKind.MODE),
                         _host(_spec(Verb.MILL, 0), kind=HostKind.MODE)))
    run.register(Verb.DRAW, _Recorder())
    assert er.can_execute(modal) is False
    run.register(Verb.MILL, _Recorder())
    assert er.can_execute(modal) is True


def test_a_replacement_names_only_victims_in_its_own_sequence(er, run):
    run.register(Verb.DRAW, _Recorder())
    nested = _spec(Verb.DRAW, 1, then=(_spec(Verb.DRAW, 2, replaces=(0,)),))
    assert er.can_execute(_host(_spec(Verb.DRAW, 0), nested)) is False


# ── resolve_ability (A37): refuse without raising ──────────────────────

def test_a_non_executable_ability_resolves_nothing_and_does_not_raise(er):
    class _Raising:
        def __getattr__(self, name):
            raise AssertionError(f"game touched: {name}")
    host = _host(_spec(Verb.DRAW, 0, optional=True))
    assert er.resolve_ability(_Raising(), _src(), 0, host, ()) is False


def test_specs_resolve_in_printed_order_with_one_executor_call_each(er, run):
    """CR 608.2c: in printed order; A33: one simultaneous action per spec."""
    rec = _Recorder()
    for v in (Verb.DRAW, Verb.MILL, Verb.SCRY):
        run.register(v, rec)
    host = _host(_spec(Verb.MILL, 0), _spec(Verb.DRAW, 1), _spec(Verb.SCRY, 2))
    assert er.resolve_ability(_game(), _src(), 0, host, ()) is True
    assert [c[0] for c in rec.calls] == [0, 1, 2]


def test_resolution_reports_whether_anything_was_performed(er, run):
    run.register(Verb.DRAW, _Recorder(performed=False))
    assert er.resolve_ability(_game(), _src(), 0, _host(_spec(Verb.DRAW, 0)), ()) is False


def test_a_player_set_acts_as_one_action_in_apnap_order(er, run):
    """CR 101.4: choices in APNAP order, then ONE simultaneous action."""
    rec = run.register(Verb.DRAW, _Recorder())
    each = _spec(Verb.DRAW, 0, actor=Selector(SelectorKind.ALL_PLAYERS))
    opp = _spec(Verb.DRAW, 1, actor=Selector(SelectorKind.OPPONENTS))
    you = _spec(Verb.DRAW, 2)
    er.resolve_ability(_game(active=1, n_players=3), _src(), 0,
                       _host(each, opp, you), ())
    assert rec.calls == [(0, (1, 2, 0)), (1, (1, 2)), (2, (0,))]


def test_a_targeted_player_acts_through_its_chosen_slot(er, run):
    rec = run.register(Verb.DRAW, _Recorder())
    s = _spec(Verb.DRAW, 0, actor=Ref(RefKind.TARGET, index=0))
    er.resolve_ability(_game(), _src(), 0, _host(s), ((1,),))
    assert rec.calls == [(0, (1,))]


def test_an_actor_the_dispatcher_cannot_bind_is_not_executable(er, run):
    run.register(Verb.DRAW, _Recorder())
    s = _spec(Verb.DRAW, 0, actor=Ref(RefKind.EVENT_PLAYER))
    assert er.can_execute(_host(s)) is False


def test_a_false_condition_skips_the_spec_and_takes_its_otherwise_branch(er, run):
    rec = _Recorder()
    run.register(Verb.DRAW, rec)
    run.register(Verb.MILL, rec)
    run.evaluator(ConditionKind.CAST_FACT, lambda ctx, c: c.pred in ctx.cast_facts)
    s = _spec(Verb.DRAW, 0, condition=Condition(ConditionKind.CAST_FACT, pred="kicked"),
              then=(_spec(Verb.MILL, 1),), otherwise=(_spec(Verb.MILL, 2),))
    er.resolve_ability(_game(), _src(), 0, _host(s), ())
    assert [c[0] for c in rec.calls] == [2]
    rec.calls.clear()
    er.resolve_ability(_game(), _src(), 0, _host(s), (), cast_facts=frozenset({"kicked"}))
    assert [c[0] for c in rec.calls] == [0, 1]


def test_an_unperformed_spec_takes_its_otherwise_branch(er, run):
    run.register(Verb.SACRIFICE, _Recorder(performed=False))
    rec = run.register(Verb.DRAW, _Recorder())
    s = _spec(Verb.SACRIFICE, 0, then=(_spec(Verb.DRAW, 1),),
              otherwise=(_spec(Verb.DRAW, 2),))
    er.resolve_ability(_game(), _src(), 0, _host(s), ())
    assert [c[0] for c in rec.calls] == [2]


def test_a_declined_optional_effect_is_not_performed_and_its_result_is_empty(er, run):
    """A35: 'you may' asks the controller through choose_optional_effect;
    a declined spec's result is empty, so THAT_MUCH of it is 0."""
    seen = {}

    def _then(ctx, spec, actors):
        seen["prior"] = (ctx.performed.get(0), ctx.results.get(0))
        return {}
    run.register(Verb.SACRIFICE, _Recorder(result={0: ("h",)}))
    run.register(Verb.DRAW, _Recorder(result=_then))
    s = _spec(Verb.SACRIFICE, 0, optional=True, then=(_spec(Verb.MILL, 1),),
              otherwise=(_spec(Verb.DRAW, 2),))
    run.register(Verb.MILL, _Recorder())
    g = _game(accept=False)
    er.resolve_ability(g, _src(), 0, _host(s), ())
    assert g.callbacks.asked == [0]
    assert seen["prior"] == (False, {})


def test_an_instead_condition_is_read_once_at_its_first_victims_position(er, run):
    """A33: a replacing spec's condition is evaluated lazily -- after the
    specs printed before its first victim have resolved -- exactly once,
    and a group of victims is replaced by ONE run of the replacement."""
    order, reads = [], []

    def _ex(ctx, spec, actors):
        order.append(spec.seq)
        return {}
    for v in (Verb.GAIN_LIFE, Verb.DRAW, Verb.MILL, Verb.SCRY):
        run.register(v, _Recorder(result=_ex))

    def _holds(ctx, c):
        reads.append(dict(ctx.performed))
        return c.pred in ctx.cast_facts
    run.evaluator(ConditionKind.CAST_FACT, _holds)
    host = _host(_spec(Verb.GAIN_LIFE, 0), _spec(Verb.DRAW, 1), _spec(Verb.MILL, 2),
                 _spec(Verb.SCRY, 3, replaces=(1, 2),
                       condition=Condition(ConditionKind.CAST_FACT, pred="kicked")))
    er.resolve_ability(_game(), _src(), 0, host, (), cast_facts=frozenset({"kicked"}))
    assert order == [0, 3]
    assert reads == [{0: True}]
    order.clear(); reads.clear()
    er.resolve_ability(_game(), _src(), 0, host, ())
    assert order == [0, 1, 2]
    assert len(reads) == 1


def test_a_declined_optional_instead_leaves_the_replaced_effect_to_happen(er, run):
    """'You may <Y> instead': the choice is part of whether the replacement
    applies, so declining it leaves the replaced effect to happen (CR
    608.2c, A35); the choice is asked once, at the first victim, and not
    asked at all when the replacement's condition is false."""
    order = []

    def _ex(ctx, spec, actors):
        order.append(spec.seq)
        return {}
    run.register(Verb.MOVE, _Recorder(result=_ex))
    run.evaluator(ConditionKind.CAST_FACT, lambda ctx, c: c.pred in ctx.cast_facts)
    gate = Condition(ConditionKind.CAST_FACT, pred="kicked")
    host = _host(_spec(Verb.MOVE, 0),
                 _spec(Verb.MOVE, 1, replaces=(0,), optional=True, condition=gate))
    for accept, facts, want, asked in ((False, {"kicked"}, [0], [1]),
                                       (True, {"kicked"}, [1], [1]),
                                       (True, set(), [0], [])):
        order.clear()
        g = _game(accept=accept)
        assert er.resolve_ability(g, _src(), 0, host, (),
                                  cast_facts=frozenset(facts)) is True
        assert (order, g.callbacks.asked) == (want, asked), (accept, facts)


def test_every_clause_of_an_instead_group_replaces_the_shared_victim(er, run):
    """'X and Y instead' types as two replacing specs naming the same
    victim: when they apply, both run, in printed order, and the victim
    does not; when they do not, only the victim runs."""
    order = []

    def _ex(ctx, spec, actors):
        order.append(spec.seq)
        return {}
    run.register(Verb.CONTINUOUS, _Recorder(result=_ex))
    run.evaluator(ConditionKind.CAST_FACT, lambda ctx, c: c.pred in ctx.cast_facts)
    gate = Condition(ConditionKind.CAST_FACT, pred="kicked")
    host = _host(_spec(Verb.CONTINUOUS, 0),
                 _spec(Verb.CONTINUOUS, 1, replaces=(0,), condition=gate),
                 _spec(Verb.CONTINUOUS, 2, replaces=(0,), condition=gate))
    assert er.can_execute(host) is True
    er.resolve_ability(_game(), _src(), 0, host, (), cast_facts=frozenset({"kicked"}))
    assert order == [1, 2]
    order.clear()
    er.resolve_ability(_game(), _src(), 0, host, ())
    assert order == [0]


def test_an_optional_instead_group_is_not_executable(er, run):
    """'You may X and Y instead' is ONE choice; asking it per replacing
    clause could perform half the replacement, so the shape fails closed
    until it is modelled as one choice."""
    run.register(Verb.CONTINUOUS, _Recorder())
    host = _host(_spec(Verb.CONTINUOUS, 0),
                 _spec(Verb.CONTINUOUS, 1, replaces=(0,), optional=True),
                 _spec(Verb.CONTINUOUS, 2, replaces=(0,), optional=True))
    assert er.can_execute(host) is False
    single = _host(_spec(Verb.CONTINUOUS, 0),
                   _spec(Verb.CONTINUOUS, 1, replaces=(0,), optional=True))
    assert er.can_execute(single) is True


def test_chosen_modes_resolve_after_the_hosts_own_specs(er, run):
    rec = _Recorder()
    for v in (Verb.DRAW, Verb.MILL, Verb.SCRY):
        run.register(v, rec)
    modal = _host(_spec(Verb.SCRY, 0),
                  modes=(_host(_spec(Verb.DRAW, 1), kind=HostKind.MODE),
                         _host(_spec(Verb.MILL, 1), kind=HostKind.MODE)))
    er.resolve_ability(_game(), _src(), 0, modal, (), modes=(1,))
    assert [(c[0]) for c in rec.calls] == [0, 1]
    assert len(rec.calls) == 2


# ── Sub-abilities: delayed (CR 603.7) and reflexive (CR 603.12) ────────

def _moved(er):
    return er.Handle(instance_id=9, zone="battlefield", entry_seq=3)


def test_a_delayed_trigger_is_registered_with_a_handle_snapshot(er, run):
    """CR 603.7 / A34: the delayed ability is registered with the owner
    queue, closed over Handles (an object identity, CR 400.7), never live
    objects -- and fires with the game the queue hands it."""
    moved = _moved(er)
    run.register(Verb.MOVE, _Recorder(result={0: (moved,)}))
    seen = []

    def _exile(ctx, spec, actors):
        seen.append((ctx.results[0], ctx.source, ctx.controller, ctx.game))
        return {}
    run.register(Verb.EXILE, _Recorder(result=_exile))
    sub = _host(_spec(Verb.EXILE, 2, ref=Ref(RefKind.RESULT, index=0)),
                kind=HostKind.TRIGGERED)
    host = _host(_spec(Verb.MOVE, 0), _spec(
        Verb.CREATE_TRIGGER, 1, raw="exile it at the beginning of the next end step",
        payload=SubAbility(SubAbilityKind.DELAYED,
                           DelayedTriggerTiming.NEXT_END_STEP, sub)))
    g = _game()
    assert er.resolve_ability(g, _src(), 1, host, ()) is True
    assert seen == []                                   # not yet: it is delayed
    (t,) = g.registered
    assert isinstance(t, DelayedTrigger)
    assert (t.timing, t.controller, t.created_turn, t.description) == (
        DelayedTriggerTiming.NEXT_END_STEP, 1, 7,
        "exile it at the beginning of the next end step")
    later = _game()
    t.effect(later)
    assert seen == [({0: (moved,)}, _src(), 1, later)]


def test_a_snapshot_is_frozen_against_later_resolution(er, run):
    moved = _moved(er)
    run.register(Verb.MOVE, _Recorder(result={0: (moved,)}))
    run.register(Verb.EXILE, _Recorder())
    ctx = er.Resolution(game=_game(), source=_src(), controller=0,
                        ability=_host(), chosen=())
    ctx.results[0] = {0: (moved,)}
    snap = ctx.snapshot()
    ctx.results[0][0] = ()
    ctx.results[5] = {0: (1,)}
    assert dict(snap.results_map()) == {0: {0: (moved,)}}
    hash(snap)


def test_a_reflexive_trigger_resolves_after_its_parent_and_rechecks_its_if(er, run):
    """CR 603.12: 'when you do' resolves after the parent ability is done,
    with its intervening-if (CR 603.4) checked again then."""
    order = []

    def _ex(ctx, spec, actors):
        order.append(spec.seq)
        return {}
    for v in (Verb.SACRIFICE, Verb.DAMAGE, Verb.DRAW):
        run.register(v, _Recorder(result=_ex))
    run.evaluator(ConditionKind.CAST_FACT, lambda ctx, c: c.pred in ctx.cast_facts)
    gate = Condition(ConditionKind.CAST_FACT, pred="kicked")
    sub = _host(_spec(Verb.DAMAGE, 3), kind=HostKind.TRIGGERED,
                trigger=TriggerHead(intervening_if=gate))
    reflexive = _spec(Verb.CREATE_TRIGGER, 1, payload=SubAbility(
        SubAbilityKind.REFLEXIVE, None, sub))
    host = _host(_spec(Verb.SACRIFICE, 0), reflexive, _spec(Verb.DRAW, 2))
    er.resolve_ability(_game(), _src(), 0, host, (), cast_facts=frozenset({"kicked"}))
    assert order == [0, 2, 3]
    order.clear()
    er.resolve_ability(_game(), _src(), 0, host, ())
    assert order == [0, 2]


def test_a_triggered_abilitys_intervening_if_is_rechecked_on_resolution(er, run):
    """CR 603.4: an intervening-if is checked again as the triggered
    ability resolves; false then, the ability does nothing. Its leaves need
    an evaluator like any condition, or the host is not executable."""
    rec = run.register(Verb.DRAW, _Recorder())
    gate = Condition(ConditionKind.CAST_FACT, pred="kicked")
    host = _host(_spec(Verb.DRAW, 0), kind=HostKind.TRIGGERED,
                 trigger=TriggerHead(intervening_if=gate))
    assert er.can_execute(host) is False
    assert er.resolve_ability(_game(), _src(), 0, host, ()) is False
    assert rec.calls == []
    run.evaluator(ConditionKind.CAST_FACT, lambda ctx, c: c.pred in ctx.cast_facts)
    assert er.can_execute(host) is True
    assert er.resolve_ability(_game(), _src(), 0, host, ()) is False
    assert rec.calls == []
    assert er.resolve_ability(_game(), _src(), 0, host, (),
                              cast_facts=frozenset({"kicked"})) is True
    assert rec.calls == [(0, (0,))]


def test_a_sub_ability_chooses_its_own_targets_unbound(er, run):
    """A30/A36: a sub-ability's targets are not the parent's; the
    dispatcher never picks, so its slots reach the owner unbound."""
    seen = []

    def _ex(ctx, spec, actors):
        seen.append(ctx.chosen)
        return {}
    run.register(Verb.DAMAGE, _Recorder(result=_ex))
    run.register(Verb.DRAW, _Recorder())
    from engine.target_solver import TargetRequirement
    req = TargetRequirement(zone="battlefield", types=frozenset({"creature"}))
    sub = _host(_spec(Verb.DAMAGE, 2, target=req, target_slot=0),
                kind=HostKind.TRIGGERED, targets=(req,))
    host = _host(_spec(Verb.DRAW, 0), _spec(Verb.CREATE_TRIGGER, 1, payload=SubAbility(
        SubAbilityKind.REFLEXIVE, None, sub)))
    er.resolve_ability(_game(), _src(), 0, host, ((er.Handle(5, "battlefield", 1),),))
    assert seen == [((),)]


# ── chosen_from_legacy (A36) ───────────────────────────────────────────

def _two_slot_legacy_host():
    from engine.target_solver import TargetRequirement
    a = TargetRequirement(zone="battlefield", types=frozenset({"creature"}))
    b = TargetRequirement(zone="battlefield", types=frozenset({"any"}))
    host = _host(targets=(a, b), text="x" * 100)
    spans = ((10, 25), (60, 70))          # each slot's printed phrase in host.text
    cards = {11: SimpleNamespace(instance_id=11, zone="battlefield",
                                 battlefield_entry_seq=2),
             12: SimpleNamespace(instance_id=12, zone="graveyard",
                                 battlefield_entry_seq=4)}
    return host, spans, SimpleNamespace(get_card_by_id=cards.get)


def test_legacy_targets_map_onto_printed_order_slots(er):
    """A36: the legacy flat list (whole-oracle category order) maps onto
    this host's printed-order slots by printed position; the -1 face
    sentinel becomes the face player; a target outside the host is not
    this host's."""
    host, spans, game = _two_slot_legacy_host()
    # legacy order: [the 'any' target (printed 2nd), creature x2 (printed 1st), other host]
    chosen = er.chosen_from_legacy(host, [-1, 11, 12, 99], [60, 10, 10, 500],
                                   slot_spans=spans, game=game, face=1)
    assert chosen == ((er.Handle(11, "battlefield", 2), er.Handle(12, "graveyard", 4)),
                      (1,))
    # an unfilled slot stays empty: the owner's picker decides (A36)
    assert er.chosen_from_legacy(host, [11], [10], slot_spans=spans,
                                 game=game, face=1) == (
        (er.Handle(11, "battlefield", 2),), ())


def test_a_legacy_target_lands_in_the_slot_whose_printed_span_holds_it(er):
    """A36 step 2: a position is matched to the slot whose printed span
    contains it, never to its rank among the positions present -- so an
    earlier slot left unchosen ('up to one') does not shift later targets
    into it."""
    host, spans, game = _two_slot_legacy_host()
    assert er.chosen_from_legacy(host, [-1], [60], slot_spans=spans,
                                 game=game, face=1) == ((), (1,))
    assert er.chosen_from_legacy(host, [-1, 11], [-1, 62], slot_spans=spans,
                                 game=game, face=1) == ((), (er.Handle(11, "battlefield", 2),))


def test_a_legacy_target_inside_the_host_but_in_no_slot_is_refused(er):
    """An in-host position no slot's span holds means the legacy parse and
    the host disagree on a requirement: refuse rather than guess a slot."""
    host, spans, game = _two_slot_legacy_host()
    with pytest.raises(ValueError):
        er.chosen_from_legacy(host, [11], [40], slot_spans=spans, game=game, face=1)
    with pytest.raises(ValueError):                  # one span per slot
        er.chosen_from_legacy(host, [11], [10], slot_spans=spans[:1],
                              game=game, face=1)


def test_a_handle_is_an_immutable_object_identity(er):
    h = er.Handle(1, "battlefield", 2)
    assert h == er.Handle(1, "battlefield", 2) != er.Handle(1, "battlefield", 3)
    with pytest.raises(Exception):
        h.zone = "graveyard"
