"""A token's quoted ability belongs to the token, and a creature sacrificed
for mana dies (CR 111.4, 700.4, 603.6).

Two defects on one mechanic — the one-shot "Sacrifice this token: Add
{C}" mana of Eldrazi Spawn / Scion tokens:

1. `parse_sacrifice_mana_units` matched the ability inside the maker's
   quoted token text, so 21 Spawn/Scion makers carried their TOKEN's
   sacrifice-for-mana units on their own template — the payment solver
   could sacrifice Basking Broodscale itself for {C}. The quoted grant is
   the token's (the factory parses the inner text for the token); the
   maker's own text outside quotes is what the maker's field reads.
2. The payment solver's sacrifice-for-mana commit moved a creature token
   to the graveyard through a bare zone move that dispatches no dies
   triggers. A Spawn sacrificed for mana therefore never counted as a
   creature dying — no observer (Blade of the Bloodchief) ever saw it, and
   the Broodscale loop's third leg never turned. A creature sacrificed
   for mana dies through the same funnel every other death uses.

Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


def _add(game, card_db, name, controller, zone="battlefield"):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    return card


def test_a_token_makers_own_template_carries_no_sacrifice_mana_units(card_db):
    from engine.oracle_parser import parse_sacrifice_mana_units
    maker = card_db.get_card("Basking Broodscale")
    assert maker.sacrifice_mana_units == [], (
        "the quoted 'Sacrifice this token: Add {C}' is the TOKEN's ability")
    assert parse_sacrifice_mana_units(
        'Sacrifice this artifact: Add one mana of any color.') is not None, (
        "an ability printed on the card itself still parses")
    game = GameState(rng=random.Random(0))
    spawn = game.create_token(0, "creature", count=1, source_oracle=maker.oracle_text)[-1]
    assert spawn.template.sacrifice_mana_units == [["C"]], "the token keeps it"


def test_a_creature_sacrificed_for_mana_dies_through_the_death_funnel(card_db):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    bearer = _add(game, card_db, "Basking Broodscale", 0)
    blade = _add(game, card_db, "Blade of the Bloodchief", 0)
    assert game.attach_equipment(0, blade, bearer)
    spawn = game.create_token(0, "creature", count=1,
                              source_oracle=bearer.template.oracle_text)[-1]
    counters_before = bearer.plus_counters
    # No lands: the only way to pay {1} is to sacrifice the Spawn.
    assert game.tap_lands_for_mana(0, ManaCost(generic=1), None), (
        "fixture: the Spawn's sacrifice-for-mana must pay {1}")
    assert spawn.zone == "graveyard" and spawn not in game.players[0].battlefield
    assert bearer.plus_counters == counters_before + 1, (
        "a creature sacrificed for mana died — the death observer must fire")
    assert any(c.is_token and c is not spawn for c in game.players[0].battlefield), (
        "and the bearer's counters-placed trigger makes the next Spawn: the loop turns")
