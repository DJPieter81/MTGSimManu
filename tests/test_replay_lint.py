"""Replay-linter rules — small synthetic-event unit tests.

Each rule gets a minimal positive + negative event list.  The
historical anchor test runs R1 against the committed pre-E4 replay
dump and must keep flagging the real violation forever (the linter's
reason to exist).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.replay_lint import lint_events, lint_file


def _turn(game=1, turn=3, pidx=0, mine=None, opp=None):
    """boards[pidx] is the acting player's snapshot."""
    boards = [None, None]
    boards[pidx] = {"creatures": mine or [], "lands": [], "other": [],
                    "life": 20}
    boards[1 - pidx] = {"creatures": opp or [], "lands": [], "other": [],
                        "life": 20}
    return {"kind": "TURN_START", "game": game, "turn": turn,
            "pidx": pidx, "board": boards}


def _play(action, card, phase="Main1", pidx=0, game=1, turn=3, seq=10):
    return {"kind": "PLAY", "game": game, "turn": turn, "phase": phase,
            "pidx": pidx, "actor": f"P{pidx+1}", "action": action,
            "card": card, "targets": [], "seq": seq}


def _rules(findings):
    return [f.rule for f in findings]


# ─── R1 / R2: token & unknown-card casts ─────────────────────────────

def test_token_cast_flagged_as_error():
    ev = [_turn(), _play("cast_spell", "Construct Token")]
    f = lint_events(ev, db=None)
    assert _rules(f) == ["TOKEN_CAST"]
    assert f[0].level == "ERROR"


def test_unknown_card_cast_flagged():
    ev = [_turn(), _play("cast_spell", "Totally Fake Card")]
    assert _rules(lint_events(ev, db=None)) == ["UNKNOWN_CARD_PLAYED"]


def test_known_card_main_phase_cast_clean(card_db):
    ev = [_turn(), _play("cast_spell", "Memnite")]
    assert lint_events(ev, db=card_db) == []


# ─── R3: sorcery-speed timing ────────────────────────────────────────

def test_sorcery_speed_cast_in_upkeep_flagged(card_db):
    ev = [_turn(), _play("cast_spell", "Memnite", phase="Upkeep")]
    f = lint_events(ev, db=card_db)
    assert _rules(f) == ["NON_MAIN_SORCERY_CAST"]


def test_sorcery_speed_cast_off_turn_flagged(card_db):
    ev = [_turn(pidx=0), _play("cast_spell", "Memnite", pidx=1)]
    assert _rules(lint_events(ev, db=card_db)) == ["NON_MAIN_SORCERY_CAST"]


def test_instant_cast_off_turn_clean(card_db):
    ev = [_turn(pidx=0), _play("cast_spell", "Counterspell", pidx=1,
                               phase="Main1")]
    assert lint_events(ev, db=card_db) == []


# ─── R4: land drops ──────────────────────────────────────────────────

def test_second_land_drop_warned():
    ev = [_turn(),
          _play("play_land", "Island", seq=1),
          _play("play_land", "Island", seq=2)]
    f = lint_events(ev, db=None)
    assert _rules(f) == ["MULTIPLE_LAND_DROPS"]
    assert f[0].level == "WARN"


def test_single_land_drop_clean():
    ev = [_turn(), _play("play_land", "Island")]
    assert lint_events(ev, db=None) == []


# ─── R5: lethal available, no attack ─────────────────────────────────

def _cre(name, p, t, tapped=False, sick=False):
    return {"name": name, "p": p, "t": t, "tapped": tapped,
            "summoning_sick": sick}


def test_no_attack_with_untapped_lethal_and_no_blockers_warned():
    ev = [_turn(pidx=1, mine=[_cre("A", 12, 12), _cre("B", 9, 9)],
                opp=[]),
          {"kind": "COMBAT", "sub": "no_attack", "game": 1, "turn": 3,
           "pidx": 1, "actor": "P2", "seq": 30}]
    # opponent life 20 < 21 attackable
    f = lint_events(ev, db=None)
    assert _rules(f) == ["NO_ATTACK_WITH_LETHAL"]
    assert f[0].level == "WARN"


def test_summoning_sick_lethal_not_flagged():
    """The A1 lesson: fresh bodies don't count as attackable."""
    ev = [_turn(pidx=1,
                mine=[_cre("A", 12, 12, sick=True),
                      _cre("B", 9, 9, sick=True)],
                opp=[]),
          {"kind": "COMBAT", "sub": "no_attack", "game": 1, "turn": 3,
           "pidx": 1, "actor": "P2", "seq": 30}]
    assert lint_events(ev, db=None) == []


def test_untapped_blocker_suppresses_lethal_warn():
    ev = [_turn(pidx=1, mine=[_cre("A", 25, 25)],
                opp=[_cre("Wall", 0, 4)]),
          {"kind": "COMBAT", "sub": "no_attack", "game": 1, "turn": 3,
           "pidx": 1, "actor": "P2", "seq": 30}]
    assert lint_events(ev, db=None) == []


# ─── Historical anchor: the linter catches the real E4 event ─────────

_HIST = Path(__file__).resolve().parent.parent / "replays" / \
    "az_aff_60104.ndjson"


@pytest.mark.skipif(not _HIST.exists(),
                    reason="historical replay not present")
def test_linter_flags_the_real_e4_token_cast(card_db):
    findings = lint_file(str(_HIST), db=card_db)
    token = [f for f in findings if f.rule == "TOKEN_CAST"]
    assert token, "linter must flag the historical token cast (E4)"
    assert token[0].game == 2 and token[0].turn == 7
