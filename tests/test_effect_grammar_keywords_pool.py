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
# * front faces gated by their MTGJSON keywords: 99.76% of the keyword
#   lines are typed with a cost the cost owner reads (9830 typed, of which
#   18 carry the owner's 'unrecognised' refusal -- ward 5, cumulative
#   upkeep 5, equip 2, flashback 2, bestow, madness, recover, splice 1 --
#   and are kept out of the share; 6 unmodelled: 5 "{g} or {w}" cost
#   choices the cost owner cannot hold, 1 self-form ward cost with no
#   printed span);
#   no paragraph that opens with one of the face's keywords and is not a
#   sentence is dropped as "not a keyword line" (A1);
# * back faces, CR 702 table alone: 318 typed, 0 unmodelled;
# * the table alone types two front-face lines more than the gated run
#   (9832 / 6): equip-quality lines on faces whose MTGJSON keywords omit
#   equip -- the M3 gate otherwise removes nothing the table admits, it
#   only keeps CR 701 actions out by construction;
# * 99.46% of the (card, CR 702 keyword) pairs MTGJSON lists are found on a
#   typed line of either face (10905 / 10964). The rest are keywords printed
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


def _cost_refused(spec):
    """The cost owner's image of the spec's cost carries its 'unrecognised'
    refusal (`oracle_parser.parse_activation_cost`)."""
    return (spec.cost_snapshot is not None and "unrecognised"
            in dict(spec.cost_snapshot.items)["unpayable"])


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
            gate = cands if gated else None
            r = K.parse_keyword_line(para, candidates=gate)
            if r is None:
                # A1: a paragraph that opens with one of the face's keywords
                # and is not a sentence is a keyword line -- typed or
                # UNMODELLED, never handed on as resolution text.
                head = K._head(para, 0)
                assert head is None or para.endswith(".") or (
                    gate is not None and head[0] not in gate), (name, para)
                continue
            assert (r.value is None) != (r.unmodelled is None), (name, para, r)
            key = "back_" if face else ""
            if r.value is not None:
                assert r.value and all(isinstance(s, KeywordSpec) for s in r.value)
                assert 0 <= r.span[0] <= r.span[1] <= len(para)
                counts[key + "typed"] += 1
                # A line whose cost the cost owner could not read is typed
                # by the keyword table but refused by the cost owner: it
                # is reported apart and kept out of the typed share.
                refused = [s.name for s in r.value if _cost_refused(s)]
                if refused:
                    counts[key + "cost_refused"] += 1
                    for kw in refused:
                        counts["refused:" + kw] += 1
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
    typed_share = (counts["typed"] - counts["cost_refused"]) / lines
    back = counts["back_typed"] + counts["back_unmodelled"]
    back_share = (counts["back_typed"] - counts["back_cost_refused"]) / max(1, back)
    recall = found["found"] / max(1, found["listed"])
    print("\nkeyword lines (front, gated): typed=%d (of which cost refused by "
          "the cost owner=%d) unmodelled=%d (%.2f%% typed with an unrefused cost)"
          % (counts["typed"], counts["cost_refused"], counts["unmodelled"],
             100 * typed_share))
    print("keyword lines (back, table only): typed=%d (cost refused=%d) "
          "unmodelled=%d (%.2f%%)"
          % (counts["back_typed"], counts["back_cost_refused"],
             counts["back_unmodelled"], 100 * back_share))
    print("cost refused by keyword:", sorted(
        (k, v) for k, v in counts.items() if k.startswith("refused:")))
    print("front faces, table only (no gate): typed=%d unmodelled=%d"
          % (table_counts["typed"], table_counts["unmodelled"]))
    print("face keywords found on a typed line: %d/%d (%.2f%%)"
          % (found["found"], found["listed"], 100 * recall))
    print("unmodelled codes:", sorted(
        (k, v) for k, v in counts.items() if k.startswith("um:")))
    assert typed_share >= TYPED_SHARE_FLOOR
    assert back_share >= BACK_FACE_TYPED_SHARE_FLOOR
    assert recall >= FACE_KEYWORD_RECALL_FLOOR


