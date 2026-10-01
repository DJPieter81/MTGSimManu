"""The verb lexicon of the clause grammar (design doc 2026-09-29, section 4;
A12, A13, A18, A19; E0 step 11).

`engine/effect_grammar/lexicon.py` is the closed table L4 reads to find the
lemma of a clause and pick its reading: each printed lemma and inflection
maps to its table entries in table order (most specific first), an entry
carries the section-4 fields (lemma, verb, family, roles, other_role,
mod_kind, hostile, owner), and an unknown or recognised-but-unsupported
verb is a typed Unmodelled, never a guess. It follows the leaf contract of
`engine.effect_grammar.sub`.

Synthetic L0 text only (lowercased, self-forms ~, quotes masked); no card
names.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from engine.effect_model import ModKind
from engine.effect_spec import (ACTOR_ONLY_VERBS, Amount, AmountKind, Stage,
                                Verb)

REPO = Path(__file__).resolve().parent.parent
LEXICON = REPO / "engine" / "effect_grammar" / "lexicon.py"


def _L():
    from engine.effect_grammar import lexicon
    return lexicon


def _read(text, **kw):
    return _L().find_verb(text, (0, len(text)), **kw)


def _verb(text):
    r = _read(text)
    assert r.value is not None, (text, r)
    return r.value


# ── The table: lemmas, inflections, entry fields ───────────────────────

@pytest.mark.parametrize("word,lemma", [
    ("destroy", "destroy"), ("destroys", "destroy"), ("search", "search"),
    ("searches", "search"), ("copies", "copy"), ("has", "have"),
    ("have", "have"), ("can't", "can't"), ("deals", "deal"),
])
def test_every_printed_inflection_maps_to_its_lemmas_entries(word, lemma):
    L = _L()
    entries = L.VERB_LEXICON[word]
    assert entries and all(isinstance(e, L.LexEntry) for e in entries)
    assert all(e.lemma.split()[0] == lemma for e in entries
               if e.lemma.split()[0] != "the"), (word, entries)
    # One lemma's inflections share one bucket, in table order.
    if word != lemma and lemma in L.VERB_LEXICON:
        assert L.VERB_LEXICON[lemma] == entries


def test_a_bucket_lists_its_readings_most_specific_first():
    """Section 4 disambiguation: bucket order is table order."""
    L = _L()
    order = [e.verb for e in L.VERB_LEXICON["put"]]
    assert order[0] is Verb.PUT_COUNTERS and set(order[1:]) == {Verb.MOVE}
    gain = [(e.verb, e.mod_kind) for e in L.VERB_LEXICON["gain"]]
    assert gain[0] == (Verb.GAIN_LIFE, None)
    assert gain[1] == (Verb.CONTINUOUS, ModKind.SET_CONTROLLER)
    assert gain[2] == (Verb.CONTINUOUS, ModKind.GRANT_ABILITY)
    assert gain[-1] == (Verb.CONTINUOUS, ModKind.ADD_KEYWORDS)
    lose = [(e.verb, e.mod_kind) for e in L.VERB_LEXICON["lose"]
            if e.verb is not Verb.UNMODELLED]
    assert lose == [(Verb.LOSE_LIFE, None),
                    (Verb.CONTINUOUS, ModKind.REMOVE_ALL_ABILITIES),
                    (Verb.CONTINUOUS, ModKind.REMOVE_KEYWORDS)]
    get = [(e.verb, e.mod_kind) for e in L.VERB_LEXICON["get"]]
    assert get == [(Verb.CONTINUOUS, ModKind.MODIFY_PT),
                   (Verb.CREATE_EMBLEM, None), (Verb.PLAYER_COUNTERS, None)]
    shuffle = [e.verb for e in L.VERB_LEXICON["shuffle"]]
    assert shuffle == [Verb.MOVE, Verb.SHUFFLE]


def test_every_effect_verb_has_a_lexicon_entry_and_one_migration_family():
    """Section 14 lands executors by verb family; every verb the lexicon can
    produce has exactly one family. CREATE_TRIGGER comes from a frame (a
    reflexive or delay opener), never from a lemma; UNMODELLED is not a
    verb a lemma reads."""
    L = _L()
    produced = {e.verb for e in L.LEXICON if e.verb is not Verb.UNMODELLED}
    assert produced == set(Verb) - {Verb.CREATE_TRIGGER, Verb.UNMODELLED}
    for e in L.LEXICON:
        if e.verb is not Verb.UNMODELLED:
            assert e.family is L.VERB_FAMILY[e.verb], e
    assert set(L.VERB_FAMILY) >= produced | {Verb.CREATE_TRIGGER}


def test_actor_only_verbs_have_only_a_player_role():
    """The lexicon's 'player' role is effect_spec's ACTOR_ONLY_VERBS
    (schema invariant 1): those verbs have no object principal."""
    L = _L()
    for e in L.LEXICON:
        assert set(e.roles) <= L.ROLES, e
        if e.verb in ACTOR_ONLY_VERBS:
            assert "player" in e.roles, e
            assert not {"object", "recipient", "stack_object"} & set(e.roles), e
        elif e.verb is not Verb.UNMODELLED:
            assert {"object", "recipient", "stack_object", "filter"} & set(e.roles) \
                or e.verb in (Verb.CONTINUOUS, Verb.KEYWORD_ACTION, Verb.PAY,
                              Verb.CREATE_TOKEN, Verb.LOOK, Verb.REVEAL,
                              Verb.REVEAL_UNTIL, Verb.CHOOSE, Verb.DISCARD,
                              Verb.LOSE_LIFE, Verb.SET_LIFE,
                              Verb.EXCHANGE_LIFE), e


def test_a_continuous_entry_carries_a_mod_kind_hint_and_no_other_entry_does():
    L = _L()
    for e in L.LEXICON:
        assert (e.mod_kind is not None) == (e.verb is Verb.CONTINUOUS), e
        assert e.other_role in L.OTHER_ROLES, e
        assert e.hostile in (True, False, None), e
        assert isinstance(e.owner, str)


def test_damage_names_its_recipient_as_principal_and_its_source_as_the_other_role():
    """Section 4: DAMAGE's principal is the recipient, `other` the source;
    FIGHT's principal is B and `other` is A."""
    d = _verb("~ deals 3 damage to any target")
    assert d.verb is Verb.DAMAGE and "recipient" in d.roles
    assert d.other_role == "source" and d.hostile is True
    f = _verb("target creature you control fights target creature you don't control")
    assert f.verb is Verb.FIGHT and f.other_role == "fighter"


