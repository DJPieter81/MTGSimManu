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

Trend mode (structural finding #6, docs/proposals/
2026-07-09_structural_findings.md): ``--trend PREV.json CURR.json``
compares two saved results snapshots — per-deck field-WR deltas over
the shared deck pool plus band-composition transitions (which anchors
moved IN/OUT, not just the flat count).  Optional
``--exclude-out-of-band-opponents`` recomputes the field averages
without opponents whose own field WR is out of band in either
snapshot, so a deck's number is not propped up by farming known-broken
rows.  Pure reporting: trend mode never writes and always exits 0
(2 on usage errors); band VALUES are read from the bands file, never
altered.

Usage:
    python tools/check_calibration.py [results.json] [--bands path]
    python tools/check_calibration.py --strict          # exit 1 on OUT
    python tools/check_calibration.py --trend PREV.json CURR.json \
        [--bands path] [--exclude-out-of-band-opponents]
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


def _field_wr(matrix: dict, decks: List[str], deck: str,
              exclude: frozenset = frozenset()) -> Optional[float]:
    """Field average for ``deck``: mean matchup WR over every other
    deck in ``decks`` (minus ``exclude``), orientation-safe."""
    rates = [matchup_wr(matrix, deck, opp)
             for opp in decks if opp != deck and opp not in exclude]
    rates = [r for r in rates if r is not None]
    if not rates:
        return None
    return sum(rates) / len(rates)


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
        wr = _field_wr(matrix, decks, d)
        if wr is None:
            findings.append(Finding(
                "FIELD", "SKIP", anchor, None, (lo, hi),
                detail="no matchup cells for this deck"))
            continue
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


# ─── Trend mode (structural finding #6) ─────────────────────────────


def _status_map(results: dict, bands: dict) -> Dict[Tuple[str, str], str]:
    """{(kind, anchor): status} for every band finding on a snapshot."""
    return {(f.kind, f.anchor): f.status
            for f in check_results(results, bands)}


def _out_of_band_field_decks(results: dict, bands: dict) -> set:
    """Decks whose FIELD average is out of its band on this snapshot."""
    suffix = " (field)"
    return {f.anchor[: -len(suffix)]
            for f in check_results(results, bands)
            if f.kind == "FIELD" and f.status == "OUT"}


def compute_trend(prev: dict, curr: dict, bands: dict,
                  exclude_out_of_band_opponents: bool = False) -> dict:
    """Pure trend computation between two parsed results dicts.

    Per-deck field-WR deltas are taken over the SHARED deck pool (and,
    with ``exclude_out_of_band_opponents``, minus the union of decks
    that are out of their field band in either snapshot) so both
    averages use the same opponent set and deltas stay
    apples-to-apples.  Band-composition transitions list every anchor
    whose IN/OUT/SKIP status changed between snapshots.
    """
    pm = _normalize_matrix(prev.get("matrix") or {})
    cm = _normalize_matrix(curr.get("matrix") or {})
    pdecks = _decks_in_results(prev, pm)
    cdecks = _decks_in_results(curr, cm)
    shared = [d for d in pdecks if d in cdecks]

    excluded: set = set()
    if exclude_out_of_band_opponents:
        excluded = (_out_of_band_field_decks(prev, bands)
                    | _out_of_band_field_decks(curr, bands))
    ex = frozenset(excluded)

    deck_deltas = []
    for d in shared:
        pwr = _field_wr(pm, shared, d, exclude=ex)
        cwr = _field_wr(cm, shared, d, exclude=ex)
        delta = None if (pwr is None or cwr is None) else cwr - pwr
        deck_deltas.append({"deck": d, "prev_wr": pwr, "curr_wr": cwr,
                            "delta": delta})
    deck_deltas.sort(key=lambda r: (r["delta"] is None,
                                    -(r["delta"] or 0.0)))

    ps, cs = _status_map(prev, bands), _status_map(curr, bands)
    transitions = [{"kind": kind, "anchor": anchor,
                    "prev": ps.get((kind, anchor)),
                    "curr": cs.get((kind, anchor))}
                   for kind, anchor in sorted(set(ps) | set(cs))
                   if ps.get((kind, anchor)) != cs.get((kind, anchor))]

    def _composition(smap: Dict[Tuple[str, str], str]) -> Dict[str, int]:
        comp = {"IN": 0, "OUT": 0, "SKIP": 0}
        for status in smap.values():
            comp[status] = comp.get(status, 0) + 1
        return comp

    return {
        "deck_deltas": deck_deltas,
        "transitions": transitions,
        "composition": {"prev": _composition(ps), "curr": _composition(cs)},
        "excluded_opponents": sorted(excluded),
        "decks_only_in_prev": sorted(set(pdecks) - set(cdecks)),
        "decks_only_in_curr": sorted(set(cdecks) - set(pdecks)),
    }


