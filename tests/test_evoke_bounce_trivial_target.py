"""Evoke ETBs that target stack-spells must not fire on an empty stack.

Reference: replays/affinity_vs_living_end_s60100.txt — G1 T2 / G2 T2.

Pre-fix behaviour (the bug):

    Living End evokes Subtlety on its own MAIN1 with no spell on the
    stack.  Subtlety's ETB targets a "creature spell or planeswalker
    spell" — i.e. a SPELL on the stack, not a permanent on the
    battlefield.  With an empty stack the ETB fizzles ("Subtlety
    enters (no creature/PW spell on stack to target)").  Yet the
    pitch cost (Force of Negation, a free counter the deck cannot
    replace) is paid in full, and Subtlety itself is sacrificed.
    Net: 2 cards spent (pitch + evoked body) for zero impact —
    a self-mulligan worse than discarding for hand size.

Root cause: `ai/board_eval.py::_eval_evoke` lines 214-218 detect
oracle text "creature spell" / "target creature" and gate on
`opp.creatures` (battlefield).  That gate is the wrong proxy for
Subtlety-style ETBs whose "target X spell" wording targets the
STACK, not the battlefield.  Affinity has Memnite + Signal Pest on
the battlefield, so the gate passes; the stack is empty, so the
ETB still fizzles.

The right rule (rule, not card-name):

    An evoke ETB whose oracle says "target <type> spell" requires
    a spell of that type on the stack to do anything.  When the
    stack contains no opponent spell of the matching type, the
    ETB fizzles and the evoke pitch is wasted.  Don't pay it.

Class size: every evoke + stack-targeting ETB.  Currently Subtlety;
the same gate covers any future printing whose "target creature
spell" / "target planeswalker spell" wording fires on the stack.
Detection is oracle-driven — no card names.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardType
from engine.game_state import GameState, Phase
from engine.stack import StackItem, StackItemType


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
    bucket = 'library' if zone == 'library' else zone
    getattr(game.players[controller], bucket).append(card)
    return card


def _build_living_end_t2_state(card_db):
    """Reproduce the seed-60100 G1 T2 state.

    Affinity (P0) has Memnite + Signal Pest on the battlefield and
    nothing on the stack.  Living End (P1) has Subtlety + Force of
    Negation in hand on its own MAIN1 — the very moment the engine
    asks `should_evoke(Subtlety)`.

    Existing line-216 gate (`'creature spell' in oracle ...`)
    matches Subtlety but checks `opp.creatures`; the opponent
    HAS creatures, so the gate passes.  The post-fix gate should
    inspect the stack instead.
    """
    game = GameState(rng=random.Random(0))
    # Affinity battlefield — non-empty creatures (this is the
    # condition that defeats the existing line-216 gate).
    _add(game, card_db, "Memnite", controller=0, zone="battlefield")
    _add(game, card_db, "Signal Pest", controller=0, zone="battlefield")
    _add(game, card_db, "Urza's Saga", controller=0, zone="battlefield")
    # Living End hand: Subtlety + Force of Negation (free pitch
    # counter — irreplaceable) + lands.
    _add(game, card_db, "Subtlety", controller=1, zone="hand")
    _add(game, card_db, "Force of Negation", controller=1, zone="hand")
    # Living End mana base
    _add(game, card_db, "Watery Grave", controller=1, zone="battlefield")
    _add(game, card_db, "Blood Crypt", controller=1, zone="battlefield")
    game.players[0].deck_name = "Affinity"
    game.players[1].deck_name = "Living End"
    game.current_phase = Phase.MAIN1
    game.active_player = 1
    game.priority_player = 1
    game.turn_number = 2
    game.players[0].life = 20
    game.players[1].life = 13
    # Stack is empty — the precise condition the bug ignores.
    assert game.stack.is_empty
    return game


def _put_creature_spell_on_stack(game, card_db, controller):
    """Push an opponent creature spell onto the stack — the
    legitimate target Subtlety needs to do work."""
    tmpl = card_db.get_card("Sojourner's Companion")
    assert tmpl is not None
    spell_card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="stack",
    )
    spell_card._game_state = game
    item = StackItem(
        item_type=StackItemType.SPELL,
        source=spell_card,
        controller=controller,
    )
    game.stack.items.append(item)
    return spell_card


class TestEvokeBounceTrivialTarget:
    """Evoke ETBs targeting stack-spells must not fire when the
    stack is empty (or has no valid target spell)."""

    def test_evoke_skips_stack_targeting_etb_when_stack_empty(self, card_db):
        """G1 T2 / G2 T2 of seed-60100 reproducer.

        Subtlety's ETB targets a "creature spell or planeswalker
        spell" — that wording is stack-targeting in MTG.  When the
        stack is empty, the ETB does nothing.  Evoking exiles a
        free counterspell (Force of Negation) for zero impact.

        Test name encodes the rule: stack-targeting evoke ETBs
        require a spell on the stack to be worth pitching for.
        """
        from ai.board_eval import evaluate_action, ActionType, Action

        game = _build_living_end_t2_state(card_db)
        subtlety = next(c for c in game.players[1].hand
                        if c.name == "Subtlety")

        score = evaluate_action(
            game, player_idx=1,
            action=Action(ActionType.EVOKE, {'card': subtlety}),
        )

        assert score < 0, (
            f"Living End must NOT evoke Subtlety with an empty "
            f"stack — its ETB targets a creature/PW *spell on the "
            f"stack*, not a permanent.  Pitching Force of Negation "
            f"(a free counter the deck cannot replace) for a "
            f"fizzling ETB is a self-mulligan.\n\n"
            f"evaluate_action returned {score} (expected < 0).\n"
            f"Affinity battlefield: "
            f"{[c.name for c in game.players[0].creatures]}\n"
            f"Stack: {[i.source.name for i in game.stack.items]} "
            f"(must be empty for this case).\n\n"
            f"Existing gate at ai/board_eval.py line 214-218 reads "
            f"'creature spell' / 'target creature' from oracle and "
            f"checks `opp.creatures` — but Subtlety targets the "
            f"STACK, not the battlefield."
        )

    def test_evoke_fires_stack_targeting_etb_when_stack_has_target(
            self, card_db):
        """Regression: when the opponent IS casting a creature
        spell, Subtlety's ETB has a valid target and evoke is the
        whole point of the card.  Don't over-tighten.
        """
        from ai.board_eval import evaluate_action, ActionType, Action

        game = _build_living_end_t2_state(card_db)
        # Affinity casts Sojourner's Companion (CMC 7) — a real
        # threat on the stack that Subtlety can profitably bounce.
        _put_creature_spell_on_stack(game, card_db, controller=0)
        # Stack-targeting evoke is at instant speed, so the active
        # player is Affinity (P0) when its spell is on the stack.
        game.active_player = 0
        game.priority_player = 1
        subtlety = next(c for c in game.players[1].hand
                        if c.name == "Subtlety")

        score = evaluate_action(
            game, player_idx=1,
            action=Action(ActionType.EVOKE, {'card': subtlety}),
        )

        assert score > -2.0, (
            f"With an opponent creature spell on the stack, "
            f"Subtlety's evoke is exactly the right play — the "
            f"ETB sends a 7-CMC threat back on top of the library, "
            f"a value-positive trade.  The fix must not over-"
            f"tighten and disable evoke entirely.\n\n"
            f"evaluate_action returned {score} (must be > -2.0)."
        )

    def test_evoke_skips_stack_targeting_etb_when_only_own_spell_on_stack(
            self, card_db):
        """Edge case: the stack is non-empty but contains only the
        evoker's own spells (e.g. its own counterspell).  Subtlety
        bouncing your own spell is even worse than fizzling — same
        rule applies: don't pay the pitch cost.
        """
        from ai.board_eval import evaluate_action, ActionType, Action

        game = _build_living_end_t2_state(card_db)
        # Push a Living End-controlled creature spell onto the
        # stack.  Subtlety targets "target creature spell" — its
        # own spell is technically a legal target, but bouncing
        # your own spell back to library is purely self-harm.
        _put_creature_spell_on_stack(game, card_db, controller=1)
        subtlety = next(c for c in game.players[1].hand
                        if c.name == "Subtlety")

        score = evaluate_action(
            game, player_idx=1,
            action=Action(ActionType.EVOKE, {'card': subtlety}),
        )

        assert score < 0, (
            f"Subtlety with only the evoker's OWN spell on the "
            f"stack must not fire — bouncing your own spell is a "
            f"strict negative.  The gate must require an OPPONENT "
            f"creature/PW spell on the stack.\n\n"
            f"evaluate_action returned {score} (expected < 0)."
        )
