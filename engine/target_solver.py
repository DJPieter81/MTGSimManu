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
from typing import FrozenSet, List, Literal, Optional, Tuple, TYPE_CHECKING

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
        subtype:        lowercase SUBTYPE the target must have ("forest",
                        "gate", "goblin", "equipment") or None. Distinct
                        from `types` (card types) and `supertype`; a
                        subtype restriction narrows an otherwise-wildcard
                        "permanent" requirement — "Untap target Forest"
                        is types={"permanent"} + subtype="forest".
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
    subtype: Optional[str] = None
    owner_scope: OwnerScope = "any"
    is_optional: bool = False
    count_min: int = 1
    count_max: int = 1
    mode_group: Optional[int] = None
    raw_phrase: str = ""
    max_mana_value: Optional[int] = None
    # "with mana value X or less": the ceiling is the X the caster pays,
    # chosen before targets (CR 601.2b/c). Bound at enumeration time by
    # the `x_ceiling` the caller derives from its affordable X; with no
    # ceiling supplied the requirement is unbounded (resolution-time
    # checks still apply).
    max_mana_value_is_x: bool = False


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
    r"target\s+(?P<super>(?:non)?legendary\s+)?"
    r"(?P<type>creature|instant|sorcery|artifact|enchantment|"
    r"planeswalker|land|permanent|card)"
    r"(?:\s+cards?)?"
    # Optional mana-value ceiling between the type word and the
    # graveyard-zone phrase (Unearth: "target creature card WITH MANA
    # VALUE 3 OR LESS from your graveyard"). Not tied to any card
    # name — any reanimation-shaped effect with this clause parses it.
    r"(?:\s+with\s+(?:total\s+)?mana\s+value\s+(?P<mv>\d+)\s+or\s+less)?"
    r"\s+(?:from|in)\s+(?P<scope>your|a)\s+graveyard"
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

# Battlefield compound targets: a list of two or more permanent types
# joined by commas and a final "or" ("target artifact or creature",
# "target artifact, creature, enchantment, or planeswalker") is the union
# of its types — one grammar for every length, with the optional
# controller scope that may follow it.
_TYPE_WORD = r"(?:artifact|creature|enchantment|planeswalker|land|battle)"
_COMPOUND_RE = re.compile(
    rf"\btarget\s+({_TYPE_WORD}(?:\s*,\s*(?:or\s+)?{_TYPE_WORD})*\s*,?\s+or\s+{_TYPE_WORD})"
    r"(\s+(?:an\s+opponent|that\s+player)\s+controls?|\s+you\s+control)?\b")


def _compound_matches(oracle_l: str):
    for m in _COMPOUND_RE.finditer(oracle_l):
        types = frozenset(re.findall(_TYPE_WORD, m.group(1)))
        scope_phrase = (m.group(2) or "").strip()
        scope = ("you" if scope_phrase.startswith("you")
                 else "opponent" if scope_phrase else "any")
        yield m, types, scope


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
# Compound "instant or sorcery spell" is checked first so the
# single-type regex below does not greedily capture only "instant".
_SPELL_TARGET_INSTANT_OR_SORCERY = re.compile(
    r"\btarget\s+instant\s+or\s+sorcery\s+spell\b"
)
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


# "Any number of target …" (CR 115.1): no upper bound on the count.
ANY_NUMBER = 1 << 30

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_PLURAL_TARGET_NOUNS = re.compile(
    r"\b(creatures|permanents|artifacts|enchantments|lands|planeswalkers|cards"
    r"|spells|players|opponents|battles)\b")
_TARGET_SPAN = re.compile(r"\btarget\b[^.;•\n]*")
_COUNT_BEFORE = re.compile(
    r"(?:(up to) (\w+)|(one) or (two|three)|(any number) of|(\w+))\s+$")


