"""A turn plan reads every card the player may play (CR 305.1, 601.2a).

"Exile the top N cards of your library. Until the end of your next turn,
you may play those cards" leaves its cards in exile, castable through a
permission (`rules_query.permitted_cards`). A plan that reads the hand
alone -- a storm chain's fuel, a combo's readiness, a payoff check, a
lethal line -- never counts them, so they expire unplayed. Each plan reads
`ai.playable_cards`: the hand and the exiled cards a permission names.
An exiled card no permission names is not playable, and a hand-only read
(discard, hand size) keeps the hand.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


def _game(opp_life=20):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.players[1].life = opp_life
    return game


def _template(name, *, cmc=1, types=(CardType.INSTANT,), oracle="",
              tags=(), ritual_mana=None, keywords=()):
    return CardTemplate(
        name=name, card_types=list(types), mana_cost=ManaCost(generic=cmc),
        supertypes=[], subtypes=[], power=None, toughness=None, loyalty=None,
        keywords=set(keywords), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False, oracle_text=oracle,
        tags=set(tags), ritual_mana=ritual_mana)


def _put(game, tmpl, zone, idx=0):
    card = CardInstance(template=tmpl, owner=idx, controller=idx,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[idx], zone).append(card)
    return card


def _lands(game, n, idx=0):
    for i in range(n):
        land = CardTemplate(
            name=f"Land{i}", card_types=[CardType.LAND], mana_cost=None,
            supertypes=[], subtypes=[], power=None, toughness=None,
            loyalty=None, keywords=set(), abilities=[],
            color_identity=set(), produces_mana=["any"],
            enters_tapped=False, oracle_text="", tags=set())
        _put(game, land, "battlefield", idx)


def _permit(game, cards, action="play"):
    from engine.effect_model import THIS_TURN, permit_play
    game.continuous_effects.register_effect(
        permit_play(0, [c.instance_id for c in cards], action, THIS_TURN))


def _storm_fuel(game, zone, n_rituals=4):
    """Rituals (cost 1, add 3) and a storm finisher. The chain estimator
    reads the finisher by its printed name, so the fixture uses it."""
    from engine.cards import Keyword
    cards = [_put(game, _template(f"Ritual{i}", oracle="add {r}{r}{r}.",
                                  tags={"ritual"}, ritual_mana=("R", 3)),
                  zone) for i in range(n_rituals)]
    cards.append(_put(game, _template(
        "Grapeshot", cmc=2, types=(CardType.SORCERY,),
        oracle="grapeshot deals 1 damage to any target. storm",
        tags={"storm_payoff"}, keywords={Keyword.STORM}), zone))
    return cards


@pytest.fixture
def no_tags(monkeypatch):
    """The classifier's committed cache is not this test's input."""
    from ai import oracle_classifier as oc
    monkeypatch.setattr(oc, "_LOADED_CACHE", {})
    monkeypatch.setattr(oc, "_LOADED_PATH", None)
    monkeypatch.setattr(oc, "load_oracle_tags", lambda **kw: {})


# ── The read path ──────────────────────────────────────────────────────

def test_permitted_cards_are_the_exiled_cards_a_permission_names():
    from engine import rules_query
    from engine.turn_clock import Clock, ClockEvent, emit
    game = _game()
    named = _put(game, _template("Named"), "exile")
    _put(game, _template("Unnamed"), "exile")
    assert rules_query.permitted_cards(game, 0) == []
    _permit(game, [named])
    assert rules_query.permitted_cards(game, 0) == [named]
    assert rules_query.permitted_cards(game, 1) == []
    emit(game, ClockEvent(Clock.CLEANUP, 0))           # "this turn" ends
    assert rules_query.permitted_cards(game, 0) == []


def test_a_turn_plan_sees_the_hand_and_the_permitted_exiled_cards():
    from ai.playable_cards import plan_view, playable_cards
    game = _game()
    in_hand = _put(game, _template("InHand"), "hand")
    named = _put(game, _template("Named"), "exile")
    _put(game, _template("Unnamed"), "exile")
    me = game.players[0]
    assert plan_view(game, 0) is me                    # nothing permitted
    _permit(game, [named])
    assert playable_cards(game, 0) == [in_hand, named]
    view = plan_view(game, 0)
    assert view.hand == [in_hand, named]
    assert view.life == me.life and view.exile is me.exile
    assert me.hand == [in_hand]                        # the hand itself
    with pytest.raises(AttributeError):
        view.life = 1


# ── The plans ──────────────────────────────────────────────────────────

def test_a_storm_chain_counts_the_fuel_a_permission_lets_the_player_cast(
        no_tags):
    """Rituals and a storm finisher that a permission lets the player cast
    from exile are the chain's fuel exactly as in hand; the same cards in
    exile with no permission are none."""
    from ai.ev_evaluator import _estimate_combo_chain
    game = _game(opp_life=5)
    _lands(game, 4)
    fuel = _storm_fuel(game, "exile")
    assert _estimate_combo_chain(game, 0)[0] is False   # not castable
    _permit(game, fuel)
    can_kill, storm, damage, chain = _estimate_combo_chain(game, 0)
    assert can_kill is True and "Grapeshot" in chain and damage >= 5


def test_the_storm_readiness_counts_permitted_exiled_fuel_and_payoff(no_tags):
    from ai.combo_calc import assess_combo
    from tests.test_combo_calc import (MockGameplan, MockGoal,
                                       MockGoalEngine, _make_snap)
    goal = MockGoal(resource_zone="storm", resource_target=5,
                    card_roles={"payoffs": {"Grapeshot"}})
    engine = MockGoalEngine(gameplan=MockGameplan(goals=[goal]))
    game = _game(opp_life=5)
    _lands(game, 4)
    fuel = _storm_fuel(game, "exile")
    snap = _make_snap(opp_life=5, my_mana=4)
    assert not assess_combo(game, 0, engine, snap).has_payoff
    _permit(game, fuel)
    ready = assess_combo(game, 0, engine, snap)
    assert ready.has_payoff and ready.best_chain is not None


# ── The permission's last turn (CR 611.2): a card playable only until this
#    turn's cleanup is gone then whether or not it is played -- it is
#    never deferred, it is not a held resource, and spending it spends
#    nothing the player would keep. ──

def _permit_until_next_turn(game, cards, created_turn):
    from engine.effect_model import permit_play, until_end_of_your_next_turn
    game.continuous_effects.register_effect(permit_play(
        0, [c.instance_id for c in cards], "play",
        until_end_of_your_next_turn(0, created_turn)))


def test_a_permission_ends_this_turn_on_its_last_turn_only():
    from engine import rules_query
    game = _game()
    game.turn_number = 5
    now = _put(game, _template("ThisTurn"), "exile")
    later = _put(game, _template("NextTurn"), "exile")
    _permit(game, [now])
    _permit_until_next_turn(game, [later], created_turn=5)
    assert rules_query.permission_ends_this_turn(game, 0, now)
    assert not rules_query.permission_ends_this_turn(game, 0, later)
    game.turn_number, game.active_player = 6, 1          # their turn
    assert not rules_query.permission_ends_this_turn(game, 0, later)
    game.turn_number, game.active_player = 7, 0          # your next turn
    assert rules_query.permission_ends_this_turn(game, 0, later)


def test_a_position_holds_the_permitted_cards_that_outlast_this_turn():
    from ai.ev_evaluator import snapshot_from_game
    from ai.playable_cards import held_cards, playable_cards
    game = _game()
    game.turn_number = 5
    in_hand = _put(game, _template("InHand"), "hand")
    now = _put(game, _template("ThisTurn"), "exile")
    later = _put(game, _template("NextTurn"), "exile")
    _permit(game, [now])
    _permit_until_next_turn(game, [later], created_turn=5)
    assert playable_cards(game, 0) == [in_hand, now, later]
    assert held_cards(game, 0) == [in_hand, later]
    assert snapshot_from_game(game, 0).my_hand_size == 2


def test_casting_a_card_whose_permission_ends_this_turn_spends_no_held_card():
    from ai.ev_evaluator import _project_spell, snapshot_from_game
    game = _game()
    game.turn_number = 5
    now = _put(game, _template("ThisTurn"), "exile")
    later = _put(game, _template("NextTurn"), "exile")
    _permit(game, [now])
    _permit_until_next_turn(game, [later], created_turn=5)
    snap = snapshot_from_game(game, 0)
    assert _project_spell(now, snap, game=game,
                          player_idx=0).my_hand_size == snap.my_hand_size
    assert _project_spell(later, snap, game=game,
                          player_idx=0).my_hand_size == snap.my_hand_size - 1


def test_a_spell_cast_from_exile_counts_for_storm_like_any_other_cast():
    """CR 702.40a: storm counts the spells cast this turn, from wherever.
    Casting a card whose permission ends this turn spends no held card,
    but it is a spell cast."""
    from ai.ev_evaluator import _project_spell, snapshot_from_game
    game = _game()
    game.turn_number = 5
    now = _put(game, _template("ThisTurn"), "exile")
    held = _put(game, _template("InHand"), "hand")
    _permit(game, [now])
    snap = snapshot_from_game(game, 0)
    for card in (now, held):
        assert _project_spell(card, snap, game=game,
                              player_idx=0).storm_count == snap.storm_count + 1


def test_a_card_whose_permission_ends_this_turn_is_never_deferred():
    from ai.ev_evaluator import (_enumerate_this_turn_signals,
                                 snapshot_from_game)
    game = _game()
    game.turn_number = 5
    now = _put(game, _template("ThisTurn"), "exile")
    later = _put(game, _template("NextTurn"), "exile")
    _permit(game, [now])
    _permit_until_next_turn(game, [later], created_turn=5)
    snap = snapshot_from_game(game, 0)
    assert "permission_ends_this_turn" in _enumerate_this_turn_signals(
        now, snap, game, 0)
    assert "permission_ends_this_turn" not in _enumerate_this_turn_signals(
        later, snap, game, 0)


def test_a_payoff_a_permission_lets_the_player_cast_is_reachable(no_tags):
    from ai.ev_evaluator import _payoff_reachable_this_turn
    game = _game()
    *rituals, finisher = _storm_fuel(game, "exile", n_rituals=1)
    in_hand = _put(game, _template("Ritual", oracle="add {r}{r}{r}.",
                                   tags={"ritual"}, ritual_mana=("R", 3)),
                   "hand")
    assert not _payoff_reachable_this_turn(in_hand, game, 0)
    _permit(game, [finisher])
    assert _payoff_reachable_this_turn(in_hand, game, 0)


def test_a_land_playable_only_this_turn_is_played_before_a_land_in_hand(
        card_db):
    """The expiring land is gone at cleanup unless played; the land in
    hand stays: the land play goes to the expiring one."""
    from ai.ev_player import EVPlayer
    game = _game()
    game.turn_number = 5
    from engine.cards import CardInstance
    def _real(name, zone):
        c = CardInstance(template=card_db.get_card(name), owner=0,
                         controller=0, instance_id=game.next_instance_id(),
                         zone=zone)
        c._game_state = game
        getattr(game.players[0], zone).append(c)
        return c
    in_hand = _real("Mountain", "hand")
    exiled = _real("Mountain", "exile")
    _permit(game, [exiled])
    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    me = game.players[0]
    assert player._score_land(exiled, me, [], game) > \
        player._score_land(in_hand, me, [], game)


# ── An impulse draw is no draw (CR 121.1c), read from the parsed spell:
#    a classifier tag neither makes a spell one nor unmakes it. ──

@pytest.mark.parametrize("name,impulse", [("Reckless Impulse", True),
                                          ("Act on Impulse", True),
                                          ("Opt", False),
                                          ("Consult the Star Charts", False)])
def test_an_impulse_draw_is_read_from_the_spell(card_db, name, impulse):
    from ai.predicates import is_impulse_draw
    assert is_impulse_draw(card_db.get_card(name)) is impulse


def test_a_chain_step_counts_no_draw_for_an_impulse_spell_tagged_or_not(
        card_db, monkeypatch):
    from ai import oracle_classifier as oc
    from ai.ev_evaluator import _draw_count_for_chain_step
    monkeypatch.setattr(oc, "_LOADED_CACHE", {})
    monkeypatch.setattr(oc, "_LOADED_PATH", None)
    monkeypatch.setattr(oc, "load_oracle_tags", lambda **kw: {})
    assert _draw_count_for_chain_step(card_db.get_card("Act on Impulse")) == 0
    assert _draw_count_for_chain_step(card_db.get_card("Opt")) == 1


def test_an_impulse_tag_alone_never_unmakes_a_draw(card_db, monkeypatch):
    from ai import oracle_classifier as oc
    from ai.ev_evaluator import _projected_real_draws
    monkeypatch.setattr(oc, "tags_for",
                        lambda name: frozenset({oc.Tag.IMPULSE_DRAW}))
    monkeypatch.setattr(oc, "has_tag", lambda name, tag: True)
    game = _game()
    opt = CardInstance(template=card_db.get_card("Opt"), owner=0,
                       controller=0, instance_id=game.next_instance_id(),
                       zone="hand")
    assert _projected_real_draws(opt) == 1
