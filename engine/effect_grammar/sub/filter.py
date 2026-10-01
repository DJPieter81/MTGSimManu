"""Filter sub-grammar (design doc 2026-09-29, section 2 ``CardFilter``;
section 5 subjects and untargeted choices; A19, A21, A22 / F5).

Types an untargeted object description, once, at LOAD (never at
resolution), over L0 output, under the one leaf contract in
`engine.effect_grammar.sub` (``(host, span, *, lemma="")``, one
`SlotResult`, host-absolute spans):

* a FILTER subject ("creatures you control", "each other creature");
* the selection of an untargeted choice or search ("a nontoken creature",
  "a basic land card", "up to two land cards");
* the counted object of a quantity ("artifact you control" in "for each
  artifact you control").

A filter is never a target (section 5: TargetRequirements come only from
`target_solver`): a slot that prints "target" as its own determiner is
refused. A player or opponent scope ("each opponent") is the participant
sub-grammar's, not a filter.

**Full consumption (A21).** Every token of the slot must be placed by the
closed tables below; a token the leaf cannot place makes the slot
``UNMODELLED(FILTER)`` with detail ``filter.<code>[:<word>]``. The leaf never
returns a broader filter that drops a qualifier.

**Determiners** are the selection, not the filter: a count is
`SlotResult.amount` ("a" / "one" -> 1, "up to two" -> UP_TO 2, "any number
of" -> ANY_NUMBER); "each" / "every" and "all" are the ``each`` / ``all``
flags; "another" is a count of one plus ``other``.

**Closed tables.**

* card types, supertypes and subtypes (CR 205.2-205.3; the subtype table is
  the CR 205.3 vocabulary of the non-planeswalker types, as printed in the
  MTGJSON type lines of the pool plus the CR 111.10 predefined token
  types). Adjacent card types are a conjunction ("artifact creature",
  ``all_types``); a list joined by "or" / "and" is a union (``types``);
  "non<type>" is an exclusion;
* descriptor scope: a CardFilter has one set of descriptor fields, so a
  union is typed only when its members print the same descriptors or all
  of them precede the leading member's first noun ("legendary creature or
  planeswalker"); a descriptor on one member only is that member's rule
  and the slot is refused (``filter.modifier_scope``);
* descriptor coordination: ``colors`` and ``state`` are disjunctive (any
  listed value: "black or red", "attacking or blocking"), every other set
  field is conjunctive (every listed value: "noncreature, nonland",
  "legendary snow"). Descriptors joined by "or" / "and/or" are a union of
  one disjunctive field; stacked, comma- or "and"-joined descriptors are a
  conjunction. A phrase that needs the meaning its field lacks ("white and
  blue", "untapped attacking", "noncreature or nonland", "multicolored or
  colorless") is refused (``filter.modifier_join``);
* colours (CR 105.1) as mana letters, ``colorless``, and the derived
  classes ``historic`` (CR 700.6), ``colored`` ("one or more colors"),
  ``multicolored`` and ``monocolored`` (CR 105.2, A19);
* object states (CR 110.5, 506.4): tapped, untapped, attacking, blocking,
  blocked, unblocked, face-down, face-up, modified;
* controller and owner clauses ("you control", "your opponents control",
  "target player controls", ...); an anaphoric player ("they", "that
  player", "its owner's") is left to the linker in ``pending``;
* zones ("in your graveyard", "from exile", "from among them");
* characteristic bounds ("with mana value 3 or less", CR 202.3, 208);
* keyword qualifiers ("with flying", "without first strike", CR 702) typed
  as `cards.Keyword` values ('first_strike'), the value
  `Selector.covers_object` compares (F5 / A22);
* counter qualifiers ("with a +1/+1 counter on it"), read through the one
  counter noun-phrase parser in `payload` (one count table, one kind
  vocabulary);
* names ("named ⟨nk⟩", "named ~", "with different names") and "other than
  ~".

The leaf reads no card name and no game state.
"""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from engine.effect_grammar.sub import (CACHE_SIZE, SlotResult, Span,
                                       rest_spans_after, unmodelled)
from engine.effect_grammar.sub import payload as _payload
from engine.effect_spec import (Amount, AmountKind, CardFilter, Ref, RefKind,
                                Stage, Unmodelled)
from engine.target_solver import _NUMBER_WORDS

__all__ = ["LEAF", "DETAIL_CODES", "parse_filter", "CARD_TYPES",
           "PERMANENT_TYPES", "SUPERTYPES", "SUBTYPES", "CLASSES", "STATES",
           "QUALIFIER_KEYWORDS", "EACH", "ALL", "clear_caches"]

LEAF = "filter"
DETAIL_CODES = frozenset({
    "empty", "unparsed", "no_head", "targeted", "reference", "player_head",
    "card_zone", "zone_union", "type_mix", "subtype_conjunction", "np_union",
    "keyword", "keyword_list", "stat", "counter", "that_clause",
    "modifier_scope", "modifier_join"})

EACH = "each"      # SlotResult flag: "each" / "every" quantifies the filter
ALL = "all"        # SlotResult flag: "all" quantifies the filter


# ── Closed tables ──────────────────────────────────────────────────────

CARD_TYPES = frozenset({"artifact", "creature", "enchantment", "land",
                        "planeswalker", "battle", "instant", "sorcery",
                        "kindred"})
