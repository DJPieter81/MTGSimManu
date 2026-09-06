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


def select_modal_modes(game, card, controller: int, targets=None) -> list:
    """Return the indices of the mode(s) to resolve — the highest-value
    ``modal_choose_count`` modes, ties broken toward the earlier mode."""
    modes = card.template.modes or []
    if not modes:
        return []
    k = max(1, min(int(getattr(card.template, 'modal_choose_count', 1) or 1),
                   len(modes)))
    scored = sorted(
        range(len(modes)),
        key=lambda i: (_mode_value(game, controller, modes[i].get('text', '')), -i),
        reverse=True,
    )
    return sorted(scored[:k])
