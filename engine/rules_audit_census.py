"""Keyword census for the rules auditor: a keyword-ability word printed on
a card that the engine has NO model for is recorded once per word — a
fact for the coverage report, not a violation.

"Modelled" means one of: a `Keyword` enum member of that name, a typed
`CardTemplate` field the loader populates for that mechanic, or a
registered effect handler keyed by the mechanic word. The mapping below
is the engine's own vocabulary; a word missing from it and from the enum
is by definition unmodelled. Both lists are data, never card names.
"""
from __future__ import annotations

import re
from typing import Iterable

from .cards import Keyword
from .rules_audit import census

# CR 702 keyword abilities and the ability words the pool uses, in the
# form they appear in oracle text (lowercase). Extend as printings arrive.
KEYWORD_WORDS: tuple = (
    "deathtouch", "defender", "double strike", "first strike", "flash",
    "flying", "haste", "hexproof", "indestructible", "lifelink", "menace",
    "protection from", "reach", "shroud", "trample", "vigilance", "ward",
    "kicker", "flashback", "cycling", "landfall", "devoid", "delirium",
    "convoke", "prowess", "suspend", "daybound", "affinity for",
    "changeling", "infect", "disturb", "toxic", "warp", "madness",
    "max speed", "start your engines", "plot", "storm", "wither",
    "exhaust", "exalted", "mutate", "metalcraft", "evoke", "persist",
    "ferocious", "gift", "escape", "delve", "undying", "improvise",
    "threshold", "cascade", "backup", "adventure", "emerge", "offspring",
    "impending", "harmonize", "mobilize", "flurry", "rally", "revolt",
    "surveil", "scry", "annihilator", "phasing", "dash", "spectacle",
    "splice", "equip", "ninjutsu", "bloodthirst", "unearth", "overload",
    "prototype", "bargain", "craft", "saddle", "outlast", "riot",
    "amass", "enlist", "squad", "training", "connive", "casualty",
    "blitz", "decayed", "eternalize", "embalm", "afterlife", "mentor",
    "afflict", "crew", "fabricate", "partner", "meld", "explore",
    "ascend", "assist", "jump-start", "spectacle",
)

# Typed template fields the loader populates for a mechanic word, when
# the word has no enum member of its own. Presence of the field with a
# truthy value means "modelled".
_FIELD_FOR_WORD = {
    "ward": "ward_cost",
    "flashback": "flashback_cost",
    "cycling": "cycling_cost_data",
    "kicker": "kicker_cost",
    "evoke": "evoke_cost",
    "escape": "escape_cost",
    "dash": "dash_cost",
    "warp": "warp_cost",
    "plot": "plot_cost",
    "delve": "has_delve",
    "cascade": "is_cascade",
    "spectacle": "spectacle_cost",
    "madness": "madness_cost",
    "splice": "splice_cost",
    "landfall": "has_landfall",
    "delirium": "has_delirium",
    "protection from": "protection_from_colors",
    "surveil": "has_surveil",
    "scry": "has_scry",
    "equip": "equip_cost",
    "mobilize": "has_mobilize",
    "storm": "is_storm_spell",
    "metalcraft": "has_artifact_count_scaling",
    "affinity for": "has_artifact_synergy",
    "revolt": "removal_mv_condition",
    # Flurry (Bloomburrow) is the ordinal-cast-trigger mechanic: "whenever
    # you cast your second spell each turn" is typed into
    # ordinal_cast_trigger (parse_ordinal_cast_trigger), so a flurry card
    # is modelled iff that field is populated.
    "flurry": "ordinal_cast_trigger",
}

_ENUM_NAMES = {k.name.lower().replace("_", " ") for k in Keyword}


def _modelled(word: str, template) -> bool:
    if word in _ENUM_NAMES:
        return True
    field = _FIELD_FOR_WORD.get(word)
    if field and getattr(template, field, None):
        return True
    return False


def keyword_words(oracle_text: str) -> Iterable[str]:
    lo = (oracle_text or "").lower()
    for w in KEYWORD_WORDS:
        # Word boundary so "flash" does not match "flashback".
        if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", lo):
            yield w


def census_template_keywords(template, game=None) -> list:
    """Record every keyword word on `template` the engine has no model
    for (once per word, process-wide). Returns the words recorded."""
    out = []
    for w in keyword_words(getattr(template, "oracle_text", "") or ""):
        if w == "flash" and "flashback" in (template.oracle_text or "").lower() \
                and not re.search(r"(?<![a-z])flash(?![a-z])", (template.oracle_text or "").lower()):
            continue
        if not _modelled(w, template):
            census("keyword/unmodelled", w, f"first seen on {template.name}", game=game)
            out.append(w)
    return out


def census_unhandled_effects(game=None) -> list:
    """Fold the process-level unhandled-effect sink into the audit census.

    Every effect that resolved through no handler — recorded in
    `engine.effect_diagnostics` at its resolution seam — becomes an
    `unhandled/<timing>` coverage fact (once per (timing, card) process-wide,
    via `census`'s dedupe), so a full audited matrix ranks silent no-ops by
    frequency alongside the keyword census. Pure observation; returns the
    (timing, card) pairs recorded."""
    from . import effect_diagnostics
    out = []
    for card_name, timing in effect_diagnostics.unhandled_effects():
        census(f"unhandled/{timing}", card_name, game=game)
        out.append((timing, card_name))
    return out
