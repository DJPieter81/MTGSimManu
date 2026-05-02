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
    from .game_state import GameState


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
    ``TargetRequirement``s. Modal spells (Drown in the Loch,
    Charms, Wear // Tear) emit one requirement per mode and group
    them under a shared ``mode_group`` int — see CR 700.2.

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
        mode_group:     None for non-modal spells (each requirement is
                        AND-required). For modal spells ("Choose one"
                        / "Choose two" / "Choose up to N"), all
                        requirements emitted from the same modal block
                        share the same int id. The legality query
                        treats requirements with matching mode_group
                        as OR — at least one must be legal — while
                        mode_group=None requirements remain AND.
        raw_phrase:     original oracle substring matched, for debugging.
    """
    zone: Zone
    types: FrozenSet[str]
    supertype: Optional[str] = None
    owner_scope: OwnerScope = "any"
    is_optional: bool = False
    count_min: int = 1
    count_max: int = 1
    mode_group: Optional[int] = None
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


_MODAL_PREFIX_RE = re.compile(
    r"choose\s+(?:one|two|three|up\s+to\s+(?:one|two|three|\d+))\b"
)


def _detect_mode_group(oracle_l: str, hit_idx: int,
                       modal_section_start: int) -> Optional[int]:
    """If a "Choose one — / Choose up to two —" prefix appears
    before ``hit_idx`` and the section continues to (or past)
    ``hit_idx``, return a non-None mode-group identifier (int).
    Otherwise return None.

    For Phase 1's scope, every modal section maps to a single shared
    int id (1). The parser does not yet support nested modal blocks
    (no Modern card has them).
    """
    if modal_section_start < 0 or hit_idx < modal_section_start:
        return None
    return 1


def parse(oracle_text: str) -> List[TargetRequirement]:
    """Parse all target requirements from an oracle text.

    Returns an empty list when no targets are required (draw, mill,
    lifegain, mass effects with no "target" keyword). The caller
    treats requirements with ``mode_group=None`` as logical AND
    (every non-optional one must be legal). Requirements with the
    same non-None ``mode_group`` are OR — at least one must be
    legal (CR 700.2 — modal spells need only one chosen mode).

    See ``docs/proposals/2026-05-02_unified_target_solver.md``.
    """
    if not oracle_text:
        return []
    oracle_l = oracle_text.lower()
    # Detect the start of a modal block. We use the first occurrence
    # of "Choose one —" / "Choose up to two —" / etc; everything
    # after that index is in the modal section. Cards with both a
    # non-modal prefix ("Sacrifice an artifact, then choose one —")
    # and a modal block work because the prefix's targets parse
    # before the modal index and get mode_group=None correctly.
    m_modal = _MODAL_PREFIX_RE.search(oracle_l)
    modal_start = m_modal.start() if m_modal else -1
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
            mode_group=_detect_mode_group(oracle_l, gy_match.start(),
                                          modal_start),
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
            mode_group=_detect_mode_group(oracle_l, spell_match.start(),
                                          modal_start),
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
                mode_group=_detect_mode_group(oracle_l, idx, modal_start),
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
            mode_group=_detect_mode_group(oracle_l, perm_match.start(),
                                          modal_start),
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
            mode_group=_detect_mode_group(oracle_l, you_ctrl.start(),
                                          modal_start),
            raw_phrase=you_ctrl.group(0),
        ))
    elif opp_ctrl is not None:
        out.append(TargetRequirement(
            zone="battlefield",
            types=frozenset({"creature"}),
            owner_scope="opponent",
            is_optional=_is_optional_at(oracle_l, opp_ctrl.start()),
            mode_group=_detect_mode_group(oracle_l, opp_ctrl.start(),
                                          modal_start),
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
            mode_group=_detect_mode_group(oracle_l, bare_creature.start(),
                                          modal_start),
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
            mode_group=_detect_mode_group(oracle_l, m.start(), modal_start),
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
            mode_group=_detect_mode_group(oracle_l, m.start(), modal_start),
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
            mode_group=_detect_mode_group(oracle_l, player_match.start(),
                                          modal_start),
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


# ── Legality queries ────────────────────────────────────────────────


def _matches_type(card: "CardInstance", types: FrozenSet[str],
                  zone: Zone) -> bool:
    """Predicate: does ``card`` satisfy any token in ``types``?

    Mapping by zone:
      battlefield — checks card_types + supertype-derived booleans
      graveyard / hand / library / exile — same; "card" is the universal
        token (no filter).
      stack — special: tokens are *_spell variants checked elsewhere.
    """
    from .cards import CardType

    t = card.template
    for tok in types:
        if tok == "creature" and t.is_creature:
            return True
        if tok == "artifact" and CardType.ARTIFACT in t.card_types:
            return True
        if tok == "enchantment" and CardType.ENCHANTMENT in t.card_types:
            return True
        if tok == "planeswalker" and CardType.PLANESWALKER in t.card_types:
            return True
        if tok == "land" and t.is_land:
            return True
        if tok == "instant" and t.is_instant:
            return True
        if tok == "sorcery" and t.is_sorcery:
            return True
        if tok == "permanent":
            # Any permanent (battlefield card). On the battlefield,
            # all cards are permanents by definition.
            if zone == "battlefield":
                return True
            # In graveyard zone, "permanent" means
            # creature/artifact/enchantment/planeswalker/land card —
            # i.e. anything except instant/sorcery.
            if not (t.is_instant or t.is_sorcery):
                return True
        if tok == "permanent_nonland":
            if zone == "battlefield":
                return not t.is_land
            if not (t.is_instant or t.is_sorcery or t.is_land):
                return True
        if tok == "card":
            return True  # No type filter
    return False


def _matches_supertype(card: "CardInstance",
                       supertype: Optional[str]) -> bool:
    """Filter by supertype. None = no filter. Mirrors the legendary /
    nonlegendary check in cast_manager.can_cast (line 232)."""
    if supertype is None:
        return True
    from .cards import Supertype

    supertypes = getattr(card.template, "supertypes", []) or []
    if supertype == "legendary":
        return Supertype.LEGENDARY in supertypes
    if supertype == "nonlegendary":
        return Supertype.LEGENDARY not in supertypes
    if supertype == "basic":
        return Supertype.BASIC in supertypes
    if supertype == "snow":
        return Supertype.SNOW in supertypes
    return True


def _matches_owner(card: "CardInstance", controller: int,
                   owner_scope: OwnerScope) -> bool:
    """Owner / controller filter. CR 109.4: cards on the battlefield
    have a controller; cards in non-battlefield zones have an owner.
    For zone-agnostic call sites we use ``controller`` if set,
    otherwise fall back to ``owner``."""
    if owner_scope == "any":
        return True
    card_ctrl = getattr(card, "controller", None)
    if card_ctrl is None:
        card_ctrl = card.owner
    if owner_scope == "you":
        return card_ctrl == controller
    if owner_scope == "opponent":
        return card_ctrl != controller
    return True


def _zone_cards(game: "GameState", controller: int,
                req: TargetRequirement) -> List["CardInstance"]:
    """Return the candidate-card list for a TargetRequirement,
    pre-filtered by zone + owner scope but NOT by type/supertype."""
    cards: List["CardInstance"] = []
    if req.zone == "battlefield":
        for i, p in enumerate(game.players):
            if req.owner_scope == "you" and i != controller:
                continue
            if req.owner_scope == "opponent" and i == controller:
                continue
            cards.extend(p.battlefield)
    elif req.zone == "graveyard":
        for i, p in enumerate(game.players):
            if req.owner_scope == "you" and i != controller:
                continue
            if req.owner_scope == "opponent" and i == controller:
                continue
            cards.extend(p.graveyard)
    elif req.zone == "hand":
        for i, p in enumerate(game.players):
            if req.owner_scope == "you" and i != controller:
                continue
            if req.owner_scope == "opponent" and i == controller:
                continue
            cards.extend(p.hand)
    elif req.zone == "exile":
        for i, p in enumerate(game.players):
            if req.owner_scope == "you" and i != controller:
                continue
            if req.owner_scope == "opponent" and i == controller:
                continue
            cards.extend(p.exile)
    elif req.zone == "library":
        for i, p in enumerate(game.players):
            if req.owner_scope == "you" and i != controller:
                continue
            if req.owner_scope == "opponent" and i == controller:
                continue
            cards.extend(p.library)
    elif req.zone == "stack":
        for item in game.stack.items:
            cards.append(item.source)
    elif req.zone == "any":
        # Players are always present; nothing to enumerate as a card.
        pass
    return cards


def _spell_token_matches(item_source: "CardInstance",
                         types: FrozenSet[str]) -> bool:
    """Stack-zone spell token check. ``types`` contains tokens like
    "spell", "creature_spell", "noncreature_spell"."""
    t = item_source.template
    for tok in types:
        if tok == "spell":
            return True
        if tok == "creature_spell" and t.is_creature:
            return True
        if tok == "noncreature_spell" and not t.is_creature:
            return True
        if tok == "instant_spell" and t.is_instant:
            return True
        if tok == "sorcery_spell" and t.is_sorcery:
            return True
    return False


def has_legal_target(game: "GameState", controller: int,
                     req: TargetRequirement,
                     exclude: Optional["CardInstance"] = None) -> bool:
    """CR 601.2c — does at least one legal target exist for this
    requirement in the current game state?

    For ``zone == "any"`` (Lightning Bolt's "any target" / "target
    player"), the predicate is always True: players are always
    present. The caller is responsible for refusing "any target"
    casts when the player would, e.g., gain life from the cast (no
    legal targets among creatures/planeswalkers AND damaging the
    controller is irrational); that policy lives in the AI layer.

    ``exclude`` is the spell being cast — for graveyard-cast spells
    (Persist), the spell on the stack is no longer a legal target in
    its source zone (CR 601.2c).
    """
    if req.zone == "any":
        return True

    if req.zone == "stack":
        for item in game.stack.items:
            if exclude is not None and item.source is exclude:
                continue
            if _spell_token_matches(item.source, req.types):
                return True
        return False

    for card in _zone_cards(game, controller, req):
        if exclude is not None and card is exclude:
            continue
        if not _matches_type(card, req.types, req.zone):
            continue
        if not _matches_supertype(card, req.supertype):
            continue
        # Owner already pre-filtered by _zone_cards.
        return True
    return False


def enumerate_legal_targets(game: "GameState", controller: int,
                            req: TargetRequirement,
                            exclude: Optional["CardInstance"] = None,
                            ) -> List["CardInstance"]:
    """Same predicate as ``has_legal_target`` but returns every
    candidate. Phase 6 will use this for AI scoring (best-target
    pick); Phase 5 uses it for stack fizzle-on-illegal-target
    re-validation at resolve time (CR 608.2b).

    Returns an empty list for ``zone == "any"`` because there is no
    card-instance candidate (the target is a player). Callers that
    need to enumerate players should not call this function.
    """
    if req.zone == "any":
        return []

    if req.zone == "stack":
        out: List["CardInstance"] = []
        for item in game.stack.items:
            if exclude is not None and item.source is exclude:
                continue
            if _spell_token_matches(item.source, req.types):
                out.append(item.source)
        return out

    out = []
    for card in _zone_cards(game, controller, req):
        if exclude is not None and card is exclude:
            continue
        if not _matches_type(card, req.types, req.zone):
            continue
        if not _matches_supertype(card, req.supertype):
            continue
        out.append(card)
    return out


def has_legal_target_for_spell(game: "GameState", controller: int,
                               requirements: List[TargetRequirement],
                               exclude: Optional["CardInstance"] = None,
                               ) -> bool:
    """Convenience wrapper used by Phase 3 cast_manager migration.

    A spell is castable iff:
      - every non-modal (``mode_group=None``) non-optional
        requirement has at least one legal candidate (logical AND),
        AND
      - for each modal group (a set of requirements sharing the same
        non-None ``mode_group``), at least one requirement in the
        group has a legal candidate (logical OR per CR 700.2 — the
        caster needs only one chosen mode).

    Optional requirements ("up to N target X") never block the cast,
    independent of mode_group.

    Returns True for spells with no requirements (no "target"
    keyword in the oracle).
    """
    # Bucket modal requirements by mode_group; non-modal go through
    # the AND path directly.
    modal_groups: dict = {}
    for req in requirements:
        if req.is_optional:
            continue
        if req.mode_group is None:
            if not has_legal_target(game, controller, req, exclude=exclude):
                return False
        else:
            modal_groups.setdefault(req.mode_group, []).append(req)

    # For each modal group, at least one requirement must be legal.
    for group_id, group_reqs in modal_groups.items():
        if not any(has_legal_target(game, controller, r, exclude=exclude)
                   for r in group_reqs):
            return False
    return True
