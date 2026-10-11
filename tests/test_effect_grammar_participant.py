"""The participant sub-grammar (design doc 2026-09-29, section 5 "Subjects"
and "Untargeted choices", section 7 reference surface forms; A9 / M1, A23,
A24, A26, A28; E0 step 11).

A participant is who or what a clause acts on or through when it is not a
target: a player set, a reference to an object or player the ability
already knows, or an untargeted object group. It is a closed table over L0
output that runs at load, never at resolution, under the one leaf contract
(`engine.effect_grammar.sub`). These tests pin the rules:

* player sets are `effect_model.Selector` with ``player=None``: the
  dispatcher binds them relative to the resolving controller;
* "~" is ``Ref(SELF)``, "enchanted / equipped <noun>" is ``Ref(ATTACHED)``,
  "~'s owner / controller" is a possessive of SELF (M1), and every other
  pronoun or demonstrative is an `Anaphor` the linker binds (section 7),
  carrying its noun, number and part;
* an untargeted object group is the filter leaf's CardFilter, never a
  target; a counted "target" word belongs to the target leaf;
* the chooser of an untargeted choice is read from its printed phrase
  (CR 115.1, 701.21a);
* an unknown phrase is UNMODELLED over the whole slot, never a broader
  participant.

Synthetic phrases only; no card names.
"""
from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

import pytest

from engine.effect_model import Selector, SelectorKind
from engine.effect_spec import (Amount, AmountKind, CardFilter, Chooser, Ref,
                                RefKind, RefPart, Stage)
from engine.effect_grammar.sub import participant as P

REPO = Path(__file__).resolve().parent.parent
LEAF_PATH = REPO / "engine" / "effect_grammar" / "sub" / "participant.py"

_ONE = Amount(AmountKind.LITERAL, n=1)


def _p(text, **kw):
    return P.parse_participant(text, (0, len(text)), lemma="x", **kw)


def _code(r):
    return r.unmodelled.detail.split(":")[0]


# ── Player sets (section 5 "Subjects") ────────────────────────────────

@pytest.mark.parametrize("text,kind,flags", [
    ("you", SelectorKind.PLAYER, frozenset()),
    ("each opponent", SelectorKind.OPPONENTS, frozenset({P.EACH})),
    ("each of your opponents", SelectorKind.OPPONENTS, frozenset({P.EACH})),
    ("your opponents", SelectorKind.OPPONENTS, frozenset()),
    ("opponents", SelectorKind.OPPONENTS, frozenset()),
    ("each player", SelectorKind.ALL_PLAYERS, frozenset({P.EACH})),
    ("every player", SelectorKind.ALL_PLAYERS, frozenset({P.EACH})),
    ("all players", SelectorKind.ALL_PLAYERS, frozenset()),
    ("players", SelectorKind.ALL_PLAYERS, frozenset()),
])
def test_player_sets_are_selectors_relative_to_the_resolving_controller(text, kind, flags):
    r = _p(text)
    assert r.value == Selector(kind, player=None), r
    assert r.flags == flags | {P.PLAYER}
    assert r.amount is None and r.pending == ()


def test_a_player_set_covers_players_relative_to_whoever_resolves_it():
    """The selector names no seat: binding it to the resolving controller
    gives "you" and "each opponent" their meaning (CR 102.2)."""
    for controller in (0, 1):
        you = dataclasses.replace(_p("you").value, player=controller)
        opp = dataclasses.replace(_p("each opponent").value, player=controller)
        assert you.covers_player(controller) and not you.covers_player(1 - controller)
        assert opp.covers_player(1 - controller) and not opp.covers_player(controller)


@pytest.mark.parametrize("text,kind", [
    ("an opponent", SelectorKind.OPPONENTS),
    ("a player", SelectorKind.ALL_PLAYERS),
    # CR 102.3: in a free-for-all every other player is an opponent.
    ("another player", SelectorKind.OPPONENTS),
])
def test_a_single_unspecified_player_is_a_count_of_one_from_its_player_set(text, kind):
    r = _p(text)
    assert r.value == Selector(kind) and r.amount == _ONE, r


