"""Counters are put on each creature the named player controls (CR 122.1,
608.2c).

"Put N +1/+1 counter(s) on each [other] creature <you control | target
player controls>" puts that many counters on every creature that player
controls as it resolves -- through the one counter owner
(`CardInstance.add_plus_counters`, which applies the counter
replacements). A target player is chosen as the spell is cast (CR
601.2c, 115.1).

The defect: the dispatcher had no counters executor, and no legacy path
read the shape on a spell, so Inspiring Roar resolved as nothing and
Practiced Offense (Domain Zoo and Hollow One main deck x2) put no
counters at all.

Card names are fixture carriers: 124 pool cards put +1/+1 counters on
each creature a player controls.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
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


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(4):
        _add(game, card_db, "Plains", 0)
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    mine = [_add(game, card_db, "Grizzly Bears", 0) for _ in range(2)]
    theirs = [_add(game, card_db, "Grizzly Bears", 1)]
    return game, mine, theirs


def _resolve(game):
    while not game.stack.is_empty:
        game.resolve_stack()


def test_each_creature_you_control_gets_a_counter(card_db):
    game, mine, theirs = _game(card_db)
    roar = _add(game, card_db, "Inspiring Roar", 0, "hand")
    assert game.cast_spell(0, roar, [])
    _resolve(game)
    assert [c.plus_counters for c in mine] == [1, 1]
    assert [c.plus_counters for c in theirs] == [0]


def test_the_counters_go_through_the_counter_owner(card_db, monkeypatch):
    """A counter replacement (CR 614.1) applies: the owner is called once
    per creature, with the printed amount."""
    calls = []
    real = CardInstance.add_plus_counters

    def spy(self, n, game=None, source=None):
        calls.append((self.instance_id, n))
        return real(self, n, game, source)
    monkeypatch.setattr(CardInstance, "add_plus_counters", spy)
    game, mine, theirs = _game(card_db)
    roar = _add(game, card_db, "Inspiring Roar", 0, "hand")
    assert game.cast_spell(0, roar, [])
    _resolve(game)
    assert sorted(calls) == sorted((c.instance_id, 1) for c in mine)


# ── The auditor (CR 122.1) ────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "122.1/counters_placed"]


def test_the_audit_records_a_creature_left_without_its_counter(
        card_db, audit, monkeypatch):
    monkeypatch.setattr(CardInstance, "add_plus_counters",
                        lambda self, n, game=None, source=None: None)
    game, mine, theirs = _game(card_db)
    roar = _add(game, card_db, "Inspiring Roar", 0, "hand")
    assert game.cast_spell(0, roar, [])
    _resolve(game)
    assert _findings(audit)


def test_the_audit_is_silent_when_every_creature_gets_its_counter(card_db,
                                                                  audit):
    game, mine, theirs = _game(card_db)
    roar = _add(game, card_db, "Inspiring Roar", 0, "hand")
    assert game.cast_spell(0, roar, [])
    _resolve(game)
    assert not _findings(audit)


# ── "each creature target player controls" (CR 115.1, 601.2c) ─────────

_TARGET_PLAYER_TEXT = ("Put a +1/+1 counter on each creature target player "
                       "controls.")


def _target_player_host():
    from engine.effect_grammar import parse_text_effects
    return parse_text_effects(_TARGET_PLAYER_TEXT).spell(0)


def test_a_group_a_target_player_controls_is_that_players_group():
    """The target player is a requirement chosen as the spell is cast; the
    group is the creatures that player controls."""
    from engine.effect_spec import Ref, RefKind, Verb
    h = _target_player_host()
    (s,) = h.specs
    assert s.verb is Verb.PUT_COUNTERS
    assert [set(r.types) for r in h.targets] == [{"player"}]
    assert dict(s.subject.filter)["controller"] == Ref(RefKind.TARGET, 0)


def test_the_chosen_players_creatures_get_the_counters(card_db):
    from engine import effect_resolver as er
    game, mine, theirs = _game(card_db)
    host = _target_player_host()
    assert er.can_execute(host, "tokens_counters")
    assert er.resolve_ability(game, er.handle_of(mine[0]), 0, host, ((1,),),
                              family="tokens_counters",
                              source_object=mine[0])
    assert [c.plus_counters for c in theirs] == [1]
    assert [c.plus_counters for c in mine] == [0, 0]


# ── "Target creature gains your choice of <kw> or <kw> ..." (CR 608.2d) ─

def test_a_keyword_choice_is_typed_as_its_alternatives():
    """A19: the instruction carries one option per printed alternative; its
    target and duration are the instruction's."""
    from engine.effect_grammar import parse_text_effects
    from engine.effect_model import ModKind
    from engine.effect_resolver import is_choice
    h = parse_text_effects("Target creature gains your choice of double "
                           "strike or lifelink until end of turn.").spell(0)
    (s,) = [x for x in h.specs]
    assert is_choice(s) and s.target_slot == 0 and s.duration is not None
    assert [(a.payload.kind, [k for k, _ in a.payload.get("keywords")])
            for a in s.alternatives] == [
        (ModKind.ADD_KEYWORDS, ["double_strike"]),
        (ModKind.ADD_KEYWORDS, ["lifelink"])]


