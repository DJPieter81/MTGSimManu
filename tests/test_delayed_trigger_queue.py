"""Delayed triggered abilities (CR 603.7) — the general queue.

A delayed trigger is created by a resolving spell or ability, waits, fires
ONCE at the step it names, and is then gone. Before this queue the engine
had two hand-rolled special cases for the shape (`_end_of_turn_exiles`,
`_end_of_turn_sacrifices`), each end-step-only with its effect hard-coded
at the firing site, and no upkeep-timed delayed trigger at all.

Measured class (ModernAtomic, 22 506 cards):

    "at the beginning of the next end step, …"        307 cards
    "at the beginning of your next upkeep, …"          44 cards
    "at the beginning of your next end step, …"        13 cards
    "at the beginning of the next turn's upkeep, …"     5 cards
    ────────────────────────────────────────────────────────────
    any "at the beginning of the NEXT …" delayed trigger  375

Rules pinned here:
  * the timing vocabulary parses to a queue timing, and "UNTIL the
    beginning of your next upkeep" — a duration, not a trigger — does not;
  * the delay is ORTHOGONAL to the effect: the classified effect kind is
    the inner effect's own, and the timing rides separately on
    `ActivatedAbility.delayed_timing`, so no effect kind needs a delayed
    twin;
  * a state-free clause (looking at a library's top card) is stripped
    before classification, and a body that reduces to nothing stays
    UNCLASSIFIED rather than executing as a no-op;
  * a delayed effect does NOT happen on resolution — it enqueues;
  * it fires at its stated step, exactly once, and never in the turn it
    was created in (for the upkeep timings);
  * "your next upkeep" skips the opponent's turns; "the next turn's
    upkeep" does not;
  * CR 603.7d — the trigger is independent of its source: a source that
    sacrificed ITSELF to pay its own activation cost still delivers;
  * an ability whose stated timing the queue does not drain is refused
    before any cost is charged.

Card names in test bodies are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.activated_effects import resolve_activated_ability
from engine.activation import ActivationManager
from engine.card_database import CardDatabase
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.delayed_triggers import (DelayedTrigger, DelayedTriggerQueue,
                                     DelayedTriggerStep,
                                     DelayedTriggerTiming, TIMING_STEP)
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import (classify_activation_effect,
                                  is_information_only_clause,
                                  parse_activated_abilities,
                                  parse_activation_delay,
                                  parse_delayed_effect)

_DB = CardDatabase()


# ── parsing: timing vocabulary, durations, state-free clauses ─────────

def test_printed_timing_phrases_parse_to_queue_timings():
    for sentence, timing, inner in (
            ("Draw a card at the beginning of the next turn's upkeep",
             "NEXT_UPKEEP", "draw a card"),
            ("At the beginning of your next upkeep, draw a card",
             "YOUR_NEXT_UPKEEP", "draw a card"),
            ("Sacrifice it at the beginning of the next end step",
             "NEXT_END_STEP", "sacrifice it"),
            ("At the beginning of your next end step, draw two cards",
             "YOUR_NEXT_END_STEP", "draw two cards")):
        parsed = parse_delayed_effect(sentence)
        assert parsed is not None, sentence
        assert parsed == (timing, inner), sentence


def test_until_the_beginning_of_a_step_is_a_duration_not_a_trigger():
    """"Until the beginning of your next upkeep, X" says how long a
    continuous effect LASTS. Reading it as a delayed trigger would fire an
    effect that must never fire at all."""
    for sentence in (
            "Until the beginning of your next upkeep, you may play that card",
            "Prevent all damage until the beginning of your next end step"):
        assert parse_delayed_effect(sentence) is None, sentence


def test_only_the_state_free_look_clause_counts_as_information_only():
    assert is_information_only_clause(
        "look at the top card of target player's library")
    assert is_information_only_clause(
        "look at the top card of your library")
    # These CHANGE state — order, zone, or a revealed card — so none of
    # them may be stripped.
    for sentence in (
            "look at the top two cards of your library, then put them back "
            "in any order",
            "look at the top card of your library, then put it into your "
            "graveyard",
            "reveal the top card of your library"):
        assert not is_information_only_clause(sentence), sentence


def test_delay_is_orthogonal_to_the_effect_kind():
    """The classified kind is the INNER effect's own; WHEN it happens rides
    separately. That is what stops every effect kind needing a delayed
    twin."""
    kind, amount, _p, _t = classify_activation_effect(
        "Draw a card at the beginning of the next turn's upkeep.")
    assert kind is ActivationEffectKind.DRAW_N
    assert amount == 1

    delayed_two, amount_two, _p, _t = classify_activation_effect(
        "At the beginning of your next upkeep, draw two cards.")
    assert delayed_two is ActivationEffectKind.DRAW_N
    assert amount_two == 2


def test_state_free_clause_is_stripped_before_classification():
    kind, amount, _p, _t = classify_activation_effect(
        "Look at the top card of target player's library. Draw a card at "
        "the beginning of the next turn's upkeep.")
    assert kind is ActivationEffectKind.DRAW_N and amount == 1


def test_a_body_that_reduces_to_nothing_stays_unclassified():
    """An ability with no representable effect is refused, not executed as
    a no-op that charges a cost for nothing."""
    kind, *_ = classify_activation_effect(
        "Look at the top card of your library.")
    assert kind is ActivationEffectKind.UNCLASSIFIED


def test_delayed_effect_whose_inner_effect_is_unexecutable_stays_unclassified():
    """Peeling the delay off never widens the executable set: the inner
    sentence still has to be a shape the resolver runs."""
    for sentence in (
            "Sacrifice it at the beginning of the next end step.",
            "Return it to the battlefield under its owner's control at the "
            "beginning of the next end step.",
            "At the beginning of the next end step, exile it."):
        kind, *_ = classify_activation_effect(sentence)
        assert kind is ActivationEffectKind.UNCLASSIFIED, sentence


def test_timing_rides_on_the_ability_as_a_typed_field():
    """Parsed once at DB load. The resolver dispatches off the field and
    never re-reads oracle text at runtime."""
    ab = parse_activated_abilities(
        "{T}, Sacrifice this artifact: Look at the top card of target "
        "player's library. Draw a card at the beginning of the next turn's "
        "upkeep.")[0]
    assert ab.effect_kind is ActivationEffectKind.DRAW_N
    assert ab.delayed_timing is DelayedTriggerTiming.NEXT_UPKEEP
    assert ab.cost.tap_self and ab.cost.sacrifice_self
    assert ab.cost.unpayable == ()

    # An immediate draw carries no timing — there is nothing to delay.
    immediate = parse_activated_abilities("{T}: Draw a card.")[0]
    assert immediate.effect_kind is ActivationEffectKind.DRAW_N
    assert immediate.delayed_timing is None

    assert parse_activation_delay("Draw a card.") is None


def test_every_parsed_timing_has_a_drain_point():
    """A timing the parser can produce but no step drains would be an
    ability that pays its cost and never fires."""
    for timing in DelayedTriggerTiming:
        assert timing in TIMING_STEP


# ── queue semantics ───────────────────────────────────────────────────

def _game(turn=5, active=0):
    game = GameState(rng=random.Random(0))
    game.active_player = active
    game.current_phase = Phase.MAIN1
    game.turn_number = turn
    return game


def _counting_trigger(timing, controller=0, created_turn=5, log=None):
    fired = [] if log is None else log

    def _effect(game):
        fired.append(game.turn_number)

    return DelayedTrigger(timing=timing, controller=controller,
                          effect=_effect,
                          description="test trigger",
                          created_turn=created_turn), fired


def test_upkeep_trigger_does_not_fire_in_the_turn_it_was_created():
    game = _game(turn=5)
    trigger, fired = _counting_trigger(DelayedTriggerTiming.NEXT_UPKEEP,
                                       created_turn=5)
    game.register_delayed_trigger(trigger)

    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)
    assert fired == []

    game.turn_number = 6
    game.active_player = 1
    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)
    assert fired == [6]


def test_a_delayed_trigger_fires_exactly_once():
    game = _game(turn=5)
    trigger, fired = _counting_trigger(DelayedTriggerTiming.NEXT_UPKEEP,
                                       created_turn=5)
    game.register_delayed_trigger(trigger)

    for turn in (6, 7, 8):
        game.turn_number = turn
        game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)
    assert fired == [6]
    assert game.delayed_triggers.pending == ()


def test_your_next_upkeep_skips_the_opponents_turns():
    game = _game(turn=5, active=0)
    trigger, fired = _counting_trigger(
        DelayedTriggerTiming.YOUR_NEXT_UPKEEP, controller=0, created_turn=5)
    game.register_delayed_trigger(trigger)

    game.turn_number, game.active_player = 6, 1
    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)
    assert fired == []

    game.turn_number, game.active_player = 7, 0
    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)
    assert fired == [7]


def test_the_next_turns_upkeep_does_not_skip_the_opponents_turn():
    """The discriminator between the two upkeep timings: 'the next turn's
    upkeep' is whoever's turn comes next."""
    game = _game(turn=5, active=0)
    trigger, fired = _counting_trigger(
        DelayedTriggerTiming.NEXT_UPKEEP, controller=0, created_turn=5)
    game.register_delayed_trigger(trigger)

    game.turn_number, game.active_player = 6, 1
    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)
    assert fired == [6]


def test_a_timing_only_fires_at_its_own_step():
    game = _game(turn=5)
    trigger, fired = _counting_trigger(DelayedTriggerTiming.NEXT_UPKEEP,
                                       created_turn=5)
    game.register_delayed_trigger(trigger)

    game.turn_number = 6
    game.fire_delayed_triggers(DelayedTriggerStep.END_STEP)
    assert fired == []
    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)
    assert fired == [6]


def test_end_step_timing_fires_in_the_turn_it_was_created():
    """'The next end step' is THIS turn's when the trigger was created
    before it — unlike 'the next turn's upkeep'."""
    game = _game(turn=5)
    trigger, fired = _counting_trigger(DelayedTriggerTiming.NEXT_END_STEP,
                                       created_turn=5)
    game.register_delayed_trigger(trigger)

    game.fire_delayed_triggers(DelayedTriggerStep.END_STEP)
    assert fired == [5]


