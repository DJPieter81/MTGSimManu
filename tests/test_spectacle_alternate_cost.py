"""Spectacle alternate-cost mechanic (CR 702.131).

Rule: a player may cast a spell with a Spectacle cost for that cost
rather than its mana cost if an opponent lost life this turn.

Tests are phrased on the mechanic, not on card names:
  - spectacle_cost_parsed_from_oracle
  - spectacle_enables_alternate_cost_when_opponent_lost_life
  - spectacle_unavailable_when_no_opponent_life_loss
  - spectacle_not_available_when_cannot_afford_spectacle_cost
  - spectacle_cast_pays_spectacle_cost_not_normal_cost
  - life_lost_tracking_resets_each_turn
  - opponent_life_loss_tracked_via_take_damage
"""
from __future__ import annotations

import random

import pytest

from engine.oracle_parser import parse_spectacle_cost
from engine.mana import ManaCost
from engine.cards import CardInstance, CardTemplate, CardType, Color
from engine.game_state import GameState, Phase
from engine.player_state import PlayerState


# ── Oracle-parser unit tests (no DB, no GameState) ──────────────────────────

class TestParseSpectacleCost:
    """parse_spectacle_cost must return a ManaCost for each Spectacle cost."""

    def test_single_red_spectacle_cost(self):
        """Light Up the Stage / Skewer the Critics: Spectacle {R}."""
        oracle = (
            "Spectacle {R} (You may cast this spell for its spectacle cost "
            "rather than its mana cost if an opponent lost life this turn.)\n"
            "Exile the top two cards of your library."
        )
        cost = parse_spectacle_cost(oracle)
        assert cost is not None, "Spectacle {R} oracle must yield a ManaCost"
        assert isinstance(cost, ManaCost), \
            "parse_spectacle_cost must return ManaCost, not int"
        assert cost.red == 1, "spectacle cost should be 1 red pip"
        assert cost.generic == 0

    def test_two_color_spectacle_cost(self):
        """Rix Maadi Reveler: Spectacle {2}{B}{R}."""
        oracle = (
            "Spectacle {2}{B}{R} (You may cast this spell for its spectacle cost "
            "rather than its mana cost if an opponent lost life this turn.)\n"
            "When this creature enters, discard a card, then draw a card."
        )
        cost = parse_spectacle_cost(oracle)
        assert cost is not None, "Spectacle {2}{B}{R} oracle must yield a ManaCost"
        assert cost.generic == 2
        assert cost.black == 1
        assert cost.red == 1

    def test_no_spectacle_returns_none(self):
        """Oracle with no Spectacle keyword returns None."""
        oracle = "Flying\nDash {1}{R}"
        assert parse_spectacle_cost(oracle) is None

    def test_case_insensitive(self):
        """Parser handles lowercase 'spectacle'."""
        oracle = "spectacle {r}"
        cost = parse_spectacle_cost(oracle)
        assert cost is not None
        assert cost.red == 1


# ── life_lost_this_turn tracking tests ──────────────────────────────────────

class TestLifeLostThisTurnTracking:
    """PlayerState must track life lost this turn (CR 702.131b condition)."""

    def test_take_damage_increments_life_lost_this_turn(self):
        """take_damage must increment life_lost_this_turn by the amount."""
        ps = PlayerState(player_idx=0)
        assert hasattr(ps, 'life_lost_this_turn'), \
            "PlayerState must have life_lost_this_turn field"
        assert ps.life_lost_this_turn == 0
        ps.take_damage(3, source=None)
        assert ps.life_lost_this_turn == 3, \
            "life_lost_this_turn must increment when take_damage is called"

    def test_take_damage_accumulates_across_multiple_hits(self):
        """Multiple damage events accumulate in life_lost_this_turn."""
        ps = PlayerState(player_idx=0)
        ps.take_damage(2, source=None)
        ps.take_damage(3, source=None)
        assert ps.life_lost_this_turn == 5

    def test_reset_turn_tracking_clears_life_lost_this_turn(self):
        """reset_turn_tracking clears life_lost_this_turn to 0."""
        ps = PlayerState(player_idx=0)
        ps.take_damage(5, source=None)
        assert ps.life_lost_this_turn == 5
        ps.reset_turn_tracking()
        assert ps.life_lost_this_turn == 0, \
            "life_lost_this_turn must reset to 0 at the start of each turn"


# ── GameState integration: can_cast with spectacle ───────────────────────────

def _spectacle_oracle(spectacle_cost_str: str, extra_text: str = "") -> str:
    return (
        f"Spectacle {spectacle_cost_str} (You may cast this spell for its "
        f"spectacle cost rather than its mana cost if an opponent lost life "
        f"this turn.)\n{extra_text}"
    )


def _make_game() -> GameState:
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 2
    return game


def _make_mountain(game: GameState, owner: int = 0) -> CardInstance:
    """A basic Mountain (produces {R})."""
    land_t = CardTemplate(
        name="Mountain",
        card_types=[CardType.LAND],
        mana_cost=ManaCost(),
        supertypes=["Basic"],
        subtypes=["Mountain"],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[],
        color_identity={Color.RED},
        produces_mana=["R"],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )
    return CardInstance(
        template=land_t, owner=owner, controller=owner,
        instance_id=game.next_instance_id(), zone="battlefield",
    )


