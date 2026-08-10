"""CDA coverage extension — generalized graveyard-census P/T scaling.

Rules-foundation sweep tracker, Phase 3, "CDA coverage extension"
item. The task named three candidate CDA shapes (Death's Shadow-class
negative life scaling, Mortivore/Bonehoard-class "creature cards in
all graveyards", Multani-class land+graveyard compound). Real-DB
research (documented in the tracker doc) found:

- Death's Shadow-class and Multani-class shapes are real but too
  narrow (2-6 and 1-3 cards respectively, depending on how generously
  the formula family is drawn) to clear CLAUDE.md's "fewer than 10 ⇒
  you are patching" bar — excluded, see `TestExcludedShapesUncaught`
  below for the regression guard pinning that exclusion.
- The literal Mortivore/Bonehoard "creature cards in all graveyards"
  shape is ALSO narrow on its own (6 cards) — but it is one member of
  a much larger family the existing `detect_power_scaling`'s
  "graveyard" bucket had already partially covered (instant/sorcery,
  controller's-graveyard-only, hardcoded). Generalizing that bucket's
  TYPE and SCOPE into parameters — mirroring how Phase 1d generalized
  `_get_artifact_count` into `_get_permanent_type_count` — surfaces
  26 real DB cards sharing the general "power[/toughness] derived
  from a graveyard card census" mechanic. This comfortably clears the
  class-size bar and is the actual mechanic underlying the named
  Mortivore/Bonehoard shape.

Generalizing also fixed two real, previously-shipped bugs in the
narrow "graveyard" bucket (both discovered, not assumed, via full-DB
oracle-text inspection):

1. Enigma Drake/Haughty Djinn/Kinetic Augur/Spellheart Chimera are
   POWER-ONLY CDAs ("power is equal to the number of instant and
   sorcery cards in your graveyard" — no toughness clause at all),
   but the old bucket applied the SAME count to both power AND
   toughness unconditionally, inflating their printed toughness.
2. Magnivore's real oracle is "power and toughness are each equal to
   the number of SORCERY cards in ALL graveyards" — the old bucket's
   single hardcoded `_get_gy_instants_sorceries()` resolver counted
   instant-OR-sorcery in the CONTROLLER'S graveyard only, silently
   wrong on both axes (type and scope) for this one real member.

Card names appear only as fixture carriers (synthetic CardTemplates
and real DB lookups) per CLAUDE.md's ABSTRACTION CONTRACT — the
mechanic under test is the graveyard-census CDA family, not any
specific card.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost, Supertype
from engine.game_state import GameState
from engine.oracle_parser import detect_power_scaling


# ─── oracle-parser unit tests ──────────────────────────────────────


class TestDetectGraveyardCountScaling:
    def test_symmetric_creature_all_graveyards(self):
        assert detect_power_scaling(
            "This creature's power and toughness are each equal to "
            "the number of creature cards in all graveyards."
        ) == "graveyard_count:sym:creature:all"

    def test_symmetric_any_card_your_graveyard(self):
        assert detect_power_scaling(
            "This creature's power and toughness are each equal to "
            "the number of cards in your graveyard."
        ) == "graveyard_count:sym:any:your"

    def test_symmetric_any_card_opponents_graveyards(self):
        assert detect_power_scaling(
            "This creature's power and toughness are each equal to "
            "the number of cards in your opponents' graveyards."
        ) == "graveyard_count:sym:any:opponents"

    def test_power_only_instant_and_sorcery_your_graveyard(self):
        assert detect_power_scaling(
            "Flying\nThis creature's power is equal to the number of "
            "instant and sorcery cards in your graveyard."
        ) == "graveyard_count:power_only:instant_and_sorcery:your"

    def test_goyf_asymmetric_toughness_plus_one(self):
        assert detect_power_scaling(
            "This creature's power is equal to the number of creature "
            "cards in all graveyards and its toughness is equal to "
            "that number plus 1."
        ) == "graveyard_count:goyf:creature:all"

    def test_artifact_type_all_graveyards(self):
        assert detect_power_scaling(
            "This creature's power and toughness are each equal to "
            "the number of artifact cards in all graveyards."
        ) == "graveyard_count:sym:artifact:all"

    def test_no_scaling_returns_empty_string(self):
        assert detect_power_scaling("Deal 3 damage to any target.") == ""


class TestGraveyardCountFallbackPreservesLegacyBehavior:
    """A graveyard-census CDA that doesn't fit the structured
    TYPE-cards-in-SCOPE-graveyard(s) shape (a compound zone like
    "you own in exile and in your graveyard") must still fall back to
    the legacy bare "graveyard" bucket — zero behavior change for
    cards this generalization can't cleanly parameterize."""

    def test_compound_exile_and_graveyard_falls_back_to_legacy_bucket(self):
        oracle = (
            "Flying\nThis creature's power is equal to the total "
            "number of instant and sorcery cards you own in exile "
            "and in your graveyard."
        )
        assert detect_power_scaling(oracle) == "graveyard"

    def test_real_db_crackling_drake_shaped_card_still_legacy_bucket(self, card_db):
        card = card_db.get_card("Crackling Drake")
        if card is None:
            pytest.skip("Crackling Drake not in DB")
        assert card.power_scales_with == "graveyard", (
            "Crackling Drake's exile+graveyard compound zone must "
            "keep using the legacy bucket unchanged — it does not "
            "fit the structured TYPE-cards-in-SCOPE-graveyard(s) "
            "shape the new bucket parameterizes."
        )


