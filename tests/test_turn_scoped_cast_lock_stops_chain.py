"""Turn-scoped cast-lock (silence class): enforced by the engine,
enumerable as a response, fired against a mid-cast chain.

Rules (mechanic-phrased, no card names in the rules):

    1. ENGINE — a player under a turn-scoped cast-lock
       (`silenced_this_turn`) cannot cast spells for the rest of the
       turn.  The effect layer already sets the flag from oracle text
       ("[target player] can't cast spells this turn"); the cast
       gate must read it, or the mechanic is a no-op.

    2. ENUMERATION — a silence-tagged instant-speed card in hand is a
       legal RESPONSE candidate.  It is the anti-chain answer class:
       resolving it denies every remaining cast of the opponent's
       turn.  Prior to this fix the response enumerator surfaced only
       counter / pitch / removal / discard / channel candidates, so a
       control deck's dedicated anti-combo interaction was a dead
       card in the sim ("No castable instants in hand" recorded with
       a castable silence instant in hand — 2026-07-05 storm
       overshoot diagnostic, Candidate 2b).

    3. DECISION — against MID-CAST chain fuel (bottleneck_probability
       == 0.0: the opponent has committed >=1 prior cast this turn
       and a payoff is reachable), a silence response FIRES instead
       of being chain-held.  The hold's polarity is inverted for this
       class: a counter is at its best against the payoff, but a
       cast-lock is at its best the EARLIER it lands — every spell it
       denies is chain EV removed, and it never trades with the
       payoff at all.  Outside a mid-cast chain the silence stays
       held (it would deny nothing).

Class size: every turn-scoped cast-lock instant (tag-driven from the
"can't cast spells" oracle clause) held by every reactive deck vs
every chain deck (storm, cascade, ritual combo).  Lift-check: the
same rule arms the class for any deck that boards it vs any
spell-velocity archetype; no deck-specific knowledge involved.
"""
from __future__ import annotations

import random

from ai.response import ResponseDecider
from ai.turn_planner import TurnPlanner
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


def _add_to_zone(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "library":
        game.players[controller].library.append(card)
    return card


def _put_on_stack(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="stack",
    )
    card._game_state = game
    item = StackItem(
        item_type=StackItemType.SPELL,
        source=card,
        controller=controller,
        targets=[],
    )
    game.stack.push(item)
    return item


# ─────────────────────────────────────────────────────────────────────
# Rule 1 — engine enforcement
# ─────────────────────────────────────────────────────────────────────


def test_cast_lock_blocks_casting_for_the_rest_of_the_turn(card_db):
    """A player whose `silenced_this_turn` flag is set cannot cast a
    castable spell from hand.  Pre-fix: the flag was set by the
    effect layer but read by nothing — the cast-lock mechanic was
    unenforced and the anti-chain interaction class did nothing even
    when cast.
    """
    game = GameState(rng=random.Random(0))
    idx = 0
    game.active_player = idx
    for _ in range(3):
        _add_to_zone(game, card_db, "Mountain", idx, "battlefield")
    spell = _add_to_zone(game, card_db, "Pyretic Ritual", idx, "hand")

    assert game.can_cast(idx, spell), (
        "Sanity: the spell is castable before the lock"
    )
    game.players[idx].silenced_this_turn = True
    assert not game.can_cast(idx, spell), (
        "A turn-scoped cast-lock must make can_cast return False for "
        "the locked player"
    )


def test_cast_lock_expires_on_turn_reset(card_db):
    """The lock is turn-scoped: after the player's per-turn reset the
    spell is castable again (no carryover beyond the documented
    silenced_next_turn hand-off).
    """
    game = GameState(rng=random.Random(0))
    idx = 0
    game.active_player = idx
    for _ in range(3):
        _add_to_zone(game, card_db, "Mountain", idx, "battlefield")
    spell = _add_to_zone(game, card_db, "Pyretic Ritual", idx, "hand")

    game.players[idx].silenced_this_turn = True
    assert not game.can_cast(idx, spell)
    game.players[idx].reset_turn_tracking()
    assert game.can_cast(idx, spell), (
        "After the turn reset the cast-lock must have expired"
    )


# ─────────────────────────────────────────────────────────────────────
# Rules 2+3 — enumeration and chain-aware firing
# ─────────────────────────────────────────────────────────────────────


def _chain_game(card_db, *, spells_cast_this_turn):
    """Combo opponent with a reachable payoff; defender holds a
    silence-tagged instant with mana open.  Production semantics for
    ``spells_cast_this_turn`` (includes the stack spell).
    """
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    game.priority_player = defender_idx
    game.turn_number = 4

    for _ in range(2):
        _add_to_zone(game, card_db, "Plains", defender_idx, "battlefield")
    for _ in range(3):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "battlefield")

    silence = _add_to_zone(game, card_db, "Orim's Chant", defender_idx,
                           "hand")

    _add_to_zone(game, card_db, "Wish", attacker_idx, "hand")
    _add_to_zone(game, card_db, "Grapeshot", attacker_idx, "library")
    for _ in range(20):
        _add_to_zone(game, card_db, "Mountain", attacker_idx, "library")

    game.players[attacker_idx].spells_cast_this_turn = spells_cast_this_turn
    game.players[attacker_idx].deck_name = "Ruby Storm"
    game.players[defender_idx].deck_name = "Azorius Control"

    item = _put_on_stack(game, card_db, "Wrenn's Resolve", attacker_idx)
    return game, item, defender_idx, silence