def test_practiced_offense_counters_the_chosen_players_team_and_grants_one_keyword(
        card_db):
    """Both instructions resolve: a +1/+1 counter on each creature the
    chosen player controls, then the chosen creature gains one of the two
    keywords (its controller's choice on resolution, CR 608.2d)."""
    from engine.cards import Keyword
    from engine.constants import PLAYER_TARGET_SELF
    game, mine, theirs = _game(card_db)
    for _ in range(3):
        _add(game, card_db, "Plains", 0)
    po = _add(game, card_db, "Practiced Offense", 0, "hand")
    assert game.cast_spell(0, po, [PLAYER_TARGET_SELF, mine[0].instance_id])
    _resolve(game)
    assert [c.plus_counters for c in mine] == [1, 1]
    assert [c.plus_counters for c in theirs] == [0]
    granted = {Keyword.DOUBLE_STRIKE, Keyword.LIFELINK} & mine[0].keywords
    assert len(granted) == 1
    assert not ({Keyword.DOUBLE_STRIKE, Keyword.LIFELINK} & mine[1].keywords)


# ── The announcement (CR 601.2c; AI) ──────────────────────────────────

def test_a_list_in_announcement_order_is_placed_on_the_slots_that_admit_it(
        card_db):
    """The cast's list is in announcement order (the AI appends a required
    player after its objects): each entry goes to the one slot whose
    requirement admits it."""
    from engine.cards import Keyword
    from engine.constants import PLAYER_TARGET_SELF
    game, mine, theirs = _game(card_db)
    for _ in range(3):
        _add(game, card_db, "Plains", 0)
    po = _add(game, card_db, "Practiced Offense", 0, "hand")
    assert game.cast_spell(0, po, [mine[0].instance_id, PLAYER_TARGET_SELF])
    _resolve(game)
    assert [c.plus_counters for c in mine] == [1, 1]
    assert {Keyword.DOUBLE_STRIKE, Keyword.LIFELINK} & mine[0].keywords


def test_the_ai_names_itself_for_counters_on_its_own_team(card_db):
    """The player a team-counter instruction names gains the counters:
    its value to the caster is a gain when it names the caster
    (`ai.modal.named_player_value`), so the caster names itself."""
    from ai.cast_targets import with_required_players
    from engine.constants import PLAYER_TARGET_SELF
    game, mine, theirs = _game(card_db)
    po = _add(game, card_db, "Practiced Offense", 0, "hand")
    out = with_required_players(game, 0, po, [mine[0].instance_id])
    assert PLAYER_TARGET_SELF in out


def test_a_target_that_became_illegal_leaves_the_others_to_resolve(card_db):
    """CR 608.2b: a target that is illegal on resolution is not affected,
    and the spell still does what it can with its legal ones -- it stays
    on the slot it was chosen for, never sending the whole spell to a path
    that does nothing (the matrix's "unhandled spell" for this card)."""
    from engine import effect_diagnostics
    from engine.constants import PLAYER_TARGET_SELF
    game, mine, theirs = _game(card_db)
    for _ in range(3):
        _add(game, card_db, "Plains", 0)
    po = _add(game, card_db, "Practiced Offense", 0, "hand")
    before = set(effect_diagnostics.unhandled_effects())
    assert game.cast_spell(0, po, [PLAYER_TARGET_SELF, mine[0].instance_id])
    game._creature_dies(mine[0])
    _resolve(game)
    assert mine[1].plus_counters == 1
    assert theirs[0].plus_counters == 0
    assert set(effect_diagnostics.unhandled_effects()) == before


# ── The auditor (CR 601.2c: a target on the slot it was chosen for) ────

def _placed_findings(audit):
    return [f for f in audit.drain() if f["rule"] == "601.2c/slot_placed"]


def test_the_audit_records_a_target_placed_on_a_slot_of_another_kind(
        card_db, audit, monkeypatch):
    from engine import effect_carrier
    from engine.constants import PLAYER_TARGET_SELF

    def crossed(game, controller, req, tid):              # the defect
        player_slot = "player" in req.types
        return player_slot != (tid == PLAYER_TARGET_SELF)
    monkeypatch.setattr(effect_carrier, "_admits", crossed)
    game, mine, theirs = _game(card_db)
    for _ in range(3):
        _add(game, card_db, "Plains", 0)
    po = _add(game, card_db, "Practiced Offense", 0, "hand")
    assert game.cast_spell(0, po, [PLAYER_TARGET_SELF, mine[0].instance_id])
    _resolve(game)
    assert _placed_findings(audit)


def test_the_audit_is_silent_when_each_target_sits_on_its_slot(card_db,
                                                               audit):
    from engine.constants import PLAYER_TARGET_SELF
    game, mine, theirs = _game(card_db)
    for _ in range(3):
        _add(game, card_db, "Plains", 0)
    po = _add(game, card_db, "Practiced Offense", 0, "hand")
    assert game.cast_spell(0, po, [mine[0].instance_id, PLAYER_TARGET_SELF])
    _resolve(game)
    assert not _placed_findings(audit)
