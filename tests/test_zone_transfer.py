"""W0-C — zone_transfer primitive: per-kind trigger fan-out.

This module pins the rule that motivates the M1+R1 audit finding from
docs/history/audits/2026-05-16_5panel_bo3_audit.md:

> CR 121.1c — Putting a card into a hidden zone in a way that doesn't
> use the word "draw" is not a draw event. Impulse-style reveals
> (Reckless Impulse / Wrenn's Resolve / Glimpse the Impossible) exile
> cards face-up with "you may play this turn"; they do NOT trigger
> "whenever you draw" / "whenever an opponent draws" abilities.

The current `engine/game_state.py:draw_cards` is the single drain for
both real draws and impulse-style approximations, which collapses two
different transfer kinds into one trigger fan-out and produces the
storm_vs_dimir G1T4 self-kill recorded in
`replays/audit_storm_vs_dimir_s60101.txt` (Storm 10→0 from
Bowmasters/Sheoldred triggers on its own impulse-draws).

The structural fix is `engine/zone_transfer.py`: each
`TransferKind` is its own dispatch entry, so DRAW fires draw triggers
and IMPULSE_REVEAL does not. These tests are rule-phrased — they name
the mechanic, never a deck.

Note: this Wave 0 module only adds the primitive. Wave 1a-1 will
migrate the impulse-draw call sites in oracle_resolver.py to use it
and remove the ad-hoc fan-out from game_state.py.
"""
from __future__ import annotations

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState
from engine.mana import ManaCost
from engine.zone_transfer import TransferKind, transfer


# ─── helpers ────────────────────────────────────────────────────────


def _make_vanilla_card(game: GameState, name: str, controller: int,
                       zone: str = "library") -> CardInstance:
    """Build a synthetic vanilla card with no oracle text. Used as the
    'card being moved' across transfer kinds — its identity is
    irrelevant; the transfer kind decides what triggers fire."""
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[],
        power=1, toughness=1, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    return card


def _make_bowmasters_proxy(game: GameState, controller: int) -> CardInstance:
    """Build an opponent-side permanent whose oracle text matches the
    'whenever an opponent draws a card …deals 1 damage' clause shape.
    Identity-free; the engine's existing fan-out recognises the shape."""
    tmpl = CardTemplate(
        name="Bowmasters Proxy",
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1, black=1),
        supertypes=[], subtypes=[],
        power=1, toughness=1, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        # Match the "whenever an opponent draws" oracle shape — the
        # engine's fan-out keys off this phrase, not the card name.
        # The "except the first" clause is preserved so the
        # draw-step-first-draw exemption fires.
        oracle_text=(
            "Whenever an opponent draws a card, except the first one "
            "they draw in each of their draw steps, this creature deals "
            "1 damage to that player."
        ),
        tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _fresh_game() -> GameState:
    """Build a GameState with two players, no decks; deterministic."""
    import random
    g = GameState(rng=random.Random(0))
    return g


# ─── DRAW: real draws fire 'whenever you draw' triggers ─────────────


def test_draw_kind_fires_whenever_you_draw_triggers():
    """A DRAW transfer is a true CR 121.1 draw event; an opponent's
    'whenever an opponent draws' trigger fires and deals damage.

    This pins the positive case: real draws keep their fan-out.
    """
    game = _fresh_game()
    drawer = 0
    opponent = 1

    # Opponent controls a Bowmasters-shape trigger source.
    _make_bowmasters_proxy(game, controller=opponent)

    # Drawer has a card on top of library to draw.
    card = _make_vanilla_card(game, "TopOfLibrary", controller=drawer,
                              zone="library")
    game.players[drawer].library.append(card)

    # Force a non-draw-step phase so the "except first draw step draw"
    # exemption does not apply. MAIN1 is what impulse spells resolve in.
    from engine.game_state import Phase
    game.current_phase = Phase.MAIN1
    # Also bump the drawer's draw counter past the first-draw-step draw,
    # so even if the engine were to fall back to the exemption check
    # the trigger still fires unambiguously.
    game.players[drawer].cards_drawn_this_turn = 5

    life_before = game.players[drawer].life
    transfer(game, card,
             src_zone="library", dst_zone="hand",
             kind=TransferKind.DRAW, controller=drawer)

    # Card moved.
    assert card in game.players[drawer].hand
    assert card not in game.players[drawer].library
    # Bowmasters-shape trigger fired: drawer took damage.
    assert game.players[drawer].life < life_before, (
        "DRAW must fan out 'whenever an opponent draws' triggers"
    )


# ─── IMPULSE_REVEAL: the audit's smoking gun ────────────────────────


