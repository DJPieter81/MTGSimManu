"""Oracle Text Parser — derive card properties from oracle text.

Extracts structured data from oracle text at card load time:
- Ritual mana production
- Cycling costs
- Energy production
- Cascade status
- X-cost spell properties
- Token definitions

This replaces the hardcoded data tables in game_state.py
(RITUAL_CARDS, CYCLING_COSTS, ENERGY_PRODUCERS, etc.)
"""
from __future__ import annotations
import re
from typing import Dict, Optional, Tuple

from engine.oracle_clauses import split_abilities, split_clauses


def parse_ritual_mana(oracle: str) -> Optional[Tuple[str, int]]:
    """Parse mana production from oracle text.

    Returns (color, amount) or None if not a ritual.
    E.g., "Add {R}{R}{R}" → ("R", 3)
    """
    oracle = oracle.lower()
    # Use word boundaries to avoid matching "additional" (e.g. in "Kicker {W}
    # (You may pay an additional {W}..." on Orim's Chant, which was being
    # mis-parsed as a 2-W ritual and producing mana instead of silencing).
    if not re.search(r'\badd\b', oracle):
        return None

    # Only look at the first sentence containing a standalone "add"
    add_sentence = ''
    for sentence in oracle.split('.'):
        if re.search(r'\badd\b', sentence):
            add_sentence = sentence
            break
    if not add_sentence:
        return None

    # Count mana symbols in the add clause only
    for color in ['R', 'G', 'U', 'B', 'W', 'C']:
        pattern = '{' + color.lower() + '}'
        count = add_sentence.count(pattern)
        if count >= 2:
            return (color, count)

    # "Add two mana in any combination" (Manamorphose)
    m = re.search(r'\badd\s+(\w+)\s+mana', oracle)
    if m:
        word_to_num = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}
        amount = word_to_num.get(m.group(1), 0)
        if amount > 0:
            return ('any', amount)

    return None


def parse_cycling_cost(oracle: str) -> Optional[Dict]:
    """Parse cycling cost from oracle text.

    Returns {'mana': int, 'life': int, 'colors': set} or None.
    """
    oracle = oracle.lower()
    if 'cycling' not in oracle:
        return None

    # "Cycling {1}{U}" → mana=2, colors={'U'}
    m = re.search(r'cycling[—\s]+(?:pay\s+)?(.+?)(?:\s*\(|$)', oracle)
    if not m:
        return None

    cost_str = m.group(1).strip()
    mana = 0
    life = 0
    colors = set()

    # Count mana symbols
    for color in ['W', 'U', 'B', 'R', 'G']:
        pattern = '{' + color.lower() + '}'
        count = cost_str.count(pattern)
        mana += count
        if count > 0:
            colors.add(color)

    # Generic mana {1}, {2}, etc.
    for gm in re.findall(r'\{(\d+)\}', cost_str):
        mana += int(gm)

    # Life payment: "pay N life"
    lm = re.search(r'pay\s+(\d+)\s+life', cost_str)
    if lm:
        life = int(lm.group(1))
    elif '—pay' in oracle.replace(' ', ''):
        # "Cycling—Pay 2 life"
        lm2 = re.search(r'pay\s+(\d+)\s+life', oracle)
        if lm2:
            life = int(lm2.group(1))

    return {'mana': mana, 'life': life, 'colors': colors}


# Basic land subtypes that appear as typecycling prefixes
# (e.g. "Swampcycling" → search for a Swamp card).  Case-sensitive
# values match the subtype strings stored on CardTemplate.subtypes.
_BASIC_LAND_SUBTYPES = {
    'plains': 'Plains', 'island': 'Island', 'swamp': 'Swamp',
    'mountain': 'Mountain', 'forest': 'Forest',
    # Non-basic but valid land subtypes that appear as typecycling prefixes
    # in the wider multiverse (e.g. Desertcycling in Amonkhet).
    'desert': 'Desert',
}


