"""A turn-scoped cast cap stops a spell beyond its count (CR 101.2, 604.1).

"Each player can't cast more than one spell each turn" binds every player
while its source is on the battlefield: a player who has cast one spell
this turn can't cast another. The count is of the spells the player has
cast this turn, those cast before the cap arrived included, and it starts
again each turn. A filtered cap ("more than one noncreature spell",
"... one non-Phyrexian spell") counts and stops only the spells its filter
names; "you can't cast more than one spell each turn" binds its controller
only. A free cast is still a cast (CR 601.2).

The defect: the grammar typed the unfiltered cap and nothing applied it,
and it read a filtered cap as a prohibition whose filter no reader knows.
The only cap the engine enforced was a name-keyed tag. High Noon never
stopped a second spell: the cn-post replay, Dimir Midrange vs Boros Ponza
s51000 game 2, has Dimir cast two Murktide Regents on T6 under it.

Class: 12 pool cards say "can't cast more than one" or "can't cast
additional"; eight are applied from their typed text, Ethersworn Canonist's
relative-clause wording through its legacy tag, and three are refused (an
enchanted player, a cap on spells cast "this way", a one-shot from an
activation). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase


def _game():
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 5
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


def _lands(game, card_db, name, n, idx=0):
    for _ in range(n):
        _put(game, card_db, name, "battlefield", idx)


def _cap(game, card_db, name, idx):
    """A cap's source on the battlefield, with the layer recalculated."""
    perm = _put(game, card_db, name, "battlefield", idx)
    game.continuous_effects.recalculate(game)
    return perm


def _bolt_face(game, card):
    assert game.cast_spell(0, card, targets=[PLAYER_TARGET_OPPONENT])
    game.resolve_stack()


# ── A cap of one ──────────────────────────────────────────────────────

def test_a_player_who_cast_a_spell_this_turn_cant_cast_another(card_db):
    game = _game()
    _lands(game, card_db, "Mountain", 3)
    _cap(game, card_db, "High Noon", 1)        # the opponent's: binds each player
    first, second = (_put(game, card_db, "Lightning Bolt", "hand")
                     for _ in range(2))
    assert game.can_cast(0, first)
    _bolt_face(game, first)
    assert not game.can_cast(0, second)
    assert second not in game.get_legal_plays(0)


def test_a_spell_cast_before_the_cap_arrived_counts(card_db):
    game = _game()
    _lands(game, card_db, "Mountain", 3)
    first, second = (_put(game, card_db, "Lightning Bolt", "hand")
                     for _ in range(2))
    _bolt_face(game, first)
    _cap(game, card_db, "Rule of Law", 0)
    assert not game.can_cast(0, second)


def test_the_count_starts_again_as_the_next_turn_begins(card_db):
    game = _game()
    _lands(game, card_db, "Mountain", 3)
    _cap(game, card_db, "High Noon", 1)
    first, second = (_put(game, card_db, "Lightning Bolt", "hand")
                     for _ in range(2))
    _bolt_face(game, first)
    assert not game.can_cast(0, second)
    game.players[0].reset_turn_tracking()      # the turn-begins path
    assert game.can_cast(0, second)


def test_off_the_battlefield_the_cap_binds_no_one(card_db):
    game = _game()
    _lands(game, card_db, "Mountain", 3)
    _put(game, card_db, "High Noon", "hand", 1)
    first, second = (_put(game, card_db, "Lightning Bolt", "hand")
                     for _ in range(2))
    game.continuous_effects.recalculate(game)
    _bolt_face(game, first)
    assert game.can_cast(0, second)


def test_a_free_cast_is_still_a_cast(card_db):
    from engine.cast_manager import CastManager
    game = _game()
    _lands(game, card_db, "Mountain", 3)
    _cap(game, card_db, "High Noon", 1)
    first = _put(game, card_db, "Lightning Bolt", "hand")
    _bolt_face(game, first)
    template = card_db.get_card("Lightning Bolt")
    assert not CastManager.free_cast_allowed(game, 0, template)


def test_a_plotted_card_is_not_castable_beyond_the_cap(card_db):
    game = _game()
    _lands(game, card_db, "Mountain", 3)
    _cap(game, card_db, "High Noon", 1)
    plotted = _put(game, card_db, "Slickshot Show-Off", "exile")
    plotted._plotted, plotted._plotted_turn = True, game.turn_number - 1
    assert game.can_cast_plotted(0, plotted)
    _bolt_face(game, _put(game, card_db, "Lightning Bolt", "hand"))
    assert not game.can_cast_plotted(0, plotted)


# ── Who it binds ──────────────────────────────────────────────────────

