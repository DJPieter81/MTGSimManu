"""Combo engine disruption premium — removal target picker must
prefer opponent's declared engine pieces over equally-weighted
vanilla permanents when the opponent's archetype is `combo` and
their `combo_clock` is inside the disruption window.

Rule, phrased without naming any card:

    For a removal spell that can hit nonland permanents, when
    the opponent's gameplan declares `archetype == "combo"` and
    their `combo_clock(snap)` is below `COMBO_WINDOW`, the
    target picker must add an `engine_disruption_value(card)`
    premium to `permanent_threat(card)`. The premium is non-zero
    iff `card.name` appears in any of the opponent's gameplan
    `card_roles["engines"]` or `card_roles["payoffs"]` lists.

This generalizes across every combo deck registered with the
sim (current set: Ruby Storm, Living End, Goryo's Vengeance,
Amulet Titan) — no card-name special-casing in `_choose_targets`
or in the helper.

Concrete G1-style failure mode this pins (Boros Energy vs Ruby
Storm, post-board game): opp has resolved Ruby Medallion (engine,
cost-reducer) and Mind Stone (vanilla mana rock) on the
battlefield. We hold Naturalize. `permanent_threat` returns
roughly equal values for both (each contributes one artifact +
one mana source to opp's position). Without the premium, the
target picker takes the first one in iteration order — Mind
Stone — leaving the cost-reducer in play to fuel next turn's
storm chain. With the premium, the engine wins the tie-break.

The `engine_disruption_value` helper composes additively with
`permanent_threat` so the marginal-position-drop semantics of
the existing primitive are preserved; the premium only ever
breaks ties or amplifies a real drop, never overrides a larger
non-engine threat.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _battlefield(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _hand(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def test_nonland_removal_targets_combo_engine_over_vanilla_rock(card_db):
    """In a Ruby Storm matchup with the storm chain mid-flight
    (storm_count >= 5 → combo_clock == 1.0, urgency == 4.0),
    Naturalize on a board of (Mind Stone, Ruby Medallion) must
    target Ruby Medallion. Mind Stone is listed first to defeat
    iteration-order coincidence — `max()` is stable on ties, so
    without the engine premium Mind Stone is selected.

    The fix is: extend the nonland-removal target key from
    `permanent_threat(c)` to
    `permanent_threat(c) + engine_disruption_value(c, opp, game)`.
    """
    db = card_db
    game = GameState(rng=random.Random(0))
    # Opponent (player 1) is Ruby Storm and has resolved both a
    # vanilla mana rock (Mind Stone, listed first) and the engine
    # cost-reducer (Ruby Medallion, listed second). Both are 2-CMC
    # artifacts contributing roughly the same position value.
    opp = game.players[1]
    opp.deck_name = "Ruby Storm"
    mind_stone = _battlefield(game, db, "Mind Stone", 1)
    ruby_medallion = _battlefield(game, db, "Ruby Medallion", 1)

    # Push opp's combo_clock to 1.0 so the disruption window is
    # fully open. storm_count >= 5 collapses combo_clock to 1.0
    # directly (see ai/clock.py:150-151), bypassing hand/mana
    # bookkeeping in the test. urgency = COMBO_WINDOW - 1.0 = 4.0.
    opp.spells_cast_this_turn = 5

    # We (player 0) hold Naturalize. The "Destroy target artifact
    # or enchantment" oracle hits the nonland-removal branch in
    # _choose_targets via `'artifact' in oracle`.
    naturalize = _hand(game, db, "Naturalize", 0)
    if not isinstance(naturalize.template.tags, set):
        naturalize.template.tags = set()
    naturalize.template.tags.add("removal")

    from ai.ev_player import EVPlayer
    player = EVPlayer(player_idx=0, deck_name="Boros Energy")
    chosen = player._choose_targets(game, naturalize)

    assert chosen, "AI returned no target for nonland removal"
    chosen_id = chosen[0]
    assert chosen_id == ruby_medallion.instance_id, (
        f"Naturalize targeted "
        f"{'Mind Stone' if chosen_id == mind_stone.instance_id else f'card id {chosen_id}'}"
        f" instead of Ruby Medallion. Both artifacts have the same "
        f"raw permanent_threat (vanilla mana rock vs cost-reducer "
        f"both contribute one artifact + one mana source to opp's "
        f"position). The engine_disruption_value helper must add a "
        f"premium for cards listed in opp's gameplan card_roles "
        f"under 'engines' so the cost-reducer wins the tie. This "
        f"pins the rule for every combo-archetype deck — Storm, "
        f"Living End, Amulet Titan, Goryo's Vengeance — without "
        f"per-card overrides in ai/ev_player.py::_choose_targets."
    )


def test_engine_disruption_value_returns_zero_when_opp_is_not_combo():
    """Negative gate: when opp's gameplan archetype is not 'combo'
    (e.g. Boros Energy = aggro), the premium is identically zero
    even for a card that happens to be in *some* deck's engines
    list. This proves the helper consults opp's gameplan, not a
    global card-role lookup.
    """
    db = CardDatabase()
    game = GameState(rng=random.Random(0))
    opp = game.players[1]
    opp.deck_name = "Boros Energy"      # aggro, not combo
    medallion = _battlefield(game, db, "Ruby Medallion", 1)

    from ai.engine_disruption import engine_disruption_value
    premium = engine_disruption_value(medallion, opp, game)
    assert premium == 0.0, (
        f"engine_disruption_value returned {premium} for an "
        f"aggro opponent — must return 0.0 unless opp's gameplan "
        f"declares archetype == 'combo'. The premium is gated on "
        f"opp's deck identity, not on the card itself."
    )


def test_engine_disruption_value_returns_zero_for_non_engine_card():
    """Negative gate: even against a combo opp, a card that is not
    listed in any of opp's gameplan card_roles ('engines' /
    'payoffs') gets premium = 0. This proves the helper reads opp's
    declared roles instead of pattern-matching oracle text or
    inferring 'is this a cost reducer'.
    """
    db = CardDatabase()
    game = GameState(rng=random.Random(0))
    opp = game.players[1]
    opp.deck_name = "Ruby Storm"        # combo
    opp.spells_cast_this_turn = 5       # combo_clock == 1.0
    # Mind Stone is a vanilla mana rock, not in Storm's engines or
    # payoffs. It must score zero premium.
    mind_stone = _battlefield(game, db, "Mind Stone", 1)

    from ai.engine_disruption import engine_disruption_value
    premium = engine_disruption_value(mind_stone, opp, game)
    assert premium == 0.0, (
        f"engine_disruption_value returned {premium} for Mind "
        f"Stone vs a Storm opponent — Mind Stone is not declared "
        f"in Storm's gameplan card_roles. The premium must be 0 "
        f"unless the card name appears under 'engines' or "
        f"'payoffs'. No oracle pattern-matching, no archetype-"
        f"specific heuristics — just role-membership lookup."
    )
