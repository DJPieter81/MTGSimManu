"""Mana a player's permanents add on their next turn.

`position_value` prices mana available now. A mana engine adds mana on
future turns instead, so it is credited here as the expected extra mana
for the next turn, read from public information only — hand size and
library composition (a deck's contents are known, its order and the
hand's contents are not):

* an extra land drop (CR 305.2 allows one land per turn; the typed
  `extra_land_drops` field adds more) yields one more land, and so one
  more mana, for each extra drop a land in hand is expected to fill;
* a static cost reducer (`cost_reduction_rule`) saves its amount, capped
  at each spell's generic cost, on every matching spell expected in hand
  (`engine.oracle_resolver._cost_rule_applies` is the single matcher).

`ai.clock.position_value` turns the result into value at the same
per-mana rate as mana now, discounted by `urgency_factor`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.game_state import GameState

NORMAL_LAND_DROPS = 1   # CR 305.2: one land per turn before any extra drop


def engine_mana_next_turn(game: "GameState", player_idx: int) -> float:
    """Expected extra mana `player_idx`'s permanents add on their next
    turn (see the module docstring)."""
    player = game.players[player_idx]
    library = list(player.library)
    hand_size = len(player.hand)
    if not library or not hand_size:
        return 0.0
    templates = [c.template for c in library]
    lands_expected = hand_size * sum(t.is_land for t in templates) / len(templates)

    extra_drops = sum(getattr(p.template, 'extra_land_drops', 0) or 0
                      for p in player.battlefield)
    mana = min(float(extra_drops), max(0.0, lands_expected - NORMAL_LAND_DROPS))

    from engine.oracle_resolver import _cost_rule_applies
    for perm in player.battlefield:
        rule = getattr(perm.template, 'cost_reduction_rule', None)
        if not rule:
            continue
        saved = sum(min(rule['amount'], t.mana_cost.generic)
                    for t in templates
                    if not t.is_land and _cost_rule_applies(rule, t))
        mana += hand_size * saved / len(templates)
    return mana
