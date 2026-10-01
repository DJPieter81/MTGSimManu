"""L2 frames and L3 clause splits of the clause grammar (design doc
2026-09-29, section 3 "L2, sentence frames" and "L3, clauses"; section 7
"Links"; A13-A17, A30; E0 step 10).

L2 consumes each sentence's leading and trailing frame phrases
(connectives, sub-ability openers, conditions, instead forms, for-each,
unless-pays, where-X, absorbed riders) and L3 splits the frame body into
sibling clauses. Linking (L5: nesting connective children, binding
pronouns, resolving instead) reads these frames; the assertions here pin
what L2/L3 hand it. Each test names the rule; the texts are printed oracle
wording, and a card name appears only to build the face facts.
"""
from __future__ import annotations

import pytest

from engine.delayed_triggers import DelayedTriggerTiming
from engine.effect_grammar import clauses as CL
from engine.effect_grammar import normalize as N
from engine.effect_grammar import patterns as PT
from engine.effect_grammar import structure as S
from engine.effect_grammar.keywords import keywords702
from engine.effect_spec import (ConditionKind, HostKind, KeywordSpec, Stage,
                                SubAbilityKind, Verb)


def _facts(name="", *, types=("instant",), keywords=(), legendary=False):
    tc = frozenset(types)
    return N.Facts(
        names=N.self_names(name, is_legendary=legendary) if name else (),
        type_class=tc, is_spell=bool({"instant", "sorcery"} & tc),
        is_legendary=legendary, is_planeswalker="planeswalker" in tc,
        keywords702=keywords702(keywords))


def _host(text, *a, **kw):
    hosts = S.parse_face_structure(text, _facts(*a, **kw)).hosts
    return next(h for h in hosts if h.body)


def _frames(text, *a, **kw):
    h = _host(text, *a, **kw)
    return h, CL.frame_host(h)


def _clauses(h, frame):
    return [h.text[a:b] for a, b in (c.span for c in frame.clauses)]


def _matches(text, *a, **kw):
    h = _host(text, *a, **kw)
    return h, PT.match_host(h)


def _specs(fms):
    return [cm.spec for fm in fms for cm in fm.clauses]


# ── L3: serial lists, gapping, type lists, then ────────────────────────

def test_a_serial_comma_list_of_verb_phrases_splits_into_sibling_clauses():
    """A13: a depth-0 ", <lemma>" splits when the token after the comma is
    an inflected lexicon verb and the text before it holds its own verb."""
    h, (f,) = _frames("Search your library for a basic land card, put it "
                      "onto the battlefield tapped, then shuffle.",
                      types=("sorcery",))
    assert _clauses(h, f) == ["search your library for a basic land card",
                              "put it onto the battlefield tapped",
                              "shuffle"]
    assert [c.joiner for c in f.clauses] == ["", "serial", "then"]
    h, fms = _matches("Search your library for a basic land card, put it "
                      "onto the battlefield tapped, then shuffle.",
                      types=("sorcery",))
    assert [s.verb for s in _specs(fms)] == [Verb.SEARCH, Verb.MOVE,
                                             Verb.SHUFFLE]


def test_a_gapped_second_put_or_return_inherits_the_verb():
    """A13: "put two of them into your hand and the rest on the bottom" is
    two MOVE siblings in one group; the second copies the put verb."""
    h, frames = _frames("Look at the top four cards of your library. Put two "
                        "of them into your hand and the rest on the bottom "
                        "of your library in any order.", types=("sorcery",))
    f = frames[1]
    assert _clauses(h, f) == ["put two of them into your hand",
                              "the rest on the bottom of your library in "
                              "any order"]
    first, second = f.clauses
    assert second.gap == "verb"
    assert second.group == first.group is not None
    h, fms = _matches("Look at the top four cards of your library. Put two "
                      "of them into your hand and the rest on the bottom "
                      "of your library in any order.", types=("sorcery",))
    a, b = (cm.spec for cm in fms[1].clauses)
    assert a.verb is b.verb is Verb.MOVE
    assert a.dest.zone == "hand"
    assert (b.dest.zone, b.dest.position) == ("library", "bottom")
    assert "gapped" in b.flags and a.group == b.group is not None


