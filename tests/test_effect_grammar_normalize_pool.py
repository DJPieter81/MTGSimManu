"""L0 normalisation over the whole card pool (design doc 2026-09-29,
section 3 L0; A4, A7, A9, A10, A40; E0 step 8).

Every face's oracle text in the card DB runs through L0. It must not raise;
it must be deterministic across a cache clear; its reminder removal must be
exactly `strip_reminder_text`'s; its output must satisfy the leaf contract's
L0 bullet; and `printed_span` must map every normalised paragraph inside the
printed text. Coverage is measured and printed (``pytest -s``) and pinned
below the measurement so a regression in a closed table shows here:

* self-reference coverage: of the printed occurrences of a face's own
  names (outside "named X"), the share L0 rewrote to ``~``;
* pronoun coverage: of the he/she/him/his/her(self) occurrences on
  character faces (A9), the share rewritten;
* clean faces: the share with no L0 flag (an unbalanced quote or a quote
  nested past depth 3 is flagged, never guessed).

Measured 2026-10-01 on this branch's DB (22.7k cards, 23.2k faces): 856
quotes (8 nested), 244 named masks, 8482 reminders; self-reference
coverage 0.998 (the residue is lexicon-word names printed lowercase, which
are rightly not self-forms, and ability names that begin with the short
name); pronoun coverage 1.000 (137 of 137); clean faces 1.000.

The registered-deck witnesses at the end pin the exact L0 reading of
printed text from decks/modern_meta.py cards (A9, A10, A40, A4 witness
rows). The card name only locates the printed text and builds the facts;
L0 sees names only through `Facts.names`.
"""
from __future__ import annotations

import re
import time
from collections import Counter

import pytest


# Measured 2026-10-01 on this branch's DB (see the printed report). The
# floors sit a little under the measurement: the parse is deterministic, so
# a fall below them is a closed-table regression, not noise.
SELF_REF_FLOOR = 0.97
PRONOUN_FLOOR = 0.97
CLEAN_FLOOR = 0.995

_PRONOUN_RE = re.compile(r"(?<![\w'-])(?:he|she|him|his|her|himself|herself|"
                         r"he's|she's)(?![\w'-])", re.I)


def _faces(db):
    """(template, face index, printed text, Facts) for every face."""
    from engine.effect_grammar import normalize as N
    out = []
    for t in {id(v): v for v in db.cards.values()}.values():
        legendary = any(getattr(s, "value", s) == "legendary"
                        for s in t.supertypes)
        faces = ((0, t.oracle_text or "", t.card_types, t.subtypes),
                 (1, getattr(t, "back_face_oracle", "") or "",
                  t.back_face_types, t.back_face_subtypes))
        for i, text, types, subs in faces:
            if not text:
                continue
            tc = frozenset(getattr(c, "value", str(c)) for c in types)
            character = "planeswalker" in tc or (legendary and "creature" in tc)
            facts = N.Facts(
                names=N.self_names(t.name, is_legendary=legendary,
                                   is_character=character,
                                   subtypes=tuple(subs) if "creature" in tc else ()),
                type_class=tc,
                is_spell=bool({"instant", "sorcery"} & tc),
                is_legendary=legendary,
                is_planeswalker="planeswalker" in tc)
            out.append((t, i, text, facts))
    return out


@pytest.fixture(scope="module")
def faces():
    from tests._card_db_cache import shared_card_database
    return _faces(shared_card_database())


def _outputs(faces):
    """Every face's L0 output, in pool order (the determinism digest)."""
    from engine.effect_grammar import normalize as N
    return [N.normalize(text, facts) for _t, _i, text, facts in faces]


