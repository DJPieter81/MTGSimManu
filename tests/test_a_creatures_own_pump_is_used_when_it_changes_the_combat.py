"""A creature's own pump is used in combat when it changes the combat, and
its block counts it (CR 509.4, 602.2, 122.1, 514.2; AI).

After blockers are declared, the active player and then the defending
player receive priority before combat damage (CR 509.4). Either may
activate abilities (CR 602.2). A creature whose own instant-speed ability
pumps it fights at the stats its controller pays for:
- "Discard a card: Put a +1/+1 counter on ~": the counter lasts (CR 122.1);
- "{R}: ~ gets +2/+0 until end of turn": the bonus ends with the turn
  (CR 514.2).

The engine's rule (`GameRunner._combat_pump_window`): after the active
player's combat tricks, each player in turn (active first) is asked for its
creatures' own pumps, activated through the activation owner and resolved
before damage. The auditor `509.4/priority_after_blockers` records a combat
whose damage was dealt before both players were offered the window.

The AI's rule (`ai.combat_pump`, `EVPlayer.decide_combat_pump`): a pump is
activated when one more activation raises the projected post-combat
position -- the declared combat projected with and without it, priced by
`position_value`, the cost (a card, life, mana) in the same snapshot. A
block counts the blocker's affordable pump (`_score_block_lifespan_delta`).

The defect (docs/diagnostics/2026-10-14_dimir_midrange_root_cause.md,
subsystem 2): only the active player had a window, and only for instants.
No player ever activated a creature's own pump in combat. Psychic Frog
blocked three times and died three times holding cards that would have kept
it alive and killed the attacker.

Card names are fixture carriers: each is one member of its class (260 pool
creatures carry a typed instant-speed self pump whose cost gives up no
permanent).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.combat_manager import CombatManager
from engine.game_state import GameState, Phase


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


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for p in (0, 1):
        for _ in range(6):
            _add(game, card_db_ref[0], "Island", p, "library")
    return game


card_db_ref = []


@pytest.fixture(autouse=True)
def _db(card_db):
    card_db_ref[:] = [card_db]


def _combat(game, attackers, blocks):
    cm = CombatManager()
    game.current_phase = Phase.DECLARE_ATTACKERS
    cm.declare_attackers(game, attackers, 0)
    game.current_phase = Phase.DECLARE_BLOCKERS
    cm.declare_blockers(game, blocks)
    return cm


def _ai(idx, deck="Dimir Midrange"):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=idx, deck_name=deck, rng=random.Random(0))


def _discard_pump(frog):
    return frog.template.activated_abilities[0]


# ── The engine: the defending player's window (CR 509.4) ───────────────

def test_the_defending_player_may_activate_its_pump_after_blockers(card_db):
    from engine.game_runner import GameRunner
    game = _game()
    bears = _add(game, card_db, "Grizzly Bears", 0)
    frog = _add(game, card_db, "Psychic Frog", 1)
    _add(game, card_db, "Island", 1, "hand")
    cm = _combat(game, [bears], {bears.instance_id: [frog.instance_id]})
    active, defending = _ai(0, "Domain Zoo"), _ai(1)
    asked = []

    def _pump(g, combat):
        asked.append(combat)
        return None if frog.plus_counters else (frog, _discard_pump(frog))

    active.decide_combat_pump = lambda g, combat: None
    defending.decide_combat_pump = _pump
    GameRunner(card_db=card_db)._combat_pump_window(game, active, defending, cm)
    assert asked, "the defending player must be offered the window"
    assert frog.plus_counters == 1 and not game.players[1].hand
    cm.resolve_combat_damage(game)
    game.check_state_based_actions()
    assert bears.zone == "graveyard" and frog.zone == "battlefield"


def test_the_active_player_receives_priority_again_after_a_pump_resolves(
        card_db):
    """CR 117.3b, 117.4: after the defending player's pump resolves, the
    active player receives priority again, and the step ends only when both
    pass in succession -- so an attacker may answer a blocker's pump."""
    from engine.game_runner import GameRunner
    game = _game()
    attacker = _add(game, card_db, "Psychic Frog", 0)
    blocker = _add(game, card_db, "Psychic Frog", 1)
    for p in (0, 1):
        _add(game, card_db, "Island", p, "hand")
    cm = _combat(game, [attacker],
                 {attacker.instance_id: [blocker.instance_id]})
    active, defending = _ai(0), _ai(1)
    active.decide_combat_pump = lambda g, combat: (
        (attacker, _discard_pump(attacker))
        if blocker.plus_counters and not attacker.plus_counters else None)
    defending.decide_combat_pump = lambda g, combat: (
        None if blocker.plus_counters else (blocker, _discard_pump(blocker)))
    GameRunner(card_db=card_db)._combat_pump_window(game, active, defending, cm)
    assert blocker.plus_counters == 1 and attacker.plus_counters == 1


