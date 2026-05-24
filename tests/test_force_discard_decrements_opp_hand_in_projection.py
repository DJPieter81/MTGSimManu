"""FORCED_DISCARD projection must decrement opp_hand_size + subtract life cost.

# Mechanic the test names

A spell tagged ``Tag.FORCED_DISCARD`` removes one card from the
opponent's hand on resolution.  ``_project_spell`` in
``ai/ev_evaluator.py`` is the AI's forward-projection of the board
state after a cast — for any FORCED_DISCARD spell the projection MUST
decrement ``projected.opp_hand_size`` by one, otherwise the AI sees
"lose your own card from hand, gain nothing" and the EV of every
discard spell collapses toward zero.

The same projection must also subtract the spell's self-imposed life
cost (Thoughtseize: "You lose 2 life") from ``projected.my_life``.
The amount is read from oracle text, not from a card-name lookup —
the same shape the existing extractors use for "deals N damage" /
"gain N life" / "pay N life".

# Class size

~150 Modern cards force an opponent to discard a non-land card from
hand without a cost the projection already handles separately.  The
audit-critical members (Thoughtseize, Inquisition of Kozilek, Duress,
Hymn to Tourach, Liliana of the Veil −2, Liliana, the Last Hope's
second ability via Liliana's tax, Davriel-style hand attack) all
share the same mechanic: opp_hand_size goes down by one.  Modelling
that as `_project_spell(card).opp_hand_size = snap.opp_hand_size - 1`
is the structural fix — any FORCED_DISCARD card future printings add
inherits the correct projection for free.

# The bug, expressed without naming a card

If a spell is tagged FORCED_DISCARD, projecting its cast must produce
an ``opp_hand_size`` strictly lower than the input snapshot's; and if
the same oracle text contains "you lose N life", the projected
``my_life`` must be N lower than the input's ``my_life``.  Pre-fix,
both deltas are zero — the projection only credits the cast itself
(via the existing ``my_hand_size - 1`` line) and ignores the spell's
effect on opp's hand entirely.

The strip advisor (``score_card_for_opponent_strip``) is correct;
this test pins the OTHER side of the bug — the cast-time projection.
M6 from the 2026-05-16 5-panel Bo3 audit.
"""
from __future__ import annotations

import pytest

from ai.ev_evaluator import EVSnapshot, _project_spell
from engine.cards import CardInstance


def _mid_snap(**overrides) -> EVSnapshot:
    """Mid-game baseline. Post-projection the test reads only the fields
    its assertion names — opp_hand_size and my_life.  Defaults give
    the projection room to decrement without bottoming-out at zero."""
    defaults = dict(
        my_life=20, opp_life=20,
        my_hand_size=5, opp_hand_size=5,
        my_mana=10, opp_mana=0,
        my_total_lands=10, opp_total_lands=0,
        turn_number=3,
    )
    defaults.update(overrides)
    return EVSnapshot(**defaults)


def _project(card_db, name: str, snap: EVSnapshot) -> EVSnapshot:
    """Cast `name` from a notional hand and return the projection."""
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=1, zone="hand",
    )
    return _project_spell(card, snap, dk=None, game=None, player_idx=0)


