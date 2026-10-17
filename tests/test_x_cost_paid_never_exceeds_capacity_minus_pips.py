"""The X paid for an X-cost spell never exceeds the caster's capacity minus
the cost's fixed part (CR 601.2h: the total cost — fixed pips plus X — is
paid in full; X cannot be sized against mana the fixed pips also need).

Verify-before-build pin (payoff-sequencing design §5 U0). The design's
refuters read the inline X budget in `CastManager.cast_spell` as dividing
RAW capacity with no subtraction for the fixed pips. It does not
over-budget in effect: the base cost is paid (lands tapped) BEFORE the X
block reads `untapped_mana_capacity()`, so the budget it sees is already
net of the fixed part — and, unlike the printed-cmc formula in
`CastManager.affordable_x`, it is also net of any cost reduction actually
applied. Replacing it with `affordable_x` after payment would subtract the
base cost twice. This file pins the rule so neither copy can drift; card
names are fixture carriers only (an {X}{G} creature tutor, a 3-drop and a
1-drop in the library).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller, zone):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db, lands, library):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(lands):
        _put(game, card_db, "Forest", 0, "battlefield")
    for name in library:
        _put(game, card_db, name, 0, "library")
    for _ in range(5):
        _put(game, card_db, "Forest", 0, "library")
    tutor = _put(game, card_db, "Green Sun's Zenith", 0, "hand")   # {X}{G}
    return game, tutor


def _paid_x(game):
    line = next(l for l in reversed(game.log) if "Cast Green Sun's Zenith" in l)
    return int(line.split("(X=")[1].rstrip(")")) if "(X=" in line else 0


def test_the_x_paid_never_exceeds_capacity_minus_the_fixed_pips(card_db):
    """Three lands, {X}{G}: capacity 3, fixed part 1 → X ≤ 2. A 3-drop in the
    library is reachable only at X=3, which the caster cannot pay; the cast
    must size X within the budget net of the pip, never against raw
    capacity."""
    game, tutor = _game(card_db, lands=3,
                        library=["Eternal Witness", "Grizzly Bears"])
    assert CastManager.affordable_x(game, 0, tutor.template) == 2
    assert game.cast_spell(0, tutor, [])
    assert _paid_x(game) <= 2, game.log[-3:]
    assert sum(1 for l in game.players[0].battlefield
               if l.template.is_land and not l.tapped) == 0, \
        "X=2 plus the {G} pip is exactly the three lands"


def test_the_cast_time_x_agrees_with_the_shared_affordable_x_owner(card_db):
    """Across capacities the X actually paid is bounded by (and, when a
    target needs it, equal to) `CastManager.affordable_x` — the one formula
    cast-time target legality and the AI's target choice already share."""
    for lands, expect in ((2, 1), (3, 2), (4, 3)):
        game, tutor = _game(card_db, lands=lands,
                            library=["Eternal Witness", "Grizzly Bears",
                                     "Arboreal Grazer"])
        assert CastManager.affordable_x(game, 0, tutor.template) == expect
        assert game.cast_spell(0, tutor, [])
        assert _paid_x(game) <= expect, (lands, game.log[-3:])
