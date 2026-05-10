"""Structured replay event log — NDJSON source of truth for the replayer.

Why this exists
---------------
The verbose text log (game_runner._vlog) records *what* happened but
not *why* — when a play is bad, you can't see the alternatives the AI
considered, the EV gap, or which subsystem (clock / BHI / combo)
contributed which delta.  build_replay.py is then forced to regex-parse
human-prose lines, so any format change silently breaks the HTML.

This module is the structured channel.  game_runner emits one event per
significant moment to a ReplayLog instance; the text log is preserved
unchanged for backwards compatibility (the LLM-compression pipeline and
existing test fixtures both read it).  The HTML replayer consumes the
NDJSON instead of regex-parsing prose.

Event vocabulary
----------------
GAME_START   game header (decks, on-play, seed, sideboards)
MULLIGAN     keep/mull decision with reason and resulting hand
TURN_START   turn-N header with state snapshot
PHASE        phase boundary (Untap / Upkeep / Main1 / ...)
DRAW         card drawn (for hand-tracking)
DECISION     AI choice with chosen + alternatives + subsystem deltas
PLAY         executed play (cast / land / equip / activate)
TRIGGER      ETB / saga / loot / cycling text
COMBAT       attack declaration, blocks, breakdown, damage, lethal
LIFE         life-total change with cause
GAME_END     winner / turns / win condition / final life

Each event carries:
  seq    monotonic sequence id (stable ordering)
  game   game number within a Bo3 (1, 2, 3)
  turn   current turn number (0 = pre-game)
  phase  phase label or '' if not in a phase
  actor  active player display name or '' for system events
  pidx   active player index 0/1, or -1 for system events
  state  small state snapshot {life:[a,b], hand:[a,b], lands:[a,b]}

The DECISION event additionally carries:
  chosen        {action, card, ev, reason, target, target_reason}
  alternatives  [{action, card, ev, gap, reason, rejected_because}]  # top-N runner-ups
  subsystems    {clock, bhi, combo, ...}  # per-subsystem EV contribution
  candidates_n  total legal plays considered
  decision_id   stable id used as the HTML anchor + feedback key

NDJSON layout: one JSON object per line, no commas, no trailing
newline-in-object — easy to grep ("kind\":\"DECISION\"") and tail.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ─── Schema version ─────────────────────────────────────────────
# Bump on breaking changes to the event shape; build_replay.py checks
# this against its known-supported set and refuses unknown majors.
REPLAY_LOG_SCHEMA = "1.0"


# ─── Event kinds (string enum — no Enum class to keep NDJSON shallow) ───
KIND_GAME_START = "GAME_START"
KIND_MULLIGAN   = "MULLIGAN"
KIND_TURN_START = "TURN_START"
KIND_PHASE      = "PHASE"
KIND_DRAW       = "DRAW"
KIND_DECISION   = "DECISION"
KIND_PLAY       = "PLAY"
KIND_TRIGGER    = "TRIGGER"
KIND_COMBAT     = "COMBAT"
KIND_LIFE       = "LIFE"
KIND_NOTE       = "NOTE"          # free-form annotation (priority pass, target reason)
KIND_GAME_END   = "GAME_END"
KIND_MATCH_END  = "MATCH_END"

VALID_KINDS = {
    KIND_GAME_START, KIND_MULLIGAN, KIND_TURN_START, KIND_PHASE, KIND_DRAW,
    KIND_DECISION, KIND_PLAY, KIND_TRIGGER, KIND_COMBAT, KIND_LIFE, KIND_NOTE,
    KIND_GAME_END, KIND_MATCH_END,
}


@dataclass
class ReplayLog:
    """Structured event collector.

    A single instance covers one Bo3 match (multiple games).  Game
    boundaries are marked by GAME_START/GAME_END events; the seq counter
    is monotonic across games so every event has a globally-unique id.

    The replay log is opt-in: callers who don't enable it (default
    everywhere except the verbose / Bo3 / dump-replay paths) pay only
    the cost of `if log is not None` checks at the call sites.
    """
    schema: str = REPLAY_LOG_SCHEMA
    seed: int = 0
    deck1: str = ""
    deck2: str = ""
    events: List[Dict[str, Any]] = field(default_factory=list)
    _seq: int = 0
    _decision_seq: int = 0   # separate counter for stable decision_ids
    _current_game: int = 0
    _current_turn: int = 0
    _current_phase: str = ""

    # ─── Emit ──────────────────────────────────────────────────

    def emit(self, kind: str, **fields: Any) -> Dict[str, Any]:
        """Append an event with the current game/turn/phase context.

        Caller-supplied fields override the auto-injected context keys
        (turn/phase/game) when an event needs to refer to a different
        moment — e.g. a TURN_START event wants its own turn number, not
        the previous turn's.
        """
        if kind not in VALID_KINDS:
            raise ValueError(f"Unknown replay event kind: {kind!r}")
        evt: Dict[str, Any] = {
            "seq": self._seq,
            "kind": kind,
            "game": self._current_game,
            "turn": self._current_turn,
            "phase": self._current_phase,
        }
        evt.update(fields)
        self.events.append(evt)
        self._seq += 1
        # Update context AFTER appending so the event shows the
        # boundary it created, not the one that followed.
        if kind == KIND_TURN_START:
            self._current_turn = int(fields.get("turn", self._current_turn))
            self._current_phase = ""
        elif kind == KIND_PHASE:
            self._current_phase = str(fields.get("phase", ""))
        elif kind == KIND_GAME_START:
            self._current_game = int(fields.get("game", self._current_game))
            self._current_turn = 0
            self._current_phase = ""
        return evt

    # ─── Decision helper ───────────────────────────────────────

    def emit_decision(
        self,
        actor: str,
        pidx: int,
        chosen: Dict[str, Any],
        alternatives: List[Dict[str, Any]],
        state: Optional[Dict[str, Any]] = None,
        subsystems: Optional[Dict[str, Any]] = None,
        goal: str = "",
        candidates_n: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Emit a DECISION event with a stable decision_id.

        decision_id format: "g{game}t{turn}d{seq}" — game/turn first so
        it sorts naturally and a feedback file can be diffed across
        seeds without alignment churn.

        Alternatives should be the top-N (default 3) runner-ups, sorted
        by descending EV.  Each alt should include `gap` = chosen.ev -
        alt.ev so the renderer can colour the EV gap without
        recomputing.
        """
        decision_id = f"g{self._current_game}t{self._current_turn}d{self._decision_seq}"
        self._decision_seq += 1
        return self.emit(
            KIND_DECISION,
            actor=actor,
            pidx=pidx,
            decision_id=decision_id,
            goal=goal,
            chosen=chosen,
            alternatives=alternatives or [],
            subsystems=subsystems or {},
            candidates_n=candidates_n,
            state=state or {},
        )

    # ─── Serialization ─────────────────────────────────────────

    def to_ndjson(self) -> str:
        """Render the event stream as newline-delimited JSON.

        Header object (one line, kind == 'HEADER') prefixed so consumers
        can validate schema before iterating.  The header is not part of
        the VALID_KINDS set — it's metadata, not an event.
        """
        header = {
            "kind": "HEADER",
            "schema": self.schema,
            "seed": self.seed,
            "deck1": self.deck1,
            "deck2": self.deck2,
            "event_count": len(self.events),
        }
        lines = [json.dumps(header, separators=(",", ":"), ensure_ascii=False)]
        for e in self.events:
            lines.append(json.dumps(e, separators=(",", ":"), ensure_ascii=False))
        return "\n".join(lines)

    @classmethod
    def from_ndjson(cls, text: str) -> "ReplayLog":
        """Parse a string of NDJSON into a ReplayLog.

        Skips blank lines.  Validates schema major version against
        REPLAY_LOG_SCHEMA — refuses unknown majors with a clear error
        rather than silently misrendering.
        """
        log = cls()
        first = True
        for raw in text.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            if first:
                first = False
                if obj.get("kind") == "HEADER":
                    schema = str(obj.get("schema", "0.0"))
                    major_have = schema.split(".", 1)[0]
                    major_want = REPLAY_LOG_SCHEMA.split(".", 1)[0]
                    if major_have != major_want:
                        raise ValueError(
                            f"Replay log schema mismatch: got {schema}, "
                            f"this build understands {REPLAY_LOG_SCHEMA}"
                        )
                    log.schema = schema
                    log.seed = int(obj.get("seed", 0))
                    log.deck1 = str(obj.get("deck1", ""))
                    log.deck2 = str(obj.get("deck2", ""))
                    continue
            log.events.append(obj)
        log._seq = len(log.events)
        return log