def test_a_trigger_created_while_the_queue_is_firing_waits_for_the_next_pass():
    """Without the pre-loop snapshot a self-recreating rider would fire
    forever inside one step."""
    game = _game(turn=5)
    fired = []

    def _respawn(g):
        fired.append(g.turn_number)
        inner, _ = _counting_trigger(DelayedTriggerTiming.NEXT_END_STEP,
                                     created_turn=g.turn_number, log=fired)
        g.register_delayed_trigger(inner)

    game.register_delayed_trigger(DelayedTrigger(
        timing=DelayedTriggerTiming.NEXT_END_STEP, controller=0,
        effect=_respawn, description="respawner", created_turn=5))

    game.fire_delayed_triggers(DelayedTriggerStep.END_STEP)
    assert fired == [5]
    assert len(game.delayed_triggers.pending) == 1


def test_queue_is_a_read_only_view_outside_firing():
    queue = DelayedTriggerQueue()
    trigger, _ = _counting_trigger(DelayedTriggerTiming.NEXT_UPKEEP)
    queue.register(trigger)
    assert isinstance(queue.pending, tuple)
    assert queue.pending == (trigger,)


# ── activated-ability integration ─────────────────────────────────────

def _add(game, name, controller=0, zone="battlefield"):
    tmpl = _DB.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _delayed_draw_ability(timing=DelayedTriggerTiming.NEXT_UPKEEP, amount=1):
    return ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(), tap_self=True,
                            sacrifice_self=True),
        effect_text="Draw a card at the beginning of the next turn's upkeep.",
        effect_kind=ActivationEffectKind.DRAW_N,
        amount=amount,
        delayed_timing=timing)


