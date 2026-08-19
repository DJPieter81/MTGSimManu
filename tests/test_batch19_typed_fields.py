"""Batch 19 typed-field migration tests.

# Mechanic: energy-damage target (rule, not a card)

has_energy_damage_target replaces a five-condition compound oracle check
in oracle_resolver.py that detects permanents which deal energy-scaled
damage to a creature or planeswalker.

# Mechanic: energy production (rule, not a card)

has_energy_production replaces 'you get' in oracle in oracle_resolver.py
noncreature-cast energy trigger (Ocelot Pride / Guide of Souls pattern).

# Mechanic: look-at-top hand selection (rule, not a card)

has_look_hand_selection replaces two patterns:
- 'put one of them into your hand' (oracle_resolver.py, ev_evaluator.py)
- 'put them into your hand' (ev_evaluator.py tutor all-to-hand)

# Mechanic: cast-spell draw trigger (rule, not a card)

has_cast_spell_draw replaces two oracle checks in oracle_resolver.py
('cast a spell' + 'draw a card' without a noncreature restriction).

# Mechanic: opponent-cast damage trigger (rule, not a card)

has_opponent_cast_damage replaces the compound 'opponent casts' + 'damage'
check in oracle_resolver.py.

# Mechanic: mana-add text (rule, not a card)

has_mana_add_text replaces three 'add' in oracle checks in ev_evaluator.py
used for urgency scoring of mana-producing spells.

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.oracle_parser import (
    parse_has_energy_damage_target,
    parse_has_energy_production,
    parse_has_look_hand_selection,
    parse_has_cast_spell_draw,
    parse_has_opponent_cast_damage,
    parse_has_mana_add_text,
)


class TestHasEnergyDamageTarget:
    """Pins the replacement of the five-condition energy-damage guard in
    oracle_resolver.py — Aetherworks Marvel / Pummeler energy-damage pattern."""

    def test_full_energy_damage_pattern(self):
        # Electrostatic Pummeler / Aether Hub energy-damage pattern
        assert parse_has_energy_damage_target(
            "Target creature or planeswalker gets -1/-1 until end of turn. "
            "You get {E}{E}. Pay any amount of {E}: this deals that much "
            "damage to target creature or planeswalker."
        ) is True

    def test_choose_target_variant(self):
        assert parse_has_energy_damage_target(
            "Choose target creature or planeswalker. You get {E}. "
            "Pay any amount of {E}: deal that much damage to it."
        ) is True

    def test_plain_burn_no_energy(self):
        assert parse_has_energy_damage_target(
            "Deal 3 damage to any target."
        ) is False

    def test_energy_without_damage(self):
        assert parse_has_energy_damage_target(
            "You get {E}{E}{E}."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_energy_damage_target("") is False
        assert parse_has_energy_damage_target(None) is False


class TestHasEnergyProduction:
    """Pins the replacement of 'you get' in oracle in oracle_resolver.py
    noncreature-cast energy trigger."""

    def test_you_get_energy_text(self):
        # Ocelot Pride / Guide of Souls: "you get {E}"
        assert parse_has_energy_production(
            "Whenever you cast a noncreature spell, you get {E}."
        ) is True

    def test_multiple_energy(self):
        assert parse_has_energy_production(
            "Whenever a creature you control attacks, you get {E}{E}."
        ) is True

    def test_you_get_without_energy_symbol(self):
        # "you get to" (draw a card phrasing) — no energy symbol
        assert parse_has_energy_production(
            "You get to draw a card."
        ) is False

    def test_plain_combat_trigger_no_energy(self):
        assert parse_has_energy_production(
            "Whenever this creature attacks, it gets +1/+0 until end of turn."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_energy_production("") is False
        assert parse_has_energy_production(None) is False


class TestHasLookHandSelection:
    """Pins the replacement of two oracle patterns in oracle_resolver.py and
    ev_evaluator.py that detect look-top-N keep-one or put-all-to-hand effects."""

    def test_put_one_of_them_into_hand(self):
        # Sleight of Hand / Opt pattern — look at top N, put one into hand
        assert parse_has_look_hand_selection(
            "Look at the top two cards of your library. "
            "Put one of them into your hand and the other on the bottom."
        ) is True

    def test_put_them_into_hand_all_copies(self):
        # Squadron Hawk / tutor-all-to-hand pattern
        assert parse_has_look_hand_selection(
            "Search your library for up to three cards with the same name, "
            "reveal them, put them into your hand, then shuffle."
        ) is True

    def test_plain_draw_no_selection(self):
        assert parse_has_look_hand_selection("Draw two cards.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_look_hand_selection("") is False
        assert parse_has_look_hand_selection(None) is False


class TestHasCastSpellDraw:
    """Pins the replacement of the two-condition cast-spell draw check in
    oracle_resolver.py — Curiosity-class enchantment triggers."""

    def test_cast_a_spell_draw_a_card(self):
        # Curiosity / Tandem Lookout pattern
        assert parse_has_cast_spell_draw(
            "Whenever enchanted creature deals combat damage to a player, "
            "you may draw a card."
        ) is False  # no "cast a spell" trigger — draw on damage, not cast

    def test_whenever_you_cast_a_spell_draw(self):
        # Teferi's Ageless Insight-class or similar draw-on-cast
        assert parse_has_cast_spell_draw(
            "Whenever you cast a spell, draw a card."
        ) is True

    def test_cast_instant_or_sorcery_draw(self):
        assert parse_has_cast_spell_draw(
            "Whenever you cast an instant or sorcery spell, draw a card."
        ) is True

    def test_noncreature_restricted_draw_not_matched(self):
        # Noncreature restriction → excluded from this field (has its own path)
        assert parse_has_cast_spell_draw(
            "Whenever you cast a noncreature spell, draw a card."
        ) is False

    def test_plain_draw_no_trigger(self):
        assert parse_has_cast_spell_draw("Draw a card.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_cast_spell_draw("") is False
        assert parse_has_cast_spell_draw(None) is False


class TestHasOpponentCastDamage:
    """Pins the replacement of 'opponent casts' + 'damage' check in
    oracle_resolver.py — Orcish Bowmasters / opponent-cast trigger."""

    def test_opponent_casts_damage(self):
        # Orcish Bowmasters pattern
        assert parse_has_opponent_cast_damage(
            "Whenever an opponent casts an instant or sorcery spell, "
            "this deals 1 damage to each opponent."
        ) is True

    def test_opponent_casts_without_damage(self):
        assert parse_has_opponent_cast_damage(
            "Whenever an opponent casts a spell, draw a card."
        ) is False

    def test_damage_without_opponent_cast_trigger(self):
        assert parse_has_opponent_cast_damage(
            "Whenever this creature attacks, deal 1 damage to any target."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_opponent_cast_damage("") is False
        assert parse_has_opponent_cast_damage(None) is False


class TestHasManaAddText:
    """Pins the replacement of three 'add' in oracle checks in ev_evaluator.py
    — mana-producing spells (rituals, mana rocks) urgency detection."""

    def test_add_colored_mana_symbol(self):
        # Dark Ritual / Llanowar Elves / mana rock pattern
        assert parse_has_mana_add_text(
            "{T}: Add {G}."
        ) is True

    def test_add_any_color_mana(self):
        # Chromatic Sphere / Mox Opal pattern
        assert parse_has_mana_add_text(
            "Add one mana of any color."
        ) is True

    def test_add_counter_not_matched(self):
        # "add a counter" — should NOT be detected as mana production
        assert parse_has_mana_add_text(
            "Add a +1/+1 counter to target creature."
        ) is False

    def test_add_card_to_hand_not_matched(self):
        assert parse_has_mana_add_text(
            "Add the top card of your library to your hand."
        ) is False

    def test_ritual_adds_mana(self):
        # Dark Ritual pattern
        assert parse_has_mana_add_text(
            "Add {B}{B}{B}."
        ) is True

    def test_empty_oracle_is_false(self):
        assert parse_has_mana_add_text("") is False
        assert parse_has_mana_add_text(None) is False
