"""What this turn's finishers reach together (AI, from CR 702.40a, 106.4,
500.4).

A storm spell is copied once for each spell cast before it this turn (CR
702.40a): a damage closer cast with S spells before it is S + 1 instances
of its damage, and a second closer cast after it S + 2. A tutor whose pool
holds a closer adds one spell and then its closer (Wish: a play from
outside the game this turn, unit OG). Mana empties between steps (CR
106.4, 500.4), so this turn's casts are bounded by the mana available now.

- `per_copy_damage`: what one instance of a storm closer deals the
  opponent, read off its typed host (one DAMAGE instruction, a literal
  amount, a target that admits a player); None for any other shape.
- `finisher_casts`: the finisher casts available now -- each damage closer
  the player can cast from wherever a cast reaches (hand, a permission,
  flashback), and each tutor whose pool still holds one -- at the AI's cost
  estimate (`ai/effective_cmc`, floored at the pips, CR 601.2f).
- `reach`: the most damage a sequence of them deals within the mana, and
  the cast that sequence makes first.
- `keeps_finisher`: whether a play castable now leaves the mana a finisher
  still needs after it -- the play a held finisher may wait for this turn
  (a play not castable this turn stays the next turn's fuel, and so does
  every play while the mana does not pay the finisher) -- and does not
  spend the one play a permission allows them both (CR 400.11a,
  `rules_query.share_one_play`; the reach never casts two such cards).
- `pays`: whether the mana pays a finisher's whole cast this turn -- a
  tutor's included the closer it brings (`finisher_cost`).

No literals: every number is a count of spells, a cost or an amount of
mana read off the cards.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class FinisherCast:
    """One way to put a damage closer on the stack this turn: the card cast
    first (the closer itself, or the tutor that brings it), the mana the
    whole cast needs, the spells it adds before its closer resolves, and
    the damage each instance of the closer deals."""
    card: Any
    cost: int
    spells_before_closer: int
    damage: int


def per_copy_damage(game: Any, idx: int, card: Any) -> Optional[int]:
    """What each instance of a storm spell deals the opponent (CR 702.40a:
    every copy is one more instance): its one instruction's literal DAMAGE
    amount when its one target admits the opponent (CR 115.4,
    `target_solver.slot_admits_player`). None for any other shape -- no
    storm, a variable amount, a target no player is, an unmodelled text:
    the reach does not count it."""
    from engine.cards import Keyword
    from engine.cast_manager import CastManager
    from engine.effect_spec import AmountKind, Verb, iter_specs
    from engine.target_solver import slot_admits_player
    t = getattr(card, "template", None)
    effects = getattr(t, "effects", None)
    if effects is None or Keyword.STORM not in getattr(t, "keywords", ()):
        return None
    host = effects.spell(0)
    if host is None:
        return None
    specs = list(iter_specs(host.specs))
    if len(specs) != 1 or specs[0].verb is not Verb.DAMAGE:
        return None
    amount = specs[0].amount
    reqs = CastManager.spell_requirements(card)
    if amount is None or amount.kind is not AmountKind.LITERAL \
            or len(reqs) != 1 or not slot_admits_player(reqs[0], 1 - idx, idx):
        return None
    return amount.n


def closer(game: Any, idx: int, card: Any) -> bool:
    """A storm spell whose every instance deals the opponent a printed
    amount (the Grapeshot class, `per_copy_damage`)."""
    return per_copy_damage(game, idx, card) is not None


def _cost(game: Any, idx: int, card: Any) -> Optional[int]:
    """The AI's cost estimate, or None when the card carries no cost to
    read (a partial stand-in): the reach then knows nothing of it."""
    if getattr(getattr(card, "template", None), "mana_cost", None) is None:
        return None
    from ai.effective_cmc import effective_cmc
    return effective_cmc(card, game=game, player_idx=idx)


def _readable(game: Any) -> bool:
    """A game the reach can read: castability is asked of the engine."""
    return callable(getattr(game, "can_cast", None))


def _castable(game: Any, idx: int) -> List[Any]:
    """The cards the player can cast now: the hand and every card a
    permission or flashback lets it cast (`playable_cards`, the graveyard)."""
    from ai.playable_cards import playable_cards
    me = game.players[idx]
    pool = list(playable_cards(game, idx)) + list(me.graveyard)
    seen, out = set(), []
    for c in pool:
        if c.instance_id in seen or c.template.is_land:
            continue
        seen.add(c.instance_id)
        if game.can_cast(idx, c):
            out.append(c)
    return out


def finisher_casts(game: Any, idx: int) -> List[FinisherCast]:
    """The finisher casts available to the player now. A tutor counts once
    for each closer its pool still holds that no other cast already
    brings."""
    from ai.combo_calc import _tutor_pool
    if not _readable(game):
        return []
    castable = [c for c in _castable(game, idx)
                if _cost(game, idx, c) is not None]
    casts = [FinisherCast(c, _cost(game, idx, c), 0,
                          per_copy_damage(game, idx, c))
             for c in castable if closer(game, idx, c)]
    claimed = {cast.card.instance_id for cast in casts}
    for t in castable:
        if closer(game, idx, t) or "tutor" not in getattr(
                t.template, "tags", ()):
            continue
        brought = [c for c in _tutor_pool(t, game.players[idx])
                   if closer(game, idx, c) and c.instance_id not in claimed
                   and _cost(game, idx, c) is not None]
        if not brought:
            continue
        pick = min(brought, key=lambda c: _cost(game, idx, c))
        claimed.add(pick.instance_id)
        casts.append(FinisherCast(
            t, _cost(game, idx, t) + _cost(game, idx, pick), 1,
            per_copy_damage(game, idx, pick)))
    return casts


def _sequence_damage(order: Sequence[FinisherCast], storm: int) -> int:
    """Damage the casts deal in this order, from `storm` spells already
    cast this turn: each closer is the spells before it plus one instances
    of its damage."""
    s, dmg = storm, 0
    for cast in order:
        s += cast.spells_before_closer
        dmg += (s + 1) * cast.damage
        s += 1
    return dmg


def _never(_a: Any, _b: Any) -> bool:
    return False


def reach(casts: Sequence[FinisherCast], storm: int, mana: int,
          apart: Callable[[Any, Any], bool] = _never
          ) -> Tuple[int, Optional[Any]]:
    """The most damage a sequence of `casts` deals within `mana`, and the
    card it casts first (None when no cast is affordable). Two casts whose
    cards are `apart` -- one play a permission allows, shared -- are never
    in one sequence."""
    best, first = 0, None
    for k in range(1, len(casts) + 1):
        for order in itertools.permutations(casts, k):
            if sum(c.cost for c in order) > mana:
                continue
            if any(apart(x.card, y.card)
                   for x, y in itertools.combinations(order, 2)):
                continue
            dmg = _sequence_damage(order, storm)
            if dmg > best:
                best, first = dmg, order[0].card
    return best, first


def _shares_play(game: Any, idx: int) -> Callable[[Any, Any], bool]:
    """The cards that share one play a permission allows (CR 400.11a)."""
    from engine.rules_query import share_one_play
    return lambda a, b: share_one_play(game, idx, a, b)


def reach_now(game: Any, idx: int, mana: int) -> Tuple[int, Optional[Any]]:
    """`reach` over the player's finisher casts now."""
    casts = finisher_casts(game, idx)
    if not casts:
        return 0, None
    storm = game.players[idx].spells_cast_this_turn
    return reach(casts, storm, mana, _shares_play(game, idx))


