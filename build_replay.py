"""build_replay.py — MTG replay HTML builder.

Usage:
    # NEW format (recommended): structured NDJSON event log
    python run_meta.py --bo3 storm affinity -s 55555 --dump-replay r.ndjson
    python build_replay.py r.ndjson out.html 55555

    # LEGACY format (still works): text log from --bo3 stdout
    python run_meta.py --bo3 storm affinity -s 55555 > r.txt
    python build_replay.py r.txt out.html 55555

The two formats are auto-detected.  NDJSON renders the new
decision-first viewer with EV bars, alternative plays, subsystem
deltas, and a 👍/👎 feedback form on every decision.  Text falls back
to the original turn-card renderer in build_replay_legacy.py — kept
unchanged so existing logs in `replays/` still build.

Why decision-first
------------------
The old viewer surfaced "what happened this turn"; the new one surfaces
"where the AI's choice was close or wrong" so reviewers can flag bad
plays without scrolling.  Each decision card shows:

  • the chosen play with its EV
  • up to 4 runner-up plays with EV gap (red bar = bigger gap)
  • subsystem contributions (clock / BHI / combo) when available
  • the goal at decision time
  • a feedback form: 👍 / 👎 / freeform note → exports as JSONL

Feedback persists in localStorage AND is exportable from the toolbar:
the "Export feedback" button writes a JSONL blob you can drop into a
PR or grep across runs to find systematic AI failure modes.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from typing import Any, Dict, List, Optional


# ─── Format sniff ───────────────────────────────────────────────

def sniff_format(text: str) -> str:
    """Return 'ndjson' if the file looks like NDJSON header+events, else
    'text' (legacy human-readable log).

    Heuristic: first non-empty line parses as JSON with kind=='HEADER'.
    No partial-NDJSON support — we don't try to recover from a corrupt
    middle line because the upstream emitter is deterministic.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
                if obj.get("kind") == "HEADER":
                    return "ndjson"
            except json.JSONDecodeError:
                pass
        return "text"
    return "text"


# ─── Helpers ────────────────────────────────────────────────────

