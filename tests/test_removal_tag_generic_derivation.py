"""Failing-first tests for generic ``removal`` tag derivation
(sweep PR F-3).

The OracleEffect-driven removal predicate at
``OracleTextParser.classify_card_role`` recognises canonical
``effect_type in ("damage","destroy","exile")`` cases. Real Modern
oracle text uses many phrasings that the parser fails to extract
into a single removal effect:

  - Multi-clause ETB: ``When this enters, exile up to one other
    target creature`` (Solitude)
  - Color-restricted: ``Exile target black or red permanent``
    (Celestial Purge)
  - Conditional clause: ``Metalcraft — ... exile that creature``
    (Dispatch)
  - Activated ability: ``{G}, Sacrifice this creature: Exile target
    noncreature artifact or noncreature enchantment`` (Haywire Mite)
  - Activated board-wipe: ``Sacrifice this artifact: Destroy each
    nonland permanent`` (Engineered Explosives, Ratchet Bomb)
  - Library-bottom wipe: ``Put all creatures on the bottom of their
    owners' libraries`` (Terminus)
  - Bounce-all: ``Return all artifacts target player owns to their
    hand`` (Hurkyl's Recall)
  - Sacrifice-all: ``Each player sacrifices all permanents...``
    (All Is Dust)
  - Toughness-reduction kill: ``Target creature gets -5/-5 until end
    of turn`` (Dismember)
  - Multi-type board wipe: ``Destroy each artifact, creature, and
    enchantment...`` (Wrath of the Skies)
  - Variable-X back-reference: ``...deals that much damage to that
    permanent`` (Galvanic Discharge)

Each is the same mechanic — a removal predicate text-based fallback
covers them all without naming any one card.

Negative anchors confirm cards that should NOT acquire removal:
Lightning Bolt (already removal via OracleEffect), Counterspell
(counterspell, not removal), Snapcaster Mage / Eternal Witness
(creature ETB-value, not removal), Cultivate (ramp).
"""
from engine.card_database import CardDatabase


def _tags(name: str) -> set:
    db = CardDatabase()
    template = db.cards.get(name)
    assert template is not None, f"Card not in DB: {name}"
    return set(template.tags)


# ──────────────────────────────────────────────────────────────────
# Cards previously needing TAG_OVERRIDES['removal'] — must remain
# removal-tagged after the override entries are pruned.
# ──────────────────────────────────────────────────────────────────

def test_solitude_etb_exile_is_removal():
    """When this creature enters, exile up to one other target creature."""
    assert "removal" in _tags("Solitude")


def test_dismember_toughness_reduction_is_removal():
    """Target creature gets -5/-5 — kills via toughness."""
    assert "removal" in _tags("Dismember")


def test_celestial_purge_color_restricted_exile_is_removal():
    """Exile target black or red permanent."""
    assert "removal" in _tags("Celestial Purge")


def test_dispatch_conditional_exile_is_removal():
    """Metalcraft — If you control three or more artifacts, exile
    that creature."""
    assert "removal" in _tags("Dispatch")


def test_haywire_mite_activated_exile_is_removal():
    """Sacrifice this creature: Exile target noncreature artifact or
    noncreature enchantment."""
    assert "removal" in _tags("Haywire Mite")


def test_engineered_explosives_activated_board_wipe_is_removal():
    """Sacrifice this artifact: Destroy each nonland permanent..."""
    assert "removal" in _tags("Engineered Explosives")


def test_terminus_library_bottom_is_removal():
    """Put all creatures on the bottom of their owners' libraries."""
    assert "removal" in _tags("Terminus")


def test_hurkyls_recall_bounce_all_is_removal():
    """Return all artifacts target player owns to their hand."""
    assert "removal" in _tags("Hurkyl's Recall")


def test_all_is_dust_sacrifice_all_is_removal():
    """Each player sacrifices all permanents they control that are
    one or more colors."""
    assert "removal" in _tags("All Is Dust")


def test_ratchet_bomb_activated_destroy_each_is_removal():
    assert "removal" in _tags("Ratchet Bomb")


def test_galvanic_discharge_x_damage_is_removal():
    """Choose target creature or planeswalker. ... deals that much
    damage to that permanent."""
    assert "removal" in _tags("Galvanic Discharge")


def test_wrath_of_the_skies_multi_type_board_wipe_is_removal():
    """Destroy each artifact, creature, and enchantment with mana
    value..."""
    assert "removal" in _tags("Wrath of the Skies")


def test_thraben_charm_destroy_target_is_removal():
    """Destroy target enchantment is one of three modes."""
    assert "removal" in _tags("Thraben Charm")


# ──────────────────────────────────────────────────────────────────
# Negative anchors — must NOT acquire removal via the new predicate.
# ──────────────────────────────────────────────────────────────────

def test_counterspell_is_not_removal():
    """Counter target spell — counterspell, not removal."""
    tags = _tags("Counterspell")
    assert "counterspell" in tags
    assert "removal" not in tags


def test_cultivate_is_ramp_not_removal():
    """Search your library for two basic land cards — ramp."""
    db = CardDatabase()
    if "Cultivate" in db.cards:
        tags = _tags("Cultivate")
        assert "ramp" in tags
        assert "removal" not in tags


def test_snapcaster_mage_is_etb_value_not_removal():
    """When this creature enters, target instant or sorcery card in
    your graveyard gains flashback — not removal."""
    tags = _tags("Snapcaster Mage")
    assert "etb_value" in tags
    assert "removal" not in tags


def test_eternal_witness_is_etb_value_not_removal():
    tags = _tags("Eternal Witness")
    assert "etb_value" in tags
    assert "removal" not in tags
