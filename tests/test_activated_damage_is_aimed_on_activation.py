"""An activated damage ability is aimed when it is activated (CR 602.2b).

CR 602.2b: announcing an activated ability includes choosing its targets
(as for a spell, CR 601.2c). "~ deals N damage to any target" names a target
-- a creature, a planeswalker or a player (CR 115.4) -- so the ability carries
that requirement from load, the AI chooses the recipient when it activates,
and the engine checks the declared target like a spell's.
"""
from __future__ import annotations

import random

import pytest

from engine import rules_audit
from engine.cards import ActivationEffectKind, CardInstance, Keyword
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def _put(game, card_db, idx, name):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[idx].battlefield.append(c)
    return c


def _damage_ability(template):
    return next(a for a in template.activated_abilities
                if a.effect_kind is ActivationEffectKind.DAMAGE_ANY_TARGET)


# ── U1: the requirement is typed at load ───────────────────────────────

def test_an_activated_damage_ability_carries_its_any_target_requirement(card_db):
    ability = _damage_ability(card_db.get_card("Goblin Bombardment"))
    assert ability.targets_required == 1
    [req] = ability.target_requirements
    assert (req.zone, set(req.types)) == ("any", {"any"})


def test_every_activated_damage_ability_in_the_pool_carries_its_requirement(card_db):
    seen = missing = 0
    for t in {id(v): v for v in card_db.cards.values()}.values():
        for a in t.activated_abilities or ():
            if a.effect_kind is ActivationEffectKind.DAMAGE_ANY_TARGET:
                seen += 1
                missing += not a.target_requirements
    assert seen >= 10 and missing == 0, (seen, missing)


# ── U1: the activation target audit ────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def _violations():
    return {f["rule"] for f in rules_audit.drain() if f["kind"] == "violation"}


def test_the_activation_target_audit_sees_an_ability_aimed_at_what_it_may_not_target(audit, card_db):
    """CR 602.2b / 601.2c: every target declared for an activated ability
    is one its source may target. Silent on a legal target; it fires when
    an ability is activated at a hexproof creature."""
    from engine.activation import ActivationManager
    game = _game()
    pinger = _put(game, card_db, 0, "Prodigal Pyromancer")
    ability = _damage_ability(pinger.template)
    target = _put(game, card_db, 1, "Ragavan, Nimble Pilferer")
    assert ActivationManager.activate(game, 0, pinger, ability,
                                      [target.instance_id])
    assert "602.2b/activation_target" not in _violations()
    pinger.tapped = False
    target.temp_keywords.add(Keyword.HEXPROOF)
    ActivationManager.activate(game, 0, pinger, ability, [target.instance_id])
    assert "602.2b/activation_target" in _violations()
