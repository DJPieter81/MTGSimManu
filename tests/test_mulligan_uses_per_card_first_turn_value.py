"""Mulligan must use per-card ``first_turn_value`` instead of a flat
land-slack constant.

Reference: ``docs/history/audits/2026-04-26_storm_pro_audit.md`` F1.1 and
``docs/history/audits/2026-05-16_5panel_bo3_audit.md`` Combo F7 (D2
confirmed: Boros G2 P2 kept 4 lands + 3 spells).

The pre-existing logic at ``ai/mulligan.py`` accepted up to
``gp.mulligan_max_lands + 2`` lands whenever the hand contained ANY
``always_early`` card.  That ``+2`` is a flat slack constant — it
doesn't read the actual hand, so a Boros hand of {4 lands, Ragavan,
Goblin Bombardment, Seasoned Pyromancer} keeps even though Bombardment
needs creatures (T2 at earliest), Pyromancer is a 3-drop, and Ragavan
alone doesn't constitute enough early action to spend an extra land
draw on.

The fix replaces the flat slack with a per-card value sum:
``first_turn_value(card, hand_context)`` returns a small numeric
"value this card provides in the first two turns" derived from the
template's existing classifier tags (``storm_payoff``, ``cantrip``,
``card_advantage``, ``ritual``, ``discard``, ``cost_reducer``,
``removal``, ``threat``), ``mana_cost.cmc``, and creature stats.  No
card names; no deck names.

Class-size: this is the mulligan "land-slack" mechanism — applies to
every gameplan that declares ``always_early`` cards (Boros Energy,
Domain Zoo, Affinity, Izzet Prowess, Ruby Storm).  A fix that holds
for Boros lifts every aggro / tempo / combo deck that shares the
"deploy on T1 if possible" pattern.

Test names describe the rule (per-card first-turn value), not the
cards.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai.gameplan import create_goal_engine
from ai.mulligan import MulliganDecider
from ai.predicates import first_turn_value
from ai.strategy_profile import ArchetypeStrategy
from engine.card_database import CardDatabase
from engine.cards import CardInstance


REPO_ROOT = Path(__file__).resolve().parent.parent


def _hand_card(card_db, name: str, iid: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    return CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=iid, zone="hand",
    )


def _decider(deck_name: str, archetype: ArchetypeStrategy) -> MulliganDecider:
    goal = create_goal_engine(deck_name)
    return MulliganDecider(archetype, goal)


class TestPerCardFirstTurnValueDrivesLandSlack:
    """The flat ``+2`` land-slack constant is replaced by a sum of
    per-card ``first_turn_value`` over the hand.  A hand whose
    ``always_early`` card is the *only* early-impact card must
    mulligan when lands exceed the gameplan's ``mulligan_max_lands``;
    a hand with enough early impact across the curve still keeps."""

    def test_aggro_4_land_no_one_drop_mulligans(self, card_db):
        """Boros (max_lands=3) keeps a 4-land hand only when the
        hand has real early-turn impact beyond the always_early
        creature.  This hand has 4 lands + 1 always_early body
        (Ragavan) + 2 spells that don't fire on T1-T2 (Goblin
        Bombardment needs creatures + sac trigger; Seasoned
        Pyromancer is a 3-drop with a discard cost).  Total
        first_turn_value across spells is dominated by Ragavan
        alone, which the previous flat slack treated as "carte
        blanche for +2 extra lands" — wrong.  Audit F7 D2
        confirms: Boros G2 P2 kept this shape and stalled.
        """
        hand = [
            _hand_card(card_db, "Arid Mesa", iid=1),
            _hand_card(card_db, "Windswept Heath", iid=2),
            _hand_card(card_db, "Sacred Foundry", iid=3),
            _hand_card(card_db, "Plains", iid=4),
            _hand_card(card_db, "Ragavan, Nimble Pilferer", iid=5),
            _hand_card(card_db, "Goblin Bombardment", iid=6),
            _hand_card(card_db, "Seasoned Pyromancer", iid=7),
        ]
        decider = _decider("Boros Energy", ArchetypeStrategy.AGGRO)
        keep = decider.decide(hand, cards_in_hand=7)
        assert not keep, (
            f"Boros kept 4-land hand with low cumulative first_turn_value. "
            f"Reason: '{decider.last_reason}'.  This is the F7 D2 shape "
            f"— Ragavan alone shouldn't license +2 land slack when the "
            f"rest of the hand has no T1-T2 plays.  The slack must be "
            f"derived from per-card first_turn_value sum, not a flat +2."
        )

    def test_aggro_3_land_curve_keeps(self, card_db):
        """Regression: a Boros hand at exactly max_lands (3) with a
        proper 1/2/3-drop curve must keep.  This is the canonical
        keep shape — removing the flat slack must not over-mulligan
        in the at-floor case."""
        hand = [
            _hand_card(card_db, "Arid Mesa", iid=11),
            _hand_card(card_db, "Sacred Foundry", iid=12),
            _hand_card(card_db, "Plains", iid=13),
            _hand_card(card_db, "Guide of Souls", iid=14),     # 1-drop
            _hand_card(card_db, "Ocelot Pride", iid=15),       # 1-drop
            _hand_card(card_db, "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger", iid=16),  # 2-drop pw
            _hand_card(card_db, "Seasoned Pyromancer", iid=17),  # 3-drop
        ]
        decider = _decider("Boros Energy", ArchetypeStrategy.AGGRO)
        keep = decider.decide(hand, cards_in_hand=7)
        assert keep, (
            f"Boros mulliganed a curve-out 3-land 7-card hand. "
            f"Reason: '{decider.last_reason}'.  This is the at-floor "
            f"regression anchor — every always_early card is present, "
            f"the curve covers T1-T3 plays.  Removing the flat slack "
            f"must not regress the canonical keep."
        )

    def test_no_always_early_slack_constant_in_mulligan(self):
        """Structural check: the flat ``mulligan_max_lands + 2``
        expression must be removed from ``ai/mulligan.py``.  The
        slack is now per-card via ``first_turn_value``; the magic
        ``+ 2`` literal next to ``mulligan_max_lands`` is the exact
        anti-pattern the audit flagged."""
        src = (REPO_ROOT / "ai" / "mulligan.py").read_text()
        # Strip comments so the docstring/comment isn't matched.
        code_lines = [
            ln for ln in src.splitlines()
            if not ln.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        # The forbidden expression is ``mulligan_max_lands + 2`` (or
        # the equivalent with whitespace).  Detected via regex.
        pattern = re.compile(r"mulligan_max_lands\s*\+\s*2\b")
        match = pattern.search(code)
        assert match is None, (
            f"ai/mulligan.py still contains the flat land-slack "
            f"`mulligan_max_lands + 2` constant at offset "
            f"{match.start() if match else -1}.  This was the F1.1 / "
            f"F7 D2 finding — replace with `first_turn_value`-driven "
            f"per-card slack."
        )

    def test_first_turn_value_uses_classifier_tags(self, card_db):
        """``first_turn_value`` must read template tags (the
        oracle-derived classifier output from W0-A) and template
        primitives (CMC, creature stats), not card names.  Two
        concrete invariants:

        1. A storm_payoff / chain_fuel card (Ruby Storm payoff or
           ritual) gets *positive* first-turn value because Storm's
           T1 ritual or T1 cantrip lays the chain groundwork.
        2. A CMC>=4 non-ritual non-payoff spell with no early tags
           gets *zero* first-turn value (can't be cast on T1 or T2
           in any normal opener).
        """
        # Storm ritual: tag-driven positive value.
        manamorphose = _hand_card(card_db, "Manamorphose", iid=101)
        assert "ritual" in (manamorphose.template.tags or set()), (
            "Test setup: Manamorphose missing `ritual` tag — DB drift."
        )
        v_ritual = first_turn_value(manamorphose, hand_context={})
        assert v_ritual > 0, (
            f"Manamorphose (ritual + cantrip) returned "
            f"first_turn_value={v_ritual}; rituals are the canonical "
            f"T1-T2 fuel and must score above zero."
        )

        # CMC-4+ non-early spell: zero value.
        # Use a CMC>=4 non-cantrip non-ritual non-removal card.
        primeval_titan = card_db.get_card("Primeval Titan")
        if primeval_titan is not None:
            pt_inst = CardInstance(
                template=primeval_titan, owner=0, controller=0,
                instance_id=102, zone="hand",
            )
            v_titan = first_turn_value(pt_inst, hand_context={})
            assert v_titan == 0, (
                f"Primeval Titan (CMC 6, no early tags) returned "
                f"first_turn_value={v_titan}; a card uncastable on "
                f"T1-T2 must score zero."
            )

        # A cheap creature with creature stats: positive value.
        ragavan = _hand_card(card_db, "Ragavan, Nimble Pilferer", iid=103)
        v_rag = first_turn_value(ragavan, hand_context={})
        assert v_rag > 0, (
            f"Ragavan (CMC 1, creature, threat) returned "
            f"first_turn_value={v_rag}; 1-drop creatures are the "
            f"prototypical T1 play and must score above zero."
        )
