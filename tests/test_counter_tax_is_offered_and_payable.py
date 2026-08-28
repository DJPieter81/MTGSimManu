""""Unless its controller pays {N}" — the tax must reach the PRODUCTION
decision seam, and a payer holding the mana must be able to pay it.

Mechanic under test: a soft counter (CR 601.2b cost-imposed choice on a
counter effect) gives the targeted spell's controller a real decision.
Two rules are pinned here, both phrased on the mechanic rather than on
any card:

  1. **Offer** — when the targeted spell's controller has untapped
     sources worth at least the tax, `engine.optional_costs.
     offer_counter_tax` must present the `OptionalCost` to
     `callbacks.decide_optional_cost` (the single AI seam). A payer with
     the mana is never silently skipped.
  2. **Payability** — when the seam answers "pay", the mana is actually
     produced from the payer's untapped sources and the spell survives
     on the stack.

Coverage gap this closes: `tests/test_counter_tax_framework.py` pins
both branches with STUB callbacks (`_AlwaysPay` / `_NeverPay`), so it
cannot see a failure that lives in the production wiring — the engine
gate (`available_mana_estimate`), the payment application
(`ManaPayment.tap_lands_for_mana`), or the real
`AICallbacks.decide_optional_cost`. The Bo3 replay evidence in
`docs/diagnostics/2026-08-27_dimir_overperformance_root_cause.md`
(Ponza s62500 G2 T8: a 3-mana enchantment soft-countered for {2} with
"4 mana remaining") was flagged at n=1 against exactly that wiring.

Class size: every "counter unless its controller pays {N}" spell in
Modern (Spell Pierce, Mana Leak, Miscalculation, Censor, Metallic
Rebuke, Stubborn Denial, Condescend, ...) crossed with every spell that
can be taxed. Real-DB cards appear as fixture carriers only.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_runner import AICallbacks
from engine.game_state import GameState, Phase
from engine.optional_costs import offer_counter_tax, parse_counter_tax_cost
from engine.stack import StackItem, StackItemType

SOFT_COUNTER = "Spell Pierce"          # "unless its controller pays {2}"
TAXED_SPELL = "Fable of the Mirror-Breaker"   # 3-mana noncreature spell
LAND = "Mountain"                      # untapped red source, 1 mana each


def _make_game(callbacks):
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    game.players[0].deck_name = "Boros Energy"
    game.players[1].deck_name = "Dimir Midrange"
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _instance(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    return card


def _untapped_lands(game, card_db, controller, n):
    for _ in range(n):
        land = _instance(game, card_db, LAND, controller, "battlefield")
        land.enter_battlefield()
        land.tapped = False
        game.players[controller].battlefield.append(land)


def _stack_soft_counter_on_spell(game, card_db):
    """Targeted spell (P0) with the soft counter (P1) on top of it."""
    taxed = _instance(game, card_db, TAXED_SPELL, 0, "stack")
    counter = _instance(game, card_db, SOFT_COUNTER, 1, "stack")
    assert (counter.template.counter_tax_amount or 0) > 0, (
        f"{SOFT_COUNTER} fixture carries no counter tax — pick another "
        f"carrier for the soft-counter shape")
    game.stack.push(StackItem(
        item_type=StackItemType.SPELL, source=taxed, controller=0,
        targets=[], effect=None, description="",
    ))
    game.stack.push(StackItem(
        item_type=StackItemType.SPELL, source=counter, controller=1,
        targets=[taxed.instance_id], effect=None,
        description="Counter target spell.",
    ))
    return taxed, counter


class _RecordingCallbacks(AICallbacks):
    """Production callbacks, instrumented: records every optional cost
    the engine offers so the test can assert the offer happened."""

    def __init__(self):
        self.offered = []

    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        self.offered.append((player_idx, opt.name, opt.cost.amount))
        return super().decide_optional_cost(game, player_idx, opt)


class _RecordingPayCallbacks(_RecordingCallbacks):
    """Records the offer, then answers 'pay' — isolates the engine
    offer+payment wiring from the AI's strategic verdict."""

    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        self.offered.append((player_idx, opt.name, opt.cost.amount))
        return True


