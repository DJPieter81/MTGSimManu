"""A removal spell is valued by the targets it can take: its board
projection, its threat premium and its target picker read one candidate
set (`ai.removal_reach`).

A removal spell's target is chosen among the permanents its requirement
admits (CR 601.2c), within the mana value it can reach (CR 601.2b: X
before targets; a printed or resolution-checked bound; converge's
colours), that it may target past ward (CR 702.21a).

The defect: the projection removed the opponent's most threatening
creature whatever the spell's reach, while the picker aimed at what it
could take. Beside a mana value 7 creature, a "mana value 2 or less"
removal spell scored +13.5 and was cast at a 0/1 Eldrazi Spawn token
(Dimir Midrange vs Eldrazi Tron s50000, T16). In 48 Eldrazi Tron games
opponents spent about 16 targeted removal spells on Spawn tokens.

Card names are fixture carriers: every bounded removal spell (a mana-value
condition, converge, X) reads the candidate set.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _spawn(game, card_db, controller, count):
    """Eldrazi Spawn tokens, built by the token owner from a typed spec."""
    from engine import effect_carrier
    from engine.effect_spec import Verb, iter_specs
    host = effect_carrier.chosen_mode_host(card_db.get_card("Kozilek's Command"), 0)
    spec = next(s for s in iter_specs(host.specs) if s.verb is Verb.CREATE_TOKEN)
    return game.create_token(controller, "", count=count, token_spec=spec.payload)


def _board(card_db, *opposing, spawn=0):
    """Player 0 holds a "mana value 2 or less" removal spell with mana to
    cast it, in its own main phase."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(3):
        _add(game, card_db, "Swamp", 0, "battlefield")
    for name in opposing:
        _add(game, card_db, name, 1, "battlefield")
    tokens = _spawn(game, card_db, 1, spawn) if spawn else []
    for _ in range(5):
        _add(game, card_db, "Island", 1, "library")
        _add(game, card_db, "Swamp", 0, "library")
    _add(game, card_db, "Island", 1, "hand")
    push = _add(game, card_db, "Fatal Push", 0, "hand")
    return game, push, tokens


def _projected_power_removed(game, spell):
    from ai.ev_evaluator import _project_spell, snapshot_from_game
    snap = snapshot_from_game(game, 0)
    after = _project_spell(spell, snap, game=game, player_idx=0)
    return snap.opp_power - after.opp_power


def test_the_projection_removes_a_creature_the_spell_can_take(card_db):
    game, push, _ = _board(card_db, "Grizzly Bears", "Devourer of Destiny")
    assert _projected_power_removed(game, push) == 2, \
        "the bears (mana value 2), not the 6/6 of mana value 7"


def test_a_creature_beyond_the_spells_reach_is_not_projected_off(card_db):
    game, push, _ = _board(card_db, "Devourer of Destiny", spawn=4)
    assert _projected_power_removed(game, push) == 0, \
        "only a 0/1 token is in reach"


def test_removal_is_scored_by_its_reachable_target_not_an_untouchable_one(card_db):
    """The cast's value does not rise because a creature it cannot touch
    sits beside its reachable target: the spell is scored as if only what
    it can take were there (the projection reads the reach)."""
    from ai.ev_evaluator import snapshot_from_game
    from ai.ev_player import EVPlayer

    def score(game, push):
        ai = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                      rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        return ai._score_spell(push, snap, game, game.players[0],
                               game.players[1])
    beside, push, _ = _board(card_db, "Devourer of Destiny", spawn=4)
    bears, push_b, _ = _board(card_db, "Grizzly Bears")
    assert score(beside, push) < score(bears, push_b), \
        "a 0/1 token in reach is worth less than a 2/2 in reach"


def test_the_picker_and_the_projection_read_one_candidate_set(card_db):
    from ai.ev_player import EVPlayer
    from ai.removal_reach import removal_candidates
    game, push, tokens = _board(card_db, "Devourer of Destiny", spawn=2)
    opp = game.players[1]
    reach = removal_candidates(game, 0, push, opp.creatures)
    assert set(map(id, reach)) == set(map(id, tokens))
    ai = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                  rng=random.Random(0))
    assert all(game.get_card_by_id(t) in reach
               for t in ai._choose_targets(game, push))


def test_a_spell_on_the_stack_reaches_the_x_it_was_cast_with(card_db):
    """X is chosen as the spell is cast (CR 601.2b): an X-bound removal
    mode on the stack reaches its paid X, however little mana its caster
    has left -- the value an opponent weighs when it decides to answer."""
    from ai.removal_reach import removal_candidates
    from engine.stack import StackItem, StackItemType
    game = GameState(rng=random.Random(0))
    game.active_player = 1
    ragavan = _add(game, card_db, "Ragavan, Nimble Pilferer", 0, "battlefield")
    command = _add(game, card_db, "Kozilek's Command", 1, "hand")
    game.players[1].hand.remove(command)
    command.zone = "stack"
    game.stack.push(StackItem(item_type=StackItemType.SPELL, source=command,
                              controller=1, targets=[ragavan.instance_id],
                              x_value=3))
    assert removal_candidates(game, 1, command, [ragavan]) == [ragavan], \
        "mana value 1 is within the paid X of 3; no mana is left to pay more"
