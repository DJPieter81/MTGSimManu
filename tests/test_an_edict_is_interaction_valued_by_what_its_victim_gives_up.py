"""An edict is interaction this turn, worth what its victim gives up (AI,
from CR 701.21a).

"Each opponent sacrifices a creature of their choice", "target player
sacrifices a creature": the named player chooses what to sacrifice (CR
701.21a). The AI models that choice as it makes its own sacrifices: the
player gives up the permanent whose loss costs them least
(`ai.permanent_threat.permanent_threat`, planeswalker loyalty included).
An edict is worth to its caster what that permanent is worth to its owner,
in position-value units, the scale a targeted removal's threat premium
uses. It removes that permanent as it resolves, so it is interaction this
turn: the deferral gate does not hold it.

One owner, `ai.edict_value`, read by the deferral gate
(`ev_evaluator._is_immediate_interaction`) and by the spell's score
(`EVPlayer._score_spell`). A modal edict is valued by the modes its caster
chooses (`ai.modal.select_modal_modes`).

The defect (docs/diagnostics/2026-10-14_dimir_midrange_root_cause.md,
subsystem 1): the deferral gate read no SACRIFICE instruction, so every
edict got no this-turn signal and was scored at the deferral's exposure
cost forever. 59 of the pool's 71 edict instants and sorceries were held.
Dimir Midrange held Sheoldred's Edict from T2 to T7 while Ajani's walker
climbed, then cast it facing lethal.

Card names are fixture carriers: each is one member of its class.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, lands=4):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for p in (0, 1):
        for _ in range(lands):
            _add(game, card_db, "Swamp", p)
        for _ in range(8):
            _add(game, card_db, "Island", p, "library")
    return game


def _threat(game, perm):
    from ai.permanent_threat import permanent_threat
    return permanent_threat(perm, game.players[perm.controller], game)


EDICTS = ["Sheoldred's Edict", "Diabolic Edict", "Cruel Edict",
          "Liliana's Triumph", "Barter in Blood"]


@pytest.mark.parametrize("name", EDICTS)
def test_an_edict_is_interaction_this_turn(card_db, name):
    from ai.ev_evaluator import _is_immediate_interaction
    t = card_db.get_card(name)
    assert _is_immediate_interaction((t.oracle_text or "").lower(), t.tags, t)


def test_a_creature_spell_is_no_edict(card_db):
    from ai.edict_value import is_edict
    assert not is_edict(card_db.get_card("Grizzly Bears"))
    assert not is_edict(card_db.get_card("Lightning Bolt"))


def test_the_victim_is_the_permanent_its_player_loses_least_by(card_db):
    """Diabolic Edict against a 2/2 and a 6/4: the opponent sacrifices the
    2/2, and the edict is worth what the 2/2 is worth to them."""
    from ai.edict_value import edict_value
    game = _game(card_db)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    _add(game, card_db, "Craw Wurm", 1)
    edict = _add(game, card_db, "Diabolic Edict", 0, "hand")
    assert edict_value(game, 0, edict) == pytest.approx(_threat(game, bears))


def test_an_edict_with_no_victim_is_worth_nothing(card_db):
    from ai.edict_value import edict_value
    game = _game(card_db)
    _add(game, card_db, "Grizzly Bears", 0)          # only the caster's
    edict = _add(game, card_db, "Liliana's Triumph", 0, "hand")
    assert edict_value(game, 0, edict) == 0.0


def test_an_edict_every_player_performs_charges_its_caster_too(card_db):
    """Barter in Blood: each player sacrifices two creatures."""
    from ai.edict_value import edict_value
    game = _game(card_db)
    mine = [_add(game, card_db, n, 0) for n in ("Grizzly Bears", "Memnite")]
    theirs = [_add(game, card_db, n, 1)
              for n in ("Grizzly Bears", "Serra Angel", "Craw Wurm")]
    edict = _add(game, card_db, "Barter in Blood", 0, "hand")
    two_least = sorted(_threat(game, c) for c in theirs)[:2]
    assert edict_value(game, 0, edict) == pytest.approx(
        sum(two_least) - sum(_threat(game, c) for c in mine))


def test_a_modal_edict_is_valued_by_the_modes_its_caster_chooses(card_db):
    """Sheoldred's Edict against a planeswalker and a creature token: the
    planeswalker mode is chosen, and the walker is what is given up."""
    from ai.edict_value import edict_value
    from ai.modal import select_modal_modes
    game = _game(card_db)
    walker = _add(game, card_db, "Liliana of the Veil", 1)
    walker.loyalty_counters = 5
    edict = _add(game, card_db, "Sheoldred's Edict", 0, "hand")
    chosen = select_modal_modes(game, edict, 0)
    assert chosen == [2]                      # "each opponent sacrifices a planeswalker"
    assert edict_value(game, 0, edict) == pytest.approx(_threat(game, walker))


def test_the_replay_board_casts_the_edict(card_db):
    """The Dimir root cause, G1 T6: Ajani's walker at 7 loyalty and four
    creatures facing Dimir at 11 life. The edict scores above passing."""
    from ai.ev_evaluator import snapshot_from_game
    from ai.ev_player import EVPlayer
    game = _game(card_db, lands=5)
    ajani = _add(game, card_db, "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger", 1)
    ajani.is_transformed = True
    ajani.loyalty_counters = 7
    for n in ("Voice of Victory", "Seasoned Pyromancer", "Voice of Victory",
              "Guide of Souls"):
        _add(game, card_db, n, 1)
    game.players[0].life = 11
    edict = _add(game, card_db, "Sheoldred's Edict", 0, "hand")
    player = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    score = player._score_spell(edict, snap, game, game.players[0],
                                game.players[1])
    assert score > 0