def print_trend(trend: dict, prev_path: str, curr_path: str) -> None:
    print(f"== calibration trend {prev_path} -> {curr_path} ==")
    for side in ("prev", "curr"):
        gone = trend[f"decks_only_in_{side}"]
        if gone:
            print(f"  decks only in {side}: {', '.join(gone)} "
                  f"(excluded from deltas)")
    if trend["excluded_opponents"]:
        print("  field averages exclude out-of-band opponents: "
              + ", ".join(trend["excluded_opponents"]))
    print("-- per-deck field WR --")
    for r in trend["deck_deltas"]:
        if r["delta"] is None:
            print(f"  {r['deck']:25s} (no comparable cells)")
        else:
            print(f"  {r['deck']:25s} {r['prev_wr']:5.1f}% -> "
                  f"{r['curr_wr']:5.1f}%  ({r['delta']:+.1f}pp)")
    print("-- band-composition transitions --")
    if not trend["transitions"]:
        print("  (none — every anchor kept its status)")
    for t in trend["transitions"]:
        print(f"  {t['kind']:7} {t['anchor']}: "
              f"{t['prev'] or '—'} -> {t['curr'] or '—'}")
    comp_p, comp_c = trend["composition"]["prev"], trend["composition"]["curr"]
    print(f"-- composition: prev {comp_p['IN']} in / {comp_p['OUT']} out / "
          f"{comp_p['SKIP']} skipped;  curr {comp_c['IN']} in / "
          f"{comp_c['OUT']} out / {comp_c['SKIP']} skipped --")


def run_trend(prev_path: str, curr_path: str,
              bands_path: str = DEFAULT_BANDS_PATH,
              exclude_out_of_band_opponents: bool = False) -> int:
    """Load, compute, print.  Pure reporting — always returns 0."""
    with open(prev_path) as f:
        prev = json.load(f)
    with open(curr_path) as f:
        curr = json.load(f)
    bands = load_bands(bands_path)
    trend = compute_trend(
        prev, curr, bands,
        exclude_out_of_band_opponents=exclude_out_of_band_opponents)
    print_trend(trend, prev_path, curr_path)
    return 0


def run_check(results_path: str = DEFAULT_RESULTS_PATH,
              bands_path: str = DEFAULT_BANDS_PATH,
              strict: bool = False) -> int:
    """Load, check, print.  Returns 0 (non-fatal) or 1 (strict + OUT)."""
    with open(results_path) as f:
        results = json.load(f)
    bands = load_bands(bands_path)
    findings = check_results(results, bands)
    # A game the CPU safety budget cut off (engine.game_budget) is not a
    # game result; run_meta counts such games instead of crediting them,
    # and a results file that contains any is announced before the bands
    # so the verdict below is never read as ground truth by mistake. A
    # verdict line, not a gate: the user decides whether to re-run.
    aborted = int(results.get("aborted") or 0)
    if aborted:
        print(f"!! NOT CALIBRATION-GRADE (aborted={aborted}): {aborted} "
              f"game(s) were cut off by the CPU safety budget — re-run on a "
              f"quiet box before reading the bands below.")
    _, n_out, _ = print_report(findings, results_path, bands_path)
    return 1 if (strict and n_out) else 0


def main(argv: List[str]) -> int:
    strict = "--strict" in argv
    argv = [a for a in argv if a != "--strict"]
    exclude_oob = "--exclude-out-of-band-opponents" in argv
    argv = [a for a in argv if a != "--exclude-out-of-band-opponents"]
    trend_paths: Optional[Tuple[str, str]] = None
    if "--trend" in argv:
        i = argv.index("--trend")
        try:
            trend_paths = (argv[i + 1], argv[i + 2])
        except IndexError:
            print("check_calibration: --trend requires PREV.json CURR.json",
                  file=sys.stderr)
            return 2
        del argv[i:i + 3]
    elif exclude_oob:
        print("check_calibration: --exclude-out-of-band-opponents "
              "requires --trend", file=sys.stderr)
        return 2
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
    if trend_paths is not None:
        prev_path, curr_path = trend_paths
        for path, label in ((prev_path, "prev results"),
                            (curr_path, "curr results"),
                            (bands_path, "bands")):
            if not Path(path).exists():
                print(f"check_calibration: {label} file not found: {path}",
                      file=sys.stderr)
                return 2
        return run_trend(prev_path, curr_path, bands_path,
                         exclude_out_of_band_opponents=exclude_oob)

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
