"""The card graph holds each relation its owner decides, and only those.

`ai.card_graph` indexes the relations between a deck's cards. Each edge is
decided by the predicate that owns the relation, so the graph re-implements
none of them:
- a library search admits the card (TUTORS), with an X bound read at the
  deck's largest mana value (CR 107.3);
- a returner's graveyard requirement admits the creature (RETURNS);
- a spell that brings a card from outside the game reaches the sideboard,
  and only the sideboard (FETCHES_OUTSIDE, CR 400.11a);
- a permanent's cost-reduction rules name the spell (REDUCES_COST, CR
  601.2f);
- a graveyard-wide flashback grant reaches instants and sorceries
  (GRANTS_GRAVEYARD_CAST, CR 702.34a).
Card names are fixture carriers.
"""
from __future__ import annotations

import pytest

from ai.card_graph import Relation, build

R = Relation


@pytest.fixture
def t(card_db):
    def get(name):
        tmpl = card_db.get_card(name)
        assert tmpl is not None, name
        return tmpl
    return get


def _has(graph, src, rel, tgt) -> bool:
    return any(e.source is src and e.relation is rel and e.target is tgt
               for e in graph.edges)


def test_a_search_reaches_the_cards_its_description_admits(t):
    pact, gsz, titan, forest, bolt = (
        t("Summoner's Pact"), t("Green Sun's Zenith"), t("Primeval Titan"),
        t("Forest"), t("Lightning Bolt"))
    g = build([pact, gsz, titan, forest, bolt])
    assert _has(g, pact, R.TUTORS, titan)          # a green creature card
    assert _has(g, gsz, R.TUTORS, titan)           # X chosen as needed
    assert not _has(g, pact, R.TUTORS, bolt)
    assert not _has(g, pact, R.TUTORS, forest)


def test_a_returner_reaches_the_creatures_its_requirement_admits(t):
    persist, archon, griselbrand = (
        t("Persist"), t("Archon of Cruelty"), t("Griselbrand"))
    g = build([persist, archon, griselbrand])
    assert _has(g, persist, R.RETURNS, archon)
    assert not _has(g, persist, R.RETURNS, griselbrand)   # nonlegendary only


def test_a_card_from_outside_the_game_is_a_sideboard_card(t):
    wish, shot, ritual = t("Wish"), t("Grapeshot"), t("Desperate Ritual")
    g = build([wish, ritual], [shot])
    assert _has(g, wish, R.FETCHES_OUTSIDE, shot)
    assert not _has(g, wish, R.FETCHES_OUTSIDE, ritual)


def test_a_reducer_reaches_the_spells_its_rules_name(t):
    medallion, ritual, opt, island = (
        t("Ruby Medallion"), t("Desperate Ritual"), t("Opt"), t("Island"))
    g = build([medallion, ritual, opt, island])
    assert _has(g, medallion, R.REDUCES_COST, ritual)     # a red spell
    assert not _has(g, medallion, R.REDUCES_COST, opt)    # a blue one
    assert not _has(g, medallion, R.REDUCES_COST, island)


def test_a_graveyard_flashback_grant_reaches_instants_and_sorceries(t):
    pif, ritual, titan = (t("Past in Flames"), t("Desperate Ritual"),
                          t("Primeval Titan"))
    g = build([pif, ritual, titan])
    assert _has(g, pif, R.GRANTS_GRAVEYARD_CAST, ritual)
    assert not _has(g, pif, R.GRANTS_GRAVEYARD_CAST, titan)


def test_the_graph_is_the_same_each_time_it_is_built(card_db):
    from ai.card_graph import for_deck
    from decks.modern_meta import MODERN_DECKS
    deck = MODERN_DECKS["Ruby Storm"]
    a, b = for_deck(deck, card_db), for_deck(deck, card_db)
    assert [(e.source.name, e.relation, e.target.name) for e in a.edges] \
        == [(e.source.name, e.relation, e.target.name) for e in b.edges]
    assert a.counts()[R.GRANTS_GRAVEYARD_CAST] > 0