# MTGJSON ``keywords`` entries that are not CR 702 keyword abilities and
# are not printed as a label ("<name> - ...") on the card: CR 701 keyword
# actions, CR 207.2c ability words a face prints under another face's name,
# and CR 111.10 / role token names. Closed: a new name here must be read and
# placed, so a CR 702 keyword the table lacks fails instead of vanishing.
NON_702_KEYWORDS = frozenset({
    # CR 701 keyword actions.
    "adapt", "airbend", "amass", "assemble", "behold", "blight", "bolster",
    "clash", "cloak", "collect evidence", "connive", "detain", "discover",
    "double", "earthbend", "endure", "exert", "explore", "fateseal",
    "fight", "forage", "goad", "heal", "incubate", "investigate", "learn",
    "manifest", "manifest dread", "meld", "mill", "monstrosity", "populate",
    "prepared", "proliferate", "recruit", "regenerate", "scry", "support", "surveil",
    "suspect", "transform", "triple", "venture into the dungeon",
    "waterbend",
    # CR 207.2c ability words.
    "coven", "delirium", "descend", "domain", "enrage", "heroic", "landfall",
    "magecraft", "metalcraft", "raid", "storied", "threshold",
    # CR 111.10 predefined tokens and role tokens.
    "food", "treasure", "role token",
})


def _texts_by_card(db):
    """Every printed face text of each pool card, under every name it is
    registered by (a split or DFC card's keywords cover all its faces)."""
    by_part = {}
    for name, entry in db._raw_data.items():
        t = db.cards.get(name)
        texts = [entry.get("text") or "",
                 getattr(t, "back_face_oracle", "") if t is not None else ""]
        for part in [name] + name.split(" // "):
            by_part.setdefault(part, []).extend(texts)
    return {name: [x for part in [name] + name.split(" // ")
                   for x in by_part.get(part, ())]
            for name in db._raw_data}


@pytest.mark.timeout(120)
def test_every_pool_keyword_is_in_the_cr_702_table_or_a_closed_non_702_list(card_db):
    """A1/M3: `keywords702` drops a keyword the table lacks, and its lines
    then read as resolution text. Every MTGJSON keyword in the pool is
    therefore a CR 702 table entry (after the variant folds), a label the
    card prints ("<name> - ..."), or one of the closed non-702 names."""
    from engine.effect_grammar import keywords as K
    texts = _texts_by_card(card_db)
    unplaced = {}
    for name, entry in card_db._raw_data.items():
        for kw in entry.get("keywords") or ():
            if K.canonical_keyword(kw) is not None:
                continue
            k = " ".join(kw.split()).casefold().replace("’", "'")
            if k in NON_702_KEYWORDS:
                continue
            label = re.compile(r"(?:^|[•\n-] ?)%s ?[-—]" % re.escape(k),
                               re.IGNORECASE)
            if any(label.search(t.replace("’", "'")) for t in texts[name]):
                continue
            unplaced.setdefault(kw, name)
    assert not unplaced, sorted(unplaced.items())


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

# card -> the cost owner's image of each costed item, in printed order:
# (unpayable, the non-zero mana pips). An 'unrecognised' refusal is the
# cost owner's verdict, pinned as such -- never read as a fully typed cost.
WITNESS_COSTS = {
    "Lava Dart": [(("sacrifice",), {})],
    "Cling to Dust": [(("exile",), {"generic": 3, "black": 1})],
    "Desperate Ritual": [((), {"generic": 1, "red": 1})],
    "Goryo's Vengeance": [((), {"generic": 2, "black": 1})],
    "Unburial Rites": [((), {"generic": 3, "white": 1})],
    "Faithless Looting": [((), {"generic": 2, "red": 1})],
    "Past in Flames": [((), {"generic": 4, "red": 1})],
    "Consult the Star Charts": [((), {"generic": 1, "blue": 1})],
    "Orim's Chant": [((), {"white": 1})],
    "Consign to Memory": [((), {"generic": 1})],
    "Vandalblast": [((), {"generic": 4, "red": 1})],
    "Solitude": [(("exile",), {})],
    "Subtlety": [(("exile",), {})],
    "Endurance": [(("exile",), {})],
    "Street Wraith": [((), {})],
    # The red pip is read; "collect evidence 6" is not a cost the owner
    # models, so it refuses the rest.
    "Detective's Phoenix": [(("unrecognised",), {"red": 1})],
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
    items, costs = [], []
    for para, r in typed:
        assert r.unmodelled is None, (para, r)
        assert r.rest_spans == (), (para, r)
        items += [(s.name, s.n, s.param, s.cost) for s in r.value]
        for s in r.value:
            assert (s.cost is None) == (s.cost_snapshot is None), s
            if s.cost_snapshot is not None:
                snap = dict(s.cost_snapshot.items)
                costs.append((snap["unpayable"],
                              {k: v for k, v in snap["mana"] if v}))
    assert items == WITNESSES[name]
    assert costs == WITNESS_COSTS.get(name, []), costs
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