def parse_cycling_variant(oracle: str) -> Optional[Dict]:
    """Classify a cycling variant and return the library-search predicate.

    Plain cycling (``Cycling {cost}``) and no-cycling cards return ``None``:
    the resolver should just draw a card.
    Landcycling / typecycling returns a predicate dict whose fields a
    library card must satisfy to be a legal tutor target::
        {
            'require_types':     set[str],  # lowercase CardType values
            'require_supertypes': set[str], # lowercase Supertype values
            'require_subtypes':   set[str], # case-sensitive subtype names
        }
    All three sets are ANDed; empty set = no constraint.  Examples::
        "Artifact landcycling"  → types={land, artifact}
        "Basic landcycling"     → types={land}, supertypes={basic}
        "Landcycling"           → types={land}
        "Swampcycling"          → types={land}, subtypes={Swamp}
        "Slivercycling"         → subtypes={Sliver}   (creature type)
    The parser deliberately ignores "When you cycle this card, you may
    search your library..." triggered abilities (e.g. Krosan Tusker):
    those resolve after a plain-cycling draw, not in place of it.
    """
    oracle_lower = oracle.lower()
    if 'cycling' not in oracle_lower:
        return None

    # Landcycling comes in three forms; check most specific first.
    if re.search(r'\bartifact\s+landcycling\b', oracle_lower):
        return {'require_types': {'land', 'artifact'},
                'require_supertypes': set(),
                'require_subtypes': set()}
    if re.search(r'\bbasic\s+landcycling\b', oracle_lower):
        return {'require_types': {'land'},
                'require_supertypes': {'basic'},
                'require_subtypes': set()}
    if re.search(r'\blandcycling\b', oracle_lower):
        return {'require_types': {'land'},
                'require_supertypes': set(),
                'require_subtypes': set()}

    # Typecycling: "<prefix>cycling {cost}" where <prefix> is not
    # "land"/"artifact"/"basic".  Bound to followed-by "{" so we do not
    # capture the word "cycling" itself or an in-prose triggered clause.
    m = re.search(r'\b(\w+)cycling\b\s*\{', oracle_lower)
    if not m:
        return None
    prefix = m.group(1)
    if prefix in _BASIC_LAND_SUBTYPES:
        return {'require_types': {'land'},
                'require_supertypes': set(),
                'require_subtypes': {_BASIC_LAND_SUBTYPES[prefix]}}
    # Creature-type cycling (Slivercycling, Wizardcycling, ...) —
    # derive the subtype from the in-reminder-text search phrase to
    # preserve canonical capitalisation.
    m2 = re.search(
        r'search your library for a[n]?\s+([a-z]+)\s+card',
        oracle_lower,
    )
    if m2:
        return {'require_types': set(),
                'require_supertypes': set(),
                'require_subtypes': {m2.group(1).capitalize()}}
    # Unrecognised typecycling variant — fall back to plain cycling so
    # the engine does not silently drop the draw.
    return None


def parse_energy_production(oracle: str) -> int:
    """Count energy production from oracle text.

    Returns the number of {E} symbols in the first energy-producing clause.
    Skips clauses gated by "Whenever ... enters/attacks/dies" — those are
    triggered abilities that fire in response to other events, not static
    ETB production. Guide of Souls was being credited 1 energy at its own
    ETB because its triggered "whenever another creature you control enters"
    clause matched the raw "get {e}" regex.
    """
    oracle = oracle.lower()
    if '{e}' not in oracle and 'energy' not in oracle:
        return 0

    # Look for "get {E}" clauses. Return the first one whose sentence is
    # NOT gated by a "whenever" trigger.
    for m in re.finditer(r'(?:get|gets?)\s+((?:\{e\})+)', oracle):
        # Find the boundary of this "sentence" — the last sentence-terminator
        # (period, newline) before the match, or start of string.
        sentence_start = max(
            oracle.rfind('.', 0, m.start()),
            oracle.rfind('\n', 0, m.start()),
            -1
        ) + 1
        clause = oracle[sentence_start:m.end()]
        if 'whenever' in clause or 'when ' in clause.lstrip()[:5]:
            continue  # triggered ability — not static ETB
        return m.group(1).count('{e}')

    return 0


def has_cascade(oracle: str) -> bool:
    """Check if oracle text has cascade keyword."""
    return 'cascade' in oracle.lower()


def parse_x_cost(oracle: str, name: str, mana_cost_str: str = "") -> Optional[Dict]:
    """Parse X-cost spell properties from the printed mana cost.

    Per CR 107.3, the X that the caster chooses lives in the spell's *mana
    cost*. X tokens in oracle body (e.g. "where X is the number of lands
    you control") are derived at resolution and are unrelated to the cost
    paid at cast time. Conflating the two mis-tags fixed-cost cards like
    Consult the Star Charts ({5}{U}; oracle "Look at top X cards … where
    X is the number of lands you control") as X-cost spells, after which
    the engine asks for an X payment and the spell resolves silently.

    Therefore: parse_x_cost returns a non-None result iff `{X}` appears
    in the printed mana cost string. The oracle text is consulted only
    for downstream `effect` classification (charge counters vs +1/+1
    counters), never for the cost-X predicate itself.
    """
    mana_lower = mana_cost_str.lower() if mana_cost_str else ""
    if '{x}' not in mana_lower:
        return None

    # Detect XX costs from mana cost string (e.g. Chalice {X}{X})
    multiplier = 2 if '{x}{x}' in mana_lower else 1

    # Determine counter type from oracle text (effect classification only —
    # this does NOT affect the cost-X predicate, which is mana-cost-only).
    oracle_lower = oracle.lower()
    effect = ""
    if 'charge counter' in oracle_lower:
        effect = "charge_counters"
    elif '+1/+1 counter' in oracle_lower:
        effect = "plus1_counters"

    return {
        'multiplier': multiplier,
        'min_x': 1 if multiplier == 2 else 0,
        'effect': effect,
    }


