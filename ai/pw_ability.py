"""Planeswalker loyalty-ability choice — AI layer.

Lifted out of `engine/game_runner.py::_choose_pw_ability` (E3,
2026-07-05): ability choice is a strategic decision; the engine only
delegates here and enforces loyalty legality of the pick.

S4 (2026-09-29): a loyalty line is valued by what it does, on the same
scale as a spell. Its typed clause (`LoyaltyAbility.clause`, built by the
card database for every line) is projected through the spell projector as
an ability (`_project_spell(..., as_ability=True)` — no card, no mana), and
the walker's own future activations are re-priced at its loyalty after the
cost (`expected_future_value(..., loyalty=)`). A bounce of a noncreature
permanent — invisible to the board snapshot — is priced by that
permanent's own `permanent_threat` scaled by the opponent's replay tempo.
The line with the highest value is activated; when every line is worth
less than holding the walker, the activation is declined (CR 606.3).
"""
from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot


# CR 606.3 — a loyalty activation is optional ("may"). This sentinel tells the
# engine to activate NOTHING this turn (hold the walker). It is never a
# resolvable slot, so the engine's `ability_type not in resolvable` guard
# already skips it; the engine also checks it explicitly.
PW_DECLINE = "decline"


# ─────────────────────────────────────────────────────────────
# Ultimate-line win-condition value — Track H (2026-07-05
# calibration wave).  A loyalty ability that WINS or LOCKS the game
# is an activated win condition; its value must enter play scoring
# when the loyalty trajectory (starting loyalty + repeated plus
# ticks) reaches it inside the opponent-clock horizon.  Consumed by
# ai/ev_player.py::_score_spell for planeswalker casts.  Pinned by
# tests/test_pw_ultimate_line_valued_when_reachable.py.
# ─────────────────────────────────────────────────────────────

# Full-text loyalty-line parser.  engine.oracle_parser's
# parse_planeswalker_abilities truncates descriptions to 60 chars —
# too short for win/lock phrase detection on emblem lines — so this
# module parses the untruncated text with the same bracket grammar.
_LOYALTY_LINE_RE = re.compile(
    r'\[([+−\-]?)(\d+)\]\s*:\s*(.+?)(?=\n\[|\Z)',
    re.IGNORECASE | re.DOTALL)


def _loyalty_lines(oracle: str) -> List[Tuple[int, str]]:
    """Parse ``[±N]: effect`` lines → [(signed cost, full desc), …]."""
    lines = []
    for sign, amount, desc in _LOYALTY_LINE_RE.findall(oracle or ''):
        cost = int(amount)
        if sign in ('−', '-'):
            cost = -cost
        lines.append((cost, desc.strip()))
    return lines


def is_win_lock_line(desc: str) -> bool:
    """True when a loyalty-ability description wins or locks the game.

    Oracle-driven patterns shared by the whole class — an explicit
    win/loss clause, or a repeating-emblem lock ("you get an emblem
    with 'whenever …'": a permanent trigger that compounds every turn
    is inevitability, not one-shot value).
    """
    d = (desc or '').lower()
    if 'win the game' in d or 'wins the game' in d:
        return True
    if 'lose the game' in d or 'loses the game' in d:
        return True
    if 'emblem' in d and 'whenever' in d:
        return True
    return False


def ultimate_turns_to_reach(oracle: str,
                            starting_loyalty: int) -> Optional[float]:
    """Turns of plus-ticks until the most expensive minus (the
    ultimate) is affordable, from the loyalty trajectory.  None when
    there is no ultimate or no loyalty-positive line to build with
    (and the ultimate is not already affordable)."""
    lines = _loyalty_lines(oracle)
    minus_costs = [cost for cost, _ in lines if cost < 0]
    if not minus_costs:
        return None
    ult_cost = -min(minus_costs)
    deficit = ult_cost - starting_loyalty
    if deficit <= 0:
        return 0.0
    plus_gains = [cost for cost, _ in lines if cost > 0]
    if not plus_gains:
        return None  # trajectory never reaches the ultimate
    return float(math.ceil(deficit / max(plus_gains)))