# CR 205.3m: "tribal" is the pre-2024 printing of kindred.
_TYPE_ALIASES = {"tribal": "kindred"}
SUPERTYPES = frozenset({"legendary", "basic", "snow", "world"})
# CR 205.3 subtypes of every card type but planeswalker (the pool's MTGJSON
# type lines, the CR 111.10 predefined token types and the CR 205.3m
# creature types the pool names only on tokens).
SUBTYPES = frozenset({
    'adventure', 'advisor', 'aetherborn', 'alien', 'ally', 'angel',
    'antelope', 'ape', 'arcane', 'archer', 'archon', 'armadillo',
    'artificer', 'assassin', 'assembly-worker', 'atog', 'attraction',
    'aura', 'aurochs', 'avatar', 'azra', 'background', 'badger',
    'barbarian', 'bard', 'basilisk', 'bat', 'bear', 'beast', 'beaver',
    'beholder', 'berserker', 'bird', 'bison', 'blood', 'boar', 'book',
    'bringer', 'brushwagg', "c'tan", 'camel', 'capybara', 'carrier',
    'cartouche', 'case', 'cat', 'cave', 'centaur', 'chimera', 'citizen',
    'class', 'cleric', 'clown', 'clue', 'cockatrice', 'construct',
    'contraption', 'coward', 'coyote', 'crab', 'crocodile', 'curse',
    'cyclops', 'dauthi', 'demigod', 'demon', 'desert', 'detective', 'devil',
    'dinosaur', 'djinn', 'doctor', 'dog', 'dragon', 'drake', 'dreadnought',
    'drix', 'drone', 'druid', 'dryad', 'dwarf', 'efreet', 'egg', 'elder',
    'eldrazi', 'elemental', 'elephant', 'elf', 'elk', 'equipment',
    'eternal', 'eye', 'faerie', 'fish', 'food', 'forest', 'fortification',
    'fox', 'fractal', 'frog', 'fungus', 'gamma', 'gargoyle', 'gate', 'germ',
    'giant', 'giraffe', 'glimmer', 'gnoll', 'gnome', 'goat', 'goblin',
    'god', 'gold', 'golem', 'gorgon', 'gremlin', 'griffin', 'hag',
    'halfling', 'hamster', 'harpy', 'hellion', 'hero', 'hippo',
    'hippogriff', 'homarid', 'homunculus', 'horror', 'horse', 'human',
    'hydra', 'hyena', 'illusion', 'imp', 'incarnation', 'incubator',
    'infinity', 'inhuman', 'inkling', 'insect', 'island', 'jackal',
    'jellyfish', 'juggernaut', 'junk', 'kangaroo', 'kavu', 'kirin',
    'kithkin', 'knight', 'kobold', 'kor', 'kraken', 'kree', 'lamia',
    'lammasu', 'lander', 'leech', 'lemur', 'lesson', 'leviathan',
    'lhurgoyf', 'lizard', 'lobster', 'locus', 'manticore', 'map',
    'masticore', 'mercenary', 'merfolk', 'metathran', 'mine', 'minion',
    'minotaur', 'mite', 'mole', 'mongoose', 'monk', 'monkey', 'moogle',
    'moonfolk', 'mount', 'mountain', 'mouse', 'mutagen', 'mutant', 'myr',
    'mystic', 'nautilus', 'nephilim', 'nightmare', 'ninja', 'noble',
    'noggle', 'nomad', 'nymph', 'octopus', 'ogre', 'omen', 'ooze', 'orc',
    'orgg', 'otter', 'ouphe', 'ox', 'oyster', 'pangolin', 'peasant',
    'pegasus', 'pentavite', 'performer', 'pest', 'phoenix', 'phyrexian', 'pilot',
    'pirate', 'plains', 'plan', 'planet', 'plant', 'platypus', 'porcupine',
    'possum', 'power-plant', 'powerstone', 'praetor', 'processor', 'qu',
    'rabbit', 'raccoon', 'ranger', 'rat', 'rebel', 'reflection', 'rhino',
    'rigger', 'robot', 'rogue', 'role', 'room', 'rune', 'sable', 'saga',
    'salamander', 'samurai', 'sand', 'saproling', 'satyr', 'scarecrow',
    'scientist', 'scion', 'scorpion', 'scout', 'seal', 'serf', 'serpent',
    'servo',
    'shade', 'shaman', 'shapeshifter', 'shard', 'shark', 'sheep', 'shrine',
    'siege', 'siren', 'skeleton', 'skrull', 'skunk', 'slith', 'sliver',
    'sloth', 'slug', 'snail', 'snake', 'soldier', 'soltari', 'sorcerer',
    'spacecraft', 'spawn', 'specter', 'spellshaper', 'sphere', 'sphinx',
    'spider', 'spike', 'spirit', 'spy', 'squid', 'squirrel', 'starfish',
    'stone', 'surrakar', 'survivor', 'swamp', 'symbiote', 'thopter',
    'thrull', 'tiefling', 'tower', 'town', 'toy', 'trap', 'treasure',
    'treefolk', 'trilobite', 'troll', 'turtle', 'unicorn', "urza's",
    'utrom', 'vampire', 'varmint', 'vedalken', 'vehicle', 'villain',
    'walker', 'wall', 'warlock', 'warrior', 'weasel', 'weird', 'werewolf',
    'whale', 'wizard', 'wolf', 'wolverine', 'wombat', 'worm', 'wraith',
    'wurm', 'yeti', 'zombie', 'zubera'})
