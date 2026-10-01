"""Verb lexicon of the clause grammar (design doc 2026-09-29, section 4;
A12, A13, A18, A19; E0 step 11).

The closed table L4 reads to find a clause's lemma and pick its reading,
once, at LOAD, over L0 output. It follows the leaf contract of
`engine.effect_grammar.sub` -- ``(host, span, *, lemma="")``, the shared
`SlotResult`, host-absolute spans, ``lexicon.<code>[:<param>]`` details over
the closed `DETAIL_CODES`, bounded caches and ``clear_caches()`` -- but it
is not a sub-grammar: it sits beside them, like `keywords` and `normalize`.

It holds:

* `LEXICON` -- every reading, in table order. A reading is a `LexEntry`
  with the section-4 fields: the printed ``lemma`` (what every sub-grammar
  leaf is handed and stamps on its Unmodelled), ``verb``, the section-14
  migration ``family``, the participant ``roles`` it fills, the role of
  its ``other`` participant, the ``mod_kind`` hint of a continuous
  predicate (the payload leaf decides the kind from the printed
  predicate), whether it is ``hostile`` to its principal (None: the slot
  decides -- a bounce or a reanimation, a pump or a shrink) and the
  executing ``owner`` (documentation only);
* `VERB_LEXICON` -- each printed lemma and inflection mapped to its bucket:
  the readings of that word, most specific first (section 4
  disambiguation). A reading accepts the word only when its guard -- a
  pattern on the words after it, and for a few rows on the words before it
  -- holds, so "put ... counters on" is PUT_COUNTERS before any zone move,
  "shuffle <object> into <library>" is MOVE before SHUFFLE (A18), and "gain
  N life" is GAIN_LIFE before any keyword grant;
* `find_verb` -- the first verb of a clause slot whose reading accepts it.
  A word after a form of "be" (a participle: "is put into"), after a
  relative pronoun ("that is a zombie") or in a noun use its guard refuses
  ("in exile", "a copy of", "your draw step", "+1/+1 counter") is not the
  clause's verb, and the scan reads on. A word that is only ever a verb
  ("put", "return", "search") with no accepting reading is refused,
  never read past;
* `verb_at` -- A13: whether the token after a depth-0 comma is an
  inflected lexicon verb (the serial-list split test);
* recognised-but-unsupported actions (section 4's list, and the payload
  leaf's CR 701 actions the model does not type): a typed
  ``RECOGNIZED_UNSUPPORTED``, never a guess;
* `parse_loyalty_cost` -- A12: the grammar's loyalty-line pattern, a
  superset of `oracle_parser._LOYALTY_LINE_PATTERN` (which reads fixed
  costs only): ``[+N]:``, ``[-N]:``, ``[0]:``, and the variable ``[+X]:`` /
  ``[-X]:`` whose cost is ``Amount(X, n=+-1)`` (CR 107.3, 606.4).
  `loyalty_slot_cost` hands a cost to `oracle_parser.loyalty_slot_for`, the
  one owner of the slot rule, in the form it reads.

One table per vocabulary: the CR 701 keyword-action names are the payload
leaf's (`KEYWORD_ACTION_NAMES`, `UNSUPPORTED_KEYWORD_ACTIONS`), and the
keywords a "has"/"have"/"gain" grant or a "lose" removal names are the CR
702 table of `keywords`, and a library's possessive is destination's
`ZONE_POSSESSIVE`.
The lexicon reads no card name and no game state.

**Lemma.** The lexicon is where a clause's printed lemma comes from: a
reading's Unmodelled carries the lemma it read from the text; ``lemma``
passed by the caller restricts the readings to that lemma and is stamped on
a slot where no lemma was read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from types import MappingProxyType
from typing import Dict, FrozenSet, List, Mapping, Optional, Tuple

from engine.effect_grammar.keywords import KEYWORD_ABILITIES
from engine.effect_grammar.sub import (CACHE_SIZE, SlotResult, Span,
                                       rest_spans_after, unmodelled)
from engine.effect_grammar.sub.dest import ZONE_POSSESSIVE
from engine.effect_grammar.sub.payload import (KEYWORD_ACTION_NAMES,
                                               UNSUPPORTED_KEYWORD_ACTIONS)
from engine.effect_model import ModKind
from engine.effect_spec import Amount, AmountKind, Stage, Unmodelled, Verb

__all__ = ["LEAF", "DETAIL_CODES", "Family", "VERB_FAMILY", "ROLES",
           "OTHER_ROLES", "LexEntry", "LEXICON", "VERB_LEXICON",
           "VERB_ONLY_WORDS", "find_verb", "verb_at", "parse_loyalty_cost",
           "loyalty_slot_cost", "LOYALTY_LINE_RE", "clear_caches"]

LEAF = "lexicon"
DETAIL_CODES = frozenset({
    "no_lemma",          # no lexicon word reads as a verb in the slot
    "no_reading",        # a verb-only word whose readings all refuse
    "unsupported",       # a recognised action the model does not type
    "loyalty_unsigned_x",  # "[x]:" -- a variable loyalty cost needs a sign
})


def _um(stage: Stage, lemma: str, code: str, param: str = "") -> Unmodelled:
    return unmodelled(stage, lemma, LEAF, code, DETAIL_CODES,
                      param.replace(" ", "_"))


# ── Families, roles ────────────────────────────────────────────────────

class Family(Enum):
    """The section-14 migration family a verb's executors land in."""
    DAMAGE_LIFE = "damage_life"            # E1 (with the binding-only CHOOSE, LOOK)
    ZONE = "zone"                          # E2 removal / zone move
    CARD_FLOW = "card_flow"                # E3
    TOKENS_COUNTERS = "tokens_counters"    # E4 (and CR 701 keyword actions)
    CONTINUOUS = "continuous"              # E5
    STACK_MANA_RULES = "stack_mana_rules"  # E6 (and the remaining rules verbs)


