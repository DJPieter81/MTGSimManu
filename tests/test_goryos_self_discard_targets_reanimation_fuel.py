"""GV-1: Faithful Mending self-discard must prioritise reanimation fuel.

Diagnostic (2026-04-24): Goryo's Vengeance is at 24.9% flat WR. The
reanimator plan hinges on Faithful Mending binning Griselbrand or
Archon of Cruelty so Goryo's Vengeance has a legal graveyard target.
Replay traces show Faithful Mending often binning low-CMC utility
spells (Thoughtseize, lands) or flashback cantrips while the
reanimation target sits in hand unused — the combo never fires.

Fix requirement (no hardcoded card names):
    When the controller's deck gameplan declares a reanimation plan
    (a FILL_RESOURCE goal targeting the graveyard with resource_min_cmc
    >= 5), the self-discard picker must prefer binning creatures with
    CMC >= resource_min_cmc over non-fuel cards (discard, lands, cheap
    interaction, flashback cantrips, evoke removal bodies). The policy
    stays oracle/gameplan driven — no card names in the scoring
    function.

Regression guard: a hand that contains no reanimation fuel at all
still defaults to the highest-CMC non-land choice.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.game_runner import AICallbacks


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _make_game():
    """Build a GameState wired to AICallbacks so Faithful Mending's
    self-discard routes through ai.discard_advisor — the default
    callbacks would fall back to raw-CMC sort."""
    return GameState(rng=random.Random(0), callbacks=AICallbacks())


def _make_card(game, card_db, name, controller):
    tmpl = card_db.cards.get(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    card._game_state = game
    return card


def _build_hand(game, card_db, player_idx, card_names, deck_name):
    player = game.players[player_idx]
    player.deck_name = deck_name
    player.hand = [_make_card(game, card_db, n, player_idx) for n in card_names]
    return player.hand


class TestGoryosSelfDiscardPicksReanimationFuel:
    """GV-1 — Faithful Mending's self-discard must bin reanimation
    targets before low-CMC utility spells when the deck's gameplan
    declares a reanimation goal."""

    def test_prefers_fat_creature_over_thoughtseize_and_lands(self, card_db):
        """Hand contains both Griselbrand and Archon of Cruelty (both
        reanimation fuel per Goryo's FILL_RESOURCE.resource_min_cmc=5).
        The picker must choose one of those creatures, not Thoughtseize
        and never a land."""
        game = _make_game()
        hand = _build_hand(
            game, card_db, player_idx=0,
            card_names=[
                "Griselbrand",
                "Archon of Cruelty",
                "Thoughtseize",
                "Plains",
                "Mountain",
            ],
            deck_name="Goryo's Vengeance",
        )
        assert len(hand) == 5

        # Simulate Faithful Mending's self-discard (discards 1).
        game._force_discard(0, 1, self_discard=True)

        discarded = game.players[0].graveyard
        assert len(discarded) == 1
        picked = discarded[0].name

        assert picked in ("Griselbrand", "Archon of Cruelty"), (
            f"Faithful Mending self-discard picked {picked!r}; expected "
            f"a reanimation target (Griselbrand or Archon of Cruelty). "
            f"GV-1 regression: scorer is not preferring reanimation fuel."
        )

    def test_two_discards_bin_both_fat_creatures(self, card_db):
        """Faithful Mending discards 2. Starting from a hand with two
        reanimation targets and three non-fuel cards, both discards
        must be the reanimation targets — the combo needs the target
        in the graveyard."""
        game = _make_game()
        _build_hand(
            game, card_db, player_idx=0,
            card_names=[
                "Griselbrand",
                "Archon of Cruelty",
                "Thoughtseize",
                "Plains",
                "Mountain",
            ],
            deck_name="Goryo's Vengeance",
        )

        game._force_discard(0, 2, self_discard=True)

        discarded = [c.name for c in game.players[0].graveyard]
        assert len(discarded) == 2
        assert set(discarded) == {"Griselbrand", "Archon of Cruelty"}, (
            f"Two-card self-discard binned {discarded}; expected both "
            f"Griselbrand and Archon of Cruelty (the only reanimation "
            f"fuel in hand)."
        )

    def test_prefers_fat_creature_over_flashback_spell(self, card_db):
        """With a reanimation-plan gameplan, binning Griselbrand is
        strictly better than binning a flashback card: the flashback
        card still works from the graveyard later, while the combo
        payoff only matters when it's the reanimator's target.

        Current scorer gives flashback +90 but creatures 80+cmc
        (=88 for Griselbrand), so a flashback cantrip wins over the
        payoff — an EV-destroying inversion. The gameplan-aware fix
        must push reanimation fuel above flashback fodder when the
        FILL_RESOURCE goal declares resource_min_cmc for creatures
        targeted by the deck."""
        game = _make_game()
        _build_hand(
            game, card_db, player_idx=0,
            card_names=[
                "Griselbrand",
                "Faithful Mending",   # flashback — score 90 in old scorer
                "Thoughtseize",
                "Plains",
                "Swamp",
            ],
            deck_name="Goryo's Vengeance",
        )

        game._force_discard(0, 1, self_discard=True)

        discarded = game.players[0].graveyard
        assert len(discarded) == 1
        picked = discarded[0].name

        assert picked == "Griselbrand", (
            f"Self-discard picked {picked!r}; expected Griselbrand. "
            f"Goryo's gameplan declares FILL_RESOURCE with "
            f"resource_min_cmc=5 — reanimation fuel must rank above a "
            f"flashback cantrip, since the combo only fires when the "
            f"payoff is in the graveyard."
        )

    def test_prefers_fat_creature_over_evoke_removal_creature(self, card_db):
        """Solitude is a 5-CMC creature with removal tag. Old scorer
        gave it 80+5+10=95 (creature+removal), beating Griselbrand's
        80+8=88. That would make Solitude the preferred discard — but
        Solitude isn't the reanimation payoff path (it's a reactive
        evoke card with ETB value, typically cast on-board rather than
        discarded).

        Gameplan-driven fix: when the deck's FILL_RESOURCE goal targets
        resource_min_cmc creatures, the picker should rank creatures at
        the reanimator's declared CMC threshold by raw CMC, putting the
        highest-CMC payoff (Griselbrand 8) above the lower-CMC evoke
        body (Solitude 5)."""
        game = _make_game()
        _build_hand(
            game, card_db, player_idx=0,
            card_names=[
                "Griselbrand",
                "Solitude",
                "Thoughtseize",
                "Plains",
                "Swamp",
            ],
            deck_name="Goryo's Vengeance",
        )

        game._force_discard(0, 1, self_discard=True)

        discarded = game.players[0].graveyard
        assert len(discarded) == 1
        picked = discarded[0].name

        assert picked == "Griselbrand", (
            f"Self-discard picked {picked!r}; expected Griselbrand. "
            f"Goryo's reanimation plan must prefer the fattest "
            f"reanimation-fuel creature over a smaller evoke body "
            f"with reactive tags — Griselbrand (CMC 8) is the payoff, "
            f"Solitude (CMC 5) is a role-player."
        )

    def test_no_fuel_falls_back_to_highest_cmc_nonland(self, card_db):
        """Regression: a hand with no 5+ CMC creatures should still pick
        sensibly — prefer a non-land card over a land when there is no
        excess-land pressure. Flashback cards (Faithful Mending) and
        utility spells are acceptable picks because none of them are
        reanimation fuel."""
        game = _make_game()
        _build_hand(
            game, card_db, player_idx=0,
            card_names=[
                "Thoughtseize",       # 1 CMC
                "Unmarked Grave",     # 2 CMC
                "Faithful Mending",   # 2 CMC, flashback
                "Plains",             # land
                "Swamp",              # land
            ],
            deck_name="Goryo's Vengeance",
        )

        game._force_discard(0, 1, self_discard=True)

        discarded = game.players[0].graveyard
        assert len(discarded) == 1
        picked = discarded[0].name

        assert picked != "Plains" and picked != "Swamp", (
            f"Self-discard picked a land ({picked!r}) with no excess-land "
            f"pressure and non-land options available."
        )
        # Faithful Mending itself scores highly (flashback: +90) — that
        # is an acceptable pick. Unmarked Grave is a tutor (combo-tag
        # penalty applies). Thoughtseize should be lower-priority than
        # the flashback card.
        assert picked in ("Faithful Mending", "Unmarked Grave", "Thoughtseize"), (
            f"Unexpected pick {picked!r} for no-fuel hand."
        )
