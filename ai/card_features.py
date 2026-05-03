"""Deterministic per-card feature extraction.

The output is a typed pydantic model. NO card-name conditionals — every
field is derived from oracle text, type line, mana cost, or P/T via
regex / keyword matching. The same MTGJSON entry always produces the
same features (modulo MTGJSON updates).

Design constraints
------------------
* Features must be cheap to extract. This runs over 20k Modern-legal
  cards at deck-import time and over 100+ cards per LLM-tool call.
  Target: < 1ms per card on commodity hardware
  (`PERFORMANCE_BUDGET_PER_CARD_MS`).
* No magic numbers in the rule body — every threshold (oracle word
  count for "complex card" intuition, line cap for the prompt-budget
  excerpt) is named and documented in this module.
* Reuses the same word-boundary keyword-matching pattern that
  `engine.card_database.CardDatabase._build_template` already uses, so
  that "flash" never matches "flashback".

The schema is consumed by:
1. The pydantic-ai LLM tool surface (Phase I-3) — features replace raw
   oracle text in prompts, cutting token spend.
2. Scoring extensions (`ai/permanent_threat.py`, `ai/ev_evaluator.py`)
   that need a typed view over already-parsed mechanics.
3. The deck importer (`import_deck.py`) — pre-classify cards before
   role assignment.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ─── Module-level constants (no magic numbers) ────────────────────────

# Word-count threshold above which a card is considered "complex" by
# input-compression heuristics.  Not enforced by the schema; surfaced
# so callers can name the threshold without re-deriving it.
COMPLEX_ORACLE_WORD_COUNT: int = 60

# Maximum number of newline-separated oracle lines preserved in
# `first_two_oracle_lines`.  The first two lines are where the
# headline mechanic of >95% of MTG cards lives (keywords, the
# primary triggered/activated ability); subsequent lines are usually
# reminder text or secondary modes.  Two is a budget choice that
# trades fidelity for prompt cost.
ORACLE_EXCERPT_LINE_CAP: int = 2

# Performance budget — used in tests, surfaced as a constant so the
# expectation is documented near the code it constrains.
PERFORMANCE_BUDGET_PER_CARD_MS: float = 1.0

# Reminder-text pattern: oracle text encloses reminder text in
# parentheses (e.g. "Flying (This creature can't be blocked except by
# creatures with flying or reach.)").  Players don't read it; word
# counts shouldn't either.  The pattern is non-greedy so multiple
# reminder blocks on one card don't collapse together.
_REMINDER_TEXT_RE = re.compile(r"\([^)]*\)")

# Canonical color order for color-identity output: WUBRG.
_CANONICAL_COLORS: tuple[str, ...] = ("W", "U", "B", "R", "G")

# Keywords flagged on the `keywords` field.  This is the union of the
# keywords the engine already detects (engine/card_database.KEYWORD_MAP
# entries) plus a small set of evergreen ones MTGJSON exposes via the
# top-level `keywords` array.  Matching is word-boundary so "Flash" on
# Past in Flames doesn't trip from "flashback".
_TRACKED_KEYWORDS: tuple[str, ...] = (
    "Flying", "First Strike", "Double Strike", "Deathtouch", "Lifelink",
    "Trample", "Haste", "Vigilance", "Reach", "Menace", "Flash",
    "Hexproof", "Shroud", "Indestructible", "Defender", "Cascade",
    "Convoke", "Affinity", "Prowess", "Undying", "Persist", "Unearth",
    "Evoke", "Suspend", "Storm", "Annihilator", "Delve", "Flashback",
    "Buyback", "Madness", "Dredge", "Fading", "Vanishing", "Bushido",
    "Soulshift", "Ninjutsu", "Splice", "Equip", "Modular", "Bloodthirst",
    "Graft", "Rebound", "Replicate", "Scavenge", "Soulbond",
    "Battalion", "Bestow", "Devoid", "Emerge", "Embalm", "Eternalize",
    "Exalted", "Investigate", "Landfall", "Morph", "Ninjutsu",
    "Outlast", "Phasing", "Ramp", "Surveil", "Threshold", "Wither",
    "Energy", "Mutate", "Companion", "Disturb", "Daybound", "Nightbound",
    "Boast", "Casualty", "Channel",
)


# ─── Schema ──────────────────────────────────────────────────────────


class CardFeatures(BaseModel):
    """Typed feature view of a single card face.

    Frozen + extra="forbid" to match the LLM-input/output schema
    contract used elsewhere (`ai/schemas.py`, `ai/gameplan_schemas.py`).
    Frozen lets callers cache and hash the model; extra-forbid lets us
    catch typos in callers passing kwargs.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    name: str = Field(..., description="Exact MTGJSON card name")
    cmc: int = Field(..., ge=0, description="Converted mana cost / mana value")
    types: list[str] = Field(
        ..., description="Card supertypes + types, e.g. ['Legendary', 'Creature']"
    )
    subtypes: list[str] = Field(
        ..., description="Subtypes from the type line, e.g. ['Human', 'Wizard']"
    )
    colors: list[Literal["W", "U", "B", "R", "G"]] = Field(
        ..., description="Color identity from mana cost, in canonical WUBRG order"
    )

    # Power/toughness — only present for creatures
    power: Optional[int] = None
    toughness: Optional[int] = None

    # Flat keyword detection — word-boundary match, NOT substring
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Flying, Haste, Flash, Trample, Lifelink, Deathtouch, First Strike, "
            "Double Strike, Reach, Vigilance, Hexproof, Shroud, Indestructible, "
            "Menace, Defender, Prowess, Cascade, Storm, Convoke, Affinity, etc."
        ),
    )

    # Mechanic flags — derived from oracle text via regex
    is_ramp: bool = Field(
        False,
        description='True if oracle contains "Add {[WUBRGC]}" or land-fetch pattern',
    )
    is_removal: bool = Field(
        False,
        description=(
            "Destroy/exile/-N/-N/sacrifice/return-to-hand targeting opponent permanents"
        ),
    )
    is_card_draw: bool = Field(
        False,
        description='"Draw a card", "draws cards", "investigate", etc.',
    )
    is_counterspell: bool = Field(
        False, description='"Counter target spell"',
    )
    is_discard: bool = Field(
        False,
        description='"Target opponent discards" / "exile from hand"',
    )
    is_tutor: bool = Field(
        False, description='"Search your library for"',
    )
    is_recursion: bool = Field(
        False, description='"Return target ... from your graveyard"',
    )
    is_reanimator: bool = Field(
        False,
        description=(
            '"Return target creature card from your graveyard to the battlefield"'
        ),
    )
    is_sweeper: bool = Field(
        False,
        description='"Destroy/exile all creatures" or all permanents',
    )
    is_combo_payoff: bool = Field(
        False,
        description=(
            "Storm count, copies, infinite mana, deals damage equal to N for some "
            "N derived from board"
        ),
    )
    is_combo_enabler: bool = Field(
        False,
        description="Generates mana for free / cantrips for 0 / repeatable",
    )

    # Cast-time signals
    is_instant_speed: bool = Field(
        False, description="Has Flash or is an instant",
    )
    is_sorcery_speed_only: bool = Field(
        False, description="Sorcery, no Flash",
    )

    # Threshold-aware signals
    has_etb: bool = Field(
        False, description='"When ... enters the battlefield"',
    )
    has_attack_trigger: bool = Field(
        False, description='"Whenever ... attacks"',
    )
    has_death_trigger: bool = Field(
        False, description='"When ... dies"',
    )
    is_modal: bool = Field(
        False, description='"Choose one/two/up to N"',
    )

    # Numeric signals (deterministic, useful for LLM input compression)
    oracle_word_count: int = Field(
        ...,
        ge=0,
        description=(
            "Word count of oracle text (reminder text in parentheses excluded) — "
            "proxy for card complexity"
        ),
    )
    first_two_oracle_lines: str = Field(
        ...,
        description=(
            "First N newline-separated lines of oracle text (N = "
            "ORACLE_EXCERPT_LINE_CAP). Most mechanics live in the opening "
            "lines; bounded length for prompt budgeting."
        ),
    )


