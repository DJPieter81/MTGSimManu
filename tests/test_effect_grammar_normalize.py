"""L0 of the clause grammar: normalisation with an offset map (design doc
2026-09-29, section 3 L0 steps 1-5; A4, A7, A9, A10, A40; E0 step 8).

L0 is the one owner of the text every later layer reads. These tests pin
its output contract (the leaf contract's "L0 output" bullet in
`engine.effect_grammar.sub`) and each of its five steps:

1. reminder text is removed exactly as `oracle_parser.strip_reminder_text`
   removes it, and every removed span is recorded per paragraph (A4);
2. "named <Name>" spans are masked ``⟨nk⟩``: a name there is data, never a
   self-reference;
3. self-forms become ``~``: the card's names (longest first, word-bounded,
   on printed case), "this <noun>" for the `SELF_NOUNS`, the gated
   legendary short name, and on character faces the self-pronouns by
   grammatical case (A9, M1);
4. double-quoted spans are masked ``⟨qk⟩``, nested single quotes under the
   A10 delimiter rule;
5. dashes and apostrophes unified, whitespace collapsed, lowercased.

`printed_span` maps a normalised span back to the printed text, which the
cost and kicked-clause views read (A7, A40).

Synthetic text with invented names only; registered-deck witnesses live in
tests/test_effect_grammar_normalize_pool.py.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from engine.effect_grammar.sub import CACHE_SIZE, SELF_NOUNS

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "engine" / "effect_grammar" / "normalize.py"


def _N():
    from engine.effect_grammar import normalize
    return normalize


def _facts(name="Quillon Vey", *, types=("creature",), legendary=False,
           subtypes=()):
    N = _N()
    character = "planeswalker" in types or (legendary and "creature" in types)
    return N.Facts(
        names=N.self_names(name, is_legendary=legendary,
                           is_character=character, subtypes=subtypes),
        type_class=frozenset(types),
        is_spell=bool({"instant", "sorcery"} & set(types)),
        is_legendary=legendary,
        is_planeswalker="planeswalker" in types)


def _norm(text, facts=None):
    return _N().normalize(text, facts if facts is not None else _facts())


# ── Step 1: reminder text ──────────────────────────────────────────────

def test_reminder_text_is_removed_and_recorded_with_its_paragraph():
    text = ("Flying\nKicker {1}{U} (You may pay an additional {1}{U} as you "
            "cast this spell.)\nDraw a card.")
    r = _norm(text)
    assert r.text == "flying\nkicker {1}{u}\ndraw a card."
    (rem,) = r.reminders
    assert rem.host == -1 and rem.paragraph == 1
    # Reminder text is recorded, not parsed: unified and lowercased only.
    assert rem.text == "you may pay an additional {1}{u} as you cast this spell."
    assert text[slice(*rem.printed)].startswith("(You may pay")
    assert text[slice(*rem.printed)].endswith("spell.)")
    assert r.text[:rem.at].endswith("kicker {1}{u}")


@pytest.mark.parametrize("text", [
    "Flying (This creature can't be blocked (except by fliers).) and haste.",
    "Haste ) stray close (and an open paren",
    "(A paragraph that is all reminder.)\nTrample",
    "Ward {2} (Whenever this creature becomes the target, counter it.), vigilance",
])
def test_removing_reminder_text_is_exactly_the_one_reminder_stripper(text):
    """L0 reuses strip_reminder_text's rule; it only records what it
    removed. Removing the recorded printed spans gives the stripper's
    output, character for character."""
    from engine.oracle_parser import strip_reminder_text
    r = _norm(text)
    out, last = [], 0
    for rem in sorted(r.reminders, key=lambda x: x.printed):
        out.append(text[last:rem.printed[0]])
        last = rem.printed[1]
    out.append(text[last:])
    assert "".join(out) == strip_reminder_text(text)


@pytest.mark.parametrize("text,expected", [
    ("(One.) (Two.)\nFlying", "flying"),
    ("Flying (One.) (Two.), haste (Three.).", "flying, haste."),
    ("Ward {2} (One.)\n(Two.) Trample", "ward {2}\ntrample"),
])
def test_adjacent_reminders_and_their_spacing_leave_clean_text(text, expected):
    r = _norm(text)
    assert r.text == expected
    assert len(r.reminders) == text.count("(")


def test_a_paragraph_that_is_only_reminder_text_vanishes_but_its_reminder_is_kept():
    text = "(As this Saga enters, add a lore counter.)\nI - Draw a card."
    r = _norm(text)
    assert r.text == "i - draw a card."
    (rem,) = r.reminders
    assert rem.paragraph == -1 and rem.text.startswith("as this saga enters")


def test_a_choose_header_inside_reminder_text_is_kept_for_the_structure_layer():
    """A4: modal structure is detected before reminder text is stripped,
    so the removed reminder must carry its text."""
    text = ("Spree (Choose one or more additional costs.)\n"
            "+ {1} — Draw a card.\n+ {2} — Create a Treasure token.")
    r = _norm(text)
    assert r.text.split("\n")[0] == "spree"
    assert [m.text for m in r.reminders] == [
        "choose one or more additional costs."]
    assert r.reminders[0].paragraph == 0


# ── Step 2: named <Name> ───────────────────────────────────────────────

def test_a_name_inside_a_named_card_phrase_is_data_not_a_self_reference():
    facts = _facts("Glimmer Wisp")
    r = _norm("Search your library for a card named Glimmer Wisp, reveal it, "
              "then shuffle. Glimmer Wisp deals 1 damage to any target.", facts)
    assert r.text == ("search your library for a card named ⟨n0⟩, reveal it, "
                      "then shuffle. ~ deals 1 damage to any target.")
    assert r.names == ("Glimmer Wisp",)


def test_the_faces_own_name_in_a_named_phrase_is_taken_exactly():
    """A cost list after the name is not read into it."""
    f = _facts("Grob, the Stout", legendary=True)
    r = _norm("Discard another card named Grob, the Stout, Sacrifice two "
              "Mountains: Grob deals 4 damage to any target.", f)
    assert r.names == ("Grob, the Stout",)
    assert r.text == ("discard another card named ⟨n0⟩, sacrifice two "
                      "mountains: ~ deals 4 damage to any target.")


@pytest.mark.parametrize("printed,masked,names", [
    ("a card named Orrin of the Deep Vale and put it into your hand",
     "a card named ⟨n0⟩ and put it into your hand", ("Orrin of the Deep Vale",)),
    ("a card named Sela, the Unbent, reveal it",
     "a card named ⟨n0⟩, reveal it", ("Sela, the Unbent",)),
    ("cards named Marrow Kite and/or cards named Tallow Bell",
     "cards named ⟨n0⟩ and/or cards named ⟨n1⟩", ("Marrow Kite", "Tallow Bell")),
    ("a card named Marrow Kite or Tallow Bell, reveal it",
     "a card named ⟨n0⟩ or ⟨n1⟩, reveal it", ("Marrow Kite", "Tallow Bell")),
    ("a token named Ash-Born Wick-Tender with flying",
     "a token named ⟨n0⟩ with flying", ("Ash-Born Wick-Tender",)),
    ("named Ember Cask in all graveyards", "named ⟨n0⟩ in all graveyards",
     ("Ember Cask",)),
    ("creatures named the chosen name", "creatures named the chosen name", ()),
])
def test_a_named_phrase_masks_each_printed_name_and_stops_at_the_first_non_name_word(
        printed, masked, names):
    r = _norm(printed)
    assert r.text == masked
    assert r.names == names


# ── Step 3: self-forms ─────────────────────────────────────────────────

@pytest.mark.parametrize("noun", SELF_NOUNS)
def test_this_noun_is_a_self_form_for_every_self_noun(noun):
    r = _norm("When this %s enters, This %s deals 1 damage." % (noun, noun))
    assert r.text == "when ~ enters, ~ deals 1 damage."


def test_this_ability_names_the_ability_and_is_not_a_self_form():
    """CR 113.1: 'this ability' is the ability, not the object."""
    r = _norm("This ability costs {1} less to activate. Copy this ability.")
    assert r.text == "this ability costs {1} less to activate. copy this ability."


def test_every_self_reference_form_normalises_to_the_source():
    """Full name, face name of a two-face card, legendary short name and
    'this <noun>': one ~ each, longest first."""
    f = _facts("Brennic, the Ashen Warden // Brennic, Ember Sovereign",
               legendary=True)
    assert set(f.names) >= {
        "Brennic, the Ashen Warden // Brennic, Ember Sovereign",
        "Brennic, the Ashen Warden", "Brennic, Ember Sovereign", "Brennic"}
    r = _norm("When Brennic, the Ashen Warden enters, Brennic deals 2 damage. "
              "Brennic's power is 3. Sacrifice this creature.", f)
    assert r.text == ("when ~ enters, ~ deals 2 damage. ~'s power is 3. "
                      "sacrifice ~.")


def test_a_name_is_rewritten_only_as_a_whole_word_on_printed_case():
    f = _facts("Ember")
    r = _norm("Ember deals 1 damage. Embers and Embermaw and the ember stay.", f)
    assert r.text == "~ deals 1 damage. embers and embermaw and the ember stay."


@pytest.mark.parametrize("name,legendary,types,subtypes,short", [
    ("Sela, the Unbent", True, ("creature",), (), "Sela"),
    ("Orrin of the Deep Vale", True, ("creature",), (), "Orrin"),
    ("Varek the Hollow", True, ("creature",), (), "Varek"),
    ("Captain Ilsa of Morrow", True, ("creature",), (), "Captain Ilsa"),
    ("Tovin Ashgrave", True, ("planeswalker",), (), "Tovin"),
    ("Tovin Ashgrave", False, ("creature",), (), None),
    ("Mire Hivequeen", True, ("creature",), ("Mire",), None),
    ("Gallant's Reverie", True, ("enchantment",), (), None),
    ("The Hollow Crown", True, ("artifact",), (), None),
    ("Kessa, Who Waits", True, ("land",), (), "Kessa"),
])
def test_the_legendary_short_name_is_gated(name, legendary, types, subtypes,
                                           short):
    """A short name exists only on a legendary face: the part before the
    comma; on a character face, the part before ' the ' / ' of ', else the
    first word. Never a subtype of the face, never an article."""
    names = _facts(name, types=types, legendary=legendary,
                   subtypes=subtypes).names
    shorts = [n for n in names if n not in (name, *name.split(" // "))]
    assert shorts == ([short] if short else [])


def test_a_short_name_that_begins_a_longer_proper_name_is_not_a_self_form():
    f = _facts("Varek the Hollow", legendary=True)
    r = _norm("When Varek dies, create Varek the Returned, a legendary 2/2 "
              "black Zombie creature token. Varek of the North is not him.", f)
    assert r.text == ("when ~ dies, create varek the returned, a legendary 2/2 "
                      "black zombie creature token. varek of the north is not ~.")


def test_a_name_that_reads_as_an_imperative_verb_at_clause_start_is_left():
    """A name that collides with a verb is never rewritten at clause start
    where it takes an object; it is everywhere else."""
    f = _facts("Mend", types=("instant",))
    r = _norm("Mend target creature. Mend deals no damage. Return Mend to "
              "its owner's hand.", f)
    assert r.text == ("mend target creature. ~ deals no damage. return ~ to "
                      "its owner's hand.")


def test_self_pronouns_normalise_by_grammatical_case():
    """A9 / M1: object him/her -> ~, possessive his/her -> ~'s, subject
    he/she -> ~, he's/she's -> '~ is'; on walker and legendary-character
    faces only."""
    walker = _facts("Tovin, Storm Herald", types=("planeswalker",),
                    legendary=True)
    r = _norm("Exile Tovin, then return him to the battlefield transformed "
              "under his owner's control. As long as Tovin has a loyalty "
              "counter on him, he's a 3/4 creature and he has hexproof.", walker)
    assert r.text == ("exile ~, then return ~ to the battlefield transformed "
                      "under ~'s owner's control. as long as ~ has a loyalty "
                      "counter on ~, ~ is a 3/4 creature and ~ has hexproof.")
    hero = _facts("Ilsa, Bright Lance", legendary=True)
    r = _norm("Put a +1/+1 counter on her. She gains flying. Put her into her "
              "owner's library. Ilsa deals damage to herself equal to her power.",
              hero)
    assert r.text == ("put a +1/+1 counter on ~. ~ gains flying. put ~ into ~'s "
                      "owner's library. ~ deals damage to ~ equal to ~'s power.")


@pytest.mark.parametrize("types,legendary", [
    (("creature",), False), (("sorcery",), False), (("enchantment",), True)])
def test_a_pronoun_on_a_non_character_face_is_never_a_self_form(types, legendary):
    f = _facts("Quillon Vey", types=types, legendary=legendary)
    r = _norm("If it was a walker, her controller loses 3 life and he draws.", f)
    assert r.text == "if it was a walker, her controller loses 3 life and he draws."


# ── Step 4: quotes ─────────────────────────────────────────────────────

def test_a_double_quoted_span_is_masked_and_its_text_kept_in_the_quote_table():
    r = _norm('Creatures you control have "{T}: Add {G}." and trample.')
    assert r.text == "creatures you control have ⟨q0⟩ and trample."
    assert r.quotes == ("{t}: add {g}.",)
    assert r.quote_parents == (-1,)


def test_sentence_ends_inside_a_quote_never_reach_the_host_text():
    r = _norm('This land gains "{T}: Add {C}. Activate only once." Draw a card.')
    assert r.text == "~ gains ⟨q0⟩ draw a card."
    assert r.quotes == ("{t}: add {c}. activate only once.",)


def test_nested_single_quoted_abilities_are_masked_without_splitting_on_apostrophes():
    """A10: a single quote opens only after with/gains/gain/has/have and
    before a capital, '{' or a self-form; apostrophes (owner's, can't,
    owners') never open or close."""
    text = ('This land gains "{2}, {T}: Create a 0/0 artifact creature token '
            "with 'This token gets +1/+1 for each artifact its owner's "
            "opponents' creatures can't block.'\"")
    r = _norm(text)
    assert r.text == "~ gains ⟨q0⟩"
    assert r.quotes == (
        "{2}, {t}: create a 0/0 artifact creature token with ⟨q1⟩",
        "~ gets +1/+1 for each artifact its owner's opponents' creatures "
        "can't block.")
    assert r.quote_parents == (-1, 0)


def test_coordinated_nested_quotes_are_two_quotes():
    r = _norm("Enchanted land has \"Equipped creature has '{T}: Add {G}{G}' "
              "and '{1}, {T}: Draw a card.'\" and more.")
    assert r.text == "enchanted land has ⟨q0⟩ and more."
    assert r.quotes == ("equipped creature has ⟨q1⟩ and ⟨q2⟩",
                        "{t}: add {g}{g}", "{1}, {t}: draw a card.")
    assert r.quote_parents == (-1, 0, 0)


def test_quotes_are_numbered_in_printed_order_outer_before_inner():
    r = _norm('A has "B has \'{T}: C.\'" and D has "{1}: E."')
    assert r.text == "a has ⟨q0⟩ and d has ⟨q2⟩"
    assert r.quote_parents == (-1, 0, -1)


def test_an_unbalanced_double_quote_is_flagged_and_left_unmasked():
    r = _norm('Creatures you control have "Flying.')
    assert r.text == 'creatures you control have "flying.'
    assert "unbalanced_quote" in r.flags
    assert r.flags <= _N().FLAGS


def test_a_reminder_inside_a_quote_is_recorded_against_that_quote():
    r = _norm('Tokens you control have "Ward {1} (Pay {1} or counter it.)".')
    assert r.text == "tokens you control have ⟨q0⟩."
    assert r.quotes == ("ward {1}",)
    (rem,) = r.reminders
    assert rem.host == 0 and rem.at == len("ward {1}") and rem.paragraph == 0


# ── Step 5: dashes, apostrophes, whitespace, case ──────────────────────

def test_dashes_apostrophes_and_whitespace_are_unified_and_text_lowercased():
    r = _norm("[−2]: Landfall —  Target creature’s owner   draws.\n\n"
              "Ward–Pay 2 life.  ")
    assert r.text == "[-2]: landfall - target creature's owner draws.\nward-pay 2 life."


def test_l0_output_satisfies_the_leaf_contract():
    """The leaf contract's L0 bullet: lowercased, '-' and "'" unified,
    whitespace collapsed, self-forms '~', quotes and names masked."""
    r = _norm('When this creature enters — “quoted” isn’t   it?  This Aura '
              'has "Flying." Search for a card named Quillon Vey.\n\n')
    for s in (r.text, *r.quotes):
        assert s == s.lower()
        assert "’" not in s and "—" not in s and "–" not in s and "−" not in s
        assert "  " not in s and "\n\n" not in s and s == s.strip()
        assert not re.search(r"\bthis (?:%s)\b" % "|".join(SELF_NOUNS), s)


# ── printed_span: the offset map, recomputed per call (A7, A40) ────────

def test_printed_span_maps_a_normalised_cost_back_to_its_printed_text():
    """A7: parse_activation_cost reads 'Sacrifice this land', not '~'."""
    N = _N()
    text = "{T}, Sacrifice this land: Add one mana of any color."
    r = _norm(text)
    assert r.text == "{t}, sacrifice ~: add one mana of any color."
    span = (0, r.text.index(":"))
    assert N.printed_span(text, _facts(), span) == "{T}, Sacrifice this land"


def test_printed_span_of_a_kicked_clause_is_the_exact_printed_sentence():
    """A40: from the end of the kicked frame to the end of its sentence,
    printed case kept, reminder text stripped."""
    N = _N()
    text = ("Kicker {1}{U} (You may pay an additional {1}{U} as you cast this "
            "spell.)\nLook at the top X cards. If this spell was kicked, put "
            "two of those cards into your hand instead. Put the rest back.")
    f = _facts(types=("instant",))
    r = _norm(text, f)
    a = r.text.index("put two")
    b = r.text.index("instead.") + len("instead.")
    assert N.printed_span(text, f, (a, b)) == (
        "put two of those cards into your hand instead.")
    a = r.text.index("if ~ was kicked")
    assert N.printed_span(text, f, (a, b)).startswith("If this spell was kicked,")
    # A span across a removed reminder maps to the printed text around it.
    assert N.printed_span(text, f, (0, r.text.index("\n") + 5)) == (
        "Kicker {1}{U} (You may pay an additional {1}{U} as you cast this "
        "spell.)\nLook")


def test_printed_span_inside_a_quote_indexes_that_quotes_text():
    N = _N()
    text = 'Creatures you control have "{T}, Sacrifice this creature: Draw a card."'
    r = _norm(text)
    q = r.quotes[0]
    assert N.printed_span(text, _facts(), (0, q.index(":")), quote=0) == (
        "{T}, Sacrifice this creature")
    assert N.printed_span(text, _facts(), (r.text.index("⟨"), len(r.text))) == (
        '"{T}, Sacrifice this creature: Draw a card."')


def test_every_normalised_character_maps_inside_the_printed_text():
    N = _N()
    text = ("Ilsa, Bright Lance (Reminder (nested).) — draws. "
            '"Quote with \'{T}: X.\'" named Orrin Vale. Her power.')
    f = _facts("Ilsa, Bright Lance", legendary=True)
    r = _norm(text, f)
    for i in range(len(r.text)):
        s = N.printed_span(text, f, (i, i + 1))
        assert s and s in text


# ── Determinism, caches, imports ───────────────────────────────────────

def test_normalize_is_memoised_in_a_bounded_cache_the_package_clears():
    from engine.effect_grammar import sub
    N = _N()
    assert N.normalize.cache_info().maxsize == CACHE_SIZE
    a = _norm("Draw a card.")
    assert _norm("Draw a card.") is a
    assert N.normalize.cache_info().currsize > 0
    sub.clear_caches()
    assert N.normalize.cache_info().currsize == 0
    b = _norm("Draw a card.")
    assert b == a and hash(b) == hash(a)


def test_the_output_is_a_pure_function_of_text_and_facts():
    f1 = _facts("Quillon Vey")
    f2 = _facts("Other Name")
    t = "Quillon Vey deals 1 damage."
    assert _norm(t, f1).text == "~ deals 1 damage."
    assert _norm(t, f2).text == "quillon vey deals 1 damage."


def test_l0_imports_no_game_state_and_no_other_leaf():
    tree = ast.parse(SRC.read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    engine_mods = {m for m in mods if m.startswith("engine")}
    assert engine_mods <= {"engine.effect_grammar.sub"}, engine_mods


def test_l0_holds_no_card_names():
    """Self-names come from facts; the module's code literals are rule
    words only (docstrings aside)."""
    tree = ast.parse(SRC.read_text())
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and id(n) not in docs:
            assert not re.search(r"\b[A-Z][a-z]+(?:,)? [A-Z][a-z]", n.value), n.value
