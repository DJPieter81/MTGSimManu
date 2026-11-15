"""A permanent's cast trigger that exiles a target permanent exiles the
target it chose as it was put on the stack, if that target is still legal
(CR 603.2, 603.3d, 701.13a, 608.2b).

"Whenever you cast a colorless spell, exile up to one target permanent
that's one or more colors" triggers on the cast and takes its target from
the choices its requirement admits: a coloured permanent that is not
hexproof against it (CR 115.1). It exiles that permanent on resolution.

The defect: the trigger was performed by nothing. The grammar left "that's
one or more colors" as residue although the requirement models it (unit
CQ), and the cast carrier had no exile of a target permanent.

Class: 10 pool cards print a cast trigger that exiles a target permanent;
"whenever you cast" ones are carried here, the spell's own "when you cast
~" ones stay on the legacy self-cast resolver (a recorded lead).
Registered: Ugin, Eye of the Storms (x6). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _wastes(game, card_db, n, idx=0):
    for _ in range(n):
        _put(game, card_db, "Wastes", "battlefield", idx)


def _cast_and_resolve(game, card_db, name, lands, idx=0, targets=None):
    _wastes(game, card_db, lands, idx)
    spell = _put(game, card_db, name, "hand", idx)
    assert game.cast_spell(idx, spell, targets=targets)
    while game.stack.items:
        game.resolve_stack()
    return spell


# ── "Whenever you cast a <kind> spell, exile up to one target ..." ────

def test_a_colorless_spell_cast_under_the_trigger_exiles_a_coloured_target(card_db):
    game = _game()
    _put(game, card_db, "Ugin, Eye of the Storms", "battlefield", 0)
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast_and_resolve(game, card_db, "Thought-Knot Seer", 4)    # colorless
    assert bears.zone == "exile"


def test_a_spell_of_another_kind_does_not_trigger_it(card_db):
    game = _game()
    _put(game, card_db, "Ugin, Eye of the Storms", "battlefield", 0)
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _put(game, card_db, "Mountain", "battlefield", 0)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", 0)     # red
    from engine.constants import PLAYER_TARGET_OPPONENT
    assert game.cast_spell(0, bolt, targets=[PLAYER_TARGET_OPPONENT])
    while game.stack.items:
        game.resolve_stack()
    assert bears.zone == "battlefield"


def test_the_target_is_one_its_requirement_admits(card_db):
    """A colourless permanent is not "one or more colors" (CR 105.2c), and
    a hexproof one is not a target for it (CR 702.11b)."""
    game = _game()
    _put(game, card_db, "Ugin, Eye of the Storms", "battlefield", 0)
    thopter = _put(game, card_db, "Ornithopter", "battlefield", 1)
    scout = _put(game, card_db, "Gladecover Scout", "battlefield", 1)  # hexproof
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast_and_resolve(game, card_db, "Thought-Knot Seer", 4)
    assert (thopter.zone, scout.zone, bears.zone) == (
        "battlefield", "battlefield", "exile")


def _ai_game():
    from engine.game_runner import AICallbacks
    game = _game()
    game.callbacks = AICallbacks()       # the AI's own pick
    return game


def test_up_to_one_takes_none_rather_than_its_controllers_own(card_db):
    game = _ai_game()
    _put(game, card_db, "Ugin, Eye of the Storms", "battlefield", 0)
    mine = _put(game, card_db, "Grizzly Bears", "battlefield", 0)
    _cast_and_resolve(game, card_db, "Thought-Knot Seer", 4)
    assert mine.zone == "battlefield"


def test_the_ai_aims_it_at_the_opposing_permanent_worth_most(card_db):
    game = _ai_game()
    _put(game, card_db, "Ugin, Eye of the Storms", "battlefield", 0)
    small = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    big = _put(game, card_db, "Primeval Titan", "battlefield", 1)
    _cast_and_resolve(game, card_db, "Thought-Knot Seer", 4)
    assert (small.zone, big.zone) == ("battlefield", "exile")


# ── Typed: the qualifier the requirement models is no residue ─────────

def test_a_colour_qualifier_the_requirement_models_leaves_no_residue(card_db):
    from engine.effect_spec import HostKind
    for name in ("Ugin, Eye of the Storms", "Devourer of Destiny"):
        hosts = [h for h in card_db.get_card(name).effects.faces[0]
                 if h.kind is HostKind.TRIGGERED]
        assert hosts and all(
            "target.colored" not in s.residue for h in hosts for s in h.specs)


# ── Auditor (CR 701.13a) ──────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_chosen_target_left_unexiled(card_db, audit,
                                                         monkeypatch):
    from engine import zone_manager
    real = zone_manager.ZoneManager.move_card

    def refuse_exile(self, game, card, src, dst, **kw):
        if dst == "exile" and src == "battlefield":
            return False                                      # the defect
        return real(self, game, card, src, dst, **kw)
    monkeypatch.setattr(zone_manager.ZoneManager, "move_card", refuse_exile)
    game = _game()
    _put(game, card_db, "Ugin, Eye of the Storms", "battlefield", 0)
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast_and_resolve(game, card_db, "Thought-Knot Seer", 4)
    assert "701.13a/target_exiled" in _rules(audit)


def test_the_audit_is_silent_when_the_target_is_exiled(card_db, audit):
    game = _game()
    _put(game, card_db, "Ugin, Eye of the Storms", "battlefield", 0)
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast_and_resolve(game, card_db, "Thought-Knot Seer", 4)
    assert "701.13a/target_exiled" not in _rules(audit)
