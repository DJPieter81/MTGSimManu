"""An ordinal cast trigger fires on the Nth spell of its kind its caster
casts this turn (CR 603.2, 603.4, 601.2i).

"Whenever you cast your second spell each turn" and "whenever you cast a
spell, if it's the second creature spell you cast this turn" name the same
event: the cast of the caster's Nth spell of a kind this turn. The
intervening "if" reads only the cast history and the spell as it was cast,
which nothing changes before the ability resolves, so checking it as the
ability triggers is checking it on resolution too (CR 603.4).

The engine's rule:
- The grammar types the ordinal on the cast head (`CastEvent.ordinal`),
  from either phrasing, over the kinds the cast tally records (card types:
  any, noncreature, creature, instant or sorcery, artifact, enchantment).
  A colour kind is left untyped: the tally does not record colours.
- `effect_carrier.cast_matches` counts the caster's spells of that kind
  this turn (`PlayerState.spells_cast_this_turn_kinds`, which already holds
  this cast) and fires on exactly the ordinal.

The defect: the intervening-if phrasing was refused by the grammar (an
UNMODELLED condition), so Vengevine's return never triggered. The head
phrasing ran only through a legacy view (`ordinal_cast_trigger`) counting
every spell, whatever its kind.

Class: 44 pool cards print an ordinal cast head, plus the intervening-if
phrasing. Card names are fixture carriers.
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


def _board(card_db, lands=("Forest", "Mountain")):
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    for _ in range(4):
        for land in lands:
            _add(game, card_db, land, 0)
    return game


def _cast(game, card_db, name, targets=()):
    """Cast a spell from hand and let it resolve (a sorcery-speed spell
    needs an empty stack, CR 307.1)."""
    spell = _add(game, card_db, name, 0, "hand")
    assert game.cast_spell(0, spell, list(targets))
    while not game.stack.is_empty:
        game.resolve_stack()
    return spell


def _cast_head(card_db, name):
    from engine.effect_spec import EventHint
    (h,) = [h for h in card_db.get_card(name).effects.faces[0]
            if h.trigger is not None
            and EventHint.SPELL_CAST in h.trigger.event_hints]
    return h


# ── The grammar ───────────────────────────────────────────────────────

def test_the_intervening_if_phrasing_is_typed_as_an_ordinal_cast_head(card_db):
    h = _cast_head(card_db, "Vengevine")
    cast = h.trigger.cast
    assert (cast.caster, cast.spell, cast.ordinal) == ("you", "creature", 2)
    assert h.trigger.intervening_if is None
    assert all(s.verb.value != "unmodelled" for s in h.specs)


@pytest.mark.parametrize("name, caster, kind", [
    ("Cori-Steel Cutter", "you", "any"),
    ("Arcane Bombardment", "you", "instant_or_sorcery"),
    ("Esper Sentinel", "opponent", "noncreature"),
])
def test_the_head_phrasing_is_typed_with_its_ordinal(card_db, name, caster,
                                                     kind):
    cast = _cast_head(card_db, name).trigger.cast
    assert cast is not None
    assert (cast.caster, cast.spell) == (caster, kind)
    assert cast.ordinal in (1, 2)


def test_a_colour_kind_ordinal_is_left_untyped(card_db):
    # Zenith Chronicler: "... casts their first multicolored spell each
    # turn". The cast tally records types, not colours.
    assert _cast_head(card_db, "Zenith Chronicler").trigger.cast is None


# ── The carrier ───────────────────────────────────────────────────────

def _matches(game, card_db, cast, controller, caster, spell_name):
    from engine.effect_carrier import cast_matches
    spell = _add(game, card_db, spell_name, caster, "hand")
    from engine.rules_query import spell_kind
    game.players[caster].spells_cast_this_turn += 1
    game.players[caster].spells_cast_this_turn_kinds.append(
        spell_kind(spell.template))
    return cast_matches(cast, controller, caster, spell, game.active_player,
                        game=game)


def test_an_ordinal_head_fires_on_exactly_the_nth_spell_of_its_kind(card_db):
    from engine.effect_spec import CastEvent
    game = _board(card_db)
    second_creature = CastEvent(caster="you", spell="creature", ordinal=2)
    assert not _matches(game, card_db, second_creature, 0, 0, "Grizzly Bears")
    # A sorcery between them neither fires it nor advances the count.
    assert not _matches(game, card_db, second_creature, 0, 0, "Lava Spike")
    assert _matches(game, card_db, second_creature, 0, 0, "Grizzly Bears")
    assert not _matches(game, card_db, second_creature, 0, 0, "Grizzly Bears")


def test_an_ordinal_head_counts_only_the_casters_spells(card_db):
    from engine.effect_spec import CastEvent
    game = _board(card_db)
    second = CastEvent(caster="you", spell="any", ordinal=2)
    assert not _matches(game, card_db, second, 0, 1, "Lava Spike")
    assert not _matches(game, card_db, second, 0, 1, "Lava Spike")
    assert not _matches(game, card_db, second, 0, 0, "Lava Spike")
    assert _matches(game, card_db, second, 0, 0, "Lava Spike")


# ── End to end: the graveyard return the ordinal gates ────────────────

def test_the_second_creature_spell_returns_the_card_from_the_graveyard(
        card_db):
    game = _board(card_db)
    vine = _add(game, card_db, "Vengevine", 0, "graveyard")
    game.callbacks.choose_optional_effect = lambda *a, **k: True
    _cast(game, card_db, "Grizzly Bears")
    assert vine.zone == "graveyard"
    _cast(game, card_db, "Grizzly Bears")
    assert vine.zone == "battlefield" and vine in game.players[0].battlefield


def test_a_noncreature_second_spell_does_not_return_it(card_db):
    game = _board(card_db)
    vine = _add(game, card_db, "Vengevine", 0, "graveyard")
    game.callbacks.choose_optional_effect = lambda *a, **k: True
    _cast(game, card_db, "Grizzly Bears")
    _cast(game, card_db, "Lava Spike", [-2])
    assert vine.zone == "graveyard"


# ── The rules audit (CR 603.2) ────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    from engine import rules_audit
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _ordinal_violations(audit):
    return [f for f in audit.drain()
            if f["rule"] == "603.2/ordinal_cast_head"
            and f["kind"] == "violation"]


def test_the_audit_is_silent_when_the_ordinal_holds(card_db, audit):
    game = _board(card_db)
    _add(game, card_db, "Vengevine", 0, "graveyard")
    game.callbacks.choose_optional_effect = lambda *a, **k: True
    for _ in range(2):
        _cast(game, card_db, "Grizzly Bears")
    assert not _ordinal_violations(audit)


def test_the_audit_fires_when_the_carrier_ignores_the_ordinal(
        card_db, audit, monkeypatch):
    from engine import effect_carrier
    real = effect_carrier.cast_matches
    # The defect, restored: the ordinal is not counted.
    monkeypatch.setattr(
        effect_carrier, "cast_matches",
        lambda ev, *a, **k: real(
            __import__("dataclasses").replace(ev, ordinal=0), *a, **k))
    game = _board(card_db)
    _add(game, card_db, "Vengevine", 0, "graveyard")
    game.callbacks.choose_optional_effect = lambda *a, **k: True
    _cast(game, card_db, "Grizzly Bears")
    assert _ordinal_violations(audit)
