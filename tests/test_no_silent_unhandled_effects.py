"""Every registered-deck card effect must resolve through *some* handler.

Card effects resolve through parallel three-layer fallback chains in
``engine/spell_resolution.py``:

  * Spells: EFFECT_REGISTRY -> ``resolve_spell_from_oracle`` -> legacy
    ability parser.
  * ETBs:   EFFECT_REGISTRY -> ``resolve_etb_from_oracle`` -> generic
    ``trigger_etb`` over parsed ETB abilities. The ETB silent-miss
    predicate is keyed on a self-ETB phrase ("when/whenever/as <name>
    enters") so static-watcher triggers ("whenever ANOTHER X enters",
    Amulet of Vigor / Eldrazi Mimic / Risen Reef shape) don't false-positive.

When all three layers in either chain miss, the effect resolves to a SILENT
no-op — the simulator quietly mis-plays the card with no error.
``engine/effect_diagnostics`` records those misses; this test drives every
registered deck through real games and asserts no spell or ETB silently
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
    # Cascade itself (the payoff) is handled by the engine cascade mechanic;
    # only Demonic Dread's minor "target creature gets -3/-0" rider is unmodeled.
    ("Demonic Dread", "spell"),
    # "Look at the top X cards, put one into your hand" — card-selection /
    # impulse-style advantage not modeled by the legacy parser.
    ("Consult the Star Charts", "spell"),
    # "Exile the top two cards; you may play them" — impulse draw not modeled
    # for this specific card by any handler/oracle branch.
    ("Wrenn's Resolve", "spell"),
    # "Reveal top 4, may take a permanent to hand, mill the rest, make a
    # mana token" — same impulse/library-dig class as the two entries
    # above, not modeled by any handler/oracle branch. Newly registered
    # in Amulet Titan's Aug 2026 decklist refresh (PR #486); tracked here
    # rather than rushing a fresh engine mechanic into a data-only PR.
    ("Malevolent Rumble", "spell"),
    # "Look at top 5, may take a colorless card to hand, rest to bottom" —
    # same impulse/library-dig class as the entries above, not modeled.
    # Newly registered via Eldrazi Ramp / Broodscale Bloodchief (Aug 2026
    # meta-gap fill, PR #486).
    ("Ancient Stirrings", "spell"),
    # "Each player draws 3, then discards 3 at random" — symmetric
    # wheel/hellbent effect, not modeled by any handler/oracle branch:
    # the spell resolves without drawing/discarding for either player.
    # Newly registered via Hollow One (Aug 2026 meta-gap fill, unblocked
    # by refresh_card_db.yml) as a core graveyard-fill enabler.
    ("Burning Inquiry", "spell"),
}


def test_no_new_silent_unhandled_spell_effects():
    """Drive every deck (as the active caster) through deterministic games and
    assert no instant/sorcery resolves to a silent no-op, and no permanent's
    self-ETB silently no-ops, outside the allowlist."""
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
        "Card effects resolved to a SILENT no-op (no EFFECT_REGISTRY handler, "
        "no resolve_*_from_oracle branch, no parser/trigger fallback):\n"
        + "\n".join(f"  {timing}: {name}" for name, timing in sorted(unexpected))
        + "\nAdd a handler/oracle branch, or — if intentionally out of scope — "
        "add it to ALLOWED_UNHANDLED with a justification."
    )