def is_living_end_cascader(oracle: str, card_types: list) -> bool:
    """Check if this card cascades into Living End."""
    return has_cascade(oracle)


def parse_splice_cost(oracle: str) -> Optional[int]:
    """Parse splice onto Arcane cost from oracle text.

    "Splice onto Arcane {1}{R}" → 2 (estimated CMC)
    Returns total CMC or None if no splice.
    """
    m = re.search(r'splice onto arcane[—\s]*((?:\{[^}]+\})+)', oracle, re.IGNORECASE)
    if not m:
        return None
    symbols = re.findall(r'\{([^}]+)\}', m.group(1))
    total = 0
    for s in symbols:
        if s.isdigit():
            total += int(s)
        else:
            total += 1  # colored mana = 1
    return total if total > 0 else None


def parse_cost_reduction(oracle: str) -> Optional[Dict]:
    """Parse cost reduction rules from oracle text.

    Returns {'target': str, 'amount': int, 'color': str|None} or None.

    A cost-reduction effect requires an explicit ``cost {N} less``
    pattern (e.g. "Spells you cast cost {1} less to cast"). The mere
    co-occurrence of ``'cost'`` and ``'less'`` is not sufficient — the
    substring ``'less'`` lives inside ``'colorless'`` and ``'cost'``
    appears in any ``mana cost {N}`` phrase, generating false
    positives on non-reducers like Urza's Saga, Trinisphere, and
    every cascade card. See ``tests/test_parse_cost_reduction_strict.py``.
    """
    oracle = oracle.lower()
    m = re.search(r'cost\s*\{(\d+)\}\s*less', oracle)
    if not m:
        return None
    amount = int(m.group(1))

    target = 'all'
    if 'instant and sorcery' in oracle or 'instants and sorceries' in oracle:
        target = 'instant_sorcery'
    elif 'creature spell' in oracle:
        target = 'creature'
    elif 'noncreature' in oracle:
        target = 'noncreature'

    color = None
    for c_name, c_code in [('red','R'),('blue','U'),('black','B'),('white','W'),('green','G')]:
        if c_name in oracle:
            color = c_code
            break

    return {'target': target, 'amount': amount, 'color': color}


def parse_counter_tax(oracle: str) -> int:
    """Parse a "soft counter" tax amount from oracle text.

    Rule: "Counter target spell unless its controller pays {N}" gives
    the targeted spell's controller a real choice — pay {N} generic
    mana or the spell is countered. Modern examples: Metallic Rebuke,
    Mana Leak, Countersquall (Mana Leak/Countersquall have no "improvise"
    prefix; the pattern below only anchors on the counter+unless+pays
    clause, not on any particular preceding ability).

    Returns 0 (no tax — an unconditional "hard" counter like
    Counterspell/Essence Scatter) when the clause is absent.

    Scoped to a single ability paragraph via `split_abilities` so an
    unrelated "unless...pays" clause elsewhere on a multi-ability card
    (e.g. a land's "enters tapped unless you pay {N} life") cannot be
    mistaken for a counter tax on a card that also happens to counter
    something in a separate ability.
    """
    for clause in split_abilities(oracle or ''):
        low = clause.lower()
        if 'counter target' not in low:
            continue
        m = re.search(
            r"unless\s+(?:its|their)\s+controller\s+pays\s*\{(\d+)\}",
            low,
        )
        if m:
            return int(m.group(1))
    return 0


def parse_ward_cost(oracle: str) -> int:
    """Parse a Ward {N} mana-cost tax from oracle text (CR 702.21a).

    Ward is a triggered ability that lives on the PERMANENT itself:
    "Whenever this permanent becomes the target of a spell or ability
    an opponent controls, counter that spell or ability unless its
    controller pays [cost]." Structurally this is the mirror image of
    `parse_counter_tax`/1a's counter-tax framework: there, a
    counterSPELL taxes the TARGETED spell's controller; here, the
    TARGETED PERMANENT taxes the SOURCE spell/ability's own caster
    for having chosen it as a target at all.

    Scoped to a clause (`split_clauses` — sentence-level, not
    `split_abilities`'s paragraph level) that STARTS with "ward",
    matching how the keyword is always printed as its own standalone
    ability line ("Ward {2}"). This deliberately excludes ward
    CONFERRED to another object mid-sentence — "Equipped creature...
    has ward {1}" (an Equipment granting ward to whatever it's
    attached to), "...becomes a 7/7 ... creature with ward {3}" (an
    activated ability that temporarily grants ward to its source) —
    since those clauses don't start with "ward" they never match
    here. That's a real, separate mechanism (dynamically granted
    keyword, same class as 0b's `ContinuousEffectsManager` migration,
    not a static field on the granting card's own template) —
    deferred; see the rules-foundation tracker doc's Ward section.

    Scope: mana-cost shape only ("Ward {N}"). DB-wide census (a card
    whose oracle has any clause literally starting with "ward"): 76
    are mana-cost-shaped, 15 are "Ward—Pay N life" (life-shaped), 26
    are other cost shapes (discard/sacrifice/exile/collect evidence/
    etc, several with no fixed numeric amount at all — "pay life
    equal to this creature's power" has no static {N} to tax with).
    Mana-shape is the dominant, most clearly-scoped bucket (per
    CLAUDE.md's class-size discipline) and the only one implemented
    in this first pass. Returns 0 for every excluded shape too (same
    "0 = no tax to enforce" contract `parse_counter_tax` uses for
    hard counters) — callers must not read a 0 as proof the card has
    no ward at all, only that this parser doesn't yet enforce it.
    """
    for clause in split_clauses(oracle or ''):
        low = clause.strip().lower()
        if not low.startswith('ward'):
            continue
        m = re.search(r'ward\s*[\{—-]*\s*\{(\d+)\}', low)
        if m:
            return int(m.group(1))
    return 0