def test_a_cap_on_you_binds_only_its_controller(card_db):
    game = _game()
    _lands(game, card_db, "Mountain", 3)
    _cap(game, card_db, "Moderation", 1)       # "You can't cast more than one"
    first, second = (_put(game, card_db, "Lightning Bolt", "hand")
                     for _ in range(2))
    _bolt_face(game, first)
    assert game.can_cast(0, second)            # it binds player 1, not 0
    game.players[1].spells_cast_this_turn_kinds.append(
        (frozenset({"instant"}), frozenset()))
    from engine import rules_query
    assert rules_query.cast_prohibited(game, 1,
                                       card_db.get_card("Lightning Bolt"))


# ── A filtered cap counts and stops only what its filter names ────────

def test_a_noncreature_cap_counts_and_stops_only_noncreature_spells(card_db):
    game = _game()
    _lands(game, card_db, "Mountain", 3)
    _cap(game, card_db, "Deafening Silence", 1)
    first = _put(game, card_db, "Lightning Bolt", "hand")
    creature = _put(game, card_db, "Monastery Swiftspear", "hand")
    second = _put(game, card_db, "Lightning Bolt", "hand")
    assert game.cast_spell(0, creature)        # a creature spell: not counted
    game.resolve_stack()
    assert game.can_cast(0, first)
    _bolt_face(game, first)
    assert not game.can_cast(0, second)
    another = _put(game, card_db, "Monastery Swiftspear", "hand")
    assert game.can_cast(0, another)           # creatures are not capped


def test_a_non_phyrexian_cap_reads_the_spells_subtype(card_db):
    from engine import rules_query
    game = _game()
    _cap(game, card_db, "Phyrexian Censor", 1)
    p = game.players[0]
    p.spells_cast_this_turn_kinds.append(
        (frozenset({"creature"}), frozenset({"phyrexian", "horror"})))
    bolt = card_db.get_card("Lightning Bolt")
    assert not rules_query.cast_prohibited(game, 0, bolt)  # a Phyrexian: free
    p.spells_cast_this_turn_kinds.append((frozenset({"instant"}), frozenset()))
    assert rules_query.cast_prohibited(game, 0, bolt)


def test_a_nonartifact_cap_leaves_artifact_spells_free(card_db):
    from engine import rules_query
    game = _game()
    canonist = _cap(game, card_db, "Ethersworn Canonist", 1)
    canonist.instance_tags.add("canonist_active")   # its enter handler's tag
    game.continuous_effects.recalculate(game)
    p = game.players[0]
    p.spells_cast_this_turn_kinds.append((frozenset({"instant"}), frozenset()))
    assert rules_query.cast_prohibited(game, 0,
                                       card_db.get_card("Lightning Bolt"))
    assert not rules_query.cast_prohibited(game, 0,
                                           card_db.get_card("Ornithopter"))


# ── The grammar types the cap ─────────────────────────────────────────

def _limit(card_db, name):
    from engine.effect_model import ModKind
    from engine.effect_spec import iter_specs
    t = card_db.get_card(name)
    for host in t.effects.faces[0]:
        for s in iter_specs(host.specs):
            mod = s.payload
            if getattr(mod, "kind", None) is ModKind.LIMIT:
                return s, mod
    return None, None


@pytest.mark.parametrize("name, spell_filter", [
    ("High Noon", None),
    ("Deafening Silence", "noncreature"),
    ("Phyrexian Censor", "non_phyrexian"),
])
def test_the_grammar_types_a_cap_and_its_filter(card_db, name, spell_filter):
    spec, mod = _limit(card_db, name)
    assert mod is not None and mod.action == "cast"
    assert mod.get("max").n == 1
    assert mod.get("filter") == spell_filter


def test_a_cap_on_spells_cast_this_way_is_not_this_shape(card_db):
    assert _limit(card_db, "Hedonist's Trove") == (None, None)


# ── Auditor (CR 101.2) ────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_a_spell_cast_beyond_a_cap(card_db, audit,
                                                     monkeypatch):
    from engine import rules_query
    game = _game()
    _lands(game, card_db, "Mountain", 3)
    _cap(game, card_db, "High Noon", 1)
    first, second = (_put(game, card_db, "Lightning Bolt", "hand")
                     for _ in range(2))
    _bolt_face(game, first)
    monkeypatch.setattr(rules_query, "cast_prohibited",
                        lambda *a, **k: False)            # the defect
    assert game.cast_spell(0, second, targets=[PLAYER_TARGET_OPPONENT])
    assert "101.2/cast_limit" in [f["rule"] for f in audit.drain()]


def test_the_audit_is_silent_for_a_spell_within_the_cap(card_db, audit):
    game = _game()
    _lands(game, card_db, "Mountain", 3)
    _cap(game, card_db, "High Noon", 1)
    _cap(game, card_db, "Deafening Silence", 1)
    creature = _put(game, card_db, "Monastery Swiftspear", "hand")
    assert game.cast_spell(0, creature)        # the turn's first spell
    game.resolve_stack()
    assert "101.2/cast_limit" not in [f["rule"] for f in audit.drain()]
