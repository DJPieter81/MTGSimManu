"""Library-search tutors must project actual N-card hand value.

Picks up the audit row from
`docs/design/2026-05-10_oracle_pattern_projection_blindspot_audit.md`
and the on-file fix shape from
`docs/diagnostics/2026-05-10_multi_card_tutor_projection_audit.md`.

Mechanic (rule-phrased): the card-draw projection in
`compute_play_ev` (post-PR-#334) extracts the actual N from oracle
text for two patterns — literal `draw N cards` and impulse-draw
`exile the top N cards … may play/cast`. A third pattern is the
library-search tutor: `search your library for (up to )?N cards …
into your hand`. Cultivate / Kodama's Reach (2 lands), Squadron
Hawk (up to 3 named copies), Gifts Ungiven (search 4, hand 2),
Increasing Ambition / Tooth and Nail (2 cards) all match.

# Class size

40 printed Modern cards match the multi-card-tutor pattern. The
2026-05-10 active-pool measurement showed 0 of these in the 16
registered decks — but the parsed extractor is principled and
generic, future-proofing for any deck that registers one of the 40
matching cards. Singular "search your library for a card" phrasings
fall through to the baseline +1 (already correct via
`is_draw_engine`).

# Failing-test rule

Library-search tutor cards with `cantrip` / `card_advantage` tag in
the projection's predicate gate must project hand-size delta = N for
the matching oracle phrasing, not the +1 baseline. Same shape as
PR #334's impulse-draw test — the rule names the mechanic, not the
card.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import compute_play_ev, snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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
        card.summoning_sick = False
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _project_hand_delta(card_db, card_name, archetype="midrange"):
    """Build a snapshot and project a single cast, returning the
    `(projected.my_hand_size - snap.my_hand_size)` delta. The
    projection has already subtracted 1 for the cast itself, so the
    returned delta is "net hand-size change after resolution":
      * 0 for a 1-card cantrip (subtracted 1, drew 1 back)
      * +1 for a 2-card cantrip (subtracted 1, drew 2)
      * +N-1 for an N-card library tutor (subtracted 1, fetched N)
    """
    from ai.ev_evaluator import _project_spell
    from ai.deck_knowledge import DeckKnowledge
    game = GameState(rng=random.Random(0))
    for _ in range(8):
        _add(game, card_db, "Forest", controller=0, zone="battlefield")
    for _ in range(8):
        _add(game, card_db, "Forest", controller=0, zone="library")
    card = _add(game, card_db, card_name, controller=0, zone="hand")
    _add(game, card_db, "Mountain", controller=1, zone="battlefield")
    game.players[0].deck_name = "Test"
    game.players[1].deck_name = "Test Opp"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1
    snap = snapshot_from_game(game, 0)

    dk = DeckKnowledge()
    projected = _project_spell(card, snap, dk, game, 0)
    return projected.my_hand_size - snap.my_hand_size


class TestTutorProjectsCardCountWhenMultipleTargets:
    """Library-search tutors with oracle text `search your library
    for (up to )?N cards … into your hand` project an N-card hand
    delta in `_project_spell`. Singular `search for a card`
    phrasings fall through to the baseline +1 (already correct via
    `is_draw_engine`)."""

    def test_search_three_cards_all_to_hand_projects_three(
            self, card_db):
        """Squadron Hawk pattern: 'search for up to three cards
        named Squadron Hawk and PUT THEM into your hand'. All N
        searched cards reach hand. Net hand delta: cast removes 1,
        search returns 3 → +2.

        Pre-fix: regex didn't match → `is_draw_engine` baseline
        added +1, net 0.
        Post-fix: regex matches `up to three cards` + `put them
        into your hand` → +3 total, net +2.
        """
        delta = _project_hand_delta(card_db, "Squadron Hawk")
        assert delta >= 2, (
            f"Squadron Hawk projected hand delta = {delta} "
            f"(expected ≥ 2). 'Search for up to three cards … put "
            f"them into your hand' must project +3 hand value (net "
            f"+2 after cast subtraction), not the 1-card cantrip "
            f"baseline (net 0). The projection block at "
            f"ai/ev_evaluator.py:1918 needs the search-tutor branch: "
            f"`search your library` + `put them into your hand` + "
            f"_parse_oracle_count of the matched numeral."
        )

    def test_split_fate_search_does_NOT_match_all_to_hand_branch(
            self, card_db):
        """Cultivate / Kodama's Reach pattern: search up to TWO
        basic lands, but `put one onto the battlefield and the
        other into your hand` — only 1 card reaches hand. Net hand
        delta: cast removes 1, search returns 1 to hand → 0.

        This is the regression anchor. The regex must require `put
        them into your hand` (all-N) and NOT match the split-fate
        phrasing — otherwise Cultivate would be over-credited as a
        +2-hand-cantrip when its actual hand impact is +1 (cast -1,
        +1 to hand, +1 to battlefield)."""
        delta = _project_hand_delta(card_db, "Cultivate")
        # Cultivate `cantrip` tag fires is_draw_engine baseline +1.
        # The split-fate phrasing must NOT additionally credit +1
        # via the multi-card search branch, so net delta stays at 0
        # (cast -1, +1 baseline). Strictly < 1.
        assert delta < 1, (
            f"Cultivate projected hand delta = {delta} "
            f"(expected < 1 i.e. baseline-only). The split-fate "
            f"phrasing 'put one onto the battlefield and the other "
            f"into your hand' must NOT match the all-N `put them "
            f"into your hand` regex — Cultivate's actual hand impact "
            f"is +1 (one card to hand), not +2. If this fires, the "
            f"regex is too greedy."
        )

    def test_singular_tutor_falls_through_to_baseline(
            self, card_db):
        """Anchor: a tutor with 'search your library for a card'
        (singular) must NOT accidentally match the multi-card
        extractor. Net hand delta: cast removes 1, baseline +1 → 0.

        This protects against a too-greedy regex that would treat
        every `search your library` + `into your hand` as multi-card.
        """
        # Mastermind's Acquisition: `Search your library for a card,
        # put it into your hand, then shuffle.` Tags={'tutor'} only —
        # NOT cantrip — so it doesn't enter the projection block at
        # all (is_draw_engine returns False). Net delta is 0 because
        # cast subtracted 1 and no draw projection ran. The test
        # verifies the projection didn't crash on the singular `a
        # card` phrasing or accidentally credit hand-size.
        delta = _project_hand_delta(card_db, "Mastermind's Acquisition")
        # Acceptance: delta is finite and < 1. Baseline cast = -1
        # (no is_draw_engine), so net is -1; the regex must not have
        # matched and added +N. If the regex were too greedy, delta
        # would be non-negative.
        assert delta < 1, (
            f"Mastermind's Acquisition projected hand delta = "
            f"{delta} (expected < 1). The singular 'search for a "
            f"card' phrasing must NOT match the multi-card regex. "
            f"If this fires, the regex needs tightening."
        )
