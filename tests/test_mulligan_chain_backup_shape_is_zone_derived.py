"""The "no cost reducer → need ritual + cantrip + finisher backup" keep rule
encodes the STORM chain shape (rituals make mana, cantrips find the next
spell, a storm finisher closes).  It is only meaningful for a gameplan
whose combo resource zone is "storm".  Applying it to every
combo-archetype deck with an `always_early` list shipped perfectly good
hands back: a creature-combo deck's 3-land hand with a mana dork, a
tutor and a tutor-creature was mulliganed for lacking a ritual (replay
Creatures Toolbox vs Dimir s50001 G2, mulled to 5), and every non-storm
combo gameplan with `always_early` (Amulet Titan, Goryo's, Hollow One,
Instant Reanimator) was under the same rule.

Card names below are fixture carriers only; the rule is zone-derived.
"""
from __future__ import annotations

from ai.gameplan import create_goal_engine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.cards import CardInstance

_BACKUP_REASON = "no cost reducer and no ritual+cantrip+finisher backup"


def _hand(card_db, names):
    out = []
    for i, n in enumerate(names, 1):
        t = card_db.get_card(n)
        assert t is not None, f"missing card in DB: {n}"
        out.append(CardInstance(template=t, owner=0, controller=0,
                                instance_id=i, zone="hand"))
    return out


def test_storm_chain_backup_rule_does_not_apply_to_non_storm_combo_gameplan(card_db):
    ge = create_goal_engine("Creatures Toolbox")
    assert ge.gameplan.always_early, "fixture needs an always_early list"
    d = MulliganDecider(ArchetypeStrategy.COMBO, ge)
    # The exact 7 from the replay: 3 lands, mana dork, tutor-creature,
    # X-tutor, one 4-drop.  No always_early card, no ritual.
    hand = _hand(card_db, ["Leyline of Abundance", "Fiend Artisan",
                           "Overgrown Tomb", "Underground Mortuary",
                           "Devoted Druid", "Dryad Arbor",
                           "Green Sun's Zenith"])
    keep = d.decide(hand, 7)
    assert d.last_reason != _BACKUP_REASON, d.last_reason
    assert keep, d.last_reason


def test_storm_zone_gameplan_still_requires_chain_backup(card_db):
    ge = create_goal_engine("Ruby Storm")
    d = MulliganDecider(ArchetypeStrategy.COMBO, ge)
    # 3 lands, no Medallion, a draw spell and two finishers but NO ritual:
    # the storm chain cannot start → the backup rule must still reject.
    hand = _hand(card_db, ["Mountain", "Mountain", "Scalding Tarn",
                           "Reckless Impulse", "Wish",
                           "Ral, Monsoon Mage // Ral, Leyline Prodigy",
                           "Grapeshot"])
    keep = d.decide(hand, 7)
    assert not keep
    assert d.last_reason == _BACKUP_REASON, d.last_reason