def test_each_other_player_is_the_set_of_opponents_in_a_free_for_all():
    """CR 102.3: in a two-player or free-for-all game every player other
    than you is an opponent."""
    r = _p("each other player")
    assert r.value == Selector(SelectorKind.OPPONENTS)
    assert P.EACH in r.flags


@pytest.mark.parametrize("text,kind", [
    ("any player", SelectorKind.ALL_PLAYERS),
    ("any opponent", SelectorKind.OPPONENTS),
])
def test_any_player_is_every_player_of_the_set_holding_an_option(text, kind):
    r = _p(text)
    assert r.value == Selector(kind) and P.ANY in r.flags and r.amount is None


@pytest.mark.parametrize("text", [
    "each opponent who lost life this turn",
    "each player who controls the most creatures",
    "an opponent with the most life",
    "the player with the highest life total",
])
def test_a_player_set_restricted_by_a_relative_clause_is_unmodelled_not_the_bare_set(text):
    r = _p(text)
    assert r.value is None and _code(r) == "participant.relative_clause", r
    assert r.unmodelled.stage is Stage.REFERENCE


@pytest.mark.parametrize("text", [
    "the monarch", "the active player", "the nonactive player",
    "the attacking player"])
def test_a_game_designation_is_unmodelled_until_the_model_names_it(text):
    r = _p(text)
    assert r.value is None and _code(r) == "participant.designation", r


@pytest.mark.parametrize("text,ref", [
    ("defending player", Ref(RefKind.DEFENDING_PLAYER)),
    ("the defending player", Ref(RefKind.DEFENDING_PLAYER)),
    ("enchanted player", Ref(RefKind.ATTACHED, noun="player")),
    ("the chosen player", Ref(RefKind.CHOSEN, noun="player")),
    ("the chosen opponent", Ref(RefKind.CHOSEN, noun="opponent")),
])
def test_a_player_the_game_or_the_ability_already_fixed_is_a_ref(text, ref):
    r = _p(text)
    assert r.value == ref and P.PLAYER in r.flags, r


# ── Object references (section 7) ─────────────────────────────────────

@pytest.mark.parametrize("text,ref", [
    ("~", Ref(RefKind.SELF)),
    ("enchanted creature", Ref(RefKind.ATTACHED, noun="creature")),
    ("equipped creature", Ref(RefKind.ATTACHED, noun="creature")),
    ("enchanted permanent", Ref(RefKind.ATTACHED, noun="permanent")),
    ("fortified land", Ref(RefKind.ATTACHED, noun="land")),
])
def test_the_source_is_a_self_ref_and_the_object_it_is_attached_to_an_attached_ref(text, ref):
    """CR 301.5 / 303.4: "enchanted" and "equipped" name the object the
    source is attached to; the quantity leaf types the same phrase the
    same way."""
    r = _p(text)
    assert r.value == ref and P.OBJECT in r.flags, r


@pytest.mark.parametrize("text,ref,pending", [
    ("~'s owner", Ref(RefKind.OWNER_OF, of=Ref(RefKind.SELF)), ()),
    ("~'s controller", Ref(RefKind.CONTROLLER_OF, of=Ref(RefKind.SELF)), ()),
    ("enchanted creature's controller",
     Ref(RefKind.CONTROLLER_OF, of=Ref(RefKind.ATTACHED, noun="creature")), ()),
    ("its controller", Ref(RefKind.CONTROLLER_OF), (("ref", "its"),)),
    ("its owner", Ref(RefKind.OWNER_OF), (("ref", "its"),)),
    ("their controller", Ref(RefKind.CONTROLLER_OF), (("ref", "their"),)),
    ("their owners", Ref(RefKind.OWNER_OF), (("ref", "their"),)),
    ("that creature's controller", Ref(RefKind.CONTROLLER_OF),
     (("ref", "that creature"),)),
    ("the exiled card's owner", Ref(RefKind.OWNER_OF),
     (("ref", "the exiled card"),)),
])
def test_the_controller_or_owner_of_a_known_object_is_a_possessive_ref(text, ref, pending):
    """A9 / M1: "~'s owner" keeps the self-reference exact (it never goes
    through pronoun binding); a possessor the linker must bind is left in
    ``pending``."""
    r = _p(text)
    assert r.value == ref and r.pending == pending and P.PLAYER in r.flags, r


