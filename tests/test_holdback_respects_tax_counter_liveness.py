"""Holdback must not reserve mana for a tax counter the opponent pays through.

`_holdback_penalty` prices the option value of held instant-speed interaction
and charges it against every tap-out play. It priced a held "counter unless
its controller pays {N}" counter identically to a hard counter — but such a
counter stops a spell ONLY when casting it leaves the opponent unable to pay
the tax (1a framework; the payer side is `project_counter_tax_payment`, the
fire-time side is the response gate's payability skip). Against a deck whose
castable pool is cheap relative to its lands, the held tax counter stops
almost nothing, so reserving mana for it — and vetoing a goal-prioritized
finisher to do so — buys nothing.

Live bug this pins (docs/diagnostics/2026-08-26_decider_loss_root_cause.md,
secondary root cause): seed 54500 game 3, control at 3 life with six lands
held a 5-CMC payoff its own goal listed at priority 24 and cast a draw spell
instead, because holdback charged -12 for stranding a Mystical Dispute that
the opponent — four lands, a pool of 1-2 effective-cost threats — would
always pay through. Control died to 1-power attackers with the finisher in
hand.

Rule under test: the holdback charge for a held tax counter scales with the
fraction of the opponent's castable pool it can actually stop. Card names
are fixture carriers; the mechanic is `counter_tax_amount` vs the opponent's
next-turn capacity and effective spell costs.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game

_DB = CardDatabase()


def _add(game, name, controller, zone, tapped=False):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = tapped
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _decider_state(held_counter, opp_pool):
    """The reconstructed decider turn: reactive deck, six untapped lands,
    3 life, one small attacker across the table, a payoff + a draw spell +
    the held counter in hand."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 8
    me, opp = game.players[0], game.players[1]
    me.deck_name = "4/5c Control"
    opp.deck_name = "Domain Zoo"
    me.life = 3
    opp.life = 20
    for n in ["Hallowed Fountain", "Breeding Pool", "Steam Vents",
              "Temple Garden", "Sacred Foundry", "Stomping Ground"]:
        _add(game, n, 0, "battlefield")
    payoff = _add(game, "Quantum Riddler", 0, "hand")
    _add(game, "Stock Up", 0, "hand")
    counter = _add(game, held_counter, 0, "hand")
    for _ in range(10):
        _add(game, "Island", 0, "library")
    _add(game, "Doorkeeper Thrull", 1, "battlefield")
    for _ in range(4):
        _add(game, "Mountain", 1, "battlefield")
    for n in opp_pool:
        _add(game, n, 1, "library")
    return game, payoff, counter


_CHEAP_POOL = ["Wild Nacatl", "Ragavan, Nimble Pilferer",
               "Doorkeeper Thrull", "Wild Nacatl"]


def _penalty(game, ai, cost, exclude=None):
    me, opp = game.players[0], game.players[1]
    snap = snapshot_from_game(game, 0)
    return ai._holdback_penalty(me, opp, snap, cost,
                                exclude_instance_id=exclude, game=game)


def test_payable_tax_counter_carries_less_holdback_than_a_hard_counter():
    """Same state, same tap-out: a held tax counter the opponent's cheap
    pool pays through must charge strictly less holdback than a held hard
    counter (which stops those spells unconditionally)."""
    g_tax, payoff_t, _ = _decider_state("Mystical Dispute", _CHEAP_POOL)
    g_hard, payoff_h, _ = _decider_state("Counterspell", _CHEAP_POOL)
    ai_t = EVPlayer(player_idx=0, deck_name="4/5c Control",
                    rng=random.Random(0))
    ai_h = EVPlayer(player_idx=0, deck_name="4/5c Control",
                    rng=random.Random(0))
    ai_t._init_deck_knowledge(g_tax)
    ai_h._init_deck_knowledge(g_hard)
    p_tax = _penalty(g_tax, ai_t, cost=5, exclude=payoff_t.instance_id)
    p_hard = _penalty(g_hard, ai_h, cost=5, exclude=payoff_h.instance_id)
    assert p_tax > p_hard, (
        f"holdback for a payable-through tax counter ({p_tax:+.2f}) must be "
        f"strictly smaller in magnitude than for a hard counter "
        f"({p_hard:+.2f}) — the tax counter stops none of the opponent's "
        f"castable pool")


def test_finisher_deploys_over_draw_when_the_held_counter_is_payable():
    """The behavioural pin of the decider bug: with the held tax counter
    dead against the opponent's pool, the goal-prioritized payoff must be
    the chosen main-phase play — not the draw spell."""
    game, payoff, _ = _decider_state("Mystical Dispute", _CHEAP_POOL)
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control",
                  rng=random.Random(0))
    decision = ai.decide_main_phase(game)
    assert decision is not None
    action, card, _t = decision
    assert (action, card.instance_id) == ("cast_spell", payoff.instance_id), (
        f"expected the payoff cast, got {action} {card.name} — a reactive "
        f"deck at 3 life must not durdle behind a dead tax counter")


def test_hard_counter_holdback_is_unchanged_by_the_liveness_weighting():
    """Control case: a hard counter (tax 0) prices exactly as before —
    the liveness weighting applies only to tax counters."""
    g, payoff, counter = _decider_state("Counterspell", _CHEAP_POOL)
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control",
                  rng=random.Random(0))
    ai._init_deck_knowledge(g)
    me, opp = g.players[0], g.players[1]
    snap = snapshot_from_game(g, 0)
    with_game = ai._holdback_penalty(me, opp, snap, 5,
                                     exclude_instance_id=payoff.instance_id,
                                     game=g)
    without_game = ai._holdback_penalty(me, opp, snap, 5,
                                        exclude_instance_id=payoff.instance_id)
    assert with_game == without_game, (
        "a hard counter's holdback must not depend on the liveness pass")
