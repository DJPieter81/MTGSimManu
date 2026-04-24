"""LE-A1 + LE-A2 — Cascade projection + graveyard asymmetry in EVSnapshot.

Living End consolidated findings (2026-04-24) found that `_project_spell`
in `ai/ev_evaluator.py` does not model two things:

  LE-A1: Cascade spells (Shardless Agent, Demonic Dread) are scored as
         vanilla 3-mana creatures. The projection does NOT credit the
         expected value of the cascade hit — for Living End decks, the
         cascade is the entire point of the card, and skipping its
         projection drops cascade-payoff EV to zero.

  LE-A2: `EVSnapshot` tracks `my_gy_creatures` but not `opp_gy_creatures`.
         Living End's reanimation is SYMMETRIC — both players return all
         their exiled creature cards. If opp has 3 discarded creatures
         and I have 2, the projection should net to -1, not +2.

These tests exercise both bugs via `_project_spell` directly.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import EVSnapshot, _project_spell, snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_library(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="library",
    )
    card._game_state = game
    game.players[controller].library.append(card)
    return card


def _add_to_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _mid_snap(**overrides) -> EVSnapshot:
    """Mid-game baseline. Callers override only the fields they care about."""
    defaults = dict(
        my_life=20, opp_life=20,
        my_power=0, opp_power=0,
        my_toughness=0, opp_toughness=0,
        my_creature_count=0, opp_creature_count=0,
        my_hand_size=5, opp_hand_size=5,
        my_mana=3, opp_mana=3,
        my_total_lands=3, opp_total_lands=3,
        turn_number=3,
    )
    defaults.update(overrides)
    return EVSnapshot(**defaults)


# ─────────────────────────────────────────────────────────────
# LE-A1 — Cascade spell projection credits cascade-hit value
# ─────────────────────────────────────────────────────────────

class TestCascadeProjection:
    """Cascade spells must project their cascade hit, not be scored as
    vanilla N-mana creatures. The projection is probabilistic — P(hit)
    × E[value of hit] — derived from library composition."""

    def test_shardless_agent_cascade_projection_exceeds_vanilla_2_2(
            self, card_db):
        """Shardless Agent cast into a Living End library MUST project
        more power than a vanilla 3-mana 2/2 with no other text.

        Setup: library has 4 Living End copies + 5 other cascade-legal
        cards + graveyard has 3 creatures to reanimate. P(cascade hits
        Living End) > 0, and when it does the reanimation projection
        credits the returned creatures. Therefore Shardless must be
        strictly more valuable than a vanilla 2/2 for 3."""
        game = GameState(rng=random.Random(0))
        # Graveyard has 3 creatures ready to come back.
        _add_to_graveyard(game, card_db, "Striped Riverwinder", 0)
        _add_to_graveyard(game, card_db, "Curator of Mysteries", 0)
        _add_to_graveyard(game, card_db, "Architects of Will", 0)
        # Library: 4 Living End (the finisher) + 5 cheaper cascadable cards.
        for _ in range(4):
            _add_to_library(game, card_db, "Living End", 0)
        for _ in range(5):
            _add_to_library(game, card_db, "Street Wraith", 0)

        # Snapshot reflects 3 creatures in my GY.
        snap = _mid_snap(my_gy_creatures=3)
        shardless = _add_to_hand(game, card_db, "Shardless Agent", 0)
        shardless_proj = _project_spell(
            shardless, snap, game=game, player_idx=0)

        # Compare against a vanilla 3-cmc 2/2 (Shardless body) without
        # cascade. Direct baseline projection keeps this apples-to-apples.
        vanilla_proj = EVSnapshot(
            my_life=snap.my_life, opp_life=snap.opp_life,
            my_power=snap.my_power + 2,   # Shardless 2/2 body only
            opp_power=snap.opp_power,
            my_toughness=snap.my_toughness + 2,
            opp_toughness=snap.opp_toughness,
            my_creature_count=snap.my_creature_count + 1,
            opp_creature_count=snap.opp_creature_count,
            my_hand_size=snap.my_hand_size - 1,
            opp_hand_size=snap.opp_hand_size,
            my_mana=max(0, snap.my_mana - 3),
            opp_mana=snap.opp_mana,
            my_total_lands=snap.my_total_lands,
            opp_total_lands=snap.opp_total_lands,
            turn_number=snap.turn_number,
            my_gy_creatures=snap.my_gy_creatures,
        )

        # Shardless must project strictly more power than a vanilla 2/2
        # because ~4/9 ≈ 44% of cascades hit Living End, which reanimates
        # 3 creatures from my graveyard.
        assert shardless_proj.my_power > vanilla_proj.my_power, (
            f"Cascade projection ({shardless_proj.my_power}) did not "
            f"exceed vanilla 2/2 body ({vanilla_proj.my_power}). "
            f"LE-A1: _project_spell treats Shardless as a vanilla 2/2 "
            f"when it should credit expected cascade-hit value."
        )


# ─────────────────────────────────────────────────────────────
# LE-A2 — Symmetric reanimation nets opp_gy_creatures
# ─────────────────────────────────────────────────────────────

class TestGraveyardAsymmetry:
    """EVSnapshot must track opp_gy_creatures and symmetric-reanimation
    projections (Living End pattern) must net both sides."""

    def test_snapshot_tracks_opp_gy_creatures(self, card_db):
        """`snapshot_from_game` must populate `opp_gy_creatures` from
        the opponent's graveyard creature count."""
        game = GameState(rng=random.Random(0))
        # Opp has 3 creatures in graveyard; I have 1.
        _add_to_graveyard(game, card_db, "Grizzly Bears", 0)
        _add_to_graveyard(game, card_db, "Centaur Courser", 1)
        _add_to_graveyard(game, card_db, "Grizzly Bears", 1)
        _add_to_graveyard(game, card_db, "Runeclaw Bear", 1)

        snap = snapshot_from_game(game, player_idx=0)
        assert snap.my_gy_creatures == 1
        assert snap.opp_gy_creatures == 3, (
            f"LE-A2: EVSnapshot.opp_gy_creatures = {snap.opp_gy_creatures}, "
            f"expected 3 (opp's graveyard has 3 creature cards)."
        )

    def test_symmetric_reanimation_nets_opp_gy(self, card_db):
        """A symmetric-reanimation spell (Living End) must project
        NET creature delta — my GY minus opp GY. When opp has more
        creatures in their GY than I do, the projection should be
        NEGATIVE (or zero) for my side, because opp benefits more."""
        game = GameState(rng=random.Random(0))
        # Me: 2 medium creatures in GY.
        _add_to_graveyard(game, card_db, "Grizzly Bears", 0)   # 2/2
        _add_to_graveyard(game, card_db, "Runeclaw Bear", 0)   # 2/2
        # Opp: 3 creatures in GY (bigger overall presence).
        _add_to_graveyard(game, card_db, "Centaur Courser", 1)  # 3/3
        _add_to_graveyard(game, card_db, "Grizzly Bears", 1)
        _add_to_graveyard(game, card_db, "Runeclaw Bear", 1)

        snap = snapshot_from_game(game, player_idx=0)
        living_end = _add_to_hand(game, card_db, "Living End", 0)
        projected = _project_spell(living_end, snap, game=game, player_idx=0)

        # Opp's graveyard-creature return must be credited to opp's side.
        assert projected.opp_power >= snap.opp_power, (
            f"LE-A2: Symmetric reanimation projection did not credit "
            f"opp's returning creatures. opp_power: "
            f"{snap.opp_power} → {projected.opp_power}."
        )
        assert projected.opp_creature_count >= snap.opp_creature_count + 3, (
            f"LE-A2: Projected opp_creature_count "
            f"({projected.opp_creature_count}) did not grow by opp's 3 "
            f"GY creatures; baseline was {snap.opp_creature_count}."
        )

        # EV delta: opp returns more creatures than I do (3 vs 2), and
        # opp's set includes a stronger 3/3 — so opp's power delta must
        # be ≥ my power delta.
        my_delta = projected.my_power - snap.my_power
        opp_delta = projected.opp_power - snap.opp_power
        assert opp_delta >= my_delta, (
            f"LE-A2: Symmetric reanimation with opp's GY (3) > my GY (2) "
            f"must leave opp with equal-or-greater power delta. "
            f"my_delta={my_delta} opp_delta={opp_delta}."
        )


