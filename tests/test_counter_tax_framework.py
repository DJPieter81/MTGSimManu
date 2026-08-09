""""Unless its controller pays {N}" soft-counter framework.

Mechanic under test (CR 601.2i-style cost-imposed choice, applied to
counterspells): "Counter target spell unless its controller pays
{N}." gives the TARGETED spell's controller a real decision — pay the
tax or lose the spell — instead of behaving as an unconditional
("hard") counter. Before this fix, `engine/spell_resolution.py`
synthesized a "Counter target X" ability description from oracle text
but discarded the "unless...pays {N}" clause entirely, so soft
counters (Metallic Rebuke, Mana Leak, Countersquall) counter every
spell unconditionally, identical to Counterspell.

Also covers the structural dispatch fix: the counter branch used to
key off `"counter" in ability.description.lower()` — a raw substring
match. Two independent problems with that:

1. It only worked because no OTHER synthesized ability description
   happens to contain "counter" (confirmed empirically); a raw
   ORACLE-text substring check (the plan's original framing) would
   have collided with "+1/+1 counter"/"charge counter" text.
2. Worse, gating on a TEMPLATE-level `is_counterspell` flag alone
   (with no per-ability scoping) mis-fires on every OTHER ability of
   a multi-ability counterspell — 58 Modern-legal counterspells
   (Cryptic Command, Absorb, Censor, Bone to Ash, ...) carry a second
   synthesized ability (e.g. "Draw 1 card(s)") alongside the counter
   effect. The fix scopes the dispatch to the ability whose own
   description is the synthesized "Counter target ..." string, in
   addition to the structured template flag.

A third, more severe pre-existing bug surfaced while fixing the
above: `engine/oracle_resolver.py::resolve_spell_from_oracle` (tried
BEFORE the generic per-ability loop) matches its "draw N cards"
pattern against the WHOLE oracle string, not a single clause. On a
real "draw + counter" counterspell (Censor, Confirm Suspicions,
Exclude, Cryptic Command, ...) it fires the draw, returns True, and
the caller skips the per-ability loop entirely — the counter effect
NEVER runs. These cards currently don't counter anything at all
(worse than "counters unconditionally, ignoring tax" — they don't
counter at all). Fix: counterspells (`template.is_counterspell`) skip
`resolve_spell_from_oracle` and always route through the per-ability
loop, which correctly fires the draw ability AND the counter ability
as two independent entries.

Card names appear only as fixture carriers (synthetic CardTemplates
preserving real oracle-text shapes) per CLAUDE.md's ABSTRACTION
CONTRACT — the mechanic under test is the "unless...pays" tax choice,
not any specific card.
"""
from __future__ import annotations

import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.cards import (
    Ability, AbilityType, CardInstance, CardTemplate, CardType, ManaCost,
)
from engine.game_state import GameState
from engine.oracle_parser import parse_counter_tax
from engine.stack import StackItem, StackItemType


# ─── oracle-parser unit tests ──────────────────────────────────────


class TestParseCounterTax:
    """`parse_counter_tax` extracts the {N} from a soft-counter clause."""

    def test_hard_counter_has_zero_tax(self):
        assert parse_counter_tax("Counter target spell.") == 0

    def test_soft_counter_extracts_tax_amount(self):
        assert parse_counter_tax(
            "Counter target spell unless its controller pays {3}."
        ) == 3

    def test_soft_counter_noncreature_variant(self):
        assert parse_counter_tax(
            "Counter target noncreature spell unless its controller "
            "pays {2}."
        ) == 2

    def test_no_tax_when_no_counter_ability_present(self):
        """An unrelated 'unless...pays' clause (e.g. a land's optional
        untapped-entry cost) must not be mistaken for a counter tax on
        a card that doesn't counter anything."""
        assert parse_counter_tax(
            "You may pay 1 life. If you don't, this land enters "
            "tapped."
        ) == 0

    def test_tax_not_leaked_from_a_separate_ability_paragraph(self):
        """A multi-ability card with an unrelated 'unless...pays'
        clause in ONE paragraph and an unconditional counter in a
        DIFFERENT paragraph must report zero tax — the two clauses
        don't belong to the same ability."""
        oracle = (
            "Flying\n"
            "Counter target spell.\n"
            "{1}: Regenerate this creature unless you pay {5}."
        )
        assert parse_counter_tax(oracle) == 0


