"""A memo that answers only for the very object it was built for.

`id()` is only a lookup key: CPython reuses a freed object's id, so a memo
keyed on it alone can hand a later object an earlier one's value, and which
one depends on the process's allocation history. `memo_on` checks identity
through a weak reference and drops the entry when its object is collected,
so a stale value can never reach a new object and the memo cannot grow
without bound. An object that cannot be weakly referenced is computed, not
memoised.
"""
from __future__ import annotations

import weakref
from typing import Any, Callable, Dict, Hashable, Tuple


def memo_on(cache: Dict[Hashable, Tuple[Any, Any]], anchor: Any,
            extra_key: Tuple[Hashable, ...], compute: Callable[[], Any]) -> Any:
    """`compute()` once per live `anchor` (and `extra_key`)."""
    key = (id(anchor),) + tuple(extra_key)
    entry = cache.get(key)
    if entry is not None and entry[0]() is anchor:
        return entry[1]
    value = compute()
    try:
        ref = weakref.ref(anchor, lambda _r, k=key: cache.pop(k, None))
    except TypeError:
        return value
    cache[key] = (ref, value)
    return value