def esc(s: Any) -> str:
    """HTML-escape a value rendered as text."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def scryfall_url(card: str) -> str:
    """Build a Scryfall thumbnail URL for hover-image previews.

    Uses `version=small` (~150KB) so a 200-card replay loads fast.
    """
    return ("https://api.scryfall.com/cards/named?exact="
            + urllib.parse.quote(card)
            + "&format=image&version=small")


def play_kind(action: str, card: Optional[str]) -> str:
    """Coarse category for the colored badge on each play.

    Mirrors the legacy classify() — kept tiny because the structured
    log already has `action`, so we mostly route on that.
    """
    if action in ("play_land",):
        return "land"
    if action == "cycle":
        return "cycle"
    if action == "suspend":
        return "suspend"
    if action == "equip":
        return "equip"
    if action == "cast_spell":
        return "spell"
    return "other"


def ev_bar_pct(value: float, lo: float, hi: float) -> int:
    """Map an EV in [lo, hi] to a 0-100% bar width.  Clamps."""
    if hi <= lo:
        return 50
    pct = (value - lo) / (hi - lo) * 100.0
    return max(0, min(100, int(pct)))


# ─── NDJSON parser → render-ready model ─────────────────────────

def parse_ndjson(text: str) -> Dict[str, Any]:
    """Parse the structured replay log into a render model.

    Output shape:
        {
          "header":  {schema, seed, deck1, deck2, ...},
          "games":   [{number, on_play, events: [...], result: {...}}],
        }

    Within `events`, each item carries the original kind plus a
    `display_turn` for grouping in the HTML.  No regex; pure JSON.
    """
    header: Dict[str, Any] = {}
    games: List[Dict[str, Any]] = []
    current_game: Optional[Dict[str, Any]] = None

    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        obj = json.loads(raw)
        kind = obj.get("kind")
        if kind == "HEADER":
            header = obj
            continue
        if kind == "GAME_START":
            current_game = {
                "number": obj.get("game", len(games) + 1),
                "on_play": obj.get("on_play"),
                "deck1": obj.get("deck1"),
                "deck2": obj.get("deck2"),
                "events": [],
                "result": None,
            }
            games.append(current_game)
            continue
        if kind == "GAME_END":
            if current_game is not None:
                current_game["result"] = {
                    "winner": obj.get("winner"),
                    "winner_idx": obj.get("winner_idx"),
                    "turns": obj.get("turns"),
                    "win_condition": obj.get("win_condition"),
                    "life": obj.get("life", []),
                }
            continue
        if kind == "MATCH_END":
            header["match_winner"] = obj.get("winner")
            header["match_score"] = obj.get("score")
            continue
        if current_game is None:
            continue
        current_game["events"].append(obj)

    return {"header": header, "games": games}


# ─── HTML renderer ──────────────────────────────────────────────

def render_html(model: Dict[str, Any], seed: Any) -> str:
    """Render the NDJSON model as a self-contained HTML document."""
    header = model["header"]
    games = model["games"]
    deck1 = header.get("deck1", "Deck 1")
    deck2 = header.get("deck2", "Deck 2")
    total_decisions = sum(
        1 for g in games for e in g["events"]
        if e.get("kind") in ("DECISION", "RESPONSE_DECISION")
    )

    games_html_parts = [render_game(g, gi) for gi, g in enumerate(games)]
    games_html = "\n".join(games_html_parts)

    return _PAGE_TEMPLATE.format(
        title=f"{esc(deck1)} vs {esc(deck2)} — seed {seed}",
        deck1=esc(deck1),
        deck2=esc(deck2),
        seed=esc(seed),
        match_score=_match_score_html(header),
        total_games=len(games),
        total_decisions=total_decisions,
        games_html=games_html,
        css=_CSS,
        js=_JS,
    )


def _match_score_html(header: Dict[str, Any]) -> str:
    score = header.get("match_score") or []
    winner = header.get("match_winner")
    if not score or winner is None:
        return ""
    return (f'<span class="match-score">'
            f'<strong>{esc(winner)}</strong> '
            f'wins {max(score)}–{min(score)}'
            f'</span>')


def render_game(game: Dict[str, Any], game_index: int) -> str:
    """Render one game as a section: header + grouped turn cards."""
    number = game.get("number", game_index + 1)
    deck1 = game.get("deck1") or "Deck 1"
    deck2 = game.get("deck2") or "Deck 2"
    on_play = game.get("on_play") or "?"
    result = game.get("result") or {}
    winner = result.get("winner") or "draw"
    turns = result.get("turns", "?")
    win_cond = result.get("win_condition", "?")

    turns_html = render_turns(game)

    return f'''
<section class="game" id="game-{number}" data-game="{number}">
  <header class="game-header">
    <h2>Game {number}</h2>
    <div class="game-meta">
      <span class="chip on-play">on play: <strong>{esc(on_play)}</strong></span>
      <span class="chip result">winner: <strong>{esc(winner)}</strong> ({esc(win_cond)}, T{esc(turns)})</span>
      <span class="chip decks">{esc(deck1)} <em>vs</em> {esc(deck2)}</span>
    </div>
  </header>
  <div class="turns">
    {turns_html}
  </div>
</section>
'''


def render_turns(game: Dict[str, Any]) -> str:
    """Group events by turn, render each as a collapsible card."""
    turns: Dict[int, Dict[str, Any]] = {}
    turn_order: List[int] = []
    mulligans: List[Dict[str, Any]] = []
    for e in game["events"]:
        kind = e.get("kind")
        if kind == "MULLIGAN":
            mulligans.append(e)
            continue
        # Coerce turn to int — early replay logs from before the
        # display_turn fix emitted strings, and we want stable sort.
        try:
            t = int(e.get("turn", 0))
        except (TypeError, ValueError):
            t = 0
        if t not in turns:
            turns[t] = {"events": [], "decisions": [],
                        "response_decisions": [],
                        "header": None, "boards": None}
            turn_order.append(t)
        turns[t]["events"].append(e)
        if kind == "TURN_START":
            turns[t]["header"] = e
            turns[t]["boards"] = e.get("board")
        elif kind == "DECISION":
            turns[t]["decisions"].append(e)
        elif kind == "RESPONSE_DECISION":
            # Response decisions live in their own bucket so the turn
            # card renders them as a sibling section to main-phase
            # decisions.  Reviewers can collapse one without losing the
            # other and grep the HTML for `response-decision` to find
            # counter/blink/instant-removal choices.
            turns[t]["response_decisions"].append(e)

    parts = []
    if mulligans:
        parts.append(render_mulligans(mulligans))
    for t in sorted(turn_order):
        if t == 0:
            interesting = [
                e for e in turns[t]["events"]
                if e.get("kind") in (
                    "DECISION", "RESPONSE_DECISION", "PLAY", "TRIGGER")
            ]
            if not interesting:
                continue
        parts.append(render_turn(t, turns[t], game.get("number", 1)))
    return "\n".join(parts)


def render_mulligans(events: List[Dict[str, Any]]) -> str:
    rows = []
    for e in events:
        actor = esc(e.get("actor", "?"))
        kept = "KEEP" if e.get("keep") else "MULLIGAN"
        size = e.get("hand_size", "?")
        reason = esc(e.get("reason", ""))
        cards = e.get("kept") or []
        pills = " ".join(card_pill(c) for c in cards)
        cls = "keep" if e.get("keep") else "mull-no"
        rows.append(
            f'<div class="mull">'
            f'<span class="mull-decision {cls}">{kept}</span>'
            f'<span class="mull-actor">{actor}</span>'
            f'<span class="mull-size">→ {esc(size)}</span>'
            f'<span class="mull-reason">{reason}</span>'
            f'<div class="mull-cards">{pills}</div>'
            f'</div>'
        )
    return f'<div class="mulls"><h3>Mulligans</h3>{"".join(rows)}</div>'


def card_pill(name: str) -> str:
    """A small inline card chip with hover-thumbnail."""
    if not name:
        return ""
    return (f'<span class="card-pill" data-card="{esc(name)}">'
            f'{esc(name)}</span>')


def render_turn(turn: int, bundle: Dict[str, Any], game_number: int) -> str:
    """Render a single turn card."""
    header = bundle.get("header") or {}
    actor = esc(header.get("actor", "?"))
    pidx = header.get("pidx", -1)
    state = header.get("state") or {}
    life = state.get("life", [])
    boards = bundle.get("boards") or [None, None]

    decision_html = "".join(
        render_decision(d, game_number) for d in bundle.get("decisions", [])
    )
    response_html = "".join(
        render_response_decision(d, game_number)
        for d in bundle.get("response_decisions", [])
    )
    combat_html = render_combat(bundle.get("events", []))
    play_html = render_plays(bundle.get("events", []))

    life_str = (f'<span class="life">'
                f'<span class="p1-life">{life[0]}</span>'
                f' <span class="vs-dot">·</span> '
                f'<span class="p2-life">{life[1]}</span>'
                f'</span>') if life else ''

    actor_meta = f' <em class="p">(P{pidx+1})</em>' if pidx >= 0 else ''

    n_main = len(bundle.get("decisions", []))
    n_resp = len(bundle.get("response_decisions", []))
    # Sum both for the turn header so reviewers see the full decision
    # surface at a glance.
    n_total = n_main + n_resp
    resp_count_html = (
        f' <span class="turn-resp-count">({n_resp} response)</span>'
        if n_resp else ""
    )

    return f'''
<details class="turn" data-turn="{turn}" open>
  <summary class="turn-summary">
    <span class="turn-num">T{turn}</span>
    <span class="turn-actor">{actor}{actor_meta}</span>
    {life_str}
    <span class="turn-counts">{n_total} decisions{resp_count_html}</span>
  </summary>
  <div class="turn-body">
    {render_boards(boards)}
    {decision_html}
    {response_html}
    {combat_html}
    {play_html}
  </div>
</details>
'''


def render_boards(boards: List[Optional[Dict[str, Any]]]) -> str:
    """Side-by-side mini-boards for each player at start of turn."""
    if not boards or not any(boards):
        return ""
    cols = []
    for pidx, b in enumerate(boards):
        if not b:
            continue
        creatures = b.get("creatures", [])
        creature_html = "".join(
            f'<span class="cre" data-card="{esc(c["name"])}">'
            f'{esc(c["name"])} <em>{c["p"]}/{c["t"]}</em>'
            + ('<span class="tap">↻</span>' if c.get("tapped") else '')
            + ('<span class="sick">z</span>' if c.get("summoning_sick") else '')
            + '</span>'
            for c in creatures
        )
        other_html = "".join(card_pill(n) for n in b.get("other") or [])
        lands_html = "".join(card_pill(n) for n in b.get("lands") or [])
        cols.append(f'''
<div class="board p{pidx+1}">
  <div class="board-head">
    <span class="dot p{pidx+1}"></span>
    <strong>P{pidx+1}</strong>
    <span class="board-stats">life {b.get("life", "?")} · hand {b.get("hand_size", "?")} · lib {b.get("library", "?")}</span>
  </div>
  <div class="row creatures">{creature_html or '<em class="mute">no creatures</em>'}</div>
  {f'<div class="row other">{other_html}</div>' if other_html else ""}
  <div class="row lands">{lands_html or '<em class="mute">no lands</em>'}</div>
</div>
''')
    return f'<div class="boards">{"".join(cols)}</div>'


def render_decision(d: Dict[str, Any], game_number: int) -> str:
    """Render one DECISION event as the centerpiece subcard."""
    decision_id = d.get("decision_id", f"d{d.get('seq', 0)}")
    actor = esc(d.get("actor", "?"))
    goal = esc(d.get("goal") or "")
    chosen = d.get("chosen") or {}
    alts = d.get("alternatives") or []
    n_cand = d.get("candidates_n", "?")

    chosen_card = chosen.get("card")
    chosen_action = esc(chosen.get("action", "pass"))
    chosen_ev = chosen.get("ev", 0.0)
    chosen_reason = esc(chosen.get("reason", ""))
    chosen_targets = chosen.get("targets") or []
    target_html = (' → ' + " ".join(card_pill(t) for t in chosen_targets)
                   if chosen_targets else "")
    chosen_card_html = (card_pill(chosen_card) if chosen_card
                        else f'<em>{chosen_action}</em>')

    all_evs = [float(chosen_ev)] + [float(a.get("ev", 0)) for a in alts]
    lo = min(all_evs) if all_evs else 0
    hi = max(all_evs) if all_evs else 1
    if hi - lo < 0.1:
        hi = lo + 1.0

    alt_rows = []
    for a in alts:
        a_card = a.get("card") or "Pass"
        a_action = esc(a.get("action", "?"))
        a_ev = float(a.get("ev", 0))
        a_gap = float(a.get("gap", 0))
        a_reason = esc(a.get("rejected_because") or a.get("reason", ""))
        bar = ev_bar_pct(a_ev, lo, hi)
        gap_class = ("gap-tight" if a_gap < 1.0 else
                     ("gap-wide" if a_gap > 5.0 else "gap-mid"))
        a_card_html = (card_pill(a_card) if a.get("card")
                       else f'<em>{a_action}</em>')
        alt_rows.append(f'''
<div class="alt {gap_class}">
  <div class="alt-row">
    <span class="alt-card">{a_card_html}</span>
    <span class="alt-ev">{a_ev:.2f}</span>
    <span class="alt-gap">Δ {a_gap:.2f}</span>
    <span class="alt-bar"><span style="width:{bar}%"></span></span>
  </div>
  <div class="alt-reason">{a_reason}</div>
</div>
''')

    subsystems = d.get("subsystems") or {}
    subs_html = ""
    if subsystems:
        chips = []
        for k, v in subsystems.items():
            try:
                vf = float(v)
                cls = "pos" if vf > 0 else ("neg" if vf < 0 else "zero")
                chips.append(
                    f'<span class="sub-chip {cls}">{esc(k)} {vf:+.2f}</span>'
                )
            except (TypeError, ValueError):
                chips.append(
                    f'<span class="sub-chip">{esc(k)}: {esc(v)}</span>'
                )
        subs_html = f'<div class="subsystems">{"".join(chips)}</div>'

    goal_html = (f'<span class="dec-goal">goal: {goal}</span>'
                 if goal else "")
    alts_section = (f'<div class="alts-label">runner-ups</div>'
                    f'{"".join(alt_rows)}' if alt_rows else "")
    chosen_reason_html = (f'<div class="chosen-reason">{chosen_reason}</div>'
                          if chosen_reason else "")

    return f'''
<div class="decision" id="{decision_id}" data-decision-id="{decision_id}">
  <div class="dec-head">
    <a class="dec-anchor" href="#{decision_id}">#{decision_id}</a>
    <span class="dec-actor">{actor}</span>
    {goal_html}
    <span class="dec-cands">{esc(n_cand)} candidates</span>
  </div>
  <div class="chosen">
    <div class="chosen-row">
      <span class="chosen-label">CHOSEN</span>
      <span class="chosen-card">{chosen_card_html}{target_html}</span>
      <span class="chosen-ev">{float(chosen_ev):.2f}</span>
    </div>
    {chosen_reason_html}
    {subs_html}
  </div>
  {alts_section}
  <form class="feedback" data-decision-id="{decision_id}" onsubmit="return false;">
    <button type="button" class="thumbs up" data-thumb="up" title="good play">👍</button>
    <button type="button" class="thumbs down" data-thumb="down" title="bad play">👎</button>
    <input type="text" class="fb-note" placeholder="why? (saved locally)" />
    <span class="fb-status"></span>
  </form>
</div>
'''


def render_response_decision(d: Dict[str, Any], game_number: int) -> str:
    """Render one RESPONSE_DECISION event as its own sub-card.

    The structure mirrors render_decision (chosen card, EV, alternatives,
    feedback form) so reviewers don't have to relearn the UI; the extras
    are:
      • Stack-item banner above the chosen action — the spell being
        responded to, with its controller and effective cost.  This is
        the audit-driving info: "which spell on the stack did the AI
        decide to counter (or let resolve)?"
      • Held-counter-floor EV chip — the threshold the threat had to
        clear before a counter was justified.  Makes "why didn't the
        AI counter?" reviewable.
      • A muted/pass style when the AI chose not to respond, so
        reviewers can scan for declined-counter moments in the HTML.

    Uses the `response-decision` CSS class so the test suite (and
    reviewers grepping the output) can find the section reliably.
    """
    decision_id = d.get("decision_id", f"d{d.get('seq', 0)}")
    actor = esc(d.get("actor", "?"))
    goal = esc(d.get("goal") or "")
    chosen = d.get("chosen") or {}
    alts = d.get("alternatives") or []
    n_cand = d.get("candidates_n", "?")
    stack_item = d.get("stack_item") or {}
    floor_ev = d.get("held_counter_floor_ev")

    chosen_action_raw = chosen.get("action", "pass")
    chosen_card = chosen.get("card")
    chosen_action = esc(chosen_action_raw)
    chosen_ev = chosen.get("ev", 0.0)
    chosen_reason = esc(chosen.get("reason", ""))
    chosen_targets = chosen.get("targets") or []
    target_html = (' → ' + " ".join(card_pill(t) for t in chosen_targets)
                   if chosen_targets else "")
    chosen_card_html = (card_pill(chosen_card) if chosen_card
                        else f'<em>{chosen_action}</em>')

    # Style hook for "AI declined to respond" — distinct from the
    # active CHOSEN-a-card flow.
    is_pass = (chosen_action_raw == "pass" or not chosen_card)
    extra_cls = " response-pass" if is_pass else ""

    # Stack-item banner: the reviewer's primary "what was being
    # responded to?" surface. Falls back gracefully when absent.
    if stack_item:
        si_name = stack_item.get("name", "?")
        si_controller = stack_item.get("controller", "?")
        si_cost = stack_item.get("cost", "?")
        stack_banner = (
            f'<div class="stack-item">'
            f'→ Counter target: {card_pill(si_name)} '
            f'<span class="stack-meta">cost {esc(si_cost)} · '
            f'controlled by P{esc(int(si_controller) + 1 if isinstance(si_controller, int) else si_controller)}</span>'
            f'</div>'
        )
    else:
        stack_banner = ""

    floor_html = ""
    if floor_ev is not None:
        try:
            floor_html = (
                f'<span class="floor-ev" title="held_counter_floor_ev — '
                f'minimum EV a held counter is worth, used to gate firing">'
                f'floor EV {float(floor_ev):.2f}</span>'
            )
        except (TypeError, ValueError):
            floor_html = ""

    all_evs = [float(chosen_ev)] + [float(a.get("ev", 0)) for a in alts]
    lo = min(all_evs) if all_evs else 0
    hi = max(all_evs) if all_evs else 1
    if hi - lo < 0.1:
        hi = lo + 1.0

    alt_rows = []
    for a in alts:
        a_card = a.get("card") or "Pass"
        a_action = esc(a.get("action", "?"))
        a_ev = float(a.get("ev", 0))
        a_gap = float(a.get("gap", 0))
        a_reason = esc(a.get("rejected_because") or a.get("reason", ""))
        bar = ev_bar_pct(a_ev, lo, hi)
        gap_class = ("gap-tight" if a_gap < 1.0 else
                     ("gap-wide" if a_gap > 5.0 else "gap-mid"))
        a_card_html = (card_pill(a_card) if a.get("card")
                       else f'<em>{a_action}</em>')
        alt_rows.append(f'''
<div class="alt {gap_class}">
  <div class="alt-row">
    <span class="alt-card">{a_card_html}</span>
    <span class="alt-ev">{a_ev:.2f}</span>
    <span class="alt-gap">Δ {a_gap:.2f}</span>
    <span class="alt-bar"><span style="width:{bar}%"></span></span>
  </div>
  <div class="alt-reason">{a_reason}</div>
</div>
''')

    subsystems = d.get("subsystems") or {}
    subs_html = ""
    if subsystems:
        chips = []
        for k, v in subsystems.items():
            try:
                vf = float(v)
                cls = "pos" if vf > 0 else ("neg" if vf < 0 else "zero")
                chips.append(
                    f'<span class="sub-chip {cls}">{esc(k)} {vf:+.2f}</span>'
                )
            except (TypeError, ValueError):
                chips.append(
                    f'<span class="sub-chip">{esc(k)}: {esc(v)}</span>'
                )
        subs_html = f'<div class="subsystems">{"".join(chips)}</div>'

    goal_html = (f'<span class="dec-goal">goal: {goal}</span>'
                 if goal else "")
    alts_section = (f'<div class="alts-label">runner-ups</div>'
                    f'{"".join(alt_rows)}' if alt_rows else "")
    chosen_reason_html = (f'<div class="chosen-reason">{chosen_reason}</div>'
                          if chosen_reason else "")

    chosen_label = "PASSED" if is_pass else "RESPOND"

    return f'''
<div class="decision response-decision{extra_cls}" id="{decision_id}" data-decision-id="{decision_id}">
  <div class="dec-head">
    <a class="dec-anchor" href="#{decision_id}">#{decision_id}</a>
    <span class="dec-actor">{actor}</span>
    <span class="response-tag">RESPONSE</span>
    {goal_html}
    {floor_html}
    <span class="dec-cands">{esc(n_cand)} candidates</span>
  </div>
  {stack_banner}
  <div class="chosen">
    <div class="chosen-row">
      <span class="chosen-label">{chosen_label}</span>
      <span class="chosen-card">{chosen_card_html}{target_html}</span>
      <span class="chosen-ev">{float(chosen_ev):.2f}</span>
    </div>
    {chosen_reason_html}
    {subs_html}
  </div>
  {alts_section}
  <form class="feedback" data-decision-id="{decision_id}" onsubmit="return false;">
    <button type="button" class="thumbs up" data-thumb="up" title="good play">👍</button>
    <button type="button" class="thumbs down" data-thumb="down" title="bad play">👎</button>
    <input type="text" class="fb-note" placeholder="why? (saved locally)" />
    <span class="fb-status"></span>
  </form>
</div>
'''


def render_plays(events: List[Dict[str, Any]]) -> str:
    plays = [e for e in events if e.get("kind") == "PLAY"]
    if not plays:
        return ""
    rows = []
    for p in plays:
        kind = play_kind(p.get("action", ""), p.get("card"))
        card_html = card_pill(p.get("card") or "") or "—"
        targets = p.get("targets") or []
        tgt = (' → ' + " ".join(card_pill(t) for t in targets)
               if targets else "")
        rows.append(
            f'<li class="play kind-{kind}">'
            f'<span class="play-act">{esc(p.get("action", ""))}</span>'
            f'{card_html}{tgt}</li>'
        )
    return f'<ul class="plays">{"".join(rows)}</ul>'


def render_combat(events: List[Dict[str, Any]]) -> str:
    combat = [e for e in events if e.get("kind") == "COMBAT"]
    if not combat:
        return ""
    rows = []
    for c in combat:
        sub = c.get("sub")
        actor = esc(c.get("actor", "?"))
        if sub == "declare_attackers":
            atk = c.get("attackers") or []
            html = " ".join(
                f'<span class="atk">{esc(a["name"])} '
                f'<em>{a["p"]}/{a["t"]}</em></span>' for a in atk
            )
            rows.append(
                f'<div class="cmb attack">⚔ {actor} attacks: {html}</div>'
            )
        elif sub == "no_attack":
            rows.append(
                f'<div class="cmb no-atk">{actor} does not attack</div>'
            )
        elif sub == "declare_blockers":
            blocks = c.get("blocks") or []
            blk_html = " · ".join(
                f'<span class="blk">{esc(b["attacker"])} '
                f'blocked by {esc(", ".join(b["blockers"])) or "—"}</span>'
                for b in blocks
            )
            rows.append(
                f'<div class="cmb block">🛡 {actor} blocks: {blk_html}</div>'
            )
        elif sub == "no_block":
            rows.append(
                f'<div class="cmb no-blk">{actor} does not block</div>'
            )
        elif sub == "damage":
            dmg = c.get("damage", 0)
            defender = esc(c.get("defender", "?"))
            life_b = c.get("defender_life_before", "?")
            life_a = c.get("defender_life_after", "?")
            cls = "lethal" if c.get("lethal") else ""
            lethal_tag = ('<span class="lethal-tag">☠ LETHAL</span>'
                          if c.get("lethal") else '')
            rows.append(
                f'<div class="cmb damage {cls}">'
                f'💥 {actor} deals <strong>{dmg}</strong> to {defender}'
                f' ({life_b} → {life_a}){lethal_tag}</div>'
            )
    return f'<div class="combat">{"".join(rows)}</div>'


# ─── CSS / JS / Page template ───────────────────────────────────

_CSS = r"""
:root {
  --bg: #ffffff; --fg: #1f2328; --mute: #656d76; --line: #d0d7de;
  --p1: #0969da; --p2: #d1242f; --good: #1a7f37; --warn: #bf8700;
  --bad: #cf222e; --card-bg: #f6f8fa; --hover: #ddf4ff;
  --chosen-bg: #dafbe1; --gap-tight: #fff8c5; --gap-mid: #ffe9b3;
  --gap-wide: #ffcecb;
}
* { box-sizing: border-box; }
body { font: 14px/1.5 -apple-system, "Segoe UI", system-ui, sans-serif;
       color: var(--fg); background: var(--bg); margin: 0; padding: 0; }
