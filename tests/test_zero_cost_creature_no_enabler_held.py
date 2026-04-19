"""Bug A — Zero-cost creatures cast T1 without enabler.

Evidence: `replays/boros_rarakkyo_vs_affinity_s63000_bo3.txt` line 54 —
Affinity T1 casts Ornithopter when no Mox Opal metalcraft is imminent,
no Cranial Plating is in hand, and no sacrifice outlet exists on the
battlefield.  The creature will do the same thing next turn (it enters
summoning-sick, blocks nothing useful as a 0/2, and attacks for 0).

Deferrability principle (see `docs/design/ev_correctness_overhaul.md` §1):
EV(cast) should compare against "the best alternative, including cast
later," not against "do nothing this turn."  A 0/2 flyer with no same-
turn payoff signal is strictly deferrable — casting it now exposes a
card to removal for zero incremental value.

Regression anchor: when the same Ornithopter cast WOULD trigger a same-
turn payoff (here: equipping Cranial Plating for immediate damage, or
crossing the 3-artifact metalcraft threshold that enables Mox Opal's
mana), the EV must remain positive so the AI still casts it.  This
guards against over-conservative regressions that hold every 0-cost
creature indefinitely.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


class TestOrnithopterWithoutEnablerHeld:
    """Ornithopter in hand on T1 with no enabler present — the AI must
    defer the cast to a later turn when a payoff signal fires."""

    def test_ornithopter_no_enabler_scores_below_pass_threshold(
            self, card_db):
        """Bare hand: 1 Mountain on battlefield, Ornithopter in hand,
        nothing else.  No Plating, no Mox Opal, no artifact synergy.
        Casting Ornithopter now yields nothing this turn; the same body
        is achievable next turn.  EV must be below pass_threshold."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Mountain", 0)
        orn = _add_to_hand(game, card_db, "Ornithopter", 0)
        # Opp has an empty board — no same-turn block value either.

        player = EVPlayer(player_idx=0, deck_name="Affinity",
                          rng=random.Random(0))
        me = game.players[0]
        opp = game.players[1]
        snap = snapshot_from_game(game, 0)

        ev = player._score_spell(orn, snap, game, me, opp)

        assert ev < player.profile.pass_threshold, (
            f"Ornithopter (0/2 flyer) has no same-turn payoff when no "
            f"enabler is in play or in hand — no Plating to equip, no "
            f"imminent metalcraft threshold, no sacrifice outlet. "
            f"Casting it now is deferrable; EV should be below "
            f"pass_threshold ({player.profile.pass_threshold}) so the "
            f"AI holds the card. Got EV={ev:.3f}."
        )

    def test_ornithopter_with_plating_enabler_still_cast(self, card_db):
        """Regression: when a same-turn payoff signal exists (Cranial
        Plating in hand, usable as an equip target for scaling damage),
        Ornithopter's EV must remain ABOVE pass_threshold so the AI
        still casts it.  Guards against over-conservatism after the
        deferrability fix lands."""
        game = GameState(rng=random.Random(0))
        # More lands so we can realistically cast Plating later this turn.
        _add_to_battlefield(game, card_db, "Mountain", 0)
        _add_to_battlefield(game, card_db, "Darksteel Citadel", 0)
        orn = _add_to_hand(game, card_db, "Ornithopter", 0)
        # Plating in hand → Ornithopter becomes a carrier. Same-turn or
        # next-turn equip materially changes Ornithopter's damage clock.
        _add_to_hand(game, card_db, "Cranial Plating", 0)

        player = EVPlayer(player_idx=0, deck_name="Affinity",
                          rng=random.Random(0))
        me = game.players[0]
        opp = game.players[1]
        snap = snapshot_from_game(game, 0)

        ev = player._score_spell(orn, snap, game, me, opp)

        assert ev >= player.profile.pass_threshold, (
            f"Ornithopter with Plating enabler in hand SHOULD be cast — "
            f"it's a usable Plating carrier and its deployment enables a "
            f"same-turn (or very-next-turn) damage payoff. EV must stay "
            f"above pass_threshold ({player.profile.pass_threshold}); "
            f"got EV={ev:.3f}. Over-conservative deferral breaks Affinity."
        )
