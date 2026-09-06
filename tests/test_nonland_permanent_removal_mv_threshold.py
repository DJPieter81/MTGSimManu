"""CR 608.2b/608.2c — "destroy/exile target [nonland] permanent, only
if a resolution-time condition (usually a mana-value threshold) holds"
mechanic cluster.

`engine/card_effects.py` had 6 EFFECT_REGISTRY handlers (Abrupt Decay,
Assassin's Trophy, Fatal Push, Leyline Binding, Prismatic Ending,
March of Otherworldly Light) that were all instances of this one
mechanic, each with its own copy-pasted candidate-filtering,
target-legality, and zone-dispatch logic. Two real bugs were found in
that duplication, both fixed by the generic
`_resolve_nonland_permanent_removal` these tests pin:

1. Four of the six handlers (Abrupt Decay, Prismatic Ending, Leyline
   Binding, March of Otherworldly Light) ignored the `targets` a
   cast-time (or trigger-time) target selection had already produced
   and always re-derived their own pick — so an already-made choice
   was silently discarded and replaced with a DIFFERENT permanent.
2. Fatal Push's explicit-target branch, when the chosen target failed
   its mana-value condition, fell through to auto-picking a
   *different* creature instead of doing nothing (CR 608.2c: a
   resolution-time condition that isn't met means the spell/ability
   has no effect — it does not re-target).

Target legality (zone/type/owner) is delegated to
`engine.target_solver.enumerate_legal_targets` rather than re-derived
per handler — this is the same primitive `ai/response.py` and
`cast_manager.py` use elsewhere, so once that module grows
hexproof-aware filtering every card in this cluster inherits it for
free.

Card names below are fixture carriers only (CLAUDE.md ABSTRACTION
CONTRACT) — the mechanic under test is the shared resolver, not any
one card.
"""
from __future__ import annotations

import random

from engine.card_effects import (
    _fatal_push_mv_max,
    _march_otherworldly_light_mv_max,
    _prismatic_ending_mv_max,
    _resolve_nonland_permanent_removal,
)
from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.game_state import GameState


# ─── helpers ──────────────────────────────────────────────────────────


