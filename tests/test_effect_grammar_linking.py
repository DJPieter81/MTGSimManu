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
    # CR 608.2d: the choice is made once, by the head. A follower that
    # only inherits the head's subject is not a second optional action.
    assert [s.optional for s in specs[1:]] == [False, False]
    h, fms = _matches("Its controller may search their library for a basic "
                      "land card, put it onto the battlefield tapped, then "
                      "shuffle.", types=("sorcery",))
    assert [s.optional for s in _specs(fms)] == [True, False, False]


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
    it does not restate are left for L5's inheritance, never guessed. The
    common upgrade restates only the amount: its recipient is inherited,
    never a refusal."""
    h, fms = _matches("~ deals 2 damage to any target. If you control three "
                      "or more artifacts, ~ deals 4 damage instead.",
                      "Some Blast", types=("instant",))
    first, second = fms
    assert first.clauses[0].spec.verb is Verb.DAMAGE
    assert second.frame.instead is True
    (cm,) = second.clauses
    assert cm.spec.verb is Verb.DAMAGE, cm.spec
    assert cm.spec.amount.n == 4
    assert cm.targets == ()
    assert ("principal", "inherited", None) in cm.participants
    # Without "instead" a recipient-less damage clause has nothing to
    # inherit from: still refused.
    cm = PT.match_clause("~ deals 4 damage")
    assert cm.spec.verb is Verb.UNMODELLED


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


# ── L2: where-X, "this way" tests ──────────────────────────────────────

def test_a_clause_after_a_where_x_definition_is_its_own_clause_never_absorbed_into_the_definition():
    """The where-X definition is a frame token that ends where its
    expression ends; a clause printed after it is split like any other,
    never consumed with the definition."""
    h, (f,) = _frames("Target creature gets +X/+0 until end of turn, where X "
                      "is the number of creature cards in your graveyard, "
                      "then draw a card.", types=("instant",))
    assert f.where_x is not None
    assert _clauses(h, f) == ["target creature gets +x/+0 until end of turn",
                              "draw a card"]
    assert f.clauses[1].joiner == "then"
    (wx,) = [h.text[a:b] for k, (a, b) in f.consumed if k == "where_x"]
    assert "draw" not in wx
    assert CL.uncovered(h, (f,)) == ""
    h, fms = _matches("Search your library for up to X basic land cards, "
                      "where X is the number of lands you control, put them "
                      "onto the battlefield tapped, then shuffle.",
                      types=("sorcery",))
    assert [s.verb for s in _specs(fms)][1:] == [Verb.MOVE, Verb.SHUFFLE]


def test_a_would_test_on_a_result_of_this_way_is_a_replacement_never_a_performed_test():
    """CR 614: "if a creature dealt damage this way would die this turn,
    exile it instead" is a replacement effect; it is refused as one, never
    typed as an if-you-do connective with an instead clause."""
    h, frames = _frames("~ deals 3 damage to target creature. If a creature "
                        "dealt damage this way would die this turn, exile it "
                        "instead.", "Some Fire", types=("instant",))
    f = frames[1]
    assert f.connective == ""
    assert f.unmodelled and f.unmodelled[0][0].stage is Stage.REPLACEMENT
    assert CL.uncovered(h, frames) == ""


def test_a_this_way_test_is_a_performed_test_only_when_its_subject_is_a_player():
    """A14: "If you <VP> this way," tests the player's own action, like
    "if you do". An object-qualified result test ("if an insect card was
    milled this way") narrows what was done by a filter the connective
    cannot carry: refused, never a bare performed test."""
    h, frames = _frames("Search your library for a creature card, reveal it, "
                        "put it into your hand, then shuffle. If you "
                        "search your library this way, you gain 2 life.",
                        types=("sorcery",))
    assert frames[1].connective == "if_you_do"
    assert frames[1].named_lemma == "search"
    h, frames = _frames("Mill three cards. If an insect card was milled this "
                        "way, draw a card.", types=("sorcery",))
    f = frames[1]
    assert f.connective == ""
    assert f.unmodelled and f.unmodelled[0][0].stage is Stage.CONDITION
    assert CL.uncovered(h, frames) == ""


def test_a_rider_subject_is_any_object_reference_the_participant_leaf_reads():
    """CR 701.15: the no-regeneration rider's subject is an object
    reference; the participant leaf owns that vocabulary, so a
    demonstrative of any card type is a rider subject."""
    h, frames = _frames("Destroy target artifact. That artifact can't be "
                        "regenerated.")
    assert len(frames) == 1
    assert ("no_regeneration", True) in frames[0].riders


# ════════════════════════════════════════════════════════════════════════
# L5: linking (design section 3 "L5, link", section 7; A14-A16, A23-A30)
# ════════════════════════════════════════════════════════════════════════
# The assertions below read the package entry point `parse_face`, the
# whole L0-L5 pipeline. Texts are printed oracle wording; a card name
# appears only to build the face facts.

from engine.effect_grammar import parse_face  # noqa: E402
from engine.effect_spec import (EffectSpec, HostKind, Ref,  # noqa: E402,F811
                                RefKind, RefPart, validate_card_effects)


def _linked(text, *a, **kw):
    """The hosts of one face, every spec checked against the schema."""
    from engine.effect_spec import CardEffects
    hosts = parse_face(text, _facts(*a, **kw))
    assert validate_card_effects(CardEffects.of((hosts,))) is None
    return hosts


def _effect_host(text, *a, **kw):
    return next(h for h in _linked(text, *a, **kw) if h.specs or h.modes)


def _sub(spec):
    assert spec.verb is Verb.CREATE_TRIGGER, spec
    return spec.payload


def _refusal(spec):
    assert spec.verb is Verb.UNMODELLED, spec
    return spec.payload


# ── Sub-abilities (A30, M8) ────────────────────────────────────────────

def test_a_reflexive_sub_ability_owns_the_rest_of_its_ability_and_its_targets():
    """CR 603.12, A30: "When you do" opens a reflexive triggered ability in
    the performed action's ``then``; it owns the rest of the ability and
    its own targets (chosen when it triggers), the parent none of them."""
    h = _effect_host("Whenever you attack, you may pay {E}{E}{E}. When you "
                     "do, put a +1/+1 counter on target creature. It gains "
                     "flying until end of turn.", types=("creature",))
    (pay,) = h.specs
    assert pay.verb is Verb.PAY and pay.optional
    assert h.targets == ()
    (ct,) = pay.then
    sub = _sub(ct)
    assert sub.kind is SubAbilityKind.REFLEXIVE and sub.timing is None
    assert "reflexive" in ct.flags
    put, grant = sub.host.specs
    assert len(sub.host.targets) == 1
    assert put.verb is Verb.PUT_COUNTERS and put.target_slot == 0
    assert put.target is sub.host.targets[0]
    assert grant.verb is Verb.CONTINUOUS
    assert grant.ref == Ref(RefKind.TARGET, 0)
    assert pay.seq < ct.seq < put.seq < grant.seq


def test_a_reflexive_intervening_if_is_the_sub_ability_heads_condition():
    """CR 603.4, F9: "when you do, if C," is the sub-ability head's
    intervening-if, never a condition on its specs."""
    h = _effect_host("Whenever ~ attacks, you may sacrifice an artifact. When "
                     "you do, if you control three or more artifacts, draw a "
                     "card.", "Some Golem", types=("creature",))
    sub = _sub(h.specs[0].then[0])
    assert sub.host.trigger.intervening_if.kind is ConditionKind.STATE
    (draw,) = sub.host.specs
    assert draw.verb is Verb.DRAW and draw.condition is None


def test_a_reflexive_sub_ability_stops_at_the_end_of_its_mode():
    """M8, CR 700.2: each mode is its own instruction, so a reflexive
    sub-ability opened in one mode never absorbs the next mode."""
    h = _effect_host("Choose one —\n• You may sacrifice a creature. When you "
                     "do, destroy target creature.\n• Draw a card.",
                     types=("sorcery",))
    m0, m1 = h.modes
    sub = _sub(m0.specs[0].then[0])
    assert [s.verb for s in sub.host.specs] == [Verb.DESTROY]
    assert m0.targets == ()
    assert [t.mode_group for t in sub.host.targets] == [1]
    assert [s.verb for s in m1.specs] == [Verb.DRAW]


def test_a_delayed_sub_ability_absorbs_only_sentences_that_bind_to_its_results_and_reads_its_parent_by_result():
    """CR 603.7, A30, A34: a delayed sub-ability holds its own sentence and
    a later sentence bound to its results; an independent later sentence
    stays in the parent. A reference from the delayed host to the parent's
    moved object is the parent spec's RESULT (a snapshot), never the
    parent's target slot."""
    h = _effect_host("Return target legendary creature card from your "
                     "graveyard to the battlefield. That creature gains "
                     "haste. Exile it at the beginning of the next end step.")
    ret, haste, ct = h.specs
    assert ret.verb is Verb.MOVE and ret.target_slot == 0
    assert haste.ref == Ref(RefKind.RESULT, ret.seq)
    sub = _sub(ct)
    assert sub.kind is SubAbilityKind.DELAYED
    assert sub.timing is DelayedTriggerTiming.NEXT_END_STEP
    (exile,) = sub.host.specs
    assert exile.verb is Verb.EXILE and exile.ref == Ref(RefKind.RESULT, ret.seq)
    assert sub.host.targets == ()
    h = _effect_host("Exile up to one target creature. At the beginning of "
                     "the next end step, return that card to the battlefield "
                     "under its owner's control. Put a +1/+1 counter on it.")
    exile, ct = h.specs
    ret, put = _sub(ct).host.specs
    assert put.verb is Verb.PUT_COUNTERS and put.ref == Ref(RefKind.RESULT, ret.seq)
    h = _effect_host("Exile target creature. Return it to the battlefield "
                     "under its owner's control at the beginning of the next "
                     "end step. You gain 2 life.")
    assert [s.verb for s in h.specs] == [Verb.EXILE, Verb.CREATE_TRIGGER,
                                         Verb.GAIN_LIFE]


def test_a_delay_paragraph_of_a_spell_holds_the_connective_bound_to_its_payment():
    """CR 603.7, A5: a spell's delay-prefixed paragraph is a delayed
    sub-ability, and an "if you don't" bound to its payment is in it."""
    h = _effect_host("Search your library for a green creature card, reveal "
                     "it, put it into your hand, then shuffle.\nAt the "
                     "beginning of your next upkeep, pay {2}{G}{G}. If you "
                     "don't, you lose the game.", "Some Pact")
    ct = h.specs[-1]
    sub = _sub(ct)
    assert sub.timing is DelayedTriggerTiming.YOUR_NEXT_UPKEEP
    (pay,) = sub.host.specs
    assert pay.verb is Verb.PAY
    (lose,) = pay.otherwise
    assert _refusal(lose).stage is Stage.RECOGNIZED_UNSUPPORTED


# ── Connectives and may-scope (A14, A29) ───────────────────────────────

def test_if_you_do_nests_every_clause_of_its_sentence_under_the_performed_action():
    """A14: "If you do" gates every clause of its sentence on the previous
    action; each nested spec is flagged ``if_you_do``."""
    h = _effect_host("You may sacrifice a creature. If you do, draw two cards "
                     "and you gain 2 life.")
    (sac,) = h.specs
    assert sac.verb is Verb.SACRIFICE and sac.optional
    assert [s.verb for s in sac.then] == [Verb.DRAW, Verb.GAIN_LIFE]
    assert all("if_you_do" in s.flags for s in sac.then)


def test_if_you_dont_and_otherwise_nest_into_the_else_branch_of_the_named_action():
    """A14: "If you don't <VP>" names the action whose declining it tests
    (by its lemma); "If you don't" and "Otherwise" read the previous one."""
    h = _effect_host(
        "Whenever ~ or another Elemental you control enters, look at the "
        "top card of your library. If it's a land card, you may put it onto "
        "the battlefield tapped. If you don't put the card onto the "
        "battlefield, put it into your hand.", "Risen Reef",
        types=("creature",))
    look, put = h.specs
    assert put.verb is Verb.MOVE and put.optional
    assert put.ref == Ref(RefKind.RESULT, look.seq)
    assert put.condition.ref == Ref(RefKind.RESULT, look.seq)       # rule 0
    (to_hand,) = put.otherwise
    assert to_hand.dest.zone == "hand"
    h = _effect_host("You may discard a card. If you don't, you lose 3 life.")
    (discard,) = h.specs
    assert [s.verb for s in discard.otherwise] == [Verb.LOSE_LIFE]
    h = _effect_host("Exile target card from a graveyard. If it was a "
                     "creature card, you gain 3 life. Otherwise, you draw a "
                     "card.")
    exile, gain = h.specs
    assert gain.condition.ref == Ref(RefKind.RESULT, exile.seq)
    assert [s.verb for s in gain.otherwise] == [Verb.DRAW]


def test_a_declined_optional_action_skips_only_the_followers_that_depend_on_it():
    """A29, CR 608.2d: in an optional head's sentence, a follower bound to
    the head's result, or shuffling the library it searched, is under the
    head (flag ``may_scope``); a REST of an earlier result is a sibling."""
    h = _effect_host("Exile target creature. Its controller may search their "
                     "library for a basic land card, put that card onto the "
                     "battlefield tapped, then shuffle.")
    exile, search = h.specs
    assert search.verb is Verb.SEARCH and search.optional
    put, shuffle = search.then
    assert put.verb is Verb.MOVE and put.ref == Ref(RefKind.RESULT, search.seq)
    assert shuffle.verb is Verb.SHUFFLE
    assert {"may_scope"} <= put.flags and {"may_scope"} <= shuffle.flags
    # The followers inherit the head's actor as bound, never a fresh read.
    assert put.actor == shuffle.actor == search.actor
    h = _effect_host("Look at the top four cards of your library. You may "
                     "reveal a creature card from among them and put it into "
                     "your hand. Put the rest on the bottom of your library "
                     "in a random order.", types=("sorcery",))
    look, reveal, rest = h.specs
    assert reveal.optional and [s.verb for s in reveal.then] == [Verb.MOVE]
    assert rest.ref == Ref(RefKind.RESULT, look.seq, part=RefPart.REST)


# ── Instead (A15, G9) ──────────────────────────────────────────────────

def test_an_instead_sibling_replaces_the_earlier_spec_and_inherits_what_it_does_not_restate():
    """A15, G9: an instead clause ``replaces`` the earlier spec of its verb;
    an argument it does not restate is the replaced spec's (the same
    target slot), never a new requirement."""
    h = _effect_host("~ deals 2 damage to any target. If you control three "
                     "or more artifacts, ~ deals 4 damage instead.",
                     "Some Blast")
    base, upgrade = h.specs
    assert upgrade.replaces == (base.seq,)
    assert len(h.targets) == 1
    assert upgrade.target is base.target and upgrade.target_slot == 0
    h = _effect_host("Draw a card. If you control an artifact, instead draw "
                     "two cards.")
    one, two = h.specs
    assert two.replaces == (one.seq,) and two.amount.n == 2
    h = _effect_host("~ deals 2 damage to target creature.\nIf you control "
                     "a Wizard, ~ deals 3 damage to that creature instead.",
                     "Some Bolt")
    base, upgrade = h.specs
    assert upgrade.replaces == (base.seq,)
    assert upgrade.ref == Ref(RefKind.TARGET, 0)


def test_an_instead_clause_whose_same_verb_antecedent_is_refused_is_unmodelled_never_rewired():
    """A15, section 7: an instead clause replaces the nearest earlier spec
    with its verb. When that spec is refused, what it replaces is unknown:
    the clause is UNMODELLED(REFERENCE, no_antecedent), never wired to the
    nearest spec of another verb. Its siblings with a typed same-verb
    antecedent still replace it."""
    h = _effect_host("Reveal a card from your hand. Search your library for "
                     "a card with the same name as that card, reveal it, put "
                     "it into your hand, then shuffle.\nHellbent — If you "
                     "have no cards in hand, instead search your library for "
                     "a card, put it into your hand, then shuffle.",
                     types=("sorcery",))
    specs = list(h.specs)
    first_search = next(s for s in specs if "same name" in s.raw)
    assert first_search.verb is Verb.UNMODELLED
    later = specs[specs.index(first_search) + 1:]
    put, shuffle = later[0], later[1]
    search2, put2, shuffle2 = later[2:5]
    assert (_refusal(search2).stage, _refusal(search2).detail) == (
        Stage.REFERENCE, "link.no_antecedent")
    assert search2.replaces == ()
    assert put2.replaces == (put.seq,) and shuffle2.replaces == (shuffle.seq,)


def test_a_would_replacement_clause_never_replaces_an_earlier_sibling():
    """Section 7: a leading "if ... would ..." is a REPLACEMENT refusal (a
    replacement effect, CR 614), not an instead sibling: it carries no
    ``replaces``."""
    h = _effect_host("Some Chandra deals 3 damage to target creature or "
                     "planeswalker. If a permanent dealt damage this way "
                     "would die this turn, exile it instead.", "Some Chandra",
                     types=("sorcery",))
    dmg, rep = h.specs
    assert _refusal(rep).stage is Stage.REPLACEMENT
    assert rep.replaces == ()


def test_an_instead_clause_with_no_same_verb_antecedent_replaces_the_nearest_spec():
    """A15: "deals 2 damage to target creature. If ..., destroy that
    creature instead" -- with no earlier spec of its verb at all, the
    instead clause replaces the nearest earlier spec."""
    h = _effect_host("Some Temper deals 2 damage to target creature. If you "
                     "control a black permanent, destroy that creature "
                     "instead.", "Some Temper")
    dmg, destroy = h.specs
    assert destroy.verb is Verb.DESTROY and destroy.replaces == (dmg.seq,)


def test_one_instead_clause_replaces_every_simultaneous_sibling_of_a_split_group():
    """Section 7 Links, A15: "exile target creature and target artifact" is
    one simultaneous group of two siblings; an instead clause of that verb
    replaces the whole group, never only its first or last sibling."""
    h = _effect_host("Exile target creature and target artifact. If you "
                     "control a Wizard, exile target enchantment instead.")
    a, b, alt = h.specs
    assert a.group == b.group is not None
    assert alt.replaces == (a.seq, b.seq)


def test_a_restated_instead_target_is_an_alternative_target_slot():
    """G9: an instead clause that prints a target of its own records the
    pair (replaced slot, its slot) in ``target_alts``."""
    h = _effect_host("Return target creature an opponent controls to its "
                     "owner's hand. If you control a Wizard, instead return "
                     "target nonland permanent an opponent controls to its "
                     "owner's hand.")
    base, alt = h.specs
    assert alt.replaces == (base.seq,)
    assert len(h.targets) == 2 and h.target_alts == ((0, 1),)


def test_instead_of_putting_it_into_a_zone_folds_into_the_named_actions_destination():
    """CR 701.5a, A15: the countered-this-way sentence is a destination
    override of the counter, never a spec of its own."""
    h = _effect_host("Counter target noncreature spell. If that spell is "
                     "countered this way, exile it instead of putting it "
                     "into its owner's graveyard.")
    (counter,) = h.specs
    assert counter.verb is Verb.COUNTER
    assert (counter.dest.zone, counter.dest.instead_of) == ("exile",
                                                            "graveyard")
    assert "dest_override" in counter.flags


# ── Pronouns and references (section 7; A23-A28) ───────────────────────

def test_a_pronoun_in_a_specs_own_condition_binds_to_that_specs_principal():
    """Rule 0 (A23): "if it has ..." and "unless its controller pays" on a
    spec are about that spec's own principal."""
    h = _effect_host("Destroy target creature if it has mana value 2 or "
                     "less.")
    (d,) = h.specs
    assert d.condition.kind is ConditionKind.OBJECT
    assert d.condition.ref == Ref(RefKind.TARGET, 0)
    h = _effect_host("Counter target noncreature spell unless its controller "
                     "pays {2}.")
    (c,) = h.specs
    assert c.condition.kind is ConditionKind.UNLESS
    assert c.condition.ref == Ref(RefKind.CONTROLLER_OF,
                                  of=Ref(RefKind.TARGET, 0))


def test_a_pronoun_in_a_quantified_subjects_condition_binds_to_each_member():
    """A23: a condition on a quantified subject's pronoun is evaluated per
    member: ``Ref(MEMBER)``."""
    for text, types in (("Each creature you control gets +1/+0 until end of "
                         "turn if it's red.", ("sorcery",)),
                        ("Each creature you control has trample as long as "
                         "it's red.", ("enchantment",))):
        (s,) = _effect_host(text, types=types).specs
        assert s.condition.ref == Ref(RefKind.MEMBER), text


def test_every_earlier_participant_mention_is_a_pronoun_antecedent():
    """A24: the source named as a principal, an attached object named as a
    principal, and a condition's subject are all antecedents of a later
    "it" -- a prior mention wins over the trigger head (M2)."""
    h = _effect_host("Whenever ~ or another artifact you control enters, put "
                     "a +1/+1 counter on ~. It can't be blocked this turn.",
                     "Some Cannon", types=("artifact", "creature"))
    put, prohibit = h.specs
    assert prohibit.ref == Ref(RefKind.SELF)
    h = _effect_host("Whenever a creature dies, put a +1/+1 counter on "
                     "equipped creature. If equipped creature is a Vampire, "
                     "put two +1/+1 counters on it instead.",
                     types=("artifact",))
    one, two = h.specs
    assert two.ref == Ref(RefKind.ATTACHED, noun="creature")
    assert two.replaces == (one.seq,)
    h = _effect_host("If equipped creature is a Vampire, you gain 1 life. Put "
                     "a +1/+1 counter on it.", types=("artifact",))
    gain, put = h.specs
    assert put.ref == gain.condition.ref == Ref(RefKind.ATTACHED,
                                                noun="creature")


def test_a_pronoun_binds_to_the_nearest_compatible_prior_object():
    """Section 7: an object pronoun skips a nearer player mention."""
    h = _effect_host("Tap target creature. Target player draws a card. Put a "
                     "+1/+1 counter on it.", types=("sorcery",))
    tap, draw, put = h.specs
    assert draw.actor == Ref(RefKind.TARGET, 1)
    assert put.ref == Ref(RefKind.TARGET, 0)


def test_after_a_zone_change_a_reference_to_the_moved_object_binds_to_the_result():
    """CR 400.7, A25: an object that changed zones is a new object; a later
    reference names the moving spec's RESULT, not the old target."""
    h = _effect_host("Exile target creature. Return it to the battlefield "
                     "under its owner's control.")
    exile, ret = h.specs
    assert ret.ref == Ref(RefKind.RESULT, exile.seq)


def test_a_mixed_self_or_other_trigger_head_binds_it_to_the_event_object_only_without_a_prior_mention():
    """M2: "~ or another <noun>" heads give the event object, which is the
    source when the source triggered -- but only when no earlier mention
    in the body is nearer."""
    h = _effect_host("Whenever ~ or another creature you control enters, it "
                     "gains haste until end of turn.", "Some Herald",
                     types=("creature",))
    (s,) = h.specs
    assert s.ref == Ref(RefKind.EVENT_OBJECT)
    h = _effect_host("Whenever ~ or another artifact you control enters, put "
                     "a +1/+1 counter on ~. It can't be blocked this turn.",
                     "Some Cannon", types=("artifact", "creature"))
    assert h.specs[1].ref == Ref(RefKind.SELF)


def test_a_pronoun_in_a_trigger_body_binds_to_the_event_object_or_the_source_by_head():
    """Rule 3: an other-object head gives EVENT_OBJECT, a self head SELF,
    a player head EVENT_PLAYER."""
    (s,) = _effect_host("Whenever another creature you control enters, it "
                        "gains haste until end of turn.",
                        types=("enchantment",)).specs
    assert s.ref == Ref(RefKind.EVENT_OBJECT)
    (s,) = _effect_host("When ~ enters, it deals 1 damage to any target.",
                        "Some Elemental", types=("creature",)).specs
    assert s.other == Ref(RefKind.SELF)
    (s,) = _effect_host("Whenever an opponent casts a spell, that player "
                        "loses 1 life.", types=("enchantment",)).specs
    assert s.actor == Ref(RefKind.EVENT_PLAYER)


def test_an_ambiguous_or_unbound_pronoun_is_unmodelled_never_guessed():
    """Section 7 rule 4: no antecedent, or two equally near (simultaneous
    siblings), is UNMODELLED(REFERENCE) for that clause only."""
    (s,) = _effect_host("Return it to its owner's hand.").specs
    um = _refusal(s)
    assert (um.stage, um.detail) == (Stage.REFERENCE, "link.unbound")
    h = _effect_host("Exile target creature and target artifact. Return it "
                     "to the battlefield under its owner's control.",
                     types=("sorcery",))
    a, b, ret = h.specs
    # Two principal requirements are two simultaneous siblings.
    assert a.verb is b.verb is Verb.EXILE and a.group == b.group is not None
    assert (a.target_slot, b.target_slot) == (0, 1)
    assert _refusal(ret).detail == "link.ambiguous"


def test_a_player_reference_never_binds_to_the_result_of_a_refused_clause():
    """Section 7 "That player": a player reference binds to the nearest
    player mention. A refused clause's result is an object set of unknown
    kind, never a player, so "that player" skips it for the target player
    while "that card" may still name it."""
    h = _effect_host("Target player reveals their hand. You choose a nonland "
                     "card from it. That player discards that card.",
                     types=("sorcery",))
    discard = h.specs[-1]
    assert discard.verb is Verb.DISCARD
    assert discard.actor == Ref(RefKind.TARGET, 0)


def test_a_number_only_actor_pronoun_names_the_nearest_player():
    """Section 7: "they" as a clause's actor names the nearest player who
    could perform it -- a targeted player, or each player of a multi-player
    subject (CR 101.4) -- never the object an earlier clause produced."""
    from engine.effect_model import Selector, SelectorKind
    h = _effect_host("Target opponent sacrifices a creature. If they can't, "
                     "they lose 2 life.", types=("sorcery",))
    (sac,) = h.specs
    (lose,) = sac.otherwise
    assert lose.verb is Verb.LOSE_LIFE and lose.actor == Ref(RefKind.TARGET, 0)
    h = _effect_host("Each opponent may discard a card. If they don't, they "
                     "lose 3 life.", types=("sorcery",))
    (discard,) = h.specs
    (lose,) = discard.otherwise
    assert isinstance(lose.actor, Selector)
    assert lose.actor.kind is SelectorKind.OPPONENTS


def test_a_gapped_follower_of_a_refused_multi_player_choice_reads_its_result_per_player():
    """CR 101.4, A28: "each opponent chooses ..., then sacrifices the rest"
    -- the follower's elided subject is the chooser's, so each opponent
    sacrifices the rest of their own choice even when the choice clause is
    refused."""
    h = _effect_host("Each opponent chooses an artifact, a creature, an "
                     "enchantment, and a planeswalker from among the nonland "
                     "permanents they control, then sacrifices the rest.",
                     types=("sorcery",))
    sac = h.specs[-1]
    assert sac.verb is Verb.SACRIFICE
    assert sac.ref.part is RefPart.REST and sac.ref.per_actor


def test_the_exiled_card_binds_to_an_exile_in_the_same_ability_before_a_linked_ability():
    """A26, CR 607: "the exiled card" is the RESULT of an EXILE earlier in
    the same ability; with none, it names the card a linked ability
    exiled."""
    h = _effect_host("Exile target creature. Return the exiled card to the "
                     "battlefield under its owner's control.")
    exile, ret = h.specs
    assert ret.ref == Ref(RefKind.RESULT, exile.seq)
    (ret,) = _effect_host("{T}: Return the exiled card to the battlefield "
                          "under its owner's control.",
                          types=("artifact",)).specs
    assert ret.ref.kind is RefKind.LINKED


def test_the_source_is_read_with_last_known_information_after_a_cost_or_leave_event():
    """A27, CR 608.2h: an ability whose cost sacrifices its source, or whose
    head is the source dying, reads the source as it last existed."""
    (draw,) = _effect_host("{2}, Sacrifice this artifact: Draw cards equal "
                           "to the number of charge counters on this "
                           "artifact.", "Some Orb", types=("artifact",)).specs
    assert draw.amount.quantity.ref == Ref(RefKind.SELF, lki=True)
    (dmg,) = _effect_host("When ~ dies, it deals 2 damage to any target.",
                          "Some Imp", types=("creature",)).specs
    assert dmg.other == Ref(RefKind.SELF, lki=True)
    (dmg,) = _effect_host("When ~ enters, it deals 2 damage to any target.",
                          "Some Imp", types=("creature",)).specs
    assert dmg.other == Ref(RefKind.SELF)


def test_its_controller_is_the_controller_of_the_antecedent_with_last_known_information():
    """CR 608.2h: "its controller" after the object was exiled or
    destroyed is the controller of that object as it last existed."""
    for verb in ("Exile", "Destroy"):
        h = _effect_host("%s target creature. Its controller may search their "
                         "library for a basic land card, put it onto the "
                         "battlefield tapped, then shuffle." % verb)
        assert h.specs[1].actor == Ref(
            RefKind.CONTROLLER_OF, of=Ref(RefKind.TARGET, 0, lki=True)), verb


def test_rest_is_the_result_minus_every_later_consumer_including_instead_siblings():
    """A28: "the rest" names the whole earlier result as a REST part --
    the set difference is taken at resolution, after every consumer,
    the instead sibling included, has run; it never names a selection."""
    h = _effect_host("Look at the top three cards of your library. Put one of "
                     "them into your hand. If this spell was kicked, put two "
                     "of them into your hand instead. Put the rest on the "
                     "bottom of your library in a random order.",
                     types=("instant",), keywords=("Kicker",))
    look, one, two, rest = h.specs
    assert two.replaces == (one.seq,)
    assert one.ref.index == two.ref.index == look.seq
    assert rest.ref == Ref(RefKind.RESULT, look.seq, part=RefPart.REST)


def test_this_way_and_that_many_bind_to_the_result_of_the_named_prior_action():
    """A16, A28: "that many" and "<noun> discarded this way" read the
    result of the named earlier action."""
    h = _effect_host("Sacrifice any number of lands. Search your library for "
                     "up to that many land cards, put them onto the "
                     "battlefield tapped, then shuffle.", types=("sorcery",))
    sac, search = h.specs[:2]
    assert search.amount.inner.ref == Ref(RefKind.RESULT, sac.seq)
    h = _effect_host("When ~ enters, discard two cards, then draw two cards. "
                     "For each nonland card discarded this way, create a 1/1 "
                     "red Elemental creature token.", "Some Pyromancer",
                     types=("creature",))
    discard, draw, create = h.specs
    q = create.amount.quantity
    assert create.amount.kind.name == "FOR_EACH"
    assert q.kind.name == "RESULT_SIZE" and q.ref == Ref(RefKind.RESULT,
                                                         discard.seq)


def test_results_of_a_multi_player_actor_bind_per_player():
    """CR 101.4, A28: when each player acts and then reads "them", each
    player reads their own result (``per_actor``)."""
    h = _effect_host("Each player mills two cards, then returns them to their "
                     "hand.", types=("sorcery",))
    mill, ret = h.specs
    assert ret.ref == Ref(RefKind.RESULT, mill.seq, per_actor=True)
    h = _effect_host("Each player sacrifices a creature, then returns the "
                     "sacrificed creature to the battlefield.",
                     types=("sorcery",))
    sac, ret = h.specs
    assert ret.ref.per_actor and ret.ref.index == sac.seq


# ── Granted hosts and the face memo ────────────────────────────────────

def test_a_quoted_ability_is_parsed_as_a_granted_host_of_its_recipient():
    """CR 113.1a, A10: a token's quoted ability is an ability of the token,
    parsed as hosts of its own (``TokenSpec.granted``); "this token"
    inside it is the recipient."""
    (create,) = _effect_host('Create a 0/0 colorless Construct artifact '
                             'creature token with "This token gets +1/+1 for '
                             'each artifact you control."',
                             types=("sorcery",)).specs
    (granted,) = create.payload.granted
    assert granted.kind is HostKind.STATIC
    (pump,) = granted.specs
    assert pump.verb is Verb.CONTINUOUS and pump.ref == Ref(RefKind.SELF)
    assert pump.amount.kind.name == "FOR_EACH"
    (grant,) = _effect_host('Target creature gains "{T}: Add {G}." until end '
                            'of turn.').specs
    (g,) = [v for k, v in grant.payload.data if k == "granted"]
    assert [h.kind for h in g.hosts] == [HostKind.MANA_ABILITY]


def test_the_face_memo_key_is_every_fact_the_parse_reads():
    """A32: the face parse is memoised on (text, facts, face); a changed
    fact that changes the output is a different key, never a stale hit."""
    text = "Some Bolt deals 3 damage to any target."
    named = parse_face(text, _facts("Some Bolt"))
    unnamed = parse_face(text, _facts())
    assert named[0].specs[0].other == Ref(RefKind.SELF)
    assert unnamed[0].specs[0] != named[0].specs[0]
    assert parse_face(text, _facts("Some Bolt")) is named


def test_a_connective_before_a_delayed_instruction_gates_the_creation_of_the_delayed_ability():
    """A14, A30: "If you do, return it ... at the beginning of your next
    upkeep" creates the delayed ability only when the named action was
    performed: its CREATE_TRIGGER is in that action's ``then``."""
    h = _effect_host("At the beginning of your end step, you may exile ~. If "
                     "you do, return it to the battlefield under its owner's "
                     "control at the beginning of your next upkeep.",
                     "Some Ghost", types=("creature",))
    (exile,) = h.specs
    (ct,) = exile.then
    assert "if_you_do" in ct.flags
    sub = _sub(ct)
    assert sub.timing is DelayedTriggerTiming.YOUR_NEXT_UPKEEP
    (ret,) = sub.host.specs
    assert ret.ref == Ref(RefKind.RESULT, exile.seq)


def test_a_trigger_bodys_that_much_with_no_earlier_action_reads_the_event_amount():
    """CR 603.2: "whenever ~ is dealt damage, it deals that much damage"
    -- with no earlier spec in the ability, "that much" is the triggering
    event's amount; in a spell it would be unbound."""
    (s,) = _effect_host("Whenever ~ is dealt damage, it deals that much "
                        "damage to any target.", "Some Reckoner",
                        types=("creature",)).specs
    assert s.amount.kind.name == "THAT_MUCH"
    assert s.amount.ref == Ref(RefKind.EVENT_OBJECT)
    assert s.other == Ref(RefKind.SELF)


def test_a_demonstrative_after_a_group_action_names_the_affected_set():
    """Section 7: a quantified group a spec acted on is an antecedent of a
    later "those <noun>" -- that spec's RESULT, the affected set."""
    h = _effect_host("Creatures you control get +1/+2 until end of turn. "
                     "Untap those creatures.")
    pump, untap = h.specs
    assert untap.ref == Ref(RefKind.RESULT, pump.seq)


def test_itself_is_the_clauses_own_subject():
    """A reflexive pronoun names the clause's own subject, and a rule-0
    pronoun after it reads that principal."""
    (s,) = _effect_host("Target creature deals damage to itself equal to its "
                        "power.").specs
    assert s.other == s.ref == Ref(RefKind.TARGET, 0)
    assert s.amount.quantity.ref == Ref(RefKind.TARGET, 0)


# ── The package entry points (section 3) ───────────────────────────────

def test_the_package_entry_points_parse_specs_faces_and_printed_spans():
    """parse_effects / parse_text_effects / parse_face share one pipeline;
    printed_span maps a host span back to the printed text (A40)."""
    import engine.effect_grammar as grammar
    from engine.effect_spec import CardEffects
    specs = grammar.parse_effects("Draw two cards, then discard a card.")
    assert [s.verb for s in specs] == [Verb.DRAW, Verb.DISCARD]
    specs = grammar.parse_effects("{T}: Draw a card.", HostKind.ACTIVATED)
    assert [s.verb for s in specs] == [Verb.DRAW]
    assert grammar.parse_effects("Draw a card.", HostKind.LOYALTY) == ()
    ce = grammar.parse_text_effects("Destroy target creature.")
    assert isinstance(ce, CardEffects) and Verb.DESTROY in ce.verbs
    printed = "Flying\nWhenever Some Bird attacks, Some Bird deals 1 damage to any target."
    facts = _facts("Some Bird", types=("creature",), keywords=("Flying",))
    hosts = grammar.parse_face(printed, facts)
    trig = next(h for h in hosts if h.kind is HostKind.TRIGGERED)
    (dmg,) = trig.specs
    assert grammar.printed_span(printed, facts, 0, trig.index, dmg.span) == \
        "Some Bird deals 1 damage to any target"
    grammar.clear_caches()
    assert grammar.parse_face(printed, facts) == hosts


def test_a_templates_keyword_facts_are_its_printed_keyword_list_never_the_typed_enum():
    """Section 3 facts: a face's CR 702 keywords are the printed MTGJSON
    list. The typed engine enum omits keywords the engine does not model
    and holds granted ones; a parse from it would make the lazy
    per-template path differ from the eager pool path."""
    from engine.cards import CardTemplate, CardType, Keyword
    from engine.mana import ManaCost
    import engine.effect_grammar as grammar
    t = CardTemplate(name="Some Cannoneer", oracle_text="Ward {4}",
                     card_types=[CardType.CREATURE],
                     mana_cost=ManaCost(generic=0),
                     keywords={Keyword.PROWESS},
                     printed_keywords=("Ward",))
    facts = grammar.template_facts(t)
    assert facts.keywords702 == keywords702(("Ward",))
    assert grammar.template_facts(t, 0, ("Ward",)) == facts
    (host,) = grammar.parse_template(t).faces[0]
    assert host.kind is HostKind.KEYWORD
    assert [k.name for k in host.keywords] == ["ward"]


def test_a_participle_reference_names_the_earlier_action_its_lemma_inflects():
    """A26, section 7: "the <participle> <noun>" names the RESULT of the
    earlier spec whose lemma the participle inflects -- an -ied form
    ("copied" -> copy) included. The inflection is the lexicon's one
    table, never a second one in the linker."""
    from engine.effect_grammar import lexicon
    h = _effect_host("Copy target instant spell. Exile the copied spell.")
    copy, exile = h.specs
    assert copy.verb is Verb.COPY
    assert exile.verb is Verb.EXILE
    assert exile.ref == Ref(RefKind.RESULT, copy.seq)
    assert [lexicon.participle_lemma(w) for w in (
        "copied", "exiled", "milled", "dealt", "chosen", "tapped",
        "sacrificed")] == ["copy", "exile", "mill", "deal", "choose", "tap",
                           "sacrifice"]
    assert lexicon.participle_lemma("spent") == ""      # no lexicon lemma


def test_a_trigger_heads_named_player_and_object_are_typed_at_l1():
    """Section 3 L1, section 7 rule 3: what a trigger head's event names --
    a player, an object besides the source -- is typed onto the head by
    the participant leaf; the linker's host antecedent reads it, never
    the raw head text."""
    def head(text):
        return next(h for h in parse_face(text, _facts(
            "Some Watcher", types=("creature",)))
            if h.kind is HostKind.TRIGGERED).trigger
    h = head("Whenever an opponent casts a spell, that player loses 1 life.")
    assert (h.names_player, h.names_object) == (True, True)
    h = head("Whenever another creature dies, you gain 1 life.")
    assert (h.names_player, h.names_object) == (False, True)
    h = head("When Some Watcher enters, draw a card.")
    assert (h.names_player, h.names_object) == (False, False)


def test_a_pending_references_noun_is_its_singular_head_with_the_possessive_suffix_removed():
    """Section 7: the noun a pending reference names is read by the
    participant leaf -- a possessive suffix is removed as a suffix ("that
    class's" is a class), a plural is singular."""
    from engine.effect_grammar.sub import participant
    assert participant.reference_noun("those creatures") == "creature"
    assert participant.reference_noun("that creature's") == "creature"
    assert participant.reference_noun("those creatures'") == "creature"
    assert participant.reference_noun("that class's") == "class"
    assert participant.reference_noun("them") == ""


def test_schema_lowering_repeats_until_no_spec_of_the_host_violates(monkeypatch):
    """L5 step 11: a violating spec is lowered to UNMODELLED(INVALID); a
    lowering may expose a new violation, so the pass repeats until none
    is left -- a host is never returned holding a violation, however many
    rounds that takes."""
    import engine.effect_spec as ES
    from engine.effect_grammar import link
    from engine.effect_spec import iter_specs

    def first_typed_violates(spec, host=None, parents=()):
        typed = [s.seq for s in iter_specs(host.specs)
                 if s.verb is not Verb.UNMODELLED] if host is not None else []
        if spec.verb is not Verb.UNMODELLED and typed and spec.seq == min(typed):
            return "test.cascade"
        return None
    link.clear_caches()
    monkeypatch.setattr(ES, "validate_spec", first_typed_violates)
    monkeypatch.setattr(link, "validate_spec", first_typed_violates)
    try:
        (h,) = parse_face("Draw a card. Draw a card. Draw a card. Draw a "
                          "card. Draw a card.", _facts(types=("sorcery",)))
        assert len(h.specs) == 5
        assert link._violations(h) == {}
        assert all(_refusal(s).stage is Stage.INVALID for s in h.specs)
    finally:
        link.clear_caches()


def test_a_violation_that_survives_lowering_refuses_the_whole_host(monkeypatch):
    """L5 step 11: when lowering every named spec leaves a violation, the
    pass ends with every spec of the host lowered -- it terminates, and no
    typed spec is returned beside the violation."""
    import engine.effect_spec as ES
    from engine.effect_grammar import link

    def always(spec, host=None, parents=()):
        return "test.always"
    link.clear_caches()
    monkeypatch.setattr(ES, "validate_spec", always)
    monkeypatch.setattr(link, "validate_spec", always)
    try:
        (h,) = parse_face("Draw a card. Discard a card.",
                          _facts(types=("sorcery",)))
        assert [s.verb for s in h.specs] == [Verb.UNMODELLED] * 2
    finally:
        link.clear_caches()
