"""Drill-down — Construct token threat scoring must not double-credit.

Rule under test
---------------
Urza's Saga's chapter II creates a 0/0 Construct artifact creature
token with the static "+1/+1 for each artifact you control." The
engine's ``_dynamic_base_power`` (`engine/cards.py:397-398`) detects
this pattern via a strict regex and adds the artifact count to the
token's base power and toughness at battlefield read time.

Separately, ``creature_threat_value`` in ``ai/ev_evaluator.py:601``
adds a `+THREAT_SCALING_FUTURE_VP` (=3) virtual-power bonus whenever
the oracle text matches a loose `for each (artifact|creature|land|
card)` regex — intended for cards whose oracle hints at future
scaling but isn't yet captured in dynamic P/T.

For the Construct token specifically, the dynamic P/T ALREADY
captures the scaling (current artifact count). Adding virtual_power
on top double-counts the scaling contribution: a 5/5 Construct on a
5-artifact board gets evaluated as if it were 8/8, biasing the
opponent's removal-target selection and the controller's
threat-projection upward.

Phase L claim: a Construct's effective threat is over-stated by ~1-2
power-equivalents per turn it lives. With Affinity churning out
1-2 Constructs per game, the cumulative bias is non-trivial.

This test pins the rule: a creature whose dynamic P/T already
includes the per-artifact scaling must NOT receive a
THREAT_SCALING_FUTURE_VP bonus on top.

Discriminator: oracle matches the strict
`\\+\\d+/\\+\\d+\\s+for\\s+each\\s+artifact\\s+you\\s+control`
pattern (the same regex `_dynamic_base_power` uses). If yes, the
dynamic P/T already accounts for it; skip virtual_power.

Reference: /root/.claude/plans/now-lets-fix-affinity-keen-penguin.md
Phase 1C.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import creature_threat_value
from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardType
from engine.game_state import GameState


def _put_in_play(game, card_db, name, controller):
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


def _make_construct_token(game, controller):
    """Replicate the Construct token created by Urza's Saga Ch II."""
    return game.create_token(controller, "construct", count=1)[0]


def test_construct_dynamic_pt_scales_with_artifact_count(card_db):
    """Sanity baseline: card.power on a Construct token equals the
    artifact count (0/0 base + N from "+1/+1 for each artifact").
    Asserts the engine-side dynamic P/T is rules-correct."""
    game = GameState(rng=random.Random(0))
    # Drop 5 artifacts on the battlefield BEFORE creating the token,
    # so when we read the token's power, the dynamic computation
    # sees 5 artifacts (Memnite, Mox Opal, Ornithopter, Plating, Frogmite).
    _put_in_play(game, card_db, "Memnite", 0)
    _put_in_play(game, card_db, "Mox Opal", 0)
    _put_in_play(game, card_db, "Ornithopter", 0)
    _put_in_play(game, card_db, "Cranial Plating", 0)
    _put_in_play(game, card_db, "Frogmite", 0)
    # Now create the Construct token. There are 5 non-token artifacts
    # already + the token itself (=6 artifacts total), so the token's
    # power should be 0 + 6 = 6 (the token counts itself).
    construct = _make_construct_token(game, 0)
    assert construct is not None

    arts = [c.name for c in game.players[0].battlefield
            if CardType.ARTIFACT in c.template.card_types]
    assert len(arts) == 6, f"Expected 6 artifacts, got {len(arts)}: {arts}"

    p = construct.power
    assert p == 6, (
        f"Construct token power should be 6 (0 base + 6 artifacts). "
        f"Got {p}."
    )


