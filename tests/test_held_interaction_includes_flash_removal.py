"""Held-interaction classification must include flash-speed removal.

`is_held_interaction` decides whether a card is something a player keeps
mana open to cast on the opponent's turn — it drives `_holdback_penalty`,
which reserves mana for interaction instead of tapping out on a
sorcery-speed play.

Bug: the predicate gated on `is_instant` ONLY, so flash removal that is
not a plain instant — evoke elementals (Solitude / Subtlety / Endurance)
and flash enchantment removal (Leyline Binding) — read as "not
interaction". A control deck holding Solitude therefore received the
proactive tap-out bonus, emptied its mana on its own turn, and could not
cast the removal during the opponent's combat. This is the defender
mana-availability defect behind the aggro-overperformance skew
(docs/diagnostics/2026-08-20_domain_zoo_overperformance_root_cause.md).

Rule under test: a card castable at instant speed (`is_instant` OR
`has_flash`) that carries an interaction tag is held interaction —
regardless of card type. Class size: every flash/evoke removal or
counter creature in Modern x every deck that holds one. No card names in
the predicate.
"""
from __future__ import annotations

from ai.card_classes import is_held_interaction


def test_plain_instant_removal_is_held_interaction(card_db):
    """Regression pin: an ordinary instant removal spell still counts."""
    bolt = card_db.get_card("Lightning Bolt")
    assert bolt is not None
    assert bolt.is_instant and "removal" in bolt.tags
    assert is_held_interaction(bolt) is True


def test_flash_evoke_creature_removal_is_held_interaction(card_db):
    """Solitude: flash evoke Elemental with removal — castable at instant
    speed via flash, so it is held interaction even though is_instant is
    False."""
    sol = card_db.get_card("Solitude")
    assert sol is not None
    assert not sol.is_instant, "fixture precondition: Solitude is a creature, not an instant"
    assert sol.has_flash and "removal" in sol.tags
    assert is_held_interaction(sol) is True, (
        "flash-speed removal must count as held interaction — otherwise "
        "holdback taps out the mana needed to cast it on the opp's turn"
    )


def test_flash_enchantment_removal_is_held_interaction(card_db):
    """Leyline Binding: flash enchantment removal — instant-speed answer,
    so it is held interaction."""
    lb = card_db.get_card("Leyline Binding")
    assert lb is not None
    assert not lb.is_instant
    assert lb.has_flash and "removal" in lb.tags
    assert is_held_interaction(lb) is True


def test_sorcery_speed_removal_is_not_held_interaction(card_db):
    """Negative pin: sorcery-speed removal (no instant, no flash) is NOT
    held interaction — you cannot hold it up on the opponent's turn."""
    pe = card_db.get_card("Prismatic Ending")
    assert pe is not None
    assert not pe.is_instant and not pe.has_flash
    assert is_held_interaction(pe) is False


def test_flash_creature_without_interaction_tag_is_not_held(card_db):
    """Negative pin: a flash body with no interaction tag (removal /
    counterspell / silence) is not held interaction — flash alone does
    not make a card an answer."""
    ambush = card_db.get_card("Ambush Viper") or card_db.get_card("Brazen Borrower")
    if ambush is None:
        return  # card not in DB; skip without failing
    # Only assert the negative if it genuinely lacks an interaction tag.
    if not ({"removal", "counterspell", "silence"} & set(ambush.tags)):
        assert is_held_interaction(ambush) is False
