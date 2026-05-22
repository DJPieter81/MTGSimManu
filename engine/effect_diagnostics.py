"""Process-level sink for effects that resolved through no handler.

Card effects resolve through a three-layer chain: EFFECT_REGISTRY ->
``resolve_*_from_oracle`` -> the legacy ability parser. When all three miss,
the spell/ETB resolves to a silent no-op — an invisible simulation-fidelity
failure. The resolution code records those misses here so they become
observable (a test asserts the set is empty for the registered decks; a future
card that slips through turns the test red).

This is a diagnostic sink only — recording an event has no effect on the game.
"""

_unhandled: set[tuple[str, str]] = set()


def record_unhandled_effect(card_name: str, timing: str) -> None:
    """Record that ``card_name``'s effect at ``timing`` (e.g. "spell", "etb")
    matched no handler, oracle-resolver branch, or ability-parser verb."""
    _unhandled.add((card_name, timing))


def unhandled_effects() -> set[tuple[str, str]]:
    """Return the recorded (card_name, timing) misses observed so far."""
    return set(_unhandled)


def reset() -> None:
    """Clear the sink (call before a measurement window)."""
    _unhandled.clear()