_F = Family
VERB_FAMILY: Mapping[Verb, Family] = MappingProxyType({
    Verb.DAMAGE: _F.DAMAGE_LIFE, Verb.FIGHT: _F.DAMAGE_LIFE,
    Verb.LOSE_LIFE: _F.DAMAGE_LIFE, Verb.GAIN_LIFE: _F.DAMAGE_LIFE,
    Verb.SET_LIFE: _F.DAMAGE_LIFE, Verb.EXCHANGE_LIFE: _F.DAMAGE_LIFE,
    Verb.CHOOSE: _F.DAMAGE_LIFE, Verb.LOOK: _F.DAMAGE_LIFE,
    Verb.DESTROY: _F.ZONE, Verb.EXILE: _F.ZONE, Verb.SACRIFICE: _F.ZONE,
    Verb.MOVE: _F.ZONE, Verb.SHUFFLE: _F.ZONE,
    Verb.DRAW: _F.CARD_FLOW, Verb.DISCARD: _F.CARD_FLOW,
    Verb.MILL: _F.CARD_FLOW, Verb.SCRY: _F.CARD_FLOW,
    Verb.SURVEIL: _F.CARD_FLOW, Verb.REVEAL: _F.CARD_FLOW,
    Verb.REVEAL_UNTIL: _F.CARD_FLOW, Verb.SEARCH: _F.CARD_FLOW,
    Verb.CAST_FREE: _F.CARD_FLOW,
    Verb.CREATE_TOKEN: _F.TOKENS_COUNTERS, Verb.PUT_COUNTERS: _F.TOKENS_COUNTERS,
    Verb.REMOVE_COUNTERS: _F.TOKENS_COUNTERS,
    Verb.MOVE_COUNTERS: _F.TOKENS_COUNTERS,
    Verb.DOUBLE_COUNTERS: _F.TOKENS_COUNTERS,
    Verb.PLAYER_COUNTERS: _F.TOKENS_COUNTERS,
    Verb.KEYWORD_ACTION: _F.TOKENS_COUNTERS,
    Verb.CONTINUOUS: _F.CONTINUOUS,
    Verb.COUNTER: _F.STACK_MANA_RULES, Verb.ADD_MANA: _F.STACK_MANA_RULES,
    Verb.PAY: _F.STACK_MANA_RULES, Verb.END_TURN: _F.STACK_MANA_RULES,
    Verb.TAP: _F.STACK_MANA_RULES, Verb.UNTAP: _F.STACK_MANA_RULES,
    Verb.TRANSFORM: _F.STACK_MANA_RULES, Verb.ATTACH: _F.STACK_MANA_RULES,
    Verb.COPY: _F.STACK_MANA_RULES, Verb.CHANGE_TARGETS: _F.STACK_MANA_RULES,
    Verb.CREATE_EMBLEM: _F.STACK_MANA_RULES,
    Verb.EXTRA_TURN: _F.STACK_MANA_RULES, Verb.SKIP: _F.STACK_MANA_RULES,
    Verb.CREATE_TRIGGER: _F.STACK_MANA_RULES,
})

# The participant slots a reading fills (section 4 "Roles and slots").
# "player" alone is effect_spec's ACTOR_ONLY_VERBS role: no principal.
ROLES = frozenset({"object", "filter", "player", "recipient", "stack_object",
                   "dest", "amount", "payload", "chooser"})
# The role of EffectSpec.other: DAMAGE's source, FIGHT's first fighter.
OTHER_ROLES = frozenset({None, "source", "fighter"})

