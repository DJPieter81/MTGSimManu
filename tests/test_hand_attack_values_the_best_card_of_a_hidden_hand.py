"""A caster-chosen hand attack ("target player reveals their hand. You
choose a nonland card from it. That player discards that card") is worth
the best card it can take out of a hidden hand — not a card-neutral trade.

# Mechanic the tests name

The cast-time projection of a forced discard decrements the opponent's
hand by one card and charges the caster's own card, so a caster-chosen
strip nets to zero card advantage minus its life cost, and the AI holds
it for turns while the opponent deploys the very threats it would have
taken (Goryo's Vengeance vs Domain Zoo s50000: Thoughtseize scored -0.1
on turns 1–5 with black open, cast on turn 6 at five life).  But the
caster does not take an average card — they take the best eligible card
in the hand.  Under hidden information the value of that choice is
derived from the opponent's observable pool (hand ∪ library, the same
public-decklist premise ai.bhi rests on): the k-th ranked eligible card
is the strip when no higher-ranked eligible card sits in the hand, an
exact hypergeometric over a hand of size H drawn from N pool cards.
The ranking is the one the resolution itself uses
(``score_card_for_opponent_strip``), so the cast-time expectation and the
resolved choice agree; the value denied is the removal-priority value for
a creature and the average card's clock impact otherwise.  The overlay
retracts the projection's average-card credit, so the spell's net card
credit is exactly the expected value of the best card it takes.

Class: every caster-chosen forced discard (Thoughtseize / Inquisition /
Duress / Despise / Distress … shapes) in every deck.  The victim-chosen
and random forms are classified but keep the projection's average-card
credit — the victim gives up their worst card.

Card names below are fixture carriers only.
"""
from __future__ import annotations

import random
from itertools import combinations

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_hand_attack
from ai.clock import card_clock_impact
from ai.ev_evaluator import (creature_threat_value,
                             snapshot_from_game)
from ai.hand_denial import hand_denial_value, strip_rank_probabilities


THOUGHTSEIZE = ("Target player reveals their hand. You choose a nonland "
                "card from it. That player discards that card. You lose 2 life.")
INQUISITION = ("Target player reveals their hand. You choose a nonland "
               "card from it with mana value 3 or less. That player "
               "discards that card.")
DURESS = ("Target opponent reveals their hand. You choose a noncreature, "
          "nonland card from it. That player discards that card.")


def _put(game, card_db, name, controller, zone):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        game.players[controller].battlefield.append(c)
    elif zone == "library":
        game.players[controller].library.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    _put(game, card_db, "Swamp", 0, "battlefield")
    return game


# ── Parse ────────────────────────────────────────────────────────────


def test_caster_chosen_hand_attack_parses_chooser_target_and_restriction():
    d = parse_hand_attack(THOUGHTSEIZE)
    assert d["chooser"] == "caster" and d["target"] == "player"
    assert "nonland" in d["choose_clause"]
    d = parse_hand_attack(INQUISITION)
    assert d["chooser"] == "caster"
    assert "mana value 3 or less" in d["choose_clause"]
    d = parse_hand_attack(DURESS)
    assert d["chooser"] == "caster" and d["target"] == "opponent"
    assert "noncreature" in d["choose_clause"]


def test_victim_chosen_and_random_discards_are_classified_not_caster_chosen():
    d = parse_hand_attack("Target player discards two cards.")
    assert d["chooser"] == "victim" and d["count"] == 2
    d = parse_hand_attack("Target opponent discards a card at random.")
    assert d["chooser"] == "random" and d["count"] == 1


def test_own_hand_discard_and_symmetric_discard_are_not_hand_attacks():
    assert parse_hand_attack("Draw a card, then discard a card.") is None
    assert parse_hand_attack("Each player discards their hand.") is None
    assert parse_hand_attack("") is None


def test_caster_chosen_hand_attack_is_immediate_interaction_not_deferrable(card_db):
    """The deferral gate must see a hand attack as same-turn value: the
    card it takes is gone before the opponent untaps.  The generic
    discard predicate read "discards a card" but not "discards THAT
    card", so the whole class carried no signal and was never cast."""
    from engine.oracle_parser import parse_has_discard_effect
    from ai.ev_evaluator import _is_immediate_interaction
    assert parse_has_discard_effect(THOUGHTSEIZE)
    for name in ("Thoughtseize", "Inquisition of Kozilek", "Duress"):
        t = card_db.get_card(name)
        assert _is_immediate_interaction(
            (t.oracle_text or "").lower(), t.tags, t), name


# ── Order statistic ──────────────────────────────────────────────────