def ultimate_win_line_value(oracle: str, starting_loyalty: int,
                            snap: "EVSnapshot") -> float:
    """Win-condition value of a planeswalker's ultimate line.

    Zero unless the ultimate wins/locks the game AND the loyalty
    trajectory reaches it before the opponent's clock ends the game.
    When reachable, arriving at a game-winning activation in K turns
    is a K-turn clock: the same convention ``creature_clock_impact``
    uses (a K-turn clock has power ≈ opp_life/K, so its impact is
    1/K fraction-of-kill per turn), scaled by
    ``CLOCK_IMPACT_LIFE_SCALING`` like every other play-scoring term.
    """
    from ai.clock import combat_clock
    from ai.scoring_constants import CLOCK_IMPACT_LIFE_SCALING

    lines = _loyalty_lines(oracle)
    minus_costs = [cost for cost, _ in lines if cost < 0]
    if not minus_costs:
        return 0.0
    ult_cost = min(minus_costs)
    ult_desc = next(desc for cost, desc in lines if cost == ult_cost)
    if not is_win_lock_line(ult_desc):
        return 0.0

    turns = ultimate_turns_to_reach(oracle, starting_loyalty)
    if turns is None:
        return 0.0
    # +1: the ultimate itself fires on the activation after the last
    # build tick (rules constant — one activation per turn).
    horizon = turns + 1

    opp_clock = combat_clock(
        snap.opp_power, snap.my_life,
        snap.opp_evasion_power, snap.my_toughness)
    if horizon > opp_clock:
        return 0.0  # the controller does not live to reach the line

    return CLOCK_IMPACT_LIFE_SCALING / max(1.0, horizon)


def _replay_tempo(card, opp_snap_lands: int) -> float:
    """Share of the opponent's next turn spent replaying a bounced
    permanent: its mana value over the mana the opponent will have."""
    cmc = card.template.cmc or 0
    return min(1.0, cmc / max(1.0, float(opp_snap_lands + 1)))


def loyalty_line_value(pw, ability, game: "GameState", player_idx: int) -> float:
    """Value of activating `ability` now, in position-value units (the
    scale `compute_play_ev` uses for spells): the projected board after
    the line's clause resolves, plus the change in the walker's future
    activation pool at its new loyalty, minus the board now."""
    from ai.ev_evaluator import (_project_spell, evaluate_board,
                                 expected_future_value, snapshot_from_game)
    from engine.cards import CardInstance

    snap = snapshot_from_game(game, player_idx)
    current = evaluate_board(snap)
    projected = snap
    clause = getattr(ability, 'clause', None)
    if clause is not None:
        probe = CardInstance(template=clause, owner=player_idx, controller=player_idx,
                             instance_id=pw.instance_id, zone="stack")
        probe._game_state = game
        projected = _project_spell(probe, snap, None, game, player_idx, as_ability=True)

    # The walker's own future activations at its new loyalty, through the
    # same channel a cast walker's pool uses (persistent_power, decayed by
    # position_value's urgency factor).
    loyalty_now = pw.loyalty_counters or 0
    pool_now = expected_future_value(pw, snap, loyalty=loyalty_now)
    pool_after = expected_future_value(pw, snap, loyalty=loyalty_now + ability.cost)
    projected = projected.model_copy(update={
        'persistent_power': projected.persistent_power + pool_after - pool_now})
    value = evaluate_board(projected) - current

    # A bounce of a noncreature permanent has no snapshot term: price it by
    # that permanent's marginal worth to its owner, for the tempo it costs.
    bounce_req = getattr(clause, 'bounce_target', None) if clause is not None else None
    if bounce_req is not None and projected.opp_creature_count == snap.opp_creature_count:
        from engine.target_solver import enumerate_legal_targets
        from ai.permanent_threat import permanent_threat
        opp = game.players[1 - player_idx]
        theirs = [c for c in enumerate_legal_targets(game, player_idx, bounce_req, exclude=pw)
                  if c.controller != player_idx and not c.effective_is_creature]
        if theirs:
            value += max(permanent_threat(c, opp, game)
                         * _replay_tempo(c, snap.opp_total_lands) for c in theirs)

    if is_win_lock_line(getattr(ability, 'text', '')):
        value += ultimate_win_line_value(
            pw.template.oracle_text, loyalty_now, snap)
    return value


def choose_pw_ability(pw, abilities, player, opp, game: "GameState",
                      player_idx: int) -> str:
    """The slot to activate — the affordable line with the highest
    `loyalty_line_value` — or PW_DECLINE when every line is worth less
    than holding the walker (CR 606.3). `abilities` is the walker's
    typed, engine-resolvable lines ``{slot: LoyaltyAbility}``; the engine
    validates and pays the loyalty cost of whatever is returned."""
    best_slot, best_value = PW_DECLINE, None
    for slot, ability in abilities.items():
        if (pw.loyalty_counters or 0) + ability.cost < 0:
            continue
        v = loyalty_line_value(pw, ability, game, player_idx)
        if best_value is None or v > best_value:
            best_slot, best_value = slot, v
    if best_value is None or best_value < 0:
        return PW_DECLINE
    return best_slot
