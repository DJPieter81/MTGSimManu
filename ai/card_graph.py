"""The card-interaction graph of a deck: which card does what for which.

A combo is a line through the relations between cards. This card's tutor
finds that card, this spell returns that creature, this permanent reduces
those spells' cost, this spell lets those cards be cast from the
graveyard. The AI recomputed each relation ad hoc in about a dozen
predicates, and part of each deck's plan was hand-declared in gameplan
JSON. Read one card at a time, no plan sees a multi-step line.

This module is one typed index over those relations, built once per deck.
Every edge is decided by the predicate that already owns the relation, so
the graph re-implements none of them:

- RETURNS: a returner puts a creature card from a graveyard onto the
  battlefield (`ai.card_classes.deck_can_return`, the returner's parsed
  graveyard requirement).
- TUTORS: a library search admits the card (`effect_executors.
  library_search` with the engine's own description matcher, `effect_
  conditions.permanent_described`). An X bound ("mana value X or less")
  is the caster's choice (CR 107.3), so it is read at the deck's largest
  mana value.
- FETCHES_OUTSIDE: the spell brings a card from outside the game, and so
  reaches the sideboard (`effect_executors.brings_from_outside_the_game`,
  CR 400.11a).
- REDUCES_COST: a permanent's own cost-reduction rules name the spell
  (`ai.effective_cmc.reducer_reduction`, CR 601.2f).
- GRANTS_GRAVEYARD_CAST: a graveyard-wide flashback grant reaches the
  deck's instants and sorceries (`ai.ev_evaluator._grants_graveyard_
  flashback`, CR 702.34a).

The graph is read-only and deterministic: it changes no decision. The line
planner that reads it is a later unit
(docs/proposals/2026-11-08_card_interaction_graph_and_line_planner.md).
"""
from __future__ import annotations

import dataclasses
import enum
from typing import Dict, FrozenSet, Iterable, List, Tuple


class Relation(enum.Enum):
    RETURNS = "returns"
    TUTORS = "tutors"
    FETCHES_OUTSIDE = "fetches_outside"
    REDUCES_COST = "reduces_cost"
    GRANTS_GRAVEYARD_CAST = "grants_graveyard_cast"


@dataclasses.dataclass(frozen=True)
class Edge:
    """`source` does `relation` for `target` (both card templates)."""
    source: object
    relation: Relation
    target: object


@dataclasses.dataclass(frozen=True)
class CardGraph:
    """The relations among one deck's cards (main deck and sideboard)."""
    edges: Tuple[Edge, ...]

    def from_card(self, template, relation: Relation = None) -> List[object]:
        """The targets `template` reaches, by one relation or any."""
        return [e.target for e in self.edges if e.source is template
                and (relation is None or e.relation is relation)]

    def to_card(self, template, relation: Relation = None) -> List[object]:
        """The sources that reach `template`, by one relation or any."""
        return [e.source for e in self.edges if e.target is template
                and (relation is None or e.relation is relation)]

    def counts(self) -> Dict[Relation, int]:
        out = {r: 0 for r in Relation}
        for e in self.edges:
            out[e.relation] += 1
        return out


def _held(template):
    """A template held as a card object (in a library), for the owners
    that read an object's characteristics (colour, counters, types)."""
    from engine.cards import CardInstance
    return CardInstance(template=template, owner=0, controller=0,
                        instance_id=0, zone="library")


def _search_filters(template) -> List[object]:
    """The descriptions the card's library searches name, from its parsed
    hosts (the spell's, and the abilities' for a permanent)."""
    from engine.effect_executors import library_search
    from engine.effect_spec import iter_specs
    effects = getattr(template, "effects", None)
    if effects is None:
        return []
    out = []
    for face in getattr(effects, "faces", ()) or ():
        for host in face:
            for spec in iter_specs(host.specs):
                if library_search(spec):
                    out.append(spec.filter)
    return out


def _searches_admit(filters, template, x_value: int) -> bool:
    from engine import effect_conditions as cond
    held = _held(template)
    for f in filters:
        g = dataclasses.replace(f, zone="battlefield")
        try:
            bounds = cond.bound_values(None, 0, g, x_value=x_value)
        except (AttributeError, TypeError, KeyError):
            bounds = None
        try:
            if cond.permanent_described(held, g, None, bounds):
                return True
        except (AttributeError, TypeError, KeyError):
            continue
    return False


def build(main: Iterable[object], sideboard: Iterable[object] = ()
          ) -> CardGraph:
    """The graph of a deck given as card templates (one per distinct
    card)."""
    from ai.card_classes import deck_can_return
    from ai.effective_cmc import reducer_reduction
    from ai.ev_evaluator import _grants_graveyard_flashback
    from engine.effect_executors import brings_from_outside_the_game

    main = _distinct(main)
    side = _distinct(sideboard)
    every = _distinct(main + side)
    top_mv = max((t.cmc or 0 for t in every), default=0)
    edges: List[Edge] = []
    for src in every:
        filters = _search_filters(src)
        outside = brings_from_outside_the_game(src)
        grants = _grants_graveyard_flashback((src.oracle_text or "").lower())
        reducer = (not (src.is_instant or src.is_sorcery)
                   and getattr(src, "cost_reduction_rules", None))
        for tgt in every:
            if tgt is src:
                continue
            if tgt.is_creature and deck_can_return(tgt, [src]):
                edges.append(Edge(src, Relation.RETURNS, tgt))
            if filters and tgt in main and _searches_admit(
                    filters, tgt, top_mv):
                edges.append(Edge(src, Relation.TUTORS, tgt))
            if outside and tgt in side:
                edges.append(Edge(src, Relation.FETCHES_OUTSIDE, tgt))
            if reducer and not tgt.is_land and reducer_reduction(
                    _held(src), tgt) > 0:
                edges.append(Edge(src, Relation.REDUCES_COST, tgt))
            if grants and (tgt.is_instant or tgt.is_sorcery):
                edges.append(Edge(src, Relation.GRANTS_GRAVEYARD_CAST, tgt))
    return CardGraph(tuple(edges))


def _distinct(templates: Iterable[object]) -> List[object]:
    seen: Dict[int, object] = {}
    for t in templates:
        if t is not None:
            seen.setdefault(id(t), t)
    return list(seen.values())


def for_deck(deck: Dict[str, Dict[str, int]], card_db) -> CardGraph:
    """The graph of a registered deck entry ({'mainboard', 'sideboard'})."""
    def _templates(section: str) -> List[object]:
        return [card_db.get_card(n) for n in (deck.get(section) or {})]
    return build(_templates("mainboard"), _templates("sideboard"))


__all__: FrozenSet[str] = frozenset(
    {"Relation", "Edge", "CardGraph", "build", "for_deck"})