# ─── Internal helpers ────────────────────────────────────────────────


def _strip_reminder_text(oracle: str) -> str:
    """Remove parenthesised reminder text from oracle.  No effect on
    cards with no parentheses."""
    return _REMINDER_TEXT_RE.sub(" ", oracle)


def _word_boundary_match(text_lower: str, keyword: str) -> bool:
    """True iff `keyword` (case-insensitive) appears as a standalone
    word in `text_lower`.  Avoids the classic "flash" → "flashback"
    false positive via the `\\b` word boundary.

    NOTE: this is the *generic* word-boundary check used by mechanic
    detection; the keyword-list build (`_detect_keywords`) uses a
    stricter "start-of-line" anchor (matching
    engine/card_database.py) so "creatures with flying" doesn't tag
    the card itself as having flying.
    """
    pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
    return re.search(pattern, text_lower) is not None


def _keyword_line_match(text_lower: str, keyword: str) -> bool:
    """True iff `keyword` appears as a *standalone* keyword listing —
    at the start of the oracle text, or after a newline, terminated
    by whitespace / comma / end-of-line.  Mirrors the pattern used in
    engine/card_database.py::CardDatabase._build_template so callers
    get the same answer the engine already does.
    """
    pattern = (
        r"(?:^|\n)" + re.escape(keyword.lower()) + r"(?:\s|$|,|\n)"
    )
    return re.search(pattern, text_lower) is not None


