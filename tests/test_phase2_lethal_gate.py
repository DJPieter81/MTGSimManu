"""Phase-2c.1: state-query routing gate for OutcomeDistribution dispatcher.

The earlier Phase-2b approach routed *all* combo categories
(ritual / cascade / reanimate / finisher / tutor) through the
single-turn `OutcomeDistribution` builder regardless of game state.
That regressed Storm 24%->3.8% and Goryo's 24%->11% because rituals
cast at storm=0 with no finisher in reach are *not* a single-turn
problem — the legacy `_combo_modifier` correctly models them
multi-turn.

Phase-2c.1 narrows the dispatcher to *lethal-now* chains only.  The
gate consults `combo_chain.find_all_chains(...)` for the live hand,
mana, medallions and storm count.  Routing through the dispatcher
happens iff a chain exists with `payoff_deals_damage=True` AND
`storm_damage >= opp_life`.  Otherwise we fall through to the unchanged
legacy `compute_play_ev` + `_combo_modifier` path.

These tests pin the routing decision (NOT the numeric outcome of the
distribution math, which is covered by `test_combo_distribution.py`).
"""
from __future__ import annotations

import random

import pytest

import ai.outcome_ev as outcome_ev
from ai.ev_evaluator import snapshot_from_game
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
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _setup_storm(card_db, hand_names, gy_names=(), bf_names=(),
                 lands=2, opp_lands=2, storm_count=0,
                 medallions=0, opp_life=20, my_life=20,
                 turn_number=4):
    """Mid-turn GameState in a Ruby Storm shell."""
    game = GameState(rng=random.Random(0))
    for _ in range(lands):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    for _ in range(medallions):
        _add(game, card_db, "Ruby Medallion", controller=0,
             zone="battlefield")
    for _ in range(opp_lands):
        _add(game, card_db, "Plains", controller=1, zone="battlefield")
    cards = []
    for n in hand_names:
        cards.append(_add(game, card_db, n, controller=0, zone="hand"))
    for n in gy_names:
        _add(game, card_db, n, controller=0, zone="graveyard")
    for n in bf_names:
        _add(game, card_db, n, controller=0, zone="battlefield")

    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = turn_number
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = storm_count
    game._global_storm_count = storm_count
    game.players[0].life = my_life
    game.players[1].life = opp_life
    return game, cards


def _build_player(deck_name="Ruby Storm"):
    return EVPlayer(player_idx=0, deck_name=deck_name,
                    rng=random.Random(0))


# ──────────────────────────────────────────────────────────────────
# Routing — lethal-now → dispatcher; otherwise → legacy
# ──────────────────────────────────────────────────────────────────


def test_lethal_now_routes_to_distribution(card_db, monkeypatch):
    """When a lethal-this-turn chain physically exists in
    `find_all_chains`, the score must come from the OutcomeDistribution
    dispatcher, NOT from `_combo_modifier`.

    Setup: storm=4, hand has Grapeshot + extra ritual, 1 Medallion,
    plenty of mana, opp at 4 life. Casting one more ritual gives
    storm_count=5 + Grapeshot copy storm=6 hits >= 4 life lethal.
    """
    # Force the flag ON for this test (Phase 2c.1 ships it ON anyway,
    # but the test should be independent of module-level state).
    monkeypatch.setattr(outcome_ev, 'OUTCOME_DIST_COMBO', True)

    game, cards = _setup_storm(
        card_db,
        hand_names=["Pyretic Ritual", "Grapeshot"],
        lands=4, medallions=1, storm_count=4, opp_life=4,
    )
    ritual = cards[0]
    player = _build_player()

    # Spy on dispatcher path: count calls to build_combo_distribution.
    dispatcher_calls = {'count': 0, 'last_dist_ev': None}
    real_builder = outcome_ev.build_combo_distribution

    def spy_builder(*args, **kwargs):
        dist = real_builder(*args, **kwargs)
        dispatcher_calls['count'] += 1
        if dist is not None:
            dispatcher_calls['last_dist_ev'] = dist.expected_value()
        return dist

    # Monkey-patch the symbol that `_score_spell` imports locally.
    monkeypatch.setattr(outcome_ev, 'build_combo_distribution', spy_builder)

    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]
    score = player._score_spell(ritual, snap, game, me, opp)

    assert dispatcher_calls['count'] >= 1, (
        "Lethal-now chain must route through OutcomeDistribution "
        "dispatcher; build_combo_distribution was not invoked."
    )
    # The dispatcher returns a positive value scaled by LETHAL_VALUE
    # (= 100.0, the rules-constant lethal award also used at line 1460).
    # Score should reflect the lethal threat, hence be substantially
    # positive — much higher than the pass_threshold.
    assert score > player.profile.pass_threshold + 5.0, (
        f"Lethal-routed ritual returned score={score:.2f}, "
        f"expected >> pass_threshold ({player.profile.pass_threshold})"
    )