@pytest.mark.parametrize("text,per_object", [
    ("their owners", True), ("its controllers", True),
    ("the exiled cards' owners", True), ("those creatures' controllers", True),
    ("its owner", False), ("their controller", False),
    ("the exiled cards' owner", False), ("~'s owner", False)])
def test_a_plural_possessed_role_is_one_player_per_object(text, per_object):
    """"their owners" names the owner of each object of a plural possessor
    -- a set of players, one per object -- so it is flagged ``PER_OBJECT``,
    never collapsed into the single player of "its owner"."""
    r = _p(text)
    assert isinstance(r.value, Ref) and P.PLAYER in r.flags, r
    assert (P.PER_OBJECT in r.flags) is per_object, r


def test_a_possessor_outside_the_reference_table_is_unmodelled_possessor():
    r = _p("the blorp's controller")
    assert r.value is None and _code(r) == "participant.possessor", r


@pytest.mark.parametrize("text,anaphor", [
    ("it", P.Anaphor()),
    ("that creature", P.Anaphor(noun="creature")),
    ("that card", P.Anaphor(noun="card")),
    ("that spell", P.Anaphor(noun="spell")),
    ("the creature", P.Anaphor(noun="creature")),
    ("the token", P.Anaphor(noun="token")),
    ("those creatures", P.Anaphor(noun="creature", plural=True)),
    ("those cards", P.Anaphor(noun="card", plural=True)),
    ("that zombie", P.Anaphor(noun="zombie")),
    ("those goblins", P.Anaphor(noun="goblin", plural=True)),
    ("that creature card", P.Anaphor(noun="creature card")),
    ("itself", P.Anaphor(reflexive=True)),
])
def test_an_object_pronoun_or_demonstrative_is_an_anaphor_with_noun_and_number(text, anaphor):
    """Section 7: compatibility checks noun and number, so the anaphor
    carries both; the linker binds it, never the leaf."""
    r = _p(text)
    assert r.value == dataclasses.replace(anaphor, player=False), r
    assert r.pending == (("ref", text),) and P.OBJECT in r.flags


@pytest.mark.parametrize("text,noun,plural,player", [
    ("that player", "player", False, True),
    ("that opponent", "opponent", False, True),
    ("those players", "player", True, True),
    ("the player", "player", False, True),
    ("he or she", "", False, True),
    ("they", "", None, None),
    ("them", "", None, None),
    ("he", "", False, None),
    ("him", "", False, None),
])
def test_a_player_pronoun_is_an_anaphor_the_linker_binds(text, noun, plural, player):
    """Section 7 "That player": the nearest target player, then a
    single-player subject, then CONTROLLER_OF the nearest object -- the
    linker's rule, so the leaf only records the noun and number. "they"
    and "them" may be players or objects, of either number."""
    r = _p(text)
    assert r.value == P.Anaphor(noun=noun, plural=plural, player=player), r
    key = "player" if player else P.EITHER
    assert r.pending == ((key, text),)


@pytest.mark.parametrize("text", ["they", "them"])
def test_they_and_them_leave_the_number_of_their_antecedent_open(text):
    """Current Oracle wording uses singular "they" / "them" for one player
    ("~ deals 2 damage to them" after "enchanted player"), and plural for
    objects ("sacrifice them"). Section 7 checks number, so the anaphor
    leaves it open (None) instead of fixing it plural."""
    r = _p(text)
    assert r.value.plural is None and r.value.player is None, r


@pytest.mark.parametrize("text", ["they", "them", "he", "she", "him", "her"])
def test_an_anaphor_that_may_be_a_player_or_an_object_is_flagged_as_both(text):
    """The flags say what the participant is: a word that may name a
    player or an object is both, never a player alone, and its pending key
    says the same (``EITHER``)."""
    r = _p(text)
    assert r.value.player is None, r
    assert r.flags == frozenset({P.PLAYER, P.OBJECT}), r
    assert r.pending == ((P.EITHER, text),), r