def parse_protection_from(oracle: str) -> frozenset:
    """Parse "protection from <color>" clauses (CR 702.16), including
    the compound "protection from X and from Y" form (e.g. Sanctifier
    en-Vec's "Protection from black and from red").

    Returns a frozenset of `engine.mana.Color` values a blocker/target
    with this protection can't be blocked-by/targeted-by (CR 702.16d,
    702.16e). Type-based protection ("protection from artifacts") and
    "protection from everything" are not covered here — 0 cards in
    the registered 16-deck pool use those forms; extend when one
    enters the pool (same class-size discipline as every other
    oracle-derived field in this module).
    """
    from engine.mana import Color
    color_words = {
        'red': Color.RED, 'blue': Color.BLUE, 'black': Color.BLACK,
        'white': Color.WHITE, 'green': Color.GREEN,
    }
    _COLOR_ALT = r'(?:red|blue|black|white|green)'
    # Match the whole compound span ("protection from black and from
    # red") first, then pull every color word out of just that span —
    # a plain findall on the whole oracle would miss the second color
    # in the compound form, since only the FIRST one is preceded by
    # the word "protection" (the rest are "and from <color>").
    clause = re.search(
        rf'protection from {_COLOR_ALT}(?:\s+and\s+from\s+{_COLOR_ALT})*',
        (oracle or '').lower(),
    )
    if not clause:
        return frozenset()
    found = re.findall(_COLOR_ALT, clause.group(0))
    return frozenset(color_words[c] for c in found)


def parse_domain_reduction(oracle: str) -> Optional[int]:
    """Parse domain-based cost reduction.

    Returns reduction per basic land type, or None.
    """
    oracle = oracle.lower()
    if 'basic land type' not in oracle or 'less' not in oracle:
        return None
    m = re.search(r'costs?\s*\{(\d+)\}\s*less.*basic land type', oracle)
    return int(m.group(1)) if m else 1


def _normalize_gy_type(raw: str) -> str:
    """Turn a captured card-type phrase ("creature", "instant and
    sorcery", "nonbasic land", "" for bare "cards") into a canonical
    bucket token. Resolution-side dispatch (CardInstance._get_
    graveyard_type_count) decides what each token means; this is
    pure text normalization, mirroring _get_permanent_type_count's
    "parse generically, resolve specifically" split."""
    raw = raw.strip().lower().replace(',', '')
    if not raw:
        return "any"
    return re.sub(r'\s+', '_', raw)


def _normalize_gy_scope(raw: str) -> str:
    """"your opponents'"/"your opponents" -> "opponents"; "your" and
    "all" pass through unchanged."""
    raw = raw.strip().lower()
    if raw.startswith("your opponent"):
        return "opponents"
    return raw


def _detect_gy_formula(clause: str) -> str:
    """Which of the three real graveyard-census P/T shapes a clause
    uses:
    - "sym": "power and toughness are each equal to <count>" (both
      stats share the same value — Mortivore, Apocalypse Demon, ...).
    - "goyf": "power is equal to <count> and its toughness is equal
      to that number plus 1" (Tarmogoyf's own asymmetric offset,
      reused by Lhurgoyf/Urborg Lhurgoyf/Souls of the Lost — but NOT
      Tarmogoyf itself, which is claimed by the earlier "card type"
      bucket check before this function ever runs).
    - "power_only": only power is defined by the count; toughness
      stays at its own fixed printed value (Enigma Drake-class —
      "power is equal to <count>" with no toughness clause at all).
    """
    if 'power and toughness are each equal to' in clause:
        return 'sym'
    if re.search(r'toughness is equal to that number plus (?:1|one)', clause):
        return 'goyf'
    return 'power_only'


