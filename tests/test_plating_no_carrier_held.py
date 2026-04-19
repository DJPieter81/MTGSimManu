"""Bug B — Cranial Plating cast with no carrier on the battlefield.

Known from the Plating prototype session (`ai/ev_player.py:533-611`):
Affinity with 2 lands + Plating in hand but no creatures on the board
would cast Plating anyway, leaving it unattached and exposed to removal
for zero same-turn value.  The prototype tuned an ad-hoc "hold bonus"
to suppress this specific case; the proper fix is the generic
deferrability principle from `docs/design/ev_correctness_overhaul.md`.

Principle: Plating's effect ("equipped creature gets +1/+0 for each
artifact") only materializes once a carrier exists AND equip mana is
available.  With zero creatures on the battlefield, there is no
same-turn payoff signal firing — the Plating sits inert, identical to
"cast next turn when a carrier is down."  EV should therefore be
negative (or at most ≈0 from future option value, dominated by the
removal exposure cost) and below pass_threshold.

Regression anchor: when a carrier IS on the battlefield and the AI
has equip mana available, Plating's EV must remain positive so the
same spell is cast when the scaling damage payoff actually fires.
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


class TestPlatingNoCarrierHeld:
    """Plating is deferrable when no creature exists to equip it to."""

    def test_plating_no_carrier_scores_below_pass_threshold(self, card_db):
        """Affinity T2 with 2 lands + Plating in hand, NO creatures on
        the battlefield and NO creatures in hand.  Plating has no
        carrier this turn, no carrier that will be cast this turn, and
        no same-turn payoff.  It must score below pass_threshold."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Mountain", 0)
        _add_to_battlefield(game, card_db, "Mountain", 0)
        # Darksteel Citadel = artifact land. Enables metalcraft
        # signalling but still no carrier body exists on the board.
        _add_to_battlefield(game, card_db, "Darksteel Citadel", 0)
        plating = _add_to_hand(game, card_db, "Cranial Plating", 0)
        # Hand is otherwise empty — no creature in hand means Plating
        # can't find a carrier on this or next turn without a topdeck.

        player = EVPlayer(player_idx=0, deck_name="Affinity",
                          rng=random.Random(0))
        me = game.players[0]
        opp = game.players[1]
        snap = snapshot_from_game(game, 0)

        ev = player._score_spell(plating, snap, game, me, opp)

        assert ev < player.profile.pass_threshold, (
            f"Cranial Plating with NO carrier on battlefield AND NO "
            f"creature in hand has no same-turn payoff. The equipment "
            f"sits unattached and dies to any removal for zero value. "
            f"EV should be below pass_threshold "
            f"({player.profile.pass_threshold}); got EV={ev:.3f}."
        )

    def test_plating_with_carrier_and_equip_mana_still_cast(self, card_db):
        """Regression anchor — when a carrier exists AND equip mana
        can be spent this turn, Plating's EV must remain above
        pass_threshold so the AI still casts it.  This is the payoff
        state the deck is built around."""
        game = GameState(rng=random.Random(0))
        # 4 lands: enough to cast Plating (2) AND equip ({1}) the same turn.
        _add_to_battlefield(game, card_db, "Mountain", 0)
        _add_to_battlefield(game, card_db, "Mountain", 0)
        _add_to_battlefield(game, card_db, "Mountain", 0)
        _add_to_battlefield(game, card_db, "Darksteel Citadel", 0)
        # Carrier on the battlefield, no summoning sickness.
        _add_to_battlefield(game, card_db, "Ornithopter", 0)
        plating = _add_to_hand(game, card_db, "Cranial Plating", 0)

        player = EVPlayer(player_idx=0, deck_name="Affinity",
                          rng=random.Random(0))
        me = game.players[0]
        opp = game.players[1]
        snap = snapshot_from_game(game, 0)

        ev = player._score_spell(plating, snap, game, me, opp)

        assert ev >= player.profile.pass_threshold, (
            f"Plating with a carrier (Ornithopter) on the battlefield "
            f"AND equip mana available must be cast — this is the "
            f"intended payoff state. EV must stay above pass_threshold "
            f"({player.profile.pass_threshold}); got EV={ev:.3f}. "
            f"Over-conservative deferral here would brick Affinity's "
            f"game plan."
        )
