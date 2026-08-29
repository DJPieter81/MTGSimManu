"""An activation cost that removes a permanent from the board must be
charged EVERY snapshot resource that permanent was contributing.

`ai.activation_ev.activation_candidates` projects an activation forward
by editing the `EVSnapshot` and taking the `position_value` delta. A cost
that consumes a permanent — sacrifice-self, sacrifice-another,
exile-self — therefore has to subtract that permanent's contribution
first, or the activation is priced as free.

Lands (`my_mana`) and creatures (`my_power`) were already charged. The
remaining fields `position_value` reads for a permanent are the ARTIFACT
and ENCHANTMENT counts, and those were not — so a deck that genuinely
scales with artifact count (affinity cost reduction, "+1/+0 per
artifact" equipment, metalcraft) read cracking one of its own artifacts
as costless. That is exactly the board such a deck must not eat, and it
is reachable the moment any artifact carries a payable activation with a
classified effect — a graveyard-hate artifact being the immediate case.

The rule is the mechanic, not the card: whatever the consumed permanent
contributed to the snapshot, the projection stops counting.
"""
from __future__ import annotations

import random

from ai.activation_ev import activation_candidates
from ai.ev_evaluator import snapshot_from_game
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.mana import ManaCost

_DB = CardDatabase()


def _add(game, name, controller=0, zone="battlefield"):
    tmpl = _DB.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    for _ in range(4):
        _add(game, "Swamp")
    return game


def _draw_engine(game, host_name, sacrifice_self, amount=1):
    """A "[cost]: Draw N" line on a real permanent — the simplest
    already-classified effect, used so the test measures the COST half."""
    ability = ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(), tap_self=True,
                            sacrifice_self=sacrifice_self),
        effect_text=f"Draw {amount} cards",
        effect_kind=ActivationEffectKind.DRAW_N, amount=amount)
    perm = _add(game, host_name)
    perm.template = perm.template.__class__(**{
        **{f: getattr(perm.template, f)
           for f in perm.template.__dataclass_fields__},
        'activated_abilities': [ability]})
    return perm


def _cands(game, perm):
    snap = snapshot_from_game(game, 0)
    return [c for c in activation_candidates(game, 0, snap)
            if c[0].instance_id == perm.instance_id]


def _ev(game, perm):
    got = _cands(game, perm)
    assert got, "the activation must be enumerated at all"
    return got[0][3]


def _artifact_board(sacrifice_self, amount=1):
    """Identical boards on which artifact count actually scores —
    Cranial Plating makes `my_artifact_scaling_active` true."""
    game = _game()
    _add(game, "Cranial Plating")
    for _ in range(3):
        _add(game, "Ornithopter")
    return game, _draw_engine(game, "Springleaf Drum", sacrifice_self,
                              amount)


def test_consuming_an_artifact_from_a_scaling_board_is_not_free():
    """A cantrip is not worth an artifact to a deck whose artifacts are
    its engine. Keeping the source is enumerated; eating it is not."""
    keep_game, keeper = _artifact_board(sacrifice_self=False)
    eat_game, eater = _artifact_board(sacrifice_self=True)

    assert snapshot_from_game(eat_game, 0).my_artifact_scaling_active, (
        "fixture must actually have artifact scaling live")
    assert _cands(keep_game, keeper), (
        "the same line that keeps its source is worth taking")
    assert not _cands(eat_game, eater), (
        "an activation that eats one of our own scaling artifacts for a "
        "single card must not be enumerated")


def test_the_charge_is_exactly_the_count_the_permanent_contributed():
    """Not a tuned penalty: the price of eating the artifact is exactly
    the `position_value` difference its own count made. Sized so both
    lines still clear zero, or the comparison would measure the
    suppression instead of the charge."""
    from ai.clock import position_value

    big = 10   # enough card draw that the artifact charge does not veto
    keep_game, keeper = _artifact_board(sacrifice_self=False, amount=big)
    eat_game, eater = _artifact_board(sacrifice_self=True, amount=big)

    snap = snapshot_from_game(keep_game, 0)
    drew = snap.fast_replace(my_hand_size=snap.my_hand_size + big)
    ate = drew.fast_replace(my_artifact_count=snap.my_artifact_count - 1)
    expected = position_value(drew) - position_value(ate)
    assert expected > 0, "fixture must make the artifact count matter"

    assert _ev(keep_game, keeper) - _ev(eat_game, eater) == expected