# ─── Convenience: snapshot helper ───────────────────────────────

def snapshot_state(game) -> Dict[str, Any]:
    """Build the minimal {life, hand, lands, board} snapshot embedded
    in most events.  Kept tiny on purpose — the HTML doesn't need full
    permanent lists at every event, only at TURN_START.
    """
    if game is None or not getattr(game, "players", None):
        return {}
    p0, p1 = game.players[0], game.players[1]
    return {
        "life": [p0.life, p1.life],
        "hand": [len(p0.hand), len(p1.hand)],
        "lands": [len(p0.lands), len(p1.lands)],
    }


def snapshot_board(game, pidx: int) -> Dict[str, Any]:
    """Per-player board snapshot for TURN_START events.

    Format mirrors what build_replay.py needs to render a board card —
    creatures with P/T + tapped + equipment, lands by name, other
    permanents as plain names.
    """
    if game is None or pidx not in (0, 1):
        return {}
    p = game.players[pidx]
    creatures = []
    for c in p.creatures:
        creatures.append({
            "name": c.name,
            "p": c.power if c.power is not None else 0,
            "t": c.toughness if c.toughness is not None else 0,
            "tapped": bool(getattr(c, "tapped", False)),
            "summoning_sick": bool(getattr(c, "has_summoning_sickness", False)),
        })
    other = []
    for c in p.battlefield:
        if c in p.creatures or c in p.lands:
            continue
        other.append(c.name)
    return {
        "creatures": creatures,
        "lands": [c.name for c in p.lands],
        "other": other,
        "hand_size": len(p.hand),
        "library": len(p.library),
        "graveyard": len(p.graveyard),
        "life": p.life,
    }
