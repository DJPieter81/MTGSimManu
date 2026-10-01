"""L1 of the clause grammar: paragraph structure (design doc 2026-09-29,
section 3 "L1, structure" and "F1 merge"; A1-A8, A11, A12, M3, M7; E0 step 9).

Each test names the rule it pins. The texts are printed oracle wording; a
card name appears only to build the face facts (the full name is a
self-form, CR 201.4b), never in engine code.
"""
from __future__ import annotations

import pytest

from engine.effect_grammar import normalize as N
from engine.effect_grammar import structure as S
from engine.effect_grammar.keywords import keywords702
from engine.effect_spec import AmountKind, ConditionKind, EventHint, HostKind


def _facts(name="", *, types=("instant",), keywords=(), legendary=False,
           subtypes=()):
    tc = frozenset(types)
    character = "planeswalker" in tc or (legendary and "creature" in tc)
    return N.Facts(
        names=N.self_names(name, is_legendary=legendary,
                           is_character=character, subtypes=subtypes)
        if name else (),
        type_class=tc,
        is_spell=bool({"instant", "sorcery"} & tc),
        is_legendary=legendary,
        is_planeswalker="planeswalker" in tc,
        keywords702=keywords702(keywords))


def _hosts(text, *a, face=0, **kw):
    return S.parse_face_structure(text, _facts(*a, **kw), face=face).hosts


def _body(h):
    return " | ".join(h.text[a:b] for a, b in h.body)


def _kinds(hosts):
    return [h.kind for h in hosts]


# ── Reminder text, self-forms, quotes (L0 seen through L1) ──────────────

def test_reminder_text_never_yields_an_effect_spec():
    """A colon inside keyword reminder text is not an activated ability:
    the reminder is removed before classification (strip_reminder_text)."""
    hosts = _hosts("Equip {3} ({3}: Attach to target creature you control. "
                   "Equip only as a sorcery.)", types=("artifact",),
                   keywords=("Equip",))
    assert _kinds(hosts) == [HostKind.KEYWORD]
    assert hosts[0].body == () and hosts[0].activation_index is None
    hosts = _hosts("Cycling {2} ({2}, Discard this card: Draw a card.)",
                   "Some Card", types=("creature",), keywords=("Cycling",))
    assert _kinds(hosts) == [HostKind.KEYWORD]


def test_every_self_reference_form_normalises_to_the_source():
    """Full name, face name, legendary short name and "this <noun>" are
    all the source (~); an invented name is never one."""
    h = _hosts("Zorblax Vance deals 2 damage to any target. Vance gains "
               "flying. This creature gets +1/+1.", "Zorblax Vance, the Odd",
               types=("creature",), legendary=True)
    assert h[0].text == ("~ deals 2 damage to any target. vance gains "
                         "flying. ~ gets +1/+1.")
    h = _hosts("When Zorblax, the Odd enters, Zorblax deals 1 damage to "
               "each opponent. This creature can't block.",
               "Zorblax, the Odd", types=("creature",), legendary=True)
    assert h[0].trigger.raw == "when ~ enters"
    assert _body(h[0]).startswith("~ deals 1 damage")
    assert "~ can't block" in h[0].text


def test_self_pronouns_normalise_by_grammatical_case():
    """A9 on a walker face: object him -> ~, possessive his -> ~'s."""
    h = _hosts("[+1]: Return him to the battlefield under his owner's "
               "control.", "Quillo, Wandering Sage",
               types=("planeswalker",), legendary=True)
    assert h[0].text == "[+1]: return ~ to the battlefield under ~'s owner's control."


def test_a_name_inside_a_named_card_phrase_is_data_not_a_self_reference():
    h = _hosts("Search your library for a card named Gloomwick Lantern.",
               "Gloomwick Lantern", types=("sorcery",))
    assert "~" not in h[0].text and "⟨n0⟩" in h[0].text


def test_a_self_name_that_reads_as_a_rules_word_is_not_rewritten_there():
    """A one-word name that is an imperative verb at clause start is that
    verb there (it takes an object noun phrase)."""
    h = _hosts("Exile target creature. Exile deals 2 damage to you.",
               "Exile", types=("instant",))
    assert h[0].text.startswith("exile target creature.")
    assert "~ deals 2 damage" in h[0].text


def test_nested_single_quoted_abilities_are_masked_without_splitting_on_apostrophes():
    h = _hosts('Creatures you control have "{T}: Create a token with '
               "'This token's power is 1.'\"", types=("enchantment",))
    assert _kinds(h) == [HostKind.STATIC]
    assert h[0].text == "creatures you control have ⟨q0⟩"


