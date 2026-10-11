"""Every name the engine and the AI load is bound (a NameError is a bug,
not a fallback).

A function that loads a name no module binds raises NameError when it
runs. Two callers of the turn planner's board (`ai/ev_player`'s combat
planner, `ai/response`'s response evaluation) catch every exception and
fall back, so `ai/evaluator.estimate_spell_value` reading a deleted
`_game_phase` / `GamePhase` turned both planners off for every hand that
held a spell -- 54 of 61 calls over nine games -- and nothing failed.

The scan is static and conservative: a load is flagged when its name is
bound nowhere in its module (no assignment, argument, import, def, class,
loop / with / except / comprehension target, global) and is no builtin.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__spec__", "__path__",
    "__package__", "__builtins__", "__loader__", "__annotations__"}


def _bound(tree: ast.AST) -> set:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
        elif isinstance(n, ast.MatchAs) and n.name:
            out.add(n.name)
    return out


def _unbound_loads(path: Path) -> list:
    tree = ast.parse(path.read_text())
    bound = _bound(tree) | _BUILTINS
    return [(str(path.relative_to(REPO)), n.lineno, n.id)
            for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and n.id not in bound]


@pytest.mark.parametrize("root", ["engine", "ai"])
def test_every_name_a_module_loads_is_bound(root):
    found = [hit for p in sorted((REPO / root).rglob("*.py"))
             for hit in _unbound_loads(p)]
    assert found == [], found


def test_the_turn_planners_board_builds_for_a_hand_holding_spells():
    """The board every turn-planner caller reads values each spell in
    hand; a hand of spells is the common case, not an error."""
    import random
    from engine.cards import CardInstance, CardTemplate, CardType
    from engine.game_state import GameState, Phase
    from engine.mana import ManaCost
    from ai.turn_planner import extract_virtual_board
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    for name, types, tags in (("Bear", [CardType.CREATURE], set()),
                              ("Ramp", [CardType.SORCERY], {"ramp"})):
        tmpl = CardTemplate(
            name=name, card_types=types, mana_cost=ManaCost(generic=2),
            supertypes=[], subtypes=[], power=2 if name == "Bear" else None,
            toughness=2 if name == "Bear" else None, loyalty=None,
            keywords=set(), abilities=[], color_identity=set(),
            produces_mana=[], enters_tapped=False, oracle_text="",
            tags=tags)
        game.players[0].hand.append(CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="hand"))
    board = extract_virtual_board(game, 0)
    assert sorted(s.name for s in board.my_hand) == ["Bear", "Ramp"]


def test_ramp_is_worth_more_while_the_game_has_turns_to_compound_it():
    """A ramp spell's bonus is read from the clock's stage of the game
    (`ai.clock.life_phase`): the early bonus while the game is developing,
    the late one when the opponent has lethal on board."""
    import random
    from engine.cards import CardInstance, CardTemplate, CardType
    from engine.game_state import GameState, Phase
    from engine.mana import ManaCost
    from ai.evaluator import estimate_spell_value
    from ai.scoring_constants import (RAMP_EARLY_GAME_BONUS,
                                      RAMP_LATE_GAME_BONUS)

    def _card(game, name, tags, types=(CardType.SORCERY,), power=None,
              zone="hand", idx=0):
        tmpl = CardTemplate(
            name=name, card_types=list(types), mana_cost=ManaCost(generic=2),
            supertypes=[], subtypes=[], power=power, toughness=power,
            loyalty=None, keywords=set(), abilities=[], color_identity=set(),
            produces_mana=["any"] if CardType.LAND in types else [],
            enters_tapped=False, oracle_text="", tags=tags)
        c = CardInstance(template=tmpl, owner=idx, controller=idx,
                         instance_id=game.next_instance_id(), zone=zone)
        c._game_state = game
        if zone == "battlefield":
            c.enter_battlefield()
            c.summoning_sick = False
        getattr(game.players[idx], zone).append(c)
        return c

    def _ramp_term(game):
        for _ in range(2):                  # it is affordable
            _card(game, "Land", set(), types=(CardType.LAND,),
                  zone="battlefield")
        ramp = _card(game, "Ramp", {"ramp"})
        plain = _card(game, "Plain", set())
        return (estimate_spell_value(ramp, game, 0)
                - estimate_spell_value(plain, game, 0))

    early = GameState(rng=random.Random(0))
    early.current_phase = Phase.MAIN1
    assert _ramp_term(early) == pytest.approx(RAMP_EARLY_GAME_BONUS)

    dying = GameState(rng=random.Random(0))
    dying.current_phase = Phase.MAIN1
    dying.players[0].life = 3
    _card(dying, "Beater", set(), types=(CardType.CREATURE,), power=5,
          zone="battlefield", idx=1)
    assert _ramp_term(dying) == pytest.approx(RAMP_LATE_GAME_BONUS)