def _singularize_targets(oracle_l: str) -> str:
    """Rewrite plural target nouns to singular (and "and/or" to "or") inside
    each target phrase, keeping every index in place (a plural noun loses
    its "s" to a space), so one singular grammar parses both."""
    def _span(m):
        t = _PLURAL_TARGET_NOUNS.sub(lambda n: n.group(1)[:-1] + " ", m.group(0))
        return t.replace("and/or", "    or")
    return _TARGET_SPAN.sub(_span, oracle_l)


def _singularize_targets_located(oracle_text: str):
    """`_singularize_targets(oracle_text.lower())` plus its offset map:
    `offsets[i]` is the input-text index of normalised index `i`
    (`offsets[len(norm)] == len(oracle_text)`). Singularisation keeps every
    index in place, so the map only absorbs `str.lower()` length changes
    (a character whose lowercase form is longer, e.g. U+0130)."""
    lowered = oracle_text.lower()
    norm = _singularize_targets(lowered)
    if len(lowered) == len(oracle_text):
        offsets = range(len(norm) + 1)
    else:
        m = []
        for i, ch in enumerate(oracle_text):
            m.extend([i] * len(ch.lower()))
        m.append(len(oracle_text))
        offsets = m
    return norm, offsets


def _count_before(text: str, idx: int):
    """(count_min, count_max) from the words just before a target phrase:
    "up to N" → (0, N), "one or two" → (1, 2), "any number of" → (0, ∞),
    "N" → (N, N); None when no count word precedes it."""
    m = _COUNT_BEFORE.search(text[max(0, idx - 24):idx])
    if m is None:
        return None
    if m.group(1):
        n = _NUMBER_WORDS.get(m.group(2)) or (int(m.group(2)) if m.group(2).isdigit() else None)
        return None if n is None else (0, n)
    if m.group(3):
        return (1, _NUMBER_WORDS[m.group(4)])
    if m.group(5):
        return (0, ANY_NUMBER)
    w = m.group(6)
    n = _NUMBER_WORDS.get(w) or (int(w) if w.isdigit() else None)
    return None if n is None or n == 1 else (n, n)


def _parse_placed(oracle_text: str):
    """The one placement owner. Returns `[(req, norm_start, norm_end)]` in
    parse() order plus the normalised-to-input offset map; each start is the
    occurrence parse() claimed for the requirement's phrase (and read its
    count before, and its mana-value ceiling after), -1 when the phrase was
    not found."""
    if not oracle_text:
        return [], range(1)
    norm, offsets = _singularize_targets_located(oracle_text)
    reqs = _parse_singular(norm)
    import dataclasses as _dc
    # Each requirement reads the count before ITS occurrence of its phrase:
    # longer phrases claim their text first, and an occurrence inside an
    # already-claimed phrase is not this requirement's.
    claimed: list = []
    where = {}
    for i in sorted(range(len(reqs)), key=lambda i: -len(reqs[i].raw_phrase or "")):
        phrase = reqs[i].raw_phrase
        start = norm.find(phrase) if phrase else -1
        while start >= 0 and any(a <= start < b for a, b in claimed):
            start = norm.find(phrase, start + 1)
        if start >= 0:
            claimed.append((start, start + len(phrase)))
        where[i] = start
    out = []
    for i, r in enumerate(reqs):
        idx = where[i]
        counts = _count_before(norm, idx) if idx >= 0 else None
        if counts is not None:
            r = _dc.replace(r, count_min=counts[0], count_max=counts[1],
                            is_optional=r.is_optional or counts[0] == 0)
        elif r.is_optional and r.count_min == 1:
            # "up to one target X": zero or one (CR 115.1).
            r = _dc.replace(r, count_min=0)
        end = idx + len(r.raw_phrase or "") if idx >= 0 else -1
        out.append((_with_mana_value_ceiling(norm, r, end), idx, end))
    return out, offsets


def parse_located(oracle_text: str) -> List[Tuple[TargetRequirement, int]]:
    """`parse()` with each requirement's start in the input text: the
    occurrence parse() itself claimed (never a second search), -1 when its
    phrase was not found. Order is parse() order, not printed order."""
    placed, offsets = _parse_placed(oracle_text)
    return [(r, offsets[s] if s >= 0 else -1) for r, s, _ in placed]