class TestExcludedShapesUncaught:
    """Death's Shadow-class negative life scaling and Multani-class
    land+graveyard compound scaling were investigated (see tracker
    doc) and found too narrow (well under CLAUDE.md's 10-card bar
    even under the most generous combination of their respective
    formula families) to warrant a dedicated bucket. Regression guard:
    neither shape should get swept up into an unrelated bucket by
    coincidence as the parser evolves."""

    def test_negative_life_scaling_not_detected(self, card_db):
        card = card_db.get_card("Death's Shadow")
        if card is None:
            pytest.skip("Death's Shadow not in DB")
        assert card.power_scales_with == ""

    def test_land_plus_graveyard_compound_not_detected(self, card_db):
        card = card_db.get_card("Multani, Yavimaya's Avatar")
        if card is None:
            pytest.skip("Multani, Yavimaya's Avatar not in DB")
        assert card.power_scales_with == ""


# ─── real-DB structured-field integration ──────────────────────────


class TestRealCardsGetGraveyardCountBucket:
    def test_mortivore(self, card_db):
        t = card_db.get_card("Mortivore")
        if t is None:
            pytest.skip("Mortivore not in DB")
        assert t.power_scales_with == "graveyard_count:sym:creature:all"

    def test_necrogoyf_power_only(self, card_db):
        t = card_db.get_card("Necrogoyf")
        if t is None:
            pytest.skip("Necrogoyf not in DB")
        assert t.power_scales_with == "graveyard_count:power_only:creature:all"

    def test_lhurgoyf_goyf_shape(self, card_db):
        t = card_db.get_card("Lhurgoyf")
        if t is None:
            pytest.skip("Lhurgoyf not in DB")
        assert t.power_scales_with == "graveyard_count:goyf:creature:all"

    def test_consuming_aberration_opponents_scope(self, card_db):
        t = card_db.get_card("Consuming Aberration")
        if t is None:
            pytest.skip("Consuming Aberration not in DB")
        assert t.power_scales_with == "graveyard_count:sym:any:opponents"

    def test_magnivore_sorcery_only_all_graveyards(self, card_db):
        """The bug the generalization fixed: Magnivore's real oracle
        is sorcery-only, all-graveyards — the old bucket's hardcoded
        instant-or-sorcery/your-graveyard-only resolver silently
        mismodeled both axes for this card."""
        t = card_db.get_card("Magnivore")
        if t is None:
            pytest.skip("Magnivore not in DB")
        assert t.power_scales_with == "graveyard_count:sym:sorcery:all"

    def test_enigma_drake_is_power_only_not_symmetric(self, card_db):
        """The other bug the generalization fixed: Enigma Drake has
        no toughness clause at all — the old bucket applied the
        graveyard count to toughness too, inflating it."""
        t = card_db.get_card("Enigma Drake")
        if t is None:
            pytest.skip("Enigma Drake not in DB")
        assert t.power_scales_with == "graveyard_count:power_only:instant_and_sorcery:your"

    def test_class_size_clears_claude_md_bar(self, card_db):
        """CLAUDE.md: "how many of Modern's 20k+ cards could
        legitimately hit this code path? If fewer than 10, you are
        patching." The generalized graveyard_count family must
        comfortably clear that bar DB-wide (not just for the
        hand-picked fixtures above)."""
        tagged = [
            name for name, t in card_db.cards.items()
            if t.power_scales_with.startswith("graveyard_count:")
        ]
        assert len(tagged) >= 15, (
            f"expected the generalized graveyard_count family to "
            f"comfortably clear the 10-card class-size bar; got "
            f"{len(tagged)}: {tagged[:10]}"
        )


# ─── live P/T computation ───────────────────────────────────────────


def _gy_count_creature(name, bucket, power=0, toughness=0):
    return CardTemplate(
        name=name, card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False,
        oracle_text="(synthetic graveyard-count CDA fixture)",
        tags=set(),
        power_scales_with=bucket,
    )


