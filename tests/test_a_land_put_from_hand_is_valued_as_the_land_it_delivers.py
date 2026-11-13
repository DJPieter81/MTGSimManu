"""A land put from hand is valued as the land it delivers, wherever the
instruction sits (AI, from CR 305.4).

"You may put a land card from your hand onto the battlefield" can be a
spell's instruction (Growth Spiral), a permanent's enter trigger (Arboreal
Grazer, Spelunking) or an activation (Sakura-Tribe Scout). It puts a land
without spending a land drop (CR 305.4).

The AI's one owner, `ai.land_put`, says how many lands such an instruction
puts:
- one per instruction the engine performs (the hosts its carriers take);
- no more than the land cards the hand holds.

A land put is the land card's use, as its land drop would be, so no card is
charged for it:
- the cast projection credits one more land and its mana;
- the activation scorer prices that mana at the per-mana clock rate its
  untap and tutor branches use.

The defects:
- The activation scorer valued an UNCLASSIFIED card-flow activation only as
  an impulse draw, so Sakura-Tribe Scout (Amulet Titan x2) was never
  activated.
- The cast projection read a text tag gated on creatures. An enchantment's
  enter trigger or a spell went uncredited.
- A creature's trigger that no executor performs (Cultivator Colossus's
  loop) was credited.
- The land card was charged as a lost card, so Arboreal Grazer projected
  below a vanilla creature of its cost.

Card names are fixture carriers: 8 pool activations and 30 pool cards put a
land card from a hand onto the battlefield.
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


def _game(card_db, hand_lands=("Forest",)):
    """Player 0 has made this turn's land drop, holds `hand_lands`, and
    has three Forests and an untapped Sakura-Tribe Scout ("{T}: You may
    put a land card from your hand onto the battlefield.")."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(3):
        _add(game, card_db, "Forest", 0)
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    for name in hand_lands:
        _add(game, card_db, name, 0, "hand")
    game.players[0].lands_played_this_turn = 1
    scout = _add(game, card_db, "Sakura-Tribe Scout", 0)
    return game, scout


def _scout_host(scout):
    ability = scout.template.activated_abilities[0]
    return scout.template.effects.activated(ability.index)


# ── The count (one owner) ─────────────────────────────────────────────

def test_an_instruction_puts_one_land_out_of_the_lands_in_hand(card_db):
    from ai.land_put import lands_put
    game, scout = _game(card_db, hand_lands=("Forest", "Forest"))
    assert lands_put(game, 0, (_scout_host(scout),)) == 1


def test_nothing_is_put_from_a_hand_holding_no_land(card_db):
    from ai.land_put import lands_put
    game, scout = _game(card_db, hand_lands=())
    _add(game, card_db, "Grizzly Bears", 0, "hand")
    assert lands_put(game, 0, (_scout_host(scout),)) == 0


@pytest.mark.parametrize("name", ["Growth Spiral", "Spelunking",
                                  "Arboreal Grazer"])
def test_a_cast_puts_the_land_its_spell_or_enter_trigger_puts(card_db, name):
    """A spell's own instruction, or a permanent's enter trigger, whatever
    the permanent's type."""
    from ai.land_put import lands_put_on_cast
    assert lands_put_on_cast(None, 0, card_db.get_card(name)) == 1


def test_an_instruction_no_executor_performs_puts_nothing(card_db):
    """Cultivator Colossus's enter trigger repeats ("... and repeat this
    process"), a loop the grammar refuses: no carrier takes it."""
    from ai.land_put import lands_put_on_cast
    assert lands_put_on_cast(None, 0,
                             card_db.get_card("Cultivator Colossus")) == 0


# ── The cast projection ───────────────────────────────────────────────

def _project(card_db, name):
    from ai.ev_evaluator import EVSnapshot, _project_spell
    snap = EVSnapshot(my_life=20, opp_life=20, my_hand_size=5,
                      opp_hand_size=5, my_mana=3, opp_mana=3,
                      my_total_lands=3, opp_total_lands=3, turn_number=3)
    card = CardInstance(template=card_db.get_card(name), owner=0,
                        controller=0, instance_id=1, zone="hand")
    return snap, _project_spell(card, snap, dk=None, game=None,
                                player_idx=0)


@pytest.mark.parametrize("name", ["Growth Spiral", "Spelunking",
                                  "Arboreal Grazer"])
def test_a_cast_that_puts_a_land_projects_one_more_land(card_db, name):
    snap, proj = _project(card_db, name)
    assert proj.my_total_lands - snap.my_total_lands == 1


def test_a_land_put_charges_no_card(card_db):
    """The land card's use is the land it becomes: the cast charges the
    cast card alone, and the land's mana pays the cast's cost back."""
    from ai.scoring_constants import ETB_LAND_FROM_HAND_MANA_VALUE
    snap, proj = _project(card_db, "Arboreal Grazer")
    cost = card_db.get_card("Arboreal Grazer").cmc
    assert proj.my_hand_size - snap.my_hand_size == -1
    assert proj.my_mana - snap.my_mana == pytest.approx(
        ETB_LAND_FROM_HAND_MANA_VALUE - cost)


def test_a_put_no_executor_performs_projects_no_land(card_db):
    snap, proj = _project(card_db, "Cultivator Colossus")
    assert proj.my_total_lands == snap.my_total_lands


# ── The activation ────────────────────────────────────────────────────

def _candidates(game, card):
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game
    snap = snapshot_from_game(game, 0)
    return snap, [c for c in activation_candidates(game, 0, snap)
                  if c[0] is card]


def test_an_activation_that_puts_a_land_is_worth_the_mana_it_delivers(
        card_db):
    """Its only cost is {T}, which moves no projected field; what it buys
    is the land's mana, at the per-mana clock rate."""
    from ai.clock import mana_clock_impact
    from ai.scoring_constants import (CLOCK_IMPACT_LIFE_SCALING,
                                      ETB_LAND_FROM_HAND_MANA_VALUE)
    game, scout = _game(card_db)
    snap, cands = _candidates(game, scout)
    assert len(cands) == 1
    _perm, _index, targets, ev, _reason = cands[0]
    assert targets == []
    assert ev == pytest.approx(ETB_LAND_FROM_HAND_MANA_VALUE
                               * mana_clock_impact(snap)
                               * CLOCK_IMPACT_LIFE_SCALING)


def test_no_activation_when_the_hand_holds_no_land(card_db):
    game, scout = _game(card_db, hand_lands=())
    _add(game, card_db, "Grizzly Bears", 0, "hand")
    _snap, cands = _candidates(game, scout)
    assert cands == []


def test_the_ai_activates_it_after_its_land_drop(card_db):
    from ai.ev_player import EVPlayer
    game, scout = _game(card_db)
    ai = EVPlayer(player_idx=0, deck_name="Amulet Titan",
                  rng=random.Random(0))
    decision = ai.decide_main_phase(game)
    assert decision is not None and decision[0] == "activate"
    assert decision[1] is scout
