"""Typed schema of the site data file (data/meta_site.json).

One file feeds both the matrix dashboard and the showcase. Every derived
figure (flat and weighted win rates, tiers, band verdicts, symmetry) is
computed once, by tools/meta_site/build_data.py, and stored here; the
pages only render it.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CardCount(_Model):
    card: str
    count: int
    desc: str = ""


class DeckDetail(_Model):
    """Card-level detail for one deck, from verbose Bo3 logs."""
    mvp_casts: List[CardCount] = Field(default_factory=list)
    mvp_damage: List[CardCount] = Field(default_factory=list)
    finishers: List[CardCount] = Field(default_factory=list)
    summary: str = ""


class Deck(_Model):
    name: str
    archetype: str                       # from decks/gameplans/*.json
    meta_share: float                    # % of the field (decks.modern_meta)
    flat_wr: float                       # mean of the deck's cells
    weighted_wr: float                   # cells weighted by opponent share
    tier: str                            # T1..T4 on weighted_wr
    band: Tuple[float, float]            # tools/calibration_bands.json
    band_verdict: str                    # "below" | "in" | "above"
    band_provenance: str = ""
    best: str = ""                       # strongest matchup (opponent)
    worst: str = ""                      # weakest matchup (opponent)
    detail: Optional[DeckDetail] = None


class MatchupDetail(_Model):
    """Card-level detail for one pairing, from verbose Bo3 logs, in the
    perspective of the row deck."""
    insight: str = ""
    avg_turns: Optional[float] = None
    went_to_3: Optional[int] = None      # % of matches that reached game 3
    g1_wr: Optional[int] = None          # game-1 win rate, %
    comebacks: Tuple[int, int] = (0, 0)
    sweeps: Tuple[int, int] = (0, 0)
    win_conditions: Dict[str, int] = Field(default_factory=dict)
    top_casts: List[CardCount] = Field(default_factory=list)
    opp_top_casts: List[CardCount] = Field(default_factory=list)
    top_damage: List[CardCount] = Field(default_factory=list)
    opp_top_damage: List[CardCount] = Field(default_factory=list)
    finishers: List[CardCount] = Field(default_factory=list)
    opp_finishers: List[CardCount] = Field(default_factory=list)
    sideboard: List[str] = Field(default_factory=list)
    opp_sideboard: List[str] = Field(default_factory=list)


class Cell(_Model):
    """deck vs opp over n matches: wins by `deck`, draws and aborts (never
    credited to either side)."""
    deck: str
    opp: str
    n: int
    wins: int
    draws: int = 0
    aborted: int = 0
    wr: float                            # wins / n * 100
    band: Optional[Tuple[float, float]] = None   # matchup calibration band
    band_verdict: str = ""               # "" when no matchup band exists
    detail: Optional[MatchupDetail] = None


class Provenance(_Model):
    generated: str                       # results timestamp
    commit: str = ""
    format: str = "bo3"
    n_per_pair: int
    total_matches: int
    seed_start: int = 0
    seed_step: int = 0
    draws: int = 0
    aborted: int = 0
    rules_audit_violations: Optional[int] = None
    command: str = ""


class Calibration(_Model):
    decks_in_band: int
    decks_total: int
    matchups_in_band: int
    matchups_total: int


class ContentFile(_Model):
    """Hand-curated narrative, kept as data under tools/meta_site/content/."""
    architecture: dict = Field(default_factory=dict)
    timeline: dict = Field(default_factory=dict)
    roadmap: dict = Field(default_factory=dict)
    project: dict = Field(default_factory=dict)


class Replay(_Model):
    title: str
    path: str
    seed: int


class SiteData(_Model):
    schema_version: int = SCHEMA_VERSION
    decks: List[Deck]
    cells: List[Cell]
    provenance: Provenance
    calibration: Calibration
    replays: List[Replay] = Field(default_factory=list)
    content: ContentFile = Field(default_factory=ContentFile)

    def cell(self, deck: str, opp: str) -> Cell:
        for c in self.cells:
            if c.deck == deck and c.opp == opp:
                return c
        raise KeyError((deck, opp))

    def deck_index(self) -> Dict[str, int]:
        return {d.name: i for i, d in enumerate(self.decks)}