def test_sentence_split_ignores_periods_inside_quoted_abilities():
    """A colon or period inside a quoted ability is the granted ability's:
    the carrier paragraph is not an activated ability of its own."""
    h = _hosts('Equipped creature has "{T}: Add {C}. Activate only once '
               'each turn."', types=("artifact",))
    assert _kinds(h) == [HostKind.STATIC]
    assert h[0].activation_index is None


def test_a_quoted_ability_is_granted_to_its_recipient_never_an_effect_of_its_carrier():
    """CR 113.1a: the quoted ability is masked as data; its colon, its
    'add' and its target never make the carrier ACTIVATED or a mana
    ability, and the carrier takes no activation ordinal."""
    h = _hosts('{1}: Target creature gains "{T}: Add {G}." until end of turn.\n'
               "{T}: Add {G}.", types=("creature",))
    assert _kinds(h) == [HostKind.ACTIVATED, HostKind.MANA_ABILITY]
    assert h[0].activation_index is None       # legacy skips quote lines
    assert h[1].activation_index == 0


# ── Hosts by paragraph (CR 113.2, 113.3a, F1) ──────────────────────────

def test_each_paragraph_of_a_permanent_is_one_ability_host_of_its_printed_kind():
    """CR 113.2: one paragraph, one ability, of the kind its text prints."""
    h = _hosts("Flying\n"
               "When this creature enters, draw a card.\n"
               "{2}{U}: This creature gets +1/+0 until end of turn.\n"
               "Creatures you control get +0/+1.",
               "Gale Sprite", types=("creature",), keywords=("Flying",))
    assert _kinds(h) == [HostKind.KEYWORD, HostKind.TRIGGERED,
                         HostKind.ACTIVATED, HostKind.STATIC]
    assert [x.paragraphs for x in h] == [(0,), (1,), (2,), (3,)]
    assert [x.index for x in h] == [0, 1, 2, 3]


def test_all_spell_ability_paragraphs_of_an_instant_or_sorcery_face_form_one_spell_host():
    """CR 113.3a, 608.2c, F1: the spell's instructions are one ability in
    printed order, so a later paragraph's 'instead' sees the earlier one;
    a label-prefixed paragraph merges too."""
    h = _hosts("Destroy target creature if it has mana value 2 or less.\n"
               "Revolt — Destroy that creature if it has mana value 4 or less "
               "instead if a permanent left the battlefield under your "
               "control this turn.", keywords=("Revolt",))
    assert _kinds(h) == [HostKind.SPELL]
    spell = h[0]
    assert spell.paragraphs == (0, 1)
    assert [p.label for p in spell.parts] == ["", "revolt"]
    assert spell.text.split("\n")[1].startswith("revolt - destroy that")
    assert _body(spell).startswith("destroy target creature")
    assert "destroy that creature" in _body(spell)
    assert "revolt" not in _body(spell)


def test_a_keyword_ability_line_is_a_keyword_host_never_resolution_text():
    """CR 702 / A1: em-dash non-mana costs with verbs, multi-word and
    noun-parameter keywords are keyword lines; a keyword line never merges
    into the SPELL host and its costs are parsed from the printed span."""
    h = _hosts("Lava Dart deals 1 damage to any target.\n"
               "Flashback—Sacrifice a Mountain. (You may cast this card from "
               "your graveyard for its flashback cost. Then exile it.)",
               "Lava Dart", keywords=("Flashback",))
    assert _kinds(h) == [HostKind.SPELL, HostKind.KEYWORD]
    kw = h[1].keywords[0]
    assert kw.name == "flashback" and kw.cost == "sacrifice a mountain"
    assert kw.cost_snapshot is not None
    assert h[0].paragraphs == (0,)
    h = _hosts("Splice onto Arcane {1}{R} (reminder.)\nDraw a card.",
               keywords=("Splice",))
    assert h[0].kind is HostKind.KEYWORD and h[0].keywords[0].name == "splice"
    h = _hosts("Gift a tapped Fish (reminder.)\nDraw a card.",
               keywords=("Gift",))
    assert h[0].kind is HostKind.KEYWORD and h[0].keywords[0].param == "a tapped fish"
    h = _hosts("Escape—{3}{B}, Exile five other cards from your graveyard.",
               "Grave Hound", types=("creature",), keywords=("Escape",))
    assert h[0].kind is HostKind.KEYWORD
    assert h[0].keywords[0].cost.startswith("{3}{b}, exile five other cards")


