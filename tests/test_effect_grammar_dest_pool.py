"""The destination sub-grammar over the whole card pool (design doc
2026-09-29, section 4 MOVE row; A13, A15, A18).

Every put / return / shuffle-into slot and every "instead of putting it
into <zone>" override in the pool's oracle text runs through the leaf. The
leaf must not raise, must return exactly one of a typed Destination or an
UNMODELLED stage (never a guess), and must be deterministic across a cache
clear. The coverage counts are printed (``pytest -s``) and pinned below
their measurement so a regression in the closed table shows here.

The slots are cut by a deliberately crude stand-in for L2/L3 (the clause
splitter is another leaf): split at depth-0 punctuation and at the frame
words that end a destination PP. A slot the stand-in cuts badly counts as
"no destination", which lowers coverage but never hides an exception.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest

from engine.effect_spec import Destination, canonical

# Active put / return / shuffle; a passive "is put into" is an event, not
# an effect.
_MOVE = re.compile(
    r"(?<!\bis )(?<!\bare )(?<!\bwas )(?<!\bwere )(?<!\bbe )(?<!\bbeen )"
    r"\b(return|put|shuffle)s? (?!and\b)")
# "put <count> <kind> counter(s) on" is PUT_COUNTERS (section 4 put order).
_PUT_COUNTERS = re.compile(
    r"(?:[a-z]+|\d+) (?:additional )?\S+ counters?\b|(?:[a-z]+|\d+) counters\b")
_SLOT_END = re.compile(
    r"[,;.:(\"]| as it resolves| then | instead\b| unless | if | at the "
    r"beginning | at end of | until | for as long | this turn| this way| "
    r"where | and (?!attacking|transformed|tapped)")
_INSTEAD = re.compile(r"\b(?:exile|put|return|shuffle)s? [^,;.:\"]*? instead of "
                      r"(?:putting|into) [^,;.:\"]*?(?= if |[,;.:\"]|$)")

# Measured 2026-09-30 on the full pool (see the printed report): 96.4% of
# the located destination slots are typed (2834 typed, 106 unmodelled, 298
# slots the stand-in splitter left without a destination). Instead-of forms
# are passed as the whole sentence: 5 of the 6 A15 overrides led by the
# 'this way' rider are typed (the other is a choice of top or bottom); 11
# 'would <event>' / 'anywhere else' / discard CR 614 replacements are
# refused with no override flag, and 7 unlinked instead-of moves are left
# to the clause linker. The floors sit a little under the measurement: a
# fall below them is a closed-table regression, not noise (the parse is
# deterministic).
LOCATED_TYPED_FLOOR = 0.94
INSTEAD_OF_TYPED_FLOOR = 0.8


def _pool_sentences(db):
    from engine.oracle_parser import strip_reminder_text
    out = set()
    for t in {id(v): v for v in db.cards.values()}.values():
        for text in (t.oracle_text or "", getattr(t, "back_face_oracle", "") or ""):
            if not text:
                continue
            for line in strip_reminder_text(text).lower().split("\n"):
                for sent in re.split(r"(?<=[.])\s+", line):
                    if sent:
                        out.add(sent)
    return sorted(out)


def _run(sentences):
    """(counts, source-zone counts, instead-of counts, canonical digest)."""
    from engine.effect_grammar.sub import dest as D
    counts, zones, instead = Counter(), Counter(), Counter()
    digest = []
    for s in sentences:
        for m in _MOVE.finditer(s):
            verb = m.group(1)
            if verb == "put" and _PUT_COUNTERS.match(s, m.end()):
                continue
            cut = _SLOT_END.search(s, m.end())
            end = cut.start() if cut else len(s)
            lemma = "shuffle" if verb == "shuffle" else ""
            span = D.locate_destination(s, (m.end(), end), lemma=lemma)
            if span is None:
                counts["no_destination"] += 1
                continue
            assert m.end() <= span[0] <= span[1] <= end, (s, span)
            r = D.parse_destination(s, span, lemma=lemma)
            assert (r.value is None) != (r.unmodelled is None), (s, r)
            if r.value is not None:
                assert isinstance(r.value, Destination)
                counts["typed"] += 1
            else:
                assert r.unmodelled.lemma and r.unmodelled.detail, (s, r)
                counts["unmodelled"] += 1
            z = D.source_zone(s[m.end():span[0]])
            zones["typed" if z else "none"] += 1
            digest.append(canonical((s, span, r.value, r.unmodelled, z)))
        for m in _INSTEAD.finditer(s):
            # The whole sentence up to the override, never a slice from the
            # move verb: the leaf must see the 'this way' rider or the
            # 'would <event>' frame to tell an A15 override from a CR 614
            # replacement effect.
            r = D.parse_instead_of(s, (0, m.end()))
            assert (r.value is None) != (r.unmodelled is None), (s, r)
            if r.value is not None:
                assert r.value.instead_of and "dest_override" in r.flags
                instead["typed"] += 1
            elif r.unmodelled.detail in ("instead_of.replacement_effect",
                                         "instead_of.unlinked"):
                assert "dest_override" not in r.flags
                instead[r.unmodelled.detail.split(".")[1]] += 1
            else:
                instead["unmodelled"] += 1
            digest.append(canonical((s, r.value, r.unmodelled, r.object_span)))
    return counts, zones, instead, digest


# Pool-wide (~48k distinct sentences). Measured 2026-09-30 on this container
# (quiet, 4 cores): ~3 s for two passes plus the sentence build, plus ~16 s
# when it is the first test of the process to load the shared card DB. 120 s
# bounds a hang with room for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_the_destination_grammar_types_or_refuses_every_pool_move_slot_deterministically(card_db):
    from engine.effect_grammar.sub import dest as D
    sentences = _pool_sentences(card_db)
    D.clear_caches()
    counts, zones, instead, first = _run(sentences)
    D.clear_caches()
    _, _, _, second = _run(sentences)
    assert first == second

    located = counts["typed"] + counts["unmodelled"]
    typed_share = counts["typed"] / located
    instead_share = instead["typed"] / max(1, instead["typed"] + instead["unmodelled"])
    print("\ndestination slots: typed=%d unmodelled=%d no_destination=%d "
          "(typed share of located %.1f%%)" % (
              counts["typed"], counts["unmodelled"], counts["no_destination"],
              100 * typed_share))
    print("source zone from object span: typed=%d none=%d" % (
        zones["typed"], zones["none"]))
    print("instead-of: overrides typed=%d unmodelled=%d; replacement "
          "effects refused=%d; unlinked=%d" % (
              instead["typed"], instead["unmodelled"],
              instead["replacement_effect"], instead["unlinked"]))
    assert typed_share >= LOCATED_TYPED_FLOOR
    assert instead_share >= INSTEAD_OF_TYPED_FLOOR
