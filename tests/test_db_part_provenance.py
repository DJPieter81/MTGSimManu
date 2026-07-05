"""DB part-file provenance guard.

Rule: every ``ModernAtomic_part*.json`` override layer must carry MTGJSON
export provenance (a ``meta`` wrapper with a ``version`` stamp).

Why this is a rules-level invariant and not bookkeeping: ``merge_db.py``
(and the test sidecar in ``tests/conftest.py``) apply part files in numeric
order with last-writer-wins and no validation.  A hand-authored part with
no provenance silently clobbers validated MTGJSON card data for every name
it contains — the 2026-07-05 Storm-vs-Dimir diagnostic
(``docs/diagnostics/2026-07-05_storm_dimir_canonical_gap.md``) traced a
14% canonical-DB win rate to exactly this: an unprovenanced part9 rewrote
30 staple cards (mana costs, card types, oracle clauses) with fabricated
pre-errata texts, and every downstream layer (oracle resolver, combo EV,
classifier pins) that was keyed to the real texts silently went dead.

These tests make that failure mode loud: an override layer that cannot say
which MTGJSON export it came from does not get merged.
"""
import glob
import json
import os
import re

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# MTGJSON version stamps look like "5.3.0+20260410" — a semver core plus a
# build date.  The date suffix is what lets a human audit which export a
# part came from, so we require the full shape, not just any string.
_MTGJSON_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\+\d{8}$")


def _part_files():
    parts = sorted(glob.glob(os.path.join(PROJECT_ROOT, "ModernAtomic_part*.json")))
    assert parts, "no ModernAtomic_part*.json found at repo root"
    return parts


def test_every_db_part_carries_mtgjson_provenance():
    """A DB override layer without an MTGJSON meta wrapper is untrusted
    data and must not participate in the merge."""
    missing = []
    for path in _part_files():
        with open(path) as f:
            payload = json.load(f)
        meta = payload.get("meta") if isinstance(payload, dict) else None
        version = (meta or {}).get("version", "")
        if not isinstance(version, str) or not _MTGJSON_VERSION_RE.match(version):
            missing.append(os.path.basename(path))
    assert not missing, (
        "DB part files without MTGJSON provenance (meta.version "
        "'X.Y.Z+YYYYMMDD'): "
        f"{missing}. Unprovenanced parts silently override validated card "
        "data during merge (see docs/diagnostics/"
        "2026-07-05_storm_dimir_canonical_gap.md). Regenerate the part from "
        "a real MTGJSON export via update_modern_atomic.py or remove it."
    )


def test_db_part_entries_are_wrapped_not_bare():
    """Parts must use the {"meta": ..., "data": ...} MTGJSON shape.

    A bare name->faces dict is the fingerprint of a hand-assembled part:
    it parses, it merges, and it carries zero provenance.  The merge
    tooling tolerates the bare shape for backward compatibility, so the
    test suite is the layer that rejects it.
    """
    bare = []
    for path in _part_files():
        with open(path) as f:
            payload = json.load(f)
        if not (isinstance(payload, dict) and "meta" in payload and "data" in payload):
            bare.append(os.path.basename(path))
    assert not bare, (
        f"DB part files in bare card-dict shape (no meta/data wrapper): {bare}"
    )
