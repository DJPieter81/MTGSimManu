"""Modal spell mode selection (CR 700.2 — "Choose one/two —").

The engine enforces modal resolution (resolve exactly the chosen
mode(s)); THIS layer makes the strategic choice of which mode(s) to
take, per the engine/AI split. Selection is derived from board state —
the mode that removes the most opposing value net of the caster's own
losses — with no card names.

Consumed by ``engine.spell_resolution._execute_spell_effects`` at modal
resolution time.
"""
from __future__ import annotations

import re


def _mode_value(game, controller: int, mode_text: str) -> float:
    """Estimate the net board value (opponent's loss minus the
    caster's own) of resolving one mode clause. Higher is better."""
    from engine.cards import CardType

    text = (mode_text or '').lower()
    me = game.players[controller]
    opp = game.players[1 - controller]

    def _perm_worth(perm) -> float:
        # A hit is worth at least 1 (denying any permanent matters),
        # scaled by mana value (bigger investments hurt more to lose).
        return 1.0 + float(perm.template.cmc or 0)

    # ── mass damage to each creature [and planeswalker] ──
    m = re.search(r'(\d+)\s+damage\s+to\s+each\s+creature', text)
    if m:
        amount = int(m.group(1))
        also_pw = 'planeswalker' in text

        def _dies(perm) -> bool:
            is_c = CardType.CREATURE in perm.template.card_types
            is_pw = CardType.PLANESWALKER in perm.template.card_types
            if is_c:
                return (perm.toughness or 0) <= amount
            return also_pw and is_pw
        return (sum(_perm_worth(p) for p in opp.battlefield if _dies(p))
                - sum(_perm_worth(p) for p in me.battlefield if _dies(p)))

    # ── destroy / exile all <type> [with mana value N or less] ──
    m = re.search(r'(?:destroy|exile)\s+all\s+'
                  r'(artifacts?|creatures?|enchantments?|permanents?)', text)
    if m:
        noun = m.group(1).rstrip('s')
        cap = re.search(r'mana value (\d+) or less', text)
        max_mv = int(cap.group(1)) if cap else None
        type_map = {'artifact': CardType.ARTIFACT, 'creature': CardType.CREATURE,
                    'enchantment': CardType.ENCHANTMENT}
        want = type_map.get(noun)

        def _hit(perm) -> bool:
            if perm.template.is_land:
                return False
            if want is not None and want not in perm.template.card_types:
                return False
            if max_mv is not None and (perm.template.cmc or 0) > max_mv:
                return False
            return True
        return (sum(_perm_worth(p) for p in opp.battlefield if _hit(p))
                - sum(_perm_worth(p) for p in me.battlefield if _hit(p)))

    # Any other mode shape: only mass-sweep / mass-destroy modes reach
    # this selector today (the resolution gate admits no other), so both
    # branches above always score. Neutral fallback — no tuning knob.
    return 0.0


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


def select_modal_modes(game, card, controller: int, targets=None,
                       x_value: int = 0) -> list:
    """Return the indices of the mode(s) to resolve — the highest-value
    ``modal_choose_count`` modes, ties broken toward the earlier mode."""
    modes = card.template.modes or []
    if not modes:
        return []
    k = max(1, min(int(getattr(card.template, 'modal_choose_count', 1) or 1),
                   len(modes)))

    def _value(i: int) -> float:
        mode = modes[i]
        if mode.get('removal'):
            return _targeted_removal_mode_value(
                game, controller, mode['removal'], targets, x_value)
        return _mode_value(game, controller, mode.get('text', ''))

    scored = sorted(range(len(modes)), key=lambda i: (_value(i), -i), reverse=True)
    return sorted(scored[:k])
