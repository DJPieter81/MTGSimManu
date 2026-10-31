"""Modal spell mode selection (CR 700.2 — "Choose one/two —").

The engine enforces modal resolution (resolve exactly the chosen
mode(s)); THIS layer makes the strategic choice of which mode(s) to
take, per the engine/AI split. Selection is derived from board state —
the mode that removes the most opposing value net of the caster's own
losses — with no card names.

Asked by ``engine.modal_spell.choose_modes`` as the spell is cast (CR
601.2b), which holds the answer to the legal modes and printed range.
"""
from __future__ import annotations

import math


def _worth(mana_value) -> float:
    """A permanent is worth at least 1 (denying any permanent matters),
    scaled by mana value (bigger investments hurt more to lose)."""
    return 1.0 + float(mana_value or 0)


def _perm_worth(perm) -> float:
    return _worth(perm.template.cmc)


def named_player_value(game, ai_player: int, spec, player: int,
                       x_value: int = 0):
    """What an instruction that names its acting player is worth to
    `ai_player` when it names `player`, in this module's currency:
    - tokens it creates are a gain to their creator's side (CR 111.2),
      each a permanent of mana value 0 (it has no mana cost, CR 202.3);
    - permanents it sacrifices are a loss to theirs: the least valuable
      it matches, since that player chooses (CR 701.21a);
    - cards it mills leave a hidden library, where that player's expected
      draws stay what they were, for their graveyard, where each is a gain
      to their side when it is fuel to them, one per card (CR 701.17a;
      `ai.predicates.graveyard_fuel_share`). A mill that empties a library
      loses its player the game at their next draw (CR 121.4, 704.5b),
      which outweighs any fuel;
    - +1/+1 counters it puts on each creature that player controls are a
      gain to their side (CR 122.1), each counter worth what a token is.
    None for an instruction this does not value."""
    from engine.effect_spec import TokenSpec, Verb
    verb = getattr(spec, "verb", None)
    if verb is Verb.MILL and spec.amount is not None:
        from ai.predicates import graveyard_fuel_share
        from engine.effect_conditions import amount_value
        n = max(0, amount_value(game, ai_player, spec.amount, x_value))
        library = len(game.players[player].library)
        if 0 < library <= n:
            return -math.inf if player == ai_player else math.inf
        fuel = min(n, library) * graveyard_fuel_share(game, ai_player, player)
        return fuel if player == ai_player else -fuel
    if verb is Verb.CREATE_TOKEN and isinstance(spec.payload, TokenSpec):
        from engine.effect_conditions import amount_value
        n = amount_value(game, ai_player, spec.amount, x_value) \
            if spec.amount is not None else 1
        gained = max(0, n) * _worth(0)
        return gained if player == ai_player else -gained
    if verb is Verb.PUT_COUNTERS and spec.filter is not None:
        from engine.effect_conditions import amount_value
        n = amount_value(game, ai_player, spec.amount, x_value) \
            if spec.amount is not None else 1
        team = sum(1 for c in game.players[player].battlefield
                   if c.effective_is_creature)
        gained = max(0, n) * team * _worth(0)
        return gained if player == ai_player else -gained
    if verb is Verb.SACRIFICE and spec.filter is not None \
            and spec.amount is not None:
        from engine.effect_executors import edict_victims
        lost = sum(sorted(_perm_worth(c) for c in edict_victims(
            game, player, spec.filter))[:spec.amount.n])
        return -lost if player == ai_player else lost
    return None


def _named_players_value(game, card, controller: int, host, verb,
                         x_value: int = 0) -> float:
    """Net value of a mode whose `verb` instructions name their acting
    players (`named_player_value` per player): "you" names the caster,
    "each opponent" the opponents, "each player" everyone, and a player
    chosen as a target the legal one worth most to the caster -- whom it
    chooses (`modal_spell.choose_mode_players`). An instruction that names
    no player it can value adds nothing."""
    from engine.effect_spec import RefKind, SelectorKind, iter_specs
    from engine.target_solver import legal_slot_choices
    value = 0.0
    me, opp = controller, 1 - controller
    for s in iter_specs(host.specs):
        if s.verb is not verb:
            continue
        kind = getattr(s.actor, "kind", None)
        if s.actor is None or kind is SelectorKind.PLAYER:
            groups = [(me,)]
        elif kind is SelectorKind.OPPONENTS:
            groups = [(opp,)]
        elif kind is SelectorKind.ALL_PLAYERS:
            groups = [(me, opp)]
        elif kind is RefKind.TARGET and s.actor.index is not None \
                and s.actor.index < len(host.targets):
            players, _ = legal_slot_choices(
                game, controller, host.targets[s.actor.index], source=card)
            groups = [(p,) for p in players]
        else:
            continue
        values = [sum(named_player_value(game, controller, s, p, x_value)
                      or 0.0 for p in g) for g in groups]
        value += max(values, default=0.0)
    return value