# ── Disambiguation (section 4) ─────────────────────────────────────────

@pytest.mark.parametrize("text,verb", [
    ("put two +1/+1 counters on target creature", Verb.PUT_COUNTERS),
    ("put a +1/+1 counter on each creature you control", Verb.PUT_COUNTERS),
    ("put up to three lore counters on it", Verb.PUT_COUNTERS),
    ("put up to one +1/+1 counter on each creature you control",
     Verb.PUT_COUNTERS),
    ("put it onto the battlefield with a +1/+1 counter on it", Verb.MOVE),
    ("put that card onto the battlefield tapped", Verb.MOVE),
    ("put the rest on the bottom of your library in a random order", Verb.MOVE),
    ("put one of them into your hand", Verb.MOVE),
])
def test_put_reads_counters_before_a_zone_move(text, verb):
    assert _verb(text).verb is verb


@pytest.mark.parametrize("text,verb,mod", [
    ("you gain 3 life", Verb.GAIN_LIFE, None),
    ("you gain life equal to its power", Verb.GAIN_LIFE, None),
    ("you gain that much life", Verb.GAIN_LIFE, None),
    ("gain control of target creature until end of turn", Verb.CONTINUOUS,
     ModKind.SET_CONTROLLER),
    ("target creature gains ⟨q0⟩ until end of turn", Verb.CONTINUOUS,
     ModKind.GRANT_ABILITY),
    ("target creature gains your choice of double strike or lifelink until "
     "end of turn", Verb.CONTINUOUS, ModKind.ADD_KEYWORDS),
    ("target creature gains flying and lifelink until end of turn",
     Verb.CONTINUOUS, ModKind.ADD_KEYWORDS),
    ("each opponent loses 2 life", Verb.LOSE_LIFE, None),
    ("each opponent loses life equal to the number of cards in your hand",
     Verb.LOSE_LIFE, None),
    ("target creature loses all abilities until end of turn", Verb.CONTINUOUS,
     ModKind.REMOVE_ALL_ABILITIES),
    ("target creature loses flying until end of turn", Verb.CONTINUOUS,
     ModKind.REMOVE_KEYWORDS),
    ("target creature gets +2/+2 until end of turn", Verb.CONTINUOUS,
     ModKind.MODIFY_PT),
    ("creatures your opponents control get -1/-1 until end of turn",
     Verb.CONTINUOUS, ModKind.MODIFY_PT),
    ("you get an emblem with ⟨q0⟩", Verb.CREATE_EMBLEM, None),
    ("you get {e}{e}", Verb.PLAYER_COUNTERS, None),
    ("each opponent gets a poison counter", Verb.PLAYER_COUNTERS, None),
])
def test_gain_lose_and_get_read_life_and_counters_before_continuous_predicates(
        text, verb, mod):
    e = _verb(text)
    assert (e.verb, e.mod_kind) == (verb, mod), (text, e)