def test_a_damage_recipient_pronoun_after_an_enchanted_player_head_is_not_a_plural_object():
    host = "whenever enchanted player casts a spell, ~ deals 2 damage to them."
    a = host.index("them")
    r = P.parse_participant(host, (a, len(host)), lemma="deal")
    assert r.value == P.Anaphor(plural=None, player=None), r
    assert P.PLAYER in r.flags and host[slice(*r.span)] == "them"


@pytest.mark.parametrize("text,participle,noun,plural", [
    ("the exiled card", "exiled", "card", False),
    ("the exiled cards", "exiled", "card", True),
    ("the sacrificed creature", "sacrificed", "creature", False),
    ("the revealed card", "revealed", "card", False),
    ("the card exiled this way", "exiled", "card", False),
    ("the cards discarded this way", "discarded", "card", True),
])
def test_a_participle_reference_names_the_action_that_produced_it(text, participle, noun, plural):
    """A26 / section 7: "the exiled card" refers to what an earlier action
    of the ability (or a linked ability) handled; the participle tells the
    linker which."""
    r = _p(text)
    assert r.value == P.Anaphor(noun=noun, plural=plural, player=False,
                                participle=participle), r


def test_the_chosen_object_is_a_chosen_ref():
    r = _p("the chosen creature")
    assert r.value == Ref(RefKind.CHOSEN, noun="creature")


@pytest.mark.parametrize("text,part,n,noun,plural", [
    ("one of them", RefPart.ONE, _ONE, "", False),
    ("two of those cards", RefPart.ONE, Amount(AmountKind.LITERAL, n=2),
     "card", True),
    ("each of them", RefPart.EACH, None, "", True),
    ("each of those creatures", RefPart.EACH, None, "creature", True),
    ("all of them", RefPart.ALL, None, "", True),
    ("the rest", RefPart.REST, None, "", True),
    ("the rest of them", RefPart.REST, None, "", True),
    ("the other", RefPart.OTHER, None, "", False),
    ("up to one of them", RefPart.ONE, Amount(AmountKind.UP_TO, n=1), "", False),
    ("up to two of them", RefPart.ONE, Amount(AmountKind.UP_TO, n=2), "", True),
    ("up to two of those cards", RefPart.ONE, Amount(AmountKind.UP_TO, n=2),
     "card", True),
    ("up to x of them", RefPart.ONE,
     Amount(AmountKind.UP_TO, inner=Amount(AmountKind.X, n=1)), "", True),
    ("any number of them", RefPart.ONE, Amount(AmountKind.ANY_NUMBER), "",
     True),
])
def test_a_partitive_reference_keeps_its_part_and_count(text, part, n, noun, plural):
    """A28: REST and OTHER are set differences over an earlier result,
    computed at resolution; ONE carries its printed count."""
    r = _p(text)
    v = r.value
    assert isinstance(v, P.Anaphor), r
    assert (v.part, v.n, v.noun, v.player, v.plural) == (
        part, n, noun, False, plural)


@pytest.mark.parametrize("text", [
    "that blorp", "those whatsits", "the frobbed card", "it all"])
def test_an_unknown_reference_is_unmodelled_reference_never_a_filter(text):
    r = _p(text)
    assert r.value is None and _code(r) == "participant.reference", r


@pytest.mark.parametrize("text,code", [
    ("any of them", "participant.reference"),
    ("one of them at random", "participant.reference"),
    ("two of those blorps", "participant.reference"),
    ("defending player's", "participant.possessor"),
    ("each opponent's", "participant.possessor"),
    ("~'s", "participant.possessor"),
])
def test_a_reference_form_outside_the_tables_is_refused_by_the_reference_grammar(text, code):
    """A partitive of an earlier result ("... of them", "... of those
    <noun>") and a dangling possessive are reference forms: a gap there is
    the reference grammar's (Stage.REFERENCE), never blamed on the filter
    leaf."""
    r = _p(text)
    assert r.value is None and r.unmodelled.stage is Stage.REFERENCE, r
    assert _code(r) == code, r


