"""A rejected cast must be excluded and the phase re-planned, not ended.

Root cause (aggro-defense scan, seed 40000): `can_cast` can green-light a
play that `cast_spell` then refuses (e.g. evoke pitch-value veto, flashback
reanimation gate). The main-phase loop retried the SAME top-scored card once
and then BROKE the entire phase — so a control deck whose best play is
un-executable develops nothing and dies, and Goryo's locks itself out of its
own combo. `decide_main_phase` already accepts an `excluded_cards` set; the
runner just never used it.

Rule under test: when `cast_spell` rejects the chosen card, the runner adds it
to the excluded set and re-plans, casting the next affordable play in the same
main phase. Deck-agnostic; gates on cast-failure, not card names.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from ai.ev_player import EVPlayer

_DB = CardDatabase()


def _add(game, name, controller, zone):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def test_failed_cast_excludes_card_and_replans():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 3
    p = game.players[0]
    p.deck_name = "Domain Zoo"
    game.players[1].deck_name = "Dimir Midrange"

    # Untapped mana already in play; two distinct castable 1-drops in hand.
    for _ in range(3):
        _add(game, "Stomping Ground", 0, "battlefield")
    a = _add(game, "Wild Nacatl", 0, "hand")
    b = _add(game, "Ragavan, Nimble Pilferer", 0, "hand")

    ai0 = EVPlayer(player_idx=0, deck_name="Domain Zoo", rng=random.Random(0))
    ai1 = EVPlayer(player_idx=1, deck_name="Dimir Midrange", rng=random.Random(0))
    runner = GameRunner.__new__(GameRunner)

    attempted = []
    real_cast = game.cast_spell
    reject_id = {"v": None}

    def fake_cast(pidx, card, targets=None):
        attempted.append(card.instance_id)
        # Reject the FIRST distinct card the AI attempts; accept the rest.
        if reject_id["v"] is None:
            reject_id["v"] = card.instance_id
        if card.instance_id == reject_id["v"]:
            return False
        # Accept: move to battlefield so it isn't re-picked.
        if card in p.hand:
            p.hand.remove(card)
            card.zone = "battlefield"
            card.enter_battlefield()
            p.battlefield.append(card)
        return True

    game.cast_spell = fake_cast
    try:
        runner._execute_main_phase(game, ai0, ai1)
    finally:
        game.cast_spell = real_cast

    distinct = set(attempted)
    assert len(distinct) >= 2, (
        f"after the first card was rejected the runner must exclude it and "
        f"attempt a different play; only attempted {distinct}")
    # The accepted (second) card actually resolved onto the battlefield.
    assert any(c.instance_id != reject_id["v"] and c.zone == "battlefield"
               for c in p.battlefield if getattr(c, "is_token", False) is False
               and c.instance_id in {a.instance_id, b.instance_id}), (
        "the non-rejected 1-drop should have been cast in the same phase")
