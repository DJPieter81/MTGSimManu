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

import pytest

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
    # "Search your library for a creature with MV ≤ X, put it onto the
    # battlefield" with Harmonize alternative cost — the tutor resolves
    # correctly for the primary mode, but the Harmonize keyword causes
    # the resolve path to be counted as unhandled for the graveyard-cast
    # variant (Harmonize cost-reduction/exile sequence not modeled).
    ("Nature's Rhythm", "spell"),
    # "Target creature gets +2/+0 until end of turn. Create a Monster Role
    # token attached to it." — the ROLE TOKEN class (31 Modern cards):
    # an Aura token that attaches, replaces any other Role its controller
    # has on that creature, and grants a static buff (+1/+1 and trample
    # here). Not modeled.
    #
    # Deliberately allowlisted rather than half-implemented. The pump half
    # alone IS expressible, but shipping it without the Role would be the
    # "refuse rather than half-execute" rule inverted — the card would
    # silently apply +2/+0 where the real card applies +3/+1 and trample
    # permanently. The Role half needs the attached-permanent static-buff
    # infrastructure that is missing engine-wide (`_dynamic_base_power`
    # applies only artifact-COUNT scaling from `equipped_` tags; there is
    # no path for "enchanted/equipped creature gets +N/+M and has X"),
    # which is a hundreds-of-cards mechanic and would move combat maths
    # and the WR anchor broadly.
    #
    # Registered via the real UR Cutter Prowess list (2026-08-30). It was
    # EXPOSED, not caused, by the ordinal-cast-trigger fix in the same
    # session: stripping reminder text removed 15 pool-wide false-positive
    # token handlers, one of which had been firing on this card's Role
    # reminder text. The card was equally unmodeled before — a bogus
    # handler was masking it.
    ("Monstrous Rage", "spell"),
    # "Put a +1/+1 counter on each creature target player controls. Target
    # creature gains your choice of double strike or lifelink until end of
    # turn." + Flashback — the mass +1/+1-counter distribution class (put a
    # counter on EACH creature a player controls), not modeled by any
    # handler/oracle branch. Deliberately allowlisted rather than
    # half-implemented: the counter half alone is expressible, but shipping
    # it would buff the whole board of two registered decks (Domain Zoo,
    # Hollow One) and move the WR anchor, while still dropping the modal
    # double-strike/lifelink grant — a mechanic build that belongs in a
    # focused pass, not a card-flow-exposed fix. EXPOSED, not caused, by the
    # 2026-09-01 ETB-reveal-hand-exile fix: that fix shifted Domain Zoo's
    # game flow so the deck now reaches a turn where it casts this card at
    # the sweep seeds; the card was equally unmodeled before, simply never
    # cast in this deterministic sweep. Tracked in
    # docs/design/rules-foundation-sweep-tracker.md.
    ("Practiced Offense", "spell"),
    # ── Replacement path (newly observable as of the diagnostic's
    #    coverage of ZoneManager.move_card) ──────────────────────────
    # "If a card would be put into a(n opponent's) graveyard, exile it
    # instead" — the Rest in Peace / Leyline of the Void continuous
    # REPLACEMENT family. The engine models the sideboard ADVICE value of
    # these statics (ai/discard_advisor) but not the graveyard-exile
    # replacement itself: graveyard-bound cards still reach the graveyard.
    # Grandfathered here (make-it-visible-then-declare) until a generic
    # graveyard-exile replacement mechanic lands.
    ("Dauthi Voidwalker", "replacement"),
    ("Sanctifier en-Vec", "replacement"),
}


# Legitimate long work, not a hang: this drives every registered deck through
# two real games (25 decks x 2 seats = 50 games), which is the whole point —
# a narrower workload would stop covering the decks it exists to police, and
# CLAUDE.md is explicit that the suite-wide --timeout=120 "exists to catch
# HANGS, not to bound legitimate long work" and that shrinking a test to duck
# under the cap is the wrong fix.
#
# Measured 2026-08-29 on this container: 266s wall for the full 50-game sweep
# (the host has been ~4x slower since a restart; it was comfortably inside the
# suite default before). 600s leaves headroom for further deck registrations
# without re-tuning, while still bounding a genuine hang. Same convention and
# rationale as tests/test_parallel_matrix.py's exemption.
@pytest.mark.timeout(600)
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
