"""Failing-first tests for generic ``discard`` tag derivation
(sweep PR F-4).

The OracleEffect-driven discard predicate at
``OracleTextParser.classify_card_role`` recognises only
``effect_type == "discard"`` with target_type in {"opponent",
"each_opponent"}. The parser fails to extract a discrete discard
effect for the canonical Modern hand-rip phrasing:

  - "Target player reveals their hand. You choose a nonland card
    from it. That player discards that card." (Thoughtseize,
    Inquisition of Kozilek, Duress, Distress)
  - "Target player discards N cards." (Mind Rot, Wrench Mind)
  - "Each player discards a card." (Liliana of the Veil [+1])
  - "When this creature enters, target opponent reveals their hand
    and you choose a nonland card from it. Exile that card."
    (Brain Maggot, Tidehollow Sculler — functionally discard via
    exile-from-hand; the AI scores them under the same heading)

A two-branch text predicate covers all variants:
  1. Direct "(that player|target player|target opponent|each player|
     each opponent) discards?"
  2. Reveal-then-discard-or-exile "reveals their hand" + ("discards
     that card" | "exile that card")

Negative anchors confirm cards that should NOT acquire ``discard``:
Lightning Bolt (damage), Counterspell, Cultivate (ramp), Snapcaster
Mage (etb_value), Faithful Mending (self-discard as cost, not
effect on opponent), Mulldrifter.
"""
from engine.card_database import CardDatabase


def _tags(name: str) -> set:
    db = CardDatabase()
    template = db.cards.get(name)
    assert template is not None, f"Card not in DB: {name}"
    return set(template.tags)


# ──────────────────────────────────────────────────────────────────
# Cards previously needing TAG_OVERRIDES['discard'] — must remain
# discard-tagged after the override entries are pruned.
# ──────────────────────────────────────────────────────────────────

def test_thoughtseize_reveal_pattern_is_discard():
    """Target player reveals their hand. You choose a nonland card
    from it. That player discards that card."""
    tags = _tags("Thoughtseize")
    assert "discard" in tags
    assert "interaction" in tags


def test_inquisition_of_kozilek_reveal_pattern_is_discard():
    tags = _tags("Inquisition of Kozilek")
    assert "discard" in tags
    assert "interaction" in tags


def test_duress_reveal_pattern_is_discard():
    """Same hand-rip shape as Thoughtseize, different filter."""
    tags = _tags("Duress")
    assert "discard" in tags


def test_mind_rot_direct_target_discard():
    """Target player discards two cards — direct phrasing."""
    db = CardDatabase()
    if "Mind Rot" in db.cards:
        assert "discard" in _tags("Mind Rot")


def test_liliana_of_the_veil_each_player_discard():
    """[+1]: Each player discards a card."""
    db = CardDatabase()
    if "Liliana of the Veil" in db.cards:
        assert "discard" in _tags("Liliana of the Veil")


def test_brain_maggot_reveal_then_exile_is_discard():
    """When this creature enters, target opponent reveals their hand
    and you choose a nonland card from it. Exile that card. — the
    exile-from-hand variant of the discard pattern; AI scores it
    under the same heading."""
    db = CardDatabase()
    if "Brain Maggot" in db.cards:
        assert "discard" in _tags("Brain Maggot")


def test_tidehollow_sculler_reveal_then_exile_is_discard():
    db = CardDatabase()
    if "Tidehollow Sculler" in db.cards:
        assert "discard" in _tags("Tidehollow Sculler")


# ──────────────────────────────────────────────────────────────────
# Negative anchors — must NOT acquire discard via the new predicate.
# ──────────────────────────────────────────────────────────────────

def test_faithful_mending_self_discard_is_not_target_discard():
    """Faithful Mending makes YOU discard as an effect on yourself,
    not a discard targeting an opponent. Must not pick up the
    discard tag (which encodes hand-disruption interaction)."""
    tags = _tags("Faithful Mending")
    assert "discard" not in tags


def test_lightning_bolt_is_not_discard():
    tags = _tags("Lightning Bolt")
    assert "discard" not in tags


def test_counterspell_is_not_discard():
    tags = _tags("Counterspell")
    assert "discard" not in tags


def test_snapcaster_mage_is_not_discard():
    tags = _tags("Snapcaster Mage")
    assert "discard" not in tags


def test_cultivate_is_not_discard():
    db = CardDatabase()
    if "Cultivate" in db.cards:
        tags = _tags("Cultivate")
        assert "discard" not in tags