def mana_made(card: Any) -> int:
    """The mana a spell adds when it resolves (its typed ritual mana)."""
    ritual = getattr(card.template, "ritual_mana", None)
    return ritual[1] if isinstance(ritual, tuple) else 0


def keeps_finisher(game: Any, idx: int, fuel: Any,
                   finisher_cost: Optional[int], mana: int,
                   finisher: Any = None) -> bool:
    """May a held finisher wait for `fuel`? Not when playing `fuel` spends
    the one play a permission allows the `finisher` too (CR 400.11a: "a
    card"). While the mana pays the finisher costing `finisher_cost`, a
    play castable now may be waited for only when it leaves the mana the
    finisher still needs after it (CR 106.4, 500.4: one that spends it
    strands the finisher for the turn). A finisher the mana does not pay
    now is stranded by no play, and a play not castable this turn is the
    next turn's fuel: the hold reads either as before. Unknown when a cost
    cannot be read: then the play is not ruled out."""
    if not _readable(game):
        return True
    if finisher is not None and _shares_play(game, idx)(finisher, fuel):
        return False
    fuel_cost = _cost(game, idx, fuel)
    if finisher_cost is None or fuel_cost is None:
        return True
    if finisher_cost > mana or not game.can_cast(idx, fuel):
        return True
    return mana - fuel_cost + mana_made(fuel) >= finisher_cost


def pays(finisher_cost: Optional[int], mana: int) -> bool:
    """Does the mana pay a finisher's whole cast this turn? (True when its
    cost cannot be read: then nothing is ruled out.)"""
    return finisher_cost is None or finisher_cost <= mana


def finisher_cost(game: Any, idx: int, card: Any) -> Optional[int]:
    """The mana a finisher's whole cast needs: its own cost -- a tutor's
    with the cheapest closer its pool holds. None when unreadable."""
    from ai.combo_calc import _tutor_pool
    own = _cost(game, idx, card)
    if own is None or closer(game, idx, card) or "tutor" not in getattr(
            card.template, "tags", ()):
        return own
    costs = [_cost(game, idx, c) for c in _tutor_pool(card, game.players[idx])
             if closer(game, idx, c)]
    costs = [c for c in costs if c is not None]
    return own + min(costs, default=0)