def _detect_keywords(card_data: dict, oracle: str) -> list[str]:
    """Combine the MTGJSON `keywords` array with word-boundary scan of
    oracle text to produce a stable, de-duplicated keyword list."""
    detected: set[str] = set()

    # MTGJSON keywords array is the primary signal — it's curated.
    raw_keywords = card_data.get("keywords") or []
    for k in raw_keywords:
        # Normalise capitalisation to title case ("First strike" → "First Strike")
        normalised = " ".join(part.capitalize() for part in k.split())
        if any(normalised.lower() == tk.lower() for tk in _TRACKED_KEYWORDS):
            for tk in _TRACKED_KEYWORDS:
                if tk.lower() == normalised.lower():
                    detected.add(tk)
                    break

    # Oracle text scan picks up keywords listed on their own line
    # (the canonical MTG layout for keywords).  We deliberately use
    # the strict line-anchor variant so "creatures with flying" does
    # NOT tag the spell as having flying itself — same convention as
    # the engine's KEYWORD_MAP scan.
    text_lower = oracle.lower()
    for kw in _TRACKED_KEYWORDS:
        if _keyword_line_match(text_lower, kw):
            detected.add(kw)

    # Stable order — alphabetical for determinism.
    return sorted(detected)


def _extract_colors(mana_cost: str, color_field: list[str] | None) -> list[str]:
    """Extract WUBRG color list in canonical order from `manaCost` (or
    fall back to the MTGJSON `colors` array).  Hybrid pips contribute
    each of their colors."""
    found: set[str] = set()

    if mana_cost:
        # Symbols inside braces — covers W, U, B, R, G, and hybrid pips
        # like W/U, R/W, 2/W, W/P (phyrexian counts as the colour).
        for sym in re.findall(r"\{([^}]+)\}", mana_cost):
            for color in _CANONICAL_COLORS:
                if color in sym.upper():
                    found.add(color)

    # Fallback to MTGJSON's `colors` array when the cost is null
    # (Living End, Lotus Bloom — suspend cards have manaCost: null).
    if not found and color_field:
        for c in color_field:
            if c in _CANONICAL_COLORS:
                found.add(c)

    return [c for c in _CANONICAL_COLORS if c in found]


