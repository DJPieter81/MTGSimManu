"""M12-engine — defender chumps when life trajectory forecasts
lethal-next-turn.

Per audits/2026-05-16 Pattern B + Fix 2:
Dimir-vs-Boros G1/G2 — Dimir let through 9 → 4 → -3 across consecutive
combat phases with idle 1/1 chump-blockers available.

Rule pinned (no magic number — all comparisons route through
``ai.clock.life_phase`` / ``ai.clock.life_as_resource``):

  - When ``life_phase(snap) == LifePhase.PANIC`` and an unblocked
    swing reduces my life below my current ``life_as_resource``
    buffer (i.e. my buffer collapses) AND a chumpable token is
    available, the chump becomes mandatory.
  - The block-decision score for a single (attacker, blocker) pair
    is a single numeric:
        ``life_as_resource(my_life_after_block, opp_power_after_block)
         - life_as_resource(my_life_after_no_block, opp_power)``
    No chump/trade/favorable enum — the formula picks the right
    option through ``life_as_resource``'s shape (low life is
    disproportionately valuable).

The fix lifts to every defender — every deck that ever has a token
blocker available (Dimir tokens from Bowmasters, Boros tokens from
Ajani, Living End tokens, Goryo's tokens).
"""
from __future__ import annotations

import random

import pytest


@pytest.fixture(scope="module")
def card_db():
    from engine.card_database import CardDatabase
    return CardDatabase()


def _battlefield(game, card_db, name: str, controller: int):
    """Add ``name`` to ``controller``'s battlefield, return CardInstance."""
    from engine.cards import CardInstance
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _mk_player(idx=0):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=idx, deck_name="Dimir Midrange",
                    rng=random.Random(0))


# ─────────────────────────────────────────────────────────────
# Rule 1 — PANIC life-phase mandates chump
# ─────────────────────────────────────────────────────────────

def test_defender_chumps_when_life_phase_is_panic(card_db):
    """Life 4, attacker 5/5, defender has a 1/1 token. Unblocked, the
    swing is lethal; ``life_phase`` returns ``PANIC`` (we're past the
    development window and our life buffer is strictly less than the
    opponent's). The 1/1 chump becomes mandatory.

    The mechanic phrased without naming a card: PANIC life-phase +
    chumpable creature ⇒ chump assigned.
    """
    from engine.game_state import GameState
    game = GameState(rng=random.Random(0))

    # Defender — 1/1 chump available + a few lands (so we're past the
    # development window per ``is_early_game``).
    chump = _battlefield(game, card_db, "Memnite", controller=0)
    # Opp lands — gives opp a non-trivial board for is_early_game to
    # consider the game past the development window.
    for _ in range(4):
        _battlefield(game, card_db, "Swamp", controller=1)
    for _ in range(4):
        _battlefield(game, card_db, "Plains", controller=0)

    attacker = _battlefield(game, card_db, "Watchwolf", controller=1)
    # Bump Watchwolf to a lethal 5/5 (matches the audit's 9→4→-3 line).
    attacker.temp_power_mod = 2
    attacker.temp_toughness_mod = 2
    assert attacker.power == 5

    me = game.players[0]
    me.life = 4  # at PANIC threshold per life_as_resource math

    # Sanity: the snapshot confirms PANIC.
    from ai.ev_evaluator import snapshot_from_game
    from ai.clock import life_phase, LifePhase
    snap = snapshot_from_game(game, 0)
    assert snap.opp_power >= snap.my_life > 0, (
        f"smoke: PANIC requires opp_power >= my_life (got opp_power="
        f"{snap.opp_power}, my_life={snap.my_life})"
    )
    # am_dead_next == True puts us in LETHAL — which is even *more*
    # urgent than PANIC and the chump must still happen.
    assert life_phase(snap) in (LifePhase.PANIC, LifePhase.LETHAL), (
        f"smoke: expected PANIC or LETHAL, got {life_phase(snap)}"
    )

    player = _mk_player()
    blocks = player.decide_blockers(game, [attacker])

    assert attacker.instance_id in blocks and blocks[attacker.instance_id], (
        f"PANIC + chumpable token ⇒ chump must be assigned. Got "
        f"{blocks}. life={me.life}, opp_power={attacker.power}."
    )
    assert chump.instance_id in blocks[attacker.instance_id], (
        f"expected the 1/1 token (Memnite) to chump the 5/5, got "
        f"{blocks}."
    )


