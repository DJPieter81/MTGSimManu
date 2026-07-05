#!/usr/bin/env python3
"""Calibration check — ground-truth divergence audit over saved matrix results.

Method (docs/diagnostics/2026-07-05_calibration_probe_findings.md, on
PR #441's branch): matchups whose real-world Modern prior diverges
most from the sim matrix are the cheapest detectors of *generic*
architecture bugs — a pair the whole format agrees on (Azorius
Control vs Affinity is near-even; the pre-fix sim said 90-95 for
Affinity) that the sim gets badly wrong points at a broken subsystem,
not a mistuned deck.  This tool institutionalizes that probe:
~10 ground-truth matchup bands plus deck-level field bands live in
``tools/calibration_bands.json`` (schema-versioned DATA with a
provenance string per band, never code constants), and every
``run_meta.py --matrix --save`` auto-runs this check against the
freshly written ``metagame_results.json``.

Sibling of ``tools/replay_lint.py`` (merged PR #444) and mirrors its
pattern: non-fatal report, findings printed with anchors (here:
matchup anchors instead of game/turn anchors), standalone CLI plus an
auto-run hook in run_meta.py, schema-versioned input.

Out-of-band findings are diagnostics, not failures — the report exits
0 in hook mode so a sim run never fails on miscalibration.  Use
``--strict`` (CI / manual audits) to exit 1 when any band is missed.

Usage:
    python tools/check_calibration.py [results.json] [--bands path]
    python tools/check_calibration.py --strict          # exit 1 on OUT
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_BANDS_PATH = str(Path(__file__).resolve().parent /
                         "calibration_bands.json")
DEFAULT_RESULTS_PATH = str(Path(__file__).resolve().parent.parent /
                           "metagame_results.json")

# Only major version 1 band files are understood (mirrors the
# replay-dump schema convention from PR #444).
SUPPORTED_SCHEMA_MAJOR = "1"


@dataclass
class Finding:
    kind: str                 # "MATCHUP" | "FIELD"
    status: str               # "IN" | "OUT" | "SKIP"
    anchor: str               # "Azorius Control vs Affinity" / "Boros Energy (field)"
    sim_wr: Optional[float]
    band: Optional[Tuple[float, float]]
    direction: str = ""       # "above" | "below" | ""
    provenance: str = ""
    detail: str = ""

    def line(self) -> str:
        band = (f"[{self.band[0]:g}-{self.band[1]:g}]"
                if self.band else "[-]")
        sim = f"{self.sim_wr:5.1f}%" if self.sim_wr is not None else "   —  "
        head = (f"{self.status:4} {self.kind:7} {self.anchor}: "
                f"sim {sim} vs band {band}")
        if self.direction:
            head += f" ({self.direction} band)"
        if self.detail:
            head += f" — {self.detail}"
        if self.provenance:
            head += f"\n       prior: {self.provenance}"
        return head


def load_bands(path: str = DEFAULT_BANDS_PATH) -> dict:
    with open(path) as f:
        bands = json.load(f)
    major = str(bands.get("schema", "")).split(".")[0]
    if major != SUPPORTED_SCHEMA_MAJOR:
        print(f"check_calibration: unknown bands schema "
              f"{bands.get('schema')!r} (expected major "
              f"{SUPPORTED_SCHEMA_MAJOR}.x) — proceeding best-effort",
              file=sys.stderr)
    return bands


def _normalize_matrix(matrix: dict) -> Dict[str, float]:
    """Accept both the on-disk shape (``"A|B": pct``) and the in-memory
    shape from run_meta.load_results (``("A", "B"): pct``)."""
    out: Dict[str, float] = {}
    for k, v in (matrix or {}).items():
        key = f"{k[0]}|{k[1]}" if isinstance(k, tuple) else str(k)
        out[key] = float(v)
    return out


def matchup_wr(matrix: dict, deck_a: str, deck_b: str) -> Optional[float]:
    """Percent WR of ``deck_a`` against ``deck_b``, orientation-safe.

    The saved matrix stores the ROW deck's WR: ``"A|B"`` is A's
    percentage.  The reverse cell ``"B|A"`` therefore holds the
    complement.  When both orientations are present they are averaged
    (a symmetry-violating save should not silently prefer one side);
    when only the reverse exists, the complement is returned.
    """
    m = _normalize_matrix(matrix)
    samples = []
    if f"{deck_a}|{deck_b}" in m:
        samples.append(m[f"{deck_a}|{deck_b}"])
    if f"{deck_b}|{deck_a}" in m:
        samples.append(100.0 - m[f"{deck_b}|{deck_a}"])
    if not samples:
        return None
    return sum(samples) / len(samples)


def _decks_in_results(results: dict, matrix: Dict[str, float]) -> List[str]:
    names = results.get("names") or []
    if names:
        return list(names)
    seen: List[str] = []
    for key in matrix:
        for d in key.split("|"):
            if d not in seen:
                seen.append(d)
    return seen


def _band_status(wr: float, lo: float, hi: float) -> Tuple[str, str]:
    if wr < lo:
        return "OUT", "below"
    if wr > hi:
        return "OUT", "above"
    return "IN", ""


def check_results(results: dict, bands: dict) -> List[Finding]:
    """Pure band pass over a parsed results dict (unit-test surface)."""
    matrix = _normalize_matrix(results.get("matrix") or {})
    decks = _decks_in_results(results, matrix)
    findings: List[Finding] = []

    for b in bands.get("matchup_bands", []):
        a, o = b["deck_a"], b["deck_b"]
        lo, hi = b["expected_wr_a"]
        prov = b.get("provenance", "")
        anchor = f"{a} vs {o}"
        missing = [d for d in (a, o) if d not in decks]
        if missing:
            findings.append(Finding(
                "MATCHUP", "SKIP", anchor, None, (lo, hi),
                provenance="",
                detail=f"deck(s) not in results: {', '.join(missing)}"))
            continue
        wr = matchup_wr(matrix, a, o)
        if wr is None:
            findings.append(Finding(
                "MATCHUP", "SKIP", anchor, None, (lo, hi),
                detail="pair not present in matrix"))
            continue
        status, direction = _band_status(wr, lo, hi)
        findings.append(Finding("MATCHUP", status, anchor, wr, (lo, hi),
                                direction=direction, provenance=prov))

    explicit = {b["deck"]: b for b in bands.get("field_bands", [])}
    default = bands.get("default_field_band")
    for d in decks:
        b = explicit.get(d)
        if b is None:
            if default is None:
                continue
            b = {"expected_wr": default["expected_wr"],
                 "provenance": default.get("provenance", "")}
        lo, hi = b["expected_wr"]
        anchor = f"{d} (field)"
        rates = [matchup_wr(matrix, d, opp)
                 for opp in decks if opp != d]
        rates = [r for r in rates if r is not None]
        if not rates:
            findings.append(Finding(
                "FIELD", "SKIP", anchor, None, (lo, hi),
                detail="no matchup cells for this deck"))
            continue
        wr = sum(rates) / len(rates)
        status, direction = _band_status(wr, lo, hi)
        findings.append(Finding("FIELD", status, anchor, wr, (lo, hi),
                                direction=direction,
                                provenance=b.get("provenance", "")))
    # Explicit field bands whose deck never appears in the results at
    # all still deserve a skip notice (a silently absent anchor deck
    # is itself a finding).
    for d, b in explicit.items():
        if d not in decks:
            findings.append(Finding(
                "FIELD", "SKIP", f"{d} (field)", None,
                tuple(b["expected_wr"]),
                detail=f"deck not in results: {d}"))
    return findings


def print_report(findings: List[Finding], results_path: str,
                 bands_path: str) -> Tuple[int, int, int]:
    n_in = sum(1 for f in findings if f.status == "IN")
    n_out = sum(1 for f in findings if f.status == "OUT")
    n_skip = sum(1 for f in findings if f.status == "SKIP")
    print(f"== check_calibration {results_path} "
          f"vs {bands_path} ==")
    for f in findings:
        print("  " + f.line())
    print(f"-- calibration summary: {n_in} in band / {n_out} out of band"
          f" / {n_skip} skipped --")
    if n_out:
        print("   out-of-band pairs are ground-truth divergence probes: "
              "each points at a generic subsystem bug, not deck tuning "
              "(see module docstring).")
    return n_in, n_out, n_skip


def run_check(results_path: str = DEFAULT_RESULTS_PATH,
              bands_path: str = DEFAULT_BANDS_PATH,
              strict: bool = False) -> int:
    """Load, check, print.  Returns 0 (non-fatal) or 1 (strict + OUT)."""
    with open(results_path) as f:
        results = json.load(f)
    bands = load_bands(bands_path)
    findings = check_results(results, bands)
    _, n_out, _ = print_report(findings, results_path, bands_path)
    return 1 if (strict and n_out) else 0


def main(argv: List[str]) -> int:
    strict = "--strict" in argv
    argv = [a for a in argv if a != "--strict"]
    bands_path = DEFAULT_BANDS_PATH
    if "--bands" in argv:
        i = argv.index("--bands")
        try:
            bands_path = argv[i + 1]
        except IndexError:
            print("check_calibration: --bands requires a path",
                  file=sys.stderr)
            return 2
        del argv[i:i + 2]
    positional = [a for a in argv if not a.startswith("--")]
    results_path = positional[0] if positional else DEFAULT_RESULTS_PATH

    for path, label in ((results_path, "results"), (bands_path, "bands")):
        if not Path(path).exists():
            print(f"check_calibration: {label} file not found: {path}",
                  file=sys.stderr)
            return 2
    return run_check(results_path, bands_path, strict=strict)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
