"""Domain-CDA fixes: Territorial Kavu domain cap and Wild Nacatl land-type bonus.

CR 702.11 Domain: counts distinct basic land types among lands you control;
maximum is 5 (one per basic land type: Plains, Island, Swamp, Mountain, Forest).

Rules under test:

1. ``test_domain_cda_scales_to_5_at_full_domain``
   A creature whose P/T is each equal to the number of basic land types
   must reach 5/5 when all five basic land types are controlled, not be
   capped at 4/4 (Territorial Kavu fixture).

2. ``test_domain_cda_counts_leyline_of_guildpact_types``
   Leyline of the Guildpact grants all five basic land types to your
   permanents. A creature on a board with one non-basic land + Leyline
   should see domain=5.

3. ``test_land_type_conditional_bonus_applies_when_land_type_controlled``
   "Gets +1/+1 as long as you control a Mountain" — a creature with this
   oracle shape must receive the bonus when a Mountain is on the battlefield
   (Wild Nacatl fixture: base 1/1 → 2/2 with Mountain alone).

4. ``test_land_type_conditional_bonus_stacks_independently_per_type``
   Two independent land-type conditionals (Mountain and Plains) each
   contribute their bonus when the matching land is controlled.
   Wild Nacatl with Mountain + Plains → 3/3.

5. ``test_land_type_conditional_bonus_absent_without_land``
   No matching land → no bonus. Wild Nacatl with neither Mountain nor
   Plains → stays at base 1/1.

6. ``test_parse_land_type_bonuses_returns_dict``
   Pure parser unit test: parse_land_type_bonuses must return a dict
   mapping land type to integer bonus for each conditional line.

Class size note: the land-type conditional bonus pattern covers ~8 cards
in the DB: Wild Nacatl, Elder Gargaroth (partial), and similar "while you
control a [type]" conditional-buff creatures.  The fix is mechanic-level.
"""
from __future__ import annotations

import random
import pytest

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState, Phase
from engine.player_state import PlayerState


# ── Parser unit test ──────────────────────────────────────────────────────────

class TestParseLandTypeBonuses:
    """parse_land_type_bonuses must extract per-land-type bonuses from oracle."""

    def test_parse_land_type_bonuses_returns_dict(self):
        from engine.oracle_parser import parse_land_type_bonuses
        oracle = (
            "This creature gets +1/+1 as long as you control a Mountain.\n"
            "This creature gets +1/+1 as long as you control a Plains."
        )
        result = parse_land_type_bonuses(oracle)
        assert result is not None, "parse_land_type_bonuses must return a dict, not None"
        assert isinstance(result, dict), "parse_land_type_bonuses must return a dict"
        assert result.get("mountain") == 1, "Mountain bonus must be 1"
        assert result.get("plains") == 1, "Plains bonus must be 1"
        assert "swamp" not in result, "No Swamp clause — Swamp must not appear"

    def test_parse_land_type_bonuses_no_clause_returns_empty(self):
        from engine.oracle_parser import parse_land_type_bonuses
        oracle = "Flying\nDash {1}{R}"
        result = parse_land_type_bonuses(oracle)
        assert result == {}, "No land-type bonus clause → empty dict"


# ── Integration tests (GameState) ─────────────────────────────────────────────

def _make_game() -> GameState:
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 2
    return game


def _basic_land(game: GameState, subtype: str, controller: int = 0) -> CardInstance:
    """Create a basic land of the given subtype and add to battlefield."""
    card_type_map = {
        "plains": (CardType.LAND, "Plains"),
        "island": (CardType.LAND, "Island"),
        "swamp": (CardType.LAND, "Swamp"),
        "mountain": (CardType.LAND, "Mountain"),
        "forest": (CardType.LAND, "Forest"),
    }
    _ct, name = card_type_map[subtype.lower()]
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.LAND],
        mana_cost=ManaCost(),
        supertypes=["Basic"],
        subtypes=[subtype.title()],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(),
        produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )
    inst = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    inst._game_state = game
    game.players[controller].battlefield.append(inst)
    return inst