_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
# Derived classes (A19): closed, CR-defined.
CLASSES = frozenset({"historic", "colored", "multicolored", "monocolored"})
STATES = frozenset({"tapped", "untapped", "attacking", "blocking", "blocked",
                    "unblocked", "face-down", "face-up", "modified"})
# CR 110.4b: a permanent card is an artifact, creature, enchantment, land,
# planeswalker or battle card.
PERMANENT_TYPES = frozenset({"artifact", "creature", "enchantment", "land",
                             "planeswalker", "battle"})
# Head nouns that carry no type: where the object is.
_HEADS = {"permanent": "battlefield", "card": None, "spell": "stack"}
# CR 702 keywords a filter qualifier names ("with flying", "without
# first strike"). Typed as `cards.Keyword` value spelling (spaces -> '_').
QUALIFIER_KEYWORDS = frozenset({
    "flying", "first strike", "double strike", "deathtouch", "lifelink",
    "trample", "haste", "vigilance", "reach", "menace", "defender",
    "hexproof", "shroud", "indestructible", "flash", "prowess", "infect",
    "wither", "toxic", "changeling", "decayed", "shadow", "fear",
    "intimidate", "horsemanship", "skulk", "flanking", "protection", "ward",
    "islandwalk", "swampwalk", "forestwalk", "mountainwalk", "plainswalk",
    "landwalk", "flashback", "cycling", "disturb", "foretell", "crew",
    "modular", "convoke", "cascade", "storm", "affinity", "undying",
    "persist", "unearth", "evoke", "suspend", "annihilator", "improvise",
    "kicker", "madness", "morph", "mutate", "bushido", "ninjutsu",
    "partner", "devoid", "exalted", "afterlife", "riot", "equip", "echo",
    "escape", "embalm", "eternalize", "dash", "blitz", "bestow", "delve",
    "split second", "rebound", "retrace", "encore", "ravenous"})

# Words a filter slot may open with that name something already known (a
# reference, CR 608.2b) rather than describe a set.
_REFERENCE_WORDS = frozenset({"it", "them", "that", "those", "this", "these",
                              "its", "their", "~", "the", "enchanted",
                              "equipped", "fortified", "such"})
_PLAYER_NOUNS = frozenset({"player", "opponent", "players", "opponents"})

_WORD_COUNTS: Dict[str, int] = dict(_NUMBER_WORDS, a=1, an=1)
_N = r"(?:%s|\d+|x)" % "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_X_UNIT = Amount(AmountKind.X, n=1)
_THAT_MUCH = Amount(AmountKind.THAT_MUCH)


def _number(word: str) -> Optional[Amount]:
    if word == "x":
        return _X_UNIT
    if word.isdigit():
        return Amount(AmountKind.LITERAL, n=int(word))
    n = _WORD_COUNTS.get(word)
    return None if n is None else Amount(AmountKind.LITERAL, n=n)


# ── Words ──────────────────────────────────────────────────────────────

def _singulars(word: str) -> Tuple[str, ...]:
    """The word and the singular forms its plural could stand for."""
    out = [word]
    if word.endswith("ies"):
        out.append(word[:-3] + "y")
    if word.endswith("ves"):
        out += [word[:-3] + "f", word[:-3] + "fe"]
    if word.endswith("es"):
        out.append(word[:-2])
    if word.endswith("s"):
        out.append(word[:-1])
    return tuple(out)


def _lookup(word: str, table) -> Optional[str]:
    for w in _singulars(word):
        w = _TYPE_ALIASES.get(w, w)
        if w in table:
            return w
    return None


def _classify(word: str) -> Optional[Tuple[str, Any]]:
    """(kind, value) of one descriptor word, or None outside the tables."""
    if word == "other":
        return ("other", True)
    if word in ("nontoken", "non-token"):
        return ("token", False)
    if word in _COLORS:
        return ("color", _COLORS[word])
    if word == "colorless":
        return ("colorless", True)
    if word in CLASSES:
        return ("class", word)
    if word in SUPERTYPES:
        return ("super", word)
    if word in STATES:
        return ("state", word)
    if word in ("token", "tokens"):
        return ("token", True)
    head = _lookup(word, _HEADS)
    if head is not None:
        return ("head", head)
    t = _lookup(word, CARD_TYPES)
    if t is not None:
        return ("type", t)
    s = _lookup(word, SUBTYPES)
    if s is not None:
        return ("subtype", s)
    if word in _PLAYER_NOUNS:
        return ("player", word)
    m = re.match(r"non-?(?P<inner>.+)$", word)
    if m:
        inner = m.group("inner")
        if inner in _COLORS:
            return ("not_color", _COLORS[inner])
        if inner in SUPERTYPES:
            return ("not_super", inner)
        t = _lookup(inner, CARD_TYPES)
        if t is not None:
            return ("not_type", t)
        s = _lookup(inner, SUBTYPES)
        if s is not None:
            return ("not_subtype", s)
    return None


# ── Determiners and connectors ─────────────────────────────────────────