def _battlefield(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _hand(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _synthetic_creature(game, name, controller, cmc=1, power=1, toughness=1,
                        keywords=None):
    """A synthetic vanilla creature template so Undying/indestructible
    mechanic tests do not depend on which real cards happen to carry
    those keywords in the current DB."""
    tmpl = CardTemplate(
        name=name, card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=cmc), supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=keywords or set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _cast_and_resolve(game, controller, spell, target_ids):
    assert game.cast_spell(controller, spell, targets=list(target_ids),
                           free_cast=True)
    game.resolve_stack()


# ─── CR 608.2c: condition failure does not re-target ──────────────────


class TestConditionFailureDoesNotRetarget:
    """A resolution-time numeric condition (mana-value threshold) that
    the chosen target fails means the spell does nothing — it must NOT
    fall back to destroying a different, eligible permanent instead."""

    def test_destroy_removal_condition_failure_leaves_both_permanents_alive(
            self, card_db):
        game = GameState(rng=random.Random(0))
        # Fatal Push: MV<=2 (no revolt). Target a too-big creature
        # explicitly; a smaller, eligible creature also sits on the
        # board as a bystander a buggy "auto-pick a substitute" path
        # would have killed instead.
        too_big = _battlefield(game, card_db, "Murktide Regent", 1)  # cmc 6
        bystander = _synthetic_creature(game, "Bystander", 1, cmc=1)
        push = _hand(game, card_db, "Fatal Push", 0)

        _cast_and_resolve(game, 0, push, [too_big.instance_id])

        assert too_big in game.players[1].battlefield, (
            "over-threshold target must survive (condition unmet)")
        assert bystander in game.players[1].battlefield, (
            "a condition failure must NOT re-target a different "
            "eligible permanent — CR 608.2c says the spell does nothing")

    def test_destroy_removal_condition_met_destroys_the_chosen_target(
            self, card_db):
        game = GameState(rng=random.Random(0))
        small = _synthetic_creature(game, "Small Creature", 1, cmc=1)
        push = _hand(game, card_db, "Fatal Push", 0)

        _cast_and_resolve(game, 0, push, [small.instance_id])

        assert small not in game.players[1].battlefield
        assert small.zone == "graveyard"


# ─── Revolt threshold (Fatal Push's specific condition formula) ───────


class TestFatalPushRevoltThreshold:
    def test_mv_threshold_is_two_without_revolt(self, card_db):
        game = GameState(rng=random.Random(0))
        assert _fatal_push_mv_max(game, None, 0, None) == 2

    def test_mv_threshold_is_four_with_revolt(self, card_db):
        game = GameState(rng=random.Random(0))
        # CR 702.139 revolt reads the broad "a permanent left the battlefield
        # this turn" tally, not the narrower creature-death signal.
        game.players[0].permanents_left_battlefield_this_turn = 1
        assert _fatal_push_mv_max(game, None, 0, None) == 4


# ─── Honoring a pre-chosen target instead of re-deriving one ──────────


class TestHonorsPreChosenTarget:
    """Four of the six handlers previously ignored `targets` entirely
    and always re-derived their own "best" pick — discarding whatever
    choice had already been made at cast time. The shared resolver
    must use the supplied target when present."""

    def test_nonland_removal_honors_explicit_target_over_auto_pick(
            self, card_db):
        game = GameState(rng=random.Random(0))
        # Abrupt Decay (MV<=3): two legal candidates. The auto-pick
        # heuristic (_nonland_permanent_threat) favors higher CMC+power,
        # so make `high_threat` the auto-pick winner and `low_threat`
        # the explicitly-chosen target — asserting `low_threat` is the
        # one destroyed proves the explicit choice wins.
        high_threat = _synthetic_creature(game, "High Threat", 1, cmc=3,
                                          power=3, toughness=3)
        low_threat = _synthetic_creature(game, "Low Threat", 1, cmc=1,
                                         power=0, toughness=1)
        decay = _hand(game, card_db, "Abrupt Decay", 0)

        _cast_and_resolve(game, 0, decay, [low_threat.instance_id])

        assert low_threat.zone == "graveyard"
        assert high_threat in game.players[1].battlefield, (
            "the un-targeted higher-threat permanent must survive — "
            "the resolver must not silently re-target it")

    def test_nonland_removal_auto_picks_highest_threat_when_no_target_chosen(
            self, card_db):
        """When no target was pre-chosen (e.g. a dies-trigger fan-out
        or a legacy call site), the resolver falls back to the shared
        `_nonland_permanent_threat` heuristic — same behavior the
        cluster always had for its auto-pick path."""
        game = GameState(rng=random.Random(0))
        high_threat = _synthetic_creature(game, "High Threat", 1, cmc=3,
                                          power=3, toughness=3)
        low_threat = _synthetic_creature(game, "Low Threat", 1, cmc=1,
                                         power=0, toughness=1)
        decay = _battlefield(game, card_db, "Abrupt Decay", 0)

        _resolve_nonland_permanent_removal(
            game, decay, 0, targets=None, item=None,
            zone_dest="graveyard", mv_max_fn=lambda g, c, ctl, it: 3,
        )

        assert high_threat.zone == "graveyard"
        assert low_threat in game.players[1].battlefield


# ─── Destroy vs exile: indestructible + death-replacement funnel ──────


class TestDestroyVsExileZoneDispatch:
    """The destroy/exile distinction is not cosmetic: CR 702.12b
    (indestructible) and Undying/Persist death replacement apply only
    to the DESTROY path (graveyard-bound), never to exile."""

    def test_destroy_based_removal_does_nothing_to_indestructible_permanent(
            self, card_db):
        game = GameState(rng=random.Random(0))
        rock = _synthetic_creature(
            game, "Indestructible Rock", 1, cmc=2,
            keywords={Keyword.INDESTRUCTIBLE})
        decay = _hand(game, card_db, "Abrupt Decay", 0)

        _cast_and_resolve(game, 0, decay, [rock.instance_id])

        assert rock in game.players[1].battlefield
        assert rock.zone == "battlefield"

    def test_exile_based_removal_is_not_blocked_by_indestructible(
            self, card_db):
        game = GameState(rng=random.Random(0))
        rock = _synthetic_creature(
            game, "Indestructible Rock", 1, cmc=1,
            keywords={Keyword.INDESTRUCTIBLE})
        binding = _battlefield(game, card_db, "Leyline Binding", 0)

        _resolve_nonland_permanent_removal(
            game, binding, 0, targets=[rock.instance_id], item=None,
            zone_dest="exile", log_verb="exiles",
        )

        assert rock.zone == "exile"
        assert rock not in game.players[1].battlefield

    def test_destroy_removal_routes_creature_through_death_replacement_funnel(
            self, card_db):
        """A creature destroyed by this cluster still gets its
        Undying return — proof the resolver calls
        `game._permanent_destroyed` (which dispatches to
        `game._creature_dies`), not a bespoke zone mutation."""
        game = GameState(rng=random.Random(0))
        zombie = _synthetic_creature(
            game, "Undying Zombie", 1, cmc=2,
            keywords={Keyword.UNDYING})
        decay = _hand(game, card_db, "Abrupt Decay", 0)

        _cast_and_resolve(game, 0, decay, [zombie.instance_id])

        assert zombie in game.players[1].battlefield, (
            "Undying must return the creature to the battlefield")
        assert zombie.plus_counters == 1

    def test_exile_removal_does_not_trigger_undying_replacement(
            self, card_db):
        """CR 700.4/CR 702.92d: Undying only replaces dying (going to
        the graveyard from the battlefield) — exile bypasses it."""
        game = GameState(rng=random.Random(0))
        zombie = _synthetic_creature(
            game, "Undying Zombie", 1, cmc=1,
            keywords={Keyword.UNDYING})
        binding = _battlefield(game, card_db, "Leyline Binding", 0)

        _resolve_nonland_permanent_removal(
            game, binding, 0, targets=[zombie.instance_id], item=None,
            zone_dest="exile", log_verb="exiles",
        )

        assert zombie.zone == "exile"
        assert zombie not in game.players[1].battlefield
        assert zombie.plus_counters == 0


# ─── Per-card restriction shape: no shared behavior gets flattened ────


class TestPerCardRestrictionShapeIsPreserved:
    """The cluster shares a resolver, not a restriction. Each card's
    real oracle-text differences must survive the migration."""

    def test_assassins_trophy_has_no_mana_value_condition(self, card_db):
        game = GameState(rng=random.Random(0))
        big = _synthetic_creature(game, "Huge Threat", 1, cmc=10)
        trophy = _hand(game, card_db, "Assassin's Trophy", 0)

        _cast_and_resolve(game, 0, trophy, [big.instance_id])

        assert big.zone == "graveyard"

    def test_assassins_trophy_can_target_a_land(self, card_db):
        """Assassin's Trophy's real oracle text ('Destroy target
        permanent') has no nonland restriction, unlike every other
        card in this cluster — must stay targetable at a land."""
        game = GameState(rng=random.Random(0))
        land = _battlefield(game, card_db, "Island", 1)
        trophy = _hand(game, card_db, "Assassin's Trophy", 0)

        _cast_and_resolve(game, 0, trophy, [land.instance_id])

        assert land.zone == "graveyard"

    def test_leyline_binding_has_no_mana_value_condition(self, card_db):
        """Leyline Binding's domain-scaled COST reduction is not a
        targeting restriction — verified against the real oracle text
        (no mana-value clause on the exile target at all)."""
        game = GameState(rng=random.Random(0))
        big = _synthetic_creature(game, "Huge Threat", 1, cmc=10)
        binding = _battlefield(game, card_db, "Leyline Binding", 0)

        _resolve_nonland_permanent_removal(
            game, binding, 0, targets=[big.instance_id], item=None,
            zone_dest="exile", log_verb="exiles",
        )

        assert big.zone == "exile"

    def test_prismatic_ending_mv_threshold_equals_colors_of_mana_spent(self):
        class _Item:
            colors_spent = {"W", "U"}

        game = GameState(rng=random.Random(0))
        assert _prismatic_ending_mv_max(game, None, 0, _Item()) == 2

    def test_prismatic_ending_mv_threshold_floors_at_one_colorless(self):
        class _Item:
            colors_spent = set()

        game = GameState(rng=random.Random(0))
        game.players[0].battlefield = []  # no lands ⇒ no fallback colors
        assert _prismatic_ending_mv_max(game, None, 0, _Item()) == 1

    def test_march_otherworldly_light_mv_threshold_equals_x_paid(self):
        class _Item:
            x_value = 3

        game = GameState(rng=random.Random(0))
        assert _march_otherworldly_light_mv_max(game, None, 0, _Item()) == 3

    def test_march_otherworldly_light_type_filter_excludes_lands(
            self, card_db):
        """March's type filter is artifact/creature/enchantment — a
        land at or under the paid X must not be a legal candidate."""
        game = GameState(rng=random.Random(0))
        land = _battlefield(game, card_db, "Island", 1)
        march = _hand(game, card_db, "March of Otherworldly Light", 0)

        class _Item:
            x_value = 10

        _resolve_nonland_permanent_removal(
            game, march, 0, targets=[land.instance_id], item=_Item(),
            zone_dest="exile", types=frozenset({"artifact", "creature", "enchantment"}),
            mv_max_fn=lambda g, c, ctl, it: it.x_value if it else 0,
            log_verb="exiles",
        )

        assert land.zone == "battlefield", (
            "a land is not a legal target for March's type filter, "
            "even at an arbitrarily high paid X")
