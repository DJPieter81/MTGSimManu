"""Rollout evidence for a golden decision: play both choices forward.

For a `main`-kind label of `tests/eval/decisions/labels.json`, each seed
runs one game twice with the same seed:
1. The runner sets up the label's decks (libraries from the registered
   lists, shuffled by the seed). After the mulligans, a start hook lays the
   position out on player 0's turn (`run_game(start_hook=...)`).
2. Player 0's first main-phase decision is forced: once to the labelled
   play, once to the label's `alternative`. From then on both sides play
   as the AI plays.

The outcome is the number of turns until player 0 wins; a loss or draw
counts as the turn cap. The two runs share every seed, so the comparison
is paired. `p_better` is the share of seeded bootstrap resamples of the
paired differences in which the labelled play wins sooner on average (a
tie counts half). It is written to the label's `evidence.rollout`, which
`tools/decision_eval.py` reads.

    python tools/decision_rollout.py reanimate_with_target_in_graveyard -n 20
    python tools/decision_rollout.py --all -n 20 --write
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import random
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BOOTSTRAP_RESAMPLES = 2000   # resamples of the paired differences
BOOTSTRAP_SEED = 0           # the bootstrap is deterministic
SEED0 = 70000                # the goldfish/matrix seed convention


def _deck_list(name: str):
    from decks.modern_meta import MODERN_DECKS
    if name in MODERN_DECKS:
        d = MODERN_DECKS[name]
        return dict(d["mainboard"]), dict(d.get("sideboard") or {})
    from tools.goldfish import DECK_SIZE, GOLDFISH_LAND, GOLDFISH_NAME
    assert name == GOLDFISH_NAME, name
    return {GOLDFISH_LAND: DECK_SIZE}, {}


def _forced(play: Dict[str, Any], game, ai, real):
    """The decision a forced play makes: cast the named card, or the AI's
    own choice with the named card excluded."""
    from ai.playable_cards import playable_cards
    if "cast" in play:
        card = next(c for c in playable_cards(game, ai.player_idx)
                    if c.name == play["cast"])
        return ("cast_spell", card, ai._choose_targets(game, card))
    if "not_cast" in play:
        banned = {c.instance_id for c in playable_cards(game, ai.player_idx)
                  if c.name == play["not_cast"]}
        return real(game, excluded_cards=banned)
    raise ValueError(f"unsupported forced play {play!r}")


def run_one(label: Dict[str, Any], card_db, seed: int,
            play: Dict[str, Any]) -> int:
    from engine.game_runner import GameRunner
    from engine.game_state import Phase
    from tests.eval.decisions import positions as P
    kind, builder = P.POSITIONS[label["id"]]
    if kind != "main":
        raise ValueError(f"rollout supports main-phase labels, not {kind}")
    probe = builder(card_db)[0]
    deck, opp = probe.players[0].deck_name, probe.players[1].deck_name
    main1, side1 = _deck_list(deck)
    main2, side2 = _deck_list(opp)
    done = {"forced": False}

    def hook(game, ais):
        game.active_player = 0
        ai = ais[0]
        real = ai.decide_main_phase

        def first(g, *a, **kw):
            if not done["forced"] and g.current_phase is Phase.MAIN1 \
                    and g.active_player == 0:
                # Lay the position out at the decision itself, so its
                # this-turn state (storm count, flashback grants, the land
                # drop) is the state the forced play is made in.
                done["forced"] = True
                P._INTO.append(g)
                try:
                    builder(card_db)
                finally:
                    P._INTO.pop()
                return _forced(play, g, ai, real)
            return real(g, *a, **kw)
        ai.decide_main_phase = first

    runner = GameRunner(card_db)
    random.seed(seed)
    runner.rng.seed(seed)
    with contextlib.redirect_stdout(io.StringIO()):
        res = runner.run_game(deck, main1, opp, main2,
                              deck1_sideboard=side1, deck2_sideboard=side2,
                              start_hook=hook)
    cap = runner_cap(res)
    return res.turns if res.winner == 0 else cap


def runner_cap(res) -> int:
    from engine.game_state import GameState
    return GameState(rng=random.Random(0)).max_turns + 1


def p_better(labelled: List[int], alternative: List[int]) -> float:
    """Share of bootstrap resamples of the paired differences in which the
    labelled play wins sooner on average; a tie counts half."""
    diffs = [a - b for a, b in zip(labelled, alternative)]
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(diffs)
    score = 0.0
    for _ in range(BOOTSTRAP_RESAMPLES):
        mean = sum(diffs[rng.randrange(n)] for _ in range(n)) / n
        score += 1.0 if mean < 0 else (0.5 if mean == 0 else 0.0)
    return score / BOOTSTRAP_RESAMPLES


def evidence(label: Dict[str, Any], card_db, n: int) -> Dict[str, Any]:
    seeds = [SEED0 + i for i in range(n)]
    lab = [run_one(label, card_db, s, label["expect"]) for s in seeds]
    alt = [run_one(label, card_db, s, label["alternative"]) for s in seeds]
    return {"p_better": round(p_better(lab, alt), 3), "n": n,
            "metric": "turns until player 0 wins (loss = turn cap)",
            "labelled_mean": round(sum(lab) / n, 2),
            "alternative_mean": round(sum(alt) / n, 2)}


def main(argv=None) -> int:
    os.environ.setdefault("MTG_LLM_DECISION_SCORER_OFFLINE", "1")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("label", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--write", action="store_true",
                    help="record the evidence in labels.json")
    a = ap.parse_args(argv)
    from tools.decision_eval import LABELS, load_labels
    with contextlib.redirect_stdout(io.StringIO()):
        from engine.card_database import CardDatabase
        db = CardDatabase()
    data = load_labels()
    todo = [lab for lab in data["labels"] if "alternative" in lab
            and (a.all or lab["id"] == a.label)]
    for lab in todo:
        ev = evidence(lab, db, a.n)
        print(json.dumps({lab["id"]: ev}))
        if a.write:
            lab["evidence"]["rollout"] = ev
    if a.write:
        with open(LABELS, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
