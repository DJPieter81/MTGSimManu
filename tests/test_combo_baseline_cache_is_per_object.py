"""A cached combo baseline is served only for the snapshot it was built from.

The baseline cache in ai/combo_evaluator was keyed on `id(snap)` and
`id(me)`. CPython reuses a freed object's id, so a later snapshot could
land on a dead one's id and be handed its stale baseline — which one
depended on the process's allocation history (the cells a matrix worker
had played before). That made identical-code runs disagree on a few
combo cells. The cache now checks it holds the very same objects, and an
entry leaves when its snapshot dies.
"""
from __future__ import annotations

import gc

from ai import combo_evaluator as ce
from ai.ev_evaluator import EVSnapshot


class _Me:
    pass


def test_a_reused_object_id_never_serves_another_snapshots_baseline(monkeypatch):
    monkeypatch.setattr(ce, "_BASELINE_CACHE", {})
    monkeypatch.setattr(ce, "id", lambda o: 1, raising=False)   # address reuse
    me = _Me()
    first = ce._cached_baseline(EVSnapshot(), me, "combo", lambda: "first")
    second = ce._cached_baseline(EVSnapshot(), me, "combo", lambda: "second")
    assert (first, second) == ("first", "second")


def test_the_same_snapshot_is_served_from_the_cache(monkeypatch):
    monkeypatch.setattr(ce, "_BASELINE_CACHE", {})
    snap, me, calls = EVSnapshot(), _Me(), []
    for _ in range(2):
        ce._cached_baseline(snap, me, "combo", lambda: calls.append(1) or "v")
    assert calls == [1]


def test_an_entry_leaves_the_cache_when_its_snapshot_dies(monkeypatch):
    monkeypatch.setattr(ce, "_BASELINE_CACHE", {})
    me = _Me()
    ce._cached_baseline(EVSnapshot(), me, "combo", lambda: "v")
    gc.collect()
    assert ce._BASELINE_CACHE == {}
