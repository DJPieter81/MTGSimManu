"""The one read path for rule-modifying effects (CR 611 / 101.2).

Every gate that asks "may this happen?", "at what cost?" or "how many?"
asks here — cast legality, cast timing, cost, draws, attacks, combat
damage — instead of reading effect state itself. Answers are defined by the
effect model (engine/effect_model.py); a family's internals move onto
registered/derived `Effect` records without its gates changing.

Current internals (stage G1): adapters over the pre-model state, so
routing the gates here changed no behaviour.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from engine.game_state import GameState


# ── Casting ──────────────────────────────────────────────────────────

def cast_prohibited(game: "GameState", player_idx: int, template) -> bool:
    """A turn-scoped or static cast prohibition covers this spell."""
    from engine.cast_manager import CastManager
    return CastManager.cast_is_prohibited(game.players[player_idx], template)


def sorcery_speed_only(game: "GameState", player_idx: int) -> bool:
    """A static restricts this player to casting at sorcery speed."""
    return player_idx in game._sorcery_speed_lockout_set()


def cast_as_though_flash(game: "GameState", player_idx: int, template) -> bool:
    """This player may cast this spell as though it had flash (CR 702.8d)."""
    types = game.players[player_idx].flash_permission_types
    return (('sorcery' in types and template.is_sorcery)
            or ('creature' in types and template.is_creature))


def cost_delta(game: "GameState", player_idx: int, template) -> int:
    """Total generic cost reduction for this spell, from statics and
    temporary rules alike."""
    from engine.oracle_resolver import count_cost_reducers
    return count_cost_reducers(game, player_idx, template)


# ── Drawing ──────────────────────────────────────────────────────────

def draw_limit(game: "GameState", player_idx: int) -> Optional[int]:
    """The most cards this player may draw this turn, or None."""
    return game._draw_limit_for(player_idx)


# ── Combat ───────────────────────────────────────────────────────────

def attack_prohibited(game: "GameState", player_idx: int) -> bool:
    """This player's creatures can't attack (a turn-scoped lock)."""
    return bool(game.players[player_idx].cannot_attack_this_turn)


def attacking_player_prohibited(game: "GameState", defender_idx: int) -> bool:
    """Creatures can't attack this player."""
    return bool(getattr(game.players[defender_idx], 'cannot_be_attacked_this_turn', False))


def combat_damage_prevented(game: "GameState") -> bool:
    """All combat damage is prevented (Fog-class effect)."""
    return any(getattr(p, 'combat_damage_prevented_this_turn', False)
               for p in game.players)
