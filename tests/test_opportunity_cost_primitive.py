"""Phase 2a — ``ai.clock.opportunity_cost`` (docs/design/rules-foundation-
sweep-tracker.md).

Rule pinned: "what do I lose by spending this permanent right now" must
be a real computation, not a categorical proxy. Prior to this primitive,
``ai/ev_player.py``'s chump-block candidate filter answered the question
with raw power alone (``if b_pow == 0: continue``) — the audited
specimen bug. This suite proves the replacement primitive prices a
0-power creature's future value correctly in both directions:

  * a 0-power creature with a genuine un-exhausted activated ability
    (the "Dash-Ragavan-class chump-blocker" shape audited in Boros
    Energy vs Dimir Midrange — any deck holding a 0-power engine
    creature it should NOT casually chump away) scores ABOVE zero.
  * a 0-power creature with an equipment ceiling nearby (same shape,
    equipment-flavoured) also scores ABOVE zero.
  * a genuinely dead 0-power creature — no keywords, no toughness, no
    activated ability, no equipment on the board — the literal
    "stripped Ornithopter" shape audited in Affinity vs 4c Omnath —
    scores at/near zero, so the AI is free to spend it without the
    old veto standing in the way.

Class size: every 0-power creature in Modern with an activated ability
(mana dorks, Walking Ballista-style pingers, utility creatures) or a
static equipment buff nearby hits this primitive — not a single card.
Card names appear only in fixture setup; the assertions are about the
mechanic (activated-ability presence, equipment-ceiling presence).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState


def _battlefield(game, db, name: str, controller: int) -> CardInstance:
    tmpl = db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def test_dead_zero_power_creature_prices_at_near_zero(card_db):
    """The literal "stripped Ornithopter" shape (Affinity vs 4c Omnath,
    bug #6): a 0-power creature with no keywords beyond a small
    toughness, no activated ability, and no equipment anywhere on the
    board. ``opportunity_cost`` must price it at/near zero — nothing
    is lost by spending it as a chump blocker.
    """
    from ai.clock import opportunity_cost
    from ai.ev_evaluator import BASELINE_SNAPSHOT

    game = GameState(rng=random.Random(0))
    orn = _battlefield(game, card_db, "Ornithopter", 0)
    board = game.players[0]

    cost = opportunity_cost(orn, board, BASELINE_SNAPSHOT)

    assert cost < 1.0, (
        f"opportunity_cost(Ornithopter, no equipment/abilities) = "
        f"{cost:.3f}; expected near-zero — a genuinely dead 0-power "
        f"creature has nothing worth preserving."
    )


def test_zero_power_creature_with_equipment_ceiling_scores_above_zero(card_db):
    """Same Ornithopter, same board otherwise, but with an unattached
    Cranial Plating (a real static +N/+M equipment) also on the
    battlefield. The equipment-ceiling term must lift the score
    strictly above the dead-creature baseline — Ornithopter is a live
    equip target, not disposable fodder.
    """
    from ai.clock import opportunity_cost
    from ai.ev_evaluator import BASELINE_SNAPSHOT

    game = GameState(rng=random.Random(0))
    orn = _battlefield(game, card_db, "Ornithopter", 0)
    _battlefield(game, card_db, "Mox Opal", 0)
    _battlefield(game, card_db, "Springleaf Drum", 0)
    plating = _battlefield(game, card_db, "Cranial Plating", 0)
    plating.instance_tags.discard("equipment_attached")
    plating.instance_tags.add("equipment_unattached")
    board = game.players[0]

    cost_with_plating = opportunity_cost(orn, board, BASELINE_SNAPSHOT)

    game_bare = GameState(rng=random.Random(0))
    orn_bare = _battlefield(game_bare, card_db, "Ornithopter", 0)
    cost_bare = opportunity_cost(orn_bare, game_bare.players[0], BASELINE_SNAPSHOT)

    assert cost_with_plating > 0.0, (
        f"opportunity_cost(Ornithopter, unattached Cranial Plating) = "
        f"{cost_with_plating:.3f}; expected > 0 — the equipment ceiling "
        f"must be priced."
    )
    assert cost_with_plating > cost_bare, (
        f"opportunity_cost with an equip-target ceiling ({cost_with_plating:.3f}) "
        f"must exceed the bare-board baseline ({cost_bare:.3f})."
    )


def test_zero_power_creature_with_activated_ability_scores_above_zero(card_db):
    """The "Dash-Ragavan-class chump-blocker" shape (Boros Energy vs
    Dimir Midrange, bug #5): a 0-power creature whose real value is an
    un-exhausted activated ability, not its combat stats. Walking
    Ballista with zero +1/+1 counters is a real Modern 0/0 with a
    genuine activated ability and nothing else (no keywords, no
    toughness) — isolates the activated-ability term specifically.
    """
    from ai.clock import opportunity_cost
    from ai.ev_evaluator import BASELINE_SNAPSHOT

    game = GameState(rng=random.Random(0))
    ballista = _battlefield(game, card_db, "Walking Ballista", 0)
    assert (ballista.power or 0) == 0, "fixture must enter with 0 counters"
    assert (ballista.toughness or 0) == 0, "fixture must enter with 0 counters"
    board = game.players[0]

    cost = opportunity_cost(ballista, board, BASELINE_SNAPSHOT)

    assert cost > 0.0, (
        f"opportunity_cost(0/0 Walking Ballista, un-exhausted activated "
        f"ability) = {cost:.3f}; expected > 0 — a repeatable engine's "
        f"future value must be priced even when raw power/toughness "
        f"are both zero."
    )


def test_creature_value_is_a_caller_of_opportunity_cost(card_db):
    """``ai.ev_evaluator.creature_value`` must delegate to
    ``opportunity_cost`` rather than re-implementing the same clock
    math in parallel — otherwise the activated-ability / equipment-
    ceiling terms only exist in one of the two call paths and callers
    of ``creature_value`` (e.g. the emergency-block portfolio
    accounting) keep seeing the old, narrower answer.
    """
    from ai.ev_evaluator import creature_value, BASELINE_SNAPSHOT
    from ai.clock import opportunity_cost

    game = GameState(rng=random.Random(0))
    ballista = _battlefield(game, card_db, "Walking Ballista", 0)
    board = game.players[0]

    assert creature_value(ballista, BASELINE_SNAPSHOT) == pytest.approx(
        opportunity_cost(ballista, board, BASELINE_SNAPSHOT)
    )
    # And, concretely: the 0/0 Ballista's activated ability must make
    # creature_value strictly positive (pre-fix it was 0.0 — a 0/0
    # with no keywords has zero raw clock impact).
    assert creature_value(ballista, BASELINE_SNAPSHOT) > 0.0


def test_non_creature_permanent_has_zero_opportunity_cost(card_db):
    """Regression bound: the primitive only prices creatures (what's
    lost by spending a BLOCKER). A non-creature permanent returns 0.0
    rather than raising or double-counting via some other path.
    """
    from ai.clock import opportunity_cost
    from ai.ev_evaluator import BASELINE_SNAPSHOT

    game = GameState(rng=random.Random(0))
    land = _battlefield(game, card_db, "Darksteel Citadel", 0)
    board = game.players[0]

    cost = opportunity_cost(land, board, BASELINE_SNAPSHOT)
    assert cost == 0.0