def test_construct_threat_equals_clock_impact_only(card_db):
    """For a Construct token on the battlefield, ``creature_threat_value``
    must equal ``creature_clock_impact_from_card * CREATURE_VALUE_OUTER_SCALE``
    plus only the equipment-ceiling lift — there must be NO additive
    virtual_power contribution because dynamic P/T already captures
    the scaling.

    Pre-fix: the loose regex ``for each (artifact|creature|land|card)``
    matches Construct's oracle and adds THREAT_SCALING_FUTURE_VP=3
    virtual power, making the threat formula
    ``clock_impact(power+3) > clock_impact(power)``.

    Post-fix: the strict regex discriminator
    ``\\+\\d+/\\+\\d+ for each artifact you control`` matches the same
    oracle on battlefield, so virtual_power stays 0 and threat equals
    the clock-impact-from-card baseline (plus ceiling lift).
    """
    from ai.clock import creature_clock_impact_from_card
    from ai.ev_evaluator import _DEFAULT_SNAP, CREATURE_VALUE_OUTER_SCALE
    from ai.permanent_threat import _equipment_ceiling_for_creature

    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Memnite", 0)
    _put_in_play(game, card_db, "Mox Opal", 0)
    _put_in_play(game, card_db, "Ornithopter", 0)
    construct = _make_construct_token(game, 0)
    assert construct.power == 4

    # Decompose the threat:
    base = creature_clock_impact_from_card(construct, _DEFAULT_SNAP) \
        * CREATURE_VALUE_OUTER_SCALE
    ceiling_lift = _equipment_ceiling_for_creature(
        construct, game.players[0], game)
    expected_threat = base + ceiling_lift

    actual_threat = creature_threat_value(construct)

    delta = actual_threat - expected_threat
    assert abs(delta) < 0.5, (
        f"Construct threat must equal clock_impact_from_card + "
        f"ceiling_lift (no virtual_power on top, because dynamic P/T "
        f"already captures the scaling). Got actual_threat="
        f"{actual_threat:.2f}, expected_threat={expected_threat:.2f}, "
        f"delta={delta:.2f}. Non-zero delta indicates virtual_power "
        f"is double-counting the scaling already in card.power."
    )


def test_construct_in_hand_keeps_virtual_power(card_db):
    """Regression anchor: when a creature with the scaling oracle is
    NOT on battlefield (e.g. in hand or library — relevant for
    anticipatory threat scoring), the dynamic P/T does NOT fire
    (engine/cards.py:_dynamic_base_power early-returns to template.power).
    In that case virtual_power MUST still be applied — otherwise we
    under-rate hand-side threats.

    Constructs are token-only (never in hand), but a synthesized
    in-hand fixture verifies the discriminator works correctly. We
    use a fake CardTemplate that mirrors Construct's oracle but with
    zone='hand'.
    """
    from engine.cards import CardTemplate
    from engine.mana import ManaCost

    # Synthesized in-hand fixture mirroring Construct's oracle text.
    construct_t = CardTemplate(
        name="Synth Construct",
        card_types=[CardType.CREATURE, CardType.ARTIFACT],
        mana_cost=ManaCost(generic=0),
        power=0, toughness=0,
        oracle_text="This creature gets +1/+1 for each artifact you control.",
    )
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Memnite", 0)
    _put_in_play(game, card_db, "Mox Opal", 0)

    in_hand = CardInstance(
        template=construct_t, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="hand",
    )
    in_hand._game_state = game
    game.players[0].hand.append(in_hand)

    # In-hand: card.power = template.power = 0 (no dynamic). The loose
    # "for each artifact" regex matches → virtual_power should fire.
    threat_in_hand = creature_threat_value(in_hand)

    # Vanilla 0/0 in hand for baseline (no scaling oracle).
    vanilla_t = CardTemplate(
        name="Synth Vanilla",
        card_types=[CardType.CREATURE, CardType.ARTIFACT],
        mana_cost=ManaCost(generic=0),
        power=0, toughness=0, oracle_text="",
    )
    vanilla_h = CardInstance(
        template=vanilla_t, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="hand",
    )
    vanilla_h._game_state = game

    threat_vanilla = creature_threat_value(vanilla_h)

    # The synth Construct in hand should score HIGHER than the vanilla
    # 0/0 because virtual_power's anticipatory scaling fires.
    assert threat_in_hand > threat_vanilla, (
        f"In-hand creature with scaling oracle must score higher than "
        f"a vanilla 0/0 (virtual_power anticipates future scaling). "
        f"Got threat_in_hand={threat_in_hand:.2f}, "
        f"threat_vanilla={threat_vanilla:.2f}."
    )
