"""How many cards an instruction returns from its controller's graveyard to
their hand, and where that is counted (AI, from CR 400.7, 608.2b).

"[You may] return [up to N] target <card> from your graveyard to your hand"
can be:
- a spell's instruction;
- a permanent's enter trigger;
- a loyalty line.

Each card it returns is one more card in its controller's hand. This module
is the one owner of the count, shared by the cast projection
(`ai.ev_evaluator._project_spell`) and a loyalty line's value
(`ai.pw_ability.loyalty_line_value`). Both count the cards on the
snapshot's card term (`my_hand_size`), the term a drawn card counts on. A
returned card is not drawn (CR 121.1), so `cards_drawn_this_turn` does not
move.

* **Count.** One instruction of the typed shape the engine performs
  (`engine.effect_executors.graveyard_to_hand`) returns its target count,
  but no more than the cards its requirement admits in that graveyard now
  (`target_solver.enumerate_legal_targets`). Nothing to return counts 0.
* **Hosts.** For a cast, the hosts the engine performs when the card is
  cast and resolves (`ai.land_put.cast_hosts`). For a loyalty line, the
  walker's own line host.
"""
from __future__ import annotations

from typing import Any, Iterable


def cards_returned(game: Any, player_idx: int, hosts: Iterable[Any],
                   source: Any = None) -> int:
    """Cards `hosts` return from `player_idx`'s graveyard to their hand: per
    instruction of the executor's shape, its count, but no more than the
    cards its requirement admits there now."""
    from engine.effect_executors import graveyard_to_hand
    from engine.effect_spec import iter_specs
    from engine.target_solver import enumerate_legal_targets
    n = 0
    for h in hosts:
        if h is None:
            continue
        for s in iter_specs(h.specs):
            if not graveyard_to_hand(s):
                continue
            legal = enumerate_legal_targets(game, player_idx, s.target,
                                            source=source)
            n += min(s.target.count_max, len(legal))
    return n


def cards_returned_on_cast(game: Any, player_idx: int, template: Any) -> int:
    """Cards casting `template` returns from its caster's graveyard to their
    hand."""
    from ai.land_put import cast_hosts
    return cards_returned(game, player_idx, cast_hosts(template))
