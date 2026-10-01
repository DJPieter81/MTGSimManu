"""Payload sub-grammar of the clause grammar (design doc 2026-09-29,
section 4 payload column; section 2 ``Payload`` union; E0).

`engine/effect_grammar/sub/payload.py` types the object of a payload verb
once, at load: mana (CR 106), counters (CR 122), tokens (CR 111), continuous
modifications (CR 611-613), keyword actions (CR 701) and alternatives (A19).
It is a closed table: an unknown phrase is a typed UNMODELLED, never a guess.

Synthetic oracle phrases only; no card names. The e0_tests named in
``test_effect_grammar_amounts_conditions.py`` / ``..._structure.py`` whose
payload half this leaf owns carry the same test names here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from engine.effect_model import ModKind, Modification
from engine.effect_spec import (Amount, AmountKind, CounterSpec, Granted,
                                KeywordAction, ManaSpec, Ref, RefKind, Stage,
                                TokenSpec, Verb, canonical)
from engine.effect_grammar.sub.payload import (
    SlotResult, adds_mana, parse_alternatives, parse_cost_modifier,
    parse_counters, parse_keyword_action, parse_mana, parse_modification,
    parse_payload, KEYWORD_ACTION_NAMES)


@dataclass(frozen=True)
class _Entry:
    """A stand-in lexicon entry: the fields the payload leaf reads."""
    verb: Verb
    lemma: str = ""
    mod_kind: Optional[ModKind] = None


def _lit(n):
    return Amount(AmountKind.LITERAL, n=n)


def _mana(text):
    r = parse_mana(text, (0, len(text)))
    assert isinstance(r, SlotResult)
    return r


def _rest(r, host):
    """The rest spans' text: every leaf hands the rest on as host spans."""
    return r.rest_text(host)


# ── Mana (CR 106.1, 106.4): a symbol multiset, not an amount ──────────

def test_mana_is_a_symbol_multiset_not_an_amount():
    r = _mana("{g}{g}")
    assert r.value == ManaSpec(symbols=("G", "G"))
    assert r.amount is None                       # never Amount(LITERAL, 2)
    assert r.rest_spans == ()
    # Order-insensitive multiset: {W}{U} and {U}{W} add the same mana.
    assert sorted(_mana("{w}{u}").value.symbols) == sorted(
        _mana("{u}{w}").value.symbols)
    # Colourless and generic-shaped symbols stay symbols.
    assert _mana("{c}{c}").value.symbols == ("C", "C")
    # A counted "mana of any color" is one wildcard unit per mana.
    r = _mana("three mana of any one color")
    assert r.value.symbols == ("*", "*", "*") and r.value.one_color
    assert r.amount is None
    # A variable count is the per-unit multiset plus a multiplier amount.
    r = _mana("x mana of any one color")
    assert r.value.symbols == ("*",)
    assert r.amount == Amount(AmountKind.X, n=1)
    # "for each" is a multiplier the amount grammar owns: left in `rest`.
    text = "{g} for each elf you control"
    r = _mana(text)
    assert r.value.symbols == ("G",) and _rest(r, text) == "for each elf you control"


@pytest.mark.parametrize("text,choice", [
    ("{r} or {g}", ("{R}", "{G}")),
    ("{w}, {u}, or {b}", ("{W}", "{U}", "{B}")),
    ("{b}{b} or {r}{r}", ("{B}{B}", "{R}{R}")),
])
def test_a_mana_choice_is_a_set_of_symbol_multisets(text, choice):
    r = _mana(text)
    assert r.value.choice == choice and r.value.symbols == ()


@pytest.mark.parametrize("text,flag", [
    ("one mana of any color", "any_color"),
    ("two mana of any one color", "one_color"),
    ("three mana in any combination of colors", "combination"),
    ("one mana of the chosen color", "chosen_color"),
])
def test_colour_choice_mana_forms_are_typed_flags(text, flag):
    spec = _mana(text).value
    assert getattr(spec, flag) is True
    assert set(spec.symbols) == {"*"}


def test_a_combination_restricted_to_symbols_keeps_the_symbol_set():
    spec = _mana("two mana in any combination of {r} and/or {g}").value
    assert spec.combination and spec.choice == ("{R}", "{G}")
    assert spec.symbols == ("*", "*")


def test_mana_of_the_type_a_tapped_land_produced_mirrors_the_event_object():
    spec = _mana("one mana of any type that land produced").value
    assert spec.mirror == Ref(RefKind.EVENT_OBJECT)


