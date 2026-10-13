"""Where a spell that removes a card from a graveyard is aimed, and what the
removal is worth (AI).

A spell whose resolution removes its target from the graveyard it was
chosen in -- "exile target card from a graveyard"; "choose target card in a
graveyard ... search its owner's graveyard, hand, and library for any
number of cards with the same name as that card and exile them" (CR
608.2c, 701.19; the typed host's shape is
`ai.predicates.graveyard_target_removal`) -- takes from the card's owner
that card and, for a same-name exile, every card of its name in the zones
it searches.

* **Worth aiming at.**  An opponent's card whose name their published
  gameplan declares a keystone (critical pieces, mulligan keys,
  always-early: `ai.discard_advisor._declared_keystones`, the fields the
  forced-discard picker reads), or a card still a resource to them in
  their graveyard (`ai.predicates.graveyard_fuel`: castable from there, a
  revealed reanimation target, fuel for a graveyard-size consumer).
  Anything else -- a spent fetchland -- takes nothing its owner will miss.
* **Ranking.**  Among those, the card the caster most wants out of its
  owner's resources: the hand attack's ranking
  (`ai.ev_evaluator.score_card_for_opponent_strip`), then the more cards
  taken, then graveyard order.
* **Value.**  What the cards taken are worth to their owner, in the
  hand-denial currency (`ai.hand_denial._denied_value`): each graveyard
  copy that is fuel; each copy in the owner's hidden pool -- hand and
  library, one pool as `ai.bhi` and `ai.hand_denial` read it, never split
  by the zone a copy actually sits in -- by the chance it is in their hand;
  plus the owner's plan-disruption premium for the name
  (`ai.engine_disruption.engine_disruption_value`), counted once: losing
  every copy delays the plan at least as much as losing one.

A spell whose every instruction is the removal (the same-name exile's
choose / search / exile / shuffle) is not cast at a card not worth aiming
at; one with a rider ("... Draw a card.") keeps the engine's printed
default aim, its rider being worth the cast whatever it exiles.  A host
with a second target slot is left to the picks that know it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, FrozenSet, List, Optional

if TYPE_CHECKING:
    from engine.cards import CardInstance, CardTemplate
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot


@dataclass(frozen=True)
class RemovalShape:
    """A spell's graveyard removal, read from its typed host."""
    requirement: Any               # the one target slot's TargetRequirement
    by_name: bool                  # a same-name exile (every copy goes)
    zones: FrozenSet[str]          # the owner's zones the exile reaches
    removal_only: bool             # every instruction is the removal


def removal_shape(template: "CardTemplate") -> Optional[RemovalShape]:
    """The removal shape of a one-target spell whose resolution removes
    its graveyard target, else None."""
    from engine.effect_spec import Verb, iter_specs
    from ai.predicates import graveyard_target_removal
    effects = getattr(template, "effects", None)
    host = effects.spell(0) if effects is not None else None
    if host is None or len(host.targets) != 1:
        return None
    found = graveyard_target_removal(host)
    if found is None:
        return None
    slot, by_name = found
    specs = list(iter_specs(host.specs))
    zones = frozenset({"graveyard"})
    if by_name:
        zones = next((frozenset(s.filter.zones) for s in specs
                      if s.verb is Verb.SEARCH and s.filter is not None
                      and s.filter.zones), zones)
    own = {Verb.CHOOSE, Verb.SEARCH, Verb.EXILE, Verb.SHUFFLE}
    return RemovalShape(host.targets[slot], by_name, zones,
                        all(s.verb in own for s in specs))


def cards_taken(game: "GameState", card: "CardInstance",
                shape: RemovalShape) -> List["CardInstance"]:
    """The cards the removal takes when aimed at `card`: the card itself,
    or every card of its name in its owner's zones the exile reaches."""
    if not shape.by_name:
        return [card]
    owner = game.players[card.owner]
    return [c for zone in sorted(shape.zones)
            for c in getattr(owner, zone, ()) if c.name == card.name]


