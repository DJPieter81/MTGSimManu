"""The line planner: the shortest line from what a player holds to a payoff.

A combo is a sequence of plays, and per-card scoring sees one play at a
time. Faithful Mending is worth the creature it puts into the graveyard
for the Persist in hand, not its own two draws. Abhorrent Oculus's
"exile six cards from your graveyard" can exile that creature. This
module reads the relations between the player's cards (`ai.card_graph`,
each edge decided by the predicate that owns it) and finds the cheapest
line that reaches a payoff.

Version 1 covers the reanimation family (graph step 2 of
docs/proposals/2026-11-08_card_interaction_graph_and_line_planner.md):
- RETURN: a returner the player may play has a creature in the graveyard
  it can return (`Relation.RETURNS`);
- FILL, then RETURN: a filler puts a creature from the hand into the
  graveyard, then a returner returns it. A filler is a spell whose
  resolution lets its caster choose a card of its own hand to discard: a
  loot whose discard is not random (typed `loot_data`), or a hand attack
  that may target its caster and whose choose clause admits the creature
  (typed `hand_attack_data`, the engine's own `_targeted_discard_
  candidates`).

A line's cost is the AI's cost estimate of each play
(`effective_cmc.chain_cost`). Lines are ranked by the turn they complete,
then by the threat they return (`ev_evaluator.creature_threat_value`),
then by cost. A line the mana available now pays is a line
for this turn. A line whose first play is paid now is a line that
completes next turn. The planner is read-only: it decides nothing. Its
first consumer is the audit that measures how often the main phase's
choice disagrees with it (`tools/line_planner_audit.py`).
"""
from __future__ import annotations

import dataclasses
from typing import Any, List, Optional, Tuple

THIS_TURN = 0          # a line the mana available now pays completes now
NEXT_TURN = 1          # its first play now, the rest after the next untap


@dataclasses.dataclass(frozen=True)
class Line:
    """Plays in order (card objects the player may play), the payoff the
    line reaches, the line's total cost, and the turn it completes."""
    plays: Tuple[Any, ...]
    payoff: Any
    cost: int
    completes: int

    @property
    def first(self) -> Any:
        return self.plays[0]


def _graph(game: Any, player_idx: int):
    """The card graph of the cards the player holds anywhere, built once
    per game and player (the player's cards do not change identity)."""
    from ai.card_graph import build
    cache = getattr(game, "_card_graph_cache", None)
    if cache is None:
        cache = {}
        try:
            game._card_graph_cache = cache
        except AttributeError:
            pass
    me = game.players[player_idx]
    zones = ("hand", "library", "graveyard", "battlefield", "exile",
             "sideboard")
    templates = {id(c.template): c.template for z in zones
                 for c in (getattr(me, z, None) or ())}
    key = (player_idx, frozenset(templates))
    if key not in cache:
        cache[key] = build(list(templates.values()))
    return cache[key]


def _returns(graph, returner, creature) -> bool:
    from ai.card_graph import Relation
    return any(t is creature.template
               for t in graph.from_card(returner.template, Relation.RETURNS))


def _is_spell(card) -> bool:
    t = card.template
    return bool(t.is_instant or t.is_sorcery)


def _fills(filler, creature) -> bool:
    """Does casting `filler` let its caster put `creature` from the hand
    into the graveyard by its own choice?"""
    t = filler.template
    loot = getattr(t, "loot_data", None) or {}
    if loot.get("discard") and not loot.get("random"):
        return True
    attack = getattr(t, "hand_attack_data", None) or {}
    if attack.get("target") == "player" and attack.get("chooser") == "caster":
        from engine.oracle_resolver import _targeted_discard_candidates
        legal = _targeted_discard_candidates(
            [creature], attack.get("choose_clause") or "")
        return any(c is creature for c in legal)
    return False


def best_line(game: Any, player_idx: int) -> Optional[Line]:
    """The cheapest reanimation line from the cards the player may play,
    this turn's lines before next turn's, or None when there is none."""
    from ai.effective_cmc import chain_cost
    from ai.playable_cards import playable_cards
    me = game.players[player_idx]
    playable = [c for c in playable_cards(game, player_idx)
                if not c.template.is_land]
    mana = me.available_mana_estimate
    graph = _graph(game, player_idx)

    def cost(c) -> int:
        return chain_cost(c, game, player_idx)

    lines: List[Line] = []
    returners = [c for c in playable if _is_spell(c)]
    for r in returners:
        for creature in me.graveyard:
            if creature.template.is_creature and _returns(graph, r, creature):
                lines.append(Line((r,), creature, cost(r),
                                  THIS_TURN if cost(r) <= mana else NEXT_TURN))
    in_hand = [c for c in me.hand if c.template.is_creature]
    for creature in in_hand:
        for r in returners:
            if r is creature or not _returns(graph, r, creature):
                continue
            for f in playable:
                if f is r or f is creature or not _is_spell(f) \
                        or not _fills(f, creature):
                    continue
                total = cost(f) + cost(r)
                if cost(f) > mana or total >= cost(creature):
                    # A creature the player can cast for no more than the
                    # line costs is no reason to bin and return it.
                    continue
                lines.append(Line((f, r), creature, total,
                                  THIS_TURN if total <= mana else NEXT_TURN))
    if not lines:
        return None
    # A reanimation is worth what it returns: among the lines completing
    # soonest, the biggest threat (the AI's own measure,
    # `ev_evaluator.creature_threat_value`), then the cheapest.
    from ai.ev_evaluator import creature_threat_value, snapshot_from_game
    snap = snapshot_from_game(game, player_idx)
    threat = {id(ln.payoff): creature_threat_value(ln.payoff, snap)
              for ln in lines}
    return min(lines, key=lambda ln: (ln.completes, -threat[id(ln.payoff)],
                                      ln.cost, len(ln.plays)))
