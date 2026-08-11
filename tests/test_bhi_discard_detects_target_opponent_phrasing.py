"""BHI discard detection must recognise 'target opponent ... discards'.

The mechanic: any card that causes an opponent to discard is a hand-
disruption threat that BHI should factor into the p_discard prior.
Oracle text is not uniform — Thoughtseize says "Target player ... discards
that card", while Duress says "Target opponent reveals their hand ...
That player discards that card."

The old raw-oracle check ('target player' in oracle and 'discards' in
oracle) correctly detects Thoughtseize but silently misses Duress: the
first clause is False because Duress says "Target opponent", not
"Target player".

The fix: use the pre-computed 'discard' tag on CardTemplate (populated
by card_database.py's regex which covers both 'target player' and
'target opponent' phrasings) instead of re-parsing oracle text at
decision time in bhi._compute_discard_prior.

Card-as-fixture-carrier disclaimer: Duress is used here solely because
its oracle text exercises the 'target opponent' phrasing. The mechanic
under test is "any hand-disruption spell phrased with 'target opponent
... discards' must be detected as a discard spell."
"""
from __future__ import annotations

import random
from unittest.mock import MagicMock, patch

import pytest

from ai.bhi import BayesianHandTracker
from engine.cards import CardInstance
from engine.game_state import GameState


def _make_card(game, card_db, name, controller):
    tmpl = card_db.cards.get(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="library",
    )
    card._game_state = game
    return card


def _make_game_with_lib(card_db, my_deck, opp_deck, opp_lib_names):
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = my_deck
    game.players[1].deck_name = opp_deck
    game.players[1].library = [
        _make_card(game, card_db, n, 1) for n in opp_lib_names
    ]
    game.players[1].hand = []
    return game


def _fake_plan(signal_cards):
    """Create a minimal gameplan mock with the given cards as mulligan keys."""
    plan = MagicMock()
    plan.mulligan_keys = set(signal_cards)
    plan.critical_pieces = set()
    plan.always_early = set()
    return plan


class TestBhiDiscardDetectsTargetOpponentPhrasing:
    """BHI must detect 'target opponent ... discards' as a discard spell."""

    def test_target_opponent_phrasing_is_missing_from_raw_oracle(self, card_db):
        """Pre-condition / documents the bug: Duress says 'Target opponent',
        NOT 'target player', so the old raw-oracle check returns False.
        This test pins the gap that the tag-based fix closes."""
        t = card_db.cards.get("Duress")
        assert t is not None, "Duress must be present in the card DB"
        oracle_lower = (t.oracle_text or '').lower()
        old_check = ('target player' in oracle_lower and 'discards' in oracle_lower)
        assert not old_check, (
            "Duress oracle does not contain 'target player' — the raw "
            "substring check 'target player in oracle and discards in oracle' "
            "returns False, confirming the detection gap this fix closes."
        )

    def test_target_opponent_discard_card_is_tagged_discard(self, card_db):
        """card_database.py's regex must tag Duress as 'discard' even
        though its oracle says 'Target opponent' not 'Target player'.
        This verifies the tag is already populated correctly."""
        t = card_db.cards.get("Duress")
        assert t is not None
        assert "discard" in getattr(t, 'tags', set()), (
            "Duress oracle 'Target opponent ... discards' must produce "
            "the 'discard' tag in card_database.py — the regex covers "
            "'target opponent' via '(?:target player|target opponent)'. "
            "This typed-field path is the one bhi must consume."
        )

    def test_bhi_discard_prior_detects_target_opponent_card_in_signal_names(
        self, card_db
    ):
        """Integration: when the opponent's gameplan lists a card phrased
        as 'target opponent ... discards' as a mulligan key, BHI must
        set p_discard > 0.

        This fails with the old 'target player' raw-oracle check but
        passes after the tag-based fix."""
        game = _make_game_with_lib(
            card_db, "Ruby Storm", "Dimir Midrange",
            ["Duress", "Island", "Swamp"],
        )

        fake = _fake_plan(["Duress"])

        bhi = BayesianHandTracker(player_idx=0)
        with patch("ai.gameplan.get_gameplan", return_value=fake):
            bhi.initialize_from_game(game)

        assert bhi.get_discard_probability() > 0.0, (
            "Opponent's gameplan signals Duress as a mulligan key. "
            "Duress says 'Target opponent ... discards' — not 'target player'. "
            "BHI must still detect it as a discard spell via the 'discard' tag "
            "(already set correctly by card_database.py). "
            f"Got p_discard={bhi.get_discard_probability()!r}; expected > 0."
        )

    def test_thoughtseize_detected_after_fix(self, card_db):
        """Regression guard: Thoughtseize ('Target player ... discards')
        must still produce p_discard > 0 after the tag-based fix."""
        game = _make_game_with_lib(
            card_db, "Ruby Storm", "Dimir Midrange",
            ["Thoughtseize", "Swamp"],
        )

        fake = _fake_plan(["Thoughtseize"])

        bhi = BayesianHandTracker(player_idx=0)
        with patch("ai.gameplan.get_gameplan", return_value=fake):
            bhi.initialize_from_game(game)

        assert bhi.get_discard_probability() > 0.0, (
            "Thoughtseize carries the 'discard' tag — the tag-based fix "
            "must still detect it, since it also says 'target player'."
        )

    def test_non_discard_card_in_signal_names_leaves_p_discard_zero(self, card_db):
        """Regression guard: a non-discard spell in mulligan_keys must NOT
        raise p_discard. Lightning Bolt has no discard effect."""
        game = _make_game_with_lib(
            card_db, "Ruby Storm", "Boros Energy",
            ["Lightning Bolt", "Mountain"],
        )

        fake = _fake_plan(["Lightning Bolt"])

        bhi = BayesianHandTracker(player_idx=0)
        with patch("ai.gameplan.get_gameplan", return_value=fake):
            bhi.initialize_from_game(game)

        assert bhi.get_discard_probability() == 0.0, (
            "Lightning Bolt is not a discard spell; p_discard must "
            "remain 0.0 when only non-discard cards are in signal_names."
        )
