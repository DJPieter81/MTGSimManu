"""An Equipment that statically grants a keyword ("Equipped creature has
trample and haste") must give that keyword to the equipped creature.

Rule (CR 301.5c / 613): a keyword grant from an attached Equipment is a
continuous effect on the equipped creature. Only the flat P/T grant class was
applied; keyword grants — ~200 Modern Equipment including Shadowspear
(trample+lifelink), Cori-Steel Cutter (trample+haste), Lavaspur Boots (haste),
the Sword cycle — were a silent no-op, so an equipped creature never gained
trample/haste/lifelink/etc. Haste in particular changes attack legality.

Card names are fixture carriers; the mechanic is the equipment keyword grant,
parsed once at load into equip_keyword_grant and applied in CardInstance.keywords.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, Keyword
from engine.game_state import GameState
from engine.oracle_parser import parse_equip_keyword_grant


class TestParseEquipKeywordGrant:
    def test_extracts_multiple_keywords(self):
        assert parse_equip_keyword_grant(
            "Equipped creature has trample and haste.") == frozenset(
            {"trample", "haste"})

    def test_grant_alongside_pt(self):
        assert parse_equip_keyword_grant(
            "Equipped creature gets +1/+1 and has first strike.") == frozenset(
            {"first_strike"})

    def test_conditional_grant_is_not_matched(self):
        # "as long as" is a state the sim cannot gate — do not over-grant.
        assert parse_equip_keyword_grant(
            "Equipped creature has flying as long as you control an Island."
        ) == frozenset()

    def test_no_equipped_clause_returns_empty(self):
        assert parse_equip_keyword_grant(
            "When this enters, draw a card.") == frozenset()


def _bf(game, card_db, name, controller=0):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    game.players[controller].battlefield.append(c)
    return c


def _equip(creature, equipment):
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


def test_equipment_grants_its_keywords_to_the_equipped_creature(card_db):
    game = GameState(rng=random.Random(0))
    bear = _bf(game, card_db, "Grizzly Bears")            # no keywords
    spear = _bf(game, card_db, "Shadowspear")             # trample + lifelink
    assert Keyword.TRAMPLE not in bear.keywords
    _equip(bear, spear)
    assert Keyword.TRAMPLE in bear.keywords
    assert Keyword.LIFELINK in bear.keywords
    # DamageSource hooks read off keywords — lifelink/deathtouch must follow.
    assert bear.has_lifelink is True


def test_haste_grant_is_visible(card_db):
    game = GameState(rng=random.Random(0))
    bear = _bf(game, card_db, "Grizzly Bears")
    cutter = _bf(game, card_db, "Cori-Steel Cutter")      # trample + haste
    _equip(bear, cutter)
    assert Keyword.HASTE in bear.keywords
    assert Keyword.TRAMPLE in bear.keywords


def test_pure_pt_equipment_grants_no_keyword(card_db):
    game = GameState(rng=random.Random(0))
    bear = _bf(game, card_db, "Grizzly Bears")
    axe = _bf(game, card_db, "Bonesplitter")              # +2/+0, no keyword
    before = set(bear.keywords)
    _equip(bear, axe)
    assert set(bear.keywords) == before, "a flat-P/T equipment grants no keyword"


def test_grant_gone_when_unequipped(card_db):
    game = GameState(rng=random.Random(0))
    bear = _bf(game, card_db, "Grizzly Bears")
    spear = _bf(game, card_db, "Shadowspear")
    _equip(bear, spear)
    assert Keyword.TRAMPLE in bear.keywords
    bear.instance_tags.discard(f"equipped_{spear.instance_id}")
    assert Keyword.TRAMPLE not in bear.keywords, "grant is a continuous effect"
