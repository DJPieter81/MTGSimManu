"""Evoke gate in can_cast (Bug E3).

The evoke alternative cost must be available independently of whether
the hardcast cost can be paid. Evoke is a choice the caster makes, not
a fallback when mana is short.

Previous behaviour (buggy): `can_cast` only considered the evoke path
when `total_mana < effective_cmc` — i.e. evoke was a last resort. With
five untapped Mountains in play (total_mana == effective_cmc but no
white source) Solitude reports as uncastable, even though its evoke
cost is "exile a white card from your hand" — no mana required. Jeskai
Blink frequently sits with five+ mana of mixed colours during opponent
turns; this gate masked the evoke removal response that should have
been available, contributing to a poor Affinity matchup.

Correct behaviour: evoke is available whenever the evoke cost is
payable (mana portion + exile fodder + valid target). `can_cast`
returns True if EITHER the hardcast OR the evoke cost is payable; the
AI layer decides which mode to use.
"""
from __future__ import annotations

import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


class _EvokeYesCallbacks(DefaultCallbacks):
    """AI stub that always opts to evoke when asked.

    can_cast consults `should_evoke` as a final gate. The default says
    no; tests that exercise the evoke path must opt in explicitly.
    """

    def should_evoke(self, game, player_idx, card):
        return True


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


