"""Whether a ward makes a permanent a pointless target.

Ward counters a spell or ability that targets the permanent unless its
caster pays the ward cost (CR 702.21a). The payment is decided when the
spell resolves (`engine.optional_costs.offer_ward_tax`): the caster must
be able to pay — enough mana left, at least as much life as the life part
(CR 119.4) — and the payment decision (`decide_optional_cost` over the
ward's own OptionalCost) must choose to pay.

Target choice asks the same question before casting. A target whose ward
the caster could not pay, or would decline to pay, would only get the
spell countered: it is not a target. One question, answered by the same
gate and the same decision the resolution uses, so target choice and
payment can never disagree.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState


def ward_rules_out_target(game: "GameState", caster_idx: int,
                          spell: "CardInstance", target: "CardInstance") -> bool:
    """True when `target`'s ward would counter `spell` (see module docs)."""
    from engine.optional_costs import parse_ward_tax_cost, ward_owed
    template = target.template
    if target.controller == caster_idx or not ward_owed(template):
        return False
    from ai.effective_cmc import effective_cmc
    player = game.players[caster_idx]
    mana_after_spell = (player.available_mana_estimate
                        - effective_cmc(spell, game=game, player_idx=caster_idx))
    if mana_after_spell < (getattr(template, "ward_cost", 0) or 0):
        return True
    if player.life < (getattr(template, "ward_life_cost", 0) or 0):
        return True     # CR 119.4
    opt = parse_ward_tax_cost(target, spell)
    return not game.callbacks.decide_optional_cost(game, caster_idx, opt)
