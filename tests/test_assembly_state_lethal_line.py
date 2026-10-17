"""Payoff sequencing — one owner of engine / sink / lethal-line facts
(docs/design/2026-09-16_payoff_sequencing_design.md §2, units U2+U3).

Rules pinned (mechanic-phrased; card names are fixture carriers — a
self-untapping mana creature plus its untap-cost replacement form the loop,
a mana-costed team-counter activation and an Overrun-shape mass pump are
the sinks, an X creature tutor is the access):

  * a mana-scaled team-counter activation on the battlefield with a live
    engine projects a lethal line, and the line is projected THROUGH
    blocks (CR 509.1a / 702.19b), so blockers can make it non-lethal;
  * a mass-pump sink in hand projects the pumped attack of the team left
    untapped after payment, the entering body attacking only with haste;
  * a live engine has no completers and earns no second completion credit;
  * a completion credit whose only sink access is the tutor being spent is
    draw-discounted, never a hard zero; with another access it is whole;
  * a zero-toughness X sink delivered to the battlefield without entry
    counters is not an access (CR 704.5f);
  * a token maker that scales on something other than mana is not a sink;
  * the first step of the best line is offered and credited the win swing
    (activation and cast paths); the attack declarer and the projector
    read the same reach function;
  * the X-tutor patience hold treats an engine completion as acceleration
    (the negative control: the enabler is still fetched at a small X).
"""
from __future__ import annotations

import random

from ai.assembly_state import (STEP_ACTIVATE, STEP_CAST, assemble,
                               attack_reach, engine_completion_credit,
                               is_mana_sink)
from ai.ev_evaluator import snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller=0, zone="battlefield", sick=False):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = sick
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db, forests=4, engine=True, opp_blockers=0):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    game.players[0].deck_name = "Creatures Toolbox"
    game.players[1].deck_name = "Domain Zoo"
    for _ in range(forests):
        _put(game, card_db, "Forest")
    _put(game, card_db, "Devoted Druid")
    if engine:
        _put(game, card_db, "Vizier of Remedies")
    for _ in range(2):
        _put(game, card_db, "Grizzly Bears")
    for _ in range(opp_blockers):
        _put(game, card_db, "Leatherback Baloth", 1)     # a fixed 4/5 body
    for _ in range(6):
        _put(game, card_db, "Forest", 0, "library")
    for _ in range(3):
        _put(game, card_db, "Mountain", 1, "battlefield")
    return game


def _state(game):
    return assemble(game, 0, snapshot_from_game(game, 0))


def test_a_team_counter_activation_with_a_live_engine_projects_a_lethal_line(card_db):
    game = _game(card_db)
    leyline = _put(game, card_db, "Leyline of Abundance")
    st = _state(game)
    assert st.engine_live and st.engine_spins_now
    assert st.best_line is not None, [a for a in st.sinks]
    assert st.best_line.first_step == (STEP_ACTIVATE, leyline.instance_id,
                                       st.best_line.access.ability_index)
    assert st.best_line.access.damage >= game.players[1].life
    assert st.best_line.swing > 0


def test_the_line_is_projected_through_blocks(card_db):
    game = _game(card_db, opp_blockers=3)   # three untapped 4/5s, no trample
    _put(game, card_db, "Leyline of Abundance")
    st = _state(game)
    assert st.best_line is None, st.best_line


def test_a_mass_pump_sink_in_hand_projects_the_pumped_attack_through_blocks(card_db):
    game = _game(card_db, opp_blockers=2)
    hoof = _put(game, card_db, "Craterhoof Behemoth", zone="hand")
    st = _state(game)
    assert st.best_line is not None, st.sinks
    assert st.best_line.first_step == (STEP_CAST, hoof.instance_id)
    access = st.best_line.access
    # Two blockers absorb toughness from two tramplers; the rest connects.
    assert access.damage >= game.players[1].life


def test_without_an_engine_the_mass_pump_is_not_castable_and_no_line_exists(card_db):
    game = _game(card_db, engine=False)
    _put(game, card_db, "Craterhoof Behemoth", zone="hand")
    st = _state(game)
    assert not st.engine_live
    assert st.best_line is None


def test_a_live_engine_has_no_completers_and_no_second_completion_credit(card_db):
    game = _game(card_db)
    _put(game, card_db, "Vizier of Remedies", zone="hand")
    st = _state(game)
    assert st.completers == ()
    snap = snapshot_from_game(game, 0)
    assert engine_completion_credit(
        game, 0, st, card_db.get_card("Vizier of Remedies"), snap) == 0.0


def test_completion_credit_is_draw_discounted_when_the_only_access_is_the_tutor_being_spent(card_db):
    from engine.constants import LOOP_SHORTCUT_MANA
    # The opponent has a clock (two 4/5s), so the horizon is finite; with
    # no opposing clock the horizon covers the library and the credit is
    # whole — there is all the time in the world to draw the sink.
    game = _game(card_db, forests=3, engine=False, opp_blockers=2)
    # An X creature tutor with no colour constraint (the replacement piece
    # is off-colour for a green-only search).
    finale = _put(game, card_db, "Finale of Devastation", zone="hand")
    piece = _put(game, card_db, "Vizier of Remedies", zone="library")
    _put(game, card_db, "Walking Ballista", zone="library")   # the only sink
    snap = snapshot_from_game(game, 0)
    st = assemble(game, 0, snap)
    vizier = card_db.get_card("Vizier of Remedies")
    assert piece.instance_id in st.completers, st
    credit = engine_completion_credit(game, 0, st, vizier, snap,
                                      spending=finale)
    full = LOOP_SHORTCUT_MANA - vizier.cmc
    assert 0.0 < credit < full, credit
    # A second access in hand restores the whole credit.
    _put(game, card_db, "Finale of Devastation", zone="hand")
    st2 = assemble(game, 0, snap)
    assert engine_completion_credit(game, 0, st2, vizier, snap,
                                    spending=finale) == full


