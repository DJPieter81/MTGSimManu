"""Failing-test-first contract for the response_enumeration primitive (W0-G).

Per CLAUDE.md `§Hard prohibitions`: "No fix without a failing test in
the same diff.  Test goes red first, then the fix lands and turns it
green.  Both in the same commit."

The primitive under test — `ai/response_enumeration.py:available_responses` —
is the single iteration site over every legal response action the AI
could take in an instant-speed window (counter, removal, discard,
channel, activate, pitch-cast, pass).  Consumers (M2 chain-aware
counter, M8 channel-response, M10 burn-to-PW enumeration) read from
this iterator and filter for their own needs.

Tests pin the mechanic, not the card.  Card names appear only as
fixture data, never in the test identifier.  This file is the W0-G
red-phase contract; the green phase ships in the same diff.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


# ─── Fixtures ──────────────────────────────────────────────────────────


def _add_to_zone(game, card_db, name, controller, zone):
    """Mirrors the helper in test_response_gate_*.py — keep the shape
    identical so cross-test fixture maintenance is mechanical."""
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "stack":
        pass  # caller pushes the StackItem
    return card


def _make_stack_item(card, controller):
    return StackItem(
        item_type=StackItemType.SPELL,
        source=card,
        controller=controller,
    )


# ─── Counterspell enumeration ─────────────────────────────────────────


def test_counterspell_in_hand_yields_counter_candidate_for_stack_item(card_db):
    """Mechanic: a counterspell card in hand with a spell on the stack
    that we have mana to cast against yields a candidate with
    action='counter' and source=the counterspell card.

    This is the structural minimum for M2 (chain-aware counter): the
    enumerator MUST surface every counterable spell as a counter
    candidate so downstream scoring can rank counter-now vs. hold-for-
    later among the full set of options.
    """
    from ai.response_enumeration import available_responses

    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx

    # Defender has UU to cast Counterspell.
    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    counter = _add_to_zone(
        game, card_db, "Counterspell", defender_idx, "hand")

    # Opponent puts a spell on the stack — needs to be a non-creature so
    # this test isolates the counter-vs-stack mechanic from the (separate)
    # triage rule "skip counter when post-resolution removal exists".
    threat = _add_to_zone(
        game, card_db, "Wrath of God", attacker_idx, "stack")
    item = _make_stack_item(threat, attacker_idx)
    game.stack.push(item)

    candidates = list(available_responses(game, item, controller=defender_idx))
    counter_cands = [c for c in candidates if c.action == "counter"]

    assert counter_cands, (
        "A Counterspell in hand with a spell on the stack must yield "
        "at least one action='counter' candidate"
    )
    assert any(c.source is counter for c in counter_cands), (
        "The candidate's source must be the actual Counterspell "
        "instance from hand"
    )


def test_no_stack_item_yields_no_counter_candidates(card_db):
    """Mechanic: counters need a target on the stack.  With no stack
    item the enumerator MUST NOT emit counter candidates (they would
    have no legal target — fizzles on resolve, CR 601.2c)."""
    from ai.response_enumeration import available_responses

    game = GameState(rng=random.Random(0))
    defender_idx = 1
    game.active_player = defender_idx
    game.priority_player = defender_idx

    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    # No stack item.
    candidates = list(available_responses(game, None, controller=defender_idx))
    counter_cands = [c for c in candidates if c.action == "counter"]

    assert not counter_cands, (
        "A counter candidate requires a stack item to target; with an "
        "empty stack the enumerator must not emit any counters."
    )


# ─── Channel-from-hand enumeration ────────────────────────────────────


def test_channel_land_in_hand_yields_channel_candidate(card_db):
    """Mechanic: a card with Channel in hand (oracle-classified via
    Tag.CHANNEL_ABILITY) MUST yield an action='channel' candidate
    whose source is that card.  Drives M8 (channel response — Otawara
    held all game vs Storm Medallion chains in the audit corpus).

    The check is timing-agnostic at this layer — the enumerator
    surfaces channel as an option whenever the card is in hand;
    downstream filters decide whether to actually fire on a given
    stack item or in a given window.
    """
    from ai.oracle_classifier import Tag, has_tag
    from ai.response_enumeration import available_responses

    # Cache contract: Otawara MUST be tagged CHANNEL_ABILITY in the
    # smoke cache for this test to mean what it says.  Failing this
    # assertion means the W0-A cache regressed.
    assert has_tag("Otawara, Soaring City", Tag.CHANNEL_ABILITY), (
        "W0-A cache must tag Otawara, Soaring City as CHANNEL_ABILITY "
        "for the channel-response mechanic to be testable here"
    )

    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx

    # Defender has Otawara in hand — and also some Islands to support
    # the {3}{U} channel cost (the enumerator yields the candidate
    # independent of mana; mana legality is the caller's job).  We
    # provide a target on opp's battlefield so target legality holds.
    for _ in range(4):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    otawara = _add_to_zone(
        game, card_db, "Otawara, Soaring City", defender_idx, "hand")
    # A bounce target on opp's battlefield so target legality holds
    # (Otawara channels into "return target artifact/creature/...").
    _add_to_zone(
        game, card_db, "Grizzly Bears", attacker_idx, "battlefield")

    candidates = list(available_responses(game, None, controller=defender_idx))
    channel_cands = [c for c in candidates if c.action == "channel"]

    assert channel_cands, (
        "A Channel-tagged card in hand must yield at least one "
        "action='channel' candidate"
    )
    assert any(c.source is otawara for c in channel_cands), (
        "The candidate's source must be the actual Otawara instance "
        "from hand"
    )


# ─── Pass-is-always-a-candidate ───────────────────────────────────────


def test_pass_is_always_a_candidate(card_db):
    """Mechanic: passing priority is ALWAYS a legal response.  The
    enumerator MUST yield at least one action='pass' candidate in
    every state — empty hand, full stack, no mana, anything.

    This is what lets the consumer treat "do nothing" as a scoreable
    option alongside every active response — no special-case
    early-return needed."""
    from ai.response_enumeration import available_responses

    game = GameState(rng=random.Random(0))
    defender_idx = 1
    game.active_player = 0
    game.priority_player = defender_idx
    # Deliberately empty hand and empty board — pass must STILL appear.

    candidates = list(available_responses(game, None, controller=defender_idx))
    pass_cands = [c for c in candidates if c.action == "pass"]

    assert len(pass_cands) >= 1, (
        "Pass must always appear as a candidate; the enumerator yielded "
        f"no pass candidate (full candidate list: {candidates!r})"
    )


def test_pass_appears_exactly_once(card_db):
    """Mechanic: passing is a single distinct option.  The enumerator
    MUST NOT emit duplicate pass candidates — exactly one represents
    the 'do nothing' branch in the decision tree."""
    from ai.response_enumeration import available_responses

    game = GameState(rng=random.Random(0))
    defender_idx = 1
    game.active_player = 0
    game.priority_player = defender_idx
    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")

    candidates = list(available_responses(game, None, controller=defender_idx))
    pass_cands = [c for c in candidates if c.action == "pass"]
    assert len(pass_cands) == 1, (
        f"Pass must appear exactly once; got {len(pass_cands)} copies"
    )


# ─── Disjointness contract ────────────────────────────────────────────


def test_candidate_iteration_is_disjoint_per_source(card_db):
    """Mechanic: each (action, source) pair is yielded at most once.
    A single card in hand cannot legitimately appear twice as the
    same action on the same iteration — that would represent the
    same move twice and skew downstream scoring.

    Different actions on the same source ARE allowed (a card that
    could be cast normally or pitch-cast — same source, two distinct
    cost paths)."""
    from ai.response_enumeration import available_responses

    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx

    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(game, card_db, "Counterspell", defender_idx, "hand")
    threat = _add_to_zone(
        game, card_db, "Wrath of God", attacker_idx, "stack")
    item = _make_stack_item(threat, attacker_idx)
    game.stack.push(item)

    candidates = list(available_responses(game, item, controller=defender_idx))
    # (action, source-id-or-None) tuples — None for pass.
    seen = []
    for c in candidates:
        key = (c.action, getattr(c.source, "instance_id", None))
        seen.append(key)

    assert len(seen) == len(set(seen)), (
        f"Duplicate (action, source) pairs in enumeration: "
        f"{[k for k in seen if seen.count(k) > 1]!r}"
    )


# ─── Public API shape ─────────────────────────────────────────────────


def test_response_candidate_has_expected_fields(card_db):
    """Mechanic: ResponseCandidate carries enough information for a
    downstream consumer to score it WITHOUT re-querying the game
    state (action kind, source card, cost, targets, tap-out flag).
    Pin the public shape so adding/removing a field is a deliberate
    breaking change."""
    from ai.response_enumeration import ResponseCandidate

    fields = {f for f in ResponseCandidate.__dataclass_fields__}
    required = {"action", "source", "cost", "targets", "requires_tap_out"}
    missing = required - fields
    assert not missing, (
        f"ResponseCandidate is missing required fields: {missing}.  "
        f"Existing fields: {fields}"
    )


def test_available_responses_returns_iterator(card_db):
    """Mechanic: enumeration is lazy.  Chains can trigger many
    candidates and the downstream filter cuts the live set small;
    materialising every option up front would be wasteful in deep
    storm chains.  Pin the return type as an iterator (not a list)."""
    import collections.abc
    from ai.response_enumeration import available_responses

    game = GameState(rng=random.Random(0))
    defender_idx = 1
    game.active_player = 0
    game.priority_player = defender_idx
    _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")

    result = available_responses(game, None, controller=defender_idx)
    assert isinstance(result, collections.abc.Iterator), (
        f"available_responses must return an Iterator (lazy), "
        f"not {type(result).__name__}"
    )
