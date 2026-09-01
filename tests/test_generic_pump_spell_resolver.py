"""A "target creature gets +N/+M until end of turn" combat trick pumps
its target — generically, without a per-card handler.

Monstrous Rage ("Target creature gets +2/+0 until end of turn. Create
a Monster Role token...") resolved as a COMPLETE no-op — no +2/+0, no
keyword — because only bespoke EFFECT_REGISTRY entries pumped (Mutagenic
Growth, Violent Urge) and Monstrous Rage had none (audit: Izzet Prowess
vs Amulet Titan, s59003).

Rule: the ~200-card "target creature gets +N/+M until end of turn [and
gains <keyword>]" class resolves via one generic resolver that applies
the parsed temporary bonus to the target. Card names are fixture
carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, Keyword
from engine.game_state import GameState


def _bf(game, card_db, name, controller):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    if t.is_creature:
        game.players[controller].creatures.append(c)
    return c


def _resolve(game, card_db, name, controller, target_id=None):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    spell = CardInstance(template=t, owner=controller, controller=controller,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    from engine.oracle_resolver import resolve_spell_from_oracle
    targets = [target_id] if target_id is not None else None
    return resolve_spell_from_oracle(game, spell, controller, targets)


def test_monstrous_rage_pumps_its_target(card_db):
    game = GameState(rng=random.Random(0))
    creature = _bf(game, card_db, "Ragavan, Nimble Pilferer", 0)  # 2/1
    base_p, base_t = creature.power, creature.toughness

    fired = _resolve(game, card_db, "Monstrous Rage", 0, creature.instance_id)

    assert fired, "the generic pump resolver did not fire"
    assert creature.power == base_p + 2, (
        f"Monstrous Rage grants +2/+0; power {creature.power}, "
        f"expected {base_p + 2}"
    )
    assert creature.toughness == base_t, "Monstrous Rage is +2/+0 (no toughness)"


def test_giant_growth_pumps_plus_three_three(card_db):
    game = GameState(rng=random.Random(0))
    creature = _bf(game, card_db, "Memnite", 0)  # 1/1
    _resolve(game, card_db, "Giant Growth", 0, creature.instance_id)
    assert creature.power == 1 + 3 and creature.toughness == 1 + 3


def test_pump_with_keyword_grants_the_keyword(card_db):
    """A pump that also grants a keyword ("and gains trample") applies
    the keyword to the target."""
    game = GameState(rng=random.Random(0))
    tmpl = card_db.get_card("Blossoming Defense")
    if tmpl is None or tmpl.pump_spell_keyword == "":
        pytest.skip("no keyword-granting pump fixture in this DB")
    creature = _bf(game, card_db, "Memnite", 0)
    _resolve(game, card_db, "Blossoming Defense", 0, creature.instance_id)
    kw = getattr(Keyword, tmpl.pump_spell_keyword.upper().replace(" ", "_"), None)
    if kw is not None:
        assert kw in creature.keywords
