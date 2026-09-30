"""The target sub-grammar over the whole card pool (design doc 2026-09-29,
section 5; F3, F11, A20, A21; E0 step 11).

Every counted target word of every oracle paragraph in the card DB runs
through the leaf. The leaf must not raise; it must return exactly one of a
typed `TargetSlot` or an UNMODELLED stage (never a guess); every typed
requirement must be an unmodified element of `target_solver.parse` of its
clause; every residue code must be a declared `RESIDUE_CODES` entry; and
the parse must be deterministic across a cache clear (F10). The coverage
counts are printed (``pytest -s``) and pinned below their measurement so a
regression in the closed table shows here.

Paragraphs are normalised by a light stand-in for L0 (reminder text
stripped, double quotes masked ``⟨qk⟩``, the card's names and "this <noun>"
-> ``~``, lowercased, dashes and apostrophes unified). The slot is cut by
a deliberately crude stand-in for L3/L4: from the count prefix before a
counted target word to the first verb, preposition or clause boundary after
it. A slot the stand-in cuts badly shows as residue or UNMODELLED, which
lowers coverage but never hides an exception.

The registered-deck witnesses at the end pin the exact reading of printed
target phrases from decks/modern_meta.py cards (A20, A21 witness rows). The
card name only locates the printed text in the DB; the leaf never sees it.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest

from engine.effect_grammar.sub import SELF_NOUNS
from engine.effect_spec import (NARROWING, UNPARSED, WIDENING, Stage,
                                canonical, residue_polarity)
from engine.oracle_parser import strip_reminder_text

_QUOTE_RE = re.compile(r'"[^"]*"')
_THIS_NOUN_RE = re.compile(r"\bthis (?:%s)\b" % "|".join(SELF_NOUNS))

# The slot stand-in. Before the target word: an optional count and
# "another"/"other". After it: up to the first clause boundary, verb or
# preposition that ends a target noun phrase.
_NUM = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+|x)"
_COUNT = (r"(?:(?:each of )?(?:up to %s|one or %s|one, two,? (?:and|or) "
          r"three|any number of|%s) )?(?:(?:another|other) )?" % ((_NUM,) * 3))
_PREFIX_RE = re.compile(r"(?:%s)$" % _COUNT)
_ANY_RE = re.compile(r"any (?:other )?$")
_TYPEWORD = (r"(?:an? )?(?:[\w-]+ )?(?:creature|artifact|enchantment|land|"
             r"planeswalker|battle|permanent|spell|player|opponent|card|"
             r"instant|sorcery|ability)")
_SLOT_END = re.compile(
    r"[.;:(⟨]|,(?! (?:or |and/or )?%s)|(?<![\w'])(?:gets?|gains?|has|have|"
    r"deals?|can't|cannot|becomes?|fights?|loses?|(?<!that )(?:is|are|was|"
    r"were)|attacks?|blocks?|(?<!you )doesn't|(?<!you )don't|each|"
    r"(?<!equal )to|onto|into|on (?:the )?top|on the bottom|until|unless|if|"
    r"where|then|and(?!/or)|this turn|as long as|for|at the beginning|"
    r"instead|draws?|discards?|sacrifices?|mills?|creates?|puts?|reveals?|"
    r"shuffles?|searches?|exiles?|returns?|chooses?|may|must|scry|scries|surveils?|"
    r"pays?|adds?|would|when|whenever|except|phases?|transforms?|untaps?|"
    r"taps?|explores?|connives?|perpetually|during|takes?|skips?|exchange|looks?|"
    r"(?<!blocked )by|chosen|without paying|(?<!power )(?<!toughness )(?<!value )"
    r"(?<!or )equal to)(?![\w'])"
    % _TYPEWORD)

# Measured 2026-09-30 on this branch's DB (22.7k cards), see the printed
# report: of 7977 slots, 6750 (84.6%) are typed (a TargetSlot, with or
# without residue) and 5436 (68.1%) are typed with no residue at all.
# Typed slots with residue: WIDENING 842, NARROWING 272 (mostly "target
# player or planeswalker" read as a player), UNPARSED 300 (mostly "you
# control" after a requirement the solver scoped "any": RESIDUE_CODES has
# no code for it, and "target nonbasic land" read as any land). Of 1227
# UNMODELLED slots, 1002 are solver gaps (no_requirement: "target attacking
# creature", "one or two targets", subtype targets), 121 zone mismatches
# (chiefly the solver's loose graveyard fallback reading "in your
# graveyard" elsewhere in the sentence), 70 unread counts ("x target
# creatures", "one, two, or three"), 33 zone unions. Floors sit a few
# points under the measurement: a fall below them is a closed-table
# regression, not noise (the parse is deterministic).
TYPED_FLOOR = 0.82
CLEAN_FLOOR = 0.65


def _normalise(template, text: str) -> str:
    text = strip_reminder_text(text)
    k = [0]

    def _mask(_m):
        k[0] += 1
        return "⟨Q%d⟩" % (k[0] - 1)
    text = _QUOTE_RE.sub(_mask, text)
    names = {template.name, *template.name.split(" // ")}
    for nm in sorted(names, key=len, reverse=True):
        if nm:
            text = text.replace(nm, "~")
    text = text.lower().replace("—", "-").replace("’", "'")
    return _THIS_NOUN_RE.sub("~", text)


def _paragraphs(card_db):
    out = set()
    for t in {id(v): v for v in card_db.cards.values()}.values():
        for text in (t.oracle_text or "", getattr(t, "back_face_oracle", "") or ""):
            if "target" not in text.lower():
                continue
            for line in _normalise(t, text).split("\n"):
                if "target" in line:
                    out.add(line.strip())
    return sorted(out)


def _slots(paragraphs):
    """(host, slot) pairs: one slot per counted target word, merged when a
    later word falls inside an earlier word's slot."""
    from engine.effect_grammar.sub.target import target_words
    for host in paragraphs:
        prev_end = -1
        for a, b in target_words(host):
            if a < prev_end:
                continue
            head = host[max(0, a - 40):a]
            m = _ANY_RE.search(head) or _PREFIX_RE.search(head)
            start = a - (len(m.group(0)) if m else 0)
            cut = _SLOT_END.search(host, b)
            end = cut.start() if cut else len(host)
            while end > b and host[end - 1] == " ":
                end -= 1
            prev_end = end
            yield host, (start, end)