@pytest.mark.parametrize("text,verb", [
    ("each opponent loses two times x life", Verb.LOSE_LIFE),
    ("each opponent loses twice x life", Verb.LOSE_LIFE),
    ("each player loses a third of their life, rounded up", Verb.LOSE_LIFE),
    ("you gain three times x life", Verb.GAIN_LIFE),
    ("you gain half x life and draw half x cards", Verb.GAIN_LIFE),
])
def test_a_multiplied_or_fractional_life_amount_is_a_life_change(text, verb):
    """CR 119.3: 'lose/gain <amount> life' is a life change whatever the
    printed amount -- a multiple of X or a fraction of a life total too --
    never a keyword change."""
    assert _verb(text).verb is verb, text


@pytest.mark.parametrize("text,mod", [
    ("target creature gains protection from the color of your choice until "
     "end of turn", ModKind.ADD_KEYWORDS),
    ("target creature gains islandwalk until end of turn", ModKind.ADD_KEYWORDS),
    ("target creature gains hexproof from that color", ModKind.ADD_KEYWORDS),
    ("~ loses defender and gains flying", ModKind.REMOVE_KEYWORDS),
    ("creatures your opponents control lose hexproof", ModKind.REMOVE_KEYWORDS),
    ("it's a 0/1 aura and loses all other abilities",
     ModKind.REMOVE_ALL_ABILITIES),
])
def test_gain_and_lose_change_keywords_only_when_they_name_a_cr_702_keyword(
        text, mod):
    e = _verb(text)
    assert e.verb is Verb.CONTINUOUS and e.mod_kind is mod, (text, e)


@pytest.mark.parametrize("text,lemma", [
    ("you lose the flip", "lose"),
    ("if you lose a flip, ~ has no effect", "lose"),
    ("you lose control of that equipment", "lose"),
    ("each player loses all unspent mana", "lose"),
    ("target opponent loses all counters", "lose"),
    ("creatures you control gain that ability until end of turn", "gain"),
    ("~ gains all activated abilities of that card until end of turn", "gain"),
])
def test_a_gain_or_lose_object_that_is_neither_life_nor_a_keyword_is_refused(
        text, lemma):
    """'gain'/'lose' are verb-only words: an object the table does not read
    (a coin flip, control, unspent mana, counters, a borrowed ability) is
    lexicon.no_reading, never guessed as a keyword grant or removal."""
    r = _read(text)
    assert r.value is None, (text, r.value)
    assert r.unmodelled.stage is Stage.CLAUSE
    assert r.unmodelled.detail == "lexicon.no_reading:" + lemma


