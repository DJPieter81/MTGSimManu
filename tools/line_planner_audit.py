"""How often does the main phase's choice agree with the line planner?

Plays goldfish games (`tools/goldfish.run`) and, at every main-phase
decision of the goldfished deck where the line planner
(`ai.line_planner.best_line`) has a line completing THIS turn, records
whether the play the AI chose is that line's first play. The planner is
read-only: nothing here changes a decision. The disagreement rate is the
measurement that licenses hooking the planner into a decision family
(docs/proposals/2026-11-08_card_interaction_graph_and_line_planner.md).

    python tools/line_planner_audit.py "Grixis Reanimator" -n 20 -s 70000
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def audit(deck: str, n: int, seed0: int, side: str = "play") -> dict:
    os.environ.setdefault("MTG_LLM_DECISION_SCORER_OFFLINE", "1")
    from ai.ev_player import EVPlayer
    from ai.line_planner import THIS_TURN, best_line
    import tools.goldfish as goldfish

    seen = collections.Counter()
    examples = []
    real = EVPlayer.decide_main_phase

    def spy(self, game, *a, **kw):
        line = None
        if getattr(game.players[self.player_idx], "deck_name", "") == deck:
            line = best_line(game, self.player_idx)
        chosen = real(self, game, *a, **kw)
        if line is not None and line.completes == THIS_TURN:
            card = chosen[1] if chosen else None
            agree = card is line.first
            seen["agree" if agree else "disagree"] += 1
            if not agree and len(examples) < 10:
                examples.append({
                    "turn": game.display_turn,
                    "line": [c.name for c in line.plays],
                    "payoff": line.payoff.name,
                    "chosen": (f"{chosen[0]}: {card.name}" if chosen
                               else "pass")})
        return chosen

    EVPlayer.decide_main_phase = spy
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            goldfish.run(deck, n, seed0, side)
    finally:
        EVPlayer.decide_main_phase = real
    total = seen["agree"] + seen["disagree"]
    return {"deck": deck, "decisions_with_a_line": total,
            "agree": seen["agree"], "disagree": seen["disagree"],
            "examples": examples}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("deck")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("-s", "--seed", type=int, default=70000)
    ap.add_argument("--side", choices=("play", "draw"), default="play")
    a = ap.parse_args(argv)
    import json
    print(json.dumps(audit(a.deck, a.n, a.seed, a.side), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
