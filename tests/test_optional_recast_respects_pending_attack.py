"""An OPTIONAL free recast is declined when it would forfeit an attack.

Mechanic: rebound (CR 702.88b) and every other "you may cast it" free
recast from exile is a CHOICE, not a mandatory action — "you *may* cast
that card". When the recast is a state-resetting spell (a blink/flicker:
CR 400.7, the permanent returns as a NEW object, summoning-sick and
carrying only its printed keywords), taking it in the controller's
upkeep removes that permanent's ability to attack THIS turn. The rule
this file pins:

    An optional recast that would return a creature to a state where it
    cannot attack this turn must be DECLINED when the creature would
    otherwise attack and the recast's own value does not cover the
    forfeited combat step.

The price of the forfeited step is the existing clock primitive
`ai.clock.forfeited_attack_clock_impact` (power kill-fraction + lifelink
swing, PR #557), converted to EV via CLOCK_IMPACT_LIFE_SCALING — the
same charge `ai/ev_player._score_spell` applies to a Main-1 blink. The
choice belongs to the AI layer: the engine offers the option (it knows
the recast is legal), `EVPlayer.decide_optional_recast` decides.

Root cause: `docs/diagnostics/2026-08-27_dimir_overperformance_root_cause.md`
(win 8, Reanimator s62000 G1) — the reanimated 7/7's rebound blink was
taken at EVERY upkeep, re-summoning-sicking the body, so it attacked
exactly once per game and the controller lost with an untapped 7/7 on
board.

Class size: every rebound spell x every attack-capable creature, plus
any future "cast it for free from exile at the beginning of your
upkeep" recast. Real-DB cards are fixture carriers only; the
implementation names none of them.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_runner import GameRunner
from engine.game_state import GameState, Phase

DECK = "Goryo's Vengeance"
RECAST = "Ephemerate"          # {W} instant, blink tag, Rebound
BODY = "Griselbrand"           # 7/7 flying lifelink, no printed haste
HASTY = "Goblin Guide"         # printed haste — survives a blink
LAND = "Plains"


def _make_game():
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = DECK
    game.players[1].deck_name = "Dimir Midrange"
    game.active_player = 0
    game.current_phase = Phase.UPKEEP
    return game


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


def _pending_recast(game, card_db, controller=0):
    """A rebound-exiled spell awaiting its free upkeep recast."""
    rc = _add(game, card_db, RECAST, controller, "exile")
    rc._rebound_controller = controller
    game._rebound_cards = [rc]
    return rc


def _setup(card_db, body_name=BODY, tapped=False, lands=1):
    game = _make_game()
    for _ in range(lands):
        land = _add(game, card_db, LAND, 0, "battlefield")
        land.tapped = False
    body = _add(game, card_db, body_name, 0, "battlefield")
    body.tapped = tapped
    rc = _pending_recast(game, card_db)
    return game, rc, body


def _player(seed=0):
    return EVPlayer(player_idx=0, deck_name=DECK, rng=random.Random(seed))


class TestOptionalRecastDecision:
    """The AI seam: `EVPlayer.decide_optional_recast`."""

    def test_recast_that_resets_a_pending_attack_is_declined(self, card_db):
        game, rc, body = _setup(card_db)
        assert body.can_attack, "fixture body must be able to attack"

        assert _player().decide_optional_recast(game, rc) is False, (
            "the free recast re-blinks an attack-capable body into "
            "summoning sickness (CR 400.7) — it must be declined while "
            "the body would otherwise attack"
        )

    def test_recast_is_taken_when_no_attack_is_forfeited(self, card_db):
        """Body already tapped (it attacked, or was tapped for a cost):
        there is no combat step left to lose, so the free recast — which
        costs nothing — is taken."""
        game, rc, body = _setup(card_db, tapped=True)
        assert not body.can_attack

        assert _player().decide_optional_recast(game, rc) is True, (
            "a free recast with no forfeited attack must be taken")

    def test_recast_is_taken_when_the_body_keeps_haste_through_the_reset(
            self, card_db):
        """PRINTED haste survives re-entry (CR 400.7 — the new object
        has its printed abilities), so the reset costs no combat step
        and the recast is taken."""
        game, rc, body = _setup(card_db, body_name=HASTY)
        assert body.can_attack

        assert _player().decide_optional_recast(game, rc) is True, (
            "a printed-haste body loses no attack to the reset — the "
            "recast must still be taken")

    def test_declined_recast_charge_is_the_clock_price_of_one_combat_step(
            self, card_db):
        """Derivation pin: the decline is driven by the shared clock
        primitive, not a flat nudge — the charge equals
        `forfeited_attack_clock_impact` x CLOCK_IMPACT_LIFE_SCALING."""
        from ai.clock import forfeited_attack_clock_impact
        from ai.scoring_constants import CLOCK_IMPACT_LIFE_SCALING

        game, _rc, body = _setup(card_db)
        snap = snapshot_from_game(game, 0)
        kws = {str(getattr(k, 'value', k)).lower() for k in body.keywords}
        expected = (forfeited_attack_clock_impact(body.power or 0, kws, snap)
                    * CLOCK_IMPACT_LIFE_SCALING)

        assert expected > 0
        assert _player()._forfeited_attack_charge(body, snap) == pytest.approx(
            expected)


class TestOptionalRecastExecution:
    """The engine seam: the upkeep recast pass honours the decision."""

    def _run_upkeep(self, game, ai, card_db=None):
        """One upkeep recast window, followed by the stack resolution
        the turn loop performs at the end of the upkeep step."""
        GameRunner(card_db)._process_rebound_recasts(game, 0, ai)
        game.resolve_stack()

    def test_upkeep_pass_does_not_reset_a_body_that_would_attack(
            self, card_db):
        game, rc, body = _setup(card_db)
        entry_seq = body.battlefield_entry_seq

        self._run_upkeep(game, _player(), card_db)

        assert body.battlefield_entry_seq == entry_seq, (
            "the body was blinked (new object) at upkeep — its attack "
            "this turn is forfeited")
        assert body.can_attack, "body lost its attack to the free recast"
        assert not any("Blink" in line for line in game.log), (
            f"an attack-forfeiting recast was taken: {game.log}")

    def test_declined_recast_opportunity_does_not_recur(self, card_db):
        """CR 702.88b: the recast is offered once, at the beginning of
        the next upkeep. Declining spends the opportunity — the card
        stays exiled and is not re-offered on later upkeeps (the
        replayed failure re-blinked at EVERY upkeep)."""
        game, rc, body = _setup(card_db)
        ai = _player()

        self._run_upkeep(game, ai, card_db)
        assert rc not in getattr(game, '_rebound_cards', []), (
            "declined recast is still queued — it will be re-offered")
        assert rc in game.players[0].exile, (
            "a declined rebound card must remain in exile, not vanish")

        entry_seq = body.battlefield_entry_seq
        self._run_upkeep(game, ai, card_db)
        self._run_upkeep(game, ai, card_db)
        assert body.battlefield_entry_seq == entry_seq, (
            "the recast fired again on a later upkeep")

    def test_upkeep_pass_takes_the_recast_when_nothing_is_forfeited(
            self, card_db):
        """Counterfactual: with the body tapped, the same pass DOES take
        the free recast — the gate is the forfeited attack, not the
        recast itself."""
        game, rc, body = _setup(card_db, tapped=True)
        entry_seq = body.battlefield_entry_seq

        self._run_upkeep(game, _player(), card_db)

        assert body.battlefield_entry_seq != entry_seq, (
            "the free recast was skipped even though no attack was at "
            "stake")
        assert rc not in getattr(game, '_rebound_cards', [])

    def test_free_recast_from_exile_does_not_earn_another_recast(
            self, card_db):
        """CR 702.88a scopes the rebound replacement to a spell cast
        FROM HAND. The free recast is cast from exile, so it resolves
        into the graveyard — one repetition, not an unbounded loop."""
        game, rc, body = _setup(card_db, tapped=True)

        self._run_upkeep(game, _player(), card_db)

        assert not getattr(game, '_rebound_cards', []), (
            "the free recast re-queued itself for another free recast")
        assert rc in game.players[0].graveyard, (
            f"recast spell should resolve to the graveyard; it is in "
            f"{rc.zone}")

    def test_recast_without_a_legal_target_leaves_the_card_exiled(
            self, card_db):
        """Target gate (pre-existing): a blink with no creature to
        target is not cast — and the uncast card stays in exile rather
        than being dropped out of every zone."""
        game = _make_game()
        _add(game, card_db, LAND, 0, "battlefield")
        rc = _pending_recast(game, card_db)

        self._run_upkeep(game, _player(), card_db)

        assert rc in game.players[0].exile
        assert rc not in getattr(game, '_rebound_cards', [])
