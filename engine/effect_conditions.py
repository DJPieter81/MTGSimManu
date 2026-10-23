"""Resolution-time counts and conditions: one owner.

Design doc: docs/design/2026-09-29_clause_and_trigger_grammar.md, section 11
("Amount"): the quantity and condition evaluators that a resolving effect
reads live here, moved out of the handlers that each counted for themselves.

Two kinds of caller read the same primitives, so they cannot disagree about a
count while both resolution paths exist:

* the legacy resolution handlers, through adapters over the shapes their
  parsers produce (`scaler_count` for a "draw N for each <X>" scaler,
  `direct_damage_condition_met` for a burn spell's upgrade label);
* the effect dispatcher's typed evaluators (`engine/effect_executors.py`),
  over parsed `Condition` and `Quantity` specs.

This module reads no oracle text and writes no game state.
"""
from __future__ import annotations

import operator
from typing import Any, Callable, Optional

from .cards import CardType, Keyword, Supertype
from .effect_spec import (Amount, AmountKind, CardFilter, Condition,
                          ConditionKind, Quantity, QuantityKind, Ref, RefKind,
                          Selector, SelectorKind)


# Thresholds of the two ability-word conditions legacy burn upgrades carry
# as labels (`direct_damage_data['upgrade_condition']`). Each card prints
# its own number; the typed condition reads the printed one instead.
DELIRIUM_CARD_TYPES = 4    # delirium: four or more card types among cards in your graveyard
METALCRAFT_ARTIFACTS = 3   # metalcraft: three or more artifacts you control


# ── Primitives ────────────────────────────────────────────────────────

def graveyard_card_types(game: Any, player_idx: int) -> int:
    """Number of distinct card types (CR 205.2a) among cards in the
    player's graveyard: the unit of delirium and of every "for each card
    type among cards in your graveyard" count."""
    player = game.players[player_idx]
    return len({t for c in player.graveyard for t in c.template.card_types})


def permanents_controlled(game: Any, player_idx: int,
                          pred: Callable[[Any], bool], *,
                          exclude: Optional[Any] = None) -> int:
    """How many permanents the player controls satisfy `pred`; `exclude`
    (an "other" count's source) is left out by object identity."""
    skip = getattr(exclude, "instance_id", None)
    return sum(1 for perm in game.players[player_idx].battlefield
               if (skip is None or perm.instance_id != skip) and pred(perm))


def opponents_who_lost_life(game: Any, player_idx: int) -> int:
    """How many of the player's opponents lost life this turn."""
    return sum(1 for i, p in enumerate(game.players)
               if i != player_idx and p.life_lost_this_turn > 0)


def basic_land_types(game: Any, player_idx: int) -> int:
    """Domain: the basic land types among lands the player controls, from
    each land's current types (the engine's one domain count)."""
    from .mana_payment import ManaPayment
    return ManaPayment.count_domain(game, player_idx)


# ── Typed evaluators (the dispatcher's, through effect_executors) ─────
#
# Each shape is supported only exactly as printed here; any other field set
# on the condition, quantity or filter makes `*_supported` refuse it, so the
# dispatcher's `can_execute` refuses the host and its carrier keeps the
# legacy apply (fail closed).

_OPS = {">=": operator.ge, "<=": operator.le, "==": operator.eq,
        ">": operator.gt, "<": operator.lt}
_CARD_TYPE_VALUES = frozenset(t.value for t in CardType)


def _only(f: Optional[CardFilter], **want: Any) -> bool:
    """Is `f` exactly the filter whose non-default entries are `want`?"""
    return isinstance(f, CardFilter) and dict(f.as_tuple()) == want


def _literal(a: Optional[Amount]) -> Optional[int]:
    if isinstance(a, Amount) and a.kind is AmountKind.LITERAL \
            and isinstance(a.n, int) and not isinstance(a.n, bool):
        return a.n
    return None


def _controlled_types_filter(f: Optional[CardFilter]) -> bool:
    """"<types> you control": battlefield permanents of any of the card
    types, controlled by the condition's controller."""
    if not isinstance(f, CardFilter) or not f.types \
            or not f.types <= _CARD_TYPE_VALUES:
        return False
    return _only(f, types=tuple(sorted(f.types)), controller="you")


