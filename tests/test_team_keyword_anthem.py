"""A static team keyword anthem ("Creatures you control have trample") must
grant that keyword to the controller's creatures (CR 611 continuous effect).

Only Scion of Draco's colour-conditional grant had a bespoke handler; the
unconditional class — ~121 Modern permanents (Archetype of Aggression,
Archetype of Imagination, the anthem cycle, …) — was a silent no-op, so a
creature next to the anthem never gained the keyword. Parsed once at load into
CardTemplate.team_keyword_grant and registered as a continuous lord effect on
ETB (no card names). The one-shot "gain <kw> until end of turn" pump form
(Craterhoof) is handled separately via team_pump_data and is not this shape.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, Keyword
from engine.game_state import GameState
from engine.oracle_parser import parse_team_keyword_grant


class TestParseTeamKeywordGrant:
    def test_unconditional_team_anthem(self):
        d = parse_team_keyword_grant("Creatures you control have trample.")
        assert d == {"keywords": frozenset({"trample"}), "others_only": False}

    def test_other_creatures_sets_others_only(self):
        d = parse_team_keyword_grant("Other creatures you control have vigilance.")
        assert d["others_only"] is True and "vigilance" in d["keywords"]

    def test_conditional_is_not_matched(self):
        # colour-conditional (Scion of Draco) keeps its bespoke handler
        assert parse_team_keyword_grant(
            "Each creature you control has trample if it's green.") is None

    def test_one_shot_pump_is_not_matched(self):
        assert parse_team_keyword_grant(
            "Creatures you control gain trample until end of turn.") is None

    def test_no_anthem_returns_none(self):
        assert parse_team_keyword_grant("When this enters, draw a card.") is None


def _bf(game, card_db, name, controller=0):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    game.players[controller].battlefield.append(c)
    return c


def test_team_anthem_grants_keyword_to_your_creatures(card_db):
    game = GameState(rng=random.Random(0))
    bear = _bf(game, card_db, "Grizzly Bears")
    assert Keyword.TRAMPLE not in bear.keywords
    # Archetype of Aggression: "Creatures you control have trample."
    arch = _bf(game, card_db, "Archetype of Aggression")
    game._handle_permanent_etb(arch, controller=0)
    game.continuous_effects.recalculate(game)
    assert Keyword.TRAMPLE in bear.keywords, "anthem must grant trample to your creatures"


def test_anthem_grant_retracts_when_source_leaves(card_db):
    game = GameState(rng=random.Random(0))
    bear = _bf(game, card_db, "Grizzly Bears")
    arch = _bf(game, card_db, "Archetype of Aggression")
    game._handle_permanent_etb(arch, controller=0)
    game.continuous_effects.recalculate(game)
    assert Keyword.TRAMPLE in bear.keywords
    # Source leaves: the continuous effect must retract.
    game.players[0].battlefield.remove(arch)
    arch.zone = "graveyard"
    game.continuous_effects.recalculate(game)
    assert Keyword.TRAMPLE not in bear.keywords, "grant must retract with its source"


def test_later_entering_creature_is_also_covered(card_db):
    game = GameState(rng=random.Random(0))
    arch = _bf(game, card_db, "Archetype of Aggression")
    game._handle_permanent_etb(arch, controller=0)
    # A creature that enters AFTER the anthem is still affected (dynamic filter).
    bear = _bf(game, card_db, "Grizzly Bears")
    game.continuous_effects.recalculate(game)
    assert Keyword.TRAMPLE in bear.keywords
