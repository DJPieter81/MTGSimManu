"""What a sweep takes from each player (AI, from CR 701.8a, 608.2h, 702.44a).

A sweep -- "destroy each nonland permanent with mana value equal to the
number of charge counters on ~" -- destroys every permanent its description
admits as it resolves, its controller's as well as its opponent's (CR
701.8a); its bound is read once, from what the source last had when its
cost removed it (CR 608.2h). So a sweep is worth to its controller what the
opponent loses (`ai.permanent_threat.permanent_threat`, the removal
scorer's currency) less what the controller loses with it.

Two readers, one owner:
- the activation plan (`ai.activation_ev`): a sweep is activated when what
  it takes is worth more to the opponent than to its controller;
- the X of a sunburst sweep (`EVPlayer.choose_x`): a sunburst permanent
  enters with a counter per colour of mana spent (CR 702.44a), so its X buys
  the number of colours -- the mana value it will sweep -- and the AI pays
  for the count whose sweep is worth most.

Which permanents a description admits is the engine's description owner's
(`engine.effect_conditions.permanent_described`, with its bounds); which
instructions are a sweep is the destroy owner's
(`engine.effect_executors.destroy_each_supported`). Typed instructions
only: no oracle text, no card names.
"""
from __future__ import annotations

from typing import Any, Iterator, List, Optional, Tuple


def sweeps(host: Any) -> Iterator[Any]:
    """The "destroy each <description>" instructions of one host."""
    from engine.effect_executors import destroy_each_supported
    from engine.effect_spec import iter_specs
    if host is None:
        return
    for s in iter_specs(host.specs):
        if destroy_each_supported(s):
            yield s


def victims(game: Any, controller: int, s: Any, source: Any,
            bounds: Optional[tuple] = None) -> List[Any]:
    """The permanents the sweep `s` would destroy now: those its
    description admits, every player's (or its controller's alone), not
    indestructible, and never its source, which its cost removes first.
    `bounds` are the bound values; by default the source's counters now --
    what it will last have had when its cost removes it."""
    from engine import effect_conditions as conditions
    from engine.cards import Keyword
    f = s.filter
    if bounds is None:
        lki = source.last_known() if source is not None else None
        bounds = conditions.bound_values(game, controller, f, source, lki)
    players = (range(len(game.players)) if f.controller == "any"
               else (controller,))
    return [c for p in players for c in game.players[p].battlefield
            if c is not source
            and Keyword.INDESTRUCTIBLE not in c.keywords
            and conditions.permanent_described(c, f, source=source,
                                               bounds=bounds)]


def sweep_value(game: Any, controller: int, s: Any, source: Any,
                bounds: Optional[tuple] = None) -> float:
    """What the sweep takes from the opponent less what it takes from its
    controller, each permanent priced by what its loss costs its owner."""
    from ai.permanent_threat import permanent_threat
    total = 0.0
    for c in victims(game, controller, s, source, bounds):
        worth = permanent_threat(c, game.players[c.controller], game)
        total += -worth if c.controller == controller else worth
    return total


def host_value(game: Any, controller: int, host: Any, source: Any) -> float:
    """The value of every sweep of one host, its bounds read now."""
    return sum(sweep_value(game, controller, s, source)
               for s in sweeps(host))


def colours_reachable(game: Any, controller: int, mana: int) -> int:
    """How many different colours `mana` units from the controller's
    untapped sources can be: a matching of sources to colours, each source
    paying one unit of one colour it can make (CR 106.1, 105.1)."""
    player = game.players[controller]
    options = []
    for land in player.untapped_lands:
        cols = set(game._effective_produces_mana(controller, land) or ())
        cols &= set("WUBRG")
        if cols:
            options.append(cols)
    match: dict = {}

    def _augment(i: int, seen: set) -> bool:
        for col in options[i]:
            if col in seen:
                continue
            seen.add(col)
            if col not in match or _augment(match[col], seen):
                match[col] = i
                return True
        return False

    for i in range(len(options)):
        _augment(i, set())
    return min(len(match), max(0, mana))


def sunburst_sweep_x(game: Any, controller: int, template: Any,
                     max_x: int) -> Optional[int]:
    """The X to pay for a sunburst permanent whose sweep reads its own
    counters: X buys one counter per new colour (CR 702.44a), so the X is
    the count, among those the controller's sources can colour, whose sweep
    is worth most now; ties go to the smaller X (less mana spent). None when
    the permanent prints no such sweep."""
    from engine import effect_conditions as conditions
    from engine.effect_spec import AmountKind
    effects = getattr(template, "effects", None)
    hosts = [effects.activated(a.index)
             for a in getattr(template, "activated_abilities", None) or ()]
    specs = [s for h in hosts for s in sweeps(h)
             if any(a.kind is AmountKind.EQUAL_TO for _st, _op, a
                    in s.filter.stat_bounds)]
    if not specs:
        return None
    reach = colours_reachable(game, controller, max_x)
    best: Tuple[float, int] = (float("-inf"), 0)
    for k in range(reach + 1):
        value = 0.0
        for s in specs:
            bounds = tuple((stat, op, k if a.kind is AmountKind.EQUAL_TO
                            else conditions.amount_value(game, controller, a))
                           for stat, op, a in s.filter.stat_bounds)
            value += sweep_value(game, controller, s, None, bounds)
        if value > best[0]:
            best = (value, k)
    return best[1]
