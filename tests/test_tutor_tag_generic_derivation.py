"""Failing-first tests for generic ``tutor`` tag derivation.

The ``tutor`` tag identifies a card whose effect is "search your
library for a non-basic-land target." Pre-fix the detector recognised
only four canonical phrasings:

  - "search your library for a card"
  - "search your library for a creature"
  - "search your library for an instant"
  - "search your library for a sorcery"

Real-world Modern oracle text uses many more variants:
  - "search your library for up to four cards" (Gifts Ungiven)
  - "search your library for a nonlegendary card" (Unmarked Grave)
  - "search your library for an Equipment card" (Stoneforge Mystic)
  - "search your library for an artifact card" (Trinket Mage / Trophy Mage)
  - "search your library for an enchantment card" (Idyllic Tutor)

Each of the four cards covered by the TAG_OVERRIDES dict is in this
class; a single relaxed predicate replaces all four overrides. The
generic predicate also picks up several dozen Modern printings beyond
the override list — class size comfortably above the 10-card floor.

Negative anchor: ``Rampant Growth``-style cards search for *basic
lands* and must NOT acquire ``tutor`` (they are ``ramp``). The
basic-land path is structurally distinct (driven by
``OracleEffect.target_type == 'search_land'``) so the text-match
predicate is safe even without an explicit basic-land filter.
"""
from engine.card_database import CardDatabase


def _tags(name: str) -> set:
    db = CardDatabase()
    template = db.cards.get(name)
    assert template is not None, f"Card not in DB: {name}"
    return set(template.tags)


# ──────────────────────────────────────────────────────────────────
# Cards previously tagged via TAG_OVERRIDES — must remain tutor-tagged
# after the override entry is removed.
# ──────────────────────────────────────────────────────────────────

def test_gifts_ungiven_is_tutor_via_oracle():
    """Search for *up to four cards* — current detection misses
    because of the "up to" quantifier and plural target.
    """
    assert "tutor" in _tags("Gifts Ungiven")


def test_unmarked_grave_is_tutor_via_oracle():
    """Search for a *nonlegendary* card — qualified target."""
    assert "tutor" in _tags("Unmarked Grave")


def test_stoneforge_mystic_is_tutor_via_oracle():
    """Search for an *Equipment* card — type-qualified target."""
    assert "tutor" in _tags("Stoneforge Mystic")


# ──────────────────────────────────────────────────────────────────
# Cards already passing — pinning anchors so the relaxation does not
# regress existing classifications.
# ──────────────────────────────────────────────────────────────────

def test_canonical_search_for_a_card_is_tutor():
    """The four canonical phrasings already detected — pin them.
    Demonic Tutor is the platonic example; the project DB may not
    include it, so try a few cards likely to be present."""
    db = CardDatabase()
    found = False
    for canonical in ("Demonic Tutor", "Diabolic Tutor", "Worldly Tutor"):
        if canonical in db.cards and "tutor" in db.cards[canonical].tags:
            found = True
            break
    if not found:
        # Fallback: any card with "search your library for a creature"
        # in oracle and the tutor tag confirms the canonical path works.
        for n, t in db.cards.items():
            oracle = (t.oracle_text or "").lower()
            if "search your library for a creature" in oracle:
                assert "tutor" in t.tags, (
                    f"Canonical creature-tutor phrasing on {n} did not "
                    f"produce 'tutor' tag — regression in baseline detector"
                )
                found = True
                break
    assert found, "No canonical-phrasing tutor card found in DB"


# ──────────────────────────────────────────────────────────────────
# Negative anchor — basic-land searches must remain ``ramp``, not
# ``tutor``. The text-match relaxation must not catch these.
# ──────────────────────────────────────────────────────────────────

def test_basic_land_search_is_not_tutor():
    """Rampant Growth / Cultivate / Search for Tomorrow — these
    are ramp, NOT tutor. The basic-land path is driven by
    OracleEffect targeting and is structurally distinct from the
    tutor predicate.
    """
    db = CardDatabase()
    for ramp_card in ("Rampant Growth", "Cultivate", "Kodama's Reach"):
        if ramp_card in db.cards:
            tags = db.cards[ramp_card].tags
            # Must be ramp (positive anchor) and must NOT be tutor
            # (the predicate must exclude basic-land searches).
            assert "ramp" in tags, (
                f"{ramp_card} lost its ramp tag — regression"
            )
            # tutor on a basic-land search is wrong; AI scoring would
            # treat the ramp spell as a finisher-access card.
            assert "tutor" not in tags, (
                f"{ramp_card} acquired tutor tag — basic-land search "
                f"must not be classified as tutor"
            )


def test_class_size_above_floor():
    """The generic tutor predicate must apply to >= 10 distinct
    cards in the DB, satisfying the abstraction-contract class-size
    floor. Pre-fix the canonical phrases match a tight set; post-fix
    the relaxed predicate extends substantially.
    """
    db = CardDatabase()
    tutor_count = sum(1 for t in db.cards.values() if "tutor" in t.tags)
    assert tutor_count >= 10, (
        f"Tutor class size below abstraction-contract floor: {tutor_count}"
    )