def test_shuffling_an_object_into_a_library_is_a_zone_move_not_a_library_shuffle():
    """A18: 'shuffle(s) <object> into <library>' is MOVE; only a player
    shuffling their library (or a bare 'then shuffle') is SHUFFLE."""
    for text in ("shuffle ~ into its owner's library",
                 "shuffle ~ into ~'s owner's library",
                 "shuffle target creature into their owners' library",
                 "shuffle that card into that card's owner's library",
                 "each player shuffles their hand and graveyard into their library",
                 "shuffle your graveyard into your library"):
        assert _verb(text).verb is Verb.MOVE, text
    for text in ("shuffle", "then shuffle", "target player shuffles their library",
                 "shuffle your library"):
        assert _verb(text).verb is Verb.SHUFFLE, text


def test_pay_any_amount_of_is_a_pay_with_an_any_number_amount():
    """A19: 'pay any amount of {e}' is PAY whose amount is ANY_NUMBER (chosen
    at resolution through choose_amount), never a literal."""
    text = "then you may pay any amount of {e}"
    r = _read(text)
    assert r.value.verb is Verb.PAY
    assert r.amount == Amount(AmountKind.ANY_NUMBER)
    assert text[slice(*r.span)] == "pay any amount of"
    assert r.rest_text(text) == "then you may {e}"
    r = _read("you may pay {e}{e}{e}")
    assert r.value.verb is Verb.PAY and r.amount is None


def test_your_choice_of_is_a_continuous_grant_with_alternatives():
    """A19: 'gains your choice of X or Y' reads as a keyword grant whose
    options the payload leaf types; the lexicon flags the alternatives."""
    r = _read("target creature gains your choice of double strike or lifelink")
    assert r.value.mod_kind is ModKind.ADD_KEYWORDS
    assert "alternatives" in r.flags


@pytest.mark.parametrize("text,verb", [
    ("your life total becomes 10", Verb.SET_LIFE),
    ("exchange life totals with target player", Verb.EXCHANGE_LIFE),
    ("counter target noncreature spell", Verb.COUNTER),
    ("copy target instant or sorcery spell", Verb.COPY),
    ("change the target of target spell with a single target", Verb.CHANGE_TARGETS),
    ("take an extra turn after this one", Verb.EXTRA_TURN),
    ("end the turn", Verb.END_TURN),
    ("skip your next combat phase", Verb.SKIP),
    ("transform ~", Verb.TRANSFORM),
    ("attach ~ to target creature you control", Verb.ATTACH),
    ("tap target creature", Verb.TAP),
    ("untap all lands you control", Verb.UNTAP),
    ("add {r}{r}{r}", Verb.ADD_MANA),
    ("add one mana of any color", Verb.ADD_MANA),
    ("look at the top three cards of your library", Verb.LOOK),
    ("reveal cards from the top of your library until you reveal a land card",
     Verb.REVEAL_UNTIL),
    ("target player reveals their hand", Verb.REVEAL),
    ("search your library for a basic land card", Verb.SEARCH),
    ("you choose a nonland card from it", Verb.CHOOSE),
    ("you may cast it without paying its mana cost", Verb.CAST_FREE),
    ("create a 1/1 white soldier creature token", Verb.CREATE_TOKEN),
    ("remove a +1/+1 counter from target creature", Verb.REMOVE_COUNTERS),
    ("move all counters from target creature onto ~", Verb.MOVE_COUNTERS),
    ("double the number of +1/+1 counters on target creature", Verb.DOUBLE_COUNTERS),
    ("draw two cards", Verb.DRAW), ("that player discards that card", Verb.DISCARD),
    ("mill three cards", Verb.MILL), ("scry 2", Verb.SCRY), ("surveil 1", Verb.SURVEIL),
    ("return target creature to its owner's hand", Verb.MOVE),
    ("exile target creature", Verb.EXILE),
    ("destroy all creatures", Verb.DESTROY),
    ("each opponent sacrifices a creature of their choice", Verb.SACRIFICE),
])
def test_each_section_four_row_reads_its_verb(text, verb):
    assert _verb(text).verb is verb, text


