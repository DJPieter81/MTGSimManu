"""Alternative-cost parsers must return ManaCost objects, not raw CMC integers.

Also covers splice-onto-Arcane, which has the same root cause:
``parse_splice_cost`` returned ``Optional[int]`` (total CMC), so the splice
payment in ``cast_spell`` was hardcoded as ``ManaCost(generic=N-1, red=1)``
— a card-specific assumption invisible to the engine.  After the fix,
``parse_splice_cost`` returns a ``ManaCost`` and the payment uses it directly.

Root cause of the alternative-cost color-blindness class (Track H):

  ``parse_dash_cost`` returned ``Optional[int]`` — a plain CMC value that
  loses all color information.  Dash {1}{R} → 2 (indistinguishable from
  {2}), so ``can_cast`` did ``total_mana >= 2`` without checking that the
  {R} pip requires a red source.  ``cast_spell`` then hardcoded the
  payment as ``ManaCost(generic=template.dash_cost - 1, red=1)  # {1}{R}
  for Ragavan`` — a direct card-name comment in engine code.

  ``parse_escape_cost`` had the same bug: the dict's ``'cmc'`` key held a
  plain integer, not a ``ManaCost``.  ``cast_spell`` patched it with
  ``ManaCost(red=2, white=2)  # Phlage-specific``.

Both parsers must return ``ManaCost`` objects so that:
  - ``can_cast`` can run the full MRV colour check against the alternative
    cost (not just a quantity gate).
  - ``cast_spell`` can pass ``template.dash_cost`` / ``template.escape_cost``
    directly to ``tap_lands_for_mana`` without any per-card knowledge.

Rules, phrased on the mechanic:

1. ``parse_dash_cost`` returns a ``ManaCost`` object preserving colour pips.
2. ``parse_escape_cost`` includes a ``ManaCost`` value in its result dict.
3. ``can_cast`` rejects a dash spell when the coloured pip has no real source.
4. ``can_cast`` rejects an escape spell when the coloured pip has no real source.
"""
from __future__ import annotations

import random

import pytest

from engine.oracle_parser import parse_dash_cost, parse_escape_cost, parse_splice_cost
from engine.mana import ManaCost
from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState, Phase


# ── oracle-parser unit tests (pure, no DB needed) ────────────────────────────

class TestParseDashCostPreservesColor:
    """parse_dash_cost must return ManaCost, not int."""

    def test_generic_plus_red_dash_has_correct_pip(self):
        cost = parse_dash_cost("Dash {1}{R} (You may cast this card for its dash cost.)")
        assert cost is not None
        assert hasattr(cost, 'red'), (
            "parse_dash_cost must return ManaCost — returned int loses color info"
        )
        assert cost.red == 1
        assert cost.generic == 1

    def test_pure_red_dash_has_correct_pip(self):
        cost = parse_dash_cost("Dash {R} (You may cast this card for its dash cost.)")
        assert cost is not None
        assert cost.red == 1
        assert cost.generic == 0

    def test_no_dash_returns_none(self):
        assert parse_dash_cost("Flying\nWhen this creature enters, draw a card.") is None

    def test_case_insensitive(self):
        cost = parse_dash_cost("dash {1}{R}")
        assert cost is not None
        assert cost.red == 1


class TestParseEscapeCostPreservesColor:
    """parse_escape_cost must include a ManaCost in its result."""

    def test_quad_colored_escape_cost_is_mana_cost(self):
        data = parse_escape_cost(
            "Escape—{R}{R}{W}{W}, Exile four other cards from your graveyard."
        )
        assert data is not None
        cost = data.get('cost')
        assert cost is not None, (
            "parse_escape_cost result must have 'cost' key with ManaCost — "
            "found only 'cmc' (int) which loses colour info"
        )
        assert hasattr(cost, 'red'), "escape cost must be a ManaCost object"
        assert cost.red == 2
        assert cost.white == 2

    def test_exile_count_still_present(self):
        data = parse_escape_cost(
            "Escape—{G}{G}{U}{U}, Exile five other cards from your graveyard."
        )
        assert data is not None
        assert data.get('exile') == 5

    def test_generic_escape_cost_preserved(self):
        data = parse_escape_cost(
            "Escape—{3}{G}, Exile two other cards from your graveyard."
        )
        assert data is not None
        cost = data['cost']
        assert cost.green == 1
        assert cost.generic == 3