def _mode_value(game, controller: int, mass) -> float:
    """Estimate the net board value (opponent's loss minus the caster's
    own) of a mass-sweep mode, from its shape typed at load
    (`CardTemplate.modes[i]['mass']`, scope included). Higher is better.
    Any other mode shape is valued at nothing (no tuning knob): it is
    never chosen over a mode this selector can read, and beyond the
    header's fewest modes it is not chosen at all."""
    from engine.cards import CardType

    if not mass:
        return 0.0
    me = game.players[controller]
    opp = game.players[1 - controller]
    theirs = mass['scope'] in ('all', 'opponents')
    mine = mass['scope'] in ('all', 'you')

    if mass['kind'] == 'damage':
        amount = mass['amount']

        def _hit(perm) -> bool:
            is_c = CardType.CREATURE in perm.template.card_types
            is_pw = CardType.PLANESWALKER in perm.template.card_types
            if is_c:
                return (perm.toughness or 0) <= amount
            return mass['planeswalkers'] and is_pw
    else:
        type_map = {'artifact': CardType.ARTIFACT, 'creature': CardType.CREATURE,
                    'enchantment': CardType.ENCHANTMENT}
        want = type_map.get(mass['type']) if mass['type'] else None
        max_mv = mass['max_mv']

        def _hit(perm) -> bool:
            if perm.template.is_land:
                return False
            if want is not None and want not in perm.template.card_types:
                return False
            if max_mv is not None and (perm.template.cmc or 0) > max_mv:
                return False
            return True

    gain = sum(_perm_worth(p) for p in opp.battlefield if _hit(p)) if theirs else 0.0
    loss = sum(_perm_worth(p) for p in me.battlefield if _hit(p)) if mine else 0.0
    return gain - loss


def _targeted_removal_mode_value(game, controller: int, removal: dict,
                                 targets, x_value: int) -> float:
    """Net value of a typed "destroy/exile target <type> [with mana value
    N/X or less]" mode: the worth of the chosen target when the mode's
    bound reaches it (an X bound reads the X actually paid), else the best
    reachable opposing permanent, else zero — a mode whose bound reaches
    nothing is worth nothing, however big the creature on the other side."""
    if not removal:
        return 0.0
    opp = game.players[1 - controller]
    mv = removal.get('mv')
    ceiling = (x_value if mv == 'x' else mv)

    def _reaches(perm) -> bool:
        return ceiling is None or (perm.template.cmc or 0) <= ceiling

    def _worth(perm) -> float:
        return 1.0 + float(perm.template.cmc or 0)

    chosen = [game.get_card_by_id(t) for t in (targets or [])]
    chosen = [c for c in chosen if c is not None and c in opp.battlefield]
    if chosen:
        return sum(_worth(c) for c in chosen if _reaches(c))
    reachable = [p for p in opp.battlefield
                 if not p.template.is_land and _reaches(p)]
    return max((_worth(p) for p in reachable), default=0.0)


def mode_value(game, card, controller: int, index: int, targets=None,
               x_value: int = 0) -> float:
    """The net board value of performing one mode (opponent's loss minus
    the caster's own), read from the mode's typed shape."""
    mode = (card.template.modes or [])[index]
    if mode.get('removal'):
        return _targeted_removal_mode_value(
            game, controller, mode['removal'], targets, x_value)
    from engine.effect_carrier import (TOKEN_FAMILY, chosen_mode_host,
                                       mode_family)
    from engine.effect_spec import Verb
    family = mode_family(card.template, index)
    named = {"edict": Verb.SACRIFICE, TOKEN_FAMILY: Verb.CREATE_TOKEN}
    if family in named:
        return _named_players_value(game, card, controller,
                                    chosen_mode_host(card.template, index),
                                    named[family], x_value)
    return _mode_value(game, controller, mode.get('mass'))


def select_modal_modes(game, card, controller: int, targets=None,
                       x_value: int = 0, *, legal=None, choose=None) -> list:
    """The modes to choose as the spell is cast (CR 601.2b), in printed
    order: among the `legal` modes (CR 700.2a; the engine's
    `modal_spell.legal_modes` by default), every one worth more than
    nothing, up to the most the header allows; then, short of its fewest,
    the best of the rest. Ties break toward the earlier mode."""
    modes = card.template.modes or []
    if not modes:
        return []
    from engine import modal_spell
    lo, hi = choose if choose is not None else modal_spell.choose_range(
        card.template)
    if legal is None:
        legal = modal_spell.legal_modes(game, card, controller, targets or [])
    values = {i: mode_value(game, card, controller, i, targets, x_value)
              for i in legal}
    return _pick_modes(legal, values, lo, hi)


