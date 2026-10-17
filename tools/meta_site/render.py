"""Render the matrix dashboard and the showcase from the site data.

Both pages are built from tools/meta_site/templates/ with the shared
design tokens (_tokens.css) and the data embedded as ONE
`<script type="application/json" id="site-data">` block that the page
parses. Nothing is interpolated into script source, so no deck or card
name can break a page.

Output paths are resolved against the repository root passed in (the
one the tool runs from), never an absolute path: a run from a worktree
writes into that worktree.

Also emits metagame_data.jsx (the D object) for the remaining readers
(build_guide.py, meta_audit.py, merge_matrix_results.py).

Usage:
    python -m tools.meta_site.render            # data file -> pages
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.meta_site.schema import SiteData  # noqa: E402

TEMPLATES = Path(__file__).resolve().parent / "templates"

PAGES = {
    "matrix": ("matrix.html", ("modern_meta_matrix_full.html",)),
    "showcase": ("showcase.html", ("templates/reference_showcase.html",
                                   "mtgsimmanu_showcase.html")),
}


def _grids(data: SiteData):
    idx = data.deck_index()
    n = len(data.decks)
    m = [[None] * n for _ in range(n)]
    dr = [[0] * n for _ in range(n)]
    ab = [[0] * n for _ in range(n)]
    bv = [[""] * n for _ in range(n)]
    bands = [[None] * n for _ in range(n)]
    mu: Dict[str, dict] = {}
    for c in data.cells:
        i, j = idx[c.deck], idx[c.opp]
        m[i][j], dr[i][j], ab[i][j], bv[i][j] = c.wr, c.draws, c.aborted, c.band_verdict
        bands[i][j] = list(c.band) if c.band else None
        if c.detail is not None:
            mu[f"{i},{j}"] = c.detail.model_dump()
    return m, dr, ab, bv, bands, mu


def page_payload(kind: str, data: SiteData) -> dict:
    m, dr, ab, bv, bands, mu = _grids(data)
    decks = [d.model_dump(exclude={"detail"} if kind == "showcase" else set())
             for d in data.decks]
    payload = {"decks": decks, "m": m, "prov": data.provenance.model_dump(),
               "cal": data.calibration.model_dump()}
    if kind == "matrix":
        payload.update(dr=dr, ab=ab, bv=bv, bands=bands, mu=mu)
    else:
        payload.update(replays=[r.model_dump() for r in data.replays],
                       content=data.content.model_dump())
    return payload


def _json_block(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # "</" can never close the block early; JSON.parse reads "<\/" as "</".
    body = body.replace("</", "<\\/")
    return f'<script type="application/json" id="site-data">{body}</script>'


def render_page(kind: str, data: SiteData) -> str:
    template, _ = PAGES[kind]
    html = (TEMPLATES / template).read_text()
    tokens = (TEMPLATES / "_tokens.css").read_text()
    for marker in ("/*@TOKENS*/", "<!--@DATA-->"):
        if html.count(marker) != 1:
            raise ValueError(f"{template}: expected one {marker}")
    html = html.replace("/*@TOKENS*/", tokens)
    return html.replace("<!--@DATA-->", _json_block(page_payload(kind, data)))


def legacy_D(data: SiteData) -> dict:
    """The D object metagame_data.jsx carried, rebuilt from the site data."""
    idx = data.deck_index()
    n = data.provenance.n_per_pair
    names = [d.name for d in data.decks]
    wins = [[0] * len(names) for _ in names]
    mc: Dict[str, dict] = {}
    for c in data.cells:
        i, j = idx[c.deck], idx[c.opp]
        wins[i][j] = c.wins
    for c in data.cells:
        i, j = idx[c.deck], idx[c.opp]
        if i < j and c.detail is not None:
            back = data.cell(c.opp, c.deck).detail
            p = c.detail
            mc[f"{i},{j}"] = {
                "d1": c.deck, "d2": c.opp, "d1_wins": c.wins, "d2_wins": wins[j][i],
                "avg_turns": p.avg_turns, "went_to_3": p.went_to_3,
                "g1_wins": [p.g1_wr, back.g1_wr if back else None],
                "comebacks": list(p.comebacks), "sweeps": list(p.sweeps),
                "insight": p.insight, "win_conditions": dict(p.win_conditions),
                "d1_top_casts": [x.model_dump() for x in p.top_casts],
                "d2_top_casts": [x.model_dump() for x in p.opp_top_casts],
                "d1_top_damage": [x.model_dump() for x in p.top_damage],
                "d2_top_damage": [x.model_dump() for x in p.opp_top_damage],
                "d1_finishers": [x.model_dump() for x in p.finishers],
                "d2_finishers": [x.model_dump() for x in p.opp_finishers],
                "d1_sb": list(p.sideboard), "d2_sb": list(p.opp_sideboard),
            }
    overall = []
    deck_cards: List[dict] = []
    for i, d in enumerate(data.decks):
        total = sum(wins[i])
        overall.append({"deck": d.name, "idx": i, "win_rate": d.flat_wr,
                        "weighted_wr": d.weighted_wr, "total_wins": total,
                        "total_matches": n * (len(names) - 1)})
        det = d.detail
        deck_cards.append({"deck": d.name, "idx": i, **({
            "mvp_casts": [x.model_dump() for x in det.mvp_casts],
            "mvp_damage": [x.model_dump() for x in det.mvp_damage],
            "finishers": [x.model_dump() for x in det.finishers],
            "summary": det.summary} if det else {})})
    return {"decks": names, "wins": wins, "matches_per_pair": n,
            "overall": overall, "matchup_cards": mc, "deck_cards": deck_cards,
            "meta_shares": {d.name: d.meta_share for d in data.decks}}


def write_legacy_jsx(data: SiteData, root: Path) -> Path:
    out = Path(root) / "metagame_data.jsx"
    body = json.dumps(legacy_D(data), separators=(",", ":"))
    out.write_text(f"const D = {body};\nconst N = D.decks.length;\n")
    return out


def render_all(data: SiteData, root: Path) -> List[Path]:
    root = Path(root)
    written: List[Path] = []
    for kind, (_, outs) in PAGES.items():
        html = render_page(kind, data)
        for rel in outs:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(html)
            written.append(p)
    written.append(write_legacy_jsx(data, root))
    return written


def main(argv=None) -> int:
    path = ROOT / "data" / "meta_site.json"
    data = SiteData.model_validate_json(path.read_text())
    for p in render_all(data, ROOT):
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