def test_keyword_actions_in_the_face_keyword_list_do_not_make_an_effect_paragraph_a_keyword_line():
    """CR 701 vs 702, M3: MTGJSON lists Scry / Mill / Investigate; they are
    effects, never keyword hosts."""
    h = _hosts("Scry 2.\nDraw a card.", keywords=("Scry",))
    assert _kinds(h) == [HostKind.SPELL]
    assert _body(h[0]) == "scry 2. | draw a card."
    h = _hosts("Investigate.", types=("sorcery",), keywords=("Investigate",))
    assert _kinds(h) == [HostKind.SPELL]


def test_an_alternative_cost_is_its_own_host_and_never_resolves():
    """CR 118.9, A2: the alternative cost carries its cost and condition,
    holds no effect text, and is lifted out of the SPELL host."""
    h = _hosts("If it's not your turn, you may exile a blue card from your "
               "hand rather than pay this spell's mana cost.\n"
               "Counter target noncreature spell.")
    assert _kinds(h) == [HostKind.ALTERNATIVE_COST, HostKind.SPELL]
    alt = h[0]
    assert alt.body == ()
    assert alt.cost_condition.kind is ConditionKind.TURN
    assert alt.cost_condition.pred == "not_your_turn"
    assert alt.cost is not None
    assert h[1].paragraphs == (1,)


def test_this_spell_statics_are_lifted_on_every_face_type():
    """CR 601.2f, A3: "This spell costs ..." / "can't be countered" are
    STATIC(from_zone='stack') hosts on permanent and spell faces alike."""
    for types in (("creature",), ("sorcery",)):
        h = _hosts("This spell costs {1} less to cast for each card type "
                   "among cards in your graveyard.\n"
                   "This spell can't be countered.\nDraw a card.",
                   types=types)
        assert h[0].kind is HostKind.STATIC and h[0].from_zone == "stack"
        assert len(h[0].cost_modifiers) == 1
        assert h[1].kind is HostKind.STATIC and h[1].from_zone == "stack"
        assert "uncounterable" in h[1].flags
        assert h[2].kind is (HostKind.SPELL if "sorcery" in types
                             else HostKind.STATIC)


def test_spell_statics_and_additional_costs_are_lifted_out_of_the_resolution_text():
    h = _hosts("As an additional cost to cast this spell, sacrifice a "
               "creature.\nThis spell can't be countered.\n"
               "Draw two cards.", types=("sorcery",))
    assert _kinds(h) == [HostKind.ADDITIONAL_COST, HostKind.STATIC,
                         HostKind.SPELL]
    assert h[0].cost is not None and h[0].body == ()
    assert _body(h[2]) == "draw two cards."


# ── Activated abilities (A6, A7, A8; CR 602, 605) ───────────────────────

def test_an_activation_cost_is_not_an_effect_and_is_typed_from_the_printed_cost_text():
    """A7: the cost is parse_activation_cost(printed head); the printed
    'this land' is payable where the normalised '~' would not be."""
    h = _hosts("{T}, Pay 1 life, Sacrifice this land: Search your library "
               "for a Plains or Island card, put it onto the battlefield, "
               "then shuffle.", "Tidal Expanse", types=("land",))
    a = h[0]
    assert a.kind is HostKind.ACTIVATED and a.activation_index == 0
    cost = a.cost.thaw()
    assert cost.tap_self and cost.sacrifice_self and cost.life == 1
    assert not cost.unpayable
    assert _body(a).startswith("search your library")
    assert "pay 1 life" not in _body(a)


def test_an_activation_cost_modifier_is_absorbed_onto_the_host_not_resolved():
    """CR 601.2f via 602.2b, A8: "This ability costs {1} less to activate
    ..." is a COST_DELTA on the host's cost_modifiers, never an effect."""
    h = _hosts("{T}: Add {G}.\n"
               "Channel — {1}{G}, Discard this card: Destroy target artifact, "
               "enchantment, or nonbasic land an opponent controls. This "
               "ability costs {1} less to activate for each legendary "
               "creature you control.", "Rootwise Spire", types=("land",),
               legendary=True, keywords=("Channel",))
    ch = h[1]
    assert ch.kind is HostKind.ACTIVATED and ch.label == "channel"
    assert len(ch.cost_modifiers) == 1
    assert "costs {1} less" not in _body(ch)
    assert _body(ch).startswith("destroy target artifact")


def test_a_channel_ability_is_activated_from_the_hand_with_its_parsed_cost():
    h = _hosts("Channel — {1}{G}, Discard this card: Destroy target "
               "artifact.", "Rootwise Spire", types=("land",),
               keywords=("Channel",))
    ch = h[0]
    assert ch.kind is HostKind.ACTIVATED and ch.from_zone == "hand"
    assert ch.cost is not None and ch.body == ((ch.text.index("destroy"),
                                                len(ch.text)),)
    assert ch.activation_index == 0


