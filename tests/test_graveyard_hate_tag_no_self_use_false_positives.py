"""Failing-first tests for F-6 graveyard_hate over-tagging audit.

The F-6 predicate's zone-wipe branch matched too broadly: any
"exile target ... card ... from your graveyard" line was treated
as graveyard hate, but those phrasings describe SELF-graveyard
utility (escape cost, delve, flashback, activated abilities that
read from one's own graveyard) — not opponent disruption.

Similarly, the replacement branch ("exile it instead" + "graveyard"
in oracle) was too loose. Geth ("If this creature would leave the
battlefield, exile it instead") and Draconic Intervention ("If a
creature would die, exile it instead") use "exile it instead" to
intercept other zone transitions, not graveyard arrival.

This test pins the corrected behavior:
  - Self-graveyard utility cards (Ritual of the Returned, Hour of
    Eternity, Patchwork Crawler, Demilich, Soul Separator, etc.)
    must NOT be tagged graveyard_hate.
  - Reanimator with side replacement clauses (Geth, Draconic
    Intervention) must NOT be tagged either.
  - True graveyard hate (Relic of Progenitus, Bojuka Bog,
    Leyline of the Void, Rest in Peace, Faerie Macabre) must
    keep the tag.
"""
from engine.card_database import CardDatabase


def _has_gy_hate(name: str) -> bool:
    db = CardDatabase()
    t = db.cards.get(name)
    if t is None:
        return None
    return "graveyard_hate" in t.tags


# ──────────────────────────────────────────────────────────────────
# Self-graveyard utility — must NOT be graveyard_hate
# ──────────────────────────────────────────────────────────────────

def test_ritual_of_the_returned_self_graveyard_not_hate():
    """Exile target creature card from your graveyard — token-maker
    that USES your own graveyard, not opponent disruption."""
    db = CardDatabase()
    if "Ritual of the Returned" in db.cards:
        assert _has_gy_hate("Ritual of the Returned") is False


def test_hour_of_eternity_self_graveyard_not_hate():
    """Exile X target creature cards from your graveyard — zombie
    army from own graveyard, not hate."""
    db = CardDatabase()
    if "Hour of Eternity" in db.cards:
        assert _has_gy_hate("Hour of Eternity") is False


def test_midnight_ritual_self_graveyard_not_hate():
    db = CardDatabase()
    if "Midnight Ritual" in db.cards:
        assert _has_gy_hate("Midnight Ritual") is False


def test_patchwork_crawler_self_graveyard_not_hate():
    db = CardDatabase()
    if "Patchwork Crawler" in db.cards:
        assert _has_gy_hate("Patchwork Crawler") is False


def test_soul_separator_self_graveyard_not_hate():
    db = CardDatabase()
    if "Soul Separator" in db.cards:
        assert _has_gy_hate("Soul Separator") is False


# ──────────────────────────────────────────────────────────────────
# Reanimator with side replacement clause — must NOT be hate
# ──────────────────────────────────────────────────────────────────

def test_geth_replacement_clause_not_graveyard_hate():
    """Returns creature from graveyard, then grants a leave-the-
    battlefield exile replacement. The 'exile it instead' clause
    intercepts battlefield → exile, not graveyard arrival —
    NOT hate."""
    db = CardDatabase()
    if "Geth, Thane of Contracts" in db.cards:
        assert _has_gy_hate("Geth, Thane of Contracts") is False


def test_draconic_intervention_creature_death_replacement_not_hate():
    """Damage spell with finality-style 'exile if would die' clause
    — the 'exile it instead' intercepts creature death, not
    graveyard arrival. Not graveyard hate."""
    db = CardDatabase()
    if "Draconic Intervention" in db.cards:
        assert _has_gy_hate("Draconic Intervention") is False


# ──────────────────────────────────────────────────────────────────
# Real graveyard hate — must keep the tag
# ──────────────────────────────────────────────────────────────────

def test_relic_of_progenitus_still_hate():
    assert _has_gy_hate("Relic of Progenitus") is True


def test_leyline_of_the_void_still_hate():
    db = CardDatabase()
    if "Leyline of the Void" in db.cards:
        assert _has_gy_hate("Leyline of the Void") is True


def test_rest_in_peace_still_hate():
    db = CardDatabase()
    if "Rest in Peace" in db.cards:
        assert _has_gy_hate("Rest in Peace") is True


def test_faerie_macabre_plural_graveyards_still_hate():
    """`Exile up to two target cards from graveyards.` Plural,
    no article — must still match (single-card-generic branch)."""
    db = CardDatabase()
    if "Faerie Macabre" in db.cards:
        assert _has_gy_hate("Faerie Macabre") is True


def test_bojuka_bog_still_hate():
    db = CardDatabase()
    if "Bojuka Bog" in db.cards:
        assert _has_gy_hate("Bojuka Bog") is True


def test_scavenging_ooze_still_hate():
    db = CardDatabase()
    if "Scavenging Ooze" in db.cards:
        assert _has_gy_hate("Scavenging Ooze") is True
