"""A sideboard counterspell is chosen by what it counters, not by a deck name.

The default sideboarder (`engine/sideboard_manager.sideboard`) brought
counterspells in by matching the opponent's deck NAME: "storm", "living
end", "goryo", "titan" for "counterspells vs combo", "energy", "zoo",
"prowess", "affinity" for "vs aggro". Against red Ruby Storm that boarded
Mystical Dispute -- a three-mana soft counter against a deck of one- and
two-mana spells -- and Storm went 0/12 in game 2 against Domain Zoo (game 1
5/12; the Storm diagnostic).

A counterspell answers a spell efficiently when it can target it
(`counter_target_kind`) at an effective cost no greater than that spell's
mana value: its printed cost, less a cost reduction whose condition the
spell meets (`target_cost_reduction`, Dispute against a blue spell), or
nothing when it has a free alternative cost (`alternate_exile_color`, Force
of Negation). It comes in when most of the opponent's nonland spells are
spells it answers efficiently. Card and deck names are fixture carriers.
"""
from __future__ import annotations


def _deck(card_db, names):
    out = []
    for n, c in names.items():
        t = card_db.get_card(n)
        assert t is not None, n
        out += [t] * c
    return out


def test_dispute_answers_blue_spells_not_a_red_spell_deck(card_db):
    from engine.sideboard_manager import counter_answers_most
    dispute = card_db.get_card("Mystical Dispute")
    blue = _deck(card_db, {"Counterspell": 8, "Opt": 8,
                           "Island": 20})
    red = _deck(card_db, {"Desperate Ritual": 8, "Manamorphose": 8,
                          "Mountain": 20})
    assert counter_answers_most(dispute, blue)
    assert not counter_answers_most(dispute, red)


def test_a_cheap_counter_answers_a_deck_of_cheap_instants(card_db):
    from engine.sideboard_manager import counter_answers_most
    red = _deck(card_db, {"Desperate Ritual": 8, "Manamorphose": 8,
                          "Mountain": 20})
    assert counter_answers_most(card_db.get_card("Spell Pierce"), red)


def test_a_free_counter_answers_noncreature_spells(card_db):
    from engine.sideboard_manager import counter_answers_most
    red = _deck(card_db, {"Desperate Ritual": 8, "Manamorphose": 8,
                          "Mountain": 20})
    assert counter_answers_most(card_db.get_card("Force of Negation"), red)


def test_a_noncreature_counter_does_not_answer_a_creature_deck(card_db):
    from engine.sideboard_manager import counter_answers_most
    creatures = _deck(card_db, {"Grizzly Bears": 16, "Forest": 20})
    assert not counter_answers_most(card_db.get_card("Spell Pierce"),
                                    creatures)


def test_zoo_no_longer_boards_dispute_against_red_storm(card_db):
    from decks.modern_meta import MODERN_DECKS
    from engine.sideboard_manager import sideboard
    zoo = MODERN_DECKS["Domain Zoo"]
    new_main, _ = sideboard(dict(zoo["mainboard"]), dict(zoo["sideboard"]),
                            "Domain Zoo", "Ruby Storm")
    assert new_main.get("Mystical Dispute", 0) == \
        zoo["mainboard"].get("Mystical Dispute", 0)


def test_dispute_answers_a_blue_control_mainboard(card_db):
    from engine.sideboard_manager import counter_answers_most, _opp_templates
    assert counter_answers_most(card_db.get_card("Mystical Dispute"),
                                _opp_templates("Azorius Control"))
