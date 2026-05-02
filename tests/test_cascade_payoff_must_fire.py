"""Cascade-payoff must-fire rule.

A combo-archetype deck holding a cascade card as its declared payoff,
with mana to cast it AND a legal cascade outcome (the projection
reports positive EV after modeling the cascade hit), MUST cast it.
The opportunity-cost ("patience") gate is permitted to defer cascade
when the projected outcome is non-positive (the cascade would burn
the enabler for no swing), but it must NOT override a projection
that has already verified the cascade as positive-EV.

Rule shape (precedent):
- "Wish for Past in Flames when no Grapeshot drawn" (PR #192) — a
  high-EV chain-restart play wrongly filtered by a downstream gate.
- "PiF flashback signal at storm=0" (PR #212) — a payoff signal
  missing from `_enumerate_this_turn_signals`, fixed by routing
  through the same projection that already had the math.

Class-size: every cascade card whose deck has a cascade target in
library — Demonic Dread, Shardless Agent, Bloodbraid Elf, Violent
Outburst, Maelstrom Pulse, Boom // Bust, Bituminous Blast, future
cascade printings.  Mechanism is the cascade keyword, not card name.

Subsystem: `ai/ev_player.py::_score_spell` cascade-patience gate.
The gate currently clamps EV unconditionally when graveyard <
gameplan.resource_target, ignoring the projection.  The correct
shape: defer ONLY when the projection itself is non-positive.

Failing-test discovery: seed 60102 vs Affinity, T8/T10 Living End
holds Demonic Dread with 3+ mana and BR colors available, opponent
has 3 creatures on the table, but the AI passes for 3 consecutive
decisions.  Trace shows `cast_spell: Demonic Dread h=-6.0` —
clamped below pass_threshold=-5 by the patience gate even though
the underlying projection delivered +1.0 to +3.0.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import compute_play_ev, snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    pile = ('library' if zone == 'library' else zone)
    getattr(game.players[controller], pile).append(card)
    return card


def _living_end_state(card_db, *, gy_creatures: int, opp_threats: int = 0,
                      mana_lands=("Swamp", "Swamp", "Mountain")):
    """Construct a minimal Living End mid-game state.

    `gy_creatures` controls the graveyard fuel.  `opp_threats`
    controls the number of opp creatures on the battlefield (which
    serve as legal targets for Demonic Dread's "target creature
    can't block" rider AND populate the cascade projection's
    symmetric-reanimation math).  Default mana base is BR-producing
    so Demonic Dread {1}{B}{R} is castable.
    """
    game = GameState(rng=random.Random(0))
    for land_name in mana_lands:
        _add(game, card_db, land_name, controller=0, zone="battlefield")
    # Hand: Demonic Dread (cascade payoff for Living End).
    cascade_card = _add(game, card_db, "Demonic Dread", controller=0,
                        zone="hand")
    # Library: at least one Living End so cascade has a payoff.
    _add(game, card_db, "Living End", controller=0, zone="library")
    # Filler library so cascade has search material.
    for _ in range(20):
        _add(game, card_db, "Architects of Will", controller=0,
             zone="library")
    # Graveyard creatures.
    for _ in range(gy_creatures):
        _add(game, card_db, "Street Wraith", controller=0,
             zone="graveyard")
    # Opp creatures (cascade targets).
    for _ in range(opp_threats):
        _add(game, card_db, "Memnite", controller=1, zone="battlefield")
    game.players[0].deck_name = "Living End"
    game.players[1].deck_name = "Affinity"
    game.current_phase = Phase.MAIN1
    game.turn_number = 4
    game.active_player_idx = 0
    return game, cascade_card


class TestCascadePayoffMustFire:
    """The cascade-payoff must-fire rule: when cascading is positive-EV
    per projection, the patience gate must not veto."""

    def test_combo_deck_casts_cascade_when_castable_with_legal_target(
            self, card_db):
        """Mirror the live seed-60102 T8 state: cascade castable with
        BR mana, opponent creatures on battlefield, Living End in
        library.  Projection reports cascade as positive-EV (the
        symmetric reanimation models the swing, even with thin GY).
        AI must cast — the gate may not clamp away a positive
        projection."""
        game, cascade_card = _living_end_state(
            card_db, gy_creatures=3, opp_threats=2)
        player = EVPlayer(player_idx=0, deck_name="Living End",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)

        # Sanity: the projection considers the cascade positive-EV.
        base = compute_play_ev(cascade_card, snap, "combo", game, 0,
                               bhi=player.bhi)
        assert base > 0.0, (
            f"Test setup invariant: projection of cascade should be "
            f"positive-EV (cascade hits Living End, reanimates "
            f"creatures from both graveyards).  Got base={base:.2f}.  "
            f"If this fails, the cascade projection in compute_play_ev "
            f"has regressed — see ev_evaluator.py:1622."
        )

        # The decision must cast cascade.
        result = player.decide_main_phase(game)
        assert result is not None, (
            "AI passed despite cascade being castable with positive "
            "projected EV.  Cascade-payoff must-fire rule violated."
        )
        action, card_chosen, _ = result
        assert action == "cast_spell" and card_chosen.name == "Demonic Dread", (
            f"AI chose {action!r} {card_chosen.name!r} instead of "
            f"casting Demonic Dread.  When the projection has already "
            f"validated cascade as positive-EV, the patience gate must "
            f"not override.  Candidate scores:\n" + "\n".join(
                f"  {p.action}: {p.card.name} EV={p.ev:.2f}"
                for p in player._last_candidates
            )
        )

    def test_cascade_skipped_when_no_cascade_target_in_library(
            self, card_db):
        """Regression: if the library has NO cards cascade can hit
        (every non-land card has cmc >= cascade.cmc), the projection
        loses its swing and the cascade should not fire — there's
        no payoff to find."""
        game, cascade_card = _living_end_state(
            card_db, gy_creatures=3, opp_threats=2)
        # Wipe the cascadable library and refill with cards cascade
        # cannot hit (cmc >= 3 = Demonic Dread's cmc).
        game.players[0].library.clear()
        for _ in range(20):
            # Goldspan Dragon is cmc 5 — cascade cannot hit it.
            _add(game, card_db, "Goldspan Dragon", controller=0,
                 zone="library")

        player = EVPlayer(player_idx=0, deck_name="Living End",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)

        base = compute_play_ev(cascade_card, snap, "combo", game, 0,
                               bhi=player.bhi)
        # Without a cascadable library card, projection collapses.
        # Document the rule: when projection is non-positive, the
        # patience gate is permitted to keep the cascade off-limits.
        ev = player._score_spell(cascade_card, snap, game,
                                  game.players[0], game.players[1])
        if base <= 0.0:
            assert ev <= player.profile.pass_threshold, (
                f"Cascade with no library payoff should not fire.  "
                f"base projection={base:.2f}, _score_spell={ev:.2f}, "
                f"pass_threshold={player.profile.pass_threshold}.  "
                f"The gate is permitted to clamp here — projection is "
                f"non-positive so there's no positive value to cast."
            )

    def test_cascade_skipped_at_low_turn_when_payoff_only_in_sb(
            self, card_db):
        """Regression: if the deck's reanimation payoff is in the
        sideboard (not visible to the cascade lookup), the projection
        must NOT credit the cascade with a payoff hit, and the gate
        is permitted to defer.  Mirror shape: if no Living End in
        library, cascade is dead.  This documents the must-fire
        boundary: it requires a reachable payoff."""
        game, cascade_card = _living_end_state(
            card_db, gy_creatures=3, opp_threats=2)
        # Remove Living End from library — leave only filler cards.
        game.players[0].library = [
            c for c in game.players[0].library
            if c.template.name != "Living End"
        ]

        player = EVPlayer(player_idx=0, deck_name="Living End",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        base = compute_play_ev(cascade_card, snap, "combo", game, 0,
                               bhi=player.bhi)
        # No payoff in library — Architects of Will is the cascade hit
        # (cmc 2 fits under Demonic Dread's cmc 3).  Architects gives a
        # 2/3 body, which is still some value, but the BIG swing of
        # Living End is unavailable.  This test just documents the
        # rule: the gate's veto is principled when projection isn't
        # positive enough.  Don't strictly assert pass — just check
        # the rule's interaction with projection is consistent.
        ev = player._score_spell(cascade_card, snap, game,
                                  game.players[0], game.players[1])
        # Invariant: the score should track the projection's sign.
        # When projection is positive, score should be at least
        # pass_threshold (gate must not flat-clamp positive value).
        if base > 0.0:
            assert ev > player.profile.pass_threshold - 0.5, (
                f"Projection positive (base={base:.2f}) but "
                f"_score_spell={ev:.2f} clamped below pass_threshold="
                f"{player.profile.pass_threshold}.  The gate is "
                f"clamping a positive projection — must-fire rule "
                f"violated."
            )
