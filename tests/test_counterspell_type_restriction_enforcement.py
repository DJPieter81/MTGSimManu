"""Counter type restriction enforcement — targeting and resolution layers.

Mechanic under test: a counterspell with a type restriction
("counter target noncreature spell", "counter target instant or sorcery spell")
must:

  (A) Treat only matching spell types as legal targets when evaluating
      cast legality (target_solver._spell_token_matches).
  (B) Fizzle at resolution with no effect when the targeted spell is of
      the wrong type, leaving the targeted spell on the stack.

Restriction tokens tested:
  - "noncreature_spell"      — excludes creature spells (Negate shape)
  - "instant_or_sorcery_spell" — restricts to instants/sorceries (gaps
    pre-fix: _spell_token_matches had no handler for this token, so
    has_legal_target returned False even for legal instant/sorcery targets)

Names in this file are fixture labels only; no card name is hardcoded in
engine/ or ai/ source (ABSTRACTION CONTRACT, CLAUDE.md).
"""
from __future__ import annotations

import random
import types as _types_mod

import pytest

from engine.cards import (
    Ability, AbilityType, CardInstance, CardTemplate, CardType, ManaCost,
)
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_game():
    return GameState(rng=random.Random(0))


def _creature_spell_tmpl():
    return CardTemplate(
        name="Test Fixture: Creature Spell",
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=2, green=1),
        supertypes=[], subtypes=["Beast"],
        power=3, toughness=3, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )


