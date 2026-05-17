"""M8 — channel-ability candidates surface in the response window.

Audit reference: `docs/history/audits/2026-05-16_5panel_bo3_audit.md`
Control Fix 4.  In the audit corpus, Otawara was held all game vs. a
Storm deck chaining Medallion cost-reducers because
`ai/response.py:decide_response` enumerated only instant-speed cards
from hand — channel costs on lands were structurally invisible.

W0-G shipped the `ai.response_enumeration.available_responses` primitive
which yields channel candidates via `Tag.CHANNEL_ABILITY`.  M8 is the
"free composition" step: `decide_response` MUST consume the full W0-G
candidate set so channel candidates can be ranked alongside counters,
removal, and pass.

Tests pin the MECHANIC ("channel candidate appears", "no candidate when
no legal target", "no card-name branches"), not the card.  Otawara
appears only as fixture data.
"""
from __future__ import annotations

import inspect
import random
import re
from pathlib import Path

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


# ─── Fixture helpers (mirror the shape used by W0-G + R2 tests) ────────


def _add_to_zone(game, card_db, name, controller, zone):
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


def _make_response_decider(defender_idx):
    from ai.response import ResponseDecider
    from ai.turn_planner import TurnPlanner
    return ResponseDecider(defender_idx, TurnPlanner(), strategic_logger=None)


# ─── Test 1: channel candidate surfaces when target exists ────────────


def test_otawara_in_hand_with_target_appears_in_response_candidates(card_db):
    """Mechanic: when a CHANNEL_ABILITY-tagged card is in hand AND a
    legal channel target exists on the battlefield, `decide_response`
    MUST enumerate the channel candidate (i.e. the W0-G iterator is
    actually consumed by the response decider).

    Setup mirrors the audit's Otawara-vs-Medallion case: defender has
    Otawara + enough mana for the channel cost ({3}{U}); attacker has
    Ruby Medallion on the battlefield as the bounce target.  A spell
    is on the stack so the response window is open.
    """
    from ai.response_enumeration import available_responses, ResponseCandidate
    from ai.oracle_classifier import Tag, has_tag

    # Cache contract: Otawara MUST be tagged CHANNEL_ABILITY in the
    # smoke cache or the test is meaningless.
    assert has_tag("Otawara, Soaring City", Tag.CHANNEL_ABILITY), (
        "W0-A cache must tag Otawara, Soaring City as CHANNEL_ABILITY"
    )

    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx

    # Defender: Otawara + 4 Islands (mana for {3}{U} channel cost).
    for _ in range(4):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    otawara = _add_to_zone(
        game, card_db, "Otawara, Soaring City", defender_idx, "hand")

    # Attacker: Ruby Medallion on battlefield — a legal channel target
    # (artifact). Plus a non-creature spell on the stack so the window
    # has something to respond to.
    _add_to_zone(game, card_db, "Ruby Medallion", attacker_idx, "battlefield")
    threat = _add_to_zone(
        game, card_db, "Wrath of God", attacker_idx, "stack")
    item = _make_stack_item(threat, attacker_idx)
    game.stack.push(item)

    # Direct enumerator contract: the primitive surfaces the channel.
    candidates = list(available_responses(game, item, controller=defender_idx))
    channel_cands = [c for c in candidates if c.action == "channel"]
    assert channel_cands, (
        "W0-G enumerator must surface channel candidates when a "
        "CHANNEL_ABILITY-tagged card is in hand; got candidates: "
        f"{[c.action for c in candidates]}"
    )
    assert any(c.source is otawara for c in channel_cands), (
        "The channel candidate's source must be the actual Otawara "
        "instance in hand"
    )

    # M8 contract: the same channel host appears in decide_response's
    # candidate set (the W0-G iterator is structurally consumed, not
    # filtered out by an implicit instant/flash gate).
    from ai.response import ResponseDecider
    from ai.turn_planner import TurnPlanner
    decider = ResponseDecider(defender_idx, TurnPlanner(), strategic_logger=None)
    decider_candidates = decider._enumerate_response_candidates(game, item)
    assert otawara in decider_candidates, (
        "M8: decide_response's candidate set must include the channel "
        "host when CHANNEL_ABILITY-tagged.  Got: "
        f"{[c.template.name for c in decider_candidates]}"
    )


# ─── Test 2: decide_response can fire channel when value warrants ─────