def test_an_activated_ability_that_could_add_mana_without_a_target_is_a_mana_ability_even_with_riders():
    """CR 605.1a, A6: no target and an ADD_MANA anywhere is a mana ability,
    with painland damage, haste or 'instead' riders."""
    h = _hosts("{T}: Add {C}.\n{T}: Add {U} or {R}. This land deals 1 "
               "damage to you.", "Coral Shoal", types=("land",))
    assert _kinds(h) == [HostKind.MANA_ABILITY, HostKind.MANA_ABILITY]
    assert [x.activation_index for x in h] == [0, 1]
    h = _hosts("{T}: Add {C}. If this land has a luck counter on it, "
               "instead add one mana of any color.", types=("land",))
    assert h[0].kind is HostKind.MANA_ABILITY
    h = _hosts("{1}, {T}: Add {R}. Target creature gains haste.",
               types=("artifact",))
    assert h[0].kind is HostKind.ACTIVATED      # a target: not a mana ability


def test_a_triggered_ability_that_adds_mana_from_a_mana_event_is_a_triggered_mana_ability():
    """CR 605.1b, A6: a mana-event head with no target and an ADD_MANA is
    a triggered mana ability (host flag mana_ability)."""
    h = _hosts("Whenever you tap a creature for mana, add an additional {G}.",
               types=("enchantment",))
    t = h[0]
    assert t.kind is HostKind.TRIGGERED
    assert EventHint.TAPPED_FOR_MANA in t.trigger.event_hints
    assert "mana_ability" in t.flags
    h = _hosts("Whenever enchanted Forest is tapped for mana, its controller "
               "adds an additional one mana of the chosen color.",
               types=("enchantment",))
    assert "mana_ability" in h[0].flags
    h = _hosts("Whenever a creature enters, add {G}.", types=("enchantment",))
    assert "mana_ability" not in h[0].flags     # not a mana event


def test_activate_only_riders_are_restrictions_not_effects():
    h = _hosts("{2}, {T}: Draw a card. Activate only as a sorcery.\n"
               "{1}: Scry 1. Activate only once each turn.\n"
               "{3}: Untap this artifact. Activate only during your turn.",
               types=("artifact",))
    assert "sorcery_speed" in h[0].flags and _body(h[0]) == "draw a card."
    assert "once_each_turn" in h[1].restrictions and _body(h[1]) == "scry 1."
    assert h[2].restrictions == ("Activate only during your turn.",)
    assert _body(h[2]) == "untap ~."


# ── Loyalty (CR 606; A12) ───────────────────────────────────────────────

def test_a_loyalty_line_is_one_host_with_its_signed_cost_and_the_shared_slot():
    h = _hosts("[+1]: Draw a card.\n[−2]: Destroy target creature.\n"
               "[−7]: You get an emblem.", "Vessa Orn",
               types=("planeswalker",), legendary=True)
    assert _kinds(h) == [HostKind.LOYALTY] * 3
    assert [x.loyalty_cost.n for x in h] == [1, -2, -7]
    assert [x.loyalty_slot for x in h] == ["plus", "minus", "ult"]
    assert _body(h[1]) == "destroy target creature."


def test_a_variable_loyalty_cost_is_a_loyalty_host_with_x_bound_by_the_cost():
    """CR 107.3, 606.4: [-X] is LOYALTY with Amount(X, n=-1), never an
    activated ability, and takes no slot (oracle_parser.loyalty_slot_for)."""
    h = _hosts("[+1]: Draw a card.\n[−X]: Destroy target creature with mana "
               "value X.\n[−8]: You get an emblem.", "Vessa Orn",
               types=("planeswalker",), legendary=True)
    assert _kinds(h) == [HostKind.LOYALTY] * 3
    x = h[1]
    assert x.loyalty_cost.kind is AmountKind.X and x.loyalty_cost.n == -1
    assert x.loyalty_slot == ""
    assert [y.loyalty_slot for y in h] == ["plus", "", "minus"]


# ── Modal (CR 700.2; A4) ────────────────────────────────────────────────

def test_modal_bullets_are_mode_hosts_with_the_header_choose_bounds():
    h = _hosts("Choose two —\n• Target player draws a card.\n"
               "• Exile target creature.\n• You gain 2 life.")
    assert _kinds(h) == [HostKind.SPELL]
    spell = h[0]
    assert spell.choose == (2, 2)
    assert [m.kind for m in spell.modes] == [HostKind.MODE] * 3
    assert [m.mode_index for m in spell.modes] == [0, 1, 2]
    assert _body(spell.modes[1]) == "exile target creature."
    for header, bounds in (("Choose one —", (1, 1)),
                           ("Choose one or both —", (1, 2)),
                           ("Choose up to two —", (0, 2))):
        h = _hosts(header + "\n• Draw a card.\n• Gain 2 life.\n• Scry 1.")
        assert h[0].choose == bounds, header