# ── The AI: a pump that changes the fight, and only then ───────────────

def test_a_blocker_pumps_when_it_turns_a_trade_into_a_kill(card_db):
    """Frog (1/2) blocks a 2/2 with a card in hand: one discard makes it a
    2/3 that survives and kills."""
    game = _game()
    bears = _add(game, card_db, "Grizzly Bears", 0)
    frog = _add(game, card_db, "Psychic Frog", 1)
    _add(game, card_db, "Island", 1, "hand")
    cm = _combat(game, [bears], {bears.instance_id: [frog.instance_id]})
    assert _ai(1).decide_combat_pump(game, cm) == (frog, _discard_pump(frog))


def test_a_blocker_pumps_when_only_a_second_activation_wins_the_fight(
        card_db):
    """Frog (1/2) blocks a 3/3 holding two cards: one discard (a 2/3)
    still dies and kills nothing; two (a 3/4) survive and kill. The first
    activation is made toward the plan the hand can pay for."""
    game = _game()
    courser = _add(game, card_db, "Centaur Courser", 0)
    frog = _add(game, card_db, "Psychic Frog", 1)
    for _ in range(2):
        _add(game, card_db, "Island", 1, "hand")
    cm = _combat(game, [courser], {courser.instance_id: [frog.instance_id]})
    assert _ai(1).decide_combat_pump(game, cm) == (frog, _discard_pump(frog))


def test_a_pump_that_changes_nothing_is_held(card_db):
    """Frog blocks a 1/1: it survives and kills without a discard."""
    game = _game()
    memnite = _add(game, card_db, "Memnite", 0)
    frog = _add(game, card_db, "Psychic Frog", 1)
    _add(game, card_db, "Island", 1, "hand")
    cm = _combat(game, [memnite], {memnite.instance_id: [frog.instance_id]})
    assert _ai(1).decide_combat_pump(game, cm) is None


def test_a_pump_its_controller_cannot_pay_for_is_not_made(card_db):
    game = _game()
    bears = _add(game, card_db, "Grizzly Bears", 0)
    frog = _add(game, card_db, "Psychic Frog", 1)          # empty hand
    cm = _combat(game, [bears], {bears.instance_id: [frog.instance_id]})
    assert _ai(1).decide_combat_pump(game, cm) is None


def test_an_attacker_pumps_to_win_its_fight(card_db):
    """Frog attacks and a 2/2 blocks it: one discard wins the fight."""
    game = _game()
    frog = _add(game, card_db, "Psychic Frog", 0)
    _add(game, card_db, "Island", 0, "hand")
    bears = _add(game, card_db, "Grizzly Bears", 1)
    cm = _combat(game, [frog], {frog.instance_id: [bears.instance_id]})
    assert _ai(0).decide_combat_pump(game, cm) == (frog, _discard_pump(frog))


