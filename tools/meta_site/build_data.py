"""Build data/meta_site.json, the one data file both site pages render.

Inputs (all read, none hand-copied):
  * metagame_results.json         matrix cells, draws, aborts, provenance
  * card_data.json                card-level detail (extract_card_data.py)
  * decks/gameplans/*.json        each deck's archetype
  * decks.modern_meta             meta shares (passed in for tests)
  * tools/calibration_bands.json  field and matchup bands
  * replays/*.html                replay gallery
  * tools/meta_site/content/      hand-curated narrative, kept as data

Every derived number is computed here, once:
  * flat_wr      = mean of the deck's cell win rates;
  * weighted_wr  = the deck's cell win rates weighted by each opponent's
                   meta share (opponents with no share excluded; flat_wr
                   when no opponent has a share);
  * tier         = T1..T4 on weighted_wr (TIER_FLOORS);
  * band_verdict = flat_wr against the deck's field band.

Usage:
    python -m tools.meta_site.build_data            # writes data/meta_site.json
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.meta_site.schema import (Calibration, CardCount, Cell, ContentFile,  # noqa: E402
                                    Deck, DeckDetail, MatchupDetail,
                                    Provenance, Replay, SiteData)

# Weighted-WR floors for the display tiers (T1 >= 60, T2 >= 50, T3 >= 40,
# else T4): a deck winning 60% of the weighted field is a top-tier pick, 50%
# is break-even. Display grouping only; calibration verdicts use the bands.
TIER_FLOORS = (("T1", 60.0), ("T2", 50.0), ("T3", 40.0))


def _slug_title(stem: str) -> str:
    return stem.replace("_", " ").replace("  ", " ").strip()


def load_archetypes(root: Path) -> Dict[str, str]:
    """deck name -> archetype, from each gameplan file's own fields."""
    out: Dict[str, str] = {}
    for f in sorted((root / "decks" / "gameplans").glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            d = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(d, dict) and d.get("deck_name"):
            out[d["deck_name"]] = str(d.get("archetype") or "unknown")
    return out


def load_bands(root: Path):
    path = root / "tools" / "calibration_bands.json"
    if not path.exists():
        return {}, ((30.0, 70.0), ""), {}
    b = json.loads(path.read_text())
    field = {e["deck"]: (tuple(float(x) for x in e["expected_wr"]),
                         e.get("provenance", "")) for e in b.get("field_bands", [])}
    dflt = b.get("default_field_band", {"expected_wr": [30, 70]})
    default = (tuple(float(x) for x in dflt["expected_wr"]), dflt.get("provenance", ""))
    matchup = {}
    for e in b.get("matchup_bands", []):
        lo, hi = (float(x) for x in e["expected_wr_a"])
        matchup[(e["deck_a"], e["deck_b"])] = (lo, hi)
        matchup[(e["deck_b"], e["deck_a"])] = (100.0 - hi, 100.0 - lo)
    return field, default, matchup


def _verdict(value: float, band: Tuple[float, float]) -> str:
    lo, hi = band
    return "below" if value < lo else "above" if value > hi else "in"


def _counts(rows) -> List[CardCount]:
    return [CardCount(card=r["card"], count=int(r["count"]), desc=r.get("desc", ""))
            for r in rows or [] if isinstance(r, dict) and "card" in r]


def load_card_detail(root: Path):
    """(deck detail by name, matchup detail by (deck, opp)) from
    card_data.json; both empty when the file is absent."""
    path = root / "card_data.json"
    if not path.exists():
        return {}, {}
    raw = json.loads(path.read_text())
    decks = {}
    for d in raw.get("deck_cards", []):
        if d.get("deck"):
            decks[d["deck"]] = DeckDetail(
                mvp_casts=_counts(d.get("mvp_casts")),
                mvp_damage=_counts(d.get("mvp_damage")),
                finishers=_counts(d.get("finishers")),
                summary=d.get("summary", ""))
    matchups = {}
    for mc in (raw.get("matchup_cards") or {}).values():
        a, b = mc.get("d1"), mc.get("d2")
        if not a or not b:
            continue
        g1 = mc.get("g1_wins") or [None, None]
        cb = tuple(mc.get("comebacks") or (0, 0))
        sw = tuple(mc.get("sweeps") or (0, 0))
        for deck, opp, me, them, flip in ((a, b, "d1", "d2", False),
                                          (b, a, "d2", "d1", True)):
            matchups[(deck, opp)] = MatchupDetail(
                insight=mc.get("insight", "") if not flip else "",
                avg_turns=mc.get("avg_turns"),
                went_to_3=mc.get("went_to_3"),
                g1_wr=(g1[1] if flip else g1[0]),
                comebacks=(cb[1], cb[0]) if flip else (cb[0], cb[1]),
                sweeps=(sw[1], sw[0]) if flip else (sw[0], sw[1]),
                win_conditions=dict(mc.get("win_conditions") or {}),
                top_casts=_counts(mc.get(f"{me}_top_casts")),
                opp_top_casts=_counts(mc.get(f"{them}_top_casts")),
                top_damage=_counts(mc.get(f"{me}_top_damage")),
                opp_top_damage=_counts(mc.get(f"{them}_top_damage")),
                finishers=_counts(mc.get(f"{me}_finishers")),
                opp_finishers=_counts(mc.get(f"{them}_finishers")),
                sideboard=list(mc.get(f"{me}_sb") or []),
                opp_sideboard=list(mc.get(f"{them}_sb") or []))
    return decks, matchups


def load_replays(root: Path) -> List[Replay]:
    out = []
    for f in sorted((root / "replays").glob("*.html")) if (root / "replays").exists() else []:
        m = re.match(r"(?:replay_)?(.+?)_s(\d+)$", f.stem)
        if not m:
            continue
        a, _, b = m.group(1).partition("_vs_")
        title = f"{_slug_title(a).title()} vs {_slug_title(b).title()}" if b else _slug_title(a)
        out.append(Replay(title=title, path=f"replays/{f.name}", seed=int(m.group(2))))
    return out


def codebase_counts(root: Path) -> Dict[str, int]:
    """Module and line counts read from the tree at build time."""
    out: Dict[str, int] = {}
    for pkg in ("engine", "ai"):
        files = [f for f in (root / pkg).rglob("*.py")] if (root / pkg).exists() else []
        out[f"{pkg}_modules"] = len(files)
        out[f"{pkg}_lines"] = sum(len(f.read_text(errors="ignore").splitlines()) for f in files)
    tests = (root / "tests")
    out["test_files"] = len(list(tests.glob("test_*.py"))) if tests.exists() else 0
    return out


def load_content(root: Path) -> ContentFile:
    base = root / "tools" / "meta_site" / "content"
    parts = {}
    for key in ("architecture", "timeline", "roadmap", "project"):
        f = base / f"{key}.json"
        parts[key] = json.loads(f.read_text()) if f.exists() else {}
    parts["project"] = dict(parts["project"], codebase=codebase_counts(root))
    return ContentFile(**parts)


def _commit(root: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _audit_violations(results: dict) -> Optional[int]:
    """Rule violations the results file records for its own run (None when
    the run was not audited). An audit JSONL on disk may belong to another
    run, so it is never consulted."""
    audit = results.get("rules_audit")
    return int(audit["violations"]) if audit else None


def _repo_relative_command(command: str) -> str:
    """The run command with any absolute path to a repository script cut to
    the path from the repository root (a CI runner records its own dirs)."""
    return re.sub(r"\S*/((?:tools|engine|ai)/\S+|run_meta\.py)", r"\1", command)


def build_site_data(root: Path, *, shares: Optional[Dict[str, float]] = None,
                    results_path: Optional[Path] = None) -> SiteData:
    root = Path(root)
    results = json.loads((results_path or root / "metagame_results.json").read_text())
    if results.get("type") != "matrix":
        raise ValueError("site data needs a matrix results file")
    if shares is None:
        from decks.modern_meta import METAGAME_SHARES as shares  # noqa: N811
    names: List[str] = list(results["names"])
    n = int(results["n_games"])
    m = results["matrix"]
    cdraws = results.get("cell_draws") or {}
    caborted = results.get("cell_aborted") or {}
    archetypes = load_archetypes(root)
    field_bands, default_band, matchup_bands = load_bands(root)
    deck_detail, matchup_detail = load_card_detail(root)

    cells: List[Cell] = []
    by_deck: Dict[str, Dict[str, float]] = {d: {} for d in names}
    for d in names:
        for o in names:
            if d == o:
                continue
            key = f"{d}|{o}"
            pct = float(m.get(key, 0.0))
            wins = round(pct / 100.0 * n)
            band = matchup_bands.get((d, o))
            cells.append(Cell(deck=d, opp=o, n=n, wins=wins,
                              draws=int(cdraws.get(key, 0)),
                              aborted=int(caborted.get(key, 0)),
                              wr=round(wins / n * 100.0, 1) if n else 0.0,
                              band=band,
                              band_verdict=_verdict(wins / n * 100.0, band) if band and n else "",
                              detail=matchup_detail.get((d, o))))
            by_deck[d][o] = wins / n * 100.0 if n else 0.0

    decks: List[Deck] = []
    in_band = 0
    for d in names:
        row = by_deck[d]
        flat = sum(row.values()) / len(row) if row else 0.0
        weights = {o: float(shares.get(o, 0.0)) for o in row}
        wsum = sum(w for w in weights.values() if w > 0)
        weighted = (sum(row[o] * w for o, w in weights.items() if w > 0) / wsum) if wsum else flat
        tier = next((t for t, floor in TIER_FLOORS if weighted >= floor), "T4")
        band, prov = field_bands.get(d, default_band)
        verdict = _verdict(flat, band)
        in_band += verdict == "in"
        best = max(row, key=row.get) if row else ""
        worst = min(row, key=row.get) if row else ""
        decks.append(Deck(name=d, archetype=archetypes.get(d, "unknown"),
                          meta_share=float(shares.get(d, 0.0)),
                          flat_wr=round(flat, 1), weighted_wr=round(weighted, 1),
                          tier=tier, band=band, band_verdict=verdict,
                          band_provenance=prov, best=best, worst=worst,
                          detail=deck_detail.get(d)))

    banded = [c for c in cells if c.band is not None]
    geom = results.get("seed_geometry") or {}
    prov = Provenance(
        generated=str(results.get("timestamp", "")), commit=_commit(root),
        format=str(results.get("format", "bo3")), n_per_pair=n,
        total_matches=n * len(names) * (len(names) - 1) // 2,
        seed_start=int(geom.get("seed_start", 0)), seed_step=int(geom.get("step", 0)),
        draws=int(results.get("draws", 0)), aborted=int(results.get("aborted", 0)),
        rules_audit_violations=_audit_violations(results),
        command=_repo_relative_command(str((results.get("generated_by") or {}).get("command", ""))))
    cal = Calibration(decks_in_band=in_band, decks_total=len(decks),
                      matchups_in_band=sum(c.band_verdict == "in" for c in banded),
                      matchups_total=len(banded))
    return SiteData(decks=decks, cells=cells, provenance=prov, calibration=cal,
                    replays=load_replays(root), content=load_content(root))


def write_site_data(data: SiteData, root: Path) -> Path:
    out = Path(root) / "data" / "meta_site.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(data.model_dump_json(indent=1))
    return out


def main(argv=None) -> int:
    data = build_site_data(ROOT)
    path = write_site_data(data, ROOT)
    print(f"wrote {path.relative_to(ROOT)}: {len(data.decks)} decks, "
          f"{len(data.cells)} cells, n={data.provenance.n_per_pair}, "
          f"aborted={data.provenance.aborted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
