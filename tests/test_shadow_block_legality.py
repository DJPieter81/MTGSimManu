"""Shadow is enforced at block legality (CR 702.28b).

"Shadow (This creature can block or be blocked by only creatures with
shadow.)" — a symmetric evasion keyword: a shadow creature and a
non-shadow creature can never be in a block together, in EITHER
direction. The engine modelled flying/reach/menace/protection but had
no shadow branch, so a grounded creature could illegally block a
shadow attacker (audit: Creatures Toolbox vs Dimir Midrange, s58004 —
Devoted Druid blocked Dauthi Voidwalker).

Class: 13 Modern-legal shadow creatures (the Dauthi / Soltari / il-
cycles), Dauthi Voidwalker being the pool-relevant one. Rule-phrased;
card names are fixture carriers for the keyword.
"""
from __future__ import annotations

import pytest

from engine.cards import CardInstance, Keyword
from engine.card_database import CardDatabase
from engine.combat_manager import CombatManager

_DB = CardDatabase()


def _creature(name):
    tmpl = _DB.get_card(name)
    assert tmpl is not None, f"missing {name}"
    return CardInstance(template=tmpl, owner=0, controller=0,
                        instance_id=1, zone="battlefield")


def test_shadow_keyword_is_parsed_onto_the_creature():
    dauthi = _creature("Dauthi Voidwalker")
    assert Keyword.SHADOW in dauthi.keywords, (
        "a creature with the Shadow keyword must carry Keyword.SHADOW"
    )


def test_non_shadow_creature_cannot_block_a_shadow_attacker():
    dauthi = _creature("Dauthi Voidwalker")   # shadow
    grounded = _creature("Memnite")           # no shadow
    assert CombatManager._can_block(dauthi, grounded) is False, (
        "a non-shadow creature may not block a shadow attacker (CR 702.28b)"
    )


def test_shadow_creature_cannot_block_a_non_shadow_attacker():
    grounded = _creature("Memnite")           # no shadow
    dauthi = _creature("Dauthi Voidwalker")   # shadow blocker
    assert CombatManager._can_block(grounded, dauthi) is False, (
        "a shadow creature may block ONLY shadow attackers (CR 702.28b)"
    )


def test_shadow_creature_may_block_a_shadow_attacker():
    attacker = _creature("Dauthi Voidwalker")  # shadow
    blocker = _creature("Dauthi Slayer")       # shadow
    assert CombatManager._can_block(attacker, blocker) is True, (
        "two shadow creatures may be in a block together"
    )


def test_two_grounded_creatures_are_unaffected_by_the_shadow_rule():
    attacker = _creature("Ragavan, Nimble Pilferer")
    blocker = _creature("Memnite")
    assert CombatManager._can_block(attacker, blocker) is True, (
        "the shadow rule must not affect ordinary blocks"
    )