header.top { position: sticky; top: 0; z-index: 50; background: var(--bg);
             border-bottom: 1px solid var(--line);
             padding: 12px 24px; display: flex; gap: 16px; align-items: center;
             flex-wrap: wrap; }
header.top h1 { margin: 0; font-size: 16px; font-weight: 600; }
header.top h1 em { color: var(--mute); font-style: normal; font-weight: 400;
                   margin: 0 6px; }
header.top .seed { color: var(--mute);
                   font-family: ui-monospace, "JetBrains Mono", monospace; }
header.top .stat { color: var(--mute); font-size: 13px; }
header.top .stat strong { color: var(--fg); }
header.top .actions { margin-left: auto; display: flex; gap: 8px; }
header.top .actions button { font: inherit; padding: 6px 12px; cursor: pointer;
                            border: 1px solid var(--line);
                            background: var(--card-bg);
                            border-radius: 6px; }
header.top .actions button:hover { background: var(--hover); }
header.top .match-score { padding: 4px 10px; background: var(--chosen-bg);
                         border-radius: 6px; font-size: 13px; }
main { max-width: 1100px; margin: 0 auto; padding: 16px 24px 80px; }
.game { margin-bottom: 32px; }
.game-header { display: flex; align-items: baseline; gap: 16px;
               flex-wrap: wrap; margin-bottom: 12px; }
