"""Failing-first tests for generic ``cantrip`` text fallback
(sweep PR F-5).

The OracleEffect-driven cantrip predicate at
``OracleTextParser.classify_card_role`` recognises
``effect_type == "draw"`` events. Multi-clause oracle text confuses
the effect parser:

  - ``You gain 2 life, draw two cards, then discard two cards.``
    (Faithful Mending) — parser extracts ``gain_life`` only.
  - ``Flying / When this creature enters, draw two cards. /
    Evoke {2}{U}`` (Mulldrifter) — parser extracts no draw effect
    because the trigger structure isn't recognised.

The fallback predicate is a strict regex: ``draw (a|one|two|three|
four|five|six|seven|N) cards?`` anywhere in oracle, with negative
guard against opponent-only draws (``target opponent draws``,
``each opponent draws``). Self-draw and shared-draw cards both
qualify; opponent-only draws (Howling Mine — though
asymmetric — symmetrically draws so the predicate fires) are
intentional cantrips.

Negative anchors: cards without any "draw" effect must remain
untagged — Lightning Bolt, Counterspell, Cultivate (ramp),
Mulldrifter and Faithful Mending are the canonical positives.
"""
from engine.card_database import CardDatabase


def _tags(name: str) -> set:
    db = CardDatabase()
    template = db.cards.get(name)
    assert template is not None, f"Card not in DB: {name}"
    return set(template.tags)


# ──────────────────────────────────────────────────────────────────
# Cards previously needing TAG_OVERRIDES['cantrip'] — must remain
# cantrip-tagged after override entries are pruned.
# ──────────────────────────────────────────────────────────────────

def test_faithful_mending_multi_clause_draw_is_cantrip():
    """You gain 2 life, draw two cards, then discard two cards."""
    tags = _tags("Faithful Mending")
    assert "cantrip" in tags


def test_mulldrifter_etb_two_cards_is_cantrip():
    """When this creature enters, draw two cards."""
    tags = _tags("Mulldrifter")
    assert "cantrip" in tags


def test_two_card_draw_is_card_advantage():
    """Drawing >= 2 cards is also card_advantage. Pin the rule on
    Mulldrifter and Faithful Mending."""
    assert "card_advantage" in _tags("Mulldrifter")
    assert "card_advantage" in _tags("Faithful Mending")


# ──────────────────────────────────────────────────────────────────
# Already auto-detected — pin to ensure the fallback doesn't
# regress the existing detector.
# ──────────────────────────────────────────────────────────────────

def test_preordain_is_cantrip():
    """Scry 2, then draw a card — already detected via OracleEffect."""
    tags = _tags("Preordain")
    assert "cantrip" in tags


def test_omnath_etb_draw_is_cantrip():
    """When Omnath enters, draw a card — already detected."""
    tags = _tags("Omnath, Locus of Creation")
    assert "cantrip" in tags


def test_wall_of_omens_is_cantrip():
    tags = _tags("Wall of Omens")
    assert "cantrip" in tags


# ──────────────────────────────────────────────────────────────────
# Negative anchors — must NOT acquire cantrip via the new predicate.
# ──────────────────────────────────────────────────────────────────

def test_lightning_bolt_is_not_cantrip():
    tags = _tags("Lightning Bolt")
    assert "cantrip" not in tags


def test_counterspell_is_not_cantrip():
    tags = _tags("Counterspell")
    assert "cantrip" not in tags


def test_snapcaster_mage_is_not_cantrip():
    """Grants flashback; does not draw cards itself."""
    tags = _tags("Snapcaster Mage")
    # Snapcaster Mage previously had cantrip from the override; now
    # it does NOT (no "draw" in oracle). Confirm it stays
    # uncantripped.
    assert "cantrip" not in tags


def test_eternal_witness_is_not_cantrip():
    """Returns a card from graveyard to hand — not a draw effect."""
    tags = _tags("Eternal Witness")
    assert "cantrip" not in tags


def test_thoughtseize_is_not_cantrip():
    """Discard, not draw."""
    tags = _tags("Thoughtseize")
    assert "cantrip" not in tags
