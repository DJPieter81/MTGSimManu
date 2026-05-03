"""Failing-test contract for `DeckGameplan.mulligan_cmc_profile`.

The mulligan heuristic in `ai/mulligan.py` previously hard-coded the
"cheap" / "medium" CMC brackets as literal `<= 2` / `<= 3` checks.
Centralizing them as `gp.mulligan_cmc_profile` lets ramp / land-pinging
archetypes (Tron, Amulet Titan) raise their effective curve without
patching the mulligan code, and lets Phase 4's pydanticAI synthesizer
write per-deck profiles into the gameplan JSON.

The contract this test locks in:
  - The gp-aware mulligan path reads `gp.mulligan_cmc_profile` rather
    than referring to the literal CMC values.
  - Two gameplans with identical hands but different `medium` brackets
    produce different keep decisions.

Test setup:
  - 7 cards: 3 lands + 4 copies of a CMC-3 spell (Anguished Unmaking),
    which is also declared as `mulligan_keys`.
  - CONTROL archetype, cards_in_hand=7 → keep gate is `cheap_spells >= 2`
    via the key-card path.

Behaviour:
  - profile{medium=3}: 4 spells satisfy `<= 3` ⇒ cheap_spells=4 ⇒ KEEP
  - profile{medium=2}: 0 spells satisfy `<= 2` ⇒ cheap_spells=0 ⇒ MULLIGAN
"""
from __future__ import annotations

import pytest

from ai.gameplan import DeckGameplan, GoalEngine, Goal
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.card_database import CardDatabase
from engine.cards import CardInstance


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _card(card_db, name: str, iid: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=iid, zone="hand",
    )


def _build_decider(medium_cmc: int) -> MulliganDecider:
    """Construct a MulliganDecider whose gameplan declares a single
    `mulligan_keys` entry (Anguished Unmaking, CMC 3) and a
    `mulligan_cmc_profile` whose `medium` bracket is the test
    parameter. All other gp fields default."""
    gp = DeckGameplan(
        deck_name="TestProfile",
        goals=[],
        mulligan_keys={"Anguished Unmaking"},
        mulligan_min_lands=2,
        mulligan_max_lands=4,
        mulligan_cmc_profile={"cheap": 2, "medium": medium_cmc, "premium": 5},
        archetype="control",
    )
    engine = GoalEngine(gp)
    return MulliganDecider(ArchetypeStrategy.CONTROL, engine)


def test_mulligan_cmc_profile_medium_threshold_drives_keep(card_db):
    """Two gameplans with identical hands but different
    `mulligan_cmc_profile['medium']` produce different keep decisions —
    proves the mulligan code reads the profile rather than a literal."""
    hand = [
        _card(card_db, "Plains", 1),
        _card(card_db, "Plains", 2),
        _card(card_db, "Island", 3),
        _card(card_db, "Anguished Unmaking", 4),
        _card(card_db, "Anguished Unmaking", 5),
        _card(card_db, "Anguished Unmaking", 6),
        _card(card_db, "Anguished Unmaking", 7),
    ]

    decider_loose = _build_decider(medium_cmc=3)
    keep_loose = decider_loose.decide(hand, cards_in_hand=7)
    assert keep_loose is True, (
        f"medium=3: 4 CMC-3 spells should count as cheap_spells (cheap >= 2 "
        f"under the key-card path) and the hand should KEEP. "
        f"Got mulligan with reason: {decider_loose.last_reason}"
    )

    decider_tight = _build_decider(medium_cmc=2)
    keep_tight = decider_tight.decide(hand, cards_in_hand=7)
    assert keep_tight is False, (
        f"medium=2: 0 CMC-3 spells satisfy cheap (cheap < 2 under the "
        f"key-card path AND no critical_pieces, AND cheap_spells < 1 at "
        f"the generic check) so the hand should MULLIGAN. "
        f"Got keep with reason: {decider_tight.last_reason}"
    )


def test_mulligan_cmc_profile_default_matches_legacy_thresholds(card_db):
    """Sanity: the default `mulligan_cmc_profile` preserves the legacy
    literal thresholds (cheap=2, medium=3). A gameplan that doesn't set
    the field should behave identically to a gameplan that explicitly
    sets `{cheap: 2, medium: 3, premium: 5}`."""
    from ai.gameplan import DEFAULT_MULLIGAN_CMC_PROFILE
    assert DEFAULT_MULLIGAN_CMC_PROFILE == {"cheap": 2, "medium": 3, "premium": 5}

    gp_default = DeckGameplan(deck_name="Default", goals=[], archetype="control")
    assert gp_default.mulligan_cmc_profile == DEFAULT_MULLIGAN_CMC_PROFILE