def _add_land_untapped(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing land: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.tapped = False
    game.players[controller].battlefield.append(card)
    return card


def _put_opp_creature(game, card_db):
    """Drop a creature on the opponent's side so Solitude has a target."""
    tmpl = card_db.get_card("Goblin Guide")
    assert tmpl is not None
    opp_creature = CardInstance(
        template=tmpl, owner=1, controller=1,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    opp_creature._game_state = game
    game.players[1].battlefield.append(opp_creature)
    return opp_creature


def _make_game(callbacks=None):
    return GameState(rng=random.Random(0), callbacks=callbacks or _EvokeYesCallbacks())


class TestEvokeWithFullHardcastMana:
    """Evoke must stay available even when hardcast is also payable."""

    def test_solitude_with_5_plains_castable(self, card_db):
        """Baseline: 5 Plains lets Solitude be hardcast.

        Pre-fix this case already returned True via the hardcast path,
        so it does not by itself exercise the bug; included to anchor
        that hardcast is unaffected.
        """
        game = _make_game()
        for _ in range(5):
            _add_land_untapped(game, card_db, "Plains", 0)
        _add_to_hand(game, card_db, "Guide of Souls", 0)
        solitude = _add_to_hand(game, card_db, "Solitude", 0)
        _put_opp_creature(game, card_db)

        assert game.can_cast(0, solitude), (
            "Solitude should be castable with 5 Plains + white exile fodder."
        )

    def test_solitude_with_5_mountains_castable_via_evoke(self, card_db):
        """Regression for E3: 5 Mountains == CMC but no white source.

        Pre-fix: `total_mana < effective_cmc` (5 < 5) is False, so the
        evoke branch is skipped entirely. The colour check then fails
        (no W sources for {W}{W}) and can_cast returns False — even
        though evoke would trivially pay (exile a white card, no mana).

        Post-fix: the evoke branch is evaluated whenever the evoke cost
        is payable, regardless of the hardcast mana total.
        """
        game = _make_game()
        # Five mountains: total mana meets CMC=5, but no white source.
        for _ in range(5):
            _add_land_untapped(game, card_db, "Mountain", 0)
        _add_to_hand(game, card_db, "Guide of Souls", 0)  # white exile fodder
        solitude = _add_to_hand(game, card_db, "Solitude", 0)
        _put_opp_creature(game, card_db)

        assert game.can_cast(0, solitude), (
            "Solitude should be castable via evoke with 5 Mountains + a "
            "white card to exile. Evoke cost has no mana component; the "
            "hardcast gate must not suppress the evoke path."
        )


class TestEvokeWithInsufficientHardcastMana:
    """Evoke available when hardcast is NOT payable but evoke cost is."""

    def test_solitude_with_4_plains_still_castable_via_evoke(self, card_db):
        game = _make_game()
        # Only 4 untapped Plains — not enough to hardcast Solitude (CMC 5).
        for _ in range(4):
            _add_land_untapped(game, card_db, "Plains", 0)
        _add_to_hand(game, card_db, "Guide of Souls", 0)
        solitude = _add_to_hand(game, card_db, "Solitude", 0)
        _put_opp_creature(game, card_db)

        assert game.can_cast(0, solitude), (
            "Solitude should be castable via evoke with only 4 Plains. "
            "Evoke cost is 'exile a white card' — no mana required."
        )


class TestEvokeFodderRequired:
    """Evoke fails when the exile fodder is absent."""

    def test_solitude_with_no_white_card_not_castable(self, card_db):
        game = _make_game()
        # No lands: can't hardcast. No other white card: can't evoke.
        solitude = _add_to_hand(game, card_db, "Solitude", 0)
        _put_opp_creature(game, card_db)

        assert not game.can_cast(0, solitude), (
            "Solitude should NOT be castable with 0 mana and no other "
            "white card to exile. Neither hardcast nor evoke is payable."
        )


class TestNonEvokeInstantUnchanged:
    """Regression: a non-evoke instant behaves identically to pre-fix."""

    def test_counterspell_with_sufficient_mana_castable(self, card_db):
        # Counterspell needs both sufficient mana AND a legal target on
        # the stack. Pre-refactor, can_cast only checked mana — empty
        # stack passed silently. The unified target solver enforces
        # CR 601.2c, so this regression seeds the stack with an
        # opposing spell to keep the "sufficient mana" axis the
        # variable under test.
        from engine.stack import StackItem, StackItemType
        game = _make_game()
        # Counterspell is {U}{U}.
        for _ in range(2):
            _add_land_untapped(game, card_db, "Island", 0)
        cspell = _add_to_hand(game, card_db, "Counterspell", 0)
        # Seed an opposing spell so a legal target exists.
        opp_spell = _add_to_hand(game, card_db, "Memnite", 1)
        game.stack.push(StackItem(
            item_type=StackItemType.SPELL, source=opp_spell, controller=1,
        ))
        assert cspell.template.evoke_cost is None, (
            "Counterspell should not parse an evoke cost; the test is "
            "verifying the non-evoke branch."
        )
        assert game.can_cast(0, cspell), (
            "Counterspell should be castable with 2 Islands and a "
            "spell on the stack."
        )

    def test_counterspell_with_insufficient_mana_not_castable(self, card_db):
        # As above, seed the stack so the only failing axis is mana.
        from engine.stack import StackItem, StackItemType
        game = _make_game()
        _add_land_untapped(game, card_db, "Island", 0)  # only 1 Island
        cspell = _add_to_hand(game, card_db, "Counterspell", 0)
        opp_spell = _add_to_hand(game, card_db, "Memnite", 1)
        game.stack.push(StackItem(
            item_type=StackItemType.SPELL, source=opp_spell, controller=1,
        ))
        assert not game.can_cast(0, cspell), (
            "Counterspell should NOT be castable with only 1 Island — "
            "and there is no evoke path to rescue it."
        )

    def test_counterspell_with_empty_stack_not_castable(self, card_db):
        # CR 601.2c regression: Counterspell with no spell on the
        # stack has no legal target and cannot be cast, regardless of
        # mana. Pre-refactor this passed silently and fizzled at
        # resolution; the unified target solver rejects at cast time.
        game = _make_game()
        for _ in range(2):
            _add_land_untapped(game, card_db, "Island", 0)
        cspell = _add_to_hand(game, card_db, "Counterspell", 0)
        assert game.stack.is_empty
        assert not game.can_cast(0, cspell), (
            "Counterspell on empty stack must be uncastable — no "
            "legal target."
        )
