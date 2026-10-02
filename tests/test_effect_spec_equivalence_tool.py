"""The field views of the clause grammar (design doc 2026-09-29, section 10
and its per-field derivation table 10.1; E0, no behaviour change).

`engine/effect_views.py` holds one `FieldDerivation` per legacy typed
field the table lists (the derivation from `CardTemplate.effects`, the
family's strict-shape predicate and, where legacy's domain is narrower than
the grammar's, a `_legacy_domain_*` mask), `NON_EFFECT_FIELDS` for every
other legacy field, the printed-span views `kicked_clause` /
`channel_clause` (A40) and `host_for_override` (A41), the static table the
switched handlers will resolve an `oracle_override` through. Nothing in the
engine calls the module in E0; the equivalence tool
(`tools/effect_spec_equivalence.py`, later in E0) reads it.

The rules below are phrased by mechanic; the synthetic templates carry
generic names, and the deck-card pins read the shared card DB without
mutating it.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
VIEWS = REPO / "engine" / "effect_views.py"


# ── helpers ────────────────────────────────────────────────────────────

def _views():
    from engine import effect_views
    return effect_views


def _template(name, types, text, *, subtypes=(), supertypes=()):
    """A synthetic template (never the shared DB's): a fresh object whose
    `effects` property parses its own text lazily."""
    from engine.cards import CardTemplate, CardType, ManaCost, Supertype
    return CardTemplate(
        name=name, card_types=[CardType(t) for t in types],
        mana_cost=ManaCost(), oracle_text=text, subtypes=list(subtypes),
        supertypes=[Supertype(s) for s in supertypes])


def _derive(field, template, key=None):
    rec = _views().DERIVATIONS[field]
    return rec.derive(template.effects, key=key, template=template)


def _deck_card_names():
    from decks.modern_meta import MODERN_DECKS
    return sorted({c for d in MODERN_DECKS.values()
                   for part in ("mainboard", "sideboard")
                   for c in (d.get(part) or {})})


def _deck_templates(card_db):
    out = []
    for n in _deck_card_names():
        t = card_db.cards.get(n)
        if t is not None:
            out.append(t)
    return out


# ── the derivation table is complete and typed (section 10, A41) ───────

# Every field the per-field derivation table (10.1) names, with the tier
# and step the row gives it. Scoped carriers use `Carrier.field[KIND]`.
TABLE_ROWS = {
    "direct_damage_data": ("A", "E1"),
    "ActivatedAbility.effect_kind[DAMAGE_ANY_TARGET]": ("A", "E1"),
    "LoyaltyAbility.effect_kind[DAMAGE]": ("A", "E1"),
    "LoyaltyAbility.effect_kind[GAIN_LIFE_AND_DRAW]": ("A", "E1"),
    "tap_damage": ("A", "E1"),
    "has_energy_damage_target": ("A", "E1"),
    "has_x_damage": ("B", "E1"),
    "ordinal_cast_trigger": ("C", "E1"),
    "creature_dies_observer": ("C", "E1"),
    "attack_observer": ("C", "E1"),
    "cycling_watch_trigger_damage": ("C", "E1"),
    "landfall_first_life_gain": ("C", "E1"),
    "landfall_third_damage": ("C", "E1"),
    "has_opponent_cast_damage": ("C", "E1"),
    "has_another_creature_enters_lifegain": ("C", "E1"),
    "targeted_removal_data": ("A", "E2"),
    "modes[removal]": ("A", "E2"),
    "etb_targeted_removal_data": ("A", "E2"),
    "removal_mv_condition": ("A", "E2"),
    "board_sweep_data": ("A", "E2"),
    "bounce_target": ("A", "E2"),
    "land_destruction_data": ("A", "E2"),
    "destroys_target_land": ("A", "E2"),
    "mass_graveyard_return": ("A", "E2"),
    "has_symmetric_reanimation": ("B", "E2"),
    "ActivatedAbility.graveyard_exile_data": ("A", "E2"),
    "LoyaltyAbility.effect_kind[RETURN_TO_HAND]": ("A", "E2"),
    "LoyaltyAbility.effect_kind[TUCK_TARGET_INTO_LIBRARY]": ("A", "E2"),
    "LoyaltyAbility.effect_kind[EMBLEM_EXILE_PERMANENT]": ("A", "E2"),
    "etb_exile_returns_on_leave": ("A", "E2"),
    "loot_data": ("A", "E3"),
    "hand_attack_data": ("A", "E3"),
    "library_dig_data": ("A", "E3"),
    "hand_refill": ("A", "E3"),
    "x_creature_tutor_data": ("A", "E3"),
    "ActivatedAbility.effect_kind[TUTOR_TO_HAND]": ("A", "E3"),
    "ActivatedAbility.effect_kind[TUTOR_CREATURE_TO_BATTLEFIELD]": ("A", "E3"),
    "fetchland": ("A", "E3"),
    "is_land_sacrifice_tutor": ("A", "E3"),
    "ActivatedAbility.effect_kind[DRAW_N]": ("A", "E3"),
    "cycling_variant_data": ("C", "E3"),
    "ActivatedAbility.effect_kind[PUT_COUNTER_SELF]": ("A", "E4"),
    "ActivatedAbility.effect_kind[PUT_COUNTER_TARGET]": ("A", "E4"),
    "ActivatedAbility.effect_kind[PUT_COUNTER_TEAM]": ("A", "E4"),
    "pump_spell_power": ("A", "E5"),
    "pump_spell_toughness": ("A", "E5"),
    "pump_spell_keyword": ("A", "E5"),
    "pump_spell_keywords": ("A", "E5"),
    "team_pump_data": ("A", "E5"),
    "next_turn_effect": ("A", "E5"),
    "turn_scoped_restriction": ("A", "E5"),
    "cast_prohibition": ("A", "E5"),
    "object_restriction": ("A", "E5"),
    "group_restriction": ("A", "E5"),
    "equip_power_grant": ("A", "E5"),
    "equip_toughness_grant": ("A", "E5"),
    "equip_keyword_grant": ("A", "E5"),
    "team_keyword_grant": ("A", "E5"),
    "ActivatedAbility.effect_kind[PUMP_SELF_UEOT]": ("A", "E5"),
    "ActivatedAbility.effect_kind[ANIMATE_SELF_UEOT]": ("A", "E5"),
    "ActivatedAbility.effect_kind[GRANT_HASTE_TARGET]": ("A", "E5"),
    "ActivatedAbility.effect_kind[UNTAP_TARGET_PERMANENT]": ("A", "E5"),
    "LoyaltyAbility.effect_kind[DRAW_AND_UNTAP_LANDS]": ("A", "E5"),
    "is_counterspell": ("A", "E6"),
    "counter_target_kind": ("A", "E6"),
    "counter_tax_amount": ("A", "E6"),
    "counter_upgrade_condition": ("A", "E6"),
    "counters_colorless_only": ("A", "E6"),
    "ritual_mana": ("A", "E6"),
    "mana_units": ("A", "E6"),
    "sacrifice_mana_units": ("A", "E6"),
    "conditional_mana": ("A", "E6"),
    "cost_reduction_rule": ("A", "E6"),
    "self_cost_reduction_amount": ("A", "E6"),
    "self_cost_reduction_unit": ("A", "E6"),
    "domain_reduction": ("A", "E6"),
    "kicked_clause": ("A", "E6"),
    "channel_clause": ("A", "E6"),
    "aura_mana_units": ("C", "E6"),
    "tap_for_mana_trigger": ("C", "E6"),
}


def _legacy_fields():
    """Every public field of the legacy carriers: CardTemplate,
    ActivatedAbility and LoyaltyAbility (scoped `Carrier.field`)."""
    from engine.cards import ActivatedAbility, CardTemplate, LoyaltyAbility
    out = [f.name for f in dataclasses.fields(CardTemplate)
           if not f.name.startswith("_")]
    for cls in (ActivatedAbility, LoyaltyAbility):
        out += [f"{cls.__name__}.{f.name}" for f in dataclasses.fields(cls)]
    return out


def _covered(field):
    v = _views()
    return field in v.NON_EFFECT_FIELDS or any(
        k == field or k.startswith(field + "[") for k in v.DERIVATIONS)


def test_every_public_field_of_the_legacy_carriers_is_derived_or_named_non_effect():
    missing = [f for f in _legacy_fields() if not _covered(f)]
    assert not missing, missing


def test_every_card_database_template_assignment_is_derived_or_named_non_effect():
    """A41's completeness scan: every `template.<attr> = ...` in
    engine/card_database.py names a field the views account for."""
    tree = ast.parse((REPO / "engine" / "card_database.py").read_text())
    attrs = []
    for n in ast.walk(tree):
        targets = (n.targets if isinstance(n, ast.Assign) else
                   [n.target] if isinstance(n, (ast.AugAssign, ast.AnnAssign))
                   else [])
        for t in targets:
            for x in (t.elts if isinstance(t, ast.Tuple) else [t]):
                if isinstance(x, ast.Attribute) and \
                        isinstance(x.value, ast.Name) and x.value.id == "template":
                    attrs.append(x.attr)
    # The scan is not vacuous: the design counted 193 assignments.
    assert len(attrs) >= 190, len(attrs)
    missing = sorted({a for a in attrs if not _covered(a)})
    assert not missing, missing


def test_a_field_is_either_derived_or_non_effect_never_both():
    v = _views()
    bases = {k.split("[", 1)[0] for k in v.DERIVATIONS}
    assert not (bases & set(v.NON_EFFECT_FIELDS)), bases & set(v.NON_EFFECT_FIELDS)
    assert all(isinstance(r, str) and r for r in v.NON_EFFECT_FIELDS.values())


def test_every_field_the_derivation_table_names_has_a_record_at_its_tier_and_step():
    v = _views()
    wrong = {}
    for field, (tier, step) in TABLE_ROWS.items():
        rec = v.DERIVATIONS.get(field)
        if rec is None or (rec.tier, rec.estep) != (tier, step):
            wrong[field] = None if rec is None else (rec.tier, rec.estep)
    assert not wrong, wrong


def test_a_derivation_record_is_typed_and_names_its_strict_shape_and_mask():
    v = _views()
    for name, rec in v.DERIVATIONS.items():
        assert rec.field == name
        assert rec.tier in v.TIERS, name
        assert rec.estep in v.ESTEPS, name
        assert rec.scope in v.SCOPES, name
        assert rec.family in v.STRICT, name
        assert rec.strict is v.STRICT[rec.family], name
        assert callable(rec.view) and callable(rec.legacy), name
        if rec.domain is not None:
            assert rec.domain.__name__.startswith("_legacy_domain_"), name
        # A scoped record names its carrier; a card record does not.
        assert (rec.scope == v.SCOPE_CARD) == ("." not in name
                                               and not name.startswith("modes")), name


def test_the_named_legacy_quirk_predicates_and_domain_masks_are_counted():
    """Ratchet (c) counts the `_legacy_*` quirk predicates and the
    `_legacy_domain_*` masks; the module lists exactly the functions it
    defines under those prefixes, and the ones the table names exist."""
    v = _views()
    tree = ast.parse(VIEWS.read_text())
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name.startswith("_legacy_")}
    assert set(v.LEGACY_PREDICATES) == defined
    for named in ("_legacy_prefix_window", "_legacy_rider_tokens",
                  "_legacy_loyalty_damage_word", "_legacy_tap_damage_one"):
        assert named in defined, named
    masks = {n for n in defined if n.startswith("_legacy_domain_")}
    assert masks, "A39 needs at least the ETB-removal domain mask"
    used = {r.domain.__name__ for r in v.DERIVATIONS.values() if r.domain}
    assert used <= masks and masks <= used, (masks, used)


# ── the module's boundary (section 13) ─────────────────────────────────

def test_the_views_module_reads_no_oracle_text_and_is_policed_by_the_runtime_parse_ratchet():
    import sys
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import check_oracle_runtime_parse as ratchet
    finally:
        sys.path.pop(0)
    assert "engine/effect_views.py" not in ratchet._EXCLUDED
    assert ratchet._count_violations(VIEWS) == []
    # It reads specs and host text: no oracle attribute is touched at all;
    # printed text comes from the grammar's own parse input.
    tree = ast.parse(VIEWS.read_text())
    reads = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
             and n.attr in {"oracle_text", "oracle", "back_face_oracle"}]
    assert not reads, reads


def test_no_engine_or_ai_module_calls_the_views_in_e0():
    hits = []
    for d in ("engine", "ai"):
        for p in sorted((REPO / d).rglob("*.py")):
            if p == VIEWS:
                continue
            if "effect_views" in p.read_text():
                hits.append(str(p.relative_to(REPO)))
    assert not hits, hits


def test_the_views_hold_no_card_names(card_db):
    names = set()
    for n in card_db._raw_data:
        names.add(n)
        names.update(n.split(" // "))
    multi = sorted((n for n in names if " " in n), key=len)
    tree = ast.parse(VIEWS.read_text())
    skip = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and id(n) not in skip:
            v = n.value
            if v in names or any(m in v for m in multi if len(m) <= len(v)):
                hits.append((n.lineno, v[:60]))
    assert not hits, hits


# ── E1: fixed damage ───────────────────────────────────────────────────

def test_a_fixed_burn_spells_view_is_its_printed_literal_amount():
    t = _template("Probe Bolt", ["instant"],
                  "Probe Bolt deals 3 damage to any target.")
    assert _derive("direct_damage_data", t) == {"amount": 3}


def test_an_instead_damage_upgrade_under_a_graveyard_card_type_condition_is_the_upgrade():
    t = _template("Probe Heat", ["instant"],
                  "Probe Heat deals 2 damage to any target.\n"
                  "Delirium — Probe Heat deals 6 damage instead if there "
                  "are four or more card types among cards in your graveyard.")
    assert _derive("direct_damage_data", t) == {
        "amount": 2, "upgrade_amount": 6, "upgrade_condition": "delirium"}


def test_a_burn_spell_with_a_resolution_rider_has_no_fixed_burn_view():
    t = _template("Probe Drain", ["sorcery"],
                  "Probe Drain deals 3 damage to any target. You gain 3 life.")
    assert _derive("direct_damage_data", t) is None


def test_a_flashback_line_does_not_refuse_a_fixed_burn_spell():
    t = _template("Probe Dart", ["instant"],
                  "Probe Dart deals 1 damage to any target.\n"
                  "Flashback—Sacrifice a Mountain.")
    assert _derive("direct_damage_data", t) == {"amount": 1}


def test_a_burn_line_after_another_printed_line_is_outside_the_legacy_prefix_window():
    """Legacy reads the burn sentence only as the first printed line; the
    strict damage shape holds the host anyway (the window is a quirk)."""
    v = _views()
    t = _template("Probe Flash Bolt", ["instant"],
                  "Flash\nProbe Flash Bolt deals 3 damage to any target.")
    assert _derive("direct_damage_data", t) is None
    spell = t.effects.spell(0)
    assert v.STRICT["damage"](spell)


def test_a_pain_lands_one_damage_mana_ability_is_its_tap_damage():
    t = _template("Probe Reef", ["land"],
                  "{T}: Add {C}.\n{T}: Add {U} or {R}. This land deals 1 "
                  "damage to you.")
    assert _derive("tap_damage", t) == 1
    plain = _template("Probe Basin", ["land"], "{T}: Add {C}.")
    assert _derive("tap_damage", plain) == 0


def test_an_energy_payment_for_that_much_damage_is_the_energy_damage_view():
    t = _template("Probe Discharge", ["instant"],
                  "Choose target creature or planeswalker. You get {E}{E}{E} "
                  "(three energy counters), then you may pay any amount of "
                  "{E}. Probe Discharge deals that much damage to that "
                  "permanent.")
    assert _derive("has_energy_damage_target", t) is True
    bolt = _template("Probe Bolt", ["instant"],
                     "Probe Bolt deals 3 damage to any target.")
    assert _derive("has_energy_damage_target", bolt) is False


def test_an_activated_fixed_damage_ability_is_typed_with_its_amount():
    t = _template("Probe Pinger", ["artifact"],
                  "{T}: Probe Pinger deals 1 damage to any target.")
    assert _derive("ActivatedAbility.effect_kind[DAMAGE_ANY_TARGET]", t,
                   key=0) == 1
    assert _derive("ActivatedAbility.effect_kind[DAMAGE_ANY_TARGET]", t,
                   key=7) is None


# ── E2: removal ────────────────────────────────────────────────────────

def test_targeted_removal_view_gives_action_types_and_mana_value_ceiling():
    t = _template("Probe Exile", ["instant"],
                  "Exile target creature with mana value X or less.")
    assert _derive("targeted_removal_data", t) == {
        "action": "exile", "types": ["creature"], "mv": "x"}
    t2 = _template("Probe Doom", ["instant"],
                   "Destroy target artifact or enchantment.")
    assert _derive("targeted_removal_data", t2) == {
        "action": "destroy", "types": ["artifact", "enchantment"], "mv": None}


def test_removal_with_a_resolution_rider_has_no_targeted_removal_view():
    t = _template("Probe Purge", ["instant"],
                  "Destroy target creature. You gain 3 life.")
    assert _derive("targeted_removal_data", t) is None


def test_a_removal_mode_is_typed_per_mode():
    t = _template("Probe Charm", ["instant"],
                  "Choose one —\n• Exile target creature with mana "
                  "value 3 or less.\n• Draw a card.")
    assert _derive("modes[removal]", t, key=0) == {
        "action": "exile", "types": ["creature"], "mv": 3}
    assert _derive("modes[removal]", t, key=1) is None


def test_symmetric_destroy_all_creatures_is_the_board_sweep_view_and_a_scoped_sweep_is_not():
    t = _template("Probe Wrath", ["sorcery"],
                  "Destroy all creatures. They can't be regenerated.")
    assert _derive("board_sweep_data", t) == {
        "action": "destroy", "types": ["creature"]}
    scoped = _template("Probe Scoped Wrath", ["sorcery"],
                       "Destroy all creatures your opponents control.")
    assert _derive("board_sweep_data", scoped) is None


def test_a_resolution_mana_value_bound_and_its_revolt_raise_are_the_condition_view():
    t = _template("Probe Push", ["instant"],
                  "Destroy target creature if it has mana value 2 or less.\n"
                  "Revolt — Destroy that creature if it has mana value 4 "
                  "or less instead if a permanent left the battlefield under "
                  "your control this turn.")
    assert _derive("removal_mv_condition", t) == {
        "mv": 2, "mv_if_permanent_left": 4}


def test_an_etb_removal_trigger_is_typed_with_its_owner_scope():
    t = _template("Probe Sage", ["creature"],
                  "When this creature enters, you may destroy target "
                  "artifact or enchantment.")
    assert _derive("etb_targeted_removal_data", t) == {
        "action": "destroy", "types": ["artifact", "enchantment"],
        "mv": None, "owner_scope": "any", "optional": True}


@pytest.mark.parametrize("text", [
    # an intervening-if (CR 603.4) the legacy shape never reads
    "When this creature enters, if {G}{G} was spent to cast it, exile "
    "target artifact or enchantment an opponent controls.",
    # a linked duration (CR 610.3) the legacy shape never reads
    "When this creature enters, exile target nonland permanent an "
    "opponent controls until this creature leaves the battlefield.",
])
def test_an_etb_removal_outside_the_legacy_domain_is_masked_to_none(text):
    """A39: the grammar types the growth, the field view masks it to the
    legacy value (None), and the record reports the mask."""
    v = _views()
    t = _template("Probe Binder", ["creature"], text)
    rec = v.DERIVATIONS["etb_targeted_removal_data"]
    assert rec.view(t.effects, None, t) is not None
    assert rec.derive(t.effects, template=t) is None
    assert rec.masked(t.effects, template=t)


def test_a_return_to_hand_spells_bounce_view_is_its_battlefield_requirement():
    t = _template("Probe Unsummon", ["instant"],
                  "Return target creature to its owner's hand.")
    req = _derive("bounce_target", t)
    assert req is not None and req.zone == "battlefield"
    assert req.types == frozenset({"creature"})


def test_destroy_target_land_is_the_land_destruction_view():
    t = _template("Probe Stone Rain", ["sorcery"], "Destroy target land.")
    data = _derive("land_destruction_data", t)
    assert data is not None and data["can_target_artifact"] is False
    assert _derive("destroys_target_land", t) is True


# ── E3: card flow ──────────────────────────────────────────────────────

def test_draw_then_discard_is_the_loot_view():
    t = _template("Probe Looting", ["sorcery"],
                  "Draw two cards, then discard two cards.")
    assert _derive("loot_data", t) == {
        "draw": 2, "discard": 2, "random": False, "each_player": False}


def test_sacrificing_any_number_of_lands_to_search_that_many_is_the_land_sacrifice_tutor():
    t = _template("Probe Shift", ["sorcery"],
                  "Sacrifice any number of lands. Search your library for up "
                  "to that many land cards, put them onto the battlefield "
                  "tapped, then shuffle.")
    assert _derive("is_land_sacrifice_tutor", t) is True


def test_a_self_sacrificing_basic_type_land_search_is_the_fetchland_view():
    t = _template("Probe Mesa", ["land"],
                  "{T}, Pay 1 life, Sacrifice this land: Search your library "
                  "for a Mountain or Plains card, put it onto the "
                  "battlefield, then shuffle.")
    fetch = _derive("fetchland", t)
    assert fetch is not None
    assert (fetch.colors, fetch.life_cost, fetch.count) == (("W", "R"), 1, 1)


# ── E5: pump ───────────────────────────────────────────────────────────

def test_a_target_creature_pump_until_end_of_turn_is_the_pump_spell_view():
    t = _template("Probe Growth", ["instant"],
                  "Target creature gets +3/+3 until end of turn.")
    assert (_derive("pump_spell_power", t),
            _derive("pump_spell_toughness", t)) == (3, 3)


def test_an_equipment_static_pump_is_the_equip_grant_view():
    t = _template("Probe Splitter", ["artifact"],
                  "Equipped creature gets +2/+0.\nEquip {1}",
                  subtypes=["Equipment"])
    assert (_derive("equip_power_grant", t),
            _derive("equip_toughness_grant", t)) == (2, 0)


# ── E6: stack and mana ─────────────────────────────────────────────────

def test_a_soft_counter_view_reads_the_unless_tax_and_the_target_kind():
    t = _template("Probe Pierce", ["instant"],
                  "Counter target noncreature spell unless its controller "
                  "pays {2}.")
    assert _derive("is_counterspell", t) is True
    assert _derive("counter_target_kind", t) == "noncreature_spell"
    assert _derive("counter_tax_amount", t) == 2


def test_a_hard_counter_upgrade_under_a_creature_power_condition_is_typed():
    t = _template("Probe Denial", ["instant"],
                  "Counter target noncreature spell unless its controller "
                  "pays {1}.\nFerocious — If you control a creature with "
                  "power 4 or greater, counter that spell instead.")
    assert _derive("counter_upgrade_condition", t) == {
        "creature_power_at_least": 4}


def test_two_or_more_same_colour_pips_added_by_a_spell_are_the_ritual_view():
    t = _template("Probe Ritual", ["instant"], "Add {R}{R}{R}.")
    assert _derive("ritual_mana", t) == ("R", 3)
    one = _template("Probe Spark", ["instant"], "Add {R}.")
    assert _derive("ritual_mana", one) is None


# ── A40: printed spans ─────────────────────────────────────────────────

def test_a_bundle_choice_of_two_mana_is_one_unit_per_produced_mana():
    """CR 106.1 / 605: "Add {W}{W}, {W}{U}, or {U}{U}" produces two mana,
    each white or blue; every choice is a pick per mana, so the units are
    one per produced mana, each a colour."""
    t = _template("Probe Filter", ["land"],
                  "{T}: Add {C}.\n{W/U}, {T}: Add {W}{W}, {W}{U}, or {U}{U}.")
    assert _derive("mana_units", t) == [["C"], ["U", "W"], ["U", "W"]]


def test_a_bundle_choice_that_is_not_a_pick_per_mana_has_no_units():
    """A choice between bundles whose per-mana picks would add a bundle
    the card does not print ({R}{R} or {G}{G}: never {R}{G}) is not a list
    of independent units, so the view refuses it (the legacy default)."""
    t = _template("Probe Bundle", ["land"],
                  "{T}: Add {R}{R} or {G}{G}.")
    assert _derive("mana_units", t) == []


def test_an_instead_colorless_upgrade_under_a_control_condition_is_its_bonus_over_the_base():
    """CR 614.1a: "add {C}{C}{C} instead" replaces the base {C}; the
    conditional mana is the extra mana the upgrade produces (legacy's
    `bonus`), compared on that key alone."""
    rec = _views().DERIVATIONS["conditional_mana"]
    assert rec.compare == ("bonus",)
    t = _template("Probe Cave Mine", ["land"],
                  "{T}: Add {C}. If you control a Cave, add {C}{C}{C} "
                  "instead.")
    assert _derive("conditional_mana", t) == {"bonus": 2}


def test_an_instead_upgrade_to_one_mana_of_any_colour_is_no_conditional_bonus():
    """The upgrade changes the colour, not the amount: no extra mana."""
    t = _template("Probe Luck Cavern", ["land"],
                  "{T}: Add {C}. If this land has a luck counter on it, "
                  "instead add one mana of any color.")
    assert _derive("conditional_mana", t) is None


def test_kicked_clause_is_the_printed_span_from_the_kicked_frame_to_the_sentence_end():
    """Printed case is kept (the self-name, not `~`), reminder text is
    stripped, and the replacing word 'instead' is part of the span."""
    v = _views()
    t = _template("Probe Kicker", ["sorcery"],
                  "Kicker {2}{R} (You may pay an additional {2}{R} as you "
                  "cast this spell.)\nProbe Kicker deals 2 damage to any "
                  "target. If this spell was kicked, Probe Kicker deals 5 "
                  "damage to any target instead.")
    assert v.kicked_clause(t) == \
        "Probe Kicker deals 5 damage to any target instead"
    assert _derive("kicked_clause", t) == v.kicked_clause(t)


def test_a_kicked_cast_trigger_body_is_the_kicked_clause():
    v = _views()
    t = _template("Probe Spawner", ["sorcery"],
                  "Kicker {1}{C}\nWhen you cast this spell, if it was "
                  "kicked, exile target land.")
    assert v.kicked_clause(t) == "exile target land"


def test_an_unkicked_card_has_no_kicked_clause():
    t = _template("Probe Bolt", ["instant"],
                  "Probe Bolt deals 3 damage to any target.")
    assert _views().kicked_clause(t) is None


def test_channel_clause_is_the_printed_channel_host_text():
    v = _views()
    t = _template("Probe Spire", ["land"],
                  "{T}: Add {U}.\nChannel — {3}{U}, Discard this card: "
                  "Return target creature to its owner's hand.",
                  supertypes=["legendary"])
    assert v.channel_clause(t) == (
        "channel — {3}{u}, discard this card: return target creature "
        "to its owner's hand.")
    plain = _template("Probe Basin", ["land"], "{T}: Add {C}.")
    assert v.channel_clause(plain) == ""


# ── A41: host_for_override ─────────────────────────────────────────────

def test_host_for_override_finds_a_mode_by_its_printed_clause():
    from engine.effect_spec import HostKind
    v = _views()
    t = _template("Probe Charm", ["instant"],
                  "Choose one —\n• Exile target creature with mana "
                  "value 3 or less.\n• Draw a card.")
    h = v.host_for_override(t, "Draw a card")
    assert h is not None and h.kind is HostKind.MODE and h.mode_index == 1
    h0 = v.host_for_override(t, "exile target creature with mana value 3 "
                                "or less.")
    assert h0 is not None and h0.mode_index == 0


def test_host_for_override_finds_the_kicked_and_the_cast_trigger_hosts():
    from engine.effect_spec import HostKind
    v = _views()
    kicked = _template("Probe Kicker", ["sorcery"],
                       "Kicker {2}\nDraw a card. If this spell was kicked, "
                       "draw two cards instead.")
    h = v.host_for_override(kicked, v.kicked_clause(kicked))
    assert h is not None and h.kind is HostKind.SPELL
    titan = _template("Probe Titan", ["creature"],
                      "When you cast this spell, exile two target permanents.")
    h2 = v.host_for_override(titan, "exile two target permanents.")
    assert h2 is not None and h2.kind is HostKind.TRIGGERED


def test_host_for_override_finds_the_channel_host():
    from engine.effect_spec import HostKind
    v = _views()
    t = _template("Probe Spire", ["land"],
                  "{T}: Add {U}.\nChannel — {3}{U}, Discard this card: "
                  "Return target creature to its owner's hand.",
                  supertypes=["legendary"])
    h = v.host_for_override(t, v.channel_clause(t))
    assert h is not None and h.kind is HostKind.ACTIVATED
    assert h.from_zone == "hand"


def test_an_override_body_two_triggers_of_one_card_share_names_no_host_unless_the_event_names_one():
    """A41: a body printed by two triggers is ambiguous, so the bare
    lookup names no host (the handler stays on legacy); the trigger event
    the handler resolves for narrows it to the one host."""
    from engine.effect_spec import EventHint
    v = _views()
    t = _template("Probe Twin Draw", ["creature"],
                  "When this creature enters, draw a card.\nWhenever "
                  "another creature you control enters, draw a card.")
    assert v.host_for_override(t, "draw a card.") is None
    own = v.host_for_override(t, "draw a card.", event=EventHint.SELF_ENTERS)
    other = v.host_for_override(t, "draw a card.",
                                event=EventHint.OTHER_ENTERS)
    assert own is not None and own.index == 0
    assert other is not None and other.index == 1


def test_a_trigger_body_names_the_host_that_prints_it_not_one_whose_spec_starts_with_it():
    """A41: a body names the trigger whose head-stripped body it is, not
    a trigger whose body merely ends with it after a multiplier or an
    intervening-if."""
    v = _views()
    each = _template("Probe Shrine", ["enchantment"],
                     "When this enchantment enters, for each artifact you "
                     "control, create a 1/1 colorless Servo artifact "
                     "creature token.\nWhenever another artifact you control "
                     "enters, create a 1/1 colorless Servo artifact creature "
                     "token.")
    h = v.host_for_override(each, "create a 1/1 colorless servo artifact "
                                  "creature token.")
    assert h is not None and h.index == 1
    gated = _template("Probe Uprising", ["enchantment"],
                      "When this enchantment enters, if you control a "
                      "creature with power 4 or greater, draw a card.\n"
                      "Whenever a creature you control with power 4 or "
                      "greater enters, draw a card.")
    g = v.host_for_override(gated, "draw a card.")
    assert g is not None and g.index == 1


def test_host_for_override_is_a_lookup_and_never_parses(monkeypatch):
    """Section 11: the override table is a lookup, never a parse -- once
    the template's effects exist, a lookup (hit or miss) runs no grammar
    layer."""
    from engine.effect_grammar import link
    v = _views()
    t = _template("Probe Charm", ["instant"],
                  "Choose one —\n• Exile target creature with mana "
                  "value 3 or less.\n• Draw a card.")
    t.effects                                        # the one parse
    calls = []
    real = link.parse_face_hosts
    monkeypatch.setattr(link, "parse_face_hosts",
                        lambda *a, **k: calls.append(a) or real(*a, **k))
    assert v.host_for_override(t, "Draw a card") is not None
    assert v.host_for_override(t, "a clause the card does not print") is None
    assert calls == []


# ── totality and purity ────────────────────────────────────────────────

def test_every_derivation_is_total_over_the_empty_card():
    from engine.effect_spec import EMPTY_EFFECTS
    v = _views()
    for name, rec in v.DERIVATIONS.items():
        got = rec.derive(EMPTY_EFFECTS, key=0 if rec.scope != v.SCOPE_CARD
                         else None)
        assert got == rec.default, (name, got)


def test_a_derivation_never_mutates_the_specs_it_reads():
    from engine.effect_spec import canonical
    v = _views()
    t = _template("Probe Denial", ["instant"],
                  "Counter target noncreature spell unless its controller "
                  "pays {1}.\nFerocious — If you control a creature with "
                  "power 4 or greater, counter that spell instead.")
    before = canonical(t.effects)
    first = {n: r.derive(t.effects, key=0, template=t)
             for n, r in v.DERIVATIONS.items()}
    second = {n: r.derive(t.effects, key=0, template=t)
              for n, r in v.DERIVATIONS.items()}
    assert canonical(t.effects) == before
    assert first == second


# ── the registered decks (shared DB, read only) ────────────────────────

# Every registered-deck card plus every 97th pool template (~590). Measured
# 2026-10-02 on this container (quiet, 4 cores): ~2 s for the body (the
# lazy parses plus every derivation) plus ~18 s when it is the first test
# of the process to load the shared card DB; 120 s bounds a hang with room
# for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_every_derivation_runs_on_every_registered_deck_card_and_a_pool_sample(card_db):
    v = _views()
    pool = sorted({id(t): t for t in card_db.cards.values()}.values(),
                  key=lambda t: t.name)
    sample = {t.name: t for t in pool[::97]}
    sample.update({t.name: t for t in _deck_templates(card_db)})
    failures = []
    for t in sample.values():
        for name, rec in v.DERIVATIONS.items():
            for key in rec.keys(t):
                try:
                    rec.derive(t.effects, key=key, template=t)
                    rec.legacy(t, key)
                except Exception as e:          # pragma: no cover - report
                    failures.append((t.name, name, key, repr(e)[:80]))
    assert not failures, failures[:10]


_MANA_COLOURS = frozenset("WUBRGC")


@pytest.mark.timeout(120)
def test_every_mana_unit_a_view_yields_is_a_single_colour_or_colorless(card_db):
    """A mana unit is the colours ONE produced mana can be (CR 106.1b):
    every entry is one of W U B R G C, over the registered decks and a
    pool sample, for every view that yields units."""
    v = _views()
    pool = sorted({id(t): t for t in card_db.cards.values()}.values(),
                  key=lambda t: t.name)
    sample = {t.name: t for t in pool[::97]}
    sample.update({t.name: t for t in _deck_templates(card_db)})
    bad = []
    for t in sample.values():
        for field in ("mana_units", "sacrifice_mana_units",
                      "aura_mana_units", "tap_for_mana_trigger"):
            got = _derive(field, t)
            units = (got or {}).get("units", []) if isinstance(got, dict) \
                else got
            for unit in units or ():
                if not unit or not set(unit) <= _MANA_COLOURS:
                    bad.append((t.name, field, unit))
    assert not bad, bad[:20]


def test_kicked_clause_view_equals_the_legacy_field_on_every_registered_deck_card(card_db):
    v = _views()
    diffs = [(t.name, t.kicked_clause, v.kicked_clause(t))
             for t in _deck_templates(card_db)
             if t.kicked_clause != v.kicked_clause(t)]
    assert not diffs, diffs


def test_channel_clause_view_equals_the_legacy_field_on_every_registered_deck_card(card_db):
    v = _views()
    diffs = [(t.name, t.channel_clause, v.channel_clause(t))
             for t in _deck_templates(card_db)
             if t.channel_clause != v.channel_clause(t)]
    assert not diffs, diffs


def test_host_for_override_resolves_every_registered_deck_override(card_db):
    """Every override text the legacy handlers pass for a registered-deck
    card -- a mode clause, the kicked clause, the channel clause -- names
    a host of that card."""
    from engine.effect_spec import HostKind
    v = _views()
    missing = []
    for t in _deck_templates(card_db):
        for i, m in enumerate(t.modes or ()):
            h = v.host_for_override(t, m.get("text", ""))
            if h is None or h.kind is not HostKind.MODE or h.mode_index != i:
                missing.append((t.name, "mode", i))
        if t.kicked_clause and v.host_for_override(t, t.kicked_clause) is None:
            missing.append((t.name, "kicked", t.kicked_clause))
        if t.channel_clause and v.host_for_override(
                t, t.channel_clause) is None:
            missing.append((t.name, "channel", t.channel_clause))
    assert not missing, missing


def _body_start(h):
    raw = h.trigger.raw if h.trigger is not None else ""
    if not raw or not h.text.startswith(raw):
        return None
    rest = h.text[len(raw):]
    return len(raw) + (len(rest) - len(rest.lstrip(" ,")))


@pytest.mark.timeout(120)
def test_every_triggered_hosts_printed_body_names_that_host_or_an_ambiguity(card_db):
    """A41 over the registered decks and a pool sample: the printed
    head-stripped body of every TRIGGERED host names that host; when
    another trigger of the card prints the same body the bare lookup names
    none, and the trigger's own event names it unless a second trigger of
    that event prints it too. Never a different host."""
    from engine.effect_spec import HostKind
    v = _views()
    pool = sorted({id(t): t for t in card_db.cards.values()}.values(),
                  key=lambda t: t.name)
    sample = {t.name: t for t in pool[::97]}
    sample.update({t.name: t for t in _deck_templates(card_db)})
    wrong = []
    for t in sample.values():
        trig = [h for hosts in t.effects.faces for h in hosts
                if h.kind is HostKind.TRIGGERED]
        for face, hosts in enumerate(t.effects.faces):
            for h in (x for x in hosts if x.kind is HostKind.TRIGGERED):
                start = _body_start(h)
                if start is None:
                    continue
                body = v._printed(t, face, h, (start, len(h.text)))
                key = v._host_key(h.text[start:])
                twins = [o for o in trig if o is not h and
                         _body_start(o) is not None and
                         v._host_key(o.text[_body_start(o):]) == key]
                got = v.host_for_override(t, body)
                if got is not h and not (got is None and twins):
                    wrong.append((t.name, face, h.index, "bare",
                                  None if got is None else got.index))
                hint = h.trigger.event_hints[0] if h.trigger.event_hints \
                    else None
                if hint is None:
                    continue
                same = [o for o in twins if hint in o.trigger.event_hints]
                got = v.host_for_override(t, body, event=hint)
                if got is not h and not (got is None and same):
                    wrong.append((t.name, face, h.index, hint.value,
                                  None if got is None else got.index))
    assert not wrong, wrong[:20]


# Registered-deck cards where a non-partial Tier A view differs from the
# legacy field today, each with the side that is wrong, pinned as
# {field: {"card[ key]": digest of the (view, legacy) pair}}. A new
# difference fails (a derivation regressed or a legacy parser changed); a
# pinned one whose values change fails; a listed one that disappears fails
# too, so the list only shrinks (the tool's equivalence baseline takes over
# the pool-wide count).
KNOWN_DECK_DISAGREEMENTS = {
    # legacy reads a permanent's activated burn line as a burn spell
    "direct_damage_data": {"Goblin Bombardment": "caad058ce7a2"},
    # target.zone_union: legacy keeps one union member (design section 10)
    "bounce_target": {
        "Sink into Stupor // Soporific Springs": "a93d9e3d4fb5"},
    # legacy refuses any text naming "this creature"
    "mass_graveyard_return": {"Tyvar, Jubilant Brawler": "5d5be987d596"},
    # the grammar refuses "target cards from graveyards" (no requirement)
    "ActivatedAbility.effect_kind[EXILE_FROM_GRAVEYARD]": {
        "Faerie Macabre 0": "d40be18e9798"},
    # legacy reads the source only as "this creature / permanent / it"
    "ActivatedAbility.effect_kind[PUT_COUNTER_SELF]": {
        "The Filigree Sylex 0": "215f31289668"},
    # the grammar refuses "your choice of"; legacy also reads the
    # delirium upgrade's keyword as the base grant's
    "pump_spell_keyword": {"Practiced Offense": "609c4e0ce535"},
    "pump_spell_keywords": {"Practiced Offense": "daf7ac434763",
                            "Violent Urge": "14fd6d1689d2"},
    # legacy refuses "another target permanent"
    "ActivatedAbility.effect_kind[UNTAP_TARGET_PERMANENT]": {
        "Formidable Speaker 0": "5d5be987d596"},
}


# Registered-deck cards where a PARTIAL Tier A record differs from legacy
# on its compare projection (the `compare` keys, or the whole value for
# "eq"), pinned the same way. A partial record never switches; the pins
# make a regressed view, a wrong compare key or a changed legacy value
# fail instead of passing silently.
KNOWN_DECK_PARTIAL_DISAGREEMENTS = {
    # the same two cards as the non-partial effect_kind pins above
    "ActivatedAbility.graveyard_exile_data": {
        "Faerie Macabre 0": "df12ee68376b"},
    "ActivatedAbility.put_counter_data": {
        "The Filigree Sylex 0": "34b2693457ca"},
    # the grammar leaves the tron condition "an X and a Y" UNMODELLED
    # (filter.np_union), so the view has no bonus yet
    "conditional_mana": {"Urza's Mine": "7524a645a815",
                         "Urza's Power Plant": "7524a645a815",
                         "Urza's Tower": "5fe0554e9a20"},
    # legacy types the reduction, the view gives None today
    "cost_reduction_rule": {
        "Artist's Talent": "fd6e03546e22",
        "Ral, Monsoon Mage // Ral, Leyline Prodigy": "8c55025aadd3",
        "Ruby Medallion": "5d759227e775"},
    # the view types the dig (rest to the bottom), legacy gives None
    "library_dig_data": {"Narset, Parter of Veils": "033ff35d528c",
                         "Stock Up": "033ff35d528c"},
    # the view concatenates the units of every {T} mana ability and reads
    # any permanent; legacy reads lands only, merges a land's alternative
    # abilities into one unit, drops pain/spend-restricted/paid lines and
    # widens all-{C} lines (Eldrazi Temple) by its own rule
    "mana_units": {
        "Abstergo Entertainment": "256d7928363e",
        "Arena of Glory": "e6679def187e",
        "Delighted Halfling": "8c5551b0226c",
        "Eldrazi Temple": "b873a86e04aa",
        "Fiery Islet": "447f3979491a",
        "Gemstone Caverns": "256d7928363e",
        "Gloomlake Verge": "a37ae88530a6",
        "Horizon Canopy": "5b18b512c073",
        "Mox Opal": "cee6696b2a21",
        "Mystic Gate": "986617a3b3f6",
        "Nurturing Peatland": "2b94e64175d7",
        "Shang-Chi, Master of Kung Fu": "5411ce2eca62",
        "Shivan Reef": "529afc19c3bc",
        "Silent Clearing": "3899ea0efb30",
        "Spire of Industry": "256d7928363e",
        "Springleaf Drum": "cee6696b2a21",
        "Sunbaked Canyon": "99457c741d3d",
        "Sunken Citadel": "60dc1b264ca9",
        "Talisman of Resilience": "97f6b5daf12d",
        "The Mycosynth Gardens": "256d7928363e"},
    # legacy types the X pump and trample grant, the view gives None
    "team_pump_data": {"Craterhoof Behemoth": "dcb6f5e46316"},
}


def _views_equal(a, b):
    def norm(v):
        if isinstance(v, (list, tuple)):
            return tuple(norm(x) for x in v)
        if isinstance(v, (set, frozenset)):
            return tuple(sorted(norm(x) for x in v))
        if isinstance(v, dict):
            return tuple(sorted((k, norm(x)) for k, x in v.items()))
        return v
    return norm(a) == norm(b)


def _stable(v):
    """A hash-seed-independent form of a view or legacy value for the
    digest: sets sorted, dicts by key, dataclasses by field."""
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return (type(v).__name__,) + tuple(
            (f.name, _stable(getattr(v, f.name)))
            for f in dataclasses.fields(v))
    if isinstance(v, dict):
        return ("dict",) + tuple(sorted(((repr(_stable(k)), _stable(x))
                                         for k, x in v.items()), key=repr))
    if isinstance(v, (set, frozenset)):
        return ("set",) + tuple(sorted((_stable(x) for x in v), key=repr))
    if isinstance(v, (list, tuple)):
        return tuple(_stable(x) for x in v)
    return v


def _digest(view, legacy):
    import hashlib
    return hashlib.sha256(repr((_stable(view), _stable(legacy)))
                          .encode()).hexdigest()[:12]


def _projection(rec, value):
    """A partial record's compare projection: its `compare` keys of a
    dict (or attributes of an object), or the whole value for "eq"."""
    if rec.compare == _views().COMPARE_EQ or value is None:
        return value
    if isinstance(value, dict):
        return {k: value.get(k) for k in rec.compare}
    return {k: getattr(value, k, None) for k in rec.compare}


def _deck_disagreements(card_db, partial):
    v = _views()
    found = {}
    for t in _deck_templates(card_db):
        for name, rec in v.DERIVATIONS.items():
            if rec.tier != "A" or rec.partial != partial:
                continue
            for key in rec.keys(t):
                got = rec.derive(t.effects, key=key, template=t)
                legacy = rec.legacy(t, key)
                if partial:
                    got, legacy = (_projection(rec, got),
                                   _projection(rec, legacy))
                if not _views_equal(got, legacy):
                    label = t.name if key is None else f"{t.name} {key}"
                    found.setdefault(name, {})[label] = _digest(got, legacy)
    return found


def test_tier_a_views_equal_legacy_on_registered_deck_cards_but_the_named_ones(card_db):
    assert _deck_disagreements(card_db, False) == KNOWN_DECK_DISAGREEMENTS


def test_partial_tier_a_views_equal_legacy_on_their_compare_keys_but_the_named_ones(card_db):
    """Every partial Tier A record's compare keys are keys of the legacy
    value, and its projection equals legacy on every registered-deck card
    but the pinned ones."""
    v = _views()
    bad_keys = set()
    for t in _deck_templates(card_db):
        for name, rec in v.DERIVATIONS.items():
            if rec.tier != "A" or not rec.partial or \
                    rec.compare == v.COMPARE_EQ:
                continue
            for key in rec.keys(t):
                legacy = rec.legacy(t, key)
                if isinstance(legacy, dict):
                    bad_keys |= {(name, k) for k in rec.compare
                                 if k not in legacy}
    assert not bad_keys, bad_keys
    assert _deck_disagreements(card_db, True) == \
        KNOWN_DECK_PARTIAL_DISAGREEMENTS


def test_the_etb_removal_mask_hides_exactly_the_intervening_if_and_linked_duration_witnesses(card_db):
    """A39's witnesses: the grammar types both ETB removals, the field
    view masks them to legacy None."""
    v = _views()
    rec = v.DERIVATIONS["etb_targeted_removal_data"]
    masked = {t.name for t in _deck_templates(card_db)
              if rec.masked(t.effects, template=t)}
    assert masked == {"Wistfulness", "Leyline Binding"}


def test_a_self_cast_trigger_body_and_its_kicked_payoff_index_their_triggered_hosts(card_db):
    """A41 / A40 witnesses: the head-stripped cast-trigger body and the
    kicked payoff of a SELF_CAST trigger name TRIGGERED(SELF_CAST) hosts."""
    from engine.effect_spec import EventHint, HostKind
    v = _views()
    hunger = card_db.cards["Ulamog, the Ceaseless Hunger"]
    h = v.host_for_override(hunger, "exile two target permanents.")
    assert h is not None and h.kind is HostKind.TRIGGERED
    assert h.trigger.event_hints == (EventHint.SELF_CAST,)
    spawn = card_db.cards["Sowing Mycospawn"]
    assert v.kicked_clause(spawn) == spawn.kicked_clause == "exile target land"
    k = v.host_for_override(spawn, spawn.kicked_clause)
    assert k is not None and k.kind is HostKind.TRIGGERED
    assert k.trigger.intervening_if is not None
