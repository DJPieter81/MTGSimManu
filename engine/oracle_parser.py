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
from typing import Dict, List, Optional, Tuple

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


def grants_flashback_to_gy_spells(oracle: str) -> bool:
    """True for cards that grant flashback to instant/sorcery cards in the graveyard.

    Matches Past in Flames, Snapcaster Mage, and any other card whose oracle
    text combines all three signals: flashback, graveyard, and instant/sorcery.
    Used to identify "chain extender" cards in combo scoring without re-reading
    oracle text at decision time.

    Class size: every card that grants flashback to GY instants/sorceries in
    Modern — PiF, Snapcaster Mage, and any future printings of the pattern.
    Stored as CardTemplate.grants_flashback_to_gy_spells (bool) at DB load.
    """
    o = oracle.lower()
    return (
        'flashback' in o
        and 'graveyard' in o
        and ('instant' in o or 'sorcery' in o)
    )


def parse_flashback_mana_cost(oracle: str) -> "Optional[ManaCost]":
    """Parse the mana portion of a native Flashback cost from oracle text.

    Returns a ManaCost for the flashback mana cost if the card has a printed
    Flashback keyword with a mana-cost component, or None when:
      - the card has no Flashback keyword, or
      - the flashback cost is entirely non-mana (e.g. 'Sacrifice a Mountain').

    Supported oracle patterns (MTGJSON uses {X} notation throughout):
      "Flashback {2}{R}"               -> ManaCost(generic=2, red=1)
      "Flashback {4}{R}"               -> ManaCost(generic=4, red=1)
      "Flashback {1}{B}"               -> ManaCost(generic=1, black=1)
      "Flashback--{1}{U}, Pay 3 life." -> ManaCost(generic=1, blue=1)
      "Flashback--Sacrifice a Mountain." -> None (no mana component)

    Cards granted flashback by Past in Flames / Snapcaster Mage return None;
    those pay their regular mana_cost (oracle: 'flashback cost is equal to its
    mana cost'). None for Lava Dart pattern (sacrifice-only, no mana to pay).

    Class size: every card with a printed Flashback cost in Modern.
    Subsystem: oracle_parser -> card_database (CardTemplate.flashback_cost).
    """
    m = re.search(
        r'[Ff]lashback[\s—\-]*((?:\{[^}]+\})+)',
        oracle,
    )
    if not m:
        return None  # No mana-cost component in flashback cost
    symbols = re.findall(r'\{([^}]+)\}', m.group(1))
    if not symbols:
        return None
    cost = _parse_mana_symbols_to_cost(symbols)
    return cost if cost.cmc > 0 else None


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


def parse_splice_cost(oracle: str) -> "Optional[ManaCost]":
    """Parse splice onto Arcane cost from oracle text.

    "Splice onto Arcane {1}{R}" → ManaCost(generic=1, red=1)
    Returns a ManaCost preserving colour pips, or None if no splice.
    """
    m = re.search(r'splice onto arcane[—\s]*((?:\{[^}]+\})+)', oracle, re.IGNORECASE)
    if not m:
        return None
    symbols = re.findall(r'\{([^}]+)\}', m.group(1))
    cost = _parse_mana_symbols_to_cost(symbols)
    return cost if cost.cmc > 0 else None


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


def parse_is_land_sacrifice_tutor(oracle: str) -> bool:
    """True for the Scapeshift shape: a spell that sacrifices any number of
    the caster's lands and searches the library for that many lands.
    Parsed once at DB load into `CardTemplate.is_land_sacrifice_tutor`
    (oracle-runtime-parse ratchet: consumers read the typed field)."""
    low = (oracle or '').lower()
    return ('sacrifice any number of lands' in low
            and 'search your library' in low)


# ── X-bound creature tutor, SPELL side (Green Sun's Zenith shape) ────
#
# Same discipline as `parse_activation_tutor` below, applied to a spell's
# own resolution text instead of an activated ability's effect text, and
# producing the SAME structured spec so one delivery helper serves both:
#
#   * The search sentence is matched with `fullmatch` — a trailing rider
#     the vocabulary does not know fails the match, and the card stays
#     unclassified instead of resolving with the rider silently dropped.
#   * Every OTHER sentence in the same ability paragraph must match a
#     known rider; every other PARAGRAPH must be a keyword line (which
#     carries no resolution effect of its own).
#   * Battlefield destination is limited to CREATURE searches, for the
#     same reason as the activation parser: a non-creature permanent
#     entering this way carries entry statics this path does not wire up.

# The search sentence.  "creature" rides inside the qualifier group, as
# in `_TUTOR_SENTENCE_RE`, so one vocabulary serves both parsers.
_X_TUTOR_SEARCH_RE = re.compile(
    r'search your library(?P<gy> and/or graveyard)? for an? '
    r"(?P<quals>(?:[a-z][a-z\-\']* )*?)card with mana value x or less"
    r'(?:,| and)'
    r'(?: reveal it(?:,| and)?)?'
    r' put (?:it|that card) '
    r'(?P<dest>onto the battlefield(?P<tapped> tapped)?|into your hand)'
    r'(?P<counters> with x additional \+1/\+1 counters on it)?'
    r'(?:,? then shuffle(?: your library)?)?')

# Riders the engine executes, each with an existing typed mechanism:
#   shuffle-self         -> the zone funnel (graveyard -> library)
#   search-zone shuffle  -> bookkeeping this resolver already performs
#   haste at X           -> CardInstance.temp_keywords (the until-EOT channel)
#   team pump + haste    -> temp_power_mod / temp_toughness_mod / temp_keywords
_X_TUTOR_RIDERS = (
    ('self_shuffle_into_library',
     re.compile(r"shuffle .+ into its owner's library")),
    ('search_shuffle_note',
     re.compile(r'if you search your library this way, shuffle')),
    ('haste_at_x',
     re.compile(r'if x is (?P<n>\d+) or (?:more|greater), '
                r'it gains haste until end of turn')),
    ('team_pump_haste_at_x',
     re.compile(r'if x is (?P<n>\d+) or (?:more|greater), creatures you '
                r'control get \+x/\+x and gain haste until end of turn')),
)

# A paragraph carrying no resolution effect of its own: a keyword ability,
# optionally followed by its cost ("Convoke", "Harmonize {X}{G}{G}{G}{G}").
# At most two words so a sentence-shaped paragraph can never pass.
_KEYWORD_ONLY_LINE_RE = re.compile(
    r"[a-z][a-z'\-]*(?: [a-z][a-z'\-]*)?"
    r"(?:[ —-]*(?:\{[^}]+\}|\d+))*")


def parse_x_creature_tutor(oracle: str) -> Optional[Dict]:
    """Parse the Green Sun's Zenith shape from a SPELL's own resolution
    text: an X-bound search for a creature card with mana value X or less,
    put onto the battlefield (GSZ, Chord of Calling, Finale of Devastation,
    Nature's Rhythm, Vision Quest — the X-cost creature tutors in Modern).

    Parsed once at DB load into `CardTemplate.x_creature_tutor_data`
    (oracle-runtime-parse ratchet: consumers read the typed field).  The
    AI's valuation gate and the cast path's X picker both key off this
    field, so it must be set ONLY for shapes the resolver actually
    delivers — an unexecutable rider refuses the whole card.

    Returns ``None`` for non-matching / refused cards, else the same
    structured search spec `parse_activation_tutor` produces, plus the
    spell-side riders::

        {'dest': 'battlefield' | 'hand',
         'types', 'not_types', 'supertypes', 'subtypes', 'colors',
         'max_mv': None, 'mv_bound_is_x': True, 'tapped': bool,
         'also_graveyard': bool,             # "library and/or graveyard"
         'self_shuffle_into_library': bool,  # the card shuffles itself back
         'enters_with_x_counters': bool,     # +X +1/+1 counters on entry
         'haste_at_x': int | None,           # delivered creature gains haste
         'team_pump_haste_at_x': int | None} # team +X/+X and haste

    Refused (field stays ``None``, so no valuation credit is given):

      * the clause sits on an ACTIVATED ability — the activation path
        (`ActivationEffectKind.TUTOR_*`) owns those, and a permanent
        spell's own resolution must not tutor;
      * the clause sits on a TRIGGERED ability — its condition (e.g.
        "if you cast it") depends on facts outside this resolver;
      * any sentence in the ability, or any other ability paragraph,
        outside the known rider / keyword vocabulary.

    The "mana value X or less" phrase only occurs on cards whose printed
    cost carries {X} (CR 107.3), so no separate mana-cost gate is needed;
    `x_cost_data` still carries the multiplier/min_x for the cost side.
    """
    # Cheap reject first — stripping reminder text only removes characters,
    # so a phrase absent from the raw text is absent from the stripped text.
    if 'mana value x or less' not in (oracle or '').lower():
        return None
    text = strip_reminder_text(oracle)

    paragraphs = split_abilities(text)
    tutor_para = None
    for para in paragraphs:
        low = para.lower()
        if 'search your library' in low and 'mana value x or less' in low:
            if tutor_para is not None:
                return None  # two search abilities — not this shape
            tutor_para = para
        elif not _KEYWORD_ONLY_LINE_RE.fullmatch(low.strip().rstrip('.')):
            # A paragraph with a resolution effect of its own; claiming
            # the card here would drop it.
            return None
    if tutor_para is None:
        return None

    low = tutor_para.lower().strip()
    if ':' in low:
        return None       # activated ability — owned by the activation path
    if re.match(r'^(when|whenever|at)\b', low):
        return None       # triggered ability — its condition is not ours

    spec = None
    riders: Dict = {'also_graveyard': False,
                    'self_shuffle_into_library': False,
                    'enters_with_x_counters': False,
                    'haste_at_x': None,
                    'team_pump_haste_at_x': None}
    for sentence in split_clauses(low):
        m = _X_TUTOR_SEARCH_RE.fullmatch(sentence)
        if m is not None:
            if spec is not None:
                return None  # two search sentences — not this shape
            spec = _x_tutor_spec_from_match(m)
            if spec is None:
                return None
            riders['also_graveyard'] = bool(m.group('gy'))
            riders['enters_with_x_counters'] = bool(m.group('counters'))
            continue
        for key, pattern in _X_TUTOR_RIDERS:
            rm = pattern.fullmatch(sentence)
            if rm is None:
                continue
            if key.endswith('_at_x'):
                riders[key] = int(rm.group('n'))
            elif key != 'search_shuffle_note':
                riders[key] = True
            break
        else:
            return None  # an unknown sentence — refuse, never approximate

    if spec is None:
        return None
    spec.update(riders)
    return spec


def _x_tutor_spec_from_match(m) -> Optional[Dict]:
    """Structured search constraint from an `_X_TUTOR_SEARCH_RE` match, or
    ``None`` when the qualifier vocabulary cannot hold it exactly."""
    types: list = []
    not_types: list = []
    supertypes: list = []
    subtypes: list = []
    colors: list = []
    for word in (m.group('quals') or '').split():
        if word in _TUTOR_COLOR_WORDS:
            colors.append(_TUTOR_COLOR_WORDS[word])
        elif word in _TUTOR_SUPERTYPE_WORDS:
            supertypes.append(word)
        elif word in _TUTOR_TYPE_WORDS:
            types.append(word)
        elif word.startswith('non'):
            stripped = word[3:].lstrip('-')
            if stripped in _TUTOR_TYPE_WORDS:
                not_types.append(stripped)
            else:
                return None  # "non-Human" — a subtype negation the spec
                             # cannot hold; refuse rather than approximate
        elif word in ('or', 'and'):
            return None      # union constraint — refuse, never approximate
        else:
            subtypes.append(word)

    dest = 'battlefield' if m.group('dest').startswith('onto') else 'hand'
    if dest == 'battlefield' and 'creature' not in types:
        # Same refusal as the activation parser: a non-creature permanent
        # put onto the battlefield carries entry statics (land ETB /
        # enters-tapped rules) this path does not wire through.
        return None
    return {
        'dest': dest,
        'types': types,
        'not_types': not_types,
        'supertypes': supertypes,
        'subtypes': subtypes,
        'colors': colors,
        'max_mv': None,
        'mv_bound_is_x': True,
        'tapped': bool(m.group('tapped')),
    }


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


def parse_can_target_player(oracle: str) -> bool:
    """Return True when the spell can legally target a player (CR 601.2c).

    Oracle phrases that permit player targeting:
      - "any target"        (Lightning Bolt, Grapeshot, Lava Dart)
      - "target player"     (Thoughtseize, Geistflame, Thought Erasure)
      - "target opponent"   (Thoughtseize, Inquisition of Kozilek)

    "target creature or planeswalker" (Galvanic Discharge, Unholy Heat)
    is intentionally excluded — that wording cannot target players.
    The function is a pure oracle-text read at load time; no substring
    matching inside comments or reminder text is needed since the
    canonical phrasing is always identical across printings.
    """
    low = (oracle or '').lower()
    return (
        'any target' in low
        or 'target player' in low
        or 'target opponent' in low
    )


def parse_can_target_planeswalker(oracle: str) -> bool:
    """Return True when the spell can legally target a planeswalker.

    Oracle phrases that permit planeswalker targeting:
      - "any target"        (Lightning Bolt — hits players, creatures, PWs)
      - "planeswalker"      (Hero's Downfall, Dreadbore, creature-or-PW burns)

    A spell with "any target" reaches all three legal target categories
    (player, creature, planeswalker) so the first check alone suffices;
    the second catches the "target creature or planeswalker" form.
    Reminder text like "planeswalker (It must be attacking...)" will
    not match the "planeswalker" substring here as a false positive
    because that reminder text appears on creatures/enchantments, not
    on spells that have the "target ... planeswalker" targeting pattern.
    """
    low = (oracle or '').lower()
    return 'any target' in low or 'planeswalker' in low


def parse_has_attack_trigger(oracle: str, name: str = "") -> bool:
    """Return True when the card has an on-attack triggered ability that
    belongs to the card itself (CR 603.2).

    Two oracle phrasings identify self-attack triggers:
      - "Whenever this creature attacks" — the generic self-referential form
        used by most printed cards (Goblin Rabblemaster, Hero of Bladehold,
        Brutal Cathar, etc.)
      - "Whenever [Card Name] attacks" — the self-named form used by legendary
        creatures (Ragavan, Nimble Pilferer; Satoru Umezawa; etc.)

    Deliberately excluded: "Whenever a creature attacks" / "Whenever a creature
    attacks you" / "Whenever you attack" — those are triggers on *other* cards
    (blockers, defensive permanents) that fire when any attacker attacks, not
    just when this card does. The 'this creature' / self-name anchor is the
    discriminator between the two classes (same logic the callers in
    ev_player.py / ev_evaluator.py / oracle_resolver.py use).

    `name` is the card's printed name (first face for DFCs, split before
    ' // '). Passing it enables the self-named form to be detected at load
    time rather than requiring a runtime string-format check.
    """
    low = (oracle or '').lower()
    # Modern templating writes the combined form "Whenever this creature enters
    # or attacks, …" (Grave Titan, Primeval Titan, Archon of Cruelty, the
    # Overlord cycle — 88 Modern cards). That IS an attack trigger; matching
    # only the bare "attacks" phrasing left every one of them with
    # has_attack_trigger == False, which silently disabled the flag-gated
    # attack-time dispatches (land search, energy) and under-valued all 88 in
    # `creature_threat_value`'s virtual-power credit.
    suffixes = ('attacks', 'enters or attacks')
    # The self-referential subject is not always "this creature": the DB also
    # uses "this permanent" (the Overlord enchantment-creature cycle), "this
    # Vehicle", "this land" (creature-lands), "this token" and "this
    # Spacecraft" — 50 cards beyond the creature phrasing. In modern
    # templating "this <noun>" is ALWAYS a self-reference, so a single-word
    # noun is a safe anchor; crucially it still excludes the watcher forms
    # ("whenever YOUR COMMANDER enters or attacks", "whenever A CREATURE YOU
    # CONTROL attacks"), which belong to a different trigger class.
    if re.search(r'whenever this \w+ (?:enters or )?attacks\b', low):
        return True
    if name:
        # Full name minus alternate-face suffix (DFCs use "Front // Back").
        cname = name.lower().split(' //')[0].strip()
        # Legendary creatures with a title ("Ragavan, Nimble Pilferer") refer
        # to themselves in oracle text by just the personal name before the
        # comma ("Whenever Ragavan attacks"). Check both forms.
        short = cname.split(',')[0].strip()
        for anchor in (cname, short):
            if not anchor:
                continue
            if any(f'whenever {anchor} {s}' in low for s in suffixes):
                return True
    return False


