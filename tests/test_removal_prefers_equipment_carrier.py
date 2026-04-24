"""Bug R3 — Removal targeting must boost equipment carriers.

`_pick_best_removal_target` ranks candidates by `creature_threat_value`,
which is derived from raw P/T (already augmented for "for each artifact"
scaling) plus oracle amplifiers.  It does NOT add a tempo bonus when
the candidate is currently carrying equipment.  Killing the carrier
removes the body AND disassembles the combo — the equipment falls
off and must be re-equipped (paying the equip cost again).

This test pins the carrier-priority bonus.  Reference:
docs/diagnostics/2026-04-23_affinity_consolidated_findings.md §R3.

Detection rules (all oracle-driven):
  * Identify equipment attached to a creature via the
    `equipped_{instance_id}` `instance_tags` convention used by the
    engine (engine/game_state.py::equip_creature).
  * Compute the equipment's pump contribution from its oracle text
    (regex `equipped creature gets +X/+Y`, with optional `for each
    <artifact|creature|land|card>` scaler).  No card names are
    referenced.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
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


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _add_vanilla_creature(game, name, power, toughness, controller):
    """Add a synthetic vanilla creature with no oracle clauses, so its
    threat value comes purely from raw P/T.  Used as the "naked higher
    P/T" foil for the carrier-priority test."""
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=None,
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _attach_equipment(equipment, creature):
    """Attach `equipment` to `creature` via the engine's instance-tag
    convention (engine/game_state.py::equip_creature)."""
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


class TestRemovalPrefersEquipmentCarrier:
    """When a candidate creature is wearing equipment, removing it is
    worth more than its raw threat — the equipment falls off and must
    be re-equipped, costing tempo.  The removal-target ranker must
    apply that bonus."""

    def test_fatal_push_targets_carrier_over_higher_pt_naked_creature(
            self, card_db):
        """Memnite (1/1) carrying 1 Cranial Plating + 1 other artifact
        on board ⇒ Memnite at 3/1 (Plating's +X/+0 with X=2 artifacts).
        A naked 4/4 vanilla creature is also on board.  By raw threat
        value the 4/4 is bigger, but killing the Memnite ALSO
        disassembles the Plating engine — so the carrier is the
        correct removal target.

        Pre-fix: targets the 4/4 (`creature_threat_value` ignores the
        carrier bonus).  Post-fix: targets the Memnite (carrier
        bonus tips the ranking)."""
        game = GameState(rng=random.Random(0))

        # Opp board: Memnite + Plating (carrier) + a 4/4 naked vanilla
        memnite = _add_to_battlefield(game, card_db, "Memnite", controller=1)
        plating = _add_to_battlefield(game, card_db, "Cranial Plating",
                                       controller=1)
        big_naked = _add_vanilla_creature(game, "Big Vanilla 4/4",
                                          4, 4, controller=1)
        _attach_equipment(plating, memnite)

        # Sanity: with 2 artifacts (Memnite + Plating), Plating
        # contributes +2/+0 ⇒ Memnite at 3/1; the 4/4 vanilla outranks
        # it on raw P/T so the bug condition holds.
        assert memnite.power < big_naked.power, (
            f"setup precondition: carrier (P={memnite.power}) must have "
            f"strictly less raw power than the naked alternative "
            f"(P={big_naked.power}) so the bug condition holds."
        )

        # Player 0 has Fatal Push in hand and removes from opp's board.
        push = _add_to_hand(game, card_db, "Fatal Push", controller=0)
        player = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                          rng=random.Random(0))
        target = player._pick_best_removal_target(
            push, [memnite, big_naked], game.players[1], game, 1)
        assert target is memnite, (
            f"expected removal to target the Plating carrier (Memnite, "
            f"id={memnite.instance_id}); got "
            f"{target.name} (id={target.instance_id}). The carrier "
            f"bonus should outweigh the naked 4/4's higher base power."
        )

    def test_fatal_push_prefers_higher_pt_when_no_equipment_attached(
            self, card_db):
        """Regression anchor — same board WITHOUT equipment on the
        Memnite.  The 4/4 is now the legitimate larger threat and
        Fatal Push should prefer it."""
        game = GameState(rng=random.Random(0))
        memnite = _add_to_battlefield(game, card_db, "Memnite", controller=1)
        # Plating is on the battlefield but unattached — does not boost
        # any creature, so Memnite remains a vanilla 1/1.
        _add_to_battlefield(game, card_db, "Cranial Plating", controller=1)
        big_naked = _add_vanilla_creature(game, "Big Vanilla 4/4",
                                          4, 4, controller=1)

        push = _add_to_hand(game, card_db, "Fatal Push", controller=0)
        player = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                          rng=random.Random(0))
        target = player._pick_best_removal_target(
            push, [memnite, big_naked], game.players[1], game, 1)
        assert target is big_naked, (
            f"with no equipment attached, the naked 4/4 is the larger "
            f"raw threat and must be the target. Got "
            f"{target.name} (id={target.instance_id})."
        )

    def test_fatal_push_prefers_plated_smaller_over_bigger_carrier(
            self, card_db):
        """Two carriers: a 1-Plating Frogmite (5/2 with 3 artifacts)
        vs an unequipped 4/4.  Carrier bonus + raw P/T both favour the
        Frogmite — verifies the bonus stacks correctly without flipping
        when the carrier is also the larger raw body.

        This is the "Frogmite (2/2) equipped with Plating, and naked
        Sojourner's Companion (4/4)" scenario from the R3 spec, with
        the synthetic vanilla as the foil so the test does not depend
        on a specific card name's stats."""
        game = GameState(rng=random.Random(0))
        frog = _add_to_battlefield(game, card_db, "Frogmite", controller=1)
        plating = _add_to_battlefield(game, card_db, "Cranial Plating",
                                       controller=1)
        big_naked = _add_vanilla_creature(game, "Big Vanilla 4/4",
                                          4, 4, controller=1)
        _attach_equipment(plating, frog)

        # Sanity: 3 artifacts (Frogmite + Plating + the 4/4 is NOT
        # an artifact, so just 2) — Plating contributes +2/+0 →
        # Frogmite=4/2.  Either way, the carrier bonus must keep the
        # ranking on the equipped Frogmite.
        push = _add_to_hand(game, card_db, "Fatal Push", controller=0)
        player = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                          rng=random.Random(0))
        target = player._pick_best_removal_target(
            push, [frog, big_naked], game.players[1], game, 1)
        assert target is frog, (
            f"equipped Frogmite must be the target over a naked 4/4; "
            f"got {target.name} (id={target.instance_id})."
        )
