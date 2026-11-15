"""How many lands an instruction puts from its controller's hand onto the
battlefield, and what a land put is worth (AI, from CR 305.4).

"[You may] put a land card from your hand onto the battlefield [tapped]"
can be a spell's instruction, a permanent's enter trigger or an
activation. It puts one land without spending a land drop (CR 305.4).
This module is the one owner the AI's two readers share: the cast
projection (`ai.ev_evaluator._project_spell`) and the activation scorer
(`ai.activation_ev.activation_candidates`).

* **Count.** One land per instruction of the typed shape the executor
  performs (`engine.effect_executors.land_from_hand`), on the hosts the
  engine's carriers take:
  - for a cast, the spell host a landed spell family takes, and the
    enter-trigger hosts the enter carrier takes;
  - for an activation, the host its activation family takes.

  No more lands are put than the hand holds. An instruction no carrier
  takes is worth nothing here, because the engine will not perform it.
* **Worth.** The land card's use is the land it becomes, as its land drop
  would be, so no card is charged. Each land is
  `ETB_LAND_FROM_HAND_MANA_VALUE` mana: its mana on every turn after,
  which is the snapshot's own land-entry constant.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

from ai.scoring_constants import ETB_LAND_FROM_HAND_MANA_VALUE


def lands_put(game: Any, player_idx: int, hosts: Iterable[Any]) -> int:
    """Land cards `hosts` put from their controller's hand onto the
    battlefield: one per instruction that puts one, no more than the land
    cards that hand holds. With no `game`, the hand's lands are not
    capped."""
    from engine.effect_executors import land_from_hand
    from engine.effect_spec import iter_specs
    n = sum(1 for h in hosts for s in iter_specs(h.specs)
            if land_from_hand(s))
    if not n or game is None:
        return n
    held = sum(1 for c in game.players[player_idx].hand
               if c.template.is_land)
    return min(n, held)


def cast_hosts(template: Any) -> Tuple[Any, ...]:
    """The hosts the engine performs when `template` is cast and resolves,
    of a card with a MOVE instruction:
    - its spell host, when a landed spell family takes it
      (`effect_carrier.spell_family`);
    - the enter-trigger hosts the enter carrier takes for the face it
      enters with (`effect_carrier.etb_plan`)."""
    from engine.effect_carrier import etb_plan, spell_family
    from engine.effect_spec import Verb
    effects = template.effects
    if Verb.MOVE not in effects.verbs:
        return ()
    hosts = []
    if spell_family(template, effects) is not None:
        hosts.append(effects.spell(0))
    hosts.extend(h for h, _family in etb_plan(effects.front()) or ())
    return tuple(hosts)


def lands_put_on_cast(game: Any, player_idx: int, template: Any) -> int:
    """Land cards casting `template` puts from its caster's hand onto the
    battlefield."""
    return lands_put(game, player_idx, cast_hosts(template))


def land_put_mana(n: int) -> float:
    """The mana `n` lands put from hand deliver: each land's mana on every
    turn after (`ETB_LAND_FROM_HAND_MANA_VALUE`)."""
    return n * ETB_LAND_FROM_HAND_MANA_VALUE


def land_put_projection(n: int) -> Dict[str, float]:
    """The snapshot fields `n` lands put from hand move, as deltas: one
    more land each, and its mana. The hand is not charged: the land card's
    use is the land it becomes."""
    return {"my_total_lands": n, "my_mana": land_put_mana(n)}
