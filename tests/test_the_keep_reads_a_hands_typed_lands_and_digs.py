"""The keep reads a hand's lands and digs from their typed facts (AI, from
CR 712 and CR 121.1).

A keep decision asks what the hand can do. Two of its reads were tags and
oracle substrings, and both disagreed with the cards:
- **A land's colours.** A modal double-faced card played as a land is its
  land face (CR 712), so its colours are that face's: Valakut Awakening's
  Valakut Stoneforge taps for {R}. A fetchland supplies the colours its
  typed search names (`template.fetchland.colors`).
- **A dig.** A spell that draws for its caster (`spell_draws`: "draw that
  many cards") or exposes cards to play (`has_draw_effect`) finds the
  next piece. The storm keep's "ritual + dig + finisher" backup read a
  `cantrip`/`draw` tag that Hex Magic lacks.

The defect: Ruby Storm mulliganed 42% of its games (24-game audit).
- 26% of random 7s failed "no cost reducer and no ritual+cantrip+finisher
  backup". For example: two lands and Valakut Awakening, two rituals,
  Hex Magic and Wish.
- Some hands whose only red source was Valakut Awakening failed "lands
  miss colors ['R']".

Card names are fixture carriers: every modal double-faced card with a land
face, and every spell that draws.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance


def _hand(card_db, names):
    return [CardInstance(template=card_db.get_card(n), owner=0, controller=0,
                         instance_id=i + 1, zone="hand")
            for i, n in enumerate(names)]


def _decider(deck="Ruby Storm"):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name=deck,
                    rng=random.Random(0))._mulligan_decider


# ── A land's colours ──────────────────────────────────────────────────

def test_a_modal_double_faced_cards_land_face_supplies_its_colours(card_db):
    from ai.mulligan import MulliganDecider
    (awakening,) = _hand(card_db, ["Valakut Awakening // Valakut Stoneforge"])
    assert MulliganDecider._land_supplies_colors(awakening) == {"R"}


def test_a_fetchland_supplies_the_colours_its_search_names(card_db):
    from ai.mulligan import MulliganDecider
    mesa, mire = _hand(card_db, ["Arid Mesa", "Bloodstained Mire"])
    assert MulliganDecider._land_supplies_colors(mesa) == {"R", "W"}
    assert MulliganDecider._land_supplies_colors(mire) == {"B", "R"}


def test_a_land_face_meets_a_combo_sets_colour(card_db):
    """The only red source is the land face; the red combo pieces are
    covered."""
    from ai.mulligan import MulliganDecider
    from ai.predicates import staying_lands
    hand = _hand(card_db, ["Valakut Awakening // Valakut Stoneforge",
                           "Pyretic Ritual", "Desperate Ritual", "Grapeshot"])
    lands = staying_lands(hand)
    gap = MulliganDecider._combo_set_color_gap(
        hand, lands, [{"Pyretic Ritual", "Desperate Ritual", "Grapeshot"}])
    assert gap == set()


# ── A dig ─────────────────────────────────────────────────────────────

def test_a_spell_that_draws_is_a_dig_for_the_storm_keep(card_db):
    hand = _hand(card_db, ["Arid Mesa", "Mountain",
                           "Valakut Awakening // Valakut Stoneforge",
                           "Desperate Ritual", "Pyretic Ritual", "Hex Magic",
                           "Wish"])
    decider = _decider()
    assert decider.decide(hand, 7) is True, decider.last_reason


def test_a_storm_hand_with_no_dig_is_still_mulliganed(card_db):
    """Rituals and finisher access with nothing to find the rest: the
    backup rule still holds."""
    hand = _hand(card_db, ["Arid Mesa", "Mountain", "Scalding Tarn",
                           "Desperate Ritual", "Pyretic Ritual", "Wish",
                           "Empty the Warrens"])
    decider = _decider()
    assert decider.decide(hand, 7) is False
    assert "backup" in decider.last_reason