@pytest.mark.parametrize("text", [
    "Destroy target artifact, creature, or enchantment.",
    "Exile target artifact, creature, enchantment, or planeswalker.",
    "Return target artifact, creature, or enchantment card from your "
    "graveyard to your hand.",
])
def test_a_comma_inside_a_type_list_never_splits_a_clause(text):
    h, (f,) = _frames(text, types=("instant",))
    assert len(f.clauses) == 1
    assert _clauses(h, f) == [h.text[f.clauses[0].span[0]:
                                     f.clauses[0].span[1]]]
    assert h.text[slice(*f.clauses[0].span)] == h.text.rstrip(".")


def test_shuffling_an_object_into_a_library_is_a_zone_move_not_a_library_shuffle():
    """A18: "shuffle <object> into <library>" is MOVE to the library with
    position shuffle; "shuffle (your|their) library" is SHUFFLE."""
    m = PT.match_clause("target player shuffles their graveyard into their "
                        "library")
    assert m.spec.verb is Verb.MOVE
    assert (m.spec.dest.zone, m.spec.dest.position) == ("library", "shuffle")
    m = PT.match_clause("shuffle ~ into its owner's library")
    assert m.spec.verb is Verb.MOVE and m.spec.dest.position == "shuffle"
    m = PT.match_clause("then shuffle".split(" ", 1)[1])
    assert m.spec.verb is Verb.SHUFFLE
    m = PT.match_clause("each player shuffles their library")
    assert m.spec.verb is Verb.SHUFFLE


def test_comma_then_is_text_order_not_a_dependency():
    """CR 608.2c: ", then" and a sentence-initial "Then" order the
    instructions; neither gates the later one on the earlier."""
    h, (f,) = _frames("Draw two cards, then discard a card.")
    assert _clauses(h, f) == ["draw two cards", "discard a card"]
    assert [c.joiner for c in f.clauses] == ["", "then"]
    assert f.connective == ""
    h, frames = _frames("Draw a card. Then discard a card.")
    assert frames[1].connective == "then"
    h, fms = _matches("Draw two cards, then discard a card.")
    specs = _specs(fms)
    assert [s.verb for s in specs] == [Verb.DRAW, Verb.DISCARD]
    assert all(s.condition is None and not s.then for s in specs)


def test_may_scope_nests_only_followers_that_depend_on_the_optional_action():
    """L2/L3 half of A29: the optional head and its followers are
    sibling clauses of one frame, the head flagged optional; the
    dependency nesting itself is the linker's (L5)."""
    h, fms = _matches("You may search your library for a basic land card, "
                      "put it onto the battlefield tapped, then shuffle.",
                      types=("sorcery",))
    specs = _specs(fms)
    assert [s.verb for s in specs] == [Verb.SEARCH, Verb.MOVE, Verb.SHUFFLE]
    assert specs[0].optional is True


# ── L2: connectives ────────────────────────────────────────────────────

def test_if_you_do_gates_every_clause_of_its_sentence():
    """A14: the connective is sentence-wide: every clause of the frame is
    under it, not only the first."""
    h, frames = _frames("You may sacrifice a creature. If you do, draw two "
                        "cards and you gain 2 life.")
    f = frames[1]
    assert f.connective == "if_you_do"
    assert _clauses(h, f) == ["draw two cards", "you gain 2 life"]
    assert ("connective", h.text.index("if you do,")) in [
        (k, s[0]) for k, s in f.consumed]


def test_if_you_dont_verb_phrase_is_the_negated_performed_test_of_the_named_action():
    """A14: "If you don't <VP>," names the action whose performance it
    negates; the VP is consumed as a frame token with its lemma."""
    h, frames = _frames(
        "Whenever ~ or another Elemental you control enters, look at the "
        "top card of your library. If it's a land card, you may put it onto "
        "the battlefield tapped. If you don't put the card onto the "
        "battlefield, put it into your hand.", "Risen Reef",
        types=("creature",))
    f = frames[2]
    assert f.connective == "if_you_dont"
    assert h.text[slice(*f.named_vp)] == "put the card onto the battlefield"
    assert f.named_lemma == "put"
    assert _clauses(h, f) == ["put it into your hand"]


