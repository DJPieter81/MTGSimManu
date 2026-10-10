"""Evoke is cast from the rules (CR 702.74a, 118.9, 601.2f-h, 105.2).

"Evoke [cost]" means "You may cast this spell by paying [cost] rather than
paying its mana cost" and "When this permanent enters, if its evoke cost
was paid, its controller sacrifices it." The cost is what the card prints:
a mana cost ("Evoke {2}{U}", 29 pool cards) or a card to exile ("Evoke --
Exile a white card from your hand.", 3). "A white card" is a card whose
colour is white (CR 105.2), whatever its type -- not its colour identity.
Evoke grants no flash.

The engine read only the em-dash form, so no mana evoke could be cast; it
exiled a card sharing the spell's colour identity and never a land; it paid
no mana for an evoke; and it decided for the caster: no evoke while the
mana cost was payable, none while the opponent had no creature (the
elementals' targets are their enter triggers', chosen as the trigger is
put on the stack, CR 603.3d), and a card to exile picked -- and the cast
vetoed -- by tag thresholds after the controller had chosen to evoke. The
controller makes those choices (`should_evoke`, `choose_exile_from_hand`);
the engine checks and pays the printed cost.
"""
from __future__ import annotations

import copy
import random

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance, Keyword
from engine.game_state import GameState, Phase
from engine.mana import Color


class _Evoker(DefaultCallbacks):
    """Always evokes; exiles the card `pick` names (default: the first
    candidate), or declines to pay with `pick=None` returning None."""

    def __init__(self, pick=lambda candidates: candidates[0]):
        self.pick = pick
        self.offered = []

    def should_evoke(self, game, player_idx, card):
        return True

    def choose_exile_from_hand(self, game, player_idx, spell, candidates):
        self.offered.append(list(candidates))
        return self.pick(candidates)


