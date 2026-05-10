"""Failing-first tests for sweep PR D — replace archetype-string gates with
profile-flag predicates.

The two anti-patterns being lifted:

  1. ``self.archetype in ("combo", "storm")`` in ev_player.py:419
     — gate for combo-chain detection. Should be replaced by the existing
     ``StrategyProfile.has_combo_chain`` flag.

  2. ``self.archetype in ('aggro', 'tempo')`` in ev_player.py:2180
     — gate that decides whether the closing-attack threshold reduction
     applies. Should be replaced by *always* applying
     ``StrategyProfile.aggro_closing_threshold_reduction``, with the
     non-aggressive profiles overriding the field to ``0.0`` (no-op
     subtraction).

  3. ``opp_archetype in ('combo', 'storm')`` in ev_player.py:2171
     — same as (1) but on the opponent's archetype string. Should be
     replaced by ``get_profile(opp_archetype).has_combo_chain``.

These tests pin the *profile distribution* — the flag values for each
archetype — not any one card. They run in <50ms and exercise pure data.
"""
from ai.strategy_profile import (
    AGGRO, MIDRANGE, CONTROL, COMBO, STORM, RAMP, TEMPO,
    PROFILES, get_profile,
)


# ──────────────────────────────────────────────────────────────────
# Rule 1 — has_combo_chain pins the COMBO+STORM gate
# ──────────────────────────────────────────────────────────────────

def test_has_combo_chain_true_for_combo_profile():
    """The COMBO archetype profile chains combos."""
    assert COMBO.has_combo_chain is True


def test_has_combo_chain_true_for_storm_profile():
    """The STORM archetype profile chains combos (it is the canonical
    combo-chaining archetype). Pinning this means the
    ``("combo", "storm")`` gate can be replaced by a single
    profile-flag check.
    """
    assert STORM.has_combo_chain is True


def test_has_combo_chain_false_for_non_combo_profiles():
    """AGGRO, MIDRANGE, CONTROL, RAMP, TEMPO do NOT chain combos.
    The flag must remain False for these so that replacing the
    archetype-string gate with the flag does not silently include them.
    """
    for profile in (AGGRO, MIDRANGE, CONTROL, RAMP, TEMPO):
        assert profile.has_combo_chain is False


def test_get_profile_storm_has_combo_chain():
    """``get_profile('storm')`` returns the STORM profile, whose
    has_combo_chain flag captures the opponent-side spell-deck check
    that ``opp_archetype in ('combo', 'storm')`` previously encoded.
    """
    assert get_profile("storm").has_combo_chain is True
    assert get_profile("combo").has_combo_chain is True
    assert get_profile("aggro").has_combo_chain is False
    assert get_profile("midrange").has_combo_chain is False


# ──────────────────────────────────────────────────────────────────
# Rule 2 — aggro_closing_threshold_reduction pins the AGGRO+TEMPO gate
# ──────────────────────────────────────────────────────────────────

def test_aggressive_closing_reduction_active_for_aggro():
    """AGGRO archetypes get a closing-threshold reduction when the
    opponent is at low life. This pins the value > 0.
    """
    assert AGGRO.aggro_closing_threshold_reduction > 0.0


def test_aggressive_closing_reduction_active_for_tempo():
    """TEMPO is also a racing archetype. Pre-fix it received the
    reduction via the ``('aggro', 'tempo')`` gate; post-fix the
    same reduction must be encoded on the profile itself.
    """
    assert TEMPO.aggro_closing_threshold_reduction > 0.0


def test_aggressive_closing_reduction_zero_for_non_aggressive():
    """Non-aggressive profiles (MIDRANGE, CONTROL, COMBO, STORM, RAMP)
    must NOT receive the closing-threshold reduction. With the gate
    removed in ev_player.py the reduction is applied unconditionally,
    so a value of 0.0 is required to preserve current behavior.
    """
    for profile in (MIDRANGE, CONTROL, COMBO, STORM, RAMP):
        assert profile.aggro_closing_threshold_reduction == 0.0, (
            f"Profile must have closing reduction = 0.0 to preserve "
            f"current gated behavior; got {profile.aggro_closing_threshold_reduction}"
        )


def test_aggressive_closing_reduction_value_matches_pre_fix():
    """Pin the reduction magnitude. Pre-fix this was ``2.0`` for
    AGGRO and TEMPO (inherited from the dataclass default). Post-fix
    each profile carries the value explicitly. The magnitude must
    remain ``2.0`` to preserve existing combat thresholds.
    """
    assert AGGRO.aggro_closing_threshold_reduction == 2.0
    assert TEMPO.aggro_closing_threshold_reduction == 2.0
