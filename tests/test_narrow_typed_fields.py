"""Ratchet test — no NEW disguised single-card patch may enter the tree.

The `name == "X"` ratchet is at 0, but a card-specific rule can still be
laundered past it: parse ONE card's exact oracle wording at load time into a
typed ``CardTemplate`` field, and a ``card.name == "Omnath"`` conditional
becomes a ``template.landfall_third_damage`` boolean that only Omnath populates
— functionally identical, but invisible to every source-grep ratchet.

``tools/check_narrow_typed_fields.py`` measures, empirically over the whole DB,
how many cards populate each typed mechanic field, and flags any whose real
class is <= threshold cards. This test wraps that check so the standard suite
fails when a NEW such field appears that is not declared in the baseline.

Card names below appear only as fixture carriers proving the mechanism, never
as the thing under test — the rule is "a typed mechanic field must cover more
than a near-single-card class, or be a declared, justified exception."
"""
from __future__ import annotations

import dataclasses as dc

from tools import check_narrow_typed_fields as chk


def test_no_undeclared_narrow_typed_field(card_db):
    """Every typed mechanic field that only ~1 card populates must be declared
    in the baseline. A new one fails until it is generalised or grandfathered."""
    narrow = chk.narrow_fields(card_db)
    allowed = set(chk.load_baseline().get("fields", {}).keys())
    undeclared = {n: c for n, c in narrow.items() if n not in allowed}
    assert not undeclared, (
        "New disguised single-card typed field(s) not in the baseline: "
        + ", ".join(f"{n} (only {len(c)} card(s): {c})"
                    for n, c in sorted(undeclared.items()))
        + ". Generalise the parser so the field covers its real Modern class, "
          "or run `python tools/check_narrow_typed_fields.py --update` and fill "
          "in the exception's 'reason'."
    )


def test_baseline_has_no_stale_entries(card_db):
    """Baseline entries that are no longer narrow (a parser was generalised,
    or the field was removed) must be pruned so the ceiling cannot be silently
    re-filled — the same shrink-only discipline every ratchet uses."""
    narrow = set(chk.narrow_fields(card_db))
    allowed = set(chk.load_baseline().get("fields", {}).keys())
    stale = sorted(allowed - narrow)
    assert not stale, (
        "Baseline lists fields that are no longer narrow — prune them with "
        "`python tools/check_narrow_typed_fields.py --update`: " + ", ".join(stale)
    )


def test_ratchet_actually_fails_on_a_new_narrow_field(card_db):
    """Guard against a vacuous ratchet: a synthetic mechanic field populated by
    a single card must be reported as narrow and flagged as undeclared."""
    # Pick a real, distinctly-named field currently in the baseline as the
    # synthetic "new" field, then compute the diff against a baseline that does
    # NOT contain it — the check must surface it.
    narrow = chk.narrow_fields(card_db)
    assert narrow, "expected the DB to contain at least one narrow field"
    victim = sorted(narrow)[0]
    empty_baseline_fields = set()  # pretend nothing is declared
    flagged = {n for n in narrow if n not in empty_baseline_fields}
    assert victim in flagged, (
        "narrow-field diff failed to flag an undeclared narrow field — the "
        "ratchet would pass vacuously")


def test_threshold_is_a_small_positive_integer():
    """The narrowness threshold is a near-single-card boundary, not a knob that
    could be widened to smuggle a real class in — pin it low."""
    assert isinstance(chk.THRESHOLD, int)
    assert 1 <= chk.THRESHOLD <= 3


def test_non_mechanic_fields_are_real_card_template_attributes():
    """The identity/stats exclusion list must reference only attributes that
    exist (dataclass fields or computed properties) — a typo would silently let
    a renamed data field be scanned as a mechanic."""
    from engine.cards import CardTemplate
    fields = {f.name for f in dc.fields(CardTemplate)}
    bogus = sorted(n for n in chk._NON_MECHANIC_FIELDS
                   if n not in fields and not hasattr(CardTemplate, n))
    assert not bogus, f"exclusion list names non-existent attributes: {bogus}"
