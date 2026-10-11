"""Mana a player's permanents add on their next turn.

`position_value` prices mana available now. A mana engine adds mana on
future turns instead, so it is credited here as the expected extra mana
for the next turn, read from public information only — hand size and
library composition (a deck's contents are known, its order and the
hand's contents are not):

* an extra land drop (CR 305.2 allows one land per turn; the typed
  `extra_land_drops` field adds more) yields one more land, and so one
  more mana, for each extra drop a land in hand is expected to fill;
* a static cost reducer (`oracle_resolver.reduction_rules_of`: the rules
  of the face it shows) saves its amount, capped at each spell's generic
  cost, on every matching spell expected in hand
  (`engine.oracle_resolver._cost_rule_applies` is the single matcher).

`ai.clock.position_value` turns the result into value at the same
per-mana rate as mana now, discounted by `urgency_factor`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from engine.cards import CardTemplate
    from engine.game_state import GameState

NORMAL_LAND_DROPS = 1   # CR 305.2: one land per turn before any extra drop


def engine_mana_next_turn(game: "GameState", player_idx: int,
                          extra: Iterable["CardTemplate"] = (),
                          hand_delta: int = 0) -> float:
    """Expected extra mana `player_idx`'s permanents add on their next
    turn (see the module docstring). `extra` adds permanents not yet on
    the battlefield and `hand_delta` adjusts the hand size — the cast
    projection's view of the board after a spell resolves."""
    player = game.players[player_idx]
    library = list(player.library)
    hand_size = len(player.hand) + hand_delta
    if not library or hand_size <= 0:
        return 0.0
    templates = [c.template for c in library]
    permanents = [p.template for p in player.battlefield] + list(extra)
    lands_expected = hand_size * sum(t.is_land for t in templates) / len(templates)

    extra_drops = sum(getattr(t, 'extra_land_drops', 0) or 0 for t in permanents)
    mana = min(float(extra_drops), max(0.0, lands_expected - NORMAL_LAND_DROPS))

    from engine.oracle_resolver import _cost_rule_applies, reduction_rules_of
    # The reductions each permanent has now (the face it shows), and those
    # a permanent not yet on the battlefield would enter with.
    rules = [r for p in player.battlefield for r in reduction_rules_of(p)]
    rules += [r for t in extra for r in (t.cost_reduction_rules or ())]
    for rule in rules:
        saved = sum(min(rule['amount'], t.mana_cost.generic)
                    for t in templates
                    if not t.is_land and _cost_rule_applies(rule, t))
        mana += hand_size * saved / len(templates)
    return mana