def _run(faces):
    from engine.effect_grammar import normalize as N
    from engine.effect_grammar.sub import SELF_NOUNS
    from engine.oracle_parser import strip_reminder_text
    this_noun = re.compile(r"\bthis (?:%s)\b" % "|".join(SELF_NOUNS))
    counts, other_this = Counter(), Counter()
    for t, i, text, facts in faces:
        r = N.normalize(text, facts)
        counts["faces"] += 1
        if r.flags:
            counts["flagged"] += 1
            for f in r.flags:
                assert f in N.FLAGS, f
                counts["flag_" + f] += 1
        counts["quotes"] += len(r.quotes)
        counts["nested_quotes"] += sum(p >= 0 for p in r.quote_parents)
        counts["named"] += len(r.names)
        counts["reminders"] += len(r.reminders)

        # Reminder removal is exactly strip_reminder_text's.
        out, last = [], 0
        for m in sorted(r.reminders, key=lambda x: x.printed):
            out.append(text[last:m.printed[0]])
            last = m.printed[1]
        out.append(text[last:])
        assert "".join(out) == strip_reminder_text(text), t.name

        # The leaf contract's L0 bullet.
        for s in (r.text, *r.quotes):
            assert s == s.lower(), t.name
            assert "’" not in s and "—" not in s and "−" not in s, t.name
            assert "  " not in s and "\n\n" not in s and s == s.strip(), t.name
            assert not this_noun.search(s), (t.name, s)
            for m in re.finditer(r"\bthis ([a-z]+)\b", s):
                other_this[m.group(1)] += 1

        # Self-reference coverage: printed own-name occurrences outside
        # "named X" that survive into the host or quote text.
        masked = re.sub(r"named [^.,;\n\"]*", "", text)
        own = sum(len(re.findall(r"(?<![\w-])%s(?![\w-])" % re.escape(n),
                                 masked)) for n in facts.names)
        counts["self_printed"] += own
        left = sum(len(re.findall(r"(?<![\w-])%s(?![\w-])"
                                  % re.escape(n.lower()), s))
                   for n in facts.names for s in (r.text, *r.quotes))
        counts["self_left"] += min(left, own)
        if facts.is_planeswalker or (facts.is_legendary and
                                     "creature" in facts.type_class):
            stripped = strip_reminder_text(text)
            counts["pronoun_printed"] += len(_PRONOUN_RE.findall(stripped))
            counts["pronoun_left"] += sum(len(_PRONOUN_RE.findall(s))
                                          for s in (r.text, *r.quotes))

        # printed_span maps the whole normalised text onto the printed
        # text from its first to its last surviving character.
        if r.text:
            p = N.printed_span(text, facts, (0, len(r.text)))
            assert p and text.find(p) >= 0, t.name
            assert p[0].lower() == r.text[0] or r.text[0] in "~⟨-'", t.name
    return counts, other_this


@pytest.mark.timeout(240)  # measured: 7.3 s wall after the shared DB load
def test_l0_normalises_every_pool_face_deterministically_within_its_floors(faces):
    from engine.effect_grammar import normalize as N
    from engine.effect_grammar.sub import clear_caches
    import gc
    gc.collect()
    gc.freeze()     # keep full collections over the DB heap out of the pass
    try:
        clear_caches()
        t0 = time.process_time()
        counts, other_this = _run(faces)
        elapsed = time.process_time() - t0
        first = _outputs(faces)
        clear_caches()
        assert _outputs(faces) == first
        clear_caches()
    finally:
        gc.unfreeze()

    self_cov = 1 - counts["self_left"] / max(1, counts["self_printed"])
    pron_cov = 1 - counts["pronoun_left"] / max(1, counts["pronoun_printed"])
    clean = 1 - counts["flagged"] / counts["faces"]
    print("\nL0 pool report (%.2f s CPU incl. checks)" % elapsed)
    for k in sorted(counts):
        print("  %-22s %d" % (k, counts[k]))
    print("  self-reference coverage %.4f" % self_cov)
    print("  pronoun coverage        %.4f" % pron_cov)
    print("  clean faces             %.4f" % clean)
    print("  'this <word>' left (not a SELF_NOUN):",
          other_this.most_common(12))
    assert self_cov >= SELF_REF_FLOOR
    assert pron_cov >= PRONOUN_FLOOR
    assert clean >= CLEAN_FLOOR
    assert N.normalize.cache_info().currsize == 0


# Design section 12: the whole grammar (L0-L5) gets 3.0 s of process CPU
# for the pool. L0 is held to half of it.
POOL_PARSE_CPU_BUDGET_S = 3.0
L0_SHARE_OF_BUDGET = 0.5


@pytest.mark.timeout(120)  # measured: 0.8 s CPU for L0 after the shared DB load
def test_l0_fits_its_share_of_the_pool_load_budget(faces):
    """Process CPU of L0 over every pool face, caches cleared first. The
    card DB's objects are frozen out of the cyclic collector for the
    measurement so a full collection over the 600 MB DB heap (which the
    load pays once, not per layer) is not charged to L0. Measured
    2026-10-01: 0.78 s over 23.5k faces."""
    import gc
    from engine.effect_grammar import normalize as N
    N.clear_caches()
    gc.collect()
    gc.freeze()
    try:
        t0 = time.process_time()
        for _t, _i, text, facts in faces:
            N.normalize(text, facts)
        elapsed = time.process_time() - t0
    finally:
        gc.unfreeze()
        N.clear_caches()
    print("\nL0 pool CPU: %.2f s over %d faces" % (elapsed, len(faces)))
    assert elapsed <= POOL_PARSE_CPU_BUDGET_S * L0_SHARE_OF_BUDGET


# ── Registered-deck witnesses (decks/modern_meta.py) ──────────────────

def _face(faces, name, index=0):
    for t, i, text, facts in faces:
        if t.name.startswith(name) and i == index:
            return text, facts
    pytest.skip("%s not in the card DB" % name)


def test_registered_deck_witness_names_are_in_a_registered_deck():
    from decks.modern_meta import MODERN_DECKS
    listed = set()
    for deck in MODERN_DECKS.values():
        for part in ("mainboard", "sideboard"):
            listed.update((deck.get(part) or {}).keys())
    for name in _WITNESS_NAMES:
        assert any(n == name or n.startswith(name + " // ") for n in listed), name


