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
