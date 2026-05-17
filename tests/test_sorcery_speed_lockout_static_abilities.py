"""R4: sorcery-speed-lockout static abilities are enforced at cast time.

A permanent whose oracle text bans opponents from casting non-sorcery-
speed spells (Teferi, Time Raveler's "Each opponent can cast spells
only any time they could cast a sorcery"; Grand Abolisher's "During
your turn, your opponents can't cast spells or activate abilities of
artifacts, creatures, or enchantments") must cause ``GameState.can_cast``
to reject an opponent's instant/flash spell cast attempt that breaks
the restriction.

Mechanism, not card:
  - Per-game registry of player indices currently restricted to sorcery
    speed, derived from battlefield permanents that carry the
    ``Tag.SORCERY_SPEED_LOCKOUT`` classifier tag (oracle-pattern
    detection until the W0-A classifier cache lands; semantics
    unchanged).
  - ``can_cast`` consults the registry — if the caster is in the
    restricted set AND the spell would be cast outside sorcery-speed
    windows, reject.

The audit (docs/history/audits/2026-05-16_rules_audit.md §R4) noted
that no engine machinery enforced this rule: Storm / Cascade / Living
End / Goryo's chains could chain instants on Teferi's controller's turn
freely, contrary to the static.

Lift-check: every Azorius / Boros control list with Teferi-TR or Grand
Abolisher; every white-weenie variant with Grand Abolisher.

Drannith Magistrate has a DIFFERENT mechanism — "can't cast spells
from anywhere other than their hands" — that is a zone restriction,
not a sorcery-speed restriction. A `Tag.CAST_FROM_NON_HAND_LOCKOUT`
tag would route through a different registry; covered by the
``test_drannith_magistrate_zone_restriction_is_a_different_tag``
documentation test below (asserts the engine does not conflate the
two restrictions).
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _put_on_battlefield(game, card_db, name, controller):
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
    game.players[controller].battlefield.append(card)
    return card


def _put_in_hand(game, card_db, name, controller):
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


def _give_lands(game, card_db, controller, lands):
    for land_name in lands:
        land_tmpl = card_db.get_card(land_name)
        assert land_tmpl is not None, f"missing land: {land_name}"
        land = CardInstance(
            template=land_tmpl,
            owner=controller,
            controller=controller,
            instance_id=game.next_instance_id(),
            zone="battlefield",
        )
        land._game_state = game
        land.enter_battlefield()
        game.players[controller].battlefield.append(land)


class TestSorcerySpeedLockoutStaticAbilities:
    """Tag.SORCERY_SPEED_LOCKOUT permanents make can_cast reject
    opponent's instant-speed casts that break the static."""

    def test_opponent_cannot_cast_instant_when_teferi_time_raveler_is_in_play(self, card_db):
        """Teferi, Time Raveler on P1's battlefield; it is P1's turn
        (so P2 would normally have priority for instants). P2 holds
        Lightning Bolt and has mana — can_cast must return False."""
        game = GameState(rng=random.Random(0))

        # P1 controls Teferi, Time Raveler
        _put_on_battlefield(game, card_db, "Teferi, Time Raveler", 0)

        # P2 holds Lightning Bolt with a Mountain available
        bolt = _put_in_hand(game, card_db, "Lightning Bolt", 1)
        _give_lands(game, card_db, 1, ("Mountain",))

        # Need an attacker on P1's side as a legal Bolt target
        _put_on_battlefield(game, card_db, "Monastery Swiftspear", 0)

        # It's P1's turn; P2 would normally hold priority for an instant
        game.active_player = 0
        game.priority_player = 1
        game.current_phase = Phase.MAIN1

        assert game.can_cast(1, bolt) is False, (
            "can_cast returned True for opponent's Lightning Bolt on "
            "Teferi controller's turn — sorcery-speed-lockout static "
            "is not being enforced."
        )

    def test_own_instant_cast_unaffected_by_teferi_static(self, card_db):
        """Teferi's controller can still cast instants on their own
        turn — the static targets opponents only."""
        game = GameState(rng=random.Random(0))

        _put_on_battlefield(game, card_db, "Teferi, Time Raveler", 0)

        bolt = _put_in_hand(game, card_db, "Lightning Bolt", 0)
        _give_lands(game, card_db, 0, ("Mountain",))

        # Legal target on P2's side
        _put_on_battlefield(game, card_db, "Monastery Swiftspear", 1)

        game.active_player = 0
        game.priority_player = 0
        game.current_phase = Phase.MAIN1

        assert game.can_cast(0, bolt) is True, (
            "Teferi's controller cannot cast their own Bolt — the "
            "static should restrict opponents, not the controller."
        )

    def test_static_lifts_when_teferi_leaves_battlefield(self, card_db):
        """Once Teferi leaves the battlefield, the lockout is gone."""
        game = GameState(rng=random.Random(0))

        teferi = _put_on_battlefield(game, card_db, "Teferi, Time Raveler", 0)

        bolt = _put_in_hand(game, card_db, "Lightning Bolt", 1)
        _give_lands(game, card_db, 1, ("Mountain",))
        _put_on_battlefield(game, card_db, "Monastery Swiftspear", 0)

        game.active_player = 0
        game.priority_player = 1
        game.current_phase = Phase.MAIN1

        # Sanity: locked while Teferi is in play
        assert game.can_cast(1, bolt) is False

        # Teferi leaves the battlefield (e.g., destroyed)
        game.players[0].battlefield.remove(teferi)
        teferi.zone = "graveyard"
        game.players[0].graveyard.append(teferi)

        assert game.can_cast(1, bolt) is True, (
            "Lockout did not lift after Teferi left the battlefield."
        )

    def test_grand_abolisher_blocks_opp_instant_speed_spells(self, card_db):
        """Grand Abolisher's "During your turn, your opponents can't
        cast spells…" carries the same SORCERY_SPEED_LOCKOUT tag — its
        opponents must also be denied instant-speed casts on the
        controller's turn."""
        game = GameState(rng=random.Random(0))

        _put_on_battlefield(game, card_db, "Grand Abolisher", 0)

        bolt = _put_in_hand(game, card_db, "Lightning Bolt", 1)
        _give_lands(game, card_db, 1, ("Mountain",))
        _put_on_battlefield(game, card_db, "Monastery Swiftspear", 0)

        game.active_player = 0
        game.priority_player = 1
        game.current_phase = Phase.MAIN1

        assert game.can_cast(1, bolt) is False, (
            "Grand Abolisher's during-your-turn lockout is not "
            "enforced via SORCERY_SPEED_LOCKOUT."
        )

    def test_drannith_magistrate_zone_restriction_is_a_different_tag(self, card_db):
        """Drannith Magistrate's "Your opponents can't cast spells from
        anywhere other than their hands" is a zone restriction, NOT a
        sorcery-speed restriction. It should not pull spells into the
        SORCERY_SPEED_LOCKOUT registry — opponents' hand-cast instants
        remain legal under Drannith alone (a separate
        ``Tag.CAST_FROM_NON_HAND_LOCKOUT`` would handle the zone gate;
        the present commit does NOT implement that — it is a known TODO
        — but the engine must not conflate the two restrictions)."""
        game = GameState(rng=random.Random(0))

        _put_on_battlefield(game, card_db, "Drannith Magistrate", 0)

        bolt = _put_in_hand(game, card_db, "Lightning Bolt", 1)
        _give_lands(game, card_db, 1, ("Mountain",))
        _put_on_battlefield(game, card_db, "Monastery Swiftspear", 0)

        game.active_player = 0
        game.priority_player = 1
        game.current_phase = Phase.MAIN1

        # Hand-cast instant remains legal under Drannith — the static
        # only restricts non-hand zones (GY, exile, library).
        assert game.can_cast(1, bolt) is True, (
            "Drannith Magistrate is wrongly being treated as a "
            "sorcery-speed lockout; its restriction is a zone gate, "
            "not a timing gate."
        )