def parse_spans(oracle_text: str) -> List[Tuple[TargetRequirement, int, int]]:
    """`parse_located()` plus each requirement's end offset, both in
    input-text coordinates; (-1, -1) when the phrase was not found."""
    placed, offsets = _parse_placed(oracle_text)
    return [(r, offsets[s], offsets[e]) if s >= 0 else (r, -1, -1)
            for r, s, e in placed]


def parse(oracle_text: str) -> List[TargetRequirement]:
    """Parse all target requirements from an oracle text, each with its
    count (CR 115.1 / 601.2c): plural and counted target phrases are the
    same requirement as their singular form with count_min / count_max."""
    return [r for r, _ in parse_located(oracle_text)]


def _parse_singular(oracle_text: str) -> List[TargetRequirement]:
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
    if gy_match is None:
        # The loose fallback needs a graveyard-zone hint; reminder text
        # (CR 207.2 — parenthesised, no rules meaning) must not supply
        # it: a flashback / escape reminder says "from your graveyard"
        # on a spell whose real target is on the battlefield ("Target
        # creature gains … / Flashback {1}{W} (You may cast this card
        # from your graveyard …)"), and the fallback then read the
        # battlefield creature as a graveyard-creature target.
        from .oracle_parser import strip_reminder_text
        hint_text = strip_reminder_text(oracle_text).lower()
        if any(h in hint_text for h in _GRAVEYARD_ZONE_HINTS):
            gy_match = _GRAVEYARD_LOOSE_PATTERN.search(oracle_l)
            # A target's zone is named by its own sentence (CR 115.1):
            # the graveyard phrase must follow the target phrase before
            # the sentence ends -- "... among cards in your graveyard" in
            # a later delirium / threshold sentence does not move a
            # battlefield target into the graveyard.
            if gy_match is not None:
                end = len(oracle_l)
                for stop in (".", "\n"):
                    at = oracle_l.find(stop, gy_match.end())
                    if at != -1:
                        end = min(end, at)
                sentence = strip_reminder_text(
                    oracle_l[gy_match.start():end])
                if not any(h in sentence for h in _GRAVEYARD_ZONE_HINTS):
                    gy_match = None
    if gy_match is not None:
        super_word = (gy_match.group(1) or "").strip() or None
        type_word = gy_match.group(2)
        # Named groups only exist on the strict pattern — the loose
        # fallback has neither, so groupdict() safely returns {} and
        # both fall through to their existing inference below.
        gd = gy_match.groupdict()
        mv_word = gd.get("mv")
        max_mv = int(mv_word) if mv_word else None
        # Source-zone scope: the strict pattern's named 'scope' group,
        # else infer from the explicit zone hint phrase ("from a
        # graveyard" → any, "from your graveyard" / "in your
        # graveyard" → you).
        zone_scope_word = gd.get("scope")
        if not zone_scope_word:
            if "from a graveyard" in oracle_l or "in a graveyard" in oracle_l:
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
            max_mana_value=max_mv,
        ))
        return out

    # ── 2. Stack-target spells (counterspells) ──────────────────────
    # Check compound "instant or sorcery spell" before the single-type regex
    # so "instant" is not captured alone, leaving "or sorcery spell" unmatched.
    ios_match = _SPELL_TARGET_INSTANT_OR_SORCERY.search(oracle_l)
    if ios_match is not None:
        out.append(TargetRequirement(
            zone="stack",
            types=frozenset({"instant_or_sorcery_spell"}),
            owner_scope="any",
            is_optional=_is_optional_at(oracle_l, ios_match.start()),
            mode_group=_detect_mode_group(oracle_l, ios_match.start(),
                                          modal_start),
            raw_phrase=ios_match.group(0),
        ))
    spell_match = _SPELL_TARGET.search(oracle_l) if ios_match is None else None
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
    for m, types, scope in _compound_matches(oracle_l):
        # An "instead" alternative (a kicked / conditional replacement)
        # re-states the same target rather than adding a second one.
        sentence_start = oracle_l.rfind(".", 0, m.start()) + 1
        sentence = oracle_l[sentence_start:oracle_l.find(".", m.end()) % (len(oracle_l) + 1)]
        if "instead" in sentence and any(
                r.types == types and r.owner_scope == scope for r in out):
            continue
        out.append(TargetRequirement(
            zone="battlefield",
            types=types,
            owner_scope=scope,
            is_optional=_is_optional_at(oracle_l, m.start()),
            mode_group=_detect_mode_group(oracle_l, m.start(), modal_start),
            raw_phrase=m.group(0),
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

    # ── 5. Creature targets, each with its owner scope ─────────────
    # Every distinct creature target phrase is its own requirement ("up to
    # two target creatures you control each deal damage … to target
    # creature an opponent controls" has two).
    scoped_spans = []
    for pat, scope in ((_CREATURE_YOU_CONTROL, "you"),
                       (_CREATURE_OPP_CONTROL, "opponent")):
        m = pat.search(oracle_l)
        if m is not None:
            scoped_spans.append((m.start(), m.end()))
            out.append(TargetRequirement(
                zone="battlefield",
                types=frozenset({"creature"}),
                owner_scope=scope,
                is_optional=_is_optional_at(oracle_l, m.start()),
                mode_group=_detect_mode_group(oracle_l, m.start(), modal_start),
                raw_phrase=m.group(0),
            ))
    for bare_creature in _CREATURE_BARE.finditer(oracle_l):
        if (any(a <= bare_creature.start() < b for a, b in scoped_spans)
                or _is_inside_compound(oracle_l, bare_creature.start())
                or _is_target_creature_spell(oracle_l, bare_creature.start())):
            continue
        if scoped_spans and "another target creature" not in oracle_l[
                max(0, bare_creature.start() - 8):bare_creature.end()]:
            continue
        out.append(TargetRequirement(
            zone="battlefield",
            types=frozenset({"creature"}),
            owner_scope="any",
            is_optional=_is_optional_at(oracle_l, bare_creature.start()),
            mode_group=_detect_mode_group(oracle_l, bare_creature.start(),
                                          modal_start),
            raw_phrase=bare_creature.group(0),
        ))
        break

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

_MV_BOUND_AFTER_RE = re.compile(
    r"^(?:\s+(?:an opponent controls|you don't control|you control))?"
    r"\s+with mana value (x|\d+) or less")


def _with_mana_value_ceiling(norm: str, req: TargetRequirement,
                             end: int) -> TargetRequirement:
    """`req` with the trailing "with mana value N/X or less" clause that
    follows its phrase where parse() placed it (ending at `end`; CR 601.2c:
    the printed ceiling is part of the target's legality). Read at that
    occurrence only -- never at the phrase's first occurrence, which can be
    inside another requirement's longer phrase. Battlefield requirements
    only. A numeric ceiling populates `max_mana_value`; an X ceiling sets
    `max_mana_value_is_x`, bound at enumeration time by the caller's
    affordable X."""
    import dataclasses as _dc
    if req.zone != "battlefield" or not req.raw_phrase or end < 0:
        return req
    m = _MV_BOUND_AFTER_RE.match(norm[end:])
    if m is None:
        return req
    if m.group(1) == "x":
        return _dc.replace(req, max_mana_value_is_x=True)
    if req.max_mana_value is None:
        return _dc.replace(req, max_mana_value=int(m.group(1)))
    return req


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
    for m, types, _scope in _compound_matches(oracle_l):
        if "creature" in types and m.start() <= idx <= m.end():
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


def _blocked_by_hexproof(card: "CardInstance", controller: int) -> bool:
    """CR 702.11d — a permanent with hexproof can't be the target of a
    spell or ability an OPPONENT controls (your own spells can still
    target it). Battlefield-zone only; hexproof has no meaning for
    cards in other zones.
    """
    from .cards import Keyword
    if card.controller == controller:
        return False
    return Keyword.HEXPROOF in card.keywords


def _blocked_by_protection(card: "CardInstance", source) -> bool:
    """CR 702.16b — a permanent with protection from a colour can't be
    the target of a spell of that colour. `source` is the spell (a
    CardInstance; a template is accepted too). Typed field
    `protection_from_colors` (oracle_parser.parse_protection_from);
    combat already read it (`combat_manager._can_block`), targeting
    never did — a red burn spell killed a pro-red creature."""
    if source is None:
        return False
    prot = getattr(card.template, 'protection_from_colors', None) or frozenset()
    if not prot:
        return False
    src_colors = getattr(source, 'colors', None)
    if src_colors is None:
        src_colors = getattr(getattr(source, 'template', source), 'colors', None)
    return bool(prot & set(src_colors or ()))


def can_be_targeted(card: "CardInstance", source, controller: int) -> bool:
    """May `source` (a spell or ability's card, controlled by
    `controller`) target `card` on the battlefield? One owner for the
    targeting restrictions a permanent carries — hexproof (702.11d) and
    protection from a colour (702.16b) — read by cast-time legality,
    the AI's candidate enumeration and the resolution re-check alike."""
    if card.zone == "battlefield" and _blocked_by_hexproof(card, controller):
        return False
    if _blocked_by_protection(card, source):
        return False
    return True


def slot_admits_player(req: TargetRequirement, player_idx: int,
                       controller: int) -> bool:
    """Does a target slot admit this player? "Any target" and "target
    player" do (CR 115.4), within the slot's controller scope ("target
    opponent" admits only an opponent of `controller`). One owner for the
    check, read by the binding on resolution and the AI's aim alike."""
    if not set(req.types) & {"any", "player"}:
        return False
    scope = getattr(req, "owner_scope", "any")
    return not ((scope == "opponent" and player_idx == controller)
                or (scope == "you" and player_idx != controller))


def slot_admits_permanent(req: TargetRequirement, card: "CardInstance",
                          controller: int) -> bool:
    """Does a target slot admit this permanent by its CURRENT types (CR
    608.2b reads them on resolution, CR 601.2c when it is chosen)? "Any
    target" admits a creature or a planeswalker (CR 115.4); a typed slot
    admits its own types; the slot's controller scope applies. Whether the
    source may target it is `can_be_targeted`'s question, not this one."""
    scope = getattr(req, "owner_scope", "any")
    if (scope == "opponent" and card.controller == controller) or \
            (scope == "you" and card.controller != controller):
        return False
    types = set(req.types)
    if "permanent" in types:
        return True                # every object on the battlefield is one
    admitted = ({"creature", "planeswalker"} if "any" in types
                else types - {"player"})
    return bool({t.value for t in card.effective_card_types} & admitted)


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


def _matches_subtype(card: "CardInstance", subtype: Optional[str]) -> bool:
    """Filter by SUBTYPE (CR 205.3). None = no filter.

    Printed subtypes only. A type-adding continuous effect ("lands you
    control are Forests") is a layer-7 question the continuous-effects
    manager owns; reading it here would put two owners on the same rule.
    """
    if subtype is None:
        return True
    have = getattr(card.template, "subtypes", None) or ()
    return subtype in {str(s).lower() for s in have}


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
        # CR 111 / 701.5: only a SPELL on the stack is a legal target for
        # "target spell". A triggered or activated ability is a distinct kind
        # of stack object and is answered only by cards that say so (Stifle,
        # Disallow). Enumerating `item.source` unconditionally offered the
        # ability's SOURCE CARD as a spell — and that source is typically a
        # permanent, whose `template.is_spell` (defined as `not is_land`) is
        # True, so nothing downstream could tell the difference.
        from .stack import StackItemType as _SIT
        for item in game.stack.items:
            if item.item_type != _SIT.SPELL:
                continue
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
        if tok == "instant_or_sorcery_spell" and (t.is_instant or t.is_sorcery):
            return True
    return False


def spell_matches(req: TargetRequirement, spell: "CardInstance") -> bool:
    """A spell on the stack is of a kind a stack-zone requirement names
    ("target sorcery spell", "target creature spell")."""
    return req.zone == "stack" and _spell_token_matches(spell, req.types)


def has_legal_target(game: "GameState", controller: int,
                     req: TargetRequirement,
                     exclude: Optional["CardInstance"] = None,
                     x_ceiling: Optional[int] = None,
                     source: Optional["CardInstance"] = None) -> bool:
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
        # Same CR 111 discriminator as the two enumeration paths below/above:
        # only a SPELL is a legal target for "target spell". This is the path
        # `can_cast` reaches, so without the check the ENGINE would let a
        # counterspell be cast at a triggered ability.
        from .stack import StackItemType as _SIT
        for item in game.stack.items:
            if item.item_type != _SIT.SPELL:
                continue
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
        if not _matches_subtype(card, req.subtype):
            continue
        if req.zone == "battlefield" and not can_be_targeted(card, source, controller):
            continue
        # Owner already pre-filtered by _zone_cards.
        if req.max_mana_value_is_x and x_ceiling is not None and \
                (card.template.cmc or 0) > x_ceiling:
            continue  # CR 601.2c: beyond the X the caster can pay
        return True
    return False


def choose_targets(game: "GameState", controller: int, req: TargetRequirement,
                   preferred=None, key=None, hostile: bool = True,
                   exclude: Optional["CardInstance"] = None,
                   source: Optional["CardInstance"] = None) -> List["CardInstance"]:
    """Up to ``req.count_max`` distinct legal targets (CR 115.3, 601.2c):
    the chosen ids that are legal first, in order, then — to fill the count
    — the remaining legal candidates by ``key`` (highest first; default
    mana value). ``hostile`` leaves the controller's own permanents out of
    the fill unless the requirement is scoped to them. The one place a
    counted target set is chosen."""
    candidates = enumerate_legal_targets(game, controller, req,
                                         exclude=exclude, source=source)
    by_id = {c.instance_id: c for c in candidates}
    chosen: List["CardInstance"] = []
    for tid in (preferred or []):
        c = by_id.get(tid) if isinstance(tid, int) else None
        if c is not None and all(c is not x for x in chosen):
            chosen.append(c)
    limit = max(1, req.count_max or 1)
    if len(chosen) < limit:
        def _ctrl(c):
            return c.controller if c.controller is not None else c.owner
        pool = [c for c in candidates if all(c is not x for x in chosen)
                and (not hostile or req.owner_scope == "you" or _ctrl(c) != controller)]
        pool.sort(key=key or (lambda c: c.template.cmc or 0), reverse=True)
        chosen.extend(pool[:limit - len(chosen)])
    return chosen[:limit]


def enumerate_legal_targets(game: "GameState", controller: int,
                            req: TargetRequirement,
                            exclude: Optional["CardInstance"] = None,
                            x_ceiling: Optional[int] = None,
                            source: Optional["CardInstance"] = None,
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
        from .stack import StackItemType as _SIT
        for item in game.stack.items:
            # Same CR 111 discriminator as `_zone_cards` above.
            if item.item_type != _SIT.SPELL:
                continue
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
        if not _matches_subtype(card, req.subtype):
            continue
        if req.zone == "battlefield" and not can_be_targeted(card, source, controller):
            continue
        if req.max_mana_value is not None and \
                (card.template.cmc or 0) > req.max_mana_value:
            continue
        if req.max_mana_value_is_x and x_ceiling is not None and \
                (card.template.cmc or 0) > x_ceiling:
            continue  # CR 601.2c: beyond the X the caster can pay
        out.append(card)
    return out


def legal_slot_choices(game: "GameState", controller: int,
                       req: TargetRequirement,
                       source: Optional["CardInstance"] = None,
                       ) -> Tuple[List[int], List["CardInstance"]]:
    """(players, permanents) a target slot may name now (CR 115.4, 601.2c,
    603.3d): the players the slot admits within its controller scope
    (`slot_admits_player`) and the battlefield permanents it admits by
    their current types (`slot_admits_permanent`) that `source` may target
    (`can_be_targeted`: hexproof, protection). One owner for the choices a
    triggered ability's target is picked from as it is put on the stack;
    a slot in another zone keeps `enumerate_legal_targets`."""
    players = [p for p in range(len(game.players))
               if slot_admits_player(req, p, controller)]
    if req.zone not in ("any", "battlefield"):
        return players, enumerate_legal_targets(game, controller, req,
                                                source=source)
    permanents = []
    for p in game.players:
        for card in p.battlefield:
            if not slot_admits_permanent(req, card, controller):
                continue
            if not can_be_targeted(card, source, controller):
                continue
            if not _matches_supertype(card, req.supertype) \
                    or not _matches_subtype(card, req.subtype):
                continue
            if req.max_mana_value is not None and \
                    (card.template.cmc or 0) > req.max_mana_value:
                continue
            permanents.append(card)
    return players, permanents


def has_legal_target_for_spell(game: "GameState", controller: int,
                               requirements: List[TargetRequirement],
                               exclude: Optional["CardInstance"] = None,
                               x_ceiling: Optional[int] = None,
                               source: Optional["CardInstance"] = None,
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
            if not has_legal_target(game, controller, req, exclude=exclude,
                                    x_ceiling=x_ceiling, source=source):
                return False
        else:
            modal_groups.setdefault(req.mode_group, []).append(req)

    # For each modal group, at least one requirement must be legal.
    for group_id, group_reqs in modal_groups.items():
        if not any(has_legal_target(game, controller, r, exclude=exclude,
                                    x_ceiling=x_ceiling, source=source)
                   for r in group_reqs):
            return False
    return True


def player_index_for_target(game: "GameState", controller: int,
                            tid) -> Optional[int]:
    """Map a player-target sentinel in a spell's target list to a player
    index: ``PLAYER_TARGET_OPPONENT`` (-1, the historical "face" value)
    → the caster's opponent, ``PLAYER_TARGET_SELF`` (-2) → the caster.
    Any other id is a permanent instance id → None.  One owner for the
    encoding (engine/constants.py); every player-target effect —
    face burn, targeted discard — resolves through here."""
    from .constants import PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF
    if tid == PLAYER_TARGET_OPPONENT:
        return 1 - controller
    if tid == PLAYER_TARGET_SELF:
        return controller
    return None


def targeted_player(game: "GameState", controller: int,
                    targets: Optional[list], default_opponent: bool = True
                    ) -> Optional[int]:
    """The player a resolving spell targets, from its target list.  With
    no player sentinel in the list the opponent is assumed when
    ``default_opponent`` (the pre-sentinel behaviour every existing
    caller relied on)."""
    for tid in targets or []:
        idx = player_index_for_target(game, controller, tid)
        if idx is not None:
            return idx
    return (1 - controller) if default_opponent else None


def pick_resolution_target(game: "GameState", controller: int,
                           source: "CardInstance", candidates,
                           preferred=(), key=None) -> Optional["CardInstance"]:
    """The permanent a resolving spell or ability acts on — the one owner
    of a handler's target choice.

    CR 601.2c / 608.2b: the target chosen when it was put on the stack
    (`preferred`) is the one it affects while still legal. When none was
    chosen then, the pick made here is the best legal candidate (`key`,
    highest first): never one `source` may not target (hexproof,
    protection — `can_be_targeted`), never one whose ward its controller
    would not get past, and it meets that ward now (CR 702.21a) — an
    unpaid ward counters the effect, so the pick is None."""
    legal = [c for c in candidates if can_be_targeted(c, source, controller)]
    by_id = {c.instance_id: c for c in legal}
    for tid in (preferred or ()):
        if tid in by_id:
            return by_id[tid]      # met ward when it was put on the stack
    from ai.ward_targeting import ward_rules_out_target
    pool = [c for c in legal
            if not ward_rules_out_target(game, controller, source, c, mana_committed=0)]
    if not pool:
        return None
    pick = max(pool, key=key) if key is not None else pool[0]
    from .optional_costs import ward_gate
    survives, _ = ward_gate(game, source, controller, [pick.instance_id])
    return pick if survives else None
