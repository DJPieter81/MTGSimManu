"""Whether to pay an "unless" cost (CR 118.12).

"Sacrifice this enchantment unless you pay {E}" offers its controller a
payment; paid, the instruction does not happen. Declining is no "do
nothing": the instruction happens. So the decision compares the two
positions -- after paying, and after the instruction -- in the one
currency (`ai.clock.position_value` through `evaluate_board`), and pays
exactly when paying is worth more.

The projections:
- Paying spends what the cost names: energy counters (CR 107.14) or mana.
- Declining performs the guarded instruction. A sacrifice of the
  ability's own source (CR 701.21a) is read off the board without the
  source and with the cards it exiled until it left back on their
  owners' battlefields (CR 610.3) -- the hypothetical-board technique
  `ai.permanent_threat` uses (take the permanent off, snapshot, put it
  back). Any other instruction has no term yet and projects the board as
  it is.
"""
from __future__ import annotations

from typing import Any


def project_unless_paid(snap: Any, energy: int, mana: int) -> Any:
    """The position after paying an "unless" cost of `energy` counters or
    `mana` mana."""
    return snap.model_copy(update={"my_energy": snap.my_energy - energy,
                                   "my_mana": snap.my_mana - mana})


def project_unless_declined(game: Any, payer: int, source: Any, spec: Any,
                            snap: Any) -> Any:
    """The position after declining: the guarded instruction `spec`
    performed (see module docs)."""
    from engine.effect_executors import sacrifice_self
    if spec is None or source is None or not sacrifice_self(spec) \
            or source.zone != "battlefield":
        return snap
    from ai.ev_evaluator import snapshot_from_game
    holder = game.players[source.controller].battlefield
    if not any(c is source for c in holder):
        return snap
    back = [c for c, zone in game.zone_mgr.linked_to(source)
            if zone == "battlefield"]
    idx = next(i for i, c in enumerate(holder) if c is source)
    holder.pop(idx)
    added = []
    try:
        for card in back:
            game.players[card.owner].battlefield.append(card)
            added.append(card)
        return snapshot_from_game(game, payer)
    finally:
        for card in added:
            game.players[card.owner].battlefield.remove(card)
        holder.insert(idx, source)
