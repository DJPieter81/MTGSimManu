"""The CombatPlanner attack path keeps home a creature whose non-combat
worth exceeds the damage it adds — the same rule the on-board-lethal path
and the send-everything fallback already apply.

Observed (Domain Zoo vs Creatures Toolbox s50000 G2, T4): Devoted Druid +
Vizier of Remedies assembled (the engine credited the loop, 80 mana);
`decide_attackers` took the planner's plan unfiltered and sent Vizier
alone into an untapped 4/4; it died in the block and the infinite mana was
gone. See docs/diagnostics/2026-09-12_zoo_lane_loop_break.md.

Rules pinned (card names are fixture carriers):

1. A creature in the planner's plan whose `noncombat_opportunity_cost`
   exceeds its power stays home when the plan is not lethal; a vanilla
   body in the same plan attacks.
2. When the plan IS lethal it is sent as planned — the lethal path's own
   exception.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


class _Plan:
    """A stand-in plan: the planner's return shape is (list of virtual
    creatures with `instance_id`, score_delta)."""

    def __init__(self, instance_id):
        self.instance_id = instance_id
        self.has_combat_damage_player_trigger = False


def _board(card_db, opp_life):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.DECLARE_ATTACKERS
    game.turn_number = 5
    game.players[0].deck_name = "Creatures Toolbox"
    game.players[1].deck_name = "Domain Zoo"
    game.players[1].life = opp_life
    for _ in range(4):
        _add(game, card_db, "Forest", 0, "battlefield")
    _add(game, card_db, "Devoted Druid", 0, "battlefield")
    vizier = _add(game, card_db, "Vizier of Remedies", 0, "battlefield")   # 2/1, engine enabler
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")         # 2/2 vanilla
    _add(game, card_db, "Hill Giant", 1, "battlefield")                     # 3/3 untapped blocker
    for _ in range(3):
        _add(game, card_db, "Mountain", 1, "battlefield")
    return game, vizier, bears


def _ai_with_plan(picks):
    from ai.ev_player import EVPlayer
    ai = EVPlayer(player_idx=0, deck_name="Creatures Toolbox", rng=random.Random(0))

    class _Planner:
        def plan_attack(self, vboard):
            return [_Plan(c.instance_id) for c in picks], 999.0   # far above any threshold

    ai.combat_planner = _Planner()
    return ai


def test_an_engine_piece_in_a_non_lethal_plan_stays_home_while_a_vanilla_body_attacks(card_db):
    from ai.clock import noncombat_opportunity_cost
    from ai.ev_evaluator import snapshot_from_game
    game, vizier, bears = _board(card_db, opp_life=20)
    snap = snapshot_from_game(game, 0)
    assert noncombat_opportunity_cost(vizier, game.players[0], snap) > (vizier.power or 0), (
        "fixture: the engine enabler must be worth more than its two power")
    ai = _ai_with_plan([vizier, bears])
    ai._init_deck_knowledge(game)
    attackers = ai.decide_attackers(game)
    assert bears in attackers, "the vanilla body in the plan attacks"
    assert vizier not in attackers, (
        "the engine enabler was sent into a losing block although its "
        "non-combat worth exceeds the two damage it adds")


def test_a_hand_only_ability_is_not_battlefield_worth(card_db):
    """Cycling's reminder text has the "[cost]: [effect]" shape, but
    cycling is activated from hand (CR 702.29); a cycling creature on
    the battlefield has no ability worth keeping it home for, while a
    creature with a real battlefield ability does."""
    from ai.clock import _has_activated_ability
    game = GameState(rng=random.Random(0))
    wraith = _add(game, card_db, "Street Wraith", 0, "battlefield")
    druid = _add(game, card_db, "Devoted Druid", 0, "battlefield")
    assert not _has_activated_ability(wraith), "cycling counted as a battlefield ability"
    assert _has_activated_ability(druid)


def test_a_lethal_plan_is_sent_as_planned(card_db):
    game, vizier, bears = _board(card_db, opp_life=4)      # 2 + 2 = lethal if unblocked
    game.players[1].battlefield = [c for c in game.players[1].battlefield
                                   if not c.template.is_creature]
    ai = _ai_with_plan([vizier, bears])
    ai._init_deck_knowledge(game)
    attackers = ai.decide_attackers(game)
    assert vizier in attackers and bears in attackers, (
        "with lethal on board every needed body is sent, engine pieces included")
