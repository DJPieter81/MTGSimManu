"""Cast-time validation must reject reanimator spells with no legal
graveyard target.

Reference: 5-seed --bo3 sweep of Goryo's Vengeance vs Affinity (seeds
60100..60500).  Trace S60400 G2 T4: Goryo's Vengeance was cast for
{1}{B}, the spell resolved with no log message, and the board state
was unchanged — the spell silently fizzled because the controller's
graveyard contained no legendary creature card to target.

CR 601.2c (Choosing targets): "If the spell or ability requires
targets but no possible targets exist on the battlefield (or in
whatever zone the targets must be), the spell or ability can't be
cast."  The engine's resolve handler in ``engine/card_effects.py``
correctly does nothing when no target exists (CR 608.2b — fizzle on
illegal target at resolution), but ``engine.cast_manager.can_cast``
does not gate the cast at announcement.  The AI sees the spell as
castable, pays {1}{B}, and the spell fizzles invisibly.

Cost of this bug: every wasted cast burns mana that could have paid
for Faithful Mending (the discard outlet that *would* fill the
graveyard with a legal target).  In replay seed 60400 G2 the wasted
T4 cast removed the only window in which Goryo's could have set up
T5 reanimation; instead the deck spent another four turns trying
again with nothing in the graveyard and lost the game.

Class-of-bug scope: 50+ Modern-legal cards have the pattern
``return target [...]creature card from your graveyard`` (verified
via ``CardDatabase`` regex sweep).  Notable members in current deck
pools:
  - Goryo's Vengeance      (target legendary creature)
  - Unburial Rites         (target creature)
  - Persist (the card)     (target nonlegendary creature)

The fix mechanic — verify the controller's graveyard contains at
least one card matching the oracle's typed target predicate — is
oracle-driven and contains zero hardcoded card names.  Adding new
reanimator spells to a deck list does not require code changes.

The fix lives in ``engine.cast_manager.can_cast`` alongside the
existing "target creature" predicate (CR 601.2c block, ~line 130).
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _land(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing land: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _hand(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _gy(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _battlefield_creature(game, card_db, name: str, controller: int) -> CardInstance:
    """Place a creature on the battlefield — used to bypass the
    existing 'target creature' battlefield check in cast_manager.
    Without this, can_cast rejects graveyard-target spells for the
    wrong reason and the test cannot isolate the targeting bug."""
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing creature: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _setup_main_phase(game) -> None:
    """Force the game into a state where can_cast accepts sorceries."""
    from engine.game_state import Phase
    game.current_phase = Phase.MAIN1
    game.active_player = 0


def _give_lands_for(game, card_db, n: int, kinds=("Swamp",)) -> None:
    """Give P1 enough lands to pay for any 2-CMC reanimator + colored pips."""
    # Mix of B/U/W/R sources so all our test reanimators are payable.
    pool = ("Swamp", "Island", "Plains", "Mountain")
    for i in range(n):
        _land(game, card_db, pool[i % len(pool)], 0)


def _add_battlefield_creature_for_existing_check(game, card_db) -> None:
    """The existing cast_manager.can_cast contains a 'target creature'
    check that requires a creature on the battlefield (CR 601.2c
    block).  That check fires for graveyard-target spells too because
    the regex matches \"target creature card from your graveyard\"
    same as \"target creature\".  To isolate the actual
    graveyard-target bug under test, pre-place a creature on the
    battlefield so that incidental check is satisfied."""
    _battlefield_creature(game, card_db, "Memnite", 1)


class TestGoryosVengeanceCannotBeCastWithoutLegalTarget:
    """Goryo's Vengeance must require a legendary creature card in the
    controller's graveyard at cast time.  Without one, the spell has
    no legal target (CR 601.2c) and ``can_cast`` must return False."""

    def test_goryos_mulls_no_target_in_empty_graveyard(self, card_db):
        """Empty graveyard → can_cast must return False.

        Today: cast_manager.can_cast accepts the spell; AI casts it,
        2 mana wasted, spell silently fizzles at resolution.
        """
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=3)
        _add_battlefield_creature_for_existing_check(game, card_db)

        goryos = _hand(game, card_db, "Goryo's Vengeance", 0)
        # P1 graveyard is empty.  Goryo's has no legal target.

        assert game.can_cast(0, goryos) is False, (
            f"Goryo's Vengeance was reported castable with an empty "
            f"graveyard.  CR 601.2c requires a legal target to exist "
            f"at cast announcement.  The AI burns 2 mana and a card "
            f"on a silent fizzle (replay seed 60400 G2 T4)."
        )

    def test_goryos_with_only_nonlegendary_creature_in_gy(self, card_db):
        """Memnite (nonlegendary creature) in GY → still no legal
        target.  Goryo's oracle requires the target be LEGENDARY."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=3)
        _add_battlefield_creature_for_existing_check(game, card_db)

        # Place a non-legendary creature card in P1's graveyard.
        _gy(game, card_db, "Memnite", 0)

        goryos = _hand(game, card_db, "Goryo's Vengeance", 0)
        assert game.can_cast(0, goryos) is False, (
            f"Goryo's Vengeance was reported castable when the only "
            f"creature card in graveyard (Memnite) is non-legendary.  "
            f"Goryo's oracle requires the target be a *legendary* "
            f"creature card.  Memnite cannot be a legal target."
        )

    def test_goryos_with_only_legendary_noncreature_in_gy(self, card_db):
        """Liliana of the Veil (legendary planeswalker) in GY →
        still no legal target.  Goryo's requires legendary CREATURE."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=3)
        _add_battlefield_creature_for_existing_check(game, card_db)

        # Place a legendary non-creature card in P1's graveyard.
        _gy(game, card_db, "Liliana of the Veil", 0)

        goryos = _hand(game, card_db, "Goryo's Vengeance", 0)
        assert game.can_cast(0, goryos) is False, (
            f"Goryo's Vengeance was reported castable when the only "
            f"legendary card in graveyard (Liliana of the Veil) is a "
            f"planeswalker.  Goryo's requires legendary *creature*."
        )

    def test_goryos_with_legendary_creature_target_is_castable(self, card_db):
        """Regression: Griselbrand (legendary creature) in own
        graveyard satisfies Goryo's target requirement.  can_cast
        must return True so the AI can fire the combo."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=3)
        _add_battlefield_creature_for_existing_check(game, card_db)

        _gy(game, card_db, "Griselbrand", 0)
        goryos = _hand(game, card_db, "Goryo's Vengeance", 0)
        assert game.can_cast(0, goryos) is True, (
            f"Regression: Goryo's Vengeance was reported NOT castable "
            f"when Griselbrand is in own graveyard.  The fix must "
            f"not over-reject hands that can fire the combo."
        )

    def test_goryos_does_not_target_opponent_graveyard(self, card_db):
        """CR 601.2c specificity: Goryo's oracle says \"from your
        graveyard\".  A legendary creature in the OPPONENT'S
        graveyard is not a legal target."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=3)
        _add_battlefield_creature_for_existing_check(game, card_db)

        # P2's graveyard has Griselbrand; P1 owns the spell and has empty GY.
        _gy(game, card_db, "Griselbrand", 1)

        goryos = _hand(game, card_db, "Goryo's Vengeance", 0)
        assert game.can_cast(0, goryos) is False, (
            f"Goryo's Vengeance was reported castable based on a "
            f"legendary creature in the *opponent's* graveyard.  "
            f"Oracle says \"from your graveyard\" — only the "
            f"controller's graveyard is searched."
        )


class TestUnburialRitesCannotBeCastWithoutLegalTarget:
    """Unburial Rites: hardcast (4B) requires \"target creature card
    from your graveyard\" (per the card DB oracle).  This class-of-bug
    check ensures the fix generalizes beyond Goryo's Vengeance — same
    pattern (target ... from your graveyard), different type filter
    (any creature, not legendary)."""

    def test_unburial_rites_hardcast_with_empty_graveyard(self, card_db):
        """No creature card in own graveyard → no legal target →
        can_cast False."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        for n in ("Plains", "Plains", "Plains", "Swamp", "Swamp"):
            _land(game, card_db, n, 0)
        _add_battlefield_creature_for_existing_check(game, card_db)

        rites = _hand(game, card_db, "Unburial Rites", 0)
        assert game.can_cast(0, rites) is False, (
            f"Unburial Rites was reported castable with empty own "
            f"graveyard.  Hardcast oracle: \"return target creature "
            f"card from your graveyard\".  No creature in own GY = "
            f"no legal target = uncastable."
        )

    def test_unburial_rites_with_creature_in_own_gy_castable(self, card_db):
        """Regression: Griselbrand in own GY → Unburial Rites
        castable."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        for n in ("Plains", "Plains", "Plains", "Swamp", "Swamp"):
            _land(game, card_db, n, 0)
        _add_battlefield_creature_for_existing_check(game, card_db)
        _gy(game, card_db, "Griselbrand", 0)

        rites = _hand(game, card_db, "Unburial Rites", 0)
        assert game.can_cast(0, rites) is True, (
            f"Regression: Unburial Rites NOT castable with creature "
            f"in own graveyard.  The fix must not over-reject hands "
            f"that can fire the spell."
        )

    def test_unburial_rites_with_creature_in_opp_gy_only(self, card_db):
        """Per DB oracle (\"from your graveyard\"), a creature in the
        opponent's graveyard does NOT satisfy the target requirement.
        Even though the printed Avacyn-Restored Unburial Rites read
        \"from a graveyard\", the canonical DB oracle here is owner-
        scoped and the engine must respect that."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        for n in ("Plains", "Plains", "Plains", "Swamp", "Swamp"):
            _land(game, card_db, n, 0)
        _add_battlefield_creature_for_existing_check(game, card_db)
        _gy(game, card_db, "Memnite", 1)  # opponent's graveyard

        rites = _hand(game, card_db, "Unburial Rites", 0)
        assert game.can_cast(0, rites) is False, (
            f"Unburial Rites was reported castable when only the "
            f"opponent has a creature card in graveyard.  DB oracle "
            f"says \"from your graveyard\" — only the controller's "
            f"graveyard satisfies the target."
        )
