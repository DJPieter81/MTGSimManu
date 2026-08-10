"""Phase 2c (docs/design/rules-foundation-sweep-tracker.md) — turn_planner's
opponent-block PREDICTION and ev_player's own-block DECISION must resolve
to the SAME underlying joint-assignment algorithm.

Rule pinned (structural, not card-specific): given the same board state
(same attacker, same blocker, same life totals), ``ai.ev_player.
EVPlayer.decide_blockers`` (a real block DECISION) and
``ai.turn_planner.CombatPlanner._predict_blocks`` (a PREDICTION of the
same decision, made from the opposite side of the table during turn
planning) must produce the identical attacker -> blocker assignment.

Before Phase 2c these were two independently-maintained algorithms and
they genuinely disagreed on this exact fixture (verified via ``git
stash`` before writing the fix): a defender at low-but-not-literally-
lethal life facing an UNKILLABLE attacker (high toughness, no blocker
present can trade with it) with one available chump blocker.

  - ``decide_blockers``'s emergency detection is three-way (literal
    lethal-this-turn OR low-life-with-incoming-floor OR two-turn-
    lethal) — the low-life branch fires here even though the hit
    isn't literally lethal THIS turn, and Phase 2b's coverage pass
    force-chumps with the cheapest blocker regardless of whether it
    can kill anything (that's the whole point of the ``opportunity_
    cost``-ranked coverage pass — a "pure waste" 0-value chump is
    still worth spending to stay alive).
  - the OLD (pre-Phase-2c) ``_predict_blocks`` only checked BARE
    lethal-this-turn for its "must-block" phase (``total_incoming >=
    board.opp_life``), which does not fire here, and its trade-up/
    trade-even phases both require the candidate blocker to be able
    to KILL the attacker (``blocker.power >= attacker.toughness``) —
    which a 1-power chump against a 10-toughness attacker cannot. The
    old prediction was therefore an empty ``{}`` — "opponent takes the
    hit" — while the real AI, asked to actually make that decision,
    chumps every time.

Card names are fixture carriers only — the rule under test is
"opponent-block prediction and self-block decision agree," not
anything about Watchwolf or Memnite specifically.
"""
from __future__ import annotations

import random

from ai.ev_player import EVPlayer
from ai.turn_planner import CombatPlanner, extract_virtual_board
from engine.cards import CardInstance
from engine.game_state import GameState


def _battlefield(game, db, name: str, controller: int) -> CardInstance:
    tmpl = db.get_card(name)
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


def _build_low_life_unkillable_attacker_shape(card_db):
    """Player 0 (defender) at life=4 facing one 3/10 attacker (power
    low enough that this hit alone isn't literally lethal, toughness
    high enough that nothing on the board can kill it) from player 1,
    with one 1/1 chump blocker. Low-life-emergency for the defender
    (life 4 - incoming 3 = 1, within the low-life-with-incoming-floor
    band); no possible trade-up/trade-even (blocker can't kill the
    attacker) — isolates the "chump anyway to buy a turn" decision
    from any kill-driven heuristic.
    """
    game = GameState(rng=random.Random(0))
    game.players[0].life = 4
    game.players[1].life = 20

    chump = _battlefield(game, card_db, "Memnite", 0)
    attacker = _battlefield(game, card_db, "Watchwolf", 1)
    attacker.temp_power_mod = 3 - (attacker.template.power or 0)
    attacker.temp_toughness_mod = 10 - (attacker.template.toughness or 0)
    assert attacker.power == 3 and attacker.toughness == 10, (
        "fixture setup: attacker must present as an unkillable-by-the-"
        "lone-chump 3/10"
    )
    assert chump.power == 1 and chump.toughness == 1, (
        "fixture setup: the lone blocker must be a 1/1 that cannot "
        "trade with a 10-toughness attacker"
    )
    return game, attacker, chump


def test_prediction_matches_decision_on_low_life_chump_shape(card_db):
    """Same fixture, two independent code paths, one algorithm."""
    game, attacker, chump = _build_low_life_unkillable_attacker_shape(card_db)

    # ── The real DECISION, from the defender's (player 0) own AI ──
    defender = EVPlayer(player_idx=0, deck_name="Boros Energy",
                         rng=random.Random(0))
    decided = defender.decide_blockers(game, [attacker])

    # ── The PREDICTION, from the attacker's (player 1) turn-planning
    # perspective — exactly how the real call site captures a board
    # (extract_virtual_board(game, self.player_idx) with
    # self.player_idx == the attacking pilot; see
    # ai.ev_player.EVPlayer's combat_planner call site). ──
    vboard = extract_virtual_board(game, player_idx=1)
    v_attackers = [c for c in vboard.my_creatures
                   if c.instance_id == attacker.instance_id]
    v_blockers = [c for c in vboard.opp_creatures
                  if c.instance_id == chump.instance_id]
    assert len(v_attackers) == 1 and len(v_blockers) == 1, (
        "fixture setup: virtual board must carry the attacker as "
        "'my_creatures' (attacking pilot's own side) and the chump "
        "as 'opp_creatures' (the defender being predicted)"
    )

    predicted = CombatPlanner()._predict_blocks(v_attackers, v_blockers, vboard)

    # ── Structural equality: prediction and decision must agree that
    # the defender chumps, with the SAME blocker. ──
    assert decided.keys() == predicted.keys(), (
        f"decision and prediction disagree on WHETHER the defender "
        f"chumps here. decided={decided}, predicted={predicted}."
    )
    assert decided == predicted, (
        f"decision and prediction assigned different blockers. "
        f"decided={decided}, predicted={predicted}."
    )
    assert attacker.instance_id in decided, (
        f"sanity: the real decision must chump-block here — the "
        f"defender is at low-but-not-lethal life and the block, "
        f"though it can't kill anything, buys a turn. Got {decided}."
    )
