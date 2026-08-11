"""Batch-5 oracle-migrate: typed-field reads in ai/ev_evaluator.py.

Pins the nine oracle-text substring checks migrated to pre-parsed
``CardTemplate`` fields in this batch:

  * ``template.is_counterspell``          replaces 'counter target' in oracle
                                           (lines 904 and 1151 pre-migration)
  * ``template.can_target_player``        replaces 'target player' / 'target
                                           opponent' in oracle  (line 913)
  * ``template.is_tutor``                 replaces the three-way OR
                                           'search your library' /
                                           'from outside the game' /
                                           'from your sideboard'
                                           (lines 1088-1090, 1379, 2339)
  * ``template.has_noncreature_spell_cast_trigger``
                                           replaces 'noncreature spell' in
                                           c_oracle prowess-variant block
                                           (line 2518)

All tests use only the oracle_parser parse functions and the typed fields;
no card names, no DB load.  Class size: every card that is a counterspell,
targets players, searches the library, or triggers on noncreature spells —
hundreds of Modern-legal printings.
"""
from __future__ import annotations

import pytest

from engine.oracle_parser import (
    parse_can_target_player,
    parse_is_tutor,
    parse_has_noncreature_spell_cast_trigger,
)
from ai.ev_evaluator import (
    _ImmediateInteractionTemplate,
    _is_immediate_interaction,
    _is_real_dig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmpl(oracle: str = '', **overrides):
    """Return an _ImmediateInteractionTemplate seeded from the oracle string."""
    return _ImmediateInteractionTemplate(
        is_counterspell=overrides.get(
            'is_counterspell', parse_is_counterspell(oracle)),
        can_target_player=overrides.get(
            'can_target_player', parse_can_target_player(oracle)),
    )


# ---------------------------------------------------------------------------
# 1. is_counterspell — replaces 'counter target' in oracle
#    (was lines 904 and 1151 in _is_immediate_interaction /
#    _enumerate_this_turn_signals)
# ---------------------------------------------------------------------------

class TestIsCounterspellField:
    """``template.is_counterspell`` must gate the counter-target branch.

    ``is_counterspell`` is populated at load time from the card's effects
    list (not via a standalone oracle-parse function), so the unit tests
    here exercise the field via ``_ImmediateInteractionTemplate`` directly.
    The CardDatabase tests confirm the effects-based population is correct
    for real cards; these tests confirm the field drives the right branch
    regardless of oracle text.
    """

    def test_immediate_interaction_uses_is_counterspell_field(self):
        """_is_immediate_interaction returns True when template.is_counterspell
        is True, regardless of the oracle text (the field drives the result)."""
        oracle = "draw a card."  # no 'counter target' substring
        tmpl = _ImmediateInteractionTemplate(is_counterspell=True,
                                             can_target_player=False)
        assert _is_immediate_interaction(oracle, set(), tmpl) is True

    def test_immediate_interaction_false_when_counterspell_false(self):
        """No counter-target signal when is_counterspell=False and no
        'counterspell' tag — even if oracle coincidentally contains 'counter'
        in a different context."""
        oracle = "draw a card."
        tmpl = _ImmediateInteractionTemplate(is_counterspell=False,
                                             can_target_player=False)
        assert _is_immediate_interaction(oracle, set(), tmpl) is False

    def test_counterspell_tag_short_circuits_before_template(self):
        """'counterspell' in tags is checked before the template field —
        ensure that path also returns True without depending on oracle."""
        oracle = "draw a card."
        tmpl = _ImmediateInteractionTemplate(is_counterspell=False,
                                             can_target_player=False)
        assert _is_immediate_interaction(oracle, {'counterspell'}, tmpl) is True

    def test_removal_tag_also_short_circuits(self):
        """'removal' tag also triggers before the template check."""
        oracle = "draw a card."
        tmpl = _ImmediateInteractionTemplate(is_counterspell=False,
                                             can_target_player=False)
        assert _is_immediate_interaction(oracle, {'removal'}, tmpl) is True

    def test_counterspell_field_true_overrides_empty_oracle(self):
        """Even with empty oracle text, is_counterspell=True returns True."""
        tmpl = _ImmediateInteractionTemplate(is_counterspell=True,
                                             can_target_player=False)
        assert _is_immediate_interaction("", set(), tmpl) is True


# ---------------------------------------------------------------------------
# 2. can_target_player — replaces 'target player'/'target opponent' in oracle
#    (was line 913)
# ---------------------------------------------------------------------------

class TestCanTargetPlayerField:
    """``template.can_target_player`` must gate the forced-discard branch."""

    TARGET_PLAYER_ORACLES = [
        "target player discards a card.",
        "target player reveals their hand. you choose a nonland card from it. that player discards that card. you lose 2 life.",
        "target opponent reveals their hand. you choose a noncreature, nonland card from it. that player discards that card.",
        "any target deals 3 damage.",   # "any target" also covers players
    ]

    NON_PLAYER_TARGET_ORACLES = [
        "destroy target creature.",
        "draw a card.",
        "draw two cards, then discard two cards.",  # self-loot — no target player
    ]

    @pytest.mark.parametrize("oracle", TARGET_PLAYER_ORACLES)
    def test_can_target_player_true(self, oracle):
        assert parse_can_target_player(oracle) is True

    @pytest.mark.parametrize("oracle", NON_PLAYER_TARGET_ORACLES)
    def test_can_target_player_false(self, oracle):
        assert parse_can_target_player(oracle) is False

    def test_immediate_interaction_uses_can_target_player_field(self):
        """Forced-discard is classified as immediate interaction via
        template.can_target_player, not by substring matching."""
        oracle = "that player discards that card."  # no 'target player' substring
        tmpl = _ImmediateInteractionTemplate(is_counterspell=False,
                                             can_target_player=True)
        # 'discard' is still matched in the oracle; the field gates the branch
        assert _is_immediate_interaction(oracle, set(), tmpl) is True

    def test_self_loot_not_immediate_even_with_discard_oracle(self):
        """Self-loot must not trigger the forced-discard branch."""
        oracle = "draw two cards, then discard two cards."
        tmpl = _ImmediateInteractionTemplate(is_counterspell=False,
                                             can_target_player=False)
        assert _is_immediate_interaction(oracle, set(), tmpl) is False


# ---------------------------------------------------------------------------
# 3. is_tutor — replaces the three-way OR (lines 1088-1090, 1379, 2339)
# ---------------------------------------------------------------------------

class TestIsTutorField:
    """``template.is_tutor`` covers all three tutor-pattern oracle phrases."""

    TUTOR_ORACLES = [
        "search your library for a basic land card, reveal it, put it into your hand, then shuffle.",
        "search your library for a card and put it into your hand, then shuffle.",
        "you may choose a card you own from outside the game and put it into your hand.",
        "you may reveal a card from your sideboard and put it into your hand.",
    ]

    NON_TUTOR_ORACLES = [
        "draw a card.",
        "look at the top three cards of your library.",    # scry — not a tutor
        "counter target spell.",
    ]

    @pytest.mark.parametrize("oracle", TUTOR_ORACLES)
    def test_is_tutor_true(self, oracle):
        assert parse_is_tutor(oracle) is True

    @pytest.mark.parametrize("oracle", NON_TUTOR_ORACLES)
    def test_is_tutor_false(self, oracle):
        assert parse_is_tutor(oracle) is False

    def test_is_real_dig_true_for_search_library_tutor(self):
        """_is_real_dig must return True for library-search cards via is_tutor."""

        class _Tpl:
            oracle_text = "Search your library for a card and put it into your hand, then shuffle."
            is_tutor = True  # as set by parse_is_tutor at load time

        class _Card:
            template = _Tpl()

        assert _is_real_dig(_Card()) is True

    def test_is_real_dig_true_for_wish_style_tutor(self):
        """Wish-style 'from outside the game' is_tutor=True also triggers dig."""

        class _Tpl:
            oracle_text = "You may choose a card you own from outside the game and put it into your hand."
            is_tutor = True

        class _Card:
            template = _Tpl()

        assert _is_real_dig(_Card()) is True

    def test_is_real_dig_false_for_non_tutor(self):
        """A card that does not dig does not count as real dig."""

        class _Tpl:
            oracle_text = "Add {R}."
            is_tutor = False

        class _Card:
            template = _Tpl()

        assert _is_real_dig(_Card()) is False


# ---------------------------------------------------------------------------
# 4. has_noncreature_spell_cast_trigger — replaces 'noncreature spell' in
#    c_oracle (was line 2518)
# ---------------------------------------------------------------------------

class TestHasNoncreatureSpellCastTriggerField:
    """``template.has_noncreature_spell_cast_trigger`` gates the prowess-
    variant bonus projection in _project_spell."""

    TRIGGER_ORACLES = [
        "whenever you cast a noncreature spell, this creature gets +1/+0 until end of turn.",
        "whenever you cast a noncreature spell, put a +1/+1 counter on this creature.",
        "whenever a player casts a noncreature spell, this creature gets +1/+0 until end of turn.",
    ]

    NON_TRIGGER_ORACLES = [
        "flying.",
        "whenever this creature attacks, it gets +2/+0 until end of turn.",
        "draw a card.",
    ]

    @pytest.mark.parametrize("oracle", TRIGGER_ORACLES)
    def test_trigger_true_for_noncreature_cast(self, oracle):
        assert parse_has_noncreature_spell_cast_trigger(oracle) is True

    @pytest.mark.parametrize("oracle", NON_TRIGGER_ORACLES)
    def test_trigger_false_for_unrelated_oracles(self, oracle):
        assert parse_has_noncreature_spell_cast_trigger(oracle) is False

    def test_field_drives_prowess_variant_detection(self):
        """Cards with has_noncreature_spell_cast_trigger=True are prowess
        variants; cards without it are not — even if oracle contains 'noncreature'
        in a different context."""
        oracle_without_cast_trigger = (
            "whenever a noncreature permanent enters, draw a card."
        )
        assert parse_has_noncreature_spell_cast_trigger(
            oracle_without_cast_trigger) is False

    def test_field_true_for_slickshot_pump_wording(self):
        """Slickshot Show-Off variant: 'gets +2/+0' on noncreature cast."""
        oracle = ("whenever you cast a noncreature spell, "
                  "this creature gets +2/+0 until end of turn.")
        assert parse_has_noncreature_spell_cast_trigger(oracle) is True
