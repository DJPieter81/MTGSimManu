"""The mana units a typed mana ability produces (CR 106.1b, 605.1a).

A unit is the list of colours one produced mana could be: "{C}" is one
unit ["C"], "{G}{G}" two units ["G"], "one mana of any color" one unit of
all five colours. The shape `CardTemplate.mana_units` and
`CardTemplate.sacrifice_mana_units` hold, and that mana payment reads.

One owner of the derivation from typed specs: read by the effect views (a
card's own mana abilities, compared against the legacy parse) and by the
token owner (the abilities a token's creator grants it, CR 111.3). Reads
no oracle text: a choice's mana symbols are the typed payload's.
"""
from __future__ import annotations

import re
from itertools import combinations_with_replacement
from typing import Iterable, List, Optional

from .effect_spec import HostKind, ManaSpec, Verb, iter_specs

WUBRG = ("W", "U", "B", "R", "G")
_MANA_SYMBOL = re.compile(r"\{([^}]+)\}")
_UNIT_COLOURS = frozenset(WUBRG) | {"C"}


def mana_specs(h) -> list:
    """The ADD_MANA specs of host `h`."""
    return [s for s in iter_specs(h.specs)
            if s.verb is Verb.ADD_MANA and isinstance(s.payload, ManaSpec)]


def bundle_units(choice) -> Optional[list]:
    """The units of a choice between mana bundles ("{W}{W}, {W}{U}, or
    {U}{U}"): one unit per produced mana, each the union of the bundles'
    colours, when every bundle has the same size and the bundles are
    exactly the per-mana picks of that union (CR 106.1b: a unit is the
    colours one mana can be). None when the choice is not independent per
    mana ({R}{R} or {G}{G} never gives {R}{G}) or names a non-colour."""
    bundles = [tuple(_MANA_SYMBOL.findall(c)) for c in choice]
    sizes = {len(b) for b in bundles}
    if len(sizes) != 1 or 0 in sizes:
        return None
    n = sizes.pop()
    union = sorted({c for b in bundles for c in b})
    if not set(union) <= _UNIT_COLOURS:
        return None
    printed = {tuple(sorted(b)) for b in bundles}
    picks = set(combinations_with_replacement(union, n))
    if printed != picks:
        return None
    return [list(union) for _ in range(n)]


def units_of(spec) -> Optional[list]:
    """The units one ADD_MANA spec produces, or None when its choice is
    not a list of independent units (`bundle_units`)."""
    p = spec.payload
    if p.choice:
        if all(len(_MANA_SYMBOL.findall(c)) == 1 for c in p.choice):
            return [list(dict.fromkeys(c.strip("{}") for c in p.choice))]
        return bundle_units(p.choice)
    if p.any_color or "*" in p.symbols:
        return [sorted(WUBRG)] * max(1, len(p.symbols))
    return [[c] for c in p.symbols]


def host_units(h) -> Optional[list]:
    """Every unit a host's mana specs produce; None when one spec's
    choice is not a list of units."""
    units: List[list] = []
    for s in mana_specs(h):
        got = units_of(s)
        if got is None:
            return None
        units.extend(got)
    return units


def sacrifice_units(hosts: Iterable) -> list:
    """The units of the first "Sacrifice this <permanent>: Add <mana>"
    mana ability among `hosts`; [] when there is none, or when its choice
    is not a list of units."""
    for h in hosts:
        if h.kind is HostKind.MANA_ABILITY and h.cost is not None and \
                dict(h.cost.items).get("sacrifice_self"):
            return host_units(h) or []
    return []
