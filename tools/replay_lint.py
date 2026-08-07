#!/usr/bin/env python3
"""Replay linter — rules-legality audit over ``--dump-replay`` NDJSON.

Every replay dump becomes an automatic audit: derivable CR violations
and decision-tier red flags are reported with (game, turn, actor,
seq) anchors, so a single ``--bo3 --dump-replay`` run self-checks.

Motivation (docs/diagnostics/2026-07-05_calibration_probe_findings.md):
the E4 token-cast and E2 upkeep-cast violations sat in plain sight in
replay logs for weeks.  This tool makes that class of finding
mechanical.  R1 flags the historical E4 event in the committed
``replays/az_aff_60104.ndjson`` — kept as a permanent negative anchor
in tests.

Rules (schema 1.0 — only what the event stream can support):

  R1 TOKEN_CAST            ERROR  CR 111.2   token cast as a spell
  R2 UNKNOWN_CARD_PLAYED   ERROR  —          played card not in DB
  R3 NON_MAIN_SORCERY_CAST ERROR  CR 307.1   sorcery-speed cast
                                             outside a main phase /
                                             off-turn
  R4 MULTIPLE_LAND_DROPS   WARN   CR 305.2   >1 land drop per turn
                                             (extra-drop effects
                                             exist → warn tier)
  R5 NO_ATTACK_WITH_LETHAL WARN   decision   attackable lethal (not
                                             tapped, not summoning
                                             sick, opponent has zero
                                             untapped blockers) and
                                             the turn ends with
                                             sub=no_attack

Schema gaps (checks BLOCKED by missing events — schema 1.1 wishlist):
  - PLAY.targets is never populated → CR 601.2c (cast without legal
    target) cannot be linted.
  - No MANA events → mana-produced-vs-oracle-units cannot be linted.
  - No ACTIVATE events → loyalty deltas / once-per-turn cannot be
    linted.
  - Engine-initiated casts (upkeep/free casts) are not emitted →
    the E2 class is invisible to the dump; emit from
    CastManager.cast_spell to close this.

Usage:
    python tools/replay_lint.py replays/foo.ndjson [more.ndjson ...]
    python tools/replay_lint.py --warn-as-error replays/foo.ndjson

Exit code 1 iff any ERROR finding (or WARN with --warn-as-error).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MAIN_PHASES = ("Main1", "Main2")

SCHEMA_GAPS = [
    "PLAY.targets never populated → CR 601.2c target-legality unlintable",
    "no MANA events → mana-produced vs oracle units unlintable",
    "no ACTIVATE events → loyalty deltas / once-per-turn unlintable",
    "engine-initiated (free/upkeep) casts not emitted → E2 class "
    "invisible; emit from CastManager.cast_spell",
]


@dataclass
class Finding:
    rule: str
    level: str          # "ERROR" | "WARN"
    cr: str             # CR citation or "decision"
    game: int
    turn: int
    actor: str
    seq: int
    detail: str

    def line(self) -> str:
        return (f"{self.level:5} {self.rule:24} g{self.game} t{self.turn} "
                f"seq{self.seq:<4} {self.actor}: {self.detail} [{self.cr}]")


@dataclass
class TurnCtx:
    """Per-(game, turn) context assembled from TURN_START."""
    boards: List[dict] = field(default_factory=list)
    active_pidx: Optional[int] = None


def _load_events(path: str) -> List[dict]:
    events = []
    with open(path) as f:
        for raw in f:
            raw = raw.strip()
            if raw:
                events.append(json.loads(raw))
    return events


def _template_lookup(db):
    def look(name: str):
        if db is None or not name:
            return None
        return db.get_card(name)
    return look


def _is_sorcery_speed(tmpl) -> bool:
    """True when the card can only be cast at sorcery speed
    (CR 307.1 family: sorceries, and any non-instant without flash)."""
    if tmpl is None:
        return False
    if getattr(tmpl, "is_instant", False):
        return False
    kws = {getattr(k, "value", str(k)).lower()
           for k in getattr(tmpl, "keywords", ())}
    return "flash" not in kws


def lint_events(events: List[dict], db=None) -> List[Finding]:
    """Pure rule pass over a parsed event list (unit-test surface)."""
    look = _template_lookup(db)
    findings: List[Finding] = []
    turn_ctx: Dict[tuple, TurnCtx] = {}
    land_drops: Dict[tuple, int] = {}

    for e in events:
        kind = e.get("kind")
        key = (e.get("game"), e.get("turn"))

        if kind == "TURN_START":
            turn_ctx[key] = TurnCtx(boards=e.get("board", []),
                                    active_pidx=e.get("pidx"))

        elif kind == "PLAY":
            actor = e.get("actor", "?")
            card = e.get("card") or ""
            g, t, seq = e.get("game", 0), e.get("turn", 0), e.get("seq", -1)

            if e.get("action") == "play_land":
                k2 = (g, t, e.get("pidx"))
                land_drops[k2] = land_drops.get(k2, 0) + 1
                if land_drops[k2] == 2:
                    findings.append(Finding(
                        "MULTIPLE_LAND_DROPS", "WARN", "CR 305.2",
                        g, t, actor, seq,
                        f"second land drop this turn ({card})"))

            elif e.get("action") == "cast_spell":
                tmpl = look(card)
                if tmpl is None:
                    if card.endswith(" Token"):
                        findings.append(Finding(
                            "TOKEN_CAST", "ERROR", "CR 111.2",
                            g, t, actor, seq,
                            f"token cast as a spell: {card}"))
                    else:
                        findings.append(Finding(
                            "UNKNOWN_CARD_PLAYED", "ERROR", "-",
                            g, t, actor, seq,
                            f"cast of card absent from DB: {card}"))
                else:
                    ctx = turn_ctx.get(key)
                    off_phase = e.get("phase") not in MAIN_PHASES
                    off_turn = (ctx is not None
                                and ctx.active_pidx is not None
                                and e.get("pidx") != ctx.active_pidx)
                    if _is_sorcery_speed(tmpl) and (off_phase or off_turn):
                        where = e.get("phase") if off_phase else "off-turn"
                        findings.append(Finding(
                            "NON_MAIN_SORCERY_CAST", "ERROR", "CR 307.1",
                            g, t, actor, seq,
                            f"sorcery-speed cast of {card} at {where}"))

        elif kind == "COMBAT" and e.get("sub") == "no_attack":
            ctx = turn_ctx.get(key)
            pidx = e.get("pidx")
            if not ctx or pidx is None or len(ctx.boards) < 2:
                continue
            mine = ctx.boards[pidx]
            opp = ctx.boards[1 - pidx]
            # Structured snapshots only (schema with dict creatures);
            # skip silently on older string-shaped boards.
            creatures = mine.get("creatures") or []
            if not creatures or not isinstance(creatures[0], dict):
                continue
            attackable = sum(
                c.get("p", 0) for c in creatures
                if not c.get("tapped") and not c.get("summoning_sick"))
            opp_creatures = opp.get("creatures") or []
            opp_untapped = [c for c in opp_creatures
                            if isinstance(c, dict) and not c.get("tapped")]
            opp_life = opp.get("life", 99)
            if attackable >= opp_life > 0 and not opp_untapped:
                findings.append(Finding(
                    "NO_ATTACK_WITH_LETHAL", "WARN", "decision",
                    e.get("game", 0), e.get("turn", 0),
                    e.get("actor", "?"), e.get("seq", -1),
                    f"{attackable} attackable power vs {opp_life} life, "
                    f"zero untapped blockers — no attack declared"))

    return findings


def lint_file(path: str, db=None) -> List[Finding]:
    return lint_events(_load_events(path), db=db)


def _load_db():
    try:
        from engine.card_database import CardDatabase
        return CardDatabase("ModernAtomic.json")
    except Exception as exc:  # tooling context without DB
        print(f"replay_lint: no card DB ({exc}) — DB-dependent rules "
              f"degraded", file=sys.stderr)
        return None


def main(argv: List[str]) -> int:
    warn_as_error = "--warn-as-error" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    db = _load_db()
    exit_code = 0
    for path in paths:
        findings = lint_file(path, db=db)
        errors = [f for f in findings if f.level == "ERROR"]
        warns = [f for f in findings if f.level == "WARN"]
        print(f"== replay_lint {path}: {len(errors)} error(s), "
              f"{len(warns)} warn(s) ==")
        for f in findings:
            print("  " + f.line())
        if errors or (warn_as_error and warns):
            exit_code = 1
    print("-- schema gaps (unlintable until schema 1.1) --")
    for g in SCHEMA_GAPS:
        print("  * " + g)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
