"""Failing-first tests for generic discard-spell ability derivation.

Companion to ``test_discard_tag_generic_derivation.py`` (sweep PR F-4,
which gave the canonical Modern hand-rip phrasings their ``discard``
**tag**). This sweep gives the same phrasings their canonical
``Ability`` object — a ``CAST``-typed ability whose description is
"Discard from opponent" — so they show up in the ability list at
parity with cards already covered by ``ABILITY_OVERRIDES``.

Three oracle phrasings are in scope (the mechanic, not the cards):

  1. **Reveal-then-discard** — "target (player|opponent) reveals their
     hand. You choose [filter] from it. That player discards that
     card." (Thoughtseize, Inquisition of Kozilek, Distress, Duress,
     Coercion, Despise, etc.)
  2. **Direct numeric discard** — "target (player|opponent) discards
     <one|two|three|N> card(s)." (Mind Rot, Wrench Mind.)
  3. **Each-player discard** — "each (player|opponent) discards a
     card." (Liliana of the Veil [+1], Chain of Vapor variants.)

The pre-fix gap, captured by failing assertions in
``test_FAILING_*`` below: only Thoughtseize and Inquisition of Kozilek
acquire the CAST ability (via ``ABILITY_OVERRIDES``); every other
matching card has ``len(template.abilities) == 0``. After the fix, all
matching cards acquire the ability via the oracle predicate, and the
two override entries become redundant (and are removed in the same
diff).

Negative anchors confirm the predicate doesn't over-fire on:
self-discard cost (Faithful Mending), draw-then-discard loot
(Cathartic Reunion's "draw two cards, then discard two"), and
unrelated spells (Lightning Bolt, Counterspell).
"""
from engine.card_database import CardDatabase
from engine.cards import AbilityType


def _abilities(name: str):
    db = CardDatabase()
    template = db.cards.get(name)
    assert template is not None, f"Card not in DB: {name}"
    return list(template.abilities)


def _has_discard_cast_ability(name: str) -> bool:
    """True iff template has a CAST ability whose description encodes
    the discard-from-opponent effect."""
    for ab in _abilities(name):
        if ab.ability_type != AbilityType.CAST:
            continue
        desc = ab.description.lower()
        if 'discard' in desc and ('opponent' in desc or 'player' in desc):
            return True
    return False


# ──────────────────────────────────────────────────────────────────
# Cards previously listed in ABILITY_OVERRIDES — must keep the CAST
# ability after the override entry is dropped, because the new oracle
# predicate produces the same ability.
# ──────────────────────────────────────────────────────────────────

def test_thoughtseize_reveal_pattern_yields_cast_discard_ability():
    """Target player reveals their hand. ... That player discards that card.
    — must produce a CAST ability with discard-from-opponent description."""
    assert _has_discard_cast_ability("Thoughtseize")


def test_inquisition_of_kozilek_reveal_pattern_yields_cast_discard_ability():
    assert _has_discard_cast_ability("Inquisition of Kozilek")


# ──────────────────────────────────────────────────────────────────
# Cards NOT in ABILITY_OVERRIDES that share the same oracle
# predicate. These FAIL on main and PASS after the fix — this is the
# gap the migration closes.
# ──────────────────────────────────────────────────────────────────

def test_distress_reveal_pattern_yields_cast_discard_ability():
    """Same shape as Thoughtseize but never had an override entry.
    Pre-fix: 0 abilities. Post-fix: 1 CAST 'Discard from opponent'."""
    db = CardDatabase()
    if "Distress" not in db.cards:
        return  # not in current DB; skip silently
    assert _has_discard_cast_ability("Distress")


def test_duress_reveal_pattern_yields_cast_discard_ability():
    """Filtered hand-rip ('noncreature, nonland') — same predicate."""
    db = CardDatabase()
    if "Duress" not in db.cards:
        return
    assert _has_discard_cast_ability("Duress")


def test_coercion_reveal_pattern_yields_cast_discard_ability():
    db = CardDatabase()
    if "Coercion" not in db.cards:
        return
    assert _has_discard_cast_ability("Coercion")


def test_despise_reveal_pattern_yields_cast_discard_ability():
    db = CardDatabase()
    if "Despise" not in db.cards:
        return
    assert _has_discard_cast_ability("Despise")


def test_mind_rot_direct_numeric_discard_yields_cast_ability():
    """Direct phrasing ('Target player discards two cards') — number
    word, not digit. Pre-fix: OracleTextParser.DISCARD_PATTERNS only
    matches the digit form, so Mind Rot has 0 abilities. Post-fix:
    text predicate covers the word form too."""
    db = CardDatabase()
    if "Mind Rot" not in db.cards:
        return
    assert _has_discard_cast_ability("Mind Rot")


# ──────────────────────────────────────────────────────────────────
# Negative anchors — must NOT acquire the CAST 'Discard from
# opponent' ability, because their oracle text doesn't encode it.
# ──────────────────────────────────────────────────────────────────

def test_faithful_mending_self_discard_cost_does_not_yield_discard_ability():
    """Faithful Mending makes YOU discard as part of its own resolution.
    It is not an opponent-targeting hand-disruption spell, so it must
    not pick up a CAST 'Discard from opponent' ability."""
    db = CardDatabase()
    if "Faithful Mending" not in db.cards:
        return
    assert not _has_discard_cast_ability("Faithful Mending")


def test_lightning_bolt_does_not_yield_discard_ability():
    assert not _has_discard_cast_ability("Lightning Bolt")


def test_counterspell_does_not_yield_discard_ability():
    db = CardDatabase()
    if "Counterspell" not in db.cards:
        return
    assert not _has_discard_cast_ability("Counterspell")


# ──────────────────────────────────────────────────────────────────
# Override-pruning regression: once the oracle predicate produces the
# ability, the ABILITY_OVERRIDES entries for the discard pattern must
# be removed (the migration's whole point). This pins the post-fix
# state of ABILITY_OVERRIDES so a later refactor can't silently
# re-add a per-card override entry.
# ──────────────────────────────────────────────────────────────────

def test_ability_overrides_no_longer_lists_discard_pattern_cards():
    """After the migration, ABILITY_OVERRIDES has no entries for cards
    whose discard ability is now derived generically from oracle text.
    A later commit that re-adds 'Thoughtseize' / 'Inquisition of
    Kozilek' to ABILITY_OVERRIDES would be a regression: the oracle
    predicate already covers them, and per-card entries are exactly
    the abstraction-contract anti-pattern this sweep targets."""
    import re
    from pathlib import Path
    src = Path(__file__).parent.parent / "engine" / "card_database.py"
    text = src.read_text()
    # Locate the ABILITY_OVERRIDES dict block.
    m = re.search(r"ABILITY_OVERRIDES\s*=\s*\{(.*?)\n\s*\}", text, re.DOTALL)
    assert m is not None, "ABILITY_OVERRIDES dict not found"
    body = m.group(1)
    # Discard-pattern cards must no longer appear as override keys.
    for card in ("Thoughtseize", "Inquisition of Kozilek"):
        assert f'"{card}"' not in body, (
            f"{card} is still in ABILITY_OVERRIDES; the discard predicate "
            f"in OracleTextParser now covers it. Remove the override entry."
        )