.game-header h2 { margin: 0; font-size: 22px; }
.game-meta { display: flex; gap: 8px; flex-wrap: wrap; }
.chip { padding: 3px 10px; background: var(--card-bg); border-radius: 12px;
        font-size: 12px; color: var(--mute); }
.chip strong { color: var(--fg); }
.chip em { font-style: italic; color: var(--mute); }
.mulls { background: var(--card-bg); border: 1px solid var(--line);
         border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; }
.mulls h3 { margin: 0 0 8px 0; font-size: 13px; color: var(--mute);
            text-transform: uppercase; letter-spacing: 0.05em; }
.mull { padding: 6px 0; border-bottom: 1px dashed var(--line); display: flex;
        gap: 8px; align-items: baseline; flex-wrap: wrap; }
.mull:last-child { border-bottom: 0; }
.mull-decision { font-size: 11px; padding: 1px 8px; border-radius: 3px;
                 font-weight: 600; }
.mull-decision.keep { background: var(--chosen-bg); color: var(--good); }
.mull-decision.mull-no { background: var(--gap-wide); color: var(--bad); }
.mull-actor { font-weight: 600; }
.mull-size { font-family: ui-monospace, "JetBrains Mono", monospace;
             color: var(--mute); }
.mull-reason { color: var(--mute); flex: 1; }
.mull-cards { width: 100%; padding-top: 4px; }
details.turn { border: 1px solid var(--line); border-radius: 8px;
               margin-bottom: 12px; background: var(--bg); }