class TestForceDiscardDecrementsOppHandInProjection:
    """The projection of a FORCED_DISCARD-tagged spell must decrement
    opp_hand_size and (when oracle says so) subtract self-life cost."""

    def test_thoughtseize_projection_decrements_opp_hand_size(
            self, card_db):
        """A canonical FORCED_DISCARD spell on the audit cures list:
        projecting Thoughtseize against opp_hand_size=5 must yield
        opp_hand_size=4 in the projected snapshot.  Without this,
        Thoughtseize projects as "I lose my own card, gain nothing"
        — its EV settles near zero and the AI never casts it,
        leaving Dimir holding 3 copies while Storm goes off."""
        snap = _mid_snap(opp_hand_size=5)
        proj = _project(card_db, "Thoughtseize", snap)

        assert proj.opp_hand_size == snap.opp_hand_size - 1, (
            f"FORCED_DISCARD projection did not decrement opp_hand_size "
            f"(input={snap.opp_hand_size}, projected={proj.opp_hand_size}). "
            f"The _project_spell branch for Tag.FORCED_DISCARD must "
            f"subtract one from opp_hand_size — this is the structural "
            f"fix the audit names M6.  The strip advisor at "
            f"score_card_for_opponent_strip is unrelated; the bug is in "
            f"the cast-time projection, not in WHICH card gets stripped."
        )

    def test_thoughtseize_projection_subtracts_life_cost_from_my_life(
            self, card_db):
        """Thoughtseize's oracle reads 'You lose 2 life'.  The
        projection must subtract that life cost from my_life — pre-fix
        the projection ignores it and the AI sees the spell as free
        life-wise.  The amount comes from parsing the oracle text
        ("you lose N life"), not from a per-card constant."""
        snap = _mid_snap(my_life=20)
        proj = _project(card_db, "Thoughtseize", snap)

        assert proj.my_life < snap.my_life, (
            f"Thoughtseize projection did not subtract any life "
            f"(input={snap.my_life}, projected={proj.my_life}). "
            f"The FORCED_DISCARD projection branch must parse the "
            f"oracle 'you lose N life' clause and subtract N from "
            f"projected.my_life, otherwise the AI undervalues life "
            f"loss-paying interaction in low-life states."
        )
        # The printed amount is 2; assert the delta matches so the
        # parser cannot regress to "subtract any old amount and pass".
        assert proj.my_life == snap.my_life - 2, (
            f"Thoughtseize printed 'You lose 2 life'; projected delta "
            f"was {snap.my_life - proj.my_life}, expected 2.  The oracle "
            f"extractor read the wrong N (or none at all)."
        )

    def test_inquisition_projects_same_shape_as_thoughtseize(
            self, card_db):
        """Inquisition of Kozilek shares Tag.FORCED_DISCARD with
        Thoughtseize but has NO 'you lose N life' clause in its
        oracle.  The projection must still decrement opp_hand_size
        (the discard mechanic fires) but my_life is unchanged
        (no self-cost to parse).  This proves the fix is tag-driven,
        not card-name-driven — every FORCED_DISCARD card gets the
        opp_hand_size projection; only those whose oracle includes
        a 'you lose N life' clause pay life on top."""
        snap = _mid_snap(opp_hand_size=5, my_life=20)
        proj = _project(card_db, "Inquisition of Kozilek", snap)

        assert proj.opp_hand_size == snap.opp_hand_size - 1, (
            f"A non-Thoughtseize FORCED_DISCARD card must also "
            f"decrement opp_hand_size — the fix is tag-gated, not "
            f"card-name-gated.  input={snap.opp_hand_size}, "
            f"projected={proj.opp_hand_size}."
        )
        assert proj.my_life == snap.my_life, (
            f"Inquisition of Kozilek oracle has no 'you lose N life' "
            f"clause; the projection must leave my_life unchanged "
            f"(input={snap.my_life}, projected={proj.my_life}).  "
            f"A regression here would mean the extractor is firing "
            f"on the wrong predicate."
        )

    def test_non_discard_spell_projection_unchanged(self, card_db):
        """Negative anchor: a Counterspell-class spell has no
        FORCED_DISCARD tag.  Its projection must NOT touch
        opp_hand_size — the spell removes from the stack, not from
        hand.  Pinning this guarantees the new branch is gated on
        the tag, not on some accidental superset (e.g. all
        non-creature instants)."""
        snap = _mid_snap(opp_hand_size=5, my_life=20)
        proj = _project(card_db, "Counterspell", snap)

        assert proj.opp_hand_size == snap.opp_hand_size, (
            f"Counterspell has no FORCED_DISCARD tag; the "
            f"projection must not decrement opp_hand_size "
            f"(input={snap.opp_hand_size}, projected="
            f"{proj.opp_hand_size}). A regression here means the "
            f"new branch is firing on too wide a predicate."
        )
