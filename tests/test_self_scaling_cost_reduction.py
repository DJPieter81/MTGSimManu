"""Self-scaling own-cost reduction — "This spell costs {N} less to cast
for each <dynamic quantity>".

Rule under test
---------------
A spell may carry a cost reduction that scales with a live quantity
the caster controls (CR 601.2f: cost reductions are applied after the
total cost is determined, and can only ever reduce the GENERIC portion
— coloured pips are never discounted).  Two sub-shapes dominate the
Modern pool:

  (a) per card the caster has cycled or discarded this turn
      (fixture carrier: Hollow One — {4}, "{2} less for each");
  (b) per distinct card type among cards in the caster's graveyard
      (fixture carrier: Emrakul, the Promised End — {13}, "{1} less
      for each").

Contract
--------
* `engine.oracle_parser.parse_self_cost_reduction(oracle)` returns a
  typed `(amount, unit)` pair; static reducers ("spells you cast cost
  {1} less") and the domain shape ("for each basic land type" — owned
  by `domain_reduction`) must NOT match.  Unmodelled units are refused
  outright as `(0, '')`, never half-applied.
* `CardTemplate.self_cost_reduction_amount` / `_unit` are populated at
  DB load, so no runtime code inspects oracle text.
* `PlayerState.cards_discarded_or_cycled_this_turn` is incremented at
  the zone funnel for every hand -> graveyard move (discard, forced
  discard, cycling, discard-to-hand-size) and reset each turn.
* The live reduction (amount x live count, floored at the generic
  portion) is applied by every cost consumer: `can_cast`, mana
  payment, and the AI's `effective_cmc` primitive.

Card names appear only as fixture carriers — never in test names or
assertions.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.mana import ManaCost


# ─── Fixture helpers ─────────────────────────────────────────────────


def _make_card(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card from DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    return card


def _put_in_play(game, card_db, name, controller):
    card = _make_card(game, card_db, name, controller, "battlefield")
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _put_in_hand(game, card_db, name, controller):
    card = _make_card(game, card_db, name, controller, "hand")
    game.players[controller].hand.append(card)
    return card


def _put_in_graveyard(game, card_db, name, controller):
    card = _make_card(game, card_db, name, controller, "graveyard")
    game.players[controller].graveyard.append(card)
    return card


def _main_phase(game, player_idx=0):
    from engine.game_state import Phase
    game.current_phase = Phase.MAIN1
    game.active_player = player_idx


# ─── Parser ──────────────────────────────────────────────────────────


class TestParseSelfCostReduction:

    def test_per_card_discarded_or_cycled_this_turn_parses_amount_and_unit(self):
        """Shape (a): the per-turn discard/cycle counter is the unit.
        Reminder text (the cycling parenthetical also says "Discard
        this card") must be stripped first."""
        from engine.oracle_parser import parse_self_cost_reduction
        oracle = ("This spell costs {2} less to cast for each card you've "
                  "cycled or discarded this turn.\n"
                  "Cycling {2} ({2}, Discard this card: Draw a card.)")
        assert parse_self_cost_reduction(oracle) == (
            2, "discarded_or_cycled_this_turn")

    def test_discarded_or_cycled_word_order_is_symmetric(self):
        from engine.oracle_parser import parse_self_cost_reduction
        oracle = ("This spell costs {1} less to cast for each card you've "
                  "discarded or cycled this turn.")
        assert parse_self_cost_reduction(oracle) == (
            1, "discarded_or_cycled_this_turn")

    def test_per_card_type_in_graveyard_parses_amount_and_unit(self):
        """Shape (b): distinct card types in the caster's graveyard."""
        from engine.oracle_parser import parse_self_cost_reduction
        oracle = ("This spell costs {1} less to cast for each card type "
                  "among cards in your graveyard.\n"
                  "Flying, trample, protection from instants")
        assert parse_self_cost_reduction(oracle) == (1, "graveyard_card_types")

    def test_static_reducer_for_other_spells_does_not_match(self):
        """"Spells you cast cost {1} less" is a static on-board reducer
        owned by `parse_cost_reduction`; it must not be read as a
        self-scaling reduction."""
        from engine.oracle_parser import parse_self_cost_reduction
        assert parse_self_cost_reduction(
            "Instant and sorcery spells you cast cost {1} less to cast."
        ) == (0, "")
        assert parse_self_cost_reduction(
            "Red spells you cast cost {1} less to cast."
        ) == (0, "")

    def test_domain_shape_is_not_claimed(self):
        """"For each basic land type" is the domain reduction, already
        typed as `domain_reduction`; claiming it here would double-count."""
        from engine.oracle_parser import parse_self_cost_reduction
        assert parse_self_cost_reduction(
            "This spell costs {1} less to cast for each basic land type "
            "among lands you control."
        ) == (0, "")

    def test_unmodelled_unit_is_refused_outright(self):
        """A unit the engine cannot count live is refused as (0, ''),
        never half-applied with a guessed count."""
        from engine.oracle_parser import parse_self_cost_reduction
        assert parse_self_cost_reduction(
            "This spell costs {1} less to cast for each creature in your party."
        ) == (0, "")

    def test_absent_oracle_is_empty(self):
        from engine.oracle_parser import parse_self_cost_reduction
        assert parse_self_cost_reduction("") == (0, "")
        assert parse_self_cost_reduction(None) == (0, "")


# ─── Template population at DB load ──────────────────────────────────


class TestTemplateFieldsPopulatedAtLoad:

    def test_discard_cycle_shape_populates_typed_fields(self, card_db):
        tmpl = card_db.get_card("Hollow One")
        assert tmpl.self_cost_reduction_amount == 2
        assert tmpl.self_cost_reduction_unit == "discarded_or_cycled_this_turn"

    def test_graveyard_card_type_shape_populates_typed_fields(self, card_db):
        tmpl = card_db.get_card("Emrakul, the Promised End")
        assert tmpl.self_cost_reduction_amount == 1
        assert tmpl.self_cost_reduction_unit == "graveyard_card_types"

    def test_static_reducer_has_no_self_reduction(self, card_db):
        tmpl = card_db.get_card("Ruby Medallion")
        assert tmpl.self_cost_reduction_amount == 0
        assert tmpl.self_cost_reduction_unit == ""


# ─── Per-turn discard/cycle counter ──────────────────────────────────


class TestDiscardedOrCycledThisTurnCounter:

    def test_forced_discard_increments_owner_counter(self, card_db):
        game = GameState(rng=random.Random(0))
        _put_in_hand(game, card_db, "Lightning Bolt", 0)
        _put_in_hand(game, card_db, "Lightning Bolt", 0)
        assert game.players[0].cards_discarded_or_cycled_this_turn == 0
        game._force_discard(0, 2, self_discard=False)
        assert game.players[0].cards_discarded_or_cycled_this_turn == 2

    def test_cycling_increments_counter(self, card_db):
        game = GameState(rng=random.Random(0))
        _main_phase(game)
        for _ in range(3):
            _put_in_play(game, card_db, "Mountain", 0)
        game.players[0].library.append(
            _make_card(game, card_db, "Mountain", 0, "library"))
        cycler = _put_in_hand(game, card_db, "Hollow One", 0)
        assert game.activate_cycling(0, cycler)
        assert game.players[0].cards_discarded_or_cycled_this_turn == 1

    def test_counter_resets_with_turn_tracking(self, card_db):
        game = GameState(rng=random.Random(0))
        _put_in_hand(game, card_db, "Lightning Bolt", 0)
        game._force_discard(0, 1, self_discard=True)
        assert game.players[0].cards_discarded_or_cycled_this_turn == 1
        game.players[0].reset_turn_tracking()
        assert game.players[0].cards_discarded_or_cycled_this_turn == 0

    def test_zone_funnel_credits_the_card_owner_not_the_actor(self, card_db):
        """"Cards YOU'VE discarded" — an opponent-forced discard counts
        for the discarding player, whose graveyard receives the card."""
        game = GameState(rng=random.Random(0))
        _put_in_hand(game, card_db, "Lightning Bolt", 1)
        game._force_discard(1, 1, self_discard=False)
        assert game.players[1].cards_discarded_or_cycled_this_turn == 1
        assert game.players[0].cards_discarded_or_cycled_this_turn == 0


# ─── Live reduction applied at cast time ─────────────────────────────


class TestReductionAppliedAtCastTime:

    def test_two_discards_reduce_a_two_less_per_spell_by_four(self, card_db):
        """{5} generic, {2} less per discard: two discards -> {1} (one
        land suffices), three discards -> {0} (castable with no mana)."""
        game = GameState(rng=random.Random(0))
        _main_phase(game)
        spell = _put_in_hand(game, card_db, "Hollow One", 0)
        assert spell.template.mana_cost.cmc == 5
        assert not game.can_cast(0, spell)
        game.players[0].cards_discarded_or_cycled_this_turn = 2
        assert not game.can_cast(0, spell)          # {1} still owed
        _put_in_play(game, card_db, "Mountain", 0)
        assert game.can_cast(0, spell)              # 1 land pays {1}
        game.players[0].cards_discarded_or_cycled_this_turn = 3
        game.players[0].battlefield.clear()
        assert game.can_cast(0, spell)              # {0} — free

    def test_payment_taps_only_the_reduced_generic(self, card_db):
        """Mana payment must charge the reduced cost, not the printed one:
        with two discards a {5} spell reduced by {2} each taps one land."""
        game = GameState(rng=random.Random(0))
        _main_phase(game)
        lands = [_put_in_play(game, card_db, "Mountain", 0) for _ in range(5)]
        spell = _put_in_hand(game, card_db, "Hollow One", 0)
        game.players[0].cards_discarded_or_cycled_this_turn = 2
        assert game.tap_lands_for_mana(0, spell.template.mana_cost,
                                       card_name=spell.template.name)
        assert sum(1 for l in lands if l.tapped) == 1

    def test_three_graveyard_card_types_reduce_a_one_less_per_spell_by_three(
            self, card_db):
        """{13} generic, {1} less per distinct card type in graveyard:
        instant + creature + land = 3 types -> {10}.  Two copies of the
        same type do not count twice."""
        game = GameState(rng=random.Random(0))
        _main_phase(game)
        spell = _put_in_hand(game, card_db, "Emrakul, the Promised End", 0)
        _put_in_graveyard(game, card_db, "Lightning Bolt", 0)
        _put_in_graveyard(game, card_db, "Lightning Bolt", 0)   # duplicate type
        _put_in_graveyard(game, card_db, "Memnite", 0)          # artifact creature
        for _ in range(9):
            _put_in_play(game, card_db, "Mountain", 0)
        # instant + artifact + creature = 3 types -> {10}; 9 lands short.
        assert not game.can_cast(0, spell)
        _put_in_play(game, card_db, "Mountain", 0)
        assert game.can_cast(0, spell)

    def test_reduction_never_eats_coloured_pips(self, card_db):
        """Cost reductions apply to the generic portion only (CR 601.2f);
        a huge live count cannot drive a spell below its coloured pips."""
        from engine.oracle_resolver import self_cost_reduction
        from engine.cards import CardTemplate, CardType
        game = GameState(rng=random.Random(0))
        tmpl = CardTemplate(
            name="Synthetic Discard Payoff", mana_cost=ManaCost(generic=2, red=1),
            card_types=[CardType.CREATURE], oracle_text=(
                "This spell costs {2} less to cast for each card you've "
                "cycled or discarded this turn."),
        )
        game.players[0].cards_discarded_or_cycled_this_turn = 5
        assert self_cost_reduction(game, 0, tmpl) == 2   # capped at generic


# ─── AI effective-CMC primitive ──────────────────────────────────────


class TestEffectiveCmcPrimitive:

    def test_effective_cmc_reflects_live_discard_count(self, card_db):
        from ai.effective_cmc import effective_cmc
        game = GameState(rng=random.Random(0))
        spell = _put_in_hand(game, card_db, "Hollow One", 0)
        assert effective_cmc(spell, None, game=game, player_idx=0) == 5
        game.players[0].cards_discarded_or_cycled_this_turn = 1
        assert effective_cmc(spell, None, game=game, player_idx=0) == 3
        game.players[0].cards_discarded_or_cycled_this_turn = 2
        assert effective_cmc(spell, None, game=game, player_idx=0) == 1
        game.players[0].cards_discarded_or_cycled_this_turn = 3
        assert effective_cmc(spell, None, game=game, player_idx=0) == 0

    def test_effective_cmc_reflects_graveyard_card_types(self, card_db):
        from ai.effective_cmc import effective_cmc
        game = GameState(rng=random.Random(0))
        spell = _put_in_hand(game, card_db, "Emrakul, the Promised End", 0)
        assert effective_cmc(spell, None, game=game, player_idx=0) == 13
        _put_in_graveyard(game, card_db, "Lightning Bolt", 0)
        _put_in_graveyard(game, card_db, "Memnite", 0)
        _put_in_graveyard(game, card_db, "Mountain", 0)
        # instant, artifact, creature, land = 4 types
        assert effective_cmc(spell, None, game=game, player_idx=0) == 9
