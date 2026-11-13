"""Resolution-time counts have one owner: engine/effect_conditions.py.

Design doc 2026-09-29, section 11 ("Amount"): the counts a resolving effect
reads -- card types among cards in a graveyard, permanents a player controls
of some kind, opponents who lost life this turn -- are computed in one
module, which both the legacy handlers (through adapters over their parsed
shapes) and the dispatcher's typed evaluators read. A second counting path
is how an engine and its dispatcher come to disagree about the same board.
"""
from __future__ import annotations

import ast
import random
from pathlib import Path

from engine import effect_conditions as ec
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "engine" / "effect_conditions.py"


def _template(name, types, subtypes=()):
    return CardTemplate(name=name, card_types=list(types), mana_cost=ManaCost(),
                        supertypes=[], subtypes=list(subtypes), power=None,
                        toughness=None, loyalty=None, keywords=set(),
                        abilities=[], color_identity=set(), produces_mana=[],
                        enters_tapped=False, oracle_text="", tags=set())


def _put(game, idx, template, zone):
    c = CardInstance(template=template, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        game.players[idx].battlefield.append(c)
    else:
        getattr(game.players[idx], zone).append(c)
    return c


def test_resolution_time_counts_read_no_oracle_text_and_write_no_state():
    """Counts are read from typed fields at resolution: no oracle text, no
    regex, and no assignment that could reach a game object."""
    tree = ast.parse(MODULE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("oracle_text", "text_lower"), node.lineno
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""])
            for m in mods:
                assert m not in ("re", "regex"), node.lineno
                assert "oracle" not in m and "effect_grammar" not in m, node.lineno
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = list(node.targets if isinstance(node, ast.Assign)
                           else [node.target])
            while targets:
                t = targets.pop()
                if isinstance(t, (ast.Tuple, ast.List)):     # a, b = ...
                    targets.extend(t.elts)
                    continue
                assert isinstance(t, ast.Name), (
                    f"line {node.lineno}: only local names are assigned")


def test_each_resolution_time_count_has_exactly_one_definition():
    """The counts that moved here are defined nowhere else in the engine
    or the AI, so a resolving effect cannot read a second copy."""
    moved = {"count_graveyard_card_types", "graveyard_card_types",
             "_scaler_count", "scaler_count",
             "_direct_damage_condition_met", "direct_damage_condition_met",
             "effective_direct_damage"}
    found = {}
    for root in ("engine", "ai"):
        for path in sorted((REPO / root).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.FunctionDef) and node.name in moved:
                    found.setdefault(node.name, []).append(
                        str(path.relative_to(REPO)))
    assert {n: p for n, p in found.items()
            if p != ["engine/effect_conditions.py"]} == {}


def test_card_types_in_a_graveyard_count_each_type_once():
    game = GameState(rng=random.Random(0))
    _put(game, 0, _template("A", [CardType.ARTIFACT, CardType.CREATURE]), "graveyard")
    _put(game, 0, _template("B", [CardType.CREATURE]), "graveyard")
    _put(game, 0, _template("C", [CardType.INSTANT]), "graveyard")
    _put(game, 1, _template("D", [CardType.SORCERY]), "graveyard")
    assert ec.graveyard_card_types(game, 0) == 3
    assert ec.graveyard_card_types(game, 1) == 1


def test_an_other_count_leaves_out_its_source_and_only_its_source():
    game = GameState(rng=random.Random(0))
    src = _put(game, 0, _template("Src", [CardType.CREATURE]), "battlefield")
    _put(game, 0, _template("Mate", [CardType.CREATURE]), "battlefield")
    _put(game, 1, _template("Foe", [CardType.CREATURE]), "battlefield")
    assert ec.scaler_count(game, 0, ("you_control", "creature", False), source=src) == 2
    assert ec.scaler_count(game, 0, ("you_control", "creature", True), source=src) == 1


def test_a_metalcraft_label_holds_from_its_third_artifact():
    game = GameState(rng=random.Random(0))
    for i in range(2):
        _put(game, 0, _template(f"Mox{i}", [CardType.ARTIFACT]), "battlefield")
    assert not ec.direct_damage_condition_met(game, 0, "metalcraft")
    _put(game, 0, _template("Mox2", [CardType.ARTIFACT]), "battlefield")
    assert ec.direct_damage_condition_met(game, 0, "metalcraft")
    assert not ec.direct_damage_condition_met(game, 1, "metalcraft")
