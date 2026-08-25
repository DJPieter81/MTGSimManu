"""Held reactive removal deploys against a fast life-relative clock.

Root cause (post-sweep control-execution audit, seed 50000): the reactive-only
enumeration gate in `EVPlayer.decide_main_phase` required
`opp_power >= profile.dying_opp_power` AND `opp_clock_discrete <=
dying_opp_clock` before a held non-creature reactive spell (spot removal, board
wipe) was even ENUMERATED as a candidate play. The raw-power conjunct meant a
board of small attackers never qualified no matter how short the clock, so a
control deck bled out from 20 to 0 holding its removal, deploying it only once a
single attacker crossed the power floor — several turns and ~10 life too late.
Death by a thousand cuts was invisible to the gate.

`opp_clock_discrete` (= ceil(my_life / opp_power)) is ALREADY the life-relative
quantity that answers "how many turns until this board kills me", and it already
returns a no-clock sentinel when the opponent has no power. Size is therefore
redundant: a short clock is the correct, attacker-size-agnostic trigger.

Rule under test: when the opponent's board kills us within the profile's dying
clock horizon, held reactive removal is enumerated — even though no single
attacker meets the big-creature power floor. Mechanic-driven (life-relative
clock), no card names in the assertions.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game

_DB = CardDatabase()


def _add(game, name, controller, zone):
    t = _DB.get_card(name)
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


def _candidate_names(ai):
    """Names of the cards the main-phase scorer actually enumerated."""
    names = set()
    for play in getattr(ai, "_last_candidates", []) or []:
        card = getattr(play, "card", None)
        nm = getattr(card, "name", None)
        if nm:
            names.add(nm)
    return names


def _control_at_life(life, small_attackers):
    """Control deck holding reactive removal, facing `small_attackers`."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    me, opp = game.players[0], game.players[1]
    me.deck_name = "4/5c Control"
    opp.deck_name = "Domain Zoo"
    me.life = life
    opp.life = 20
    # Enough untapped mana to actually cast the removal.
    for _ in range(4):
        _add(game, "Hallowed Fountain", 0, "battlefield")
    for nm in small_attackers:
        _add(game, nm, 1, "battlefield")
    removal = _add(game, "Prismatic Ending", 0, "hand")
    return game, removal


def test_reactive_removal_enumerated_on_fast_clock_without_a_big_attacker():
    # A lone 2/1 at 6 life: opp_power=2 is BELOW the dying_opp_power floor (3),
    # but the clock is ceil(6/2)=3 — lethal in three turns. Removal must be
    # considered.
    game, removal = _control_at_life(6, ["Ragavan, Nimble Pilferer"])
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control", rng=random.Random(0))

    snap = snapshot_from_game(game, 0)
    prof = ai.profile
    # Pin the fixture's premise: this is exactly the case the old gate missed.
    assert snap.opp_power < prof.dying_opp_power, (
        "fixture premise: no single attacker meets the power floor")
    assert snap.opp_clock_discrete <= prof.dying_opp_clock, (
        "fixture premise: the life-relative clock IS short")
    assert not snap.am_dead_next, (
        "fixture premise: not already dead next turn (that path always fired)")
    assert removal.name in ai._reactive_only, (
        "fixture premise: the spell is gated as reactive-only for this deck")

    ai.decide_main_phase(game)
    considered = _candidate_names(ai)
    hit = removal.name in considered
    assert hit, (
        f"held reactive removal must be enumerated when the life-relative "
        f"clock is short (clock={snap.opp_clock_discrete} <= "
        f"{prof.dying_opp_clock}) even though no single attacker meets the "
        f"power floor (opp_power={snap.opp_power} < {prof.dying_opp_power}); "
        f"candidates were {considered}")


def test_reactive_removal_still_held_when_clock_is_long():
    # Regression: at high life the same lone 2/1 gives a long clock
    # (ceil(20/2)=10) — control should still HOLD, not fire on every small
    # creature. The fix must not turn patience off wholesale.
    game, removal = _control_at_life(20, ["Ragavan, Nimble Pilferer"])
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control", rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    assert snap.opp_clock_discrete > ai.profile.dying_opp_clock, (
        "fixture premise: clock is long at high life")

    ai.decide_main_phase(game)
    assert removal.name not in _candidate_names(ai), (
        "with a long clock and no big threat, control should still hold its "
        "reactive removal — the fix must not retire patience entirely")
