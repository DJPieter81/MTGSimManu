"""Ward on a target bound to a permanent spell's triggered ability must
not counter the permanent spell itself.

Rule (CR 603.3 / 702.21): a permanent spell (creature/artifact/
enchantment/planeswalker that is not an Aura) does not target — its
ETB/attack trigger does, and that trigger is a separate object on the
stack. Ward may counter that TRIGGERED ABILITY, but it can never counter
the permanent spell, which still enters the battlefield.

The bug: the resolution-time ward check iterated `item.targets` for
EVERY item including permanent spells and countered the spell on an
unpaid ward — even though the sibling CR 608.2b fizzle branch a few
lines below already computes `_is_permanent_spell`/`_is_aura` and cites
CR 603.3 to exempt exactly these spells. A creature whose attack/ETB
trigger the AI aimed at a warded permanent was sent to the graveyard
instead of entering (audit: Affinity vs Jeskai Blink, s55620 — a second
Phelia countered by Kappa Cannoneer's ward {4}).

Card names are fixture carriers; the rule is the permanent-spell ward
exemption, shared by every creature with a targeted ETB/attack trigger.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState


def _mk(game, card_db, name, owner, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    return c


def test_ward_target_does_not_counter_a_permanent_spell(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0

    # Opponent controls a warded permanent (ward {4}).
    warded = _mk(game, card_db, "Kappa Cannoneer", 1, "battlefield")
    warded.enter_battlefield()
    game.players[1].battlefield.append(warded)
    game.players[1].creatures.append(warded)

    # A creature spell is cast; its (trigger-bound) target is the warded
    # permanent. The caster does NOT pay ward.
    spell = _mk(game, card_db, "Phelia, Exuberant Shepherd", 0, "hand")
    game.players[0].hand = [spell]
    game.cast_spell(0, spell, targets=[warded.instance_id], free_cast=True)

    guard = 0
    while not game.stack.is_empty and guard < 20:
        game.resolve_stack()
        game.check_state_based_actions()
        guard += 1

    on_bf = any(c.instance_id == spell.instance_id
                for c in game.players[0].battlefield)
    in_gy = any(c.instance_id == spell.instance_id
                for c in game.players[0].graveyard)
    assert on_bf and not in_gy, (
        "a permanent spell must enter the battlefield — ward on a target "
        "bound to its triggered ability cannot counter the spell itself "
        f"(on_battlefield={on_bf}, in_graveyard={in_gy})"
    )


def test_ward_still_counters_a_spell_that_actually_targets(card_db):
    """Regression guard: ward must still counter an instant/sorcery that
    genuinely targets the warded permanent when the tax goes unpaid."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    warded = _mk(game, card_db, "Kappa Cannoneer", 1, "battlefield")
    warded.enter_battlefield()
    game.players[1].battlefield.append(warded)
    game.players[1].creatures.append(warded)

    bolt = _mk(game, card_db, "Lightning Bolt", 0, "hand")  # instant, targets
    game.players[0].hand = [bolt]
    game.cast_spell(0, bolt, targets=[warded.instance_id], free_cast=True)
    guard = 0
    while not game.stack.is_empty and guard < 20:
        game.resolve_stack()
        game.check_state_based_actions()
        guard += 1

    # The instant genuinely targets the warded permanent; unpaid ward
    # counters it (it hits the graveyard without dealing its damage).
    in_gy = any(c.instance_id == bolt.instance_id
                for c in game.players[0].graveyard)
    assert in_gy, "an instant that targets a warded permanent is countered by unpaid ward"