def test_an_additional_mana_rider_is_the_same_multiset():
    assert _mana("an additional {g}").value == ManaSpec(symbols=("G",))


def test_spend_this_mana_only_is_a_restriction_on_the_mana_spec():
    r = _mana("{c}{c}. spend this mana only to cast colorless spells")
    assert r.value.symbols == ("C", "C")
    assert r.value.restriction == "to cast colorless spells"
    assert r.rest_spans == ()


def test_an_unknown_mana_phrase_is_unmodelled_not_a_guess():
    r = _mana("one mana of any color that a gate you control could produce")
    assert r.value is None and r.unmodelled is not None
    assert r.unmodelled.stage is Stage.CLAUSE


def test_mana_spans_are_in_the_callers_coordinates():
    host = "you may add {g}{g} instead"
    at = host.index("{g}")
    r = parse_mana(host, (at, len(host)))
    assert r.span == (at, at + 6) and host[slice(*r.span)] == "{g}{g}"
    assert r.rest_spans == ((at + 7, len(host)),)
    assert _rest(r, host) == "instead"


# ── A6: ADD_MANA recognition for the mana-ability rule (CR 605.1a/b) ──

@pytest.mark.parametrize("text", [
    "add {c}",
    "add {r} or {u}. ~ deals 1 damage to you",   # painland rider
    "add one mana of any color",
    "add {c}{c}{c} instead",
    "its controller adds an additional {g}",       # triggered, CR 605.1b
    "add x mana of any one color",
])
def test_an_activated_ability_that_could_add_mana_without_a_target_is_a_mana_ability_even_with_riders(text):
    # Payload half of the structure test (CR 605.1a): the ADD_MANA clause is
    # recognised with riders around it; structure adds the no-target check.
    assert adds_mana(text)


@pytest.mark.parametrize("text", [
    "draw a card",
    "put a +1/+1 counter on target creature",
    "it's a 2/2 creature in addition to its other types",
    "creatures you control get +1/+1",
])
def test_text_without_an_add_mana_clause_is_not_a_mana_ability(text):
    assert not adds_mana(text)


def test_a_triggered_ability_that_adds_mana_from_a_mana_event_is_a_triggered_mana_ability():
    # Payload half (CR 605.1b): the body of a tapped-for-mana trigger types
    # as ADD_MANA with a ManaSpec, so structure can flag the host.
    body = "its controller adds an additional one mana of any color"
    assert adds_mana(body)
    r = parse_payload(_Entry(Verb.ADD_MANA, "add"),
                      "an additional one mana of any color", (0, 35), None)
    assert isinstance(r.value, ManaSpec) and r.value.any_color


# ── A8: activation cost modifiers (CR 602.2b applying 601.2f) ─────────

def test_an_activation_cost_modifier_is_absorbed_onto_the_host_not_resolved():
    text = ("this ability costs {1} less to activate for each legendary "
            "creature you control")
    r = parse_cost_modifier(text, (0, len(text)))
    mod = r.value
    assert isinstance(mod, Modification) and mod.kind is ModKind.COST_DELTA
    assert mod.get("scope") == "this_ability"
    assert mod.get("cost_of") == "activate"
    assert mod.get("sign") == -1 and mod.get("amount") == _lit(1)
    assert _rest(r, text) == "for each legendary creature you control"


def test_a_spell_or_static_cost_delta_types_scope_and_direction():
    # L0 has rewritten "this spell" to ~ (the leaf contract).
    r = parse_cost_modifier("~ costs {2} more to cast", (0, 24))
    assert r.value.get("scope") == "this_spell" and r.value.get("sign") == 1
    r = parse_cost_modifier(
        "noncreature spells cost {1} more to cast", (0, 40))
    assert r.value.get("scope") == "spells"
    assert r.value.get("subject") == "noncreature spells"


def test_text_that_is_not_a_cost_modifier_returns_none():
    assert parse_cost_modifier("draw a card", (0, 11)) is None


# ── Counters (CR 122): a kind multiset ────────────────────────────────

def _ctr(text):
    return parse_counters(text, (0, len(text)))


def test_literal_counters_are_a_kind_multiset_and_the_rest_is_the_object():
    text = "two +1/+1 counters on target creature"
    r = _ctr(text)
    assert r.value == CounterSpec(kinds=("+1/+1", "+1/+1"))
    assert r.amount is None and _rest(r, text) == "on target creature"


