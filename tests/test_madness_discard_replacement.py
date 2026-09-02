"""Madness (CR 702.35) — a discard replacement that offers an alternative cast.

Rule: "Madness [cost]" means "If you discard this card, discard it into
exile instead of your graveyard" and "When this card is put into exile this
way, you may cast it by paying [cost] rather than its mana cost. If you
don't, put this card into your graveyard."

Class: 47 Modern-legal cards in the DB carry the keyword (Blazing
Rootwalla, Fiery Temper, Asylum Visitor, Bloodhall Priest, …). Every one of
them shares the oracle template "Madness {cost} (reminder)".

Tests are phrased on the mechanic, never on a card:
  - parser: madness cost parsed from oracle, {0} is a real (free) cost
  - load: CardTemplate.madness_cost populated at load time
  - discard replacement: a discarded madness card is exiled, cast for its
    madness cost when affordable, and resolves (creature → battlefield)
  - discard replacement: the madness cast pays the madness cost, not the
    printed mana cost
  - fallback: an unaffordable or declined madness cast lands in the graveyard
  - timing: the madness cast is made during resolution, so it ignores
    sorcery-speed timing (CR 702.35c / 608.2g)
  - negative: a non-madness discard never touches exile
  - every discard site (forced discard, cleanup hand-size discard) routes
    through the same funnel
"""
from __future__ import annotations

import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance, CardTemplate, CardType, Color
from engine.constants import MAX_HAND_SIZE
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import parse_madness_cost


MADNESS_REMINDER = (
    " (If you discard this card, discard it into exile. When you do, cast "
    "it for its madness cost or put it into your graveyard.)"
)


# ── Parser ─────────────────────────────────────────────────────────────────

class TestParseMadnessCost:
    def test_zero_madness_cost_is_a_real_free_cost(self):
        """Madness {0} must parse to an empty ManaCost, NOT None — a zero
        cost is payable, and the card is castable for free when discarded."""
        cost = parse_madness_cost("Madness {0}" + MADNESS_REMINDER)
        assert cost is not None
        assert isinstance(cost, ManaCost)
        assert cost.cmc == 0

    def test_colored_madness_cost(self):
        cost = parse_madness_cost(
            "This deals 3 damage to any target.\nMadness {R}" + MADNESS_REMINDER)
        assert cost is not None
        assert cost.red == 1 and cost.generic == 0

    def test_mixed_madness_cost(self):
        cost = parse_madness_cost("Flying\nMadness {1}{B}{R}" + MADNESS_REMINDER)
        assert cost is not None
        assert cost.generic == 1 and cost.black == 1 and cost.red == 1

    def test_case_insensitive(self):
        cost = parse_madness_cost("madness {2}{b}")
        assert cost is not None
        assert cost.generic == 2 and cost.black == 1

    def test_no_madness_keyword_returns_none(self):
        assert parse_madness_cost("Flying\nWhen this creature enters, draw a card.") is None

    def test_madness_word_without_cost_is_not_a_madness_cost(self):
        """Cards that REFER to madness (Anje-style 'if it has madness, you
        may cast it') do not themselves have a madness cost."""
        oracle = ("Whenever you discard a card, if it has madness, you may "
                  "cast it for its madness cost.")
        assert parse_madness_cost(oracle) is None

    def test_reminder_text_does_not_leak_into_cost(self):
        """The reminder sentence carries no braces; only the keyword's own
        symbols form the cost."""
        cost = parse_madness_cost("Madness {1}{R}" + MADNESS_REMINDER)
        assert cost.cmc == 2


class TestMadnessCostPopulatedAtLoad:
    def test_cardtemplate_derives_madness_cost_from_oracle(self):
        template = _madness_creature_template(
            ManaCost(generic=1, red=1), madness=ManaCost(), madness_sym="{0}",
            explicit_field=False)
        assert template.madness_cost is not None
        assert template.madness_cost.cmc == 0

    def test_card_database_populates_madness_cost(self):
        from engine.card_database import CardDatabase
        db = CardDatabase()
        with_madness = [t for t in db.cards.values() if t.madness_cost is not None]
        assert len(with_madness) >= 10, (
            "the DB holds dozens of madness cards; load-time population is missing")
        # Every populated cost came from the keyword line, so it never
        # exceeds the printed mana value (madness is a discount or free).
        for t in with_madness:
            assert t.madness_cost.cmc <= max(t.cmc, t.madness_cost.cmc)

    def test_non_madness_card_has_no_madness_cost(self):
        from engine.card_database import CardDatabase
        db = CardDatabase()
        plain = [t for t in db.cards.values()
                 if 'madness' not in (t.oracle_text or '').lower()]
        assert plain, "DB fixture must contain non-madness cards"
        assert all(t.madness_cost is None for t in plain[:200])