def test_an_unblocked_attacker_spends_no_card_on_damage_that_kills_nothing(
        card_db):
    """Frog attacks unblocked into 20 life: a discard buys one damage and
    changes no fight -- the cards stay in hand (the digest's Living End vs
    Dimir s59000 g1 discarded Force of Negation and a planeswalker for two
    damage)."""
    game = _game()
    frog = _add(game, card_db, "Psychic Frog", 0)
    for _ in range(2):
        _add(game, card_db, "Island", 0, "hand")
    cm = _combat(game, [frog], {})
    assert _ai(0).decide_combat_pump(game, cm) is None


def test_an_unblocked_attacker_pumps_when_the_damage_is_lethal(card_db):
    game = _game()
    game.players[1].life = 2
    frog = _add(game, card_db, "Psychic Frog", 0)       # 1 power unblocked
    _add(game, card_db, "Island", 0, "hand")
    cm = _combat(game, [frog], {})
    assert _ai(0).decide_combat_pump(game, cm) == (frog, _discard_pump(frog))


def test_an_until_end_of_turn_pump_paid_with_mana_trades_up(card_db):
    """Blazing Rootwalla (1/1, "{R}: +2/+0 until end of turn") is blocked by
    a 2/2: the pump turns a chump into a trade."""
    game = _game()
    _add(game, card_db, "Mountain", 0)
    walla = _add(game, card_db, "Blazing Rootwalla", 0)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    cm = _combat(game, [walla], {walla.instance_id: [bears.instance_id]})
    assert _ai(0, "Hollow One").decide_combat_pump(game, cm) == (
        walla, walla.template.activated_abilities[0])


@pytest.mark.parametrize("cards, steps", [(1, 1), (2, 2)])
def test_the_plan_takes_the_best_fight_it_can_pay_for(card_db, cards, steps):
    """Frog (1/2) facing a 2/3: one discard lets it survive, two let it
    survive and kill. The plan pays for the best fight its hand allows, and
    no more."""
    from ai.combat_pump import fight_plan
    game = _game()
    scholar = _add(game, card_db, "Tolarian Scholar", 0)
    frog = _add(game, card_db, "Psychic Frog", 1)
    for _ in range(cards):
        _add(game, card_db, "Island", 1, "hand")
    assert fight_plan(game, 1, frog, scholar) == (_discard_pump(frog), steps)


def test_the_block_counts_the_blockers_affordable_pump(card_db):
    """Blocking a 2/2 with Frog is worth more holding a card to discard."""
    def _delta(with_card):
        game = _game()
        bears = _add(game, card_db, "Grizzly Bears", 0)
        frog = _add(game, card_db, "Psychic Frog", 1)
        if with_card:
            _add(game, card_db, "Island", 1, "hand")
        ai = _ai(1)
        return ai._score_block_lifespan_delta(
            game, bears, frog, my_life=20, my_power=1, opp_power=2)
    assert _delta(True) > _delta(False)


# ── The auditor (CR 509.4) ────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain()
            if f["rule"] == "509.4/priority_after_blockers"]


def test_the_audit_records_damage_dealt_before_both_players_had_the_window(
        card_db, audit):
    game = _game()
    bears = _add(game, card_db, "Grizzly Bears", 0)
    frog = _add(game, card_db, "Psychic Frog", 1)
    cm = _combat(game, [bears], {bears.instance_id: [frog.instance_id]})
    cm.resolve_combat_damage(game)                      # no window offered
    assert _findings(audit)


def test_the_audit_is_silent_after_the_window(card_db, audit):
    from engine.game_runner import GameRunner
    game = _game()
    bears = _add(game, card_db, "Grizzly Bears", 0)
    frog = _add(game, card_db, "Psychic Frog", 1)
    cm = _combat(game, [bears], {bears.instance_id: [frog.instance_id]})
    GameRunner(card_db=card_db)._combat_pump_window(
        game, _ai(0, "Domain Zoo"), _ai(1), cm)
    cm.resolve_combat_damage(game)
    assert not _findings(audit)