def _controlled_count_filter(f: Optional[CardFilter]) -> bool:
    """"<types> you control" or "<subtype>s you control" ("Mountains you
    control"): the permanents a count amount counts."""
    if _controlled_types_filter(f):
        return True
    return (isinstance(f, CardFilter) and len(f.subtypes) == 1
            and _only(f, subtypes=tuple(sorted(f.subtypes)),
                      controller="you"))


def _counted(perm: Any, f: CardFilter) -> bool:
    """The permanent is one the count's description admits now
    (`permanent_described`)."""
    return permanent_described(perm, f)


# ── Descriptions of permanents (one owner, unit PF) ───────────────────
#
# "each Cat you control", "each other artifact creature you control",
# "creatures you control with flying": a typed battlefield description
# (`CardFilter`) admits a permanent by what is true of it now -- the face it
# shows and the types, colours and keywords effects give it (CR 613), its
# state (CR 110.5, 506.4) and its counters (CR 122). Every consumer that
# asks which permanents a description admits reads `permanent_described`;
# each still decides which descriptions it takes, never more than
# `description_supported`. The description's controller is the consumer's
# to bind (a count's "you", a team counter's target player): it is not
# read here.

_SUPERTYPE_VALUES = frozenset(s.value for s in Supertype)
_KEYWORD_VALUES = frozenset(k.value for k in Keyword)
_COLOR_LETTERS = frozenset("WUBRG")
# The object states a permanent records (CR 110.5, 506.4).
_DESCRIBED_STATES = frozenset({"tapped", "untapped", "attacking", "blocking"})
# The derived classes (A19): historic (CR 700.6) and the colour counts of
# CR 105.2.
_DESCRIBED_CLASSES = frozenset({"historic", "colored", "multicolored",
                                "monocolored"})


def description_supported(f: Any, bounds: bool = False) -> bool:
    """Can `permanent_described` read every part of this description? A
    battlefield description whose values are in the vocabulary it compares.
    A name, a position, an owner or another zone is refused, so the
    instruction holding it is refused whole (fail closed). A characteristic
    bound is read only by a consumer that evaluates its values as it applies
    (`bounds`: `bound_values`, CR 608.2h), and only over the bounds
    `bounds_supported` takes."""
    if not isinstance(f, CardFilter) or f.zone != "battlefield" or f.zones:
        return False
    if f.stat_bounds and not (bounds and bounds_supported(f)):
        return False
    if f.named is not None or f.position is not None \
            or f.different_names or f.owner != "any":
        return False
    return (f.types <= _CARD_TYPE_VALUES and f.all_types <= _CARD_TYPE_VALUES
            and f.not_types <= _CARD_TYPE_VALUES
            and f.supertypes <= _SUPERTYPE_VALUES
            and f.not_supertypes <= _SUPERTYPE_VALUES
            and f.colors <= _COLOR_LETTERS and f.not_colors <= _COLOR_LETTERS
            and f.classes <= _DESCRIBED_CLASSES
            and f.state <= _DESCRIBED_STATES
            and f.with_keywords <= _KEYWORD_VALUES
            and f.without_keywords <= _KEYWORD_VALUES
            and all(isinstance(kind, str) and isinstance(has, bool)
                    for kind, has in f.counters))


# Characteristic bounds ("with mana value 2 or less", "with power equal to
# the number of charge counters on ~"): the characteristic, read as the
# permanent has it now (CR 202.3 mana value; 208.1 power and toughness),
# compared with a value determined once as the instruction applies (CR
# 608.2h).
_BOUND_OPS = {"==": operator.eq, "<=": operator.le, ">=": operator.ge,
              "<": operator.lt, ">": operator.gt}
_BOUND_STATS = frozenset({"mana_value", "power", "toughness"})


def _characteristic(perm: Any, stat: str) -> int:
    if stat == "mana_value":
        return perm.template.cmc or 0
    return (perm.power if stat == "power" else perm.toughness) or 0