# ─── structured template fields (real cards) ───────────────────────


class TestCounterTemplateFields:
    """`CardTemplate.is_counterspell`/`counter_target_kind`/
    `counter_tax_amount` are populated at DB-load time from the same
    parsed effect the ability description is synthesized from."""

    def test_soft_counter_card_has_tax_amount(self, card_db):
        rebuke = card_db.get_card("Metallic Rebuke")
        if rebuke is None:
            pytest.skip("Metallic Rebuke not in DB")
        assert rebuke.is_counterspell
        assert rebuke.counter_target_kind == "spell"
        assert rebuke.counter_tax_amount == 3

    def test_hard_counter_card_has_zero_tax(self, card_db):
        cs = card_db.get_card("Counterspell")
        if cs is None:
            pytest.skip("Counterspell not in DB")
        assert cs.is_counterspell
        assert cs.counter_tax_amount == 0

    def test_creature_only_counter_has_correct_kind(self, card_db):
        essence = card_db.get_card("Essence Scatter")
        if essence is None:
            pytest.skip("Essence Scatter not in DB")
        assert essence.is_counterspell
        assert essence.counter_target_kind == "creature_spell"

    def test_no_card_with_plus_one_counter_text_is_miscategorized(
            self, card_db):
        """Regression guard at DB scale: no card whose ONLY 'counter'
        mention is a +1/+1-counter/charge-counter ability (i.e. it
        never says 'counter target') is flagged is_counterspell."""
        false_positives = [
            name for name, tmpl in card_db.cards.items()
            if tmpl.is_counterspell
            and 'counter target' not in (tmpl.oracle_text or '').lower()
        ]
        assert not false_positives, (
            f"{len(false_positives)} card(s) flagged is_counterspell "
            f"without a 'counter target' clause: {false_positives[:10]}"
        )


# ─── resolution-engine integration ─────────────────────────────────


def _soft_counter_template(tax_amount=3):
    return CardTemplate(
        name="Test Fixture: Soft Counter",
        card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=1, blue=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(),
        abilities=[Ability(ability_type=AbilityType.CAST,
                           description="Counter target spell.",
                           targets_required=1)],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text=(f"Counter target spell unless its controller "
                     f"pays {{{tax_amount}}}."),
        tags={"counterspell"},
        is_counterspell=True, counter_target_kind="spell",
        counter_tax_amount=tax_amount,
    )


def _multi_ability_counter_template():
    """Mirrors the real Cryptic-Command-class shape: a counter ability
    PLUS a second, unrelated ability ('Draw 1 card(s)') on the same
    card — the exact shape that would mis-fire the counter branch
    twice under a template-only (unscoped) dispatch flag."""
    return CardTemplate(
        name="Test Fixture: Counter Plus Draw",
        card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=2, blue=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(),
        abilities=[
            Ability(ability_type=AbilityType.CAST,
                   description="Draw 1 card(s)"),
            Ability(ability_type=AbilityType.CAST,
                   description="Counter target spell.",
                   targets_required=1),
        ],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="Draw a card. Counter target spell.",
        tags={"counterspell"},
        is_counterspell=True, counter_target_kind="spell",
        counter_tax_amount=0,
    )


def _hard_counter_template():
    return CardTemplate(
        name="Test Fixture: Hard Counter",
        card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=1, blue=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(),
        abilities=[Ability(ability_type=AbilityType.CAST,
                           description="Counter target spell.",
                           targets_required=1)],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="Counter target spell.",
        tags={"counterspell"},
        is_counterspell=True, counter_target_kind="spell",
        counter_tax_amount=0,
    )


