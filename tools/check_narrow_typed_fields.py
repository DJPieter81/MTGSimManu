#!/usr/bin/env python3
"""Narrow-typed-field ratchet — the guardrail for DISGUISED single-card patches.

The abstraction contract's `name == "X"` ratchet is at 0, but card-specific
knowledge can still be laundered past it: parse ONE card's exact oracle wording
at load time into a typed ``CardTemplate`` field, and a
``if card.name == "Omnath"`` conditional becomes a ``template.landfall_third_damage``
boolean populated by only Omnath — functionally identical, but invisible to
every source-grep ratchet and reads as "abstracted" in review.

This check is deliberately GENERIC: it holds no card names, field names, or
mechanic knowledge in its LOGIC. It measures, empirically over the whole card
DB, how many cards each typed mechanic field actually populates, and flags any
field whose real class is <= THRESHOLD cards — i.e. a "generic" field that is
really a single/near-single-card carrier. The grandfathered set of already-narrow
fields lives in a baseline JSON (DATA, like every other ratchet's baseline); the
ratchet fails only when a NEW narrow field appears that is not declared there.

A genuinely-unique card (Omnath, Endbringer) legitimately needs card-specific
handling — the contract even allows it with a reason. The point of this ratchet
is not to forbid that, but to make it VISIBLE, COUNTED, and DELIBERATE: a new
disguised patch fails CI until the author either (a) generalises the parser so it
covers its real Modern class (>THRESHOLD), or (b) adds a baseline entry with a
justification, turning a silent leak into a declared decision. The baseline may
only shrink — a field leaves it only by ceasing to be narrow.

Usage:
    python tools/check_narrow_typed_fields.py            # check (exit 1 on new narrow field)
    python tools/check_narrow_typed_fields.py --list     # every narrow field + its cards
    python tools/check_narrow_typed_fields.py --update    # regenerate the baseline
"""
from __future__ import annotations

import dataclasses as dc
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASELINE = os.path.join(_ROOT, "tools", "narrow_typed_fields_baseline.json")

# Importable standalone (run from anywhere) as well as under pytest: the engine
# and tests packages resolve only with the repo root on the path.
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# A field is "narrow" when the number of DB cards that populate it (with a
# non-default value) is <= this. 1-2 cards = a single-card patch in a generic
# costume; a real Modern mechanic populates far more.
THRESHOLD = 2

# Identity / cost / stats / raw-capture fields carry no MECHANIC — they are
# universal or pure data, never a laundered single-card rule. Excluded so the
# check reasons only about behaviour-bearing typed fields. (All of these
# populate far more than THRESHOLD anyway; the list is belt-and-suspenders and
# keeps the check's intent legible.)
_NON_MECHANIC_FIELDS = frozenset({
    "name", "cmc", "mana_cost", "power", "toughness", "loyalty",
    "card_types", "subtypes", "supertypes", "colors", "color_identity",
    "oracle_text", "keywords", "produces_mana", "mana_units", "tags",
    "abilities", "is_creature", "is_land", "is_spell",
    # Back-face raw capture (DFC data, not a mechanic rule).
    "back_face_oracle", "back_face_types", "back_face_subtypes",
    "back_face_power", "back_face_toughness", "back_face_keywords",
    "back_face_loyalty", "back_face_loyalty_abilities",
})


def _load_db():
    # Reuse the process-wide cached DB when running under pytest; fall back to a
    # fresh load standalone.
    try:
        from tests._card_db_cache import shared_card_database
        return shared_card_database()
    except Exception:
        from engine.card_database import CardDatabase
        return CardDatabase()


def _defaults():
    from engine.cards import CardTemplate
    out = {}
    for f in dc.fields(CardTemplate):
        if f.default is not dc.MISSING:
            out[f.name] = f.default
        elif f.default_factory is not dc.MISSING:  # type: ignore[misc]
            try:
                out[f.name] = f.default_factory()  # type: ignore[misc]
            except Exception:
                out[f.name] = None
        else:
            out[f.name] = None
    return out


