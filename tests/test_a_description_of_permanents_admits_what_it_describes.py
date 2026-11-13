"""A description of permanents admits exactly the permanents it describes,
by what is true of them now (CR 122.1, 608.2h, 613).

"Put a +1/+1 counter on each Cat you control", "each other Ally creature
you control", "each other artifact creature you control", "each attacking
creature you control": the instruction reaches every permanent its player
controls that the description admits as it is performed, and no other.
What it reads is what is true of the permanent then: the types, subtypes and
colours it has (CR 613), its state (CR 110.5, 506.4), its counters (CR 122)
and its keywords (CR 702).

The engine's rule:
- One owner, `effect_conditions.permanent_described`, answers whether a
  typed battlefield description (`CardFilter`) admits a permanent.
  `description_supported` names the descriptions it reads. A name, a
  characteristic bound or another zone is refused.
- The team-counter instruction (`effect_executors.execute_put_counters`)
  covers what the description admits, with as many counters as it prints.
- A count and an edict read the same owner.

The defect: the team-counter instruction ran only for "each [other]
creature you control" with one counter. Every other description was
refused, and its carrier fell back. Ajani, Nacatl Avenger's +2 (Boros
Energy x4) was never offered. Enter triggers such as "each other Ally
creature you control" resolved as nothing.

Card names are fixture carriers: each is one member of its class.
"""
from __future__ import annotations

import dataclasses
import random

import pytest

from engine.cards import CardInstance, Keyword
from engine.game_state import GameState, Phase
from engine.planeswalker_manager import PlaneswalkerManager

AJANI = "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger"


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


def _game(card_db, lands=("Mountain", "Plains", "Forest")):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for p in (0, 1):
        for name in lands:
            for _ in range(3):
                _add(game, card_db, name, p)
        for _ in range(4):
            _add(game, card_db, "Island", p, "library")
    return game


def _cast(game, card_db, name, controller=0):
    card = _add(game, card_db, name, controller, "hand")
    assert game.cast_spell(controller, card, [])
    game.resolve_stack()
    return card


# ── Through the carriers ──────────────────────────────────────────────

def test_a_counter_on_each_subtype_reaches_exactly_that_subtype(card_db):
    """Ajani, Nacatl Avenger's +2: "Put a +1/+1 counter on each Cat you
    control." It is offered, and only its controller's Cats get one."""
    game = _game(card_db)
    ajani = _add(game, card_db, AJANI, 0)
    ajani.is_transformed = True
    ajani.loyalty_counters = 3
    cat = _add(game, card_db, "Ocelot Pride", 0)
    bear = _add(game, card_db, "Grizzly Bears", 0)
    their_cat = _add(game, card_db, "Ocelot Pride", 1)
    assert "plus" in PlaneswalkerManager.resolvable_ability_slots(ajani, game)
    assert game.activate_planeswalker(0, ajani, "plus")
    assert (cat.plus_counters, bear.plus_counters,
            their_cat.plus_counters) == (1, 0, 0)


def test_each_other_creature_of_a_subtype_excludes_the_source(card_db):
    """Earth King's Lieutenant: "When this creature enters, put a +1/+1
    counter on each other Ally creature you control." """
    game = _game(card_db)
    ally = _add(game, card_db, "Expedition Envoy", 0)     # a vanilla Ally
    bear = _add(game, card_db, "Grizzly Bears", 0)
    their_ally = _add(game, card_db, "Expedition Envoy", 1)
    lieutenant = _cast(game, card_db, "Earth King's Lieutenant")
    assert (ally.plus_counters, bear.plus_counters,
            their_ally.plus_counters, lieutenant.plus_counters) == (1, 0, 0, 0)


def test_every_type_a_description_joins_must_be_present(card_db):
    """Arcbound Shikari: "each other artifact creature you control" -- an
    artifact that is not a creature, and a creature that is not an
    artifact, get none."""
    game = _game(card_db)
    construct = _add(game, card_db, "Ornithopter", 0)
    bear = _add(game, card_db, "Grizzly Bears", 0)
    bauble = _add(game, card_db, "Mishra's Bauble", 0)
    _cast(game, card_db, "Arcbound Shikari")
    assert (construct.plus_counters, bear.plus_counters,
            bauble.plus_counters) == (1, 0, 0)


# ── The owner (`effect_conditions.permanent_described`) ───────────────

def _described(card, source=None, **entries):
    from engine.effect_conditions import (description_supported,
                                          permanent_described)
    from engine.effect_spec import CardFilter
    f = CardFilter(**{k: (frozenset(v) if isinstance(v, (set, list))
                          else v) for k, v in entries.items()})
    assert description_supported(f)
    return permanent_described(card, f, source=source)