class TestCounterTaxIsOfferedWhenPayable:

    def test_tax_is_offered_to_a_payer_holding_more_than_the_tax(
            self, card_db):
        """A controller with untapped sources strictly above the tax
        must be ASKED — the affordability gate only skips the offer
        when the mana genuinely is not there."""
        cb = _RecordingCallbacks()
        game = _make_game(cb)
        _untapped_lands(game, card_db, controller=0, n=4)
        taxed, counter = _stack_soft_counter_on_spell(game, card_db)
        tax = counter.template.counter_tax_amount

        offer_counter_tax(game, counter, taxed)

        assert cb.offered, (
            f"soft counter's {{{tax}}} tax was never offered though the "
            f"controller held 4 untapped sources "
            f"(available_mana_estimate="
            f"{game.players[0].available_mana_estimate})"
        )
        payer_idx, _name, amount = cb.offered[0]
        assert payer_idx == 0, "the tax must be offered to the TARGETED "\
                               "spell's controller, not the counter's"
        assert amount == tax

    def test_paying_the_tax_taps_mana_and_saves_the_spell(self, card_db):
        """When the seam answers 'pay', the payment is really made
        (sources tapped) and the spell is NOT countered."""
        cb = _RecordingPayCallbacks()
        game = _make_game(cb)
        _untapped_lands(game, card_db, controller=0, n=4)
        taxed, counter = _stack_soft_counter_on_spell(game, card_db)
        tax = counter.template.counter_tax_amount

        paid = offer_counter_tax(game, counter, taxed)

        assert paid is True, "an affordable, accepted tax must apply"
        tapped = sum(1 for c in game.players[0].battlefield if c.tapped)
        assert tapped >= tax, (
            f"paying a {{{tax}}} tax tapped only {tapped} sources")

    def test_paid_tax_leaves_the_spell_on_the_stack_through_resolution(
            self, card_db):
        """End-to-end through `resolve_stack`: the counter resolves,
        the tax is paid, and the taxed spell survives."""
        game = _make_game(_RecordingPayCallbacks())
        _untapped_lands(game, card_db, controller=0, n=4)
        taxed, _counter = _stack_soft_counter_on_spell(game, card_db)

        game.resolve_stack()

        assert taxed in [si.source for si in game.stack.items], (
            "taxed spell left the stack even though the tax was paid")
        assert not any("is countered" in line for line in game.log), (
            f"log shows a counter despite payment: {game.log}")

    def test_unpayable_tax_is_not_offered(self, card_db):
        """Rules gate (not a strategic choice): with no untapped
        sources the decision is never presented."""
        cb = _RecordingCallbacks()
        game = _make_game(cb)
        taxed, counter = _stack_soft_counter_on_spell(game, card_db)

        paid = offer_counter_tax(game, counter, taxed)

        assert paid is False
        assert not cb.offered, (
            f"an unpayable tax was offered anyway: {cb.offered}")

    def test_verbose_mana_log_reports_mana_left_after_the_cost_is_paid(
            self, card_db):
        """Diagnostic integrity: the "[Mana] ... N mana remaining" line
        must describe what the controller can still spend AFTER this
        spell, not include the mana earmarked for it. Measured before
        payment, a 4-land player casting a 3-cost spell reads as "4
        mana remaining" while holding one untapped land — which is what
        produced the n=1 "unpaid counter tax" flag this file
        investigates."""
        from engine.mana import ManaCost
        from engine.mana_payment import ManaPayment

        game = _make_game(AICallbacks())
        game.verbose = True
        _untapped_lands(game, card_db, controller=0, n=4)

        assert ManaPayment.tap_lands_for_mana(
            game, 0, ManaCost(generic=3), card_name="Test Fixture: Spell")

        mana_lines = [l for l in game.log if "mana remaining" in l]
        assert mana_lines, f"no mana log emitted: {game.log}"
        reported = int(mana_lines[-1].split("mana remaining")[0]
                       .rsplit(",", 1)[1].strip())
        untapped = sum(1 for c in game.players[0].battlefield
                       if not c.tapped)
        assert reported == untapped, (
            f"log claims {reported} mana remaining but the controller "
            f"has {untapped} untapped sources and an empty pool")

    def test_tax_descriptor_exists_for_every_soft_counter_shape(
            self, card_db):
        """Parser-side pin: the OptionalCost is built from the typed
        `counter_tax_amount` field, so any soft counter produces one."""
        game = _make_game(AICallbacks())
        taxed, counter = _stack_soft_counter_on_spell(game, card_db)
        opt = parse_counter_tax_cost(counter, taxed)
        assert opt is not None
        assert opt.cost.kind == "mana"
        assert opt.cost.amount == counter.template.counter_tax_amount
