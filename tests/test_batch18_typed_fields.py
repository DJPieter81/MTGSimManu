"""Batch 18 typed-field migration tests.

# Mechanic: artifact-count P/T scaling (rule, not a card)

has_artifact_count_scaling replaces the tight regex
r'[+]d+/[+]d+ for each artifact you control' in
cards.py _dynamic_base_power/_dynamic_base_toughness — Construct
tokens and Nettlecyst-class cards scale P/T with artifact count.

# Mechanic: surveil keyword (rule, not a card)

has_surveil replaces 'surveil' in oracle in oracle_resolver.py —
used to detect cast-trigger surveil effects on permanents.

# Mechanic: coin-flip transform (rule, not a card)

has_coin_flip replaces 'flip a coin' in oracle in oracle_resolver.py
— used to dispatch the coin-flip transform variant.

# Mechanic: mobilize keyword (rule, not a card)

has_mobilize replaces 'mobilize' in oracle in oracle_resolver.py
attack trigger dispatch.

# Mechanic: transform effect (rule, not a card)

has_transform_effect replaces 'transformed' in oracle in
oracle_resolver.py — detects permanents that transform as an effect.

# Mechanic: instant-or-sorcery reference (rule, not a card)

has_instant_or_sorcery_reference replaces three compound checks
('instant or sorcery' | 'instant and/or sorcery' | 'instant and sorcery')
in oracle_resolver.py — spells-matter trigger condition.

# Mechanic: graveyard targeting (rule, not a card)

has_graveyard_target replaces 'from a graveyard' in oracle in
target_solver.py — reanimation and graveyard-zone target detection.

# Mechanic: dual land search (rule, not a card)

has_dual_land_search replaces the compound check 'search' and 'two land'
in oracle in triggers.py — Primeval Titan attack-trigger pattern.

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.oracle_parser import (
    parse_has_artifact_count_scaling,
    parse_has_surveil,
    parse_has_coin_flip,
    parse_has_mobilize,
    parse_has_transform_effect,
    parse_has_instant_or_sorcery_reference,
    parse_has_graveyard_target,
    parse_has_dual_land_search,
)


class TestHasArtifactCountScaling:
    """Pins the tight regex replacement in cards.py P/T scaling —
    Construct tokens (Urza's Saga) scale P/T with artifact count."""

    def test_plus_n_plus_n_for_each_artifact(self):
        # Construct token / Nettlecyst pattern
        assert parse_has_artifact_count_scaling(
            "This creature gets +1/+1 for each artifact you control."
        ) is True

    def test_affinity_cost_reduction_not_flagged(self):
        # Affinity reminder text: "costs {1} less for each artifact you control"
        # must NOT match — this was the historical false-positive
        assert parse_has_artifact_count_scaling(
            "Affinity for artifacts (This spell costs {1} less to cast "
            "for each artifact you control.)"
        ) is False

    def test_plain_artifact_check_not_flagged(self):
        assert parse_has_artifact_count_scaling(
            "Tap target artifact."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_artifact_count_scaling("") is False
        assert parse_has_artifact_count_scaling(None) is False


class TestHasSurveil:
    """Pins the replacement of 'surveil' in oracle in oracle_resolver.py —
    noncreature-spell cast triggers that surveil."""

    def test_surveil_keyword(self):
        # Consider / Thought Erasure pattern
        assert parse_has_surveil("Surveil 2.") is True

    def test_surveil_in_trigger(self):
        assert parse_has_surveil(
            "Whenever you cast a noncreature spell, surveil 1."
        ) is True

    def test_plain_draw_no_surveil(self):
        assert parse_has_surveil("Draw a card.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_surveil("") is False
        assert parse_has_surveil(None) is False


class TestHasCoinFlip:
    """Pins the replacement of 'flip a coin' in oracle in oracle_resolver.py
    — transform-on-coin-flip variant (Ral pattern)."""

    def test_flip_a_coin_text(self):
        assert parse_has_coin_flip(
            "Flip a coin. If you win the flip, exile this permanent, "
            "then return it to the battlefield transformed."
        ) is True

    def test_plain_transform_no_coin(self):
        assert parse_has_coin_flip(
            "If you've cast two or more instant and/or sorcery spells "
            "this turn, exile this permanent, then return it transformed."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_coin_flip("") is False
        assert parse_has_coin_flip(None) is False


class TestHasMobilize:
    """Pins the replacement of 'mobilize' in oracle in oracle_resolver.py
    attack trigger dispatch."""

    def test_mobilize_keyword(self):
        # Kellan / mobilize-keyword card pattern
        assert parse_has_mobilize(
            "Mobilize 2 (Whenever this creature attacks, "
            "create two tapped and attacking 1/1 Warrior creature tokens.)"
        ) is True

    def test_attack_trigger_without_mobilize(self):
        assert parse_has_mobilize(
            "Whenever this creature attacks, create a 1/1 token."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_mobilize("") is False
        assert parse_has_mobilize(None) is False


class TestHasTransformEffect:
    """Pins the replacement of 'transformed' in oracle in oracle_resolver.py
    — permanents that transform as a triggered effect."""

    def test_exile_return_transformed(self):
        # Fable of the Mirror-Breaker / Ral pattern
        assert parse_has_transform_effect(
            "Exile this permanent, then return it to the battlefield "
            "transformed under your control."
        ) is True

    def test_transform_in_rules_text(self):
        assert parse_has_transform_effect(
            "When this creature is dealt damage, transform it."
        ) is True

    def test_permanent_without_transform(self):
        assert parse_has_transform_effect("Flying. Vigilance.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_transform_effect("") is False
        assert parse_has_transform_effect(None) is False


class TestHasInstantOrSorceryReference:
    """Pins the replacement of three compound checks in oracle_resolver.py —
    permanents that count or reference instants or sorceries."""

    def test_instant_or_sorcery(self):
        # Fable-class: "if you've cast two or more instant or sorcery spells"
        assert parse_has_instant_or_sorcery_reference(
            "If you've cast two or more instant or sorcery spells this turn, "
            "exile this, then return it transformed."
        ) is True

    def test_instant_and_or_sorcery(self):
        # Ledger Shredder / Slickshot: "instant and/or sorcery"
        assert parse_has_instant_or_sorcery_reference(
            "Whenever you cast your second spell each turn, "
            "if it's an instant and/or sorcery, connive."
        ) is True

    def test_instant_and_sorcery(self):
        assert parse_has_instant_or_sorcery_reference(
            "This gets +1/+1 for each instant and sorcery in your graveyard."
        ) is True

    def test_creature_spell_no_match(self):
        assert parse_has_instant_or_sorcery_reference(
            "Whenever you cast a creature spell, draw a card."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_instant_or_sorcery_reference("") is False
        assert parse_has_instant_or_sorcery_reference(None) is False


class TestHasGraveyardTarget:
    """Pins the replacement of 'from a graveyard' in oracle in target_solver.py
    — spells that target cards in any graveyard (not just yours)."""

    def test_from_a_graveyard(self):
        # Reanimate / Goryo's Vengeance pattern
        assert parse_has_graveyard_target(
            "Put target creature card from a graveyard onto the battlefield "
            "under your control."
        ) is True

    def test_from_your_graveyard_not_matched(self):
        # Unearth/flashback — "from your graveyard" is narrower;
        # the 'from a graveyard' pattern should NOT match this
        assert parse_has_graveyard_target(
            "Return target creature card from your graveyard to your hand."
        ) is False

    def test_plain_draw_no_graveyard(self):
        assert parse_has_graveyard_target("Draw a card.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_graveyard_target("") is False
        assert parse_has_graveyard_target(None) is False


class TestHasDualLandSearch:
    """Pins the replacement of 'search' + 'two land' in oracle in triggers.py
    — Primeval Titan attack-trigger pattern."""

    def test_search_two_land_cards(self):
        # Primeval Titan: "search your library for up to two land cards"
        assert parse_has_dual_land_search(
            "Whenever this creature enters the battlefield or attacks, "
            "you may search your library for up to two land cards, "
            "put them onto the battlefield tapped, then shuffle."
        ) is True

    def test_search_one_land_not_matched(self):
        # Sylvan Scrying / Farseek — single land search
        assert parse_has_dual_land_search(
            "Search your library for a land card, reveal it, "
            "put it into your hand, then shuffle."
        ) is False

    def test_plain_attack_trigger_no_search(self):
        assert parse_has_dual_land_search(
            "Whenever this creature attacks, create two 4/4 Elemental tokens."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_dual_land_search("") is False
        assert parse_has_dual_land_search(None) is False