def test_the_description_reads_state_counters_keywords_and_colours(card_db):
    game = _game(card_db)
    bear = _add(game, card_db, "Grizzly Bears", 0)
    pride = _add(game, card_db, "Ocelot Pride", 0)        # white Cat, lifelink
    assert _described(pride, types={"creature"}, with_keywords={"lifelink"})
    assert not _described(bear, types={"creature"},
                          with_keywords={"lifelink"})
    bear.temp_keywords.add(Keyword.LIFELINK)               # an effect grants it
    assert _described(bear, types={"creature"}, with_keywords={"lifelink"})
    assert _described(pride, colors={"W"})
    assert not _described(bear, colors={"W", "R"})
    assert _described(bear, not_colors={"W"})
    assert not _described(bear, state={"tapped"})
    bear.tapped = True
    assert _described(bear, state={"tapped", "attacking"})
    assert not _described(bear, counters=(("+1/+1", True),))
    bear.add_plus_counters(1, game)
    assert _described(bear, counters=(("+1/+1", True),))
    assert _described(pride, counters=(("+1/+1", False),))
    assert not _described(pride, other=True, source=pride)


def test_a_type_an_effect_adds_is_described(card_db):
    """CR 613: the description reads the types the permanent has now."""
    from engine.cards import CardType
    game = _game(card_db)
    bear = _add(game, card_db, "Grizzly Bears", 0)
    assert not _described(bear, all_types={"artifact", "creature"})
    bear.cem_types_added = {CardType.ARTIFACT}
    assert _described(bear, all_types={"artifact", "creature"})


def test_a_description_the_owner_cannot_read_is_refused(card_db):
    """A name ("named Charmed Stray") or a characteristic bound is not read:
    the instruction is refused, never half-applied."""
    from engine.effect_conditions import description_supported
    from engine.effect_executors import team_counters_supported
    from engine.effect_spec import CardFilter, Verb, iter_specs
    stray = card_db.get_card("Charmed Stray")
    spec = next(s for h in stray.effects.walk() for s in iter_specs(h.specs)
                if s.verb is Verb.PUT_COUNTERS)
    assert not team_counters_supported(spec)
    assert not description_supported(CardFilter(
        types=frozenset({"creature"}),
        stat_bounds=(("power", "<=", None),)))


def test_a_printed_number_of_counters_is_put_on_each(card_db):
    """"Put two +1/+1 counters on each creature you control": the counter
    noun is typed once per counter."""
    from engine.effect_executors import (execute_put_counters,
                                         team_counters_supported)
    from engine.effect_spec import Verb, iter_specs
    practicum = card_db.get_card("Germination Practicum")
    spec = next(s for h in practicum.effects.walk()
                for s in iter_specs(h.specs) if s.verb is Verb.PUT_COUNTERS)
    assert team_counters_supported(spec)
    game = _game(card_db)
    bear = _add(game, card_db, "Grizzly Bears", 0)
    execute_put_counters(_ctx(game, card_db, bear), spec, (0,))
    assert bear.plus_counters == 2


def _ctx(game, card_db, source):
    from engine import effect_resolver as er
    return er.Resolution(game=game, source=er.handle_of(source),
                         controller=0, ability=None, chosen=(),
                         source_object=source)


# ── The auditor (CR 122.1) ────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain()
            if f["rule"] == "122.1/described_counters"]


def test_the_audit_records_a_described_permanent_left_without_its_counter(
        card_db, audit, monkeypatch):
    from engine import effect_conditions
    game = _game(card_db)
    ajani = _add(game, card_db, AJANI, 0)
    ajani.is_transformed = True
    ajani.loyalty_counters = 3
    _add(game, card_db, "Ocelot Pride", 0)
    real = effect_conditions.permanent_described
    monkeypatch.setattr(                                   # the defect: a
        effect_conditions, "permanent_described",          # subtype is not
        lambda perm, f, source=None: real(                 # read
            perm, dataclasses.replace(f, subtypes=frozenset({"x"})),
            source=source))
    game.activate_planeswalker(0, ajani, "plus")
    assert _findings(audit)


def test_the_audit_is_silent_when_the_description_is_covered(card_db, audit):
    game = _game(card_db)
    ajani = _add(game, card_db, AJANI, 0)
    ajani.is_transformed = True
    ajani.loyalty_counters = 3
    _add(game, card_db, "Ocelot Pride", 0)
    _add(game, card_db, "Grizzly Bears", 0)
    assert game.activate_planeswalker(0, ajani, "plus")
    assert not _findings(audit)
