"""A self-untapping mana source whose untap cost is fully replaced away is
an unbounded mana engine, and the sim shortcuts it (CR 726.4) instead of
refusing it.

Rule 9 refuses to put a free, repeatable ability on the stack — nothing
depletes, so nothing terminates the loop and the AI would spin. That guard
is a sim safety valve, not a rule of Magic: "Put a -1/-1 counter on this
creature: untap it" alongside "that many -1/-1 counters minus one are put on
it instead" is a legal loop a paper player shortcuts N times. The engine now
exposes the loop as MANA: `ActivationManager.unbounded_mana_engines` names
the permanents whose tap mana ability can be repeated for free, the mana
capacity estimate counts a finite shortcut allowance
(`LOOP_SHORTCUT_MANA`), and payment executes the iterations it needs —
tap for mana, pay the (replaced-to-zero) counter cost through the funnel,
untap — logging one shortcut line. The stack activation stays refused, so
nothing spins.

Card names below are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.activation import ActivationManager
from engine.cards import COUNTER_KIND_MINUS, CardInstance
from engine.constants import LOOP_SHORTCUT_MANA
from engine.game_state import GameState
from engine.mana import ManaCost


def _bf(game, card_db, name, controller=0, sick=False):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = sick
    game.players[controller].battlefield.append(c)
    return c


def _untap_ability(perm):
    return next(a for a in perm.template.activated_abilities
                if ActivationManager._untaps_its_own_source(a))


def test_self_untap_with_a_depleting_counter_cost_is_not_an_engine(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Devoted Druid")
    assert ActivationManager.unbounded_mana_engines(game, 0) == []


def test_self_untap_whose_counter_cost_is_fully_replaced_is_an_engine(card_db):
    game = GameState(rng=random.Random(0))
    druid = _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    assert ActivationManager.unbounded_mana_engines(game, 0) == [druid]


def test_a_summoning_sick_engine_cannot_tap_and_is_excluded(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Devoted Druid", sick=True)
    _bf(game, card_db, "Vizier of Remedies")
    assert ActivationManager.unbounded_mana_engines(game, 0) == []


def test_the_free_untap_stays_off_the_stack(card_db):
    game = GameState(rng=random.Random(0))
    druid = _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    assert not ActivationManager.can_activate(game, 0, druid, _untap_ability(druid))


def test_engine_counts_toward_mana_capacity(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Devoted Druid")
    before = game.players[0].untapped_mana_capacity()
    _bf(game, card_db, "Vizier of Remedies")
    assert game.players[0].untapped_mana_capacity() >= before + LOOP_SHORTCUT_MANA


def test_payment_loops_the_engine_to_cover_a_shortfall(card_db):
    game = GameState(rng=random.Random(0))
    druid = _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    ok = game.tap_lands_for_mana(0, ManaCost(generic=6, green=1))
    assert ok
    assert druid.minus_counters == 0, "each iteration's cost is replaced away"
    assert druid.tapped
    assert any("shortcut" in line for line in game.log)


def test_payment_cannot_loop_for_a_color_the_engine_does_not_make(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    assert not game.tap_lands_for_mana(0, ManaCost(blue=1))


def test_without_the_replacement_payment_does_not_loop(card_db):
    game = GameState(rng=random.Random(0))
    druid = _bf(game, card_db, "Devoted Druid")
    assert not game.tap_lands_for_mana(0, ManaCost(generic=3))
    assert druid.minus_counters == 0


# ── Engine completion as a rules query (feeds tutor delivery + AI credit) ──

def test_a_replacement_source_completes_an_engine_with_a_self_untapper_on_board(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Devoted Druid")
    assert ActivationManager.would_complete_unbounded_engine(
        game, 0, card_db.get_card("Vizier of Remedies"))


def test_a_self_untapper_completes_an_engine_with_the_replacement_on_board(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Vizier of Remedies")
    # The candidate would enter summoning sick; the query asks whether the
    # LOOP exists, not whether it can be spun this very turn.
    assert ActivationManager.would_complete_unbounded_engine(
        game, 0, card_db.get_card("Devoted Druid"))


def test_an_unrelated_creature_completes_nothing(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Devoted Druid")
    assert not ActivationManager.would_complete_unbounded_engine(
        game, 0, card_db.get_card("Grizzly Bears"))


def test_nothing_completes_when_the_engine_already_exists(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    assert not ActivationManager.would_complete_unbounded_engine(
        game, 0, card_db.get_card("Vizier of Remedies"))


def test_tutor_delivery_ranks_the_engine_completing_piece_first(card_db):
    """A creature tutor's delivery ranking is rules-derived: a candidate that
    completes an unbounded mana loop with the board outranks a bigger body."""
    from engine.cast_manager import pick_creature_tutor_x_value
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Devoted Druid")
    me = game.players[0]
    for i, n in enumerate(["Vizier of Remedies", "Eternal Witness",
                           "Grizzly Bears"]):
        t = card_db.get_card(n)
        lib = CardInstance(template=t, owner=0, controller=0,
                           instance_id=100 + i, zone="library")
        lib._game_state = game
        me.library.append(lib)
    best_x, target, _top = pick_creature_tutor_x_value(
        game, 0, 3, card_db.get_card("Nature's Rhythm"))
    assert target is not None and target.name == "Vizier of Remedies"
    assert best_x == 2


# ── Cast feasibility must see the engine's colours, not only its units ──

def _hand(game, card_db, name):
    c = CardInstance(template=card_db.get_card(name), owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone="hand")
    c._game_state = game
    game.players[0].hand.append(c)
    return c


def test_pip_heavy_spell_is_castable_through_an_engine_of_its_colour(card_db):
    from engine.game_state import Phase
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    hoof = _hand(game, card_db, "Craterhoof Behemoth")   # {5}{G}{G}{G}
    assert game.can_cast(0, hoof)


def test_engine_colours_do_not_cover_other_pips(card_db):
    from engine.game_state import Phase
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    blue = _hand(game, card_db, "Counterspell")           # {U}{U}
    assert not game.can_cast(0, blue)
