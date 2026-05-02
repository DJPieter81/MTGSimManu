"""
Unified target solver — CR 601.2c compliance.

Consolidates target requirement parsing and legal-target queries
that were previously scattered across five sites in
``engine/cast_manager.py`` plus separate paths in
``engine/oracle_resolver.py`` and ``engine/stack.py``. See
``docs/proposals/2026-05-02_unified_target_solver.md``.

Phase 1 of the refactor lands this module with the parser + dataclass
only. Subsequent phases migrate call sites.

The parser is oracle-driven: every behavior derives from
``CardTemplate.oracle_text`` (and the typed predicates already exposed
on ``CardTemplate``). No card-name lookups, no per-card tables.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import FrozenSet, List, Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .cards import CardInstance


Zone = Literal[
    "battlefield",
    "graveyard",
    "hand",
    "library",
    "exile",
    "stack",
    "any",  # players / "any target"
]

OwnerScope = Literal["you", "opponent", "any"]


@dataclass(frozen=True)
class TargetRequirement:
    """One target requirement parsed from an oracle text fragment.

    A spell with multiple required targets has multiple
    ``TargetRequirement``s. Modal spells (Charms, Wear // Tear) have
    one per mode; the caller picks the chosen mode and asks the
    solver about that mode's requirements (Q2 default: solver returns
    the flat union and the legality query treats the union as
    "any-mode legal").

    Fields:
        zone:           where the target lives (battlefield/graveyard/...)
        types:          frozenset of acceptable type tokens; non-empty.
                        Examples: {"creature"}, {"creature","planeswalker"},
                        {"permanent"}, {"permanent_nonland"}, {"card"},
                        {"spell"}, {"player"}, {"any"} (Lightning Bolt
                        — creature OR planeswalker OR player).
        supertype:      "legendary" / "nonlegendary" / "basic" / "snow"
                        / None.
        owner_scope:    "you" / "opponent" / "any".
        is_optional:    True for "up to" / "you may target".
        count_min:      1 for plain "target X"; 0 for "up to N target X";
                        N for "N target X-s".
        count_max:      equal to count_min unless "up to N".
        raw_phrase:     original oracle substring matched, for debugging.
    """
    zone: Zone
    types: FrozenSet[str]
    supertype: Optional[str] = None
    owner_scope: OwnerScope = "any"
    is_optional: bool = False
    count_min: int = 1
    count_max: int = 1
    raw_phrase: str = ""


# ── Regex catalogue ─────────────────────────────────────────────────
#
# All patterns share these design rules:
#   1. Match against the lowercased oracle text.
#   2. Capture the supertype word ("legendary"/"nonlegendary") when
#      adjacent to the target type, so callers can narrow the legal
#      set without re-parsing.
#   3. Order in ``parse()`` is most-specific-first. The parser strips
#      each match from the working string before testing the next
#      pattern, so a permissive late pattern (e.g. "target creature")
#      cannot double-fire on the same phrase as "target creature you
#      control".

_OPTIONAL_PREFIX_WINDOW = 30  # chars; matches existing _is_optional()

_OPTIONAL_MARKERS = (
    "up to",
    "you may",
    "may exile",
    "may return",
    "may target",
)


def _is_optional_at(oracle_l: str, idx: int) -> bool:
    """Mirror of ``cast_manager._battlefield_legal_targets._is_optional``.

    Looks back ``_OPTIONAL_PREFIX_WINDOW`` characters from ``idx`` for
    any of the optional markers. The window is small enough that an
    unrelated "you may" in an earlier sentence does not falsely make
    a later "target X" optional.
    """
    if idx < 0:
        return False
    prefix = oracle_l[max(0, idx - _OPTIONAL_PREFIX_WINDOW):idx]
    return any(marker in prefix for marker in _OPTIONAL_MARKERS)


# Graveyard-target pattern — covers Goryo's Vengeance, Unburial Rites,
# Persist (the card), Reanimate, Dread Return, etc. Mirrors the regex
# already in cast_manager.can_cast (line 158). The trailing "card" /
# "cards" is optional so "target card from a graveyard" matches with
# the type word being "card" itself.
_GRAVEYARD_PATTERN = re.compile(
    r"target\s+((?:non)?legendary\s+)?"
    r"(creature|instant|sorcery|artifact|enchantment|"
    r"planeswalker|land|permanent|card)"
    r"(?:\s+cards?)?\s+(?:from|in)\s+(your|a)\s+graveyard"
)

# Loose graveyard fallback — same source-zone phrase but without the
# "card" marker. Mirrors the slack pattern in cast_manager.can_cast
# (line 173). Used only when the strict pattern fails AND the oracle
# contains a graveyard zone phrase.
_GRAVEYARD_LOOSE_PATTERN = re.compile(
    r"target\s+((?:non)?legendary\s+)?"
    r"(creature|instant|sorcery|artifact|enchantment|"
    r"planeswalker|land|permanent|card)"
)
_GRAVEYARD_ZONE_HINTS = (
    "from your graveyard",
    "from a graveyard",
    "in your graveyard",
)

# Battlefield compound patterns — same dispatch order as the existing
# _battlefield_legal_targets() helper.
_BATTLEFIELD_COMPOUND = [
    ("target artifact or creature",     frozenset({"artifact", "creature"})),
    ("target artifact or enchantment",  frozenset({"artifact", "enchantment"})),
    ("target creature or planeswalker", frozenset({"creature", "planeswalker"})),
]

# "target [non]land permanent" / "target permanent" — supertype-aware.
_PERMANENT_PATTERN = re.compile(
    r"target\s+(nonland\s+)?permanent\b"
)

# Single-type battlefield targets (excluding "creature" — that one needs
# special handling for "target creature you control" / "an opponent
# controls").
_SINGLE_TYPE_BATTLEFIELD = [
    ("artifact",     re.compile(r"\btarget\s+artifact\b")),
    ("enchantment",  re.compile(r"\btarget\s+enchantment\b")),
    ("planeswalker", re.compile(r"\btarget\s+planeswalker\b")),
    ("land",         re.compile(r"\btarget\s+(?:non\w+\s+)?land\b")),
]

# Creature with explicit owner scope. Order: scoped first, then bare.
_CREATURE_YOU_CONTROL = re.compile(r"\btarget\s+creature\s+you\s+control\b")
_CREATURE_OPP_CONTROL = re.compile(
    r"\btarget\s+creature\s+(?:an\s+opponent|that\s+player)\s+controls?\b"
)
_CREATURE_BARE = re.compile(r"\btarget\s+creature\b")

# "any target" — Lightning Bolt, Galvanic Discharge's plain mode, etc.
# Always legal (players are always present); we still emit a record so
# callers can introspect requirements.
_ANY_TARGET = re.compile(r"\bany\s+target\b")

# Player / opponent targeting.
_PLAYER_TARGET = re.compile(r"\btarget\s+(player|opponent)\b")

# Spell targeting — counterspells.
_SPELL_TARGET = re.compile(
    r"\btarget\s+(?:(creature|instant|sorcery|noncreature)\s+)?spell\b"
)


def parse(oracle_text: str) -> List[TargetRequirement]:
    """Parse all target requirements from an oracle text.

    Returns an empty list when no targets are required (draw, mill,
    lifegain, mass effects with no "target" keyword). Caller treats
    a non-empty list as a logical AND of required targets — except
    for entries with ``is_optional=True``, which the caller may skip.

    See ``docs/proposals/2026-05-02_unified_target_solver.md`` for
    the contract; Phase 1 covers the patterns currently checked by
    ``cast_manager._battlefield_legal_targets`` and the inline
    graveyard-target dispatcher in ``cast_manager.can_cast``.
    """
    if not oracle_text:
        return []
    oracle_l = oracle_text.lower()
    out: List[TargetRequirement] = []

    # ── 1. Graveyard targets ────────────────────────────────────────
    gy_match = _GRAVEYARD_PATTERN.search(oracle_l)
    if gy_match is None and any(h in oracle_l for h in _GRAVEYARD_ZONE_HINTS):
        gy_match = _GRAVEYARD_LOOSE_PATTERN.search(oracle_l)
    if gy_match is not None:
        super_word = (gy_match.group(1) or "").strip() or None
        type_word = gy_match.group(2)
        # Source-zone scope. The strict pattern captures (your|a) as
        # group 3; the loose fallback has no group 3, so we infer from
        # the explicit zone hint phrase ("from a graveyard" → any,
        # "from your graveyard" / "in your graveyard" → you).
        groups = gy_match.groups()
        if len(groups) >= 3 and groups[2]:
            zone_scope_word = groups[2]
        elif "from a graveyard" in oracle_l or "in a graveyard" in oracle_l:
            zone_scope_word = "a"
        else:
            zone_scope_word = "your"
        owner: OwnerScope = "you" if zone_scope_word == "your" else "any"
        types = _types_for_word(type_word)
        out.append(TargetRequirement(
            zone="graveyard",
            types=types,
            supertype=super_word,
            owner_scope=owner,
            is_optional=_is_optional_at(oracle_l, gy_match.start()),
            count_min=1,
            count_max=1,
            raw_phrase=gy_match.group(0),
        ))
        return out

    # ── 2. Stack-target spells (counterspells) ──────────────────────
    spell_match = _SPELL_TARGET.search(oracle_l)
    if spell_match is not None:
        sub = spell_match.group(1) or ""
        if sub == "noncreature":
            types = frozenset({"noncreature_spell"})
        elif sub:
            types = frozenset({f"{sub}_spell"})
        else:
            types = frozenset({"spell"})
        out.append(TargetRequirement(
            zone="stack",
            types=types,
            owner_scope="any",
            is_optional=_is_optional_at(oracle_l, spell_match.start()),
            raw_phrase=spell_match.group(0),
        ))
        # A counterspell may also target a permanent in modal text
        # (rare). Keep scanning so modal patterns are not lost.

    # ── 3. Battlefield compound targets ─────────────────────────────
    for phrase, types in _BATTLEFIELD_COMPOUND:
        idx = oracle_l.find(phrase)
        if idx >= 0:
            out.append(TargetRequirement(
                zone="battlefield",
                types=types,
                owner_scope="any",
                is_optional=_is_optional_at(oracle_l, idx),
                raw_phrase=phrase,
            ))

    # ── 4. "target permanent" / "target nonland permanent" ──────────
    perm_match = _PERMANENT_PATTERN.search(oracle_l)
    if perm_match is not None:
        kind = "permanent_nonland" if perm_match.group(1) else "permanent"
        out.append(TargetRequirement(
            zone="battlefield",
            types=frozenset({kind}),
            owner_scope="any",
            is_optional=_is_optional_at(oracle_l, perm_match.start()),
            raw_phrase=perm_match.group(0),
        ))

    # ── 5. Creature with owner scope ────────────────────────────────
    you_ctrl = _CREATURE_YOU_CONTROL.search(oracle_l)
    opp_ctrl = _CREATURE_OPP_CONTROL.search(oracle_l)
    bare_creature = _CREATURE_BARE.search(oracle_l)
    if you_ctrl is not None:
        out.append(TargetRequirement(
            zone="battlefield",
            types=frozenset({"creature"}),
            owner_scope="you",
            is_optional=_is_optional_at(oracle_l, you_ctrl.start()),
            raw_phrase=you_ctrl.group(0),
        ))
    elif opp_ctrl is not None:
        out.append(TargetRequirement(
            zone="battlefield",
            types=frozenset({"creature"}),
            owner_scope="opponent",
            is_optional=_is_optional_at(oracle_l, opp_ctrl.start()),
            raw_phrase=opp_ctrl.group(0),
        ))
    elif (bare_creature is not None
          and not _is_inside_compound(oracle_l, bare_creature.start())
          and not _is_target_creature_spell(oracle_l, bare_creature.start())):
        out.append(TargetRequirement(
            zone="battlefield",
            types=frozenset({"creature"}),
            owner_scope="any",
            is_optional=_is_optional_at(oracle_l, bare_creature.start()),
            raw_phrase=bare_creature.group(0),
        ))

    # ── 6. Single-type battlefield targets ──────────────────────────
    for token, pat in _SINGLE_TYPE_BATTLEFIELD:
        m = pat.search(oracle_l)
        if m is None:
            continue
        # Skip if this match is part of an already-emitted compound
        # (e.g. "target artifact or creature" matches "target artifact"
        # too). Compound entries always come first in `out`.
        if _already_covered_by_compound(out, token):
            continue
        out.append(TargetRequirement(
            zone="battlefield",
            types=frozenset({token}),
            owner_scope="any",
            is_optional=_is_optional_at(oracle_l, m.start()),
            raw_phrase=m.group(0),
        ))

    # ── 7. "any target" / "target player" / "target opponent" ───────
    if _ANY_TARGET.search(oracle_l):
        m = _ANY_TARGET.search(oracle_l)
        assert m is not None
        out.append(TargetRequirement(
            zone="any",
            types=frozenset({"any"}),
            owner_scope="any",
            is_optional=_is_optional_at(oracle_l, m.start()),
            raw_phrase=m.group(0),
        ))
    player_match = _PLAYER_TARGET.search(oracle_l)
    if player_match is not None:
        scope: OwnerScope = (
            "opponent" if player_match.group(1) == "opponent" else "any"
        )
        out.append(TargetRequirement(
            zone="any",
            types=frozenset({"player"}),
            owner_scope=scope,
            is_optional=_is_optional_at(oracle_l, player_match.start()),
            raw_phrase=player_match.group(0),
        ))

    return out


def _types_for_word(type_word: str) -> FrozenSet[str]:
    """Map a captured type word from a graveyard-target match to the
    canonical type token set used by the legality query."""
    return frozenset({type_word})


def _is_target_creature_spell(oracle_l: str, idx: int) -> bool:
    """True when the "target creature" hit at ``idx`` is actually
    "target creature spell" (a stack-zone counterspell phrase
    already emitted by the spell-target dispatch). Avoids
    double-counting bare creature for Disdainful Stroke / Essence
    Capture-style counterspells."""
    after = oracle_l[idx + len("target creature"):idx + len("target creature") + 6]
    return after.startswith(" spell")


def _is_inside_compound(oracle_l: str, idx: int) -> bool:
    """Is ``idx`` (start of a "target creature" hit) inside a compound
    phrase like "target artifact or creature" or "target creature or
    planeswalker"? Avoids double-counting bare creature when the
    compound already fired."""
    for compound, _ in _BATTLEFIELD_COMPOUND:
        if "creature" not in compound:
            continue
        c_idx = oracle_l.find(compound)
        if c_idx < 0:
            continue
        if c_idx <= idx <= c_idx + len(compound):
            return True
    return False


def _already_covered_by_compound(
    existing: List[TargetRequirement], token: str
) -> bool:
    """For a single-type token like "artifact", return True if a
    compound TargetRequirement already in ``existing`` lists that
    type. Avoids duplicate emission when "target artifact or
    creature" matches both the compound and the single-type
    "artifact" pattern."""
    for req in existing:
        if len(req.types) > 1 and token in req.types:
            return True
    return False
