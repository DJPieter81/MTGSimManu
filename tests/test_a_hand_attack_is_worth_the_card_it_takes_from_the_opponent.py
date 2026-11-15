"""A hand attack is worth the card it takes from the opponent.

The oracle sideboard solver (`ai.sideboard_solver.sb_value`) values a card
against an opponent's deck composition, one clause per card class. It had
no clause for a targeted hand attack (the typed `hand_attack_data`: "target
player reveals their hand, you choose a nonland card from it", "... discards
two cards", "... at random"), so Thoughtseize scored 0 against every deck
and never came in: Ruby Storm sideboarded nothing against Dimir Midrange,
its 0-of-8 game-1 matchup (the Storm diagnostic's fifth lead).

The value is the card the attack takes, from a seven-card hand drawn from
the opponent's deck (CR 103.4):
- what it may take is the engine's own reading of the choose clause
  (`oracle_resolver._targeted_discard_candidates`);
- a taken card is worth what the solver's other clauses say a card is: a
  creature its threat (`_avg_creature_threat`), any other spell its mana
  value over the residency window (the counterspell clause's own measure);
- the chooser decides which card: the caster takes the best (the expected
  maximum, hypergeometric), a random discard the mean, the victim the worst.
Card names are fixture carriers.
"""
from __future__ import annotations

import pytest


def _templates(card_db, deck):
    from decks.modern_meta import MODERN_DECKS
    out = []
    for name, n in MODERN_DECKS[deck]["mainboard"].items():
        t = card_db.get_card(name)
        if t is not None:
            out += [t] * n
    return out


def _value(card_db, card, deck):
    from ai.sideboard_solver import sb_value
    return sb_value(card_db.get_card(card), _templates(card_db, deck))


def test_a_hand_attack_has_value_against_a_deck_of_spells(card_db):
    assert _value(card_db, "Thoughtseize", "Dimir Midrange") > 0


def test_the_caster_choosing_takes_more_than_a_random_or_victim_discard(
        card_db):
    from ai.sideboard_solver import _clause_hand_disruption
    opp = _templates(card_db, "Dimir Midrange")
    caster = card_db.get_card("Thoughtseize")
    attack = dict(caster.hand_attack_data)
    by = {}
    for chooser in ("caster", "random", "victim"):
        attack.update(chooser=chooser, choose_clause=None)
        by[chooser] = _clause_hand_disruption(attack, opp)
    assert by["caster"] >= by["random"] >= by["victim"]
    assert by["caster"] > by["victim"]


def test_a_narrower_choose_clause_takes_less(card_db):
    """Inquisition (mana value 3 or less) against a deck with bigger
    spells is worth less than Thoughtseize (any nonland card)."""
    assert (_value(card_db, "Inquisition of Kozilek", "Eldrazi Tron")
            < _value(card_db, "Thoughtseize", "Eldrazi Tron"))


def test_a_card_with_no_hand_attack_gets_no_such_value(card_db):
    from ai.sideboard_solver import _clause_hand_disruption
    assert _clause_hand_disruption(None, _templates(card_db,
                                                    "Dimir Midrange")) == 0


@pytest.mark.parametrize("opp", ["Dimir Midrange", "Azorius Control"])
def test_storm_brings_its_hand_attacks_in_against_spell_decks(card_db, opp):
    from ai.sideboard_solver import plan_sideboard
    from ai.gameplan import get_gameplan as load_gameplan
    from decks.modern_meta import MODERN_DECKS
    me, them = MODERN_DECKS["Ruby Storm"], MODERN_DECKS[opp]
    new_main, _sb, _log = plan_sideboard(
        dict(me["mainboard"]), dict(me["sideboard"]), opp, card_db,
        opp_mainboard=dict(them["mainboard"]),
        opp_gameplan_loader=load_gameplan, my_deck_name="Ruby Storm")
    assert new_main.get("Thoughtseize", 0) > 0
    protected = set(load_gameplan("Ruby Storm").critical_pieces or ())
    for name in protected:
        assert new_main.get(name, 0) >= me["mainboard"].get(name, 0)
