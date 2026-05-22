"""Every registered-deck spell must resolve through *some* handler.

Card effects resolve through a three-layer chain: EFFECT_REGISTRY ->
``resolve_spell_from_oracle`` -> the legacy ability parser
(engine/spell_resolution.py). When all three miss, an instant/sorcery resolves
to a SILENT no-op — the simulator quietly mis-plays the card with no error.
``engine/effect_diagnostics`` records those misses; this test drives every
registered deck through real games and asserts no instant/sorcery silently
no-ops, except a small, explicitly-justified allowlist of known gaps.

A new or edited card that slips through every handler turns this test red.
"""
from __future__ import annotations

from engine import effect_diagnostics
from decks.modern_meta import MODERN_DECKS
from run_meta import _get_runner, _run_game


# Known unhandled spell effects (card_name, timing). Each is a documented,
# out-of-scope gap — NOT a license to add more. Shrinking this set is the goal;
# growing it requires a justification here.
ALLOWED_UNHANDLED: set[tuple[str, str]] = {
    # Mass-reanimate is resolved via _resolve_living_end on the CASCADE path
    # (cast_manager._handle_cascade). When Living End is instead suspend-cast it
    # transits _execute_spell_effects, where that special path isn't invoked —
    # a real gap in the suspend route, tracked separately.
    ("Living End", "spell"),
    # Cascade itself (the payoff) is handled by the engine cascade mechanic;
    # only Demonic Dread's minor "target creature gets -3/-0" rider is unmodeled.
    ("Demonic Dread", "spell"),
    # "Look at the top X cards, put one into your hand" — card-selection /
    # impulse-style advantage not modeled by the legacy parser.
    ("Consult the Star Charts", "spell"),
    # "Exile the top two cards; you may play them" — impulse draw not modeled
    # for this specific card by any handler/oracle branch.
    ("Wrenn's Resolve", "spell"),
}


def test_no_new_silent_unhandled_spell_effects():
    """Drive every deck (as the active caster) through a deterministic game and
    assert no instant/sorcery resolves to a silent no-op outside the allowlist."""
    effect_diagnostics.reset()

    decks = list(MODERN_DECKS.keys())
    runner = _get_runner()
    seed = 41000
    # Each deck plays as caster against the next deck, and as opponent in the
    # reverse pairing — covers every deck's spell suite in both seats.
    for i, d1 in enumerate(decks):
        d2 = decks[(i + 1) % len(decks)]
        _run_game(runner, d1, d2, seed)
        _run_game(runner, d2, d1, seed + 1)
        seed += 2

    observed = effect_diagnostics.unhandled_effects()
    unexpected = observed - ALLOWED_UNHANDLED

    assert not unexpected, (
        "Instant/sorcery effects resolved to a SILENT no-op (no EFFECT_REGISTRY "
        "handler, no resolve_spell_from_oracle branch, no ability-parser verb):\n"
        + "\n".join(f"  {timing}: {name}" for name, timing in sorted(unexpected))
        + "\nAdd a handler/oracle branch, or — if intentionally out of scope — "
        "add it to ALLOWED_UNHANDLED with a justification."
    )