_WITNESS_NAMES = ("Ajani, Nacatl Pariah", "Kaito, Bane of Nightmares",
                  "Ral, Monsoon Mage", "Tamiyo, Inquisitive Student",
                  "Urza's Saga", "Consult the Star Charts", "Fire Magic",
                  "Ragavan, Nimble Pilferer", "Emrakul, the Promised End")


def test_witness_pronouns_by_case_on_a_transforming_legend(faces):
    """A9: exile Ajani, return him ... under his owner's control."""
    from engine.effect_grammar import normalize as N
    text, facts = _face(faces, "Ajani, Nacatl Pariah")
    assert N.normalize(text, facts).text.split("\n")[1] == (
        "whenever one or more other cats you control die, you may exile ~, "
        "then return ~ to the battlefield transformed under ~'s owner's control.")
    text, facts = _face(faces, "Ajani, Nacatl Pariah", 1)
    assert "if you control a red permanent other than ~, ~ deals damage" in (
        N.normalize(text, facts).text)


@pytest.mark.parametrize("name,expected", [
    ("Kaito, Bane of Nightmares",
     "during your turn, as long as ~ has one or more loyalty counters on ~, "
     "~ is a 3/4 ninja creature and has hexproof."),
    ("Ral, Monsoon Mage",
     "whenever you cast an instant or sorcery spell during your turn, flip a "
     "coin. if you lose the flip, ~ deals 1 damage to you. if you win the "
     "flip, you may exile ~. if you do, return ~ to the battlefield "
     "transformed under ~'s owner's control."),
    ("Tamiyo, Inquisitive Student",
     "when you draw your third card in a turn, exile ~, then return ~ to the "
     "battlefield transformed under ~'s owner's control."),
])
def test_witness_self_pronouns_on_walker_and_legend_faces(faces, name, expected):
    from engine.effect_grammar import normalize as N
    text, facts = _face(faces, name)
    assert expected in N.normalize(text, facts).text.split("\n")


def test_witness_nested_quote_on_a_saga_chapter(faces):
    """A10: chapter II grants an ability whose token carries a nested
    single-quoted static."""
    from engine.effect_grammar import normalize as N
    text, facts = _face(faces, "Urza's Saga")
    r = N.normalize(text, facts)
    assert r.text.split("\n")[:2] == ["i - this saga gains ⟨q0⟩",
                                      "ii - this saga gains ⟨q1⟩"]
    assert r.quotes == (
        "{t}: add {c}.",
        "{2}, {t}: create a 0/0 colorless construct artifact creature token "
        "with ⟨q2⟩",
        "~ gets +1/+1 for each artifact you control.")
    assert r.quote_parents == (-1, -1, 1)
    (rem,) = r.reminders
    assert rem.paragraph == -1 and "lore counter" in rem.text


def test_witness_kicked_clause_is_its_exact_printed_span(faces):
    """A40: the kicked clause runs from the end of the kicked frame to the
    end of its sentence, printed case kept, reminder stripped."""
    from engine.effect_grammar import normalize as N
    text, facts = _face(faces, "Consult the Star Charts")
    r = N.normalize(text, facts)
    frame = "if ~ was kicked, "
    a = r.text.index(frame) + len(frame)
    b = r.text.index(".", a) + 1
    assert N.printed_span(text, facts, (a, b)) == (
        "put two of those cards into your hand instead.")
    assert [m.text for m in r.reminders] == [
        "you may pay an additional {1}{u} as you cast this spell."]


def test_witness_tiered_reminder_is_kept_and_tier_labels_are_not_self_forms(faces):
    """A4: Tiered's header lives in reminder text. The tier label shares a
    word with the card name but is not the name."""
    from engine.effect_grammar import normalize as N
    text, facts = _face(faces, "Fire Magic")
    r = N.normalize(text, facts)
    assert r.text.split("\n") == [
        "tiered",
        "• fire - {0} - ~ deals 1 damage to each creature.",
        "• fira - {2} - ~ deals 2 damage to each creature.",
        "• firaga - {5} - ~ deals 3 damage to each creature."]
    assert [(m.paragraph, m.text) for m in r.reminders] == [
        (0, "choose one additional cost.")]


@pytest.mark.parametrize("name,line,expected", [
    ("Ragavan, Nimble Pilferer", 0,
     "whenever ~ deals combat damage to a player, create a treasure token and "
     "exile the top card of that player's library. until end of turn, you may "
     "cast that card."),
    ("Emrakul, the Promised End", 0,
     "~ costs {1} less to cast for each card type among cards in your "
     "graveyard."),
])
def test_witness_short_name_and_this_spell_are_the_source(faces, name, line,
                                                          expected):
    from engine.effect_grammar import normalize as N
    text, facts = _face(faces, name)
    assert N.normalize(text, facts).text.split("\n")[line] == expected
