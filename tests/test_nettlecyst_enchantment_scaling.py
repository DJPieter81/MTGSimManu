"""Class A — Nettlecyst's "and/or enchantment" clause is unscored.

Phase L finding (`docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md`).

Nettlecyst oracle:
    Living weapon (When this Equipment enters, create a 0/0 black
    Phyrexian Germ creature token, then attach this to it.)
    Equipped creature gets +1/+1 for each artifact and/or enchantment
    you control.
    Equip {2}

The engine's equipment-power-scaling code (`engine/cards.py:392, 423`)
matches `'for each artifact'` in oracle text and adds
`_get_artifact_count()`. The `'and/or enchantment'` clause is
silently dropped — enchantments do NOT contribute to the scaling.

This is rules-incorrect under MTG comprehensive rules: the equipped
creature gets +1/+1 per (artifact OR enchantment) you control. The
mechanic is a closed-form set union, not a "first listed type" scan.

Affinity itself rarely runs enchantments mainboard, so this is a
silent-but-zero-impact bug for that deck. Pinnacle Affinity and any
future variant that splashes enchantments (Saheeli's Lattice, Leyline
of the Guildpact, etc.) will be mis-scored.

Rule-phrased test: "equipment that scales with 'X and/or Y' counts
both X and Y." The mechanic is the union, not the first listed type.
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


def _add_to_battlefield(game, card_db, name, controller, fire_etb=True):
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
    if fire_etb:
        # Run the EFFECT_REGISTRY-registered handler directly so
        # Living-Weapon / similar ETB side effects (Germ-token
        # creation + auto-attach) actually take effect in tests.
        game._handle_permanent_etb(card, controller)
    return card


class TestNettlecystEnchantmentScaling:
    """Equipment whose scaling clause names 'X and/or Y' must count
    both X and Y, not just X."""

    def test_nettlecyst_counts_artifacts_alone(self, card_db):
        """Baseline regression: with N artifacts and 0 enchantments,
        Nettlecyst grants the carrier +N/+N. This already works at
        HEAD; the test pins it so the fix doesn't regress it."""
        game = GameState(rng=random.Random(0))
        # 3 artifacts on board + Nettlecyst (also an artifact)
        _add_to_battlefield(game, card_db, "Mox Opal", controller=0)
        _add_to_battlefield(game, card_db, "Springleaf Drum", controller=0)
        _add_to_battlefield(game, card_db, "Memnite", controller=0)
        nettle = _add_to_battlefield(game, card_db, "Nettlecyst",
                                      controller=0)
        # Living weapon should have created a Germ token already
        # attached. Find it on battlefield.
        nettle_iid = nettle.instance_id
        equipped_tag = f"equipped_{nettle_iid}"
        carriers = [c for c in game.players[0].battlefield
                    if equipped_tag in c.instance_tags]
        assert carriers, "Living Weapon should have created and " \
                         "equipped a Germ token"
        carrier = carriers[0]

        # Artifact count: Mox Opal + Springleaf Drum + Memnite +
        # Nettlecyst itself + Germ token (Germ token is a creature,
        # NOT an artifact, per the Nettlecyst oracle).
        # So 4 artifacts total → +4/+4 to Germ.
        # Germ base 0/0 → 4/4.
        assert carrier.power == 4, (
            f"Germ token power should be 4 (4 artifacts × +1/+1), "
            f"got {carrier.power}"
        )
        assert carrier.toughness == 4, (
            f"Germ token toughness should be 4 (4 artifacts × +1/+1), "
            f"got {carrier.toughness}"
        )

    def test_nettlecyst_counts_enchantments_too(self, card_db):
        """The smoking gun: with 2 artifacts + 2 enchantments, the
        Germ token should get +4/+4 (2 + 2 = 4 permanents from the
        union "artifact and/or enchantment").

        Pre-fix: only artifacts count → +2/+2. Test fails.
        Post-fix: union count → +4/+4. Test passes.
        """
        game = GameState(rng=random.Random(0))
        # 2 artifacts (one of which is Nettlecyst itself)
        _add_to_battlefield(game, card_db, "Mox Opal", controller=0)
        # 2 non-artifact, non-land enchantments
        _add_to_battlefield(game, card_db, "Leyline Binding", controller=0)
        _add_to_battlefield(game, card_db, "Leyline of Sanctity",
                            controller=0)
        nettle = _add_to_battlefield(game, card_db, "Nettlecyst",
                                      controller=0)

        nettle_iid = nettle.instance_id
        equipped_tag = f"equipped_{nettle_iid}"
        carriers = [c for c in game.players[0].battlefield
                    if equipped_tag in c.instance_tags]
        assert carriers, "Living Weapon should have created Germ token"
        carrier = carriers[0]

        # Permanents counted: Mox Opal (artifact) + Nettlecyst
        # (artifact) + Leyline Binding (enchantment) + Leyline of
        # Sanctity (enchantment) = 4 (artifacts ∪ enchantments).
        # Some of these might also be artifact+enchantment, so we
        # use a set union — the test counts each card once.
        # Germ base 0/0 → 4/4.
        assert carrier.power == 4, (
            f"Germ token power should be 4 (2 artifacts + 2 "
            f"enchantments), got {carrier.power}. "
            f"The 'and/or enchantment' clause must count enchantments "
            f"in addition to artifacts."
        )
        assert carrier.toughness == 4, (
            f"Germ token toughness should be 4 (2 artifacts + 2 "
            f"enchantments), got {carrier.toughness}."
        )