# ─────────────────────────────────────────────────────────────
# Rule 2 — DEVELOP / GRIND life-phase = no forced chump
# ─────────────────────────────────────────────────────────────

def test_defender_doesnt_chump_at_high_life(card_db):
    """Life 18, attacker 5/5, defender has only a high-value 4/4
    blocker (no 1/1 token).  The lifespan-delta formula must say
    lifespan-after-block < lifespan-after-no-block: losing a 4/4 to
    a 5/5 it can't kill costs more buffer than the 5 face damage
    saves at comfortable life.  No block is declared.

    Rule: the single formula must correctly penalise high-value
    chumps — losing a 4/4 to take a 5/5 hit at high life is the
    classic "don't chump with your best creature" mistake.
    """
    from engine.game_state import GameState
    game = GameState(rng=random.Random(0))

    # Defender has only a 4/4 (high-value).  No 1/1 chump available.
    blocker = _battlefield(game, card_db, "Sojourner's Companion",
                           controller=0)
    for _ in range(4):
        _battlefield(game, card_db, "Swamp", controller=1)
    for _ in range(4):
        _battlefield(game, card_db, "Plains", controller=0)

    attacker = _battlefield(game, card_db, "Watchwolf", controller=1)
    attacker.temp_power_mod = 2
    attacker.temp_toughness_mod = 2
    assert attacker.power == 5

    me = game.players[0]
    me.life = 18  # comfortable

    player = _mk_player()
    blocks = player.decide_blockers(game, [attacker])

    assigned = blocks.get(attacker.instance_id, [])
    assert not assigned, (
        f"At 18 life vs a 5/5 with only a 4/4 blocker, the formula "
        f"must say no-block — losing the 4/4 to a 5/5 it can't kill "
        f"costs more buffer than the 5 face damage at comfortable "
        f"life.  Got {blocks}."
    )


# ─────────────────────────────────────────────────────────────
# Rule 3 — favorable trade beats chump
# ─────────────────────────────────────────────────────────────

def test_defender_takes_favorable_trade_over_chump(card_db):
    """Defender has BOTH a 1/1 chump and a 4/4 with strictly better
    block math: the 4/4 SURVIVES the 3/3 attacker AND kills it
    (favorable trade — only opp's creature dies).

    Per the single-formula scoring, ``score_block_assignment`` for
    the favorable-trade block exceeds the chump score: post-block
    opp_power drops to 0 AND my_power is unchanged (blocker lives).
    The 1/1 chump leaves the attacker alive on board.

    No enum branch — the trade emerges from the same formula.
    """
    from engine.game_state import GameState
    game = GameState(rng=random.Random(0))

    chump = _battlefield(game, card_db, "Memnite", controller=0)
    # 4/4 blocker — survives a 3/3 attacker and kills it.
    trader = _battlefield(game, card_db, "Sojourner's Companion",
                          controller=0)
    for _ in range(4):
        _battlefield(game, card_db, "Swamp", controller=1)
    for _ in range(4):
        _battlefield(game, card_db, "Plains", controller=0)

    # Attacker is a 3/3 (vanilla Watchwolf).
    attacker = _battlefield(game, card_db, "Watchwolf", controller=1)
    assert attacker.power == 3

    me = game.players[0]
    me.life = 4

    player = _mk_player()
    blocks = player.decide_blockers(game, [attacker])

    assigned = blocks.get(attacker.instance_id, [])
    assert assigned, f"expected a block at life=4 vs lethal, got {blocks}"
    assert trader.instance_id in assigned, (
        f"expected the 4/4 (Sojourner's Companion) to take the "
        f"favorable trade — it removes the 3/3 attacker AND survives, "
        f"strictly dominating the 1/1 chump. Got blocker_ids="
        f"{assigned}, expected {trader.instance_id} (trader).  "
        f"chump_id={chump.instance_id} would be wrong."
    )


# ─────────────────────────────────────────────────────────────
# Rule 4 — single-numeric scoring (no chump/trade/favorable enum)
# ─────────────────────────────────────────────────────────────