def bound_amount_supported(a: Any) -> bool:
    """The values a characteristic bound compares with: a printed number, X
    (the value chosen as it was cast or activated, CR 107.3), and the
    number of counters of one kind on the instruction's own source (CR
    122.1) -- read from the counters it last had when it left the
    battlefield as part of the cost (CR 608.2h)."""
    if not isinstance(a, Amount) or a.ref is not None or a.inner is not None \
            or a.rounding or a.evenly:
        return False
    if a.kind in (AmountKind.LITERAL, AmountKind.X):
        return amount_supported(a)
    q = a.quantity
    return (a.kind is AmountKind.EQUAL_TO and isinstance(q, Quantity)
            and q.kind is QuantityKind.COUNTERS_ON and q.filter is None
            and isinstance(q.counter_kind, str) and q.stat is None
            and q.event is None and isinstance(q.ref, Ref)
            and q.ref.kind is RefKind.SELF and q.ref.of is None)


def bounds_supported(f: CardFilter) -> bool:
    """Is every characteristic bound of the description one
    `bound_values` evaluates?"""
    return all(stat in _BOUND_STATS and op in _BOUND_OPS
               and bound_amount_supported(a)
               for stat, op, a in f.stat_bounds)


def bound_values(game: Any, controller: int, f: CardFilter,
                 source: Any = None, source_lki: Any = None,
                 x_value: int = 0) -> tuple:
    """Each bound of a supported description as (characteristic, operator,
    value), the value determined once as the instruction applies (CR
    608.2h). Counters on the source are its last-known counters
    (`source_lki`, a `LastKnown`) when the reference reads last-known
    information and the source left; else those it has now."""
    out = []
    for stat, op, a in f.stat_bounds:
        if a.kind is AmountKind.EQUAL_TO:
            q = a.quantity
            if q.ref.lki and source_lki is not None:
                n = source_lki.count(q.counter_kind)
            elif source is not None and source.zone == "battlefield":
                n = source.counter_count(q.counter_kind)
            else:
                n = 0
        else:
            n = amount_value(game, controller, a, x_value)
        out.append((stat, op, n))
    return tuple(out)


def _in_state(perm: Any, state: str) -> bool:
    if state == "tapped":
        return bool(perm.tapped)
    if state == "untapped":
        return not perm.tapped
    if state == "attacking":
        return bool(perm.attacking)
    return perm.blocking is not None                     # "blocking"


def _in_class(perm: Any, cls: str, types: set, colors: set) -> bool:
    if cls == "historic":                                # CR 700.6
        return ("artifact" in types
                or Supertype.LEGENDARY in perm.template.supertypes
                or any(st.lower() == "saga" for st in perm.effective_subtypes))
    n = len(colors)
    return {"colored": n >= 1, "multicolored": n >= 2,
            "monocolored": n == 1}[cls]


def permanent_described(perm: Any, f: CardFilter, source: Any = None,
                        bounds: Optional[tuple] = None) -> bool:
    """Does the description admit this permanent now? A union of card types
    admits any of them, a joined type ("artifact creature") all of them;
    colours and states are a union, every other listed value must hold.
    "Other" leaves out `source`, the instruction's own object. A
    description with characteristic bounds admits a permanent only against
    their values (`bounds`, from `bound_values`); without them it admits
    none. Only a supported description is asked (`description_supported`)."""
    if f.stat_bounds and (bounds is None or not all(
            _BOUND_OPS[op](_characteristic(perm, stat), n)
            for stat, op, n in bounds)):
        return False
    types = {t.value for t in perm.effective_card_types}
    if f.types and not types & f.types:
        return False
    if not f.all_types <= types or types & f.not_types:
        return False
    supertypes = {s.value for s in perm.template.supertypes}
    if not f.supertypes <= supertypes or supertypes & f.not_supertypes:
        return False
    subtypes = {str(st).lower() for st in perm.effective_subtypes}
    if not f.subtypes <= subtypes or subtypes & f.not_subtypes:
        return False
    colors = {c.value for c in perm.colors} & _COLOR_LETTERS
    if f.colors and not colors & f.colors:
        return False
    if colors & f.not_colors:
        return False
    if f.colorless is not None and f.colorless != (not colors):
        return False
    if not all(_in_class(perm, c, types, colors) for c in f.classes):
        return False
    if f.other and source is not None and perm is source:
        return False
    if f.token is not None and bool(getattr(perm, "is_token", False)) != f.token:
        return False
    keywords = {k.value for k in perm.keywords}
    if not f.with_keywords <= keywords or keywords & f.without_keywords:
        return False
    if f.state and not any(_in_state(perm, s) for s in f.state):
        return False
    return all((perm.counter_count(kind) > 0) == has
               for kind, has in f.counters)


