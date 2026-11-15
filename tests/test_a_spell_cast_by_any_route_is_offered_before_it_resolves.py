"""A spell cast by any route is offered to the other player before it
resolves (CR 117.3b, 117.3c, 117.4; CR 514.3a).

A spell cast as an ability or effect resolves, or as a special action's
consequence, goes on the stack like any other: players receive priority, and
it resolves only once every player has passed. So a suspended card cast as
its last time counter is removed (CR 702.62a), a copy an imprint artifact
casts (CR 707.10), a plotted card cast from exile (CR 702.170d) and a
madness card cast while the cleanup step discards to hand size (CR 702.35a,
514.3a) are each offered to the other player before they resolve.

The engine models this with one owner: `GameRunner._resolve_stack_loop`
offers the player who doesn't control the top spell a response before it
resolves (unit PB). The defect: four routes resolved what they cast
themselves -- the suspend tick and the cleanup step each drained the stack
inline, the imprint copy resolved its copy directly, and the main phase's
plotted cast left its spell on the stack for the next decision -- so none of
those spells could ever be answered.

A cleanup step whose discard puts anything on the stack ends there; players
receive priority, and once the stack is empty another cleanup step begins
(CR 514.3a).

Several spells cast by upkeep triggers -- suspended cards whose last counters
come off together, rebound cards (CR 702.88a) -- are cast one trigger at a
time, and each spell resolves before the next trigger does (CR 603.3b,
117.4): two are never on the stack together. The rebound recasts were all
cast before any resolved, so a second one aimed at a creature the first
returned would have nothing to resolve against.

Class: every spell cast by a route other than the main phase's own cast --
suspend (53 pool cards), plot (31), madness (41), rebound (26), imprint
copies. Card names are fixture carriers.
"""
from __future__ import annotations

import ast
import pathlib
import random

import pytest

from engine.cards import CardInstance
from engine.constants import MAX_HAND_SIZE, PLAYER_TARGET_OPPONENT
from engine.game_runner import AICallbacks, GameRunner
from engine.game_state import GameState, Phase

_RULE = "117.4/priority_before_resolution"


class _Seat:
    """A stand-in for a seat's AI: records each offer and passes; picks its
    spells' targets at the opponent; plays its scripted main-phase plays."""

    def __init__(self, idx, plays=(), targets=None):
        self.player_idx = idx
        self.offers = []
        self.stack_sizes = []
        self.plays = list(plays)
        self.stack_at_decision = []
        self.targets = targets

    def decide_response(self, game, item):
        self.offers.append((item.source.name, item.source.zone))
        self.stack_sizes.append(len(game.stack.items))
        return None

    def _choose_targets(self, game, card):
        return self.targets() if self.targets else [PLAYER_TARGET_OPPONENT]

    def decide_optional_recast(self, game, card):
        return True

    def decide_main_phase(self, game, excluded_cards=None,
                          excluded_activations=None):
        self.stack_at_decision.append(
            [i.source.name for i in game.stack.items])
        return self.plays.pop(0) if self.plays else None


def _game(seats, active=0, phase=Phase.MAIN1):
    game = GameState(rng=random.Random(0), callbacks=AICallbacks())
    game.current_phase = phase
    game.active_player = game.priority_player = active
    game.turn_number = 5
    game.callbacks.bind_players(seats)
    return game


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


@pytest.fixture
def runner(card_db):
    return GameRunner(card_db)


@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f for f in audit.drain() if f["rule"] == _RULE]


# ── Suspend (CR 702.62a) ───────────────────────────────────────────────

def test_a_suspended_cards_last_counter_cast_waits_on_the_stack_and_is_offered(
        card_db, runner, audit):
    seats = [_Seat(0), _Seat(1)]
    game = _game(seats)
    _put(game, card_db, "Mountain", "battlefield", 0)
    bolt = _put(game, card_db, "Rift Bolt", "hand", 0)
    assert game.suspend_card(0, bolt)
    game.current_phase = Phase.UPKEEP
    game.tick_suspend_upkeep(0)
    assert bolt.zone == "stack"                 # cast, not yet resolved
    assert game.players[1].life == 20
    runner._resolve_stack_loop(game)
    assert seats[1].offers == [("Rift Bolt", "stack")]
    assert game.players[1].life == 17
    assert _violations(audit) == []


def test_each_suspended_spell_resolves_before_the_next_card_is_cast(
        card_db, runner):
    """Each card's last-counter trigger casts it, and that spell resolves
    before the next trigger does (CR 702.62a, 603.3b, 117.4): no two are on
    the stack together."""
    seats = [_Seat(0), _Seat(1)]
    game = _game(seats)
    for _ in range(2):
        _put(game, card_db, "Mountain", "battlefield", 0)
        assert game.suspend_card(0, _put(game, card_db, "Rift Bolt", "hand", 0))
    game.current_phase = Phase.UPKEEP
    game.tick_suspend_upkeep(0, after_cast=runner._resolve_stack_loop)
    assert seats[1].offers == [("Rift Bolt", "stack")] * 2
    assert seats[1].stack_sizes == [1, 1]
    assert game.players[1].life == 14


# ── Rebound (CR 702.88a) ───────────────────────────────────────────────