_DET_RE = re.compile(
    r"(?:(?P<q>all|each|every) "
    r"|up to (?P<upto>%s|that many) "
    r"|(?P<thatmany>that many) "
    r"|(?P<anynum>any number of) "
    r"|(?P<another>another) "
    r"|(?P<count>an|a|%s) )" % (_N, _N))
_CONN_RE = re.compile(r"(?:,? (?:and/or|or|and) |, )")
_WORD_RE = re.compile(r"[^\s,]+")


def _determiner(t: str) -> Tuple[int, Optional[Amount], FrozenSet[str], bool]:
    """(end, amount, flags, other) of a leading determiner."""
    m = _DET_RE.match(t)
    if m is None:
        return 0, None, frozenset(), False
    if m.group("q"):
        return m.end(), None, frozenset({ALL if m.group("q") == "all" else EACH}), False
    if m.group("thatmany"):
        return m.end(), _THAT_MUCH, frozenset(), False
    if m.group("upto"):
        n = _THAT_MUCH if m.group("upto") == "that many" else _number(m.group("upto"))
        if n is not None and n.kind is AmountKind.LITERAL:
            return m.end(), Amount(AmountKind.UP_TO, n=n.n), frozenset(), False
        return m.end(), Amount(AmountKind.UP_TO, inner=n), frozenset(), False
    if m.group("anynum"):
        return m.end(), Amount(AmountKind.ANY_NUMBER), frozenset(), False
    if m.group("another"):
        return m.end(), Amount(AmountKind.LITERAL, n=1), frozenset(), True
    return m.end(), _number(m.group("count")), frozenset(), False


# ── Postmodifiers ──────────────────────────────────────────────────────

_WHO = (r"you|your opponents|opponents|an opponent|each opponent|target player"
        r"|target opponent|that player|that opponent|they|he or she"
        r"|its controller|its owner|defending player|a player|each player"
        r"|another player|the chosen player")
_PLAYER_CLAUSE_RE = re.compile(
    r"(?P<who>%s) (?P<neg>don't |doesn't )?(?P<verb>control|own)s?(?![\w'])"
    % _WHO)
_POSS = (r"your|their|his or her|that player's|target player's"
         r"|target opponent's|an opponent's|each opponent's|each player's"
         r"|a player's|its owner's|its controller's|your opponents'"
         r"|opponents'|defending player's|that opponent's|a single|all|any|a")
_ZONE_WORD = r"graveyards?|hands?|librar(?:y|ies)"
_ZONE_RE = re.compile(
    r"(?:from|in) (?:(?P<exile>exile)|(?:(?P<poss>%s) )?(?P<zone>%s)"
    r"(?P<more>(?:,? (?:and/or|or|and) (?:(?:%s) )?(?:%s|exile))+)?)(?![\w'])"
    % (_POSS, _ZONE_WORD, _POSS, _ZONE_WORD))
_BATTLEFIELD_RE = re.compile(r"on the battlefield(?![\w'])")
_AMONG_RE = re.compile(
    r"from among (?P<among>them|those cards|the revealed cards"
    r"|the exiled cards|the milled cards|the chosen cards"
    r"|the cards revealed this way|the cards exiled this way"
    r"|cards exiled with ~)(?![\w'])")
_STAT_RE = re.compile(
    r"with (?P<stat>power|toughness|mana value) "
    r"(?:(?P<n>%s) or (?P<cmp>less|greater|more)"
    r"|(?P<cmp2>less|greater) than (?P<eq>or equal to )?(?P<n2>%s)"
    r"|(?P<n3>%s))(?![\w/'])" % (_N, _N, _N))
_STAT_SHAPE_RE = re.compile(
    r"with (?:(?:total |base |the greatest |the least |converted )?"
    r"(?:power|toughness|mana value|mana cost)|an? (?:even|odd|lesser|greater)"
    r"|lesser|greater)(?![\w'])")
_COUNTER_RE = re.compile(
    r"with(?P<out>out)? (?:(?P<q>a|an|one or more|no|any) )?"
    r"(?P<np>(?:[+-]\d+/[+-]\d+ |[a-z][a-z'\-]* )?counters?) on (?:it|them)(?![\w'])")
_KW_ALT = "|".join(re.escape(k) for k in sorted(QUALIFIER_KEYWORDS, key=len,
                                                 reverse=True))
_KEYWORD_RE = re.compile(
    r"with(?P<out>out)? (?P<kw>%s)(?P<more>(?:,? (?:and|or) (?:%s))*)(?![\w'])"
    % (_KW_ALT, _KW_ALT))
# A "with <word>" that ends the phrase has the shape of a keyword qualifier.
_KEYWORD_SHAPE_RE = re.compile(
    r"with(?:out)? (?P<w>[a-z][a-z'\-]*)(?=$|,| or | and )")