def test_a_modal_header_inside_reminder_text_still_makes_the_spell_modal_with_mode_costs():
    """A4: Tiered and Spree are modal through the face keywords or the
    removed reminder; tiered bullets carry a mode name and cost, spree
    bullets a '+ {c}' cost."""
    h = _hosts("Tiered (Choose one additional cost.)\n"
               "• Fire — {0} — This spell deals 1 damage to each creature.\n"
               "• Fira — {2} — This spell deals 2 damage to each creature.",
               keywords=("Tiered", "Fire", "Fira"))
    assert _kinds(h) == [HostKind.KEYWORD, HostKind.SPELL]
    spell = h[1]
    assert spell.choose == (1, 1)
    assert [m.mode_cost for m in spell.modes] == ["{0}", "{2}"]
    assert [m.label for m in spell.modes] == ["fire", "fira"]
    assert _body(spell.modes[0]) == "~ deals 1 damage to each creature."
    h = _hosts("Spree (Choose one or more additional costs.)\n"
               "+ {1} — Draw a card.\n+ {2} — You gain 3 life.",
               keywords=("Spree",))
    spell = h[1]
    assert spell.choose == (1, 2)
    assert [m.mode_cost for m in spell.modes] == ["+ {1}", "+ {2}"]
    # The reminder alone (no face keyword data) still makes it modal.
    h = _hosts("Spree (Choose one or more additional costs.)\n"
               "+ {1} — Draw a card.\n+ {2} — You gain 3 life.")
    assert h[-1].choose == (1, 2) and len(h[-1].modes) == 2


def test_one_or_more_and_any_number_modal_headers_are_recognised():
    for header, bounds in (("Choose one or more —", (1, 3)),
                           ("Choose any number —", (0, 3))):
        h = _hosts(header + "\n• Draw a card.\n• Gain 2 life.\n• Scry 1.")
        assert h[0].choose == bounds and len(h[0].modes) == 3, header


def test_a_modal_trigger_header_keeps_its_trigger_head():
    h = _hosts("Whenever this creature attacks, choose one —\n"
               "• Discard a card. If you do, draw a card.\n"
               "• Exile up to one target card from a graveyard.",
               types=("creature",))
    assert _kinds(h) == [HostKind.TRIGGERED]
    t = h[0]
    assert t.trigger.event_hints == (EventHint.SELF_ATTACKS,)
    assert t.choose == (1, 1) and len(t.modes) == 2
    assert t.body == ()
    assert t.paragraphs == (0, 1, 2)


# ── Delays, triggers, labels (A5, A11, M7; CR 603) ──────────────────────

def test_a_delay_prefixed_paragraph_on_a_spell_is_a_delayed_sub_ability_of_the_spell():
    """CR 603.7, A5: the paragraph is part of the SPELL host, marked with
    its delayed timing, never a triggered ability."""
    h = _hosts("Search your library for a green creature card, reveal it, "
               "put it into your hand, then shuffle.\n"
               "At the beginning of your next upkeep, pay {2}{G}{G}. If you "
               "don't, you lose the game.")
    assert _kinds(h) == [HostKind.SPELL]
    parts = h[0].parts
    assert parts[0].delay is None
    assert parts[1].delay is not None and parts[1].delay.name == "YOUR_NEXT_UPKEEP"
    assert "pay {2}{g}{g}" in h[0].text[slice(*parts[1].body[0])]


def test_a_delay_prefixed_paragraph_on_a_permanent_is_unmodelled_structure():
    h = _hosts("At the beginning of the next end step, sacrifice this "
               "creature.", types=("creature",))
    assert _kinds(h) == [HostKind.UNKNOWN]
    assert h[0].unmodelled[0][0].stage.name == "STRUCTURE"
    assert h[0].body == ()


def test_a_trigger_host_keeps_its_head_and_lifts_an_intervening_if_onto_the_head():
    """CR 603.4: 'When/Whenever/At <event>, if <condition>, <effect>' --
    the condition lives on the head, never in the body."""
    h = _hosts("When this creature enters, if you control a red permanent, "
               "it deals 2 damage to any target.", "Ember Pup",
               types=("creature",))
    t = h[0]
    assert t.trigger.raw == "when ~ enters"
    assert t.trigger.intervening_if is not None
    assert t.trigger.intervening_if.kind is ConditionKind.STATE
    assert _body(t) == "it deals 2 damage to any target."


