"""Train and emit the calibrated P_win logistic for ai.win_probability.

Pipeline:
  1. Load 2,058 turn-snapshots from `replays/*.txt` via
     `tools.parse_replay_snapshots`.
  2. Featurize: 8 numerical features (oriented from P1's perspective)
     + 16-way one-hot for `archetype_p1` and `archetype_p2`.
  3. Logistic regression by Newton-Raphson with L2 ridge λ=1.0.
  4. 80/20 train/test split (deterministic seed) for AUC + Brier.
  5. Platt scaling on the held-out 20% to map raw logits → calibrated
     probabilities.
  6. Per-pair WR offset table loaded from
     `metagame_results.json::matrix` and centered around 0.5 (logit
     scale, added at inference time as a free intercept).
  7. Emit `ai/win_probability_coeffs.json`.

  Risk fallback: if AUC < 0.65 with the full feature set, retrain with a
  single feature (`position_value`) and write `feature_set` =
  `position_value_only` in the JSON.

Pure stdlib — no numpy/scipy.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.parse_replay_snapshots import parse_replays_dir, REPLAY_FORMAT_V


# ─────────────────────────────────────────────────────────────
# Archetype index — fixed order so JSON is reproducible
# ─────────────────────────────────────────────────────────────
ARCHETYPE_INDEX: List[str] = [
    "Boros Energy", "Jeskai Blink", "Ruby Storm", "Affinity",
    "Eldrazi Tron", "Amulet Titan", "Goryo's Vengeance", "Domain Zoo",
    "Living End", "Izzet Prowess", "Dimir Midrange", "4c Omnath",
    "4/5c Control", "Azorius Control", "Azorius Control (WST)",
    "Pinnacle Affinity",
]
_ARCH_TO_IDX = {name: i for i, name in enumerate(ARCHETYPE_INDEX)}


# Numerical feature names (oriented from P1's perspective)
NUM_FEATURES: List[str] = [
    "life_diff",         # life_p1 - life_p2
    "hand_diff",         # hand_p1 - hand_p2
    "lands_diff",        # lands_p1 - lands_p2
    "lib_size_log",      # log(active_player_library + 1)
    "gy_size",           # observed graveyard counts (sum)
    "turn_number",       # game turn
    "opp_clock_to_lethal",  # crude life-as-clock proxy (capped at 99)
    "log_turn",          # log(turn_number+1) for nonlinearity
]


# ─────────────────────────────────────────────────────────────
# Featurization
# ─────────────────────────────────────────────────────────────

def _featurize(snap: Dict[str, Any]) -> Optional[List[float]]:
    """Build the per-snapshot feature vector. Returns None if archetype
    is unknown to the index (skip)."""
    if snap["p1_arch"] not in _ARCH_TO_IDX:
        return None
    if snap["p2_arch"] not in _ARCH_TO_IDX:
        return None

    life_p1 = snap["life_p1"]
    life_p2 = snap["life_p2"]
    hand_p1 = snap["hand_p1"]
    hand_p2 = snap["hand_p2"]
    lands_p1 = snap["lands_p1"]
    lands_p2 = snap["lands_p2"]
    lib_p1 = snap["lib_p1"]
    lib_p2 = snap["lib_p2"]
    gy_p1 = snap["gy_p1"]
    gy_p2 = snap["gy_p2"]
    turn = snap["turn"]

    # Library: only the active player's library is observed each turn.
    # Use whichever side is non-zero.
    lib_size = max(lib_p1, lib_p2)

    # opp_clock_to_lethal: a crude proxy assuming a "1 dmg/turn" floor.
    # Since we don't have per-snapshot power, fall back to a life-as-
    # clock surrogate: how many turns of life I have remaining.
    # Capped at 99.0 (sentinel from ai.clock).
    if life_p2 <= 0:
        opp_clock = 0.0  # dead → I won
    else:
        opp_clock = min(99.0, float(life_p2))

    feats: List[float] = [
        float(life_p1 - life_p2),
        float(hand_p1 - hand_p2),
        float(lands_p1 - lands_p2),
        math.log(lib_size + 1.0),
        float(gy_p1 + gy_p2),
        float(turn),
        opp_clock,
        math.log(turn + 1.0),
    ]

    # 16-way one-hot for archetype_p1 and archetype_p2
    n_arch = len(ARCHETYPE_INDEX)
    arch_p1_oh = [0.0] * n_arch
    arch_p2_oh = [0.0] * n_arch
    arch_p1_oh[_ARCH_TO_IDX[snap["p1_arch"]]] = 1.0
    arch_p2_oh[_ARCH_TO_IDX[snap["p2_arch"]]] = 1.0
    feats.extend(arch_p1_oh)
    feats.extend(arch_p2_oh)
    return feats


def _featurize_position_only(snap: Dict[str, Any]) -> Optional[List[float]]:
    """Fallback feature set: a single position-value-style feature.

    Approximates `ai.clock.position_value` from header-only data:
        clock_diff_proxy = (life_p2 - life_p1) / 5
    where life_p2 is opp life (positive = I'm ahead in the race).
    This is a single scalar plus a constant intercept term.
    """
    if snap["p1_arch"] not in _ARCH_TO_IDX:
        return None
    if snap["p2_arch"] not in _ARCH_TO_IDX:
        return None
    life_p1 = float(snap["life_p1"])
    life_p2 = float(snap["life_p2"])
    if life_p1 <= 0:
        return [-100.0]
    if life_p2 <= 0:
        return [100.0]
    return [(life_p2 - life_p1) / 5.0]


# ─────────────────────────────────────────────────────────────
# Logistic regression — Newton-Raphson with L2 ridge
# ─────────────────────────────────────────────────────────────

def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _matvec(M: List[List[float]], v: List[float]) -> List[float]:
    return [sum(M[i][j] * v[j] for j in range(len(v))) for i in range(len(M))]


def _solve_linear(A: List[List[float]], b: List[float]) -> List[float]:
    """Solve A x = b via Gaussian elimination with partial pivoting.
    A is square (n×n); A is mutated."""
    n = len(b)
    # Build augmented matrix
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        # Pivot
        pivot_row = col
        max_abs = abs(M[col][col])
        for r in range(col + 1, n):
            if abs(M[r][col]) > max_abs:
                max_abs = abs(M[r][col])
                pivot_row = r
        if max_abs < 1e-14:
            # Singular — use small ridge term to recover
            M[col][col] += 1e-9
            max_abs = abs(M[col][col])
        if pivot_row != col:
            M[col], M[pivot_row] = M[pivot_row], M[col]
        # Eliminate below
        pv = M[col][col]
        for r in range(col + 1, n):
            factor = M[r][col] / pv
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                M[r][c] -= factor * M[col][c]
    # Back-substitute
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = M[i][n]
        for j in range(i + 1, n):
            s -= M[i][j] * x[j]
        x[i] = s / M[i][i]
    return x


def fit_logistic(
    X: List[List[float]],
    y: List[int],
    lambda_l2: float = 1.0,
    max_iter: int = 50,
    tol: float = 1e-6,
) -> Tuple[List[float], float]:
    """Newton-Raphson logistic regression with L2 ridge.

    Returns (coeffs, intercept).  Intercept is NOT regularised.
    """
    n = len(y)
    if n == 0:
        raise ValueError("empty training set")
    p = len(X[0])
    # Augment X with a leading "1" column for the intercept
    Xa = [[1.0] + row for row in X]
    pa = p + 1
    beta = [0.0] * pa
    # Ridge penalty matrix: 0 for intercept (index 0), λ elsewhere
    ridge = [0.0] + [lambda_l2] * p

    for it in range(max_iter):
        # mu_i = sigmoid(Xa_i · beta)
        mu = [_sigmoid(sum(Xa[i][j] * beta[j] for j in range(pa))) for i in range(n)]
        # Gradient: Xa^T (mu - y) + ridge * beta
        grad = [0.0] * pa
        for i in range(n):
            r = mu[i] - y[i]
            row = Xa[i]
            for j in range(pa):
                grad[j] += row[j] * r
        for j in range(pa):
            grad[j] += ridge[j] * beta[j]

        # Hessian: Xa^T W Xa + diag(ridge); W_ii = mu_i (1 - mu_i)
        H = [[0.0] * pa for _ in range(pa)]
        for i in range(n):
            w = mu[i] * (1.0 - mu[i])
            row = Xa[i]
            # Outer product accumulation (upper + diag)
            for j in range(pa):
                rj = row[j] * w
                if rj == 0.0:
                    continue
                Hj = H[j]
                for k in range(j, pa):
                    Hj[k] += rj * row[k]
        # Mirror upper to lower
        for j in range(pa):
            for k in range(j + 1, pa):
                H[k][j] = H[j][k]
        for j in range(pa):
            H[j][j] += ridge[j]

        # Solve H · delta = grad ;  beta -= delta
        try:
            delta = _solve_linear(H, grad)
        except Exception:
            break
        new_beta = [beta[j] - delta[j] for j in range(pa)]
        # Convergence
        change = math.sqrt(sum((new_beta[j] - beta[j]) ** 2 for j in range(pa)))
        beta = new_beta
        if change < tol:
            break

    intercept = beta[0]
    coeffs = beta[1:]
    return coeffs, intercept


# ─────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────

def _predict(X: List[List[float]], coeffs: List[float], intercept: float) -> List[float]:
    return [
        _sigmoid(intercept + sum(X[i][j] * coeffs[j] for j in range(len(coeffs))))
        for i in range(len(X))
    ]


def _logit_predict(X: List[List[float]], coeffs: List[float], intercept: float) -> List[float]:
    return [
        intercept + sum(X[i][j] * coeffs[j] for j in range(len(coeffs)))
        for i in range(len(X))
    ]


def auc(probs: List[float], y: List[int]) -> float:
    """Mann-Whitney U–based AUC.  O(n log n)."""
    pairs = sorted(zip(probs, y))
    n_pos = sum(y)
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    # Rank-based: sum of ranks of positives
    rank_sum = 0.0
    i = 0
    n = len(pairs)
    while i < n:
        j = i
        while j < n and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j + 1) / 2.0  # 1-indexed
        for k in range(i, j):
            if pairs[k][1] == 1:
                rank_sum += avg_rank
        i = j
    u = rank_sum - n_pos * (n_pos + 1) / 2.0
    return u / (n_pos * n_neg)


def brier(probs: List[float], y: List[int]) -> float:
    if not probs:
        return float("nan")
    return sum((probs[i] - y[i]) ** 2 for i in range(len(y))) / len(y)


# ─────────────────────────────────────────────────────────────
# Platt scaling
# ─────────────────────────────────────────────────────────────

def fit_platt(z: List[float], y: List[int]) -> Tuple[float, float]:
    """Fit a 1-D logistic on raw logits z to recover (a, b) such that
    p_calibrated = sigmoid(a*z + b)."""
    X = [[z_i] for z_i in z]
    coeffs, intercept = fit_logistic(X, y, lambda_l2=0.0)
    return coeffs[0], intercept


# ─────────────────────────────────────────────────────────────
# WR offset table from metagame_results.json
# ─────────────────────────────────────────────────────────────

def load_wr_offsets(results_path: Path) -> Dict[str, float]:
    """Parse metagame_results.json::matrix into WR offsets on the logit
    scale.  Keys are ``"<p1>|<p2>"``; value is `logit(wr) - logit(0.5)`
    so that adding it to the universal logit shifts the prediction
    toward the observed pair WR.

    Values in `matrix` are P(P1 wins) as percentages.
    """
    if not results_path.exists():
        return {}
    try:
        with open(results_path) as f:
            r = json.load(f)
    except Exception:
        return {}
    matrix = r.get("matrix", {})
    out: Dict[str, float] = {}
    for k, v in matrix.items():
        try:
            wr = float(v) / 100.0
        except Exception:
            continue
        # Clamp to avoid log(0)
        wr = max(0.02, min(0.98, wr))
        out[k] = math.log(wr / (1.0 - wr))  # offset = logit(wr); adding to logits centers vs 50/50 implicitly
    return out


# ─────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────

def train(
    snapshots: List[Dict[str, Any]],
    feature_set: str = "full",
    seed: int = 20260425,
) -> Dict[str, Any]:
    """Fit the chosen feature-set and return an artifact dict."""
    if feature_set == "full":
        feats: List[List[float]] = []
        labels: List[int] = []
        for s in snapshots:
            f = _featurize(s)
            if f is None:
                continue
            feats.append(f)
            labels.append(1 if s["p1_won"] else 0)
        feature_names = list(NUM_FEATURES)
        for arch in ARCHETYPE_INDEX:
            feature_names.append(f"arch_p1__{arch}")
        for arch in ARCHETYPE_INDEX:
            feature_names.append(f"arch_p2__{arch}")
    elif feature_set == "position_value_only":
        feats = []
        labels = []
        for s in snapshots:
            f = _featurize_position_only(s)
            if f is None:
                continue
            feats.append(f)
            labels.append(1 if s["p1_won"] else 0)
        feature_names = ["clock_diff_proxy"]
    else:
        raise ValueError(f"unknown feature_set: {feature_set}")

    # 80/20 split
    rng = random.Random(seed)
    idx = list(range(len(feats)))
    rng.shuffle(idx)
    split = int(0.8 * len(idx))
    train_idx = idx[:split]
    test_idx = idx[split:]

    X_train = [feats[i] for i in train_idx]
    y_train = [labels[i] for i in train_idx]
    X_test = [feats[i] for i in test_idx]
    y_test = [labels[i] for i in test_idx]

    coeffs, intercept = fit_logistic(X_train, y_train, lambda_l2=1.0)

    # Train AUC
    p_train = _predict(X_train, coeffs, intercept)
    p_test = _predict(X_test, coeffs, intercept)
    train_auc = auc(p_train, y_train)
    test_auc = auc(p_test, y_test)
    test_brier = brier(p_test, y_test)

    # Platt scaling on raw test-logits
    z_test = _logit_predict(X_test, coeffs, intercept)
    platt_a, platt_b = fit_platt(z_test, y_test)

    # WR offsets
    wr_offsets = load_wr_offsets(ROOT / "metagame_results.json")

    return {
        "feature_set": feature_set,
        "feature_names": feature_names,
        "coeffs": coeffs,
        "intercept": intercept,
        "platt_a": platt_a,
        "platt_b": platt_b,
        "archetype_index": ARCHETYPE_INDEX,
        "wr_offsets": wr_offsets,
        "n_samples": len(feats),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "train_auc": train_auc,
        "test_auc": test_auc,
        "test_brier": test_brier,
        "lambda_l2": 1.0,
        "replay_format_v": REPLAY_FORMAT_V,
    }


def write_artifact(artifact: Dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(artifact, f, indent=2, sort_keys=False)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Train P_win calibrator")
    ap.add_argument("--replays", default=str(ROOT / "replays"))
    ap.add_argument("--out", default=str(ROOT / "ai" / "win_probability_coeffs.json"))
    ap.add_argument("--self-check", action="store_true",
                    help="Train, verify AUC≥0.65 (with fallback), write JSON")
    ap.add_argument("--seed", type=int, default=20260425)
    args = ap.parse_args(argv)

    snapshots = list(parse_replays_dir(args.replays))
    if not snapshots:
        print("No snapshots found — aborting", file=sys.stderr)
        return 1
    print(f"Loaded {len(snapshots)} snapshots from {args.replays}")

    artifact = train(snapshots, feature_set="full", seed=args.seed)
    print(
        f"[full] train_auc={artifact['train_auc']:.3f} "
        f"test_auc={artifact['test_auc']:.3f} "
        f"brier={artifact['test_brier']:.3f}"
    )

    if args.self_check and (
        artifact["test_auc"] is None
        or math.isnan(artifact["test_auc"])
        or artifact["test_auc"] < 0.65
    ):
        print("[fallback] AUC < 0.65, retrying with position_value_only")
        artifact = train(
            snapshots, feature_set="position_value_only", seed=args.seed
        )
        print(
            f"[fallback] train_auc={artifact['train_auc']:.3f} "
            f"test_auc={artifact['test_auc']:.3f} "
            f"brier={artifact['test_brier']:.3f}"
        )

    # Add training metadata
    import datetime as _dt
    artifact["train_date"] = _dt.datetime.utcnow().isoformat() + "Z"

    out_path = Path(args.out)
    write_artifact(artifact, out_path)
    print(f"Wrote {out_path}")

    if args.self_check:
        if artifact["test_auc"] < 0.65:
            print(f"FAIL: test_auc={artifact['test_auc']:.3f} < 0.65", file=sys.stderr)
            return 2
        if artifact["test_brier"] > 0.30:
            print(f"WARN: test_brier={artifact['test_brier']:.3f} > 0.30")
        print(f"OK: test_auc={artifact['test_auc']:.3f} ≥ 0.65")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