class TestOneSidedReanimationUnaffected:
    """Regression: one-sided reanimation (Goryo's Vengeance, Persist)
    reads only my graveyard. opp_gy_creatures must NOT reduce my
    projected gain for these spells."""

    def test_goryos_vengeance_ignores_opp_gy(self, card_db):
        """Goryo's Vengeance returns a target from MY graveyard only.
        Projecting it must credit my GY's best creature without
        subtracting any value for opp's GY contents."""
        game = GameState(rng=random.Random(0))
        # My GY has 1 big creature.
        _add_to_graveyard(game, card_db, "Griselbrand", 0)
        # Opp GY has 3 creatures — should NOT affect the projection.
        _add_to_graveyard(game, card_db, "Centaur Courser", 1)
        _add_to_graveyard(game, card_db, "Grizzly Bears", 1)
        _add_to_graveyard(game, card_db, "Runeclaw Bear", 1)

        snap = snapshot_from_game(game, player_idx=0)
        goryo = _add_to_hand(game, card_db, "Goryo's Vengeance", 0)
        projected = _project_spell(goryo, snap, game=game, player_idx=0)

        # Goryo's is one-sided: my power goes up, opp's does not.
        assert projected.my_power > snap.my_power, (
            f"Goryo's Vengeance did not credit reanimated creature. "
            f"my_power {snap.my_power} → {projected.my_power}."
        )
        assert projected.opp_power == snap.opp_power, (
            f"Goryo's Vengeance (one-sided reanimation) erroneously "
            f"raised opp_power {snap.opp_power} → {projected.opp_power}. "
            f"Regression: opp_gy_creatures must NOT inflate opp's side "
            f"for one-sided reanimation spells."
        )
