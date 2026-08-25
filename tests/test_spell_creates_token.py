"""An instant or sorcery that creates a token actually creates it.

Root cause (Eldrazi ramp mana audit, 2026-08-25): token creation was wired for
ATTACK triggers and DIES triggers, but `resolve_spell_from_oracle` had no
branch for a spell whose own effect is "Create a N/N … creature token". 522
Modern instants/sorceries carry that clause. Verified before the fix: casting
such a sorcery resolved to a complete no-op — battlefield count unchanged, the
spell simply vanished into the graveyard.

For ramp shells this is load-bearing: the token in question carries a
"Sacrifice this token: Add {C}" mana ability, so a dead token is dead ramp. (The
mana ability itself is a separate gap tracked in the same audit; this test pins
only that the body arrives.)

`resolve_spell_from_oracle` runs only after `EFFECT_REGISTRY` declines the
card, so a generic branch here cannot double-fire with a card-specific handler.

Rule under test: a spell whose oracle contains a create-token clause puts the
described token(s) onto its controller's battlefield, with the printed power,
toughness and any inner-quoted granted ability. Mechanic-driven (oracle clause),
no card names asserted.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.oracle_resolver import resolve_spell_from_oracle

_DB = CardDatabase()


def _game_with_spell(spell_name, library_names=()):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 3
    p = game.players[0]
    p.deck_name = "Eldrazi Ramp"
    game.players[1].deck_name = "Dimir Midrange"
    for nm in library_names:
        t = _DB.get_card(nm)
        assert t is not None, f"missing {nm}"
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="library")
        c._game_state = game
        p.library.append(c)
    t = _DB.get_card(spell_name)
    assert t is not None, f"missing {spell_name}"
    spell = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    return game, spell


def _tokens(game):
    return [c for c in game.players[0].battlefield
            if getattr(c, "is_token", False)]


def test_sorcery_create_token_clause_puts_a_body_onto_the_battlefield():
    game, spell = _game_with_spell(
        "Malevolent Rumble",
        ["Forest", "Sire of Seven Deaths", "Forest", "Forest", "Forest"])
    oracle = (spell.template.oracle_text or "").lower()
    assert "creature token" in oracle, "fixture premise: spell creates a token"

    before = len(game.players[0].battlefield)
    resolve_spell_from_oracle(game, spell, 0, None)
    tokens = _tokens(game)

    assert len(game.players[0].battlefield) > before, (
        "a spell whose effect creates a token must actually create it; the "
        "battlefield was unchanged")
    assert tokens, "the new permanent must be a token"
    tok = tokens[0]
    assert (tok.power, tok.toughness) == (0, 1), (
        f"the token must use the printed power/toughness from the clause; "
        f"got {tok.power}/{tok.toughness}")


def test_created_token_carries_its_inner_quoted_ability():
    """The quoted ability on the token is part of the printed effect."""
    game, spell = _game_with_spell(
        "Malevolent Rumble",
        ["Forest", "Sire of Seven Deaths", "Forest", "Forest", "Forest"])
    resolve_spell_from_oracle(game, spell, 0, None)
    tokens = _tokens(game)
    assert tokens, "precondition: a token was created"
    tok_oracle = (tokens[0].template.oracle_text or "").lower()
    assert "sacrifice" in tok_oracle and "add" in tok_oracle, (
        f"the token must carry the ability quoted in the creating spell's "
        f"text; got oracle {tok_oracle!r}")


def test_spell_without_a_token_clause_creates_nothing():
    """Negative case — the branch must not fire on unrelated spells."""
    game, spell = _game_with_spell("Lightning Bolt")
    oracle = (spell.template.oracle_text or "").lower()
    assert "token" not in oracle, "fixture premise: no token clause"
    resolve_spell_from_oracle(game, spell, 0, None)
    assert not _tokens(game), (
        "a spell with no create-token clause must not produce a token")
