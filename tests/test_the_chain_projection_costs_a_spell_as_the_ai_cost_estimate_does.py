"""The chain projection costs a spell as the AI's cost estimate does (AI,
from CR 601.2f).

A cost reduction reduces the generic part of a spell's cost and nothing
else: every coloured, colourless and hybrid pip stays owed (CR 601.2f).
It reduces the spells its rule names: "red spells you cast cost {1} less"
reduces a red spell, "instant and sorcery spells" an instant or a sorcery,
whatever other reducer is on the battlefield.

The AI's cost estimate has one owner, `ai/effective_cmc` (unit CM floored
it at the pips). The chain projection (`ai/combo_chain`: the storm-zone
assessment, the reducer's value, the finisher simulators) prices each spell
through it: `effective_cmc.chain_cost`, the estimate now less what each
reducer the projection casts first takes off by its typed rule.

The defect: the chain kept its own arithmetic, `max(0, cmc - medallions)`
for red instants and sorceries. Under two Ruby Medallions it read a {1}{R}
ritual as free, so each ritual netted one mana more than it makes, and it
read no other reducer (a planeswalker's "cost {1} less" rule, a reducer
for instants and sorceries).

Card names are fixture carriers: the class is every spell a chain
projection prices, under every reducer.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, medallions=0):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(medallions):
        _add(game, card_db, "Ruby Medallion", 0)
    game.continuous_effects.recalculate(game)
    return game


def test_the_chain_prices_a_spell_at_the_cost_estimate(card_db):
    """Two red reducers on the battlefield: a {1}{R} ritual costs its pip,
    {3}{R} costs two -- what the AI's cost estimate (and the engine)
    charges."""
    from ai.combo_chain import classify_card
    from ai.effective_cmc import effective_cmc
    game = _game(card_db, medallions=2)
    ritual = _add(game, card_db, "Desperate Ritual", 0, "hand")
    flames = _add(game, card_db, "Past in Flames", 0, "hand")
    for card, cost in ((ritual, 1), (flames, 2)):
        role = classify_card(card, 5, set(), game=game, player_idx=0)
        assert role.effective_cost == cost
        assert cost == effective_cmc(card, game=game, player_idx=0)


def test_a_reducer_the_chain_casts_first_reduces_what_its_rule_names(
        card_db):
    from ai.effective_cmc import chain_cost
    game = _game(card_db)
    medallion = _add(game, card_db, "Ruby Medallion", 0, "hand")
    electromancer = _add(game, card_db, "Goblin Electromancer", 0, "hand")
    flames = _add(game, card_db, "Past in Flames", 0, "hand")     # {3}{R}
    consider = _add(game, card_db, "Consider", 0, "hand")         # {U}
    assert chain_cost(flames, game, 0) == 4
    assert chain_cost(flames, game, 0, reducers=[medallion]) == 3
    assert chain_cost(flames, game, 0,
                      reducers=[medallion, electromancer]) == 2
    assert chain_cost(consider, game, 0, reducers=[medallion]) == 1, \
        "a red reducer does not name a blue spell"


def test_the_chain_counts_the_pip_no_reducer_takes(card_db):
    """Two Medallions, one land's mana: Desperate Ritual, Pyretic Ritual,
    Grapeshot. Each costs its {R}: 1 - 1 + 3 - 1 + 3 - 1 leaves 4, not
    the 7 a free ritual would leave."""
    from ai.combo_chain import find_all_chains
    game = _game(card_db, medallions=2)
    hand = [_add(game, card_db, n, 0, "hand")
            for n in ("Desperate Ritual", "Pyretic Ritual", "Grapeshot")]
    chains = find_all_chains(hand, 1, set(), game=game, player_idx=0)
    full = [c for c in chains if c.storm_count == 3]
    assert full and max(c.final_mana for c in full) == 4


def test_a_reducer_cast_in_the_chain_reduces_the_spells_after_it(card_db):
    """No reducer on the battlefield, three mana: Ruby Medallion first
    (2) makes Desperate Ritual and Grapeshot cost their pip -- 3 - 2 - 1
    + 3 - 1 leaves 2. Without its rule the ritual's {1}{R} is out of
    reach after the Medallion."""
    from ai.combo_chain import find_all_chains
    game = _game(card_db)
    hand = [_add(game, card_db, n, 0, "hand")
            for n in ("Ruby Medallion", "Desperate Ritual", "Grapeshot")]
    chains = find_all_chains(hand, 3, set(), game=game, player_idx=0)
    full = [c for c in chains if c.storm_count == 3]
    assert full and full[0].sequence[0] == "Ruby Medallion"
    assert max(c.final_mana for c in full) == 2


# ── The chain the hand can cast ───────────────────────────────────────

def test_a_large_hands_chain_passes_over_a_spell_the_mana_cannot_pay(
        card_db):
    """More fuel than the exhaustive search takes, so the chain is built
    in order: eight rituals (each {1}{R} for RRR) leave 10 mana, and a
    13-mana spell among them is passed over -- not the chain's end. The
    chain still closes: Grapeshot at storm 8 deals 9."""
    from ai.combo_chain import find_all_chains
    from ai.scoring_constants import CHAIN_EXHAUSTIVE_FUEL_BUDGET
    game = _game(card_db)
    names = (["Desperate Ritual"] * (CHAIN_EXHAUSTIVE_FUEL_BUDGET + 1)
             + ["Emrakul, the Promised End", "Grapeshot"])
    hand = [_add(game, card_db, n, 0, "hand") for n in names]
    chains = find_all_chains(hand, 2, set(), game=game, player_idx=0)
    assert max((c.storm_damage for c in chains), default=0) == 9


def test_the_residue_is_what_the_castable_chain_leaves(card_db):
    """Desperate Ritual and two Past in Flames on two lands: the chain the
    hand can cast is the ritual (2 for RRR), which leaves 3. The two
    Past in Flames, castable by neither 2 mana nor 3, are not charged."""
    from ai.combo_calc import assess_combo
    from ai.ev_evaluator import snapshot_from_game
    from ai.ev_player import EVPlayer
    game = _game(card_db)
    for _ in range(2):
        _add(game, card_db, "Mountain", 0)
    for p in (0, 1):
        for _ in range(5):
            _add(game, card_db, "Island", p, "library")
    for name in ("Desperate Ritual", "Past in Flames", "Past in Flames"):
        _add(game, card_db, name, 0, "hand")
    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    a = assess_combo(game, 0, player.goal_engine,
                     snapshot_from_game(game, 0), None)
    assert a.r_res == 3
