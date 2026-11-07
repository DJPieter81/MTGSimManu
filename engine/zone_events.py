"""Cards changing zones, gathered into the events that trigger on them.

CR 603.2, 603.6: an ability that triggers on cards being put into a zone or
leaving one triggers on the EVENT. "Whenever one or more cards are put into
your graveyard" triggers once for a mill of three (CR 700.14 batch wording),
"whenever a card is put into your graveyard" three times.

The zone funnel (`ZoneManager.move_card`, `zone_transfer.transfer`) records
every card that changes zones here. An event scope (`event`) gathers the
moves made inside it -- one instruction a spell or ability performs, one
state-based-action pass, one cost payment, one resolution's remainder -- and
hands them to the zone-change carrier (`effect_carrier.
dispatch_zone_change_triggers`) as one event when it closes. Scopes nest: the
innermost open scope takes a move, so each instruction of a resolution is
its own event. A move made with no scope open is an event by itself.

Only cards change zones in this sense: a token or a copy of a spell is not a
card (CR 111.7, 707.10).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, List, Optional


@dataclass(frozen=True)
class ZoneMove:
    """One card's move: from which zone of which player to which zone of
    which player."""
    card: Any
    from_zone: str
    from_player: int
    to_zone: str
    to_player: int


def _scopes(game: Any) -> Optional[List[list]]:
    scopes = getattr(game, "_zone_event_scopes", None)
    if scopes is None:
        try:
            scopes = []
            game._zone_event_scopes = scopes
        except AttributeError:          # a game stub that takes no fields
            return None
    return scopes


@contextmanager
def event(game: Any) -> Iterator[None]:
    """Gather the moves made inside as one event; dispatch it on exit."""
    scopes = _scopes(game)
    if scopes is None:
        yield
        return
    scopes.append([])
    try:
        yield
    finally:
        moves = scopes.pop()
        if moves:
            _dispatch(game, tuple(moves))


def is_card(card: Any) -> bool:
    """A card, as zone-change events count them: not a token and not a
    copy of a spell (CR 111.7, 707.10)."""
    return not (getattr(card, "is_token", False)
                or getattr(card, "is_copy", False))


def record(game: Any, card: Any, from_zone: str, from_player: int,
           to_zone: str, to_player: int) -> None:
    """A card changed zones: add it to the innermost open event, or make it
    an event by itself."""
    if from_zone == to_zone or not is_card(card):
        return
    move = ZoneMove(card, from_zone, from_player, to_zone, to_player)
    scopes = _scopes(game)
    if scopes:
        scopes[-1].append(move)
    else:
        _dispatch(game, (move,))


def _dispatch(game: Any, moves: tuple) -> None:
    if getattr(game, "game_over", False) or not hasattr(game, "players") \
            or not hasattr(game, "active_player"):      # a game stub
        return
    from .effect_carrier import dispatch_zone_change_triggers
    dispatch_zone_change_triggers(game, moves)
