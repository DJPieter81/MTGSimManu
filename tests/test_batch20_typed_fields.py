"""Batch 20 typed-field migration tests.

# Mechanic: bounce-land oracle (rule, not a card)

has_bounce_land_oracle replaces 'return a land you control' in oracle in
engine/card_effects.py line 3102 (Scapeshift land-priority helper).

# Mechanic: sacrifice-search-land (rule, not a card)

has_sacrifice_search_land replaces the compound 'sacrifice'+'search'+'land'
in oracle check in engine/game_runner.py line 1821 (Expedition Map pattern).

# Mechanic: Emry graveyard cast (rule, not a card)

has_emry_graveyard_cast replaces 'choose target artifact card in your graveyard'
in oracle in engine/game_runner.py line 2032 (Emry, Lurker of the Loch pattern).

# Mechanic: {C}{C},{T}: draw a card (rule, not a card)

has_cc_tap_draw replaces the re.search('{c}{c},...') in oracle in
engine/game_runner.py line 2002 (Endbringer pattern).

# Mechanic: stax ability (rule, not a card)

has_stax_ability replaces two checks in engine/sideboard_manager.py lines 87-89
(Stony Silence / Damping Sphere pattern).

# Mechanic: Pithing Needle lock (rule, not a card)

has_pithing_needle_lock replaces two checks in engine/sideboard_manager.py
lines 96-97 (Pithing Needle / Phyrexian Revoker pattern).

# Mechanic: another-creature-enters trigger (rule, not a card)

has_another_creature_enters_trigger replaces 'another creature' in oracle in
engine/triggers.py line 56 (outer gate for both lifegain + energy paths).

# Mechanic: another-creature-enters lifegain trigger (rule, not a card)

has_another_creature_enters_lifegain replaces 'gain' in oracle in
engine/triggers.py line 57 (inner lifegain guard, conjunction of all 4 conditions).

# Mechanic: may-play-or-cast (rule, not a card)

has_may_play_or_cast replaces 'may play' in oracle in ai/ev_evaluator.py line 2344
(exile-and-play effects like Outpost Siege / light-up-the-stage pattern).

# Mechanic: damage-equal-to scaling (rule, not a card)

has_damage_equal_scaling replaces re.search('deals?.*damage.*equal', oracle) in
ai/evaluator.py line 477 (domain-scaling damage, Tribal Flames pattern).

# Mechanic: X-damage spell (rule, not a card)

has_x_damage replaces re.search('deals? x damage', oracle) in ai/evaluator.py
line 500 (X-cost damage spells, Blaze/Lightning Storm pattern).

# Mechanic: artifact pump equipment (rule, not a card)

has_artifact_pump_equipment replaces 'artifact' in oracle in engine/card_effects.py
line 1466 (equipment with +power scaling against artifact count, Cranial Plating).

# Mechanic: artifact-or-enchantment scaling (rule, not a card)

has_artifact_or_enchantment_scaling replaces 'artifact and/or enchantment' in oracle
in ai/ev_player.py line 3890 (equipment scaling against artifact+enchantment count).

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.oracle_parser import (
    parse_has_bounce_land_oracle,
    parse_has_sacrifice_search_land,
    parse_has_emry_graveyard_cast,
    parse_has_cc_tap_draw,
    parse_has_stax_ability,
    parse_has_pithing_needle_lock,
    parse_has_another_creature_enters_trigger,
    parse_has_another_creature_enters_lifegain,
    parse_has_may_play_or_cast,
    parse_has_damage_equal_scaling,
    parse_has_x_damage,
    parse_has_artifact_pump_equipment,
    parse_has_artifact_or_enchantment_scaling,
)


class TestHasBounceLandOracle:
    """Pins replacement of 'return a land you control' in oracle in
    card_effects.py — Karoo / Ravnica bounce land pattern."""

    def test_return_a_land_you_control(self):
        # Gruul Turf / Azorius Chancery pattern
        assert parse_has_bounce_land_oracle(
            "Gruul Turf enters tapped. When it enters, return a land you control to its owner's hand. {T}: Add {R}{G}."
        ) is True

    def test_bounce_land_lowercase(self):
        assert parse_has_bounce_land_oracle(
            "return a land you control to your hand"
        ) is True

    def test_plain_land_no_bounce(self):
        assert parse_has_bounce_land_oracle("{T}: Add {G}.") is False

    def test_return_creature_not_land(self):
        assert parse_has_bounce_land_oracle(
            "Return target creature to its owner's hand."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_bounce_land_oracle("") is False
        assert parse_has_bounce_land_oracle(None) is False


class TestHasSacrificeSearchLand:
    """Pins replacement of compound 'sacrifice'+'search'+'land' check in
    game_runner.py — Expedition Map / Wayfarer's Bauble pattern."""

    def test_expedition_map_pattern(self):
        # Expedition Map: {2}, {T}, Sacrifice ~: Search your library for a land card
        assert parse_has_sacrifice_search_land(
            "{2}, {T}, Sacrifice Expedition Map: Search your library for a land card, "
            "reveal it, put it into your hand, then shuffle."
        ) is True

    def test_sacrifice_and_search_without_land(self):
        assert parse_has_sacrifice_search_land(
            "Sacrifice Elvish Hunter: Search your library for a creature card."
        ) is False

    def test_search_for_land_without_sacrifice(self):
        assert parse_has_sacrifice_search_land(
            "Search your library for a basic land card and put it into your hand."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_sacrifice_search_land("") is False
        assert parse_has_sacrifice_search_land(None) is False


class TestHasEmryGraveyardCast:
    """Pins replacement of 'choose target artifact card in your graveyard'
    in game_runner.py — Emry, Lurker of the Loch pattern."""

    def test_emry_pattern(self):
        assert parse_has_emry_graveyard_cast(
            "{T}: Choose target artifact card in your graveyard. "
            "You may cast that card this turn."
        ) is True

    def test_plain_graveyard_without_artifact_choose(self):
        assert parse_has_emry_graveyard_cast(
            "Return target creature card from your graveyard to your hand."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_emry_graveyard_cast("") is False
        assert parse_has_emry_graveyard_cast(None) is False


class TestHasCcTapDraw:
    """Pins replacement of re.search('{c}{c},{t}: draw a card') in
    game_runner.py — Endbringer / colorless-tap-draw pattern."""

    def test_endbringer_pattern(self):
        assert parse_has_cc_tap_draw(
            "Untap Endbringer during each other player's untap step. "
            "{T}: Endbringer deals 1 damage to any target. "
            "{C}, {T}: Target creature can't attack or block this turn. "
            "{C}{C}, {T}: Draw a card."
        ) is True

    def test_draw_card_without_cc_cost(self):
        assert parse_has_cc_tap_draw(
            "{1}, {T}: Draw a card."
        ) is False

    def test_cc_without_draw(self):
        assert parse_has_cc_tap_draw(
            "{C}{C}, {T}: Add {C}{C}."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_cc_tap_draw("") is False
        assert parse_has_cc_tap_draw(None) is False


class TestHasStaxAbility:
    """Pins replacement of two checks in sideboard_manager.py —
    Stony Silence / Damping Sphere / Collector Ouphe pattern."""

    def test_activated_abilities_of_artifacts_pattern(self):
        # Stony Silence / Collector Ouphe
        assert parse_has_stax_ability(
            "Activated abilities of artifacts can't be activated."
        ) is True

    def test_damping_sphere_pattern(self):
        # Damping Sphere
        assert parse_has_stax_ability(
            "If a land is tapped for two or more mana, it produces {C} instead of any other type and amount."
        ) is True

    def test_plain_artifact_restriction_no_stax(self):
        assert parse_has_stax_ability(
            "Target artifact loses all abilities until end of turn."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_stax_ability("") is False
        assert parse_has_stax_ability(None) is False


class TestHasPithingNeedleLock:
    """Pins replacement of two checks in sideboard_manager.py —
    Pithing Needle / Phyrexian Revoker pattern."""

    def test_pithing_needle_pattern(self):
        assert parse_has_pithing_needle_lock(
            "As Pithing Needle enters, choose a card name. "
            "Activated abilities of sources with the chosen name can't be activated."
        ) is True

    def test_choose_card_name_without_ability_lock(self):
        assert parse_has_pithing_needle_lock(
            "As this enters, choose a card name. Spells with that name cost {1} more."
        ) is False

    def test_ability_lock_without_name_choice(self):
        assert parse_has_pithing_needle_lock(
            "Activated abilities of artifacts can't be activated."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_pithing_needle_lock("") is False
        assert parse_has_pithing_needle_lock(None) is False


class TestHasAnotherCreatureEntersTrigger:
    """Pins replacement of 'another creature' in oracle in triggers.py line 56 —
    outer gate for ETB fan-out (lifegain + energy production triggers)."""

    def test_another_creature_enters_trigger(self):
        # Soul Warden / Guide of Souls: whenever another creature enters
        assert parse_has_another_creature_enters_trigger(
            "Whenever another creature enters the battlefield under your control, "
            "you gain 1 life."
        ) is True

    def test_another_creature_enters_produces_energy(self):
        # Guide of Souls: another creature enters → {E}
        assert parse_has_another_creature_enters_trigger(
            "Whenever another creature enters the battlefield, you get {E}."
        ) is True

    def test_plain_etb_trigger_self(self):
        # "Whenever this creature enters" — does NOT say "another"
        assert parse_has_another_creature_enters_trigger(
            "Whenever this creature enters the battlefield, draw a card."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_another_creature_enters_trigger("") is False
        assert parse_has_another_creature_enters_trigger(None) is False


class TestHasAnotherCreatureEntersLifegain:
    """Pins replacement of 'gain' in oracle in triggers.py line 57 —
    inner lifegain guard (all 4 conditions: another creature + enters + gain + life)."""

    def test_soul_warden_pattern(self):
        # Soul Warden
        assert parse_has_another_creature_enters_lifegain(
            "Whenever another creature enters the battlefield, you gain 1 life."
        ) is True

    def test_another_creature_enters_energy_only(self):
        # Has another-creature-enters but only energy, not life
        assert parse_has_another_creature_enters_lifegain(
            "Whenever another creature enters the battlefield, you get {E}."
        ) is False

    def test_lifegain_without_another_creature_trigger(self):
        assert parse_has_another_creature_enters_lifegain(
            "You gain 3 life."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_another_creature_enters_lifegain("") is False
        assert parse_has_another_creature_enters_lifegain(None) is False


class TestHasMayPlayOrCast:
    """Pins replacement of 'may play' in oracle in ev_evaluator.py line 2344 —
    exile-and-play / exile-and-cast effects (Outpost Siege / Light Up the Stage)."""

    def test_may_play_exiled_cards(self):
        # Outpost Siege / Chandra: exile top card, may play
        assert parse_has_may_play_or_cast(
            "Exile the top card of your library. Until end of turn, you may play that card."
        ) is True

    def test_may_cast_exiled_cards(self):
        # Emry / escape-cast pattern
        assert parse_has_may_play_or_cast(
            "You may cast that card this turn."
        ) is True

    def test_plain_draw_no_play(self):
        assert parse_has_may_play_or_cast("Draw a card.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_may_play_or_cast("") is False
        assert parse_has_may_play_or_cast(None) is False


class TestHasDamageEqualScaling:
    """Pins replacement of re.search('deals?.*damage.*equal', oracle) in
    evaluator.py — domain-scaling damage (Tribal Flames pattern)."""

    def test_tribal_flames_pattern(self):
        # Tribal Flames: deals damage equal to the number of basic land types
        assert parse_has_damage_equal_scaling(
            "Tribal Flames deals damage to any target equal to the number of "
            "basic land types among lands you control."
        ) is True

    def test_fixed_damage_not_equal_scaling(self):
        assert parse_has_damage_equal_scaling(
            "This deals 3 damage to any target."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_damage_equal_scaling("") is False
        assert parse_has_damage_equal_scaling(None) is False


class TestHasXDamage:
    """Pins replacement of re.search('deals? x damage', oracle) in evaluator.py —
    X-cost damage spells (Blaze / Lightning Storm pattern)."""

    def test_x_damage_spell(self):
        # Blaze / Lightning Storm / Rolling Thunder
        assert parse_has_x_damage(
            "Blaze deals X damage to any target."
        ) is True

    def test_fixed_damage_not_x(self):
        assert parse_has_x_damage(
            "This deals 3 damage to target creature."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_x_damage("") is False
        assert parse_has_x_damage(None) is False


class TestHasArtifactPumpEquipment:
    """Pins replacement of 'artifact' in oracle in card_effects.py line 1466 —
    equipment with +1/+0 power scaling (Cranial Plating / Nettlecyst pattern)."""

    def test_cranial_plating_pattern(self):
        # Cranial Plating: +1/+0 for each artifact
        assert parse_has_artifact_pump_equipment(
            "Equipped creature gets +1/+0 for each artifact you control. "
            "{B}{P}: Attach Cranial Plating to target creature."
        ) is True

    def test_equipment_gets_bonus_per_artifact(self):
        # Generic: "gets +X/+Y for each artifact"
        assert parse_has_artifact_pump_equipment(
            "Equipped creature gets +2/+2 for each artifact you control."
        ) is True

    def test_plain_equipment_no_artifact_scaling(self):
        assert parse_has_artifact_pump_equipment(
            "Equipped creature gets +2/+2. Equip {2}."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_artifact_pump_equipment("") is False
        assert parse_has_artifact_pump_equipment(None) is False


class TestHasArtifactOrEnchantmentScaling:
    """Pins replacement of 'artifact and/or enchantment' in oracle in ev_player.py
    line 3890 — equipment that scales with artifact+enchantment count."""

    def test_artifact_and_or_enchantment(self):
        # Nettlecyst: +1/+1 for each artifact and/or enchantment
        assert parse_has_artifact_or_enchantment_scaling(
            "Equipped creature gets +1/+1 for each artifact and/or enchantment you control."
        ) is True

    def test_artifact_or_enchantment_variant(self):
        assert parse_has_artifact_or_enchantment_scaling(
            "This creature gets +1/+1 for each artifact or enchantment you control."
        ) is True

    def test_artifact_scaling_only_not_matched(self):
        # Only artifact scaling, not artifact+enchantment
        assert parse_has_artifact_or_enchantment_scaling(
            "Equipped creature gets +1/+0 for each artifact you control."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_artifact_or_enchantment_scaling("") is False
        assert parse_has_artifact_or_enchantment_scaling(None) is False
