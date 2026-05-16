"""M14 — PiF viability formula must include post-chain graveyard fuel.

Audit: docs/history/audits/2026-05-16_5panel_bo3_audit.md Combo F6.

The old `_has_viable_pif` was a boolean gate that only inspected
CURRENT graveyard fuel + mana to cast PiF.  Past in Flames' oracle is
"Each instant and sorcery card in your graveyard gains flashback
until end of turn" — the relevant graveyard state is the one AFTER
this turn's chain has populated it, not the snapshot before the
chain begins.

Symptom (canonical, audit row M14 P1): Storm holds at storm=5
(would fire Grapeshot for 6 damage) when casting two more rituals
into the chain produces a post-PiF graveyard with enough flashback
fuel to chain through to storm=8+, lethal at 9 damage.  The old
gate said "PiF's current GY has nothing castable" and clamped the
ritual EV.  The chain looked unviable even though, when projected,
it was lethal.

Replacement: `flashback_chain_viable(card, me, snap, ...)` — a
formula returning a non-boolean viability score derived from the
chain's projected post-step graveyard.  Walks chain steps; at each
step, post_step_gy_fuel + mana_to_cast_pif >= chain_to_close marks
the chain as viable.  The return value is a float (storm-coverage
ratio in [0.0, +inf)) so the caller can compare across decisions
rather than receiving a binary verdict.

Generic by construction:
- Cards detected via tags (`'ritual'`, `'flashback'`+`'combo'`) and
  via `ai/oracle_classifier.Tag.FLASHBACK`.  No card-name checks.
- No deck-name gates.
- Numeric thresholds derive from `combo_chain.find_all_chains`
  storm counts, not bare literals.

Lift-check: the same "chain viability via post-step GY" formula
serves Dredge (post-discard GY for Dread Return) and Living End
(post-cycle GY before cascade).  Storm is the most exposed case
because it has the tightest mana arithmetic.
"""
from __future__ import annotations

import random

import pytest

from ai.combo_calc import flashback_chain_viable
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone, summoning_sick=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = summoning_sick
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _setup_storm(game, card_db, mountains: int, storm: int = 0,
                 opp_life: int = 20) -> None:
    """Boilerplate: Ruby Storm side mid-turn, no medallions on board."""
    for _ in range(mountains):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    _add(game, card_db, "Guide of Souls", controller=1, zone="battlefield")
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = storm
    game._global_storm_count = storm
    game.players[0].life = 20
    game.players[1].life = opp_life


class TestFlashbackChainViableReturnsFloat:
    """Contract: `flashback_chain_viable` returns a viability score
    (float in [0.0, +inf)), NOT a boolean.  Callers need to compare
    viabilities across decisions; a bool collapses signal."""

    def test_flashback_chain_viable_returns_float_not_boolean(
            self, card_db):
        game = GameState(rng=random.Random(0))
        _setup_storm(game, card_db, mountains=6)
        ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                      zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
        _add(game, card_db, "Past in Flames", controller=0, zone="hand")
        _add(game, card_db, "Grapeshot", controller=0, zone="hand")

        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        result = flashback_chain_viable(ritual, me, snap)

        # bool is a subclass of int (which can be compared to float),
        # so explicitly reject bool subclass returns — the formula
        # must give a continuous viability number, not a verdict.
        assert not isinstance(result, bool), (
            f"flashback_chain_viable returned a bool ({result!r}). "
            f"The formula must produce a float viability score so "
            f"two chains can be compared by magnitude.  A bool "
            f"collapses signal — barely-lethal vs 2x-overlethal "
            f"would both register True."
        )
        assert isinstance(result, float), (
            f"flashback_chain_viable must return a float; got "
            f"{type(result).__name__}={result!r}."
        )


