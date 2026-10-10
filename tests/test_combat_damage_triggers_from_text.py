"""Combat-damage-to-a-player triggers are read from the card's text (CR
510.2, 603.2, 603.10).

"Whenever ~ deals combat damage to a player, create a Treasure token and
exile the top card of that player's library. Until end of turn, you may
cast that card." The head names its event -- who deals the damage and to
what -- and "that player" is the player dealt the damage. The engine ran
an oracle-substring block for every attacker that dealt damage to a player:
any "draw a card" drew, any "treasure" made a Treasure, and any "exile the
top card" exiled the defending player's top card, whatever the card printed.
"""
from __future__ import annotations

import pytest

from engine.effect_spec import (CombatDamageEvent, EventHint, HostKind, Ref,
                                RefKind, Verb, iter_specs)


def _combat_hosts(template):
    return [h for h in template.effects.walk(include_sub=False)
            if h.kind is HostKind.TRIGGERED and h.trigger is not None
            and EventHint.COMBAT_DAMAGE_TO_PLAYER in h.trigger.event_hints]


# ── The head ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,dealer,recipient", [
    ("Ragavan, Nimble Pilferer", "self", "player"),
    ("Psychic Frog", "self", "player_or_planeswalker"),
    ("Sword of Fire and Ice", "equipped", "player"),
])
def test_a_combat_damage_head_types_its_dealer_and_recipient(
        card_db, name, dealer, recipient):
    (host,) = _combat_hosts(card_db.get_card(name))
    assert host.trigger.combat_damage == CombatDamageEvent(
        dealer=dealer, recipient=recipient)


def test_a_head_with_a_rider_the_table_does_not_read_is_untyped(card_db):
    """"one or more creatures you control deal combat damage to a player"
    is one event for the batch (CR 603.2c); the table types single dealers
    only."""
    from engine.effect_grammar.structure import _combat_damage_event
    assert _combat_damage_event(
        "whenever one or more creatures you control deal combat damage to "
        "a player") is None


# ── "that player's library" ───────────────────────────────────────────

def test_the_top_card_of_that_players_library_is_the_damaged_players(
        card_db):
    """In a head that names a player, "that player" is the event's player
    (the player dealt the damage): the exile reads the top card of THEIR
    library, and "that card" is the exile's result."""
    (host,) = _combat_hosts(card_db.get_card("Ragavan, Nimble Pilferer"))
    specs = list(iter_specs(host.specs))
    exile = next(s for s in specs if s.verb is Verb.EXILE)
    assert exile.filter.zone == "library" and exile.filter.position == "top"
    assert exile.filter.owner == Ref(RefKind.EVENT_PLAYER)
    assert exile.amount.n == 1
    permit = next(s for s in specs if s.verb is Verb.CONTINUOUS)
    assert permit.ref.kind is RefKind.RESULT and permit.ref.index == exile.seq


def test_the_top_card_of_defending_players_library_is_typed():
    from engine.effect_grammar.sub.filter import parse_filter
    t = "the top card of defending player's library"
    f = parse_filter(t, (0, len(t))).value
    assert (f.zone, f.position) == ("library", "top")
    assert f.owner == Ref(RefKind.DEFENDING_PLAYER)


# ── The executors (R2.2) ──────────────────────────────────────────────

def _game():
    import random
    from engine.game_state import GameState, Phase
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    from engine.cards import CardInstance
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def test_a_draw_spec_draws_through_the_draw_owner(card_db):
    """"Draw a card": the controller draws through `draw_cards`."""
    from engine import effect_resolver as er
    game = _game()
    frog = _put(game, card_db, "Psychic Frog", "battlefield")
    top = _put(game, card_db, "Island", "library")
    (host,) = _combat_hosts(frog.template)
    assert er.can_execute(host, "card_flow")
    assert er.resolve_ability(game, er.handle_of(frog), 0, host, (),
                              family="card_flow",
                              event=er.TriggerEvent(player=1),
                              source_object=frog)
    assert top in game.players[0].hand