def test_this_ability_names_the_ability_not_a_participant():
    """CR 113.1: "this ability" is not a self-form (L0 leaves it), and no
    effect acts on it as a participant."""
    r = _p("this ability")
    assert r.value is None and _code(r) == "participant.ability", r


@pytest.mark.parametrize("text", [
    "~'s power", "its toughness", "that creature's mana value",
    "your life total", "~'s power and toughness"])
def test_a_characteristic_is_not_a_participant(text):
    """A characteristic (CR 208, 119) is the quantity leaf's, so a clause
    whose subject is one ("~'s power is equal to ...") is refused here."""
    r = _p(text)
    assert r.value is None and _code(r) == "participant.characteristic", r


@pytest.mark.parametrize("text", [
    "creatures with power less than ~'s power",
    "each creature with toughness less than its power",
    "creatures with mana value less than that creature's mana value"])
def test_a_group_compared_against_a_characteristic_is_the_filter_leafs(text):
    """Only a slot that is itself a characteristic is refused
    ``characteristic``; a group whose comparison operand is one reaches the
    filter leaf, which types or refuses it with its own code -- the same
    bucket whichever possessive the operand prints."""
    from engine.effect_grammar.sub import filter as F
    r = _p(text)
    f = F.parse_filter(text, (0, len(text)))
    if f.value is None:
        assert r.unmodelled.stage is Stage.FILTER, r
        assert r.unmodelled.detail == f.unmodelled.detail, (r, f)
    else:
        assert r.value == f.value and P.GROUP in r.flags, r


# ── Groups and targets (section 5) ────────────────────────────────────

@pytest.mark.parametrize("text,filt,flags,amount", [
    ("creatures you control",
     CardFilter(types=frozenset({"creature"}), controller="you"),
     frozenset(), None),
    ("each creature", CardFilter(types=frozenset({"creature"})),
     frozenset({"each"}), None),
    ("all nonland permanents", CardFilter(not_types=frozenset({"land"})),
     frozenset({"all"}), None),
    ("a creature", CardFilter(types=frozenset({"creature"})),
     frozenset(), _ONE),
])
def test_untargeted_groups_are_filter_selectors_not_targets(text, filt, flags, amount):
    """Section 5: an object group is the filter leaf's CardFilter (one
    owner of object descriptions), whose `as_selector` is a FILTER
    selector bound by the dispatcher."""
    from engine.effect_grammar.sub import filter as F
    r = _p(text)
    assert dataclasses.replace(r.value, raw="") == filt, r
    assert r.value == F.parse_filter(text, (0, len(text))).value
    assert r.flags == flags | {P.GROUP} and r.amount == amount
    assert r.value.as_selector().kind is SelectorKind.FILTER


def test_a_group_the_filter_leaf_refuses_is_the_filters_refusal_unchanged():
    from engine.effect_grammar.sub import filter as F
    r = _p("creatures with a blorp")
    assert r.value is None and r.unmodelled.stage is Stage.FILTER
    assert r.unmodelled.detail == F.parse_filter(
        "creatures with a blorp").unmodelled.detail == "filter.unparsed:with", r


@pytest.mark.parametrize("text", [
    "target player", "target opponent", "any target",
    "up to one target creature", "another target creature you control",
    "target creature's controller", "each of up to two target creatures"])
def test_a_slot_with_a_counted_target_word_belongs_to_the_target_leaf(text):
    """Section 5: TargetRequirements come only from target_solver through
    the target leaf, so a participant slot that prints a counted "target"
    word is routed there -- never typed as a player set or a filter."""
    r = _p(text)
    assert r.value is None and _code(r) == "participant.targeted", r


@pytest.mark.parametrize("text,stage,detail", [
    # The verb use: an object group the filter leaf reads (and here
    # refuses with its own code), never the target leaf's.
    ("spells that target ~", Stage.FILTER, "filter.unparsed:that"),
    # The noun use: a reference to an earlier target, the linker's.
    ("the target creature", Stage.REFERENCE, "participant.reference:the"),
    ("that target", Stage.REFERENCE, "participant.reference:that"),
])
def test_a_noun_use_of_target_is_not_routed_to_the_target_leaf(text, stage, detail):
    """F11: "spells that target ~" and "the target creature" print no
    counted target word, so the slot goes to the filter leaf or the
    reference tables -- an exact outcome, not merely "not targeted"."""
    from engine.effect_grammar.sub import target as T
    assert T.target_words(text) == ()
    r = _p(text)
    assert r.value is None and r.unmodelled.stage is stage, r
    assert r.unmodelled.detail == detail, r


