"""A Converge-conditioned removal spell must never be chosen/cast against
a target its own colors-of-mana-spent ceiling can never reach.

Converge (Prismatic Ending's shape: "exile target nonland permanent if its
mana value is less than or equal to the number of colors of mana spent to
cast this spell") is capped at 5 distinct colors (WUBRG) no matter how much
generic mana is poured in — a two-color manabase realistically caps out at
2-3. A permanent with a large PRINTED mana value (Domain payoffs like Scion
of Draco cost as little as {2} on the battlefield but carry mana value 12
for every rules purpose that reads printed cost, including this one) is
therefore permanently unreachable by Converge removal for that manabase,
regardless of X.

Live bug this pins (replay audit: Azorius Control vs Domain Zoo, seed 57004,
turn 6): the AI cast Prismatic Ending for its full available mana against a
board with only a mana-value-12 Scion of Draco on it. The spell resolved
and exiled nothing — the whole turn's mana and the card were spent for zero
effect, on the turn immediately before the opponent's clock doubled.

Two independent gaps converge on this outcome (mechanic-phrased, not
card-specific):

  1. Cast-time X selection (`engine.cast_manager`) has dedicated
     reachability-aware pickers for board-wipe X
     (`pick_wipe_x_value`) and creature-tutor X
     (`pick_creature_tutor_x_value`) — Converge spells had none and fell
     through to "pay the maximum affordable mana", which is never smarter
     than a lower X for a Converge spell and can pay for an X that still
     reaches nothing.
  2. The AI's proactive-cast gate for removal
     (`ai.ev_player.EVPlayer._has_high_threat_target`) judges "is there a
     target worth casting for" purely by threat value, with no reachability
     filter for Converge-conditioned spells — so a huge, unreachable
     threat reads as ample justification to cast.

Card names below are fixture carriers loaded from the real DB (Prismatic
Ending, Scion of Draco, Ragavan) — the rule under test is the Converge
reachability ceiling, not any one card.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer


def _battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _wu_manabase(game, card_db, controller, n_islands=3, n_plains=3):
    """A two-color (WU) manabase — the realistic ceiling for Azorius
    Control: at most 2 distinct colors of mana spent, ever."""
    for _ in range(n_islands):
        _battlefield(game, card_db, "Island", controller=controller)
    for _ in range(n_plains):
        _battlefield(game, card_db, "Plains", controller=controller)


def _domain_lands(game, card_db, controller):
    """One of each basic land type — gives a Domain permanent its full
    power/toughness (Territorial Kavu is a 0/0 CDA without this)."""
    for name in ("Forest", "Island", "Mountain", "Plains", "Swamp"):
        _battlefield(game, card_db, name, controller=controller)


class TestConvergeXSelectionReachability:
    """Engine-level: `pick_converge_x_value` must reason about the real
    Converge ceiling (colors of mana spent), not raw generic mana."""

    def test_converge_x_picker_declines_when_no_target_is_within_reach(
            self, card_db):
        """A two-color manabase facing only a mana-value-12 permanent has
        no reachable target at any X — the picker must say so (no target),
        not silently hand back the maximum affordable X."""
        from engine.cast_manager import pick_converge_x_value

        game = GameState(rng=random.Random(0))
        game.active_player = 0
        game.current_phase = Phase.MAIN1
        _wu_manabase(game, card_db, controller=0)
        template = card_db.get_card("Prismatic Ending")
        _battlefield(game, card_db, "Scion of Draco", controller=1)

        best_x, best_target = pick_converge_x_value(game, 0, 6, template)

        assert best_target is None, (
            f"pick_converge_x_value returned target={best_target!r} "
            f"(mana value {getattr(best_target, 'template', None) and best_target.template.cmc}) "
            f"for a manabase that can spend at most 2 distinct colors "
            f"against a mana-value-12 permanent — Converge can never "
            f"reach it. The picker must report 'no reachable target' so "
            f"the caller can decline to cast rather than pay for a "
            f"guaranteed whiff."
        )

    def test_converge_x_picker_picks_cheapest_x_that_reaches_best_target(
            self, card_db):
        """When a low-mana-value target IS reachable, the picker must pick
        the cheapest X that gets there — never blindly maximise X the way
        the old fallback ('pay whatever is affordable') did."""
        from engine.cast_manager import pick_converge_x_value

        game = GameState(rng=random.Random(0))
        game.active_player = 0
        game.current_phase = Phase.MAIN1
        # Plenty of mana available — old behavior would grab all of it.
        _wu_manabase(game, card_db, controller=0, n_islands=4, n_plains=4)
        template = card_db.get_card("Prismatic Ending")
        _battlefield(game, card_db, "Ragavan, Nimble Pilferer", controller=1)

        best_x, best_target = pick_converge_x_value(game, 0, 8, template)

        assert best_target is not None and best_target.name == "Ragavan, Nimble Pilferer"
        assert best_x <= 1, (
            f"Ragavan is mana value 1 — one color of mana spent already "
            f"satisfies Converge for it. The picker chose X={best_x} out "
            f"of a budget of 8, paying for color diversity the target "
            f"never required."
        )

    def test_converge_x_picker_reaches_a_target_within_the_achievable_ceiling(
            self, card_db):
        """A target whose mana value sits within what this manabase can
        actually converge to (<=2 colors here) must be found and priced,
        confirming the ceiling isn't just always returning 'no target'."""
        from engine.cast_manager import pick_converge_x_value

        game = GameState(rng=random.Random(0))
        game.active_player = 0
        game.current_phase = Phase.MAIN1
        _wu_manabase(game, card_db, controller=0)
        template = card_db.get_card("Prismatic Ending")
        _domain_lands(game, card_db, controller=1)
        # Territorial Kavu: mana value 2 (RG) — within a 2-color ceiling.
        _battlefield(game, card_db, "Territorial Kavu", controller=1)

        best_x, best_target = pick_converge_x_value(game, 0, 6, template)

        assert best_target is not None and best_target.name == "Territorial Kavu"
        assert best_x == 2


