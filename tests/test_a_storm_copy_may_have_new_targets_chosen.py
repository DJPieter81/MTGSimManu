"""A storm copy may have new targets chosen for it (CR 702.40a, 707.10c);
the AI plans where K copies of N damage go.

Storm copies the spell once for each other spell cast before it this turn
(CR 702.40a). A copy copies the spell's targets, and its controller may
choose new ones for it (CR 707.10c). A new target must be one the spell
could have chosen. The engine asks the copy's controller
(`callbacks.choose_copy_targets`). A target the spell could not have
chosen is refused, and the copy keeps the original's.

The AI's rule (`ai.damage_targets.plan_copy_damage`): copies of a spell
that deals N damage to one target are aimed together.
- Copies that are lethal together go to the face.
- Otherwise the copies kill the opposing permanents they can destroy that
  are worth most to their controller (`permanent_threat`). A permanent
  needs as many copies as its toughness less its marked damage demands,
  so two 1-damage copies kill a 1/2.
- The rest go to the face, valued as the face value of what they deal.

The defect: every copy re-ran the original's stack item, so all of them
took its target. Ruby Storm vs Boros Energy s50000, T4: a Grapeshot with 6
copies sent all 7 damage to the face, while 6 of them would have killed
Boros's four attackers (two 1/2s, a 2/1, a 1/1).

Card names are fixture carriers: every storm spell with a target.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
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


BOROS_BOARD = ["Guide of Souls", "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger",
               "Ocelot Pride", "Memnite"]


def _game(card_db, opposing=BOROS_BOARD, opp_life=17):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(2):
        _add(game, card_db, "Mountain", 0)
    for _ in range(3):
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    theirs = [_add(game, card_db, n, 1) for n in opposing]
    game.players[1].life = opp_life
    return game, theirs


def _grapeshot(game, card_db, prior_spells, target=PLAYER_TARGET_OPPONENT):
    """Cast Grapeshot as the (prior_spells + 1)th spell of the turn, at
    `target`, and resolve it with its storm copies."""
    shot = _add(game, card_db, "Grapeshot", 0, "hand")
    game._global_storm_count = prior_spells
    assert game.cast_spell(0, shot, [target])
    while not game.stack.is_empty:
        game.resolve_stack()
    game.check_state_based_actions()
    return shot


# ── The engine: a copy's targets (CR 707.10c) ─────────────────────────

def test_by_default_a_copy_keeps_the_originals_targets(card_db):
    game, theirs = _game(card_db)
    _grapeshot(game, card_db, prior_spells=3)
    assert game.players[1].life == 17 - 4
    assert all(c.zone == "battlefield" for c in theirs)


def test_a_storm_copy_may_have_new_targets_chosen(card_db):
    game, theirs = _game(card_db)
    memnite = theirs[3]
    game.callbacks.choose_copy_targets = \
        lambda g, p, item, remaining: [memnite.instance_id]
    _grapeshot(game, card_db, prior_spells=1)
    assert game.players[1].life == 17 - 1        # the original
    assert memnite.zone == "graveyard"           # its copy


def test_a_target_the_spell_could_not_have_chosen_is_refused(card_db):
    """A land of the caster's own is not "any target" (CR 115.4): the copy
    keeps the original's target."""
    game, theirs = _game(card_db)
    own_land = game.players[0].battlefield[0]
    game.callbacks.choose_copy_targets = \
        lambda g, p, item, remaining: [own_land.instance_id]
    _grapeshot(game, card_db, prior_spells=1)
    assert game.players[1].life == 17 - 2
    assert own_land.zone == "battlefield"


# ── The AI: where K copies of N damage go ─────────────────────────────

def _plan(game, card_db, copies, amount=1):
    from ai.damage_targets import plan_copy_damage
    from engine.cast_manager import CastManager
    shot = _add(game, card_db, "Grapeshot", 0, "hand")
    (req,) = CastManager.spell_requirements(shot)
    return plan_copy_damage(game, 0, shot, amount, copies, req)


def test_copies_kill_what_they_destroy_together_then_go_to_the_face(card_db):
    """Two 1/2s, a 1/1 and a 1/1 artifact: six 1-damage copies kill all
    four, the seventh goes to the face."""
    game, theirs = _game(card_db)
    plan = _plan(game, card_db, copies=7)
    ids = {c.instance_id: c for c in theirs}
    killed = {t for t in plan if t in ids}
    assert killed == set(ids)
    for c in theirs:
        need = (c.toughness or 0) - (c.damage_marked or 0)
        assert plan.count(c.instance_id) == need
    assert plan.count(PLAYER_TARGET_OPPONENT) == 1


def test_copies_that_are_lethal_together_go_to_the_face(card_db):
    game, _theirs = _game(card_db, opp_life=5)
    assert _plan(game, card_db, copies=5) == [PLAYER_TARGET_OPPONENT] * 5


def test_no_copy_is_spent_on_a_permanent_damage_does_not_destroy(card_db):
    game, theirs = _game(card_db, opposing=["Darksteel Myr"])   # indestructible
    assert theirs[0].instance_id not in _plan(game, card_db, copies=3)


def test_the_ai_aims_a_storm_spells_copies_at_the_board(card_db):
    """Through the AI's own callbacks: the copies clear the attackers."""
    from engine.game_runner import AICallbacks
    game, theirs = _game(card_db)
    game.players[0].deck_name = "Ruby Storm"
    game.callbacks = AICallbacks()
    _grapeshot(game, card_db, prior_spells=6)
    assert all(c.zone == "graveyard" for c in theirs)
    assert game.players[1].life == 17 - 1


# ── The auditor (CR 707.10c) ──────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "707.10c/copy_targets"]


def test_the_audit_records_a_copy_target_the_spell_could_not_have_chosen(
        card_db, audit, monkeypatch):
    from engine.cast_manager import CastManager
    monkeypatch.setattr(CastManager, "copy_targets_legal",
                        staticmethod(lambda *a, **k: True))       # the defect
    game, _theirs = _game(card_db)
    own_land = game.players[0].battlefield[0]
    game.callbacks.choose_copy_targets = \
        lambda g, p, item, remaining: [own_land.instance_id]
    _grapeshot(game, card_db, prior_spells=1)
    assert _findings(audit)


def test_the_audit_is_silent_on_a_legal_copy_target(card_db, audit):
    game, theirs = _game(card_db)
    game.callbacks.choose_copy_targets = \
        lambda g, p, item, remaining: [theirs[3].instance_id]
    _grapeshot(game, card_db, prior_spells=1)
    assert not _findings(audit)