def test_mixed_counter_groups_join_into_one_multiset():
    r = _ctr("two +1/+1 counters and a flying counter on target creature")
    assert r.value.kinds == ("+1/+1", "+1/+1", "flying")


def test_a_counter_kind_list_with_or_is_a_choice():
    r = _ctr("a flying, first strike, or lifelink counter on it")
    assert r.value == CounterSpec(kinds=("flying", "first strike", "lifelink"),
                                  choice=True)


def test_variable_counter_counts_are_amounts():
    assert _ctr("x +1/+1 counters on it").amount == Amount(AmountKind.X, n=1)
    assert _ctr("that many -1/-1 counters on it").amount.kind is \
        AmountKind.THAT_MUCH
    assert _ctr("all counters from it").amount.kind is AmountKind.ALL
    assert _ctr("all counters from it").value.kinds == ("*",)


def test_energy_symbols_are_player_counters():
    r = parse_payload(_Entry(Verb.PLAYER_COUNTERS, "get"), "{e}{e}{e}",
                      (0, 9), None)
    assert r.value == CounterSpec(kinds=("energy",) * 3)
    r = parse_payload(_Entry(Verb.PLAYER_COUNTERS, "get"),
                      "two poison counters", (0, 19), None)
    assert r.value == CounterSpec(kinds=("poison", "poison"))


# ── Tokens (CR 111) ───────────────────────────────────────────────────

def _tok(text):
    return parse_payload(_Entry(Verb.CREATE_TOKEN, "create"), text,
                         (0, len(text)), None)


def test_a_creature_token_types_pt_colours_subtypes_types_and_keywords():
    r = _tok("two 1/1 white soldier creature tokens with flying and lifelink")
    t = r.value
    assert isinstance(t, TokenSpec)
    assert (t.power, t.toughness) == (_lit(1), _lit(1))
    assert t.colors == frozenset({"W"})
    assert t.subtypes == ("soldier",) and t.types == ("creature",)
    assert t.keywords == (("flying", None), ("lifelink", None))
    assert r.amount == _lit(2)


def test_a_predefined_token_is_named_by_its_cr_111_10_kind():
    t = _tok("a treasure token").value
    assert t.predefined == "treasure" and t.types == ()


def test_a_token_granted_ability_is_left_for_the_linker():
    r = _tok("a 1/1 colorless eldrazi scion creature token with ⟨q0⟩")
    assert r.value.colors == frozenset()
    assert r.value.subtypes == ("eldrazi", "scion")
    assert ("granted", "⟨q0⟩") in r.pending


def test_variable_token_power_is_an_x_amount():
    t = _tok("an x/x green ooze creature token").value
    assert t.power == Amount(AmountKind.X, n=1)


def test_a_copy_token_of_self_is_a_self_ref():
    t = _tok("a token that's a copy of ~").value
    assert t.copy_of == Ref(RefKind.SELF)


# ── A19: resolution-time alternatives ─────────────────────────────────

def test_your_choice_of_x_or_y_is_a_resolution_time_alternative():
    def options(text):
        return tuple(text[a:b] for a, b in parse_alternatives(text, (0, len(text))))
    assert options("your choice of flying or first strike") == (
        "flying", "first strike")
    assert options("a food token or a treasure token") == (
        "a food token", "a treasure token")
    # A plain disjunction inside one payload is not an alternative.
    assert parse_alternatives("{r} or {g}", (0, 10)) == ()
    # The payload keeps every option, typed, for EffectSpec.alternatives.
    r = _tok("a food token or a treasure token")
    assert r.value is None
    assert [a.value.predefined for a in r.alternatives] == ["food",
                                                            "treasure"]
    r = parse_payload(_Entry(Verb.CONTINUOUS, "gain", ModKind.ADD_KEYWORDS),
                      "gains your choice of flying or first strike",
                      (0, 43), None)
    kws = [a.value.get("keywords") for a in r.alternatives]
    assert kws == [(("flying", None),), (("first_strike", None),)]


# ── Modifications (CR 611-613) ────────────────────────────────────────

def _mod(text, kind=None, lemma=""):
    r = parse_modification(_Entry(Verb.CONTINUOUS, lemma, kind), text,
                           (0, len(text)))
    return r


