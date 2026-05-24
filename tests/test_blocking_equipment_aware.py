"""Phase 2B — Block evaluation reads equipped P/T and equipped value.

Rule under test
---------------
The block-prediction subsystem (``ai/turn_planner.py:_predict_blocks``
and the ``VirtualCreature`` projection in ``capture_board``) must
read the **dynamic** P/T of each attacker — the value already
reflecting any attached equipment, +1/+1 counters, and active
scaling effects.

A 1/1 Memnite carrying Cranial Plating on a 4-artifact board has
effective P/T 5/1, not 1/1. The block evaluation must:

  1. Project ``power=5, toughness=1`` (not ``power=1`` from the
     printed stats) so the must-block phase recognizes the swing.
  2. Project ``value`` that includes the equipment's contribution
     so trade-up / chump decisions value the equipped carrier
     correctly (a high-value attacker triggers double-block;
     killing the carrier strands the equipment unattached for the
     opponent's next turn).

Pre-existing infrastructure
---------------------------
- ``CardInstance.power`` (engine/cards.py:262 area) is dynamic via
  ``_dynamic_base_power`` — already includes attached-equipment
  bonus through ``equipped_{iid}`` instance tags.
- ``ai/evaluator.py:_permanent_value`` reads ``card.power`` and adds
  ``EQUIPPED_CREATURE_VULNERABILITY_BONUS`` for any creature
  carrying instance tags.
- ``to_virtual_creature`` (turn_planner.py:1112) reads ``card.power``
  and ``card.toughness`` directly + delegates ``value`` to
  ``_permanent_value``.

Therefore the propagation should already work. This test pins the
rule end-to-end so any regression that breaks the dynamic-P/T →
VirtualCreature → block-prediction chain is caught immediately.

Reference: /root/.claude/plans/now-lets-fix-affinity-keen-penguin.md
Phase 2B.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.game_state import GameState


def _put_in_play(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _equip(equipment, creature):
    """Attach equipment to creature via instance tag (mirrors what
    game_state.equip_creature produces)."""
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")


# ─── Dynamic P/T propagates to VirtualCreature ───────────────────────


def test_equipped_memnite_virtual_power_includes_plating(card_db):
    """A 1/1 Memnite with Cranial Plating attached on a 4-artifact
    board (Memnite + Mox + Ornithopter + Plating = 4 artifacts) has
    effective power 1 + 4 = 5. ``to_virtual_creature`` must read
    that dynamic power, not the printed 1."""
    from ai.turn_planner import extract_virtual_board as capture_board

    game = GameState(rng=random.Random(0))
    memnite = _put_in_play(game, card_db, "Memnite", 0)
    mox = _put_in_play(game, card_db, "Mox Opal", 0)
    orni = _put_in_play(game, card_db, "Ornithopter", 0)
    plating = _put_in_play(game, card_db, "Cranial Plating", 0)
    _equip(plating, memnite)

    # Dynamic P/T sanity: Memnite should read 5 power on the engine
    # side already.
    assert memnite.power == 5, (
        f"Engine-side dynamic P/T expected Memnite=5, got "
        f"{memnite.power}. Plating's '+1/+0 for each artifact you "
        f"control' isn't propagating to the carrier."
    )

    board = capture_board(game, 0)
    memnite_vc = next(
        (vc for vc in board.my_creatures if vc.instance_id == memnite.instance_id),
        None,
    )
    assert memnite_vc is not None
    assert memnite_vc.power == 5, (
        f"VirtualCreature for equipped Memnite must report power=5 "
        f"(includes Plating bonus). Got {memnite_vc.power}. "
        f"to_virtual_creature is reading template.power instead of "
        f"dynamic card.power."
    )


def test_equipped_memnite_virtual_value_includes_equipment(card_db):
    """The VirtualCreature.value for an equipped Memnite must reflect
    the equipment's threat (higher than a bare 1/1 Memnite). This
    drives the block-evaluation to recognize the carrier as a
    high-value attacker."""
    from ai.turn_planner import extract_virtual_board as capture_board

    # Bare board
    g_a = GameState(rng=random.Random(0))
    bare = _put_in_play(g_a, card_db, "Memnite", 0)
    board_a = capture_board(g_a, 0)
    bare_vc = next(vc for vc in board_a.my_creatures
                   if vc.instance_id == bare.instance_id)

    # Equipped board
    g_b = GameState(rng=random.Random(0))
    memnite = _put_in_play(g_b, card_db, "Memnite", 0)
    _put_in_play(g_b, card_db, "Mox Opal", 0)
    _put_in_play(g_b, card_db, "Ornithopter", 0)
    plating = _put_in_play(g_b, card_db, "Cranial Plating", 0)
    _equip(plating, memnite)
    board_b = capture_board(g_b, 0)
    equipped_vc = next(vc for vc in board_b.my_creatures
                       if vc.instance_id == memnite.instance_id)

    assert equipped_vc.value > bare_vc.value, (
        f"Equipped Memnite must score higher VirtualCreature.value "
        f"than a bare Memnite. Got equipped={equipped_vc.value:.2f}, "
        f"bare={bare_vc.value:.2f}. _permanent_value is not picking "
        f"up the equipment via dynamic power + instance_tags."
    )


# ─── Block prediction prioritizes the equipped carrier ───────────────


def test_block_prediction_prefers_blocking_equipped_carrier_over_bare(card_db):
    """When two attackers are present — a bare Memnite (1/1) and an
    equipped Memnite (5/1 with Plating) — the must-block phase must
    direct blockers at the equipped one when only one block is
    available. (The test pins that ``_predict_blocks`` reads the
    dynamic power, so the equipped Memnite shows up as a higher
    threat in the sorted list and consumes the block.)
    """
    from ai.turn_planner import extract_virtual_board as capture_board
    from ai.turn_planner import CombatPlanner

    game = GameState(rng=random.Random(0))
    # Attackers (player 0)
    bare = _put_in_play(game, card_db, "Memnite", 0)
    equipped_carrier = _put_in_play(game, card_db, "Memnite", 0)
    _put_in_play(game, card_db, "Mox Opal", 0)
    _put_in_play(game, card_db, "Ornithopter", 0)
    plating = _put_in_play(game, card_db, "Cranial Plating", 0)
    _equip(plating, equipped_carrier)
    # One blocker on the defender side (player 1) — say a 4/4.
    # Use Phlage as a 3/3 stand-in is awkward; synthesize a
    # vanilla 4/4 instead.
    from engine.cards import CardTemplate
    from engine.mana import ManaCost
    blocker_t = CardTemplate(
        name="Test Big Blocker 4/4",
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=4),
        power=4, toughness=4, oracle_text="",
    )
    blocker = CardInstance(
        template=blocker_t, owner=1, controller=1,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    blocker._game_state = game
    blocker.enter_battlefield()
    blocker.summoning_sick = False
    game.players[1].battlefield.append(blocker)

    # Set defender life low enough that the must-block phase fires.
    # Bare + equipped attack = 1 + 6 = 7 damage. Defender at 3 life
    # → must-block to survive. (Equipped carrier reads power 6:
    # Plating's "+1/+0 for each artifact you control" + 5 artifacts:
    # both Memnites + Mox + Ornithopter + Plating.)
    game.players[1].life = 3

    board = capture_board(game, 1)  # player_idx=1 = defender

    # Filter to attacking-side creatures (controller=0).
    attackers = [vc for vc in board.opp_creatures
                 if vc.instance_id in
                 {bare.instance_id, equipped_carrier.instance_id}]
    blockers = [vc for vc in board.my_creatures
                if vc.instance_id == blocker.instance_id]

    # Sanity: equipped carrier reports power = 1 (template) + 5
    # (artifacts: 2 Memnites + Mox + Ornithopter + Plating).
    eq_vc = next(vc for vc in attackers
                 if vc.instance_id == equipped_carrier.instance_id)
    assert eq_vc.power == 6, (
        f"Expected equipped Memnite power=6 (1 + 5 artifacts), got "
        f"{eq_vc.power}."
    )

    # Build a planner and ask for predicted blocks.
    planner = CombatPlanner()
    blocks = planner._predict_blocks(attackers, blockers, board)

    # The defender's single blocker should be assigned to the
    # equipped carrier (highest-power attacker, reduces incoming
    # most). The bare 1/1 Memnite gets through.
    assert equipped_carrier.instance_id in blocks, (
        f"Block prediction must assign the lone blocker to the "
        f"equipped carrier (5/1 Plating Memnite), not the bare 1/1. "
        f"Got blocks={blocks}. Reading bare power=1 vs equipped "
        f"power=5 — must-block should target the high-damage "
        f"attacker."
    )
    assert bare.instance_id not in blocks, (
        f"Bare 1/1 Memnite should NOT be the must-block target when "
        f"the equipped 5/1 carrier is present. blocks={blocks}."
    )