details.turn[open] { box-shadow: 0 1px 4px rgba(0,0,0,0.04); }
.turn-summary { display: flex; gap: 12px; align-items: baseline;
                padding: 10px 16px; cursor: pointer; user-select: none;
                list-style: none; }
.turn-summary::-webkit-details-marker { display: none; }
.turn-summary::before { content: "▸"; color: var(--mute);
                        transition: transform 0.15s; }
details.turn[open] .turn-summary::before { transform: rotate(90deg);
                                          display: inline-block; }
.turn-num { font-family: ui-monospace, "JetBrains Mono", monospace;
            font-weight: 700; color: var(--p1); }
.turn-actor { font-weight: 600; }
.turn-actor .p { color: var(--mute); font-style: normal; font-size: 12px; }
.life { font-family: ui-monospace, "JetBrains Mono", monospace;
        margin-left: auto; font-size: 13px; }
.p1-life { color: var(--p1); font-weight: 600; }
.p2-life { color: var(--p2); font-weight: 600; }
.vs-dot { color: var(--mute); }
.turn-counts { color: var(--mute); font-size: 12px; }
.turn-body { padding: 0 16px 16px; }
.boards { display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
          margin-bottom: 12px; }
.board { background: var(--card-bg); border: 1px solid var(--line);
         border-radius: 6px; padding: 10px; }