def state_condition_supported(cond: Condition) -> bool:
    """The STATE conditions evaluated here, each compared with a printed
    number: a count of the permanents you control that a battlefield
    description admits (metalcraft's "artifacts you control", "a red
    permanent other than ~", "a creature with a +1/+1 counter on it"), and
    the number of card types among cards in your graveyard (delirium's)."""
    if not isinstance(cond, Condition) or cond.kind is not ConditionKind.STATE:
        return False
    if cond.op not in _OPS or _literal(cond.n) is None or cond.ref is not None \
            or cond.payer is not None or cond.cost is not None or cond.children:
        return False
    if cond.pred == "count":
        f = cond.filter
        return description_supported(f) and f.controller == "you"
    if cond.pred == "card_types":
        return _only(cond.filter, zone="graveyard", owner="you")
    if cond.pred == "devotion":
        f = cond.filter
        return (isinstance(f, CardFilter) and bool(f.colors)
                and f.colors <= _COLOR_LETTERS
                and _set_fields(f) <= {"colors"})
    return False


def _set_fields(f: CardFilter) -> set:
    """The fields a description sets, its printed text aside."""
    return {k for k, _ in f.as_tuple()} - {"raw"}


def devotion(game: Any, player_idx: int, colors: frozenset) -> int:
    """`player_idx`'s devotion to `colors` (CR 700.5): the mana symbols of
    any of those colours among the mana costs of the permanents they
    control. A Phyrexian pip is a symbol of its colour, a hybrid pip counts
    once if any of its colours is named (CR 700.5a); generic and colourless
    symbols count toward no colour."""
    by_letter = {"W": "white", "U": "blue", "B": "black", "R": "red",
                 "G": "green"}
    total = 0
    for perm in game.players[player_idx].battlefield:
        cost = getattr(perm.template, "mana_cost", None)
        if cost is None:
            continue
        total += sum(getattr(cost, by_letter[c], 0) for c in colors)
        total += sum(1 for pip in getattr(cost, "hybrid", ())
                     if any(o in colors for o in pip))
    return total


def state_condition_holds(game: Any, controller: int, cond: Condition,
                          source: Any = None) -> bool:
    """Evaluate a supported STATE condition for `controller` now. A count
    reads the description owner (`permanent_described`); "other" leaves
    out `source`, the object whose ability the condition belongs to."""
    if cond.pred == "count":
        count = permanents_controlled(
            game, controller,
            lambda p: permanent_described(p, cond.filter, source))
    elif cond.pred == "devotion":
        count = devotion(game, controller, cond.filter.colors)
    else:
        count = graveyard_card_types(game, controller)
    return _OPS[cond.op](count, _literal(cond.n))


# The turn's life tallies a HISTORY condition reads ("if you gained life this
# turn", "if an opponent lost life this turn"), kept by the life owners
# (`damage.lose_life`, the lifelink hook, `PermanentEffects.gain_life`) and
# cleared for every player as each turn begins.
_LIFE_TALLIES = {"life_gained": "life_gained_this_turn",
                 "life_lost": "life_lost_this_turn"}
_HISTORY_PLAYER_SETS = frozenset({SelectorKind.PLAYER, SelectorKind.OPPONENTS,
                                  SelectorKind.ALL_PLAYERS})


def history_condition_supported(cond: Condition) -> bool:
    """The HISTORY conditions evaluated here: the life a player gained or
    lost this turn, compared with a printed number ("if you gained 3 or more
    life this turn", "if an opponent lost life this turn"), for you, an
    opponent or any player."""
    if not isinstance(cond, Condition) or cond.kind is not ConditionKind.HISTORY:
        return False
    if cond.pred not in _LIFE_TALLIES or cond.op not in _OPS \
            or _literal(cond.n) is None or cond.filter is not None \
            or cond.ref is not None or cond.cost is not None or cond.children:
        return False
    p = cond.payer
    return (isinstance(p, Selector) and p.kind in _HISTORY_PLAYER_SETS
            and p.player is None and p.filter is None and p.obj is None)


