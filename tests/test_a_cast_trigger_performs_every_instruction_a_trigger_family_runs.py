"""A permanent's cast trigger performs every instruction the other trigger
families run (CR 601.2i, 603.2).

"Whenever <player> casts a <kind> spell, <effect>" triggers when that player
casts a spell of that kind, and its instructions are performed as printed
(CR 608.2c): surveil, tokens, life, damage, counters, keyword actions, a
characteristic change on a target. "That player" is the caster. A body
that names the spell ("that spell") or carries an intervening "if" stays
refused, with its census row.

The defect: the cast family's strict shape held three closed shapes (a
plain draw, a rummage, the exile of a target permanent), so every other
cast trigger did nothing. Dragon's Rage Channeler (Izzet Prowess) never
surveilled and Glaring Fleshraker (Broodscale Bloodchief) never made its
Spawn.

Class: 49 typed cast hosts on pool cards join the 18 the carrier took.
Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(6):
        _add(game, card_db, "Mountain", 0)
        _add(game, card_db, "Island", 1)
    for p in (0, 1):
        for _ in range(6):
            _add(game, card_db, "Swamp", p, "library")
    return game


def _cast(game, card_db, name, controller=0, targets=None):
    spell = _add(game, card_db, name, controller, "hand")
    assert game.cast_spell(controller, spell, targets or [])
    return spell


def _watcher(game, oracle, controller=0):
    tmpl = CardTemplate(
        name="Cast Probe", card_types=[CardType.ENCHANTMENT],
        mana_cost=ManaCost(), supertypes=[], subtypes=[], power=None,
        toughness=None, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text=oracle, tags=set())
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(),
                        zone="battlefield")
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _surveils(game, monkeypatch):
    seen = []
    real = type(game).surveil

    def spy(self, player, n, *a, **kw):
        seen.append((player, n))
        return real(self, player, n, *a, **kw)
    monkeypatch.setattr(type(game), "surveil", spy)
    return seen


# ── The carrier ────────────────────────────────────────────────────────

def test_a_cast_trigger_that_surveils_surveils_on_a_spell_of_its_kind(
        card_db, monkeypatch):
    game = _game(card_db)
    _add(game, card_db, "Dragon's Rage Channeler", 0)
    seen = _surveils(game, monkeypatch)
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert seen == [(0, 1)]


def test_a_spell_of_another_kind_does_not_trigger_it(card_db, monkeypatch):
    game = _game(card_db)
    _add(game, card_db, "Dragon's Rage Channeler", 0)
    seen = _surveils(game, monkeypatch)
    _cast(game, card_db, "Memnite")
    assert seen == []


def test_a_cast_trigger_creates_the_token_its_creator_prints(card_db):
    """Glaring Fleshraker's Spawn, with its sacrifice-for-mana ability."""
    game = _game(card_db)
    _add(game, card_db, "Glaring Fleshraker", 0)
    _cast(game, card_db, "Memnite")              # a colorless spell
    tokens = [c for c in game.players[0].battlefield
              if getattr(c, "is_token", False)]
    assert [t.template.subtypes for t in tokens] == [["Eldrazi", "Spawn"]]
    assert tokens[0].template.sacrifice_mana_units


def test_that_player_is_the_caster(card_db):
    game = _game(card_db)
    _watcher(game, "Whenever an opponent casts a spell, that player loses "
             "1 life.")
    game.active_player = 1
    _cast(game, card_db, "Opt", controller=1)
    assert (game.players[0].life, game.players[1].life) == (20, 19)


def test_a_cast_trigger_gains_life_once_per_spell(card_db):
    game = _game(card_db)
    _watcher(game, "Whenever you cast an instant or sorcery spell, you gain "
             "1 life.")
    _cast(game, card_db, "Memnite")              # sorcery speed: first
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert game.players[0].life == 21


def test_a_cast_trigger_with_an_intervening_if_stays_refused(card_db):
    from engine.effect_carrier import cast_plan
    game = _game(card_db)
    probe = _watcher(game, "Whenever you cast a spell, if you control an "
                     "artifact, you gain 1 life.")
    assert cast_plan(probe.template.effects.front()) is None


def test_a_cast_trigger_limited_to_once_each_turn_stays_refused(card_db):
    """"This ability triggers only once each turn": no carrier counts a
    trigger's turn yet, so the host is not taken (it would trigger on every
    spell)."""
    from engine.effect_carrier import cast_plan
    game = _game(card_db)
    probe = _watcher(game, "Whenever you cast a spell, you gain 1 life. "
                     "This ability triggers only once each turn.")
    assert probe.template.effects.front()[0].trigger.once_each_turn
    assert cast_plan(probe.template.effects.front()) is None


def test_the_closed_shapes_are_still_taken(card_db):
    """The plain draw and the rummage (unit CT) and the exile of a target
    permanent (unit UG) keep their place in the family."""
    from engine.effect_carrier import cast_plan
    for name in ("Archmage of Runes", "Artist's Talent"):
        assert cast_plan(card_db.get_card(name).effects.front()) is not None


# ── Auditor (CR 603.2), restated from the spell's raw fields ──────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_cast_trigger_performed_for_a_spell_it_does_not_name(
        card_db, audit, monkeypatch):
    from engine import effect_carrier
    monkeypatch.setattr(effect_carrier, "cast_matches",
                        lambda *a, **k: True)                  # the defect
    game = _game(card_db)
    _add(game, card_db, "Dragon's Rage Channeler", 0)
    _cast(game, card_db, "Memnite")
    assert "603.2/cast_trigger_once" in _rules(audit)


def test_the_audit_is_silent_over_spells_of_every_kind(card_db, audit):
    game = _game(card_db)
    _add(game, card_db, "Dragon's Rage Channeler", 0)
    _add(game, card_db, "Glaring Fleshraker", 0)
    _cast(game, card_db, "Memnite")              # sorcery speed: first
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    game.active_player = 1
    _cast(game, card_db, "Opt", controller=1)
    assert "603.2/cast_trigger_once" not in _rules(audit)