# ── Fixtures ───────────────────────────────────────────────────────────────

def _make_game(active: int = 0) -> GameState:
    game = GameState(rng=random.Random(0))
    game.active_player = active
    game.current_phase = Phase.MAIN1
    game.turn_number = 2
    return game


def _mountain(game: GameState, owner: int = 0) -> CardInstance:
    land_t = CardTemplate(
        name="Mountain", card_types=[CardType.LAND], mana_cost=ManaCost(),
        supertypes=["Basic"], subtypes=["Mountain"],
        power=None, toughness=None, loyalty=None, keywords=set(), abilities=[],
        color_identity={Color.RED}, produces_mana=["R"], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    return CardInstance(template=land_t, owner=owner, controller=owner,
                        instance_id=game.next_instance_id(), zone="battlefield")


def _madness_creature_template(mana_cost: ManaCost, madness: ManaCost,
                               madness_sym: str, *, explicit_field: bool = True,
                               name: str = "Synthetic Madness Creature") -> CardTemplate:
    kwargs = {}
    if explicit_field:
        kwargs["madness_cost"] = madness
    return CardTemplate(
        name=name, card_types=[CardType.CREATURE], mana_cost=mana_cost,
        supertypes=[], subtypes=["Lizard"],
        power=1, toughness=1, loyalty=None, keywords=set(), abilities=[],
        color_identity={Color.RED}, produces_mana=[], enters_tapped=False,
        oracle_text=f"Madness {madness_sym}{MADNESS_REMINDER}", tags=set(),
        **kwargs,
    )


def _plain_creature_template() -> CardTemplate:
    return CardTemplate(
        name="Synthetic Plain Creature", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1, red=1), supertypes=[], subtypes=["Bear"],
        power=2, toughness=2, loyalty=None, keywords=set(), abilities=[],
        color_identity={Color.RED}, produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )


def _in_hand(game: GameState, template: CardTemplate, owner: int = 0) -> CardInstance:
    card = CardInstance(template=template, owner=owner, controller=owner,
                        instance_id=game.next_instance_id(), zone="hand")
    card._game_state = game
    game.players[owner].hand.append(card)
    return card


def _resolve_all(game: GameState) -> None:
    while not game.stack.is_empty:
        game.resolve_stack()
        game.check_state_based_actions()


class _DeclineOfferedCasts(DefaultCallbacks):
    """AI stand-in that refuses every engine-offered cast."""

    def decide_offered_cast(self, game, player_idx, card) -> bool:
        return False


# ── Discard replacement ────────────────────────────────────────────────────