# "X's power [and toughness] [is/are each] equal to the number of
# <TYPE?> card(s) in <SCOPE> graveyard(s)" — the generalized shape of
# the graveyard-census CDA family (Mortivore/Bonehoard's "creature
# cards in all graveyards" is one member of this family, not a shape
# unto itself: verified against the full DB, 26 real cards share this
# structure once TYPE and SCOPE are treated as parameters instead of
# the original bucket's hardcoded "instant/sorcery, your graveyard
# only"). TYPE is captured generically (0-3 words directly before
# "card(s)") the same way permanent_count captures its noun — no
# enumeration of which words are valid at parse time; resolution
# (CardInstance._get_graveyard_type_count) decides what each token
# means and falls back to counting every card for an unrecognized
# token, matching permanent_count's fallback discipline.
_GY_COUNT_RE = re.compile(
    r'(?:power|toughness)[^.]*?equal to[^.]*?number of\s+'
    r'((?:\S+\s+){0,3}?)cards?\s+in\s+'
    r"(your opponents['’]?|your|all)\s+graveyards?"
)


def detect_power_scaling(oracle: str) -> str:
    """Detect dynamic P/T scaling from oracle text.

    Returns: "domain", "permanent_count:<word>", "tarmogoyf",
    "delirium", "graveyard_count:<formula>:<type>:<scope>",
    "graveyard" (legacy fallback for graveyard-census clauses that
    don't fit the structured TYPE/SCOPE shape — e.g. Crackling
    Drake's "you own in exile and in your graveyard" compound zone),
    or "".
    """
    oracle = oracle.lower()
    if 'basic land type' in oracle and ('power' in oracle or 'toughness' in oracle or 'equal' in oracle):
        return "domain"
    # "X's power and toughness are each equal to the number of <Y>
    # you control" — the single most common CDA shape in Magic
    # (Cultivator Colossus/Crusader of Odric/Master of Etherium/
    # Darksteel Juggernaut/... — 47 cards share this exact phrasing
    # in this DB). <Y> is a card type ("lands"), the generic
    # "permanents", or a tribal/land subtype ("Soldiers", "Islands"):
    # resolved generically by CardInstance._get_permanent_type_count,
    # not enumerated here.
    m = re.search(
        r'power and toughness are each equal to the number of (\w+) you control',
        oracle,
    )
    if m:
        return f"permanent_count:{m.group(1)}"
    if 'card type' in oracle and ('power' in oracle or 'equal' in oracle) and 'graveyard' in oracle:
        return "tarmogoyf"
    if ('delirium' in oracle or 'four or more card types' in oracle) and 'graveyard' in oracle:
        return "delirium"
    # "graveyard" scaling requires the actual CDA phrase shape —
    # power/toughness, then "equal to", then "number of", then
    # instant/sorcery, then graveyard, all in that order within one
    # SENTENCE ([^.]*? never crosses a period). The bare co-occurrence
    # of 'exile' + 'instant'/'sorcery' + 'graveyard' anywhere in a
    # clause false-positives on every Embalm/Eternalize reminder-text
    # card (293/21795 in this DB) AND, more subtly, on Scavenge
    # reminder text specifically ("Exile this card from your
    # graveyard: Put a number of +1/+1 counters equal to this card's
    # power ... Scavenge only as a SORCERY.") — 'power', 'equal',
    # 'graveyard', and 'sorcery' all co-occur in that one clause, but
    # in the WRONG order (power precedes "equal to", not "equal to
    # ... number of ... graveyard") and "sorcery" refers to the
    # activation timing restriction, not a CDA. The ordered,
    # sentence-scoped pattern rejects this shape while still matching
    # every real graveyard-count CDA (Enigma Drake, Haughty Djinn,
    # Crackling Drake, Melek, Magnivore, ...) and correctly excludes
    # live-pool card Murktide Regent (delve-triggered +1/+1 counters,
    # not a continuous graveyard-count CDA at all).
    gy_pattern = re.compile(
        r'(?:power|toughness)[^.]*?equal to[^.]*?number of'
        r'[^.]*?(?:instant|sorcery)[^.]*?graveyard'
    )
    for clause in split_abilities(oracle):
        m = _GY_COUNT_RE.search(clause)
        if m:
            gy_type = _normalize_gy_type(m.group(1))
            gy_scope = _normalize_gy_scope(m.group(2))
            gy_formula = _detect_gy_formula(clause)
            return f"graveyard_count:{gy_formula}:{gy_type}:{gy_scope}"
        if gy_pattern.search(clause):
            return "graveyard"
    return ""