@pytest.mark.parametrize("text,mod", [
    ("target creature can't block this turn", ModKind.PROHIBIT),
    ("each player can't draw more than one card each turn", ModKind.LIMIT),
    ("~ doesn't untap during your untap step", ModKind.PROHIBIT),
    ("target creature attacks each combat if able", ModKind.REQUIRE),
    ("you may cast sorcery spells as though they had flash", ModKind.PERMIT),
    ("you may play lands from your graveyard", ModKind.PERMIT),
    ("spells you cast cost {1} less to cast", ModKind.COST_DELTA),
    ("prevent all combat damage that would be dealt this turn",
     ModKind.PREVENT_DAMAGE),
    ("switch target creature's power and toughness until end of turn",
     ModKind.SWITCH_PT),
    ("~ has base power and toughness 1/1", ModKind.SET_BASE_PT),
    ("creatures you control have ⟨q0⟩", ModKind.GRANT_ABILITY),
    ("creatures you control have trample", ModKind.ADD_KEYWORDS),
    ("it becomes an angel in addition to its other types", ModKind.ADD_TYPES),
])
def test_continuous_predicates_read_their_mod_kind_hint(text, mod):
    e = _verb(text)
    assert e.verb is Verb.CONTINUOUS and e.mod_kind is mod, (text, e)


@pytest.mark.parametrize("text,mod", [
    ("target creature becomes the color or colors of your choice until end "
     "of turn", ModKind.SET_COLORS),
    ("~ becomes colorless", ModKind.SET_COLORS),
    ("it becomes white until end of turn", ModKind.SET_COLORS),
    ("all creatures become all colors until end of turn", ModKind.SET_COLORS),
    ("~'s base power and toughness each become equal to that creature's power",
     ModKind.SET_BASE_PT),
    ("it becomes a 3/3 elemental creature", ModKind.ADD_TYPES),
])
def test_become_hints_colour_and_base_pt_changes_by_their_layer(text, mod):
    """Section 4: 'becomes the colour(s) / <colour> / colorless' is a layer 5
    colour change (SET_COLORS) and 'base power and toughness become' a layer
    7b setting (SET_BASE_PT); only a type phrase keeps the ADD_TYPES hint."""
    e = _verb(text)
    assert e.verb is Verb.CONTINUOUS and e.mod_kind is mod, (text, e)


def test_becoming_a_copy_is_a_recognised_copy_effect_not_a_type_change():
    """CR 707.2 / 613.1a: 'becomes a copy of' is a layer 1 copy effect, not
    a type change; with no owner yet it is recognised and refused."""
    for text in ("~ becomes a copy of target permanent card in your graveyard",
                 "you may have shapeshifters you control become copies of "
                 "that creature until end of turn"):
        r = _read(text)
        assert r.value is None, (text, r.value)
        assert r.unmodelled.stage is Stage.RECOGNIZED_UNSUPPORTED
        assert r.unmodelled.lemma == "become a copy"


@pytest.mark.parametrize("text", ["it becomes plotted", "~ becomes saddled"])
def test_a_designation_a_permanent_or_card_becomes_is_not_a_type_change(text):
    """Plotted (CR 718), saddled, monstrous and the like are designations,
    not characteristics: 'becomes <designation>' has no continuous reading."""
    r = _read(text)
    assert r.value is None or r.value.mod_kind is not ModKind.ADD_TYPES, (text, r)


