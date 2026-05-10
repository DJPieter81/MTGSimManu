"""Failing-first tests for generic ``graveyard_hate`` tag derivation
(sweep PR F-6).

Pre-fix the graveyard-hate tag was applied only via two explicit
TAG_OVERRIDES entries (Thraben Charm, Relic of Progenitus). The
generic mechanism — "interact with the opponent's graveyard as a
disruption play" — has many oracle phrasings:

  - Zone-wipe: ``exile target player's graveyard``,
    ``exile each opponent's graveyard``, ``exile all graveyards``,
    ``exile any number of target players' graveyards``.
  - Single-card exile: ``exile target card from a graveyard``,
    ``exile up to two target cards from graveyards``.
  - Search-then-exile (Surgical Extraction): ``choose target card
    in a graveyard ... exile them``.
  - Replacement effect: ``If a card would be put into ... graveyard
    ... exile it instead``.
  - Bottom-of-library: ``put all the cards from their graveyard on
    the bottom of their library``.
  - Cast-from-graveyard restriction (Grafdigger's Cage): ``creature
    cards in graveyards and libraries can't enter the battlefield``.

Negative anchors confirm cards that touch graveyards in *other*
ways (reanimation, flashback-grant, self-mill) do NOT acquire the
tag — Past in Flames, Goryo's Vengeance, Eternal Witness,
Snapcaster Mage, Faithful Mending.
"""
from engine.card_database import CardDatabase


def _tags(name: str) -> set:
    db = CardDatabase()
    template = db.cards.get(name)
    if template is None:
        return None  # noqa: skipped tests handle missing
    return set(template.tags)


# ──────────────────────────────────────────────────────────────────
# Cards needing graveyard_hate via the new derivation
# ──────────────────────────────────────────────────────────────────

def test_relic_of_progenitus_is_graveyard_hate():
    """{T}: target player exiles a card from their graveyard.
    {1}, exile this artifact: exile all graveyards. Draw a card."""
    assert "graveyard_hate" in _tags("Relic of Progenitus")


def test_tormods_crypt_is_graveyard_hate():
    """{T}, sacrifice this artifact: exile target player's graveyard."""
    db = CardDatabase()
    if "Tormod's Crypt" in db.cards:
        assert "graveyard_hate" in _tags("Tormod's Crypt")


def test_leyline_of_the_void_replacement_is_graveyard_hate():
    """If a card would be put into an opponent's graveyard from
    anywhere, exile it instead."""
    db = CardDatabase()
    if "Leyline of the Void" in db.cards:
        assert "graveyard_hate" in _tags("Leyline of the Void")


def test_rest_in_peace_zone_wipe_is_graveyard_hate():
    """When this enchantment enters, exile all graveyards. If a card
    or token would be put into a graveyard from anywhere, exile it
    instead."""
    db = CardDatabase()
    if "Rest in Peace" in db.cards:
        assert "graveyard_hate" in _tags("Rest in Peace")


def test_bojuka_bog_zone_wipe_is_graveyard_hate():
    """When this land enters, exile target player's graveyard."""
    db = CardDatabase()
    if "Bojuka Bog" in db.cards:
        assert "graveyard_hate" in _tags("Bojuka Bog")


def test_nihil_spellbomb_zone_wipe_is_graveyard_hate():
    db = CardDatabase()
    if "Nihil Spellbomb" in db.cards:
        assert "graveyard_hate" in _tags("Nihil Spellbomb")


def test_scavenging_ooze_single_card_exile_is_graveyard_hate():
    """{G}: exile target card from a graveyard."""
    db = CardDatabase()
    if "Scavenging Ooze" in db.cards:
        assert "graveyard_hate" in _tags("Scavenging Ooze")


def test_endurance_bottom_of_library_is_graveyard_hate():
    """When this creature enters, up to one target player puts all
    the cards from their graveyard on the bottom of their library
    in a random order."""
    assert "graveyard_hate" in _tags("Endurance")


def test_surgical_extraction_choose_then_exile_is_graveyard_hate():
    """Choose target card in a graveyard ... search graveyard, hand,
    library ... and exile them."""
    db = CardDatabase()
    if "Surgical Extraction" in db.cards:
        assert "graveyard_hate" in _tags("Surgical Extraction")


def test_grafdiggers_cage_restriction_is_graveyard_hate():
    """Creature cards in graveyards and libraries can't enter the
    battlefield. Players can't cast spells from graveyards or
    libraries."""
    db = CardDatabase()
    if "Grafdigger's Cage" in db.cards:
        assert "graveyard_hate" in _tags("Grafdigger's Cage")


def test_thraben_charm_destroy_or_exile_graveyard_is_graveyard_hate():
    """Mode: exile any number of target players' graveyards."""
    assert "graveyard_hate" in _tags("Thraben Charm")


# ──────────────────────────────────────────────────────────────────
# Negative anchors — cards that touch graveyards but should NOT
# get the graveyard_hate tag (reanimation, flashback grant, etc.)
# ──────────────────────────────────────────────────────────────────

def test_past_in_flames_grants_flashback_not_hate():
    """Each instant and sorcery in your graveyard gains flashback —
    self-graveyard utility, not opponent disruption."""
    tags = _tags("Past in Flames")
    if tags is not None:
        assert "graveyard_hate" not in tags


def test_goryos_vengeance_reanimator_not_hate():
    """Return target legendary creature card from a graveyard to
    the battlefield — uses graveyard, not hate."""
    db = CardDatabase()
    if "Goryo's Vengeance" in db.cards:
        tags = _tags("Goryo's Vengeance")
        assert "graveyard_hate" not in tags


def test_eternal_witness_returns_card_not_hate():
    """Returns target card from your graveyard to your hand — not
    opponent disruption."""
    tags = _tags("Eternal Witness")
    assert "graveyard_hate" not in tags


def test_snapcaster_mage_grants_flashback_not_hate():
    tags = _tags("Snapcaster Mage")
    assert "graveyard_hate" not in tags


def test_faithful_mending_self_mill_not_hate():
    """Self-mill / flashback / discard — does not target opponent's
    graveyard."""
    tags = _tags("Faithful Mending")
    assert "graveyard_hate" not in tags


def test_mulldrifter_is_not_graveyard_hate():
    tags = _tags("Mulldrifter")
    assert "graveyard_hate" not in tags
