"""The golden decision eval: does the AI make the labelled play?

Each label in `tests/eval/decisions/labels.json` names a position
(`tests/eval/decisions/positions.py`), the decision family, the expected
play, the rule it follows and the replay it came from, and the evidence
behind it. A label's confidence is computed from that evidence:

- rules-forced: the alternative is illegal or strictly dominated (the
  same effect for more cost, or a play that does nothing). Confidence 1.0.
- rollout: the labelled play and its alternative were each played forward
  from the position under seeded continuations; `p_better` is the share
  of bootstrap resamples in which the labelled play's outcome is better.
- review: independent reviewers each tried to refute the label;
  confidence is the non-refuting reviewers over reviewers plus one.

Without a rules-forced verdict, independent legs that agree combine: the
label is wrong only if every leg is (1 - product of (1 - leg)). A leg below
even odds is evidence against the label: the confidence is then the
weakest leg, and the label goes back to review. With none, it
is unverified and scores the label file's `unverified_confidence`. The
gate (`tests/test_golden_decisions.py`) requires every label at or above
the file's `gate_confidence` to pass. The rest are reported.

    python tools/decision_eval.py            # table of every label
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LABELS = os.path.join(ROOT, "tests", "eval", "decisions", "labels.json")


def load_labels(path: str = LABELS) -> Dict[str, Any]:
    with open(path) as f:
        data = json.load(f)
    for lab in data["labels"]:
        for key in ("id", "family", "expect", "rule", "source",
                    "evidence"):
            if key not in lab:
                raise ValueError(f"label {lab.get('id')!r} lacks {key!r}")
    return data


CONTRADICTION = 0.5   # a leg below even odds is evidence against the label


def confidence(label: Dict[str, Any], unverified: float) -> float:
    ev = label["evidence"]
    if ev.get("rules_forced"):
        return 1.0
    legs: List[float] = []
    roll = ev.get("rollout")
    if roll:
        legs.append(float(roll["p_better"]))
    rev = ev.get("review")
    if rev and rev.get("reviewers"):
        # Reviewers who failed to refute it, out of reviewers plus one: a
        # unanimous small panel is evidence, not certainty (2 of 2 -> 2/3),
        # so a judgement label needs rollout evidence too to reach the gate.
        legs.append((rev["reviewers"] - rev["refuted"])
                    / (rev["reviewers"] + 1))
    if not legs:
        return unverified
    if min(legs) < CONTRADICTION:
        # Evidence against the label: the weakest leg, and a re-review.
        return min(legs)
    # Independent legs agreeing: the label is wrong only if every leg is.
    wrong = 1.0
    for leg in legs:
        wrong *= 1.0 - leg
    return 1.0 - wrong


def observe(kind: str, built: Any) -> Any:
    """The decision the AI makes at the position, in a judgeable form."""
    if kind == "main":
        game, ai = built
        chosen = ai.decide_main_phase(game)
        return None if not chosen else (chosen[0], chosen[1].name)
    if kind == "targets":
        game, ai, spell = built
        from engine.constants import PLAYER_TARGET_SELF
        out = []
        for tid in ai._choose_targets(game, spell) or []:
            if tid == PLAYER_TARGET_SELF:
                out.append("self")
                continue
            card = game.get_card_by_id(tid)
            out.append(card.name if card is not None else "opponent")
        return out
    if kind == "land_search":
        game, lands = built
        from ai.activation_ev import _choose_land_delivery
        picked, pool = [], list(lands)
        for _ in range(2):
            land = _choose_land_delivery(game, 0, pool, batch=tuple(picked),
                                         enters_tapped=True)
            picked.append(land)
            pool = [c for c in pool if c is not land]
        return [c.name for c in picked]
    if kind == "sideboard":
        main, side, me, opp = built
        from engine.sideboard_manager import sideboard
        new_main, _ = sideboard(dict(main), dict(side), me, opp)
        return {"before": main, "after": new_main}
    raise ValueError(f"unknown decision kind {kind!r}")


def judge(expect: Dict[str, Any], seen: Any) -> bool:
    if "cast" in expect:
        return seen == ("cast_spell", expect["cast"])
    if "not_cast" in expect:
        return seen != ("cast_spell", expect["not_cast"])
    if "targets_equal" in expect:
        return seen == expect["targets_equal"]
    if "targets_exclude" in expect:
        return expect["targets_exclude"] not in (seen or [])
    if "includes" in expect:
        return expect["includes"] in (seen or [])
    if "not_added" in expect:
        card = expect["not_added"]
        return seen["after"].get(card, 0) <= seen["before"].get(card, 0)
    raise ValueError(f"unknown expectation {expect!r}")


def evaluate(card_db, data: Optional[Dict[str, Any]] = None
             ) -> List[Dict[str, Any]]:
    from tests.eval.decisions.positions import POSITIONS
    data = data or load_labels()
    rows = []
    for lab in data["labels"]:
        kind, builder = POSITIONS[lab["id"]]
        seen = observe(kind, builder(card_db))
        rows.append({"id": lab["id"], "family": lab["family"],
                     "confidence": confidence(
                         lab, data["unverified_confidence"]),
                     "passed": judge(lab["expect"], seen), "seen": seen})
    return rows


def main() -> int:
    os.environ.setdefault("MTG_LLM_DECISION_SCORER_OFFLINE", "1")
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        from engine.card_database import CardDatabase
        db = CardDatabase()
    data = load_labels()
    rows = evaluate(db, data)
    for r in rows:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['confidence']:.2f}  {r['family']:<14} {r['id']}")
    by_family: Dict[str, List[bool]] = {}
    for r in rows:
        by_family.setdefault(r["family"], []).append(r["passed"])
    for fam, res in sorted(by_family.items()):
        print(f"  {fam:<14} {sum(res)}/{len(res)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