def test_impulse_reveal_does_not_fire_draw_triggers():
    """An IMPULSE_REVEAL transfer is library→exile with may-play; it
    is NOT a draw event under CR 121.1c. 'Whenever an opponent draws'
    triggers MUST NOT fire.

    This is the rule-phrased form of the M1+R1 audit finding:
    Reckless Impulse / Wrenn's Resolve / Glimpse the Impossible should
    not trigger Bowmasters/Sheoldred. The replay artefact is
    `replays/audit_storm_vs_dimir_s60101.txt` G1T4 — Storm self-killed
    10→0 from this bug.
    """
    game = _fresh_game()
    revealer = 0
    opponent = 1

    _make_bowmasters_proxy(game, controller=opponent)

    card = _make_vanilla_card(game, "TopOfLibrary", controller=revealer,
                              zone="library")
    game.players[revealer].library.append(card)

    from engine.game_state import Phase
    game.current_phase = Phase.MAIN1
    game.players[revealer].cards_drawn_this_turn = 5

    life_before = game.players[revealer].life
    transfer(game, card,
             src_zone="library", dst_zone="exile",
             kind=TransferKind.IMPULSE_REVEAL, controller=revealer)

    # Card moved to exile (impulse zone), not hand.
    assert card in game.players[revealer].exile
    assert card not in game.players[revealer].library
    assert card not in game.players[revealer].hand
    # Critically: NO draw trigger fired. Life unchanged.
    assert game.players[revealer].life == life_before, (
        "IMPULSE_REVEAL must NOT fan out 'whenever an opponent draws' "
        "triggers (CR 121.1c — not a draw event)"
    )


# ─── ETB: uniform across permanent types (incl. lands) ─────────────


def test_etb_kind_fires_etb_triggers_on_lands():
    """An ETB transfer of a land into the battlefield uses the same
    trigger fan-out as a creature ETB. This pins the R3 finding from
    the audit: land ETB triggers must fire (Meticulous Archive's
    surveil, Stormcarved Coast's scry, fetch-replacement ETBs).

    We verify by registering a permanent whose ETB the existing
    `EFFECT_REGISTRY` will execute — the transfer primitive's job is
    only to dispatch the fan-out; the existing registry decides what
    actually happens.
    """
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming

    game = _fresh_game()
    controller = 0

    # Register a one-shot ETB handler under a synthetic card name so
    # the test does not depend on any specific card existing in the DB.
    # The handler mutates a sentinel on the card to prove the
    # fan-out reached it.
    fired = {"count": 0}

    test_name = "ZoneTransferTestPermanent"

    @EFFECT_REGISTRY.register(test_name, EffectTiming.ETB,
                              description="W0-C test sentinel")
    def _sentinel_etb(game, card, controller, targets=None, item=None):
        fired["count"] += 1

    try:
        tmpl = CardTemplate(
            name=test_name,
            card_types=[CardType.LAND],
            mana_cost=None,
            supertypes=[], subtypes=[],
            power=None, toughness=None, loyalty=None,
            keywords=set(), abilities=[],
            color_identity=set(), produces_mana=[],
            enters_tapped=False,
            oracle_text="",
            tags=set(),
        )
        card = CardInstance(
            template=tmpl, owner=controller, controller=controller,
            instance_id=game.next_instance_id(), zone="hand",
        )
        card._game_state = game
        game.players[controller].hand.append(card)

        transfer(game, card,
                 src_zone="hand", dst_zone="battlefield",
                 kind=TransferKind.ETB, controller=controller)

        assert card in game.players[controller].battlefield
        assert card.zone == "battlefield"
        assert fired["count"] == 1, (
            "ETB transfer must fan out to EFFECT_REGISTRY regardless of "
            "permanent type (land vs creature)"
        )
    finally:
        # Clean up the registry to avoid cross-test pollution.
        EFFECT_REGISTRY._handlers.pop(test_name, None)


# ─── Robustness: unknown kind raises, never silently drops ─────────


def test_unknown_kind_raises_not_silently_skips():
    """An unregistered TransferKind value must raise rather than
    silently skip the fan-out. Silent fallthrough is what allowed the
    impulse-draw bug to live: the call site assumed 'no trigger fan-out
    means safe', when it actually meant 'wrong trigger fan-out fired'.
    """
    game = _fresh_game()
    card = _make_vanilla_card(game, "Anything", controller=0, zone="hand")
    game.players[0].hand.append(card)

    with pytest.raises((ValueError, KeyError, TypeError)):
        transfer(game, card,
                 src_zone="hand", dst_zone="graveyard",
                 kind=99, controller=0)  # type: ignore[arg-type]
