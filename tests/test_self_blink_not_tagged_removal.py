"""Self-blink spells must not be tagged as creature removal.

A self-blink ("exile target creature YOU CONTROL, then return it" —
Ephemerate, Restoration Angel, Ghostly Flicker) is a flicker effect, not
removal. The tag builder applied removal tags per-effect while detecting
blink only at card level, so every self-blink also got
`destroy_target_creature` + `any_removal`. That mis-classifies flicker
spells as removal at the data layer, polluting any consumer that trusts the
tag.

Rule under test: a self-exile-and-return effect gets `blink` and NOT
`destroy_target_creature`; genuine targeted creature removal still gets
`destroy_target_creature`. No card names in the tagger.
"""
from __future__ import annotations

from engine.card_database import CardDatabase

_DB = CardDatabase()


def test_ephemerate_is_blink_not_removal():
    t = _DB.get_card("Ephemerate")
    assert t is not None
    assert "blink" in t.tags, "Ephemerate must be tagged blink"
    assert "destroy_target_creature" not in t.tags, (
        "a self-blink must NOT be tagged destroy_target_creature")
    assert "removal" not in t.tags, (
        "a self-blink must NOT be tagged as removal")


def test_targeted_creature_removal_still_tagged():
    """Negative pin: real targeted creature removal keeps its tag."""
    for name in ("Fatal Push", "Solitude"):
        t = _DB.get_card(name)
        if t is None:
            continue
        assert "destroy_target_creature" in t.tags or "removal" in t.tags, (
            f"{name} should still carry a removal tag")


def test_restoration_angel_etb_blink_not_removal():
    """Restoration Angel's ETB self-blink is not removal either."""
    t = _DB.get_card("Restoration Angel")
    if t is None:
        return
    assert "destroy_target_creature" not in t.tags
