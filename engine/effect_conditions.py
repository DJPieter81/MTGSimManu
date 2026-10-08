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

from typing import Any, Callable, Optional

from .cards import CardType


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
