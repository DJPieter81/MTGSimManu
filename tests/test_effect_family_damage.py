"""The damage/life family (E1) resolves typed specs through the owners.

Design doc 2026-09-29, sections 11, 14 and 18.3. The dispatcher's damage
family binds a parsed DAMAGE, LOSE_LIFE or GAIN_LIFE spec at resolution and
performs it through the owner that already exists (damage through
`engine.damage.deal_damage`, life through `lose_life` / `gain_life`); the
conditions printed upgrades read (a count of artifacts, the card types in a
graveyard) and the amounts they deal (a printed number, X, domain) are
evaluated by `engine/effect_conditions.py`. Anything the family does not
bind is refused by `can_execute`, so its carrier keeps the legacy apply.

Card names appear only as fixture carriers (one real member of the class
resolved through the real path).
"""
from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest

from engine import effect_conditions as ec
from engine import effect_resolver as er
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.effect_model import Selector, SelectorKind
from engine.effect_spec import (AbilityEffects, Amount, AmountKind,
                                CardFilter, Condition, ConditionKind,
                                EffectSpec, HostKind, Quantity, QuantityKind,
                                Ref, RefKind, Verb)
from engine.game_state import GameState
from engine.target_solver import TargetRequirement

REPO = Path(__file__).resolve().parents[1]
EXECUTORS_PY = REPO / "engine" / "effect_executors.py"
ANY = TargetRequirement(zone="any", types=frozenset({"any"}))


def _template(name, types, power=None, toughness=None, subtypes=()):
    return CardTemplate(name=name, card_types=list(types), mana_cost=ManaCost(),
                        supertypes=[], subtypes=list(subtypes), power=power,
                        toughness=toughness, loyalty=None, keywords=set(),
                        abilities=[], color_identity=set(), produces_mana=[],
                        enters_tapped=False, oracle_text="", tags=set())


