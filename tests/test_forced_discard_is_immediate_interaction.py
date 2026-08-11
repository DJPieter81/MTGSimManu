"""Targeted forced discard is immediate interaction regardless of
target-player vs target-opponent oracle templating.

The deferral gate (docs/design/ev_correctness_overhaul.md §3) only
lets a spell through when `_enumerate_this_turn_signals` finds a
same-turn value signal.  Hand attack IS same-turn interaction — the
stripped card is gone before the opponent's next untap — but the
oracle fallback in `_is_immediate_interaction` matched only the
"target opponent ... discard" templating.  Modern's most-played hand
attack ("Target player reveals their hand ... discards that card")
returned False, so the entire class was scored at -exposure and
deferred forever (root cause RC-2 in
docs/diagnostics/2026-07-05_goryos_field_13pct_root_cause.md; trace
cite: goryos-dimir seed 50000, T3-T11 all pass on Thoughtseize at
-0.1 with mana open).

Class size: every "target player discards" printing (~40+ Modern
cards).  The oracle classifier's `Tag.FORCED_DISCARD` already
recognises the mechanic for the cast-time projection; this test pins
the deferral predicate to the same rule.
"""
from __future__ import annotations

from ai.ev_evaluator import _is_immediate_interaction, _ImmediateInteractionTemplate


# Oracle texts are the *templating forms*, lower-cased as the caller
# does.  The rule under test is templating-form coverage, not any
# specific card.
TARGET_PLAYER_REVEAL_DISCARD = (
    "target player reveals their hand. you choose a nonland card "
    "from it. that player discards that card. you lose 2 life."
)
TARGET_PLAYER_REVEAL_DISCARD_CMC_CAP = (
    "target player reveals their hand. you choose a nonland card "
    "from it with mana value 3 or less. that player discards that card."
)
TARGET_OPPONENT_REVEAL_DISCARD = (
    "target opponent reveals their hand. you choose a noncreature, "
    "nonland card from it. that player discards that card."
)
TARGET_PLAYER_RANDOM_DISCARD = (
    "target player discards two cards at random."
)
SELF_LOOT = (
    "draw two cards, then discard two cards. flashback {3}{u}."
)
PLAIN_CANTRIP = (
    "draw a card."
)


def _tmpl(can_target_player: bool = False,
          is_counterspell: bool = False) -> _ImmediateInteractionTemplate:
    """Convenience factory so tests can be written concisely."""
    return _ImmediateInteractionTemplate(
        is_counterspell=is_counterspell,
        can_target_player=can_target_player,
    )


class TestForcedDiscardIsImmediateInteraction:

    def test_target_player_reveal_discard_is_immediate(self):
        assert _is_immediate_interaction(
            TARGET_PLAYER_REVEAL_DISCARD, set(),
            _tmpl(can_target_player=True)) is True

    def test_target_player_reveal_discard_with_cmc_cap_is_immediate(self):
        assert _is_immediate_interaction(
            TARGET_PLAYER_REVEAL_DISCARD_CMC_CAP, set(),
            _tmpl(can_target_player=True)) is True

    def test_target_opponent_reveal_discard_is_immediate(self):
        """Regression guard: the previously-covered templating stays
        covered."""
        assert _is_immediate_interaction(
            TARGET_OPPONENT_REVEAL_DISCARD, set(),
            _tmpl(can_target_player=True)) is True

    def test_target_player_random_discard_is_immediate(self):
        assert _is_immediate_interaction(
            TARGET_PLAYER_RANDOM_DISCARD, set(),
            _tmpl(can_target_player=True)) is True

    def test_self_loot_is_not_forced_discard_interaction(self):
        """Own-hand looting (draw N, discard N) is card selection, not
        opponent interaction — it must NOT ride this branch.  (It has
        its own card_draw signal.)"""
        assert _is_immediate_interaction(
            SELF_LOOT, set(), _tmpl(can_target_player=False)) is False

    def test_plain_cantrip_is_not_immediate_interaction(self):
        assert _is_immediate_interaction(
            PLAIN_CANTRIP, set(), _tmpl(can_target_player=False)) is False
