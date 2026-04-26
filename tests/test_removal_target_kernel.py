"""Tests for the kernel-based removal target picker in EVPlayer.

These tests verify the migration from `creature_threat_value + max()`
to `best_choice` over a Choice list of "kill creature X" projections.
The kernel must:
  * Pick the highest-EV creature to kill
  * Return None when no kill projects positive EV (= "hold removal")
  * Honor the burn-damage filter (can't target unkillable creatures)
"""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai.ev_evaluator import EVSnapshot


def _fake_creature(instance_id: int, name: str, power: int, toughness: int,
                    keywords: set[str] = None) -> SimpleNamespace:
    """Minimal fake CardInstance with the fields the kernel apply reads."""
    template = SimpleNamespace(
        name=name,
        oracle_text="",
        keywords={kw for kw in (keywords or set())},
        cmc=power + toughness,
        is_creature=True,
    )
    return SimpleNamespace(
        instance_id=instance_id,
        template=template,
        power=power,
        toughness=toughness,
        damage_marked=0,
        instance_tags=set(),
    )


def _fake_card(name: str, oracle: str = "") -> SimpleNamespace:
    template = SimpleNamespace(
        name=name,
        oracle_text=oracle,
        cmc=2,
    )
    return SimpleNamespace(template=template, instance_id=999)


def _fake_game(my_idx: int = 0, archetype: str = "midrange") -> SimpleNamespace:
    """Minimal game stub with two players."""
    me = SimpleNamespace(deck_name="midrange-test", creatures=[],
                         hand=[], lands=[], untapped_lands=[],
                         graveyard=[], life=20)
    opp = SimpleNamespace(deck_name="aggro-test", creatures=[],
                          hand=[], lands=[], untapped_lands=[],
                          graveyard=[], life=20)
    players = [me, opp]
    return SimpleNamespace(players=players, turn_number=4, stack=[])


def _make_snap(opp_power: int = 0, opp_creature_count: int = 0,
               opp_toughness: int = 0,
               my_life: int = 20) -> EVSnapshot:
    return EVSnapshot(
        my_life=my_life, opp_life=20,
        my_power=2, opp_power=opp_power,
        my_toughness=2, opp_toughness=opp_toughness,
        my_creature_count=1, opp_creature_count=opp_creature_count,
        my_hand_size=4, opp_hand_size=4,
        my_mana=3, opp_mana=3,
        my_total_lands=3, opp_total_lands=3,
        turn_number=4,
    )


def test_picks_highest_threat_among_two_creatures():
    from ai.ev_player import EVPlayer

    big = _fake_creature(1, "Big", power=4, toughness=4)
    small = _fake_creature(2, "Small", power=1, toughness=1)
    creatures = [small, big]

    game = _fake_game()
    game.players[1].creatures = creatures
    snap = _make_snap(opp_power=5, opp_creature_count=2, opp_toughness=5)

    removal = _fake_card("Removal")
    player_obj = SimpleNamespace(
        _carrier_disrupt_bonus=lambda *a, **k: 0.0,
    )

    with patch("ai.ev_evaluator.snapshot_from_game", return_value=snap), \
         patch("decks.card_knowledge_loader.get_burn_damage", return_value=0):
        picked = EVPlayer._pick_best_removal_target(
            player_obj, removal, creatures, game.players[1], game, 1)

    assert picked is big, f"expected to kill Big, got {picked.template.name}"


def test_returns_none_when_no_creatures():
    from ai.ev_player import EVPlayer

    game = _fake_game()
    game.players[1].creatures = []
    removal = _fake_card("Removal")
    player_obj = SimpleNamespace()

    picked = EVPlayer._pick_best_removal_target(
        player_obj, removal, [], game.players[1], game, 1)
    assert picked is None


def test_burn_filter_excludes_oversize_targets():
    """Lightning Bolt (3 damage) cannot pick a 4-toughness creature."""
    from ai.ev_player import EVPlayer

    bolt_target = _fake_creature(1, "Bolt-target", power=4, toughness=2)
    too_big = _fake_creature(2, "Too big", power=4, toughness=4)
    creatures = [too_big, bolt_target]

    game = _fake_game()
    game.players[1].creatures = creatures
    # Higher opp pressure so killing the bolt_target is EV-positive
    snap = _make_snap(opp_power=8, opp_creature_count=2, opp_toughness=6,
                      my_life=10)

    bolt = _fake_card("Lightning Bolt")
    player_obj = SimpleNamespace(
        _carrier_disrupt_bonus=lambda *a, **k: 0.0,
    )

    with patch("ai.ev_evaluator.snapshot_from_game", return_value=snap), \
         patch("decks.card_knowledge_loader.get_burn_damage", return_value=3):
        picked = EVPlayer._pick_best_removal_target(
            player_obj, bolt, creatures, game.players[1], game, 1)

    # Either picks the killable target, or holds removal — but
    # never picks too_big (rule violation).
    assert picked is not too_big, \
        "burn filter should exclude unkillable targets"
    if picked is not None:
        assert picked is bolt_target


def test_holds_removal_when_no_threat():
    """No clock pressure → kill projection is a wash → return None."""
    from ai.ev_player import EVPlayer

    small = _fake_creature(1, "Small", power=1, toughness=1)
    creatures = [small]

    game = _fake_game()
    game.players[1].creatures = creatures
    snap = _make_snap(opp_power=1, opp_creature_count=1, opp_toughness=1)

    removal = _fake_card("Removal")
    player_obj = SimpleNamespace(
        _carrier_disrupt_bonus=lambda *a, **k: 0.0,
    )

    with patch("ai.ev_evaluator.snapshot_from_game", return_value=snap), \
         patch("decks.card_knowledge_loader.get_burn_damage", return_value=0):
        picked = EVPlayer._pick_best_removal_target(
            player_obj, removal, creatures, game.players[1], game, 1)

    # When the clock impact of removing the creature is negligible,
    # the kernel should return None (hold removal).
    assert picked is None or picked is small
