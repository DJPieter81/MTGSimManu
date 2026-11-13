"""A token is created by the player its creator names, with exactly the
characteristics its creator prints (CR 111.2, 111.3, 111.4); a modal
spell's chosen mode that targets a player has that player chosen as the
spell is cast (CR 601.2c).

* CR 111.3: the effect that creates a token defines its characteristics --
  types, subtypes, colours, power and toughness, keywords, and the
  abilities it grants ("with 'Sacrifice this token: Add {C}.'"); they are
  the token's text.
* CR 111.4: a token its creator does not name is named for its subtypes
  plus "Token".
* CR 111.2: the player who creates a token owns it, and it enters under
  that player's control.

The defects: the legacy token clause read the token off the clause with a
regex. A worded count ("two") failed it, so the token was a colourless
"Creature Token" with no subtypes or keywords; a digit count named it by
its lowercased last subtype. "Target player creates X ... tokens" matched
nothing, so Kozilek's Command's token mode -- chosen in 17 of 18 traced
casts -- created nothing (`unhandled/mode`, 25 rows of the ed-post arm).

Card names are fixture carriers: 220 pool cards create a token with a
granted ability, 41 have another player create tokens, and 59 modal
spells have a mode that targets only a player.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType, Keyword
from engine.game_state import GameState
from engine.mana import Color, ManaCost
from engine.stack import StackItem, StackItemType


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    if zone != "stack":
        getattr(game.players[controller], zone).append(card)
    return card


def _resolve(game, card, controller, **item):
    game._execute_spell_effects(StackItem(
        item_type=StackItemType.SPELL, source=card, controller=controller,
        **item))


def _tokens(game, player):
    return [c for c in game.players[player].battlefield if c.is_token]


def test_a_token_has_the_types_colours_and_keywords_its_creator_prints(card_db):
    game = GameState(rng=random.Random(0))
    screech = _add(game, card_db, "Battle Screech", 0, "stack")
    _resolve(game, screech, 0)
    birds = _tokens(game, 0)
    assert len(birds) == 2, "a worded count is still a count"
    for b in birds:
        t = b.template
        assert (t.power, t.toughness) == (1, 1)
        assert t.card_types == [CardType.CREATURE]
        assert t.subtypes == ["Bird"]
        assert t.colors == {Color.WHITE}
        assert Keyword.FLYING in t.keywords


def test_an_unnamed_token_is_named_for_its_subtypes(card_db):
    game = GameState(rng=random.Random(0))
    call = _add(game, card_db, "Allied Reinforcements", 0, "stack")
    _resolve(game, call, 0)
    names = {c.name for c in _tokens(game, 0)}
    assert names == {"Knight Ally Token"}
    assert all(c.template.subtypes == ["Knight", "Ally"]
               for c in _tokens(game, 0))


def test_an_artifact_creature_token_is_both(card_db):
    game = GameState(rng=random.Random(0))
    spell = _add(game, card_db, "Mass Production", 0, "stack")
    _resolve(game, spell, 0)
    made = _tokens(game, 0)
    assert len(made) == 4
    assert all(set(c.template.card_types) == {CardType.ARTIFACT,
                                              CardType.CREATURE}
               for c in made)


def _command_board(card_db):
    """Player 1 holds the command with X = 3; player 0 has a creature of
    mana value 2 for its exile mode."""
    game = GameState(rng=random.Random(0))
    game.active_player = 1
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    command = _add(game, card_db, "Kozilek's Command", 1, "stack")
    return game, command, bears


def test_target_player_creates_the_tokens_its_mode_names(card_db):
    game, command, bears = _command_board(card_db)
    _resolve(game, command, 1, targets=[bears.instance_id], x_value=3,
             modes_chosen=[0, 2], mode_players={0: ((1,),)})
    spawn = _tokens(game, 1)
    assert len(spawn) == 3, "X tokens"
    assert bears.zone == "exile", "the other chosen mode still performs"
    for s in spawn:
        assert s.controller == 1 and s.owner == 1
        assert s.name == "Eldrazi Spawn Token"
        assert (s.template.power, s.template.toughness) == (0, 1)
        assert s.template.colors == set(), "colorless"


def test_a_token_created_by_the_opponent_is_theirs(card_db):
    game, command, bears = _command_board(card_db)
    _resolve(game, command, 1, targets=[bears.instance_id], x_value=2,
             modes_chosen=[0, 2], mode_players={0: ((0,),)})
    assert len(_tokens(game, 0)) == 2 and not _tokens(game, 1)
    assert all(t.controller == 0 and t.owner == 0 for t in _tokens(game, 0))


def test_a_granted_sacrifice_for_mana_ability_pays_for_a_spell(card_db):
    game, command, bears = _command_board(card_db)
    _resolve(game, command, 1, targets=[bears.instance_id], x_value=1,
             modes_chosen=[0, 2], mode_players={0: ((1,),)})
    spawn, = _tokens(game, 1)
    assert spawn.template.sacrifice_mana_units == [["C"]]
    assert game.tap_lands_for_mana(1, ManaCost(generic=1), "a spell")
    assert spawn not in game.players[1].battlefield, \
        "the token was sacrificed for the mana"


def test_a_chosen_modes_player_is_chosen_as_the_spell_is_cast(card_db):
    from engine import modal_spell
    from engine.game_runner import AICallbacks
    game, command, _ = _command_board(card_db)
    game.callbacks = AICallbacks()
    players = modal_spell.choose_mode_players(game, command, 1, [0, 2],
                                              x_value=3)
    assert players == {0: ((1,),)}, \
        "tokens are worth most to their creator's side: the caster"


def test_an_edict_mode_is_aimed_at_the_opponent(card_db):
    from ai.modal import named_player_value
    from engine.effect_spec import Verb, iter_specs
    game, _, bears = _command_board(card_db)
    _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    edict = card_db.get_card("Diabolic Edict").effects.spell(0)
    s = next(s for s in iter_specs(edict.specs) if s.verb is Verb.SACRIFICE)
    assert named_player_value(game, 1, s, 0) > 0 > \
        named_player_value(game, 1, s, 1)


def test_a_token_mode_is_worth_the_tokens_it_makes(card_db):
    from ai.modal import mode_value
    game, command, _ = _command_board(card_db)
    three = mode_value(game, command, 1, 0, x_value=3)
    assert three > mode_value(game, command, 1, 0, x_value=1) > 0
    assert mode_value(game, command, 1, 0, x_value=0) == 0


def test_a_token_the_owner_does_not_build_is_refused(card_db):
    """A granted ability other than a sacrifice-for-mana one (a trigger
    here) is not built from the spec: the executor refuses it and the
    legacy carrier keeps it."""
    from engine.effect_executors import execute_create_token
    from engine.effect_spec import Verb, iter_specs
    from engine.permanent_effects import printed_token
    fable = card_db.get_card(
        "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki")
    spec = next(s for h in fable.effects.walk() for s in iter_specs(h.specs)
                if s.verb is Verb.CREATE_TOKEN)
    assert printed_token(spec.payload) is None
    assert not execute_create_token.supports(spec)


@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit, rule):
    return [f for f in audit.drain() if f["rule"] == rule]


def test_the_audit_records_a_token_built_unlike_its_text(card_db, audit,
                                                         monkeypatch):
    from engine import permanent_effects
    real = permanent_effects.printed_token

    def wrong(spec):
        built = real(spec)
        return built and dict(built, subtypes=["Goblin"])
    monkeypatch.setattr(permanent_effects, "printed_token", wrong)
    game = GameState(rng=random.Random(0))
    _resolve(game, _add(game, card_db, "Battle Screech", 0, "stack"), 0)
    assert _violations(audit, "111.3/token_characteristics")


def test_the_audit_is_silent_when_the_token_is_built_as_printed(card_db,
                                                                audit):
    game = GameState(rng=random.Random(0))
    _resolve(game, _add(game, card_db, "Battle Screech", 0, "stack"), 0)
    assert len(_tokens(game, 0)) == 2
    assert not _violations(audit, "111.3/token_characteristics")


def test_the_audit_records_a_player_mode_performed_with_no_player(card_db,
                                                                  audit):
    game, command, bears = _command_board(card_db)
    _resolve(game, command, 1, targets=[bears.instance_id], x_value=2,
             modes_chosen=[0, 2], mode_players={})
    assert _violations(audit, "601.2c/mode_player_chosen")


def test_the_audit_is_silent_when_the_modes_player_was_chosen(card_db, audit):
    game, command, bears = _command_board(card_db)
    _resolve(game, command, 1, targets=[bears.instance_id], x_value=2,
             modes_chosen=[0, 2], mode_players={0: ((1,),)})
    assert not _violations(audit, "601.2c/mode_player_chosen")
