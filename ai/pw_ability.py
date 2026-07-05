"""Planeswalker loyalty-ability choice — AI layer.

Lifted out of `engine/game_runner.py::_choose_pw_ability` (E3,
2026-07-05 calibration probe): ability choice is a strategic decision,
and per CLAUDE.md "Engine layer enforces rules; AI layer makes
choices" the ranking belongs here.  `engine/game_runner.py` now only
delegates to `choose_pw_ability` and enforces the loyalty legality of
whatever the AI picked.

The chooser is description-driven — generic oracle-text patterns, no
per-card logic — so any planeswalker with standard loyalty ability
text gets reasonable behaviour.  Two decision rules added on top of
the lifted scoring (both pinned by
tests/test_pw_ability_choice_defensive_minus.py):

1. **Defensive minus at a failing race** — when `life_phase(snap)` is
   PANIC or LETHAL, an ability that neutralizes an opposing creature
   (kill or bounce) gets an urgency bonus large enough to outrank any
   no-board-impact utility line.
2. **Suicide guard** — an ability that drops the walker to zero
   loyalty is penalized below every surviving line unless it
   neutralizes an opposing creature or the race is already failing
   (at PANIC/LETHAL the walker is spent freely; it will not survive
   to matter otherwise).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from engine.game_state import GameState


# ─────────────────────────────────────────────────────────────
# Score table — named preference constants (module-level data).
# Relative order is the contract: kill-a-creature and wipe rank
# above value lines, value lines above chip/utility lines.  The
# magnitudes are the historical engine-layer values, preserved
# verbatim by the E3 lift so behaviour outside the two new rules
# is unchanged.
# ─────────────────────────────────────────────────────────────

PW_SCORE_FLOOR = -100          # sentinel: below every real ability score
PW_SCORE_KILL_BASE = 20        # kill an opp creature (+ its cmc)
PW_SCORE_WIPE_BIG = 25         # board wipe vs 3+ opp permanents
PW_SCORE_WIPE_SMALL = 10       # board wipe vs 1-2 opp permanents
PW_SCORE_WIPE_EMPTY = -5       # never wipe an empty board
PW_SCORE_BOUNCE_BASE = 15      # bounce a >=2-cmc permanent (+ its cmc)
PW_SCORE_BOUNCE_LOW = 2        # bouncing 1-drops is rarely worth it
PW_SCORE_BOUNCE_DRAW_BONUS = 5  # bounce that also draws a card
PW_SCORE_DRAW = 14             # card advantage / selection
PW_SCORE_LAND_RECURSION = 12   # recur a land from the graveyard
PW_SCORE_NO_LANDS_TO_RETURN = -2
PW_SCORE_COST_REDUCTION = 8    # ramp for future turns
PW_SCORE_MANA = 7              # mana production
PW_SCORE_UTILITY = 5           # flash / timing static
PW_SCORE_FATESEAL = 4          # library-top manipulation
PW_SCORE_CHIP_BASE = 3         # chip damage at face (+ dmg)
PW_SCORE_CHIP_RISKY = -5       # face ping that risks the walker
PW_SCORE_UNKNOWN_PLUS = 1      # unparsed loyalty-positive ability
PW_SCORE_UNKNOWN_MINUS = -1    # unparsed loyalty-negative ability
PW_LOYALTY_BUILD_TIEBREAK = 0.5  # prefer building loyalty on near-ties

# Loyalty the walker must keep after a face-ping / low-value line for
# the line to be considered "safe" (survives a single small attack).
PW_SAFE_LOYALTY_AFTER = 2

# Opp-permanent count above which a wipe is a blowout rather than a
# trade (3+ cards answered by one activation).
PW_WIPE_BLOWOUT_COUNT = 3

# Bounce targets below this cmc are usually not worth a loyalty
# activation (the opponent replays them for almost nothing).
PW_BOUNCE_MIN_CMC = 2

# Rule 1 — failing-race urgency bonus for creature-neutralizing
# abilities.  Derived from the score table: it must lift the weakest
# creature-neutralizing line (PW_SCORE_BOUNCE_LOW) strictly above the
# strongest no-board-impact line (PW_SCORE_DRAW), so at PANIC/LETHAL
# the defensive minus always outranks pure card selection.
PW_PANIC_DEFENSE_BONUS = PW_SCORE_DRAW - PW_SCORE_BOUNCE_LOW + 1

# Rule 2 — suicide guard.  Derived from the score table: it must sink
# the strongest no-creature-impact line (wipe blowout + bounce-with-
# draw headroom) below PW_SCORE_UNKNOWN_MINUS, so a walker-killing
# activation without creature impact loses to every surviving line.
PW_SUICIDE_GUARD_PENALTY = (PW_SCORE_WIPE_BIG + PW_SCORE_BOUNCE_BASE
                            + PW_SCORE_BOUNCE_DRAW_BONUS + PW_SCORE_DRAW)


def _race_is_failing(game: "GameState", player_idx: int) -> bool:
    """True when the controller's life phase is PANIC or LETHAL —
    composed from the existing clock primitives, no life literals."""
    from ai.clock import LifePhase, life_phase
    from ai.ev_evaluator import snapshot_from_game

    snap = snapshot_from_game(game, player_idx)
    return life_phase(snap) in (LifePhase.PANIC, LifePhase.LETHAL)


def choose_pw_ability(pw, pw_data, player, opp, game: "GameState",
                      player_idx: int) -> str:
    """Choose the best planeswalker ability to activate.

    Uses ability descriptions to make generic decisions rather than
    hardcoding per-card logic.  Returns one of "plus" / "zero" /
    "minus" / "ult".  The engine validates and pays the loyalty cost;
    this function only ranks.
    """

    def can_afford(ability_key):
        if ability_key not in pw_data:
            return False
        cost, _ = pw_data[ability_key]
        return pw.loyalty_counters + cost >= 0

    def desc(ability_key):
        if ability_key not in pw_data:
            return ""
        return pw_data[ability_key][1].lower()

    def loyalty_after(ability_key):
        if ability_key not in pw_data:
            return pw.loyalty_counters
        cost, _ = pw_data[ability_key]
        return pw.loyalty_counters + cost

    # Always ult if we can (game-winning)
    if can_afford("ult"):
        return "ult"

    # Collect all non-ult abilities we can afford
    abilities = []  # (key, cost, desc)
    for key in ["plus", "zero", "minus"]:
        if can_afford(key):
            abilities.append((key, pw_data[key][0], desc(key)))

    if not abilities:
        return "plus"  # shouldn't happen, but safe fallback

    race_failing = _race_is_failing(game, player_idx)

    # ── Evaluate each ability by description patterns ──
    best_key = "plus"
    best_score = PW_SCORE_FLOOR

    for key, cost, ability_desc in abilities:
        score = 0
        remaining_loyalty = loyalty_after(key)
        # Does this line answer an opposing creature (the attacker
        # class)?  Consumed by the failing-race bonus and the
        # suicide guard below.
        neutralizes_creature = False

        # DAMAGE abilities: "deal X damage" or "deals X damage"
        if "damage" in ability_desc or "deals" in ability_desc:
            # Can we kill an opponent creature?
            if opp.creatures:
                # Parse damage amount from description
                dmg = 1
                for word in ability_desc.split():
                    if word.isdigit():
                        dmg = int(word)
                        break
                killable = [c for c in opp.creatures
                            if (c.toughness or 0) - c.damage_marked <= dmg]
                if killable:
                    best_kill = max(killable, key=lambda c: c.template.cmc)
                    # high priority: kill creatures
                    score = PW_SCORE_KILL_BASE + best_kill.template.cmc
                    neutralizes_creature = True
                else:
                    # No killable creatures, but can still go face
                    if remaining_loyalty >= PW_SAFE_LOYALTY_AFTER:
                        # low-priority chip damage
                        score = PW_SCORE_CHIP_BASE + dmg
                    else:
                        score = PW_SCORE_CHIP_RISKY  # too risky
            elif remaining_loyalty >= PW_SAFE_LOYALTY_AFTER:
                score = PW_SCORE_CHIP_BASE  # ping face when no creatures

        # BOUNCE abilities: "return" + "to" + "hand" or "bounce"
        elif "bounce" in ability_desc or (
                "return" in ability_desc and "hand" in ability_desc
                and "owner" in ability_desc):
            nonlands = [c for c in opp.battlefield if not c.template.is_land]
            if nonlands:
                best_cmc = max(c.template.cmc for c in nonlands)
                if best_cmc >= PW_BOUNCE_MIN_CMC:
                    # bounce high-value targets
                    score = PW_SCORE_BOUNCE_BASE + best_cmc
                else:
                    # not worth bouncing 1-drops usually
                    score = PW_SCORE_BOUNCE_LOW
                # A creature among the bounceable permanents means this
                # line can remove an attacker from the battlefield.
                neutralizes_creature = any(
                    c.template.is_creature for c in nonlands)
            # Bonus if it also draws a card
            if "draw" in ability_desc:
                score += PW_SCORE_BOUNCE_DRAW_BONUS

        # LAND RECURSION: "return" + "land" + "graveyard" / "hand"
        elif "land" in ability_desc and ("graveyard" in ability_desc
                                         or "return" in ability_desc):
            lands_in_gy = [c for c in player.graveyard if c.template.is_land]
            if lands_in_gy:
                score = PW_SCORE_LAND_RECURSION  # good value: recur fetchlands
            else:
                score = PW_SCORE_NO_LANDS_TO_RETURN  # no lands to return

        # DRAW / CARD SELECTION: "draw" or "brainstorm" or "look at"
        elif ("draw" in ability_desc or "brainstorm" in ability_desc
              or "look at" in ability_desc):
            score = PW_SCORE_DRAW  # card advantage is almost always good

        # COST REDUCTION / RAMP: "cost" + "less" or "add" + mana
        elif "cost" in ability_desc and "less" in ability_desc:
            score = PW_SCORE_COST_REDUCTION  # ramp for future turns
        elif "add" in ability_desc and any(c in ability_desc for c in "wubrgc"):
            score = PW_SCORE_MANA  # mana production

        # BOARD WIPE: "exile" + "each" or "all" or "destroy all"
        elif (("exile" in ability_desc or "destroy" in ability_desc)
              and ("each" in ability_desc or "all" in ability_desc)):
            opp_permanents = [c for c in opp.battlefield
                              if not c.template.is_land]
            if len(opp_permanents) >= PW_WIPE_BLOWOUT_COUNT:
                score = PW_SCORE_WIPE_BIG  # wipe when behind on board
                neutralizes_creature = any(
                    c.template.is_creature for c in opp_permanents)
            elif len(opp_permanents) >= 1:
                score = PW_SCORE_WIPE_SMALL
                neutralizes_creature = any(
                    c.template.is_creature for c in opp_permanents)
            else:
                score = PW_SCORE_WIPE_EMPTY  # don't wipe an empty board

        # FLASH / TIMING: "flash" or "as though" + "flash"
        elif "flash" in ability_desc or "any time" in ability_desc:
            score = PW_SCORE_UTILITY  # minor utility, builds loyalty

        # FATESEAL / LIBRARY MANIPULATION: "look at the top"
        elif "look at the top" in ability_desc or "fateseal" in ability_desc:
            score = PW_SCORE_FATESEAL  # minor disruption

        # Unknown ability: default to loyalty-positive
        else:
            score = (PW_SCORE_UNKNOWN_PLUS if cost >= 0
                     else PW_SCORE_UNKNOWN_MINUS)

        # ── Rule 1: failing-race urgency (E3 / A2 family) ──
        # At PANIC / LETHAL, an ability that answers an opposing
        # creature is the gear the controller needs NOW; lift it above
        # every no-board-impact utility line.
        if race_failing and neutralizes_creature:
            score += PW_PANIC_DEFENSE_BONUS

        # ── Rule 2: suicide guard (E3) ──
        # An activation that kills the walker (loyalty_after <= 0) is
        # only worth it when it answers a creature or the race is
        # already failing.  Otherwise sink it below every surviving
        # line — a replayable-permanent bounce is not worth the walker.
        if (remaining_loyalty <= 0 and not neutralizes_creature
                and not race_failing):
            score -= PW_SUICIDE_GUARD_PENALTY

        # Tiebreaker: prefer loyalty-positive abilities when scores are close
        if cost > 0:
            score += PW_LOYALTY_BUILD_TIEBREAK  # slight bonus for building loyalty

        if score > best_score:
            best_score = score
            best_key = key

    return best_key