def test_a_disjunctive_trigger_head_keeps_every_event_hint():
    """CR 603.2, A11: one body, every event in the head."""
    cases = {
        "Whenever this creature enters or attacks, you may draw a card.":
            (EventHint.SELF_ENTERS, EventHint.SELF_ATTACKS),
        "When you cast this spell and whenever this creature attacks, "
        "destroy up to one target nonland permanent.":
            (EventHint.SELF_CAST, EventHint.SELF_ATTACKS),
        "Whenever this creature or another creature you control dies, "
        "draw a card.": (EventHint.SELF_DIES, EventHint.OTHER_DIES),
        "At the beginning of your upkeep, draw a card.":
            (EventHint.BEGINNING_OF,),
        # One event: "and at least two others" is no second trigger word.
        "Whenever this creature and at least two other creatures attack, "
        "draw a card.": (EventHint.SELF_ATTACKS, EventHint.ATTACKS_OTHER),
    }
    for text, hints in cases.items():
        t = _hosts(text, types=("creature",))[0]
        assert t.kind is HostKind.TRIGGERED, text
        assert t.trigger.event_hints == hints, text
    t = _hosts("At the beginning of your upkeep, draw a card.",
               types=("creature",))[0]
    assert t.trigger.step == "your upkeep"


def test_a_plural_event_verb_types_the_same_event_hint_as_its_singular():
    """CR 603.2: "one or more <objects> die / enter / leave" is the same
    event as its singular, observed for each object."""
    cases = {
        "Whenever one or more other creatures you control die, draw a "
        "card.": (EventHint.OTHER_DIES,),
        "Whenever one or more other creatures you control enter, draw a "
        "card.": (EventHint.OTHER_ENTERS,),
        "Whenever this creature and another creature die, draw a card.":
            (EventHint.SELF_DIES, EventHint.OTHER_DIES),
        "Whenever one or more lands you control enter, draw a card.":
            (EventHint.LANDFALL,),
    }
    for text, hints in cases.items():
        t = _hosts(text, types=("creature",))[0]
        assert t.trigger.event_hints == hints, text


def test_a_self_reference_by_the_permanent_subtype_noun_is_the_source():
    """L0 step 3 (CR 201.4b): "this <noun>" is a self-reference for every
    object noun the pool prints of its own source -- a Saga, Class, Case,
    Room or Spacecraft names itself so -- in a trigger head and a body."""
    for noun, types in (("Spacecraft", ("artifact",)),
                        ("Class", ("enchantment",)),
                        ("Case", ("enchantment",)),
                        ("Room", ("enchantment",)),
                        ("Saga", ("enchantment",))):
        t = _hosts("When this %s enters, exile this %s." % (noun, noun),
                   types=types)[0]
        assert t.trigger.raw == "when ~ enters", noun
        assert t.trigger.event_hints == (EventHint.SELF_ENTERS,), noun
        assert _body(t) == "exile ~.", noun


def test_a_land_subtype_subject_entering_is_landfall():
    """CR 205.3i: a land subtype names a land, so "a Mountain you control
    enters" is a land entering -- LANDFALL, like "a land"."""
    for text in ("Whenever a Mountain you control enters, draw a card.",
                 "Whenever one or more Forests you control enter, draw a "
                 "card.",
                 "Whenever a Desert enters, draw a card."):
        t = _hosts(text, types=("land",))[0]
        assert t.trigger.event_hints == (EventHint.LANDFALL,), text
    t = _hosts("Whenever a Goblin you control enters, draw a card.",
               types=("creature",))[0]
    assert t.trigger.event_hints == (EventHint.OTHER_ENTERS,)


def test_a_trigger_filter_stays_in_the_head_and_is_not_an_effect_condition():
    """The head runs past a serial list in its subject; the filter is the
    head's, and the body starts after the head's own comma."""
    t = _hosts("Whenever an artifact, creature, or enchantment you control "
               "enters, you gain 1 life.", types=("enchantment",))[0]
    assert t.trigger.raw == ("whenever an artifact, creature, or enchantment "
                             "you control enters")
    assert t.trigger.event_hints == (EventHint.OTHER_ENTERS,)
    assert _body(t) == "you gain 1 life."
    assert t.trigger.intervening_if is None
    t = _hosts("Whenever a creature you control attacks, it gets +1/+0 "
               "until end of turn. This ability triggers only once each "
               "turn.", types=("enchantment",))[0]
    assert t.trigger.once_each_turn
    assert _body(t) == "it gets +1/+0 until end of turn."