@pytest.mark.parametrize("text,kind,data", [
    ("gets +2/+2", ModKind.MODIFY_PT,
     {"power": _lit(2), "toughness": _lit(2)}),
    ("get -1/-0", ModKind.MODIFY_PT,
     {"power": _lit(-1), "toughness": _lit(0)}),
    ("gains flying and first strike", ModKind.ADD_KEYWORDS,
     {"keywords": (("flying", None), ("first_strike", None))}),
    ("have protection from red", ModKind.ADD_KEYWORDS,
     {"keywords": (("protection", "red"),)}),
    ("loses flying", ModKind.REMOVE_KEYWORDS,
     {"keywords": (("flying", None),)}),
    ("loses all abilities", ModKind.REMOVE_ALL_ABILITIES, {}),
    ("has base power and toughness 1/1", ModKind.SET_BASE_PT,
     {"power": _lit(1), "toughness": _lit(1)}),
    ("gains ⟨q0⟩", ModKind.GRANT_ABILITY, {"abilities": ("⟨q0⟩",)}),
    ("can't block", ModKind.PROHIBIT, {}),
    ("attacks each combat if able", ModKind.REQUIRE, {}),
])
def test_a_continuous_predicate_types_one_modification(text, kind, data):
    r = _mod(text)
    assert isinstance(r.value, Modification), r
    assert r.value.kind is kind
    for k, v in data.items():
        assert r.value.get(k) == v


def test_a_prohibition_names_its_action():
    assert _mod("can't block").value.action == "block"
    both = _mod("can't attack or block").value
    assert both.get("actions") == ("attack", "block")


def test_a_type_change_in_addition_to_other_types_adds_types():
    r = _mod("becomes an angel in addition to its other types")
    assert r.value.kind is ModKind.ADD_TYPES
    assert r.value.get("subtypes") == ("angel",)


def test_a_becomes_creature_sets_types_and_base_pt():
    r = _mod("becomes a 3/3 red elemental creature with haste")
    m = r.value
    assert m.kind is ModKind.SET_TYPES
    assert m.get("types") == ("creature",)
    assert m.get("colors") == ("R",)
    assert (m.get("power"), m.get("toughness")) == (_lit(3), _lit(3))
    assert m.get("keywords") == (("haste", None),)


def test_a_pt_scaled_by_a_quantity_leaves_the_scaler_in_rest():
    text = "gets +1/+1 for each artifact you control"
    r = _mod(text)
    assert r.value.get("power") == _lit(1)
    assert _rest(r, text) == "for each artifact you control"


def test_an_unknown_predicate_is_unmodelled():
    r = _mod("phases out")
    assert r.value is None and r.unmodelled is not None


# ── CR 701 keyword actions ────────────────────────────────────────────

@pytest.mark.parametrize("text,name,amount,subtype", [
    ("investigate", "investigate", None, None),
    ("investigate twice", "investigate", _lit(2), None),
    ("proliferate", "proliferate", None, None),
    ("amass zombies 2", "amass", _lit(2), "zombie"),
    ("amass orcs x", "amass", Amount(AmountKind.X, n=1), "orc"),
    ("adapt 3", "adapt", _lit(3), None),
    ("discover 4", "discover", _lit(4), None),
    ("collect evidence 6", "collect evidence", _lit(6), None),
    ("connives", "connive", None, None),
])
def test_a_keyword_action_is_named_with_its_parameter(text, name, amount,
                                                      subtype):
    r = parse_keyword_action(text, (0, len(text)))
    assert r.value == KeywordAction(name=name, amount=amount, subtype=subtype)


def test_every_keyword_action_name_parses_without_unmodelled():
    # The payload half of the keyword-expansion-table pool test (CR 701):
    # each typed action name in its bare form is a KeywordAction.
    for name, param in KEYWORD_ACTION_NAMES.items():
        text = {"none": name, "n": name + " 2",
                "subtype_n": name + " zombies 2", "object": name}[param]
        r = parse_keyword_action(text, (0, len(text)))
        assert isinstance(r.value, KeywordAction), (name, r)


def test_a_recognised_unsupported_keyword_action_is_typed_as_such():
    r = parse_keyword_action("venture into the dungeon", (0, 24))
    assert r.value is None
    assert r.unmodelled.stage is Stage.RECOGNIZED_UNSUPPORTED


# ── Emblems and payload determinism ───────────────────────────────────

def test_an_emblem_payload_is_a_granted_placeholder():
    r = parse_payload(_Entry(Verb.CREATE_EMBLEM, "get"),
                      "an emblem with ⟨q1⟩", (0, 19), None)
    assert r.value == Granted()
    assert ("granted", "⟨q1⟩") in r.pending


