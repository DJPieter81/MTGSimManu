"""Phase L PR-L3 — equipment-ceiling threat lift.

Rule pinned: when a controller has an Equipment on the battlefield with
a static `gets +N/+M …` oracle modifier (flat or `for each <type>`
scaling), the threat value of any of that controller's creatures that
could legally be equipped should be lifted by an amount reflecting the
equipment's potential P/T contribution. Equally, the equipment itself
should register a substantive `permanent_threat` when unattached on a
board with a viable equip target — Wear // Tear / Disenchant must see
the equipment as a real removal target, not a 0.0 throwaway.

Class size: applies to all Modern Equipment with a static +N/+M
modifier — Cranial Plating, Nettlecyst, Colossus Hammer, Bonesplitter,
Sword cycle, Embercleave, Hammer of Bogardan, future printings. Card
names appear only in fixture setup; function names describe the
mechanic.

Discovered via Phase L follow-up audit
(``docs/diagnostics/2026-05-04_affinity_plating_threat_undervaluation_audit.md``).
Reproduction harness:

  Affinity board: Memnite (1/1), Cranial Plating (UNATTACHED), Mox Opal,
  Springleaf Drum, Darksteel Citadel.
  Boros board:    Goblin Guide (2/2 haste).

Pre-fix:
  creature_threat_value(Memnite) ≈ 1.15  ← Bolt picks Goblin Guide (8.15)
  permanent_threat(Cranial Plating) = 0.00  ← Wear//Tear sees no value

Post-fix:
  creature_threat_value(Memnite) ≥ 5.0  (within Goblin Guide range)
  permanent_threat(Cranial Plating) ≥ 8.0  (substantive removal target)
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _battlefield(game, db, name: str, controller: int) -> CardInstance:
    tmpl = db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _equip(equipment: CardInstance, creature: CardInstance) -> None:
    """Mirror ``GameState.equip_creature``'s instance-tag semantics
    without paying mana — fixture-only attachment."""
    equip_tag = f"equipped_{equipment.instance_id}"
    creature.instance_tags.add(equip_tag)
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")


def _setup_affinity_board(card_db, with_unattached_plating: bool = True):
    """Affinity-style board: Memnite + 4 artifacts (counting Plating
    itself), with Plating either UNATTACHED or absent.

    Returns (game, memnite, plating-or-None).
    """
    game = GameState(rng=random.Random(0))
    memnite = _battlefield(game, card_db, "Memnite", 0)
    _battlefield(game, card_db, "Mox Opal", 0)
    _battlefield(game, card_db, "Springleaf Drum", 0)
    _battlefield(game, card_db, "Darksteel Citadel", 0)
    plating = None
    if with_unattached_plating:
        plating = _battlefield(game, card_db, "Cranial Plating", 0)
        # Mark explicitly as unattached (enter_battlefield should
        # already do this via the equipment_unattached tag, but
        # be defensive — the fixture must pin the tag state).
        plating.instance_tags.discard("equipment_attached")
        plating.instance_tags.add("equipment_unattached")
    return game, memnite, plating


def test_plus_N_per_artifact_equipment_ceiling_lifts_recipient_threat(card_db):
    """Rule: Equipment with `+N/+M for each <type>` oracle text raises
    the threat ceiling of its controller's creatures that could legally
    be equipped (i.e. equipment is unattached or could rebind to them).

    Setup: Affinity-style board with Memnite + 4 artifacts (Plating
    counted) and an UNATTACHED Cranial Plating. Memnite's CURRENT P/T
    is 1/1, but next turn Memnite is a 5/1 (4 artifacts × +1/+0).
    Removal-targeting code must see this future value, not just the
    current 1/1.
    """
    from ai.ev_evaluator import creature_threat_value

    game, memnite, plating = _setup_affinity_board(card_db, True)
    threat = creature_threat_value(memnite)

    # Sanity baseline — same Memnite on a board with NO Plating
    # should score the vanilla 1/1 floor (~1.15).
    game_no_plating, memnite_no_plating, _ = _setup_affinity_board(
        card_db, with_unattached_plating=False
    )
    baseline = creature_threat_value(memnite_no_plating)
    assert baseline < 2.5, (
        f"Vanilla 1/1 Memnite baseline on a Plating-free board scored "
        f"{baseline:.2f}; expected < 2.5. Test environment changed."
    )

    # Threat with Plating unattached must be lifted to reflect the
    # equipment-ceiling. Threshold of 5.0 places Memnite-with-Plating
    # in roughly the same removal-priority bucket as Goblin Guide
    # (~8.15), which is the targeting parity Phase L PR #293 requires.
    assert threat >= 5.0, (
        f"creature_threat_value(Memnite) on a board with unattached "
        f"Cranial Plating + 4 artifacts returned {threat:.2f}; "
        f"expected ≥ 5.0. Memnite is the next equip target — once "
        f"Plating attaches, it swings as a 5/1 (within 1-shot of "
        f"Boros's life). Removal-targeting must see this ceiling, "
        f"not the current 1/1. "
        f"Fix: ai/permanent_threat.py must walk controller.battlefield "
        f"for unattached/rebindable equipment with `gets +N/+M …` "
        f"oracle, parse the modifier (and any `for each X` scaling), "
        f"and add the ceiling to creature_threat_value."
    )


def test_plus_N_per_artifact_ceiling_does_not_apply_when_equipment_committed_elsewhere(card_db):
    """When the equipment is ALREADY attached to creature A and the
    target is creature B, B's threat should NOT receive the full
    ceiling lift — the equipment is committed (re-attaching costs
    mana). The lift may apply DISCOUNTED, but must be lower than the
    unattached case.

    Setup: Affinity board with TWO creatures (Memnite + a Construct
    proxy via Phyrexian Walker), Plating already attached to Memnite.
    Walker's threat must NOT include the full Plating ceiling.
    """
    from ai.ev_evaluator import creature_threat_value

    game = GameState(rng=random.Random(0))
    # Two creatures so we have a non-equipped target whose threat we
    # want to bound.
    memnite = _battlefield(game, card_db, "Memnite", 0)
    walker_name = None
    for cand in ("Phyrexian Walker", "Ornithopter", "Memnite"):
        if card_db.get_card(cand):
            walker_name = cand
            break
    assert walker_name, "DB has no second 0/2 or 0/1 artifact creature"
    walker = _battlefield(game, card_db, walker_name, 0)
    walker.instance_id += 1  # ensure distinct instance for tag binding
    _battlefield(game, card_db, "Mox Opal", 0)
    _battlefield(game, card_db, "Springleaf Drum", 0)
    _battlefield(game, card_db, "Darksteel Citadel", 0)
    plating = _battlefield(game, card_db, "Cranial Plating", 0)
    _equip(plating, memnite)

    # Walker (the non-equipped target) should not get the full lift —
    # the equipment is already attached to a different creature.
    threat_walker = creature_threat_value(walker)
    threat_memnite = creature_threat_value(memnite)
    # Memnite (the currently equipped) has its dynamic P/T already
    # reflecting the buff. Walker has no buff applied — threat must
    # be bounded below Memnite's.
    assert threat_walker < threat_memnite, (
        f"Walker threat ({threat_walker:.2f}) should be bounded below "
        f"the currently-equipped Memnite ({threat_memnite:.2f}) — the "
        f"equipment is committed, so the rebind ceiling is discounted. "
        f"Fix must apply a re-attach feasibility multiplier when the "
        f"equipment is currently attached to a different creature."
    )


def test_flat_plus_N_equipment_lifts_threat(card_db):
    """Equipment with a flat `gets +N/+M` (no `for each` clause) also
    lifts threat ceiling. Same mechanic, simpler arithmetic.

    Setup: Memnite + UNATTACHED Colossus Hammer (+10/+10). Memnite is
    a 1/1 today, an 11/11 next turn. Threat must be substantially
    lifted.
    """
    from ai.ev_evaluator import creature_threat_value

    game = GameState(rng=random.Random(0))
    memnite = _battlefield(game, card_db, "Memnite", 0)
    hammer = _battlefield(game, card_db, "Colossus Hammer", 0)
    hammer.instance_tags.discard("equipment_attached")
    hammer.instance_tags.add("equipment_unattached")

    threat = creature_threat_value(memnite)
    # +10/+10 makes Memnite an 11/11 = roughly equivalent to the full
    # opp_life clock on the standard 20-life board. Threat must be
    # comparable to a top-end Modern threat (≥ 8.0).
    assert threat >= 8.0, (
        f"creature_threat_value(Memnite) with unattached Colossus "
        f"Hammer (+10/+10) returned {threat:.2f}; expected ≥ 8.0. "
        f"Flat-modifier equipment must lift threat ceiling identically "
        f"to scaling equipment — same mechanic, just no `for each X` "
        f"multiplier. The fix must parse `gets +N/+M` regardless of "
        f"the trailing `for each X` clause."
    )


def test_unattached_equipment_with_modifier_has_substantive_permanent_threat(card_db):
    """Rule: ``permanent_threat`` on an unattached Equipment with a
    static `gets +N/+M …` modifier must return a substantive value —
    the equipment is itself a removal-priority target because of its
    option-to-attach.

    Wear // Tear / Disenchant / Wrenn and Six's −7 etc. all consult
    ``permanent_threat`` to rank artifact/enchantment targets. A 0.0
    score makes Plating invisible to artifact removal.
    """
    from ai.permanent_threat import permanent_threat

    game, memnite, plating = _setup_affinity_board(card_db, True)
    assert plating is not None
    owner = game.players[plating.controller]
    threat = permanent_threat(plating, owner, game)

    assert threat >= 8.0, (
        f"permanent_threat(unattached Cranial Plating) returned "
        f"{threat:.2f}; expected ≥ 8.0. With 4 artifacts in play and "
        f"Memnite as an equip target, Plating is a finisher whose "
        f"value is its option-to-attach. Pre-fix, the marginal-"
        f"contribution formula returns 0.00 because removing an "
        f"unattached equipment doesn't drop any creature's dynamic "
        f"P/T — but strategically the equipment IS the win condition. "
        f"Fix: when permanent_threat sees an unattached/rebindable "
        f"Equipment with a `gets +N/+M …` modifier and at least one "
        f"viable equip target, return the projected ceiling-lift on "
        f"the best target minus the equip cost."
    )


def test_equipment_with_no_modifier_does_not_lift_threat(card_db):
    """Regression: equipment with NO `gets +N/+M` static modifier
    (e.g. Lightning Greaves — only grants haste/shroud) must NOT
    lift the recipient's threat ceiling. Without a buff in the oracle,
    there is no ceiling to project, and we must avoid over-flagging
    every creature on a Greaves board as a removal target.

    This is the negative-class regression that bounds the new rule:
    the lift fires only when the oracle text contains a `gets +N/+M`
    pattern.
    """
    from ai.ev_evaluator import creature_threat_value

    game = GameState(rng=random.Random(0))
    # Vanilla 1/1
    memnite = _battlefield(game, card_db, "Memnite", 0)
    _battlefield(game, card_db, "Mox Opal", 0)
    _battlefield(game, card_db, "Springleaf Drum", 0)
    _battlefield(game, card_db, "Darksteel Citadel", 0)
    greaves = _battlefield(game, card_db, "Lightning Greaves", 0)
    greaves.instance_tags.discard("equipment_attached")
    greaves.instance_tags.add("equipment_unattached")

    threat = creature_threat_value(memnite)

    # Greaves has no `gets +N/+M` clause — no ceiling lift expected.
    # Threat should remain near the vanilla 1/1 baseline (~1.15).
    assert threat < 3.0, (
        f"creature_threat_value(Memnite) with unattached Lightning "
        f"Greaves on board returned {threat:.2f}; expected < 3.0. "
        f"Greaves grants haste/shroud, no static +N/+M — there is no "
        f"power/toughness ceiling to project. The new rule must gate "
        f"on the presence of `gets +\\d+/+\\d+` in the equipment's "
        f"oracle text; firing on every Equipment over-flags removal "
        f"targets and breaks the ratchet."
    )
