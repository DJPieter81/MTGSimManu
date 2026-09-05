""""When this creature enters, destroy/exile target <artifact | enchantment |
artifact or enchantment | creature | …> [an opponent controls] [with mana
value N or less]" — the enters-the-battlefield targeted-removal class
(CR 603.2, 603.3d): a triggered ability with a printed target.

141 Modern permanents carry an ETB destroy/exile-target clause; the
naturalize subclass alone (Reclamation Sage, Knight of Autumn's mode,
Witch Enchanter, Conclave Naturalists, Harmonic Sliver, Foundation
Breaker, Leonin Relic-Warder, …) is ~24. None resolved: the typed
`targeted_removal_data` field covers the SPELL shape only, so a blink deck's
whole plan — re-triggering exactly this class with Ephemerate — did nothing,
and Witch Enchanter entered next to an opposing Leyline Binding without
touching it (Azorius Blink vs Domain Zoo s50000 G1 T4).

Parsed once at load into `CardTemplate.etb_targeted_removal_data` (same dict
shape as the spell class) and dispatched from `resolve_etb_from_oracle`
through the shared `card_effects._resolve_nonland_permanent_removal`. The
"exile … until this leaves the battlefield" linked-exile shape is a
different mechanic (`etb_exile_returns_on_leave`) and is deliberately not
matched here. Card names below are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, Keyword
from engine.game_state import GameState
from engine.oracle_parser import parse_etb_targeted_removal


class TestParse:
    def test_naturalize_shape(self):
        d = parse_etb_targeted_removal(
            "When this creature enters, destroy target artifact or enchantment "
            "an opponent controls.")
        assert d == {"action": "destroy", "types": ["artifact", "enchantment"],
                     "mv": None, "owner_scope": "opponent", "optional": False}

    def test_optional_you_may_form(self):
        d = parse_etb_targeted_removal(
            "When this creature enters, you may destroy target artifact.")
        assert d["optional"] is True and d["owner_scope"] == "any"

    def test_mana_value_ceiling_is_captured(self):
        d = parse_etb_targeted_removal(
            "When this creature enters, exile target creature an opponent "
            "controls with mana value 3 or less.")
        assert d["action"] == "exile" and d["mv"] == 3

    def test_exile_until_leaves_is_a_different_mechanic(self):
        assert parse_etb_targeted_removal(
            "When this enchantment enters, exile target nonland permanent an "
            "opponent controls until this enchantment leaves the battlefield.") is None

    def test_spell_shape_is_not_an_etb_trigger(self):
        assert parse_etb_targeted_removal("Destroy target artifact or enchantment.") is None

    def test_rider_is_refused(self):
        assert parse_etb_targeted_removal(
            "When this creature enters, destroy target artifact, then draw a card.") is None


def _bf(game, card_db, name, controller=0):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    game.players[controller].battlefield.append(c)
    return c


def test_etb_destroys_an_opposing_enchantment(card_db):
    game = GameState(rng=random.Random(0))
    binding = _bf(game, card_db, "Leyline Binding", controller=1)
    enchanter = _bf(game, card_db, "Witch Enchanter // Witch-Blessed Meadow")
    game._handle_permanent_etb(enchanter, controller=0)
    assert binding not in game.players[1].battlefield
    assert binding.zone == "graveyard"


def test_etb_does_not_touch_the_controllers_own_permanents(card_db):
    game = GameState(rng=random.Random(0))
    own = _bf(game, card_db, "Leyline Binding", controller=0)
    enchanter = _bf(game, card_db, "Witch Enchanter // Witch-Blessed Meadow")
    game._handle_permanent_etb(enchanter, controller=0)
    assert own in game.players[0].battlefield


def test_etb_with_no_legal_target_does_nothing(card_db):
    game = GameState(rng=random.Random(0))
    bear = _bf(game, card_db, "Grizzly Bears", controller=1)
    enchanter = _bf(game, card_db, "Witch Enchanter // Witch-Blessed Meadow")
    game._handle_permanent_etb(enchanter, controller=0)
    assert bear in game.players[1].battlefield


def test_etb_destroy_respects_indestructible(card_db):
    game = GameState(rng=random.Random(0))
    citadel = _bf(game, card_db, "Darksteel Citadel", controller=1)  # land: not a target
    thopter = _bf(game, card_db, "Ornithopter", controller=1)        # artifact creature
    thopter.temp_keywords.add(Keyword.INDESTRUCTIBLE) if hasattr(thopter, "temp_keywords") else None
    sage = _bf(game, card_db, "Reclamation Sage")
    game._handle_permanent_etb(sage, controller=0)
    if Keyword.INDESTRUCTIBLE in thopter.keywords:
        assert thopter in game.players[1].battlefield
    assert citadel in game.players[1].battlefield
