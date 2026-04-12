"""Strategic Logger — rich decision annotations for replay auditing.

Every AI decision point calls into this logger to record WHY a choice
was made, not just WHAT happened.  The annotations are collected per-game
and attached to replay snapshots by the ReplayGenerator.

Design principles (from docs/strategic_ai_design.md):
  1. Readable by humans without looking at code.
  2. Archetype-appropriate vocabulary (clock/race for aggro,
     interaction/permission for control, readiness/sequencing for combo,
     role/corner for midrange).
  3. Two-layer log: "what happened" (mechanical) vs "strategic choice" (why).
  4. Minimal overhead — string interpolation only, no deep copies.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.game_state import GameState, PlayerState
    from engine.cards import CardInstance


# ═══════════════════════════════════════════════════════════════════
# Annotation data model
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StrategicAnnotation:
    """One strategic decision record."""
    category: str          # mulligan | role | transition | plan | land | spell
                           # | combat | response | combo | turning_corner
    player: int            # 0 or 1
    action: str            # Short label: "Keep 7", "Cast Lightning Bolt", etc.
    reasoning: str         # Full human-readable explanation
    alternatives: List[str] = field(default_factory=list)
    context: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "category": self.category,
            "player": self.player,
            "action": self.action,
            "reasoning": self.reasoning,
        }
        if self.alternatives:
            d["alternatives"] = self.alternatives
        if self.context:
            d["context"] = self.context
        return d


# ═══════════════════════════════════════════════════════════════════
# StrategicLogger — per-game annotation collector
# ═══════════════════════════════════════════════════════════════════

class StrategicLogger:
    """Collects strategic annotations during a game.

    Usage:
        logger = StrategicLogger()
        # inject into AIPlayer(s) at game start
        ai1.strategic_logger = logger
        ai2.strategic_logger = logger
        # ... run game ...
        # after each snapshot, drain annotations:
        annotations = logger.drain()  # returns list and clears buffer
    """

    def __init__(self):
        self._buffer: List[StrategicAnnotation] = []

    def _add(self, category: str, player: int, action: str,
             reasoning: str, alternatives: Optional[List[str]] = None,
             context: Optional[Dict] = None):
        """Append a strategic annotation to the buffer."""
        self._buffer.append(StrategicAnnotation(
            category=category,
            player=player,
            action=action,
            reasoning=reasoning,
            alternatives=alternatives or [],
            context=context or {},
        ))

    def drain(self) -> List[dict]:
        """Return all buffered annotations as dicts and clear the buffer."""
        result = [a.to_dict() for a in self._buffer]
        self._buffer.clear()
        return result

    # ── Board context snapshot (reused across annotations) ──

    @staticmethod
    def _board_ctx(game: "GameState", player_idx: int,
                   role: str = "", goal: str = "") -> Dict:
        """Build a context dict from live game state."""
        me = game.players[player_idx]
        opp = game.players[1 - player_idx]
        my_power = sum(c.power for c in me.creatures
                       if c.power and c.power > 0)
        opp_power = sum(c.power for c in opp.creatures
                        if c.power and c.power > 0)
        my_clock = 999 if my_power <= 0 else max(
            1, (opp.life + my_power - 1) // my_power)
        opp_clock = 999 if opp_power <= 0 else max(
            1, (me.life + opp_power - 1) // opp_power)
        # Phase label helps separate Main1 and Main2 decision blocks in traces
        # (fixes the duplicate-EV-trace-block report from the 2026-04-11 audit).
        phase_obj = getattr(game, 'current_phase', None)
        phase_label = getattr(phase_obj, 'name', str(phase_obj) if phase_obj else "")
        return {
            "role": role,
            "goal": goal,
            "clock_me": my_clock,
            "clock_opp": opp_clock,
            "life_me": me.life,
            "life_opp": opp.life,
            "cards_in_hand": len(me.hand),
            "mana_available": me.available_mana_estimate,
            "my_creatures": len(me.creatures),
            "opp_creatures": len(opp.creatures),
            "turn": game.turn_number,
            "phase": phase_label,
        }

    # ── Mulligan ──

    def log_mulligan(self, player_idx: int, deck_name: str,
                     hand_names: List[str], hand_size: int,
                     keep: bool, reason: str):
        """Log a mulligan decision."""
        if keep:
            action = f"Keep {hand_size}"
            reasoning = f"Keeping {hand_size}-card hand: {', '.join(hand_names)}. {reason}"
        else:
            action = f"Mulligan to {hand_size - 1}"
            reasoning = f"Mulliganing {hand_size}-card hand: {', '.join(hand_names)}. {reason}"
        self._add("mulligan", player_idx, action, reasoning,
                  context={"deck": deck_name, "hand_size": hand_size,
                           "hand": hand_names})

    def log_role(self, player_idx: int, role: str,
                 game: "GameState", reason: str):
        """Log role assessment (beatdown/control/balanced)."""
        ctx = self._board_ctx(game, player_idx, role=role)
        self._add("role", player_idx,
                  f"Role = {role.upper()}",
                  reason, context=ctx)

    # ── Goal transitions ──

    def log_transition(self, player_idx: int, old_goal: str,
                       new_goal: str, game: "GameState",
                       reason: str):
        """Log a goal transition."""
        ctx = self._board_ctx(game, player_idx, goal=new_goal)
        self._add("transition", player_idx,
                  f"Goal: {old_goal} → {new_goal}",
                  reason, context=ctx)

    # ── Overrides (lethal, survival) ──

    def log_override(self, player_idx: int, override_type: str,
                     action_desc: str, game: "GameState",
                     reason: str):
        """Log an override decision (lethal, survival, etc.)."""
        ctx = self._board_ctx(game, player_idx)
        self._add("plan", player_idx,
                  f"OVERRIDE ({override_type}): {action_desc}",
                  reason, context=ctx)

    # ── Land play ──

    def log_land(self, player_idx: int, land_name: str,
                 game: "GameState", reason: str,
                 alternatives: List[str] = None):
        """Log a land play decision."""
        ctx = self._board_ctx(game, player_idx)
        self._add("land", player_idx,
                  f"Play {land_name}",
                  reason,
                  alternatives=alternatives or [],
                  context=ctx)

    # ── Spell selection ──

    def log_spell(self, player_idx: int, spell_name: str,
                  score, game: "GameState",
                  goal: str, reason: str,
                  alternatives: List[str] = None,
                  target_desc: str = ""):
        """Log a spell selection decision.
        
        score can be a float (legacy) or a string (concern name from reactive system).
        """
        ctx = self._board_ctx(game, player_idx, goal=goal)
        action = f"Cast {spell_name}"
        if target_desc:
            action += f" → {target_desc}"
        if isinstance(score, (int, float)):
            full_reason = f"{reason} (score: {score:.1f})"
        else:
            full_reason = f"{reason} [concern: {score}]"
        self._add("spell", player_idx, action, full_reason,
                  alternatives=alternatives or [],
                  context=ctx)

    def log_hold(self, player_idx: int, game: "GameState",
                 goal: str, reason: str):
        """Log a decision to not cast anything (hold mana/cards)."""
        ctx = self._board_ctx(game, player_idx, goal=goal)
        self._add("spell", player_idx, "Pass (hold)",
                  reason, context=ctx)

    # ── Combat ──

    def log_attack(self, player_idx: int, attackers: List[str],
                   game: "GameState", reason: str,
                   alternatives: List[str] = None):
        """Log an attack decision."""
        ctx = self._board_ctx(game, player_idx)
        if attackers:
            action = f"Attack with {', '.join(attackers)}"
        else:
            action = "Hold back (no attack)"
        self._add("combat", player_idx, action, reason,
                  alternatives=alternatives or [],
                  context=ctx)

    def log_combo_assessment(self, player_idx: int,
                             game: "GameState",
                             combo_type: str,
                             ready: bool, reason: str,
                             details: Dict = None):
        """Log a combo readiness assessment."""
        ctx = self._board_ctx(game, player_idx)
        if details:
            ctx.update(details)
        action = f"Combo check ({combo_type})"
        if ready:
            action += " — GO"
        else:
            action += " — WAIT"
        self._add("combo", player_idx, action, reason,
                  context=ctx)

    def log_turning_corner(self, player_idx: int,
                           game: "GameState", reason: str):
        """Log a 'turning the corner' moment."""
        ctx = self._board_ctx(game, player_idx)
        self._add("turning_corner", player_idx,
                  "TURNING THE CORNER",
                  reason, context=ctx)

    # ── Mana reservation ──

    def log_mana_reservation(self, player_idx: int,
                             game: "GameState",
                             held_for: str, mana_held: int,
                             reason: str):
        """Log a mana reservation decision (holding up interaction)."""
        ctx = self._board_ctx(game, player_idx)
        self._add("spell", player_idx,
                  f"Reserving {mana_held} mana for {held_for}",
                  reason, context=ctx)

    # ── Response decisions ──

    def log_no_response(self, player_idx: int, threat_name: str,
                        game: "GameState", reason: str):
        """Log a decision to not respond to a spell on the stack."""
        ctx = self._board_ctx(game, player_idx)
        self._add("response", player_idx,
                  f"No response to {threat_name}",
                  reason, context=ctx)

    def log_response(self, player_idx: int, response_name: str,
                     threat_name: str, game: "GameState", reason: str):
        """Log a decision to respond to a spell on the stack."""
        ctx = self._board_ctx(game, player_idx)
        self._add("response", player_idx,
                  f"Respond with {response_name} to {threat_name}",
                  reason, context=ctx)