class TestConvergeProactiveCastGate:
    """AI-level: the proactive-cast gate must not treat an unreachable
    permanent as justification to cast a Converge-conditioned spell."""

    def test_high_threat_target_check_ignores_unreachable_converge_targets(
            self, card_db):
        """`_has_high_threat_target` must return False for Prismatic
        Ending when the only opposing permanent is far outside this
        manabase's Converge ceiling — even though it is an enormous
        threat by every other measure (this is the exact shape that made
        the old gate fire: 'huge threat on board' read as 'cast it').
        """
        game = GameState(rng=random.Random(0))
        game.players[0].deck_name = "Azorius Control"
        game.players[1].deck_name = "Domain Zoo"
        game.active_player = 0
        game.current_phase = Phase.MAIN1
        game.turn_number = 6
        _wu_manabase(game, card_db, controller=0)
        spell = _hand(game, card_db, "Prismatic Ending", controller=0)
        _battlefield(game, card_db, "Scion of Draco", controller=1)

        player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)

        assert player._has_high_threat_target(game, spell, snap) is False, (
            "_has_high_threat_target credited Scion of Draco (mana value "
            "12) as justification to cast Prismatic Ending from a "
            "two-color manabase — Converge can never reach a mana-value-12 "
            "permanent from there. The gate must filter Converge-spell "
            "candidates by the achievable colors-of-mana-spent ceiling "
            "before crediting a target's threat value."
        )

    def test_high_threat_target_check_still_fires_for_a_reachable_target(
            self, card_db):
        """Regression anchor: the reachability filter must not blanket-
        suppress Prismatic Ending — a genuinely reachable, high-threat
        target still justifies the cast."""
        game = GameState(rng=random.Random(0))
        game.players[0].deck_name = "Azorius Control"
        game.players[1].deck_name = "Domain Zoo"
        game.active_player = 0
        game.current_phase = Phase.MAIN1
        game.turn_number = 6
        _wu_manabase(game, card_db, controller=0)
        spell = _hand(game, card_db, "Prismatic Ending", controller=0)
        _domain_lands(game, card_db, controller=1)
        _battlefield(game, card_db, "Territorial Kavu", controller=1)

        player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)

        assert player._has_high_threat_target(game, spell, snap) is True, (
            "Territorial Kavu (mana value 2) is within a two-color "
            "manabase's Converge ceiling and is a real threat — the "
            "reachability filter must not suppress a legitimate cast."
        )