# verb -> (roles, other_role, hostile, executing owner). Documentation of the
# owner only; the dispatcher's registry is the binding one.
_VERB_FACTS: Dict[Verb, Tuple[Tuple[str, ...], Optional[str], Optional[bool], str]] = {
    Verb.DESTROY: (("object", "filter"), None, True,
                   "card_effects._resolve_nonland_permanent_removal / _resolve_board_sweep"),
    Verb.EXILE: (("object", "player", "filter", "amount"), None, True,
                 "GameState._exile_permanent / zone_manager.move_card"),
    Verb.SACRIFICE: (("object", "player", "filter", "amount", "chooser"), None,
                     True, "activation.legal_sacrifice_victims + callbacks.choose_sacrifice"),
    Verb.MOVE: (("object", "filter", "dest"), None, None,
                "clause_resolver.resolve_bounce / GameState.reanimate / zone_manager"),
    Verb.SHUFFLE: (("player",), None, False, "library shuffle owner"),
    Verb.DAMAGE: (("recipient", "amount"), "source", True, "damage.deal_damage"),
    Verb.FIGHT: (("object",), "fighter", True, "damage.deal_damage x2"),
    Verb.LOSE_LIFE: (("player", "amount"), None, True, "damage.lose_life"),
    Verb.GAIN_LIFE: (("player", "amount"), None, False, "GameState.gain_life"),
    Verb.SET_LIFE: (("player", "amount"), None, None, "none yet (refused)"),
    Verb.EXCHANGE_LIFE: (("player",), None, None, "none yet (refused)"),
    Verb.DRAW: (("player", "amount"), None, False, "GameState.draw_cards"),
    Verb.DISCARD: (("player", "filter", "amount", "chooser", "object"), None,
                   True, "GameState._force_discard"),
    Verb.MILL: (("player", "amount"), None, True, "zone_manager.move_card"),
    Verb.SCRY: (("player", "amount"), None, False, "GameState.scry"),
    Verb.SURVEIL: (("player", "amount"), None, False, "GameState.surveil"),
    Verb.LOOK: (("object", "player", "amount"), None, False, "binding only"),
    Verb.REVEAL: (("object", "player", "filter"), None, False,
                  "binding + legacy reveal log line"),
    Verb.REVEAL_UNTIL: (("player", "filter"), None, False, "binding + legacy reveal log line"),
    Verb.SEARCH: (("player", "filter", "amount"), None, False,
                  "oracle_resolver._resolve_x_creature_tutor / land_manager fetch"),
    Verb.CHOOSE: (("object", "filter", "chooser"), None, False, "binding only"),
    Verb.CAST_FREE: (("object",), None, False, "cast_manager free-cast path"),
    Verb.CREATE_TOKEN: (("amount", "payload"), None, False, "GameState.create_token"),
    Verb.PUT_COUNTERS: (("object", "filter", "payload"), None, None,
                        "CardInstance.add_plus_counters / adjust_counters"),
    Verb.REMOVE_COUNTERS: (("object", "payload"), None, None,
                           "CardInstance.adjust_counters"),
    Verb.MOVE_COUNTERS: (("object", "payload"), None, None, "CardInstance.adjust_counters"),
    Verb.DOUBLE_COUNTERS: (("object", "filter", "payload"), None, None,
                           "CardInstance.adjust_counters"),
    Verb.PLAYER_COUNTERS: (("player", "payload"), None, None, "Player.add_energy etc."),
    Verb.KEYWORD_ACTION: (("payload",), None, None, "the expansion's owners"),
    Verb.CONTINUOUS: (("object", "filter", "payload"), None, None,
                      "continuous_effects.register_effect / create_pump_spell_effect"),
    Verb.TAP: (("object", "filter"), None, True, "CardInstance.tap"),
    Verb.UNTAP: (("object", "filter"), None, False, "CardInstance.untap"),
    Verb.TRANSFORM: (("object",), None, None, "transform owner"),
    Verb.ATTACH: (("object", "dest"), None, None, "attach owner"),
    Verb.COUNTER: (("stack_object", "dest"), None, True, "spell_resolution counter path"),
    Verb.ADD_MANA: (("player", "payload"), None, False, "mana pool"),
    Verb.PAY: (("payload", "amount"), None, False, "energy owner (E4) / mana_payment (E6)"),
    Verb.COPY: (("stack_object", "object"), None, False, "none yet"),
    Verb.CHANGE_TARGETS: (("stack_object",), None, None, "none yet"),
    Verb.CREATE_EMBLEM: (("player", "payload"), None, False,
                         "planeswalker_manager emblem path"),
    Verb.EXTRA_TURN: (("player",), None, False, "none yet"),
    Verb.END_TURN: (("player",), None, None, "GameState.end_the_turn"),
    Verb.SKIP: (("player",), None, True, "none yet"),
    Verb.UNMODELLED: ((), None, None, "none (recognised, unsupported)"),
}


@dataclass(frozen=True, slots=True)
class LexEntry:
    """One reading of a printed lemma (section 4). Hashable; the payload
    leaf reads `verb`, `lemma` and `mod_kind`."""
    lemma: str
    verb: Verb
    family: Optional[Family]
    roles: Tuple[str, ...]
    other_role: Optional[str]
    mod_kind: Optional[ModKind]
    hostile: Optional[bool]
    owner: str


# ── The table ──────────────────────────────────────────────────────────

_COUNT = (r"(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|"
          r"eleven|twelve|thirteen|fourteen|fifteen|twenty|\d+|x)")
_SYM = r"\{[^{}]+\}"
# The words an object noun phrase opens with ("~" has no word boundary).
_OBJ = (r"(?:(?:target|up to|all|each|it|its|them|that|those|these|this|"
        r"another|other|any number of|enchanted|equipped|the|%s)\b|~)" % _COUNT)
