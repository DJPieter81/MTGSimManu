"""Shared resolver for the "when ~ enters, draw N card(s)" ETB mechanic
(CR 121.1 card draw / CR 603.1 triggered-ability shape).

# Mechanic under test

`engine/card_effects.py` registered three independent EFFECT_REGISTRY
ETB handlers (Omnath, Locus of Creation; Quantum Riddler; Thought
Monitor) whose bodies were each a one-line `game.draw_cards(controller,
N)` call plus a log line — an identical shape, just with a different
fixed N. Per CLAUDE.md's ABSTRACTION CONTRACT, that duplication (three
handler bodies re-implementing the same mechanic) is the same class of
smell the ratchet targets for `card.name ==` checks, even though
`EFFECT_REGISTRY.register("Card Name", ...)` calls themselves are
invisible to `tools/check_abstraction.py`'s regex.

Unlike the burn-damage and nonland-permanent-removal clusters (which
needed a NEW shared function because no existing generic path covered
their shape), this cluster's generic path already existed:
`engine.oracle_resolver.resolve_etb_from_oracle` is the canonical
"no card-specific EFFECT_REGISTRY handler ⇒ try an oracle-driven
generic branch" fallback (`zone_transfer._fire_etb_triggers` and
`spell_resolution.ResolutionManager._handle_permanent_etb` both call
it identically). It just had no branch for plain "draw N cards" yet.
This item adds one (fullmatch-scoped to the WHOLE ability paragraph,
stricter than the co-occurrence-search draw-N branch already used for
spell resolution, because an ETB ability with any extra rider clause —
discard, life-gain, a conditional/derived amount — must NOT be
silently reduced to "just draw N" and drop the rider) and, having
proven each of the three registered handlers now redundant with the
generic fallback (see `TestGenericResolverCoversRegisteredHandlers`,
which resolves each real DB card via `_handle_permanent_etb` with its
registration temporarily removed BEFORE the handlers were deleted, per
CLAUDE.md's "verify before deleting" instruction), deletes all three
registrations.

Card names appear only as fixture carriers for the real-DB integration
tests; the resolver-unit tests below use synthetic `CardTemplate`
fixtures per this program's usual convention. The mechanic under test
is "a permanent's own ETB ability that does nothing but draw a fixed
number of cards", not any specific card.
"""
from __future__ import annotations

import random

import pytest

from engine.card_effects import EFFECT_REGISTRY, EffectTiming
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState
from engine.oracle_resolver import resolve_etb_from_oracle


# ─── synthetic fixtures for resolver-unit tests ────────────────────


