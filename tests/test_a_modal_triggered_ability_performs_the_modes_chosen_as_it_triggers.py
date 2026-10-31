"""A modal triggered ability's controller chooses its modes as it is put on
the stack, and the chosen modes are performed (CR 603.3c, 700.2, 700.2a).

"Whenever this creature attacks, choose one -- * Discard a card. If you do,
draw a card. * Exile up to one target card from a graveyard." As the
trigger is put on the stack its controller chooses as many modes as the
header says, and the chosen modes' targets; a mode whose required target
has no legal choice can't be chosen, and with no mode chosen the ability is
removed. As it resolves, the chosen modes are performed, in printed order.

The defect: no trigger carrier took a modal host (its instructions are its
modes', none its own), so every modal trigger stayed on its legacy pass,
which performed no mode: Territorial Kavu's attack did nothing.

Class: 159 modal triggered abilities in the pool, 123 under the carriers'
heads (77 enter, 22 step, 9 attack, 9 cast, 6 combat damage). A carrier
takes a modal host when it prints nothing before its modes and each mode,
as a host of its own, is a body that carrier runs. Registered: Territorial
Kavu (Domain Zoo x4). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


def _game(phase=Phase.DECLARE_ATTACKERS):
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = phase
    game.turn_number = 5
    return game


def _place(game, tmpl, zone, idx):
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _put(game, card_db, name, zone, idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return _place(game, tmpl, zone, idx)


def _probe(oracle, types=(CardType.CREATURE,)):
    return CardTemplate(
        name="Modal Probe", card_types=list(types), mana_cost=ManaCost(),
        supertypes=[], subtypes=[], power=2, toughness=2, loyalty=None,
        keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False, oracle_text=oracle,
        tags=set())


def _attack(game, attackers):
    from engine.combat_manager import CombatManager
    CombatManager().declare_attackers(game, list(attackers), 0)


def _choose(monkeypatch, answer):
    from engine.callbacks import DefaultCallbacks
    monkeypatch.setattr(DefaultCallbacks, "choose_trigger_modes",
                        lambda self, game, player, source, host, legal,
                        choose: list(answer), raising=False)


# ── The attack carrier ─────────────────────────────────────────────────

def test_the_attack_carrier_takes_a_modal_host_whose_every_mode_it_runs(
        card_db):
    from engine.effect_carrier import attack_taken
    game = _game()
    kavu = _put(game, card_db, "Territorial Kavu", "battlefield")
    assert kavu.instance_id in attack_taken([kavu])


def test_the_chosen_mode_of_an_attack_trigger_is_performed(card_db):
    # The engine's default chooses the first legal mode: the rummage.
    game = _game()
    kavu = _put(game, card_db, "Territorial Kavu", "battlefield")
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    island = _put(game, card_db, "Island", "library")
    _attack(game, [kavu])
    assert bolt.zone == "graveyard"
    assert island.zone == "hand"


def test_the_controller_chooses_which_mode_is_performed(card_db,
                                                       monkeypatch):
    _choose(monkeypatch, [1])
    game = _game()
    kavu = _put(game, card_db, "Territorial Kavu", "battlefield")
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    dead = _put(game, card_db, "Grizzly Bears", "graveyard", 1)
    _attack(game, [kavu])
    assert dead.zone == "exile"
    assert bolt.zone == "hand"


def test_no_more_modes_than_the_header_allows_are_performed(card_db,
                                                           monkeypatch):
    # "Choose one": of the two modes asked for, only the first is kept.
    _choose(monkeypatch, [1, 0])
    game = _game()
    kavu = _put(game, card_db, "Territorial Kavu", "battlefield")
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    dead = _put(game, card_db, "Grizzly Bears", "graveyard", 1)
    _attack(game, [kavu])
    assert dead.zone == "exile"
    assert bolt.zone == "hand"


def test_a_mode_whose_required_target_has_no_legal_choice_is_never_chosen(
        monkeypatch):
    # CR 700.2a: with no creature an opponent controls, the damage mode
    # can't be chosen; the engine keeps the controller to the legal one.
    _choose(monkeypatch, [0])
    game = _game()
    probe = _place(game, _probe(
        "Whenever this creature attacks, choose one —\n"
        "• This creature deals 1 damage to target creature an opponent "
        "controls.\n• You gain 1 life."), "battlefield", 0)
    life = game.players[0].life
    _attack(game, [probe])
    assert game.players[0].life == life + 1


# ── The step and enter carriers ────────────────────────────────────────

def test_a_modal_step_trigger_performs_its_chosen_mode(card_db,
                                                       monkeypatch):
    from engine.turn_clock import Clock, ClockEvent, emit
    _choose(monkeypatch, [2])
    game = _game(Phase.UPKEEP)
    _put(game, card_db, "Etherwrought Page", "battlefield")
    life0, life1 = game.players[0].life, game.players[1].life
    emit(game, ClockEvent(Clock.STEP_BEGINS, 0, step="upkeep"))
    assert game.players[1].life == life1 - 1
    assert game.players[0].life == life0


def test_a_modal_enter_trigger_performs_its_chosen_mode(card_db,
                                                        monkeypatch):
    _choose(monkeypatch, [1])
    game = _game(Phase.MAIN1)
    for _ in range(3):
        _put(game, card_db, "Mountain", "battlefield")
    magician = _put(game, card_db, "Exhibition Magician", "hand")
    assert game.cast_spell(0, magician, [])
    game.resolve_stack()
    tokens = [c for c in game.players[0].battlefield if c.is_token]
    assert [t.template.is_creature for t in tokens] == [False]


def test_a_modal_host_printing_an_instruction_before_its_modes_is_not_taken(
        card_db):
    # "You may pay {E}{E}. When you do, choose one --": the modes belong to
    # the payment's reflexive trigger, which no carrier performs.
    from engine.effect_carrier import step_plan
    t = card_db.get_card("Voltstorm Angel")
    assert step_plan(t.effects.faces[0]) is None


# ── The AI's choice ────────────────────────────────────────────────────

def test_the_ai_exiles_a_graveyard_card_worth_denying_and_otherwise_rummages(
        card_db):
    from ai.modal import select_trigger_modes
    game = _game()
    kavu = _put(game, card_db, "Territorial Kavu", "battlefield")
    host = next(h for h in kavu.template.effects.faces[0]
                if h.trigger is not None and h.trigger.attack is not None)
    assert select_trigger_modes(game, 0, kavu, host, [0, 1], (1, 1)) == [0]
    # A flashback card is still a resource to its owner in their graveyard.
    _put(game, card_db, "Faithless Looting", "graveyard", 1)
    assert select_trigger_modes(game, 0, kavu, host, [0, 1], (1, 1)) == [1]


# ── Auditor (CR 700.2) ─────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f["rule"] for f in audit.drain() if f["kind"] == "violation"]


def test_the_audit_records_more_modes_than_the_header_allows(card_db, audit,
                                                            monkeypatch):
    from engine import effect_carrier
    monkeypatch.setattr(effect_carrier, "_held_modes",           # the defect
                        lambda asked, legal, lo, hi: list(legal),
                        raising=False)
    game = _game()
    kavu = _put(game, card_db, "Territorial Kavu", "battlefield")
    _attack(game, [kavu])
    assert "700.2/trigger_modes" in _violations(audit)


def test_the_audit_is_silent_when_the_header_is_kept(card_db, audit):
    game = _game()
    kavu = _put(game, card_db, "Territorial Kavu", "battlefield")
    _attack(game, [kavu])
    assert "700.2/trigger_modes" not in _violations(audit)
