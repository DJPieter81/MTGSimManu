"""A cast-triggered counter is read under the mana the cast's route spends
(CR 603.2, 701.6, 601.2f-h, 118.9).

"If no mana was spent to cast it, counter that spell" reads what the cast
paid. The route its caster pays it by decides that:
- an evoke cost that exiles a card spends no mana (CR 702.74a);
- an alternative cost that exiles a card from hand spends none (CR 118.9);
- a graveyard cast pays its route's cost, a flashback that sacrifices a land
  none (CR 702.33a);
- a spell its reductions bring to {0} spends none (CR 601.2f);
- a cascade hit is cast without paying its mana cost (CR 702.85a).

The defect: the AI read the engine's counter predicate as if every cast were
an ordinary one paying its mana value, so it evoked, pitched, flashed back,
cascaded and cast free spells into a Vexing Bauble. A combat trick was also
read as cast by the opposing player.

The route is the AI's cost estimate's (`ai.effective_cmc.cast_mode_of`): an
ordinary cast when the mana cost can be paid, else an evoke, else a pitch,
and a graveyard cast pays its route's cost. The read is
`ai.effective_cmc.counter_on_cast`, and the route choices (`should_evoke`,
`should_exile_instead_of_paying`) decline a route the counter names.

Class: the routes that can spend no mana:
- 3 evoke-by-exile creatures;
- 6 exile alternative costs;
- 14 all-generic affinity spells;
- 3 flashback costs with no mana;
- 18 cascade cards, whose hits are free.
They are read against the 4 cast-triggered counters with a mana condition.
Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(active=0):
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = active
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _read(game, card, idx=0, **kw):
    from ai.effective_cmc import counter_on_cast
    return counter_on_cast(card, game=game, player_idx=idx, **kw)


def _solitude_board(card_db, *, plains=0, bauble=True):
    """Me (0): Solitude and a white card to exile; an opposing 6/6 to aim
    at. Opponent (1): a no-mana counter."""
    game = _game()
    for _ in range(plains):
        _put(game, card_db, "Plains", "battlefield", 0)
    solitude = _put(game, card_db, "Solitude", "hand", 0)
    _put(game, card_db, "Thraben Inspector", "hand", 0)
    _put(game, card_db, "Primeval Titan", "battlefield", 1)
    bauble = (_put(game, card_db, "Vexing Bauble", "battlefield", 1)
              if bauble else None)
    return game, solitude, bauble


# ── The read: what the route spends ───────────────────────────────────

def test_an_evoke_paid_by_exile_is_read_as_countered_by_a_no_mana_counter(card_db):
    game, solitude, bauble = _solitude_board(card_db)
    assert _read(game, solitude) is bauble


def test_the_same_creature_hard_cast_spends_mana_and_is_not(card_db):
    game, solitude, _ = _solitude_board(card_db, plains=5)
    assert _read(game, solitude) is None


def test_a_pitch_whose_mana_cost_cannot_be_paid_is_read_as_countered(card_db):
    game = _game(active=1)                               # not the caster's turn
    force = _put(game, card_db, "Force of Negation", "hand", 0)
    _put(game, card_db, "Opt", "hand", 0)                # a blue card to exile
    bauble = _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    assert _read(game, force) is bauble


def test_a_spell_its_reductions_bring_to_zero_is_read_as_countered(card_db):
    game = _game()
    for _ in range(4):
        _put(game, card_db, "Ornithopter", "battlefield", 0)
    frogmite = _put(game, card_db, "Frogmite", "hand", 0)     # affinity: {0}
    bauble = _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    assert _read(game, frogmite) is bauble


def test_a_reduction_that_leaves_a_coloured_pip_still_spends_mana(card_db):
    game = _game()
    _put(game, card_db, "Mountain", "battlefield", 0)
    _put(game, card_db, "Ruby Medallion", "battlefield", 0)
    ritual = _put(game, card_db, "Desperate Ritual", "hand", 0)  # {1}{R} -> {R}
    _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    assert _read(game, ritual) is None


def test_a_flashback_that_sacrifices_a_land_spends_no_mana(card_db):
    game = _game()
    _put(game, card_db, "Mountain", "battlefield", 0)
    from engine.cast_manager import printed_flashback
    dart = _put(game, card_db, "Lava Dart", "graveyard", 0)
    dart.has_flashback = printed_flashback(dart.template)    # as game setup sets it
    bauble = _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    assert _read(game, dart) is bauble
    in_hand = _put(game, card_db, "Lava Dart", "hand", 0)    # {R} from hand
    assert _read(game, in_hand) is None


def test_a_free_cast_is_read_as_countered_and_a_mana_value_lock_still_reads_its_value(
        card_db):
    from ai.effective_cmc import CAST_MODE_FREE
    game = _game()
    living_end = _put(game, card_db, "Living End", "hand", 0)
    bauble = _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    assert _read(game, living_end, cast_mode=CAST_MODE_FREE) is bauble
    game2 = _game()
    bolt = _put(game2, card_db, "Lightning Bolt", "hand", 0)
    chalice = _put(game2, card_db, "Chalice of the Void", "battlefield", 1)
    chalice.other_counters["charge"] = 1
    assert _read(game2, bolt, cast_mode=CAST_MODE_FREE) is chalice


def test_with_no_cast_counter_in_play_nothing_is_read(card_db):
    game, solitude, _ = _solitude_board(card_db, bauble=False)
    assert _read(game, solitude) is None


# ── The route choices decline a route the counter names ───────────────

def _evokes(game, card):
    from ai.board_eval import Action, ActionType, evaluate_action
    return evaluate_action(game, 0, Action(ActionType.EVOKE, {"card": card})) > 0


def test_the_evoke_choice_declines_an_evoke_a_counter_names(card_db):
    game, solitude, _ = _solitude_board(card_db, bauble=False)
    assert _evokes(game, solitude)                       # it would evoke
    game, solitude, _ = _solitude_board(card_db)
    assert not _evokes(game, solitude)


def test_the_pitch_choice_declines_a_pitch_a_counter_names(card_db):
    from ai.discard_advisor import exile_instead_of_paying
    game = _game(active=1)
    force = _put(game, card_db, "Force of Negation", "hand", 0)
    _put(game, card_db, "Opt", "hand", 0)
    assert exile_instead_of_paying(game, 0, force, can_pay_mana=False)
    _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    assert not exile_instead_of_paying(game, 0, force, can_pay_mana=False)


# ── Every cast path reads it ──────────────────────────────────────────

def test_a_spell_its_reductions_make_free_is_not_a_main_phase_candidate(card_db):
    from ai.ev_player import EVPlayer
    game = _game()
    for _ in range(4):
        _put(game, card_db, "Ornithopter", "battlefield", 0)
    frogmite = _put(game, card_db, "Frogmite", "hand", 0)
    _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    ai = EVPlayer(player_idx=0, deck_name="Affinity", rng=random.Random(0))
    decision = ai.decide_main_phase(game)
    assert decision is None or decision[1] is not frogmite, decision


def test_a_countered_pitch_is_not_a_response_candidate(card_db):
    from ai.response import ResponseDecider
    from ai.turn_planner import TurnPlanner
    from engine.stack import StackItem, StackItemType
    game = _game(active=1)
    force = _put(game, card_db, "Force of Negation", "hand", 0)
    _put(game, card_db, "Opt", "hand", 0)
    _put(game, card_db, "Vexing Bauble", "battlefield", 1)
    spell = CardInstance(template=card_db.get_card("Wrath of God"), owner=1,
                         controller=1, instance_id=game.next_instance_id(),
                         zone="stack")
    spell._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=spell,
                     controller=1, targets=[])
    game.stack.push(item)
    decider = ResponseDecider(0, TurnPlanner(), strategic_logger=None)
    assert force not in decider._enumerate_response_candidates(game, item)


def test_the_instant_window_does_not_evoke_into_a_counter(card_db):
    from ai.ev_player import EVPlayer
    from engine.game_runner import AICallbacks, GameRunner
    game, solitude, _ = _solitude_board(card_db)
    game.active_player = 1
    game.callbacks = AICallbacks()
    responder = EVPlayer(player_idx=0, deck_name="Azorius Control",
                         rng=random.Random(0))
    active = EVPlayer(player_idx=1, deck_name="Amulet Titan",
                      rng=random.Random(0))
    GameRunner(card_db=card_db)._cast_instant_removal(
        game, responder, active, context="pre_combat", max_instants=3)
    assert solitude in game.players[0].hand, (
        "Solitude must not be evoked into the Vexing Bauble")


def test_a_combat_trick_is_read_as_cast_by_its_own_controller(card_db):
    from engine.combat_manager import CombatManager
    from ai.ev_player import EVPlayer
    def trick(jin):
        game = _game()
        game.players[1].life = 3
        _put(game, card_db, "Mountain", "battlefield", 0)
        bears = _put(game, card_db, "Grizzly Bears", "battlefield", 0)  # unblocked
        rage = _put(game, card_db, "Monstrous Rage", "hand", 0)         # +2/+0
        if jin:
            # "Whenever an opponent casts an artifact, instant, or sorcery
            # spell, counter that spell": the trick's caster is that opponent.
            _put(game, card_db, "Jin-Gitaxias, Progress Tyrant", "battlefield", 1)
        cm = CombatManager()
        game.current_phase = Phase.DECLARE_ATTACKERS
        cm.declare_attackers(game, [bears], 0)
        game.current_phase = Phase.DECLARE_BLOCKERS
        cm.declare_blockers(game, {})
        ai = EVPlayer(player_idx=0, deck_name="Izzet Prowess", rng=random.Random(0))
        return ai.decide_combat_trick(game, cm), rage, bears

    chosen, rage, bears = trick(False)
    assert chosen == (rage, [bears.instance_id])        # the lethal pump
    chosen, _, _ = trick(True)
    assert chosen is None


# ── The cascade projection reads its hit's free cast ──────────────────

def test_a_cascade_projection_credits_no_hit_a_counter_counters(card_db):
    from ai.ev_evaluator import _project_spell, snapshot_from_game

    def board(bauble):
        game = _game()
        for _ in range(3):
            _put(game, card_db, "Mountain", "battlefield", 0)
        for _ in range(4):
            _put(game, card_db, "Grizzly Bears", "library", 0)   # MV 2 hits
        if bauble:
            _put(game, card_db, "Vexing Bauble", "battlefield", 1)
        return game, _put(game, card_db, "Bloodbraid Elf", "hand", 0)

    game, elf = board(False)
    snap = snapshot_from_game(game, 0)
    with_hit = _project_spell(elf, snap, None, game, 0)
    game, elf = board(True)
    snap = snapshot_from_game(game, 0)
    without = _project_spell(elf, snap, None, game, 0)
    assert with_hit.my_creature_count > without.my_creature_count
    assert without.my_creature_count == snap.my_creature_count + 1  # the elf
