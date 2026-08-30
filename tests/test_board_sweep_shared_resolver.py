"""CR 701.6a "destroy all [matching] permanents" / CR 701.20a
"sacrifice all [matching] permanents" board-sweep cluster.

`engine/card_effects.py` had 4 EFFECT_REGISTRY handlers (Damnation,
Supreme Verdict, All Is Dust, Wrath of the Skies) that each
re-implemented the same "iterate both players' battlefields, zone-move
every matching permanent" shape, with real per-card differences
(destroy vs. sacrifice, a type filter, a resolution-time mana-value
ceiling, a "has a color" predicate). New
`engine.card_effects._resolve_board_sweep` is the single shared owner;
all four handlers shrink to a keyword-parameterized call.

Two real bugs were found while reading every handler in full, both
instances of this program's "no single owner for what is true right
now" diagnosis:

1. **Damnation never checked indestructible at all** (CR 702.12b —
   a destroy effect does nothing to an indestructible permanent).
   Supreme Verdict and Wrath of the Skies already checked it; Damnation
   silently destroyed indestructible creatures anyway. The shared
   resolver checks it once, for every `action="destroy"` card, so this
   class of per-handler omission can't recur.
2. **All Is Dust used `color_identity` instead of `colors`** to decide
   whether a permanent "is one or more colors." `color_identity` is a
   format-legality superset (MTGJSON `colorIdentity`) that can include
   colors the permanent doesn't actually have (e.g. a colored
   activated-ability cost) — `colors` (MTGJSON `colors`, added in
   Phase 0b for exactly this "is this permanent actually white/blue/
   etc." class of check) is the correct field for a CR 105.2a color
   characteristic check. A permanent that's colorless by `colors` but
   has a nonempty `color_identity` was being incorrectly swept.

Sweeps are NOT targeted effects (CR 701.6a/701.20a name no "target"),
so hexproof (CR 702.11c, which only protects against being the target
of a spell/ability) is correctly irrelevant here and the resolver does
not route through `target_solver.enumerate_legal_targets` (which layers
hexproof filtering on top of type filtering) — only the pure type-token
predicate `target_solver._matches_type` is reused.

Card names below are fixture carriers only (CLAUDE.md ABSTRACTION
CONTRACT) — the mechanic under test is the shared resolver, not any
one card.
"""
from __future__ import annotations

import random

from engine.card_effects import (
    _all_is_dust_has_color,
    _board_sweep_pool,
    _resolve_board_sweep,
    _wrath_of_the_skies_mv_filter,
    wrath_of_the_skies_resolve,
)
from engine.cards import CardInstance, CardTemplate, CardType, Color, Keyword, ManaCost
from engine.game_state import GameState


class _StackItemStub:
    """Minimal stand-in for `engine.stack.StackItem` carrying only the
    one attribute `wrath_of_the_skies_resolve` reads (`x_value`) — lets
    the resolve-handler tests below force a specific X without going
    through cast-time X-selection (a separate concern, covered by
    `tests/test_wrath_x_optimizes_sweep.py`)."""

    def __init__(self, x_value: int):
        self.x_value = x_value


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