# ── Non-verb uses of verb words ────────────────────────────────────────

@pytest.mark.parametrize("text,verb", [
    ("it's put into a graveyard from the battlefield, draw a card", Verb.DRAW),
    ("each creature that is a zombie gets +1/+1", Verb.CONTINUOUS),
    ("return all cards in exile to the battlefield", Verb.MOVE),
    ("create a token that's a copy of target creature", Verb.CREATE_TOKEN),
    ("remove that counter and draw a card", Verb.REMOVE_COUNTERS),
    ("at the beginning of your draw step, draw a card", Verb.DRAW),
    ("~ gets +1/+1 for each +1/+1 counter on it", Verb.CONTINUOUS),
    ("creatures you control have no maximum hand size and gain 1 life",
     Verb.GAIN_LIFE),
])
def test_a_passive_relative_or_noun_use_of_a_verb_word_is_not_the_clause_verb(
        text, verb):
    """A participle after be ('is put'), a verb inside a relative clause
    ('that is'), and noun uses ('in exile', 'a copy of', 'draw step', '+1/+1
    counter') are not the clause's lemma; the first real verb is."""
    assert _verb(text).verb is verb, text


# ── Unmodelled ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,lemma", [
    ("venture into the dungeon", "venture into the dungeon"),
    ("the ring tempts you", "the ring tempts you"),
    ("regenerate target creature", "regenerate"),
    ("target creature phases out", "phase out"),
    ("you win the game", "win the game"),
    ("target player loses the game", "lose the game"),
    ("flip a coin", "flip a coin"),
    ("manifest the top card of your library", "manifest"),
])
def test_a_recognised_unsupported_action_is_unmodelled_with_its_printed_lemma(
        text, lemma):
    r = _read(text)
    assert r.value is None
    assert r.unmodelled.stage is Stage.RECOGNIZED_UNSUPPORTED
    assert r.unmodelled.lemma == lemma
    assert r.unmodelled.detail.startswith("lexicon.unsupported")
    assert r.span == (0, len(text))


def test_a_clause_with_no_lexicon_verb_is_unmodelled_no_lemma_with_the_callers_lemma():
    r = _read("~ blorps target creature")
    assert r.value is None and r.unmodelled.stage is Stage.NO_LEMMA
    assert r.unmodelled.lemma == "" and r.unmodelled.detail == "lexicon.no_lemma"
    r = _read("~ blorps target creature", lemma="blorp")
    assert r.unmodelled.lemma == "blorp"


def test_a_verb_only_word_no_reading_accepts_is_unmodelled_clause_not_skipped():
    """'put' is always a verb: a put whose disambiguator the table lacks is
    refused with its lemma, never read past to a later verb."""
    r = _read("put ~ under target creature, then draw a card")
    assert r.value is None and r.unmodelled.stage is Stage.CLAUSE
    assert r.unmodelled.lemma == "put"
    assert r.unmodelled.detail == "lexicon.no_reading:put"


def test_every_unmodelled_detail_is_lexicon_dot_a_closed_code():
    L = _L()
    for text in ("~ blorps", "put ~ under it", "venture into the dungeon"):
        u = _read(text).unmodelled
        leaf, _, rest = u.detail.partition(".")
        assert leaf == L.LEAF == "lexicon"
        assert rest.split(":")[0] in L.DETAIL_CODES


# ── Keyword actions: one table ─────────────────────────────────────────

def test_keyword_actions_read_through_the_payload_leafs_one_table():
    """The CR 701 keyword actions the lexicon reads are exactly the payload
    leaf's KEYWORD_ACTION_NAMES (typed) and UNSUPPORTED_KEYWORD_ACTIONS
    (recognised-unsupported): one table, two readers."""
    from engine.effect_grammar.sub import payload as P
    L = _L()
    typed = {e.lemma for e in L.LEXICON if e.verb is Verb.KEYWORD_ACTION}
    assert typed == set(P.KEYWORD_ACTION_NAMES)
    unsupported = {e.lemma for e in L.LEXICON if e.verb is Verb.UNMODELLED}
    assert set(P.UNSUPPORTED_KEYWORD_ACTIONS) <= unsupported


