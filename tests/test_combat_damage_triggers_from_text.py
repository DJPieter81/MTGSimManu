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