# ── can_cast behaviour tests (colour-blindness probes) ───────────────────────

_DASH_ORACLE = (
    "Dash {1}{R} (You may cast this card for its dash cost. "
    "If you do, it gains haste, and it's returned from the battlefield "
    "to its owner's hand at the beginning of the next end step.)"
)

_ESCAPE_ORACLE = (
    "Escape—{R}{R}{W}{W}, Exile four other cards from your graveyard. "
    "(You may cast this card from your graveyard for its escape cost.)"
)


def _fresh_game() -> GameState:
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "SyntheticDash"
    game.players[1].deck_name = "SyntheticOpponent"
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 3
    return game


def _template(name: str, *, generic: int, card_types,
              oracle_text: str = "", power=None, toughness=None) -> CardTemplate:
    return CardTemplate(
        name=name,
        card_types=list(card_types),
        mana_cost=ManaCost(generic=generic),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(),
        produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle_text,
        tags=set(),
    )


def _add_colorless_land(game: GameState, player_idx: int, count: int = 1):
    from engine.cards import CardTemplate, CardType, ManaCost, CardInstance
    for i in range(count):
        t = CardTemplate(
            name=f"Colorless Land {i}", card_types=[CardType.LAND],
            mana_cost=ManaCost(), supertypes=[], subtypes=[],
            power=None, toughness=None, loyalty=None,
            keywords=set(), abilities=[],
            color_identity=set(), produces_mana=["C"],
            enters_tapped=False, oracle_text="", tags=set(),
        )
        card = CardInstance(
            template=t, owner=player_idx, controller=player_idx,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        card._game_state = game
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[player_idx].battlefield.append(card)


def _add_to_graveyard(game: GameState, tmpl: CardTemplate,
                      player_idx: int) -> CardInstance:
    card = CardInstance(
        template=tmpl, owner=player_idx, controller=player_idx,
        instance_id=game.next_instance_id(), zone="graveyard",
    )
    card._game_state = game
    game.players[player_idx].graveyard.append(card)
    return card


def test_dash_color_pip_requires_real_colored_source():
    """can_cast must reject dash {1}{R} when only colorless mana is available.

    Root cause: dash_cost stored as int (CMC=2) loses the {R} constraint.
    Quantity check total_mana >= 2 passes for 2 colorless lands, but the
    player cannot actually pay the {R} pip — cast_spell then fails silently.
    """
    game = _fresh_game()
    _add_colorless_land(game, 0, count=2)  # 2 mana available, no red source

    t = _template("Synthetic Dash Creature", generic=3,
                  card_types=[CardType.CREATURE],
                  oracle_text=_DASH_ORACLE, power=2, toughness=1)
    # Normal cost {3}; dash cost {1}{R} — populated by __post_init__ via parse_dash_cost
    # Verify dash_cost was populated
    assert t.dash_cost is not None, "CardTemplate.__post_init__ must populate dash_cost"

    card = CardInstance(
        template=t, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[0].hand.append(card)

    assert not game.can_cast(0, card), (
        "dash {1}{R} requires a red source; 2 colorless lands can cover the "
        "CMC but not the coloured pip — color-blind int check returns True (wrong)"
    )


def test_dash_castable_when_colored_pip_has_real_source():
    """can_cast allows dash {1}{R} when a red-producing land is present."""
    from engine.cards import CardTemplate, CardType, ManaCost, CardInstance
    game = _fresh_game()

    # Add a red-producing land
    t_land = CardTemplate(
        name="Synthetic Mountain", card_types=[CardType.LAND],
        mana_cost=ManaCost(), supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=["R"],
        enters_tapped=False, oracle_text="", tags=set(),
    )
    land = CardInstance(
        template=t_land, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    land._game_state = game
    land.enter_battlefield()
    land.summoning_sick = False
    game.players[0].battlefield.append(land)

    # Add a colorless land for the generic pip
    _add_colorless_land(game, 0, count=1)

    t = _template("Synthetic Dash Creature", generic=3,
                  card_types=[CardType.CREATURE],
                  oracle_text=_DASH_ORACLE, power=2, toughness=1)
    card = CardInstance(
        template=t, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[0].hand.append(card)

    assert game.can_cast(0, card), (
        "dash {1}{R} must be castable with 1 red land + 1 colorless land"
    )


def test_escape_color_pip_requires_real_colored_source():
    """can_cast must reject escape from graveyard when colored pips lack sources.

    Root cause: escape_cost stored as int (CMC only) loses {R}{R}{W}{W}.
    Quantity check total_mana >= 4 passes for 4 colorless lands, but the
    player cannot pay the {R}{R}{W}{W} pips — cast_spell then fails silently.
    """
    game = _fresh_game()
    _add_colorless_land(game, 0, count=4)  # 4 mana, no red/white source

    # Escape spell template (escape_cost populated from oracle by parse_escape_cost)
    t = _template("Synthetic Escape Titan", generic=5,
                  card_types=[CardType.CREATURE],
                  oracle_text=_ESCAPE_ORACLE, power=6, toughness=6)
    assert t.escape_cost is not None, (
        "CardTemplate must populate escape_cost from oracle text"
    )

    escape_card = _add_to_graveyard(game, t, 0)

    # Add 4 cheap filler cards to the graveyard for exile
    filler = _template("Filler", generic=1, card_types=[CardType.INSTANT])
    for _ in range(4):
        _add_to_graveyard(game, filler, 0)

    assert not game.can_cast(0, escape_card), (
        "escape {R}{R}{W}{W} requires red and white sources; 4 colorless lands "
        "cover the CMC but not the coloured pips — color-blind int check returns True (wrong)"
    )


class TestParseSpliceCostPreservesColor:
    """parse_splice_cost must return ManaCost, not int."""

    def test_red_splice_has_correct_pip(self):
        cost = parse_splice_cost(
            "Add {R}{R}{R}.\nSplice onto Arcane {1}{R}"
        )
        assert cost is not None, "splice cost must be parsed from oracle"
        assert hasattr(cost, 'red'), (
            "parse_splice_cost must return ManaCost — returned int loses color info"
        )
        assert cost.red == 1
        assert cost.generic == 1

    def test_white_splice_has_correct_pip(self):
        cost = parse_splice_cost(
            "Prevent the next 3 damage. Splice onto Arcane {2}{W}"
        )
        assert cost is not None
        assert hasattr(cost, 'white')
        assert cost.white == 1
        assert cost.generic == 2

    def test_no_splice_returns_none(self):
        assert parse_splice_cost("Flying\nWhen this enters, draw a card.") is None

    def test_splice_cost_returns_mana_cost_not_int(self):
        cost = parse_splice_cost("Splice onto Arcane {1}{R}")
        assert cost is not None
        assert not isinstance(cost, int), (
            "parse_splice_cost must return ManaCost, not int — "
            "int loses all colour information"
        )


def test_escape_castable_when_colored_pips_have_real_sources():
    """can_cast allows escape when both red and white sources are present."""
    from engine.cards import CardTemplate, CardType, ManaCost, CardInstance
    game = _fresh_game()

    for color in ["R", "R", "W", "W"]:
        t_land = CardTemplate(
            name=f"Synthetic {color} Land", card_types=[CardType.LAND],
            mana_cost=ManaCost(), supertypes=[], subtypes=[],
            power=None, toughness=None, loyalty=None,
            keywords=set(), abilities=[],
            color_identity=set(), produces_mana=[color],
            enters_tapped=False, oracle_text="", tags=set(),
        )
        land = CardInstance(
            template=t_land, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        land._game_state = game
        land.enter_battlefield()
        land.summoning_sick = False
        game.players[0].battlefield.append(land)

    t = _template("Synthetic Escape Titan", generic=5,
                  card_types=[CardType.CREATURE],
                  oracle_text=_ESCAPE_ORACLE, power=6, toughness=6)

    escape_card = _add_to_graveyard(game, t, 0)
    filler = _template("Filler", generic=1, card_types=[CardType.INSTANT])
    for _ in range(4):
        _add_to_graveyard(game, filler, 0)

    assert game.can_cast(0, escape_card), (
        "escape {R}{R}{W}{W} must be castable with 2 red + 2 white lands"
    )
