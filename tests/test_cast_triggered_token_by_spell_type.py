"""CR 603 cast-triggered token dispatch, generalised by spell TYPE.

# Mechanic under test

"Whenever you cast a[n] <type> spell, create a <token>." A permanent
with this trigger creates its token each time its controller casts a
spell matching the type condition — for ANY qualifying spell type, not
only "noncreature". The three formulations in the pool:

  - "cast an artifact spell"           -> fires on any artifact spell
  - "cast a noncreature spell"         -> fires on any non-creature spell
  - "cast an instant or sorcery spell" -> fires on an instant or a sorcery

The condition and token count are parsed once at DB load into
`CardTemplate.cast_trigger_token`; `engine.oracle_resolver
.resolve_spell_cast_trigger` reads the typed field and fires the token,
whose P/T/subtype/keywords come from the source oracle via
`parse_token_spec`. Card names below are fixture carriers only; the
mechanic is "a cast-triggered token whose spell-type condition is
satisfied by the cast spell".

Previously the dispatcher was gated on
`has_noncreature_spell_cast_trigger AND not spell_cast.is_creature`, so
the artifact-spell and instant/sorcery formulations created no token.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.game_state import GameState
from engine.oracle_resolver import resolve_spell_cast_trigger


def _put_on_battlefield(game, tmpl, controller):
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


def _spell_instance(game, tmpl, controller):
    return CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="stack",
    )


def _tokens(game, controller):
    return [c for c in game.players[controller].battlefield
            if getattr(c, "is_token", False)]


class TestCastTriggeredTokenFiresPerMatchingSpellType:

    def test_artifact_cast_trigger_creates_token(self, card_db):
        """Pinnacle Emissary (artifact-cast -> 1/1 flying Drone): an
        artifact spell cast satisfies the {artifact} condition."""
        game = GameState(rng=random.Random(0))
        emissary = _put_on_battlefield(
            game, card_db.get_card("Pinnacle Emissary"), 0)
        assert emissary.template.cast_trigger_token is not None
        assert "artifact" in emissary.template.cast_trigger_token["spell_types"]

        # Cast an artifact spell (any artifact card works as the fixture).
        artifact_spell = _spell_instance(
            game, card_db.get_card("Ornithopter"), 0)
        assert CardType.ARTIFACT in artifact_spell.template.card_types

        before = len(_tokens(game, 0))
        resolve_spell_cast_trigger(game, 0, artifact_spell)
        after = len(_tokens(game, 0))
        assert after - before == 1, "artifact-cast trigger made no Drone"

    def test_noncreature_cast_still_fires_generic_path(self, card_db):
        """Monastery Mentor (noncreature-cast -> Monk): a noncreature
        spell satisfies the {noncreature} sentinel condition."""
        game = GameState(rng=random.Random(0))
        mentor = _put_on_battlefield(
            game, card_db.get_card("Monastery Mentor"), 0)
        assert mentor.template.cast_trigger_token is not None

        # An instant is a noncreature spell.
        spell = _spell_instance(game, card_db.get_card("Lightning Bolt"), 0)
        assert not spell.template.is_creature

        before = len(_tokens(game, 0))
        resolve_spell_cast_trigger(game, 0, spell)
        assert len(_tokens(game, 0)) - before == 1

    def test_noncreature_trigger_does_not_fire_on_creature_spell(self, card_db):
        game = GameState(rng=random.Random(0))
        _put_on_battlefield(game, card_db.get_card("Monastery Mentor"), 0)
        creature_spell = _spell_instance(
            game, card_db.get_card("Young Pyromancer"), 0)
        assert creature_spell.template.is_creature

        before = len(_tokens(game, 0))
        resolve_spell_cast_trigger(game, 0, creature_spell)
        assert len(_tokens(game, 0)) - before == 0

    def test_non_artifact_instance_proves_engine_general(self, card_db):
        """Young Pyromancer is a NON-artifact creature whose trigger is
        "instant or sorcery spell". Verifying the token fires here proves
        the fix is not an artifact/Affinity special case — the same
        dispatch serves a card with a different spell-type condition and
        no artifact typing at all."""
        yp = card_db.get_card("Young Pyromancer")
        assert CardType.ARTIFACT not in yp.card_types  # explicitly non-artifact
        spec = yp.cast_trigger_token
        assert spec is not None
        assert spec["spell_types"] == frozenset({"instant", "sorcery"})

        game = GameState(rng=random.Random(0))
        _put_on_battlefield(game, yp, 0)

        # A sorcery satisfies {instant, sorcery}.
        sorcery = _spell_instance(game, card_db.get_card("Serum Visions"), 0)
        assert sorcery.template.is_sorcery

        before = len(_tokens(game, 0))
        resolve_spell_cast_trigger(game, 0, sorcery)
        assert len(_tokens(game, 0)) - before == 1, (
            "instant/sorcery cast trigger made no Elemental — fix is not "
            "engine-general"
        )

        # A creature spell must NOT satisfy {instant, sorcery}.
        game2 = GameState(rng=random.Random(1))
        _put_on_battlefield(game2, yp, 0)
        creature_spell = _spell_instance(
            game2, card_db.get_card("Ornithopter"), 0)
        before2 = len(_tokens(game2, 0))
        resolve_spell_cast_trigger(game2, 0, creature_spell)
        assert len(_tokens(game2, 0)) - before2 == 0
