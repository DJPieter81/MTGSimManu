"""Resolution sequencing for the typed effect model (design doc 2026-09-29).

E0 scope (A35): the resolution-time choices a resolving ability can ask of
its controller -- whether to perform an optional effect, how much of a
variable amount, which cards out of a pool, how to divide a total among
slots -- are declared on the engine->AI callback protocol, one channel per
KIND of choice. In E0 nothing calls them: the dispatcher has no callers,
and every legacy resolution path keeps its own choice code until its family
switches. The engine never scores; the AI answers.

The dispatcher tests (`resolve_ability`, `can_execute`, APNAP choices,
sub-abilities) join this file with `engine/effect_resolver.py`.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

RESOLUTION_CHOICES = {
    "choose_optional_effect": ["self", "ctx", "spec"],
    "choose_amount": ["self", "ctx", "spec", "lo", "hi", "remaining_specs"],
    "choose_cards": ["self", "ctx", "spec", "pool", "n"],
    "choose_division": ["self", "ctx", "spec", "slots", "total"],
}


def test_resolution_choice_callbacks_are_declared_on_the_protocol_and_defaults():
    from engine.callbacks import DefaultCallbacks, GameCallbacks
    for cls in (GameCallbacks, DefaultCallbacks):
        for name, params in RESOLUTION_CHOICES.items():
            fn = getattr(cls, name)
            assert list(inspect.signature(fn).parameters) == params, (cls, name)


def test_an_unanswered_resolution_choice_raises_rather_than_guessing():
    from engine.callbacks import DefaultCallbacks
    from engine.game_runner import AICallbacks
    for impl in (DefaultCallbacks(), AICallbacks()):
        with pytest.raises(NotImplementedError):
            impl.choose_optional_effect(None, None)
        with pytest.raises(NotImplementedError):
            impl.choose_amount(None, None, 0, 1, ())
        with pytest.raises(NotImplementedError):
            impl.choose_cards(None, None, (), 1)
        with pytest.raises(NotImplementedError):
            impl.choose_division(None, None, (), 2)


def _calls_to(names, roots=("engine", "ai")):
    hits = []
    for root in roots:
        for path in sorted((REPO / root).rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                name = (f.attr if isinstance(f, ast.Attribute)
                        else f.id if isinstance(f, ast.Name) else None)
                if name in names:
                    hits.append(f"{path.relative_to(REPO)}:{node.lineno} {name}")
                # getattr(obj, "choose_amount") is a call too.
                if (isinstance(f, ast.Name) and f.id == "getattr"
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value in names):
                    hits.append(f"{path.relative_to(REPO)}:{node.lineno} getattr")
    return hits


def test_resolution_choice_callbacks_are_declared_but_not_called_by_play_code():
    assert _calls_to(set(RESOLUTION_CHOICES)) == []
