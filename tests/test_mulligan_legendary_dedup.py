"""P1-5: Mulligan scorer must treat duplicate legendary copies as dead.

CR 704.5j (the "legend rule"): if a player controls two or more legendary
permanents with the same name, that player chooses one and the rest are
put into their owners' graveyards. In hand, a stack of N copies of the
same legendary permanent therefore represents only ONE deployable card —
the rest will be discarded on resolution and produce no value.

The mulligan scorer previously counted every legendary copy as a live
card.  A hand of {3× Wan Shi Tong, 5 lands} would pass keep checks as a
"5 lands + 2 spells" hand (clearing the 5-land soft ceiling) when in
reality only one of the three legendary copies will ever resolve — the
hand is effectively 5 cards.

The fix is generic: count duplicate legendary copies and treat each
duplicate as a dead card (subtract from spell/cheap-spell counts) and
prefer bottoming duplicates over comparable unique alternatives. No card
names are referenced — `Supertype.LEGENDARY in card.template.supertypes`
is the sole signal.

This test uses Wan Shi Tong, Librarian (UU, Legendary Creature) because
the Azorius Control (WST) decklist runs four copies and the bug is
observable there, but the rule is mechanic-driven, not card-driven.
"""
from __future__ import annotations

import pytest

from ai.gameplan import create_goal_engine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.card_database import CardDatabase
from engine.cards import CardInstance, Supertype


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _make_card_in_hand(card_db, name, iid):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=iid, zone="hand",
    )


def _wst_decider():
    goal = create_goal_engine("Azorius Control (WST)")
    return MulliganDecider(ArchetypeStrategy.CONTROL, goal)


def test_legendary_card_marker_is_present(card_db):
    """Sanity: the Supertype.LEGENDARY marker is present on Wan Shi Tong.
    The fix relies entirely on this template flag — if the marker is
    missing, the dedup logic has nothing to bind to."""
    wan = card_db.get_card("Wan Shi Tong, Librarian")
    assert wan is not None
    assert Supertype.LEGENDARY in wan.supertypes, (
        "Wan Shi Tong must be flagged Legendary so the mulligan dedup "
        "rule can detect duplicate copies."
    )


def test_duplicate_legendaries_treated_as_dead_in_keep_decision(card_db):
    """Hand of 5 lands + 3× legendary creature must mulligan because two
    of the three copies are dead on resolution (legend rule).  The
    control hand of 5 lands + 1× legendary + 1× non-legendary spell
    has the same land count and the same nominal spell count, but
    none of its spells are duplicates — it must keep."""
    dup_hand = [
        _make_card_in_hand(card_db, "Island", iid=1),
        _make_card_in_hand(card_db, "Island", iid=2),
        _make_card_in_hand(card_db, "Plains", iid=3),
        _make_card_in_hand(card_db, "Plains", iid=4),
        _make_card_in_hand(card_db, "Plains", iid=5),
        _make_card_in_hand(card_db, "Wan Shi Tong, Librarian", iid=6),
        _make_card_in_hand(card_db, "Wan Shi Tong, Librarian", iid=7),
    ]
    decider = _wst_decider()
    keep_dup = decider.decide(dup_hand, cards_in_hand=7)

    control_hand = [
        _make_card_in_hand(card_db, "Island", iid=11),
        _make_card_in_hand(card_db, "Island", iid=12),
        _make_card_in_hand(card_db, "Plains", iid=13),
        _make_card_in_hand(card_db, "Plains", iid=14),
        _make_card_in_hand(card_db, "Plains", iid=15),
        _make_card_in_hand(card_db, "Wan Shi Tong, Librarian", iid=16),
        _make_card_in_hand(card_db, "Spell Snare", iid=17),
    ]
    decider2 = _wst_decider()
    keep_control = decider2.decide(control_hand, cards_in_hand=7)

    assert keep_control, (
        f"Control hand (5 lands + 1 legendary + 1 non-legendary "
        f"spell) should keep — it has two distinct spells.  Reason: "
        f"'{decider2.last_reason}'."
    )
    assert not keep_dup, (
        f"Duplicate-legendary hand (5 lands + 2× Wan Shi Tong) was "
        f"kept.  Reason: '{decider.last_reason}'.  Per the legend rule "
        f"only one copy resolves — the hand is effectively 5 lands + "
        f"1 spell, which fails the soft ceiling. The mulligan scorer "
        f"must subtract duplicate legendary copies from the live-spell "
        f"count."
    )


def test_choose_cards_to_bottom_prefers_duplicate_legendary(card_db):
    """When asked to bottom one card from a hand that contains a
    duplicate legendary, the scorer must prefer bottoming the duplicate
    over an equally-rated unique non-legendary card.  Without dedup,
    two copies of the same legendary score identically and the choice
    falls through to insertion order — the bug is that the duplicate
    is just as likely to be kept as bottomed."""
    # Hand designed so the duplicate Wan Shi Tong is the clearly worst
    # card to keep: there are already 4 lands + 2 spells, plus a third
    # Wan Shi Tong that is dead on resolution.
    hand = [
        _make_card_in_hand(card_db, "Island", iid=21),
        _make_card_in_hand(card_db, "Island", iid=22),
        _make_card_in_hand(card_db, "Plains", iid=23),
        _make_card_in_hand(card_db, "Plains", iid=24),
        _make_card_in_hand(card_db, "Wan Shi Tong, Librarian", iid=25),
        _make_card_in_hand(card_db, "Wan Shi Tong, Librarian", iid=26),
        _make_card_in_hand(card_db, "Counterspell", iid=27),
    ]
    decider = _wst_decider()
    bottom = decider.choose_cards_to_bottom(hand, count=1)

    assert len(bottom) == 1
    bottomed = bottom[0]
    assert bottomed.name == "Wan Shi Tong, Librarian", (
        f"choose_cards_to_bottom kept both copies of a legendary "
        f"creature instead of bottoming the dead duplicate.  Bottomed: "
        f"{bottomed.name}.  Per the legend rule, the second copy is "
        f"dead on resolution and should be the lowest-scored card to "
        f"keep."
    )


def test_unique_legendary_not_penalised(card_db):
    """Regression: a hand with exactly one copy of a legendary creature
    must NOT be penalised.  Only duplicates trigger the dead-card
    treatment."""
    hand = [
        _make_card_in_hand(card_db, "Island", iid=31),
        _make_card_in_hand(card_db, "Island", iid=32),
        _make_card_in_hand(card_db, "Plains", iid=33),
        _make_card_in_hand(card_db, "Plains", iid=34),
        _make_card_in_hand(card_db, "Plains", iid=35),
        _make_card_in_hand(card_db, "Wan Shi Tong, Librarian", iid=36),
        _make_card_in_hand(card_db, "Spell Snare", iid=37),
    ]
    decider = _wst_decider()
    keep = decider.decide(hand, cards_in_hand=7)
    assert keep, (
        f"Single-copy legendary hand must not be penalised.  Reason: "
        f"'{decider.last_reason}'."
    )