_ANY_COLOR_UNIT = ['W', 'U', 'B', 'R', 'G']


# ── Activated abilities (CR 602): "[Cost]: [Effect]" ──────────────────
# Cost verbs this tranche cannot charge. Parsed and RECORDED (never dropped)
# so the ability is visible-but-refused; a later tranche adds payers without
# re-parsing the pool.
# ── Counter cost items (tranche 4) ───────────────────────────────────
# Permanent-type words a self-referential counter cost may name ("... on
# THIS creature", "... from THIS artifact"). A cost that names another
# permanent ("from a creature you control"), a group ("from among
# creatures you control"), or the card by name is a different cost shape
# and stays on the unpayable path.
_COUNTER_SELF_WORDS = (r'creature|artifact|enchantment|land|permanent'
                       r'|planeswalker|vehicle|token|battle|equipment')
# ANCHORED (fullmatch) like every other cost item: an unbounded count
# ("any number of", "all", "one or more") or a trailing rider must fail
# the match rather than be approximated by a fixed amount.
_PUT_COUNTER_COST_RE = re.compile(
    r'put (?P<n>a|an|one|two|three|\d+) (?P<kind>[^ ]+) counters? on this '
    r'(?:' + _COUNTER_SELF_WORDS + r')')
_REMOVE_COUNTER_COST_RE = re.compile(
    r'remove (?P<n>a|an|one|two|three|\d+) (?P<kind>[^ ]+) counters? from '
    r'this (?:' + _COUNTER_SELF_WORDS + r')')
# A P/T counter kind the instance model can actually hold. `plus_counters`
# / `minus_counters` are symmetric (+N/+N, -N/-N) fields, so a -0/-1 or a
# +2/+2 counter has no representation — refused, never mapped onto the
# nearest available kind.
_PT_COUNTER_KIND_RE = re.compile(r'^[+\-]\d+/[+\-]\d+$')
_NAMED_COUNTER_KIND_RE = re.compile(r"^[a-z][a-z\-']*$")


def _canonical_counter_kind(word: str) -> Optional[str]:
    """Normalise a parsed counter-kind word, or None when the instance
    model cannot represent that kind of counter."""
    from .cards import COUNTER_KIND_MINUS, COUNTER_KIND_PLUS
    if word in (COUNTER_KIND_PLUS, COUNTER_KIND_MINUS):
        return word
    if _PT_COUNTER_KIND_RE.match(word):
        return None  # a P/T counter with no field to live on
    if _NAMED_COUNTER_KIND_RE.match(word) and word != 'counter':
        return word
    return None


_UNPAYABLE_COST_PATTERNS = (
    # Tranche 3 graduated single-victim sacrifices and plain discard-N to
    # structured fields; tranche 4 graduated self-referential counter costs;
    # what reaches these patterns now is the residue —
    # multi-victim / or-typed / subtype-restricted sacrifices, and
    # at-random / type-restricted / whole-hand discards.
    ('sacrifice', r'sacrifice\b'),
    ('discard', r'discard\b'),
    ('remove_counter', r'remove (a|an|one|two|\d+)[^,:]*counter'),
    ('put_counter', r'put (a|an|one|two|\d+)[^,:]*counter'),
    ('exile', r'exile\b'),
    ('tap_other', r'tap (an?|another|two|three|\d+)\b'),
    ('return', r'return\b'),
    ('energy', r'\{e\}'),
    ('reveal', r'reveal\b'),
)


def strip_reminder_text(oracle: str) -> str:
    """Remove parenthesised reminder text, innermost-first.

    Load-bearing for the activated-ability scan: a large fraction of
    colon-bearing lines in the pool have their ONLY colon inside keyword
    reminder text — "Equip {3} ({3}: Attach ...)", "Cycling {2} ({2}, Discard
    this card: Draw a card.)". Without stripping, those parse as real
    abilities. The engine has no reminder stripper of its own; the repo's only
    one lives in `ai/`, which `engine/` may not import.
    """
    text = oracle or ''
    while True:
        stripped = re.sub(r'\([^()]*\)', '', text)
        if stripped == text:
            return text
        text = stripped


def split_activation_riders(effect_text: str):
    """Split "Activate only ..." sentences off an effect clause.

    MUST run before effect classification — otherwise the classifier sees a
    trailing rider sentence and fails to match legitimate lines.

    Only two riders map to booleans. Everything else is returned verbatim so
    `can_activate` can REFUSE on it. In particular "Activate only once."
    (once per GAME) must not be read as once-each-turn, which would silently
    grant extra activations.

    Returns ``(body, restrictions)``.
    """
    text = effect_text or ''
    restrictions = []
    sorcery_only = False
    once_each_turn = False

    def _take(match):
        nonlocal sorcery_only, once_each_turn
        sentence = match.group(0).strip()
        low = sentence.lower()
        if low == 'activate only as a sorcery.':
            sorcery_only = True
        elif low == 'activate only once each turn.':
            once_each_turn = True
        else:
            restrictions.append(sentence)
        return ''

    body = re.sub(r'Activate only[^.]*\.', _take, text, flags=re.IGNORECASE)
    return body.strip(), tuple(restrictions), sorcery_only, once_each_turn


def parse_activation_cost(cost_text: str):
    """Parse the cost half of an activated ability.

    Closed grammar with an explicit escape hatch: mana runs and {T}/{Q} are
    payable; every recognised non-mana verb is appended to `unpayable`.
    Returns ``None`` for loyalty brackets (planeswalker abilities are owned
    elsewhere) and for empty costs.
    """
    from .cards import ActivationCost
    from .mana import ManaCost
    raw = (cost_text or '').strip()
    if not raw:
        return None
    # Loyalty abilities ([+1], [-3], [0]) belong to the planeswalker manager.
    if re.match(r'^\[[+\-−]?\d*\]', raw):
        return None

    mana = ManaCost()
    tap_self = False
    untap_self = False
    life = 0
    sacrifice_self = False
    exile_self = False
    sacrifice_type = None
    sacrifice_another = False
    discard_cards = 0
    put_counter_kind = None
    put_counter_amount = 0
    remove_counter_kind = None
    remove_counter_amount = 0
    x_count = 0
    unpayable = []
    for part in raw.split(','):
        piece = part.strip()
        if not piece:
            continue
        low = piece.lower()
        m_life = re.fullmatch(r'pay (\d+) life', low)
        if m_life:
            life += int(m_life.group(1))
            continue
        if re.match(r'sacrifice this\b', low):
            sacrifice_self = True
            continue
        # Sibling cost item, different destination zone. ANCHORED on the
        # `this` self-reference -- the same discriminator
        # `parse_sacrifice_mana_units` uses -- so "Exile a creature card
        # from your graveyard" and "Exile three cards from your
        # graveyard" (cards paid from a DIFFERENT zone, a different
        # mechanic) fall through to the unpayable patterns below.
        if re.fullmatch(r'exile this [a-z]+', low):
            exile_self = True
            continue
        # Tranche 3: single-victim typed sacrifice. FULLMATCH on a CLOSED
        # type set — "Sacrifice an artifact or land" (union), "Sacrifice
        # two creatures" (multi-victim) and "Sacrifice a Goblin" (subtype)
        # all fall through to the unpayable patterns below, refused rather
        # than approximated. A SECOND sacrifice item in the same cost is a
        # choice shape the schema cannot hold — also refused.
        m_sac = re.fullmatch(
            r'sacrifice (a|an|another) '
            r'(creature|artifact|enchantment|land|permanent)', low)
        if m_sac and sacrifice_type is None:
            sacrifice_type = m_sac.group(2)
            sacrifice_another = (m_sac.group(1) == 'another')
            continue
        # Tranche 3: plain untyped discard-N. FULLMATCH — "at random",
        # type-restricted ("a creature card") and "your hand" forms stay
        # on the unpayable path.
        m_disc = re.fullmatch(r'discard (a|one|two|three|\d+) cards?', low)
        if m_disc:
            tok = m_disc.group(1)
            discard_cards += (int(tok) if tok.isdigit()
                              else _NUM_WORDS.get(tok, 0))
            continue
        # Tranche 4: counter costs on the SOURCE. Parsed into a kind + an
        # amount; a SECOND counter item of the same direction is a choice
        # shape the schema cannot hold, so it falls through to unpayable.
        m_put = _PUT_COUNTER_COST_RE.fullmatch(low)
        if m_put and put_counter_kind is None:
            kind = _canonical_counter_kind(m_put.group('kind'))
            if kind is not None:
                tok = m_put.group('n')
                put_counter_kind = kind
                put_counter_amount = (
                    int(tok) if tok.isdigit()
                    else (1 if tok in ('a', 'an', 'one')
                          else _NUM_WORDS.get(tok, 0)))
                continue
        m_rem = _REMOVE_COUNTER_COST_RE.fullmatch(low)
        if m_rem and remove_counter_kind is None:
            kind = _canonical_counter_kind(m_rem.group('kind'))
            if kind is not None:
                tok = m_rem.group('n')
                remove_counter_kind = kind
                remove_counter_amount = (
                    int(tok) if tok.isdigit()
                    else (1 if tok in ('a', 'an', 'one')
                          else _NUM_WORDS.get(tok, 0)))
                continue
        if re.fullmatch(r'(\{[wubrgcxs0-9/]+\}\s*)+', low):
            # {X} pips are a COUNT, not fixed mana: X is chosen at
            # activation time (CR 601.2b). Whether the count is chargeable
            # is `can_activate`'s question (only effects that bind X).
            x_count += low.count('{x}')
            mana = _add_mana_symbols(mana, low)
            continue
        if low == '{t}':
            tap_self = True
            continue
        if low == '{q}':
            untap_self = True
            continue
        matched = False
        for name, pattern in _UNPAYABLE_COST_PATTERNS:
            if re.search(pattern, low):
                unpayable.append(name)
                matched = True
                break
        if not matched:
            unpayable.append('unrecognised')
    return ActivationCost(mana=mana, tap_self=tap_self,
                          untap_self=untap_self,
                          life=life, sacrifice_self=sacrifice_self,
                          exile_self=exile_self,
                          sacrifice_type=sacrifice_type,
                          sacrifice_another=sacrifice_another,
                          discard_cards=discard_cards,
                          put_counter_kind=put_counter_kind,
                          put_counter_amount=put_counter_amount,
                          remove_counter_kind=remove_counter_kind,
                          remove_counter_amount=remove_counter_amount,
                          x_count=x_count,
                          unpayable=tuple(unpayable))


def _add_mana_symbols(cost, text):
    """Fold a run of mana symbols into a ManaCost."""
    attr = {'w': 'white', 'u': 'blue', 'b': 'black', 'r': 'red', 'g': 'green'}
    for sym in re.findall(r'\{([wubrgcxs0-9/]+)\}', text):
        if '/' in sym:
            # Hybrid pip: one generic — the caster picks which colour (or
            # which half) pays it, so any mana satisfies it. Same
            # convention as `_parse_mana_symbols_to_cost` on the spell
            # side; charging it (rather than dropping it, the pre-tutor
            # behaviour) is what keeps hybrid activation costs honest.
            cost.generic += 1
        elif sym.isdigit():
            cost.generic += int(sym)
        elif sym in attr:
            setattr(cost, attr[sym], getattr(cost, attr[sym]) + 1)
        elif sym == 'c':
            cost.colorless += 1
        # {X} is COUNTED by the caller (`x_count`), never folded into the
        # fixed mana here; {S} is not chargeable in this tranche and
        # surfaces via the caller's `unpayable` path only when it appears
        # outside a mana run.
    return cost


def classify_activation_effect(effect_text: str):
    """Classify an effect clause. Returns (kind, amount, power_mod, tough_mod).

    Regexes are ANCHORED to the FULL sentence, not searched. That is not
    stylistic: a loose draw pattern matches "Draw a card, then exile a card
    from your hand face down." — a different effect whose rider would be
    silently dropped if it executed as a plain draw.
    """
    from .cards import ActivationEffectKind as K
    text = (effect_text or '').strip()
    low = text.lower().rstrip('.')

    m = re.fullmatch(r'(?:this |it )?(?:\w+ )?deals? (\d+) damage to any target', low)
    if m:
        return K.DAMAGE_ANY_TARGET, int(m.group(1)), 0, 0

    m = re.fullmatch(r'draw (a|one|two|three|\d+) cards?', low)
    if m:
        tok = m.group(1)
        n = 1 if tok in ('a', 'one') else (int(tok) if tok.isdigit()
                                           else _NUM_WORDS.get(tok, 0))
        if n > 0:
            return K.DRAW_N, n, 0, 0

    m = re.fullmatch(
        r'(?:this creature|this permanent|it) gets \+(\d+)/\+(\d+) until end of turn',
        low)
    if m:
        return K.PUMP_SELF_UEOT, 0, int(m.group(1)), int(m.group(2))

    if 'becomes a' in low and 'creature until end of turn' in low:
        return K.ANIMATE_SELF_UEOT, 0, 0, 0

    # Combat-enabler grant (Hanweir Battlements-shape). ANCHORED to the
    # exact single-target sentence: composite grants ("gets +2/+0 and
    # gains vigilance and haste") and non-targeted grants ("creatures you
    # control gain haste") are DIFFERENT effects and stay UNCLASSIFIED
    # rather than executing as a bare haste grant with riders dropped.
    if re.fullmatch(r'target creature gains haste until end of turn', low):
        return K.GRANT_HASTE_TARGET, 0, 0, 0

    # Graveyard exile (single full-sentence). The structured shape is
    # re-derived by `parse_activated_abilities` via
    # `parse_activation_graveyard_exile` and stored on the ability
    # (`graveyard_exile_data`) — both parses happen once, at DB load.
    if parse_activation_graveyard_exile(low) is not None:
        return K.EXILE_FROM_GRAVEYARD, 0, 0, 0

    # Untap (single full-sentence). The structured target constraint is
    # re-derived by `parse_activated_abilities` via `parse_activation_untap`
    # and stored on the ability's `target_requirements` — both parses happen
    # once, at DB load time.
    if parse_activation_untap(low) is not None:
        return K.UNTAP_TARGET_PERMANENT, 0, 0, 0

    # Activated tutor (single full-sentence search). The structured
    # constraint is re-derived by `parse_activated_abilities` via
    # `parse_activation_tutor` and stored on the ability (`tutor_data`) —
    # both parses happen once, at DB load time.
    tutor = parse_activation_tutor(low)
    if tutor is not None:
        if tutor['dest'] == 'battlefield':
            return K.TUTOR_CREATURE_TO_BATTLEFIELD, 0, 0, 0
        return K.TUTOR_TO_HAND, 0, 0, 0

    return K.UNCLASSIFIED, 0, 0, 0


# ── Activated tutor effects (TUTOR_* kinds) ──────────────────────────
# Closed constraint vocabulary. A qualifier word outside these sets is
# treated as a SUBTYPE word (Goblin, Sliver, Equipment, Forest, ...);
# connector words ('or', 'and') mean a union constraint the schema
# cannot hold — the whole line is refused rather than approximated.
_TUTOR_TYPE_WORDS = frozenset({
    'creature', 'artifact', 'enchantment', 'land', 'planeswalker',
    'instant', 'sorcery', 'battle', 'permanent'})