def test_a_reference_moved_out_of_a_targeted_zone_belongs_to_the_target_leaf():
    """A slot that prints a counted target word anywhere is refused
    ``participant.targeted`` whole; the source-zone recovery never hands a
    counted target word on as rest."""
    for text in ("~ from target opponent's graveyard",
                 "it from target player's graveyard"):
        r = _p(text)
        assert r.value is None and _code(r) == "participant.targeted", r
        assert r.rest_spans == () and r.span == (0, len(text)), r


@pytest.mark.parametrize("text", [
    "each creature and each player", "~ or another creature you control",
    "a permanent or player", "you and each opponent",
    "each opponent and each creature they control"])
def test_a_union_of_participants_is_unmodelled_for_the_clause_split(text):
    """A17: a recipient union is split by L3 into simultaneous siblings
    before any leaf sees it; a union that reaches the leaf is refused, not
    typed as its first member."""
    r = _p(text)
    assert r.value is None and _code(r) == "participant.union", r


# ── The chooser of an untargeted choice (CR 115.1, 701.21a) ───────────

@pytest.mark.parametrize("phrase,chooser", [
    ("of their choice", Chooser.PARTICIPANT),
    ("of his or her choice", Chooser.PARTICIPANT),
    ("of your choice", Chooser.CONTROLLER),
    ("of an opponent's choice", Chooser.OPPONENT),
    ("at random", Chooser.RANDOM),
])
def test_an_untargeted_choice_is_scope_plus_filter_and_names_its_chooser(phrase, chooser):
    """The participant is the scope, the filter plus its count the
    selection, and the printed chooser who picks: "each opponent
    sacrifices a creature of their choice" is chosen by each opponent."""
    host = "each opponent sacrifices a creature %s." % phrase
    subj = P.parse_participant(host, (0, host.index(" sacrifices")))
    assert subj.value == Selector(SelectorKind.OPPONENTS)
    a = host.index("a creature")
    c = P.parse_chooser(host, (a, len(host)), lemma="sacrifice")
    assert c.value is chooser, c
    assert host[slice(*c.span)] == phrase
    (rest,) = c.rest_spans
    assert host[slice(*rest)] == "a creature"
    sel = P.parse_participant(host, rest, lemma="sacrifice")
    assert sel.value.types == frozenset({"creature"}) and sel.amount == _ONE


def test_a_chooser_possessor_never_spans_an_earlier_of_phrase():
    host = "exile two of them of their choice"
    c = P.parse_chooser(host)
    assert c.value is Chooser.PARTICIPANT
    assert host[slice(*c.span)] == "of their choice"


def test_a_choice_that_prints_no_chooser_returns_none():
    assert P.parse_chooser("sacrifices a creature", lemma="sacrifice") is None


def test_a_chooser_the_table_lacks_is_unmodelled_never_a_default_chooser():
    host = "sacrifices a creature of that player's choice"
    c = P.parse_chooser(host, lemma="sacrifice")
    assert c.value is None and c.unmodelled.lemma == "sacrifice"
    assert c.unmodelled.detail.startswith("participant.chooser"), c
    assert host[slice(*c.span)] == "of that player's choice"


# ── The leaf contract ──────────────────────────────────────────────────

def test_spans_index_the_whole_host_and_trailing_punctuation_is_structure():
    host = "then each opponent loses 2 life. ~ deals 1 damage to its controller."
    a = host.index("each")
    r = P.parse_participant(host, (a, host.index(" loses")), lemma="lose")
    assert host[slice(*r.span)] == "each opponent" and r.rest_spans == ()
    a = host.index("its controller")
    r = P.parse_participant(host, (a, len(host)), lemma="deal")
    assert host[slice(*r.span)] == "its controller"
    assert r.rest_spans == ()


