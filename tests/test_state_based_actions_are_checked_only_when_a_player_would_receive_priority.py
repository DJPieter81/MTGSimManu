"""State-based actions are checked when a player would receive priority,
and never while a spell or ability is resolving.

CR 704.3: whenever a player would get priority, the game checks for the
conditions state-based actions name and performs them. CR 704.4: they pay
no attention to what happens during the resolution of a spell or ability.
CR 104.3c: a player who must draw from an empty library loses the next
time a player would receive priority.

The engine checked in five places while something resolved:
  * after a dealt-damage clause (the energy clause in `clause_resolver`,
    and `oracle_resolver.resolve_any_target_damage`);
  * after a dies drain (twice in `permanent_effects`);
  * after each draw trigger (`zone_transfer`).
A handler and the empty-library draw ended the game outright.

So three things went wrong:
  * a player who fell to 0 part-way through a resolution lost even when
    the same resolution gained the life back;
  * a draw stopped part-way;
  * a creature dealt lethal damage left before the resolution had
    finished.
Meanwhile a stack resolution ended with no check of its own.

The class is every state-based action crossed with every resolution:
  * the actions: 704.5a life, 704.5b the empty-library draw, 704.5f-i
    creatures, 704.5j legends, 704.5p walkers;
  * the resolutions: a stack item, an ability resolved inline, a trigger
    a carrier resolves.

The owner is `GameState.check_state_based_actions`.
`sba_manager.resolving` marks a resolution in progress, and a check
requested during one waits for the outermost resolution to end. Checks
are made:
  * when a stack resolution ends;
  * as a step begins;
  * once attackers are declared;
  * after combat damage;
  * after each action;
  * after each trigger the carriers resolve.
Fixture cards are carriers only.
"""
from __future__ import annotations

import random

import pytest

from engine import rules_audit
from engine.cards import CardInstance
from engine.game_state import GameState, Phase

ON_DRAW_LOSS = "Sheoldred, the Apocalypse"   # an opponent's draw: lose 2


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def _rules(findings):
    return sorted({f["rule"] for f in findings if f["kind"] == "violation"})


def _game(active=0):
    game = GameState(rng=random.Random(0))
    game.active_player = active
    game.current_phase = Phase.MAIN1
    return game


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


def _resolve_text(game, source, controller, text, family):
    """Resolve printed `text` as an ability of `source` (CR 608.2)."""
    from engine import effect_resolver as er
    from engine.effect_grammar import parse_text_effects
    host = parse_text_effects(text).spell(0)
    assert er.can_execute(host, family), text
    return er.resolve_ability(game, er.handle_of(source), controller, host,
                              (), family=family, source_object=source)


def _drawing_opponent(card_db, life, library=8):
    """An on-draw life-loss source under P0; P1 draws."""
    game = _game(active=1)
    _add(game, card_db, ON_DRAW_LOSS, 0, "battlefield")
    source = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    for _ in range(library):
        _add(game, card_db, "Island", 1, "library")
    game.players[1].life = life
    return game, source


# ── During a resolution: no state-based action is performed ────────────

def test_life_regained_before_a_resolution_ends_keeps_its_player_in_the_game(
        card_db):
    """3 life, "Draw two cards, then you gain 4 life." under an opponent's
    draw-loss permanent: -1 after the draws, 3 when the resolution ends. The
    check comes then, and the player is still in the game."""
    game, source = _drawing_opponent(card_db, life=3)
    assert _resolve_text(game, source, 1,
                         "Draw two cards, then you gain 4 life.", "card_flow")
    assert game.players[1].life == 3
    assert not game.game_over
    game.check_state_based_actions()
    assert not game.game_over


def test_a_resolution_draws_every_card_after_its_player_falls_to_zero(
        card_db):
    game, source = _drawing_opponent(card_db, life=2)
    assert _resolve_text(game, source, 1, "Draw four cards.", "card_flow")
    assert len(game.players[1].hand) == 4
    assert game.players[1].life == 2 - 4 * 2
    game.check_state_based_actions()
    assert game.game_over and game.winner == 0


def test_a_check_requested_during_a_resolution_waits_for_it_to_end(card_db):
    from engine.sba_manager import resolving
    game = _game()
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    bears.damage_marked = 2
    with resolving(game):
        with resolving(game):        # a trigger resolved within a spell
            game.check_state_based_actions()
        game.check_state_based_actions()
        assert bears.zone == "battlefield"
    game.check_state_based_actions()
    assert bears.zone == "graveyard"