_ZONE_WORDS = r"\b(?:onto|into|on top of|on the bottom of|under|beneath)\b"
# Keyword names a "has"/"have"/"gain" grant or a "lose" removal can name:
# the CR 702 table, and the variant families printed in a keyword's name
# (<type>walk, <type>cycling). Any other object of the verb-only "gain" /
# "lose" (a coin flip, control, unspent mana) is refused, never guessed.
_KW_NAMES = sorted((k for k in KEYWORD_ABILITIES
                    if k not in ("typecycling", "landwalk", "offering")),
                   key=len, reverse=True)
_KW = r"(?:%s|[a-z]+walk|[a-z]+cycling)\b" % "|".join(
    re.escape(k) for k in _KW_NAMES)
# The printed amount of a life change (CR 119.3): a count, an amount
# phrase, a multiple of X ("two times x", "twice x"), a fraction of X or of
# a life total ("half x", "a third of their life").
_LIFE_OWNER = r"(?:their|your|his or her)"
_LIFE_AFTER = (
    r"(?= (?:(?:%s|that much|twice that much|half that much|an amount of|"
    r"twice x|half x|%s times x|half %s|a third of %s) )?life\b)" % (
        _COUNT, _COUNT, _LIFE_OWNER, _LIFE_OWNER))
_COLOR = r"(?:white|blue|black|red|green)"
_PLAYER_COUNTER = (r"(?:\{e\}|poison counters?|experience counters?|"
                   r"rad counters?|ticket counters?)")
# A library's possessive is destination's zone possessive (one table; A9's
# "~'s owner's" included).
_LIBRARY_OWNER = ZONE_POSSESSIVE

# (lemma, verb, mod_kind, after, subject, flags, amount)
#   after:   matched right after the printed word; what it consumes beyond
#            the word is part of the lemma ("look at", "pay any amount of"),
#            lookaheads are the disambiguating guard;
#   subject: optional pattern the text before the word must end with.
_Row = Tuple[str, Verb, Optional[ModKind], str, Optional[str],
             FrozenSet[str], Optional[Amount]]
_NONE: FrozenSet[str] = frozenset()


def _r(lemma, verb, after=r"(?= |$|[.,;])", mod=None, subject=None,
       flags=_NONE, amount=None) -> _Row:
    return (lemma, verb, mod, after, subject, flags, amount)


