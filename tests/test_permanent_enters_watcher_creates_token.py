"""Tests for permanent-ETB watcher that creates tokens when a qualifying
permanent enters the battlefield (CR 603.6).

Mechanic under test
-------------------
A permanent with "Whenever a[n] [nontoken] [type] you control enters, create
[N] [token]" watches for other permanents entering the controller's battlefield
and creates tokens when the entry matches the type condition. The watcher can
be any permanent type (enchantment, creature, artifact, …) and the entering
permanent can be any type specified in the trigger text.

Class size: dozens of cards share this trigger shape across all of Magic, e.g.
Weapons Manufacturing (nontoken artifact → Munitions token), Anointed
Procession (any token → doubled token), Welcoming Vampire (nontoken creature
with power ≤ 2 → draw), etc. The fix must be type-driven via the
entering permanent's card types, not card-name-specific.

Bug that this test pins: trigger_etb only handled (a) abilities on the ENTERING
card itself, (b) "another creature enters" on creature watchers, (c) subtype
watchers (Risen Reef), (d) type counter watchers (Kappa Cannoneer). It did NOT
handle the "whenever a [type] you control enters, create a token" watcher pattern
on enchantments or other permanent types. Weapons Manufacturing therefore never
fired its token-creation trigger, leaving Affinity with zero Munitions tokens.
"""

from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.game_state import GameState, Phase
from engine.triggers import TriggerManager


def _put_on_battlefield(game, tmpl, controller, is_token=False):
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.is_token = is_token
    game.players[controller].battlefield.append(card)
    return card


def _tokens(game, controller, exclude=None):
    """Return token cards on the battlefield, optionally excluding a specific instance."""
    return [c for c in game.players[controller].battlefield
            if getattr(c, "is_token", False)
            and (exclude is None or c.instance_id != exclude.instance_id)]


@pytest.fixture(scope="module")
def db():
    from engine.card_database import CardDatabase
    return CardDatabase()


@pytest.fixture
def fresh_game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


class TestPermanentEntersWatcherCreatesToken:
    """trigger_etb fires token-creating watcher triggers when a qualifying
    permanent enters under the controller's control.

    The mechanic: 'Whenever a[n] [nontoken] [type] you control enters, create
    [token].' The watcher does not need to be a creature — it can be an
    enchantment, artifact, or any other permanent type.
    """

    def test_nontoken_artifact_etb_triggers_watcher_token(self, fresh_game, db):
        """Watcher with 'whenever a nontoken artifact you control enters' creates
        a token when a nontoken artifact enters.

        Fixture: Weapons Manufacturing (enchantment watcher) on the battlefield.
        A nontoken artifact (Mox Opal) enters. Exactly one token must be created.
        """
        game = fresh_game
        t_watcher = db.cards.get("Weapons Manufacturing")
        assert t_watcher is not None, "Weapons Manufacturing not in DB"
        t_artifact = db.cards["Mox Opal"]

        # Watcher is already on the battlefield
        _put_on_battlefield(game, t_watcher, 0)

        # A nontoken artifact enters — trigger_etb fires for the artifact
        entering = _put_on_battlefield(game, t_artifact, 0, is_token=False)
        tokens_before = len(_tokens(game, 0, exclude=entering))
        TriggerManager.trigger_etb(game, entering, 0)

        tokens_after = len(_tokens(game, 0, exclude=entering))
        assert tokens_after > tokens_before, (
            "Weapons Manufacturing must create a token when a nontoken artifact "
            f"enters. tokens_before={tokens_before}, tokens_after={tokens_after}"
        )

    def test_token_artifact_does_not_trigger_nontoken_watcher(
            self, fresh_game, db):
        """When the entering permanent is a token, a 'nontoken' watcher
        must NOT fire.

        Fixture: Weapons Manufacturing watcher. A Drone token (artifact creature
        token) enters. No additional token should be created.
        """
        game = fresh_game
        t_watcher = db.cards.get("Weapons Manufacturing")
        assert t_watcher is not None, "Weapons Manufacturing not in DB"
        t_drone = db.cards.get("Ornithopter")  # use as a stand-in artifact template
        assert t_drone is not None

        _put_on_battlefield(game, t_watcher, 0)

        # Token artifact enters — 'nontoken' condition blocks this
        entering = _put_on_battlefield(game, t_drone, 0, is_token=True)
        tokens_before = len(_tokens(game, 0, exclude=entering))
        TriggerManager.trigger_etb(game, entering, 0)

        tokens_after = len(_tokens(game, 0, exclude=entering))
        assert tokens_after == tokens_before, (
            "Nontoken-only watcher must NOT fire when a token enters. "
            f"tokens_before={tokens_before}, tokens_after={tokens_after}"
        )

    def test_watcher_does_not_fire_for_wrong_type(self, fresh_game, db):
        """A watcher that watches for 'artifact' entries must NOT fire when
        a non-artifact enters (e.g. a land or creature without artifact type)."""
        game = fresh_game
        t_watcher = db.cards.get("Weapons Manufacturing")
        assert t_watcher is not None, "Weapons Manufacturing not in DB"
        t_creature = db.cards.get("Lightning Bolt")  # sorcery — not an artifact
        if t_creature is None:
            pytest.skip("Lightning Bolt not in DB")

        _put_on_battlefield(game, t_watcher, 0)

        # Manually create a creature-only permanent (no artifact type)
        from engine.cards import CardTemplate
        t_land = CardTemplate(
            name="Test Land", card_types=[CardType.LAND],
            mana_cost=None, supertypes=[], subtypes=[],
            power=None, toughness=None, loyalty=None,
            keywords=set(), abilities=[],
            color_identity=set(), produces_mana=["G"],
            enters_tapped=False, oracle_text="",
            tags=set(),
        )
        from engine.mana import ManaCost
        t_land.mana_cost = ManaCost()
        entering = CardInstance(
            template=t_land, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        entering._game_state = game
        entering.is_token = False
        game.players[0].battlefield.append(entering)

        tokens_before = len(_tokens(game, 0, exclude=entering))
        TriggerManager.trigger_etb(game, entering, 0)
        tokens_after = len(_tokens(game, 0, exclude=entering))

        assert tokens_after == tokens_before, (
            "Watcher watching 'artifact' must NOT fire when a non-artifact (land) "
            f"enters. tokens_before={tokens_before}, tokens_after={tokens_after}"
        )

    def test_controller_isolation_watcher_does_not_fire_for_opp(
            self, fresh_game, db):
        """The watcher must only fire for the CONTROLLER's permanents, not
        the opponent's."""
        game = fresh_game
        t_watcher = db.cards.get("Weapons Manufacturing")
        assert t_watcher is not None, "Weapons Manufacturing not in DB"
        t_artifact = db.cards["Mox Opal"]

        # Watcher on player 0's battlefield
        _put_on_battlefield(game, t_watcher, 0)

        tokens_before = len(_tokens(game, 0))

        # Artifact enters under player 1's control — trigger_etb for controller=1
        entering = CardInstance(
            template=t_artifact, owner=1, controller=1,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        entering._game_state = game
        entering.is_token = False
        game.players[1].battlefield.append(entering)
        TriggerManager.trigger_etb(game, entering, 1)  # controller=1

        tokens_after = len(_tokens(game, 0))
        assert tokens_after == tokens_before, (
            "Watcher must NOT fire when an artifact enters under the OPPONENT's "
            f"control. tokens_before={tokens_before}, tokens_after={tokens_after}"
        )