def test_a_zero_toughness_x_sink_delivered_to_the_battlefield_is_not_an_access(card_db):
    game = _game(card_db)
    _put(game, card_db, "Green Sun's Zenith", zone="hand")
    _put(game, card_db, "Walking Ballista", zone="library")
    st = _state(game)
    assert not any(a.delivered is not None
                   and a.delivered.name == "Walking Ballista"
                   for a in st.sinks), st.sinks


def test_a_token_maker_scaling_on_something_other_than_mana_is_not_a_sink(card_db):
    from ai.combo_calc import unbounded_mana_sink_reachable
    t = card_db.get_card("Empty the Warrens")
    assert t.has_scaling_token_finisher
    assert is_mana_sink(t) is None

    class _Me:
        hand = [CardInstance(template=t, owner=0, controller=0,
                             instance_id=1, zone="hand")]
        battlefield: list = []
        library: list = []
    assert unbounded_mana_sink_reachable(_Me()) is False


def test_the_first_team_counter_activation_is_offered_and_credited_the_win_swing(card_db):
    from ai.activation_ev import activation_candidates
    from ai.clock import win_swing
    game = _game(card_db)
    leyline = _put(game, card_db, "Leyline of Abundance")
    snap = snapshot_from_game(game, 0)
    offered = [(perm, ev, reason) for perm, _i, _t, ev, reason
               in activation_candidates(game, 0, snap) if perm is leyline]
    assert offered, "the line's first step must be enumerated"
    assert offered[0][1] >= win_swing(snap)


def test_a_board_that_already_reaches_lethal_steps_to_combat_not_to_another_activation(card_db):
    """Once the counters on the board reach lethal through blocks, the
    line's next step is the attack: no further activation is offered
    (the s60500 replay activated forty times past lethal before this)."""
    from ai.activation_ev import activation_candidates
    from ai.assembly_state import (STEP_ATTACK,
                                   team_counter_activations_needed)
    game = _game(card_db)
    leyline = _put(game, card_db, "Leyline of Abundance")
    for c in game.players[0].creatures:
        c.add_plus_counters(20)
    snap = snapshot_from_game(game, 0)
    st = assemble(game, 0, snap)
    assert st.best_line is not None
    assert st.best_line.first_step == (STEP_ATTACK,)
    ab = st.best_line.access.source.template.activated_abilities[
        st.best_line.access.ability_index]
    assert team_counter_activations_needed(game, 0, snap, leyline, ab,
                                           st.mana) == 0
    assert not any(perm is leyline for perm, *_ in
                   activation_candidates(game, 0, snap))


def test_a_team_counter_activation_that_starts_no_line_stays_withheld(card_db):
    from ai.activation_ev import activation_candidates
    game = _game(card_db, opp_blockers=3)
    leyline = _put(game, card_db, "Leyline of Abundance")
    snap = snapshot_from_game(game, 0)
    assert not any(perm is leyline for perm, *_ in
                   activation_candidates(game, 0, snap))


def test_the_cast_that_starts_the_best_line_is_credited_the_win_swing(card_db):
    from ai.clock import win_swing
    from ai.ev_evaluator import compute_play_ev
    game = _game(card_db, opp_blockers=2)
    hoof = _put(game, card_db, "Craterhoof Behemoth", zone="hand")
    snap = snapshot_from_game(game, 0)
    st = assemble(game, 0, snap)
    assert st.best_line and st.best_line.first_step == (STEP_CAST,
                                                        hoof.instance_id)
    without = compute_play_ev(hoof, snap, "combo", game, 0)
    with_line = compute_play_ev(hoof, snap, "combo", game, 0, assembly=st)
    assert with_line - without >= win_swing(snap) - 1e-9


def test_the_attack_declarer_and_the_line_projector_read_the_same_reach(card_db):
    game = _game(card_db)
    valid = game.get_valid_attackers(0)
    assert attack_reach([(c.power or 0, False) for c in valid]) == sum(
        c.power for c in valid if (c.power or 0) > 0)
    # Through blocks: two 4/4 blockers stop the two largest non-tramplers.
    assert attack_reach([(5, False), (3, False), (2, False)], [4, 4],
                        through_blocks=True) == 2
    # A trampler pushes its excess over the blocker's toughness.
    assert attack_reach([(9, True)], [4], through_blocks=True) == 5


def test_the_x_tutor_hold_treats_an_engine_completion_as_acceleration(card_db):
    """Negative control (the design's Dimir T4 board): three lands, the
    self-untapper on board, the replacement in the library beside the
    8-mana payoff. The tutor's budget reaches only the enabler; fetching
    it completes the engine, which is acceleration — the hold must not
    withhold it."""
    from ai.ev_player import EVPlayer
    from ai.scoring_constants import PATIENCE_GATE_REJECT_SENTINEL
    game = _game(card_db, forests=3, engine=False)
    zenith = _put(game, card_db, "Finale of Devastation", zone="hand")
    _put(game, card_db, "Vizier of Remedies", zone="library")
    _put(game, card_db, "Craterhoof Behemoth", zone="library")
    ai = EVPlayer(player_idx=0, deck_name="Creatures Toolbox",
                  rng=random.Random(0))
    ai._init_deck_knowledge(game)
    if ai.goal_engine:
        ai.goal_engine.check_transition(game, 0)
    snap = snapshot_from_game(game, 0)
    ev = ai._score_spell(zenith, snap, game, game.players[0],
                         game.players[1])
    assert ev > PATIENCE_GATE_REJECT_SENTINEL, ev
