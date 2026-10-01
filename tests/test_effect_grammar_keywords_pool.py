"""The keyword-line table over the whole card pool and the registered decks
(design doc 2026-09-29, A1, M3, A8; E0 step 9).

Every paragraph of every pool face runs through
`keywords.parse_keyword_line`, gated by the face's MTGJSON ``keywords``
field intersected with the CR 702 table (M3) -- and, for the back faces the
card database keeps without keyword data, by the table alone (the
synthetic-template path of A1). The leaf must not raise, must return None
(not a keyword line) or exactly one of a typed spec tuple or an UNMODELLED
stage, and must be deterministic across a cache clear. Coverage is printed
(``pytest -s``) and pinned below its measurement.

The L0 stand-in here is deliberately crude (normalize.py is another leaf):
reminder text stripped, the card's names rewritten to ``~``, dashes and
apostrophes unified, lowercased.

Card names appear only in the witness fixtures below, never in `engine/`.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest

from engine.effect_spec import KeywordSpec, canonical

# Measured 2026-10-01 on the full pool (see the printed report):
# * front faces gated by their MTGJSON keywords: 99.94% of the keyword
#   lines are typed (9810 typed, 6 unmodelled: 5 "{g} or {w}" cost choices
#   the cost owner cannot hold, 1 self-form ward cost with no printed span);
# * back faces, CR 702 table alone: 318 typed, 0 unmodelled;
# * the table alone classifies exactly the same front-face lines as the
#   gated run (9810 / 6): on this pool the M3 gate removes nothing the
#   table admits, it only keeps CR 701 actions out by construction;
# * 99.46% of the (card, CR 702 keyword) pairs MTGJSON lists are found on a
#   typed line of either face (10898 / 10957). The rest are keywords printed
#   inside an effect or grant ("equipped creature has reach", "the top card
#   of your library has plot"), a labelled keyword ("<flavor word> - equip
#   {6}", which L1 reads after its label strip), or a split card's other
#   half.
# The floors sit a little under the measurement: a fall below them is a
# closed-table regression, not noise (the parse is deterministic).
TYPED_SHARE_FLOOR = 0.995
FACE_KEYWORD_RECALL_FLOOR = 0.99
BACK_FACE_TYPED_SHARE_FLOOR = 0.99


def _l0(text, names):
    from engine.oracle_parser import strip_reminder_text
    t = strip_reminder_text(text or "")
    for n in sorted({n for n in names if n}, key=len, reverse=True):
        t = re.sub(r"\b%s\b" % re.escape(n), "~", t)
    t = t.replace("—", "-").replace("–", "-").replace("’", "'")
    return re.sub(r"[ \t]+", " ", t).lower()


def _names(name):
    parts = [name] + name.split(" // ")
    return parts + [p.split(",")[0] for p in parts if "," in p]


def _faces(db):
    """(card name, face, L0 text, face keyword set or None)."""
    from engine.effect_grammar.keywords import keywords702
    out = []
    for name, entry in sorted(db._raw_data.items()):
        names =_names(name) + [entry.get("faceName") or ""]
        out.append((name, 0, _l0(entry.get("text"), names),
                    keywords702(entry.get("keywords") or ())))
        t = db.cards.get(name)
        back = getattr(t, "back_face_oracle", "") if t is not None else ""
        if back:
            out.append((name, 1, _l0(back, names), None))
    # A DFC is registered under its full and front name: one row each face.
    seen, uniq = set(), []
    for row in out:
        key = (row[2], row[3], row[1])
        if key not in seen:
            seen.add(key)
            uniq.append(row)
    return uniq


def _satisfied(kw, typed):
    """A listed keyword is found when a typed line holds it; typecycling is
    a cycling ability (CR 702.29e), so it satisfies a listed 'cycling'."""
    return kw in typed or (kw == "cycling" and "typecycling" in typed)


def _run(faces, gated=True):
    from engine.effect_grammar import keywords as K
    counts = Counter()
    listed, typed_by_card = {}, {}
    digest = []
    for name, face, text, cands in faces:
        typed_names = typed_by_card.setdefault(name, set())
        if cands is not None:
            listed[name] = cands
        for para in text.split("\n"):
            para = para.strip()
            if not para:
                continue
            r = K.parse_keyword_line(para, candidates=cands if gated else None)
            if r is None:
                continue
            assert (r.value is None) != (r.unmodelled is None), (name, para, r)
            key = "back_" if face else ""
            if r.value is not None:
                assert r.value and all(isinstance(s, KeywordSpec) for s in r.value)
                assert 0 <= r.span[0] <= r.span[1] <= len(para)
                counts[key + "typed"] += 1
                typed_names.update(s.name for s in r.value)
            else:
                assert r.unmodelled.detail.startswith("keywords."), r
                counts[key + "unmodelled"] += 1
                counts["um:" + r.unmodelled.detail.split(":")[0]] += 1
            digest.append(canonical((name, face, para, r.value, r.unmodelled,
                                     r.span, r.rest_spans)))
            rule = K.parse_cost_rule(para)
            if rule is not None:
                digest.append(canonical((para, rule.value, rule.unmodelled)))
    # MTGJSON lists a card's keywords on its first entry for both faces.
    found = Counter()
    for name, cands in listed.items():
        for kw in cands:
            found["listed"] += 1
            found["found" if _satisfied(kw, typed_by_card[name]) else "missing"] += 1
    return counts, found, digest


# Pool-wide (every front face and every back face the database keeps).
# Measured 2026-10-01 on this container (quiet, 4 cores): ~3.4 s for the
# face build and the three passes, plus ~16-21 s when it is the first test
# of the process to load the shared card DB. 120 s bounds a hang with room
# for a slower 2-core CI runner.
@pytest.mark.timeout(120)
def test_the_keyword_table_types_or_refuses_every_pool_keyword_line_deterministically(card_db):
    from engine.effect_grammar import keywords as K
    faces = _faces(card_db)
    K.clear_caches()
    counts, found, first = _run(faces)
    K.clear_caches()
    _, _, second = _run(faces)
    assert first == second

    table_counts, _, _ = _run([f for f in faces if f[1] == 0], gated=False)
    lines = counts["typed"] + counts["unmodelled"]
    typed_share = counts["typed"] / lines
    back = counts["back_typed"] + counts["back_unmodelled"]
    back_share = counts["back_typed"] / max(1, back)
    recall = found["found"] / max(1, found["listed"])
    print("\nkeyword lines (front, gated): typed=%d unmodelled=%d (%.2f%% typed)"
          % (counts["typed"], counts["unmodelled"], 100 * typed_share))
    print("keyword lines (back, table only): typed=%d unmodelled=%d (%.2f%%)"
          % (counts["back_typed"], counts["back_unmodelled"], 100 * back_share))
    print("front faces, table only (no gate): typed=%d unmodelled=%d"
          % (table_counts["typed"], table_counts["unmodelled"]))
    print("face keywords found on a typed line: %d/%d (%.2f%%)"
          % (found["found"], found["listed"], 100 * recall))
    print("unmodelled codes:", sorted(
        (k, v) for k, v in counts.items() if k.startswith("um:")))
    assert typed_share >= TYPED_SHARE_FLOOR
    assert back_share >= BACK_FACE_TYPED_SHARE_FLOOR
    assert recall >= FACE_KEYWORD_RECALL_FLOOR


# ── Registered-deck witnesses (A1, A8) ─────────────────────────────────

def _deck_cards():
    from decks.modern_meta import MODERN_DECKS
    out = set()
    for deck in MODERN_DECKS.values():
        for part in ("mainboard", "sideboard"):
            out.update((deck.get(part) or {}).keys())
    return out


# card -> the typed keyword items of its keyword line(s), in printed order:
# (name, n, param, cost). Every other paragraph of the card is resolution
# or ability text and must not classify as a keyword line.
WITNESSES = {
    "Lava Dart": [("flashback", None, None, "sacrifice a mountain")],
    "Cling to Dust": [("escape", None, None,
                       "{3}{b}, exile five other cards from your graveyard")],
    "Desperate Ritual": [("splice", None, "arcane", "{1}{r}")],
    "Goryo's Vengeance": [("splice", None, "arcane", "{2}{b}")],
    "Into the Flood Maw": [("gift", None, "a tapped fish", None)],
    "Unburial Rites": [("flashback", None, None, "{3}{w}")],
    "Faithless Looting": [("flashback", None, None, "{2}{r}")],
    "Past in Flames": [("flashback", None, None, "{4}{r}")],
    "Ephemerate": [("rebound", None, None, None)],
    "Consult the Star Charts": [("kicker", None, None, "{1}{u}")],
    "Orim's Chant": [("kicker", None, None, "{w}")],
    "Consign to Memory": [("replicate", None, None, "{1}")],
    "Vandalblast": [("overload", None, None, "{4}{r}")],
    "Solitude": [("flash", None, None, None), ("lifelink", None, None, None),
                 ("evoke", None, None, "exile a white card from your hand")],
    "Subtlety": [("flash", None, None, None), ("flying", None, None, None),
                 ("evoke", None, None, "exile a blue card from your hand")],
    "Endurance": [("flash", None, None, None), ("reach", None, None, None),
                  ("evoke", None, None, "exile a green card from your hand")],
    "Street Wraith": [("landwalk", None, "swamp", None),
                      ("cycling", None, None, "pay 2 life")],
    "Detective's Phoenix": [("bestow", None, None, "{r}, collect evidence 6"),
                            ("flying", None, None, None),
                            ("haste", None, None, None)],
    "Fire Magic": [("tiered", None, None, None)],
}

# Cards whose "<keyword> cost is equal to its mana cost" sentence is the A8
# cost rule of the keyword they grant.
COST_RULE_WITNESSES = {"Past in Flames": "flashback",
                       "Snapcaster Mage": "flashback"}


def _witness_lines(db, name):
    from engine.effect_grammar import keywords as K
    entry = db._raw_data[name]
    text = _l0(entry.get("text"), _names(name))
    cands = K.keywords702(entry.get("keywords") or ())
    typed, other = [], []
    for para in (p.strip() for p in text.split("\n")):
        if not para:
            continue
        r = K.parse_keyword_line(para, candidates=cands)
        (other if r is None else typed).append((para, r))
    return text, typed, other


@pytest.mark.parametrize("name", sorted(WITNESSES))
def test_a_registered_deck_keyword_line_is_a_keyword_host_never_resolution_text(card_db, name):
    """A1: the deck's keyword lines type with their costs; its resolution
    paragraphs are never keyword lines, so they cannot merge."""
    assert name in _deck_cards(), name
    _, typed, other = _witness_lines(card_db, name)
    items = []
    for para, r in typed:
        assert r.unmodelled is None, (para, r)
        assert r.rest_spans == (), (para, r)
        items += [(s.name, s.n, s.param, s.cost) for s in r.value]
        for s in r.value:
            assert (s.cost is None) == (s.cost_snapshot is None), s
    assert items == WITNESSES[name]
    # Every witness but a keyword-only face keeps its other text apart.
    assert other or name == "Street Wraith", name


@pytest.mark.parametrize("name,keyword", sorted(COST_RULE_WITNESSES.items()))
def test_a_registered_deck_cost_rule_sentence_types_as_the_granted_keywords_cost_rule(
        card_db, name, keyword):
    from engine.effect_grammar import keywords as K
    assert name in _deck_cards(), name
    text, _, _ = _witness_lines(card_db, name)
    rules = []
    for sent in re.split(r"(?<=\.)\s+", text.replace("\n", " ")):
        r = K.parse_cost_rule(sent)
        if r is not None:
            rules.append(r.value)
    assert rules == [KeywordSpec(name=keyword, cost_rule="mana_cost")]
