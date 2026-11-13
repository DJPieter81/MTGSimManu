"""A loyalty ability is performed from its typed instructions, with the
targets chosen as it is activated (CR 606.1, 602.2b, 601.2c, 608.2c).

A loyalty ability is an activated ability (CR 606.1). Its targets are
chosen as it is activated, among the choices each requirement admits (CR
602.2b, 601.2c). A required target with no legal choice means the ability
cannot be activated, so nothing is paid. On resolution its instructions are
performed in order (CR 608.2c).

The engine's rule (`effect_carrier.loyalty_family`, `dispatch_loyalty`):
a line the legacy classifier leaves UNCLASSIFIED or types as DAMAGE is
performed by the dispatcher when the dispatcher can perform its typed host.
Its targets are chosen through the controller's per-slot pick
(`callbacks.choose_trigger_targets`), the channel a trigger's targets
use.

The defects:
- An UNCLASSIFIED line was never offered, even when the dispatcher could
  run it ("Target player sacrifices a creature").
- The DAMAGE resolver read only a number from the text. It dealt that many
  to the highest-mana-value creature it killed, or else to the opponent,
  whatever the line's own target admitted:
  - "deals 4 damage to target creature" hit the opponent's face on an empty
    board;
  - "any target" never reached a planeswalker.

The AI's rule (`ai.pw_ability.loyalty_line_value`): a line whose damage the
dispatcher deals is valued by where that damage goes. That is the damage
aim the carrier's pick takes (`ai.damage_targets.choose_damage_recipient`),
on the same scale as an aimed damage activation.

Card names are fixture carriers: each line is one member of its class.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.planeswalker_manager import PlaneswalkerManager


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


def _walker(game, card_db, name, loyalty, controller=0):
    pw = _add(game, card_db, name, controller)
    pw.loyalty_counters = loyalty
    return pw


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for p in (0, 1):
        for _ in range(4):
            _add(game, card_db, "Island", p, "library")
    return game


# ── The engine (CR 602.2b, 601.2c, 608.2c) ────────────────────────────

def test_a_line_the_dispatcher_can_perform_is_offered_and_performed(card_db):
    """"Target player sacrifices a creature": once UNCLASSIFIED, so never
    offered."""
    game = _game(card_db)
    liliana = _walker(game, card_db, "Liliana of the Veil", 3)
    bear = _add(game, card_db, "Grizzly Bears", 1)
    assert "minus" in PlaneswalkerManager.resolvable_ability_slots(liliana, game)
    assert game.activate_planeswalker(0, liliana, "minus")
    assert bear.zone == "graveyard"
    assert liliana.loyalty_counters == 1


def test_a_required_target_with_no_legal_choice_refuses_the_line_unpaid(card_db):
    """"Deals 4 damage to target creature" with no creature to target cannot
    be activated (CR 602.2b, 601.2c). It once dealt the 4 to the opponent's
    face."""
    game = _game(card_db)
    chandra = _walker(game, card_db, "Chandra, Torch of Defiance", 4)
    assert "minus" not in PlaneswalkerManager.resolvable_ability_slots(
        chandra, game)
    assert not game.activate_planeswalker(0, chandra, "minus")
    assert chandra.loyalty_counters == 4
    assert game.players[1].life == 20


def test_the_damage_goes_to_the_target_chosen_as_it_is_activated(card_db):
    """The controller picks among what "target creature" admits. The legacy
    resolver dealt the damage to the highest-mana-value creature it killed."""
    game = _game(card_db)
    chandra = _walker(game, card_db, "Chandra, Torch of Defiance", 4)
    memnite = _add(game, card_db, "Memnite", 1)
    bear = _add(game, card_db, "Grizzly Bears", 1)
    game.callbacks.choose_trigger_targets = (
        lambda g, p, src, spec, req, players, perms, x_value=0: [memnite])
    assert game.activate_planeswalker(0, chandra, "minus")
    game.check_state_based_actions()
    assert memnite.zone == "graveyard"
    assert bear.zone == "battlefield"


def test_any_target_admits_a_planeswalker(card_db):
    """"1 damage to any target" (CR 115.4): an opposing planeswalker is a
    choice. The legacy resolver considered only creatures and the face."""
    game = _game(card_db)
    six = _walker(game, card_db, "Wrenn and Six", 3)
    teferi = _walker(game, card_db, "Teferi, Time Raveler", 1, controller=1)
    game.callbacks.choose_trigger_targets = (
        lambda g, p, src, spec, req, players, perms, x_value=0: [teferi])
    assert game.activate_planeswalker(0, six, "minus")
    game.check_state_based_actions()
    assert teferi.zone == "graveyard"
    assert game.players[1].life == 20


def test_a_line_the_dispatcher_cannot_perform_keeps_its_legacy_path(card_db):
    """A line outside every landed family's strict shape is not taken: the
    legacy kind still resolves it (the divided-damage line)."""
    from engine.effect_carrier import loyalty_family
    ral = card_db.get_card("Ral, Monsoon Mage // Ral, Leyline Prodigy")
    line = ral.back_face_loyalty_abilities["minus"]
    host = ral.effects.loyalty("minus", 1)
    assert loyalty_family(line, host) is None


# ── The AI: where a loyalty line's damage goes, and what it is worth ──

def _ai_game(card_db, opposing):
    from engine.game_runner import AICallbacks
    game = _game(card_db)
    game.players[0].deck_name = "4c Omnath"
    game.callbacks = AICallbacks()
    six = _walker(game, card_db, "Wrenn and Six", 3)
    theirs = _add(game, card_db, opposing, 1)
    return game, six, theirs


def test_the_ai_aims_a_loyalty_lines_damage_with_the_damage_owner(card_db):
    from ai.damage_targets import choose_damage_recipient
    from engine.effect_carrier import loyalty_host
    game, six, birds = _ai_game(card_db, "Birds of Paradise")
    line = six.template.loyalty_abilities["minus"]
    (req,) = loyalty_host(six, line).targets
    aim = choose_damage_recipient(game, 0, six, 1, req, mana_committed=0)
    life = game.players[1].life
    assert game.activate_planeswalker(0, six, "minus")
    game.check_state_based_actions()
    if aim.permanent is not None:
        assert birds.zone == "graveyard" and game.players[1].life == life
    else:
        assert birds.zone == "battlefield" and game.players[1].life == life - 1


def test_a_damage_line_is_valued_by_where_its_damage_goes(card_db):
    """Two boards differing only in the opposing 0-power creature: one the
    line's 1 damage destroys and is worth more than the face (a 0/1 that
    grows), one it does not destroy (a 0/2). The line's value differs by
    exactly the difference of its aims' values (`choose_damage_recipient`:
    a kill at `permanent_threat`, the face at `face_damage_value`). The
    projection the line was valued by credited the kill of a 0-power
    creature as almost nothing."""
    from ai.damage_targets import choose_damage_recipient
    from ai.pw_ability import loyalty_line_value
    from engine.effect_carrier import loyalty_host
    values, aims = [], []
    for opposing in ("Glaze Fiend", "Ornithopter"):          # 0/1, 0/2
        game, six, _ = _ai_game(card_db, opposing)
        line = six.template.loyalty_abilities["minus"]
        (req,) = loyalty_host(six, line).targets
        aims.append(choose_damage_recipient(game, 0, six, 1, req,
                                            mana_committed=0))
        values.append(loyalty_line_value(six, line, game, 0))
    assert aims[0].permanent is not None and aims[1].permanent is None
    assert values[0] - values[1] == pytest.approx(aims[0].value
                                                  - aims[1].value)


# ── The auditor (CR 602.2b, 115.1) ────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "602.2b/loyalty_target"]


def test_the_audit_records_a_line_damaging_what_its_target_does_not_admit(
        card_db, audit, monkeypatch):
    """The defect restored: the carrier declines, so the legacy resolver
    deals "target creature" damage to the opponent's face."""
    from engine import effect_carrier
    monkeypatch.setattr(effect_carrier, "loyalty_family",
                        lambda ability, host: None)
    game = _game(card_db)
    chandra = _walker(game, card_db, "Chandra, Torch of Defiance", 4)
    game.activate_planeswalker(0, chandra, "minus")
    assert game.players[1].life == 16
    assert _findings(audit)


def test_the_audit_is_silent_on_a_target_its_requirement_admits(card_db, audit):
    game = _game(card_db)
    chandra = _walker(game, card_db, "Chandra, Torch of Defiance", 4)
    _add(game, card_db, "Grizzly Bears", 1)
    assert game.activate_planeswalker(0, chandra, "minus")
    assert not _findings(audit)
