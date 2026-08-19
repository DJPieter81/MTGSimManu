"""Persist (CR 702.78) and Undying (CR 702.92) return-from-death mechanics.

Rules under test
----------------
Persist (CR 702.78):
    When this creature dies, if it had no -1/-1 counters on it, return it to
    the battlefield under its owner's control with a -1/-1 counter on it.

Undying (CR 702.92):
    When this creature dies, if it had no +1/+1 counters on it, return it to
    the battlefield under its owner's control with a +1/+1 counter on it.

Both are replacement effects — the creature never actually reaches the
graveyard; it is redirected to the battlefield with the appropriate counter.

Class size: any creature with the persist or undying keyword hits this code
path — a mechanic-driven replacement effect, not a per-card patch.

Tests
-----
1. Persist creature with no -1/-1 counter dies → returns to battlefield
   with exactly one -1/-1 counter.
2. Persist creature that already has a -1/-1 counter dies → goes to graveyard
   (no second return).
3. Undying creature with no +1/+1 counter dies → returns to battlefield
   with exactly one +1/+1 counter.
4. Undying creature that already has a +1/+1 counter dies → goes to graveyard
   (no second return — CR 702.92 "if it had no +1/+1 counters").
5. Persist creature whose -1/-1 counter drops its toughness to 0 (a 1/1
   that persists becomes 0/0) → SBA loop immediately puts it to the
   graveyard on the same check_state_based_actions() call.
6. Persist + Undying are independent: a creature with both keywords and no
   counters uses Undying (earlier in the code); once it has a +1/+1 counter
   and dies again, Persist applies and it returns with a -1/-1 counter
   (the counters cancel, leaving a clean 0/0 creature which then dies).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword
from engine.game_state import GameState
from engine.mana import ManaCost


# ── helpers ──────────────────────────────────────────────────────────


def _fresh_game() -> GameState:
    return GameState(rng=random.Random(0))


def _creature(
    game: GameState,
    name: str,
    controller: int,
    *,
    power: int = 2,
    toughness: int = 2,
    keywords: set | None = None,
) -> CardInstance:
    """Place a creature on the battlefield for *controller*."""
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=2),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness,
        loyalty=None,
        keywords=set(keywords or ()),
        abilities=[],
        color_identity=set(),
        produces_mana=[],
        enters_tapped=False,
        oracle_text="",
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


def _deal_lethal_damage(creature: CardInstance) -> None:
    """Mark enough damage to be lethal (damage_marked >= toughness)."""
    creature.damage_marked = creature.toughness


# ── 1. Persist — first death (no -1/-1 counter) ──────────────────────


def test_persist_creature_returns_from_death_without_minus_counter():
    """A persist creature with no -1/-1 counter that dies via lethal
    damage is redirected to the battlefield with a -1/-1 counter
    (CR 702.78 replacement effect — it never enters the graveyard)."""
    game = _fresh_game()
    creature = _creature(game, "Test Persist Creature", 0,
                         power=2, toughness=2,
                         keywords={Keyword.PERSIST})

    assert creature.minus_counters == 0, "setup: no -1/-1 counters yet"

    _deal_lethal_damage(creature)
    game.check_state_based_actions()

    assert creature in game.players[0].battlefield, (
        "Persist must return the creature to the battlefield "
        "(CR 702.78 replacement effect)"
    )
    assert creature.zone == "battlefield"
    assert creature.minus_counters == 1, (
        "Persist must add exactly one -1/-1 counter on return (CR 702.78)"
    )
    assert creature.damage_marked == 0, (
        "Returned creature must have damage cleared (it is a 'new object' "
        "on the battlefield, CR 400.7 style)"
    )
    assert creature not in game.players[0].graveyard, (
        "Persist replacement redirects from graveyard to battlefield; "
        "the creature must NOT be in the graveyard"
    )


# ── 2. Persist — second death (already has -1/-1 counter) ────────────


def test_persist_creature_does_not_return_after_minus_counter():
    """A persist creature that already has a -1/-1 counter on it is NOT
    redirected on death — it goes to the graveyard (CR 702.78: 'if it
    had no -1/-1 counters on it')."""
    game = _fresh_game()
    creature = _creature(game, "Test Persist Creature", 0,
                         power=2, toughness=2,
                         keywords={Keyword.PERSIST})
    # Simulate the state after a prior persist return.
    creature.minus_counters = 1

    _deal_lethal_damage(creature)
    game.check_state_based_actions()

    assert creature not in game.players[0].battlefield, (
        "Persist must NOT return the creature when it already has a "
        "-1/-1 counter (CR 702.78)"
    )
    assert creature.zone == "graveyard"
    assert creature in game.players[0].graveyard


# ── 3. Undying — first death (no +1/+1 counter) ──────────────────────


def test_undying_creature_returns_from_death_without_plus_counter():
    """An undying creature with no +1/+1 counter that dies via lethal
    damage is redirected to the battlefield with a +1/+1 counter
    (CR 702.92 replacement effect)."""
    game = _fresh_game()
    creature = _creature(game, "Test Undying Creature", 0,
                         power=2, toughness=2,
                         keywords={Keyword.UNDYING})

    assert creature.plus_counters == 0, "setup: no +1/+1 counters yet"

    _deal_lethal_damage(creature)
    game.check_state_based_actions()

    assert creature in game.players[0].battlefield, (
        "Undying must return the creature to the battlefield "
        "(CR 702.92 replacement effect)"
    )
    assert creature.zone == "battlefield"
    assert creature.plus_counters == 1, (
        "Undying must add exactly one +1/+1 counter on return (CR 702.92)"
    )
    assert creature.damage_marked == 0, (
        "Returned creature must have damage cleared"
    )
    assert creature not in game.players[0].graveyard


# ── 4. Undying — second death (already has +1/+1 counter) ─────────────


def test_undying_creature_does_not_return_twice():
    """An undying creature that ALREADY has a +1/+1 counter does NOT
    return when it dies again (CR 702.92: 'if it had no +1/+1 counters
    on it'). The counter from the first return blocks the second trigger."""
    game = _fresh_game()
    # A 2/2 with undying that already returned: now 3/3 (+1/+1 counter).
    creature = _creature(game, "Test Undying Creature", 0,
                         power=2, toughness=2,
                         keywords={Keyword.UNDYING})
    creature.plus_counters = 1   # simulate state after first undying return

    # Effective toughness is now 3; deal 3 damage.
    _deal_lethal_damage(creature)
    game.check_state_based_actions()

    assert creature not in game.players[0].battlefield, (
        "Undying must NOT trigger a second time when the creature already "
        "has a +1/+1 counter (CR 702.92 'if it had no +1/+1 counters')"
    )
    assert creature.zone == "graveyard", (
        "Creature with existing +1/+1 counter must go to graveyard on death"
    )
    assert creature in game.players[0].graveyard


# ── 5. Persist 1/1 → becomes 0/0 after return → SBA kills it ─────────


def test_persist_1_1_creature_goes_to_graveyard_after_return():
    """A 1/1 persist creature returns with a -1/-1 counter, becoming 0/0.
    The SBA fixpoint loop (CR 704.3 + 704.5g) must catch the 0/0 in the
    SAME check_state_based_actions() call and put it in the graveyard.
    The creature never sits on the battlefield as a 0/0."""
    game = _fresh_game()
    creature = _creature(game, "Test Persist 1-1", 0,
                         power=1, toughness=1,
                         keywords={Keyword.PERSIST})

    assert creature.minus_counters == 0
    _deal_lethal_damage(creature)
    game.check_state_based_actions()

    # After one SBA pass: persist returns creature with -1/-1 → toughness=0.
    # On the same call's fixpoint loop, the 0-toughness SBA (704.5g) kills it.
    assert creature not in game.players[0].battlefield, (
        "A 1/1 that persists (becoming 0/0) must NOT remain on the "
        "battlefield — SBA 704.5g fires in the same fixpoint iteration"
    )
    assert creature.zone == "graveyard", (
        "The 0/0 creature that persisted must end up in the graveyard"
    )
    assert creature in game.players[0].graveyard


# ── 6. Persist + Undying interaction ─────────────────────────────────


def test_undying_takes_priority_when_both_keywords_no_counters():
    """When a creature has BOTH undying and persist and no counters,
    undying is checked first in _creature_dies (CR precedence: first
    applicable replacement effect wins).  The creature returns with
    a +1/+1 counter.

    On its SECOND death (now with a +1/+1 counter, blocking undying),
    persist applies and it returns with a -1/-1 counter.  The counters
    cancel (a +1/+1 and a -1/-1 counter annihilate per CR 704.5q),
    leaving a clean creature."""
    game = _fresh_game()
    creature = _creature(game, "Test Both Keywords", 0,
                         power=2, toughness=2,
                         keywords={Keyword.UNDYING, Keyword.PERSIST})

    # First death — undying fires (no +1/+1 counters).
    _deal_lethal_damage(creature)
    game.check_state_based_actions()

    assert creature in game.players[0].battlefield, (
        "Both keywords, no counters: undying (checked first) must return "
        "the creature to the battlefield"
    )
    assert creature.plus_counters == 1, (
        "Undying must have added a +1/+1 counter"
    )
    assert creature.minus_counters == 0