def test_a_draw_from_an_empty_library_loses_at_the_next_check(card_db):
    """CR 104.3c, 704.5b: the player draws what is left, and loses the next
    time a player would receive priority -- not part-way through."""
    from engine.sba_manager import resolving
    game = _game()
    _add(game, card_db, "Island", 0, "library")
    with resolving(game):
        drawn = game.draw_cards(0, 3)
        assert len(drawn) == 1
        assert not game.game_over
    game.check_state_based_actions()
    assert game.game_over and game.winner == 1


# ── The checks at the points a player would receive priority ───────────

def test_the_checks_are_made_once_a_stack_resolution_ends(card_db):
    game = _game()
    _add(game, card_db, "Mountain", 0, "battlefield")
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    bolt = _add(game, card_db, "Lightning Bolt", 0, "hand")
    assert game.cast_spell(0, bolt, [bears.instance_id])
    game.resolve_stack()
    assert bears.zone == "graveyard"


def test_a_creature_dealt_lethal_damage_leaves_after_its_resolution_ends(
        card_db):
    """The spell finishes resolving -- its last step puts it into its
    owner's graveyard (CR 608.2n) -- before the creature it damaged dies."""
    game = _game()
    _add(game, card_db, "Mountain", 0, "battlefield")
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    spell = _add(game, card_db, "Galvanic Discharge", 0, "hand")
    assert game.cast_spell(0, spell, [bears.instance_id])
    game.resolve_stack()
    assert bears.zone == "graveyard"
    finished = next(i for i, line in enumerate(game.log)
                    if "Galvanic Discharge moved stack -> graveyard" in line)
    died = next(i for i, line in enumerate(game.log)
                if "Grizzly Bears dies" in line)
    assert finished < died, game.log[-6:]


def test_the_checks_are_made_as_a_step_begins():
    from engine.game_runner import GameRunner
    game = _game()
    game.players[1].life = 0
    GameRunner._step_begins(game, 0, Phase.DRAW)
    assert game.game_over and game.winner == 0


def test_the_checks_are_made_once_attackers_are_declared(card_db):
    from engine.combat_manager import CombatManager
    game = _game()
    attacker = _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    game.players[1].life = 0
    CombatManager().declare_attackers(game, [attacker], 0, {})
    assert game.game_over and game.winner == 0


# ── The auditor ────────────────────────────────────────────────────────

def test_the_auditor_sees_state_based_actions_performed_during_a_resolution(
        audit, monkeypatch, card_db):
    """Re-create the defect: the check no longer waits for the resolution."""
    from engine import sba_manager
    from engine.sba_manager import resolving
    monkeypatch.setattr(sba_manager, "resolution_in_progress",
                        lambda game: False)
    game = _game()
    _add(game, card_db, "Grizzly Bears", 1, "battlefield").damage_marked = 2
    with resolving(game):
        game.check_state_based_actions()
    assert "704.4/during_resolution" in _rules(audit.drain())


def test_the_auditor_is_silent_when_the_checks_wait_for_the_resolution(
        audit, card_db):
    from engine.sba_manager import resolving
    game = _game()
    _add(game, card_db, "Grizzly Bears", 1, "battlefield").damage_marked = 2
    with resolving(game):
        game.check_state_based_actions()
    game.check_state_based_actions()
    assert "704.4/during_resolution" not in _rules(audit.drain())


def test_the_auditor_sees_a_player_left_in_after_drawing_from_an_empty_library(
        audit, monkeypatch):
    """Re-create the defect: the check never performs the 704.5b loss."""
    from engine.sba_manager import SBAManager
    monkeypatch.setattr(SBAManager, "perform_empty_library_check",
                        staticmethod(lambda game: False))
    game = _game()
    game.draw_cards(0, 1)
    game.check_state_based_actions()
    assert "704.5b/empty_library" in _rules(audit.drain())


def test_the_auditor_is_silent_when_the_empty_library_loss_is_performed(
        audit):
    game = _game()
    game.draw_cards(0, 1)
    game.check_state_based_actions()
    assert game.game_over and game.winner == 1
    assert "704.5b/empty_library" not in _rules(audit.drain())
