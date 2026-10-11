"""A graveyard-bound object is exiled instead while a static replacement
covers it (CR 614.1a, 614.6, 700.4).

"If a card or token would be put into a graveyard from anywhere, exile it
instead" (Rest in Peace), "If a card would be put into an opponent's
graveyard from anywhere, exile it instead" (Leyline of the Void), "If a
black or red permanent, spell, or card not on the battlefield would be put
into a graveyard, exile it instead" (Sanctifier en-Vec), "If a creature an
opponent controls would die, exile it instead" (the death family): a
replacement effect modifies the event as it would happen, so the object is
exiled and never reaches the graveyard (CR 614.6). A creature exiled
instead of dying did not die (CR 700.4), so nothing that waits for a death
happens.

The zone funnel recorded the miss and put the card in the graveyard: Rest
in Peace, Leyline of the Void and Sanctifier en-Vec (23 registered copies,
the Bo3 answers to Living End, Goryo's Vengeance and the reanimator decks)
did nothing but their other abilities.

Card names are fixture carriers: about 20 pool permanents print this
replacement over other objects.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
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


# ── The typed replacement ─────────────────────────────────────────────

def test_the_replacement_is_typed_by_whose_graveyard_and_which_objects(card_db):
    rip = card_db.get_card("Rest in Peace").graveyard_exile_replacements
    leyline = card_db.get_card("Leyline of the Void").graveyard_exile_replacements
    sanct = card_db.get_card("Sanctifier en-Vec").graveyard_exile_replacements
    assert [(r["whose"], r["scope"], r["colors"], r["tokens"]) for r in rip] \
        == [("any", "anywhere", None, True)]
    assert [(r["whose"], r["scope"]) for r in leyline] == [("opponents", "anywhere")]
    assert [(r["whose"], r["colors"]) for r in sanct] \
        == [("any", frozenset({"B", "R"}))]
    vren = card_db.get_card("Vren, the Relentless").graveyard_exile_replacements
    assert [(r["scope"], r["controlled_by"], r["types"]) for r in vren] \
        == [("dies", "opponents", frozenset({"creature"}))]


@pytest.mark.parametrize("name", [
    "Dauthi Voidwalker",          # exiled with a void counter: refused
    "The Darkness Crystal",       # and you gain 2 life: refused
    "Kumano, Master Yamabushi",   # dealt damage this turn: refused
    "Lightning Bolt",
])
def test_a_variant_the_engine_cannot_run_is_refused(card_db, name):
    assert not card_db.get_card(name).graveyard_exile_replacements


# ── The engine ─────────────────────────────────────────────────────────

def test_a_discarded_card_is_exiled_instead_under_rest_in_peace(card_db):
    game = _game()
    _put(game, card_db, "Rest in Peace", "battlefield", 1)
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    game.zone_mgr.move_card(game, bolt, "hand", "graveyard", cause="discard")
    assert bolt.zone == "exile" and bolt in game.players[0].exile
    assert bolt not in game.players[0].graveyard


def test_a_creature_exiled_instead_of_dying_did_not_die(card_db):
    """No death: the death count, dies triggers and observers stay
    silent (CR 700.4)."""
    game = _game()
    _put(game, card_db, "Rest in Peace", "battlefield", 1)
    bears = _put(game, card_db, "Grizzly Bears", "battlefield")
    game._creature_dies(bears)
    p = game.players[0]
    assert bears.zone == "exile" and bears in p.exile
    assert p.creatures_died_this_turn == 0


def test_a_resolved_spell_is_exiled_instead(card_db):
    from engine.constants import PLAYER_TARGET_OPPONENT
    game = _game()
    _put(game, card_db, "Rest in Peace", "battlefield", 1)
    _put(game, card_db, "Mountain", "battlefield")
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    assert game.cast_spell(0, bolt, targets=[PLAYER_TARGET_OPPONENT])
    game.resolve_stack()
    assert bolt.zone == "exile"


def test_leyline_exiles_only_an_opponents_cards(card_db):
    game = _game()
    _put(game, card_db, "Leyline of the Void", "battlefield", 1)
    mine = _put(game, card_db, "Lightning Bolt", "hand", 0)
    theirs = _put(game, card_db, "Lightning Bolt", "hand", 1)
    game.zone_mgr.move_card(game, mine, "hand", "graveyard")
    game.zone_mgr.move_card(game, theirs, "hand", "graveyard")
    assert mine.zone == "exile"            # player 0 is Leyline's opponent
    assert theirs.zone == "graveyard"


def test_sanctifier_exiles_only_black_or_red_objects(card_db):
    game = _game()
    _put(game, card_db, "Sanctifier en-Vec", "battlefield", 1)
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    path = _put(game, card_db, "Path to Exile", "hand")
    game.zone_mgr.move_card(game, bolt, "hand", "graveyard")
    game.zone_mgr.move_card(game, path, "hand", "graveyard")
    assert bolt.zone == "exile" and path.zone == "graveyard"


def test_the_death_family_exiles_only_an_opponents_dying_creature(card_db):
    game = _game()
    _put(game, card_db, "Vren, the Relentless", "battlefield", 1)
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 0)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", 0)
    game._creature_dies(bears)
    game.zone_mgr.move_card(game, bolt, "hand", "graveyard")
    assert bears.zone == "exile"
    assert bolt.zone == "graveyard"       # not a creature dying


def test_the_replacement_ends_when_its_permanent_leaves(card_db):
    game = _game()
    rip = _put(game, card_db, "Rest in Peace", "battlefield", 1)
    game.zone_mgr.move_card(game, rip, "battlefield", "exile")
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    game.zone_mgr.move_card(game, bolt, "hand", "graveyard")
    assert bolt.zone == "graveyard"


# ── Every arrival is asked (one funnel) ────────────────────────────────

def test_a_countered_spell_is_exiled_instead(card_db):
    """A handler that counters a spell moves it through the stack exit's
    owner, so the replacement sees it."""
    from engine.card_effects import consign_to_memory_resolve
    from engine.game_state import StackItem, StackItemType
    game = _game()
    _put(game, card_db, "Rest in Peace", "battlefield", 0)
    plating = _put(game, card_db, "Cranial Plating", "hand", 1)
    game.players[1].hand.remove(plating)
    plating.zone = "stack"
    game.stack.items.append(StackItem(item_type=StackItemType.SPELL,
                                      source=plating, controller=1,
                                      description=plating.name))
    consign = _put(game, card_db, "Consign to Memory", "hand")
    consign_to_memory_resolve(game, consign, 0, targets=[plating.instance_id])
    assert plating.zone == "exile" and plating in game.players[1].exile


def test_a_milled_card_is_exiled_instead(card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    game = _game()
    _put(game, card_db, "Rest in Peace", "battlefield", 1)
    lib = [_put(game, card_db, "Lightning Bolt", "library") for _ in range(5)]
    emry = _put(game, card_db, "Emry, Lurker of the Loch", "battlefield")
    EFFECT_REGISTRY.execute(emry.template.name, EffectTiming.ETB, game, emry, 0)
    assert [c.zone for c in lib] == ["exile"] * 4 + ["library"]
    assert not game.players[0].graveyard


def test_no_code_appends_to_a_graveyard_outside_the_zone_funnel():
    """The replacement applies only to a move it is asked about: every
    engine or AI write into a graveyard goes through the zone funnel
    (a revert of a provisional move is annotated `graveyard-revert:`)."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    funnel = {"engine/zone_manager.py", "engine/zone_transfer.py"}
    offenders = []
    for path in sorted([*root.glob("engine/*.py"), *root.glob("ai/*.py")]):
        rel = path.relative_to(root).as_posix()
        if rel in funnel:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if (re.search(r"graveyard\.(?:append|extend|insert)\(", line)
                    and "graveyard-revert:" not in line):
                offenders.append(f"{rel}:{n}")
    assert offenders == []