.board-head { display: flex; align-items: center; gap: 8px;
              margin-bottom: 6px; font-size: 13px; }
.board-head .dot { width: 8px; height: 8px; border-radius: 50%;
                   display: inline-block; }
.board-head .dot.p1 { background: var(--p1); }
.board-head .dot.p2 { background: var(--p2); }
.board-stats { color: var(--mute);
              font-family: ui-monospace, "JetBrains Mono", monospace;
              font-size: 11px; margin-left: auto; }
.board .row { padding: 3px 0; display: flex; flex-wrap: wrap; gap: 4px; }
.cre { padding: 2px 8px; background: #fff; border: 1px solid var(--line);
       border-radius: 11px; font-size: 12px; }
.cre em { color: var(--mute); font-style: normal;
          font-family: ui-monospace, "JetBrains Mono", monospace; }
.cre .tap, .cre .sick { color: var(--warn); margin-left: 2px; }
.card-pill { padding: 1px 7px; background: #fff; border: 1px solid var(--line);
             border-radius: 10px; font-size: 11px; cursor: pointer; }
.card-pill:hover { background: var(--hover); }
.mute { color: var(--mute); }
.decision { border-left: 3px solid var(--p1); padding: 10px 12px; margin: 8px 0;
            background: #f9fbff; border-radius: 0 6px 6px 0;
            scroll-margin-top: 80px; }
.decision:target { box-shadow: 0 0 0 2px var(--p1); }
.dec-head { display: flex; gap: 12px; align-items: baseline;
            margin-bottom: 6px; font-size: 12px; flex-wrap: wrap; }
.dec-anchor { font-family: ui-monospace, "JetBrains Mono", monospace;
              color: var(--mute); text-decoration: none; }
.dec-anchor:hover { color: var(--p1); }
.dec-actor { font-weight: 600; color: var(--fg); }
.dec-goal { color: var(--mute); font-style: italic; }
.dec-cands { margin-left: auto; color: var(--mute); }
.chosen { background: var(--chosen-bg); border-radius: 5px; padding: 8px 10px; }
.chosen-row { display: flex; align-items: baseline; gap: 8px; }
.chosen-label { font-size: 10px; padding: 1px 6px; border-radius: 3px;
                background: var(--good); color: #fff; font-weight: 600;
                letter-spacing: 0.04em; }
.chosen-card { flex: 1; }
.chosen-ev { font-family: ui-monospace, "JetBrains Mono", monospace;
             font-weight: 700; color: var(--good); }
