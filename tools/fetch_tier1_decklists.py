#!/usr/bin/env python3
"""Fetch tier-1 Modern decklists from mtgtop8.com and diff against our registered decks.

No card-name-specific logic — pure HTML scraping + generic text parsing.
Network calls are isolated in fetch_* functions so parse_* functions are unit-testable
against saved fixtures with no network access.

Usage:
    python3 tools/fetch_tier1_decklists.py --top 16 --events-per-archetype 2
    python3 tools/fetch_tier1_decklists.py --diff-only   # metagame diff, no decklist download

Output:
    data/tier1_decklists/<YYYY-MM-DD>/<archetype_slug>.txt   (import_deck.py-compatible)
    data/tier1_decklists/<YYYY-MM-DD>/DIFF_REPORT.md

mtgdecks.net returns 403 to scripted requests (verified 2026-08-07) — mtgtop8.com only.
"""
import argparse
import datetime
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

BASE = "https://mtgtop8.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (MTGSimManu calibration tool)"}
TIMEOUT = 20


@dataclass
class Archetype:
    name: str
    pct: float
    archetype_id: Optional[str] = None
    meta_id: Optional[str] = None


@dataclass
class Decklist:
    archetype_name: str
    event_id: str
    deck_id: str
    mainboard: List[Tuple[int, str]] = field(default_factory=list)
    sideboard: List[Tuple[int, str]] = field(default_factory=list)


# ─── Network (thin — one function per HTTP call, no parsing logic here) ───