def test_no_lethal_now_falls_through(card_db, monkeypatch):
    """storm=0, single Pyretic Ritual, no Grapeshot in hand or GY.
    No chain in `find_all_chains` deals lethal. The gate must bypass
    the dispatcher and run the legacy projection (`compute_play_ev` +
    `_combo_modifier`) verbatim."""
    monkeypatch.setattr(outcome_ev, 'OUTCOME_DIST_COMBO', True)

    game, cards = _setup_storm(
        card_db,
        hand_names=["Pyretic Ritual"],   # no finisher in hand
        lands=2, medallions=0, storm_count=0, opp_life=20,
    )
    ritual = cards[0]
    player = _build_player()

    dispatcher_calls = {'count': 0}
    real_builder = outcome_ev.build_combo_distribution

    def spy_builder(*args, **kwargs):
        dispatcher_calls['count'] += 1
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(outcome_ev, 'build_combo_distribution', spy_builder)

    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]

    # Capture legacy score by also disabling the dispatcher entirely.
    monkeypatch.setattr(outcome_ev, 'OUTCOME_DIST_COMBO', False)
    legacy_score = player._score_spell(ritual, snap, game, me, opp)

    # Now re-enable; must produce the SAME score because the gate
    # falls through.
    monkeypatch.setattr(outcome_ev, 'OUTCOME_DIST_COMBO', True)
    dispatcher_calls['count'] = 0
    gated_score = player._score_spell(ritual, snap, game, me, opp)

    assert dispatcher_calls['count'] == 0, (
        "No-lethal-now case must bypass build_combo_distribution; "
        f"dispatcher was invoked {dispatcher_calls['count']}x."
    )
    assert abs(gated_score - legacy_score) < 1e-9, (
        f"Gated score ({gated_score:.6f}) must equal legacy score "
        f"({legacy_score:.6f}) when no lethal chain exists."
    )


def test_cascade_falls_through_even_with_combo_category(card_db,
                                                        monkeypatch):
    """Cascade enablers (Living End shell) classify as 'cascade' but
    the cascaded Living End is a sorcery (not a damage payoff), so
    `payoff_deals_damage=False` and `storm_damage=0`. The gate must
    fall through to the legacy path even though category is 'cascade'.
    """
    monkeypatch.setattr(outcome_ev, 'OUTCOME_DIST_COMBO', True)

    game = GameState(rng=random.Random(0))
    for _ in range(3):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    for _ in range(2):
        _add(game, card_db, "Plains", controller=1, zone="battlefield")
    cascade = _add(game, card_db, "Demonic Dread", controller=0,
                   zone="hand")
    game.players[0].deck_name = "Living End"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 3
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = 0
    game._global_storm_count = 0
    game.players[0].life = 20
    game.players[1].life = 20

    player = _build_player(deck_name="Living End")

    dispatcher_calls = {'count': 0}
    real_builder = outcome_ev.build_combo_distribution

    def spy_builder(*args, **kwargs):
        dispatcher_calls['count'] += 1
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(outcome_ev, 'build_combo_distribution', spy_builder)

    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]

    monkeypatch.setattr(outcome_ev, 'OUTCOME_DIST_COMBO', False)
    legacy_score = player._score_spell(cascade, snap, game, me, opp)

    monkeypatch.setattr(outcome_ev, 'OUTCOME_DIST_COMBO', True)
    dispatcher_calls['count'] = 0
    gated_score = player._score_spell(cascade, snap, game, me, opp)

    assert dispatcher_calls['count'] == 0, (
        "Cascade enabler with non-damage payoff must bypass dispatcher; "
        f"build_combo_distribution invoked {dispatcher_calls['count']}x."
    )
    assert abs(gated_score - legacy_score) < 1e-9, (
        f"Cascade gated score ({gated_score:.6f}) must equal legacy "
        f"({legacy_score:.6f}) — payoff_deals_damage=False blocks routing."
    )
