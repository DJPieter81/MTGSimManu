"""Sweep PR C — Mox Opal metalcraft via generic oracle predicate.

Pins the rule that ``effective_produces_mana`` correctly handles
metalcraft-gated mana sources for ANY card (not just Mox Opal by
name). Detection: card has "metalcraft" keyword AND a "{T}: Add"
mana ability — both extracted from oracle text at parse time.

Failing-first per the sweep plan: write the test against the
current code path (which name-checks "Mox Opal"), verify it
passes today, then refactor to the generic predicate.

Reference: docs/proposals/2026-05_generic_predicate_sweep.md PR C.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardType
from engine.game_state import GameState
from engine.mana_payment import ManaPayment


def _put_in_play(game, card_db, name, controller=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


# ─── Mox Opal — metalcraft active ─────────────────────────────────────


def test_mox_opal_metalcraft_active_produces_any_color(card_db):
    """3+ artifacts on the battlefield → Mox Opal taps for WUBRG."""
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Memnite")
    _put_in_play(game, card_db, "Ornithopter")
    mox = _put_in_play(game, card_db, "Mox Opal")
    # 3 artifacts: Memnite + Ornithopter + Mox = metalcraft active.
    produced = ManaPayment.effective_produces_mana(game, 0, mox)
    assert set(produced) >= {"W", "U", "B", "R", "G"}, (
        f"Mox Opal under metalcraft must produce WUBRG. Got "
        f"{produced}."
    )


def test_mox_opal_metalcraft_inactive_produces_nothing(card_db):
    """<3 artifacts on the battlefield → Mox Opal can't tap."""
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Memnite")
    mox = _put_in_play(game, card_db, "Mox Opal")
    # 2 artifacts (Memnite + Mox) = metalcraft NOT active.
    produced = ManaPayment.effective_produces_mana(game, 0, mox)
    assert produced == [], (
        f"Mox Opal without metalcraft must produce nothing. Got "
        f"{produced}."
    )


# ─── Regression: cards with metalcraft but no mana production ────────


def test_metalcraft_keyword_alone_does_not_imply_mana(card_db):
    """Cards that mention metalcraft but don't have a {T}: Add
    ability (e.g. Galvanic Blast — extra damage on metalcraft)
    must not be recognized as mana sources."""
    blast = card_db.get_card("Galvanic Blast")
    if blast is None:
        pytest.skip("Galvanic Blast not in DB")
    oracle = (blast.oracle_text or "").lower()
    assert "metalcraft" in oracle, (
        "Sanity: Galvanic Blast oracle should mention metalcraft."
    )
    # Galvanic Blast is an instant — instances aren't on the
    # battlefield; effective_produces_mana wouldn't be called on
    # them. The assertion is about the template:
    # produces_mana should be empty.
    assert not blast.produces_mana, (
        f"Galvanic Blast must not be classified as a mana source. "
        f"produces_mana={blast.produces_mana}"
    )


# ─── Generic predicate — works for Mox Opal AND any future card ─────


def test_oracle_predicate_recognizes_mox_opal(card_db):
    """The post-refactor template flag (or oracle predicate)
    must recognize Mox Opal as a metalcraft-gated any-color
    mana source."""
    from engine.oracle_parser import is_metalcraft_mana_any_color

    mox_oracle = (
        "Metalcraft — {T}: Add one mana of any color. "
        "Activate only if you control three or more artifacts."
    )
    assert is_metalcraft_mana_any_color(mox_oracle), (
        "Predicate must return True for Mox Opal's oracle."
    )


def test_oracle_predicate_rejects_galvanic_blast(card_db):
    """Galvanic Blast mentions metalcraft but is NOT a mana
    source — the predicate must return False."""
    from engine.oracle_parser import is_metalcraft_mana_any_color

    blast_oracle = (
        "Galvanic Blast deals 2 damage to any target. "
        "Metalcraft — Galvanic Blast deals 4 damage instead if "
        "you control three or more artifacts."
    )
    assert not is_metalcraft_mana_any_color(blast_oracle)


def test_oracle_predicate_rejects_non_metalcraft_card(card_db):
    """Lightning Bolt has no metalcraft mention — predicate False."""
    from engine.oracle_parser import is_metalcraft_mana_any_color
    bolt = "Lightning Bolt deals 3 damage to any target."
    assert not is_metalcraft_mana_any_color(bolt)


# ─── Abstraction-ratchet — Mox Opal name check is gone ──────────────


def test_no_mox_opal_name_check_in_mana_payment():
    """The string `"Mox Opal"` must not appear in
    engine/mana_payment.py post-refactor."""
    from pathlib import Path
    src = (
        Path(__file__).parent.parent
        / "engine" / "mana_payment.py"
    ).read_text()
    assert 'name == "Mox Opal"' not in src, (
        "Post-refactor: Mox Opal name check must be removed from "
        "engine/mana_payment.py — replaced with the generic "
        "is_metalcraft_mana_any_color predicate."
    )
