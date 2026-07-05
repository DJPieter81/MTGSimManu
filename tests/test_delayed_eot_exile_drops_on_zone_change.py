"""CR 400.7 object identity for delayed end-of-turn exile riders.

A delayed triggered ability like "exile it at the beginning of the next
end step" (Goryo's Vengeance, Sneak Attack, Through the Breach, ...)
tracks a specific *object*. CR 400.7: a card that changes zones becomes
a new object with no memory of its previous existence. If the tracked
permanent leaves the battlefield before the end step — even if the same
physical card returns (blink, bounce + return) — the rider must NOT
exile the returned object.

The engine reuses the same CardInstance across zone changes, so object
identity is modelled per battlefield entry: each battlefield entry is a
new object, and a pending rider bound to an older entry is stale.

Regression source: Instant Reanimator vs Boros s50000 G2 T4 — Ephemerate
blinked a Goryo's-reanimated Atraxa and the engine exiled it at end of
turn anyway (docs/diagnostics/2026-07-05_instant_reanimator_mechanism_gaps.md,
Gap 1).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.permanent_effects import PermanentEffects
from engine.spell_resolution import ResolutionManager
from engine.turn_manager import TurnManager


def _make_game():
    return GameState(rng=random.Random(0))


def _put_in_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _reanimate_with_eot_exile_rider(game, card_db, name="Griselbrand",
                                    controller=0):
    """Reanimate a creature under an 'exile at end of turn' delayed rider
    (the Goryo's Vengeance / Through the Breach rider shape)."""
    card = _put_in_graveyard(game, card_db, name, controller)
    PermanentEffects.reanimate(
        game, controller, card, exile_at_eot=True, give_haste=True,
    )
    assert card.zone == "battlefield"
    assert game._end_of_turn_exiles, "rider was not registered"
    return card


class TestDelayedEotExileObjectIdentity:

    def test_rider_fires_when_object_never_changed_zones(self, card_db):
        """Negative control: no zone change → the rider still exiles the
        creature at end of turn (baseline temporary-reanimation behaviour)."""
        game = _make_game()
        card = _reanimate_with_eot_exile_rider(game, card_db)

        TurnManager().end_of_turn_cleanup(game)

        assert card.zone == "exile", (
            "EOT-exile rider must still fire on an object that never "
            f"changed zones; got zone={card.zone}"
        )
        assert card in game.players[0].exile

    def test_rider_drops_when_tracked_object_is_blinked(self, card_db):
        """Blink (exile + immediate return) makes the permanent a new
        object (CR 400.7); the pending EOT-exile rider loses track of it
        and the creature survives the end step."""
        game = _make_game()
        card = _reanimate_with_eot_exile_rider(game, card_db)

        ResolutionManager._blink_permanent(game, card, 0)
        assert card.zone == "battlefield"

        TurnManager().end_of_turn_cleanup(game)

        assert card.zone == "battlefield", (
            "a blinked permanent is a new object; the stale EOT-exile "
            f"rider must not fire on it; got zone={card.zone}"
        )
        assert card in game.players[0].battlefield
        assert card not in game.players[0].exile

    def test_rider_drops_when_bounced_and_returned(self, card_db):
        """Second zone-change flavour: battlefield → hand → battlefield
        before the end step. The returned object is new; no exile."""
        game = _make_game()
        card = _reanimate_with_eot_exile_rider(game, card_db)

        assert game.zone_mgr.move_card_to_hand(game, card, cause="bounce")
        assert game.zone_mgr.move_card_to_battlefield(
            game, card, from_zone="hand", cause="recast",
        )
        assert card.zone == "battlefield"

        TurnManager().end_of_turn_cleanup(game)

        assert card.zone == "battlefield", (
            "a bounced-and-returned permanent is a new object; the stale "
            f"EOT-exile rider must not fire on it; got zone={card.zone}"
        )
        assert card not in game.players[0].exile

    def test_fresh_rider_on_current_entry_still_fires(self, card_db):
        """Staleness is per battlefield entry, not per card: a rider
        registered against the CURRENT entry fires normally even though
        the card entered the battlefield before."""
        game = _make_game()
        card = _reanimate_with_eot_exile_rider(game, card_db)

        # First rider goes stale via blink; first end step passes.
        ResolutionManager._blink_permanent(game, card, 0)
        TurnManager().end_of_turn_cleanup(game)
        assert card.zone == "battlefield"

        # A new temporary-reanimation-style rider bound to the current
        # object, registered through the engine API.
        game.register_end_of_turn_exile(card, 0)

        TurnManager().end_of_turn_cleanup(game)
        assert card.zone == "exile", (
            "a fresh rider bound to the current object must still fire"
        )