def test_each_rebound_recast_resolves_before_the_next_so_both_can_aim_at_one_creature(
        card_db, runner, audit):
    """Each rebound card's upkeep trigger casts it, and that spell resolves
    before the next trigger does (CR 702.88a, 603.3b, 117.4). A second
    recast is cast after the first has resolved, so it can target the
    creature the first one returned -- a new object (CR 400.7)."""
    game = _game([])
    body = _put(game, card_db, "Memnite", "battlefield", 0)
    seats = [_Seat(0, targets=lambda: [body.instance_id]), _Seat(1)]
    game.callbacks.bind_players(seats)
    game.current_phase = Phase.UPKEEP
    rebound = []
    for _ in range(2):
        rc = _put(game, card_db, "Ephemerate", "exile", 0)
        rc._rebound_controller = 0
        rebound.append(rc)
    game._rebound_cards = list(rebound)
    entry = body.battlefield_entry_seq
    runner._process_rebound_recasts(game, 0, seats[0])
    runner._resolve_stack_loop(game)
    assert seats[1].stack_sizes == [1, 1]
    assert body.battlefield_entry_seq >= entry + 2      # blinked twice
    assert _violations(audit) == []


# ── An imprinted copy (CR 707.10) ──────────────────────────────────────

def test_an_imprinted_copy_is_offered_to_the_other_player_before_it_resolves(
        card_db, runner, audit):
    seats = [_Seat(0), _Seat(1)]
    game = _game(seats, active=1, phase=Phase.UPKEEP)   # the holder's opponent's upkeep
    for _ in range(3):
        _put(game, card_db, "Plains", "battlefield", 0)
    scepter = _put(game, card_db, "Isochron Scepter", "battlefield", 0)
    chant = _put(game, card_db, "Orim's Chant", "exile", 0)
    chant.instance_tags.add("on_scepter")
    scepter.instance_tags.add("imprint:Orim's Chant")
    runner._process_imprint_copy_activations(game, 0, timing="opp_upkeep")
    assert scepter.tapped                       # the copy was cast
    assert seats[1].offers == [("Orim's Chant", "stack")]
    assert game.stack.is_empty                  # and then it resolved
    assert chant.zone == "exile"                # the imprinted card stays
    assert _violations(audit) == []


# ── A plotted card (CR 702.170d) ───────────────────────────────────────

def test_a_plotted_cast_is_offered_and_resolves_before_the_next_decision(
        card_db, runner, audit):
    game = _game([])
    showoff = _put(game, card_db, "Slickshot Show-Off", "exile", 0)
    showoff._plotted = True
    showoff._plotted_turn = game.turn_number - 1   # plotted on an earlier turn
    seats = [_Seat(0, [("cast_plotted", showoff, None)]), _Seat(1)]
    game.callbacks.bind_players(seats)
    runner._execute_main_phase(game, seats[0], seats[1])
    assert seats[1].offers == [("Slickshot Show-Off", "stack")]
    assert seats[0].stack_at_decision == [[], []]   # empty at each decision
    assert showoff.zone == "battlefield"
    assert _violations(audit) == []


# ── Madness in the cleanup step (CR 702.35a, 514.3a) ───────────────────

def _cleanup_with_a_madness_discard(card_db, monkeypatch, seats):
    game = _game(seats, phase=Phase.CLEANUP)
    me = game.players[0]
    for _ in range(MAX_HAND_SIZE):
        _put(game, card_db, "Mountain", "hand", 0)
    rootwalla = _put(game, card_db, "Blazing Rootwalla", "hand", 0)
    monkeypatch.setattr(game.callbacks, "choose_discard",
                        lambda g, idx, hand, self_discard: rootwalla)
    assert len(me.hand) == MAX_HAND_SIZE + 1
    return game, rootwalla


def test_a_cleanup_step_whose_discard_puts_a_spell_on_the_stack_ends_there(
        card_db, monkeypatch):
    game, rootwalla = _cleanup_with_a_madness_discard(
        card_db, monkeypatch, [_Seat(0), _Seat(1)])
    assert game.cleanup_step() is False         # CR 514.3a: players get priority
    assert not game.stack.is_empty
    while not game.stack.is_empty:
        game.resolve_stack()
    assert game.cleanup_step() is True          # another cleanup step begins
    assert rootwalla in game.players[0].battlefield


def test_a_madness_cast_in_the_cleanup_step_is_offered_before_it_resolves(
        card_db, runner, monkeypatch, audit):
    seats = [_Seat(0), _Seat(1)]
    game, rootwalla = _cleanup_with_a_madness_discard(card_db, monkeypatch,
                                                      seats)
    runner._cleanup_step(game)
    assert seats[1].offers == [("Blazing Rootwalla", "stack")]
    assert rootwalla in game.players[0].battlefield
    assert game.stack.is_empty
    assert len(game.players[0].hand) == MAX_HAND_SIZE
    assert _violations(audit) == []


# ── One owner ──────────────────────────────────────────────────────────

def _enclosing_functions(tree):
    """Each `resolve_stack` call in a module, with the function it is in."""
    found = []

    def visit(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call) \
                    and isinstance(child.func, ast.Attribute) \
                    and child.func.attr == "resolve_stack":
                found.append((owner, child.lineno))
            visit(child, owner)

    visit(tree, None)
    return found


def test_only_the_priority_loop_resolves_objects_on_the_stack():
    """Every resolution goes through `GameRunner._resolve_stack_loop`, the
    one owner of priority between resolutions. `GameState.resolve_stack`
    is the delegate it calls."""
    root = pathlib.Path(__file__).resolve().parent.parent
    owners = {"_resolve_stack_loop", "resolve_stack"}
    strays = []
    for layer in ("engine", "ai"):
        for path in sorted((root / layer).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for owner, line in _enclosing_functions(tree):
                if owner not in owners:
                    strays.append(f"{path.relative_to(root)}:{line} "
                                  f"in {owner}")
    assert strays == []