_TUTOR_SUPERTYPE_WORDS = frozenset({'basic', 'legendary', 'snow'})
_TUTOR_COLOR_WORDS = {'white': 'W', 'blue': 'U', 'black': 'B',
                      'red': 'R', 'green': 'G'}

# ANCHORED to the full sentence (fullmatch) — the same discipline every
# other activation classifier follows: a trailing rider sentence or an
# unrecognised "with ..." clause fails the match and the line stays
# UNCLASSIFIED instead of executing with the rider silently dropped.
_TUTOR_SENTENCE_RE = re.compile(
    r'search your library for an? (?P<quals>(?:[a-z][a-z\-\']* )*?)card'
    r'(?: with mana value (?P<mv>x|\d+) or less)?'
    r'(?:,| and)'
    r'(?: reveal it(?:,| and)?)?'
    r' put (?:it|that card) '
    r'(?P<dest>onto the battlefield(?P<tapped> tapped)?|into your hand)'
    r',? then shuffle(?: your library)?')


def parse_activation_tutor(effect_text: str) -> Optional[Dict]:
    """Parse an activated-tutor effect sentence into its structured search
    constraint, or ``None`` when the sentence is not one of the two
    executable shapes (battlefield destination is limited to CREATURE
    searches — see ``ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD``).

    Returns::

        {'dest': 'battlefield' | 'hand',
         'types': [...], 'not_types': [...],   # card-type words
         'supertypes': [...], 'subtypes': [...],
         'colors': ['G', ...],                 # printed-colour constraint
         'max_mv': int | None,                 # fixed "mana value N or less"
         'mv_bound_is_x': bool,                # "mana value X or less"
         'tapped': bool}                       # battlefield entry rider

    Called once at DB load (via ``parse_activated_abilities``), never at
    runtime — the oracle-runtime-parse ratchet stays at 0.
    """
    low = (effect_text or '').strip().lower().rstrip('.')
    m = _TUTOR_SENTENCE_RE.fullmatch(low)
    if m is None:
        return None

    types: list = []
    not_types: list = []
    supertypes: list = []
    subtypes: list = []
    colors: list = []
    for word in (m.group('quals') or '').split():
        if word in _TUTOR_COLOR_WORDS:
            colors.append(_TUTOR_COLOR_WORDS[word])
        elif word in _TUTOR_SUPERTYPE_WORDS:
            supertypes.append(word)
        elif word in _TUTOR_TYPE_WORDS:
            types.append(word)
        elif word.startswith('non') and word[3:] in _TUTOR_TYPE_WORDS:
            not_types.append(word[3:])
        elif word in ('or', 'and'):
            return None  # union constraint — refuse, never approximate
        else:
            subtypes.append(word)

    mv = m.group('mv')
    dest = 'battlefield' if m.group('dest').startswith('onto') else 'hand'
    if dest == 'battlefield' and 'creature' not in types:
        # A non-creature permanent put onto the battlefield carries entry
        # statics (land ETB / enters-tapped rules) this tranche does not
        # wire through the activation path — refused, counted as residue.
        return None
    return {
        'dest': dest,
        'types': types,
        'not_types': not_types,
        'supertypes': supertypes,
        'subtypes': subtypes,
        'colors': colors,
        'max_mv': int(mv) if (mv and mv != 'x') else None,
        'mv_bound_is_x': mv == 'x',
        'tapped': bool(m.group('tapped')),
    }


# ── Activated untap effects (UNTAP_TARGET_PERMANENT) ─────────────────
# Same closed-vocabulary discipline as the tutor constraint above: a
# qualifier word outside these sets is treated as a SUBTYPE word (Forest,
# Gate, Myr, ...), and any word that changes WHICH objects are untapped
# ('another', 'all', 'each', a count, a union connector) refuses the whole
# line. That refusal is load-bearing, not conservatism: reading "Untap
# ANOTHER target permanent" as a plain "untap target permanent" would let
# the source untap itself and manufacture a repeatable loop the printed
# card does not have.
_UNTAP_TYPE_WORDS = frozenset({
    'creature', 'artifact', 'enchantment', 'land', 'planeswalker',
    'permanent'})
_UNTAP_SUPERTYPE_WORDS = frozenset({'basic', 'legendary', 'snow'})
_UNTAP_REFUSE_WORDS = frozenset({
    'or', 'and', 'another', 'each', 'all', 'other', 'up', 'to', 'x',
    'that', 'with', 'not', 'named', 'attacking', 'blocking', 'tapped',
    'untapped'})

_UNTAP_SELF_RE = re.compile(r'untap this (?P<word>[a-z]+)')
_UNTAP_TARGET_RE = re.compile(
    r"untap target (?P<quals>[a-z][a-z\-' ]*?)(?P<scope> you control)?")


# ── Graveyard-exile effects (EXILE_FROM_GRAVEYARD kind) ──────────────
#
# Five executable sentence shapes, all ANCHORED with fullmatch — the same
# discipline every other activation classifier follows. A rider sentence
# ("… Draw a card.", "… If it was a creature card, …") fails the match and
# the line stays UNCLASSIFIED rather than executing with the rider
# silently dropped.
#
# Class size: 33 Modern cards carry one of these five shapes on an
# activated ability; the plain "Exile target card from a graveyard" line
# alone is on 15 of them.

# Card-type words a graveyard-exile target filter may carry. Closed set:
# anything else (a subtype, "nonland", a union) refuses the whole line
# rather than approximating the constraint.
_GY_EXILE_TYPE_WORDS = frozenset({
    'creature', 'artifact', 'enchantment', 'land', 'instant', 'sorcery',
    'planeswalker'})

# "exile [up to] [N] target [<type>] card(s) from <source>"
_GY_EXILE_CARDS_RE = re.compile(
    r'exile (?:(?P<upto>up to )?(?P<n>a|one|two|three|four|five|\d+) )?'
    r"target (?P<quals>(?:[a-z][a-z\-']* )*?)cards? "
    r'from (?P<src>a graveyard|a single graveyard|graveyards'
    r"|your graveyard|an opponent's graveyard)")

# Whole-graveyard shapes → (regex, scope).
_GY_EXILE_WHOLE_RES = (
    (re.compile(r"exile target player's graveyard"), 'target_player'),
    (re.compile(r'exile all (?:cards from all )?graveyards'), 'all'),
    (re.compile(r"exile each opponent's graveyard"), 'each_opponent'),
)

_GY_EXILE_SRC_OWNER = {
    'a graveyard': ('any', False),
    'a single graveyard': ('any', True),
    'graveyards': ('any', False),
    'your graveyard': ('you', False),
    "an opponent's graveyard": ('opponent', False),
}


def parse_activation_graveyard_exile(effect_text: str) -> Optional[Dict]:
    """Parse a graveyard-exile effect sentence into its structured shape,
    or ``None`` when the sentence is not one of the executable forms.

    Returns::

        {'scope': 'cards'|'target_player'|'all'|'each_opponent',
         'count': int,             # cards scope: how many targets
         'up_to': bool,            # cards scope: "up to N"
         'types': ['creature'],    # cards scope: type filter, [] = any card
         'owner': 'you'|'opponent'|'any',   # whose graveyard
         'single_graveyard': bool} # all targets from ONE graveyard

    Deliberately refused (left ``None``, so the line stays UNCLASSIFIED):

      * any trailing rider — "… Draw a card.", "… If it was a creature
        card, put a +1/+1 counter on this creature." Those are composite
        effects; executing only the exile half is the half-execution the
        tranche discipline exists to prevent.
      * an X-bound count ("exile up to X target cards"). X is chosen at
        activation time and this effect does not BIND it, so the engine
        cannot charge the {X} pip honestly — the same rule
        ``can_activate`` applies to every non-tutor {X}.
      * "target player exiles a card from their graveyard" — the CHOICE
        belongs to the targeted player, a decision seam this shape does
        not have.
      * a union or subtype constraint, or more than one card-type word
        (a conjunction the TargetRequirement type set cannot express).

    Called once at DB load (via ``parse_activated_abilities``), never at
    runtime — the oracle-runtime-parse ratchet stays at 0.
    """
    low = (effect_text or '').strip().lower().rstrip('.')
    if not low:
        return None

    for pattern, scope in _GY_EXILE_WHOLE_RES:
        if pattern.fullmatch(low):
            return {'scope': scope, 'count': 0, 'up_to': False,
                    'types': [], 'owner': 'any', 'single_graveyard': False}

    m = _GY_EXILE_CARDS_RE.fullmatch(low)
    if m is None:
        return None

    tok = m.group('n')
    if tok is None:
        count = 1
    elif tok.isdigit():
        count = int(tok)
    elif tok in ('a', 'one'):
        count = 1
    else:
        count = _NUM_WORDS.get(tok, 0)
    if count <= 0:
        return None

    types: list = []
    for word in (m.group('quals') or '').split():
        if word not in _GY_EXILE_TYPE_WORDS:
            return None  # subtype / "nonland" / union — refuse, don't guess
        types.append(word)
    if len(types) > 1:
        # "target artifact creature card" is a CONJUNCTION; the
        # TargetRequirement type set is a disjunction.
        return None

    owner, single = _GY_EXILE_SRC_OWNER[m.group('src')]
    return {'scope': 'cards', 'count': count,
            'up_to': bool(m.group('upto')), 'types': types,
            'owner': owner, 'single_graveyard': single}


def parse_activation_untap(effect_text: str) -> Optional[Dict]:
    """Parse an activated untap sentence into its structured shape, or
    ``None`` when the sentence is not one of the two executable forms.

    Returns::

        {'self': bool,              # True for "Untap this <permanent>"
         'types': ['land', ...],    # card-type words ('permanent' wildcard)
         'supertype': str | None,
         'subtype': str | None,     # 'forest', 'gate', ...
         'owner': 'you' | 'any'}

    Called once at DB load (via ``parse_activated_abilities``), never at
    runtime — the oracle-runtime-parse ratchet stays at 0.
    """
    low = (effect_text or '').strip().lower().rstrip('.')

    m_self = _UNTAP_SELF_RE.fullmatch(low)
    if m_self is not None:
        if m_self.group('word') not in _UNTAP_TYPE_WORDS:
            return None
        return {'self': True, 'types': [], 'supertype': None,
                'subtype': None, 'owner': 'any'}

    m = _UNTAP_TARGET_RE.fullmatch(low)
    if m is None:
        return None
    types: list = []
    supertype = None
    subtype = None
    for word in (m.group('quals') or '').split():
        if word in _UNTAP_REFUSE_WORDS or word in _NUM_WORDS or word.isdigit():
            return None
        if word in _UNTAP_TYPE_WORDS:
            types.append(word)
        elif word in _UNTAP_SUPERTYPE_WORDS:
            if supertype is not None:
                return None
            supertype = word
        else:
            if subtype is not None:
                return None
            subtype = word
    if len(types) > 1:
        # "target artifact creature" is a CONJUNCTION; TargetRequirement's
        # type set is a disjunction, so the constraint cannot be expressed.
        return None
    if not types and subtype is None:
        return None
    return {'self': False,
            'types': types or ['permanent'],
            'supertype': supertype,
            'subtype': subtype,
            'owner': 'you' if m.group('scope') else 'any'}


def parse_activated_abilities(oracle: str):
    """Parse every "[Cost]: [Effect]" line a permanent has of its own.

    Excludes, by construction: reminder text (stripped first), abilities
    GRANTED to other objects via a quoted clause, and loyalty abilities.
    Mana abilities (CR 605) are parsed but flagged `is_mana_ability` so the
    enumerator can skip them — mana is produced by the payment path.
    """
    from .cards import ActivatedAbility, ActivationEffectKind as K
    text = strip_reminder_text(oracle or '')
    if ':' not in text:
        return []

    out = []
    idx = 0
    for line in re.split(r'\n+', text):
        line = line.strip()
        if ':' not in line:
            continue
        # A colon inside a quoted grant belongs to an ability given to OTHER
        # objects ("Creatures you control have \"{T}: Add {C}\"") — a
        # different mechanic, not this permanent's own activation.
        if '"' in line or '\u201c' in line:
            continue
        cost_text, _, effect_text = line.partition(':')
        cost = parse_activation_cost(cost_text)
        if cost is None:
            continue
        body, restrictions, sorcery_only, once_turn = \
            split_activation_riders(effect_text)
        kind, amount, p_mod, t_mod = classify_activation_effect(body)
        is_mana = bool(re.match(r'^add\b', body.strip().lower()))
        # CR 601.2c: a targeted effect kind carries its target requirement
        # so ActivationManager can refuse the activation when no legal
        # target exists (rule 15) — populated here, at parse time, from the
        # classified kind rather than re-scanning oracle text at runtime.
        targets_required = 0
        target_requirements: list = []
        if kind is K.GRANT_HASTE_TARGET:
            from .target_solver import TargetRequirement
            targets_required = 1
            target_requirements = [TargetRequirement(
                zone="battlefield", types=frozenset({"creature"}),
                raw_phrase="target creature")]
        elif kind is K.UNTAP_TARGET_PERMANENT:
            # The SELF shape ("Untap this creature") is not targeted at all
            # (CR 115.1) — it stays at targets_required=0, and that is the
            # discriminator the resolver and the no-free-repeatable rule read.
            spec = parse_activation_untap(body)
            if spec is not None and not spec['self']:
                from .target_solver import TargetRequirement
                targets_required = 1
                target_requirements = [TargetRequirement(
                    zone="battlefield",
                    types=frozenset(spec['types']),
                    supertype=spec['supertype'],
                    subtype=spec['subtype'],
                    owner_scope=spec['owner'],
                    raw_phrase=body.strip().lower())]
        elif kind is K.EXILE_FROM_GRAVEYARD:
            # Only the CARD-targeting scope declares targets. The
            # whole-graveyard scopes name a player (or every player) and
            # carry no card target at all, which is why the scope — not
            # the effect kind — is the discriminator the resolver reads.
            spec = parse_activation_graveyard_exile(body)
            if spec is not None and spec['scope'] == 'cards':
                from .target_solver import TargetRequirement
                targets_required = spec['count']
                target_requirements = [TargetRequirement(
                    zone="graveyard",
                    types=frozenset(spec['types'] or {"card"}),
                    owner_scope=spec['owner'],
                    is_optional=spec['up_to'],
                    count_min=0 if spec['up_to'] else spec['count'],
                    count_max=spec['count'],
                    raw_phrase=body.strip().lower())]
        # TUTOR_* kinds carry their structured search constraint on the
        # ability — parsed here, at load time, never re-derived at runtime.
        tutor_data = None
        if kind in (K.TUTOR_CREATURE_TO_BATTLEFIELD, K.TUTOR_TO_HAND):
            tutor_data = parse_activation_tutor(body)
        # EXILE_FROM_GRAVEYARD carries its structured shape the same way.
        graveyard_exile_data = None
        if kind is K.EXILE_FROM_GRAVEYARD:
            graveyard_exile_data = parse_activation_graveyard_exile(body)
        out.append(ActivatedAbility(
            index=idx, cost=cost, effect_text=body.strip(),
            effect_kind=kind, amount=amount,
            power_mod=p_mod, toughness_mod=t_mod,
            targets_required=targets_required,
            target_requirements=target_requirements,
            sorcery_speed_only=sorcery_only, once_each_turn=once_turn,
            restrictions=restrictions, is_mana_ability=is_mana,
            tutor_data=tutor_data,
            graveyard_exile_data=graveyard_exile_data,
        ))
        idx += 1
    return out



def parse_aura_enchant_restriction(oracle: str) -> Optional[str]:
    """Parse an Aura's "Enchant <quality>" ability (CR 303.4).

    Returns the quality lowercased ('land', 'forest', 'creature', 'creature
    you control', …) or ``None`` when the card has no Enchant ability. The
    quality is what makes a host legal; 786 Modern cards are Auras, so this is
    the shared entry point for attachment generally, not just the mana slice.
    """
    m = re.search(r'^enchant ([a-z][a-z \']*)$',
                  (oracle or '').lower(), re.MULTILINE)
    if m is None:
        return None
    return m.group(1).strip()