def _clause(host, span):
    s = max(host.rfind(".", 0, span[0]), host.rfind("\n", 0, span[0])) + 1
    e = host.find(".", span[1])
    return host[s:e if e >= 0 else len(host)]


def _run(slots):
    from engine.effect_grammar.sub import target as T
    counts, residue, details, digest = Counter(), Counter(), Counter(), []
    for host, span in slots:
        r = T.parse_target(host, span, lemma="x")
        if r is None:
            counts["no_target_word"] += 1
            digest.append(canonical((host, span, None)))
            continue
        assert (r.value is None) != (r.unmodelled is None), (host, span, r)
        assert span[0] <= r.span[0] <= r.span[1] <= span[1], (host, span, r)
        if r.value is not None:
            counts["typed"] += 1
            if not r.value.residue:
                counts["clean"] += 1
            pols = {residue_polarity(c) for c in r.value.residue}
            assert None not in pols, r.value.residue
            for pol in pols:
                counts["residue_" + pol] += 1
            for c in r.value.residue:
                residue[c.split(":")[0] if c.startswith("target.union")
                        else c] += 1
            for (a, b) in r.value.spans:
                assert span[0] <= a < b <= span[1], (host, span, r)
        else:
            assert r.unmodelled.stage in (Stage.TARGET, Stage.TARGET_COUNT)
            assert r.unmodelled.lemma == "x"
            assert r.span == span or host[span[0]:span[1]].strip() == \
                host[slice(*r.span)], (host, span, r)
            counts["unmodelled"] += 1
            details[r.unmodelled.detail.split(":")[0]] += 1
        digest.append(canonical((host, span, r.value, r.unmodelled, r.amount,
                                 r.rest_spans, sorted(r.flags))))
    return counts, residue, details, digest


# Pool-wide (~8k target slots). Measured 2026-09-30 on this container
# (quiet, 4 cores): ~3.5 s for two passes, the parse() cross-check and the
# paragraph build, plus ~16 s when it is the first test of
# the process to load the shared card DB. 120 s bounds a hang with room for
# a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_the_target_leaf_types_or_refuses_every_pool_target_slot_deterministically(card_db):
    from engine.effect_grammar.sub import target as T
    from engine.target_solver import parse
    slots = list(_slots(_paragraphs(card_db)))
    assert len(slots) > 5000, len(slots)
    T.clear_caches()
    counts, residue, details, first = _run(slots)
    T.clear_caches()
    *_, second = _run(slots)
    assert first == second

    # Every typed requirement is an unmodified element of parse(clause).
    for host, span in slots:
        r = T.parse_target(host, span)
        if r is not None and r.value is not None:
            got = parse(_clause(host, span))
            for req in r.value.requirements:
                assert req in got, (host, span, req)

    total = counts["typed"] + counts["unmodelled"]
    typed, clean = counts["typed"] / total, counts["clean"] / total
    print("\ntarget slots: %d (typed %d = %.1f%%, clean %d = %.1f%%, "
          "unmodelled %d, no counted word %d)" % (
              total, counts["typed"], 100 * typed, counts["clean"],
              100 * clean, counts["unmodelled"], counts["no_target_word"]))
    print("typed slots with residue by polarity: WIDENING %d, NARROWING %d, "
          "UNPARSED %d" % (counts["residue_" + WIDENING],
                           counts["residue_" + NARROWING],
                           counts["residue_" + UNPARSED]))
    print("residue codes:", residue.most_common())
    print("unmodelled details:", details.most_common())
    assert typed >= TYPED_FLOOR
    assert clean >= CLEAN_FLOOR