# ── Auditor (CR 614.6) ─────────────────────────────────────────────────

def test_the_audit_records_a_card_reaching_a_graveyard_under_the_replacement(
        card_db, monkeypatch):
    from engine import rules_audit
    from engine.zone_manager import ZoneManager
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    monkeypatch.setattr(ZoneManager, "graveyard_exile_source",
                        lambda self, game, card, from_zone: None)   # defect
    game = _game()
    _put(game, card_db, "Rest in Peace", "battlefield", 1)
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    game.zone_mgr.move_card(game, bolt, "hand", "graveyard")
    assert "614.6/graveyard_exile_replacement" in [
        f["rule"] for f in rules_audit.drain()]


def test_a_resolved_spell_exiled_instead_is_where_the_rules_put_it(
        card_db, monkeypatch):
    """The CR 608.2n audit reads the replacement too: a resolved spell
    whose text leaves it for the graveyard, exiled instead by a static
    (CR 614.6), is where the rules put it."""
    from engine import rules_audit
    from engine.constants import PLAYER_TARGET_OPPONENT
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    game = _game()
    _put(game, card_db, "Rest in Peace", "battlefield", 1)
    _put(game, card_db, "Mountain", "battlefield")
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    assert game.cast_spell(0, bolt, targets=[PLAYER_TARGET_OPPONENT])
    game.resolve_stack()
    assert bolt.zone == "exile"
    assert "608.2n/resolved_spell_destination" not in [
        f["rule"] for f in rules_audit.drain()]


def test_the_audit_is_silent_when_the_replacement_applies(card_db,
                                                         monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    game = _game()
    _put(game, card_db, "Rest in Peace", "battlefield", 1)
    bolt = _put(game, card_db, "Lightning Bolt", "hand")
    game.zone_mgr.move_card(game, bolt, "hand", "graveyard")
    assert "614.6/graveyard_exile_replacement" not in [
        f["rule"] for f in rules_audit.drain()]