def parse_planeswalker_abilities(oracle: str) -> Optional[Dict]:
    """Parse planeswalker loyalty abilities from oracle text.

    Returns dict with 'plus', 'minus', 'ult', 'starting_loyalty'.
    """
    oracle_lower = oracle.lower()
    if not any(f'[{sign}' in oracle_lower for sign in ['+', '−', '-', '0']):
        return None

    abilities = {}

    # Parse [+N]: effect
    plus_m = re.search(r'\[([+])(\d+)\]\s*:\s*(.+?)(?:\n|\[|$)', oracle, re.IGNORECASE)
    if plus_m:
        abilities['plus'] = (int(plus_m.group(2)), plus_m.group(3).strip()[:60])

    # Parse [−N]: effect or [-N]: effect
    minus_m = re.search(r'\[[−\-](\d+)\]\s*:\s*(.+?)(?:\n|\[|$)', oracle, re.IGNORECASE)
    if minus_m:
        cost = -int(minus_m.group(1))
        abilities['minus'] = (cost, minus_m.group(2).strip()[:60])

    # Parse ultimate (largest negative)
    ult_matches = re.findall(r'\[[−\-](\d+)\]\s*:\s*(.+?)(?:\n|\[|$)', oracle, re.IGNORECASE)
    if len(ult_matches) >= 2:
        # Ultimate is the one with highest cost
        ult = max(ult_matches, key=lambda m: int(m[0]))
        abilities['ult'] = (-int(ult[0]), ult[1].strip()[:60])

    if not abilities:
        return None

    return abilities


def has_delve(oracle: str) -> bool:
    """Check if card has delve keyword."""
    return 'delve' in oracle.lower()


def parse_dash_cost(oracle: str) -> Optional[int]:
    """Parse Dash cost from oracle text.

    "Dash {1}{R}" → 2 (estimated CMC)
    """
    m = re.search(r'dash\s*((?:\{[^}]+\})+)', oracle, re.IGNORECASE)
    if not m:
        return None
    symbols = re.findall(r'\{([^}]+)\}', m.group(1))
    total = 0
    for s in symbols:
        if s.isdigit():
            total += int(s)
        else:
            total += 1  # colored mana = 1
    return total if total > 0 else None


def parse_extra_land_drops(oracle: str) -> int:
    """Parse extra land drops from oracle text.

    "You may play two additional lands" → 2
    "You may play an additional land" → 1
    """
    lower = oracle.lower()
    if 'additional land' not in lower and 'extra land' not in lower:
        return 0
    if 'two additional land' in lower:
        return 2
    if 'three additional land' in lower:
        return 3
    if 'additional land' in lower or 'extra land' in lower:
        return 1
    return 0


# ─────────────────────────────────────────────────────────────
# Creature-land animation — "{cost}: … this land becomes an N/M …
# creature … until end of turn. It's still a land."  (Track H)
#
# One regex covers the whole modern creature-land class: the cost is
# the run of mana symbols immediately before the colon; "until end of
# turn" may lead or trail the clause; the keyword list (if any)
# follows "creature with".  No card names — the oracle template is
# shared by every land in the class.
# ─────────────────────────────────────────────────────────────

_LAND_ANIMATION_RE = re.compile(
    r'((?:\{[^}]+\})+)\s*:\s*'                 # activation cost
    r'(?:until end of turn, )?'                # leading duration
    r'this land becomes an? (\d+)/(\d+)\s+'    # printed P/T
    r'([^.\n]*?)creature([^.\n]*)',            # colors/types … keywords
    re.IGNORECASE)


def parse_land_animation(oracle: str) -> Optional[Dict]:
    """Parse an activated land-animation line from oracle text.

    Returns ``{'cost': total mana symbols, 'power': N, 'toughness': M,
    'keywords': set of lowercase keyword words}`` or None when the
    text carries no animate line.  ``cost`` counts a digit symbol at
    face value and any non-digit symbol as one mana — the same
    generic-count payment model the granted-ability dispatch uses.
    """
    m = _LAND_ANIMATION_RE.search(oracle)
    if m is None:
        return None
    clause_tail = m.group(5).lower()
    clause_head = m.group(0).lower()
    if 'until end of turn' not in clause_head and \
            'until end of turn' not in clause_tail:
        return None  # permanent animation is a different mechanic
    cost_symbols = re.findall(r'\{([^}]+)\}', m.group(1))
    if any(s.lower() == 't' for s in cost_symbols):
        return None  # tap-cost animation cannot attack the same turn
    cost = sum(int(s) if s.isdigit() else 1 for s in cost_symbols)
    keywords = {kw for kw in _KEYWORD_WORDS if kw in clause_tail}
    return {
        'cost': cost,
        'power': int(m.group(2)),
        'toughness': int(m.group(3)),
        'keywords': keywords,
    }


def parse_escape_cost(oracle: str) -> Optional[Dict]:
    """Parse Escape cost from oracle text.

    "Escape—{R}{R}{W}{W}, Exile five other cards" → {'cmc': 4, 'exile': 5}
    """
    m = re.search(r'escape[—\-]\s*((?:\{[^}]+\})+),?\s*exile\s+(\w+)\s+other\s+card',
                  oracle, re.IGNORECASE)
    if not m:
        return None
    cost_symbols = re.findall(r'\{([^}]+)\}', m.group(1))
    cmc = 0
    for s in cost_symbols:
        if s.isdigit():
            cmc += int(s)
        else:
            cmc += 1
    # Parse exile count
    word_to_num = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
                   'six': 6, 'seven': 7, 'eight': 8}
    exile_word = m.group(2).lower()
    exile_count = word_to_num.get(exile_word)
    if exile_count is None and exile_word.isdigit():
        exile_count = int(exile_word)
    return {'cmc': cmc, 'exile': exile_count or 5}