def test_strip_rank_probabilities_are_the_exact_hypergeometric_order_statistic():
    n_pool, hand_size, n_eligible = 7, 3, 4
    probs = strip_rank_probabilities(n_pool, hand_size, n_eligible)
    # Brute force: eligible cards are pool indices 0..n_eligible-1 in
    # rank order; the strip is the lowest eligible index in the hand.
    hands = list(combinations(range(n_pool), hand_size))
    for k in range(n_eligible):
        hit = sum(1 for h in hands
                  if k in h and not any(j in h for j in range(k)))
        assert probs[k] == pytest.approx(hit / len(hands))
    # Everything not covered is "no eligible card in hand".
    none = sum(1 for h in hands if not any(j in h for j in range(n_eligible)))
    assert sum(probs) == pytest.approx(1 - none / len(hands))


def test_strip_rank_probabilities_vanish_without_a_hand_or_eligible_cards():
    assert strip_rank_probabilities(10, 0, 3) == [0.0, 0.0, 0.0]
    assert strip_rank_probabilities(10, 4, 0) == []


# ── Value ────────────────────────────────────────────────────────────


def test_strip_value_is_the_best_creature_the_hidden_hand_can_hold(card_db):
    game = _game(card_db)
    scion = _put(game, card_db, "Scion of Draco", 1, "hand")
    for _ in range(2):
        _put(game, card_db, "Plains", 1, "hand")
    for _ in range(10):
        _put(game, card_db, "Plains", 1, "library")
    snap = snapshot_from_game(game, 0)
    tmpl = card_db.get_card("Thoughtseize")
    value = hand_denial_value(tmpl, game, 0, snap)
    # One eligible card in a 13-card pool, hand of 3: P(in hand) = 3/13.
    p_in_hand = 3 / 13
    expected = (p_in_hand * creature_threat_value(scion, snap)
                - card_clock_impact(snap))
    assert value == pytest.approx(expected)
    assert value > 0


def test_hand_attack_against_an_empty_hand_is_worth_nothing(card_db):
    game = _game(card_db)
    for _ in range(10):
        _put(game, card_db, "Scion of Draco", 1, "library")
    snap = snapshot_from_game(game, 0)
    tmpl = card_db.get_card("Thoughtseize")
    assert hand_denial_value(tmpl, game, 0, snap) == 0.0


def test_restriction_excludes_cards_the_choose_clause_cannot_take(card_db):
    game = _game(card_db)
    _put(game, card_db, "Scion of Draco", 1, "hand")        # MV 12
    for _ in range(5):
        _put(game, card_db, "Plains", 1, "library")
    snap = snapshot_from_game(game, 0)
    inquisition = card_db.get_card("Inquisition of Kozilek")  # MV ≤ 3
    thoughtseize = card_db.get_card("Thoughtseize")
    # Nothing the ceiling allows: the projection's average-card credit
    # is retracted and no strip value replaces it.
    assert (hand_denial_value(inquisition, game, 0, snap)
            == pytest.approx(-card_clock_impact(snap)))
    assert hand_denial_value(thoughtseize, game, 0, snap) > 0


def test_victim_chosen_discard_carries_no_selection_premium(card_db):
    game = _game(card_db)
    _put(game, card_db, "Scion of Draco", 1, "hand")
    snap = snapshot_from_game(game, 0)
    tmpl = card_db.get_card("Raven's Crime")   # "target player discards a card"
    assert hand_denial_value(tmpl, game, 0, snap) == 0.0


# ── Scoring integration ──────────────────────────────────────────────


def _zoo_pool(game, card_db, hand_creatures, library_creatures):
    for _ in range(hand_creatures):
        _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "hand")
    for _ in range(library_creatures):
        _put(game, card_db, "Scion of Draco", 1, "library")
    for _ in range(20):
        _put(game, card_db, "Plains", 1, "library")


def test_scored_hand_attack_is_positive_against_a_threat_dense_hidden_hand(card_db):
    from ai.ev_player import EVPlayer
    game = _game(card_db)
    seize = _put(game, card_db, "Thoughtseize", 0, "hand")
    _zoo_pool(game, card_db, hand_creatures=5, library_creatures=10)
    ai = EVPlayer(player_idx=0, deck_name="Goryo's Vengeance",
                  rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    score = ai._score_spell(seize, snap, game, game.players[0], game.players[1])
    assert score > 0


def test_scored_hand_attack_stays_negative_into_an_empty_hand(card_db):
    from ai.ev_player import EVPlayer
    game = _game(card_db)
    seize = _put(game, card_db, "Thoughtseize", 0, "hand")
    _zoo_pool(game, card_db, hand_creatures=0, library_creatures=10)
    ai = EVPlayer(player_idx=0, deck_name="Goryo's Vengeance",
                  rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    score = ai._score_spell(seize, snap, game, game.players[0], game.players[1])
    assert score < 0