def history_condition_holds(game: Any, controller: int,
                            cond: Condition) -> bool:
    """Evaluate a supported HISTORY condition for `controller` now: some
    player of the set it names ("you", "an opponent", "a player") has the
    turn's tally its predicate reads at the printed bound."""
    tally = _LIFE_TALLIES[cond.pred]
    kind = cond.payer.kind
    players = [i for i in range(len(game.players))
               if kind is SelectorKind.ALL_PLAYERS
               or (i == controller) == (kind is SelectorKind.PLAYER)]
    n = _literal(cond.n)
    return any(_OPS[cond.op](getattr(game.players[i], tally), n)
               for i in players)


# The counters an OBJECT condition on its own permanent counts: one kind,
# or "*" for counters of any kind ("as long as ~ has two or more counters
# on it").
_ANY_COUNTER = "*"
_COMBINATOR_KINDS = frozenset({ConditionKind.ALL_OF, ConditionKind.ANY_OF,
                               ConditionKind.NOT})


def self_static_condition_supported(cond: Optional[Condition]) -> bool:
    """The printed conditions a permanent's own static ability may carry and
    be applied under (CR 611.3a): its controller's turn, a supported STATE
    condition, "~ has N or more <kind> counters on it", and any
    conjunction, disjunction or negation of those."""
    if cond is None:
        return True
    if cond.kind in _COMBINATOR_KINDS:
        return bool(cond.children) and all(
            self_static_condition_supported(c) for c in cond.children)
    if cond.kind is ConditionKind.TURN:
        return rule_condition_supported(cond)
    if cond.kind is ConditionKind.STATE:
        return state_condition_supported(cond)
    if cond.kind is ConditionKind.OBJECT and cond.pred == "counters":
        f, r = cond.filter, cond.ref
        return (getattr(r, "kind", None) is RefKind.SELF
                and cond.op in _OPS and _literal(cond.n) is not None
                and isinstance(f, CardFilter) and len(f.counters) == 1
                and f.counters[0][1] is True
                and _set_fields(f) <= {"counters", "zone"}
                and f.zone in ("", "battlefield"))
    return False


def self_static_condition_holds(game: Any, controller: int,
                                cond: Optional[Condition],
                                source: Any) -> bool:
    """Does a supported condition of `source`'s own static ability hold
    now, for its controller?"""
    if cond is None:
        return True
    if cond.kind is ConditionKind.ALL_OF:
        return all(self_static_condition_holds(game, controller, c, source)
                   for c in cond.children)
    if cond.kind is ConditionKind.ANY_OF:
        return any(self_static_condition_holds(game, controller, c, source)
                   for c in cond.children)
    if cond.kind is ConditionKind.NOT:
        return not self_static_condition_holds(game, controller,
                                               cond.children[0], source)
    if cond.kind is ConditionKind.TURN:
        return rule_condition_holds(game, controller, cond)
    if cond.kind is ConditionKind.STATE:
        return state_condition_holds(game, controller, cond, source)
    kind = cond.filter.counters[0][0]
    if kind == _ANY_COUNTER:
        count = sum(n for _, n in source.last_known().counters)
    else:
        count = source.counter_count(kind)
    return _OPS[cond.op](count, _literal(cond.n))


# The TURN predicates a rule effect's condition may carry (CR 611.3a: a
# static rule applies while its printed condition holds).
_TURN_PREDICATES = frozenset({"your_turn", "not_your_turn"})


def rule_condition_supported(cond: Optional[Condition]) -> bool:
    """Can a rule effect carry this printed condition? None (no condition)
    and the TURN predicates `rule_condition_holds` evaluates."""
    return cond is None or (cond.kind is ConditionKind.TURN
                            and cond.pred in _TURN_PREDICATES)


def rule_condition_holds(game: Any, controller: int,
                         cond: Optional[Condition]) -> bool:
    """Does a supported rule-effect condition hold now? "your turn" is
    the turn of the effect's controller (CR 500.1, the active player)."""
    if cond is None:
        return True
    yours = game.active_player == controller
    return yours if cond.pred == "your_turn" else not yours


def quantity_supported(q: Optional[Quantity]) -> bool:
    """The quantities evaluated here: domain over lands you control, and
    the number of permanents of some card types (or of a subtype) you
    control."""
    if not isinstance(q, Quantity) or q.ref is not None or q.stat is not None \
            or q.counter_kind is not None or q.event is not None:
        return False
    if q.kind is QuantityKind.BASIC_LAND_TYPES:
        return q.player in ("any", "you") and _only(
            q.filter, types=("land",), controller="you")
    if q.kind is QuantityKind.COUNT:
        return q.player in ("any", "you") and _controlled_count_filter(
            q.filter)
    return False


