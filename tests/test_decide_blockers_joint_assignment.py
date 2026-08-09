"""Phase 2b — joint block-assignment (docs/design/rules-foundation-
sweep-tracker.md).

Rule pinned: ``decide_blockers``' emergency path must decide blocking
assignments JOINTLY across all attackers, not via a greedy per-attacker
loop that scores attacker A's block-or-not decision by ASSUMING every
other still-undecided attacker (B, C, …) deals its full damage on top
of A's — an assumption that gets baked in via a per-attacker
``my_life_now`` recomputed from scratch on every iteration.

Root cause, confirmed by empirical reproduction (not assumed from the
historical writeup — see the git-stash-verified red run in this
commit): when two-plus attackers are each individually non-lethal but
JOINTLY lethal, the old ``my_life_now`` formula for the FIRST attacker
processed subtracts every OTHER (still fully unblocked) attacker's
power from the current life total — driving ``my_life_now`` deeply
negative. Both the "block" and "no-block" hypothetical post-states
then read as already-dead (``ai.clock.life_as_resource`` floors at
-100 for life <= 0), so the block-vs-no-block delta is exactly 0 —
which fails the strict ``delta > 0`` selection threshold. This repeats
for every attacker, so the ENTIRE emergency pass silently selects no
blocks at all, ``emergency_blocks`` stays empty, and the function
falls through — ungated — into the non-emergency path below, which
scores against a static ``me.life`` and greedily spends BOTH available
blockers double-blocking the first attacker for a "clean kill" while
leaving the second attacker (individually just as dangerous) with
zero blockers. Two attackers, two identical blockers, and the
defending player dies to damage that was trivially preventable by
single-blocking each attacker once.

Class size: any deck with 2+ chump-capable blockers facing 2+
simultaneously-threatening attackers where neither attacker is lethal
ALONE but their sum is — every creature deck in the format hits this
shape on some turn. Card names appear only in fixture setup.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState


def _battlefield(game, db, name: str, controller: int) -> CardInstance:
    tmpl = db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def test_two_jointly_lethal_attackers_both_get_blocked(card_db):
    """Deterministic reproduction of audited bug #4 (Eldrazi Tron vs
    Amulet Titan, seed 55505's shape, minimized): defender at life=8
    faces two 8/8 attackers — neither alone is lethal-this-turn by
    itself once blocked, but leaving EITHER fully unblocked is fatal
    given the other's damage. Defender holds exactly two blockers,
    each capable of single-blocking one attacker (absorbing its full
    damage — neither can kill an 8-toughness attacker alone, so this
    also exercises the audited chump-with-real-value shape, not just
    a clean kill).

    Correct joint assignment: one blocker per attacker, survives at
    full life. The old greedy/sequential code instead spent BOTH
    blockers double-blocking ONE attacker for a "clean kill" (4+4=8
    power meets the 8 toughness) while leaving the second attacker's
    8 damage completely unanswered — defender dies (8 life - 8
    unblocked damage = 0).
    """
    game = GameState(rng=random.Random(0))
    game.players[0].life = 8
    game.players[1].life = 20

    blockers = [
        _battlefield(game, card_db, "Sojourner's Companion", 0)
        for _ in range(2)
    ]
    attackers = []
    for _ in range(2):
        a = _battlefield(game, card_db, "Watchwolf", 1)
        a.temp_power_mod = 8 - (a.template.power or 0)
        a.temp_toughness_mod = 8 - (a.template.toughness or 0)
        attackers.append(a)
    assert all(a.power == 8 and a.toughness == 8 for a in attackers), (
        "fixture setup: both attackers must present as 8/8"
    )

    player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                      rng=random.Random(0))
    blocks = player.decide_blockers(game, attackers)

    blocked_attacker_ids = set(blocks.keys())
    unblocked = [a for a in attackers if a.instance_id not in blocked_attacker_ids]
    unblocked_damage = sum(a.power or 0 for a in unblocked)
    final_life = game.players[0].life - unblocked_damage

    assert len(blocked_attacker_ids) == 2, (
        f"both jointly-lethal attackers must be blocked (one blocker "
        f"each) — a single blocker per attacker is sufficient to "
        f"survive at full life. Got blocks={blocks}, "
        f"unblocked={[a.instance_id for a in unblocked]}."
    )
    assert final_life > 0, (
        f"defender must survive this combat — both attackers were "
        f"individually blockable with the two available blockers. "
        f"Got final_life={final_life} (blocks={blocks})."
    )
    # No blocker should be spent twice on the SAME attacker when a
    # single-block-each assignment fully covers both attackers — that
    # double-commitment is exactly what starved the second attacker
    # of a blocker under the old greedy code.
    for atk_id, blocker_ids in blocks.items():
        assert len(blocker_ids) == 1, (
            f"attacker {atk_id} was assigned {len(blocker_ids)} "
            f"blockers ({blocker_ids}) while a sibling attacker got "
            f"none — that's the audited double-block-starves-a-"
            f"sibling-attacker bug. blocks={blocks}."
        )


def test_favorable_clean_kill_trade_still_happens_when_survival_not_compromised(card_db):
    """Regression bound on the fix above: a genuinely favorable trade
    (a blocker that SURVIVES and kills the attacker) must still be
    selected over a merely-adequate chump when picking it doesn't
    compromise survival. This is the "swap in favorable/clean-kill
    trades" half of the two-pass design — pass 1's cheapest-to-lose
    coverage pick (a vanilla 1/1) must get upgraded to the dominant
    option (a 4/4 that kills the 3/3 attacker and lives) once survival
    is already secured for that attacker.
    """
    game = GameState(rng=random.Random(0))
    game.players[0].life = 4
    game.players[1].life = 20

    chump = _battlefield(game, card_db, "Memnite", 0)
    trader = _battlefield(game, card_db, "Sojourner's Companion", 0)
    attacker = _battlefield(game, card_db, "Watchwolf", 1)
    assert attacker.power == 3 and attacker.toughness == 3, (
        "fixture setup: vanilla Watchwolf must be 3/3"
    )

    player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                      rng=random.Random(0))
    blocks = player.decide_blockers(game, [attacker])

    assigned = blocks.get(attacker.instance_id, [])
    assert assigned, f"expected a block at life=4 vs lethal, got {blocks}"
    assert trader.instance_id in assigned, (
        f"expected the 4/4 (Sojourner's Companion) — it kills the 3/3 "
        f"AND survives, strictly dominating the 1/1 chump. Got "
        f"blocker_ids={assigned}; chump_id={chump.instance_id} would "
        f"be the wrong (merely survival-adequate) pick."
    )
    assert chump.instance_id not in assigned, (
        f"the dominated 1/1 chump must not remain assigned once the "
        f"strictly-better trader is available and unused. Got "
        f"{assigned}."
    )