def test_cast_lock_instant_is_an_enumerable_response(card_db):
    """A silence-tagged instant in hand with mana available must
    appear in the response candidate set.  Pre-fix the enumerator
    dropped it (not counter / pitch / removal / discard / channel)
    and decide_response recorded 'No castable instants in hand'.
    """
    game, item, defender_idx, silence = _chain_game(
        card_db, spells_cast_this_turn=3)
    decider = ResponseDecider(defender_idx, TurnPlanner(),
                              strategic_logger=None)
    candidates = decider._enumerate_response_candidates(game, item)
    assert any(c.instance_id == silence.instance_id for c in candidates), (
        "Silence-tagged instant must be an enumerable response "
        f"candidate; got {[c.name for c in candidates]}"
    )


def test_cast_lock_fires_on_midcast_chain_fuel(card_db):
    """Decision level: mid-cast chain fuel on the stack (bp == 0.0)
    with a silence instant held and mana open → the response is the
    silence instant, not a hold.  Every remaining cast of the combo
    turn is denied; the counter-hold rationale does not apply to a
    response that never trades with the payoff.
    """
    game, item, defender_idx, silence = _chain_game(
        card_db, spells_cast_this_turn=3)
    decider = ResponseDecider(defender_idx, TurnPlanner(),
                              strategic_logger=None)
    decider.opp_archetype = "combo"
    result = decider.decide_response(game, item)
    assert result is not None and result[0].instance_id == silence.instance_id, (
        "Mid-cast chain fuel must be answered by the held cast-lock "
        f"instant; got {result[0].name if result else None}"
    )


def test_cast_lock_stays_held_outside_a_midcast_chain(card_db):
    """Negative: the opponent's first cast of the turn is ordinary
    development — the cast-lock denies (almost) nothing and must not
    be burned there.
    """
    game, item, defender_idx, silence = _chain_game(
        card_db, spells_cast_this_turn=1)
    decider = ResponseDecider(defender_idx, TurnPlanner(),
                              strategic_logger=None)
    decider.opp_archetype = "combo"
    result = decider.decide_response(game, item)
    assert result is None or result[0].instance_id != silence.instance_id, (
        "A cast-lock instant must not be burned on a development-turn "
        f"cast; got {result[0].name if result else None}"
    )
