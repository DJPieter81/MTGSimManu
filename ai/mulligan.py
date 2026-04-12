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
        """Return True to keep, False to mulligan.

        Also stores self.last_reason with the rationale (for logging).
        """
        from engine.cards import CardType
        from ai.ai_player import ArchetypeStrategy

        lands = [c for c in hand if c.template.is_land]
        spells = [c for c in hand if not c.template.is_land]
        land_count = len(lands)
        self.last_reason = ""

        # GoalEngine-aware mulligan
        if self.goal_engine and self.goal_engine.gameplan:
            gp = self.goal_engine.gameplan
            hand_names = {c.name for c in hand}

            has_key_card = bool(hand_names & gp.mulligan_keys) if gp.mulligan_keys else False
            has_always_early = bool(hand_names & gp.always_early) if gp.always_early else False

            if land_count < gp.mulligan_min_lands:
                self.last_reason = f"too few lands ({land_count} < {gp.mulligan_min_lands})"
                return False
            if land_count > gp.mulligan_max_lands:
                if has_always_early and land_count <= gp.mulligan_max_lands + 2:
                    pass  # keep — engine card is worth having extra lands
                else:
                    self.last_reason = f"too many lands ({land_count} > {gp.mulligan_max_lands})"
                    return False

            # Combo sets: need at least 1 card from each required set
            if gp.mulligan_combo_sets:
                for combo_set in gp.mulligan_combo_sets:
                    if not (hand_names & combo_set):
                        if cards_in_hand <= 6:
                            self.last_reason = f"missing combo piece but only {cards_in_hand} cards"
                            return True
                        self.last_reason = f"missing combo piece from {combo_set}"
                        return False

            # Combo decks with always_early: prefer reducer
            # Only apply ritual/cantrip/finisher backup check to storm/combo archetypes
            if gp.always_early and cards_in_hand >= 7 and self.archetype in ('storm', 'combo'):
                reducer_names = gp.always_early | {
                    n for n in hand_names
                    if any('cost_reducer' in getattr(c.template, 'tags', set())
                           for c in hand if c.name == n)
                }
                if not (hand_names & reducer_names):
                    has_ritual = any('ritual' in getattr(c.template, 'tags', set())
                                     for c in spells)
                    has_cantrip = any('cantrip' in getattr(c.template, 'tags', set())
                                      or 'draw' in getattr(c.template, 'tags', set())
                                      for c in spells)
                    from engine.cards import Keyword
                    has_finisher = any(
                        Keyword.STORM in getattr(c.template, 'keywords', set())
                        or 'tutor' in getattr(c.template, 'tags', set())
                        or ('flashback' in getattr(c.template, 'tags', set())
                            and 'combo' in getattr(c.template, 'tags', set()))
                        for c in spells)
                    if not (has_ritual and has_cantrip and has_finisher):
                        self.last_reason = "no cost reducer and no ritual+cantrip+finisher backup"
                        return False

            # Require creature on curve
            if gp.mulligan_require_creature_cmc > 0:
                has_creature = any(
                    c.template.is_creature and (c.template.cmc or 0) <= gp.mulligan_require_creature_cmc
                    for c in spells
                )
                if not has_creature and cards_in_hand >= 6:
                    self.last_reason = f"no creature with CMC ≤ {gp.mulligan_require_creature_cmc}"
                    return False

            # Has key card?
            # C1 fix: key card alone is not enough — also require castable
            # development. Previous behaviour short-circuited `return True`
            # on any mulligan_keys hit, letting decks with dense cheap keys
            # (Affinity, Boros Energy, Domain Zoo) auto-keep almost every
            # 7-card hand and biasing the meta toward proactive decks.
            cheap_spells = sum(1 for s in spells if (s.template.cmc or 0) <= 3)
            if gp.mulligan_keys:
                hand_names = {c.name for c in hand}
                found_keys = hand_names & gp.mulligan_keys
                if found_keys:
                    # Bar is archetype- and hand-size-dependent:
                    #   - 6 cards or fewer (already mulled): accept slower
                    #     development since ≥1 cheap spell still plays a turn-2
                    #   - combo archetype: 1 cheap spell is fine — the combo
                    #     deck often keeps a slow hand that has the piece
                    #   - else (aggro/midrange/control/tempo/ramp): need ≥2
                    #     cheap spells so the key-card hand actually develops
                    if cards_in_hand <= 6:
                        min_cheap = 1
                    elif self.archetype == ArchetypeStrategy.COMBO:
                        min_cheap = 1
                    else:
                        min_cheap = 2
                    if cheap_spells >= min_cheap:
                        self.last_reason = (
                            f"has key card(s): {', '.join(sorted(found_keys))}, "
                            f"{cheap_spells} cheap spells"
                        )
                        return True
                    # Fall through — key-card-without-development isn't keepable.

            # Generic check
            if cheap_spells >= 1:
                self.last_reason = f"{land_count} lands, {cheap_spells} castable spells"
                return True
            self.last_reason = "no castable spells"
            return False

        # Fallback: generic heuristic
        result = self._generic(hand, lands, spells, cards_in_hand)
        self.last_reason = f"generic: {land_count} lands, {len(spells)} spells" + (" — keep" if result else " — mulligan")
        return result

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