def _detect_is_ramp(text_lower: str, types: list[str]) -> bool:
    """Card adds mana or fetches/puts a land into play.

    Lands themselves are excluded — every land "produces mana", but
    treating them as ramp dilutes the signal for callers asking "does
    this spell accelerate me?".
    """
    if "Land" in types:
        return False
    # "Add {X}" — covers mana rocks, rituals, mana dorks (via Add one mana ...).
    if re.search(r"add\s+\{[wubrgc]\}", text_lower):
        return True
    if re.search(r"add\s+(?:one|two|three)\s+mana", text_lower):
        return True
    # Land fetch / put-into-play — Arboreal Grazer, Search for Tomorrow,
    # Cultivate, etc.  We exclude "search ... basic land card" inside
    # removal text (Path to Exile rider) by requiring "your library"
    # ownership of the search.
    if re.search(
        r"search\s+your\s+library\s+for\s+(?:a\s+|up\s+to\s+\w+\s+)?(?:basic\s+)?land",
        text_lower,
    ):
        # Path to Exile: "Its controller may search their library ..." —
        # not the caster's ramp.  Filter out controller-grants.
        if "controller may search" not in text_lower:
            return True
    if re.search(r"put\s+a\s+land\s+card\s+from\s+your\s+hand", text_lower):
        return True
    return False


def _detect_is_removal(text_lower: str, types: list[str]) -> bool:
    """Removes an opponent's permanent (or any permanent — modal cards
    that include destroy-target-creature still tag).

    Lands are excluded: their channel modes (Boseiju) destroy permanents
    but their primary role is mana production.
    """
    if "Land" in types:
        return False
    patterns = (
        r"destroy\s+target\s+(?:creature|artifact|enchantment|nonland\s+permanent|permanent|land|planeswalker)",
        r"destroy\s+(?:all|each)\s+(?:creatures?|artifacts?|enchantments?|nonland\s+permanents?|permanents?)",
        r"exile\s+target\s+(?:creature|nonland\s+permanent|permanent|artifact|enchantment|planeswalker)",
        r"exile\s+(?:all|each)\s+(?:creatures?|nonland\s+permanents?|permanents?)",
        r"deals?\s+\d+\s+damage\s+to\s+(?:any\s+target|target\s+creature|target\s+creature\s+or\s+player)",
        r"return\s+target\s+(?:creature|nonland\s+permanent|permanent)\s+to\s+its\s+owner's\s+hand",
        r"target\s+creature\s+gets\s+-\d+/-\d+",
        r"sacrifices?\s+a\s+creature",  # opponent-sac effects
    )
    for p in patterns:
        if re.search(p, text_lower):
            # Power-pump rider: "target creature gets +N/+N" should NOT
            # match the removal damage pattern.  Already excluded by
            # the explicit "-N/-N" sign in the pattern.
            return True
    return False


def _detect_is_card_draw(text_lower: str) -> bool:
    """Draws cards or generates virtual draws (impulse, investigate)."""
    if re.search(r"draw\s+(?:a|\d+|two|three|four|five|six|seven)\s+cards?", text_lower):
        return True
    # Variable-quantity draw: "draw that many cards", "draw cards equal to ..."
    if re.search(r"draw\s+(?:that\s+many|cards?\s+equal\s+to)", text_lower):
        return True
    # Impulse: "exile the top N ... you may play"
    if re.search(r"exile\s+the\s+top.{0,80}you\s+may\s+(?:play|cast)", text_lower):
        return True
    # Selection: "look at the top ... put one ... into your hand"
    if re.search(
        r"(?:look at|reveal).{0,80}put.{0,40}into\s+your\s+hand", text_lower
    ):
        return True
    if "investigate" in text_lower:
        return True
    return False


def _detect_is_counterspell(text_lower: str) -> bool:
    """Counters a spell (any flavour: target spell, target noncreature
    spell, target creature spell, etc.)."""
    return re.search(r"counter\s+target\s+\w*\s*spell", text_lower) is not None


def _detect_is_discard(text_lower: str) -> bool:
    """Forces an opponent to discard."""
    if re.search(
        r"target\s+(?:player|opponent)\s+(?:reveals?\s+their\s+hand|discards?)",
        text_lower,
    ):
        return True
    if re.search(r"each\s+opponent\s+discards?", text_lower):
        return True
    return False