def test_a_delayed_effect_does_not_happen_on_resolution():
    game = _game(turn=5)
    src = _add(game, "Mishra's Bauble")
    for _ in range(5):
        _add(game, "Island", 0, "library")
    before = len(game.players[0].hand)

    assert resolve_activated_ability(
        game, src, 0, [], ability=_delayed_draw_ability()) is True
    assert len(game.players[0].hand) == before
    assert len(game.delayed_triggers.pending) == 1


def test_a_delayed_effect_survives_its_source_leaving_the_battlefield():
    """CR 603.7d. The Bauble-shape pays its own cost by sacrificing itself,
    so a trigger that reached back into its source would never deliver."""
    game = _game(turn=5)
    src = _add(game, "Mishra's Bauble")
    for _ in range(5):
        _add(game, "Island", 0, "library")
    before = len(game.players[0].hand)

    resolve_activated_ability(game, src, 0, [],
                              ability=_delayed_draw_ability())
    # The source leaves — as the printed cost makes it.
    game.zone_mgr.move_card_to_graveyard(game, src, cause="test")
    assert src.zone == "graveyard"

    game.turn_number = 6
    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)
    assert len(game.players[0].hand) == before + 1


def test_a_delayed_activation_is_refused_when_no_step_drains_its_timing():
    """The 9b discipline: refuse BEFORE charging a cost the resolver cannot
    honour."""
    game = _game(turn=5)
    src = _add(game, "Mishra's Bauble")
    ability = _delayed_draw_ability()
    assert ActivationManager.can_activate(game, 0, src, ability) is True

    class _UnknownTiming:
        value = "never_drained"

    orphan = _delayed_draw_ability(timing=_UnknownTiming())
    assert ActivationManager.can_activate(game, 0, src, orphan) is False


