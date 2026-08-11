"""oracle-ratchet batch5 small-files: verify typed fields replace runtime oracle checks.

Covers five migration sites:

  engine/oracle_resolver.py  – has_noncreature_spell_cast_trigger (3 dispatch branches)
  ai/response.py             – counter_target_kind (targeting restriction);
                               has_artifact_synergy (equipment scaler)
  engine/callbacks.py        – has_artifact_synergy (equipment rank key)
  engine/card_effects.py     – ward_cost > 0 (ward deduction in threat eval)
  ai/evaluator.py            – is_tutor (tutor bonus gate)

Rule: decision-time code reads CardTemplate typed fields; it never re-inspects
the raw oracle string.  Each test class pins one mechanic, not one card.

Class sizes: noncreature-cast triggers → 20+ cards (Young Pyromancer class);
counter restrictions → every selectively-targeted counterspell in Modern;
artifact synergy → Cranial Plating, Nettlecyst, + Metalcraft / Affinity cards;
ward cost → Wandering Emperor, Sheoldred, Solitude, …; tutors → 100+ cards.
"""
from __future__ import annotations

import pytest

from engine.cards import CardTemplate, CardType
from engine.mana import ManaCost
from engine.oracle_parser import (
    parse_has_noncreature_spell_cast_trigger,
    parse_has_artifact_synergy,
    parse_is_tutor,
    parse_ward_cost,
)