def _detect_is_tutor(text_lower: str) -> bool:
    """Searches the caster's library for a card."""
    if re.search(r"search\s+your\s+library\s+for", text_lower):
        # Pure ramp ("search for a basic land") is also a tutor in the
        # mechanical sense — caller can disambiguate via is_ramp.
        return True
    return False


def _detect_is_recursion(text_lower: str) -> bool:
    """Returns a card from your graveyard to hand or play (any zone)."""
    return (
        re.search(r"return\s+target\s+\w*.{0,40}from\s+your\s+graveyard", text_lower)
        is not None
    )


def _detect_is_reanimator(text_lower: str) -> bool:
    """Specifically returns a creature from graveyard to the
    battlefield (the canonical reanimator signal)."""
    return (
        re.search(
            r"return\s+target.{0,60}creature\s+card\s+from\s+(?:a|your)\s+graveyard\s+to\s+the\s+battlefield",
            text_lower,
        )
        is not None
        or re.search(
            r"return\s+target.{0,60}creature.{0,60}from\s+your\s+graveyard\s+to\s+the\s+battlefield",
            text_lower,
        )
        is not None
    )


def _detect_is_sweeper(text_lower: str) -> bool:
    """Destroys/exiles all creatures or all (nonland) permanents."""
    return (
        re.search(
            r"(?:destroy|exile)\s+(?:all|each)\s+(?:creatures?|nonland\s+permanents?|permanents?)",
            text_lower,
        )
        is not None
    )


def _detect_is_combo_payoff(text_lower: str, keywords: list[str]) -> bool:
    """Card whose effect scales with a board-derived count.

    Patterns:
      - Storm keyword (copies for each spell cast this turn).
      - Cascade (free spell from library).
      - "Deals damage equal to ..." / "for each ..." scaling.
      - Living End-style mass reanimation.
      - Underworld-Breach-style flashback-everything.
      - Past in Flames: grants flashback to gy spells.
    """
    if "Storm" in keywords or "Cascade" in keywords:
        return True
    if re.search(r"damage\s+equal\s+to.{0,40}(?:storm\s+count|number\s+of)", text_lower):
        return True
    if re.search(r"for\s+each\s+(?:spell|creature|artifact|land)", text_lower):
        # Heuristic: "for each X" scaling.  Excludes domain ("for each
        # basic land type") since domain is already a tagged mechanic
        # in the engine and isn't a combo payoff signal — but we also
        # don't want to filter too aggressively.  We accept the broader
        # signal here; downstream callers can refine.
        return True
    if "gains flashback" in text_lower and "graveyard" in text_lower:
        return True
    if re.search(
        r"exiles?\s+all\s+creature\s+cards?\s+from\s+(?:their|all)\s+graveyards?",
        text_lower,
    ):
        return True
    return False