# ── end to end: activation → sacrifice → delayed draw ─────────────────

def test_self_sacrificing_activation_delivers_its_delayed_draw():
    game = _game(turn=5)
    src = _add(game, "Mishra's Bauble")
    for _ in range(5):
        _add(game, "Island", 0, "library")
    ability = src.template.activated_abilities[0]
    assert ability.delayed_timing is DelayedTriggerTiming.NEXT_UPKEEP
    before = len(game.players[0].hand)

    assert ActivationManager.activate(game, 0, src, ability) is True
    # The sacrifice is part of the COST (CR 602.2b) — it has already left.
    assert src.zone == "graveyard"
    assert src in game.players[0].graveyard

    # Resolve the ability off the stack.
    item = game.stack.items.pop()
    item.effect(game, item.source, item.controller, item.targets)
    assert len(game.players[0].hand) == before
    assert len(game.delayed_triggers.pending) == 1

    game.turn_number = 6
    game.active_player = 1
    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)
    assert len(game.players[0].hand) == before + 1
    assert game.delayed_triggers.pending == ()


def test_sacrificing_a_self_sacrificing_artifact_adds_its_type_to_the_graveyard():
    """The knock-on the class exists for: a permanent that sacrifices
    itself as an activation cost contributes its CARD TYPE to the
    graveyard, which is what graveyard-type payoffs (delirium, CR-style
    'four or more card types') count."""
    game = _game(turn=5)
    src = _add(game, "Mishra's Bauble")
    for _ in range(5):
        _add(game, "Island", 0, "library")

    def _gy_types(player):
        return {ct for c in player.graveyard for ct in c.template.card_types}

    assert not _gy_types(game.players[0])
    ActivationManager.activate(game, 0, src, src.template.activated_abilities[0])
    from engine.cards import CardType
    assert CardType.ARTIFACT in _gy_types(game.players[0])


# ── AI: a legal ability the AI never enumerates is still dead ─────────

def test_the_ai_enumerates_a_delayed_draw_activation():
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game

    game = _game(turn=5)
    src = _add(game, "Mishra's Bauble")
    for _ in range(10):
        _add(game, "Island", 0, "library")
    for _ in range(3):
        _add(game, "Island")

    snap = snapshot_from_game(game, 0)
    cands = [c for c in activation_candidates(game, 0, snap)
             if c[0] is src]
    assert cands, "a legal delayed-draw activation must be enumerated"
    perm, ab_idx, targets, ev, reason = cands[0]
    assert ev > 0.0
    # The reason names the delay — the log must not claim an immediate draw.
    assert "next_upkeep" in reason