def test_a_verb_without_a_payload_returns_nothing_here():
    """The contract's one "nothing here" encoding is None."""
    assert parse_payload(_Entry(Verb.DRAW, "draw"), "two cards", (0, 9),
                         None) is None


def test_the_payload_parse_is_a_pure_function_of_its_text():
    text = "two 1/1 white soldier creature tokens with flying"
    a = _tok(text)
    b = _tok(text)
    assert canonical(a) == canonical(b)
    hash(a)                                # frozen and hashable (A31)


# ── Review fixes: shared tails, qualifiers, closed tables ─────────────

def _cont(text, lemma="gain"):
    return parse_payload(_Entry(Verb.CONTINUOUS, lemma), text,
                         (0, len(text)), None)


def test_an_alternative_shares_its_trailing_duration_with_every_option():
    # A19: the duration after the last option qualifies the choice, not the
    # last option alone; it is the outer rest and every option's rest.
    text = "gains your choice of double strike or lifelink until end of turn"
    r = _cont(text)
    assert r.value is None and _rest(r, text) == "until end of turn"
    kws = [a.value.get("keywords") for a in r.alternatives]
    assert kws == [(("double_strike", None),), (("lifelink", None),)]
    assert [_rest(a, text) for a in r.alternatives] == ["until end of turn"] * 2
    # Each option's span covers exactly that option's text.
    assert [text[slice(*a.span)] for a in r.alternatives] == [
        "double strike", "lifelink"]


def test_an_alternative_shares_its_trailing_object_with_every_option():
    text = "your choice of a +1/+1 counter or a flying counter on it"
    r = parse_payload(_Entry(Verb.PUT_COUNTERS, "put"), text,
                      (0, len(text)), None)
    assert _rest(r, text) == "on it"
    assert [a.value.kinds for a in r.alternatives] == [("+1/+1",),
                                                       ("flying",)]
    assert [_rest(a, text) for a in r.alternatives] == ["on it", "on it"]


@pytest.mark.parametrize("text,rest", [
    ("a food token or a treasure token.", ""),
    ("a food token or a treasure token for each opponent",
     "for each opponent"),
])
def test_a_token_alternative_is_recognised_before_a_trailing_scaler(text,
                                                                    rest):
    r = _tok(text)
    assert r.value is None
    assert [a.value.predefined for a in r.alternatives] == ["food",
                                                            "treasure"]
    assert _rest(r, text) == rest


def test_a_counter_alternative_is_recognised_before_its_object():
    text = "a +1/+1 counter or a flying counter on it"
    r = parse_payload(_Entry(Verb.PUT_COUNTERS, "put"), text,
                      (0, len(text)), None)
    assert r.value is None
    assert [a.value.kinds for a in r.alternatives] == [("+1/+1",),
                                                       ("flying",)]
    assert _rest(r, text) == "on it"


def test_an_unrecognised_payload_disjunction_is_unmodelled_not_one_option():
    # "<payload> or <payload>" left after one typed payload would silently
    # drop the choice: the slot is Unmodelled instead.
    text = "a 1/1 white soldier creature token or a clue token with ⟨q0⟩"
    r = _tok(text)
    assert r.value is None
    assert r.unmodelled is not None or r.alternatives


def test_a_prohibition_with_an_unowned_qualifier_is_unmodelled():
    for text in ("can't attack or block alone",
                 "can't be blocked except by creatures with flying",
                 "can't be blocked by more than one creature"):
        r = _cont(text, "can't")
        assert r.value is None and r.unmodelled is not None, (text, r)


def test_a_two_action_prohibition_types_both_actions():
    text = "can't block or be blocked this turn"
    r = _cont(text, "can't")
    assert r.value.kind is ModKind.PROHIBIT
    assert r.value.get("actions") == ("block", "be_blocked")
    assert _rest(r, text) == "this turn"
    text = "can't attack or block this turn"
    r = _cont(text, "can't")
    assert r.value.get("actions") == ("attack", "block")
    assert _rest(r, text) == "this turn"


def test_a_copy_token_object_ends_at_its_sentence():
    text = ("a token that's a copy of target creature you control. it gains "
            "haste. sacrifice it at the beginning of the next end step")
    r = _tok(text)
    assert ("copy_of", "target creature you control") in r.pending
    assert _rest(r, text).startswith("it gains haste")