@pytest.mark.parametrize("ref_text,zone", [
    ("~", "from your graveyard"), ("it", "from your graveyard"),
    ("that card", "from exile"), ("~", "from your hand")])
def test_a_moved_reference_hands_its_printed_source_zone_on_as_rest(ref_text, zone):
    """Section 4: the source zone of a move comes from the object span,
    read by the destination leaf's zone reader; the reference is still the
    participant."""
    host = "return %s %s to the battlefield." % (ref_text, zone)
    slot = (len("return "), host.index(" to the"))
    r = P.parse_participant(host, slot, lemma="return")
    assert host[slice(*r.span)] == ref_text and r.value is not None, r
    assert r.rest_text(host) == zone


def test_a_group_reads_its_own_zone_through_the_filter_leaf():
    host = "return all creature cards from your graveyard"
    r = P.parse_participant(host, (len("return "), len(host)))
    assert r.value.zone == "graveyard" and r.rest_spans == ()


def test_a_failed_slot_reports_the_whole_trimmed_slot_and_the_callers_lemma():
    host = "  the monarch  "
    r = P.parse_participant(host, (0, len(host)), lemma="draw")
    assert r.span == (2, 13) and r.unmodelled.lemma == "draw"
    assert P.parse_participant("the monarch").unmodelled.lemma == ""


def test_an_empty_slot_is_unmodelled_empty():
    r = P.parse_participant("draw a card", (4, 4))
    assert r.value is None and _code(r) == "participant.empty"


_DETAIL_RE = re.compile(r"^participant\.(?P<code>[a-z_]+)(?::\S+)?$")


@pytest.mark.parametrize("text", [
    "", "target player", "the monarch", "each player who attacked",
    "that blorp", "this ability", "~'s power", "the blorp's owner",
    "you and each opponent"])
def test_every_refusal_detail_is_leaf_dot_code_from_the_closed_list(text):
    u = _p(text).unmodelled
    m = _DETAIL_RE.match(u.detail)
    assert m and m.group("code") in P.DETAIL_CODES, u.detail
    assert u.lemma == "x"


def test_the_participant_leaf_imports_only_its_declared_edges():
    from engine.effect_grammar.sub import LEAF_EDGES
    out = set()
    for node in ast.walk(ast.parse(LEAF_PATH.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("engine.effect_grammar.sub."):
                out.add(node.module.rsplit(".", 1)[1])
            elif node.module == "engine.effect_grammar.sub":
                out.update(a.name for a in node.names
                           if (LEAF_PATH.parent / (a.name + ".py")).exists())
    assert out == set(LEAF_EDGES["participant"]) == {"filter", "target"}


def _acyclic(edges):
    state = {}

    def visit(n):
        if state.get(n) == 1:
            return False
        if state.get(n) == 2:
            return True
        state[n] = 1
        ok = all(visit(m) for m in edges.get(n, ()))
        state[n] = 2
        return ok
    return all(visit(n) for n in edges)


def test_the_leaf_edge_graph_stays_acyclic_with_the_participant_edges():
    from engine.effect_grammar.sub import LEAF_EDGES
    assert "participant" in LEAF_EDGES
    assert _acyclic(LEAF_EDGES)


def test_the_participant_leaf_holds_no_memo_and_keeps_the_contracts_clear_hook():
    """A pool pass repeats almost no participant slot and the relative
    parse is keyed on the whole host, so the leaf memoises nothing; it
    still exposes the contract's clear_caches."""
    caches = [a for a in vars(P).values()
              if callable(a) and hasattr(a, "cache_info")
              and getattr(a, "__module__", "") == P.__name__]
    assert caches == []
    assert callable(P.clear_caches)
    P.clear_caches()


def test_the_participant_leaf_reads_l0_output_and_no_card_or_game_state():
    src = LEAF_PATH.read_text()
    assert "’" not in src and ".lower()" not in src
    assert not re.search(r"this \(\?:creature", src)
    tree = ast.parse(src)
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m and (m.startswith("engine.game") or m == "engine.cards")
                   for m in mods), mods
    assert ".name" not in src
