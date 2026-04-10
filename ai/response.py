"""
Stack response logic — extracted from AIPlayer (Phase 4B).

Handles instant-speed responses: counterspells, blink saves,
instant removal, and threat evaluation for stack items.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.cards import CardInstance
    from engine.stack import StackItem
    from ai.turn_planner import TurnPlanner
    from ai.strategic_logger import StrategicLogger


class ResponseDecider:
    """Decides instant-speed responses to opponent's spells."""

    def __init__(self, player_idx: int, turn_planner: Optional["TurnPlanner"] = None, strategic_logger: Optional["StrategicLogger"] = None) -> None:
        self.player_idx = player_idx
        self.turn_planner = turn_planner
        self.strategic_logger = strategic_logger

    def decide_response(self, game: "GameState", stack_item: "StackItem",
                        pick_removal_target_fn: Optional[Callable] = None) -> Optional[Tuple["CardInstance", List[int]]]:
        """Decide whether and how to respond to a stack item.

        Returns (response_card, targets) or None.
        """
        from ai.evaluator import estimate_spell_value, estimate_permanent_value
        from engine.cards import CardType, Keyword

        player = game.players[self.player_idx]
        instants = [c for c in player.hand
                    if (c.template.is_instant or c.template.has_flash)
                    and game.can_cast(self.player_idx, c)]

        # "Can't be countered" — don't try counterspells against these
        threat_oracle = (stack_item.source.template.oracle_text or '').lower()
        if "can't be countered" in threat_oracle or "can\u2019t be countered" in threat_oracle:
            instants = [c for c in instants if "counterspell" not in c.template.tags]

        if not instants:
            if self.strategic_logger:
                self.strategic_logger.log_no_response(
                    self.player_idx, stack_item.source.name, game,
                    "No castable instants in hand")
            return None

        threat = self.evaluate_stack_threat(game, stack_item)

        # Try TurnPlanner's evaluate_response first
        try:
            from ai.turn_planner import extract_virtual_board, VirtualSpell
            vboard = extract_virtual_board(game, self.player_idx)

            src = stack_item.source
            threat_vspell = VirtualSpell(
                instance_id=src.instance_id,
                name=src.name,
                cmc=src.template.cmc or 0,
                tags=set(src.template.tags),
                is_instant=src.template.is_instant or src.template.has_flash,
                is_creature=src.template.is_creature,
                power=src.template.power or 0,
                toughness=src.template.toughness or 0,
                keywords=set(),
                spell_value=threat,
                damage=0,
                has_etb="etb_value" in src.template.tags,
            )

            v_responses = []
            for inst in instants:
                v_resp = VirtualSpell(
                    instance_id=inst.instance_id,
                    name=inst.name,
                    cmc=inst.template.cmc or 0,
                    tags=set(inst.template.tags),
                    is_instant=True,
                    is_creature=False,
                    power=0, toughness=0,
                    keywords=set(),
                    spell_value=estimate_spell_value(inst, game, self.player_idx),
                    damage=0,
                    has_etb=False,
                )
                v_responses.append(v_resp)

            result = self.turn_planner.evaluate_response(
                vboard, threat, threat_vspell, v_responses)

            if result:
                v_spell, reasoning = result
                for inst in instants:
                    if inst.instance_id == v_spell.instance_id:
                        targets = self._choose_response_targets(
                            game, inst, stack_item, pick_removal_target_fn)
                        if self.strategic_logger:
                            self.strategic_logger.log_response(
                                self.player_idx, inst.name,
                                stack_item.source.name, game,
                                f"TurnPlanner: threat value {threat:.1f}, responding with {inst.name}")
                        return (inst, targets)

        except Exception:
            pass  # TurnPlanner response failed silently — fall through to legacy

        # Legacy fallback
        for instant in instants:
            tags = instant.template.tags

            # Counterspell — check targeting restrictions
            if "counterspell" in tags and stack_item.source.template.is_spell:
                # Noncreature-only counters (Spell Pierce, Negate, Stubborn Denial,
                # Mystical Dispute, Flusterstorm) can't target creature spells
                oracle = (instant.template.oracle_text or '').lower()
                target_spell = stack_item.source.template
                if 'noncreature' in oracle and target_spell.is_creature:
                    continue  # Can't counter a creature spell with this
                # "Counter target instant or sorcery" also can't hit creatures
                if ('instant or sorcery' in oracle
                    and not (target_spell.is_instant or target_spell.is_sorcery)):
                    continue
                response_value = threat
                cost = instant.template.cmc
                if response_value >= 3.0 or (response_value >= 1.5 and cost <= 2):
                    if self.strategic_logger:
                        self.strategic_logger.log_response(
                            self.player_idx, instant.name,
                            stack_item.source.name, game,
                            f"Counter: threat value {response_value:.1f} vs cost {cost}. Worth countering.")
                    return (instant, [stack_item.source.instance_id])

            # Blink response
            if "blink" in tags:
                if hasattr(stack_item, 'targets') and stack_item.targets:
                    me = game.players[self.player_idx]
                    my_creature_ids = {c.instance_id for c in me.creatures}
                    targeted_own = [tid for tid in stack_item.targets if tid in my_creature_ids]
                    if targeted_own:
                        return (instant, targeted_own[:1])
                if "removal" in stack_item.source.template.tags:
                    me = game.players[self.player_idx]
                    etb_creatures = [c for c in me.creatures
                                     if "etb_value" in c.template.tags]
                    if etb_creatures:
                        best = max(etb_creatures,
                                   key=lambda c: estimate_permanent_value(
                                       c, me, game, self.player_idx))
                        return (instant, [best.instance_id])

            # Instant-speed removal
            if "removal" in tags and pick_removal_target_fn:
                opponent = game.players[1 - self.player_idx]
                if opponent.creatures:
                    target = pick_removal_target_fn(
                        instant, opponent.creatures, opponent,
                        game, 1 - self.player_idx)
                    if target:
                        from ai.evaluator import estimate_removal_value
                        val = estimate_removal_value(
                            target, instant.template.cmc,
                            opponent, game, 1 - self.player_idx)
                        if val >= 3.0:
                            return (instant, [target.instance_id])

        if self.strategic_logger:
            self.strategic_logger.log_no_response(
                self.player_idx, stack_item.source.name, game,
                f"Threat value {threat:.1f} not worth responding to, or no suitable response")
        return None

    def _choose_response_targets(self, game: "GameState", instant: "CardInstance", stack_item: "StackItem",
                                  pick_removal_target_fn: Optional[Callable] = None) -> List[int]:
        """Choose targets for a response spell."""
        tags = instant.template.tags
        if "counterspell" in tags:
            return [stack_item.source.instance_id]
        if "blink" in tags:
            me = game.players[self.player_idx]
            if hasattr(stack_item, 'targets') and stack_item.targets:
                my_creature_ids = {c.instance_id for c in me.creatures}
                targeted_own = [tid for tid in stack_item.targets if tid in my_creature_ids]
                if targeted_own:
                    return targeted_own[:1]
            from ai.evaluator import estimate_permanent_value
            etb_creatures = [c for c in me.creatures if "etb_value" in c.template.tags]
            if etb_creatures:
                best = max(etb_creatures,
                           key=lambda c: estimate_permanent_value(c, me, game, self.player_idx))
                return [best.instance_id]
            if me.creatures:
                best = max(me.creatures,
                           key=lambda c: estimate_permanent_value(c, me, game, self.player_idx))
                return [best.instance_id]
            return []
        if "removal" in tags and pick_removal_target_fn:
            opponent = game.players[1 - self.player_idx]
            if opponent.creatures:
                target = pick_removal_target_fn(
                    instant, opponent.creatures, opponent,
                    game, 1 - self.player_idx)
                if target:
                    return [target.instance_id]
            return []
        return []

    def evaluate_stack_threat(self, game: "GameState", stack_item: "StackItem") -> float:
        """Evaluate how threatening a stack item is using clock impact.

        Threat = how much this spell worsens our position if it resolves.
        Derived from game mechanics, not arbitrary weights.
        """
        from ai.ev_evaluator import EVSnapshot, snapshot_from_game, evaluate_board
        from ai.ev_evaluator import _project_spell
        from decks.card_knowledge_loader import get_threat_value, get_burn_damage

        source = stack_item.source
        template = source.template
        opp_idx = 1 - self.player_idx

        # Use projection: what does the board look like if this spell resolves?
        # Score from OPPONENT's perspective (their spell improving their position)
        snap = snapshot_from_game(game, opp_idx)
        archetype = getattr(self, 'opp_archetype', 'midrange')
        current = evaluate_board(snap, archetype)
        projected = _project_spell(source, snap, None, game, opp_idx)
        after = evaluate_board(projected, archetype)
        threat = after - current  # positive = opponent's position improved

        # Card knowledge can override if it's higher
        known_threat = get_threat_value(source.name)
        if known_threat > 0:
            threat = max(threat, known_threat)

        # Lethal burn: huge threat
        known_burn = get_burn_damage(source.name)
        if known_burn > 0:
            my_life = game.players[self.player_idx].life
            if my_life <= known_burn:
                threat += 10.0  # lethal

        # Board wipes: scale with how many creatures we lose
        if "board_wipe" in template.tags:
            my_creatures = len(game.players[self.player_idx].creatures)
            if my_creatures >= 2:
                threat += my_creatures * 2.0

        # Cascade / reanimate: high variance, boost threat
        if 'cascade' in getattr(template, 'tags', set()):
            threat += 4.0
        if 'reanimate' in getattr(template, 'tags', set()):
            threat += 4.0

        return max(0, threat)
