"""Saga chapter-I value is projected in spell EV.

A Saga's Chapter I fires the moment it enters. The spell-EV projection
(`_project_spell`) ran its token/ETB value credit ONLY inside `if
t.is_creature:`, so a Saga (an enchantment) got no chapter value: Fable of
the Mirror-Breaker's Chapter I makes a 2/2 Goblin token, but the projection
scored the cast at ~0 (even slightly negative) — so the AI never cast it.
Affected: Fable (Jeskai Blink, Izzet Prowess) and Urza's Saga (Affinity,
Pinnacle Affinity).

Rule under test: a Saga whose Chapter I creates a creature token projects a
materially positive board delta in `_project_spell` (the token's value),
comparable to a creature that makes a similar token. Detected by the `Saga`
subtype — no card names in the scorer.
"""
from __future__ import annotations

import random

from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from ai.ev_evaluator import snapshot_from_game, _project_spell, evaluate_board


def _board_delta(card_db, name):
    g = GameState(rng=random.Random(0))
    g.active_player = 0
    g.current_phase = Phase.MAIN2
    g.turn_number = 3
    p = g.players[0]
    for _ in range(4):
        t = card_db.get_card("Mountain")
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=g.next_instance_id(), zone="battlefield")
        c._game_state = g
        c.enter_battlefield()
        p.battlefield.append(c)
    t = card_db.get_card(name)
    card = CardInstance(template=t, owner=0, controller=0,
                        instance_id=g.next_instance_id(), zone="hand")
    card._game_state = g
    p.hand.append(card)
    snap = snapshot_from_game(g, 0)
    cur = evaluate_board(snap, "midrange")
    proj = _project_spell(card, snap, None, g, 0)
    return evaluate_board(proj, "midrange") - cur


def test_fable_saga_chapter_token_is_projected(card_db):
    delta = _board_delta(
        card_db, "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki")
    assert delta > 1.0, (
        f"Fable's Chapter I makes a 2/2 token; _project_spell should credit a "
        f"materially positive board delta, got {delta:+.2f}")


def test_saga_projection_counts_the_chapter_token(card_db):
    """Structural: the Saga projection adds the Chapter-I creature token to
    the projected board (my_creature_count and my_power both rise)."""
    g = GameState(rng=random.Random(0))
    g.active_player = 0
    g.current_phase = Phase.MAIN2
    g.turn_number = 3
    p = g.players[0]
    for _ in range(4):
        t = card_db.get_card("Mountain")
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=g.next_instance_id(), zone="battlefield")
        c._game_state = g
        c.enter_battlefield()
        p.battlefield.append(c)
    t = card_db.get_card("Fable of the Mirror-Breaker // Reflection of Kiki-Jiki")
    card = CardInstance(template=t, owner=0, controller=0,
                        instance_id=g.next_instance_id(), zone="hand")
    card._game_state = g
    p.hand.append(card)
    snap = snapshot_from_game(g, 0)
    proj = _project_spell(card, snap, None, g, 0)
    assert proj.my_creature_count > snap.my_creature_count, (
        "Fable's Chapter-I 2/2 token must be counted in the projection")
    assert proj.my_power >= snap.my_power + 2, (
        "Fable's 2/2 token adds >=2 projected power")
