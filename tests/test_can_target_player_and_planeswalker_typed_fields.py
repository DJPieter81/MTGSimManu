"""
CardTemplate.can_target_player / .can_target_planeswalker typed fields.

Oracle phrases that permit a spell to target a player:
  - "any target"          (Lightning Bolt, Grapeshot)
  - "target player"       (Thought Erasure, Geistflame)
  - "target opponent"     (Thoughtseize)

Oracle phrases that permit targeting a planeswalker:
  - "any target"          (Lightning Bolt)
  - "planeswalker"        (Hero's Downfall, Dreadbore)

Spells that say "target creature or planeswalker" (Galvanic Discharge,
Unholy Heat) can hit planeswalkers but NOT players — historically modelled
by an inline oracle check in ai/ev_player.py; these typed fields are the
oracle-parse-at-load-time replacement (CR 601.2c).

Fixture card names appear only in the module docstring, not in test names.
"""
import pytest
from engine.cards import CardTemplate, CardType
from engine.mana import ManaCost


def _template(oracle: str) -> CardTemplate:
    """Minimal CardTemplate with only oracle text populated."""
    return CardTemplate(
        name="__test__",
        oracle_text=oracle,
        card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=0),
        supertypes=[],
        subtypes=[],
        keywords=set(),
        abilities=[],
        color_identity=set(),
        produces_mana=[],
        enters_tapped=False,
        tags=set(),
    )


# ── can_target_player ────────────────────────────────────────────────────────

class TestCanTargetPlayer:
    def test_any_target_wording_permits_player(self):
        t = _template("Deal 3 damage to any target.")
        assert t.can_target_player is True

    def test_target_player_wording_permits_player(self):
        t = _template("Target player discards two cards.")
        assert t.can_target_player is True

    def test_target_opponent_wording_permits_player(self):
        t = _template("Target opponent sacrifices a creature.")
        assert t.can_target_player is True

    def test_creature_or_planeswalker_cannot_target_player(self):
        # CR 601.2c: this wording excludes players
        t = _template("Deal 3 damage to target creature or planeswalker.")
        assert t.can_target_player is False

    def test_pure_creature_targeting_cannot_target_player(self):
        t = _template("Destroy target creature.")
        assert t.can_target_player is False


# ── can_target_planeswalker ──────────────────────────────────────────────────

class TestCanTargetPlaneswalker:
    def test_any_target_wording_permits_planeswalker(self):
        t = _template("Deal 3 damage to any target.")
        assert t.can_target_planeswalker is True

    def test_explicit_planeswalker_wording_permits_planeswalker(self):
        t = _template("Destroy target creature or planeswalker.")
        assert t.can_target_planeswalker is True

    def test_creature_only_targeting_cannot_target_planeswalker(self):
        t = _template("Deal 3 damage to target creature.")
        assert t.can_target_planeswalker is False

    def test_player_only_targeting_cannot_target_planeswalker(self):
        t = _template("Target player discards two cards.")
        assert t.can_target_planeswalker is False
