"""Tests for the Warp alternative-cast mechanic (CR 702.Warp).

Rule: A creature with Warp {X} may be cast from hand for {X} instead of its
normal mana cost, provided the caster controls at least one artifact.  Warped
creatures are exiled at the beginning of the next end step; they may be cast
again from exile on a later turn.

Fixture card: Pinnacle Emissary (Warp {U/R}, normal cost {1}{U}{R}) — the
specific card whose infinite-loop regression is being fixed.  Class size: 33
cards with Warp in the DB.
"""

import pytest
from engine.oracle_parser import parse_warp_cost
from engine.mana import ManaCost


# ---------------------------------------------------------------------------
# 1. Parser unit tests
# ---------------------------------------------------------------------------

class TestParseWarpCost:
    """oracle_parser.parse_warp_cost returns the Warp alternative cost."""

    def test_hybrid_warp_returns_one_generic(self):
        # "Warp {U/R}" — one hybrid mana, treated as generic=1 so any land pays
        cost = parse_warp_cost(
            "Warp {U/R} (You may cast this card from your hand for its warp cost.)"
        )
        assert cost is not None
        assert cost.cmc == 1

    def test_pure_generic_warp(self):
        # "Warp {3}" — three generic mana
        cost = parse_warp_cost(
            "Warp {3} (You may cast this card from your hand for its warp cost.)"
        )
        assert cost is not None
        assert cost.cmc == 3
        assert cost.generic == 3

    def test_mixed_warp_one_generic_one_green(self):
        # "Warp {1}{G}" — one generic + one green
        cost = parse_warp_cost(
            "Warp {1}{G} (You may cast this card from your hand for its warp cost.)"
        )
        assert cost is not None
        assert cost.cmc == 2
        assert cost.green == 1
        assert cost.generic == 1

    def test_no_warp_returns_none(self):
        # Ordinary creature oracle — must NOT produce a cost
        assert parse_warp_cost(
            "Flying\nWhen this creature enters, draw a card."
        ) is None

    def test_case_insensitive(self):
        cost = parse_warp_cost("warp {2}")
        assert cost is not None
        assert cost.cmc == 2

    def test_warp_cost_less_than_normal(self):
        # Pinnacle Emissary: Warp {U/R} (cmc=1) vs normal {1}{U}{R} (cmc=3)
        warp = parse_warp_cost(
            "Warp {U/R} (You may cast this card from your hand for its warp cost. "
            "Exile this creature at the beginning of the next end step, then you "
            "may cast it from exile on a later turn.)"
        )
        assert warp is not None
        assert warp.cmc < 3  # strictly cheaper than normal {1}{U}{R}


# ---------------------------------------------------------------------------
# 2. CardTemplate warp_cost field
# ---------------------------------------------------------------------------

class TestCardTemplateWarpCost:
    """CardTemplate.warp_cost is populated from oracle text at load time."""

    @pytest.fixture(scope="class")
    def db(self):
        from engine.card_database import CardDatabase
        return CardDatabase()

    def test_pinnacle_emissary_has_warp_cost(self, db):
        t = db.cards.get("Pinnacle Emissary")
        assert t is not None, "Pinnacle Emissary not in DB"
        assert t.warp_cost is not None, "warp_cost should be populated from oracle"
        assert t.warp_cost.cmc == 1

    def test_non_warp_card_has_no_warp_cost(self, db):
        t = db.cards.get("Lightning Bolt")
        assert t is not None
        assert t.warp_cost is None


# ---------------------------------------------------------------------------
# 3. can_cast legality: Warp requires (a) has artifact, (b) warp cost payable
# ---------------------------------------------------------------------------