def fetch_format_page(format_code: str = "MO") -> str:
    r = requests.get(f"{BASE}/format?f={format_code}", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def fetch_archetype_page(archetype_id: str, meta_id: str, format_code: str = "MO") -> str:
    r = requests.get(
        f"{BASE}/archetype?a={archetype_id}&meta={meta_id}&f={format_code}",
        headers=HEADERS, timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.text


def fetch_event_deck_page(event_id: str, deck_id: str, format_code: str = "MO") -> str:
    r = requests.get(
        f"{BASE}/event?e={event_id}&d={deck_id}&f={format_code}",
        headers=HEADERS, timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.text


# ─── Parsing (pure functions — unit tested against saved fixtures) ───

def parse_metagame_breakdown(html: str) -> List[Archetype]:
    """Extract (archetype name, meta %) pairs from a /format page.

    mtgtop8's markup isn't classic table rows for this block, so we work off
    the flattened visible text: category headers glue the number to '%'
    ("AGGRO 34%"), archetype rows have a space before it ("Boros Aggro 8 %").
    That distinction is what separates category totals from deck rows.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    start = text.find("decks ")
    end = text.find("LAST 20 EVENTS")
    if start == -1 or end == -1 or end <= start:
        return []
    window = text[start:end]

    archetypes = []
    for m in re.finditer(r"([A-Za-z0-9][A-Za-z0-9/'\-\.& ]*?)\s+(\d+(?:\.\d+)?)\s+%", window):
        name = m.group(1).strip()
        pct = float(m.group(2))
        # Drop category totals mis-captured by a preceding all-caps word run
        # (e.g. "COMBO 41% Broodscale Bloodchief 7 %" — the char class above
        # only matches names ending right before " N %", so this is already
        # excluded; guard anyway against stray "Other - X" bucket rows)
        if name.startswith("Other - "):
            continue
        archetypes.append(Archetype(name=name, pct=pct))
    return archetypes


def parse_archetype_ids(html: str) -> Dict[str, Tuple[str, str]]:
    """Map archetype name -> (archetype_id, meta_id) from any mtgtop8 page
    containing archetype links (format page or archetype page nav)."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.select('a[href*="archetype?a="]'):
        href = a.get("href", "")
        m = re.search(r"a=(\d+)&meta=(\d+)", href)
        if not m:
            continue
        name = a.get_text(strip=True)
        if name and name not in out:
            out[name] = (m.group(1), m.group(2))
    return out


def parse_archetype_events(html: str, limit: int = 3) -> List[Tuple[str, str]]:
    """Extract up to `limit` distinct (event_id, deck_id) pairs from an archetype page,
    most recent first (mtgtop8 lists them in reverse-chronological order)."""
    seen = []
    for m in re.finditer(r"event\?e=(\d+)&d=(\d+)", html):
        pair = (m.group(1), m.group(2))
        if pair not in seen:
            seen.append(pair)
        if len(seen) >= limit:
            break
    return seen


def parse_decklist(html: str, archetype_name: str, event_id: str, deck_id: str) -> Decklist:
    soup = BeautifulSoup(html, "html.parser")
    lines = soup.select(".deck_line")

    # Sideboard boundary: mtgtop8 emits a literal "SIDEBOARD" marker div between the
    # mainboard and sideboard .deck_line elements. Count .deck_line occurrences before
    # that marker in the raw HTML to find the split index (order-preserving, no need
    # to re-locate the marker node in the parsed tree).
    sb_start_idx = None
    if "SIDEBOARD" in html:
        head_html, _, _ = html.partition("SIDEBOARD")
        sb_start_idx = len(BeautifulSoup(head_html, "html.parser").select(".deck_line"))

    deck = Decklist(archetype_name=archetype_name, event_id=event_id, deck_id=deck_id)
    for i, el in enumerate(lines):
        m = re.match(r"^\s*(\d+)\s+(.+?)\s*$", el.get_text())
        if not m:
            continue
        qty, name = int(m.group(1)), m.group(2).strip()
        if sb_start_idx is not None and i >= sb_start_idx:
            deck.sideboard.append((qty, name))
        else:
            deck.mainboard.append((qty, name))
    return deck


# ─── Formatting ───

def format_decklist_text(deck: Decklist) -> str:
    out = [f"// {deck.archetype_name} — mtgtop8 event={deck.event_id} deck={deck.deck_id}"]
    for qty, name in deck.mainboard:
        out.append(f"{qty} {name}")
    if deck.sideboard:
        out.append("")
        out.append("Sideboard")
        for qty, name in deck.sideboard:
            out.append(f"{qty} {name}")
    return "\n".join(out) + "\n"


def slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return s or "unknown"


# ─── Diff against our registered decks ───

def registered_deck_names(repo_root: str) -> List[str]:
    """Deck names registered in decks/modern_meta.py — read from the
    MODERN_DECKS dict's top-level string keys (the source of truth for
    which decks are actually simulated), not METAGAME_SHARES alone."""
    path = os.path.join(repo_root, "decks", "modern_meta.py")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        src = f.read()
    m = re.search(r"MODERN_DECKS\s*:\s*Dict\[.*?\]\s*=\s*\{", src)
    if not m:
        return []
    start = m.end() - 1  # position of the dict's opening '{'

    names = []
    depth = 0
    for line in src[start:].split("\n"):
        if depth == 1:
            km = re.match(r'\s*"([^"]+)"\s*:\s*\{', line)
            if km:
                names.append(km.group(1))
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            break
    return names


def build_diff_report(tier1: List[Archetype], registered: List[str], top_n: int) -> str:
    reg_lower = {n.lower() for n in registered}
    lines = [
        f"# Tier-1 Decklist Diff Report — {datetime.date.today().isoformat()}",
        "",
        f"Source: mtgtop8.com Modern metagame breakdown (top {top_n} by meta %).",
        f"Registered decks in decks/modern_meta.py: {len(registered)}",
        "",
        "| Archetype | Meta % | Registered? |",
        "|---|---|---|",
    ]
    for a in tier1[:top_n]:
        hit = a.name.lower() in reg_lower or any(
            a.name.lower() in r or r in a.name.lower() for r in reg_lower
        )
        lines.append(f"| {a.name} | {a.pct:.1f}% | {'yes' if hit else '**NO — gap**'}")
    missing = [a for a in tier1[:top_n]
               if not (a.name.lower() in reg_lower
                       or any(a.name.lower() in r or r in a.name.lower() for r in reg_lower))]
    lines.append("")
    if missing:
        lines.append(f"## Gaps ({len(missing)})")
        for a in missing:
            lines.append(f"- **{a.name}** ({a.pct:.1f}% meta share) not in our 16-deck registry.")
    else:
        lines.append("No gaps — all top archetypes are represented.")
    return "\n".join(lines) + "\n"


# ─── Orchestration ───

def run(top_n: int, events_per_archetype: int, out_dir: str, repo_root: str,
        diff_only: bool, sleep_s: float = 1.0) -> None:
    print(f"Fetching mtgtop8 Modern metagame breakdown...")
    format_html = fetch_format_page("MO")
    tier1 = sorted(parse_metagame_breakdown(format_html), key=lambda a: -a.pct)
    ids = parse_archetype_ids(format_html)
    for a in tier1:
        if a.name in ids:
            a.archetype_id, a.meta_id = ids[a.name]

    print(f"Found {len(tier1)} archetypes. Top {top_n}:")
    for a in tier1[:top_n]:
        print(f"  {a.pct:5.1f}%  {a.name}")

    registered = registered_deck_names(repo_root)
    date_dir = os.path.join(out_dir, datetime.date.today().isoformat())
    os.makedirs(date_dir, exist_ok=True)

    report = build_diff_report(tier1, registered, top_n)
    with open(os.path.join(date_dir, "DIFF_REPORT.md"), "w") as f:
        f.write(report)
    print(f"Wrote {date_dir}/DIFF_REPORT.md")

    if diff_only:
        return

    for a in tier1[:top_n]:
        if not a.archetype_id:
            print(f"  skip {a.name}: no archetype id found")
            continue
        time.sleep(sleep_s)
        arch_html = fetch_archetype_page(a.archetype_id, a.meta_id, "MO")
        events = parse_archetype_events(arch_html, limit=events_per_archetype)
        if not events:
            print(f"  skip {a.name}: no recent events found")
            continue
        for event_id, deck_id in events:
            time.sleep(sleep_s)
            deck_html = fetch_event_deck_page(event_id, deck_id, "MO")
            deck = parse_decklist(deck_html, a.name, event_id, deck_id)
            if not deck.mainboard:
                print(f"  skip {a.name} {event_id}/{deck_id}: empty mainboard (parse miss)")
                continue
            fname = f"{slugify(a.name)}__{event_id}_{deck_id}.txt"
            with open(os.path.join(date_dir, fname), "w") as f:
                f.write(format_decklist_text(deck))
            print(f"  wrote {fname} ({len(deck.mainboard)} main / {len(deck.sideboard)} side)")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--top", type=int, default=16, help="tier-1 cutoff by archetype count")
    p.add_argument("--events-per-archetype", type=int, default=2)
    p.add_argument("--out-dir", default="data/tier1_decklists")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--diff-only", action="store_true", help="skip decklist download, report only")
    args = p.parse_args()
    run(args.top, args.events_per_archetype, args.out_dir, args.repo_root, args.diff_only)


if __name__ == "__main__":
    sys.exit(main())