def _spectacle_card(
    *,
    normal_cost: ManaCost,
    spectacle_cost_val: ManaCost,
    oracle: str = "",
    card_types=None,
) -> CardInstance:
    """Build a CardInstance for a spectacle spell with the given costs."""
    if card_types is None:
        card_types = [CardType.SORCERY]

    if not oracle:
        sym = ""
        if spectacle_cost_val.generic:
            sym += "{" + str(spectacle_cost_val.generic) + "}"
        for _ in range(spectacle_cost_val.black):
            sym += "{B}"
        for _ in range(spectacle_cost_val.red):
            sym += "{R}"
        oracle = _spectacle_oracle(sym)

    template = CardTemplate(
        name="Synthetic Spectacle Spell",
        card_types=card_types,
        mana_cost=normal_cost,
        supertypes=[],
        subtypes=[],
        power=None,
        toughness=None,
        loyalty=None,
        keywords=set(),
        abilities=[],
        color_identity={Color.RED},
        produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle,
        tags=set(),
        spectacle_cost=spectacle_cost_val,
    )
    return CardInstance(template=template, owner=0, controller=0, instance_id=0, zone="hand")


class TestSpectacleCastLegality:
    """can_cast enables spectacle when an opponent lost life this turn."""

    def test_spectacle_available_when_opponent_lost_life(self):
        """Spectacle cost is available when the opponent has lost life this turn.

        CR 702.131b: the caster may pay the spectacle cost if an opponent lost
        life this turn. With only {R} available (not {2}{R}) the spell is only
        castable via the spectacle path.
        """
        game = _make_game()
        p = game.players[0]
        opp = game.players[1]

        # One red land — enough for spectacle {R} but not {2}{R}
        p.battlefield.append(_make_mountain(game))

        # Opponent took damage this turn
        opp.take_damage(3, source=None)
        assert opp.life_lost_this_turn > 0, \
            "precondition: opponent must have lost life"

        card = _spectacle_card(
            normal_cost=ManaCost(generic=2, red=1),
            spectacle_cost_val=ManaCost(red=1),
        )
        p.hand.append(card)

        assert game.can_cast(0, card), \
            "can_cast must return True when spectacle cost is payable and opponent lost life"

    def test_spectacle_unavailable_when_no_opponent_life_loss(self):
        """Spectacle unavailable when no opponent has lost life this turn.

        With only {R} and normal cost {2}{R}, the spell is uncastable.
        """
        game = _make_game()
        p = game.players[0]

        # One red land — can pay {R} but not {2}{R}
        p.battlefield.append(_make_mountain(game))

        # No opponent life loss
        assert game.players[1].life_lost_this_turn == 0

        card = _spectacle_card(
            normal_cost=ManaCost(generic=2, red=1),
            spectacle_cost_val=ManaCost(red=1),
        )
        p.hand.append(card)

        assert not game.can_cast(0, card), \
            "can_cast must return False when spectacle not enabled and normal cost unaffordable"

    def test_spectacle_unavailable_when_cannot_afford_spectacle_cost(self):
        """can_cast returns False when spectacle enabled but cost unaffordable.

        Opponent lost life, but player has no mana to pay spectacle {R}.
        """
        game = _make_game()
        p = game.players[0]
        opp = game.players[1]

        # No lands
        assert len(p.battlefield) == 0

        opp.take_damage(3, source=None)

        card = _spectacle_card(
            normal_cost=ManaCost(generic=2, red=1),
            spectacle_cost_val=ManaCost(red=1),
        )
        p.hand.append(card)

        assert not game.can_cast(0, card), \
            "can_cast must return False when spectacle cost is unaffordable"

    def test_normal_cast_still_works_without_spectacle(self):
        """Normal casting path unaffected when spectacle condition not met.

        With {2}{R} available the spell is castable even without spectacle.
        """
        game = _make_game()
        p = game.players[0]

        # Three mountains — enough for {2}{R}
        for _ in range(3):
            p.battlefield.append(_make_mountain(game))

        # No opponent life loss
        assert game.players[1].life_lost_this_turn == 0

        card = _spectacle_card(
            normal_cost=ManaCost(generic=2, red=1),
            spectacle_cost_val=ManaCost(red=1),
        )
        p.hand.append(card)

        assert game.can_cast(0, card), \
            "Normal cast must work when the player can afford {2}{R}"

    def test_cardtemplate_spectacle_cost_parsed_from_oracle(self):
        """CardTemplate.__post_init__ parses spectacle_cost from oracle text."""
        oracle = (
            "Spectacle {R} (You may cast this spell for its spectacle cost "
            "rather than its mana cost if an opponent lost life this turn.)\n"
            "Skewer the Critics deals 3 damage to any target."
        )
        template = CardTemplate(
            name="Generic Spectacle Sorcery",
            card_types=[CardType.SORCERY],
            mana_cost=ManaCost(generic=2, red=1),
            supertypes=[], subtypes=[],
            power=None, toughness=None, loyalty=None,
            keywords=set(), abilities=[],
            color_identity={Color.RED},
            produces_mana=[],
            enters_tapped=False,
            oracle_text=oracle,
            tags=set(),
        )
        assert template.spectacle_cost is not None, \
            "CardTemplate must parse spectacle_cost from oracle text in __post_init__"
        assert template.spectacle_cost.red == 1
        assert template.spectacle_cost.generic == 0
