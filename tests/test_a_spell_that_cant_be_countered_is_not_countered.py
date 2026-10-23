"""A spell that can't be countered is not countered by anything that would
counter it (CR 101.2, 701.6a).

"This spell can't be countered" -- and a permanent's "<kind> spells you
control can't be countered" -- is a "can't" effect, and it beats every
instruction that would counter the spell (CR 101.2). None of these counter
it, and it still resolves:
- a counterspell aimed at it;
- a cast-triggered counter that names it;
- the ward trigger it sets off (CR 702.21a);
- the counter of a colorless spell.

A "triggers only once each turn" counter that it triggers is still spent for
the turn.

The defect: no counter path read it. Each of those four countered such a
spell, and the AI read uncounterability from a substring of the oracle text,
so a creature whose mana ability grants it to another spell read as
uncounterable itself.

Class: 81 pool cards print "This spell can't be countered.", and permanents
grant it to the spells a description names (creature spells you control,
noncreature spells, instant and sorcery spells, creature spells, every
spell). Refused, and recorded: a printed condition ("If X is 5 or more"), a
grant tied to the mana that paid for the spell (Cavern of Souls, Boseiju,
Delighted Halfling), a grant bounded by a characteristic ("with power 5 or
greater"). Registered: Supreme Verdict (x8), Dovin's Veto (x6), Chandra,
Awakened Inferno (x1). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase
from engine.stack import StackItem, StackItemType


def _game(active=1):
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = active
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
    if zone != "stack":
        getattr(game.players[idx], zone).append(c)
    return c


def _on_stack(game, card_db, name, idx, targets=None):
    """`name` on the stack as a spell `idx` cast earlier."""
    card = _put(game, card_db, name, "stack", idx)
    item = StackItem(item_type=StackItemType.SPELL, source=card,
                     controller=idx, targets=list(targets or []))
    game.stack.push(item)
    return card, item


def _lands(game, card_db, name, n, idx):
    for _ in range(n):
        _put(game, card_db, name, "battlefield", idx)


def _counter(game, card_db, counter_name, target, idx=0):
    """Cast `counter_name` by `idx` at the spell `target` and resolve it."""
    _lands(game, card_db, "Island", 2, idx)
    counter = _put(game, card_db, counter_name, "hand", idx)
    assert game.cast_spell(idx, counter, targets=[target.instance_id])
    game.resolve_stack()                      # the counter, on top


def _on_the_stack(game, card):
    return any(i.source is card for i in game.stack.items)


# ── A counterspell ───────────────────────────────────────────────────

@pytest.mark.parametrize("counter", ["Counterspell", "Mana Leak"])
def test_a_counterspell_does_not_counter_a_spell_that_cant_be_countered(
        card_db, counter):
    game = _game()
    verdict, _ = _on_stack(game, card_db, "Supreme Verdict", 1)
    _counter(game, card_db, counter, verdict)
    assert _on_the_stack(game, verdict)


def test_a_counterspell_counters_an_ordinary_spell(card_db):
    game = _game()
    wrath, _ = _on_stack(game, card_db, "Wrath of God", 1)
    _counter(game, card_db, "Counterspell", wrath)
    assert not _on_the_stack(game, wrath)


# ── A cast-triggered counter ─────────────────────────────────────────

def test_a_cast_triggered_counter_does_not_counter_a_spell_that_cant_be_countered(
        card_db):
    game = _game()
    chalice = _put(game, card_db, "Chalice of the Void", "battlefield", 0)
    chalice.other_counters["charge"] = 4
    _lands(game, card_db, "Plains", 4, 1)
    verdict = _put(game, card_db, "Supreme Verdict", "hand", 1)   # {1}{W}{W}{U}
    _lands(game, card_db, "Island", 1, 1)
    assert game.cast_spell(1, verdict)
    assert _on_the_stack(game, verdict)


def test_a_once_each_turn_counter_is_spent_on_a_spell_that_cant_be_countered(
        card_db):
    game = _game()
    _put(game, card_db, "Jin-Gitaxias, Progress Tyrant", "battlefield", 0)
    memnite = _put(game, card_db, "Memnite", "battlefield", 0)
    _lands(game, card_db, "Swamp", 1, 1)
    _lands(game, card_db, "Forest", 1, 1)
    decay = _put(game, card_db, "Abrupt Decay", "hand", 1)
    assert game.cast_spell(1, decay, targets=[memnite.instance_id])
    assert _on_the_stack(game, decay)
    _lands(game, card_db, "Mountain", 1, 1)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", 1)
    assert game.cast_spell(1, bolt, targets=[PLAYER_TARGET_OPPONENT])
    assert _on_the_stack(game, bolt)          # the turn's trigger is spent


# ── Ward, and the counter of a colorless spell ───────────────────────

def test_ward_does_not_counter_a_spell_that_cant_be_countered(card_db):
    game = _game()
    familiar = _put(game, card_db, "Armguard Familiar", "battlefield", 0)  # ward {2}
    _lands(game, card_db, "Swamp", 1, 1)
    _lands(game, card_db, "Forest", 1, 1)
    decay = _put(game, card_db, "Abrupt Decay", "hand", 1)
    assert game.cast_spell(1, decay, targets=[familiar.instance_id])
    game.resolve_stack()                      # no mana left for ward
    assert familiar.zone == "graveyard"


def test_the_counter_of_a_colorless_spell_spares_one_that_cant_be_countered(
        card_db):
    game = _game()
    emrakul, _ = _on_stack(game, card_db, "Emrakul, the Aeons Torn", 1)
    _lands(game, card_db, "Island", 1, 0)
    consign = _put(game, card_db, "Consign to Memory", "hand", 0)
    assert game.cast_spell(0, consign, targets=[emrakul.instance_id])
    game.resolve_stack()
    assert _on_the_stack(game, emrakul)


# ── A permanent's grant, to the spells its description names ─────────

def test_a_grant_covers_the_spells_its_controller_controls_of_its_kind(card_db):
    game = _game()
    _put(game, card_db, "Prowling Serpopard", "battlefield", 1)  # creature spells you control
    bears, _ = _on_stack(game, card_db, "Grizzly Bears", 1)
    _counter(game, card_db, "Counterspell", bears)
    assert _on_the_stack(game, bears)
    bolt, _ = _on_stack(game, card_db, "Lightning Bolt", 1, [PLAYER_TARGET_OPPONENT])
    _counter(game, card_db, "Counterspell", bolt)
    assert not _on_the_stack(game, bolt)      # not a creature spell


def test_a_grant_to_its_controller_spares_no_other_players_spell(card_db):
    game = _game(active=0)
    _put(game, card_db, "Prowling Serpopard", "battlefield", 1)
    bears, _ = _on_stack(game, card_db, "Grizzly Bears", 0)
    _counter(game, card_db, "Counterspell", bears, idx=1)
    assert not _on_the_stack(game, bears)


def test_a_grant_to_every_player_covers_each_players_spells(card_db):
    game = _game(active=0)
    _put(game, card_db, "Leyline of Lifeforce", "battlefield", 1)  # creature spells
    bears, _ = _on_stack(game, card_db, "Grizzly Bears", 0)
    _counter(game, card_db, "Counterspell", bears, idx=1)
    assert _on_the_stack(game, bears)


def test_a_grant_that_excludes_a_type_counts_its_complement(card_db):
    game = _game()
    geist = _put(game, card_db, "Malevolent Hermit // Benevolent Geist",
                 "battlefield", 1)
    geist.is_transformed = True        # its back face, as a disturbed card enters
    bolt, _ = _on_stack(game, card_db, "Lightning Bolt", 1, [PLAYER_TARGET_OPPONENT])
    _counter(game, card_db, "Counterspell", bolt)
    assert _on_the_stack(game, bolt)          # noncreature spells you control
    bears, _ = _on_stack(game, card_db, "Grizzly Bears", 1)
    _counter(game, card_db, "Counterspell", bears)
    assert not _on_the_stack(game, bears)


# ── The rule's one read; refused shapes ──────────────────────────────

def test_the_spells_own_text_is_read_from_its_parsed_effects(card_db):
    from engine.rules_query import spell_can_be_countered
    game = _game()
    assert not spell_can_be_countered(game, 0, card_db.get_card("Supreme Verdict"))
    assert spell_can_be_countered(game, 0, card_db.get_card("Wrath of God"))


@pytest.mark.parametrize("name", ["Banefire", "Delighted Halfling",
                                  "Cavern of Souls"])
def test_a_conditional_or_mana_linked_grant_is_refused(card_db, name):
    from engine.rules_query import spell_can_be_countered
    game = _game()
    assert spell_can_be_countered(game, 0, card_db.get_card(name))
    if not card_db.get_card(name).is_land:
        _put(game, card_db, name, "battlefield", 0)
        assert spell_can_be_countered(game, 0, card_db.get_card("Grizzly Bears"))


def test_a_grant_bounded_by_a_characteristic_is_refused(card_db):
    from engine.rules_query import spell_can_be_countered
    game = _game()
    _put(game, card_db, "Spellbreaker Behemoth", "battlefield", 0)
    assert spell_can_be_countered(game, 0, card_db.get_card("Grizzly Bears"))


# ── The AI reads the same rule ───────────────────────────────────────

def test_the_ai_reads_a_grant_in_a_mana_ability_as_no_grant_to_its_card(card_db):
    """Delighted Halfling's mana ability makes a legendary spell its mana
    paid for uncounterable; the Halfling itself can be countered. The AI
    read the phrase anywhere in the text."""
    from ai.response import ResponseDecider
    from ai.turn_planner import TurnPlanner
    game = _game()
    halfling, item = _on_stack(game, card_db, "Delighted Halfling", 1)
    _lands(game, card_db, "Island", 2, 0)
    counter = _put(game, card_db, "Counterspell", "hand", 0)
    decider = ResponseDecider(0, TurnPlanner(), strategic_logger=None)
    assert decider._responses_that_can_answer(game, item, [counter]) == [counter]
    verdict, item2 = _on_stack(game, card_db, "Supreme Verdict", 1)
    assert decider._responses_that_can_answer(game, item2, [counter]) == []


def test_the_ai_reads_a_cast_counter_as_no_counter_of_an_uncounterable_spell(
        card_db):
    from ai.effective_cmc import counter_on_cast
    game = _game()
    chalice = _put(game, card_db, "Chalice of the Void", "battlefield", 0)
    chalice.other_counters["charge"] = 2
    decay = _put(game, card_db, "Abrupt Decay", "hand", 1)
    assert counter_on_cast(decay, game=game, player_idx=1) is None
    bolt_like = _put(game, card_db, "Fatal Push", "hand", 1)       # MV 1
    chalice.other_counters["charge"] = 1
    assert counter_on_cast(bolt_like, game=game, player_idx=1) is chalice


# ── Auditor (CR 101.2) ───────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_countered_spell_that_cant_be_countered(
        card_db, audit, monkeypatch):
    from engine import rules_query
    monkeypatch.setattr(rules_query, "can_be_countered",
                        lambda game, item: True)              # the defect
    game = _game()
    verdict, _ = _on_stack(game, card_db, "Supreme Verdict", 1)
    _counter(game, card_db, "Counterspell", verdict)
    assert "101.2/uncounterable" in _rules(audit)


def test_the_audit_is_silent_when_the_rule_holds(card_db, audit):
    game = _game()
    verdict, _ = _on_stack(game, card_db, "Supreme Verdict", 1)
    _counter(game, card_db, "Counterspell", verdict)
    wrath, _ = _on_stack(game, card_db, "Wrath of God", 1)
    _counter(game, card_db, "Counterspell", wrath)
    assert "101.2/uncounterable" not in _rules(audit)