def test_a_trigger_head_runs_through_any_serial_list_and_ends_where_a_sentence_starts():
    """Rule 8: the head ends at the first depth-0 comma after which the
    remainder is a sentence. A list of subtypes, coordinate adjectives or
    keyword actions in the head is no sentence, whatever its nouns; an
    intervening "if" and a body that is itself a serial list end it."""
    cases = {
        "Whenever another Goblin, Orc, or Army you control dies, exile the "
        "top card of your library.":
            "whenever another goblin, orc, or army you control dies",
        "Whenever another Frog, Rabbit, Raccoon, or Squirrel you control "
        "enters, put a +1/+1 counter on it.":
            "whenever another frog, rabbit, raccoon, or squirrel you "
            "control enters",
        "Whenever a nontoken, non-Angel creature you control dies, return "
        "that card to the battlefield.":
            "whenever a nontoken, non-angel creature you control dies",
        "Whenever you waterbend, earthbend, firebend, or airbend, draw a "
        "card.": "whenever you waterbend, earthbend, firebend, or airbend",
        "Whenever this creature attacks, if you control an artifact, draw "
        "a card.": "whenever ~ attacks",
        "When this creature enters, you gain 2 life, draw a card, and "
        "scry 1.": "when ~ enters",
        "Whenever this creature attacks, it gets +2/+0, gains trample, and "
        "can't be blocked this turn.": "whenever ~ attacks",
        "Whenever you cast a creature spell with mana value 4, 5, or 6, "
        "draw a card.":
            "whenever you cast a creature spell with mana value 4, 5, or 6",
        # A list that runs to the sentence end is the body's subject.
        "Whenever you cast a noncreature spell, Birds, Frogs, Otters, and "
        "Rats you control get +1/+1 until end of turn.":
            "whenever you cast a noncreature spell",
        "When this creature enters, for each opponent, create a 1/1 "
        "token.": "when ~ enters",
    }
    for text, head in cases.items():
        t = _hosts(text, "Probe", types=("creature",))[0]
        assert t.kind is HostKind.TRIGGERED, text
        assert t.trigger.raw == head, text
        assert S.uncovered(t) == "", text


def test_an_ability_word_is_a_label_not_a_condition():
    """CR 207.2c: the ability word has no rules meaning; it is the host's
    label and the classification reads the rest."""
    t = _hosts("Landfall — Whenever a land you control enters, you gain 1 "
               "life.", types=("creature",), keywords=("Landfall",))[0]
    assert t.kind is HostKind.TRIGGERED and t.label == "landfall"
    assert t.trigger.raw == "whenever a land you control enters"
    assert EventHint.LANDFALL in t.trigger.event_hints


def test_a_kicker_line_is_a_keyword_and_its_payoff_carries_the_kicked_cast_fact():
    """CR 702.33: the kicker line is a KEYWORD host; the kicked payoff
    stays in the SPELL host's body for the L2 kicked frame."""
    h = _hosts("Kicker {1}{U} (You may pay an additional {1}{U} as you cast "
               "this spell.)\nDraw a card. If this spell was kicked, draw "
               "two cards instead.", keywords=("Kicker",))
    assert _kinds(h) == [HostKind.KEYWORD, HostKind.SPELL]
    assert h[0].keywords[0].name == "kicker" and h[0].keywords[0].cost == "{1}{u}"
    assert "if ~ was kicked" in _body(h[1])


def test_a_saga_chapter_host_carries_its_chapter_numbers():
    h = _hosts("(As this Saga enters and after your draw step, add a lore "
               "counter.)\nI, II — Draw a card.\nIII — Exile this Saga.",
               types=("enchantment",))
    assert _kinds(h) == [HostKind.CHAPTER, HostKind.CHAPTER]
    assert [x.chapters for x in h] == [(1, 2), (3,)]
    assert _body(h[0]) == "draw a card."


def test_a_replacement_static_is_an_explicit_unmodelled_replacement_host():
    """CR 614: one UNMODELLED(REPLACEMENT), never resolution text."""
    for text in ("This land enters tapped unless you control a Mountain.",
                 "If a source would deal damage to you, prevent 1 of that "
                 "damage instead.",
                 "As this creature enters, choose a color."):
        h = _hosts(text, types=("land", "creature"))
        assert _kinds(h) == [HostKind.REPLACEMENT], text
        assert h[0].unmodelled[0][0].stage.name == "REPLACEMENT"
        assert h[0].body == ()


def _refusals(h):
    return [u.detail for u, _ in h.unmodelled]