def parse_aura_mana_units(oracle: str) -> Optional[List[List[str]]]:
    """Parse "Whenever enchanted <X> is tapped for mana, … adds an additional
    <mana>" into the mana units the Aura GRANTS to its host.

    12 Modern Auras carry this (Utopia Sprawl, Fertile Ground, Wild Growth,
    Overgrowth, …). Returned in the same unit shape as ``template.mana_units``
    so the host land's unit list can simply be extended.

    "of the chosen color" is treated as any-colour: the colour is chosen as the
    Aura enters and is not modelled per-instance, but the COUNT — which is what
    mana capacity is measured in — is exact either way.
    """
    low = (oracle or '').lower()
    m = re.search(
        r'whenever enchanted [a-z]+ is tapped for mana[^.]*?'
        r'adds?\s+(?:an additional\s+)?([^.]+)', low)
    if m is None:
        return None
    effect = m.group(1).strip()

    symbols = re.findall(r'\{([wubrgc])\}', effect)
    if symbols:
        return [[s.upper()] for s in symbols]

    # "<N> mana of any color" / "of the chosen color" / "in any combination".
    m2 = re.search(r'(\w+)\s+mana\s+(?:of|in)\b', effect)
    if m2:
        tok = m2.group(1)
        try:
            count = int(tok)
        except ValueError:
            count = _NUM_WORDS.get(tok, 0)
        if count > 0:
            return [list(_ANY_COLOR_UNIT) for _ in range(count)]
    return None


def parse_aura_mana_color_is_chosen(oracle: str) -> bool:
    """True when a mana Aura's granted mana is "of the chosen color".

    Two Modern Auras word it this way (Utopia Sprawl, Shimmerwilds Growth);
    both pair it with "As this Aura enters, choose a color." The colour is a
    DECISION, so the engine records the option set in `aura_mana_units` and
    routes the pick through the `choose_mana_color` callback, narrowing the
    granted unit to the chosen colour at attachment time.

    Distinguished from "one mana of any color" (6 Auras), where the colour is
    genuinely free at payment time and the full option set is correct.
    """
    low = (oracle or '').lower()
    return bool(re.search(
        r'whenever enchanted [a-z]+ is tapped for mana[^.]*?'
        r'adds?\s+(?:an additional\s+)?[^.]*?of the chosen colou?r', low))


def parse_tap_for_mana_trigger(oracle: str):
    """Parse "Whenever you tap a <filter> for mana, add <mana>" (CR 605.1b).

    12 Modern cards carry this shape and NONE of it was modelled, so the whole
    class of controller-scoped mana accelerants — Leyline of Abundance, Nissa
    Who Shakes the World, Crypt Ghast, Nirkana Revenant, Zendikar Resurgent,
    Mirari's Wake, Nikya of the Old Ways, Vorinclex, Kinnan, Roxanne,
    Badgermole Cub, Groundchuck & Dirtbag — sat on the battlefield producing
    nothing.

    Returns a `TapForManaTrigger`, or ``None`` when the oracle carries no such
    trigger.

    Deliberately NOT matched (each below the ~10-card class threshold, and
    each needing engine surface this class does not have):
      * "whenever A PLAYER taps …" (5 cards) and "whenever a <X> IS TAPPED for
        mana, its controller adds …" (4 cards) — the mana can land in the
        OPPONENT's pool, which is a different production path.
      * "whenever AN OPPONENT taps …" (3 cards) — same reason.
      * "whenever you tap THIS <thing> for mana, <non-mana effect>" (2 cards,
        Zhur-Taa Druid / Forbidden Orchard) — same trigger event, but the
        rider is damage or a token, not mana. The `a|an` article anchor and
        the required `add` are what exclude them.
    """
    from .cards import TapForManaTrigger

    low = (oracle or '').lower()
    # Anchor: controller-scoped ("you tap"), an indefinite article (so "this
    # creature" self-references cannot match), and an `add` rider.
    m = re.search(
        r'whenever you tap an? ([a-z][a-z ]*?) for mana,\s*'
        r'(?:you )?add\s+([^.\n]+)', low)
    if m is None:
        return None
    watch = m.group(1).strip()
    effect = m.group(2).strip()

    # Rider form 1: "one mana of any type that <permanent> produced" — the
    # granted mana mirrors whatever the tapped source itself offers, so it is
    # resolved against the source rather than fixed here.
    if re.search(r'of any type that .* produced', effect):
        return TapForManaTrigger(watch=watch, units=(), mirror_source=True)

    # Rider form 2: an explicit run of mana symbols — {G}, {B}{B}, …
    symbols = re.findall(r'\{([wubrgc])\}', effect)
    if symbols:
        return TapForManaTrigger(
            watch=watch, units=tuple((s.upper(),) for s in symbols),
            mirror_source=False)

    # Rider form 3: "<N> mana of any color".
    m2 = re.search(r'(\w+)\s+mana\s+of\s+any', effect)
    if m2:
        tok = m2.group(1)
        count = int(tok) if tok.isdigit() else _NUM_WORDS.get(tok, 0)
        if count > 0:
            return TapForManaTrigger(
                watch=watch,
                units=tuple(tuple(_ANY_COLOR_UNIT) for _ in range(count)),
                mirror_source=False)
    return None


def parse_sacrifice_mana_units(oracle: str) -> Optional[List[List[str]]]:
    """Parse a "Sacrifice this <thing>: Add <mana>" ability into mana units.

    207 Modern cards create or carry this one-shot mana ability — Eldrazi
    Spawn/Scion ("Add {C}"), Treasure ("Add one mana of any color"), and
    Lotus-style multi-mana artifacts. Nothing in the engine recognised it, so
    every such permanent contributed zero mana; for ramp shells that turns a
    whole class of accelerant into dead bodies.

    Returns a list of mana UNITS in the same shape as ``template.mana_units``
    (each unit is the list of colours that unit could produce), or ``None``
    when the oracle has no such ability.

    Deliberately excluded:
      * ``{T}: Add …`` — a plain tap ability, already modelled as a normal
        mana source. Claiming it here would double-count the permanent.
      * ``Sacrifice a creature: Add …`` / ``Sacrifice another …`` — a cost
        paid with OTHER permanents, not this permanent converting itself into
        mana. Different mechanic; the ``this`` anchor is the discriminator
        (same discriminator the attack-trigger parsers use).
      * ``Sacrifice this creature: <non-mana effect>`` — no ``Add``.
    """
    low = (oracle or '').lower()
    # Anchor on "sacrifice this <noun>" so other-permanent sacrifice costs and
    # tap abilities cannot match. The effect must begin with "add".
    m = re.search(
        r'sacrifice this [a-z]+[^:.]{0,40}:\s*add\s+([^.\n"]+)', low)
    if m is None:
        return None
    effect = m.group(1).strip()

    # Form 1: an explicit run of mana symbols — {C}, {C}{C}, {B}{B}, {W}{U}…
    symbols = re.findall(r'\{([wubrgc])\}', effect)
    if symbols:
        return [[s.upper()] for s in symbols]

    # Form 2: "<N> mana of any color" / "<N> mana of any one color".
    m2 = re.search(r'(\w+)\s+mana\s+of\s+any', effect)
    if m2:
        tok = m2.group(1)
        try:
            count = int(tok)
        except ValueError:
            count = _NUM_WORDS.get(tok, 0)
        if count > 0:
            # "of any ONE color" additionally constrains every unit to the
            # same colour; that constraint is not modelled here, so the units
            # are independently any-colour. The count — which is what mana
            # capacity is measured in — is exact either way.
            return [list(_ANY_COLOR_UNIT) for _ in range(count)]

    return None


def parse_has_combat_damage_trigger(oracle: str, name: str = "") -> bool:
    """Return True when the card has an on-combat-damage-to-a-player triggered
    ability that belongs to the card itself (CR 603.2).

    This is a DISTINCT oracle shape from `parse_has_attack_trigger`: an attack
    trigger fires on declaration, a combat-damage trigger fires only when the
    creature connects. 331 Modern creatures carry the latter — the "connects →
    draw a card / make a Treasure / steal a card" value engines that power
    aggro and tempo decks (Ragavan, Psychic Frog, the ninja cycle, …). They are
    strictly more than their printed body, and `parse_has_attack_trigger`
    correctly returns False for them, so without this parser they were valued
    as vanilla creatures.

    Recognised phrasings mirror `parse_has_attack_trigger`'s self-anchor:
      - "Whenever this creature deals combat damage to a player" — generic
        self-referential form.
      - "Whenever [Card Name] deals combat damage to a player" — self-named
        form used by legendaries, including the personal-name-only variant
        ("Whenever Ragavan deals combat damage …" on "Ragavan, Nimble
        Pilferer").

    Deliberately excluded, same discriminator as the attack-trigger parser:
    "Whenever a creature you control deals combat damage" / "Whenever another
    creature …" — those are triggers on *other* permanents that fire off any
    creature's combat damage, not this body's own recurring value.

    The "to a player" anchor is required: "deals combat damage to a creature"
    is a fight/deathtouch-style rider, not a per-connection value engine. The
    common "to a player or planeswalker" phrasing is matched by the prefix.
    """
    low = (oracle or '').lower()
    anchor = 'deals combat damage to a player'
    if f'whenever this creature {anchor}' in low:
        return True
    if name:
        cname = name.lower().split(' //')[0].strip()
        if cname and f'whenever {cname} {anchor}' in low:
            return True
        # Legendaries refer to themselves by the personal name before the
        # comma ("Whenever Ragavan deals combat damage to a player, …").
        short = cname.split(',')[0].strip()
        if short and short != cname and f'whenever {short} {anchor}' in low:
            return True
    return False


def parse_targets_creature_spell(oracle: str) -> bool:
    """Return True when oracle text contains 'target creature spell'.

    Matches any card (counterspell, ETB effect, triggered ability) whose
    effect explicitly targets a creature spell on the stack — as opposed
    to 'target creature' which hits the battlefield.  The 'spell' suffix
    is the discriminator.

    Examples: Essence Scatter ("Counter target creature spell."),
    Subtlety ETB ("target creature spell or planeswalker spell").
    """
    return bool(oracle and 'target creature spell' in oracle.lower())


def parse_targets_planeswalker_spell(oracle: str) -> bool:
    """Return True when oracle text targets a planeswalker spell on the stack.

    Two oracle phrasings qualify:
      - 'target planeswalker spell'   — direct form (hypothetical; no widely
                                        printed Modern card uses this phrasing
                                        alone, but the parser must cover it)
      - 'or planeswalker spell'       — chained-clause form used when the same
                                        ability targets both types in sequence,
                                        e.g. "target creature spell or planeswalker
                                        spell" (Subtlety, Fangkeeper's Familiar).

    Intentionally excluded: 'target creature or planeswalker' (no 'spell'
    suffix) — that phrasing targets battlefield permanents, not stack objects.
    """
    if not oracle:
        return False
    lower = oracle.lower()
    return 'target planeswalker spell' in lower or 'or planeswalker spell' in lower


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


def has_delve(oracle: str) -> bool:
    """Check if card has delve keyword."""
    return 'delve' in oracle.lower()


# Basic land type names for land-type conditional bonus detection.
_BASIC_LAND_TYPES = frozenset(
    ["plains", "island", "swamp", "mountain", "forest"]
)

# "This creature gets +N/+N as long as you control a <LandType>."
# Also matches "gets +N/+M" (asymmetric) but we only record P bonus here;
# the toughness bonus is always the same value (+N) in practice.
_LAND_TYPE_BONUS_RE = re.compile(
    r'gets?\s+\+(\d+)/\+(\d+)\s+as\s+long\s+as\s+you\s+control\s+a\s+'
    r'(plains|island|swamp|mountain|forest)\b',
    re.IGNORECASE,
)


def parse_land_type_bonuses(oracle: str) -> dict:
    """Parse "gets +N/+N as long as you control a [LandType]" clauses.

    Returns a dict mapping lowercase land-type name to integer power/toughness
    bonus (assumes symmetric +N/+N; asymmetric cards would need separate
    power_bonus and toughness_bonus, but no such card exists in the current DB).
    An empty dict means no such clause is present.

    Class size: Wild Nacatl, and any future creature sharing this oracle shape.
    """
    result: dict = {}
    for m in _LAND_TYPE_BONUS_RE.finditer(oracle):
        bonus = int(m.group(1))   # power bonus (assume symmetric)
        land_type = m.group(3).lower()
        result[land_type] = result.get(land_type, 0) + bonus
    return result


def _parse_mana_symbols_to_cost(symbols: list) -> "ManaCost":
    """Convert a list of mana symbol strings to a ManaCost object.

    Hybrid symbols (e.g. 'U/R') are treated as generic=1 because the caster
    chooses which colour to pay — any mana satisfies a hybrid pip.
    Shared by parse_warp_cost, parse_dash_cost, and parse_escape_cost.
    """
    from .mana import ManaCost
    cost = ManaCost()
    for sym in symbols:
        if sym.isdigit():
            cost.generic += int(sym)
        elif '/' in sym:
            cost.generic += 1  # hybrid: any colour works
        elif sym.upper() == 'W':
            cost.white += 1
        elif sym.upper() == 'U':
            cost.blue += 1
        elif sym.upper() == 'B':
            cost.black += 1
        elif sym.upper() == 'R':
            cost.red += 1
        elif sym.upper() == 'G':
            cost.green += 1
        elif sym.upper() == 'C':
            cost.colorless += 1
        else:
            cost.generic += 1
    return cost


