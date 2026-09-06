"""A modal "Choose one —" instant/sorcery resolves exactly ONE mode —
the one its controller chooses — honoring that mode's own restriction.

The engine synthesized one ability per mode and the per-ability
resolution loop ran ALL of them, so a multi-mode modal spell executed
every mode at once. Worse, the synthesized ability descriptions are
lossy (a mana-value cap on a mode is dropped), and the generic
"destroy all" branch destroyed all CREATURES regardless of the type
the mode actually names.

Concrete live bug (resolver over-match audit; Ruby Storm / Hollow One
sideboards): Brotherhood's End ("Choose one — deals 3 damage to each
creature and each planeswalker; OR destroy all artifacts with mana
value 3 or less") resolved BOTH modes and its "destroy all artifacts"
mode destroyed every creature on both battlefields.

Rules under test (mechanic-phrased; card names are fixture carriers):
  - a modal spell resolves exactly the chosen mode, not every mode;
  - the damage-sweep mode deals its damage to each creature (a 3-sweep
    is NOT a destroy-all — a higher-toughness creature survives);
  - the destroy mode honors the permanent TYPE and mana-value cap its
    own clause states (destroys artifacts within the cap, not creatures,
    not out-of-cap artifacts).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.spell_resolution import ResolutionManager
from engine.stack import StackItem, StackItemType


def _bf(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _cast_and_resolve(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    spell = CardInstance(template=tmpl, owner=controller, controller=controller,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=spell,
                     controller=controller, targets=[])
    ResolutionManager._execute_spell_effects(game, item)
    # Real resolution runs state-based actions after the effect; a
    # damage sweep marks lethal damage that SBAs then convert to death.
    game.check_state_based_actions()


class TestBrotherhoodsEndModal:
    def test_damage_mode_is_a_sweep_not_a_destroy_all(self, card_db):
        """Facing creatures and no artifacts, the controller takes the
        3-damage mode: creatures with toughness <= 3 die, a tougher
        creature SURVIVES (proving it is a damage sweep, not the old
        'destroy all creatures')."""
        game = GameState(rng=random.Random(0))
        # Opponent board: two small creatures (die to 3) + one tough one.
        small1 = _bf(game, card_db, "Memnite", 1)          # 1/1
        small2 = _bf(game, card_db, "Ragavan, Nimble Pilferer", 1)  # 2/1
        tough = _bf(game, card_db, "Griselbrand", 1)       # 7/7 — survives 3
        # No artifacts on board → the "destroy artifacts" mode is dead,
        # so the controller must pick the damage mode.

        _cast_and_resolve(game, card_db, "Brotherhood's End", 0)

        alive = {c.name for c in game.players[1].creatures}
        assert "Griselbrand" in alive, (
            "a 7-toughness creature must SURVIVE a 3-damage sweep — if it "
            "died, the buggy 'destroy all creatures' branch fired instead "
            "of the real 3-damage mode"
        )
        assert "Memnite" not in alive and "Ragavan, Nimble Pilferer" not in alive, (
            f"toughness<=3 creatures must die to the 3-damage sweep. "
            f"Survivors: {alive}"
        )

    def test_destroy_mode_honors_type_and_mana_value_cap(self, card_db):
        """Facing artifacts and no creatures worth sweeping, the
        controller takes the 'destroy all artifacts with mana value 3
        or less' mode: it destroys in-cap artifacts, leaves an
        above-cap artifact, and never touches creatures."""
        game = GameState(rng=random.Random(0))
        # Opponent board: two cheap artifacts (<=3) + one expensive
        # artifact (>3) + one of the caster's own creatures that a
        # damage sweep would hurt (so 'destroy artifacts' is the pick).
        cheap1 = _bf(game, card_db, "Mishra's Bauble", 1)   # MV 0 artifact
        cheap2 = _bf(game, card_db, "Cranial Plating", 1)   # MV 2 artifact
        big_art = _bf(game, card_db, "Batterskull", 1)      # MV 5 artifact
        # give the controller a creature so the damage mode is costly
        my_creature = _bf(game, card_db, "Griselbrand", 0)

        _cast_and_resolve(game, card_db, "Brotherhood's End", 0)

        opp_bf = {c.name for c in game.players[1].battlefield}
        assert "Mishra's Bauble" not in opp_bf and "Cranial Plating" not in opp_bf, (
            f"in-cap artifacts (MV<=3) must be destroyed. Board: {opp_bf}"
        )
        assert "Batterskull" in opp_bf, (
            f"an above-cap artifact (MV 5) must survive the 'mana value 3 "
            f"or less' cap. Board: {opp_bf}"
        )
        assert my_creature in game.players[0].battlefield, (
            "the destroy-artifacts mode must not touch creatures"
        )
