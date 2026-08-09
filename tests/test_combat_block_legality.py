"""CR 509.1b — combat block legality enforcement.

`CombatManager.declare_blockers`'s docstring claimed to "validate and
record" blocking assignments; the body only recorded them — flying,
reach, menace, and protection were entirely unenforced at the engine
layer. `ai/ev_player.py::decide_blockers` already filters candidates
by these same rules before proposing a block, but that's the AI being
polite to itself: nothing stopped an illegal assignment from being
recorded and acted on as legal if it reached this layer any other
way (a different caller, a bug in that filter).

Card names appear only as fixture carriers (synthetic CardTemplates)
per CLAUDE.md's ABSTRACTION CONTRACT — the mechanic under test is
CR 509.1b block legality, not any specific card.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.combat_manager import CombatManager
from engine.game_state import GameState
from engine.mana import Color


def _creature(game, name, controller, power=2, toughness=2,
              keywords=None, protection_from_colors=frozenset(),
              colors=frozenset()):
    tmpl = CardTemplate(
        name=name, card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=keywords or set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
        protection_from_colors=frozenset(protection_from_colors),
        colors=set(colors),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _combat(game, attackers):
    cm = CombatManager()
    cm.declare_attackers(game, attackers, active_player=1)
    return cm


class TestFlyingEvasion:
    """CR 702.9b — a flying attacker can only be blocked by a
    creature with flying and/or reach."""

    def test_grounded_blocker_dropped_against_flyer(self):
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "Flyer", 1, keywords={Keyword.FLYING})
        blocker = _creature(game, "Grounded", 0)

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})

        assignment = cm._assignments[0]
        assert assignment.blocker_ids == [], (
            "grounded creature must not be recorded as a legal "
            "blocker of a flying attacker"
        )
        assert not assignment.is_blocked

    def test_flying_blocker_is_legal_against_flyer(self):
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "Flyer", 1, keywords={Keyword.FLYING})
        blocker = _creature(game, "AlsoFlies", 0, keywords={Keyword.FLYING})

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})

        assignment = cm._assignments[0]
        assert assignment.blocker_ids == [blocker.instance_id]
        assert assignment.is_blocked

    def test_reach_blocker_is_legal_against_flyer(self):
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "Flyer", 1, keywords={Keyword.FLYING})
        blocker = _creature(game, "Reacher", 0, keywords={Keyword.REACH})

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})

        assignment = cm._assignments[0]
        assert assignment.blocker_ids == [blocker.instance_id]
        assert assignment.is_blocked

    def test_grounded_attacker_can_be_blocked_by_grounded_creature(self):
        """Regression: no evasion means no restriction — the fix must
        not over-apply."""
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "Grounded1", 1)
        blocker = _creature(game, "Grounded2", 0)

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})

        assignment = cm._assignments[0]
        assert assignment.blocker_ids == [blocker.instance_id]
        assert assignment.is_blocked


class TestMenace:
    """CR 702.111b — a menace attacker can't be blocked by fewer than
    2 creatures; the block is illegal in its ENTIRETY, not partially
    legal with one blocker dropped."""

    def test_single_blocker_against_menace_is_illegal(self):
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "Menacer", 1, keywords={Keyword.MENACE})
        blocker = _creature(game, "Solo", 0)

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})

        assignment = cm._assignments[0]
        assert assignment.blocker_ids == [], (
            "a single blocker must not be recorded against a menace "
            "attacker — the whole block is illegal"
        )
        assert not assignment.is_blocked

    def test_two_blockers_against_menace_is_legal(self):
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "Menacer", 1, keywords={Keyword.MENACE})
        b1 = _creature(game, "Blocker1", 0)
        b2 = _creature(game, "Blocker2", 0)

        cm = _combat(game, [attacker])
        cm.declare_blockers(
            game, {attacker.instance_id: [b1.instance_id, b2.instance_id]})

        assignment = cm._assignments[0]
        assert set(assignment.blocker_ids) == {b1.instance_id, b2.instance_id}
        assert assignment.is_blocked

    def test_no_block_against_menace_stays_unblocked(self):
        """Regression: declining to block at all must not be treated
        as an illegal single-blocker case."""
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "Menacer", 1, keywords={Keyword.MENACE})

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {})

        assignment = cm._assignments[0]
        assert assignment.blocker_ids == []
        assert not assignment.is_blocked


class TestProtection:
    """CR 702.16d — a permanent with protection from a quality can't
    be blocked by a creature with that quality."""

    def test_blocker_of_protected_color_is_illegal(self):
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "ProtFromRed", 1,
                             protection_from_colors={Color.RED})
        blocker = _creature(game, "RedBlocker", 0, colors={Color.RED})

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})

        assignment = cm._assignments[0]
        assert assignment.blocker_ids == []
        assert not assignment.is_blocked

    def test_blocker_of_unprotected_color_is_legal(self):
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "ProtFromRed", 1,
                             protection_from_colors={Color.RED})
        blocker = _creature(game, "GreenBlocker", 0, colors={Color.GREEN})

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})

        assignment = cm._assignments[0]
        assert assignment.blocker_ids == [blocker.instance_id]
        assert assignment.is_blocked

    def test_real_protection_card_field_populated(self, card_db):
        """Structured-field integration: a real protection card's
        oracle text is parsed into protection_from_colors at DB-load
        time, including the compound 'from X and from Y' form."""
        sanctifier = card_db.get_card("Sanctifier en-Vec")
        if sanctifier is None:
            import pytest
            pytest.skip("Sanctifier en-Vec not in DB")
        assert sanctifier.protection_from_colors == {Color.BLACK, Color.RED}


class TestIllegalBlockLogged:
    def test_dropped_block_is_logged(self):
        """A dropped illegal block must leave a trace in game.log —
        silent drops are indistinguishable from AI-side no-block
        decisions and make debugging AI behavior impossible."""
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "Flyer", 1, keywords={Keyword.FLYING})
        blocker = _creature(game, "Grounded", 0)

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})

        assert any("illegal block" in line.lower() for line in game.log)