_DIFFERENT_NAMES_RE = re.compile(r"with different names(?![\w'])")
_NAMED_RE = re.compile(r"(?:named )?(?P<mask>⟨n\d+⟩)|named (?P<self>~)(?![\w'])")
_OTHER_THAN_RE = re.compile(r"other than ~(?![\w'])")
# Relations to an object the ability already knows; the linker binds the
# object (CR 301.5 attachment, CR 607 linked exile, CR 700.2 choices).
_OBJ = r"~|it|them|that creature|that permanent|enchanted creature|equipped creature"
_ATTACHED_RE = re.compile(r"attached to (?P<to>%s)(?![\w'])" % _OBJ)
_EXILED_WITH_RE = re.compile(r"exiled with (?P<w>%s)(?![\w'])" % _OBJ)
# "<verb>ed this way": the filtered set is a RESULT of an earlier spec in the
# ability (a quantity's RESULT_SIZE, section 6); the linker binds it.
_THIS_WAY_RE = re.compile(
    r"(?P<v>exiled|discarded|destroyed|sacrificed|returned|revealed|milled"
    r"|tapped|untapped|chosen|drawn|countered|put into (?:your|their|a|its "
    r"owner's|that player's) (?:hand|graveyard))(?: to (?:your|their|its "
    r"owner's) hand)? this way(?![\w'])")
_CHOSEN_RE = re.compile(r"of the chosen (?P<what>creature type|type|color)(?![\w'])")
_COLOR_ALT = "|".join(_COLORS)
_THAT_RE = re.compile(
    r"that(?:'s| is| are) (?:(?P<colored>one or more colors)"
    r"|(?P<colorless>colorless)"
    r"|(?P<cols>(?:%s)(?:(?:,? or |, )(?:%s))*)"
    r"|(?P<state>tapped|untapped|attacking|blocking)"
    r"|(?:a|an) (?P<subs>[a-z][a-z'\-]*(?:(?:,? or |, )[a-z][a-z'\-]*)*))"
    r"(?![\w'])" % (_COLOR_ALT, _COLOR_ALT))
_LIST_SEP_RE = re.compile(r",? or |, ")

_OPPONENTS = frozenset({"your opponents", "opponents", "an opponent",
                        "each opponent", "an opponent's", "each opponent's",
                        "your opponents'", "opponents'"})
_ANY_PLAYER = frozenset({"a player", "each player", "each player's",
                         "a player's", "all", "any", "a", "a single"})
_ANAPHORIC = frozenset({"that player", "that opponent", "they", "he or she",
                        "its controller", "its owner", "the chosen player",
                        "their", "his or her", "that player's", "its owner's",
                        "its controller's", "that opponent's"})


def _player(who: str, neg: bool):
    """(value, pending text, ok) of a controller / owner / possessor."""
    if who in ("you", "your"):
        return ("not_you" if neg else "you"), None, True
    if neg:
        return None, None, False
    if who in _OPPONENTS:
        return "opponents", None, True
    if who in ("another player",):
        return "not_you", None, True
    if who.startswith("target "):
        return Ref(RefKind.TARGET, noun=who.split()[1].rstrip("'s")), None, True
    if who.startswith("defending player"):
        return Ref(RefKind.DEFENDING_PLAYER), None, True
    if who in _ANY_PLAYER:
        return "any", None, True
    if who in _ANAPHORIC:
        return "any", who, True
    return None, None, False


def _zone_name(word: str) -> str:
    return {"graveyards": "graveyard", "hands": "hand",
            "libraries": "library"}.get(word, word)


# ── The parse ──────────────────────────────────────────────────────────

# Descriptor fields. A CardFilter has one value set per field, so each field
# has ONE meaning for several values: a DISJUNCTIVE field matches an object
# with any listed value ("black or red", "attacking or blocking"); every
# other set field is CONJUNCTIVE, every listed value holds ("noncreature,
# nonland" is neither, "legendary snow" is both). A phrase that needs the
# other meaning is refused (``filter.modifier_join``), never typed as the
# field's meaning.
_DISJUNCTIVE = frozenset({"colors", "state"})
_SET_FIELDS = ("colors", "not_colors", "classes", "supertypes",
               "not_supertypes", "not_types", "not_subtypes", "state")
# descriptor kind -> CardFilter field
_FIELD = {"color": "colors", "not_color": "not_colors", "class": "classes",
          "super": "supertypes", "not_super": "not_supertypes",
          "state": "state", "not_type": "not_types",
          "not_subtype": "not_subtypes", "colorless": "colorless",
          "token": "token", "other": "other"}


@dataclasses.dataclass
class _Mod:
    """One descriptor word: its field and value, the connector printed
    before it inside a coordination of descriptors ('' when stacked), and
    whether it precedes the member's first type or head noun."""
    field: str
    value: Any
    join: str
    pre: bool


@dataclasses.dataclass
class _Item:
    types: List[str] = dataclasses.field(default_factory=list)
    subtypes: List[str] = dataclasses.field(default_factory=list)
    heads: List[str] = dataclasses.field(default_factory=list)
    token: bool = False
    mods: List[_Mod] = dataclasses.field(default_factory=list)
    join: str = ""      # a connector read after a descriptor, not yet used

    def content(self) -> bool:
        return bool(self.types or self.subtypes or self.heads or self.token)