def _put(game, idx, template, zone="battlefield"):
    c = CardInstance(template=template, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        game.players[idx].battlefield.append(c)
    elif zone != "stack":
        getattr(game.players[idx], zone).append(c)
    return c


def _game():
    return GameState(rng=random.Random(0))


def _lit(n):
    return Amount(AmountKind.LITERAL, n=n)


def _damage(seq=0, amount=3, **kw):
    kw.setdefault("target", ANY)
    kw.setdefault("target_slot", 0)
    kw.setdefault("other", Ref(RefKind.SELF))
    return EffectSpec(verb=Verb.DAMAGE, seq=seq,
                      amount=amount if isinstance(amount, Amount) else _lit(amount),
                      **kw)


def _host(*specs, targets=(ANY,)):
    return AbilityEffects(kind=HostKind.SPELL, face=0, index=0,
                          specs=tuple(specs), targets=tuple(targets))


def _resolve(game, source, host, chosen, **kw):
    return er.resolve_ability(game, er.handle_of(source), source.controller,
                              host, chosen, family="damage",
                              source_object=source, **kw)


_METALCRAFT = Condition(
    ConditionKind.STATE, pred="count", op=">=", n=_lit(3),
    filter=CardFilter(types=frozenset({"artifact"}), controller="you"))
_DELIRIUM = Condition(
    ConditionKind.STATE, pred="card_types", op=">=", n=_lit(4),
    filter=CardFilter(zone="graveyard", owner="you"))


# ── DAMAGE ─────────────────────────────────────────────────────────────

def test_damage_to_a_chosen_creature_is_dealt_by_its_source_through_the_damage_owner(monkeypatch):
    from engine import damage
    calls = []
    real = damage.deal_damage
    monkeypatch.setattr(damage, "deal_damage",
                        lambda src, tgt, n, **k: (calls.append((src, tgt, n)),
                                                  real(src, tgt, n, **k))[1])
    game = _game()
    spell = _put(game, 0, _template("Burn", [CardType.INSTANT]), "stack")
    bear = _put(game, 1, _template("Bear", [CardType.CREATURE], 2, 4))
    assert _resolve(game, spell, _host(_damage(amount=3)),
                    ((er.handle_of(bear),),)) is True
    assert calls == [(spell, bear, 3)]
    assert bear.damage_marked == 3
    assert game.players[1].life == 20


def test_damage_to_the_chosen_opponent_is_dealt_to_that_player():
    game = _game()
    spell = _put(game, 0, _template("Burn", [CardType.INSTANT]), "stack")
    assert _resolve(game, spell, _host(_damage(amount=3)), ((1,),)) is True
    assert game.players[1].life == 17
    assert game.players[0].life == 20


def test_an_instead_damage_upgrade_keeps_the_base_target():
    """CR 608.2c: "deals 4 damage instead if <condition>" replaces the base
    damage to the SAME target; it neither adds to it nor picks anew."""
    upgrade = _damage(seq=1, amount=4, replaces=(0,), condition=_METALCRAFT)
    host = _host(_damage(amount=2), upgrade)
    for artifacts, dealt in ((2, 2), (3, 4)):
        game = _game()
        for i in range(artifacts):
            _put(game, 0, _template(f"Relic{i}", [CardType.ARTIFACT]))
        spell = _put(game, 0, _template("Blast", [CardType.INSTANT]), "stack")
        bear = _put(game, 1, _template("Bear", [CardType.CREATURE], 2, 6))
        decoy = _put(game, 1, _template("Decoy", [CardType.CREATURE], 2, 6))
        _resolve(game, spell, host, ((er.handle_of(bear),),))
        assert (bear.damage_marked, decoy.damage_marked) == (dealt, 0)
        assert game.players[1].life == 20


def test_a_damage_amount_of_x_is_the_value_chosen_on_casting():
    game = _game()
    spell = _put(game, 0, _template("Blaze", [CardType.SORCERY]), "stack")
    _resolve(game, spell, _host(_damage(amount=Amount(AmountKind.X, n=1))),
             ((1,),), x_value=5)
    assert game.players[1].life == 15


def test_a_domain_amount_is_the_basic_land_types_among_lands_you_control():
    domain = Amount(AmountKind.X_DEFINED, inner=Amount(
        AmountKind.EQUAL_TO, quantity=Quantity(
            QuantityKind.BASIC_LAND_TYPES, player="any",
            filter=CardFilter(types=frozenset({"land"}), controller="you"))))
    game = _game()
    for name, sub in (("M", "Mountain"), ("F", "Forest"), ("F2", "Forest")):
        _put(game, 0, _template(name, [CardType.LAND], subtypes=[sub]))
    _put(game, 1, _template("I", [CardType.LAND], subtypes=["Island"]))
    spell = _put(game, 0, _template("Flames", [CardType.SORCERY]), "stack")
    assert ec.amount_supported(domain)
    assert ec.amount_value(game, 0, domain) == 2
    _resolve(game, spell, _host(_damage(amount=domain)), ((1,),))
    assert game.players[1].life == 18


def test_zero_damage_is_not_dealt_and_the_spec_is_not_performed():
    game = _game()
    spell = _put(game, 0, _template("Fizzle", [CardType.SORCERY]), "stack")
    assert _resolve(game, spell, _host(_damage(amount=Amount(AmountKind.X, n=1))),
                    ((1,),), x_value=0) is False
    assert game.players[1].life == 20


# ── Conditions ─────────────────────────────────────────────────────────

def test_a_count_of_artifacts_you_control_holds_exactly_from_its_printed_number():
    game = _game()
    for i in range(2):
        _put(game, 0, _template(f"Relic{i}", [CardType.ARTIFACT]))
    _put(game, 1, _template("Theirs", [CardType.ARTIFACT]))
    assert ec.state_condition_supported(_METALCRAFT)
    assert not ec.state_condition_holds(game, 0, _METALCRAFT)
    _put(game, 0, _template("Relic2", [CardType.ARTIFACT, CardType.CREATURE], 1, 1))
    assert ec.state_condition_holds(game, 0, _METALCRAFT)


def test_card_types_among_cards_in_your_graveyard_count_each_type_once():
    game = _game()
    for name, types in (("a", [CardType.ARTIFACT, CardType.CREATURE]),
                        ("b", [CardType.CREATURE]), ("c", [CardType.INSTANT])):
        _put(game, 0, _template(name, types), "graveyard")
    _put(game, 1, _template("d", [CardType.SORCERY]), "graveyard")
    assert ec.state_condition_supported(_DELIRIUM)
    assert not ec.state_condition_holds(game, 0, _DELIRIUM)
    _put(game, 0, _template("e", [CardType.LAND]), "graveyard")
    assert ec.state_condition_holds(game, 0, _DELIRIUM)


# ── Fail closed ────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec", [
    _damage(amount=Amount(AmountKind.FOR_EACH, n=1)),           # amount kind
    _damage(target=TargetRequirement(zone="battlefield",
                                     types=frozenset({"creature"}))),  # slot type
    _damage(target=TargetRequirement(zone="any", types=frozenset({"any"}),
                                     count_min=0, count_max=2)),  # several targets
    _damage(optional=True),                                        # "you may"
    _damage(other=Ref(RefKind.TARGET, index=0)),                   # another source
    _damage(target_slot=None, target=None,
            subject=Selector(SelectorKind.OPPONENTS)),             # player set
], ids=["for_each", "creature_slot", "two_targets", "optional",
        "other_source", "player_set"])