def test_a_keyword_action_lemma_composes_with_the_payload_leaf():
    """The spine hands the payload leaf the slot after the consumed lemma and
    the lexicon's printed lemma; the payload re-joins them (section 4)."""
    from engine.effect_grammar.sub import payload as P
    host = "amass zombies 2"
    r = _L().find_verb(host, (0, len(host)))
    assert r.value.verb is Verb.KEYWORD_ACTION and r.value.lemma == "amass"
    (rest,) = r.rest_spans
    p = P.parse_payload(r.value, host, rest, None)
    assert p.value is not None and p.value.name == "amass"
    host = "investigate twice"
    r = _L().find_verb(host, (0, len(host)))
    assert r.value.lemma == "investigate"
    host = "you may goad target creature"
    assert _verb(host).verb is Verb.KEYWORD_ACTION


def test_a_continuous_entry_and_its_verb_span_compose_with_the_payload_leaf():
    from engine.effect_grammar.sub import payload as P
    host = "until end of turn, target creature gets +2/+2 and gains flying"
    r = _L().find_verb(host, (0, len(host)))
    slot = (r.span[0], len(host))
    p = P.parse_payload(r.value, host, slot, None)
    assert p.value.kind is ModKind.MODIFY_PT
    assert host[slice(*p.span)] == "gets +2/+2"


# ── The leaf contract: spans, rest, lemma restriction ──────────────────

def test_the_verb_span_and_the_rest_spans_index_the_host():
    host = "draw a card. target creature gets +1/+1 until end of turn."
    slot = (host.index("target"), len(host) - 1)
    r = _L().find_verb(host, slot)
    assert host[slice(*r.span)] == "gets"
    assert [host[a:b] for a, b in r.rest_spans] == [
        "target creature", "+1/+1 until end of turn"]
    assert all(slot[0] <= a <= b <= slot[1] for a, b in r.rest_spans)


def test_a_multi_word_lemma_consumes_its_printed_words():
    for text, consumed in (("look at the top card of target player's library",
                            "look at"),
                           ("gain control of target creature", "gain"),
                           ("take an extra turn after this one", "take"),
                           ("collect evidence 6", "collect evidence")):
        r = _read(text)
        assert text[slice(*r.span)] == consumed, (text, r)


def test_a_failed_slot_reports_the_whole_trimmed_slot():
    host = "draw a card.  ~ blorps target creature . "
    slot = (host.index(".") + 1, len(host))
    r = _L().find_verb(host, slot)
    assert host[slice(*r.span)] == "~ blorps target creature ."


def test_the_callers_lemma_restricts_the_readings():
    host = "exile target creature and draw a card"
    r = _L().find_verb(host, (0, len(host)), lemma="draw")
    assert r.value.verb is Verb.DRAW
    assert host[slice(*r.span)] == "draw"


@pytest.mark.parametrize("text,pos,expected", [
    ("search your library for a card, put it into your hand, then shuffle",
     "put", Verb.MOVE),
    ("put a +1/+1 counter on target creature, untap it", "untap", Verb.UNTAP),
    ("destroy target artifact, enchantment, or land", "enchantment", None),
    ("exile target creature, its controller gains 2 life", "its", None),
])
def test_the_serial_comma_test_reads_an_inflected_lexicon_verb_after_the_comma(
        text, pos, expected):
    """A13: L3 splits at ', <lemma>' only when the token after the comma is
    an inflected lexicon verb (never a type-list member)."""
    e = _L().verb_at(text, text.index(pos))
    assert (e.verb if e else None) is expected


