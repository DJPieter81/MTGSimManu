"""W1a-3 — R3: land-ETB oracle triggers fire uniformly with creature ETB.

Per `docs/history/audits/2026-05-16_rules_audit.md` finding R3:

> Lands with the surveil-1 ETB trigger never fire it.
>
> Mechanism (generic): The engine resolves surveil only for *creature*
> spell-cast triggers (`cast_manager.py:1198-1205`); it has no
> land-ETB hook. A general "permanent ETB triggers from Oracle text"
> pass is missing for the land subset.

Class size: ~30 Modern-legal lands. The structural fix routes
land-entry through the same `zone_transfer.transfer(..., ETB)` /
`EFFECT_REGISTRY` (timing=ETB) dispatch the creature/spell-resolution
path uses, so a land's ETB and a creature's ETB are one mechanism.

These tests are rule-phrased — each pin a mechanic, not a card.
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


def _seed_library(game: GameState, card_db: CardDatabase, player_idx: int,
                   names: list[str]) -> list[CardInstance]:
    """Place named cards on top of `player_idx`'s library in given order
    (index 0 is the top of the library).
    """
    out: list[CardInstance] = []
    for name in names:
        tmpl = card_db.get_card(name)
        assert tmpl is not None, f"missing card in DB: {name}"
        card = CardInstance(
            template=tmpl,
            owner=player_idx,
            controller=player_idx,
            instance_id=game.next_instance_id(),
            zone="library",
        )
        card._game_state = game
        game.players[player_idx].library.append(card)
        out.append(card)
    return out


def _put_in_hand(game: GameState, card_db: CardDatabase, name: str,
                  player_idx: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl,
        owner=player_idx,
        controller=player_idx,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    card._game_state = game
    game.players[player_idx].hand.append(card)
    return card


# ─── R3 rule: surveil-land ETB fires surveil ────────────────────────


def test_etb_surveil_one_on_meticulous_archive_puts_top_to_graveyard_or_returns(card_db):
    """Playing a land whose oracle says 'When this land enters,
    surveil 1' moves the top of library into the graveyard (or keeps
    it on top — the deterministic AI policy bins it to maximise
    delirium / GY-payoff density, matching the existing creature
    spell-cast surveil branch).

    This is the rule-phrased version of "Meticulous Archive surveils
    when it enters" — the assertion looks for the surveil EVENT, not
    a card name.
    """
    from engine.land_manager import LandManager

    game = GameState(rng=random.Random(0))
    player_idx = 0

    # Top-of-library: a known nonland card. The AI's surveil policy
    # bins the top to the graveyard (existing convention from the
    # creature-cast surveil branch).
    top_cards = _seed_library(game, card_db, player_idx,
                               ["Lightning Bolt", "Plains", "Mountain"])
    top = top_cards[0]
    gy_before = list(game.players[player_idx].graveyard)
    lib_size_before = len(game.players[player_idx].library)

    archive = _put_in_hand(game, card_db, "Meticulous Archive", player_idx)
    LandManager.play_land(game, player_idx, archive)

    # Land entered.
    assert archive in game.players[player_idx].battlefield, (
        "Meticulous Archive must be on the battlefield after play_land"
    )
    # Library was inspected: the top card either binned to GY or kept
    # on top. Either way: surveil 1 was performed → library size
    # decreased by 0 or 1; if decreased, top card is in GY now.
    lib_size_after = len(game.players[player_idx].library)
    gy_after = list(game.players[player_idx].graveyard)
    # The surveil EVENT happened — top card is no longer "untouched
    # at index 0" if binned. We allow both outcomes (the AI policy
    # may keep or bin).
    surveil_happened = (
        # binned to GY
        (lib_size_after == lib_size_before - 1 and top in gy_after)
        # kept on top (deck stays, no GY change)
        or (lib_size_after == lib_size_before and gy_after == gy_before
            and game.players[player_idx].library[0] is top)
    )
    # Until R3 lands, NEITHER outcome happens: top stays on top and
    # the GY is unchanged from before. So the assertion below ALSO
    # holds in the broken state — we need a stronger oracle-trigger
    # signal. Use the log line: the existing surveil mechanism in the
    # engine writes a log line of the form '... surveil 1 → X to GY'
    # whenever it fires. Until R3, no such line exists for lands.
    surveil_log_lines = [l for l in game.log if "surveil" in l.lower()]
    assert surveil_log_lines, (
        "No surveil log line after Meticulous Archive ETB. The "
        "land-ETB trigger never fired. Game log:\n  "
        + "\n  ".join(game.log[-10:])
    )
    # Also assert the structural outcome — the top card is either
    # binned to graveyard or remained on top.
    assert surveil_happened, (
        f"Top card {top.name!r} was neither binned to GY nor on top "
        f"of library after surveil. "
        f"GY: {[c.name for c in gy_after]}, "
        f"Lib top: {game.players[player_idx].library[0].name if game.players[player_idx].library else 'EMPTY'}"
    )


# ─── Regression: non-surveil lands do not surveil ───────────────────


def test_etb_surveil_on_triome_does_not_fire(card_db):
    """A Triome (oracle: 'This land enters tapped' + cycling) does
    NOT have a surveil ETB trigger and therefore must NOT surveil
    when it enters.

    Regression-prevention for an over-eager generic ETB pass that
    would fire surveil on every tapped land.
    """
    from engine.land_manager import LandManager

    game = GameState(rng=random.Random(0))
    player_idx = 0

    _seed_library(game, card_db, player_idx,
                   ["Lightning Bolt", "Plains", "Mountain"])
    gy_before = list(game.players[player_idx].graveyard)
    lib_size_before = len(game.players[player_idx].library)

    triome = _put_in_hand(game, card_db, "Raugrin Triome", player_idx)
    LandManager.play_land(game, player_idx, triome)

    # Triome on battlefield, library and GY untouched.
    assert triome in game.players[player_idx].battlefield
    assert len(game.players[player_idx].library) == lib_size_before, (
        "Triome ETB must not draw or surveil"
    )
    assert game.players[player_idx].graveyard == gy_before, (
        "Triome ETB must not move cards to graveyard"
    )
    surveil_log_lines = [l for l in game.log if "surveil" in l.lower()]
    assert not surveil_log_lines, (
        f"Triome ETB unexpectedly surveiled. Lines: {surveil_log_lines}"
    )


# ─── Don't break the creature-ETB surveil path ──────────────────────


def test_legacy_creature_etb_surveil_still_fires(card_db):
    """A creature with 'When this creature enters, surveil N' (or the
    spell-cast variant on Dragon's Rage Channeler) must continue to
    surveil after R3's refactor.

    DRC's oracle is the spell-cast variant: "Whenever you cast a
    noncreature spell, surveil 1." This test exercises that path —
    DRC on battlefield, cast a noncreature spell, surveil triggers.
    """
    from engine.cast_manager import CastManager

    game = GameState(rng=random.Random(0))
    player_idx = 0

    # Seed library so surveil has a card to look at.
    _seed_library(game, card_db, player_idx,
                   ["Mountain", "Plains", "Forest"])
    # Give controller mana to cast Lightning Bolt.
    game.players[player_idx].mana_pool.add("R", 1)
    lib_size_before = len(game.players[player_idx].library)
    gy_before = list(game.players[player_idx].graveyard)

    # Put DRC on the battlefield.
    drc_tmpl = card_db.get_card("Dragon's Rage Channeler")
    assert drc_tmpl is not None
    drc = CardInstance(
        template=drc_tmpl, owner=player_idx, controller=player_idx,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    drc._game_state = game
    drc.enter_battlefield()
    drc.summoning_sick = False
    game.players[player_idx].battlefield.append(drc)

    # Cast Lightning Bolt — DRC's spell-cast surveil should fire.
    bolt = _put_in_hand(game, card_db, "Lightning Bolt", player_idx)
    ok = CastManager.cast_spell(game, player_idx, bolt)
    assert ok, "Lightning Bolt cast should succeed"

    surveil_log_lines = [l for l in game.log if "surveil" in l.lower()]
    assert surveil_log_lines, (
        "DRC spell-cast surveil did not fire. Log:\n  "
        + "\n  ".join(game.log[-10:])
    )


# ─── Structural test: land-ETB and creature-ETB share dispatch ──────


def test_land_etb_uniform_with_creature_etb(card_db):
    """The land-ETB dispatch path must reach `EFFECT_REGISTRY.execute(
    ..., EffectTiming.ETB, ...)` — the SAME dispatch that creature
    ETBs use.

    This pins the structural-uniformity claim from R3: 'a land's ETB
    fan-out is the same primitive as a creature's'. The test
    registers a sentinel handler under a real surveil-dual land name,
    plays that land, and asserts the sentinel fired.
    """
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    from engine.land_manager import LandManager

    game = GameState(rng=random.Random(0))
    player_idx = 0

    fired = {"count": 0}
    sentinel_name = "Meticulous Archive"
    # Save existing handlers (the production handler we add in this
    # diff) so we restore them after the test.
    saved = list(EFFECT_REGISTRY._handlers.get(sentinel_name, []))

    @EFFECT_REGISTRY.register(sentinel_name, EffectTiming.ETB,
                              description="W1a-3 dispatch sentinel",
                              priority=999)
    def _sentinel_etb(game, card, controller, targets=None, item=None):
        fired["count"] += 1

    try:
        # Seed library so the real handler (when it co-runs) doesn't
        # crash on an empty library.
        _seed_library(game, card_db, player_idx,
                       ["Plains", "Mountain", "Forest"])

        land = _put_in_hand(game, card_db, sentinel_name, player_idx)
        LandManager.play_land(game, player_idx, land)

        assert land in game.players[player_idx].battlefield
        assert fired["count"] >= 1, (
            "Land-ETB did not reach EFFECT_REGISTRY.execute "
            "(EffectTiming.ETB). The sentinel handler never fired, so "
            "land-ETB is NOT using the same dispatch as creature-ETB."
        )
    finally:
        # Restore original handlers (drop our sentinel; keep the real
        # production handler the production code adds).
        EFFECT_REGISTRY._handlers[sentinel_name] = saved