def test_if_you_dont_and_otherwise_populate_the_else_branch():
    h, frames = _frames("Reveal the top card of your library. If it's a "
                        "creature card, put it into your hand. Otherwise, "
                        "put it on the bottom of your library.",
                        types=("sorcery",))
    assert frames[1].condition.kind is ConditionKind.OBJECT
    assert frames[2].connective == "otherwise"
    h, frames = _frames("You may discard a card. If you don't, you lose 3 "
                        "life.")
    assert frames[1].connective == "if_you_dont"
    assert frames[1].named_vp is None


def test_when_you_do_opens_a_reflexive_sub_ability_owning_the_rest_of_the_ability_and_its_targets():
    """CR 603.12, A30: "When you do," opens a reflexive sub-ability; its
    target is not the parent's (L5 moves it; L2 marks the opener)."""
    h, frames = _frames(
        "Whenever you attack, you may pay {E}{E}{E}. When you do, put two "
        "+1/+1 counters and a flying counter on target attacking creature.",
        types=("creature",))
    f = frames[1]
    assert f.opener is not None
    assert f.opener.kind is SubAbilityKind.REFLEXIVE
    assert f.opener.intervening_if is None
    assert f.condition is None
    assert len(f.clauses) == 1


def test_when_you_do_if_is_the_intervening_if_of_the_reflexive_sub_ability():
    """CR 603.4, F9: the "if" after "when you do," is the sub-ability
    head's intervening-if, never the frame's condition."""
    h, frames = _frames(
        "Whenever ~ attacks, you may sacrifice an artifact. When you do, if "
        "you control three or more artifacts, draw a card.", "Some Golem",
        types=("creature",))
    f = frames[1]
    assert f.opener.kind is SubAbilityKind.REFLEXIVE
    assert f.opener.intervening_if is not None
    assert f.opener.intervening_if.kind is ConditionKind.STATE
    assert f.condition is None
    assert _clauses(h, f) == ["draw a card"]


def test_a_delayed_sub_ability_absorbs_only_sentences_that_bind_to_its_results():
    """CR 603.7, A30 (L2 half): a delay prefix or suffix opens a delayed
    sub-ability with the phrase table's timing; the delay words are frame
    tokens, never a clause or a duration."""
    h, frames = _frames(
        "Return target legendary creature card from your graveyard to the "
        "battlefield. That creature gains haste. Exile it at the beginning "
        "of the next end step.", types=("instant",))
    assert frames[0].opener is None and frames[1].opener is None
    f = frames[2]
    assert f.opener.kind is SubAbilityKind.DELAYED
    assert f.opener.timing is DelayedTriggerTiming.NEXT_END_STEP
    assert _clauses(h, f) == ["exile it"]
    h, frames = _frames("At the beginning of the next end step, sacrifice "
                        "it.\nTarget creature gets +3/+3.",
                        types=("creature",))
    assert all(f.opener is None or f.opener.kind is SubAbilityKind.DELAYED
               for f in frames)


# ── L2: instead ─────────────────────────────────────────────────────────

def test_instead_replaces_the_earlier_sibling_across_spell_paragraphs():
    """A15 (L2 half): a conditional upgrade sentence is an instead frame
    with its condition, in the merged SPELL host after its antecedent."""
    h = _host("~ deals 2 damage to any target.\nIf you control a Wizard, ~ "
              "deals 3 damage to that permanent or player instead.",
              "Some Bolt", types=("instant",))
    frames = CL.frame_host(h)
    assert [f.instead for f in frames] == [False, True]
    assert frames[1].condition.kind is ConditionKind.STATE