def worth_aiming_at(game: "GameState", caster_idx: int,
                    card: "CardInstance") -> bool:
    """An opponent's card whose name is a keystone of their published plan,
    or a card still a resource to them in their graveyard."""
    from ai.discard_advisor import _declared_keystones
    from ai.predicates import graveyard_fuel
    if card.owner == caster_idx:
        return False
    if card.name in _declared_keystones(game, card.owner):
        return True
    return any(c is card for c in graveyard_fuel(game, card.owner))


def removal_value(game: "GameState", card: "CardInstance",
                  shape: RemovalShape, snap: "EVSnapshot") -> float:
    """What the cards the removal takes when aimed at `card` are worth to
    their owner (module docstring, **Value**)."""
    from ai.engine_disruption import engine_disruption_value
    from ai.hand_denial import _denied_value
    from ai.predicates import graveyard_fuel
    owner = game.players[card.owner]
    fuel = {id(c) for c in graveyard_fuel(game, card.owner)}
    pool = len(owner.hand) + len(owner.library)
    in_hand = len(owner.hand) / pool if pool else 0.0
    hidden = {id(c) for zone in (owner.hand, owner.library) for c in zone}
    value = 0.0
    for c in cards_taken(game, card, shape):
        if id(c) in fuel:
            value += _denied_value(c, snap)
        elif id(c) in hidden:
            value += in_hand * _denied_value(c, snap)
    return value + engine_disruption_value(card, owner, game)


def _rank(game: "GameState", snap: "EVSnapshot", card: "CardInstance",
          shape: Optional[RemovalShape]) -> tuple:
    """The ranking key (module docstring, **Ranking**)."""
    from ai.ev_evaluator import score_card_for_opponent_strip
    from ai.hand_denial import _victim_gameplan
    taken = cards_taken(game, card, shape) if shape is not None else [card]
    return (score_card_for_opponent_strip(
        card, snap, _victim_gameplan(game, card.owner)), len(taken))


def pick_removals(game: "GameState", caster_idx: int, pool: List[Any],
                  n: int, need: int) -> List[Any]:
    """The cards of `pool` a graveyard exile whose targets are its
    controller's pick at resolution takes (an enter trigger's, A36), up to
    `n`: the opponents' cards worth aiming at, best first; then, only to
    fill the `need` its requirement demands (CR 603.3d), the opponents'
    other cards, then the caster's own -- a card still a resource to it
    last."""
    from ai.ev_evaluator import snapshot_from_game
    from ai.predicates import graveyard_fuel
    snap = snapshot_from_game(game, caster_idx)
    order = {id(c): k for k, c in enumerate(pool)}
    worthy = sorted((c for c in pool if worth_aiming_at(game, caster_idx, c)),
                    key=lambda c: (_rank(game, snap, c, None), -order[id(c)]),
                    reverse=True)
    picked = worthy[:n]
    if len(picked) >= min(need, n):
        return picked
    mine_fuel = {id(c) for c in graveyard_fuel(game, caster_idx)}
    rest = sorted((c for c in pool if all(c is not p for p in picked)),
                  key=lambda c: (c.owner == caster_idx, id(c) in mine_fuel,
                                 order[id(c)]))
    return picked + rest[:min(need, n) - len(picked)]


def choose_removal_target(game: "GameState", caster_idx: int,
                          spell: "CardInstance", snap: "EVSnapshot"
                          ) -> Optional["CardInstance"]:
    """The legal target worth aiming at that the caster most wants out of
    its owner's resources (module docstring, **Ranking**), or None."""
    from engine.target_solver import enumerate_legal_targets
    shape = removal_shape(spell.template)
    if shape is None:
        return None
    legal = enumerate_legal_targets(game, caster_idx, shape.requirement,
                                    exclude=spell, source=spell)
    worthy = [c for c in legal if worth_aiming_at(game, caster_idx, c)]
    if not worthy:
        return None
    order = {id(c): k for k, c in enumerate(legal)}
    return max(worthy, key=lambda c: (_rank(game, snap, c, shape),
                                      -order[id(c)]))