def _is_populated(v, d) -> bool:
    """A field is populated when it carries a non-default, non-empty value."""
    if v is None or v == d:
        return False
    if isinstance(v, bool):
        return v  # only True counts
    if v in (0, 0.0, ""):
        return False
    if isinstance(v, (list, tuple, set, dict, frozenset)) and len(v) == 0:
        return False
    return True


def _mechanic_field_names():
    from engine.cards import CardTemplate
    return [f.name for f in dc.fields(CardTemplate)
            if f.name not in _NON_MECHANIC_FIELDS and not f.name.startswith("_")]


def narrow_fields(db, threshold: int = THRESHOLD):
    """Return {field_name: [card names that populate it]} for every mechanic
    field whose populated-card count is in [1, threshold]."""
    defaults = _defaults()
    cards = list(db.cards.values())
    out = {}
    for name in _mechanic_field_names():
        d = defaults.get(name)
        hits = [t.name for t in cards if _is_populated(getattr(t, name, None), d)]
        if 0 < len(hits) <= threshold:
            out[name] = sorted(hits)
    return out


def load_baseline():
    if not os.path.exists(_BASELINE):
        return {"threshold": THRESHOLD, "fields": {}}
    with open(_BASELINE) as f:
        return json.load(f)


def _write_baseline(narrow):
    data = {
        "_comment": (
            "Narrow-typed-field ratchet baseline. Each entry is a CardTemplate "
            "typed mechanic field whose real class is <= threshold cards — a "
            "single/near-single-card carrier. This is a DECLARED, grandfathered "
            "set: a NEW narrow field not listed here fails CI. Shrink it by "
            "generalising a parser (so its field covers its real Modern class) "
            "or removing a dead field. To justify a genuinely-unique card, set "
            "its 'reason'. See tools/check_narrow_typed_fields.py and "
            "docs/design/rules-foundation-sweep-tracker.md."),
        "threshold": THRESHOLD,
        "fields": {
            name: {"cards": cards, "reason": ""}
            for name, cards in sorted(narrow.items())
        },
    }
    with open(_BASELINE, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def main(argv):
    db = _load_db()
    narrow = narrow_fields(db)

    if "--list" in argv:
        print(f"Narrow typed mechanic fields (<= {THRESHOLD} cards), "
              f"{len(narrow)} total:")
        for name, cards in sorted(narrow.items(), key=lambda x: (len(x[1]), x[0])):
            print(f"  {len(cards)}  {name}: {cards}")
        return 0

    if "--update" in argv:
        _write_baseline(narrow)
        print(f"Wrote {_BASELINE} with {len(narrow)} narrow fields.")
        return 0

    baseline = load_baseline()
    allowed = set(baseline.get("fields", {}).keys())
    new_narrow = {n: c for n, c in narrow.items() if n not in allowed}

    if new_narrow:
        print("Narrow-typed-field ratchet FAILED — new disguised single-card "
              "field(s) not in the baseline:")
        for name, cards in sorted(new_narrow.items()):
            print(f"  {name}: populated by only {len(cards)} card(s) {cards}")
        print("\nA typed CardTemplate field that only ~1 card populates is a "
              "single-card hardcode in a generic costume. Either:")
        print("  1. Generalise the parser so the field covers its real Modern "
              "class (>{} cards), or".format(THRESHOLD))
        print("  2. If the card is genuinely unique, declare it: run "
              "`python tools/check_narrow_typed_fields.py --update` and fill in "
              "the 'reason' for the new entry.")
        return 1

    # Hygiene note: baseline entries that are no longer narrow can be pruned.
    stale = [n for n in allowed if n not in narrow]
    if stale:
        print("Narrow-typed-field ratchet OK — but these baseline entries are no "
              "longer narrow (prune them with --update): " + ", ".join(sorted(stale)))
    else:
        print(f"Narrow-typed-field ratchet OK — {len(narrow)} narrow fields, all "
              f"declared in the baseline (threshold {THRESHOLD}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