def _game(callbacks=None):
    game = GameState(rng=random.Random(0), callbacks=callbacks or _Evoker())
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0, template=None):
    c = CardInstance(template=template or card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _lands(game, card_db, name, n, idx=0):
    return [_put(game, card_db, name, "battlefield", idx) for _ in range(n)]


def _resolve(game):
    while not game.stack.is_empty:
        game.resolve_stack()


# ── The printed cost ──────────────────────────────────────────────────

def test_every_evoke_card_carries_its_printed_evoke_cost(card_db):
    evokers = [t for t in card_db.cards.values()
               if Keyword.EVOKE in t.keywords]
    assert len(evokers) >= 30
    assert [t.name for t in evokers if t.evoke_cost is None] == []
    drifter = card_db.get_card("Mulldrifter")                 # Evoke {2}{U}
    assert (drifter.evoke_cost.generic, drifter.evoke_cost.blue,
            drifter.evoke_cost.cmc) == (2, 1, 3)
    assert drifter.evoke_exile_color is None
    deceit = card_db.get_card("Deceit")                       # {U/B}{U/B}
    assert sorted(deceit.evoke_cost.hybrid) == [("U", "B"), ("U", "B")]
    solitude = card_db.get_card("Solitude")   # Exile a white card ...
    assert solitude.evoke_cost.cmc == 0
    assert solitude.evoke_exile_color is Color.WHITE


# ── The engine pays it ────────────────────────────────────────────────

def test_a_mana_evoke_cost_is_paid_with_mana_and_exiles_no_card(card_db):
    game = _game()
    islands = _lands(game, card_db, "Island", 3)              # {2}{U}
    drifter = _put(game, card_db, "Mulldrifter", "hand")      # {4}{U}
    other_blue = _put(game, card_db, "Opt", "hand")
    for _ in range(3):
        _put(game, card_db, "Island", "library")
    assert game.can_cast(0, drifter)
    assert game.cast_spell(0, drifter)
    assert all(land.tapped for land in islands)
    assert other_blue in game.players[0].hand
    assert game.players[0].exile == []
    _resolve(game)
    assert drifter.zone == "graveyard"                        # sacrificed


def test_an_evoke_cost_exiles_a_card_of_its_colour_whatever_its_type(
        card_db):
    """A green card is a card whose colour is green (CR 105.2): a green
    land qualifies, a red card with green in its colour identity does
    not."""
    game = _game()
    endurance = _put(game, card_db, "Endurance", "hand")
    red = copy.copy(card_db.get_card("Lightning Bolt"))
    red.color_identity = {Color.RED, Color.GREEN}
    _put(game, card_db, "", "hand", template=red)
    assert not game.can_cast(0, endurance)
    arbor = _put(game, card_db, "Dryad Arbor", "hand")        # a green land
    assert game.can_cast(0, endurance)
    assert game.cast_spell(0, endurance)
    assert arbor.zone == "exile" and arbor in game.players[0].exile


def test_evoke_is_the_controllers_choice_whatever_its_mana_and_targets(
        card_db):
    """The spell's mana cost is payable and the opponent has no creature:
    the controller chose to evoke, so the engine evokes."""
    game = _game()
    plains = _lands(game, card_db, "Plains", 5)               # {3}{W}{W}
    solitude = _put(game, card_db, "Solitude", "hand")
    chant = _put(game, card_db, "Orim's Chant", "hand")
    assert game.cast_spell(0, solitude)
    assert chant.zone == "exile"
    assert not any(land.tapped for land in plains)
    _resolve(game)
    assert solitude.zone == "graveyard"


def test_the_controller_picks_the_card_an_evoke_cost_exiles(card_db):
    callbacks = _Evoker(pick=lambda candidates: candidates[1])
    game = _game(callbacks)
    solitude = _put(game, card_db, "Solitude", "hand")
    first = _put(game, card_db, "Orim's Chant", "hand")
    second = _put(game, card_db, "Ephemerate", "hand")
    _put(game, card_db, "Island", "hand")                     # not white
    assert game.cast_spell(0, solitude)
    assert callbacks.offered == [[first, second]]
    assert second.zone == "exile" and first in game.players[0].hand


def test_declining_to_exile_a_card_casts_nothing(card_db):
    game = _game(_Evoker(pick=lambda candidates: None))
    solitude = _put(game, card_db, "Solitude", "hand")
    chant = _put(game, card_db, "Orim's Chant", "hand")
    assert not game.cast_spell(0, solitude)
    assert solitude in game.players[0].hand
    assert chant in game.players[0].hand
    assert game.stack.is_empty


def test_evoke_grants_no_flash(card_db):
    game = _game()
    game.active_player = 1                       # the opponent's turn
    _lands(game, card_db, "Island", 3)
    drifter = _put(game, card_db, "Mulldrifter", "hand")
    assert not game.can_cast(0, drifter)


# ── The AI's pick ─────────────────────────────────────────────────────

def test_the_ai_exiles_the_candidate_worth_least_to_it(card_db):
    """The card a Thoughtseize aimed at this hand would take last."""
    from ai.discard_advisor import choose_card_to_exile_from_hand
    game = _game()
    removal = _put(game, card_db, "Prismatic Ending", "hand")
    chant = _put(game, card_db, "Orim's Chant", "hand")
    walker = _put(game, card_db, "The Wandering Emperor", "hand")
    assert choose_card_to_exile_from_hand(
        game, 0, [removal, chant, walker]) is chant


def test_the_ai_never_exiles_the_last_copy_of_a_role_its_live_plan_needs(
        card_db, monkeypatch):
    """A discard relocates a flashback card within the plan's reach; a
    cost that exiles it does not -- so the plan's last copy is kept, and
    with nothing else to exile the AI declines the cost."""
    import ai.discard_advisor as da
    game = _game()
    souls = _put(game, card_db, "Lingering Souls", "hand")     # flashback
    removal = _put(game, card_db, "Prismatic Ending", "hand")
    monkeypatch.setattr(da, "_plan_role_map",
                        lambda g, i: {"payoffs": {"Lingering Souls"}})
    hand = game.players[0].hand
    assert id(souls) not in da._plan_role_protected_ids(game, 0, hand, True)
    assert da.choose_card_to_exile_from_hand(game, 0, [souls, removal]) \
        is removal
    assert da.choose_card_to_exile_from_hand(game, 0, [souls]) is None


def test_the_ai_does_not_evoke_creature_removal_with_no_opposing_creature(
        card_db):
    """The engine no longer refuses an evoke for its enter trigger's
    targets (they are the trigger's, CR 603.3d); the controller does. A
    removal trigger that targets a creature buys nothing while the
    opponent has none -- their lands, or a creature spell still on the
    stack, are no target."""
    from ai.board_eval import Action, ActionType, evaluate_action
    game = _game(DefaultCallbacks())
    game.active_player = 1
    _lands(game, card_db, "Island", 2, idx=1)
    solitude = _put(game, card_db, "Solitude", "hand")
    _put(game, card_db, "Orim's Chant", "hand")
    evoke = Action(ActionType.EVOKE, {"card": solitude})
    assert evaluate_action(game, 0, evoke) < 0
    _put(game, card_db, "Primeval Titan", "battlefield", idx=1)
    assert evaluate_action(game, 0, evoke) > 0


def test_the_ai_exiles_a_declared_keystone_only_when_nothing_else_can_go(
        card_db, monkeypatch):
    """The strip value prices a creature by its body, so a cheap enabler
    the gameplan declares a keystone would go before a big filler body;
    the declared keystones rank last."""
    import ai.discard_advisor as da
    game = _game()
    enabler = _put(game, card_db, "Shardless Agent", "hand")   # blue, 2/2
    body = _put(game, card_db, "Colossal Skyturtle", "hand")   # blue, 6/5
    monkeypatch.setattr(da, "_declared_keystones",
                        lambda g, i: {"Shardless Agent"})
    assert da.choose_card_to_exile_from_hand(game, 0, [enabler, body]) \
        is body
    assert da.choose_card_to_exile_from_hand(game, 0, [enabler]) is enabler


def test_the_ai_projects_an_evoked_creature_into_the_graveyard(card_db):
    """A cast now that only the evoke cost can pay is an evoke: the
    creature is sacrificed as it enters (CR 702.74a). The projection pays
    the evoke mana and adds no body; with the mana cost payable it is a
    hard cast, and the body stays."""
    from ai.ev_evaluator import _project_spell, snapshot_from_game
    game = _game(DefaultCallbacks())
    _lands(game, card_db, "Island", 3)                         # {2}{U}
    drifter = _put(game, card_db, "Mulldrifter", "hand")        # 2/2
    snap = snapshot_from_game(game, 0)
    evoked = _project_spell(drifter, snap, game=game, player_idx=0)
    assert evoked.my_power == snap.my_power
    assert evoked.my_creature_count == snap.my_creature_count
    assert evoked.my_gy_creatures == snap.my_gy_creatures + 1
    assert evoked.my_mana == snap.my_mana - 3
    _lands(game, card_db, "Island", 2)                         # {4}{U}
    snap = snapshot_from_game(game, 0)
    cast = _project_spell(drifter, snap, game=game, player_idx=0)
    assert cast.my_power == snap.my_power + 2
    assert cast.my_creature_count == snap.my_creature_count + 1
