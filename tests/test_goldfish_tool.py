"""tools/goldfish.py: a deck against an opponent that does nothing.

The opponent's deck is one basic land: it plays lands and nothing else, so
the deck's kill turn measures its own speed. The summary counts kills,
losses by how (an empty library is "mill", CR 704.5b), kills by each turn
and the median kill turn.
"""
from __future__ import annotations

from tools import goldfish


def test_the_goldfish_deck_is_a_constructed_deck_of_one_basic_land(card_db):
    land = card_db.get_card(goldfish.GOLDFISH_LAND)
    assert land.is_land and "basic" in {s.value for s in land.supertypes}
    assert goldfish.DECK_SIZE == 60


def test_the_summary_counts_kills_losses_and_kills_by_each_turn():
    rows = [{"won": True, "turns": 4, "how": "damage"},
            {"won": True, "turns": 6, "how": "damage"},
            {"won": False, "turns": 9, "how": "mill"},
            {"won": True, "turns": 5, "how": "damage"}]
    s = goldfish.summarise(rows)
    assert s["games"] == 4 and s["kills"] == 3
    assert s["losses"] == {"mill": 1}
    assert s["kills_by_turn"] == {4: 1, 5: 2, 6: 3}
    assert s["median_kill_turn"] == 5


def test_a_run_with_no_kill_has_no_median():
    s = goldfish.summarise([{"won": False, "turns": 10, "how": "mill"}])
    assert s["kills"] == 0 and s["median_kill_turn"] is None
    assert s["kills_by_turn"] == {}


# ── The go-off turn ────────────────────────────────────────────────────
# A combo deck "goes off" the turn it resolves the payoff its gameplan
# names (the EXECUTE_PAYOFF goal's payoff role): a reanimation spell, a
# storm closer, a Titan. Real-world combo speed is reported as that turn,
# while the kill can come turns later (a reanimated creature still has to
# attack), so the goldfish reports both.

_LOG = [
    "T1 P1: Play Swamp",
    "T2 P1: Cast Faithful Mending (WU)",
    "T3 P2: Cast Payoff Spell (1B)",            # the other player's cast
    "T4 P1: Cast Payoff Spell (1B)",
    "T5 P1: Cast Payoff Spell (1B)",
]


def test_the_go_off_turn_is_the_first_turn_the_deck_casts_a_payoff():
    assert goldfish.go_off_turn(_LOG, {"Payoff Spell"}) == 4


def test_a_payoff_put_onto_the_battlefield_without_a_cast_goes_off_too():
    log = ["T2 P1: Play Forest",
           "T3: Payoff Creature moved library -> battlefield (search)"]
    assert goldfish.go_off_turn(log, {"Payoff Creature"}) == 3


def test_a_game_with_no_payoff_has_no_go_off_turn():
    assert goldfish.go_off_turn(_LOG, {"Something Else"}) is None
    assert goldfish.go_off_turn(_LOG, set()) is None


def test_the_summary_reports_the_median_go_off_turn():
    rows = [{"won": True, "turns": 7, "how": "damage", "go_off": 4},
            {"won": True, "turns": 6, "how": "damage", "go_off": 3},
            {"won": False, "turns": 9, "how": "mill", "go_off": None}]
    s = goldfish.summarise(rows)
    assert s["went_off"] == 2 and s["median_go_off_turn"] == 3.5


def test_a_combo_gameplan_names_its_payoffs():
    assert "Grapeshot" in goldfish.payoff_names("Ruby Storm")
    assert "Primeval Titan" in goldfish.payoff_names("Amulet Titan")