def test_a_damage_shape_the_family_does_not_bind_is_not_executable(spec):
    assert er.can_execute(_host(spec), "damage") is False


def test_a_condition_predicate_no_evaluator_supports_is_not_executable():
    life = Condition(ConditionKind.STATE, pred="life_total", op="<=", n=_lit(5))
    other_count = Condition(ConditionKind.STATE, pred="count", op=">=", n=_lit(3),
                            filter=CardFilter(types=frozenset({"artifact"}),
                                              controller="any"))
    for cond in (life, other_count):
        host = _host(_damage(amount=2), _damage(seq=1, amount=4, replaces=(0,),
                                                 condition=cond))
        assert er.can_execute(host, "damage") is False
    ok = _host(_damage(amount=2), _damage(seq=1, amount=4, replaces=(0,),
                                           condition=_METALCRAFT))
    assert er.can_execute(ok, "damage") is True


# ── Life ───────────────────────────────────────────────────────────────

def test_life_loss_and_gain_go_through_their_owners():
    game = _game()
    src = _put(game, 0, _template("Drain", [CardType.SORCERY]), "stack")
    lose = EffectSpec(verb=Verb.LOSE_LIFE, seq=0, amount=_lit(3),
                      actor=Selector(SelectorKind.OPPONENTS))
    gain = EffectSpec(verb=Verb.GAIN_LIFE, seq=1, amount=_lit(2))
    host = _host(lose, gain, targets=())
    assert er.can_execute(host, "damage")
    assert _resolve(game, src, host, ()) is True
    assert (game.players[1].life, game.players[1].life_lost_this_turn) == (17, 3)
    assert (game.players[0].life, game.players[0].life_gained_this_turn) == (22, 2)
    assert any("Gain 2 life from Drain" in line for line in game.log)


# ── One real member through the real path ─────────────────────────────

def test_a_parsed_fixed_burn_spell_resolves_through_the_family(card_db):
    t = card_db.get_card("Lightning Bolt")
    host = t.effects.spell(0)
    assert er.can_execute(host, "damage")
    game = _game()
    bolt = _put(game, 0, t, "stack")
    bear = _put(game, 1, _template("Bear", [CardType.CREATURE], 2, 4))
    _resolve(game, bolt, host, ((er.handle_of(bear),),))
    assert bear.damage_marked == 3
    assert game.log[-1].endswith(f"deals 3 to {bear.name}")


# ── The executors' contract ────────────────────────────────────────────

def test_the_executors_read_no_oracle_text_and_write_no_state_themselves():
    """They bind typed specs and call owners: no oracle read, no regex or
    parse, and no assignment except to locals and to their own tables."""
    tree = ast.parse(EXECUTORS_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("oracle_text", "text_lower"), node.lineno
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""])
            for m in mods:
                assert m not in ("re", "regex"), node.lineno
                assert not any(p in m for p in ("effect_grammar", "oracle_parser",
                                                "target_solver")), node.lineno
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = list(node.targets if isinstance(node, ast.Assign)
                           else [node.target])
            while targets:
                t = targets.pop()
                if isinstance(t, (ast.Tuple, ast.List)):
                    targets.extend(t.elts)
                    continue
                if isinstance(t, ast.Name):
                    continue
                root = t
                while isinstance(root, (ast.Attribute, ast.Subscript)):
                    root = root.value
                own_table = (isinstance(t, ast.Subscript) and isinstance(root, ast.Name)
                             and root.id in ("EXECUTORS", "CONDITION_EVALUATORS"))
                own_attr = (isinstance(t, ast.Attribute) and t.attr == "supports"
                            and isinstance(t.value, ast.Name))
                assert own_table or own_attr, f"line {node.lineno}"