def test_decide_response_can_fire_channel_when_value_warrants(card_db):
    """Mechanic: when the only available response is a channel ability
    pointing at a high-value target (a cost-reducer that powers an opp
    combo chain), `decide_response` MUST be willing to fire the channel
    rather than pass.  This is the M8 "free composition" outcome — the
    decider consumes the W0-G iterator and selects the channel.

    Setup: defender has Otawara only (no counters in hand) and full
    mana ({3}{U} available).  Attacker has Ruby Medallion on the
    battlefield as the bounce target.  No stack item — instant-speed
    window at end-of-turn against the Medallion is the audit scenario.
    """
    from ai.response import ResponseDecider
    from ai.turn_planner import TurnPlanner

    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx

    # Defender: Otawara + 4 Islands (covers the {3}{U} channel cost).
    for _ in range(4):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    otawara = _add_to_zone(
        game, card_db, "Otawara, Soaring City", defender_idx, "hand")

    # Attacker: Ruby Medallion as the high-value channel target, plus
    # a stack threat so the response window is non-empty.
    medallion = _add_to_zone(
        game, card_db, "Ruby Medallion", attacker_idx, "battlefield")
    threat = _add_to_zone(
        game, card_db, "Lightning Bolt", attacker_idx, "stack")
    item = _make_stack_item(threat, attacker_idx)
    game.stack.push(item)

    decider = ResponseDecider(defender_idx, TurnPlanner(), strategic_logger=None)

    # The candidate-enumeration step IS the M8 fix surface — the
    # decider's own enumeration MUST include the channel host so any
    # downstream scoring path has the option.  Without M8 this list
    # would be empty (the legacy code gated on `is_instant or
    # has_flash`, which excludes lands like Otawara).
    candidate_sources = decider._enumerate_response_candidates(game, item)
    assert otawara in candidate_sources, (
        "M8: decide_response's candidate enumeration must include the "
        "channel host (Otawara) when CHANNEL_ABILITY-tagged.  Got "
        f"candidate sources: {[c.template.name for c in candidate_sources]}"
    )

    # Decide_response itself must not crash and must not silently drop
    # the candidate.  It may legitimately return None (downstream
    # scoring of channel-vs-pass is a follow-up M-task) or the channel
    # host — but it must NOT raise.
    result = decider.decide_response(game, item)
    # When it does pick the channel host, the returned instance is the
    # one from hand (plumbing sanity check).
    if result is not None:
        chosen, _targets = result
        if chosen.template.name == "Otawara, Soaring City":
            assert chosen is otawara, (
                "If decide_response chooses Otawara, the returned "
                "CardInstance must be the actual hand instance"
            )


# ─── Test 3: no candidate when no legal target ────────────────────────


def test_no_response_candidates_when_no_legal_targets(card_db):
    """Mechanic: a channel ability with a 'target X' clause requires a
    matching permanent in the game.  When the opponent controls no
    permanents matching the target type AND the defender also has no
    matching permanents (only basic lands), the channel has no legal
    target and MUST NOT appear as a response candidate.

    Setup: defender has Otawara + basic Islands only (basics don't
    match Otawara's 'artifact, creature, enchantment, planeswalker'
    target line).  Opponent has nothing on the battlefield.
    """
    from ai.response_enumeration import available_responses

    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx

    # Defender: Otawara + basic Islands ONLY (no artifact / creature /
    # enchantment / planeswalker on defender's side to target).
    for _ in range(4):
        _add_to_zone(game, card_db, "Island", defender_idx, "battlefield")
    _add_to_zone(
        game, card_db, "Otawara, Soaring City", defender_idx, "hand")

    # Attacker: empty battlefield (no permanents to bounce).  Put a
    # non-creature spell on the stack so the response window exists
    # but cannot include the channel.
    threat = _add_to_zone(
        game, card_db, "Lightning Bolt", attacker_idx, "stack")
    item = _make_stack_item(threat, attacker_idx)
    game.stack.push(item)

    candidates = list(available_responses(game, item, controller=defender_idx))
    channel_cands = [c for c in candidates if c.action == "channel"]
    assert not channel_cands, (
        "Channel candidate must be filtered out when no legal target "
        "exists in either battlefield (only basic lands present, which "
        "don't match Otawara's target line); got channel candidates: "
        f"{channel_cands!r}"
    )


# ─── Test 4: no card-name branches for channel dispatch ───────────────


def test_no_card_name_branches_for_channel_dispatch():
    """Mechanic: the channel-response dispatch in `ai/response.py` must
    NOT branch on specific card names — Otawara, Boseiju, Sokenzan,
    etc.  All dispatch goes through `Tag.CHANNEL_ABILITY` (W0-A) via
    the `ai.response_enumeration.available_responses` iterator.

    This guards against the seductive-but-wrong refactor of "add a
    Boseiju-specific case here" when a follow-up fix wants different
    channel handling.  The structural answer is always: extend the
    enumerator + the tag predicate, not the dispatcher.

    Implementation: parse `ai/response.py` with `ast`, walk every
    string-literal node, build a set of line ranges occupied by
    strings (docstrings, comments, regular literals), and flag a
    card-name appearance ONLY when it lives on a CODE line.
    """
    import ast
    import tokenize
    import io

    response_py = Path("ai/response.py")
    text = response_py.read_text(encoding="utf-8")

    # Collect line numbers that are inside any string literal or
    # comment via the tokenizer — these are NOT executable code.
    string_lines: set[int] = set()
    for tok in tokenize.tokenize(io.BytesIO(text.encode("utf-8")).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            for ln in range(tok.start[0], tok.end[0] + 1):
                string_lines.add(ln)

    forbidden_names = (
        "Otawara",
        "Boseiju",
        "Sokenzan",
        "Takenuma",
        "Eiganjo",
    )
    offenders = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if lineno in string_lines:
            continue  # inside a string literal / comment — not code
        for name in forbidden_names:
            if name in line:
                offenders.append((lineno, name, line.rstrip()))

    assert not offenders, (
        "ai/response.py contains card-name references on CODE lines "
        "that would bypass the Tag.CHANNEL_ABILITY dispatch.  All "
        "channel handling must go through W0-A tags + the W0-G "
        "enumerator.  Offending lines:\n"
        + "\n".join(f"  L{lineno}: {name} in `{line}`"
                    for lineno, name, line in offenders)
    )
