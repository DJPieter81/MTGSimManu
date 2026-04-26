"""Oracle-driven discovery of optional costs.

The engine inspects card templates (which derive their fields from
oracle text at DB-load time via `engine.oracle_parser`) and produces
typed `OptionalCost` descriptors.  The AI consumes these uniformly
via `decide_optional_cost` — there are NO mechanic-named callbacks.

Adding a new optional-cost mechanic means extending this module
(another conditional + apply lambdas), never adding a new callback.

This module is the engine→AI seam for optional payments.  Sites in
`engine/land_manager.py`, `engine/cast_manager.py`, etc. call
`offer_optional_costs(...)` instead of asking AI-specific yes/no
questions per mechanic.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from ai.schemas import CostDescriptor, EffectDescriptor, OptionalCost

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot


# ─────────────────────────────────────────────────────────────
# Snapshot deltas — pure functions over EVSnapshot
# ─────────────────────────────────────────────────────────────

def _snap_pay_life(snap: "EVSnapshot", amount: int) -> "EVSnapshot":
    """Subtract life from the controller side of an EVSnapshot."""
    snap.my_life = snap.my_life - amount
    return snap


def _snap_add_untapped_mana(snap: "EVSnapshot",
                             colors: tuple[str, ...]) -> "EVSnapshot":
    """Credit one extra untapped land producing the given colors.

    `evaluate_board` reads `my_mana` via `mana_clock_impact`, so
    incrementing it captures "I have one more mana available this
    turn" in the EV projection.  Per-color count is updated so
    colored-spell castability checks see the new colors too.
    """
    snap.my_mana = snap.my_mana + 1
    snap.my_total_lands = snap.my_total_lands + 1
    if not snap.my_mana_by_color:
        snap.my_mana_by_color = {}
    else:
        # `replace()` shallow-copies dataclass fields; we need a
        # fresh dict to avoid mutating the caller's snapshot
        snap.my_mana_by_color = dict(snap.my_mana_by_color)
    for c in colors:
        snap.my_mana_by_color[c] = snap.my_mana_by_color.get(c, 0) + 1
    return snap


def _snap_tapped_land(snap: "EVSnapshot") -> "EVSnapshot":
    """Credit a tapped land entering — adds total_lands but not mana."""
    snap.my_total_lands = snap.my_total_lands + 1
    return snap


# ─────────────────────────────────────────────────────────────
# Game-state apply functions — engine resolution
# ─────────────────────────────────────────────────────────────

def _game_pay_life(game: "GameState", player_idx: int, amount: int) -> None:
    """Subtract life from the player."""
    game.players[player_idx].life -= amount


def _game_etb_untapped(card: "CardInstance") -> None:
    """Mark the land as entering untapped."""
    card.tapped = False


# ─────────────────────────────────────────────────────────────
# Public discovery API
# ─────────────────────────────────────────────────────────────

def parse_optional_costs(card: "CardInstance",
                          trigger: str) -> list[OptionalCost]:
    """Return optional costs legal for this card under the given trigger.

    Triggers:
      - "etb"  : payments offered as the card enters the battlefield
      - "tap"  : payments offered when the card's mana ability fires
      - "cast" : payments offered as part of casting

    Implementation reads pre-parsed template fields populated by
    `engine.card_database` (which itself parses oracle text at
    DB-load time).  Extending to new mechanics is a matter of
    adding another conditional that builds an `OptionalCost` from
    whatever oracle-derived fields apply — no new callbacks.
    """
    out: list[OptionalCost] = []
    template = card.template

    if trigger == "etb":
        life_cost = getattr(template, "untap_life_cost", 0) or 0
        if life_cost > 0:
            colors = tuple(template.produces_mana or [])
            cost = CostDescriptor(kind="life", amount=life_cost)
            effect = EffectDescriptor(
                kind="etb_untapped", magnitude=1, colors=colors,
            )

            def _to_game(g, p, c=card, l=life_cost):
                _game_pay_life(g, p, l)
                _game_etb_untapped(c)

            def _to_snap(s, l=life_cost, cs=colors):
                _snap_pay_life(s, l)
                _snap_add_untapped_mana(s, cs)
                return s

            out.append(OptionalCost(
                name=f"{template.name}: pay {life_cost} life, ETB untapped",
                cost=cost,
                effect=effect,
                apply_to_game=_to_game,
                apply_to_snap=_to_snap,
            ))

    return out


# ─────────────────────────────────────────────────────────────
# Engine→AI offer channel
# ─────────────────────────────────────────────────────────────

def offer_optional_costs(game: "GameState", player_idx: int,
                          card: "CardInstance", trigger: str) -> None:
    """Discover optional costs for `card` and let the AI decide each.

    Called by engine sites whenever an optional payment becomes
    legal.  The AI's `decide_optional_cost` callback (uniform across
    all mechanics) returns True/False per offered cost; True ones
    are applied to the live game state.
    """
    for opt in parse_optional_costs(card, trigger):
        if game.callbacks.decide_optional_cost(game, player_idx, opt):
            opt.apply_to_game(game, player_idx)
