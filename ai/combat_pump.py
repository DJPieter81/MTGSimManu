"""A creature's own pump in combat (AI, from CR 509.4, 602.2, 122.1, 514.2).

After blockers are declared, each player may activate abilities before
combat damage (CR 509.4). A creature whose own instant-speed ability pumps
it fights at the stats its controller can pay for:
- "Discard a card: Put a +1/+1 counter on ~" -- the counter lasts after the
  combat (CR 122.1);
- "{R}: ~ gets +2/+0 until end of turn" -- the bonus ends with the turn
  (CR 514.2).

This module reads those pumps from the typed activated abilities
(`ActivatedAbility.effect_kind` PUMP_SELF_UEOT / PUT_COUNTER_SELF) and what
their typed cost asks. The decision to activate (`EVPlayer.
decide_combat_pump`) and the block that counts the pump
(`EVPlayer._score_block_lifespan_delta`) both read it: one owner of what a
creature's own pump is and what its controller can pay for. No oracle text,
no card names.
"""
from __future__ import annotations

from typing import Any, List, NamedTuple


class PumpStep(NamedTuple):
    """What one activation adds to its creature for this combat, and
    whether the creature keeps it afterwards."""
    power: int
    toughness: int
    lasting: bool


def pump_step(ability: Any) -> PumpStep:
    """One activation's bonus: a +1/+1 counter per counter put (it lasts,
    CR 122.1), or the printed until-end-of-turn bonus (it does not, CR
    514.2)."""
    from engine.cards import ActivationEffectKind as K
    if ability.effect_kind is K.PUT_COUNTER_SELF:
        n = max(0, int((ability.put_counter_data or {}).get("amount")
                       or ability.amount or 0))
        return PumpStep(n, n, True)
    return PumpStep(max(0, ability.power_mod or 0),
                    max(0, ability.toughness_mod or 0), False)


def self_pumps(perm: Any) -> List[Any]:
    """The creature's own instant-speed pumps: a typed until-end-of-turn
    bonus, or +1/+1 counters put on itself, activated from the battlefield
    at instant speed. A pump whose cost gives up a permanent (a sacrifice,
    an exile of itself) is left out: its cost is a body lost."""
    from engine.cards import ActivationEffectKind as K
    out = []
    for a in getattr(perm.template, "activated_abilities", None) or ():
        if a.sorcery_speed_only or not a.from_battlefield:
            continue
        c = a.cost
        if c.sacrifice_self or c.sacrifice_type or c.exile_self \
                or c.sacrifice_another:
            continue
        if a.effect_kind is K.PUT_COUNTER_SELF:
            data = a.put_counter_data or {}
            if data.get("kind") != "+1/+1" or not data.get("self"):
                continue
        elif a.effect_kind is not K.PUMP_SELF_UEOT:
            continue
        step = pump_step(a)
        if step.power or step.toughness:
            out.append(a)
    return out


def affordable_steps(game: Any, player_idx: int, perm: Any,
                     ability: Any) -> int:
    """How many times the controller can pay the ability now, read from its
    typed cost: the cards in hand over its discard, the life above lethal
    over its life payment, the untapped mana sources over its mana value,
    and once for an ability usable once each turn or tapping its creature.
    The engine's own check (`ActivationManager.can_activate`) still decides
    each activation; this is the count a plan may assume."""
    player = game.players[player_idx]
    c = ability.cost
    caps = []
    if c.discard_cards:
        caps.append(len(player.hand) // c.discard_cards)
    if c.life:
        caps.append(max(0, player.life - 1) // c.life)
    mv = c.mana.cmc
    if mv:
        sources = sum(1 for p in player.battlefield
                      if p.template.is_land and not p.tapped)
        caps.append(sources // mv)
    if ability.once_each_turn or c.tap_self or c.untap_self:
        caps.append(1)
    if not caps:
        return 1      # a cost this reader does not count: one at a time
    return max(0, min(caps))


def pump_cost(ability: Any, steps: int) -> dict:
    """What `steps` activations take from their controller: cards, life and
    mana, read from the typed cost."""
    c = ability.cost
    mv = c.mana.cmc
    return {"cards": c.discard_cards * steps, "life": c.life * steps,
            "mana": mv * steps}


def fight_plan(game: Any, player_idx: int, perm: Any, opposing: Any):
    """The fewest activations of one of `perm`'s own pumps, within what its
    controller can pay, after which it survives its fight with `opposing`
    -- killing it if any affordable number does -- when without them it
    would not survive or would not kill. Returns (ability, steps), or None
    when no affordable number changes the fight."""
    from engine.cards import Keyword
    o_pow = opposing.power or 0
    o_tou = opposing.toughness or 0
    o_deadly = Keyword.DEATHTOUCH in opposing.keywords and o_pow > 0

    def outcome(p, t):
        survives = not o_deadly and o_pow < t
        kills = (Keyword.DEATHTOUCH in perm.keywords and p > 0) or p >= o_tou
        return survives, kills

    base = outcome(perm.power or 0, perm.toughness or 0)
    best = None
    for ability in self_pumps(perm):
        step = pump_step(ability)
        for k in range(1, affordable_steps(game, player_idx, perm,
                                           ability) + 1):
            got = outcome((perm.power or 0) + k * step.power,
                          (perm.toughness or 0) + k * step.toughness)
            if got == base or got[0] < base[0] or got[1] < base[1]:
                continue
            # The best fight it can pay for (surviving first, then the
            # kill), at the fewest steps that reach it.
            if best is None or got > best[2] or (
                    got == best[2] and k < best[1]):
                best = (ability, k, got)
    return None if best is None else (best[0], best[1])
