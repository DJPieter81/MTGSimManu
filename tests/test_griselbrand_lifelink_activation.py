"""GV2-6 — Lifelink offsets the safe-life threshold in _activate_pay_life_draw.

Diagnostic: `engine/game_runner.py::_activate_pay_life_draw` computes a
`safe_life` threshold that treats the pay-life cost as a permanent life
deduction. A Griselbrand with lifelink attacking for 7 will regain 7 life
on combat damage, so a 7-life activation effectively nets zero life lost
over the turn — but the generic threshold refuses to activate when the
controller only has `life_cost + 1` life remaining.

Fix (GV2-6): when the source creature has LIFELINK AND will attack this
turn (untapped, not summoning sick or has haste), project
`projected_life_after_attack = life - activation_cost + creature_power`
and use that as the safety threshold. This unlocks the activation when
combat damage will restore the life cost.

Tests:
1. Griselbrand (lifelink, haste) at 8 life under opp pressure (6 power)
   → AI activates despite strict threshold refusing (safe_life=9 > 8).
2. Hypothetical Griselbrand with lifelink stripped at the same 8 life
   vs 6 opp power → AI does NOT activate (strict threshold fires).
3. Griselbrand lifelink at 6 life (would die from the 7-life cost)
   → AI does NOT activate (creature must survive to attack).
4. Regression: a non-lifelink pay_life_draw creature (Yawgmoth, Thran
   Physician — no lifelink) activates with the unchanged conservative
   threshold.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance, Keyword
from engine.game_state import GameState
from engine.game_runner import GameRunner


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller, *, tapped=False,
                        summoning_sick=False, strip_keywords=None):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = summoning_sick
    card.tapped = tapped
    if strip_keywords:
        # Simulate a keyword-stripping effect (Humility, Ovinize) by
        # overriding the keywords property on this instance. The fix must
        # use keywords (template|temp) rather than raw oracle text so it
        # respects in-game keyword removal.
        base_keywords = set(tmpl.keywords) - set(strip_keywords)
        card.__class__ = type(
            "StrippedKeywordsCardInstance",
            (CardInstance,),
            {"keywords": property(lambda self: base_keywords | self.temp_keywords)},
        )
    game.players[controller].battlefield.append(card)
    return card


def _make_runner_and_game(card_db, *, my_life, opp_life, opp_power=0,
                          hand_size=4):
    game = GameState(rng=random.Random(0))
    game.players[0].life = my_life
    game.players[1].life = opp_life
    # Give the controller a non-tiny hand — the activation routine has a
    # "desperate" branch at hand<=2 that lowers the safety threshold.
    # These tests exercise the PRIMARY threshold path.
    for _ in range(hand_size):
        tmpl = card_db.get_card("Plains")
        card = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="hand",
        )
        card._game_state = game
        game.players[0].hand.append(card)
    # Library to draw from (7 cards).
    for _ in range(30):
        tmpl = card_db.get_card("Plains")
        card = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="library",
        )
        card._game_state = game
        game.players[0].library.append(card)
    # Opponent body to produce `opp_power` power (1-power Memnites).
    if opp_power > 0:
        for _ in range(opp_power):
            tmpl = card_db.get_card("Memnite") or card_db.get_card("Grizzly Bears")
            assert tmpl is not None
            c = CardInstance(
                template=tmpl, owner=1, controller=1,
                instance_id=game.next_instance_id(), zone="battlefield",
            )
            c._game_state = game
            c.enter_battlefield()
            c.summoning_sick = False
            game.players[1].battlefield.append(c)
    runner = GameRunner(card_db=card_db, rng=random.Random(0))
    return runner, game


class TestGriselbrandLifelinkThreshold:
    """GV2-6 — lifelink on the source creature offsets the pay-life cost
    in the safe-life threshold used by _activate_pay_life_draw."""

    def test_griselbrand_lifelink_haste_at_8_life_activates(self, card_db):
        """Griselbrand (lifelink, haste) at 8 life with 6 opp power on
        board. Strict threshold demands 9 life (max(opp_power+3=9,
        cost+1=8)); pre-fix refuses to activate (life=8 < 9). Post-fix:
        lifelink+7 power projects post-combat life = 8-7+7 = 8, which
        comfortably covers opp's 6 attack damage — AI must activate."""
        runner, game = _make_runner_and_game(
            card_db, my_life=8, opp_life=20, opp_power=6,
        )
        gris = _add_to_battlefield(
            game, card_db, "Griselbrand", controller=0,
            tapped=False, summoning_sick=False,
        )
        assert Keyword.LIFELINK in gris.keywords
        assert gris.can_attack, "Griselbrand should be able to attack"

        starting_life = game.players[0].life
        starting_hand = len(game.players[0].hand)
        runner._activate_pay_life_draw(game, active=0)

        assert game.players[0].life < starting_life, (
            f"Lifelink Griselbrand at 8 life vs opp_power=6 should "
            f"activate (lifelink projects post-combat life = 8-7+7 = 8, "
            f"safely covering opp's 6 attack). Pre-fix strict threshold "
            f"max(9,8)=9 blocked activation. life unchanged at "
            f"{game.players[0].life}."
        )
        assert len(game.players[0].hand) > starting_hand, (
            f"Expected to draw cards; hand size unchanged at {starting_hand}."
        )

    def test_non_lifelink_pay_life_draw_at_8_life_vs_pressure(self, card_db):
        """Mirror of test 1 with lifelink stripped (Humility-like). At
        8 life vs opp_power=6, paying 7 is a permanent -7 (no combat
        offset). The strict threshold of 9 must still fire → ZERO
        activations. Confirms the lifelink projection is the ONLY reason
        the first test activates."""
        runner, game = _make_runner_and_game(
            card_db, my_life=8, opp_life=20, opp_power=6,
        )
        gris = _add_to_battlefield(
            game, card_db, "Griselbrand", controller=0,
            tapped=False, summoning_sick=False,
            strip_keywords={Keyword.LIFELINK},
        )
        assert Keyword.LIFELINK not in gris.keywords, (
            "Test setup failed: lifelink should be stripped for this test"
        )

        starting_life = game.players[0].life
        starting_hand = len(game.players[0].hand)
        runner._activate_pay_life_draw(game, active=0)

        # Without lifelink at 8 life, opp_power=6: safe_life = max(9, 8) = 9.
        # life(8) < 9 → zero activations regardless of the fix.
        assert game.players[0].life == starting_life, (
            f"Non-lifelink creature at {starting_life} life with opp "
            f"pressure must NOT activate (strict threshold 9 > 8); life "
            f"changed to {game.players[0].life}."
        )
        assert len(game.players[0].hand) == starting_hand, (
            f"Non-lifelink creature must not draw; hand went from "
            f"{starting_hand} to {len(game.players[0].hand)}."
        )

    def test_griselbrand_lifelink_at_6_life_does_not_activate(self, card_db):
        """Griselbrand with lifelink at 6 life cannot pay 7 — paying the
        cost would drop the controller to -1 (dead via SBA) before
        combat ever fires. The fix must still block this: the creature
        must survive the payment to deliver its lifelink damage."""
        runner, game = _make_runner_and_game(
            card_db, my_life=6, opp_life=20, opp_power=0,
        )
        gris = _add_to_battlefield(
            game, card_db, "Griselbrand", controller=0,
            tapped=False, summoning_sick=False,
        )
        assert Keyword.LIFELINK in gris.keywords

        starting_life = game.players[0].life
        starting_hand = len(game.players[0].hand)
        runner._activate_pay_life_draw(game, active=0)

        assert game.players[0].life == starting_life, (
            f"Lifelink creature at 6 life (below life_cost=7) must NOT "
            f"activate — paying 7 would kill controller before combat. "
            f"life changed from {starting_life} to {game.players[0].life}."
        )
        assert len(game.players[0].hand) == starting_hand, (
            f"Must not draw when activation is unsafe; hand: "
            f"{starting_hand} -> {len(game.players[0].hand)}."
        )

    def test_regression_non_lifelink_pay_life_draw_unchanged(self, card_db):
        """Regression: a pay_life_draw creature without lifelink at
        comfortable life totals still activates exactly as before (no
        projection applied, strict threshold). Uses Yawgmoth, Thran
        Physician (pay_life_cost_1, pay_life_draw_count_1, no lifelink)."""
        runner, game = _make_runner_and_game(
            card_db, my_life=20, opp_life=20, opp_power=0,
        )
        yaw = _add_to_battlefield(
            game, card_db, "Yawgmoth, Thran Physician", controller=0,
            tapped=False, summoning_sick=False,
        )
        assert Keyword.LIFELINK not in yaw.keywords, (
            "Yawgmoth should not have lifelink — regression baseline"
        )

        starting_life = game.players[0].life
        starting_hand = len(game.players[0].hand)
        runner._activate_pay_life_draw(game, active=0)

        # Yawgmoth: pay_life_cost_1, pay_life_draw_count_1; activations
        # capped at 3. safe_life = max(0+3, 1+1) = 3. Life=20 ≫ 3, all 3
        # activations fire → life = 17, hand +3.
        assert game.players[0].life == starting_life - 3, (
            f"Yawgmoth regression: expected 3 activations of 1-life each, "
            f"life went from {starting_life} to {game.players[0].life}."
        )
        assert len(game.players[0].hand) == starting_hand + 3, (
            f"Yawgmoth regression: expected +3 cards, "
            f"hand went from {starting_hand} to {len(game.players[0].hand)}."
        )