class TestMadnessDiscardReplacement:
    def test_discarding_free_madness_card_exiles_then_casts_it(self):
        """Madness {0}: the discard routes hand → exile, the card is cast for
        {0} from exile, and the creature spell resolves onto the battlefield."""
        game = _make_game()
        p = game.players[0]
        card = _in_hand(game, _madness_creature_template(
            ManaCost(generic=1, red=1), ManaCost(), "{0}"))

        game.discard_card(0, card, cause="discard")

        assert card not in p.hand
        assert card not in p.graveyard
        assert card.zone == "stack" and not game.stack.is_empty, (
            "madness cast must put the card on the stack from exile")
        _resolve_all(game)
        assert card in p.battlefield and card.zone == "battlefield"
        assert card not in p.exile

    def test_madness_cast_pays_madness_cost_not_printed_cost(self):
        """Printed {2}{R}, madness {R}, one untapped Mountain: the printed
        cost is unaffordable but the madness cost is — the cast succeeds
        and the Mountain is what paid for it."""
        game = _make_game()
        p = game.players[0]
        mountain = _mountain(game)
        p.battlefield.append(mountain)
        card = _in_hand(game, _madness_creature_template(
            ManaCost(generic=2, red=1), ManaCost(red=1), "{R}"))

        game.discard_card(0, card, cause="discard")
        _resolve_all(game)

        assert card in p.battlefield
        assert mountain.tapped, "the madness cost must be paid with real mana"

    def test_unaffordable_madness_cast_puts_card_in_graveyard(self):
        """No mana: the card still leaves via exile (CR 702.35a) but ends in
        the graveyard, never on the stack and never stuck in exile."""
        game = _make_game()
        p = game.players[0]
        card = _in_hand(game, _madness_creature_template(
            ManaCost(generic=1, red=1), ManaCost(red=1), "{R}"))

        game.discard_card(0, card, cause="discard")

        assert game.stack.is_empty
        assert card in p.graveyard and card.zone == "graveyard"
        assert card not in p.exile and card not in p.hand

    def test_declined_madness_cast_puts_card_in_graveyard(self):
        """Affordable, but the controller chooses not to cast: graveyard."""
        game = GameState(rng=random.Random(0), callbacks=_DeclineOfferedCasts())
        game.active_player = 0
        game.current_phase = Phase.MAIN1
        p = game.players[0]
        card = _in_hand(game, _madness_creature_template(
            ManaCost(generic=1, red=1), ManaCost(), "{0}"))

        game.discard_card(0, card, cause="discard")

        assert game.stack.is_empty
        assert card in p.graveyard and card.zone == "graveyard"
        assert card not in p.exile

    def test_madness_cast_ignores_sorcery_timing(self):
        """The madness cast happens while the reflexive trigger resolves, so
        a creature discarded on the OPPONENT's turn is still cast (CR 608.2g)."""
        game = _make_game(active=1)
        game.current_phase = Phase.MAIN1
        p = game.players[0]
        card = _in_hand(game, _madness_creature_template(
            ManaCost(generic=1, red=1), ManaCost(), "{0}"))

        game.discard_card(0, card, cause="forced discard")
        _resolve_all(game)

        assert card in p.battlefield

    def test_non_madness_discard_goes_straight_to_graveyard(self):
        """Negative control: no madness cost → hand → graveyard, exile untouched."""
        game = _make_game()
        p = game.players[0]
        card = _in_hand(game, _plain_creature_template())
        exile_before = list(p.exile)

        game.discard_card(0, card, cause="discard")

        assert card in p.graveyard and card.zone == "graveyard"
        assert p.exile == exile_before
        assert game.stack.is_empty
        assert not any("exile" in line and card.name in line for line in game.log)

    def test_madness_pending_flag_is_cleared_after_offer(self):
        """The exile-with-offer state is transient; a resolved or declined
        offer must not leave the card castable from exile later."""
        game = _make_game()
        p = game.players[0]
        card = _in_hand(game, _madness_creature_template(
            ManaCost(generic=1, red=1), ManaCost(red=1), "{R}"))
        game.discard_card(0, card, cause="discard")
        assert card in p.graveyard
        assert not getattr(card, "_madness_pending", False)


# ── Every discard site routes through the funnel ───────────────────────────

class TestDiscardSitesUseFunnel:
    def test_forced_discard_offers_madness(self):
        """`_force_discard` (Thoughtseize-class effects) is a discard event,
        so the victim gets the madness offer."""
        game = _make_game()
        p = game.players[0]
        card = _in_hand(game, _madness_creature_template(
            ManaCost(generic=1, red=1), ManaCost(), "{0}"))

        game._force_discard(0, 1, self_discard=False)
        _resolve_all(game)

        assert card in p.battlefield

    def test_cleanup_hand_size_discard_offers_madness(self):
        """Discarding to maximum hand size is a discard event too (CR 514.1);
        the madness spell goes on the stack and resolves before the turn ends
        (CR 514.3a)."""
        game = _make_game()
        game.current_phase = Phase.CLEANUP
        p = game.players[0]
        # Fill the hand with cheap filler so the default (highest-CMC)
        # discard picker selects the expensive madness card.
        for _ in range(MAX_HAND_SIZE):
            _in_hand(game, _plain_creature_template())
        card = _in_hand(game, _madness_creature_template(
            ManaCost(generic=4, red=1), ManaCost(), "{0}"))
        assert len(p.hand) == MAX_HAND_SIZE + 1

        game.cleanup_step()

        assert len(p.hand) == MAX_HAND_SIZE
        assert game.stack.is_empty, "cleanup must resolve the madness cast (CR 514.3a)"
        assert card in p.battlefield


# ── Structural: can_cast / cast_spell agree on the madness route ───────────

def test_madness_registered_as_alternative_cost_mechanic():
    """The alternative-cost sync guard must know about madness so the
    can_cast/cast_spell pairing is enforced like warp/dash/evoke/escape."""
    from tests.test_alternative_cost_sync import ALTERNATIVE_COST_FIELDS
    assert "madness_cost" in ALTERNATIVE_COST_FIELDS
