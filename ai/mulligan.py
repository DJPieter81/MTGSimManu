"""
Mulligan decision logic — extracted from AIPlayer (Phase 4B).

Handles keep/mulligan decisions and card-to-bottom selection.
Uses GoalEngine's gameplan-aware scoring when available,
with heuristic fallbacks for decks without gameplans.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from ai.gameplan import GoalEngine
    from ai.ai_player import ArchetypeStrategy


class MulliganDecider:
    """Decides whether to keep or mulligan, and which cards to bottom."""

    def __init__(self, archetype: "ArchetypeStrategy", goal_engine: Optional["GoalEngine"] = None) -> None:
        self.archetype = archetype
        self.goal_engine = goal_engine

    def decide(self, hand: List["CardInstance"], cards_in_hand: int) -> bool:
        """Return True to keep, False to mulligan."""
        from engine.cards import CardType
        from ai.ai_player import ArchetypeStrategy

        lands = [c for c in hand if c.template.is_land]
        spells = [c for c in hand if not c.template.is_land]
        land_count = len(lands)

        # GoalEngine-aware mulligan
        if self.goal_engine and self.goal_engine.gameplan:
            gp = self.goal_engine.gameplan
            hand_names = {c.name for c in hand}

            # Check for key engine cards — always keep hands with key cards
            # even if land count is off, as long as we have 1+ lands
            has_key_card = bool(hand_names & gp.mulligan_keys) if gp.mulligan_keys else False
            has_always_early = bool(hand_names & gp.always_early) if gp.always_early else False

            if land_count < gp.mulligan_min_lands:
                return False
            if land_count > gp.mulligan_max_lands:
                # Exception: keep hands with key engine cards (Medallion etc.)
                if has_always_early and land_count <= gp.mulligan_max_lands + 2:
                    pass  # keep — engine card is worth having extra lands
                else:
                    return False

            # Combo sets: need at least 1 card from each required set
            if gp.mulligan_combo_sets:
                for combo_set in gp.mulligan_combo_sets:
                    if not (hand_names & combo_set):
                        return cards_in_hand <= 5  # keep bad 5-card hands

            # Combo decks with always_early (cost reducers): mulligan 7-card
            # hands without a reducer — T2 Medallion is critical for Storm
            if gp.always_early and cards_in_hand >= 7:
                reducer_names = gp.always_early | {
                    n for n in hand_names
                    if any('cost_reducer' in getattr(c.template, 'tags', set())
                           for c in hand if c.name == n)
                }
                if not (hand_names & reducer_names):
                    return False  # no reducer in 7-card hand — mulligan

            # Require creature on curve
            if gp.mulligan_require_creature_cmc > 0:
                has_creature = any(
                    c.template.is_creature and (c.template.cmc or 0) <= gp.mulligan_require_creature_cmc
                    for c in spells
                )
                if not has_creature and cards_in_hand >= 6:
                    return False

            # Has key card?
            if gp.mulligan_keys:
                hand_names = {c.name for c in hand}
                if hand_names & gp.mulligan_keys:
                    return True

            # Generic check: 2+ lands, 1+ castable spell
            cheap_spells = sum(1 for s in spells if (s.template.cmc or 0) <= 3)
            return cheap_spells >= 1

        # Fallback: generic heuristic
        return self._generic(hand, lands, spells, cards_in_hand)

    def _generic(self, hand: List["CardInstance"], lands: List["CardInstance"], spells: List["CardInstance"], cards_in_hand: int) -> bool:
        """Generic mulligan heuristic when no gameplan is available."""
        from ai.ai_player import ArchetypeStrategy

        land_count = len(lands)
        if land_count == 1 and cards_in_hand == 7:
            if self.archetype == ArchetypeStrategy.AGGRO:
                return sum(1 for s in spells if s.template.cmc <= 2) >= 4
            return False
        if land_count >= 5 and cards_in_hand == 7:
            return False
        if self.archetype == ArchetypeStrategy.COMBO:
            has_piece = any("combo" in c.template.tags for c in spells)
            if land_count >= 2 and has_piece:
                return True
            return cards_in_hand <= 6 or land_count >= 2
        if self.archetype == ArchetypeStrategy.AGGRO:
            return 1 <= land_count <= 3 and sum(1 for s in spells if s.template.cmc <= 2) >= 2
        if self.archetype == ArchetypeStrategy.CONTROL:
            if land_count >= 3:
                return sum(1 for s in spells
                           if "removal" in s.template.tags or
                           "counterspell" in s.template.tags) >= 1
            return False
        if 2 <= land_count <= 4:
            return sum(1 for s in spells if s.template.cmc <= 3) >= 2
        return cards_in_hand <= 6

    def choose_cards_to_bottom(self, hand: List["CardInstance"],
                                count: int) -> List["CardInstance"]:
        """Choose which cards to put on the bottom after mulligan."""
        if count <= 0:
            return []
        if self.goal_engine:
            scored = [(c, self.goal_engine.card_keep_score(c, hand)) for c in hand]
        else:
            scored = [(c, self._card_keep_score(c, hand)) for c in hand]
        scored.sort(key=lambda x: x[1])
        return [c for c, _ in scored[:count]]

    def _card_keep_score(self, card: "CardInstance", hand: List["CardInstance"]) -> float:
        """Score a card for mulligan bottom. Higher = more valuable to keep."""
        from ai.ai_player import ArchetypeStrategy

        score = 0.0
        t = card.template
        lands_in_hand = sum(1 for c in hand if c.template.is_land)

        if t.is_land:
            score += 10.0 if lands_in_hand <= 3 else 2.0
            if t.produces_mana:
                score += len(t.produces_mana) * 0.5
        else:
            score += max(0, 5 - t.cmc)
            if "removal" in t.tags:
                score += 3.0
            if "threat" in t.tags:
                score += 2.0
            if "early_play" in t.tags:
                score += 4.0 if self.archetype == ArchetypeStrategy.AGGRO else 2.0
            if "combo" in t.tags:
                score += 5.0 if self.archetype == ArchetypeStrategy.COMBO else 1.0
            if "counterspell" in t.tags:
                score += 3.0 if self.archetype in (ArchetypeStrategy.CONTROL,
                                                     ArchetypeStrategy.TEMPO) else 1.0
        return score

    @staticmethod
    def reason(hand: List["CardInstance"], lands: List["CardInstance"], spells: List["CardInstance"], keep: bool) -> str:
        """Generate a human-readable mulligan reason."""
        land_count = len(lands)
        cheap = sum(1 for s in spells if (s.template.cmc or 0) <= 2)
        interaction = sum(1 for s in spells if 'removal' in s.template.tags or 'counterspell' in s.template.tags)
        if keep:
            parts = [f"{land_count} lands"]
            if cheap:
                parts.append(f"{cheap} cheap spells")
            if interaction:
                parts.append(f"{interaction} interaction")
            return f"Keepable: {', '.join(parts)}"
        else:
            issues = []
            if land_count <= 1:
                issues.append("too few lands")
            if land_count >= 5:
                issues.append("too many lands")
            if cheap == 0:
                issues.append("no early plays")
            return f"Mulligan: {', '.join(issues)}" if issues else "Suboptimal hand"
