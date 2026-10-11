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

from .cards import CardType
from .effect_spec import (Amount, AmountKind, CardFilter, Condition,
                          ConditionKind, Quantity, QuantityKind)


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


def state_condition_supported(cond: Condition) -> bool:
    """The STATE conditions evaluated here: a count of permanents of some
    card types you control (metalcraft's shape) and the number of card
    types among cards in your graveyard (delirium's), each compared with a
    printed number."""
    if not isinstance(cond, Condition) or cond.kind is not ConditionKind.STATE:
        return False
    if cond.op not in _OPS or _literal(cond.n) is None or cond.ref is not None \
            or cond.payer is not None or cond.cost is not None or cond.children:
        return False
    if cond.pred == "count":
        return _controlled_types_filter(cond.filter)
    if cond.pred == "card_types":
        return _only(cond.filter, zone="graveyard", owner="you")
    return False


def state_condition_holds(game: Any, controller: int, cond: Condition) -> bool:
    """Evaluate a supported STATE condition for `controller` now."""
    if cond.pred == "count":
        types = cond.filter.types
        count = permanents_controlled(
            game, controller,
            lambda p: any(t.value in types for t in p.effective_card_types))
    else:
        count = graveyard_card_types(game, controller)
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
    """The quantities evaluated here: domain over lands you control."""
    if not isinstance(q, Quantity) or q.ref is not None or q.stat is not None \
            or q.counter_kind is not None or q.event is not None:
        return False
    if q.kind is QuantityKind.BASIC_LAND_TYPES:
        return q.player in ("any", "you") and _only(
            q.filter, types=("land",), controller="you")
    return False


def quantity_value(game: Any, controller: int, q: Quantity) -> int:
    """Evaluate a supported quantity for `controller` now."""
    return basic_land_types(game, controller)


def amount_supported(a: Optional[Amount]) -> bool:
    """The amounts evaluated here: a printed number, X (the value chosen
    on casting, CR 107.3), and "X, where X is <quantity>" / "equal to
    <quantity>" over a supported quantity."""
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
    return False


def amount_value(game: Any, controller: int, a: Amount, x_value: int = 0) -> int:
    """Evaluate a supported amount for `controller` now."""
    if a.kind is AmountKind.LITERAL:
        return a.n
    if a.kind is AmountKind.X:
        return max(0, x_value)
    if a.kind is AmountKind.X_DEFINED:
        return amount_value(game, controller, a.inner, x_value)
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