def _synthetic_permanent(game, name, controller, oracle_text,
                          card_types=None, hand_size=0):
    tmpl = CardTemplate(
        name=name, card_types=card_types or [CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=1, toughness=1, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text=oracle_text, tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


def _game_with_library(n_cards=10, controller=0):
    """A game whose controller has a real library so draw_cards has
    something to draw (an empty library would SBA-lose the game and
    mask the assertion under test)."""
    game = GameState(rng=random.Random(0))
    filler_tmpl = CardTemplate(
        name="Filler Card", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=1, toughness=1, loyalty=None,
        keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False, oracle_text="", tags=set(),
    )
    game.players[controller].library = [
        CardInstance(template=filler_tmpl, owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone="library")
        for _ in range(n_cards)
    ]
    return game


class TestResolveEtbDrawNUnit:
    """The new "when ~ enters, draw N card(s)" branch of
    `resolve_etb_from_oracle` — resolver-level, synthetic fixtures."""

    def test_fixed_amount_word_draws_one(self):
        game = _game_with_library()
        card = _synthetic_permanent(
            game, "Test Fixture: Draw One", 0,
            "When this creature enters, draw a card.")

        before = len(game.players[0].hand)
        handled = resolve_etb_from_oracle(game, card, 0)

        assert handled is True
        assert len(game.players[0].hand) == before + 1

    def test_fixed_amount_word_draws_two(self):
        game = _game_with_library()
        card = _synthetic_permanent(
            game, "Test Fixture: Draw Two", 0,
            "When this creature enters, draw two cards.")

        before = len(game.players[0].hand)
        handled = resolve_etb_from_oracle(game, card, 0)

        assert handled is True
        assert len(game.players[0].hand) == before + 2

    def test_whenever_watcher_trigger_not_matched(self):
        """"Whenever [something else] enters, draw a card" is a
        repeatable watcher trigger (Risen Reef class), not this
        permanent's own one-shot ETB — must not fire here."""
        game = _game_with_library()
        card = _synthetic_permanent(
            game, "Test Fixture: Watcher", 0,
            "Whenever another creature enters, draw a card.")

        handled = resolve_etb_from_oracle(game, card, 0)

        assert handled is False

    @pytest.mark.parametrize("oracle_text,rider_label", [
        ("When this creature enters, discard a card, then draw a card.",
         "discard rider"),
        ("When this creature enters, you gain 2 life and draw a card.",
         "life-gain rider"),
        ("When this creature enters, if it was kicked, draw two cards.",
         "conditional amount"),
        ("When this creature enters, draw a card for each other "
         "Dinosaur you control.", "oracle-derived count"),
        ("When this creature enters, it deals 1 damage to you and "
         "you draw a card.", "damage rider"),
    ])
    def test_riders_are_not_swallowed(self, oracle_text, rider_label):
        """A rider clause alongside the draw must NOT be silently
        dropped by matching a bare 'draw N cards' fullmatch — these
        stay unhandled by this branch (their own EFFECT_REGISTRY
        handler, or a future dedicated resolver, owns them)."""
        game = _game_with_library()
        card = _synthetic_permanent(game, "Test Fixture: Rider", 0,
                                     oracle_text)

        handled = resolve_etb_from_oracle(game, card, 0)

        assert handled is False, (
            f"the {rider_label} case must not be swallowed by the "
            f"bare draw-N branch"
        )

    def test_x_cost_amount_not_guessed(self):
        """"Draw X cards" (Gadwick, the Wizened's shape) has no
        resolvable fixed amount at this layer — no-op rather than a
        wrong guess, matching the identical limitation the
        spell-resolution draw-N branch already has for X."""
        game = _game_with_library()
        card = _synthetic_permanent(
            game, "Test Fixture: Draw X", 0,
            "When this creature enters, draw x cards.")

        before = len(game.players[0].hand)
        handled = resolve_etb_from_oracle(game, card, 0)

        assert handled is False
        assert len(game.players[0].hand) == before


class TestGenericResolverCoversRegisteredHandlers:
    """Real-DB integration: the three cards whose dedicated
    EFFECT_REGISTRY ETB handlers were deleted must still draw the
    correct number of cards purely through the generic fallback —
    proving the deletion was safe (not just asserting the deletion
    happened)."""

    @pytest.mark.parametrize("card_name,expected_draw", [
        ("Omnath, Locus of Creation", 1),
        ("Quantum Riddler", 1),
        ("Thought Monitor", 2),
    ])
    def test_real_card_draws_via_generic_path_alone(
            self, card_db, card_name, expected_draw):
        # No EFFECT_REGISTRY handler should exist for these cards'
        # ETB timing any more — the deletion this test file pins.
        assert not EFFECT_REGISTRY.has_handler(card_name, EffectTiming.ETB), (
            f"{card_name!r} still has a dedicated ETB handler — the "
            f"redundancy this test proves no longer holds, or the "
            f"handler was not actually deleted"
        )

        game = _game_with_library(controller=0)
        tmpl = card_db.get_card(card_name)
        card = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        card._game_state = game
        game.players[0].battlefield.append(card)

        before = len(game.players[0].hand)
        game._handle_permanent_etb(card, controller=0)
        after = len(game.players[0].hand)

        assert after - before == expected_draw, (
            f"{card_name} ETB via the generic oracle-driven resolver "
            f"drew {after - before} cards, expected {expected_draw}"
        )