def test_block_outcome_is_a_single_formula_not_an_enum():
    """The scoring helper ``ai.clock.score_block_assignment`` must
    return a float lifespan delta, NOT a string enum label
    ('chump' / 'trade' / 'favorable_trade').  This pins the
    single-formula refactor: the choice between chump / trade is
    driven by the same numeric pipeline as every other block
    decision.
    """
    from ai.clock import score_block_assignment
    from ai.ev_evaluator import EVSnapshot

    snap = EVSnapshot(
        my_life=4, opp_life=20,
        my_power=1, opp_power=5,  # 1/1 chump on my side, 5/5 attacker
        my_creature_count=1, opp_creature_count=1,
        turn_number=5,
    )

    # Chump-style block: 1/1 absorbs the 5-power swing. Attacker dies
    # (the 1/1 deals 1 to a 5-toughness attacker so doesn't kill it,
    # but the *common* chump case is "absorb everything, blocker
    # dies, attacker survives at low life").  Caller passes the
    # post-state to score_block_assignment.
    #
    # Post-chump-block state:
    #   my_life: still 4 (chump absorbed everything)
    #   opp_power: still 5 (attacker survives but is tapped & marked)
    #   my_power: 0 (chump died)
    chump_score = score_block_assignment(
        snap,
        my_life_after=4,
        opp_power_after=5,
        my_power_after=0,
    )
    # Post-no-block state:
    #   my_life: -1 (took 5 to face — dead next turn)
    #   opp_power: 5
    #   my_power: 1 (chump still around but useless: we're dead)
    no_block_score = score_block_assignment(
        snap,
        my_life_after=-1,
        opp_power_after=5,
        my_power_after=1,
    )

    # 1. Single numeric return type — no enum/tuple/string.
    assert isinstance(chump_score, (int, float))
    assert isinstance(no_block_score, (int, float))
    # 2. At PANIC life=4, chumping saves us — chump > no-block.
    assert chump_score > no_block_score, (
        f"PANIC math says chump beats no-block: chump={chump_score}, "
        f"no_block={no_block_score}. life_as_resource(4, 5) ≈ 0.8 "
        f"vs life_as_resource(-1, 5) = -100."
    )


# ─────────────────────────────────────────────────────────────
# Rule 5 — only larger blockers available; no chumpable token
# ─────────────────────────────────────────────────────────────

def test_no_chumpable_creature_dies_to_lethal(card_db):
    """Life 4 (PANIC), attacker 5/5, defender has only a 3/3 and a
    4/4.  Both are non-chump options; the decision must derive from
    the single formula, not from a "must-chump" override.  The 3/3
    is the cheaper trader (loses to 5/5 but takes it out), and the
    4/4 stays back to defend the next swing.  Either way, *some*
    block must be assigned — the lifespan delta of any block is
    > the lifespan delta of taking 5 to face at 4 life
    (lethal_after_no_block).
    """
    from engine.game_state import GameState
    game = GameState(rng=random.Random(0))

    trader_3 = _battlefield(game, card_db, "Watchwolf", controller=0)  # 3/3
    trader_4 = _battlefield(game, card_db, "Sojourner's Companion",
                            controller=0)  # 4/4
    for _ in range(4):
        _battlefield(game, card_db, "Swamp", controller=1)
    for _ in range(4):
        _battlefield(game, card_db, "Plains", controller=0)

    attacker = _battlefield(game, card_db, "Watchwolf", controller=1)
    attacker.temp_power_mod = 2
    attacker.temp_toughness_mod = 2

    me = game.players[0]
    me.life = 4

    player = _mk_player()
    blocks = player.decide_blockers(game, [attacker])

    assigned = blocks.get(attacker.instance_id, [])
    assert assigned, (
        f"life=4 vs 5/5 lethal — must block.  No chumpable token "
        f"available, so the block must come from one of the larger "
        f"creatures, derived from the same scoring formula. "
        f"Got {blocks}."
    )
    # Some creature was assigned.  The decision is derived; we don't
    # pin which one (either 3/3 or 4/4 is correct depending on the
    # life_as_resource shape).  The bug we're guarding against is
    # "no block".
    assert assigned[0] in {trader_3.instance_id, trader_4.instance_id}, (
        f"expected one of the two defenders to block, got {assigned}"
    )