def _descriptors(mods: List[_Mod]) -> Optional[Dict[str, Any]]:
    """The descriptor fields of one union member, or None when its
    coordination needs the meaning its field does not have. Descriptors
    linked by connectors form one coordination: 'or' / 'and/or' is a union
    of ONE disjunctive field; 'and' and bare commas are a conjunction."""
    out: Dict[str, Any] = {}
    chains: List[List[_Mod]] = []
    for m in mods:
        if m.join and chains:
            chains[-1].append(m)
        else:
            chains.append([m])
    for chain in chains:
        joins = {m.join for m in chain[1:]}
        disjoint = bool(joins & {"or", "and/or"})
        if disjoint and "and" in joins:
            return None
        fields = {m.field for m in chain}
        if disjoint and (len(fields) != 1 or not fields <= _DISJUNCTIVE):
            return None
        for field in fields:
            vals = {m.value for m in chain if m.field == field}
            if field in _SET_FIELDS:
                if field in _DISJUNCTIVE and (
                        field in out or (len(vals) > 1 and not disjoint)):
                    return None        # a conjunction of disjunctive values
                out[field] = frozenset(out.get(field, frozenset()) | vals)
            else:
                out[field] = next(iter(vals)) if len(vals) == 1 else None
                if out[field] is None:
                    return None
    return out


def _shared_descriptors(content: List[_Item]):
    """(descriptors, failure code) of a union: the members' descriptors
    when every member prints the same ones, or the leading member's when
    they all precede its first noun ("legendary creature or planeswalker",
    "nontoken creature or planeswalker") and no other member prints any.
    A descriptor on one member only is that member's rule, which one
    CardFilter cannot state (A21)."""
    sigs = []
    for item in content:
        d = _descriptors(item.mods)
        if d is None:
            return None, "modifier_join"
        sigs.append(d)
    if len(content) == 1 or all(d == sigs[0] for d in sigs[1:]):
        return sigs[0], None
    if not any(sigs[1:]) and all(m.pre for m in content[0].mods):
        return sigs[0], None
    return None, "modifier_scope"


# A relative parse: (value, (code, param) | None, amount, flags, pending).
_Rel = Tuple[Optional[CardFilter], Optional[Tuple[str, str]], Optional[Amount],
             FrozenSet[str], Tuple[Tuple[str, str], ...]]


def _fail(code: str, param: str = "") -> _Rel:
    return (None, (code, param), None, frozenset(), ())


def _first_word(t: str, pos: int) -> str:
    m = _WORD_RE.search(t, pos)
    return m.group(0) if m else ""


