"""A creature that produces mana is a blocker first and a mana source
second: it is tapped for a spell only when no non-creature source can
pay, and a play that must tap a potential blocker while the opponent has
on-board lethal is not made.

Observed (Domain Zoo vs Creatures Toolbox s50000 G1): Toolbox at 4 life
into a 5/6 Psychic Frog cast Leyline of Abundance and paid with BOTH Dryad
Arbors while two untapped duals could have paid the green — the payment
solver's scarcity ordering taps a mono-colour creature-land before a dual —
and took lethal on the next attack with no untapped creature. Nothing
priced the tapped blockers: the holdback penalty prices open mana for
instants, not open bodies for blocks.

Rules pinned (card names are fixture carriers):

1. Payment ordering: among sources that can pay a pip (or a generic), a
   non-creature source is tapped before a creature source.
2. While the opponent has on-board lethal, a play whose cost exceeds the
   caster's non-creature mana capacity — one that must tap a potential
   blocker — is clamped into the patience-reject band; the same play with
   enough non-creature mana, or with no lethal on board, is not.

Class: every mana creature and creature-land × every payment; every deck
facing lethal.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from ai.scoring_constants import PATIENCE_GATE_REJECT_SENTINEL


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


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Creatures Toolbox"
    game.players[1].deck_name = "Domain Zoo"
    return game


# ─── 1. Payment ordering ─────────────────────────────────────────────


def test_a_non_creature_source_is_tapped_before_a_creature_source(card_db):
    game = _game()
    arbor = _add(game, card_db, "Dryad Arbor", 0, "battlefield")     # first in order
    f1 = _add(game, card_db, "Forest", 0, "battlefield")
    f2 = _add(game, card_db, "Forest", 0, "battlefield")
    bears = _add(game, card_db, "Grizzly Bears", 0, "hand")           # {1}{G}
    assert CastManager.cast_spell(game, 0, bears, [])
    assert not arbor.tapped, "the creature-land was tapped while two Forests could pay"
    assert f1.tapped and f2.tapped


# ─── 2. No blocker is tapped into on-board lethal ────────────────────


def _score(game, spell):
    ai = EVPlayer(player_idx=0, deck_name="Creatures Toolbox", rng=random.Random(0))
    ai._init_deck_knowledge(game)
    if ai.goal_engine:
        ai.goal_engine.check_transition(game, 0)
    snap = snapshot_from_game(game, 0)
    return ai._score_spell(spell, snap, game, game.players[0], game.players[1])


def _facing_lethal(card_db, non_creature_lands, arbors, lethal=True):
    game = _game()
    game.players[0].life = 4
    for _ in range(non_creature_lands):
        _add(game, card_db, "Forest", 0, "battlefield")
    for _ in range(arbors):
        _add(game, card_db, "Dryad Arbor", 0, "battlefield")
    if lethal:
        frog = _add(game, card_db, "Psychic Frog", 1, "battlefield")
        frog.temp_power_mod += 4          # 5/6 — lethal on board at 4 life
        frog.temp_toughness_mod += 4
    for _ in range(3):
        _add(game, card_db, "Mountain", 1, "battlefield")
    spell = _add(game, card_db, "Leyline of Abundance", 0, "hand")   # {2}{G}{G}
    return game, spell


def test_a_play_that_must_tap_a_potential_blocker_into_lethal_is_clamped(card_db):
    game, spell = _facing_lethal(card_db, non_creature_lands=2, arbors=2)
    ev = _score(game, spell)
    assert ev <= PATIENCE_GATE_REJECT_SENTINEL, (
        f"scored {ev:.2f}: paying this needs both creature-lands, the only "
        f"bodies that could chump the lethal attacker")


def test_the_same_play_with_enough_non_creature_mana_is_not_clamped(card_db):
    game, spell = _facing_lethal(card_db, non_creature_lands=4, arbors=2)
    ev = _score(game, spell)
    assert ev > PATIENCE_GATE_REJECT_SENTINEL, (
        f"scored {ev:.2f} although four non-creature lands pay it and both "
        f"blockers stay home")


def test_the_same_play_with_no_lethal_on_board_is_not_clamped(card_db):
    game, spell = _facing_lethal(card_db, non_creature_lands=2, arbors=2, lethal=False)
    ev = _score(game, spell)
    assert ev > PATIENCE_GATE_REJECT_SENTINEL, (
        f"scored {ev:.2f} with no lethal on board — the blockers are not needed")
