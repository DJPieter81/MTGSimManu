"""ETB 'put a land from hand onto the battlefield' must be credited in spell EV.

# Mechanic the test names

A creature whose ETB ability reads "When this creature enters, you may put a
land card from your hand onto the battlefield" accelerates mana by one land.
The simulator's `_project_spell` (`ai/ev_evaluator.py`) must credit this
effect as an increment to `my_total_lands` (and the equivalent forward mana
value) — otherwise the AI treats the creature as a vanilla body and may
score it at negative EV due to the casting cost penalty.

Representative Modern card: Arboreal Grazer (0/3 for {G}, ETB puts land from
hand tapped). With no ETB credit, the AI sees a 0-power creature whose cost
is -1 mana, yielding a projection delta of roughly -1.3 — so the AI scores it
negatively and deprioritises it. The correct model: casting it puts an extra
land into play, which accelerates mana by 1 land (worth ETB_LAND_FROM_HAND_MANA_VALUE).

# Class size

Multiple Modern cards carry the "put a land card from your hand onto the
battlefield" ETB idiom (e.g. Arboreal Grazer, Elvish Pioneer, other
creature-based land-drop enablers). The fix must apply to every such card
via oracle-pattern detection — no card names in engine or AI source.

# The bug, expressed without naming a card

If a creature's oracle text contains (in the same ETB ability clause)
  'when * enters', 'put', 'land', 'from your hand', 'onto the battlefield'
then `_project_spell` MUST produce a projected snapshot where
`my_total_lands > snap.my_total_lands` (land added to battlefield) and
`my_mana` is credited for the next-turn land value.

Pre-fix: `_project_spell` ignores the ETB clause entirely -- `my_total_lands`
is unchanged. The EV is negative because mana cost > body value.
Post-fix: `my_total_lands` increases by 1 and the mana value is credited,
making the EV non-negative for a creature that provides genuine ramp.
"""
from __future__ import annotations

import pytest

from ai.ev_evaluator import EVSnapshot, _project_spell
from engine.cards import CardInstance


def _ramp_snap() -> EVSnapshot:
    """Early-game baseline for ETB-land projection: 1 land in play, hand of 5,
    no creatures. The test only inspects `my_total_lands` and `my_mana` post-
    projection."""
    return EVSnapshot(
        my_life=20, opp_life=20,
        my_hand_size=5, opp_hand_size=5,
        my_mana=1, opp_mana=1,
        my_total_lands=1, opp_total_lands=1,
        turn_number=1,
    )


def _project_ramp_delta(card_db, name: str):
    """Cast `name` from hand and return (land_delta, mana_delta)."""
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=1, zone="hand",
    )
    snap = _ramp_snap()
    proj = _project_spell(card, snap, dk=None, game=None, player_idx=0)
    return proj.my_total_lands - snap.my_total_lands, proj.my_mana - snap.my_mana