def _detect_is_combo_enabler(
    text_lower: str, types: list[str], cmc: int, keywords: list[str]
) -> bool:
    """Card that fuels a combo loop: free mana, free cantrip, or
    repeatable triggers.

    Patterns:
      - 0-cost permanent that taps for mana (Mox Opal, Lotus Bloom).
      - 0-cost cantrip (Manamorphose net-zero, Gitaxian Probe).
      - Cost-reducer ("spells you cast cost X less").
      - Suspend cards that effectively cost 0 to deploy.
    """
    if "Land" in types:
        # Lands that "untap a permanent" or chain triggers can be
        # enablers but the land axis is owned by mana_planner.py;
        # don't double-count.
        return False
    # 0-cost mana producer.  Two flavours:
    #   1. Pip-exact mana ("Add {R}", "Add {G}{G}") — matches the
    #      `{[wubrgc]}` token form.
    #   2. "Add one mana of any color" / "Add two mana ..." — Mox
    #      Opal style; phrasing has no pip token at all.
    if cmc == 0 and (
        ("add" in text_lower and re.search(r"\{[wubrgc]\}", text_lower))
        or re.search(r"add\s+(?:one|two|three)\s+mana", text_lower)
    ):
        return True
    # Cost reducer — "X spells you cast cost {N} less" /
    # "this spell costs {N} less to cast".  Anchor on the "{N} less"
    # form to avoid Cascade's reminder text ("a nonland card that costs
    # less than this spell") matching as a cost reducer.
    if re.search(
        r"(?:spells?\s+you\s+cast\s+cost|this\s+spell\s+costs)\s+\{\d+\}\s+less",
        text_lower,
    ):
        return True
    if re.search(
        r"costs?\s+\{?\d\}?\s+less\s+to\s+cast", text_lower
    ):
        return True
    # Suspend with 0-mana suspend cost (Lotus Bloom)
    if "Suspend" in keywords and re.search(r"suspend\s+\d+[—\-–]\s*\{0\}", text_lower):
        return True
    # Manamorphose-shape: instant/sorcery that adds mana AND draws.
    # Net-zero in card count, generates colour fixing — classic enabler.
    if (
        "Instant" in types or "Sorcery" in types
    ) and re.search(r"add\s+\w*\s*mana", text_lower) and "draw" in text_lower:
        return True
    # Free spells via "without paying its mana cost" (Force of Negation,
    # but those aren't strictly enablers; included for completeness).
    if "without paying its mana cost" in text_lower and cmc <= 1:
        return True
    return False


def _detect_is_modal(text_lower: str) -> bool:
    """Modal: "Choose one", "Choose two", "Choose one or both",
    "Choose up to N", with the bullet/em-dash modal marker."""
    return (
        re.search(r"choose\s+(?:one|two|three|one\s+or\s+both|up\s+to\s+\w+)", text_lower)
        is not None
    )


def _count_oracle_words(oracle: str) -> int:
    """Word count excluding parenthesised reminder text."""
    no_reminder = _strip_reminder_text(oracle)
    # Split on whitespace; filter empties.
    return sum(1 for w in no_reminder.split() if w.strip())


def _excerpt_first_lines(oracle: str, line_cap: int = ORACLE_EXCERPT_LINE_CAP) -> str:
    """Return at most `line_cap` newline-separated lines of oracle
    text.  Preserves the original newline structure between kept lines,
    so the output reads as if it were the head of the oracle."""
    if not oracle:
        return ""
    lines = oracle.split("\n")
    return "\n".join(lines[:line_cap])


# ─── Public extraction surface ───────────────────────────────────────


def _extract_features_uncached(
    name: str,
    cmc: int,
    types: tuple[str, ...],
    subtypes: tuple[str, ...],
    supertypes: tuple[str, ...],
    colors_tuple: tuple[str, ...],
    power: Optional[int],
    toughness: Optional[int],
    keywords_tuple: tuple[str, ...],
    oracle: str,
) -> CardFeatures:
    """Inner extractor — receives only hashable inputs so it can be
    LRU-cached.  Wrapped by `extract_features` which adapts the
    MTGJSON dict shape."""
    text_lower = oracle.lower()

    types_combined = list(supertypes) + list(types)
    keywords = list(keywords_tuple)

    is_instant = "Instant" in types or "Flash" in keywords
    is_sorcery_speed_only = "Sorcery" in types and "Flash" not in keywords

    has_etb = (
        re.search(r"when\s+.{0,40}\benters\b", text_lower) is not None
    )
    has_attack_trigger = (
        re.search(r"whenever\s+.{0,40}\battacks\b", text_lower) is not None
    )
    has_death_trigger = (
        re.search(r"when\s+.{0,40}\bdies\b", text_lower) is not None
    )

    return CardFeatures(
        name=name,
        cmc=cmc,
        types=types_combined,
        subtypes=list(subtypes),
        colors=list(colors_tuple),
        power=power,
        toughness=toughness,
        keywords=keywords,
        is_ramp=_detect_is_ramp(text_lower, list(types)),
        is_removal=_detect_is_removal(text_lower, list(types)),
        is_card_draw=_detect_is_card_draw(text_lower),
        is_counterspell=_detect_is_counterspell(text_lower),
        is_discard=_detect_is_discard(text_lower),
        is_tutor=_detect_is_tutor(text_lower),
        is_recursion=_detect_is_recursion(text_lower),
        is_reanimator=_detect_is_reanimator(text_lower),
        is_sweeper=_detect_is_sweeper(text_lower),
        is_combo_payoff=_detect_is_combo_payoff(text_lower, keywords),
        is_combo_enabler=_detect_is_combo_enabler(
            text_lower, list(types), cmc, keywords
        ),
        is_instant_speed=is_instant,
        is_sorcery_speed_only=is_sorcery_speed_only,
        has_etb=has_etb,
        has_attack_trigger=has_attack_trigger,
        has_death_trigger=has_death_trigger,
        is_modal=_detect_is_modal(text_lower),
        oracle_word_count=_count_oracle_words(oracle),
        first_two_oracle_lines=_excerpt_first_lines(oracle),
    )