class TestFlashbackChainViableProjectsPostChainGY:
    """The core fix: post-chain GY fuel from the rituals about to be
    cast THIS turn must count toward PiF's flashback-replay
    viability.  Old gate counted only current GY → over-conservative."""

    def test_pif_fires_when_post_chain_gy_will_be_viable(
            self, card_db):
        """Hand: 2 rituals + Past in Flames + Grapeshot. GY: empty.
        Mana: 6 Mountains untapped — enough for 2 rituals (1R each)
        + Past in Flames (3R).

        Projected chain: cast 2 rituals (storm 0→2, GY gets 2 ritual
        cards) → cast PiF (storm 3, GY has 2 rituals that gain
        flashback) → flashback 2 rituals from GY (storm 5) → cast
        Grapeshot (storm 6, deals 6 damage).

        Even though the CURRENT GY is empty, the POST-CHAIN GY will
        contain the rituals we're about to cast.  Viability must be
        positive.  Old `_has_viable_pif` would return False (empty
        current GY)."""
        game = GameState(rng=random.Random(0))
        _setup_storm(game, card_db, mountains=6)
        ritual = _add(game, card_db, "Desperate Ritual",
                      controller=0, zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
        _add(game, card_db, "Past in Flames", controller=0, zone="hand")
        _add(game, card_db, "Grapeshot", controller=0, zone="hand")

        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        viability = flashback_chain_viable(ritual, me, snap)

        assert viability > 0.0, (
            f"PiF viability score = {viability:.3f} for a chain "
            f"that's ABOUT to cast 2 rituals → PiF → flashback those "
            f"rituals.  Old boolean gate said False because current "
            f"GY is empty; new formula must project post-step GY "
            f"and confirm PiF will have flashback targets after the "
            f"chain populates the graveyard."
        )

    def test_pif_held_when_post_chain_gy_still_insufficient(
            self, card_db):
        """Hand: 1 ritual + Past in Flames (no closer).  GY: empty.
        Mana: 5 Mountains.

        Projected chain: cast 1 ritual (storm 1, GY = 1 ritual) →
        cast PiF (storm 2, GY = 1 ritual flashbackable) → flashback
        1 ritual (storm 3).  No finisher in hand to close — the
        chain produces storm but no payoff to deliver it.

        Viability must be 0 (closer missing = no chain regardless
        of post-chain GY)."""
        game = GameState(rng=random.Random(0))
        _setup_storm(game, card_db, mountains=5)
        ritual = _add(game, card_db, "Desperate Ritual",
                      controller=0, zone="hand")
        _add(game, card_db, "Past in Flames", controller=0, zone="hand")
        # No Grapeshot, no Wish — no finisher reachable.

        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        viability = flashback_chain_viable(ritual, me, snap)

        assert viability == 0.0, (
            f"PiF viability = {viability:.3f} with thin hand + no "
            f"closer.  Without a finisher (Grapeshot in hand or "
            f"tutor with target), the chain cannot close even with "
            f"flashback replays.  Viability must be 0."
        )


class TestStormFiresPiFChainWhenPostChainGYViable:
    """End-to-end: the storm AI should NOT hard-hold a ritual when
    the projected post-chain GY makes PiF a viable finisher path.

    This is the regression the old boolean gate caused: storm=0 with
    PiF + rituals + Grapeshot in hand, the AI saw "empty GY → PiF
    not viable" and clamped the ritual.  Result: speculative-chain
    HARD_HOLD on a chain that would actually be lethal."""

    def test_pif_chain_not_hard_held_when_post_chain_gy_viable(
            self, card_db):
        """Storm at storm=0 with the 2-ritual + PiF + Grapeshot
        hand.  The chain IS viable (rituals about to enter GY, PiF
        will flashback them).  Ritual EV must NOT be the
        STORM_HARD_HOLD sentinel.

        Pre-fix: `_has_viable_pif` returned False (current GY empty)
        → no finisher path → STORM_HARD_HOLD clamp.

        Post-fix: `flashback_chain_viable` returns > 0 (post-chain
        GY projection sees rituals about to land) → finisher path
        confirmed → ritual scores via projection arithmetic."""
        from ai.combo_calc import STORM_HARD_HOLD

        game = GameState(rng=random.Random(0))
        _setup_storm(game, card_db, mountains=6)
        ritual = _add(game, card_db, "Desperate Ritual",
                      controller=0, zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
        _add(game, card_db, "Past in Flames", controller=0, zone="hand")
        _add(game, card_db, "Grapeshot", controller=0, zone="hand")

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(ritual, snap, game, me, opp)

        # The STORM_HARD_HOLD sentinel is a strongly negative
        # number (-NO_CLOCK * ratio_of_safety).  The ritual EV
        # for a viable PiF chain must not approach it.
        assert ev > STORM_HARD_HOLD + 1.0, (
            f"Ritual at storm=0 with viable PiF post-chain GY "
            f"projection scored EV={ev:.2f}, close to or below "
            f"STORM_HARD_HOLD ({STORM_HARD_HOLD:.2f}).  The chain "
            f"IS viable when PiF's GY-projection is included: "
            f"2 rituals enter GY, PiF flashbacks them, Grapeshot "
            f"closes.  The clamp is over-firing."
        )


class TestPiFUnaffectedWhenNoFlashbackInHand:
    """Contract: when there is no FLASHBACK-tagged card in hand,
    `flashback_chain_viable` is a no-op returning 0.  The function
    must not invent a flashback path from oracle text or card
    names."""

    def test_unaffected_when_no_flashback_in_hand(self, card_db):
        """Hand: 2 rituals + Grapeshot only.  No PiF, no other
        FLASHBACK card.  Viability must be exactly 0.0."""
        game = GameState(rng=random.Random(0))
        _setup_storm(game, card_db, mountains=4)
        ritual = _add(game, card_db, "Desperate Ritual",
                      controller=0, zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
        _add(game, card_db, "Grapeshot", controller=0, zone="hand")
        _add(game, card_db, "Pyretic Ritual", controller=0,
             zone="graveyard")  # GY fuel exists but nothing to grant FB

        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        viability = flashback_chain_viable(ritual, me, snap)

        assert viability == 0.0, (
            f"No FLASHBACK card in hand, viability = {viability}. "
            f"Must be exactly 0.0 — the function returns no-op when "
            f"the prerequisite (a flashback-granting card in hand) "
            f"is missing."
        )