def parse_equip_cost(oracle: str) -> Optional[int]:
    """Parse Equip cost from oracle text.

    "Equip {2}" → 2
    """
    m = re.search(r'equip\s*\{(\d+)\}', oracle, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Equip with colored mana: "Equip {B}{B}"
    m = re.search(r'equip\s*((?:\{[^}]+\})+)', oracle, re.IGNORECASE)
    if m:
        symbols = re.findall(r'\{([^}]+)\}', m.group(1))
        return sum(int(s) if s.isdigit() else 1 for s in symbols)
    return None


def derive_tags_from_oracle(oracle: str, keywords: set, card_types: set,
                            subtypes: set, power: int = 0) -> set:
    """Derive semantic tags from oracle text and card properties.

    Returns a set of tags like 'threat', 'ramp', 'token_maker', 'etb_value', etc.
    """
    tags = set()
    lower = oracle.lower()

    # Threat detection: big creatures, evasion, or growing
    if power >= 4:
        tags.add("threat")
    if any(kw in str(keywords).lower() for kw in ('flying', 'trample')) and power >= 3:
        tags.add("threat")
    if '+1/+1 counter' in lower and ('enters' in lower or 'combat damage' in lower):
        tags.add("threat")

    # Ramp: puts lands onto battlefield or adds mana
    if ('land' in lower and 'onto the battlefield' in lower
            and ('search' in lower or 'put' in lower)):
        tags.add("ramp")
    if 'untap' in lower and 'enters tapped' in lower:
        tags.add("ramp")

    # Token maker
    if 'create' in lower and 'token' in lower:
        tags.add("token_maker")
    if 'amass' in lower:
        tags.add("token_maker")

    # ETB value: "when * enters" with a beneficial effect.
    # Clause-scoped (E5): a triggered ability's condition and effect
    # share one ability paragraph (CR 603.1), so the value keyword must
    # appear in the SAME paragraph as the "when … enters" trigger — a
    # damage verb in a separate ability (or reminder text on another
    # line) must not tag.
    for ability in split_abilities(lower):
        if 'when ' in ability and 'enters' in ability and any(
                kw in ability for kw in ('draw', 'damage', 'destroy', 'exile',
                                         'search', 'create', 'return', 'gain')):
            tags.add("etb_value")
            break

    # Flash detection from oracle (backup if keyword not parsed)
    if 'flash' in lower.split('\n')[0] if lower else False:
        tags.add("instant_speed")

    # Evoke detection
    if 'evoke' in lower:
        tags.add("evoke")
    if re.search(r'evoke.*exile.*card.*from.*hand', lower):
        tags.add("evoke_pitch")

    # Card advantage
    if 'draw' in lower and ('cards' in lower or 'two' in lower or 'three' in lower):
        tags.add("card_advantage")

    # Equipment
    if 'equip' in lower:
        tags.add("equipment")

    return tags


# ─── Token-spec parser ──────────────────────────────────────────────


_TOKEN_SPEC_RE = re.compile(
    r"create\s+(?:a|an|\d+)?\s*"
    r"(?P<power>\d+)\s*/\s*(?P<toughness>\d+)"
    r"(?:\s+\w+)*?"        # color words, "phyrexian", etc.
    r"\s+(?P<subtype>[A-Z][a-zA-Z]+)\s+"
    r"(?P<types>(?:artifact|creature|enchantment)"
    r"(?:\s+(?:artifact|creature|enchantment))*)\s+"
    r"token",
    re.IGNORECASE,
)


_TOKEN_KEYWORD_RE = re.compile(
    r"with\s+([a-z, ]+?(?:\s+and\s+[a-z, ]+)?)"
    r"(?:[\.\"]|$|\s+(?:and|but))",
    re.IGNORECASE,
)


# Keyword vocabulary — only the abilities the engine recognizes.
# Source: engine.cards.Keyword enum members. Mapping the raw oracle
# words to the canonical Keyword names handled at lookup time.
_KEYWORD_WORDS = {
    "flying", "trample", "haste", "vigilance", "lifelink",
    "deathtouch", "first strike", "double strike", "menace",
    "reach", "hexproof", "indestructible", "ward", "flash",
    "defender", "prowess", "unblockable", "intimidate", "fear",
    "shadow", "horsemanship",
}


def parse_token_spec(oracle: str) -> Optional[Dict]:
    """Parse a "create a P/T <subtype> [types] token" idiom.

    Returns a dict::

        {
          "power": int,
          "toughness": int,
          "subtype": str,         # creature subtype ("Wurm", "Drone")
          "types": List[str],     # ["artifact", "creature"]
          "keywords": List[str],  # ["flying"], etc.
        }

    Or None if no token-creation idiom is found.

    The parser is intentionally narrow: it requires the canonical
    "P/T <subtype> <type chain> token" shape that Modern oracle
    text uses for static token specs. Tokens whose stats depend on
    game state (e.g. "0/0 colorless Construct artifact creature
    token with 'gets +1/+1 for each artifact you control'") are
    parsed on the static portion; the dynamic +N/+N is handled
    separately via `engine/cards.py:_dynamic_base_power`.
    """
    if "token" not in oracle.lower() or "create" not in oracle.lower():
        return None
    m = _TOKEN_SPEC_RE.search(oracle)
    if not m:
        return None
    types = [t.strip() for t in m.group("types").lower().split()]
    keywords = []
    # Look for "with <keyword>[ and <keyword>]" within ~80 chars
    # of the token-spec match to scope to that token's keyword
    # clause (avoids capturing keywords from a different sentence).
    after = oracle[m.end():m.end() + 120]
    kw_match = _TOKEN_KEYWORD_RE.search(after)
    if kw_match:
        kw_text = kw_match.group(1).lower()
        for word in _KEYWORD_WORDS:
            if word in kw_text:
                keywords.append(word)
    return {
        "power": int(m.group("power")),
        "toughness": int(m.group("toughness")),
        "subtype": m.group("subtype"),
        "types": types,
        "keywords": keywords,
    }


def is_metalcraft_mana_any_color(oracle: str) -> bool:
    """True iff the oracle text declares a metalcraft-gated
    "{T}: Add one mana of any color" ability.

    Pattern (case-insensitive):
      - "metalcraft —" introduces the ability
      - "{T}: Add" + "any color" within the same clause
      - "three or more artifacts" or "control three or more
        artifacts" qualifier (the metalcraft definition)

    Generic predicate replacing a previous card-name gate in
    engine/mana_payment.py. Future printings with the same
    metalcraft-mana idiom automatically hit this predicate
    without a code change.
    """
    if not oracle:
        return False
    lower = oracle.lower()
    if "metalcraft" not in lower:
        return False
    # The mana ability — "{T}: Add" + any-color clause.
    if "{t}: add" not in lower:
        return False
    if "any color" not in lower:
        return False
    return True


# ─── Saga chapter structure ────────────────────────────────────────────
#
# CR 714: saga oracle text is a sequence of chapter abilities, each
# introduced by one or more roman numerals ("I —", "I, II —"). Two
# mechanically distinct chapter shapes exist:
#
#   1. Plain one-shot effects ("Search your library ...") — the effect
#      happens when the chapter's lore counter lands.
#   2. Ability grants ('This Saga gains "<cost>: <effect>"') — the
#      permanent GAINS the quoted activated ability; the quoted effect
#      only happens when its controller activates it and pays the cost.
#
# `parse_saga_chapters` extracts the chapter map; `extract_granted_ability`
# classifies shape 2 and returns the quoted ability text.

# Roman numerals I..V — CR 714.2 sagas cap at chapter V in practice.
_SAGA_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}

