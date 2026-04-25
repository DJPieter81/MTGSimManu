"""Calibrated P(I win the game) evaluator.

Loads coefficients from ``ai/win_probability_coeffs.json`` (produced by
`tools/calibrate_win_probability.py`) at module import.  Provides:

    p_win(snap, my_arch, opp_arch) -> float   # in (0, 1)
    p_win_delta(before, after, my_arch, opp_arch) -> float

Pure stdlib — no numpy.  No engine imports (engine never scores).

Featurization is intentionally minimal: it consumes only the fields
already exposed by `EVSnapshot`, so any caller that already builds a
snapshot can ask "what's my P(win)?" with no extra cost.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ai.ev_evaluator import EVSnapshot


_COEFFS_PATH = Path(__file__).resolve().parent / "win_probability_coeffs.json"
_COEFFS: Optional[Dict[str, Any]] = None


def _load_coeffs() -> Optional[Dict[str, Any]]:
    """Read and cache the calibrator artifact.  Returns None if absent."""
    global _COEFFS
    if _COEFFS is not None:
        return _COEFFS
    if not _COEFFS_PATH.exists():
        return None
    try:
        with open(_COEFFS_PATH) as f:
            _COEFFS = json.load(f)
    except Exception:
        _COEFFS = None
    return _COEFFS


# Eager load so callers can introspect `_COEFFS`.
_load_coeffs()


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


# Numeric features must match `tools.calibrate_win_probability.NUM_FEATURES`.
_NUM_FEATURES_ORDER = [
    "life_diff",
    "hand_diff",
    "lands_diff",
    "lib_size_log",
    "gy_size",
    "turn_number",
    "opp_clock_to_lethal",
    "log_turn",
]


def _featurize(
    snap: "EVSnapshot",
    my_arch: str,
    opp_arch: str,
    archetype_index: List[str],
) -> List[float]:
    """Build the universal-model feature vector from P1 = "me" perspective."""
    my_life = max(0, int(getattr(snap, "my_life", 20)))
    opp_life = max(0, int(getattr(snap, "opp_life", 20)))
    my_hand = int(getattr(snap, "my_hand_size", 0))
    opp_hand = int(getattr(snap, "opp_hand_size", 0))
    my_lands = int(getattr(snap, "my_total_lands",
                           getattr(snap, "my_mana", 0)))
    opp_lands = int(getattr(snap, "opp_total_lands",
                            getattr(snap, "opp_mana", 0)))
    turn = int(getattr(snap, "turn_number", 1))
    my_gy = int(getattr(snap, "my_gy_creatures", 0))
    opp_gy = int(getattr(snap, "opp_gy_creatures", 0))

    # opp_clock_to_lethal proxy: capped life of opponent (lower = closer
    # to dead).  At inference the snapshot may not have observed lib
    # size, so substitute a constant (60-card deck minus 7 hand minus
    # turn ≈ 53 - turn).  Doesn't matter much — the calibrator weight
    # is small.
    if opp_life <= 0:
        opp_clock = 0.0
    else:
        opp_clock = min(99.0, float(opp_life))

    # Estimated active library size from turn count (open-information).
    est_lib = max(1, 53 - turn)

    feats: List[float] = [
        float(my_life - opp_life),
        float(my_hand - opp_hand),
        float(my_lands - opp_lands),
        math.log(est_lib + 1.0),
        float(my_gy + opp_gy),
        float(turn),
        opp_clock,
        math.log(turn + 1.0),
    ]

    # 16-way one-hot for each side.  If archetype not in the index,
    # leave the row all-zero (the universal logit still produces a
    # reasonable answer from the numeric features).
    n_arch = len(archetype_index)
    p1_oh = [0.0] * n_arch
    p2_oh = [0.0] * n_arch
    if my_arch in archetype_index:
        p1_oh[archetype_index.index(my_arch)] = 1.0
    if opp_arch in archetype_index:
        p2_oh[archetype_index.index(opp_arch)] = 1.0
    feats.extend(p1_oh)
    feats.extend(p2_oh)
    return feats


def _featurize_position_only(snap: "EVSnapshot") -> List[float]:
    """Fallback feature set: a single clock-diff scalar."""
    my_life = float(getattr(snap, "my_life", 20))
    opp_life = float(getattr(snap, "opp_life", 20))
    if my_life <= 0:
        return [-100.0]
    if opp_life <= 0:
        return [100.0]
    return [(opp_life - my_life) / 5.0]


def p_win(
    snap: "EVSnapshot",
    my_arch: str,
    opp_arch: str,
) -> float:
    """Calibrated logistic P(I win the game) ∈ (0, 1).

    Layers:
      1. Universal logistic on numeric + archetype-dummy features.
      2. Per-pair WR offset (logit-space) loaded from
         ``metagame_results.json``.
      3. Platt scaling on the resulting logit.

    Hard short-circuits on terminal positions:
      - my_life ≤ 0  → ε (loss)
      - opp_life ≤ 0 → 1 - ε (win)
    """
    # Hard rules: dead is dead.
    EPS = 1e-3
    my_life = int(getattr(snap, "my_life", 20))
    opp_life = int(getattr(snap, "opp_life", 20))
    if my_life <= 0:
        return EPS
    if opp_life <= 0:
        return 1.0 - EPS

    coeffs = _load_coeffs()
    if coeffs is None:
        # No calibrator — fall back to a simple life-ratio prior.
        total = my_life + opp_life
        if total <= 0:
            return 0.5
        raw = my_life / total
        return min(1.0 - EPS, max(EPS, raw))

    feature_set = coeffs.get("feature_set", "full")
    arch_idx = coeffs.get("archetype_index", [])
    if feature_set == "full":
        feats = _featurize(snap, my_arch, opp_arch, arch_idx)
    else:
        feats = _featurize_position_only(snap)

    coef = coeffs["coeffs"]
    intercept = coeffs["intercept"]
    z = intercept + sum(feats[j] * coef[j] for j in range(min(len(feats), len(coef))))

    # Per-pair WR offset (logit-space).  Encoded as logit(wr) in the
    # JSON; we add it as a free bias term.  Universal model has only
    # additive archetype dummies — pair offsets contribute the
    # interaction term.
    wr_offsets = coeffs.get("wr_offsets", {})
    key = f"{my_arch}|{opp_arch}"
    if key in wr_offsets:
        z += float(wr_offsets[key])

    # Platt: p = sigmoid(a * z + b).
    a = coeffs.get("platt_a", 1.0)
    b = coeffs.get("platt_b", 0.0)
    p = _sigmoid(a * z + b)

    # Clamp into the strict open interval (avoids log(0) downstream).
    return min(1.0 - EPS, max(EPS, p))


def p_win_delta(
    before: "EVSnapshot",
    after: "EVSnapshot",
    my_arch: str,
    opp_arch: str,
) -> float:
    """Δ(P_win) = p_win(after) - p_win(before)."""
    return p_win(after, my_arch, opp_arch) - p_win(before, my_arch, opp_arch)
