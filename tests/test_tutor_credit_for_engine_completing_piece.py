"""The AI's delivered-value credit for a creature tutor is what the tutor
actually delivers. When the delivered creature completes an unbounded mana
engine with the board (CR 726.4 shortcut material), what it delivers is the
engine's mana — `LOOP_SHORTCUT_MANA` — not its printed mana value, so a
two-mana engine piece is worth more than a bigger vanilla body.

Pinned for the activated X-tutor path (`ai/activation_ev.activation_candidates`)
and the cast X-tutor gate (`EVPlayer._gate_x_tutor_payoff`) — both consult the
same engine-side picker. Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.activation_ev import activation_candidates
from ai.ev_evaluator import snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _bf(game, card_db, name, controller=0):
    t = card_db.get_card(name)
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _lib(game, card_db, names):
    for i, n in enumerate(names):
        c = CardInstance(template=card_db.get_card(n), owner=0, controller=0,
                         instance_id=500 + i, zone="library")
        c._game_state = game
        game.players[0].library.append(c)


def _artisan_candidates(game):
    game.current_phase = Phase.MAIN1  # activated tutors are main-phase plays
    snap = snapshot_from_game(game, 0)
    return [(reason, ev) for _p, _i, _t, ev, reason
            in activation_candidates(game, 0, snap)
            if reason.startswith("activate: tutor")]


def test_activated_tutor_values_the_engine_piece_above_a_bigger_body(card_db):
    game = GameState(rng=random.Random(0))
    for _ in range(4):
        _bf(game, card_db, "Forest")
    _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Fiend Artisan")
    _lib(game, card_db, ["Vizier of Remedies", "Eternal Witness"])
    engine = _artisan_candidates(game)
    assert engine and "Vizier of Remedies" in engine[0][0], engine

    game2 = GameState(rng=random.Random(0))
    for _ in range(4):
        _bf(game2, card_db, "Forest")
    _bf(game2, card_db, "Devoted Druid")
    _bf(game2, card_db, "Fiend Artisan")
    _lib(game2, card_db, ["Eternal Witness"])
    vanilla = _artisan_candidates(game2)
    assert vanilla, "a vanilla delivery is still a candidate"
    assert engine[0][1] > vanilla[0][1], (engine, vanilla)


# ── The AI callbacks that actually decide delivery and sacrifice ──

def test_tutor_delivery_callback_prefers_the_engine_completing_piece(card_db):
    """`choose_tutor_delivery` is the callback the engine consults at
    resolution; it must agree with the delivery-conditioned valuation, or
    X is priced for the engine piece and a bigger body is delivered."""
    from ai.activation_ev import choose_tutor_delivery
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Devoted Druid")
    _lib(game, card_db, ["Vizier of Remedies", "Eternal Witness",
                         "Duskwatch Recruiter // Krallenhorde Howler"])
    pick = choose_tutor_delivery(game, 0, list(game.players[0].library))
    assert pick is not None and pick.name == "Vizier of Remedies"


def test_sacrifice_victim_is_not_a_mana_source_when_a_vanilla_body_will_do(card_db):
    """A sacrifice cost's victim minimises what the board gives up; a
    creature's mana production is part of what it gives up."""
    from ai.activation_ev import choose_sacrifice_victim
    game = GameState(rng=random.Random(0))
    druid = _bf(game, card_db, "Devoted Druid")      # 0/2, taps for G
    wall = _bf(game, card_db, "Ornithopter")         # 0/2, no mana
    pick = choose_sacrifice_victim(game, 0, [druid, wall])
    assert pick is wall


def test_sacrifice_victim_never_breaks_an_unbounded_engine(card_db):
    from ai.activation_ev import choose_sacrifice_victim
    game = GameState(rng=random.Random(0))
    druid = _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    bear = _bf(game, card_db, "Grizzly Bears")       # 2/2 — a bigger body
    pick = choose_sacrifice_victim(game, 0, [druid, bear])
    assert pick is bear
