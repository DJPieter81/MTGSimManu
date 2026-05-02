"""H_ACT_1 — creature-only removal must target the highest-threat
creature, not the highest-base-value creature.

The bug shape: ``ai/ev_player.py::_choose_targets`` line ~2452 used
``creature_value`` (raw clock impact) for creature-only removal target
selection. ``creature_value`` ignores oracle amplifiers (battle cry,
``for each ...`` scaling, attack triggers); the threat-aware sibling
``creature_threat_value`` / ``permanent_threat`` adds them.

Concrete failure mode (Affinity matchup): opponent has Memnite (1/1,
no triggers) AND Signal Pest (0/1 battle cry). The AI casts a
creature-only removal spell:

  creature_value(Memnite)     ≈ 1.15 / threat ≈ 1.15
  creature_value(Signal Pest) ≈ 1.00 / threat ≈ 2.15  ← amplifier

The base-value picker chooses Memnite (1.15 > 1.00). Signal Pest
survives and amplifies the next attack. Threat-aware picker chooses
Signal Pest correctly.

The burn-spell branch in the same function (line ~2376) already uses
``permanent_threat``. This test pins the consistency: the creature-
only removal branch must use the same threat-aware function.

See ``docs/diagnostics/2026-05-02_affinity_88pct_hypothesis_list.md``
H_ACT_1 for the broader investigation context.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _battlefield(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
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


def test_creature_removal_picks_battle_cry_amplifier_over_vanilla_body():
    """Signal Pest (0/1 battle cry) is a higher-priority removal
    target than Memnite (1/1 vanilla) because battle cry amplifies
    every other attacker. Threat-aware target selection must pick
    Signal Pest; raw-clock-impact selection picks Memnite — that
    is the bug this test pins."""
    db = CardDatabase()
    game = GameState(rng=random.Random(0))
    memnite = _battlefield(game, db, "Memnite", 1)
    signal_pest = _battlefield(game, db, "Signal Pest", 1)

    # Build an EVPlayer for player 0, holding a creature-only removal
    # spell. We use a real Modern card with a clean "destroy target
    # creature" oracle: "Cast Down" (without the legendary clause
    # masking it). Fall back to "Doom Blade" if absent.
    removal_name = None
    for candidate in ("Cast Down", "Doom Blade", "Murder", "Hero's Downfall"):
        t = db.get_card(candidate)
        if t is None:
            continue
        oracle_l = (t.oracle_text or "").lower()
        if "target creature" in oracle_l and "permanent" not in oracle_l:
            removal_name = candidate
            break
    if removal_name is None:
        pytest.skip("DB has no plain 'target creature' removal for this fixture")
    removal = _hand(game, db, removal_name, 0)
    # Force the 'removal' tag the AI uses to dispatch creature-only
    # removal targeting (templates may or may not auto-tag).
    if not isinstance(removal.template.tags, set):
        removal.template.tags = set()
    removal.template.tags.add("removal")

    from ai.ev_player import EVPlayer
    player = EVPlayer(player_idx=0, deck_name="Boros Energy")
    chosen = player._choose_targets(game, removal)

    assert chosen, "AI returned no target for a creature-only removal " \
                   "spell with two legal candidates"
    chosen_id = chosen[0]
    assert chosen_id == signal_pest.instance_id, (
        f"Creature-only removal targeted "
        f"{'Memnite' if chosen_id == memnite.instance_id else f'card {chosen_id}'}"
        f" instead of Signal Pest. Battle cry is an amplifier "
        f"(every other attacker gets +1/+0); the threat-aware target "
        f"picker must value the amplifier above a 1/1 vanilla body. "
        f"This pins ai/ev_player.py::_choose_targets line ~2452 to use "
        f"permanent_threat / creature_threat_value, not the base "
        f"creature_value."
    )