def _dummy_spell_template():
    return CardTemplate(
        name="Test Fixture: Dummy Sorcery",
        card_types=[CardType.SORCERY],
        mana_cost=ManaCost(generic=2),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="Draw a card.", tags=set(),
    )


def _push_counter_scenario(game, tax_amount=3):
    """Common setup: target spell + counter spell both on the stack,
    counter on top (resolves first). Returns (target_card, counter_card)."""
    target_template = _dummy_spell_template()
    target_card = CardInstance(
        template=target_template, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="stack",
    )
    target_card._game_state = game

    counter_template = (_soft_counter_template(tax_amount) if tax_amount
                         else _hard_counter_template())
    counter_card = CardInstance(
        template=counter_template, owner=1, controller=1,
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


def _land(game, controller, n):
    """N untapped colorless-producing lands so the tax is affordable."""
    lands = []
    for _ in range(n):
        tmpl = CardTemplate(
            name="Test Fixture: Basic Land",
            card_types=[CardType.LAND],
            mana_cost=ManaCost(), supertypes=[], subtypes=[],
            power=None, toughness=None, loyalty=None,
            keywords=set(), abilities=[], color_identity=set(),
            produces_mana=["C"], enters_tapped=False,
            oracle_text="{T}: Add {C}.", tags=set(),
        )
        land = CardInstance(
            template=tmpl, owner=controller, controller=controller,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        land._game_state = game
        land.tapped = False
        game.players[controller].battlefield.append(land)
        lands.append(land)
    return lands


class _AlwaysPayCallbacks(DefaultCallbacks):
    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        return True


class _NeverPayCallbacks(DefaultCallbacks):
    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        return False


class _AssertNotOfferedCallbacks(DefaultCallbacks):
    """Fails the test if the tax is ever offered — used to verify the
    engine-side affordability gate skips the decision entirely."""
    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        raise AssertionError(
            "decide_optional_cost was called despite the controller "
            "having insufficient mana — the affordability gate should "
            "have skipped the offer entirely."
        )


class TestSoftCounterResolution:
    def test_soft_counter_resolves_when_controller_pays_tax(self, card_db):
        """Target's controller can afford the tax and the AI seam
        agrees to pay: the spell must NOT be countered — it stays on
        the stack to resolve normally."""
        game = GameState(rng=random.Random(0), callbacks=_AlwaysPayCallbacks())
        _land(game, controller=0, n=3)
        target_card, counter_card = _push_counter_scenario(game, tax_amount=3)

        game.resolve_stack()  # resolves the soft counter

        assert target_card in [si.source for si in game.stack.items], (
            "target spell was removed from the stack even though its "
            "controller paid the tax"
        )
        assert target_card.zone == "stack"
        assert not any("is countered" in line for line in game.log), (
            f"log incorrectly shows a counter: {game.log}"
        )

    def test_soft_counter_counters_when_tax_unpaid(self, card_db):
        """Target's controller can afford the tax but chooses not to
        pay: the spell IS countered."""
        game = GameState(rng=random.Random(0), callbacks=_NeverPayCallbacks())
        _land(game, controller=0, n=3)
        target_card, counter_card = _push_counter_scenario(game, tax_amount=3)

        game.resolve_stack()

        assert target_card not in [si.source for si in game.stack.items]
        assert target_card.zone == "graveyard"
        assert target_card in game.players[0].graveyard
        assert any("is countered" in line for line in game.log)

    def test_soft_counter_counters_without_offering_when_unaffordable(
            self, card_db):
        """Target's controller has NO untapped mana sources: the tax
        is unpayable, so no decision is offered at all (a rules gate,
        not a strategic choice) and the spell is simply countered."""
        game = GameState(rng=random.Random(0),
                         callbacks=_AssertNotOfferedCallbacks())
        # Deliberately no lands for player 0.
        target_card, counter_card = _push_counter_scenario(game, tax_amount=3)

        game.resolve_stack()

        assert target_card not in [si.source for si in game.stack.items]
        assert target_card.zone == "graveyard"

    def test_hard_counter_still_counters_unconditionally(self, card_db):
        """Regression guard: a tax_amount=0 (hard) counter counters
        every time — the tax framework must not change unconditional-
        counter behavior."""
        game = GameState(rng=random.Random(0), callbacks=_NeverPayCallbacks())
        target_card, counter_card = _push_counter_scenario(game, tax_amount=0)

        game.resolve_stack()

        assert target_card.zone == "graveyard"


class TestMultiAbilityCounterDoesNotDoubleFire:
    def test_counter_with_second_ability_only_counters_once(self, card_db):
        """A counterspell with a second synthesized ability (e.g.
        'Draw 1 card(s)', mirroring Cryptic Command/Absorb/Censor's
        real multi-ability shape) must not re-enter the counter branch
        on that second ability's iteration — the target is countered
        exactly once, not double-processed or crashed on a re-pop of
        an already-removed stack item."""
        game = GameState(rng=random.Random(0), callbacks=_NeverPayCallbacks())

        target_template = _dummy_spell_template()
        target_card = CardInstance(
            template=target_template, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="stack",
        )
        target_card._game_state = game

        counter_template = _multi_ability_counter_template()
        counter_card = CardInstance(
            template=counter_template, owner=1, controller=1,
            instance_id=game.next_instance_id(), zone="stack",
        )
        counter_card._game_state = game

        target_item = StackItem(
            item_type=StackItemType.SPELL, source=target_card,
            controller=0, targets=[], effect=None, description="",
        )
        counter_item = StackItem(
            item_type=StackItemType.SPELL, source=counter_card,
            controller=1, targets=[target_card.instance_id], effect=None,
            description="Draw 1 card(s). Counter target spell.",
        )
        game.stack.push(target_item)
        game.stack.push(counter_item)

        game.resolve_stack()  # must not raise (e.g. IndexError on a
                               # second pop of an already-removed item)

        assert target_card.zone == "graveyard"
        counter_lines = [l for l in game.log if "is countered" in l]
        assert len(counter_lines) == 1, (
            f"expected exactly one counter log line, got {counter_lines}"
        )


class TestRealMultiAbilityCounterspellsActuallyCounter:
    """Real Modern "draw + counter" counterspells must actually
    counter their target. Previously intercepted by
    `resolve_spell_from_oracle`'s unscoped "draw N cards" pattern
    (matched against the whole oracle string, not the counter clause
    specifically) before ever reaching the counter dispatch — Censor,
    Confirm Suspicions, Exclude, and Cryptic Command would draw/bounce/
    tap per their non-counter mode(s) and return, silently never
    countering anything."""

    def test_censor_counters_its_target(self, card_db):
        censor = card_db.get_card("Censor")
        if censor is None:
            pytest.skip("Censor not in DB")
        assert censor.is_counterspell and censor.counter_tax_amount == 1

        game = GameState(rng=random.Random(0), callbacks=_NeverPayCallbacks())
        # No lands for the target's controller — tax is unaffordable
        # either way, isolating this test to "does it counter at all",
        # independent of the pay/don't-pay AI decision already covered
        # by TestSoftCounterResolution above.
        target_card = CardInstance(
            template=_dummy_spell_template(), owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="stack",
        )
        target_card._game_state = game
        counter_card = CardInstance(
            template=censor, owner=1, controller=1,
            instance_id=game.next_instance_id(), zone="stack",
        )
        counter_card._game_state = game

        game.stack.push(StackItem(
            item_type=StackItemType.SPELL, source=target_card,
            controller=0, targets=[], effect=None, description="",
        ))
        game.stack.push(StackItem(
            item_type=StackItemType.SPELL, source=counter_card,
            controller=1, targets=[target_card.instance_id], effect=None,
            description="",
        ))

        game.resolve_stack()

        assert target_card.zone == "graveyard", (
            f"Censor failed to counter its target (zone="
            f"{target_card.zone!r}) — likely intercepted by "
            f"resolve_spell_from_oracle's unscoped draw-clause match "
            f"before reaching the counter dispatch."
        )
