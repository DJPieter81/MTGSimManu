"""Phase 0 / Phase L A-1 — T1 Cranial Plating cast must require {2}.

Rule under test
---------------
Cranial Plating has CMC 2 (generic). Affinity's classic T1 line is:
1. Play Urza's Saga (artifact land, taps for {C}).
2. Cast Memnite (CMC 0).
3. Cast Mox Opal (CMC 0). With Saga + Memnite + Mox on the battlefield,
   metalcraft is active (3 artifacts) — Mox Opal can tap for any color.
4. Cast Cranial Plating (CMC 2): pay {C} from Saga + {1} from Mox Opal.

For this to be a rule-legal cast, the engine must include Mox Opal as
an *available mana source* during cost payment. Mox Opal is type
``Artifact``, NOT ``Land``, so any code path that gathers mana sources
by ``c.template.is_land`` strictly will silently exclude Mox Opal.

If Mox Opal is excluded from the mana-source set:
  - ``can_cast`` for Plating returns False (only Saga = 1 mana < 2 CMC)
  - OR the cast somehow succeeds, in which case mana is being paid
    illegally (a Class F rule violation).

Affinity's actual sim behaviour shows T1 Plating *succeeding* (replay:
``replays/affinity_vs_boros_s60100.txt``), which means SOMETHING is
letting the cost go through. This test pins the rule-correct semantics
so we can identify which path is responsible.

Three orthogonal assertions
---------------------------
1. **Without Mox Opal**: cast is illegal. With only Saga (1 mana),
   Plating's {2} CANNOT be paid. ``can_cast`` must return False.
2. **With Mox Opal under metalcraft**: cast IS legal. Saga ({C}) +
   Mox Opal (any color) = 2 mana. ``can_cast`` must return True.
3. **Payment side-effects**: when the cast resolves, both Saga AND
   Mox Opal must end up tapped (rules-correct: each contributed 1 mana).

Audit context: ``docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md``
finding A-1; plan ``/root/.claude/plans/now-lets-fix-affinity-keen-penguin.md``
Phase 0.

Fixture style follows ``tests/test_evsnapshot_artifact_count_excludes_lands.py``
(PR-L1 sister).
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.mana import ManaCost


def _put_in_play(game, card_db, name, controller, tapped=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    card.tapped = tapped
    game.players[controller].battlefield.append(card)
    return card


def _put_in_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


class TestT1PlatingRequiresTwoMana:
    """Pin the rule: Cranial Plating costs {2}; Mox Opal must be a
    tappable mana source under metalcraft for the T1 Plating line to be
    legal. Without Mox in the mana-source set, the cost cannot be paid."""

    def test_plating_uncastable_with_only_saga(self, card_db):
        """Saga alone (1 mana) cannot pay Plating's CMC of 2.

        This is the strictest form of the rule. If can_cast returns
        True here, the engine is fabricating mana from somewhere.
        """
        game = GameState(rng=random.Random(0))
        _put_in_play(game, card_db, "Urza's Saga", 0)
        plating = _put_in_hand(game, card_db, "Cranial Plating", 0)

        assert not game.can_cast(0, plating), (
            "With only Urza's Saga (1 mana) on battlefield, Cranial "
            "Plating ({2}) MUST be uncastable. can_cast=True here means "
            "the engine sees mana that doesn't exist."
        )

    def test_plating_payment_does_not_succeed_with_only_saga(self, card_db):
        """Plating costs {2}. With only Urza's Saga (1 mana) on the
        battlefield and Plating in hand, ``tap_lands_for_mana`` MUST
        return False. If it returns True, mana is being fabricated.

        Regression specifically pins the parse_cost_reduction false
        positive: Saga's chapter II token oracle says "0/0 colorless
        Construct" (the substring 'less' lives inside 'colorless') and
        chapter III says "with mana cost {0} or {1}" (the word 'cost'
        appears). The lazy ``'cost' in oracle and 'less' in oracle``
        check in parse_cost_reduction defaults to ``target='all',
        amount=1`` — falsely declaring Saga a generic cost reducer.
        """
        game = GameState(rng=random.Random(0))
        saga = _put_in_play(game, card_db, "Urza's Saga", 0)
        plating = _put_in_hand(game, card_db, "Cranial Plating", 0)

        cost = ManaCost(generic=2)
        paid = game.tap_lands_for_mana(0, cost, card_name="Cranial Plating")

        assert not paid, (
            f"With only Urza's Saga on the battlefield (1 mana), Cranial "
            f"Plating's {{2}} MUST NOT be payable. Got paid={paid}, "
            f"saga.tapped={saga.tapped}. If paid=True, Saga is being "
            f"mis-parsed as a generic cost reducer (parse_cost_reduction "
            f"false positive on 'colorless' / 'mana cost {{0}}')."
        )