def _pick_modes(legal, values, lo: int, hi: int) -> list:
    """The one choosing rule, for a modal spell and a modal trigger alike:
    every legal mode worth more than nothing, up to the most the header
    allows; then, short of its fewest, the best of the rest. Ties break
    toward the earlier mode. In printed order."""
    ranked = sorted(legal, key=lambda i: (values[i], -i), reverse=True)
    chosen = [i for i in ranked if values[i] > 0][:hi]
    for i in ranked:
        if len(chosen) >= lo:
            break
        if i not in chosen:
            chosen.append(i)
    return sorted(chosen)


def _graveyard_exile_value(game, controller: int, source, host) -> float:
    """What an "exile [up to one] target card from a graveyard" of `host`
    takes from its owner: the worth of the best card it may take that is
    worth denying them (`ai.graveyard_removal.worth_aiming_at`: a keystone
    of their plan, or still a resource to them there), nothing when none
    is."""
    from ai.graveyard_removal import worth_aiming_at
    from engine.effect_executors import graveyard_target_exile
    from engine.effect_spec import iter_specs
    from engine.target_solver import legal_slot_choices
    value = 0.0
    for s in iter_specs(host.specs):
        if not graveyard_target_exile(s) or s.target_slot is None \
                or s.target_slot >= len(host.targets):
            continue
        _, cards = legal_slot_choices(game, controller,
                                      host.targets[s.target_slot],
                                      source=source)
        value += max((_perm_worth(c) for c in cards
                      if worth_aiming_at(game, controller, c)), default=0.0)
    return value


def trigger_mode_value(game, controller: int, source, host) -> float:
    """What performing one mode of a modal triggered ability is worth to
    its controller, read from the mode as a host of its own
    (`engine.effect_carrier.trigger_mode_host`), in this module's
    currency: its instructions that name their acting players
    (`_named_players_value`: tokens, counters, mill, edicts) and its exile
    of a card from a graveyard (`_graveyard_exile_value`). Any other
    instruction adds nothing (no tuning knob), so such a mode is chosen
    only to make up the header's fewest."""
    from engine.effect_spec import Verb, iter_specs
    verbs = {s.verb for s in iter_specs(host.specs)}
    value = sum(_named_players_value(game, source, controller, host, verb)
                for verb in (Verb.CREATE_TOKEN, Verb.PUT_COUNTERS, Verb.MILL,
                             Verb.SACRIFICE) if verb in verbs)
    return value + _graveyard_exile_value(game, controller, source, host)


def select_trigger_modes(game, controller: int, source, host, legal,
                         choose) -> list:
    """The modes a modal triggered ability's controller chooses as it is
    put on the stack (CR 603.3c), by the modal spell's rule
    (`_pick_modes`) over `trigger_mode_value`, among the `legal` modes the
    engine offers (CR 700.2a)."""
    from engine.effect_carrier import trigger_mode_host
    lo, hi = choose
    values = {i: trigger_mode_value(game, controller, source,
                                    trigger_mode_host(host, i))
              for i in legal}
    return _pick_modes(legal, values, lo, hi)


def worth_casting_without_targets(game, card, controller: int) -> bool:
    """A spell that requires a target is not cast without one (CR 601.2c:
    its caster announces every target it requires). Cast with no target, a
    modal spell with targeted modes performs only its modes that need none
    (CR 700.2a): it is worth casting then only when those are worth
    something to its caster. Any other spell is not held by this rule. The
    one answer every cast path asks."""
    from engine import modal_spell
    from engine.cast_manager import CastManager
    from engine.target_solver import binds_whatever_is_chosen
    if any(binds_whatever_is_chosen(r)
           for r in CastManager.spell_requirements(card)):
        return False
    t = card.template
    if not (modal_spell.in_scope(t) and modal_spell.has_targeted_mode(t)):
        return True
    return chosen_modes_value(game, card, controller, []) > 0


def chosen_modes_value(game, card, controller: int, targets=None,
                       x_value: int = 0) -> float:
    """The value of the modes the controller would choose with these
    targets: what casting the modal spell performs."""
    return sum(mode_value(game, card, controller, i, targets, x_value)
               for i in select_modal_modes(game, card, controller, targets,
                                           x_value))