_C = Verb.CONTINUOUS
_ROWS: Tuple[_Row, ...] = (
    # zone (CR 701.8, 701.13, 701.21, 400.7)
    _r("destroy", Verb.DESTROY),
    _r("exile", Verb.EXILE, r"(?= (?!(?:and|or)\b)|$)"),
    _r("sacrifice", Verb.SACRIFICE),
    _r("return", Verb.MOVE),
    _r("put", Verb.PUT_COUNTERS,
       r"(?= (?:(?:%s|that many|an additional|any number of|a number of|all|"
       r"those|the|its|their|x|your choice of|another|twice|half|up to)\b|"
       r"[+-]\d)"
       r"(?:(?!%s)[^.;])*?"
       r"\bcounters?\b(?:(?!%s)[^.;])*? (?:on|onto)\b)" % (
           _COUNT, _ZONE_WORDS, _ZONE_WORDS)),
    _r("put", Verb.MOVE, r"(?= [^.;]*?\bonto the battlefield\b)"),
    _r("put", Verb.MOVE, r"(?= [^.;]*?\b(?:on top of|on the bottom of|"
                         r"top or bottom of|second from the top of|"
                         r"third from the top of)\b)"),
    _r("put", Verb.MOVE, r"(?= [^.;]*?\b(?:into|on the bottom|on top)\b)"),
    # A18: an object shuffled into a library is a zone move.
    _r("shuffle", Verb.MOVE,
       r"(?= (?!%s librar)(?:(?!, then\b)[^.;])*?\binto %s ?librar(?:y|ies)\b)" % (
           _LIBRARY_OWNER, _LIBRARY_OWNER)),
    _r("shuffle", Verb.SHUFFLE,
       r"(?= %s librar|$|[.,;]| and\b| then\b)" % _LIBRARY_OWNER),
    # damage and life (CR 120, 119)
    _r("deal", Verb.DAMAGE, r"(?= [^.;]*?\bdamage\b)"),
    _r("fight", Verb.FIGHT),
    _r("lose", Verb.LOSE_LIFE, _LIFE_AFTER),
    _r("gain", Verb.GAIN_LIFE, _LIFE_AFTER),
    _r("become", Verb.SET_LIFE, subject=r"\blife totals? $"),
    _r("exchange", Verb.EXCHANGE_LIFE, r"(?= life totals\b)"),
    # card flow (CR 121, 701.9, 701.13, 701.18-22)
    _r("draw", Verb.DRAW, r"(?= (?!steps?\b)|$|[.,;])"),
    _r("discard", Verb.DISCARD),
    _r("mill", Verb.MILL),
    _r("scry", Verb.SCRY),
    _r("surveil", Verb.SURVEIL),
    _r("look", Verb.LOOK, r" at\b"),
    _r("reveal", Verb.REVEAL_UNTIL,
       r"(?= [^.;]*?\buntil (?:you|they|he or she|that player|a player|"
       r"an opponent) reveals?\b)"),
    _r("reveal", Verb.REVEAL),
    _r("search", Verb.SEARCH),
    _r("choose", Verb.CHOOSE),
    _r("cast", Verb.CAST_FREE, r"(?= [^.;]*?\bwithout paying\b)"),
    _r("play", Verb.CAST_FREE, r"(?= [^.;]*?\bwithout paying\b)"),
    _r("cast", _C, r"(?= [^.;]*?\bas though\b)", ModKind.PERMIT),
    _r("cast", _C, mod=ModKind.PERMIT, subject=r"\bmay $"),
    _r("play", _C, mod=ModKind.PERMIT, subject=r"\bmay $"),
    # tokens and counters (CR 111, 122)
    _r("create", Verb.CREATE_TOKEN),
    _r("remove", Verb.REMOVE_COUNTERS, r"(?= [^.;]*?\bcounters?\b)"),
    _r("move", Verb.MOVE_COUNTERS, r"(?= [^.;]*?\bcounters?\b)"),
    _r("double", Verb.DOUBLE_COUNTERS,
       r"(?= the number of [^.;]*?\bcounters?\b| [^.;]*?\bcounters\b)"),
    # continuous predicates (CR 611-613); `mod` is a hint the payload
    # leaf confirms from the printed predicate.
    _r("get", _C, r"(?= [+-](?:\d+|x)/[+-](?:\d+|x))", ModKind.MODIFY_PT),
    _r("get", Verb.CREATE_EMBLEM, r"(?= an emblem\b)"),
    _r("get", Verb.PLAYER_COUNTERS, r"(?= [^.;,]*?%s)" % _PLAYER_COUNTER),
    _r("gain", _C, r"(?= control of\b)", ModKind.SET_CONTROLLER),
    _r("gain", _C, r"(?= ⟨q\d+⟩)", ModKind.GRANT_ABILITY),
    _r("gain", _C, r"(?= your choice of\b)", ModKind.ADD_KEYWORDS,
       flags=frozenset({"alternatives"})),
    _r("gain", _C, r"(?= %s)" % _KW, ModKind.ADD_KEYWORDS),
    _r("lose", _C, r"(?= all (?:other )?abilities\b)",
       ModKind.REMOVE_ALL_ABILITIES),
    _r("lose", _C, r"(?= %s)" % _KW, ModKind.REMOVE_KEYWORDS),
    _r("have", _C, r"(?= base power and toughness\b)", ModKind.SET_BASE_PT),
    _r("have", _C, r"(?= ⟨q\d+⟩)", ModKind.GRANT_ABILITY),
    _r("have", _C, r"(?= your choice of\b)", ModKind.ADD_KEYWORDS,
       flags=frozenset({"alternatives"})),
    _r("have", _C, r"(?= %s)" % _KW, ModKind.ADD_KEYWORDS),
    _r("exchange", _C, r"(?= control of\b)", ModKind.SET_CONTROLLER),
    # "become": a colour change (layer 5), a base P/T setting (layer 7b),
    # then a type change; a copy effect (layer 1) is recognised below and a
    # designation (tapped, blocked, plotted, ...) has no continuous reading.
    _r("become", _C,
       r"(?= (?:the colou?rs? (?:or colou?rs )?of\b|the chosen colou?r\b|"
       r"that colou?r\b|all colou?rs\b|(?:colorless|%s(?: and %s)?)"
       r"(?=$|[.,;]| until\b| instead\b)))" % (_COLOR, _COLOR),
       ModKind.SET_COLORS),
    _r("become", _C, mod=ModKind.SET_BASE_PT,
       subject=r"\bbase power and toughness (?:each )?$"),
    _r("become", _C,
       r"(?= (?!the target\b|tapped\b|untapped\b|blocked\b|attached\b|"
       r"unattached\b|monstrous\b|renowned\b|suspected\b|saddled\b|"
       r"plotted\b|the monarch\b|prepared\b|the day\b|the night\b|"
       r"day\b|night\b|a copy of\b|copies of\b))",
       ModKind.ADD_TYPES),
    _r("be", _C, r"(?= (?:an?|all|still|every|colorless|the colou?r|"
                 r"(?:white|blue|black|red|green)(?: and \w+)?)\b)",
       ModKind.ADD_TYPES),
    _r("switch", _C, r"(?= [^.;]*?\bpower and toughness\b)", ModKind.SWITCH_PT),
    _r("can't", _C, r"(?= (?:draw|cast) more than\b)", ModKind.LIMIT),
    _r("can't", _C, mod=ModKind.PROHIBIT),
    _r("doesn't", _C, r"(?= untap during\b)", ModKind.PROHIBIT),
    _r("attack", _C, r"(?= [^.;]*?\bif able\b)", ModKind.REQUIRE),
    _r("block", _C, r"(?= [^.;]*?\bif able\b)", ModKind.REQUIRE),
    _r("must", _C, r"(?= (?:be blocked|attack|block)\b)", ModKind.REQUIRE),
    _r("cost", _C, r"(?= (?:%s)+ (?:less|more)\b)" % _SYM, ModKind.COST_DELTA),
    _r("prevent", _C, r"(?= [^.;]*?\bdamage\b)", ModKind.PREVENT_DAMAGE),
    # stack, mana, rules (CR 701.5, 106, 701.3, 701.26, 701.28, 500.8, 721)
    _r("tap", Verb.TAP, r"(?= %s)" % _OBJ),
    _r("untap", Verb.UNTAP, r"(?= %s)" % _OBJ),
    _r("transform", Verb.TRANSFORM),
    _r("attach", Verb.ATTACH),
    _r("counter", Verb.COUNTER,
       r"(?= (?:(?:target|that|it|them|all|each|up to|another|any number of|"
       r"the (?:first|next))\b|~))"),
    _r("add", Verb.ADD_MANA,
       r"(?= (?:%s|[a-z0-9]+ mana\b|mana\b|that much\b|an amount of\b|"
       r"an additional\b|x %s|[a-z]+ %s))" % (_SYM, _SYM, _SYM)),
    # A19: the amount is chosen at resolution (choose_amount).
    _r("pay", Verb.PAY, r" any amount of\b", amount=Amount(AmountKind.ANY_NUMBER),
       flags=frozenset({"any_number"})),
    _r("pay", Verb.PAY),
    _r("copy", Verb.COPY,
       r"(?= (?:(?:target|it|that|them|those|each|any number of|up to|"
       r"the)\b|~))"),
    _r("change", Verb.CHANGE_TARGETS, r"(?= the targets? of\b)"),
    _r("take", Verb.EXTRA_TURN, r"(?= (?:an|%s) extra turns?\b)" % _COUNT),
    _r("end", Verb.END_TURN, r" the turn\b"),
    _r("skip", Verb.SKIP),
)