def _graveyard_card(game, name, owner, card_types, supertypes=None):
    tmpl = CardTemplate(
        name=name, card_types=card_types,
        mana_cost=ManaCost(generic=0), supertypes=supertypes or [],
        subtypes=[], power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=owner, controller=owner,
        instance_id=game.next_instance_id(), zone="graveyard",
    )
    card._game_state = game
    game.players[owner].graveyard.append(card)
    return card


def _put_on_battlefield(game, tmpl, controller):
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


class TestLiveGraveyardCountPT:
    def test_symmetric_formula_both_stats_equal_count(self):
        game = GameState(rng=random.Random(0))
        tmpl = _gy_count_creature(
            "Mortivore-shaped", "graveyard_count:sym:creature:all")
        creature = _put_on_battlefield(game, tmpl, 0)
        _graveyard_card(game, "SomeCreature", 0, [CardType.CREATURE])
        _graveyard_card(game, "SomeInstant", 0, [CardType.INSTANT])
        _graveyard_card(game, "OppCreature", 1, [CardType.CREATURE])

        # 2 creature cards across both graveyards; the instant doesn't count.
        assert creature.power == 2
        assert creature.toughness == 2

    def test_goyf_formula_toughness_is_count_plus_one(self):
        game = GameState(rng=random.Random(0))
        tmpl = _gy_count_creature(
            "Lhurgoyf-shaped", "graveyard_count:goyf:creature:all")
        creature = _put_on_battlefield(game, tmpl, 0)
        _graveyard_card(game, "C1", 0, [CardType.CREATURE])
        _graveyard_card(game, "C2", 1, [CardType.CREATURE])

        assert creature.power == 2
        assert creature.toughness == 3

    def test_power_only_formula_toughness_stays_printed(self):
        """Enigma Drake-class: only power tracks the graveyard count;
        toughness must stay at its own fixed printed value, NOT get
        the count added on top (the bug this generalization fixed)."""
        game = GameState(rng=random.Random(0))
        tmpl = _gy_count_creature(
            "Enigma-Drake-shaped",
            "graveyard_count:power_only:instant_and_sorcery:your",
            power=0, toughness=4,
        )
        creature = _put_on_battlefield(game, tmpl, 0)
        _graveyard_card(game, "I1", 0, [CardType.INSTANT])
        _graveyard_card(game, "S1", 0, [CardType.SORCERY])
        _graveyard_card(game, "C1", 0, [CardType.CREATURE])  # doesn't count

        assert creature.power == 2
        assert creature.toughness == 4  # unchanged printed value, not 4+2

    def test_scope_your_excludes_opponent_graveyard(self):
        game = GameState(rng=random.Random(0))
        tmpl = _gy_count_creature(
            "Splinterfright-shaped", "graveyard_count:sym:creature:your")
        creature = _put_on_battlefield(game, tmpl, 0)
        _graveyard_card(game, "Mine", 0, [CardType.CREATURE])
        _graveyard_card(game, "Theirs", 1, [CardType.CREATURE])

        assert creature.power == 1

    def test_scope_opponents_excludes_own_graveyard(self):
        game = GameState(rng=random.Random(0))
        tmpl = _gy_count_creature(
            "Consuming-Aberration-shaped", "graveyard_count:sym:any:opponents")
        creature = _put_on_battlefield(game, tmpl, 0)
        _graveyard_card(game, "Mine", 0, [CardType.CREATURE])
        _graveyard_card(game, "Theirs1", 1, [CardType.CREATURE])
        _graveyard_card(game, "Theirs2", 1, [CardType.INSTANT])

        assert creature.power == 2

    def test_type_any_counts_every_card_regardless_of_type(self):
        game = GameState(rng=random.Random(0))
        tmpl = _gy_count_creature(
            "Apocalypse-Demon-shaped", "graveyard_count:sym:any:your")
        creature = _put_on_battlefield(game, tmpl, 0)
        _graveyard_card(game, "L", 0, [CardType.LAND])
        _graveyard_card(game, "A", 0, [CardType.ARTIFACT])
        _graveyard_card(game, "C", 0, [CardType.CREATURE])

        assert creature.power == 3

    def test_nonbasic_land_type_excludes_basics(self):
        game = GameState(rng=random.Random(0))
        tmpl = _gy_count_creature(
            "Detritivore-shaped", "graveyard_count:sym:nonbasic_land:opponents")
        creature = _put_on_battlefield(game, tmpl, 0)
        _graveyard_card(game, "Basic", 1, [CardType.LAND], supertypes=[Supertype.BASIC])
        _graveyard_card(game, "Nonbasic", 1, [CardType.LAND])

        assert creature.power == 1

    def test_zero_cards_is_zero_zero(self):
        game = GameState(rng=random.Random(0))
        tmpl = _gy_count_creature(
            "Mortivore-shaped", "graveyard_count:sym:creature:all")
        creature = _put_on_battlefield(game, tmpl, 0)

        assert creature.power == 0
        assert creature.toughness == 0
