"""Activated tutor effects (CR 602 + CR 701.19): "[Cost]: Search your
library for a ... card, put it onto the battlefield / into your hand,
then shuffle."

The tranche-3 acceptance doc (docs/diagnostics/2026-08-26_...) named the
effect-kind whitelist as the binding gate for toolbox decks: a toolbox
deck IS its tutor activations. This file pins the mechanic class — every
activated-tutor permanent in Modern — never a single card.

Rules pinned:
  * Classification is ANCHORED to the full sentence: composite effects,
    union type constraints, multi-card searches and unsupported riders
    stay UNCLASSIFIED (the tranche discipline: never half-execute).
  * The search constraint (card type / subtype / supertype / color /
    mana-value bound) parses into structured data on the ability.
  * An {X} pip in an activation cost is a chargeable COUNT exactly when
    the effect binds X ("mana value X or less"); a hybrid pip charges as
    one generic mana — the caster picks the colour — matching the
    spell-side convention in `_parse_mana_symbols_to_cost`.
  * CR 701.19b — a search may fail: a tutor with no legal candidate in
    the library is STILL a legal activation; not paying for a whiff is
    the AI's judgment, not a legality question.
  * Resolution routes through the shared library-search machinery:
    opponents' search triggers fire, the found card moves through the
    zone funnel (battlefield entry gets ETB fan-out), the library is
    shuffled.
  * The library CHOICE is strategic: the engine default is the highest
    mana value satisfying the constraint; the AI callback picks the
    plan-best target.

Card names appearing in test BODIES are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.activation import ActivationManager
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import (classify_activation_effect,
                                  parse_activated_abilities,
                                  parse_activation_cost,
                                  parse_activation_tutor)

_DB = CardDatabase()

_BF_SENTENCE = ("Search your library for a creature card with mana value X "
                "or less, put it onto the battlefield, then shuffle")
_HAND_SENTENCE = ("Search your library for a Sliver card, reveal it, put it "
                  "into your hand, then shuffle")


# ── classification: the two executable shapes ─────────────────────────

def test_battlefield_tutor_sentence_classifies_with_x_bound_constraint():
    kind, *_ = classify_activation_effect(_BF_SENTENCE + ".")
    assert kind is ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD
    data = parse_activation_tutor(_BF_SENTENCE)
    assert data is not None
    assert data['dest'] == 'battlefield'
    assert 'creature' in data['types']
    assert data['mv_bound_is_x'] is True
    assert data['max_mv'] is None


def test_hand_tutor_sentence_classifies_with_and_without_reveal():
    kind, *_ = classify_activation_effect(_HAND_SENTENCE + ".")
    assert kind is ActivationEffectKind.TUTOR_TO_HAND
    data = parse_activation_tutor(_HAND_SENTENCE)
    assert data is not None and data['dest'] == 'hand'
    assert 'sliver' in data['subtypes']
    plain = ("Search your library for a land card, put it into your hand, "
             "then shuffle")
    kind2, *_ = classify_activation_effect(plain + ".")
    assert kind2 is ActivationEffectKind.TUTOR_TO_HAND


def test_constraint_parses_supertype_subtype_color_and_fixed_mv_bound():
    data = parse_activation_tutor(
        "Search your library for a green creature card with mana value 3 "
        "or less, put it onto the battlefield, then shuffle")
    assert data is not None
    assert data['colors'] == ['G']
    assert data['max_mv'] == 3 and data['mv_bound_is_x'] is False

    # A tapped battlefield-entry rider is captured, not dropped.
    data2 = parse_activation_tutor(
        "Search your library for a legendary creature card, put it onto "
        "the battlefield tapped, then shuffle")
    assert data2 is not None
    assert 'legendary' in data2['supertypes']
    assert data2['tapped'] is True

    # Negated type constraint.
    data3 = parse_activation_tutor(
        "Search your library for a nonland creature card, put it onto the "
        "battlefield, then shuffle")
    assert data3 is not None and 'land' in data3['not_types']


def test_unsupported_shapes_stay_unclassified():
    """The tranche discipline: a rider the resolver cannot execute
    faithfully refuses the whole line, never half-executes it."""
    refused = [
        # Union type constraint — a choice shape the schema cannot hold.
        "Search your library for an artifact or creature card, put it "
        "onto the battlefield, then shuffle",
        # Multi-card search.
        "Search your library for two creature cards, put them onto the "
        "battlefield, then shuffle",
        # Trailing rider sentence after the shuffle.
        "Search your library for a creature card, put it onto the "
        "battlefield, then shuffle. It gains haste until end of turn",
        # A "with ..." rider that is not a mana-value bound.
        "Search your library for a land card with a basic land type, put "
        "it onto the battlefield, then shuffle",
        # Non-creature battlefield destination is outside the two shapes.
        "Search your library for a basic land card, put it onto the "
        "battlefield tapped, then shuffle",
    ]
    for sentence in refused:
        kind, *_ = classify_activation_effect(sentence + ".")
        assert kind is ActivationEffectKind.UNCLASSIFIED, (
            f"must stay visible-but-refused: {sentence!r} -> {kind}")


# ── cost: X and hybrid pips ───────────────────────────────────────────

def test_x_pip_in_activation_cost_parses_as_a_chargeable_count():
    cost = parse_activation_cost("{X}{B/G}, {T}, Sacrifice another creature")
    assert cost is not None
    assert cost.x_count == 1
    # Hybrid pip charges one generic (caster picks the colour) — the
    # spell-side convention of _parse_mana_symbols_to_cost.
    assert cost.mana.generic == 1 and cost.mana.cmc == 1
    assert cost.tap_self is True
    assert cost.sacrifice_type == "creature" and cost.sacrifice_another
    assert cost.unpayable == ()


def test_hybrid_pip_charges_one_generic_like_the_spell_side_convention():
    cost = parse_activation_cost("{2}{G/U}")
    assert cost is not None
    assert cost.mana.generic == 3 and cost.mana.cmc == 3
    assert cost.x_count == 0
    assert cost.unpayable == ()


# ── full-line parse: constraint data rides on the ability ─────────────

def test_full_line_parse_attaches_structured_tutor_data():
    oracle = ("{X}{B/G}, {T}, Sacrifice another creature: " + _BF_SENTENCE
              + ". Activate only as a sorcery.")
    abilities = parse_activated_abilities(oracle)
    assert len(abilities) == 1
    ab = abilities[0]
    assert ab.effect_kind is ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD
    assert ab.sorcery_speed_only is True
    assert ab.cost.x_count == 1
    assert ab.tutor_data is not None
    assert ab.tutor_data['mv_bound_is_x'] is True
    assert 'creature' in ab.tutor_data['types']


def test_db_toolbox_carrier_card_parses_to_the_battlefield_tutor_kind():
    """The mechanic must light up for a real DB card carrying it —
    fixture carrier from the Creatures Toolbox list."""
    t = _DB.get_card("Fiend Artisan")
    assert t is not None
    tutors = [a for a in (t.activated_abilities or [])
              if a.effect_kind
              is ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD]
    assert tutors, "the DB carrier's activated tutor line must classify"
    ab = tutors[0]
    assert ab.tutor_data['mv_bound_is_x'] is True
    assert ab.cost.x_count == 1
    assert ab.cost.sacrifice_type == "creature" and ab.cost.sacrifice_another
    assert ab.cost.unpayable == ()
    assert ab.sorcery_speed_only is True