def quantity_value(game: Any, controller: int, q: Quantity) -> int:
    """Evaluate a supported quantity for `controller` now (CR 608.2h: the
    number is determined once, as the effect applies)."""
    if q.kind is QuantityKind.COUNT:
        return permanents_controlled(game, controller,
                                     lambda p: _counted(p, q.filter))
    return basic_land_types(game, controller)


def amount_supported(a: Optional[Amount]) -> bool:
    """The amounts evaluated here: a printed number, X (the value chosen
    on casting, CR 107.3), "X, where X is <quantity>" / "equal to
    <quantity>" over a supported quantity, and a printed multiple of a
    supported amount ("twice the number of ...")."""
    if not isinstance(a, Amount) or a.ref is not None or a.rounding \
            or a.evenly:
        return False
    if a.kind is AmountKind.LITERAL:
        n = _literal(a)
        return n is not None and n >= 0 and a.quantity is None and a.inner is None
    if a.kind is AmountKind.X:
        return a.n == 1 and a.quantity is None and a.inner is None
    if a.kind is AmountKind.X_DEFINED:
        return a.quantity is None and amount_supported(a.inner)
    if a.kind is AmountKind.EQUAL_TO:
        return a.inner is None and quantity_supported(a.quantity)
    if a.kind is AmountKind.MULTIPLY:
        return (isinstance(a.n, int) and not isinstance(a.n, bool)
                and a.n >= 0 and a.quantity is None
                and amount_supported(a.inner))
    return False


def amount_value(game: Any, controller: int, a: Amount, x_value: int = 0) -> int:
    """Evaluate a supported amount for `controller` now."""
    if a.kind is AmountKind.LITERAL:
        return a.n
    if a.kind is AmountKind.X:
        return max(0, x_value)
    if a.kind is AmountKind.X_DEFINED:
        return amount_value(game, controller, a.inner, x_value)
    if a.kind is AmountKind.MULTIPLY:
        return a.n * amount_value(game, controller, a.inner, x_value)
    return quantity_value(game, controller, a.quantity)


# ── Legacy adapters (deleted with their callers) ──────────────────────

def scaler_count(game: Any, controller: int, shape, source=None) -> int:
    """The count a legacy "for each <X>" scaler shape names
    (`clause_resolver._scaler_shape`): ('opponents_lost_life',) or
    ('you_control', word, other) over the permanents' current types."""
    if shape[0] == 'opponents_lost_life':
        return opponents_who_lost_life(game, controller)
    _, word, other = shape

    def matches(perm) -> bool:
        types = {t.value for t in perm.effective_card_types}
        subtypes = {s.lower() for s in perm.effective_subtypes}
        return word in types or word == 'permanent' or word in subtypes

    return permanents_controlled(game, controller, matches,
                                 exclude=source if other else None)


def direct_damage_condition_met(game: Any, controller: int, condition) -> bool:
    """Whether a burn spell's legacy upgrade label holds for its caster
    right now: delirium (card types in the graveyard) or metalcraft
    (artifacts controlled, by printed type)."""
    if condition == 'delirium':
        return graveyard_card_types(game, controller) >= DELIRIUM_CARD_TYPES
    if condition == 'metalcraft':
        return permanents_controlled(
            game, controller,
            lambda c: CardType.ARTIFACT in c.template.card_types) >= METALCRAFT_ARTIFACTS
    return False


def effective_direct_damage(game: Any, controller: int, template) -> int:
    """The damage a conditional burn spell deals RIGHT NOW: the printed
    upgrade amount when its condition holds (CR 608.2), else the base
    amount. The one evaluator the resolution handler and the AI's
    `burn_damage` accessor share, so the engine and the AI agree on how
    much a delirium or metalcraft burn spell deals."""
    dd = getattr(template, 'direct_damage_data', None) or {}
    base = dd.get('amount', 0) or 0
    up = dd.get('upgrade_amount')
    if up and direct_damage_condition_met(game, controller, dd.get('upgrade_condition')):
        return int(up)
    return int(base)