def test_the_damaged_players_top_card_is_exiled_with_a_cast_permission(
        card_db):
    """Ragavan's body: a Treasure for its controller, the top card of THE
    DAMAGED PLAYER's library into its owner's exile, castable this turn by
    the controller (R1's permission over another player's card)."""
    from engine import effect_resolver as er, rules_query
    game = _game()
    ragavan = _put(game, card_db, "Ragavan, Nimble Pilferer", "battlefield")
    mine = _put(game, card_db, "Island", "library")
    theirs = _put(game, card_db, "Lightning Bolt", "library", idx=1)
    (host,) = _combat_hosts(ragavan.template)
    assert er.can_execute(host, "card_flow")
    assert er.resolve_ability(game, er.handle_of(ragavan), 0, host, (),
                              family="card_flow",
                              event=er.TriggerEvent(player=1),
                              source_object=ragavan)
    assert [c.name for c in game.players[0].battlefield
            if c is not ragavan] == ["Treasure Token"]
    assert theirs in game.players[1].exile and mine in game.players[0].library
    assert rules_query.permitted_cards(game, 0) == [theirs]


def test_with_no_event_player_nothing_is_exiled(card_db):
    from engine import effect_resolver as er
    game = _game()
    ragavan = _put(game, card_db, "Ragavan, Nimble Pilferer", "battlefield")
    theirs = _put(game, card_db, "Lightning Bolt", "library", idx=1)
    (host,) = _combat_hosts(ragavan.template)
    er.resolve_ability(game, er.handle_of(ragavan), 0, host, (),
                       family="card_flow", source_object=ragavan)
    assert theirs in game.players[1].library


def test_a_new_executor_switches_no_other_carriers_host(card_db):
    """A38: DRAW has an executor for the combat-damage carrier; a spell
    that only draws stays outside the card-flow carriers' shape until a
    unit switches them and the harness proves it."""
    from engine import effect_resolver as er
    from engine.effect_views import STRICT
    spell = next(h for h in card_db.get_card("Divination").effects.walk(
        include_sub=False) if h.kind is HostKind.SPELL)
    assert er.can_execute(spell, "card_flow")
    assert not STRICT["card_flow"](spell)


# ── The carrier (R2.3) ────────────────────────────────────────────────

def _attack(game, attacker, planeswalker=None):
    from engine.combat_manager import CombatManager
    cm = CombatManager()
    cm.declare_attackers(game, [attacker], 0, attack_targets=(
        {attacker.instance_id: planeswalker} if planeswalker else None))
    cm.resolve_combat_damage(game)


def test_a_trigger_that_exiles_its_controllers_top_card_reads_its_own_text(
        card_db):
    """"Exile the top card of YOUR library. You may play it this turn":
    the legacy substring exiled the defending player's top card."""
    from engine import rules_query
    game = _game()
    speaker = _put(game, card_db, "Prophetic Flamespeaker", "battlefield")
    mine = _put(game, card_db, "Mountain", "library")
    theirs = _put(game, card_db, "Island", "library", idx=1)
    _attack(game, speaker)
    assert mine.zone == "exile" and mine in game.players[0].exile
    assert theirs in game.players[1].library
    assert rules_query.permitted_cards(game, 0) == [mine]   # a land: "play"


def test_ragavan_connects_through_the_carrier(card_db):
    from engine import rules_query
    game = _game()
    ragavan = _put(game, card_db, "Ragavan, Nimble Pilferer", "battlefield")
    top = _put(game, card_db, "Lightning Bolt", "library", idx=1)
    _attack(game, ragavan)
    assert top in game.players[1].exile
    assert rules_query.permitted_cards(game, 0) == [top]
    assert [c.name for c in game.players[0].battlefield
            if c is not ragavan] == ["Treasure Token"]


def test_combat_damage_to_a_planeswalker_triggers_a_player_or_planeswalker_head(
        card_db):
    """Psychic Frog: "Whenever ~ deals combat damage to a player or
    planeswalker, draw a card." The legacy path ran only for damage to a
    player."""
    game = _game()
    frog = _put(game, card_db, "Psychic Frog", "battlefield")
    top = _put(game, card_db, "Island", "library")
    walker = _put(game, card_db, "Wrenn and Six", "battlefield", idx=1)
    walker.loyalty = 5
    _attack(game, frog, planeswalker=walker)
    assert top in game.players[0].hand
