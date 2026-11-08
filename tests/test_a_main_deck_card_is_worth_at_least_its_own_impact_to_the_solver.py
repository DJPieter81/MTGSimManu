"""A main-deck card is worth at least its own impact to the sideboard solver.

The oracle sideboard solver (`ai.sideboard_solver.plan_sideboard`) swaps a
sideboard card in while its value against the opponent beats the weakest
main card's. Main cards were valued only by opponent-facing clauses, so a
deck's own plan cards -- a combo deck's digs, a ritual, an engine -- scored
0 and were cut first against every opponent. Under `SB_SOLVER=new`
(`sbh-post`) Ruby Storm cut four Hex Magic and a Twinned Vision for
Thoughtseize and Fatal Push everywhere, and its row fell 15.8 -> 8.4.

A card in a deck is worth to that deck at least what the solver says it is
worth to take from that deck (`_card_impact`, the hand-attack clause's
measure): a sideboard card comes in only when its value in the matchup
beats that. Sideboard cards keep their matchup value: that is the reason
they would come in. Card names are fixture carriers.
"""
from __future__ import annotations


def _plan(card_db, me, opp):
    from ai.gameplan import get_gameplan
    from ai.sideboard_solver import plan_sideboard
    from decks.modern_meta import MODERN_DECKS
    a, b = MODERN_DECKS[me], MODERN_DECKS[opp]
    new_main, _sb, log = plan_sideboard(
        dict(a["mainboard"]), dict(a["sideboard"]), opp, card_db,
        opp_mainboard=dict(b["mainboard"]),
        opp_gameplan_loader=get_gameplan, my_deck_name=me)
    return a["mainboard"], new_main, log


def _out(main, new_main, name):
    return main.get(name, 0) - new_main.get(name, 0)


import pytest


@pytest.mark.parametrize("me,opp", [
    ("Ruby Storm", "Dimir Midrange"), ("Ruby Storm", "Affinity"),
    ("Ruby Storm", "Eldrazi Tron"), ("Goryo's Vengeance", "Boros Energy"),
])
def test_a_swap_only_replaces_a_card_worth_less_than_what_comes_in(
        card_db, me, opp):
    from ai.sideboard_solver import _card_impact, sb_value
    from decks.modern_meta import MODERN_DECKS
    _main, _new, log = _plan(card_db, me, opp)
    opp_t = [card_db.get_card(n) for n, c in
             MODERN_DECKS[opp]["mainboard"].items()
             for _ in range(c) if card_db.get_card(n)]
    for line in log:
        out_name = line.split("-", 1)[1].split(" (v=")[0]
        in_name = line.split("+", 1)[1].split(" (v=")[0]
        out_t, in_t = card_db.get_card(out_name), card_db.get_card(in_name)
        assert sb_value(in_t, opp_t) > max(sb_value(out_t, opp_t),
                                           _card_impact(out_t)), line


def test_storm_keeps_its_digs_against_a_creature_deck(card_db):
    main, new, _ = _plan(card_db, "Ruby Storm", "Affinity")
    assert _out(main, new, "Hex Magic") == 0


def test_storm_still_brings_in_hand_attacks_against_control(card_db):
    _main, new, _ = _plan(card_db, "Ruby Storm", "Azorius Control")
    assert new.get("Thoughtseize", 0) > 0
