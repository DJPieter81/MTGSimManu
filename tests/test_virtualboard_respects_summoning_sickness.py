"""Bug LE-T1 — VirtualBoard must respect summoning sickness in plan_attack.

Context: `ai/turn_planner.py::to_virtual_creature` (inside
`extract_virtual_board`) used to set ``is_tapped=card.tapped``, which did
NOT check ``card.summoning_sick``. Because
``CombatPlanner.plan_attack`` filters attackers by ``not c.is_tapped``,
summoning-sick creatures incorrectly appeared as valid attackers in
strategic planning. The game engine itself respects summoning sickness
via ``can_attack()``, so gameplay was correct — but the AI's race/clock
evaluation was off-by-one turn on every cascade / reanimation payoff
(Living End, Goryo's, Persist decks).

Fix: `is_tapped=card.tapped or card.summoning_sick,` at
``ai/turn_planner.py:1085``. The VirtualCreature field name
``is_tapped`` is a misnomer for "cannot attack" but renaming is out of
scope for this bundle.

Reference: docs/diagnostics/2026-04-24_living_end_consolidated_findings.md
(branch claude/diag-living-end-consolidated), bug LE-T1 (Bundle LE-1).
"""
from __future__ import annotations

import random

import pytest

from ai.turn_planner import CombatPlanner, extract_virtual_board
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _put_creature_on_battlefield(game, card_db, name, controller, *,
                                  summoning_sick, tapped=False):
    """Place a creature onto the battlefield in a controlled state.

    Mirrors the helper in tests/test_decide_blockers_emergency_gate.py,
    but exposes explicit ``summoning_sick`` / ``tapped`` flags so the
    VirtualBoard extraction path can be exercised directly.
    """
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()  # sets summoning_sick=True by default
    card.summoning_sick = summoning_sick
    card.tapped = tapped
    game.players[controller].battlefield.append(card)
    return card


def _fresh_game():
    game = GameState(rng=random.Random(0))
    game.players[0].life = 20
    game.players[1].life = 20
    return game


class TestVirtualBoardSummoningSickness:
    """plan_attack() must treat summoning-sick creatures the same as
    tapped creatures — i.e. they cannot be selected as attackers."""

    def test_summoning_sick_creature_is_not_a_valid_attacker(self, card_db):
        """P1 controls a summoning-sick, untapped 2/1 (Monastery Swiftspear
        without haste being honored — we model a non-haste common to make
        the assertion concrete). plan_attack must return NO attackers."""
        game = _fresh_game()
        # Guide of Souls — 1/1 with lifelink, no haste. Classic fresh-cast.
        _put_creature_on_battlefield(
            game, card_db, "Guide of Souls",
            controller=0, summoning_sick=True, tapped=False,
        )

        board = extract_virtual_board(game, player_idx=0)
        # Sanity: extraction must produce exactly one virtual creature.
        assert len(board.my_creatures) == 1, (
            "extract_virtual_board should emit one VirtualCreature"
        )

        planner = CombatPlanner()
        attackers, _delta = planner.plan_attack(board)

        sick_ids = {c.instance_id for c in board.my_creatures}
        assert not any(a.instance_id in sick_ids for a in attackers), (
            "Summoning-sick creature must NOT appear in plan_attack()'s "
            "attackers list. Current bug (LE-T1) allows it because "
            "is_tapped=card.tapped only, ignoring summoning_sick."
        )

    def test_non_sick_untapped_creature_is_a_valid_attacker(self, card_db):
        """Regression companion: a non-sick, untapped creature SHOULD be a
        valid attacker — this guards against the fix over-restricting."""
        game = _fresh_game()
        _put_creature_on_battlefield(
            game, card_db, "Guide of Souls",
            controller=0, summoning_sick=False, tapped=False,
        )

        board = extract_virtual_board(game, player_idx=0)
        assert len(board.my_creatures) == 1

        planner = CombatPlanner()
        attackers, _delta = planner.plan_attack(board)

        # With no opposing blockers and no-sick creature, the planner
        # should attack (opp at 20 life, 1 power can't be lethal, but the
        # free-damage branch still selects it as an attacker).
        my_id = board.my_creatures[0].instance_id
        assert any(a.instance_id == my_id for a in attackers), (
            "Non-sick, untapped creature must still be selectable as an "
            "attacker after the LE-T1 fix."
        )