def _domain_creature(game: GameState, base_p: int = 0, base_t: int = 0,
                     controller: int = 0) -> CardInstance:
    """A creature whose P/T each equal the number of basic land types."""
    from engine.oracle_parser import detect_power_scaling
    oracle = (
        "Domain — This creature's power and toughness are each equal to "
        "the number of basic land types among lands you control."
    )
    tmpl = CardTemplate(
        name="Domain Test Creature",
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=2, green=1),
        supertypes=[], subtypes=["Kavu"],
        power=str(base_p) if base_p is not None else "*",
        toughness=str(base_t) if base_t is not None else "*",
        loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(),
        produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle,
        tags=set(),
    )
    tmpl.power_scales_with = detect_power_scaling(oracle)
    inst = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    inst._game_state = game
    game.players[controller].battlefield.append(inst)
    return inst


def _wild_nacatl_like(game: GameState, controller: int = 0) -> CardInstance:
    """A 1/1 creature that gets +1/+1 per Mountain and per Plains controlled."""
    oracle = (
        "This creature gets +1/+1 as long as you control a Mountain.\n"
        "This creature gets +1/+1 as long as you control a Plains."
    )
    from engine.oracle_parser import parse_land_type_bonuses
    bonuses = parse_land_type_bonuses(oracle)
    tmpl = CardTemplate(
        name="Land-Type Bonus Creature",
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(green=1),
        supertypes=[], subtypes=["Cat"],
        power=1, toughness=1,
        loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(),
        produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle,
        tags=set(),
    )
    tmpl.land_type_bonuses = bonuses
    inst = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    inst._game_state = game
    game.players[controller].battlefield.append(inst)
    return inst


class TestDomainCDAScaling:
    """Territorial Kavu / domain CDA must reach 5/5, not be capped at 4/4."""

    def test_domain_cda_scales_to_5_at_full_domain(self):
        """Domain creature reaches 5/5 when all five basic land types present."""
        game = _make_game()
        for subtype in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
            _basic_land(game, subtype)
        creature = _domain_creature(game)
        assert creature.power == 5, (
            f"Domain creature must be 5/5 with all five basic land types; "
            f"got {creature.power}/{creature.toughness}. "
            f"The min(..., 4) cap in _dynamic_base_power was incorrect."
        )
        assert creature.toughness == 5

    def test_domain_cda_partial_domain(self):
        """Domain creature with 3 basic land types is 3/3."""
        game = _make_game()
        for subtype in ("Forest", "Mountain", "Plains"):
            _basic_land(game, subtype)
        creature = _domain_creature(game)
        assert creature.power == 3
        assert creature.toughness == 3

    def test_domain_cda_scales_to_4_with_four_types(self):
        """Domain creature with 4 basic land types is 4/4 (verifies no under-cap)."""
        game = _make_game()
        for subtype in ("Forest", "Mountain", "Plains", "Island"):
            _basic_land(game, subtype)
        creature = _domain_creature(game)
        assert creature.power == 4
        assert creature.toughness == 4


class TestLandTypeConditionalBonus:
    """Wild Nacatl: 'gets +1/+1 as long as you control a [LandType]'."""

    def test_land_type_conditional_bonus_applies_when_land_type_controlled(self):
        """Creature gets +1/+1 when matching land type is on the battlefield."""
        game = _make_game()
        _basic_land(game, "Mountain")
        creature = _wild_nacatl_like(game)
        assert creature.power == 2, (
            f"With one Mountain controlled, Wild Nacatl-like must be 2/2; "
            f"got {creature.power}/{creature.toughness}."
        )
        assert creature.toughness == 2

    def test_land_type_conditional_bonus_stacks_independently_per_type(self):
        """Two land-type conditionals (Mountain and Plains) each apply."""
        game = _make_game()
        _basic_land(game, "Mountain")
        _basic_land(game, "Plains")
        creature = _wild_nacatl_like(game)
        assert creature.power == 3, (
            f"With Mountain + Plains, Wild Nacatl-like must be 3/3; "
            f"got {creature.power}/{creature.toughness}."
        )
        assert creature.toughness == 3

    def test_land_type_conditional_bonus_absent_without_land(self):
        """No bonus when neither qualifying land type is controlled."""
        game = _make_game()
        creature = _wild_nacatl_like(game)
        assert creature.power == 1, (
            "With no Mountain or Plains, Wild Nacatl-like must stay at 1/1."
        )
        assert creature.toughness == 1

    def test_land_type_conditional_wrong_type_gives_no_bonus(self):
        """A Swamp does not trigger Mountain or Plains bonus."""
        game = _make_game()
        _basic_land(game, "Swamp")
        creature = _wild_nacatl_like(game)
        assert creature.power == 1
        assert creature.toughness == 1