class TestEtbLandFromHandProjection:
    """_project_spell must credit ETB 'put a land from hand onto battlefield'
    as an increase in my_total_lands (and its forward mana equivalent)."""

    def test_etb_land_from_hand_effect_contributes_to_spell_ev(self, card_db):
        """A creature whose ETB reads 'put a land card from your hand onto
        the battlefield' must increase `my_total_lands` in the projected
        snapshot.

        Pre-fix: `_project_spell` ignores the ETB land clause entirely --
        the ETB tag is present but no land is credited, so `my_total_lands`
        is unchanged (delta == 0) and the creature scores at negative EV.

        Post-fix: `my_total_lands` increases by 1, and `my_mana` is credited
        for the forward value of an extra land, making the cast worth its cost.

        Rule: oracle-detected ETB 'put land from hand onto battlefield' =>
        projected `my_total_lands` delta > 0.

        Representative card: Arboreal Grazer (0/3, {G}, ETB lands from hand).
        The mechanic generalises to Elvish Pioneer and any future card with
        the same ETB oracle pattern -- no card names in the fix.
        """
        land_delta, mana_delta = _project_ramp_delta(card_db, "Arboreal Grazer")

        assert land_delta > 0, (
            f"Arboreal Grazer (representative of 'ETB put land from hand' "
            f"mechanic) projected my_total_lands delta={land_delta}. "
            f"Expected > 0: the ETB puts a land from hand onto the battlefield, "
            f"so `my_total_lands` MUST increase in the projected snapshot. "
            f"Pre-fix: the 'etb_land_from_hand' tag is not detected by "
            f"`derive_tags_from_oracle` (engine/oracle_parser.py) and "
            f"`_project_spell` (ai/ev_evaluator.py) has no code path for this "
            f"ETB effect. Fix: add 'etb_land_from_hand' oracle tag and handle "
            f"it in `_project_spell` by incrementing `my_total_lands += 1`."
        )

    def test_etb_land_from_hand_ev_not_negative_with_land_in_hand(self, card_db):
        """A creature that ramps by one land should score non-negatively when
        the player can afford the casting cost.

        The original bug: Grazer at EV -1.3 because the AI models it as
        'pay 1 mana, get a 0/3 body' (mana cost > body value). With the land
        ETB credited, the 'pay 1 mana, get 0/3 + 1 extra land' package should
        be neutral-to-positive in EV.

        Rule: a creature whose casting cost is at most 1 AND whose ETB adds
        one land should project a non-negative EV delta at minimum.

        We verify indirectly via my_total_lands delta > 0 (the direct rule
        test above) and a second check that my_mana delta is not worse than
        the casting cost alone -- i.e. the net mana delta is at least
        partially recovered by the land-drop value.
        """
        tmpl = card_db.get_card("Arboreal Grazer")
        assert tmpl is not None, "Arboreal Grazer missing from card DB"
        card = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=1, zone="hand",
        )
        snap = _ramp_snap()
        proj = _project_spell(card, snap, dk=None, game=None, player_idx=0)

        # The land-drop ETB should at least partially offset the casting cost.
        # Pre-fix: my_mana delta = -1 (casting cost only, no ETB credit).
        # Post-fix: my_mana delta should be recovered by ETB_LAND_FROM_HAND_MANA_VALUE.
        mana_delta = proj.my_mana - snap.my_mana
        casting_cost = tmpl.cmc or 1

        # Net mana should not be as bad as pure cost (i.e. some recovery occurred).
        assert mana_delta > -casting_cost, (
            f"Arboreal Grazer projected my_mana delta={mana_delta} vs "
            f"casting_cost={casting_cost}. Expected delta > -{casting_cost}: "
            f"the ETB land-drop should partially or fully recover the cast "
            f"cost in mana terms (ETB_LAND_FROM_HAND_MANA_VALUE credits the "
            f"forward value of having one more land). "
            f"Pre-fix: no ETB recovery, so delta == -casting_cost == {-casting_cost}. "
            f"Post-fix: ETB_LAND_FROM_HAND_MANA_VALUE is added back to my_mana."
        )

    def test_etb_land_from_hand_tag_derived_from_oracle(self, card_db):
        """The 'etb_land_from_hand' tag must be present on any creature
        whose oracle text contains the 'put land from hand onto battlefield'
        ETB pattern.

        Pre-fix: `derive_tags_from_oracle` in engine/oracle_parser.py does
        not recognise this pattern -- the tag is absent.
        Post-fix: the tag is derived from oracle text automatically, so
        Arboreal Grazer and every future card with the same ETB phrasing
        carries the tag without needing an explicit TAG_OVERRIDE entry.

        Class scope: any creature with oracle text matching
          'when * enters' + 'put' + 'land' + 'from your hand' + 'onto the battlefield'
        in a single ability clause should carry this tag after card load.
        """
        tmpl = card_db.get_card("Arboreal Grazer")
        assert tmpl is not None, "Arboreal Grazer missing from card DB"
        tags = getattr(tmpl, 'tags', set())
        assert 'etb_land_from_hand' in tags, (
            f"Arboreal Grazer (oracle: {tmpl.oracle_text!r}) is missing the "
            f"'etb_land_from_hand' tag. Expected `derive_tags_from_oracle` "
            f"in engine/oracle_parser.py to recognise the pattern "
            f"'when * enters ... put ... land ... from your hand ... onto the "
            f"battlefield' and add the tag automatically. "
            f"Current tags: {tags}"
        )