def parse_dash_cost(oracle: str) -> Optional["ManaCost"]:
    """Parse Dash cost from oracle text. Returns ManaCost or None.

    "Dash {1}{R}" → ManaCost(generic=1, red=1)
    "Dash {R}"    → ManaCost(red=1)

    Returning ManaCost (not int) preserves colour pip information so that
    can_cast and cast_spell can perform a proper colour check instead of a
    colour-blind CMC comparison.
    """
    m = re.search(r'dash\s*((?:\{[^}]+\})+)', oracle, re.IGNORECASE)
    if not m:
        return None
    cost = _parse_mana_symbols_to_cost(re.findall(r'\{([^}]+)\}', m.group(1)))
    return cost if cost.cmc > 0 else None


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

    "Escape—{R}{R}{W}{W}, Exile five other cards"
        → {'cost': ManaCost(red=2, white=2), 'exile': 5}

    The 'cost' value is a ManaCost object (not a plain CMC integer) so that
    can_cast and cast_spell can perform a proper colour check.
    """
    m = re.search(r'escape[—\-]\s*((?:\{[^}]+\})+),?\s*exile\s+(\w+)\s+other\s+card',
                  oracle, re.IGNORECASE)
    if not m:
        return None
    cost = _parse_mana_symbols_to_cost(re.findall(r'\{([^}]+)\}', m.group(1)))
    word_to_num = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
                   'six': 6, 'seven': 7, 'eight': 8}
    exile_word = m.group(2).lower()
    exile_count = word_to_num.get(exile_word)
    if exile_count is None and exile_word.isdigit():
        exile_count = int(exile_word)
    return {'cost': cost, 'exile': exile_count or 5}


def parse_warp_cost(oracle: str) -> Optional["ManaCost"]:
    """Parse Warp alternative-cast cost from oracle text.

    "Warp {U/R}" → ManaCost(generic=1)    — hybrid: any 1 mana
    "Warp {3}"   → ManaCost(generic=3)    — pure generic
    "Warp {1}{G}" → ManaCost(generic=1, green=1) — mixed

    Hybrid symbols like {U/R} are treated as generic=1 (any mana satisfies
    a hybrid pip), which is correct for payment purposes since the caster
    chooses which color to pay.
    """
    m = re.search(r'[Ww]arp\s+((?:\{[^}]+\})+)', oracle)
    if not m:
        return None
    cost = _parse_mana_symbols_to_cost(re.findall(r'\{([^}]+)\}', m.group(1)))
    return cost if cost.cmc > 0 else None


def parse_spectacle_cost(oracle: str) -> "Optional[ManaCost]":
    """Parse a Spectacle alternate cost from oracle text (CR 702.131).

    Oracle pattern: "Spectacle {cost} (You may cast this spell for its
    spectacle cost rather than its mana cost if an opponent lost life
    this turn.)"

    Returns a ManaCost representing the spectacle cost, or None when the
    card has no spectacle. ManaCost is returned (not int) so that can_cast
    can perform a proper colour check and cast_spell can pass it directly
    to tap_lands_for_mana — same contract as parse_dash_cost.

    Class size: ~5–10 Modern-legal cards with spectacle (Light Up the
    Stage, Skewer the Critics, Rix Maadi Reveler, …). The oracle
    template is shared across all of them — 'spectacle {cost}' prefix
    followed by a reminder sentence.
    """
    m = re.search(r'spectacle\s+((?:\{[^}]+\})+)', oracle, re.IGNORECASE)
    if not m:
        return None
    symbols = re.findall(r'\{([^}]+)\}', m.group(1))
    cost = _parse_mana_symbols_to_cost(symbols)
    return cost if cost.cmc > 0 else None


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

    # ETB land-from-hand: creature ETB puts a land card from hand onto the
    # battlefield (Arboreal Grazer pattern). Clause-scoped so all five
    # substrings must appear in the SAME ability paragraph (CR 603.1).
    for ability in split_abilities(lower):
        if ('when ' in ability and 'enters' in ability
                and 'put' in ability and 'land' in ability
                and 'from your hand' in ability
                and 'onto the battlefield' in ability):
            tags.add("etb_land_from_hand")
            break

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


# Card-type words that can qualify a "cast a[n] <type> spell" trigger or a
# "another <type> you control enters" trigger. These are card TYPES (CR
# 205.2), distinct from subtypes (Elemental, Turtle, …).
_CARD_TYPE_WORDS = {"artifact", "creature", "enchantment", "instant",
                    "sorcery", "planeswalker", "battle", "land"}

# Permanent card types (a subset of the above — instants/sorceries are
# never permanents, so a permanent-enters trigger can only name these).
_PERMANENT_TYPE_WORDS = {"artifact", "creature", "enchantment", "land",
                         "planeswalker", "battle"}

_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4}


def parse_cast_trigger_token(oracle: str) -> Optional[Dict]:
    """Parse "whenever you cast a[n] <type> spell, create ... token"
    (CR 603 cast-triggered token), generalised across spell TYPE.

    Returns::

        {"spell_types": frozenset[str], "count": int}

    or None when the oracle has no such trigger.

    ``spell_types`` is the SET of spell types that satisfy the trigger's
    condition:

      - "noncreature spell"        -> {"noncreature"}  (sentinel: any
                                        spell that is not a creature)
      - "artifact spell"           -> {"artifact"}
      - "instant or sorcery spell" -> {"instant", "sorcery"}

    The dispatcher matches ``spell_types`` against the cast spell's card
    types (or, for the ``noncreature`` sentinel, against "not a creature
    spell"). The token's P/T / subtype / keywords are NOT stored here —
    the dispatcher passes the source oracle to ``create_token``, which
    extracts the token spec via ``parse_token_spec``. This keeps ONE
    owner for the token-shape parse.
    """
    if not oracle:
        return None
    lo = oracle.lower()
    if "whenever" not in lo or "cast" not in lo:
        return None
    if "create" not in lo or "token" not in lo:
        return None
    m = re.search(r"cast (?:a|an|your first|another) ([a-z /]*?)spell", lo)
    if not m:
        return None
    qualifier = m.group(1).strip()
    if "noncreature" in qualifier:
        spell_types = frozenset({"noncreature"})
    else:
        spell_types = frozenset(
            w for w in re.split(r"\s+or\s+|\s+and\s+|\s+", qualifier)
            if w in _CARD_TYPE_WORDS)
    if not spell_types:
        return None
    count = 1
    cm = re.search(r"create (a|an|one|two|three|four|\d+)\b", lo)
    if cm:
        tok = cm.group(1)
        count = int(tok) if tok.isdigit() else _NUM_WORDS.get(tok, 1)
    return {"spell_types": spell_types, "count": count}


def parse_enters_type_counter(oracle: str) -> Optional[Dict]:
    """Parse "whenever this creature or another <type> you control
    enters, put a +N/+N counter on this creature[. It can't be blocked
    this turn.]" (CR 603 permanent-enters trigger, dispatched by card
    TYPE).

    Returns::

        {"permanent_type": str, "counter_power": int,
         "counter_toughness": int, "unblockable_this_turn": bool}

    or None. Only card TYPES (artifact, creature, …) are handled here;
    the SUBTYPE variant ("another Elemental you control enters" — Risen
    Reef) stays on the subtype scan in engine/triggers.py, so a captured
    word that is not a permanent card type returns None.
    """
    if not oracle:
        return None
    lo = oracle.lower()
    m = re.search(
        r"whenever this creature or another (\w+) you control enters,\s*"
        r"put a \+(\d+)/\+(\d+) counter on (?:this creature|it)", lo)
    if not m:
        return None
    ptype = m.group(1)
    if ptype not in _PERMANENT_TYPE_WORDS:
        return None
    tail = lo[m.end():m.end() + 60]
    return {
        "permanent_type": ptype,
        "counter_power": int(m.group(2)),
        "counter_toughness": int(m.group(3)),
        "unblockable_this_turn": "can't be blocked this turn" in tail,
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


def parse_has_lifegain_token_trigger(oracle: str) -> bool:
    """Return True if oracle creates a token whenever the controller gains life.

    Three substrings must all appear in the oracle text:
      - 'whenever you gain life'  — the trigger condition
      - 'create'                  — the token-creation verb
      - 'token'                   — the created object type

    This replaces the runtime inline check in permanent_effects.gain_life()
    that inspected oracle_text at every lifegain event.  Cards that merely
    mention gaining life (Starscape Cleric) or creating tokens for other
    reasons (activated abilities) do not match all three conditions and
    return False.
    """
    if not oracle:
        return False
    lower = oracle.lower()
    return ('whenever you gain life' in lower
            and 'create' in lower
            and 'token' in lower)


def parse_lifegain_token_type(oracle: str) -> str:
    """Return the creature subtype string for a lifegain-token trigger.

    Examines the oracle text for a named creature subtype.  Currently
    distinguishes 'cat' (Attended Healer, Cat Collector) from the generic
    'creature' default.  The returned string is passed directly to
    game.create_token() as the token_type argument.
    """
    if not oracle:
        return 'creature'
    lower = oracle.lower()
    if 'cat' in lower:
        return 'cat'
    return 'creature'


# ---------------------------------------------------------------------------
# Landfall and library-search opponent trigger typed fields
# (oracle-migrate batch 4b — replaces runtime substring checks in
# engine/land_manager.py trigger_landfall / trigger_library_search)
# ---------------------------------------------------------------------------

_MANA_COLORS = ('W', 'U', 'B', 'R', 'G')


def parse_has_landfall(oracle: str) -> bool:
    """Return True if the card's oracle text carries a landfall trigger.

    Covers three equivalent phrasings used across printed sets:
      - "Landfall" keyword header (most common: Omnath, Hedron Crab, etc.)
      - "land enters" (non-keyword phrasing for the same trigger)
      - "whenever a land" (older or alternate templates)
    """
    if not oracle:
        return False
    lower = oracle.lower()
    return (
        'landfall' in lower
        or 'land enters' in lower
        or 'whenever a land' in lower
    )


def parse_has_library_search_opponent_trigger(oracle: str) -> bool:
    """Return True if the card triggers when an opponent searches their library.

    Covers "whenever an opponent searches … library" (Wan Shi Tong pattern).
    """
    if not oracle:
        return False
    lower = oracle.lower()
    return 'whenever an opponent searches' in lower and 'library' in lower


def parse_library_search_trigger_draws_card(oracle: str) -> bool:
    """Return True when the library-search-opponent trigger also draws a card."""
    if not oracle:
        return False
    lower = oracle.lower()
    return (
        parse_has_library_search_opponent_trigger(oracle)
        and 'draw a card' in lower
    )


def parse_landfall_first_life_gain(oracle: str) -> int:
    """Return life gained on the first landfall trigger (0 if no such clause).

    Parses "first time … gain N life" (Omnath, Locus of Creation pattern).
    """
    if not oracle or 'first time' not in oracle.lower():
        return 0
    m = re.search(r'first time[^.]*gain\s+(\d+)\s+life', oracle, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def parse_landfall_third_damage(oracle: str) -> int:
    """Return damage dealt on the third landfall trigger (0 if no such clause).

    Parses "third time … deals N damage" (Omnath, Locus of Creation pattern).
    """
    if not oracle or 'third time' not in oracle.lower():
        return 0
    m = re.search(r'third time[^.]*deals?\s+(\d+)\s+damage', oracle, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def parse_landfall_second_mana_colors(oracle: str) -> tuple:
    """Return mana colors added on the second landfall trigger (empty tuple = none).

    Parses "{R}", "{G}", "{W}", "{U}", "{B}" symbols in the "second time" clause
    (Omnath, Locus of Creation pattern).
    """
    if not oracle or 'second time' not in oracle.lower():
        return ()
    lower = oracle.lower()
    second_idx = lower.index('second time')
    # Extract text from 'second time' to the next sentence boundary (period or
    # newline), capped at 200 chars to avoid runaway matching.
    clause = oracle[second_idx:second_idx + 200]
    for sep in ('.', '\n'):
        pos = clause.find(sep)
        if pos > 0:
            clause = clause[:pos]
            break
    return tuple(c for c in _MANA_COLORS if '{' + c + '}' in clause)


# ---------------------------------------------------------------------------
# Batch 5 typed fields: on-hit triggers, destroy capability, tutor detection,
# noncreature-spell-cast triggers, artifact synergy
# (oracle-migrate batch 5 — replaces runtime substring checks in ai/ and engine/)
# ---------------------------------------------------------------------------


def parse_has_combat_damage_player_trigger(oracle: str) -> bool:
    """Return True if the permanent triggers when it deals combat damage to a player.

    Matches "whenever [this] deals combat damage to a player" — the on-hit
    trigger pattern shared by Ragavan, Thieving Skydiver, Dreadhorde Arcanist,
    and similar cards.  Replaces runtime 'combat damage to a player' in oracle
    substring checks in ai/ev_player.py.
    """
    if not oracle:
        return False
    return 'combat damage to a player' in oracle.lower()


def parse_can_destroy_artifact(oracle: str) -> bool:
    """Return True if the card can destroy one or more artifacts.

    Matches 'destroy target artifact' or 'destroy all artifacts' — the two
    operative patterns for artifact-removal spells and ETB effects (Ancient
    Grudge, Abrupt Decay, Collector Ouphe's triggered ability).  Replaces
    runtime 'destroy target artifact' / 'destroy all artifacts' inline checks
    in ai/ev_player.py.
    """
    if not oracle:
        return False
    lower = oracle.lower()
    return 'destroy target artifact' in lower or 'destroy all artifacts' in lower


def parse_can_destroy_enchantment(oracle: str) -> bool:
    """Return True if the card can destroy an enchantment.

    Matches 'destroy target enchantment'.  Replaces the runtime substring
    check in ai/ev_player.py.
    """
    if not oracle:
        return False
    return 'destroy target enchantment' in oracle.lower()


def parse_can_destroy_nonland_permanent(oracle: str) -> bool:
    """Return True if the card can destroy a nonland permanent.

    Matches 'destroy target nonland permanent' — the broadest non-land removal
    pattern (Abrupt Decay, Leyline Binding, etc.).  Replaces the runtime
    substring check in ai/ev_player.py.
    """
    if not oracle:
        return False
    return 'destroy target nonland permanent' in oracle.lower()


def parse_has_scaling_token_finisher(oracle: str) -> bool:
    """Return True if the card creates a storm-scaled number of tokens.

    Detection: oracle text contains all of 'create', 'tokens', and 'for each'
    — the Empty-the-Warrens template ("Create two 1/1 … tokens for each spell
    cast before it this turn").  Replaces nine identical runtime substring
    checks in ai/combo_calc.py, ai/finisher_simulator.py, and
    ai/finisher_simulator_v3.py.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'create' in lo and 'tokens' in lo and 'for each' in lo


def parse_is_tutor(oracle: str) -> bool:
    """Return True if the card searches the library or fetches from outside the game.

    Matches any of:
      - 'search your library'  — classic tutor pattern
      - 'from outside the game' — Wish-type fetch
      - 'from your sideboard'  — explicit sideboard-fetch wording

    Replaces the three-way 'search your library' / 'from outside the game' /
    'from your sideboard' runtime OR checks in ai/ev_evaluator.py.
    """
    if not oracle:
        return False
    lower = oracle.lower()
    return ('search your library' in lower
            or 'from outside the game' in lower
            or 'from your sideboard' in lower)


def parse_has_noncreature_spell_cast_trigger(oracle: str) -> bool:
    """Return True if the permanent triggers whenever a noncreature spell is cast.

    Matches the three-word conjunction 'whenever … cast … noncreature spell' in
    any order, which covers the two main formulations:
      - "Whenever you cast a noncreature spell" (Young Pyromancer, Monastery
        Swiftspear, Dragon's Rage Channeler, Ocelot Pride)
      - "Whenever a player casts a noncreature spell" (Spellstutter Sprite)

    Replaces runtime 'noncreature spell' in oracle checks in engine/oracle_resolver.py
    and ai/ev_evaluator.py that dispatch or score noncreature-spell-cast triggers.
    """
    if not oracle:
        return False
    lower = oracle.lower()
    return 'whenever' in lower and 'cast' in lower and 'noncreature spell' in lower


def parse_has_artifact_synergy(oracle: str) -> bool:
    """Return True if the card scales with or requires artifacts (synergy flag).

    Matches:
      - 'for each artifact'   — power/toughness or damage that scales with artifacts
      - 'metalcraft'          — explicit Metalcraft keyword
      - 'affinity for artifacts' — Affinity mechanic

    Replaces runtime 'for each artifact' / 'metalcraft' / 'affinity for artifacts'
    inline checks in ai/ev_player.py, ai/response.py, and engine/callbacks.py.
    """
    if not oracle:
        return False
    lower = oracle.lower()
    return ('for each artifact' in lower
            or 'metalcraft' in lower
            or 'affinity for artifacts' in lower)


def parse_deals_targeted_damage(oracle: str) -> bool:
    """Return True if the card deals damage to a target / to each player /
    to any target — the direct-damage finisher shape (Grapeshot pattern).

    Replaces the identical runtime predicate ("damage" present AND one of
    target / each / any present) duplicated in
    ai/combo_calc._payoff_deals_direct_damage and ai/combo_chain's
    classify_card deals_direct flag. Stored as
    CardTemplate.deals_targeted_damage (bool) at DB load. Semantics are
    byte-for-byte the old inline predicate — no behavior change.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return ('damage' in lo
            and ('target' in lo or 'each' in lo or 'any' in lo))


def parse_has_draw_effect(oracle: str) -> bool:
    """Return True if the card draws or impulse-draws cards.

    Covers the _oracle_signals_card_draw cluster in ev_evaluator.py:
      - 'draw a card' / 'draw two cards' / 'draw three cards' / 'draw cards'
      - 'look at the top' (surveil/scry family that can put to hand)
      - 'exile the top ... play/cast' (impulse draw: Reckless Impulse pattern)

    Replaces runtime 'draw' / 'look at the top' / 'exile the top' checks in
    ai/ev_evaluator.py:1400-1407 and the tag-gated 'draw' in oracle fallback
    at ev_evaluator.py:1098.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    if ('draw a card' in lo or 'draw two card' in lo or 'draw three card' in lo
            or 'draw cards' in lo or 'draws cards' in lo or 'look at the top' in lo):
        return True
    # Impulse draw: exile top N and play/cast (Reckless Impulse, Wrenn's Resolve)
    # Use 'you may play' / 'you may cast' to avoid matching 'player' in milling text
    if 'exile the top' in lo and ('you may play' in lo or 'you may cast' in lo
                                   or 'play those cards' in lo or 'cast them' in lo):
        return True
    return False


def parse_can_exile_permanent(oracle: str) -> bool:
    """Return True if the card can exile a targeted permanent.

    Matches 'exile target <permanent-type>' — the class of cards that remove
    something from the battlefield by exile rather than destroy. Replaces
    'exile target' in oracle substring checks in ev_evaluator.py and
    ev_player.py (removal-path detection).

    Conservative: requires a permanent-type noun (creature / artifact /
    enchantment / permanent / nonland / planeswalker) to avoid matching
    graveyard-exile or hand-exile spells (Thoughtseize, Remand are not
    permanent removal).
    """
    if not oracle:
        return False
    lo = oracle.lower()
    if 'exile target' not in lo:
        return False
    return any(k in lo for k in (
        'creature', 'artifact', 'enchantment', 'permanent',
        'nonland', 'planeswalker', 'token',
    ))


def parse_has_symmetric_reanimation(oracle: str) -> bool:
    """Return True if the card returns creatures from ALL graveyards simultaneously.

    The Living End class: 'each player returns ... creature cards from their
    graveyard to the battlefield' and 'all creature cards in all graveyards'.
    Distinct from one-sided reanimation (Goryo's Vengeance).

    Replaces the 4-clause runtime check at ai/ev_evaluator.py:2118-2121.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    if not (('each player' in lo or 'all graveyards' in lo) and 'graveyard' in lo):
        return False
    return (('return' in lo or 'battlefield' in lo) and 'creature' in lo)


# NOTE (CR 107.4f): Phyrexian pips are NOT parsed here.  A card's oracle
# text carries a reminder clause that names the symbol exactly ONCE however
# many pips the cost has — Dismember's {1}{B/P}{B/P} reads "({B/P} can be
# paid with either {B} or 2 life.)" — so any count taken from oracle text
# undercounts every multi-pip card and reads 0 if reminder text is stripped.
# The pips are parsed from the printed MANA COST by
# `card_database.parse_mana_cost_mtgjson` into `ManaCost.phyrexian`
# (colour -> count), and summed into `CardTemplate.phyrexian_pip_count`.


# -- Batch 7 typed fields --------------------------------------------------

def parse_has_token_effect(oracle: str) -> bool:
    """Return True when the card creates tokens.

    Matches oracle phrasings that create tokens:
      - "create a/create two/create three ... token"
      - "creates a ... token"
      - "put a ... token [onto the battlefield]"
    Checks for co-occurrence of "create"/"put a" and "token" rather than
    the narrower "create a" to cover plural-creation phrasings
    ("Create two 2/2 Zombies").  Pure "put a" without "token" (e.g.
    "put a counter") returns False because the "token" guard rules it out.

    Replaces runtime 'create' + 'token' substring checks in
    finisher_simulator, finisher_simulator_v3, combo_calc, ev_evaluator,
    and oracle_resolver.

    Class size: all token-creating permanents and spells in Modern (hundreds).
    """
    if not oracle:
        return False
    lo = oracle.lower()
    if 'token' not in lo:
        return False
    return 'create' in lo or 'put a' in lo


def parse_has_graveyard_recursion(oracle: str) -> bool:
    """Return True when the card returns cards from a graveyard to hand or battlefield.

    Matches "from your graveyard", "from their graveyard", "from a graveyard",
    and "from an opponent's graveyard" -- the canonical phrasing for any
    reanimation or recursion effect (Goryo's Vengeance, Unburial Rites,
    Eternal Witness, etc.).  Replaces runtime graveyard-return detection in
    sideboard_solver (~3 violations).

    Class size: all reanimation / recursion spells in Modern (dozens).
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return (
        'from your graveyard' in lo
        or 'from their graveyard' in lo
        or 'from a graveyard' in lo
        or "from an opponent's graveyard" in lo
    )


def parse_has_graveyard_hate(oracle: str) -> bool:
    """Return True when the card exiles graveyards or prevents graveyard casting.

    Matches four canonical shapes:
    - "exile … graveyard" without "onto the battlefield" (Relic of Progenitus,
      Tormod's Crypt, Bojuka Bog — targeted/mass exile)
    - "exile all (cards from) graveyards" (Rest in Peace, Scavenging Ooze)
    - "can't … cast … from … graveyard" (Grafdigger's Cage)
    - "if a card would be put into … graveyard … exile" (Leyline of the Void,
      Anafenza)

    Explicitly excludes "onto the battlefield" to avoid false-positives on
    mass reanimation (Living End, See the Unwritten).

    Replaces 4 runtime re.search calls in ai/sideboard_solver._clause_gy_hate.
    Class size: all Modern-legal hate pieces (20+ cards).
    """
    if not oracle:
        return False
    lo = oracle.lower()
    # Targeted / mass graveyard exile — excluding reanimation effects.
    # Both "onto the battlefield" and "to the battlefield" mark reanimation.
    has_battlefield = 'onto the battlefield' in lo or 'to the battlefield' in lo
    if not has_battlefield:
        if re.search(r"exile [\w\s']*?graveyard", lo):
            return True
        if re.search(r'exile all (cards from )?graveyards?', lo):
            return True
    # Cast-prevention ("Grafdigger's Cage" pattern).
    if re.search(r"can'?t (be )?cast (spells )?from", lo) and 'graveyard' in lo:
        return True
    # Replacement-to-exile ("Leyline of the Void" / "Anafenza" pattern).
    if 'if a card would be put into' in lo and 'graveyard' in lo and 'exile' in lo:
        return True
    return False


def parse_has_spell_chain_hate(oracle: str) -> bool:
    """Return True when the card denies spell-chain / storm-class decks.

    Matches three canonical shapes:
    - "can't cast more than one spell" (Rule of Law, Ethersworn Canonist)
    - "costs {1} more to cast for each other spell" (per-spell surcharge)
    - "counter target triggered ability" (Trickbind / Squelch — answers storm
      triggers without countering the spell itself)

    Replaces 3 runtime re.search calls in
    ai/sideboard_solver._clause_spell_chain_hate.
    Class size: all Modern-legal spell-chain hate pieces (10+ cards).
    """
    if not oracle:
        return False
    lo = oracle.lower()
    if re.search(r"can'?t cast more than one spell", lo):
        return True
    if re.search(r"costs? \{1\} more to cast for each other spell", lo):
        return True
    if re.search(r"counter target (?:triggered|activated)", lo):
        return True
    return False


def parse_stax_class(oracle: str) -> Optional[str]:
    """Return the stax family name for locking / taxing permanents, or None.

    Families:
    - 'chalice'    — Chalice of the Void: counter spells whose mana value
                     equals the permanent's charge counters
    - 'blood_moon' — Blood Moon: nonbasic lands become a basic type
    - 'canonist'   — Ethersworn Canonist / Rule of Law: one-spell-per-turn lock
    - 'torpor_orb' — Torpor Orb / Cursed Totem: ETB abilities don't trigger

    Replaces 9 runtime oracle checks in ai/stax_ev.classify_stax.
    Class size: all Modern-legal stax pieces (~20 cards across 4 families).
    """
    if not oracle:
        return None
    lo = oracle.lower()
    # Chalice of the Void family
    if ('charge counter' in lo and 'mana value' in lo
            and ('counter that spell' in lo or 'counter it' in lo)):
        return 'chalice'
    # Blood Moon family
    if 'nonbasic lands are' in lo:
        for basic in ('mountain', 'island', 'plains', 'swamp', 'forest'):
            if basic in lo:
                return 'blood_moon'
    # Canonist / Rule of Law family
    if (("can't cast more than one" in lo and 'each turn' in lo)
            or ("can't cast additional" in lo)):
        return 'canonist'
    # Torpor Orb / Cursed Totem family
    if ('entering' in lo and 'abilities' in lo
            and ("don't cause" in lo or "don't trigger" in lo)):
        return 'torpor_orb'
    return None


def parse_stax_forced_basic(oracle: str) -> Optional[str]:
    """Return the basic land type forced by Blood-Moon-type effects, or None.

    Blood Moon forces 'mountain'; a hypothetical Island-Moon variant would
    return 'island'. Returns None for all non-Blood-Moon cards.

    Replaces 1 runtime oracle check in ai/stax_ev._blood_moon_lock_ev.
    """
    if not oracle:
        return None
    lo = oracle.lower()
    if 'nonbasic lands are' not in lo:
        return None
    for basic in ('mountain', 'island', 'plains', 'swamp', 'forest'):
        if f'are {basic}s' in lo or f'are {basic}.' in lo:
            return basic
    return None


def parse_has_discard_effect(oracle: str) -> bool:
    """Return True when the card causes discard.

    Matches both first-person ("discard a card") and third-person
    ("discards a card", "discards two cards") phrasing, plus the
    "discard/discards your/their hand" whole-hand-dump forms.

    Covers hand disruption spells (Thoughtseize, Inquisition of Kozilek),
    repeatable discarders (Liliana of the Veil), and mass-discard effects
    (Wheel of Fortune-class), regardless of whose hand is discarded.

    Replaces runtime 'discard a card' substring checks in finisher_simulator
    and ev_player.

    Class size: all discard-effect spells and permanents in Modern (dozens).
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return (
        'discard a card' in lo
        or 'discards a card' in lo
        or 'discard your hand' in lo
        or 'discards your hand' in lo
        or 'discard their hand' in lo
        or 'discards their hand' in lo
        or 'discard two cards' in lo
        or 'discards two cards' in lo
    )


def parse_is_storm_spell(oracle: str) -> bool:
    """Return True when the card has the Storm keyword (CR 702.39).

    Matches any oracle text containing the word "storm" as a standalone
    word boundary token (Grapeshot, Empty the Warrens, Brain Freeze,
    Tendrils of Agony, etc.).  The word-boundary anchor prevents false
    positives from card names that contain "storm" as a substring
    (e.g. "Brainstorm", "Thunderstorm").

    Replaces runtime 'storm' in oracle check in sideboard_solver.

    Class size: all storm spells in Modern (approximately 10-15 across the
    card pool, touching every storm-deck archetype).
    """
    import re as _re
    if not oracle:
        return False
    return bool(_re.search(r'\bstorm\b', oracle.lower()))


def parse_has_charge_counter_ability(oracle: str) -> bool:
    """Return True when the card uses charge counters.

    Matches "charge counter" in oracle text -- the canonical phrasing for
    Chalice of the Void, Aether Vial, and any other card that accumulates
    or checks charge counters.  Replaces runtime 'charge counter' in oracle
    checks in cast_manager, game_runner, and stax_ev.

    Class size: all charge-counter permanent cards in Modern (approximately
    10-20, covering both accelerating artifacts and lock pieces).
    """
    if not oracle:
        return False
    return 'charge counter' in oracle.lower()


def parse_has_cast_trigger(oracle: str) -> bool:
    """Return True when the card has a 'when you cast' triggered ability.

    Covers cast-trigger permanents (Amulet of Vigor, Kiln Fiend-class
    creatures, cascade permanents) and cast-trigger sorceries.  Replaces
    'when you cast' in oracle runtime checks.

    Class size: cast-trigger cards in Modern (Amulet, Bloodbraid Elf,
    Searing Wind, approximately 30-50 cards).
    """
    if not oracle:
        return False
    return 'when you cast' in oracle.lower()


def parse_has_recurring_trigger(oracle: str) -> bool:
    """Return True when the card has a non-ETB recurring triggered ability.

    Matches 'whenever' trigger patterns (excluding ETB text that already
    fires as an ETB trigger) and 'at the beginning of' upkeep/draw triggers.
    Covers anthem-style lords, Amulet untap triggers, draw engines, and
    recurring damage/life triggers.  Replaces runtime 'whenever' in oracle
    and 'at the beginning of' in oracle checks.

    Class size: ~hundreds of Modern cards with triggered abilities.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    if 'at the beginning of' in lo:
        return True
    return bool(re.search(r'whenever ', lo))


def parse_limits_opponent_spell_timing(oracle: str) -> bool:
    """Return True for cards that restrict opponents to sorcery-speed casts.

    Matches Teferi, Time Raveler's static: 'cast spells only any time they
    could cast a sorcery'.  Replaces the full-phrase runtime substring check.

    Class size: ~5-10 Modern-legal cards with this static (Teferi family).
    """
    if not oracle:
        return False
    return 'cast spells only any time they could cast a sorcery' in oracle.lower()


def parse_has_charge_counter_wipe(oracle: str) -> bool:
    """Return True for charge-counter permanents that destroy by mana value.

    Matches Ratchet Bomb / Engineered Explosives pattern: 'charge counter'
    + 'destroy' + 'mana value'.  Distinct from the Chalice pattern (which
    counters spells rather than destroying permanents).

    Class size: ~5 Modern cards (Ratchet Bomb, Engineered Explosives,
    and variants).
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'charge counter' in lo and 'destroy' in lo and 'mana value' in lo


def parse_prevents_graveyard_etb(oracle: str) -> bool:
    """Return True for permanents that prevent creatures from entering via graveyards.

    Matches Grafdigger's Cage pattern: 'creature cards in graveyards' +
    "can't enter the battlefield".  Distinct from has_graveyard_hate (exile-based
    hate) — this is a static ETB-prevention effect.

    Class size: ~5 Modern-legal cards (Grafdigger's Cage, Opposition Agent,
    Containment Priest family).
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return ("creature cards in graveyards" in lo
            and "can't enter the battlefield" in lo)


def parse_reanimates_from_graveyard(oracle: str) -> bool:
    """Return True when the card puts a card from a GRAVEYARD onto the
    BATTLEFIELD (Goryo's Vengeance, Unburial Rites, Living End, Persist,
    Ephemerate-via-Persist, …).

    Narrower than ``parse_has_graveyard_recursion``, deliberately. That
    predicate matches any "from your graveyard" phrase, which flashback's
    own reminder text ("Cast this spell from your graveyard for its
    flashback cost") satisfies — it answers "does this card care about a
    graveyard at all", which is the right question for sideboard advice
    and the wrong one for "is a creature in that graveyard a reanimation
    target". Ordering is the discriminator: 'graveyard' must precede
    'battlefield', which is what excludes "put a creature card from your
    HAND onto the battlefield".

    Class size: every reanimation spell and permanent in Modern (dozens).
    Consumed by ``ai.predicates.graveyard_fuel`` to decide whether the
    creature cards in a graveyard are still a resource to their owner.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    if 'from' not in lo or 'graveyard' not in lo:
        return False
    if not (('return' in lo or 'put' in lo) and 'battlefield' in lo):
        return False
    gy_idx = lo.find('graveyard')
    return lo.find('battlefield', gy_idx) >= 0


def parse_exiles_cards_bound_for_graveyard(oracle: str) -> bool:
    """Return True for the CONTINUOUS replacement "if a card would be put
    into a graveyard, exile it instead" (Leyline of the Void, Rest in
    Peace, Anafenza).

    The third slice of what `parse_has_graveyard_hate` lumps together.
    That predicate answers "does this card care about graveyards at all",
    which is right for sideboard ADVICE and wrong as a mechanism gate;
    this one answers "can this permanent stop a card reaching a
    graveyard". Sibling of `parse_prevents_graveyard_casting` (the
    can't-cast static) and of the parsed EXILE_FROM_GRAVEYARD activated
    ability (the removal ability).

    Class size: the Leyline of the Void / Rest in Peace replacement
    family.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return bool('would be put into' in lo and 'graveyard' in lo
                and 'exile' in lo)


def parse_prevents_graveyard_casting(oracle: str) -> bool:
    """Return True for the printed "Players can't cast spells from
    graveyards (or libraries)" STATIC (the Grafdigger's Cage clause).

    Sibling of ``parse_prevents_graveyard_etb``: that one owns the "can't
    ENTER the battlefield" half of the Cage, this one owns the "can't CAST"
    half. Both are static abilities on a permanent, and both are a
    different mechanic from graveyard REMOVAL — an ability that exiles
    cards out of a graveyard takes the fuel away when it is ACTIVATED and
    bans nothing while it sits on the battlefield.

    That distinction is the whole reason this predicate exists.
    ``parse_has_graveyard_hate`` is deliberately broad ("exile … graveyard"
    anywhere in the oracle) because its consumer is sideboard ADVICE; 446
    Modern permanents satisfy it. Using it as the rules gate in
    ``CastManager.can_cast`` turned every one of them into a symmetric,
    permanent Grafdigger's Cage. Exactly 4 Modern-legal cards print the
    static this predicate matches.

    Class size: the printed cast-prevention family (Grafdigger's Cage,
    Ashes of the Abhorrent, Kunoros, Weathered Runestone) plus any future
    reprint of the same clause.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return bool(re.search(r"can'?t (be )?cast (spells )?from", lo)
                and 'graveyard' in lo)


def parse_requires_creature_target(oracle: str) -> bool:
    """Return True when the card requires a creature or creature-spell target.

    Matches 'target creature' or 'creature spell' in oracle text — used by
    evoke target validation to skip evoke when no valid target exists.

    Class size: hundreds of Modern cards that target creatures.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'target creature' in lo or 'creature spell' in lo


def parse_has_alternate_exile_cost(oracle: str) -> bool:
    """Return True for spells with an 'exile a card from your hand' alternate cost.

    Matches Grief / Solitude / Ephemerate-family pattern: 'exile a' +
    'rather than pay' in oracle text.

    Class size: ~10 Modern-legal Evoke elementals and similar (Grief,
    Subtlety, Solitude, Endurance, Fury).
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'exile a' in lo and 'rather than pay' in lo


def parse_has_mana_value_wipe(oracle: str) -> bool:
    """Return True for X-cost spells that destroy permanents by mana value.

    Matches Wrath of the Skies / Engineered Explosives wipe pattern:
    'destroy each' combined with 'mana value less than or equal to'.
    Distinct from Chalice (which counters spells) and charge-counter wipes
    (which check charge counts, not paid X).

    Class size: ~5 Modern-legal mana-value-wipe cards (EE, Wrath of the
    Skies, Engineered Explosives, Suncleanser variants).
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'destroy each' in lo and 'mana value less than or equal to' in lo


def parse_has_sacrifice_for_damage(oracle: str) -> bool:
    """Return True for activated abilities that sacrifice a creature to deal damage.

    Matches Goblin Bombardment / Blasting Station pattern: 'sacrifice a
    creature' combined with a damage-dealing clause.  Used to detect
    delayed-value permanents whose damage engine consumes creatures rather
    than producing them.

    Class size: ~10-20 Modern-legal sacrifice-outlet + damage cards.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'sacrifice a creature' in lo and 'damage' in lo


def parse_has_scaling_effect(oracle: str) -> bool:
    """Return True when oracle contains a 'for each' or 'for every' scaling clause.

    Identifies cards whose effect magnitude scales with a count of permanents,
    cards, or other game objects.  A second copy of such a card stacks its
    scaling independently, making duplicates useful.  Replaces runtime
    'for each'/'for every' substring checks in ev_player.py stacks detection.

    Class size: hundreds of Modern-legal cards with 'for each' clauses.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'for each' in lo or 'for every' in lo


def parse_has_self_trigger(oracle: str) -> bool:
    """Return True when oracle contains a 'when this' self-referential trigger.

    Covers ETB triggers ('when this enters'), attack triggers ('when this
    attacks'), death triggers ('when this dies'), and any other 'when this'
    pattern.  Each copy of such a card fires its own trigger, so duplicates
    stack in value.  Replaces runtime 'when this' substring check in
    ev_player.py stacks detection.

    Class size: hundreds of Modern-legal creatures with self-triggered abilities.
    """
    if not oracle:
        return False
    return 'when this' in oracle.lower()


def parse_has_recurring_draw_trigger(oracle: str) -> bool:
    """Return True when oracle has a repeatable draw triggered ability.

    Combines the 'whenever' recurring-trigger signal with an explicit draw
    clause, targeting engines like 'Whenever you cast a spell, draw a card'
    (Rhystic Study, Mystic Remora pattern).  Replaces the runtime two-field
    AND check ('whenever' in oracle and 'draw' in oracle) in evaluator.py.

    Class size: dozens of Modern-legal draw-engine permanents.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'whenever' in lo and 'draw' in lo


def parse_has_each_opponent_effect(oracle: str) -> bool:
    """Return True when oracle targets 'each opponent' or 'each player'.

    Identifies cards whose damage or effect hits every opponent simultaneously,
    making them scale with multiplayer headcount and highly efficient in 1v1.
    Replaces the runtime 'each opponent'/'each player' check in evaluator.py.

    Class size: dozens of Modern-legal burn/damage-each-opponent effects.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'each opponent' in lo or 'each player' in lo


def parse_has_pump_grant(oracle: str) -> bool:
    """Return True when oracle grants a +X/+Y bonus to a creature.

    Covers 'gets +N/+M' (temporary combat boost) and 'additional +N/+M'
    (extra-attack/double-strike style bonus) patterns.  Replaces runtime
    'gets +' and 'additional +' substring checks in evaluator.py pump
    detection.  The '+1/+1 counter' form is left as a non-flagged literal.

    Class size: hundreds of Modern-legal pump spells, auras, and abilities.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'gets +' in lo or 'additional +' in lo


def parse_has_x_counter_scaling(oracle: str) -> bool:
    """Return True when oracle grants X +1/+1 counters based on mana paid.

    Identifies Walking Ballista / Hangarback Walker class of X-cost creatures
    that enter with a number of +1/+1 counters equal to X.  Used in
    response.py to project the expected power of such creatures when the
    opponent is about to play them.  Replaces runtime 'x +1/+1 counter' and
    'x +1/+1 counters' substring checks.

    Class size: ~20-40 Modern-legal X-cost counter creatures.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'x +1/+1 counter' in lo or 'x +1/+1 counters' in lo


def parse_has_lifegain_equal_power(oracle: str) -> bool:
    """Return True when oracle grants lifegain equal to a creature's power.

    Covers the Solitude/Fury evoke pattern: removing a creature causes its
    controller to gain life equal to its power.  Used in board_eval.py to
    detect removal ETBs that incidentally heal the opponent, lowering their
    value against small targets.  Replaces runtime 'gains life' + 'power'
    compound check.

    Class size: ~10-20 Modern-legal cards with power-scaling lifegain.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'gains life' in lo and 'power' in lo


def parse_has_lifegain_effect(oracle: str) -> bool:
    """Return True when oracle causes a player or creature to gain life.

    Catches any 'gain N life' or 'gains N life' pattern in oracle text.
    Used in ev_evaluator.py to detect ETB lifegain on creatures without
    the lifelink keyword.  Replaces runtime 'gain' + 'life' compound check.

    Class size: hundreds of Modern-legal cards with lifegain clauses.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'gain' in lo and 'life' in lo


def parse_has_exile_own_creature(oracle: str) -> bool:
    """Return True when oracle exiles a creature the caster controls.

    Covers blink / flicker effects ('exile target creature you control') used
    for ETB-value re-triggers.  Used in ev_player.py to detect blink spells
    that fizzle when the caster has no creatures.  Replaces runtime oracle
    substring check; the 'blink' tag is checked first as a faster path.

    Class size: ~30-50 Modern-legal blink/flicker instant and sorcery cards.
    """
    if not oracle:
        return False
    return 'exile target creature you control' in oracle.lower()


def parse_has_converge(oracle: str) -> bool:
    """Return True when oracle has the Converge keyword or its reminder text.

    Converge spells scale their effect with the number of colors of mana
    spent to cast them.  At cast time the engine sorts available lands to
    maximize color diversity when this flag is True.  Replaces runtime
    'converge' and 'colors of mana spent' substring checks in cast_manager.py.

    Class size: ~10-15 Modern-legal converge spells.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'converge' in lo or 'colors of mana spent' in lo


def parse_has_delirium(oracle: str) -> bool:
    """Return True when oracle has the Delirium keyword or its condition text.

    Delirium abilities activate when the controller has four or more card
    types in their graveyard.  Used in cast_manager.py to grant conditional
    keywords (e.g., Traverse the Ulvenwald flying grant) only when delirium
    is active.  Replaces runtime 'delirium' substring check.

    Class size: ~20-40 Modern-legal delirium cards.
    """
    if not oracle:
        return False
    return 'delirium' in oracle.lower()


def parse_has_all_basic_land_types(oracle: str) -> bool:
    """Return True when oracle grants all basic land types to controlled lands.

    Covers Leyline of the Guildpact pattern ('lands you control are every
    basic land type') which gives the controller 5 domain for domain spells
    and converge.  Used in cards.py _get_domain_count to short-circuit the
    land-type enumeration.  Replaces the runtime substring check there.

    Class size: ~5-10 Modern-legal land-type-granting cards.
    """
    if not oracle:
        return False
    return 'lands you control are every basic land type' in oracle.lower()


def parse_has_destroy_or_exile(oracle: str) -> bool:
    """Return True when oracle destroys or exiles a permanent.

    Covers the broad removal signal: any card whose text contains 'destroy'
    or 'exile' is a candidate for removal scoring.  Used in evaluator.py
    _spell_damage as a fallback sentinel when no damage-number clause is
    found.  Replaces the runtime 'destroy'/'exile' compound check.

    Class size: hundreds of Modern-legal removal spells and effects.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'destroy' in lo or 'exile' in lo


def parse_has_artifact_count_scaling(oracle: str) -> bool:
    """Return True when P/T scales with artifact count you control.

    Replaces the tight regex r'\\+\\d+/\\+\\d+\\s+for\\s+each\\s+artifact\\s+you\\s+control'
    used in cards.py _dynamic_base_power/_dynamic_base_toughness. The tighter
    form is preserved at parse time to avoid the Affinity-reminder-text false
    positive documented in cards.py:1019-1022.

    Class size: Construct tokens (Urza's Saga), Nettlecyst, Steel Overseer-class.
    """
    if not oracle:
        return False
    import re
    return bool(re.search(r'\+\d+/\+\d+\s+for\s+each\s+artifact\s+you\s+control',
                           oracle.lower()))


def parse_has_surveil(oracle: str) -> bool:
    """Return True when oracle contains the surveil keyword.

    Replaces 'surveil' in oracle in oracle_resolver.py.
    Class size: dozens of Dimir and multicolor cards (Consider, Thought Erasure, etc.).
    """
    if not oracle:
        return False
    return 'surveil' in oracle.lower()


def parse_has_scry(oracle: str) -> bool:
    """Return True when oracle contains the scry keyword (CR 701.18).

    Replaces runtime 'scry' substring checks in oracle_resolver.py's
    spell-resolution scry branch.

    Class size: dozens of blue and multicolor spells — Opt (scry 1),
    Serum Visions (scry 2), Deliberate (scry 2), Telling Time (scry),
    Omen of the Sea (scry 2), and any future printing with the keyword.

    Distinct from has_surveil (CR 701.42, bins to graveyard). No
    Modern-legal card oracle text contains both 'scry' and 'surveil'.
    """
    if not oracle:
        return False
    return 'scry' in oracle.lower()


def parse_has_coin_flip(oracle: str) -> bool:
    """Return True when oracle involves flipping a coin.

    Replaces 'flip a coin' in oracle in oracle_resolver.py.
    Class size: Ral, Izzet Viceroy and similar transform-on-coin-flip cards.
    """
    if not oracle:
        return False
    return 'flip a coin' in oracle.lower()


def parse_has_mobilize(oracle: str) -> bool:
    """Return True when oracle contains the mobilize keyword.

    Replaces 'mobilize' in oracle in oracle_resolver.py attack trigger dispatch.
    Class size: cards with the mobilize keyword (Kellan, etc.).
    """
    if not oracle:
        return False
    return 'mobilize' in oracle.lower()


def parse_has_transform_effect(oracle: str) -> bool:
    """Return True when oracle references transforming.

    Replaces 'transformed' in oracle in oracle_resolver.py.
    Class size: all DFC cards that transform as an effect (Fable, Ral, etc.).
    """
    if not oracle:
        return False
    return 'transform' in oracle.lower()


def parse_has_instant_or_sorcery_reference(oracle: str) -> bool:
    """Return True when oracle counts or references instants or sorceries.

    Replaces three compound checks in oracle_resolver.py:
      'instant or sorcery' | 'instant and/or sorcery' | 'instant and sorcery'
    Used to detect spells-matter trigger conditions on permanents like Fable.

    Class size: dozens of Izzet/spells-matter cards.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return ('instant or sorcery' in lo
            or 'instant and/or sorcery' in lo
            or 'instant and sorcery' in lo)


def parse_has_graveyard_target(oracle: str) -> bool:
    """Return True when oracle targets something from a graveyard.

    Replaces 'from a graveyard' in oracle in target_solver.py.
    Class size: reanimation, exile-from-graveyard, delve, and escape cards.
    """
    if not oracle:
        return False
    return 'from a graveyard' in oracle.lower()


def parse_has_dual_land_search(oracle: str) -> bool:
    """Return True when oracle searches for two land cards (Primeval Titan pattern).

    Replaces the compound check 'search' and 'two land' in oracle in triggers.py.
    Class size: Primeval Titan and any future double-land-search attack triggers.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'search' in lo and 'two land' in lo


def parse_has_energy_damage_target(oracle: str) -> bool:
    """Return True when oracle deals energy-scaled damage to a creature or planeswalker.

    Replaces the five-condition compound check in oracle_resolver.py
    for the Aetherworks Marvel / Electrostatic Pummeler energy-damage pattern:
    targets a creature or planeswalker, gains {E} energy, pays {E} for bonus
    damage, and the amount scales with the energy paid ('that much').

    Class size: energy-damage cards from Kaladesh block and any future printings.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return (('target creature or planeswalker' in lo
             or 'choose target creature or planeswalker' in lo)
            and 'you get {e}' in lo
            and 'pay any amount of {e}' in lo
            and 'that much' in lo
            and 'damage' in lo)


def parse_has_energy_production(oracle: str) -> bool:
    """Return True when oracle produces energy counters ({E}).

    Replaces 'you get' in oracle in oracle_resolver.py's noncreature-cast
    energy-trigger branch (lines 925-932). The '{e}' symbol check is already
    not ratchet-flagged; this typed field captures the 'you get' half of the
    compound.

    Class size: Boros Energy cards — Guide of Souls, Ocelot Pride, etc.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'you get' in lo and '{e}' in lo


def parse_has_look_hand_selection(oracle: str) -> bool:
    """Return True when oracle lets you look at cards and put one into your hand.

    Replaces two related checks:
    - 'put one of them into your hand' (Sleight of Hand / Opt pattern)
    - 'put them into your hand' (pile selection / look top N keep all)

    Used in oracle_resolver.py (sorcery draw dispatch) and
    ai/ev_evaluator.py (card selection EV estimation).

    Class size: Sleight of Hand, Abundant Harvest, pile-of-N selection cards.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'put one of them into your hand' in lo or 'put them into your hand' in lo


def parse_has_cast_spell_draw(oracle: str) -> bool:
    """Return True when oracle draws a card whenever a spell is cast.

    Replaces the two-condition check in oracle_resolver.py lines 958-959:
    'cast a spell' or 'cast an instant or sorcery' PLUS 'draw a card'
    with no 'noncreature' restriction (which gates a narrower variant).
    The noncreature exclusion is encoded here at parse time so the consumer
    needs no runtime oracle reads.

    Class size: Curiosity-class enchantments, Riddleform, future spell-draw cards.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    has_trigger = 'cast a spell' in lo or 'cast an instant or sorcery' in lo
    has_draw = 'draw a card' in lo
    has_noncreature = 'noncreature' in lo
    return has_trigger and has_draw and not has_noncreature


def parse_has_opponent_cast_damage(oracle: str) -> bool:
    """Return True when oracle deals damage whenever an opponent casts a spell.

    Replaces the compound 'opponent casts' + 'damage' check in oracle_resolver.py
    lines 1011-1012 (Orcish Bowmasters / opponent-cast trigger).

    Class size: Orcish Bowmasters and any future opponent-cast damage triggers.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'opponent casts' in lo and 'damage' in lo


def parse_has_mana_add_text(oracle: str) -> bool:
    """Return True when oracle contains mana-adding text.

    Replaces three 'add' in oracle checks in ai/ev_evaluator.py that detect
    mana-producing cards (urgency scoring, ritual gate). Uses a tighter
    pattern than bare 'add' to avoid false positives on 'add a counter' etc.

    Class size: all mana rocks, rituals, and land-fetch spells.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'add {' in lo or 'mana of any' in lo


def parse_has_bounce_land_oracle(oracle: str) -> bool:
    """Return True when oracle contains 'return a land you control'.

    Replaces 'return a land you control' in oracle in engine/card_effects.py
    line 3102 (Scapeshift land-priority helper: bounce lands get priority 3).

    Class size: all bounce lands (Karoo, Gruul Turf, Azorius Chancery, etc.)
    plus any future cards with this ETB replacement effect.
    """
    if not oracle:
        return False
    return 'return a land you control' in oracle.lower()


def parse_has_sacrifice_search_land(oracle: str) -> bool:
    """Return True for artifact sacrifice-then-search-for-land abilities.

    Replaces the compound 'sacrifice' + 'search' + 'land' in oracle check in
    engine/game_runner.py line 1821 (Expedition Map / Wayfarer's Bauble pattern).

    Class size: Expedition Map, Wayfarer's Bauble, Weathered Wayfarer (activated),
    and any future sacrifice-to-tutor-land effects.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'sacrifice' in lo and 'search' in lo and 'land' in lo


def parse_has_emry_graveyard_cast(oracle: str) -> bool:
    """Return True for 'choose target artifact card in your graveyard' abilities.

    Replaces the exact phrase check in engine/game_runner.py line 2032
    (Emry, Lurker of the Loch pattern: tap to cast artifact from graveyard).

    Class size: Emry and any future cards with this specific clause wording.
    """
    if not oracle:
        return False
    return 'choose target artifact card in your graveyard' in oracle.lower()


def parse_has_cc_tap_draw(oracle: str) -> bool:
    """Return True for {C}{C},{T}: draw a card activated abilities.

    Replaces re.search(r'\\{c\\}\\{c\\}\\s*,\\s*\\{t\\}\\s*:\\s*draw a card', oracle)
    in engine/game_runner.py line 2002 (Endbringer / colorless-tap-draw pattern).

    Class size: Endbringer and any future artifact with this exact mana-cost draw ability.
    """
    if not oracle:
        return False
    import re
    return bool(re.search(r'\{c\}\{c\}\s*,\s*\{t\}\s*:\s*draw a card', oracle.lower()))


def parse_has_stax_ability(oracle: str) -> bool:
    """Return True for stax/cost-tax abilities (Stony Silence, Damping Sphere pattern).

    Replaces two checks in engine/sideboard_manager.py lines 87-89:
    - 'activated abilities of artifacts ... can't be activated' (Stony Silence, Collector Ouphe)
    - 'tapped for two or more mana, it produces {c} instead' (Damping Sphere)

    Class size: all stax hate pieces that lock activated abilities or constrain mana production.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return (("activated abilities of artifacts" in lo and "can't be activated" in lo)
            or "tapped for two or more mana, it produces {c} instead" in lo)


def parse_has_pithing_needle_lock(oracle: str) -> bool:
    """Return True for Pithing Needle / Phyrexian Revoker ability-lock effects.

    Replaces two checks in engine/sideboard_manager.py lines 96-97:
    - 'choose a card name' (or 'nonland card name')
    - 'activated abilities of sources with the chosen name'

    Class size: Pithing Needle, Phyrexian Revoker, and any future name-lock effects.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return (("choose a card name" in lo or "choose a nonland card name" in lo)
            and "activated abilities of sources with the chosen name" in lo)


def parse_has_another_creature_enters_trigger(oracle: str) -> bool:
    """Return True for 'whenever another creature enters' triggers.

    Replaces 'another creature' in oracle in engine/triggers.py line 56 —
    outer gate for both lifegain and energy-production ETB fan-out triggers.

    Class size: Soul Warden, Soul's Attendant, Guide of Souls, and any future
    'whenever another creature enters' ETB watchers.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'another creature' in lo and 'enters' in lo


def parse_has_another_creature_enters_lifegain(oracle: str) -> bool:
    """Return True when 'whenever another creature enters ... gain N life'.

    Replaces 'gain' in oracle in engine/triggers.py line 57 — inner guard
    (conjunction: another creature + enters + gain + life).

    Class size: Soul Warden, Soul's Attendant, Suture Priest (and similar),
    and any future cards with this exact ETB lifegain pattern.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return ('another creature' in lo and 'enters' in lo
            and 'gain' in lo and 'life' in lo)


def parse_has_cycling_watch_trigger(oracle: str) -> bool:
    """Return True for 'whenever you cycle' global cycling-watch triggers.

    Matches battlefield permanents whose oracle text says they trigger
    whenever their controller cycles any (other) card.  Covers the
    damage, life-gain, counter, and scry subtypes — all share the same
    oracle phrase 'whenever you cycle'.

    Class size: Drannith Stinger, Drannith Healer, Flourishing Fox,
    Curator of Mysteries, Archfiend of Ifnir, Drake Haven, and any
    future Modern-legal cycling-watch permanent.
    """
    if not oracle:
        return False
    return bool(re.search(r'whenever you cycle\b', oracle.lower()))


def parse_cycling_watch_trigger_damage(oracle: str) -> int:
    """Return damage dealt to each opponent per cycling-watch trigger fire.

    Matches 'this creature deals N damage to each opponent' on
    cycling-watch permanents.  Returns 0 if no such clause exists.

    Class size: Drannith Stinger ('deals 1 damage to each opponent'),
    and any future Modern-legal cycling-watch damage permanent.
    """
    if not oracle:
        return 0
    lo = oracle.lower()
    if not re.search(r'whenever you cycle\b', lo):
        return 0
    m = re.search(r'deals?\s+(\d+)\s+damage\s+to\s+each\s+opponent', lo)
    return int(m.group(1)) if m else 0


def parse_cycling_watch_trigger_life_gain(oracle: str) -> int:
    """Return life gained per cycling-watch trigger fire.

    Matches 'you gain N life' on cycling-watch permanents.
    Returns 0 if no such clause exists.

    Class size: Drannith Healer ('you gain 1 life') and any future
    Modern-legal cycling-watch life-gain permanent.
    """
    if not oracle:
        return 0
    lo = oracle.lower()
    if not re.search(r'whenever you cycle\b', lo):
        return 0
    m = re.search(r'you gain\s+(\d+)\s+life', lo)
    return int(m.group(1)) if m else 0


def parse_has_may_play_or_cast(oracle: str) -> bool:
    """Return True for 'may play' or 'may cast' exile-and-play effects.

    Replaces 'may play' in oracle in ai/ev_evaluator.py line 2344 —
    used to estimate card-draw equivalent from exile-top-and-play effects
    (Outpost Siege, Light Up the Stage, Chandra planeswalker abilities, etc.).

    Class size: all exile-and-play or exile-and-cast effects in Modern.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'may play' in lo or 'may cast' in lo


def parse_has_damage_equal_scaling(oracle: str) -> bool:
    """Return True for domain-scaling damage ('deals damage ... equal to ...').

    Replaces re.search(r'deals?.*damage.*equal', oracle) in ai/evaluator.py
    line 477 (Tribal Flames / scaling-damage-spell detection).

    Class size: Tribal Flames and other CDAs or spells where damage = some count.
    """
    if not oracle:
        return False
    import re
    return bool(re.search(r'deals?.*damage.*equal', oracle.lower()))


def parse_has_x_damage(oracle: str) -> bool:
    """Return True for 'deals X damage' spells (X-cost damage).

    Replaces re.search(r'deals?\\s+x\\s+damage', oracle) in ai/evaluator.py
    line 500 (Blaze / Lightning Storm / Rolling Thunder fallback detection).

    Class size: all X-cost direct-damage spells in Modern.
    """
    if not oracle:
        return False
    import re
    return bool(re.search(r'deals?\s+x\s+damage', oracle.lower()))


def parse_has_artifact_pump_equipment(oracle: str) -> bool:
    """Return True for equipment that grants +1/+0 scaling with artifacts.

    Replaces 'artifact' in oracle combined check in engine/card_effects.py
    line 1466 (Cranial Plating / artifact-count-pump equipment detection).

    Class size: Cranial Plating and any future equipment with '+1/+0 for each
    artifact' or 'gets ... for each artifact' power-scaling text.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'artifact' in lo and ('+1/+0' in lo or ('gets' in lo and '+' in lo))


def parse_has_artifact_or_enchantment_scaling(oracle: str) -> bool:
    """Return True for scaling based on artifact and/or enchantment count.

    Replaces 'artifact and/or enchantment' in oracle in ai/ev_player.py
    line 3890 (Nettlecyst / artifact+enchantment combined scaling detection).

    Class size: Nettlecyst and any future cards counting artifacts+enchantments.
    """
    if not oracle:
        return False
    lo = oracle.lower()
    return 'artifact and/or enchantment' in lo or 'artifact or enchantment' in lo


# -- Batch 21 typed fields -------------------------------------------------

def parse_channel_clause(oracle: str) -> str:
    """Extract the channel ability clause from oracle text.

    Returns the substring from 'channel —' or 'channel -' to the end of
    oracle, lowercased.  Returns '' when no channel clause is present.

    Replaces oracle.find('channel —') / oracle.find('channel -') at
    ai/response_enumeration.py:232 (channel target extraction).

    Class size: every channel land — Otawara, Boseiju, Sokenzan, Takenuma,
    Eiganjo, and any future channel cards.
    """
    if not oracle:
        return ''
    lo = oracle.lower()
    idx = max(lo.find("channel —"), lo.find("channel -"))
    if idx < 0:
        return ''
    return lo[idx:]


# ---------------------------------------------------------------------------
# Land destruction (spell tranche) — parse-once typed classification
# (2026-08-27 Dimir root-cause doc, "LD mechanic hole": the destroy-target-
# land clause had no parser coverage, so the whole spell class resolved as
# a silent no-op and Boros Ponza's mainboard LD slots were dead cards.)
#
# Class: every Modern sorcery/instant whose resolution text is
# "Destroy target land" (plain / nonbasic-only / artifact-or-land
# compound) plus zero or more SUPPORTED riders:
#   * "It can't be regenerated."           — no-op (regeneration is not
#                                            simulated; plain destroy is
#                                            strictly faithful)
#   * "Its controller may search their library for a basic land card,
#      put it onto the battlefield [tapped], then shuffle."
#                                          — replacement-basic rider
#                                            (Cleansing Wildfire class)
#   * "[If that land was nonbasic, ]~ deals N damage to the/that land's
#      controller."                        — damage rider (Molten Rain
#                                            class)
#   * "Draw a card."                       — caster-draw rider
#
# Tranche discipline: a card whose LD paragraph carries ANY sentence
# outside this whitelist is REFUSED — parse_land_destruction returns
# None and the card stays unclassified rather than half-executed.
# Activated ("{T}, Sacrifice this land: Destroy …", Ghost Quarter
# class) and triggered ("When this creature enters, destroy …",
# Obsidian Charmaw class) land destruction are a LATER tranche: their
# destroy clause is never sentence-initial, so the anchored sentence
# grammar below refuses them by construction.
# ---------------------------------------------------------------------------

# The destroy clause itself — anchored to a full sentence so trigger /
# activation prefixes ("when … enters, destroy …", "sacrifice …:
# destroy …") never match.
_LD_DESTROY_RE = re.compile(
    r'^destroy target '
    r'(?:(nonbasic) )?(land|artifact or land)'
    r'(?: (an opponent controls))?$'
)

# Supported rider sentences (each anchored to the full sentence).
_LD_RIDER_NOOP_REGEN_RE = re.compile(r"^it can't be regenerated$")
_LD_RIDER_SEARCH_BASIC_RE = re.compile(
    r"^its controller may search their library for a basic land card, "
    r"put (?:it|that card) onto the battlefield( tapped)?, "
    r"then shuffles?(?: their library)?$"
)
_LD_RIDER_DAMAGE_RE = re.compile(
    r"^(if that land was nonbasic, )?[\w\s,'-]+? "
    r"deals (\d+) damage to (?:the|that) land's controller$"
)
_LD_RIDER_DRAW_RE = re.compile(r"^draw a card$")

# Keyword-ability cost lines ("Flashback {3}{R}", "Cycling {2}") are
# not resolution text — they must not trip the unknown-sentence
# refusal. Matched by a leading keyword word followed by a brace cost.
_LD_KEYWORD_COST_LINE_RE = re.compile(r"^[a-z][a-z' ]*(?: ?[—–-])? ?\{")


def parse_land_destruction(oracle: str):
    """Classify a spell-shaped destroy-target-land oracle text.

    Returns None when the text has no sentence-initial destroy-target-
    land clause OR when any sentence of its resolution text falls
    outside the supported-rider whitelist (tranche discipline: refuse,
    never half-execute).  Otherwise returns a dict of typed rider data:

      can_target_artifact       bool — "artifact or land" compound form
      nonbasic_only             bool — target restricted to nonbasic
      opponent_controls_only    bool — target restricted to opponent's
      rider_search_basic        bool — destroyed land's controller
                                       searches a basic onto battlefield
      rider_search_basic_tapped bool — the searched basic enters tapped
      rider_damage              int  — damage to the land's controller
      rider_damage_nonbasic_only bool — damage only if land was nonbasic
      rider_caster_draws        int  — cards the caster draws

    Stored on CardTemplate as `land_destruction_data`
    (`destroys_target_land` is the derived presence flag).
    """
    if not oracle:
        return None
    lower = oracle.lower()
    if 'destroy target' not in lower:
        return None

    data = {
        'can_target_artifact': False,
        'nonbasic_only': False,
        'opponent_controls_only': False,
        'rider_search_basic': False,
        'rider_search_basic_tapped': False,
        'rider_damage': 0,
        'rider_damage_nonbasic_only': False,
        'rider_caster_draws': 0,
    }
    found_destroy = False

    for paragraph in lower.split('\n'):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if _LD_KEYWORD_COST_LINE_RE.match(paragraph):
            continue  # keyword-ability cost line, not resolution text
        for sentence in paragraph.split('.'):
            sentence = sentence.strip()
            if not sentence:
                continue
            m = _LD_DESTROY_RE.match(sentence)
            if m is not None:
                if found_destroy:
                    return None  # multi-destroy form (Boom // Bust) — refuse
                found_destroy = True
                data['nonbasic_only'] = m.group(1) is not None
                data['can_target_artifact'] = (
                    m.group(2) == 'artifact or land')
                data['opponent_controls_only'] = m.group(3) is not None
                continue
            if _LD_RIDER_NOOP_REGEN_RE.match(sentence):
                continue
            ms = _LD_RIDER_SEARCH_BASIC_RE.match(sentence)
            if ms is not None:
                data['rider_search_basic'] = True
                data['rider_search_basic_tapped'] = ms.group(1) is not None
                continue
            md = _LD_RIDER_DAMAGE_RE.match(sentence)
            if md is not None:
                data['rider_damage'] = int(md.group(2))
                data['rider_damage_nonbasic_only'] = md.group(1) is not None
                continue
            if _LD_RIDER_DRAW_RE.match(sentence):
                data['rider_caster_draws'] += 1
                continue
            # Unknown sentence anywhere in the card's resolution text:
            # refuse the whole card (never half-execute a rider).
            return None

    return data if found_destroy else None