# Recognised-but-unsupported lemmas (section 4) beyond the payload leaf's
# CR 701 list: (lemma, pattern from the printed word on).
_UNSUPPORTED_ROWS: Tuple[Tuple[str, str], ...] = (
    ("phase out", r"phases? out\b"),
    ("regenerate", r"regenerates?\b"),
    # CR 707.2, 613.1a: a layer 1 copy effect has no continuous owner yet.
    ("become a copy", r"(?:becomes? a copy|become copies) of\b"),
    ("win the game", r"wins? the game\b"),
    ("lose the game", r"loses? the game\b"),
    ("face a villainous choice", r"faces? a villainous choice\b"),
    ("separate into piles", r"separates? [^.;]*? into (?:two|\w+) piles\b"),
    ("name a card", r"names? (?:a|an|one)\b"),
    ("roll", r"rolls? (?:a|an|two|three|\d+) (?:d\d+|[\w-]+ dic?e)\b"),
)

# Words that are only ever verbs in rules English: a refusal of every
# reading is a typed refusal, never a reason to read on to a later verb.
VERB_ONLY_WORDS = frozenset({
    "destroy", "destroys", "sacrifice", "sacrifices", "return", "returns",
    "put", "puts", "shuffle", "shuffles", "fight", "fights", "lose",
    "loses", "gain", "gains", "discard", "discards", "mill", "mills",
    "scry", "surveil", "surveils", "look", "looks", "reveal", "reveals",
    "search", "searches", "choose", "chooses", "create", "creates",
    "untap", "untaps", "pay", "pays"})


def _inflections(lemma: str) -> Tuple[str, ...]:
    """The printed base and third-person forms of a lemma's first word."""
    irregular = {"have": ("have", "has"), "be": ("is", "are"),
                 "can't": ("can't", "cannot"), "doesn't": ("doesn't", "don't"),
                 "must": ("must",), "the": ("the",)}
    w = lemma.split()[0]
    if w in irregular:
        return irregular[w]
    if re.search(r"(?:s|sh|ch|x|z)$", w):
        return (w, w + "es")
    if re.search(r"[^aeiou]y$", w):
        return (w, w[:-1] + "ies")
    return (w, w + "s")


def _inflected_phrase(name: str) -> str:
    """A pattern for a multi-word action name whose first word inflects."""
    head, _, tail = name.partition(" ")
    pat = "(?:%s)" % "|".join(re.escape(f) for f in _inflections(head))
    return pat + (r" " + re.escape(tail) if tail else "") + r"\b"


def _entry(lemma: str, verb: Verb, mod: Optional[ModKind]) -> LexEntry:
    roles, other, hostile, owner = _VERB_FACTS[verb]
    return LexEntry(lemma=lemma, verb=verb, family=VERB_FAMILY.get(verb),
                    roles=roles, other_role=other, mod_kind=mod,
                    hostile=hostile, owner=owner)


@dataclass(frozen=True)
class _Reading:
    entry: LexEntry
    word: "re.Pattern[str]"        # matched at the word start, the word itself
    after: "re.Pattern[str]"       # matched at the word end
    subject: Optional["re.Pattern[str]"]
    flags: FrozenSet[str]
    amount: Optional[Amount]
    supported: bool


