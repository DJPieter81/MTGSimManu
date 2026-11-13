"""A memo keyed on an object answers only for that very object.

`id()` is only a lookup key: CPython reuses a freed object's id, so a memo
keyed on it alone hands a later object the earlier one's value — which one
depends on allocation history, i.e. on whatever the process ran before.
`ai.object_memo.memo_on` checks identity through a weak reference and drops
the entry when its object is collected. Both per-snapshot combo memos (the
evaluator's baseline and the player's assessment) use it.
"""
from __future__ import annotations

import gc

from ai import object_memo
from ai.ev_evaluator import EVSnapshot


def test_a_reused_id_never_serves_another_objects_value(monkeypatch):
    monkeypatch.setattr(object_memo, "id", lambda o: 1, raising=False)
    cache = {}
    first = object_memo.memo_on(cache, EVSnapshot(), (), lambda: "first")
    second = object_memo.memo_on(cache, EVSnapshot(), (), lambda: "second")
    assert (first, second) == ("first", "second")


def test_the_same_object_is_answered_from_the_memo():
    cache, snap, calls = {}, EVSnapshot(), []
    for _ in range(2):
        object_memo.memo_on(cache, snap, (), lambda: calls.append(1) or "v")
    assert calls == [1]


def test_an_entry_leaves_when_its_object_is_collected():
    cache = {}
    object_memo.memo_on(cache, EVSnapshot(), (), lambda: "v")
    gc.collect()
    assert cache == {}


def test_the_player_assessment_memo_uses_the_identity_checked_memo():
    import inspect
    from ai import ev_player
    src = inspect.getsource(ev_player)
    assert "snap_id = id(snap)" not in src, "an id-only snapshot memo is back"