# ── Loyalty lines (A12) ────────────────────────────────────────────────

@pytest.mark.parametrize("line,amount", [
    ("[+1]: draw a card.", Amount(AmountKind.LITERAL, n=1)),
    ("[0]: draw a card.", Amount(AmountKind.LITERAL, n=0)),
    ("[-3]: ~ deals 3 damage to any target.", Amount(AmountKind.LITERAL, n=-3)),
    ("[+x]: draw x cards.", Amount(AmountKind.X, n=1)),
    ("[-x]: ~ deals x damage to target creature.", Amount(AmountKind.X, n=-1)),
])
def test_a_loyalty_line_cost_is_a_signed_amount_and_x_is_a_signed_x(line, amount):
    """A12: the grammar's loyalty-line pattern is a superset of legacy's
    fixed-cost pattern; [+X]/[-X] lines are LOYALTY lines whose cost is
    Amount(X, n=+-1), X bound from the paid loyalty (CR 107.3, 606.4)."""
    r = _L().parse_loyalty_cost(line)
    assert r.value == amount
    assert line[slice(*r.span)] == line[:line.index(":") + 1]
    assert r.rest_text(line) == line[line.index(":") + 2:]


def test_a_variable_loyalty_line_takes_no_slot_through_the_one_slot_owner():
    from engine.oracle_parser import loyalty_slot_for
    L = _L()
    lines = ["[+1]: a.", "[-x]: b.", "[-2]: c.", "[-7]: d."]
    costs = [L.loyalty_slot_cost(L.parse_loyalty_cost(x).value) for x in lines]
    assert costs == [1, "-x", -2, -7]
    assert [loyalty_slot_for(costs, i) for i in range(4)] == [
        "plus", "", "minus", "ult"]


def test_a_paragraph_that_is_not_a_loyalty_line_has_no_loyalty_cost():
    for text in ("draw a card.", "{t}: add {g}.", "[+1] draw a card."):
        assert _L().parse_loyalty_cost(text) is None, text


# ── Caches, edges, no re-normalisation ─────────────────────────────────

def test_the_lexicon_caches_are_bounded_and_cleared_by_the_package():
    import engine.effect_grammar as grammar
    from engine.effect_grammar.sub import CACHE_SIZE
    L = _L()
    caches = [a for a in vars(L).values()
              if callable(a) and hasattr(a, "cache_info")
              and getattr(a, "__module__", "") == L.__name__]
    assert caches
    assert all(c.cache_info().maxsize == CACHE_SIZE for c in caches)
    _read("draw a card")
    L.parse_loyalty_cost("[+1]: draw a card.")
    assert any(c.cache_info().currsize for c in caches)
    grammar.clear_caches()
    assert all(c.cache_info().currsize == 0 for c in caches)


def _grammar_imports(path: Path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and \
                node.module.startswith("engine.effect_grammar."):
            out.add(node.module.rsplit(".", 1)[1])
        elif isinstance(node, ast.ImportFrom) and node.module in (
                "engine.effect_grammar", "engine.effect_grammar.sub"):
            out.update(a.name for a in node.names if a.name in (
                "payload", "keywords", "dest", "duration", "filter",
                "target", "normalize"))
    return out


def test_the_lexicon_imports_grammar_modules_only_along_its_declared_edges():
    from engine.effect_grammar.sub import LEAF_EDGES
    assert _grammar_imports(LEXICON) - {"sub"} == set(LEAF_EDGES["lexicon"])
    # The graph stays acyclic: nothing the lexicon reads reads the lexicon.
    for name in LEAF_EDGES["lexicon"]:
        assert "lexicon" not in LEAF_EDGES.get(name, frozenset())


def test_the_lexicon_reads_l0_output_without_re_normalising_it():
    src = LEXICON.read_text()
    assert "’" not in src and ".lower()" not in src
    assert "this (?:creature" not in src
