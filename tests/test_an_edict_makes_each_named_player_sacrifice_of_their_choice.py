"""An edict -- "<players> sacrifice(s) <N> <permanents> [of their
choice]" -- makes each player it names sacrifice that many permanents
matching it that they control, each chosen by the player who sacrifices
it; with fewer, they sacrifice every one they have (CR 701.21a, 101.4). A
sacrificed creature dies (CR 700.4).

"Choose one -- each opponent sacrifices a nontoken creature of their
choice; ... a creature token ...; ... a planeswalker ...": the opponent
picks which of their nontoken creatures goes.

The defect: no owner performed an edict. Sheoldred's Edict (Dimir
Midrange MB, Eldrazi Ramp SB x2) and Lorehold Charm's first mode resolved
to nothing in real casts (`unhandled/mode`), as did the pool's plain edict
spells and modes.

Card names are fixture carriers: 9 pool spells, 17 pool modes and 33
triggered abilities are edicts of this shape; about 155 pool cards make a
player sacrifice.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(active=0):
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = active
    g.turn_number = 7
    return g


def _put(game, card_db, name, zone, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _token(game, idx=1):
    game.create_token(idx, "creature", 1, 1, 1)
    return game.players[idx].battlefield[-1]


def _cast(game, card_db, name, targets=(), lands=("Swamp", "Swamp")):
    for land in lands:
        _put(game, card_db, land, "battlefield")
    spell = _put(game, card_db, name, "hand")
    assert game.cast_spell(0, spell, targets=list(targets))
    game.resolve_stack()
    game.check_state_based_actions()


def test_each_opponent_sacrifices_a_creature_of_their_choice(card_db):
    game = _game()
    mine = _put(game, card_db, "Grizzly Bears", "battlefield", 0)
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    elves = _put(game, card_db, "Llanowar Elves", "battlefield", 1)
    _cast(game, card_db, "Sheoldred's Edict")
    assert elves.zone == "graveyard", "their least valuable creature"
    assert bears.zone == "battlefield" and mine.zone == "battlefield"
    assert game.players[1].creatures_died_this_turn == 1


def test_the_player_who_sacrifices_chooses_among_their_own(card_db,
                                                          monkeypatch):
    game = _game()
    _put(game, card_db, "Grizzly Bears", "battlefield", 0)
    elves = _put(game, card_db, "Llanowar Elves", "battlefield", 1)
    giant = _put(game, card_db, "Hill Giant", "battlefield", 1)
    asked = []

    def choose(g, player_idx, legal):
        asked.append((player_idx, {c.instance_id for c in legal}))
        return max(legal, key=lambda c: c.power or 0)
    monkeypatch.setattr(game.callbacks, "choose_sacrifice", choose)
    _cast(game, card_db, "Sheoldred's Edict")
    assert asked == [(1, {elves.instance_id, giant.instance_id})]
    assert giant.zone == "graveyard" and elves.zone == "battlefield"


def test_each_player_sacrifices_and_with_fewer_sacrifices_all(card_db):
    """"Each player sacrifices two creatures": the caster with one gives
    up that one; the opponent with three gives up two."""
    game = _game()
    mine = _put(game, card_db, "Grizzly Bears", "battlefield", 0)
    theirs = [_put(game, card_db, "Grizzly Bears", "battlefield", 1)
              for _ in range(3)]
    _cast(game, card_db, "Barter in Blood",
          lands=("Swamp", "Swamp", "Swamp", "Swamp"))
    assert mine.zone == "graveyard"
    assert sum(c.zone == "graveyard" for c in theirs) == 2


def test_a_token_edict_takes_only_a_token(card_db, monkeypatch):
    from ai import modal
    monkeypatch.setattr(modal, "select_modal_modes", lambda *a, **k: [1])
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    token = _token(game, 1)
    _cast(game, card_db, "Sheoldred's Edict")
    assert token not in game.players[1].battlefield
    assert bears.zone == "battlefield"


def test_a_target_player_sacrifices(card_db):
    from engine.constants import PLAYER_TARGET_OPPONENT
    game = _game()
    victim = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast(game, card_db, "Diabolic Edict", [PLAYER_TARGET_OPPONENT],
          lands=("Swamp", "Swamp"))
    assert victim.zone == "graveyard"


def test_a_player_with_nothing_to_sacrifice_sacrifices_nothing(card_db):
    game = _game()
    mine = _put(game, card_db, "Grizzly Bears", "battlefield", 0)
    _cast(game, card_db, "Sheoldred's Edict")
    assert mine.zone == "battlefield"


def test_no_card_name_handler_resolves_a_modal_spell_the_modal_owner_does(
        card_db):
    """The modal path resolves before the registry: a name-keyed handler
    for one of its spells never runs, so none is kept."""
    from engine import modal_spell
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    held = [t.name for t in card_db.cards.values()
            if modal_spell.in_scope(t)
            and EFFECT_REGISTRY.has_handler(t.name,
                                            EffectTiming.SPELL_RESOLVE)]
    assert held == []


# ── The AI ─────────────────────────────────────────────────────────────

def test_an_edict_mode_is_worth_what_it_takes(card_db):
    """The opponent gives up their least valuable match: a mode that
    matches nothing of theirs is worth nothing, so the token mode is
    chosen against a token alone and the planeswalker mode against a
    planeswalker alone."""
    from ai.modal import select_modal_modes
    game = _game()
    spell = _put(game, card_db, "Sheoldred's Edict", "hand")
    _token(game, 1)
    assert select_modal_modes(game, spell, 0, []) == [1]
    game2 = _game()
    spell2 = _put(game2, card_db, "Sheoldred's Edict", "hand")
    _put(game2, card_db, "Kaito Shizuki", "battlefield", 1)
    assert select_modal_modes(game2, spell2, 0, []) == [2]


# ── Auditor (CR 701.21a) ───────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_an_edict_that_took_too_little(card_db, audit,
                                                        monkeypatch):
    from engine import effect_executors
    monkeypatch.setattr(effect_executors, "_sacrifice",          # the defect
                        lambda ctx, perm: None)
    game = _game()
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast(game, card_db, "Sheoldred's Edict")
    assert "701.21a/edict" in [f["rule"] for f in audit.drain()]


def test_the_audit_is_silent_when_the_edict_takes_its_count(card_db, audit):
    game = _game()
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast(game, card_db, "Sheoldred's Edict")
    assert "701.21a/edict" not in [f["rule"] for f in audit.drain()]