def test_a_leading_instead_verb_phrase_replaces_the_earlier_sibling():
    h, frames = _frames("Draw a card. If you control an artifact, instead "
                        "draw two cards.")
    f = frames[1]
    assert f.instead is True
    assert f.condition is not None
    assert _clauses(h, f) == ["draw two cards"]
    h, frames = _frames("Draw a card. Instead draw two cards if you control "
                        "an artifact.")
    assert frames[1].instead is True and frames[1].condition is not None
    assert _clauses(h, frames[1]) == ["draw two cards"]


def test_an_instead_sibling_inherits_the_arguments_it_does_not_restate():
    """A15 (L4 half): the replacing clause is typed on its own; arguments
    it does not restate are left for L5's inheritance, never guessed."""
    h, fms = _matches("~ deals 2 damage to target creature. If you control "
                      "a Wizard, ~ deals 3 damage to that creature instead.",
                      "Some Bolt", types=("instant",))
    first, second = fms
    assert first.clauses[0].spec.verb is Verb.DAMAGE
    assert second.frame.instead is True
    assert second.clauses[0].spec.amount.n == 3


def test_instead_of_putting_it_into_a_zone_is_a_destination_override_of_the_named_action():
    """CR 701.5a, A15: the countered-this-way sentence is a destination
    override of the counter, never a sibling that replaces it."""
    h, frames = _frames("Counter target spell. If that spell is countered "
                        "this way, exile it instead of putting it into its "
                        "owner's graveyard.")
    f = frames[1]
    assert f.clauses == ()
    assert f.dest_override.zone == "exile"
    assert f.dest_override.instead_of == "graveyard"
    assert not f.instead
    assert CL.uncovered(h, frames) == ""


# ── L3: elliptical recipients and unions ───────────────────────────────

def test_an_elliptical_second_damage_recipient_is_a_simultaneous_sibling():
    h, (f,) = _frames("~ deals 2 damage to target creature and 1 damage to "
                      "target player.", "Some Bolt")
    assert _clauses(h, f) == ["~ deals 2 damage to target creature",
                              "1 damage to target player"]
    assert f.clauses[1].gap == "damage"
    assert f.clauses[0].group == f.clauses[1].group is not None
    h, fms = _matches("~ deals 2 damage to target creature and 1 damage to "
                      "target player.", "Some Bolt")
    a, b = _specs(fms)
    assert a.verb is b.verb is Verb.DAMAGE
    assert (a.amount.n, b.amount.n) == (2, 1)
    assert a.group == b.group is not None
    reqs = [r for cm in fms[0].clauses for _, r, _ in cm.targets]
    assert [sorted(r.types) for r in reqs] == [["creature"], ["player"]]


def test_cant_be_regenerated_is_absorbed_as_a_rider_flag():
    """CR 701.15: "It can't be regenerated." is a rider on the destroy
    frame, never a clause of its own."""
    h, frames = _frames("Destroy target creature. It can't be regenerated.")
    assert len(frames) == 1
    assert ("no_regeneration", True) in frames[0].riders
    h, fms = _matches("Destroy target creature. It can't be regenerated.")
    (spec,) = _specs(fms)
    assert spec.verb is Verb.DESTROY and "no_regeneration" in spec.flags
    assert CL.uncovered(h, CL.frame_host(h)) == ""


def test_whenever_inside_an_effect_body_is_unmodelled_trigger_embedded():
    """A trigger inside resolution text creates a triggered ability the
    model has no host for: the frame is UNMODELLED(TRIGGER_EMBEDDED)."""
    h, (f,) = _frames("Until end of turn, whenever a creature you control "
                      "attacks, it gets +1/+0.")
    assert f.unmodelled and f.unmodelled[0][0].stage is Stage.TRIGGER_EMBEDDED
    h, fms = _matches("Until end of turn, whenever a creature you control "
                      "attacks, it gets +1/+0.")
    assert all(s.verb is Verb.UNMODELLED
               and s.payload.stage is Stage.TRIGGER_EMBEDDED
               for s in _specs(fms))
    assert _specs(fms)
