#!/usr/bin/env python3
"""Deck colour feasibility -- blind-spot class B5, data validity
(docs/design/2026-11-15_root_cause_blindspot_analysis.md).

A registered list must be able to pay for what it plays. A nonland card
whose coloured pips no mana source of its own deck can produce is a dead
card the field runs never flag: it never errors; it just never gets cast.
Precedent: a Vengevine in a list with no green source, found only by a
human reading a field-diagnosis replay.

The deck's colours are the closure of:
  * each land's tap colours (`mana_units` when parsed, else
    `produces_mana`; an enter-only mana ability is not a tap colour);
  * each fetch land's targets: the tap colours of the deck's own lands that
    carry a basic land type the fetch searches for;
  * each nonland mana source whose own cost those colours already pay
    (iterated to a fixed point).

A card passes when its mana cost is payable from those colours (a
Phyrexian pip may be paid with life; a hybrid pip needs one of its
options), or when it has a typed alternative way to be used whose cost is
payable (cycling, evoke). Anything else must be declared in
`tools/colour_feasibility_declared.json` with the reason the deck plays it
without casting it (a reanimation target, a card that returns itself from
the graveyard ...). The gate is hard:
  * an undeclared infeasible card fails;
  * a declared card that is now feasible (or no longer in the list) fails as
    stale, so the declaration file cannot rot.

Known under-approximation: a source whose colours depend on the board
(Mox Opal's metalcraft, a ritual's added mana) adds no colour here. It can
only produce a false "infeasible", which the declaration file absorbs with
its reason; it never hides a real one.

    python tools/check_colour_feasibility.py           # check (CI + pytest)
    python tools/check_colour_feasibility.py --list    # each deck's colours
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DECLARED_FILE = ROOT / "tools" / "colour_feasibility_declared.json"

_BASIC_TYPE = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain",
               "G": "Forest"}
_PIP_FIELDS = (("white", "W"), ("blue", "U"), ("black", "B"), ("red", "R"),
               ("green", "G"), ("colorless", "C"))


def tap_colours(template) -> set:
    """The colours a source taps for, from its printed data."""
    units = getattr(template, "mana_units", None)
    if units:
        return {c for unit in units for c in unit}
    return set(getattr(template, "produces_mana", None) or ())


def required(cost) -> list:
    """The pips of `cost` that need a source, each as the set of letters
    that pay it (a hybrid pip lists its options; a digit option means
    generic mana pays it, so it needs no source)."""
    if cost is None:
        return []
    phyrexian = getattr(cost, "phyrexian", None) or {}
    need = []
    for attr, letter in _PIP_FIELDS:
        n = getattr(cost, attr, 0) - phyrexian.get(letter, 0)
        if n > 0:
            need.append({letter})
    for pip in getattr(cost, "hybrid", None) or ():
        if any(str(o).isdigit() for o in pip):
            continue
        need.append(set(pip))
    return need


def payable(cost, colours: set) -> bool:
    return all(options & colours for options in required(cost))


def _alternative_payable(template, colours: set) -> bool:
    """A typed alternative way to use the card, whose cost is payable."""
    cyc = getattr(template, "cycling_cost_data", None)
    if cyc and set(cyc.get("colors") or ()) <= colours:
        return True
    evoke = getattr(template, "evoke_cost", None)
    if evoke is not None and payable(evoke, colours):
        return True
    return False


def deck_colours(templates) -> set:
    lands = [t for t in templates if t.is_land]
    colours = set()
    for land in lands:
        colours |= tap_colours(land)
    for land in lands:
        fetch = getattr(land, "fetchland", None)
        if not fetch:
            continue
        types = {_BASIC_TYPE[c] for c in fetch.colors if c in _BASIC_TYPE}
        for target in lands:
            if types & set(target.subtypes or ()):
                colours |= tap_colours(target)
    grew = True
    while grew:
        grew = False
        for t in templates:
            if t.is_land:
                continue
            extra = tap_colours(t) - colours
            if extra and payable(t.mana_cost, colours):
                colours |= extra
                grew = True
    return colours


def findings(db=None, decks=None) -> dict:
    """{deck: {card: missing letters}} for every infeasible nonland card
    of every registered list (mainboard and sideboard, paid from the
    mainboard's sources)."""
    if db is None:
        from engine.card_database import CardDatabase
        db = CardDatabase.shared()
    if decks is None:
        from decks.modern_meta import MODERN_DECKS
        decks = MODERN_DECKS
    out = {}
    for deck, parts in decks.items():
        main = [db.get_card(n) for n in parts.get("mainboard", {})]
        side = [db.get_card(n) for n in parts.get("sideboard", {})]
        colours = deck_colours([t for t in main if t is not None])
        bad = {}
        for t in main + side:
            if t is None or t.is_land:
                continue
            if payable(t.mana_cost, colours) or \
                    _alternative_payable(t, colours):
                continue
            missing = sorted({c for opts in required(t.mana_cost)
                              if not opts & colours for c in opts})
            bad[t.name] = missing
        if bad:
            out[deck] = bad
    return out


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    found = findings()
    if "--list" in args:
        for deck, bad in found.items():
            print(f"{deck}: {bad}")
        return 0
    declared = {d: v for d, v in json.loads(DECLARED_FILE.read_text())
                .items() if not d.startswith("_")}
    undeclared = {(d, c): m for d, bad in found.items()
                  for c, m in bad.items() if c not in declared.get(d, {})}
    stale = [(d, c) for d, cards in declared.items() for c in cards
             if c not in found.get(d, {})]
    if undeclared or stale:
        print("Colour-feasibility check FAILED (blind-spot class B5):")
        for (d, c), m in sorted(undeclared.items()):
            print(f"  {d}: {c} needs {m}, which no source in the list "
                  "produces -- fix the list, or declare why it is played "
                  f"uncast in {DECLARED_FILE.name}")
        for d, c in sorted(stale):
            print(f"  {d}: {c} is declared but no longer infeasible (or no "
                  "longer in the list) -- remove the stale declaration")
        return 1
    n = sum(len(v) for v in found.values())
    print(f"Colour feasibility OK -- {n} declared uncast card(s) across "
          f"{len(found)} deck(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
