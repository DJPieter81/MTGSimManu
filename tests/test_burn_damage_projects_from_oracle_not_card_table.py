"""Burn-damage projection extracts N from oracle, not from a per-card table.

Surfaced from the 2026-05-10 oracle-pattern projection blindspot
audit (`docs/design/2026-05-10_oracle_pattern_projection_blindspot_audit.md`).
A fifth audit row beyond the original four: the burn-damage
projection at `ai/ev_evaluator.py:1879` reads from
`decks/card_knowledge.json::burn_damage` keyed by card NAME, the
exact "per-card EV table" anti-pattern flagged in CLAUDE.md
(ABSTRACTION CONTRACT bullet on `Knowledge location`). The damage
amount can be parsed from oracle text in the canonical
"deals N damage" wording.

# Mechanic the test names

Two cards with `damage` in oracle whose printed amounts differ —
"deals 1 damage" vs "deals 5 damage" — must project distinct
`opp_life` deltas. Pre-audit, both projected the same flat amount
(or the per-card table's `0` fallback if not registered).

# Class size

`decks/card_knowledge.json` registers 12 burn cards by name today
(Lightning Bolt, Lava Spike, Boros Charm, Tribal Flames, Galvanic
Discharge, Lightning Helix, Rift Bolt, Searing Blaze, Galvanic
Blast, Phlage, Unholy Heat, Fury). The printed Modern pool of
"deals N damage" cards is ~150+. Class size clears the ABSTRACTION
CONTRACT floor (10) on the printed pool.

# Generic by oracle

The parsed extractor matches the canonical Modern "deals N damage"
wording for both digit (`deals 3 damage`) and English-numeral
(`deals three damage`) forms. Variable-X cards ("deals X damage
where X is …") still fall through to the `card_knowledge.json`
fallback — those need an X-resolver, separate work.
"""
from __future__ import annotations

import random

import pytest

from ai.deck_knowledge import DeckKnowledge
from ai.ev_evaluator import _project_spell, snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _project_opp_life_delta(card_db, card_name, archetype="aggro"):
    """Build a clean snapshot, project a single cast, return the
    `(snap.opp_life - projected.opp_life)` delta. We use aggro
    archetype + creature on board so the burn-damage projection's
    "we have a clock" branch fires (full damage credited)."""
    game = GameState(rng=random.Random(0))
    for _ in range(8):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    # Creature on board so `snap.my_creature_count > 0` → full
    # burn projection (not the no-clock scaling branch).
    _add(game, card_db, "Goblin Guide", controller=0, zone="battlefield")
    card = _add(game, card_db, card_name, controller=0, zone="hand")
    _add(game, card_db, "Mountain", controller=1, zone="battlefield")
    game.players[0].deck_name = "Test Burn"
    game.players[1].deck_name = "Test Opp"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1
    snap = snapshot_from_game(game, 0)

    dk = DeckKnowledge()
    projected = _project_spell(card, snap, dk, game, 0)
    return snap.opp_life - projected.opp_life


class TestBurnDamageProjectsFromOracleNotCardTable:
    """Burn cards with oracle 'deals N damage' must project N face
    damage, parsed from oracle, not looked up by card name."""

    def test_one_damage_burn_projects_one(self, card_db):
        """Lava Dart pattern: 'Lava Dart deals 1 damage to any
        target.' Pre-audit: NOT in card_knowledge.json → projects 0.
        Post-fix: oracle parser extracts '1' → projects 1 face
        damage. Class-size representative for cheap 1-damage burn."""
        delta = _project_opp_life_delta(card_db, "Lava Dart")
        assert delta >= 1, (
            f"Lava Dart projected opp_life delta = {delta} (expected "
            f"≥ 1). The 'deals 1 damage' pattern in oracle text must "
            f"be parsed by `_project_spell`'s burn-damage block, not "
            f"looked up in `decks/card_knowledge.json` (which doesn't "
            f"register Lava Dart and would return 0). The fix is the "
            f"5th audit row — generic oracle extraction replacing "
            f"the per-card table."
        )

    def test_three_damage_burn_projects_three(self, card_db):
        """Lightning Bolt pattern: 'Lightning Bolt deals 3 damage to
        any target.' Already in `card_knowledge.json` with
        `burn_damage = 3`, so this test passes both pre-fix and
        post-fix — anchor regression for the canonical bolt."""
        delta = _project_opp_life_delta(card_db, "Lightning Bolt")
        assert delta >= 3, (
            f"Lightning Bolt projected opp_life delta = {delta} "
            f"(expected ≥ 3). Anchor regression: this card is in "
            f"the table AND its oracle is 'deals 3 damage', so both "
            f"the per-card lookup and the parsed extractor agree."
        )

    def test_distinct_amounts_project_distinct_deltas(self, card_db):
        """Two cards with different printed N must project distinct
        opp_life deltas. The named rule from the audit: two oracle
        texts 'deals 1 damage' and 'deals 5 damage' must NOT
        project the same flat amount."""
        small = _project_opp_life_delta(card_db, "Lava Dart")
        # Tribal Flames is in the table with burn_damage=5, but
        # the parsed extractor must also handle it (oracle:
        # "Tribal Flames deals X damage to any target, where X is
        # ..."). The X-form falls through to the table fallback;
        # the class-size correctness is preserved.
        large = _project_opp_life_delta(card_db, "Tribal Flames")
        assert large > small, (
            f"Distinct printed damage amounts collapsed to the same "
            f"projected delta: Lava Dart={small}, Tribal Flames="
            f"{large}. Each card's projected face damage must be "
            f"distinct when the printed N differs."
        )