@lru_cache(maxsize=CACHE_SIZE)
def _filter_rel(t: str, zone: str) -> _Rel:
    if not t:
        return _fail("empty")
    pos, amount, flags, other = _determiner(t)
    items = [_Item()]
    pending: List[Tuple[str, str]] = []

    # Descriptor words: premodifiers and head nouns, in union members
    # separated by connectors; a connector after a descriptor coordinates
    # descriptors inside the member. The first word no table places starts
    # the postmodifiers.
    first = True
    while pos < len(t):
        m = _WORD_RE.match(t, pos)
        if m is None:
            return _fail("unparsed", t[pos:pos + 1])
        word = m.group(0)
        if first and word == "target":
            return _fail("targeted")
        if first and word in _REFERENCE_WORDS:
            return _fail("reference", word)
        if word in ("enchanted", "equipped", "fortified"):
            return _fail("reference", word)
        if not first and (_PLAYER_CLAUSE_RE.match(t, pos)
                          or _OTHER_THAN_RE.match(t, pos)):
            break
        c = _classify(word)
        if c is None:
            if first:
                return _fail("unparsed", word)
            break
        kind, value = c
        if kind == "player":
            return _fail("player_head")
        first = False
        item = items[-1]
        descriptor = kind not in ("head", "type", "subtype") and not (
            kind == "token" and value)
        if descriptor:
            item.mods.append(_Mod(_FIELD[kind], value, item.join,
                                  not item.content()))
        elif item.join:
            return _fail("modifier_join")   # "tapped or creature"
        elif kind == "token":               # "creature token": a head noun
            item.token = True
            item.mods.append(_Mod("token", True, "", False))
        elif kind == "head":
            item.heads.append(value)
        elif kind == "type":
            item.types.append(value)
        else:
            item.subtypes.append(value)
        item.join = ""
        pos = m.end()
        conn = _CONN_RE.match(t, pos)
        if conn is None:
            if pos < len(t) and t[pos] == " ":
                pos += 1
            continue
        nxt = _WORD_RE.match(t, conn.end())
        if nxt is None:
            return _fail("unparsed", conn.group(0).strip(" ,"))
        if _DET_RE.match(t, conn.end()):
            return _fail("np_union")
        if _classify(nxt.group(0)) is None or _PLAYER_CLAUSE_RE.match(t, conn.end()):
            return _fail("unparsed", conn.group(0).strip(" ,") or ",")
        if descriptor and not item.content():
            # "white or blue", "noncreature, nonland": descriptors of one
            # member, coordinated.
            item.join = conn.group(0).strip(" ,") or ","
        else:                           # after a noun: a new union member
            items.append(_Item())
        pos = conn.end()

    content = [i for i in items if i.content()]
    if not content or len(content) != len(items):
        return _fail("no_head")
    shared, code = _shared_descriptors(content)
    if shared is None:
        return _fail(code)
    f: Dict[str, Any] = {k: set(shared.get(k, ())) for k in _SET_FIELDS}
    f.update(with_keywords=set(), without_keywords=set())
    token: Optional[bool] = shared.get("token")
    colorless: Optional[bool] = shared.get("colorless")
    other = other or bool(shared.get("other"))

    # Postmodifiers, in any order, until the slot is consumed.
    controller: Any = "any"
    owner: Any = "any"
    zone_phrase: Optional[str] = None
    stat_bounds: List[Tuple[str, str, Amount]] = []
    counters: List[Tuple[str, bool]] = []
    named: Any = None
    different_names = False
    that_subtypes: Tuple[str, ...] = ()
    while pos < len(t):
        m = _PLAYER_CLAUSE_RE.match(t, pos)
        if m:
            value, anaphor, ok = _player(m.group("who"), bool(m.group("neg")))
            if not ok:
                return _fail("unparsed", m.group("who").split()[0])
            role = "controller" if m.group("verb") == "control" else "owner"
            if role == "controller":
                controller = value
            else:
                owner = value
            if anaphor:
                pending.append((role, anaphor))
        elif _ZONE_RE.match(t, pos):
            m = _ZONE_RE.match(t, pos)
            if m.group("more"):
                return _fail("zone_union")
            if m.group("exile"):
                zone_phrase = "exile"
            else:
                zone_phrase = _zone_name(m.group("zone"))
                poss = m.group("poss")
                if poss is not None:
                    value, anaphor, ok = _player(poss, False)
                    if not ok:
                        return _fail("unparsed", poss.split()[0])
                    owner = value
                    if anaphor:
                        pending.append(("owner", anaphor))
                    if poss == "a single":
                        pending.append(("single_zone", zone_phrase))
        elif _BATTLEFIELD_RE.match(t, pos):
            m = _BATTLEFIELD_RE.match(t, pos)
            zone_phrase = "battlefield"
        elif _AMONG_RE.match(t, pos):
            m = _AMONG_RE.match(t, pos)
            zone_phrase = zone
            pending.append(("among", m.group("among")))
        elif _STAT_RE.match(t, pos):
            m = _STAT_RE.match(t, pos)
            stat = m.group("stat").replace(" ", "_")
            if m.group("n") is not None:
                op = "<=" if m.group("cmp") == "less" else ">="
                n = _number(m.group("n"))
            elif m.group("n2") is not None:
                less = m.group("cmp2") == "less"
                if m.group("eq"):
                    op = "<=" if less else ">="
                else:
                    op = "<" if less else ">"
                n = _number(m.group("n2"))
            else:
                op, n = "==", _number(m.group("n3"))
            stat_bounds.append((stat, op, n))
        elif _STAT_SHAPE_RE.match(t, pos):
            return _fail("stat")
        elif _COUNTER_RE.match(t, pos):
            m = _COUNTER_RE.match(t, pos)
            r = _payload.parse_counters(t, m.span("np"))
            if r.value is None or r.rest_spans or len(set(r.value.kinds)) != 1:
                return _fail("counter")
            has = not (m.group("out") or m.group("q") == "no")
            counters.append((r.value.kinds[0], has))
        elif _DIFFERENT_NAMES_RE.match(t, pos):
            m = _DIFFERENT_NAMES_RE.match(t, pos)
            different_names = True
        elif _KEYWORD_RE.match(t, pos):
            m = _KEYWORD_RE.match(t, pos)
            if m.group("more"):
                return _fail("keyword_list")
            kw = m.group("kw").replace(" ", "_")
            f["without_keywords" if m.group("out") else "with_keywords"].add(kw)
        elif _KEYWORD_SHAPE_RE.match(t, pos):
            return _fail("keyword", _KEYWORD_SHAPE_RE.match(t, pos).group("w"))
        elif _NAMED_RE.match(t, pos):
            m = _NAMED_RE.match(t, pos)
            named = m.group("mask") or Ref(RefKind.SELF)
        elif _OTHER_THAN_RE.match(t, pos):
            m = _OTHER_THAN_RE.match(t, pos)
            other = True
        elif _ATTACHED_RE.match(t, pos):
            m = _ATTACHED_RE.match(t, pos)
            pending.append(("attached_to", m.group("to")))
        elif _EXILED_WITH_RE.match(t, pos):
            m = _EXILED_WITH_RE.match(t, pos)
            zone_phrase = "exile"
            pending.append(("exiled_with", m.group("w")))
        elif _THIS_WAY_RE.match(t, pos):
            m = _THIS_WAY_RE.match(t, pos)
            if zone_phrase is None:
                zone_phrase = zone
            pending.append(("result", m.group("v")))
        elif _CHOSEN_RE.match(t, pos):
            m = _CHOSEN_RE.match(t, pos)
            pending.append(("chosen", m.group("what")))
        elif _THAT_RE.match(t, pos):
            m = _THAT_RE.match(t, pos)
            if m.group("colored"):
                f["classes"].add("colored")
            elif m.group("colorless"):
                colorless = True
            elif m.group("cols"):
                if f["colors"]:     # a second colour set: a conjunction
                    return _fail("modifier_join")
                f["colors"].update(_COLORS[c] for c in _LIST_SEP_RE.split(m.group("cols")))
            elif m.group("state"):
                if f["state"]:      # a second state: a conjunction
                    return _fail("modifier_join")
                f["state"].add(m.group("state"))
            else:
                subs = [_lookup(w, SUBTYPES) for w in _LIST_SEP_RE.split(m.group("subs"))]
                if None in subs or that_subtypes:
                    return _fail("that_clause")
                that_subtypes = tuple(subs)
        else:
            return _fail("unparsed", _first_word(t, pos))
        pos = m.end()
        if pos < len(t):
            if t[pos] != " ":
                return _fail("unparsed", _first_word(t, pos) or t[pos])
            pos += 1

    return _build(t, zone, zone_phrase, content, f, token, colorless, other,
                  controller, owner, stat_bounds, counters, named,
                  different_names, amount, flags, pending, that_subtypes)