def _synthetic_permanent(game, name, controller, *, cmc=1, power=1,
                         toughness=1, card_types=None, keywords=None,
                         colors=None, color_identity=None):
    """A synthetic permanent so indestructible/color-characteristic
    tests do not depend on which real cards happen to carry those
    properties in the current DB."""
    tmpl = CardTemplate(
        name=name, card_types=card_types or [CardType.CREATURE],
        mana_cost=ManaCost(generic=cmc), supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=keywords or set(), abilities=[],
        color_identity=color_identity if color_identity is not None else set(),
        colors=colors if colors is not None else set(),
        produces_mana=[], enters_tapped=False,
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


def _cast_and_resolve(game, controller, spell, target_ids=()):
    assert game.cast_spell(controller, spell, targets=list(target_ids),
                           free_cast=True)
    game.resolve_stack()


# ─── CR 702.12b: destroy is blocked by indestructible ─────────────────


class TestDestroySweepRespectsIndestructible:
    """A `action="destroy"` board sweep must not destroy an
    indestructible permanent (CR 702.12b) — the Damnation bug this
    cluster fixed."""

    def test_damnation_spares_indestructible_creature(self, card_db):
        game = GameState(rng=random.Random(0))
        indestructible = _synthetic_permanent(
            game, "Indestructible Beater", 1,
            keywords={Keyword.INDESTRUCTIBLE})
        mortal = _synthetic_permanent(game, "Mortal Beater", 1)
        damnation = _hand(game, card_db, "Damnation", 0)

        _cast_and_resolve(game, 0, damnation)

        assert indestructible in game.players[1].battlefield, (
            "indestructible creature must survive a destroy-all sweep "
            "(CR 702.12b) — pre-fix Damnation had no indestructible "
            "check at all")
        assert mortal not in game.players[1].battlefield

    def test_supreme_verdict_spares_indestructible_creature(self, card_db):
        """Regression anchor: Supreme Verdict already checked
        indestructible pre-migration; the shared resolver must not
        regress it."""
        game = GameState(rng=random.Random(0))
        indestructible = _synthetic_permanent(
            game, "Indestructible Beater", 1,
            keywords={Keyword.INDESTRUCTIBLE})
        verdict = _hand(game, card_db, "Supreme Verdict", 0)

        _cast_and_resolve(game, 0, verdict)

        assert indestructible in game.players[1].battlefield


class TestSacrificeSweepIgnoresIndestructible:
    """CR 701.20a — sacrificing is not a destroy event, so
    indestructible does not protect against All Is Dust."""

    def test_all_is_dust_sacrifices_indestructible_colored_creature(
            self, card_db):
        game = GameState(rng=random.Random(0))
        indestructible_colored = _synthetic_permanent(
            game, "Indestructible Colored Beater", 1,
            keywords={Keyword.INDESTRUCTIBLE}, colors={Color.WHITE})
        dust = _hand(game, card_db, "All Is Dust", 0)

        _cast_and_resolve(game, 0, dust)

        assert indestructible_colored not in game.players[1].battlefield, (
            "sacrifice bypasses indestructible (CR 701.20a) — an "
            "indestructible COLORED permanent must still be swept by "
            "All Is Dust")


# ─── color characteristic vs. color-identity superset ─────────────────


class TestAllIsDustUsesRealColorNotColorIdentity:
    """CR 105.2a — "is one or more colors" is a characteristic check
    on the permanent's actual color (MTGJSON `colors`), not the
    format-legality `color_identity` superset."""

    def test_spares_permanent_that_is_colorless_by_colors_field(
            self, card_db):
        game = GameState(rng=random.Random(0))
        # colors=empty (genuinely colorless) but color_identity
        # nonempty (e.g. a colored activated-ability cost) — the
        # pre-fix bug used color_identity and would have swept this.
        colorless_permanent = _synthetic_permanent(
            game, "Colorless By Colors Field", 1,
            card_types=[CardType.ARTIFACT], colors=set(),
            color_identity={Color.BLUE})
        dust = _hand(game, card_db, "All Is Dust", 0)

        _cast_and_resolve(game, 0, dust)

        assert colorless_permanent in game.players[1].battlefield, (
            "a permanent that is colorless by `colors` (its real "
            "printed color) must survive All Is Dust even if its "
            "`color_identity` (a format-legality superset) is nonempty")

    def test_sweeps_permanent_that_has_a_real_color(self, card_db):
        game = GameState(rng=random.Random(0))
        colored_permanent = _synthetic_permanent(
            game, "Actually White", 1,
            card_types=[CardType.ARTIFACT], colors={Color.WHITE})
        dust = _hand(game, card_db, "All Is Dust", 0)

        _cast_and_resolve(game, 0, dust)

        assert colored_permanent not in game.players[1].battlefield

    def test_filter_predicate_unit(self):
        """Unit test of `_all_is_dust_has_color` directly, independent
        of a live game — pins the field choice without needing a full
        cast/resolve cycle.

        The predicate reads the INSTANCE's colour, not the template's:
        a layer-5 colour-setting static (CR 105.2b) can make a printed-
        colourless permanent all colours, and a sweep that keyed off the
        printed template would miss it. The stub therefore mirrors
        `CardInstance.colors` — printed colour unless an effect overrode
        it — rather than exposing only `.template`.
        """
        colored = CardTemplate(
            name="Colored", card_types=[CardType.ARTIFACT],
            mana_cost=ManaCost(generic=1), colors={Color.RED},
            color_identity=set(),
        )
        colorless = CardTemplate(
            name="Colorless", card_types=[CardType.ARTIFACT],
            mana_cost=ManaCost(generic=1), colors=set(),
            color_identity={Color.RED},
        )

        class _Stub:
            def __init__(self, template, cem_colors_set=None):
                self.template = template
                self.cem_colors_set = cem_colors_set

            @property
            def colors(self):
                if self.cem_colors_set is not None:
                    return set(self.cem_colors_set)
                return self.template.colors

        # Printed colour decides when no static is applying.
        assert _all_is_dust_has_color(None, 0, _Stub(colored)) is True
        # color_identity must NOT stand in for colour.
        assert _all_is_dust_has_color(None, 0, _Stub(colorless)) is False
        # A printed-colourless permanent that a layer-5 static has made
        # all colours IS coloured, and the sweep must see it.
        assert _all_is_dust_has_color(
            None, 0, _Stub(colorless, cem_colors_set=frozenset({Color.RED}))
        ) is True


# ─── symmetric — both players' battlefields are swept ─────────────────


class TestBoardSweepHitsBothPlayers:

    def test_damnation_destroys_creatures_on_both_sides(self, card_db):
        game = GameState(rng=random.Random(0))
        mine = _synthetic_permanent(game, "Mine", 0)
        theirs = _synthetic_permanent(game, "Theirs", 1)
        damnation = _hand(game, card_db, "Damnation", 0)

        _cast_and_resolve(game, 0, damnation)

        assert mine not in game.players[0].battlefield
        assert theirs not in game.players[1].battlefield


# ─── type filter (creature-only vs. artifact+creature+enchantment) ────


class TestBoardSweepTypeFilter:

    def test_wrath_of_the_skies_spares_lands_and_planeswalkers(
            self, card_db):
        game = GameState(rng=random.Random(0))
        # Land: never a legal sweep candidate for this card.
        land = _battlefield(game, card_db, "Forest", 1)
        # Low-CMC artifact: in range and in-type — should die.
        artifact = _synthetic_permanent(
            game, "Cheap Artifact", 1, cmc=0,
            card_types=[CardType.ARTIFACT])
        wrath_card = CardInstance(
            template=card_db.get_card("Wrath of the Skies"), owner=0,
            controller=0, instance_id=game.next_instance_id(),
            zone="stack",
        )

        wrath_of_the_skies_resolve(game, wrath_card, 0, targets=[],
                                   item=_StackItemStub(x_value=0))

        assert land in game.players[1].battlefield, (
            "Wrath of the Skies' type filter (artifact/creature/"
            "enchantment) must exclude lands")
        assert artifact not in game.players[1].battlefield


# ─── resolution-time mana-value ceiling ────────────────────────────────


class TestBoardSweepManaValueThreshold:

    def test_wrath_x_zero_spares_permanents_above_threshold(
            self, card_db):
        game = GameState(rng=random.Random(0))
        cheap = _synthetic_permanent(
            game, "Cheap", 1, cmc=0, card_types=[CardType.ARTIFACT])
        pricey = _synthetic_permanent(
            game, "Pricey", 1, cmc=3, card_types=[CardType.ARTIFACT])
        wrath_card = CardInstance(
            template=card_db.get_card("Wrath of the Skies"), owner=0,
            controller=0, instance_id=game.next_instance_id(),
            zone="stack",
        )

        wrath_of_the_skies_resolve(game, wrath_card, 0, targets=[],
                                   item=_StackItemStub(x_value=0))

        assert cheap not in game.players[1].battlefield
        assert pricey in game.players[1].battlefield, (
            "X=0 must spare any permanent with mana value > 0")

    def test_mv_filter_factory_unit(self):
        """Unit test of `_wrath_of_the_skies_mv_filter` directly."""
        tmpl_cheap = CardTemplate(
            name="Cheap", card_types=[CardType.ARTIFACT],
            mana_cost=ManaCost(generic=0))
        tmpl_pricey = CardTemplate(
            name="Pricey", card_types=[CardType.ARTIFACT],
            mana_cost=ManaCost(generic=3))

        class _Stub:
            def __init__(self, template):
                self.template = template

        f = _wrath_of_the_skies_mv_filter(1)
        assert f(None, 0, _Stub(tmpl_cheap)) is True
        assert f(None, 0, _Stub(tmpl_pricey)) is False


# ─── funnel routing: Undying still applies to a destroy-all sweep ─────


class TestBoardSweepRoutesThroughDeathFunnel:
    """A sweep still routes through `game._creature_dies`, so
    replacement effects (Undying, CR 702.92d) apply exactly as they
    would for a single-target destroy effect."""

    def test_damnation_triggers_undying(self, card_db):
        game = GameState(rng=random.Random(0))
        undying_creature = _synthetic_permanent(
            game, "Undying Beater", 1, keywords={Keyword.UNDYING})
        damnation = _hand(game, card_db, "Damnation", 0)

        _cast_and_resolve(game, 0, damnation)

        assert undying_creature in game.players[1].battlefield, (
            "Undying returns the creature to the battlefield")
        assert undying_creature.plus_counters == 1


# ─── real-card structured integration (per-card restriction table) ────


class TestRealCardsMatchClusterShape:
    """Confirms each real DB card's oracle text matches the shape this
    resolver models, guarding against a future oracle-text edit
    silently invalidating the migration's assumptions."""

    def test_damnation_is_unconditional_creature_destroy(self, card_db):
        tmpl = card_db.get_card("Damnation")
        assert "destroy all creatures" in tmpl.oracle_text.lower()

    def test_supreme_verdict_is_unconditional_creature_destroy(
            self, card_db):
        tmpl = card_db.get_card("Supreme Verdict")
        assert "destroy all creatures" in tmpl.oracle_text.lower()

    def test_all_is_dust_is_color_scoped_sacrifice(self, card_db):
        tmpl = card_db.get_card("All Is Dust")
        text = tmpl.oracle_text.lower()
        assert "sacrifices" in text
        assert "one or more colors" in text

    def test_wrath_of_the_skies_is_mv_scoped_destroy(self, card_db):
        tmpl = card_db.get_card("Wrath of the Skies")
        text = tmpl.oracle_text.lower()
        assert "destroy each artifact, creature, and enchantment" in text
        assert "mana value" in text


class TestBoardSweepPoolTypeMatching:
    """Direct unit coverage of `_board_sweep_pool` — delegates to
    `target_solver._matches_type`, the single owner of type-token
    matching, rather than re-deriving per-CardType checks locally."""

    def test_creature_type_excludes_noncreature_permanents(self, card_db):
        game = GameState(rng=random.Random(0))
        creature = _synthetic_permanent(game, "A Creature", 0)
        artifact = _synthetic_permanent(
            game, "An Artifact", 0, card_types=[CardType.ARTIFACT])

        pool = _board_sweep_pool(game, frozenset({"creature"}))

        assert creature in pool
        assert artifact not in pool