def _make_card(game, template, owner=0, zone="hand"):
    """Create a CardInstance backed by a real template and wire it to the game."""
    from engine.cards import CardInstance
    card = CardInstance(
        template=template, owner=owner, controller=owner,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    return card


def _add_to_zone(game, card, player_idx, zone):
    p = game.players[player_idx]
    card.zone = zone
    if zone == "hand":
        p.hand.append(card)
    elif zone == "battlefield":
        p.battlefield.append(card)
    elif zone == "exile":
        p.exile.append(card)


def _add_mana(game, player_idx, **colors):
    """Add mana directly to the player's mana pool via add()."""
    pool = game.players[player_idx].mana_pool
    for color, amount in colors.items():
        pool.add(color.upper(), amount)


@pytest.fixture(scope="module")
def db():
    from engine.card_database import CardDatabase
    return CardDatabase()


@pytest.fixture
def fresh_game():
    import random
    from engine.game_state import GameState, Phase
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


class TestCanCastWarp:
    """can_cast returns True only when Warp prerequisites are met."""

    def test_can_cast_via_warp_with_artifact_and_mana(self, fresh_game, db):
        game = fresh_game
        t_emissary = db.cards["Pinnacle Emissary"]
        t_artifact = db.cards["Mox Opal"]

        emissary = _make_card(game, t_emissary)
        _add_to_zone(game, emissary, 0, "hand")

        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        # 1 blue mana — satisfies ManaCost(generic=1) Warp cost
        _add_mana(game, 0, U=1)

        assert game.can_cast(0, emissary), (
            "can_cast should return True: has artifact + 1 mana covers Warp {U/R}"
        )

    def test_cannot_cast_via_warp_without_artifact(self, fresh_game, db):
        game = fresh_game
        t_emissary = db.cards["Pinnacle Emissary"]

        emissary = _make_card(game, t_emissary)
        _add_to_zone(game, emissary, 0, "hand")
        # No artifact on battlefield; 1 mana available but not enough for normal cost
        _add_mana(game, 0, U=1)

        assert not game.can_cast(0, emissary), (
            "can_cast must return False: no artifact on battlefield for Warp"
        )

    def test_cannot_cast_via_warp_without_mana(self, fresh_game, db):
        game = fresh_game
        t_emissary = db.cards["Pinnacle Emissary"]
        t_artifact = db.cards["Mox Opal"]

        emissary = _make_card(game, t_emissary)
        _add_to_zone(game, emissary, 0, "hand")

        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")
        # 0 mana — cannot pay Warp {U/R} (cmc=1)

        assert not game.can_cast(0, emissary), (
            "can_cast must return False: 0 mana, cannot pay warp cost"
        )


# ---------------------------------------------------------------------------
# 4. cast_spell pays the Warp cost (not the normal cost) — the actual bug
# ---------------------------------------------------------------------------

class TestCastSpellWarpPayment:
    """cast_spell succeeds by paying Warp cost when normal cost is not payable."""

    def test_warp_cast_succeeds_when_only_warp_cost_payable(self, fresh_game, db):
        """The critical regression case.

        Board: only 1 mana of any color available (covers Warp {U/R} = cmc 1)
        but NOT 3 mana in the right colors for the normal cost {1}{U}{R}.
        Expect: cast succeeds, card leaves hand.
        """
        game = fresh_game
        game.active_player = 0

        t_emissary = db.cards["Pinnacle Emissary"]
        t_artifact = db.cards["Mox Opal"]

        emissary = _make_card(game, t_emissary)
        _add_to_zone(game, emissary, 0, "hand")

        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        # Only 1 blue mana — pays Warp {U/R} (generic=1) but NOT normal {1}{U}{R}
        _add_mana(game, 0, U=1)

        result = game.cast_spell(0, emissary)
        assert result is True, (
            "cast_spell should succeed via Warp cost when normal cost is unaffordable"
        )
        assert emissary not in game.players[0].hand, (
            "Pinnacle Emissary must leave hand after successful cast"
        )

    def test_warp_cast_marks_card_as_warped(self, fresh_game, db):
        """A Warp-cast creature is marked _warped so it can be exiled at end of turn."""
        game = fresh_game
        game.active_player = 0

        t_emissary = db.cards["Pinnacle Emissary"]
        t_artifact = db.cards["Mox Opal"]

        emissary = _make_card(game, t_emissary)
        _add_to_zone(game, emissary, 0, "hand")

        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        _add_mana(game, 0, R=1)  # 1 red satisfies generic-1 Warp cost

        game.cast_spell(0, emissary)
        # Resolve the stack so the creature enters the battlefield
        while not game.stack.is_empty:
            game.resolve_stack()

        bf_names = [c.name for c in game.players[0].battlefield]
        assert "Pinnacle Emissary" in bf_names, (
            "creature should be on battlefield after Warp cast"
        )
        warped_card = next(
            c for c in game.players[0].battlefield if c.name == "Pinnacle Emissary"
        )
        assert getattr(warped_card, "_warped", False), (
            "_warped flag should be set on Warp-cast creature"
        )

    def test_warp_creature_exiled_at_end_of_turn(self, fresh_game, db):
        """A Warp-cast permanent is exiled at the beginning of the next end step."""
        game = fresh_game
        game.active_player = 0

        t_emissary = db.cards["Pinnacle Emissary"]
        t_artifact = db.cards["Mox Opal"]

        emissary = _make_card(game, t_emissary)
        _add_to_zone(game, emissary, 0, "hand")

        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        _add_mana(game, 0, U=1)

        game.cast_spell(0, emissary)
        while not game.stack.is_empty:
            game.resolve_stack()

        p = game.players[0]
        assert any(c.name == "Pinnacle Emissary" for c in p.battlefield), (
            "Warp creature should be on battlefield before end step"
        )

        from engine.turn_manager import TurnManager
        tm = TurnManager()
        tm.end_of_turn_cleanup(game)

        assert not any(c.name == "Pinnacle Emissary" for c in p.battlefield), (
            "Warp creature must leave battlefield at end of turn"
        )
        assert any(c.name == "Pinnacle Emissary" for c in p.exile), (
            "Warp creature must be in exile after end-of-turn cleanup"
        )

    def test_warp_color_pip_requires_real_colored_source(self, fresh_game, db):
        """can_cast must reject warp {1}{W} when only colorless mana is available.

        All-Fates Stalker has Warp {1}{W} — two mana but the {W} pip requires
        a white source.  Colorless-only lands cover the CMC (2) but not the
        colour pip.  The colour-blind quantity check returns True (wrong).
        """
        game = fresh_game
        # Warp card with a real white pip in warp cost
        t_stalker = db.cards.get("All-Fates Stalker")
        if t_stalker is None or t_stalker.warp_cost is None:
            pytest.skip("All-Fates Stalker not in DB or has no warp cost")
        if t_stalker.warp_cost.white == 0:
            pytest.skip("All-Fates Stalker warp cost has no white pip — test needs updating")

        stalker = _make_card(game, t_stalker)
        _add_to_zone(game, stalker, 0, "hand")

        t_artifact = db.cards["Mox Opal"]
        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        # Enough colorless mana to cover CMC of warp_cost but no white source
        cmc = t_stalker.warp_cost.cmc
        for i in range(cmc):
            from engine.cards import CardTemplate, CardType, CardInstance
            t_land = CardTemplate(
                name=f"Colorless Land {i}", card_types=[CardType.LAND],
                mana_cost=ManaCost(), supertypes=[], subtypes=[],
                power=None, toughness=None, loyalty=None,
                keywords=set(), abilities=[],
                color_identity=set(), produces_mana=["C"],
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

        assert not game.can_cast(0, stalker), (
            "warp {1}{W} requires a white source; colorless lands cover the CMC "
            "but not the {W} pip — colour-blind quantity check returns True (wrong)"
        )

    def test_warp_castable_with_correct_colored_source(self, fresh_game, db):
        """can_cast allows warp {1}{W} when a white-producing land is present."""
        game = fresh_game
        t_stalker = db.cards.get("All-Fates Stalker")
        if t_stalker is None or t_stalker.warp_cost is None or t_stalker.warp_cost.white == 0:
            pytest.skip("All-Fates Stalker not in DB or wrong warp cost shape")

        stalker = _make_card(game, t_stalker)
        _add_to_zone(game, stalker, 0, "hand")

        t_artifact = db.cards["Mox Opal"]
        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        # Add a white land + colorless for generic
        from engine.cards import CardTemplate, CardType, CardInstance
        for color in ["W", "C"]:
            t_land = CardTemplate(
                name=f"Synth {color} Land", card_types=[CardType.LAND],
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

        assert game.can_cast(0, stalker), (
            "warp {1}{W} must be castable with 1 white land + 1 colorless land"
        )

    def test_warp_exile_recast_color_pip_requires_real_source(self, fresh_game, db):
        """can_cast rejects exile-recast of warp card when colored pip unmet.

        All-Fates Stalker in exile with _warped=True; player has an artifact
        but only colorless mana — the white pip in warp {1}{W} is unmet.
        """
        game = fresh_game
        t_stalker = db.cards.get("All-Fates Stalker")
        if t_stalker is None or t_stalker.warp_cost is None or t_stalker.warp_cost.white == 0:
            pytest.skip("All-Fates Stalker not in DB or wrong warp cost shape")

        stalker = _make_card(game, t_stalker, zone="exile")
        stalker._warped = True
        _add_to_zone(game, stalker, 0, "exile")

        t_artifact = db.cards["Mox Opal"]
        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        # Colorless mana covers CMC but not the white pip
        from engine.cards import CardTemplate, CardType, CardInstance
        cmc = t_stalker.warp_cost.cmc
        for i in range(cmc):
            t_land = CardTemplate(
                name=f"Colorless Land {i}", card_types=[CardType.LAND],
                mana_cost=ManaCost(), supertypes=[], subtypes=[],
                power=None, toughness=None, loyalty=None,
                keywords=set(), abilities=[],
                color_identity=set(), produces_mana=["C"],
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

        assert not game.can_cast(0, stalker), (
            "exile-recast warp {1}{W} must fail when only colorless mana available"
        )

    def test_normal_cast_not_interrupted_when_warp_available(self, fresh_game, db):
        """If the player can afford the normal cost, the normal cast proceeds (no warp)."""
        game = fresh_game
        game.active_player = 0

        t_emissary = db.cards["Pinnacle Emissary"]
        t_artifact = db.cards["Mox Opal"]

        emissary = _make_card(game, t_emissary)
        _add_to_zone(game, emissary, 0, "hand")

        artifact = _make_card(game, t_artifact, zone="battlefield")
        _add_to_zone(game, artifact, 0, "battlefield")

        # Full 3-mana for normal cost {1}{U}{R}: 1 generic (colorless) + 1 blue + 1 red
        _add_mana(game, 0, U=1, R=1, C=1)

        result = game.cast_spell(0, emissary)
        assert result is True
        # Card on stack should NOT be warped (normal cast)
        stack_items = [item.source for item in game.stack.items]
        warped_on_stack = any(
            getattr(c, "_warped", False)
            for c in stack_items
            if hasattr(c, "name") and c.name == "Pinnacle Emissary"
        )
        assert not warped_on_stack, "Normal cast must not set _warped flag"
