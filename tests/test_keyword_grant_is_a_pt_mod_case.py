"""A keyword-only grant is the zero-P/T case of the targeted modifier
(CR 613.1f — "target creature gains <keyword> until end of turn").

The pump shape only read "+N/+M [and gains <kw>]", so a spell whose whole
effect is a keyword grant ("gains double strike until end of turn") typed as
no effect at all: 135 pool cards print the grant, and the typed keyword was
populated on 1 of them. Found as the census row `unhandled/spell` for
Assault Strobe (Izzet Prowess).

Rules pinned:
* the grant is parsed by the same clause owner as the P/T modifier and
  carries every keyword it names that the engine models (the `Keyword` enum
  is the vocabulary);
* it is typed on instants and sorceries only — a permanent's activated
  "target creature gains haste" belongs to the activation path;
* one predicate, `CardTemplate.has_targeted_pump`, answers "is this a
  targeted modifier?" for the resolver and the AI alike;
* the grant resolves on the chosen creature for the stated duration.
Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine.cards import CardInstance, Keyword
from engine.clause_resolver import resolve_clause
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_pump_spell, parse_pump_spell_keywords


def test_a_keyword_only_grant_parses_as_a_zero_modifier():
    text = "Target creature gains double strike until end of turn."
    assert parse_pump_spell(text) == (0, 0, "double strike")
    assert parse_pump_spell_keywords(text) == ("double strike",)


def test_every_named_modelled_keyword_is_carried():
    kws = parse_pump_spell_keywords(
        "Target creature gains flying and first strike until end of turn.")
    assert set(kws) == {"flying", "first strike"}


def test_an_unmodelled_word_is_not_a_grant():
    assert parse_pump_spell_keywords(
        "Target creature gains shroud until end of turn.") == ()


def test_the_existing_pt_shapes_are_unchanged():
    assert parse_pump_spell(
        "Target creature gets +2/+2 and gains flying until end of turn.") == (2, 2, "flying")


def test_the_grant_is_typed_across_the_spell_class(card_db):
    grants = [t for t in card_db.cards.values()
              if (t.is_instant or t.is_sorcery) and t.pump_spell_keywords
              and not t.pump_spell_power and not t.pump_spell_toughness]
    assert len(grants) >= 20, len(grants)


def test_a_permanents_activated_grant_is_not_a_pump_spell(card_db):
    # Hanweir Battlements: "{R}, {T}: Target creature gains haste until end of turn."
    land = card_db.get_card("Hanweir Battlements // Hanweir, the Writhing Township")
    assert not land.has_targeted_pump


def test_a_keyword_grant_resolves_on_the_chosen_creature(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    bear = CardInstance(template=card_db.get_card("Grizzly Bears"), owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="battlefield")
    bear._game_state = game
    bear.enter_battlefield()
    game.players[0].battlefield.append(bear)
    tpl = copy.copy(card_db.get_card("Assault Strobe"))
    assert tpl.has_targeted_pump
    spell = CardInstance(template=tpl, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    assert resolve_clause(game, spell, 0, [bear.instance_id])
    assert Keyword.DOUBLE_STRIKE in bear.keywords


def test_a_choice_between_keywords_grants_exactly_one():
    # "gains double strike or lifelink" is a choice (CR 608.2d), not both.
    kws = parse_pump_spell_keywords(
        "Target creature gains double strike or lifelink until end of turn.")
    assert len(kws) == 1 and kws[0] in {"double strike", "lifelink"}