def _sorcery_spell_tmpl():
    return CardTemplate(
        name="Test Fixture: Sorcery Spell",
        card_types=[CardType.SORCERY],
        mana_cost=ManaCost(generic=1, black=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="Draw a card.", tags=set(),
    )


def _instant_spell_tmpl():
    return CardTemplate(
        name="Test Fixture: Instant Spell",
        card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=1, red=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="Deal 3 damage to target creature.", tags=set(),
    )


def _enchantment_spell_tmpl():
    return CardTemplate(
        name="Test Fixture: Enchantment Spell",
        card_types=[CardType.ENCHANTMENT],
        mana_cost=ManaCost(generic=1, white=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )


def _noncreature_counter_tmpl():
    """Counter targeting only noncreature spells (Negate shape)."""
    return CardTemplate(
        name="Test Fixture: Noncreature Counter",
        card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=1, blue=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(),
        abilities=[Ability(
            ability_type=AbilityType.CAST,
            description="Counter target spell.",
            targets_required=1,
        )],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="Counter target noncreature spell.",
        tags={"counterspell"},
        is_counterspell=True,
        counter_target_kind="noncreature_spell",
        counter_tax_amount=0,
    )


def _instant_or_sorcery_counter_tmpl():
    """Counter targeting only instant or sorcery spells."""
    return CardTemplate(
        name="Test Fixture: Instant-or-Sorcery Counter",
        card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=1, blue=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(),
        abilities=[Ability(
            ability_type=AbilityType.CAST,
            description="Counter target spell.",
            targets_required=1,
        )],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="Counter target instant or sorcery spell.",
        tags={"counterspell"},
        is_counterspell=True,
        counter_target_kind="instant_or_sorcery_spell",
        counter_tax_amount=0,
    )


def _push_stack(game, target_tmpl, counter_tmpl):
    """Push (target, counter) onto the stack.  Counter is on top.
    Returns (target_card, counter_card)."""
    target_card = CardInstance(
        template=target_tmpl, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="stack",
    )
    target_card._game_state = game

    counter_card = CardInstance(
        template=counter_tmpl, owner=1, controller=1,
        instance_id=game.next_instance_id(), zone="stack",
    )
    counter_card._game_state = game

    target_item = StackItem(
        item_type=StackItemType.SPELL, source=target_card, controller=0,
        targets=[], effect=None, description="",
    )
    counter_item = StackItem(
        item_type=StackItemType.SPELL, source=counter_card, controller=1,
        targets=[target_card.instance_id], effect=None,
        description="Counter target spell.",
    )
    game.stack.push(target_item)
    game.stack.push(counter_item)
    return target_card, counter_card


# ── _spell_token_matches unit tests ─────────────────────────────────────────


class TestSpellTokenMatchesNoncreature:
    """_spell_token_matches correctly handles the 'noncreature_spell' token."""

    def _card(self, tmpl):
        """Minimal CardInstance-like wrapper for _spell_token_matches."""
        obj = _types_mod.SimpleNamespace(template=tmpl)
        return obj

    def test_noncreature_token_rejects_creature_spell(self):
        """A creature spell must NOT match the noncreature_spell token."""
        from engine.target_solver import _spell_token_matches
        card = self._card(_creature_spell_tmpl())
        assert not _spell_token_matches(card, frozenset({"noncreature_spell"})), (
            "_spell_token_matches must return False for a creature spell "
            "when the restriction is 'noncreature_spell'"
        )

    def test_noncreature_token_accepts_sorcery_spell(self):
        """A sorcery spell is not a creature — must match noncreature_spell."""
        from engine.target_solver import _spell_token_matches
        card = self._card(_sorcery_spell_tmpl())
        assert _spell_token_matches(card, frozenset({"noncreature_spell"})), (
            "_spell_token_matches must return True for a sorcery spell "
            "when the restriction is 'noncreature_spell'"
        )

    def test_noncreature_token_accepts_instant_spell(self):
        """An instant spell is not a creature — must match noncreature_spell."""
        from engine.target_solver import _spell_token_matches
        card = self._card(_instant_spell_tmpl())
        assert _spell_token_matches(card, frozenset({"noncreature_spell"})), (
            "_spell_token_matches must return True for an instant spell "
            "when the restriction is 'noncreature_spell'"
        )


class TestSpellTokenMatchesInstantOrSorcery:
    """_spell_token_matches handles the 'instant_or_sorcery_spell' token.

    Pre-fix gap: the function had no branch for this token, so it returned
    False for every spell type — making valid instant/sorcery targets
    appear illegal at cast-gate time.
    """

    def _card(self, tmpl):
        obj = _types_mod.SimpleNamespace(template=tmpl)
        return obj

    def test_instant_or_sorcery_token_accepts_instant(self):
        """An instant spell must be a legal target for instant_or_sorcery_spell counters."""
        from engine.target_solver import _spell_token_matches
        card = self._card(_instant_spell_tmpl())
        assert _spell_token_matches(card, frozenset({"instant_or_sorcery_spell"})), (
            "_spell_token_matches must return True for an instant spell "
            "when the restriction is 'instant_or_sorcery_spell'"
        )

    def test_instant_or_sorcery_token_accepts_sorcery(self):
        """A sorcery spell must be a legal target for instant_or_sorcery_spell counters."""
        from engine.target_solver import _spell_token_matches
        card = self._card(_sorcery_spell_tmpl())
        assert _spell_token_matches(card, frozenset({"instant_or_sorcery_spell"})), (
            "_spell_token_matches must return True for a sorcery spell "
            "when the restriction is 'instant_or_sorcery_spell'"
        )

    def test_instant_or_sorcery_token_rejects_enchantment(self):
        """An enchantment spell is NOT an instant or sorcery — must not match."""
        from engine.target_solver import _spell_token_matches
        card = self._card(_enchantment_spell_tmpl())
        assert not _spell_token_matches(card, frozenset({"instant_or_sorcery_spell"})), (
            "_spell_token_matches must return False for an enchantment spell "
            "when the restriction is 'instant_or_sorcery_spell'"
        )

    def test_instant_or_sorcery_token_rejects_creature(self):
        """A creature spell is NOT an instant or sorcery — must not match."""
        from engine.target_solver import _spell_token_matches
        card = self._card(_creature_spell_tmpl())
        assert not _spell_token_matches(card, frozenset({"instant_or_sorcery_spell"})), (
            "_spell_token_matches must return False for a creature spell "
            "when the restriction is 'instant_or_sorcery_spell'"
        )


# ── cast-gate integration (has_legal_target) ────────────────────────────────


class TestCastGateNoncreature:
    """has_legal_target_for_spell blocks noncreature counters when only
    creature spells are on the stack (pre-cast legality)."""

    def test_noncreature_counter_has_no_legal_target_when_only_creature_on_stack(
            self):
        """Noncreature counter must report no legal target when only a creature
        spell is on the stack — engine correctly gates the cast."""
        from engine.target_solver import parse, has_legal_target_for_spell
        game = _make_game()
        creature_tmpl = _creature_spell_tmpl()
        creature_card = CardInstance(
            template=creature_tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="stack",
        )
        creature_card._game_state = game
        creature_item = StackItem(
            item_type=StackItemType.SPELL, source=creature_card, controller=0,
            targets=[], effect=None, description="",
        )
        game.stack.push(creature_item)

        reqs = parse("Counter target noncreature spell.")
        assert not has_legal_target_for_spell(game, 1, reqs), (
            "has_legal_target_for_spell must be False when only a creature "
            "spell is on the stack and the counter restricts to noncreature"
        )

    def test_noncreature_counter_has_legal_target_when_sorcery_on_stack(self):
        """Noncreature counter must report a legal target when a sorcery is
        on the stack."""
        from engine.target_solver import parse, has_legal_target_for_spell
        game = _make_game()
        sorcery_card = CardInstance(
            template=_sorcery_spell_tmpl(), owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="stack",
        )
        sorcery_card._game_state = game
        game.stack.push(StackItem(
            item_type=StackItemType.SPELL, source=sorcery_card, controller=0,
            targets=[], effect=None, description="",
        ))

        reqs = parse("Counter target noncreature spell.")
        assert has_legal_target_for_spell(game, 1, reqs), (
            "has_legal_target_for_spell must be True when a sorcery spell "
            "(noncreature) is on the stack for a noncreature counter"
        )


class TestCastGateInstantOrSorcery:
    """has_legal_target_for_spell correctly handles instant-or-sorcery counters.

    Pre-fix: _SPELL_TARGET could not parse 'instant or sorcery' qualifier, so
    parse() produced no TargetRequirement — effectively treating the spell as
    castable regardless of stack contents, which is wrong.  Post-fix: the
    cast gate correctly allows/blocks based on stack content.
    """

    def test_instant_or_sorcery_counter_has_legal_target_when_instant_on_stack(
            self):
        """Cast gate must permit the counter when an instant spell is on the stack."""
        from engine.target_solver import parse, has_legal_target_for_spell
        game = _make_game()
        instant_card = CardInstance(
            template=_instant_spell_tmpl(), owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="stack",
        )
        instant_card._game_state = game
        game.stack.push(StackItem(
            item_type=StackItemType.SPELL, source=instant_card, controller=0,
            targets=[], effect=None, description="",
        ))

        reqs = parse("Counter target instant or sorcery spell.")
        # Post-fix: a TargetRequirement for instant_or_sorcery_spell must exist
        # and an instant spell must satisfy it.
        assert has_legal_target_for_spell(game, 1, reqs), (
            "has_legal_target_for_spell must be True when an instant is on "
            "the stack for an instant-or-sorcery counter"
        )

    def test_instant_or_sorcery_counter_has_no_legal_target_when_only_enchantment(
            self):
        """Cast gate must block the counter when only an enchantment is on the stack."""
        from engine.target_solver import parse, has_legal_target_for_spell
        game = _make_game()
        enc_card = CardInstance(
            template=_enchantment_spell_tmpl(), owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="stack",
        )
        enc_card._game_state = game
        game.stack.push(StackItem(
            item_type=StackItemType.SPELL, source=enc_card, controller=0,
            targets=[], effect=None, description="",
        ))

        reqs = parse("Counter target instant or sorcery spell.")
        assert not has_legal_target_for_spell(game, 1, reqs), (
            "has_legal_target_for_spell must be False when only an enchantment "
            "is on the stack for an instant-or-sorcery counter"
        )


# ── resolution enforcement ────────────────────────────────────────────────────


def test_noncreature_counter_cannot_target_creature_spell():
    """Noncreature counter fizzles at resolution when targeting a creature spell.

    Mechanic: a counter with counter_target_kind='noncreature_spell' must
    never counter a creature spell.  The resolution layer logs the fizzle and
    leaves the targeted spell on the stack.
    """
    game = _make_game()
    target_card, counter_card = _push_stack(
        game, _creature_spell_tmpl(), _noncreature_counter_tmpl()
    )

    game.resolve_stack()  # resolves the noncreature counter

    # Target spell must NOT have been countered
    on_stack = [si.source for si in game.stack.items]
    assert target_card in on_stack, (
        "Creature spell should remain on the stack after a noncreature "
        "counter fizzles — it must not be countered"
    )
    assert "fizzle" in " ".join(game.log).lower() or "can't counter creature" in " ".join(game.log).lower(), (
        "Log must record the fizzle; got:\n" + "\n".join(game.log[-5:])
    )


def test_instant_sorcery_counter_targets_only_instant_or_sorcery():
    """Instant-or-sorcery counter fizzles at resolution when targeting an enchantment.

    Mechanic: a counter with counter_target_kind='instant_or_sorcery_spell'
    must only counter instants or sorceries.  Targeting an enchantment spell
    must fizzle the counter without affecting the enchantment.
    """
    game = _make_game()
    target_card, counter_card = _push_stack(
        game, _enchantment_spell_tmpl(), _instant_or_sorcery_counter_tmpl()
    )

    game.resolve_stack()  # resolves the instant-or-sorcery counter

    # Enchantment spell must NOT have been countered
    on_stack = [si.source for si in game.stack.items]
    assert target_card in on_stack, (
        "Enchantment spell should remain on the stack after an "
        "instant-or-sorcery counter fizzles — it must not be countered"
    )
    assert "fizzle" in " ".join(game.log).lower() or "wrong target type" in " ".join(game.log).lower(), (
        "Log must record the fizzle; got:\n" + "\n".join(game.log[-5:])
    )


def test_instant_sorcery_counter_successfully_counters_sorcery():
    """Instant-or-sorcery counter successfully counters a sorcery spell.

    Confirms the 'legal target' side: when targeting an actual sorcery, the
    counter must resolve and send the sorcery to the graveyard.
    """
    game = _make_game()
    target_card, counter_card = _push_stack(
        game, _sorcery_spell_tmpl(), _instant_or_sorcery_counter_tmpl()
    )

    game.resolve_stack()  # resolves the instant-or-sorcery counter

    # Sorcery spell must have been countered — no longer on stack
    on_stack = [si.source for si in game.stack.items]
    assert target_card not in on_stack, (
        "Sorcery spell must be countered (removed from stack) by an "
        "instant-or-sorcery counter when it is a legal target"
    )
    assert target_card in game.players[0].graveyard or target_card.zone == "graveyard", (
        "Countered sorcery must go to graveyard"
    )
    countered_lines = [l for l in game.log if "is countered" in l.lower()]
    assert countered_lines, (
        "Log must show the counter resolving; got:\n" + "\n".join(game.log[-5:])
    )