.chosen-reason { font-size: 12px; color: var(--mute); margin-top: 4px; }
.subsystems { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.sub-chip { font-family: ui-monospace, "JetBrains Mono", monospace;
            font-size: 11px; padding: 1px 7px; border-radius: 3px;
            background: #fff; border: 1px solid var(--line); }
.sub-chip.pos { background: #dafbe1; color: var(--good); border-color: #b4d7c0; }
.sub-chip.neg { background: #ffebe9; color: var(--bad); border-color: #f6c8c5; }
.sub-chip.zero { color: var(--mute); }
.alts-label { font-size: 11px; color: var(--mute); margin: 8px 0 4px;
              text-transform: uppercase; letter-spacing: 0.05em; }
.alt { padding: 4px 0; border-bottom: 1px dotted var(--line); }
.alt:last-of-type { border-bottom: 0; }
.alt-row { display: flex; align-items: baseline; gap: 10px; }
.alt-card { flex: 1; }
.alt-ev { font-family: ui-monospace, "JetBrains Mono", monospace;
          color: var(--mute); width: 50px; text-align: right; }
.alt-gap { font-family: ui-monospace, "JetBrains Mono", monospace;
           width: 70px; text-align: right; font-size: 12px; }
.alt-bar { width: 100px; height: 4px; background: #eee; border-radius: 2px;
           position: relative; overflow: hidden; }
.alt-bar > span { display: block; height: 100%; background: var(--mute); }
.gap-tight .alt-gap { color: var(--warn); font-weight: 600; }
.gap-tight .alt-bar > span { background: var(--warn); }
.gap-mid .alt-gap { color: var(--mute); }
.gap-wide .alt-gap { color: var(--mute); opacity: 0.7; }
.alt-reason { font-size: 11px; color: var(--mute); padding-left: 8px; }
.response-decision { border-left-color: var(--p2); background: #fff8f7; }
.response-decision:target { box-shadow: 0 0 0 2px var(--p2); }
.response-decision .response-tag { font-size: 10px; padding: 1px 6px;
                                   border-radius: 3px; background: var(--p2);
                                   color: #fff; font-weight: 600;
                                   letter-spacing: 0.04em; }
.response-decision .stack-item { margin: 6px 0; padding: 6px 10px;
                                 background: #fff; border: 1px solid var(--line);
                                 border-radius: 5px; font-size: 13px; }
.response-decision .stack-item .stack-meta { color: var(--mute);
                                             font-size: 11px;
                                             margin-left: 8px; }
.response-decision .floor-ev { font-family: ui-monospace, "JetBrains Mono", monospace;
                               color: var(--mute); font-size: 11px;
                               padding: 1px 6px;
                               background: var(--card-bg);
                               border-radius: 3px; }
.response-decision.response-pass .chosen { background: var(--card-bg); }
.response-decision.response-pass .chosen-label { background: var(--mute); }
.response-decision.response-pass .chosen-ev { color: var(--mute); }
.turn-resp-count { color: var(--p2); font-size: 11px; }
.feedback { display: flex; align-items: center; gap: 8px; margin-top: 8px;
            padding-top: 8px; border-top: 1px dashed var(--line); }
.thumbs { font-size: 14px; padding: 2px 8px; border: 1px solid var(--line);
          background: #fff; border-radius: 4px; cursor: pointer; }
.thumbs.active.up { background: var(--chosen-bg); border-color: var(--good); }
.thumbs.active.down { background: var(--gap-wide); border-color: var(--bad); }
.fb-note { flex: 1; padding: 3px 6px; border: 1px solid var(--line);
           border-radius: 4px; font: inherit; }
.fb-status { font-size: 11px; color: var(--good); }
.plays { list-style: none; padding: 8px 0 0; margin: 0;
         border-top: 1px dashed var(--line); margin-top: 8px; }
.plays .play { padding: 2px 0; font-size: 12px; }
.play-act { display: inline-block; min-width: 80px; color: var(--mute);
            font-family: ui-monospace, "JetBrains Mono", monospace; }
.combat { padding: 8px 0; border-top: 1px dashed var(--line);
          margin-top: 8px; font-size: 13px; }
.cmb { padding: 3px 0; }
.cmb.damage strong { color: var(--bad); }
.cmb.lethal { background: #ffebe9; padding: 5px 8px; border-radius: 4px; }
.lethal-tag { color: var(--bad); font-weight: 700; margin-left: 8px; }
.atk em, .blk em { color: var(--mute);
                  font-family: ui-monospace, "JetBrains Mono", monospace; }
#card-tip { position: fixed; pointer-events: none; z-index: 1000;
            border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.2);
            display: none; }
#card-tip img { display: block; max-width: 200px; max-height: 280px;
                border-radius: 8px; }
"""

_JS = r"""
(function(){
  const tip = document.createElement('div');
  tip.id = 'card-tip';
  document.body.appendChild(tip);
  const cache = new Map();

  function showTip(el, evt) {
    const name = el.getAttribute('data-card');
    if (!name) return;
    const url = 'https://api.scryfall.com/cards/named?exact='
              + encodeURIComponent(name) + '&format=image&version=small';
    if (!cache.has(name)) {
      const img = new Image();
      img.src = url;
      cache.set(name, img);
    }
    const img = cache.get(name);
    tip.innerHTML = '';
    tip.appendChild(img);
    tip.style.display = 'block';
    moveTip(evt);
  }
  function moveTip(evt) {
    const x = Math.min(evt.clientX + 14, window.innerWidth - 220);
    const y = Math.min(evt.clientY + 14, window.innerHeight - 290);
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  }
  function hideTip() { tip.style.display = 'none'; }
  document.addEventListener('mouseover', (e) => {
    const t = e.target.closest('[data-card]');
    if (t) showTip(t, e);
  });
  document.addEventListener('mousemove', (e) => {
    if (tip.style.display === 'block') moveTip(e);
  });
  document.addEventListener('mouseout', (e) => {
    const t = e.target.closest('[data-card]');
    if (t) hideTip();
  });

  const FB_KEY = 'mtgsim:replay-feedback:' + (window.__REPLAY_ID || 'default');
  function loadFeedback() {
    try { return JSON.parse(localStorage.getItem(FB_KEY) || '{}'); }
    catch (e) { return {}; }
  }
  function saveFeedback(fb) {
    localStorage.setItem(FB_KEY, JSON.stringify(fb));
  }
  function applyFeedback() {
    const fb = loadFeedback();
    document.querySelectorAll('.feedback').forEach(form => {
      const id = form.getAttribute('data-decision-id');
      const entry = fb[id];
      if (!entry) return;
      if (entry.thumb === 'up') {
        form.querySelector('.thumbs.up').classList.add('active');
      } else if (entry.thumb === 'down') {
        form.querySelector('.thumbs.down').classList.add('active');
      }
      if (entry.note) form.querySelector('.fb-note').value = entry.note;
      const status = form.querySelector('.fb-status');
      if (status) status.textContent = '✓ saved';
    });
    updateFeedbackCount();
  }
  function updateFeedbackCount() {
    const fb = loadFeedback();
    const n = Object.keys(fb).length;
    const el = document.getElementById('fb-count');
    if (el) el.textContent = String(n);
  }
  function setFeedback(decisionId, patch) {
    const fb = loadFeedback();
    const cur = fb[decisionId] || {};
    Object.assign(cur, patch);
    if (!cur.thumb && !cur.note) {
      delete fb[decisionId];
    } else {
      cur.timestamp = new Date().toISOString();
      fb[decisionId] = cur;
    }
    saveFeedback(fb);
    updateFeedbackCount();
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.thumbs');
    if (!btn) return;
    const form = btn.closest('.feedback');
    const id = form.getAttribute('data-decision-id');
    const thumb = btn.getAttribute('data-thumb');
    const fb = loadFeedback();
    const cur = fb[id] || {};
    const newThumb = (cur.thumb === thumb) ? null : thumb;
    setFeedback(id, { thumb: newThumb });
    form.querySelectorAll('.thumbs').forEach(b => b.classList.remove('active'));
    if (newThumb) {
      form.querySelector('.thumbs.' + newThumb).classList.add('active');
    }
    form.querySelector('.fb-status').textContent = '✓ saved';
  });

  document.addEventListener('input', (e) => {
    const inp = e.target.closest('.fb-note');
    if (!inp) return;
    const form = inp.closest('.feedback');
    const id = form.getAttribute('data-decision-id');
    setFeedback(id, { note: inp.value });
    form.querySelector('.fb-status').textContent = '✓ saved';
  });

  const exp = document.getElementById('btn-export-fb');
  if (exp) exp.addEventListener('click', () => {
    const fb = loadFeedback();
    const lines = Object.entries(fb).map(([id, v]) =>
      JSON.stringify(Object.assign({decision_id: id}, v)));
    const blob = new Blob([lines.join('\n')], {type: 'application/x-ndjson'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'replay_feedback.jsonl';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  const clr = document.getElementById('btn-clear-fb');
  if (clr) clr.addEventListener('click', () => {
    if (!confirm('Clear all feedback for this replay?')) return;
    localStorage.removeItem(FB_KEY);
    document.querySelectorAll('.feedback').forEach(f => {
      f.querySelectorAll('.thumbs').forEach(b => b.classList.remove('active'));
      f.querySelector('.fb-note').value = '';
      f.querySelector('.fb-status').textContent = '';
    });
    updateFeedbackCount();
  });
  const jmp = document.getElementById('btn-jump-flagged');
  if (jmp) jmp.addEventListener('click', () => {
    const fb = loadFeedback();
    const ids = Object.keys(fb).filter(k => fb[k].thumb === 'down');
    if (ids.length === 0) { alert('No flagged decisions yet'); return; }
    const cur = (location.hash || '').slice(1);
    const idx = ids.indexOf(cur);
    const next = ids[(idx + 1) % ids.length];
    location.hash = next;
  });
  const col = document.getElementById('btn-collapse-all');
  if (col) col.addEventListener('click', () =>
    document.querySelectorAll('details.turn').forEach(d =>
      d.removeAttribute('open')));
  const exp2 = document.getElementById('btn-expand-all');
  if (exp2) exp2.addEventListener('click', () =>
    document.querySelectorAll('details.turn').forEach(d =>
      d.setAttribute('open', '')));

  applyFeedback();
})();
"""

_PAGE_TEMPLATE = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header class="top">
  <h1>{deck1} <em>vs</em> {deck2}</h1>
  <span class="seed">seed {seed}</span>
  <span class="stat"><strong>{total_games}</strong> games · <strong>{total_decisions}</strong> decisions · <strong id="fb-count">0</strong> flagged</span>
  {match_score}
  <div class="actions">
    <button id="btn-jump-flagged" title="Jump to next flagged decision">↓ next flagged</button>
    <button id="btn-export-fb">Export feedback (JSONL)</button>
    <button id="btn-clear-fb">Clear feedback</button>
    <button id="btn-collapse-all">Collapse all</button>
    <button id="btn-expand-all">Expand all</button>
  </div>
</header>
<main>
{games_html}
</main>
<script>window.__REPLAY_ID = "{seed}";</script>
<script>{js}</script>
</body>
</html>
'''


# ─── Entry point ────────────────────────────────────────────────

def main(argv: List[str]) -> int:
    if len(argv) < 4:
        print("Usage: build_replay.py <log_file> <output_html> <seed>",
              file=sys.stderr)
        return 1
    log_path = argv[1]
    out_path = argv[2]
    seed = argv[3]
    with open(log_path) as f:
        text = f.read()
    fmt = sniff_format(text)
    if fmt == "ndjson":
        model = parse_ndjson(text)
        html = render_html(model, seed)
        with open(out_path, "w") as f:
            f.write(html)
        n_dec = sum(1 for g in model["games"]
                    for e in g["events"] if e.get("kind") == "DECISION")
        print(f"build_replay: ndjson -> {out_path}  "
              f"({len(model['games'])} games, {n_dec} decisions)")
        return 0

    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "build_replay_legacy.py")
    if os.path.exists(legacy):
        print("build_replay: text log detected -> delegating to "
              "build_replay_legacy.py")
        os.execv(sys.executable,
                 [sys.executable, legacy, log_path, out_path, seed])
    print("build_replay: text log detected but build_replay_legacy.py "
          "is missing.  Re-run --bo3 with --dump-replay to use the "
          "new viewer.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