def test_a_copy_token_of_self_keeps_its_ref_and_entry_rider():
    r = _tok("a token that's a copy of ~ that's tapped and attacking")
    assert r.value.copy_of == Ref(RefKind.SELF)
    assert ("entry", "tapped and attacking") in r.pending
    assert not any(k == "copy_of" for k, _ in r.pending)
    assert r.rest_spans == ()


@pytest.mark.parametrize("text", [
    "has protection from each of your opponents",
    "gains annihilator 2x",
    "has hexproof from each of your opponents",
])
def test_a_keyword_item_outside_the_closed_vocabulary_is_unmodelled(text):
    r = _cont(text)
    assert r.value is None and r.unmodelled is not None, r


def test_a_protection_quality_from_the_closed_vocabulary_is_typed():
    r = _cont("gains protection from the color of your choice until end of "
              "turn")
    assert r.value.get("keywords") == (("protection",
                                        "the color of your choice"),)
    assert _cont("has protection from artifacts").value.get("keywords") == (
        ("protection", "artifacts"),)


@pytest.mark.parametrize("cost", ["{s}", "{b/p}", "{2/w}", "{w/u}"])
def test_a_pay_cost_the_cost_owner_cannot_represent_is_unmodelled(cost):
    r = parse_payload(_Entry(Verb.PAY, "pay"), cost, (0, len(cost)), None)
    assert r.value is None and r.unmodelled is not None, r


def test_a_representable_pay_cost_is_a_cost_snapshot():
    from engine.effect_spec import CostSnapshot
    r = parse_payload(_Entry(Verb.PAY, "pay"), "{2}{r}", (0, 6), None)
    assert isinstance(r.value, CostSnapshot)


@pytest.mark.parametrize("subject", [
    "domain - ~", "this ability and ~", "a creature"])
def test_a_cost_modifier_subject_outside_the_table_is_unmodelled(subject):
    text = subject + " costs {1} less to cast"
    r = parse_cost_modifier(text, (0, len(text)))
    assert r.value is None and r.unmodelled is not None, r


@pytest.mark.parametrize("text", [
    "equip costs you pay cost {1} less.",
    "dash costs you pay cost {2} less.",
    "unlock costs you pay cost {1} less.",
    "plotting cards from your hand costs {2} less.",
    "boast abilities you activate cost {1} more."])
def test_a_cost_delta_naming_no_cast_or_activation_is_unmodelled_never_absent(text):
    """CR 601.2f / 118.7: a reduction scoped to a keyword's cost or a
    special action is not a spell or ability cost modifier the subject
    table types. It is refused, so the clause never falls through to a PAY
    or continuous reading, and it is never widened to every spell."""
    r = parse_cost_modifier(text, (0, len(text)))
    assert r is not None, text
    assert r.value is None
    assert r.unmodelled.detail == "payload.cost_delta_subject"


def test_a_global_cost_modifier_names_spells_or_abilities():
    r = parse_cost_modifier(
        "the first instant spell you cast each turn costs {1} less to cast",
        (0, 66))
    assert r.value.get("scope") == "spells"
    r = parse_cost_modifier(
        "activated abilities of creatures you control cost {1} less to "
        "activate", (0, 71))
    assert r.value.get("scope") == "abilities"


def test_a_keyword_action_fallback_that_consumes_nothing_is_unmodelled():
    r = parse_payload(_Entry(Verb.KEYWORD_ACTION, "explore"), "it explores",
                      (0, 11), None)
    assert r.value is None and r.unmodelled is not None
    r = parse_payload(_Entry(Verb.KEYWORD_ACTION, "amass"), "zombies 2",
                      (0, 9), None)
    assert r.value == KeywordAction(name="amass", amount=_lit(2),
                                    subtype="zombie")
    assert r.span[1] > r.span[0]


def test_a_costed_keyword_grant_leaves_its_cost_for_the_cost_rule():
    # A8: "gains flashback" with no printed cost takes its cost from the
    # "flashback cost is equal to its mana cost" rider, linked later.
    text = "gains flashback until end of turn"
    r = _cont(text)
    assert r.value.kind is ModKind.ADD_KEYWORDS
    assert r.value.get("keywords") == (("flashback", None),)
    assert ("cost_rule", "flashback") in r.pending
    assert _rest(r, text) == "until end of turn"
    r = _cont("has flashback {2}{r}")
    assert r.value.get("keywords") == (("flashback", "{2}{R}"),)
    assert r.pending == ()