_SAGA_CHAPTER_RE = re.compile(
    r"^\s*(?P<nums>[IVX]+(?:\s*,\s*[IVX]+)*)\s*[—–-]\s*(?P<text>.+)$",
    re.MULTILINE,
)

# 'gains "<ability>"' with either quote style. The quoted ability must
# contain a ':' cost separator to count as an activated ability (grants
# of keywords/static text are not activation candidates).
_SAGA_GAINS_RE = re.compile(
    r"gains\s+\"(?P<dq>[^\"]+)\"|gains\s+'(?P<sq>[^']+)'",
    re.IGNORECASE,
)


def parse_saga_chapters(oracle: str) -> Dict[int, str]:
    """Map chapter number → chapter effect text for a saga's oracle.

    Lines with multiple numerals ("I, II — <text>") assign the same
    text to each listed chapter. Returns {} when the oracle has no
    chapter markers (non-saga or unparsable text).
    """
    chapters: Dict[int, str] = {}
    if not oracle:
        return chapters
    for m in _SAGA_CHAPTER_RE.finditer(oracle):
        text = m.group("text").strip()
        for numeral in m.group("nums").split(","):
            n = _SAGA_ROMAN.get(numeral.strip())
            if n is not None:
                chapters[n] = text
    return chapters


def extract_granted_ability(chapter_text: Optional[str]) -> Optional[str]:
    """If a chapter's effect grants a quoted ACTIVATED ability, return
    the quoted ability text ("<cost>: <effect>"); otherwise None.

    Shape: '... gains "<cost>: <effect>"'. The ':' requirement filters
    out keyword/static grants, which have no activation semantics.
    """
    if not chapter_text:
        return None
    m = _SAGA_GAINS_RE.search(chapter_text)
    if not m:
        return None
    ability = (m.group("dq") or m.group("sq") or "").strip()
    if ":" not in ability:
        return None
    return ability