def test_abilities_under_a_level_band_are_refused_with_the_band_never_unconditional():
    """Rule 13 (CR 711.2): a leveler's band gates its P/T and abilities.
    The band and every paragraph up to the next band are one
    UNMODELLED(STRUCTURE) refusal -- never an always-on host."""
    h = _hosts("Level up {W}\nLEVEL 2-6\n3/3\nFirst strike\nLEVEL 7+\n4/4\n"
               "Double strike", types=("creature",),
               keywords=("Level up", "First strike", "Double strike"))
    assert _kinds(h) == [HostKind.KEYWORD, HostKind.UNKNOWN, HostKind.UNKNOWN]
    assert h[0].keywords[0].name == "level_up"
    assert [x.paragraphs for x in h[1:]] == [(1, 2, 3), (4, 5, 6)]
    assert [_refusals(x) for x in h[1:]] == [["structure.level_band"]] * 2
    assert all(x.body == () and S.uncovered(x) == "" for x in h)


def test_a_class_level_line_gates_every_ability_up_to_the_next_level_and_is_no_activated_ability():
    """Rule 13 (CR 716): a Class level line "{cost}: Level N" is the level
    gate, not an activated ability with effect text "level N", and the
    abilities it gates are refused with it. Level 1 is always on."""
    h = _hosts("(Gain the next level as a sorcery to add its ability.)\n"
               "Whenever you cast a noncreature spell, you may discard a "
               "card. If you do, draw a card.\n{2}{R}: Level 2\n"
               "Noncreature spells you cast cost {1} less to cast.\n"
               "{2}{R}: Level 3\nIf a source you control would deal "
               "noncombat damage to an opponent, it deals that much damage "
               "plus 2 instead.", types=("enchantment",))
    assert _kinds(h) == [HostKind.TRIGGERED, HostKind.UNKNOWN, HostKind.UNKNOWN]
    assert [x.paragraphs for x in h[1:]] == [(1, 2), (3, 4)]
    assert [_refusals(x) for x in h[1:]] == [["structure.class_level"]] * 2
    assert all(x.activation_index is None for x in h)


def test_a_station_threshold_gates_its_abilities_and_is_not_a_die_roll_row():
    """Rule 13: a Spacecraft's "N+ | <abilities>" row is a station
    threshold (the CR 702 station keyword), refused under its own code -- not a CR 706
    die-roll result row."""
    h = _hosts("Station (Tap another creature you control: Put charge "
               "counters equal to its power on this Spacecraft.)\n"
               "5+ | Flying, trample\n8+ | Whenever this Spacecraft attacks, "
               "draw a card.", types=("artifact",), keywords=("Station",))
    assert _kinds(h) == [HostKind.KEYWORD, HostKind.UNKNOWN, HostKind.UNKNOWN]
    assert [_refusals(x) for x in h[1:]] == [["structure.station_threshold"]] * 2


def test_a_die_roll_result_row_is_unknown_structure():
    h = _hosts("Roll a d20.\n1—9 | Draw a card.\n10—20 | Draw two cards.",
               types=("sorcery",))
    assert _kinds(h) == [HostKind.SPELL, HostKind.UNKNOWN, HostKind.UNKNOWN]
    assert [_refusals(x) for x in h[1:]] == [["structure.die_table"]] * 2


# ── The coverage invariant and determinism ──────────────────────────────

def test_every_character_of_a_host_is_a_body_a_consumed_token_or_a_refusal():
    """Section 3 coverage invariant at L1: every non-space character of a
    host's text is in a body span (L2's), a span L1 consumed (head, cost,
    label, rider, keyword, header) or a refusal span."""
    texts = [
        ("{T}, Pay 1 life, Sacrifice this land: Search your library. "
         "Activate only as a sorcery.", ("land",)),
        ("Landfall — Whenever a land you control enters, if you control "
         "a red permanent, you gain 1 life. This ability triggers only "
         "once each turn.", ("creature",)),
        ("Whenever this creature attacks, choose one —\n• Draw a card.\n"
         "• Gain 1 life.", ("creature",)),
        ("Kicker {1}{U}\nDraw a card.\nAt the beginning of the next end "
         "step, draw a card.", ("instant",)),
    ]
    for text, types in texts:
        for h in _hosts(text, types=types):
            for x in (h, *h.modes):
                assert S.uncovered(x) == "", (text, x.kind, S.uncovered(x))


def test_the_face_parse_is_a_pure_memoised_function_of_text_and_facts():
    text = "{T}: Add {G}.\nWhenever a land enters, draw a card."
    f = _facts("Grove", types=("land",))
    a = S.parse_face_structure(text, f)
    import engine.effect_grammar as grammar
    grammar.clear_caches()
    b = S.parse_face_structure(text, f)
    assert a == b and a is not b
    # A fact the layer reads changes the result: the spell fact merges.
    c = S.parse_face_structure("Draw a card.\nScry 1.", _facts())
    d = S.parse_face_structure("Draw a card.\nScry 1.",
                               _facts(types=("enchantment",)))
    assert len(c.hosts) == 1 and len(d.hosts) == 2