# Witnesses: target phrases printed by registered-deck cards
# (decks/modern_meta.py), with the exact reading the leaf must return. The
# pool floors count a slot as typed whenever a TargetSlot exists; these pin
# that it is the printed rule: A21 residue (colored), A21 zone union, A20
# printed order, and counted phrases. The card name only locates the
# printed text in the DB; the leaf never sees it.
def _printed(card_db, card, phrase):
    t = card_db.get_card(card)
    for line in _normalise(t, t.oracle_text or "").split("\n"):
        if phrase in line:
            a = line.index(phrase)
            return line, (a, a + len(phrase))
    raise AssertionError((card, phrase, t.oracle_text))


@pytest.mark.timeout(120)
def test_registered_deck_target_witnesses_read_exactly_the_printed_rule(card_db):
    from decks.modern_meta import MODERN_DECKS
    from engine.effect_grammar.sub import target as T
    from engine.effect_spec import Amount, AmountKind
    from engine.target_solver import parse

    registered = set()
    for deck in MODERN_DECKS.values():
        for part in ("mainboard", "sideboard"):
            registered.update(deck.get(part, {}))

    def slot(card, phrase, registered_deck=True):
        assert card in registered or not registered_deck, card
        host, span = _printed(card_db, card, phrase)
        r = T.parse_target(host, span)
        assert r is not None, (card, phrase)
        if r.value is not None:
            got = parse(_clause(host, span))
            assert all(q in got for q in r.value.requirements), (card, r)
        return host, r

    # A21: a colour class the requirement does not carry is WIDENING residue.
    _, r = slot("Ugin, Eye of the Storms",
                "up to one target permanent that's one or more colors")
    (req,) = r.value.requirements
    assert req.types == frozenset({"permanent"}) and req.count_min == 0
    assert r.value.residue == ("target.colored",)
    assert r.amount == Amount(AmountKind.UP_TO, n=1)
    _, r = slot("Devourer of Destiny",
                "target permanent that's one or more colors")
    assert r.value.residue == ("target.colored",) and r.amount is None

    # A21: the solver keeps the spell of a spell-or-permanent union (the
    # design's witness row; the card is in the pool, not a registered list).
    _, r = slot("Sink into Stupor",
                "target spell or nonland permanent an opponent controls",
                registered_deck=False)
    assert r.value is None
    assert (r.unmodelled.stage, r.unmodelled.detail) == (
        Stage.TARGET, "target.zone_union")

    # A20: parse() lists the creature before the player; the slots read in
    # printed order, and "controls" is the relative clause's verb.
    host = _printed(card_db, "Practiced Offense", "target player controls")[0]
    assert [sorted(q.types) for q in parse(host)] == [["creature"], ["player"]]
    order = []
    for h, span in _slots([host]):
        order.extend(sorted(q.types)
                     for q in T.parse_target(h, span).value.requirements)
    assert order == [["player"], ["creature"]]
    h, r = slot("Practiced Offense", "target player controls")
    assert r.value.residue == () and r.rest_text(h) == "controls"

    # Carried qualifiers leave no residue; a dropped one is residue.
    _, r = slot("Kozilek's Command", "target creature with mana value x or less")
    assert r.value.requirements[0].max_mana_value_is_x
    assert r.value.residue == ()
    _, r = slot("Warping Wail",
                "target creature with power or toughness 1 or less")
    assert r.value.residue == ("target.stat:power",)
    _, r = slot("Warping Wail", "target sorcery spell")
    assert r.value.requirements[0].zone == "stack" and r.value.residue == ()

    # F11 and counts: "the target of" and "a single target" are nouns; a
    # "one or two" count is the requirement's own, with no amount.
    h, span = _printed(card_db, "Untimely Malfunction",
                       "change the target of target spell or ability")
    assert len(T.target_words(h, span)) == 1
    _, r = slot("Untimely Malfunction", "one or two target creatures")
    (req,) = r.value.requirements
    assert (req.count_min, req.count_max) == (1, 2) and r.amount is None

    # Clean single targets.
    for card, phrase, types in (
            ("Fatal Push", "target creature", {"creature"}),
            ("Galvanic Discharge", "target creature or planeswalker",
             {"creature", "planeswalker"})):
        _, r = slot(card, phrase)
        assert r.value.requirements[0].types == frozenset(types)
        assert r.value.residue == ()
