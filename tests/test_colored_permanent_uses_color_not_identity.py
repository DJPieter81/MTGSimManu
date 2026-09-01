""""One or more colors" means COLOR, not color identity (CR 105.2a).

A "target permanent that's one or more colors" effect (Devourer of
Destiny / Ugin's cast trigger) must treat a devoid creature or a
colorless land as NOT colored, even though its color IDENTITY is
non-empty. The predicate fell back to color_identity when colors was
empty, so it exiled devoid Basking Broodscale and colorless dual lands
(audit: Broodscale Bloodchief vs Eldrazi Ramp, s58003).

Card names are fixture carriers; the rule is the color-vs-identity
distinction.
"""
from __future__ import annotations

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.oracle_resolver import _permanent_is_colored

_DB = CardDatabase()


def _perm(name):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    return CardInstance(template=t, owner=0, controller=0,
                        instance_id=1, zone="battlefield")


def test_devoid_creature_is_not_colored():
    # Basking Broodscale: devoid (colorless) but green color identity.
    assert _permanent_is_colored(_perm("Basking Broodscale")) is False


def test_colorless_land_is_not_colored():
    # A dual land is colorless; its color identity is not.
    assert _permanent_is_colored(_perm("Stomping Ground")) is False


def test_a_truly_colored_permanent_is_colored():
    assert _permanent_is_colored(_perm("Griselbrand")) is True


def test_a_colorless_artifact_is_not_colored():
    assert _permanent_is_colored(_perm("Memnite")) is False