# CR 701 keyword-action shapes (payload's KEYWORD_ACTION_NAMES) -> the
# guard on the words after the action name.
_KA_GUARDS = {
    "none": r"(?= |$|[.,;])",
    "object": r"(?= )",
    "n": r"(?= (?:\d+|x)\b)",
    "subtype_n": r"(?= [a-z]+ (?:\d+|x)\b)",
}


def _build() -> Tuple[Tuple[_Reading, ...], Dict[str, Tuple[_Reading, ...]]]:
    readings: List[_Reading] = []

    def add(lemma, verb, mod, word_pat, after, subject, flags, amount,
            supported=True):
        readings.append(_Reading(
            entry=_entry(lemma, verb, mod), word=re.compile(word_pat),
            after=re.compile(after),
            subject=re.compile(subject) if subject else None,
            flags=flags, amount=amount, supported=supported))

    for lemma, verb, mod, after, subject, flags, amount in _ROWS:
        add(lemma, verb, mod, _inflected_phrase(lemma), after, subject,
            flags, amount)
    # CR 701 keyword actions: the payload leaf's table, longest first so a
    # longer action ("manifest dread") is read before a shorter one.
    for name in sorted(KEYWORD_ACTION_NAMES, key=len, reverse=True):
        add(name, Verb.KEYWORD_ACTION, None, _inflected_phrase(name),
            _KA_GUARDS[KEYWORD_ACTION_NAMES[name]], None, _NONE, None)
    unsupported = [(n, _inflected_phrase(n)) for n in sorted(
        UNSUPPORTED_KEYWORD_ACTIONS, key=len, reverse=True)]
    for name, pat in unsupported + list(_UNSUPPORTED_ROWS):
        add(name, Verb.UNMODELLED, None, pat, r"", None, _NONE, None,
            supported=False)

    buckets: Dict[str, List[_Reading]] = {}
    for rd in readings:
        for form in _inflections(rd.entry.lemma):
            buckets.setdefault(form, []).append(rd)
    return tuple(readings), {k: tuple(v) for k, v in buckets.items()}


_READINGS, _BUCKETS = _build()

LEXICON: Tuple[LexEntry, ...] = tuple(rd.entry for rd in _READINGS)
VERB_LEXICON: Mapping[str, Tuple[LexEntry, ...]] = MappingProxyType(
    {w: tuple(rd.entry for rd in rds) for w, rds in _BUCKETS.items()})

# A word after a form of "be" is a participle ("is put into", "it's
# exiled"); a word after a relative pronoun is the relative clause's verb
# ("each creature that is a zombie"); a word after a preposition or an
# article is a noun use ("in exile", "a copy of", "the draw").
_SKIP_BEFORE = re.compile(
    r"(?:^|[\s(])(?:is|are|was|were|be|been|being|isn't|aren't|wasn't|"
    r"weren't|it's|that's|they're|that|which|who|whose|in|into|from|of|a|an|"
    r"there|didn't|don't|"
    r"to be|can be|can't be|would be)\s+$")
_WORD_RE = re.compile(r"[a-z][a-z']*")


# ── Finding the verb ───────────────────────────────────────────────────

def _accept(text: str, start: int, end: int, word_end: int, pre: str,
            lemma: str) -> Optional[Tuple[_Reading, int]]:
    """The first reading of the word at `start` whose guards accept it, and
    the end of what it consumed; None when none does."""
    for rd in _BUCKETS.get(text[start:word_end], ()):
        if lemma and rd.entry.lemma != lemma:
            continue
        if rd.subject is not None and not rd.subject.search(pre):
            continue
        w = rd.word.match(text, start, end)
        if w is None:
            continue
        a = rd.after.match(text, w.end(), end)
        if a is None:
            continue
        return rd, a.end()
    return None


# A relative result: ("ok", reading index, verb start, consumed end) |
# ("um", stage name, lemma, code, param).
@lru_cache(maxsize=CACHE_SIZE)
def _find_rel(t: str, lemma: str):
    refused = None
    for m in _WORD_RE.finditer(t):
        word = m.group(0)
        if word not in _BUCKETS:
            continue
        pre = t[:m.start()]
        if _SKIP_BEFORE.search(pre):
            continue
        hit = _accept(t, m.start(), len(t), m.end(), pre, lemma)
        if hit is not None:
            rd, consumed = hit
            return ("ok", _READINGS.index(rd), m.start(), consumed)
        if refused is None and word in VERB_ONLY_WORDS and not lemma:
            refused = word
            break
    if refused is not None:
        base = next(rd.entry.lemma for rd in _BUCKETS[refused])
        return ("um", Stage.CLAUSE, base, "no_reading", base)
    return ("um", Stage.NO_LEMMA, lemma, "no_lemma", "")


def _trim(host: str, span: Optional[Span]) -> Span:
    a, b = (0, len(host)) if span is None else span
    while a < b and host[a].isspace():
        a += 1
    while b > a and host[b - 1].isspace():
        b -= 1
    return a, b


