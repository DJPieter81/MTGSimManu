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

    def _perm_worth(perm) -> float:
        # A hit is worth at least 1 (denying any permanent matters),
        # scaled by mana value (bigger investments hurt more to lose).
        return 1.0 + float(perm.template.cmc or 0)

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
    ranked = sorted(legal, key=lambda i: (values[i], -i), reverse=True)
    chosen = [i for i in ranked if values[i] > 0][:hi]
    for i in ranked:
        if len(chosen) >= lo:
            break
        if i not in chosen:
            chosen.append(i)
    return sorted(chosen)


def chosen_modes_value(game, card, controller: int, targets=None,
                       x_value: int = 0) -> float:
    """The value of the modes the controller would choose with these
    targets: what casting the modal spell performs."""
    return sum(mode_value(game, card, controller, i, targets, x_value)
               for i in select_modal_modes(game, card, controller, targets,
                                           x_value))