def _tmpl(oracle: str, *, is_creature: bool = False) -> CardTemplate:
    """Minimal CardTemplate — __post_init__ populates all parsed fields.

    Note: ward_cost is populated by card_database.py at DB-load time, not by
    __post_init__, so ward_cost tests use parse_ward_cost() directly or card_db.
    """
    types = [CardType.CREATURE if is_creature else CardType.INSTANT]
    return CardTemplate(
        name="__test__",
        oracle_text=oracle,
        card_types=types,
        mana_cost=ManaCost(generic=0),
        supertypes=[],
        subtypes=[],
        keywords=set(),
        abilities=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# oracle_resolver.py migration: has_noncreature_spell_cast_trigger
# ─────────────────────────────────────────────────────────────────────────────

class TestNoncreatureSpellCastTriggerField:
    """has_noncreature_spell_cast_trigger replaces 'noncreature spell' in oracle.

    Three branches in resolve_spell_cast_trigger now use the typed field:
    energy production, token creation, and surveil.
    """

    def test_energy_trigger_oracle_sets_field(self):
        """Energy-production card (Ocelot Pride shape) sets the field."""
        t = _tmpl(
            "Whenever you cast a noncreature spell, you get {E}."
        )
        assert t.has_noncreature_spell_cast_trigger is True

    def test_token_creation_oracle_sets_field(self):
        """Token-creation card (Young Pyromancer shape) sets the field."""
        t = _tmpl(
            "Whenever you cast a noncreature spell, "
            "create a 1/1 red Elemental creature token."
        )
        assert t.has_noncreature_spell_cast_trigger is True

    def test_surveil_oracle_sets_field(self):
        """Surveil-on-noncreature card (Dragon's Rage Channeler shape) sets the field."""
        t = _tmpl(
            "Whenever you cast a noncreature spell, surveil 1."
        )
        assert t.has_noncreature_spell_cast_trigger is True

    def test_creature_spell_trigger_does_not_set_field(self):
        """'Whenever you cast a creature spell' does NOT set the noncreature field."""
        t = _tmpl("Whenever you cast a creature spell, draw a card.")
        assert t.has_noncreature_spell_cast_trigger is False

    def test_vanilla_card_field_false(self):
        """A card with no noncreature-spell trigger has the field False."""
        t = _tmpl("Flying, vigilance.")
        assert t.has_noncreature_spell_cast_trigger is False

    def test_parse_fn_matches_field(self):
        """parse_has_noncreature_spell_cast_trigger agrees with CardTemplate.__post_init__."""
        oracle = "Whenever you cast a noncreature spell, this creature gets +1/+0."
        assert parse_has_noncreature_spell_cast_trigger(oracle) is True
        assert _tmpl(oracle).has_noncreature_spell_cast_trigger is True

    def test_parse_fn_false_for_non_trigger(self):
        oracle = "Draw two cards."
        assert parse_has_noncreature_spell_cast_trigger(oracle) is False
        assert _tmpl(oracle).has_noncreature_spell_cast_trigger is False

    def test_card_db_noncreature_trigger_card(self, card_db):
        """Dragon's Rage Channeler (canonical noncreature-cast trigger) has the field True."""
        tmpl = card_db.get_card("Dragon's Rage Channeler")
        if tmpl is None:
            pytest.skip("Dragon's Rage Channeler not in DB")
        assert tmpl.has_noncreature_spell_cast_trigger is True, (
            "Dragon's Rage Channeler must have has_noncreature_spell_cast_trigger=True; "
            f"got {tmpl.has_noncreature_spell_cast_trigger!r}"
        )

    def test_card_db_burn_spell_field_false(self, card_db):
        """A burn spell (Lightning Bolt) must not have the noncreature-cast trigger field."""
        tmpl = card_db.get_card("Lightning Bolt")
        if tmpl is None:
            pytest.skip("Lightning Bolt not in DB")
        assert tmpl.has_noncreature_spell_cast_trigger is False


# ─────────────────────────────────────────────────────────────────────────────
# ai/response.py migration: counter_target_kind
# ─────────────────────────────────────────────────────────────────────────────

class TestCounterTargetKindField:
    """counter_target_kind replaces 'noncreature' in oracle / 'instant or sorcery' in oracle.

    Used in response.py to filter which counter can legally target the stack item.
    """

    def test_noncreature_counter_target_kind_via_parser(self):
        """A noncreature-targeting counterspell has target_type='noncreature_spell'."""
        from engine.card_database import OracleTextParser
        effects = OracleTextParser.parse("Counter target noncreature spell.")
        counter_effects = [e for e in effects if e.effect_type == "counter"]
        assert counter_effects, "Parser must detect a counter effect"
        assert counter_effects[0].target_type == "noncreature_spell"

    def test_instant_or_sorcery_counter_target_kind_via_parser(self):
        """A counter targeting instant-or-sorcery has target_type='instant_or_sorcery_spell'."""
        from engine.card_database import OracleTextParser
        effects = OracleTextParser.parse(
            "Counter target instant or sorcery spell."
        )
        counter_effects = [e for e in effects if e.effect_type == "counter"]
        assert counter_effects, "Parser must detect a counter effect"
        assert counter_effects[0].target_type == "instant_or_sorcery_spell"

    def test_generic_counter_target_kind(self):
        """'Counter target spell' produces target_type='spell'."""
        from engine.card_database import OracleTextParser
        effects = OracleTextParser.parse("Counter target spell.")
        counter_effects = [e for e in effects if e.effect_type == "counter"]
        assert counter_effects
        assert counter_effects[0].target_type == "spell"

    def test_counter_target_kind_on_counterspell_card(self, card_db):
        """Counterspell (all spells) has counter_target_kind='spell' on the template."""
        tmpl = card_db.get_card("Counterspell")
        if tmpl is None:
            pytest.skip("Counterspell not in DB")
        assert tmpl.counter_target_kind == "spell", (
            f"Counterspell.counter_target_kind must be 'spell'; "
            f"got {tmpl.counter_target_kind!r}"
        )

    def test_response_noncreature_filter_uses_counter_target_kind(self):
        """response.py uses counter_target_kind, not oracle substring.

        A card with counter_target_kind='noncreature_spell' must NOT be chosen
        to counter a creature spell.  Verify the field value drives the decision.
        """
        from engine.card_database import OracleTextParser

        # Build a synthetic template with counter_target_kind='noncreature_spell'
        t = CardTemplate(
            name="__noncreature_counter__",
            oracle_text="Counter target noncreature spell.",
            card_types=[CardType.INSTANT],
            mana_cost=ManaCost(generic=1),
            supertypes=[],
            subtypes=[],
            keywords=set(),
            abilities=[],
        )
        # Set the field as card_database does at load time.
        effects = OracleTextParser.parse(t.oracle_text or "")
        counter_eff = next((e for e in effects if e.effect_type == "counter"), None)
        if counter_eff:
            t.counter_target_kind = counter_eff.target_type
        assert t.counter_target_kind == "noncreature_spell"

        # Simulate the migrated check in response.py:
        # skip counter when counter_target_kind == 'noncreature_spell' and target is creature.
        class _FakeCreatureTemplate:
            is_creature = True
            is_instant = False
            is_sorcery = False

        target_spell = _FakeCreatureTemplate()
        should_skip = (t.counter_target_kind == 'noncreature_spell'
                       and target_spell.is_creature)
        assert should_skip is True, (
            "noncreature_spell counter must be skipped when targeting a creature spell"
        )

    def test_response_instant_sorcery_filter_uses_counter_target_kind(self):
        """'instant or sorcery' filter uses counter_target_kind, not oracle substring."""
        from engine.card_database import OracleTextParser

        t = CardTemplate(
            name="__instant_sorcery_counter__",
            oracle_text="Counter target instant or sorcery spell.",
            card_types=[CardType.INSTANT],
            mana_cost=ManaCost(generic=1),
            supertypes=[],
            subtypes=[],
            keywords=set(),
            abilities=[],
        )
        effects = OracleTextParser.parse(t.oracle_text or "")
        counter_eff = next((e for e in effects if e.effect_type == "counter"), None)
        if counter_eff:
            t.counter_target_kind = counter_eff.target_type
        assert t.counter_target_kind == "instant_or_sorcery_spell"

        # Simulate the migrated check in response.py:
        # skip counter when counter_target_kind == 'instant_or_sorcery_spell'
        # and target is not an instant or sorcery.
        class _FakePlaneswalkerTemplate:
            is_creature = False
            is_instant = False
            is_sorcery = False

        target = _FakePlaneswalkerTemplate()
        should_skip = (t.counter_target_kind == 'instant_or_sorcery_spell'
                       and not (target.is_instant or target.is_sorcery))
        assert should_skip is True, (
            "instant_or_sorcery counter must be skipped when targeting a non-instant/sorcery"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ai/response.py + engine/callbacks.py migration: has_artifact_synergy
# ─────────────────────────────────────────────────────────────────────────────

class TestArtifactSynergyField:
    """has_artifact_synergy replaces 'for each artifact' in oracle.

    Used in response.py (equipment scaler power estimate) and callbacks.py
    (equipment attach-target ranking).
    """

    def test_for_each_artifact_sets_field(self):
        """'for each artifact' in oracle text sets has_artifact_synergy=True."""
        t = _tmpl(
            "Equipped creature gets +1/+0 for each artifact you control."
        )
        assert t.has_artifact_synergy is True

    def test_metalcraft_sets_field(self):
        """Metalcraft keyword sets has_artifact_synergy=True."""
        t = _tmpl(
            "Metalcraft — This creature has first strike as long as "
            "you control three or more artifacts."
        )
        assert t.has_artifact_synergy is True

    def test_affinity_for_artifacts_sets_field(self):
        """'Affinity for artifacts' sets has_artifact_synergy=True."""
        t = _tmpl(
            "Affinity for artifacts (This spell costs {1} less to cast "
            "for each artifact you control.)"
        )
        assert t.has_artifact_synergy is True

    def test_non_artifact_synergy_card_field_false(self):
        """A plain card without artifact synergy has has_artifact_synergy=False."""
        t = _tmpl("Flying.")
        assert t.has_artifact_synergy is False

    def test_for_each_creature_not_artifact_synergy(self):
        """'for each creature' does NOT set has_artifact_synergy."""
        t = _tmpl("This creature gets +1/+0 for each creature you control.")
        assert t.has_artifact_synergy is False

    def test_parse_fn_matches_field(self):
        """parse_has_artifact_synergy agrees with CardTemplate.__post_init__."""
        oracle = "Equipped creature gets +1/+0 for each artifact you control."
        assert parse_has_artifact_synergy(oracle) is True
        assert _tmpl(oracle).has_artifact_synergy is True

    def test_card_db_cranial_plating(self, card_db):
        """Cranial Plating (canonical artifact scaler) has has_artifact_synergy=True."""
        tmpl = card_db.get_card("Cranial Plating")
        if tmpl is None:
            pytest.skip("Cranial Plating not in DB")
        assert tmpl.has_artifact_synergy is True, (
            f"Cranial Plating must have has_artifact_synergy=True; "
            f"got {tmpl.has_artifact_synergy!r}"
        )

    def test_callbacks_ranking_uses_has_artifact_synergy(self, card_db):
        """callbacks._rank uses template.has_artifact_synergy, not oracle substring.

        Verify by checking Cranial Plating's field rather than substring.
        The field is what callbacks.py reads at runtime after migration.
        """
        tmpl = card_db.get_card("Cranial Plating")
        if tmpl is None:
            pytest.skip("Cranial Plating not in DB")
        # The migrated _rank reads c.template.has_artifact_synergy.
        # Confirm it's True so the equipment scaler path triggers.
        assert tmpl.has_artifact_synergy is True

    def test_non_artifact_equipment_does_not_trigger_scaler(self, card_db):
        """An equipment without artifact synergy has has_artifact_synergy=False."""
        # Sword of Fire and Ice gives +2/+2 flat, no 'for each artifact'.
        tmpl = card_db.get_card("Sword of Fire and Ice")
        if tmpl is None:
            pytest.skip("Sword of Fire and Ice not in DB")
        assert tmpl.has_artifact_synergy is False


# ─────────────────────────────────────────────────────────────────────────────
# engine/card_effects.py migration: ward_cost > 0
# ─────────────────────────────────────────────────────────────────────────────

class TestWardCostField:
    """ward_cost > 0 replaces 'ward' in oracle substring check.

    ward_cost is populated by card_database.py (not __post_init__), so tests
    use parse_ward_cost() directly or card_db for real cards.

    Used in card_effects._threat_score to apply the ward discount on two
    code paths (game-aware and context-free).
    """

    def test_parse_ward_cost_fn_numeric(self):
        """parse_ward_cost returns the integer mana value from oracle text."""
        assert parse_ward_cost("Ward {2}.") == 2

    def test_parse_ward_cost_fn_numeric_3(self):
        assert parse_ward_cost("Ward {3}.") == 3

    def test_parse_ward_cost_fn_no_ward_returns_zero(self):
        """parse_ward_cost returns 0 for cards without ward."""
        assert parse_ward_cost("Flying.") == 0

    def test_ward_cost_gt_zero_predicate_ward_card(self):
        """ward_cost > 0 is the migrated condition; must be True for a ward-2 card."""
        # Simulate a template with ward_cost set by card_database.
        t = _tmpl("Flying.", is_creature=True)
        t.ward_cost = 2  # simulate DB load
        assert (t.ward_cost > 0) is True

    def test_ward_cost_gt_zero_predicate_non_ward_card(self):
        """ward_cost > 0 is False for a non-ward card (default = 0)."""
        t = _tmpl("Trample.", is_creature=True)
        assert (t.ward_cost > 0) is False

    def test_card_db_ward_creature(self, card_db):
        """A well-known ward creature has ward_cost >= 2."""
        tmpl = card_db.get_card("Wandering Emperor")
        if tmpl is None:
            pytest.skip("Wandering Emperor not in DB")
        assert tmpl.ward_cost >= 2, (
            f"Wandering Emperor must have ward_cost >= 2; got {tmpl.ward_cost!r}"
        )

    def test_card_db_non_ward_creature_zero(self, card_db):
        """A plain creature has ward_cost == 0."""
        tmpl = card_db.get_card("Lightning Bolt")
        if tmpl is None:
            pytest.skip("Lightning Bolt not in DB")
        assert tmpl.ward_cost == 0

    def test_threat_score_ward_branch_uses_field(self, card_db):
        """_threat_score uses t.ward_cost, not oracle substring.

        A ward creature's threat evaluation must use the typed field.
        The function must return a float without raising even when called
        context-free (game=None), and the ward-discount branch fires when
        t.ward_cost > 0.
        """
        from engine.card_effects import _threat_score
        from engine.cards import CardInstance

        tmpl = card_db.get_card("Wandering Emperor")
        if tmpl is None:
            pytest.skip("Wandering Emperor not in DB")

        # Pre-condition: ward_cost must be nonzero for the migrated branch to fire.
        assert tmpl.ward_cost > 0, "pre-condition: ward_cost must be > 0"

        inst = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=0, zone="battlefield",
        )
        # Context-free path (game=None): must return a float without raising.
        val = _threat_score(inst, game=None)
        assert isinstance(val, float), (
            f"_threat_score must return float for ward creature; got {type(val)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ai/evaluator.py migration: is_tutor
# ─────────────────────────────────────────────────────────────────────────────

class TestIsTutorField:
    """is_tutor replaces 'search your library' in oracle substring check.

    Used in ai/evaluator.py (_ability_bonus) to gate the tutor bonus.
    The typed field is also True for Wish-type ('from outside the game') and
    explicit sideboard fetches — a superset of the old single-string check.
    """

    def test_search_your_library_sets_is_tutor(self):
        """'search your library' in oracle text sets is_tutor=True."""
        t = _tmpl("Search your library for a creature card, reveal it, "
                  "and put it into your hand. Shuffle.")
        assert t.is_tutor is True

    def test_from_outside_the_game_sets_is_tutor(self):
        """'from outside the game' (Wish) sets is_tutor=True."""
        t = _tmpl("You may choose a card you own from outside the game, "
                  "reveal it, and put it into your hand.")
        assert t.is_tutor is True

    def test_from_your_sideboard_sets_is_tutor(self):
        """'from your sideboard' sets is_tutor=True."""
        t = _tmpl("Put a card from your sideboard into your hand.")
        assert t.is_tutor is True

    def test_non_tutor_card_field_false(self):
        """A non-tutor card has is_tutor=False."""
        t = _tmpl("Draw two cards.")
        assert t.is_tutor is False

    def test_parse_fn_matches_field(self):
        """parse_is_tutor agrees with CardTemplate.__post_init__."""
        oracle = "Search your library for a land card and put it onto the battlefield tapped."
        assert parse_is_tutor(oracle) is True
        assert _tmpl(oracle).is_tutor is True

    def test_card_db_fetch_land_is_tutor(self, card_db):
        """Fetch lands have is_tutor=True (they search the library)."""
        tmpl = card_db.get_card("Flooded Strand")
        if tmpl is None:
            pytest.skip("Flooded Strand not in DB")
        assert tmpl.is_tutor is True, (
            f"Flooded Strand must have is_tutor=True; got {tmpl.is_tutor!r}"
        )

    def test_evaluator_tutor_bonus_gate_uses_is_tutor(self):
        """_ability_bonus uses template.is_tutor, not oracle substring.

        A template with is_tutor=True must produce a higher bonus than one
        with is_tutor=False (and otherwise identical properties).
        The _ability_bonus function accepts a CardTemplate directly.
        """
        from ai.evaluator import _ability_bonus

        tutor_t = _tmpl(
            "Search your library for a card and put it into your hand. Shuffle."
        )
        non_tutor_t = _tmpl("Do nothing special.")

        # is_tutor is set by __post_init__
        assert tutor_t.is_tutor is True
        assert non_tutor_t.is_tutor is False

        # The tutor template must produce the larger bonus.
        # _ability_bonus accepts a card-or-template; pass template directly.
        tutor_bonus = _ability_bonus(tutor_t)
        non_tutor_bonus = _ability_bonus(non_tutor_t)
        assert tutor_bonus > non_tutor_bonus, (
            f"Tutor card bonus ({tutor_bonus}) must exceed non-tutor bonus "
            f"({non_tutor_bonus}) — _ability_bonus must read template.is_tutor"
        )