def _build(t, zone, zone_phrase, content, f, token, colorless, other,
           controller, owner, stat_bounds, counters, named, different_names,
           amount, flags, pending, that_subtypes=()) -> _Rel:
    if that_subtypes:
        # "that's a <subtype list>": a union constraint on the one head
        # ("each other creature you control that's a cat, ... or beast").
        if len(content) != 1 or content[0].subtypes:
            return _fail("that_clause")
        content = [_Item(types=content[0].types, heads=content[0].heads,
                         token=content[0].token)]
    kinds = set()
    for i in content:
        if i.types and i.subtypes:
            kinds.add("both")
        elif i.types:
            kinds.add("types")
        elif i.subtypes:
            kinds.add("subtypes")
    if len(content) > 1 and (len(kinds) > 1 or "both" in kinds):
        return _fail("type_mix")
    types: FrozenSet[str] = frozenset()
    all_types: FrozenSet[str] = frozenset()
    if len(content) == 1:
        if len(content[0].types) > 1:
            all_types = frozenset(content[0].types)
        else:
            types = frozenset(content[0].types)
        if len(content[0].subtypes) > 1:
            return _fail("subtype_conjunction")
    else:
        if any(len(i.types) > 1 for i in content):
            return _fail("type_mix")
        types = frozenset(x for i in content for x in i.types)
        if any(len(i.subtypes) > 1 for i in content):
            return _fail("subtype_conjunction")
    subtypes = frozenset(x for i in content for x in i.subtypes) | frozenset(
        that_subtypes)
    heads = {h for i in content for h in i.heads}
    if heads == {"permanent", "card"}:
        # "permanent card" (CR 110.4b): the union of the permanent types.
        if types or all_types:
            return _fail("type_mix")
        types, heads = PERMANENT_TYPES, {"card"}
    if len(heads) > 1:
        return _fail("type_mix")

    if zone_phrase is not None:
        where = zone_phrase
    elif zone:
        where = zone
    elif "card" in heads:
        return _fail("card_zone")
    elif "spell" in heads:
        where = "stack"
    else:
        where = "battlefield"
    if "card" in heads and where == "battlefield":
        return _fail("card_zone")

    value = CardFilter(
        zone=where, types=types, all_types=all_types,
        not_types=frozenset(f["not_types"]),
        supertypes=frozenset(f["supertypes"]),
        not_supertypes=frozenset(f["not_supertypes"]),
        subtypes=subtypes, not_subtypes=frozenset(f["not_subtypes"]),
        colors=frozenset(f["colors"]), not_colors=frozenset(f["not_colors"]),
        classes=frozenset(f["classes"]), colorless=colorless,
        controller=controller, owner=owner, other=other, token=token,
        stat_bounds=tuple(stat_bounds),
        with_keywords=frozenset(f["with_keywords"]),
        without_keywords=frozenset(f["without_keywords"]),
        state=frozenset(f["state"]), counters=tuple(counters), named=named,
        different_names=different_names, raw=t)
    return (value, None, amount, flags, tuple(pending))


def _um(code: str, param: str, lemma: str) -> Unmodelled:
    return unmodelled(Stage.FILTER, lemma, LEAF, code, DETAIL_CODES, param)


def parse_filter(host: str, span: Optional[Span] = None, *, lemma: str = "",
                 zone: str = "") -> SlotResult:
    """The CardFilter of the untargeted object description ``host[span]``
    (default: the whole host).

    ``zone`` is the zone the caller's verb reads when the phrase prints
    none (a search passes "library"); otherwise a "spell" is on the stack
    and anything else on the battlefield, and a "card" with neither is
    UNMODELLED (a card is never on the battlefield, CR 108.3). On
    success ``span`` is the whole slot (trailing punctuation handed back in
    ``rest_spans``), ``amount`` the determiner's count, ``flags`` holds
    ``each`` / ``all``, and ``pending`` the anaphoric players and "from
    among" results the linker binds. Any token the tables cannot place
    makes the slot ``UNMODELLED(FILTER)`` over the whole trimmed slot."""
    a, b = (0, len(host)) if span is None else span
    slot = host[a:b]
    lead = len(slot) - len(slot.lstrip())
    trimmed = slot.strip()
    body = trimmed.rstrip(" .,;")
    start = a + lead
    value, failure, amount, flags, pending = _filter_rel(body, zone)
    if value is None:
        code, param = failure
        return SlotResult(unmodelled=_um(code, param, lemma),
                          span=(start, start + len(trimmed)))
    end = start + len(body)
    rest = rest_spans_after(host, end, start + len(trimmed), " ")
    rest = tuple(s for s in rest if host[s[0]:s[1]].strip(" .,;"))
    return SlotResult(value=value, span=(start, end), rest_spans=rest,
                      flags=flags, pending=pending, amount=amount)


def clear_caches() -> None:
    _filter_rel.cache_clear()