def find_verb(host: str, span: Optional[Span] = None, *,
              lemma: str = "") -> SlotResult:
    """The clause verb of ``host[span]`` (default: the whole host): the
    first lexicon word whose reading accepts it.

    ``value`` is its `LexEntry`; ``span`` the printed lemma words consumed
    (the inflected word, and the fixed words of a multi-word lemma: "look
    at", "pay any amount of"); ``rest_spans`` the slot text before it (the
    participant) and after it (the object and payload), in order;
    ``flags`` hold ``alternatives`` (A19 'your choice of') and
    ``any_number``; ``amount`` is ANY_NUMBER for 'pay any amount of'.

    A recognised-unsupported action is ``RECOGNIZED_UNSUPPORTED`` with its
    lemma; a verb-only word no reading accepts is ``CLAUSE``
    (``lexicon.no_reading:<lemma>``); a slot with no verb is ``NO_LEMMA``
    with the caller's `lemma`. A non-empty `lemma` reads only that lemma's
    readings.

    The slot is a frame body (section 3). L2 owns the leading and trailing
    frames -- a condition ``if|unless|as long as|while <COND>,``, a
    connective (``if you do,``), a ``would`` replacement test, a duration
    -- and consumes them before L4 asks for the lemma; the lexicon does not
    strip them a second time. Handed a whole conditional sentence it reads
    the first accepted word, which may be the condition's ("if equipped
    creature is a vampire, put ..." reads "is")."""
    a, b = _trim(host, span)
    rel = _find_rel(host[a:b], lemma)
    if rel[0] == "um":
        _, stage, lem, code, param = rel
        return SlotResult(unmodelled=_um(stage, lem, code, param), span=(a, b))
    _, idx, vs, ve = rel
    rd = _READINGS[idx]
    if not rd.supported:
        return SlotResult(unmodelled=_um(Stage.RECOGNIZED_UNSUPPORTED,
                                         rd.entry.lemma, "unsupported",
                                         rd.entry.lemma), span=(a, b))
    vs, ve = a + vs, a + ve
    rest = (rest_spans_after(host, a, vs, " ,")
            + rest_spans_after(host, ve, b, " "))
    return SlotResult(value=rd.entry, span=(vs, ve), rest_spans=rest,
                      flags=rd.flags, amount=rd.amount)


def verb_at(host: str, pos: int) -> Optional[LexEntry]:
    """A13: the reading of the inflected lexicon verb that starts at `pos`
    (the token after a depth-0 comma), or None -- a type-list member, a
    noun, a participle or a pronoun is not a verb."""
    m = _WORD_RE.match(host, pos)
    if m is None or m.group(0) not in _BUCKETS:
        return None
    pre = host[:pos]
    if _SKIP_BEFORE.search(pre):
        return None
    hit = _accept(host, pos, len(host), m.end(), pre, "")
    if hit is None or not hit[0].supported:
        return None
    return hit[0].entry


# ── Loyalty lines (A12) ────────────────────────────────────────────────

# The grammar's loyalty-line pattern over L0 output (dashes unified, X
# lowercased): a superset of oracle_parser._LOYALTY_LINE_PATTERN, which
# reads fixed costs only.
LOYALTY_LINE_RE = re.compile(r"\[(?P<sign>[+-]?)(?P<n>\d+|x)\]:")


@lru_cache(maxsize=CACHE_SIZE)
def _loyalty_rel(t: str):
    m = LOYALTY_LINE_RE.match(t)
    if m is None:
        return None
    sign = -1 if m.group("sign") == "-" else 1
    if m.group("n") == "x":
        if not m.group("sign"):
            return ("um", m.end())
        return (Amount(AmountKind.X, n=sign), m.end())
    return (Amount(AmountKind.LITERAL, n=sign * int(m.group("n"))), m.end())


def parse_loyalty_cost(host: str, span: Optional[Span] = None, *,
                       lemma: str = "") -> SlotResult:
    """A12: the loyalty cost opening the paragraph ``host[span]``.

    ``value`` is the signed cost: ``Amount(LITERAL, n=+-N)`` for a fixed
    cost, ``Amount(X, n=+-1)`` for ``[+X]`` / ``[-X]`` (X bound from the
    paid loyalty cost, CR 107.3, 606.4); ``span`` covers ``[...]:``;
    ``rest_spans`` is the line's body. A paragraph that is not a loyalty
    line gives an empty SlotResult (value and unmodelled both None)."""
    a, b = _trim(host, span)
    rel = _loyalty_rel(host[a:b])
    if rel is None:
        return SlotResult(span=(a, a))
    value, end = rel
    if value == "um":
        return SlotResult(unmodelled=_um(Stage.STRUCTURE, lemma,
                                         "loyalty_unsigned_x"), span=(a, b))
    return SlotResult(value=value, span=(a, a + end),
                      rest_spans=rest_spans_after(host, a + end, b))


def loyalty_slot_cost(cost: Amount):
    """A loyalty cost in the form `oracle_parser.loyalty_slot_for` reads: an
    int for a fixed cost, the printed '+x' / '-x' for a variable one (which
    takes no slot in E0, A12)."""
    if cost.kind is AmountKind.X:
        return "+x" if cost.n > 0 else "-x"
    return cost.n


def clear_caches() -> None:
    for fn in (_find_rel, _loyalty_rel):
        fn.cache_clear()
