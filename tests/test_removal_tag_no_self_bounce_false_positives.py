"""Failing-first tests for sweep PR F-3 over-tagging audit.

The bounce-all branch of the generic removal predicate matched
"return all <permanent-type> ... to ... hand" too broadly. Self-bounce
cards (Part the Veil, Retract) where the owner is "you control"
were wrongly tagged ``removal`` even though they're tempo/utility
spells, not opponent disruption.

This test pins the corrected behavior: self-bounce cards must NOT
acquire the ``removal`` tag. Opponent-targeting bounce (Hurkyl's
Recall) must still be tagged removal.
"""
from engine.card_database import CardDatabase


def _tags(name: str) -> set:
    db = CardDatabase()
    t = db.cards.get(name)
    assert t is not None, f"Card not in DB: {name}"
    return set(t.tags)


def test_part_the_veil_self_bounce_is_not_removal():
    """Return all creatures you control to their owner's hand —
    self-bounce, not opponent disruption."""
    tags = _tags("Part the Veil")
    assert "removal" not in tags, (
        f"Self-bounce 'Part the Veil' must not be tagged removal; "
        f"oracle returns YOUR creatures, not the opponent's"
    )


def test_retract_self_bounce_is_not_removal():
    """Return all artifacts you control to their owner's hand —
    Affinity recursion tool, not opponent disruption."""
    tags = _tags("Retract")
    assert "removal" not in tags


def test_hurkyls_recall_opponent_bounce_is_still_removal():
    """Regression anchor: bounce-all targeting opponent's permanents
    must remain removal-tagged after the predicate tightens."""
    tags = _tags("Hurkyl's Recall")
    assert "removal" in tags


def test_lightning_bolt_is_still_removal():
    """Regression anchor: damage-target removal must remain tagged."""
    assert "removal" in _tags("Lightning Bolt")


def test_supreme_verdict_is_still_removal():
    """Regression anchor: board-wipe must remain tagged."""
    assert "removal" in _tags("Supreme Verdict")