# Cache the inner extractor by hashable inputs.  Bounded so a
# pathological call site can't pin every Modern card in memory.
_extract_features_uncached = lru_cache(maxsize=4096)(_extract_features_uncached)


def extract_features(card_data: dict) -> CardFeatures:
    """Extract features from one MTGJSON card-data dict.

    `card_data` is the value from ModernAtomic.json's "data" map for
    one card name.  Multi-faced cards: this function takes ONE face's
    data — the caller is responsible for splitting DFCs (the engine
    already chooses the front face when registering the template; LLM
    callers can call `extract_features` per face explicitly).
    """
    name = card_data.get("name") or card_data.get("faceName") or ""

    raw_cmc = card_data.get("manaValue", 0) or 0
    try:
        cmc = max(0, int(raw_cmc))
    except (TypeError, ValueError):
        cmc = 0

    types = tuple(card_data.get("types") or [])
    subtypes = tuple(card_data.get("subtypes") or [])
    supertypes = tuple(card_data.get("supertypes") or [])

    mana_cost_str = card_data.get("manaCost") or ""
    color_field = card_data.get("colors") or []
    colors_tuple = tuple(_extract_colors(mana_cost_str, color_field))

    power_raw = card_data.get("power")
    toughness_raw = card_data.get("toughness")
    power: Optional[int] = None
    toughness: Optional[int] = None
    if power_raw is not None:
        try:
            power = int(power_raw)
        except (ValueError, TypeError):
            # "*" / "X" — variable; report as 0 to keep schema typed
            power = 0
    if toughness_raw is not None:
        try:
            toughness = int(toughness_raw)
        except (ValueError, TypeError):
            toughness = 0

    oracle = card_data.get("text") or ""
    keywords_tuple = tuple(_detect_keywords(card_data, oracle))

    return _extract_features_uncached(
        name,
        cmc,
        types,
        subtypes,
        supertypes,
        colors_tuple,
        power,
        toughness,
        keywords_tuple,
        oracle,
    )


def extract_features_for_deck(
    mainboard: dict[str, int], db: Any
) -> dict[str, CardFeatures]:
    """Convenience: extract features for every unique card in a
    decklist.

    Returns ``{card_name: CardFeatures}``.  Per-card extraction is
    cached via `functools.lru_cache` on the inner extractor, so calling
    this twice with the same input is effectively free for the second
    call (only a dict-build cost).
    """
    out: dict[str, CardFeatures] = {}
    for name in mainboard.keys():
        raw = db.get_raw(name) if hasattr(db, "get_raw") else None
        if raw is None:
            # Fall back to a synthesised stub so the caller still gets
            # a CardFeatures with the name set — avoids KeyError loops
            # in deck-import flows on cards not in the DB.
            stub = {"name": name, "manaValue": 0, "types": [], "text": ""}
            out[name] = extract_features(stub)
            continue
        # `get_raw` returns the MTGJSON dict; ensure name is set so the
        # extracted CardFeatures.name matches the deck-list key.
        if "name" not in raw:
            raw = {**raw, "name": name}
        out[name] = extract_features(raw)
    return out
