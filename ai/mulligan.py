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
    from ai.strategy_profile import ArchetypeStrategy


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
        from ai.strategy_profile import ArchetypeStrategy

        lands = [c for c in hand if c.template.is_land]
        spells = [c for c in hand if not c.template.is_land]
        land_count = len(lands)
        self.last_reason = ""

        # ── Hard floor: 0 lands = always mulligan ──────────────────────
        # Exception: Affinity can keep 0-land hands with mana artifacts
        # (Mox Opal, Springleaf Drum) that produce mana without lands.
        if land_count == 0:
            deck_name = ""
            if self.goal_engine and self.goal_engine.gameplan:
                deck_name = self.goal_engine.gameplan.deck_name
            if "affinity" in deck_name.lower():
                hand_names = {c.name for c in hand}
                mana_artifacts = hand_names & {"Mox Opal", "Springleaf Drum"}
                if not mana_artifacts:
                    self.last_reason = "0 lands — no mana artifacts (Affinity)"
                    return False
                # else: Affinity with mana artifacts — allow through to normal eval
            else:
                self.last_reason = "0 lands — hard floor"
                return False

        # ── Soft ceiling: 5+ lands with < 2 spells = mulligan ─────────
        # Exception: Amulet Titan actively wants land-heavy hands.
        if land_count >= 5 and len(spells) < 2:
            deck_name = ""
            if self.goal_engine and self.goal_engine.gameplan:
                deck_name = self.goal_engine.gameplan.deck_name
            if "amulet" not in deck_name.lower():
                self.last_reason = (
                    f"{land_count} lands with only {len(spells)} spell(s) — soft ceiling"
                )
                return False

        # Suspend-only cards (Living End, Ancestral Vision) are dead in hand
        # — can only be cast via cascade/suspend, never from hand
        from engine.cards import Keyword
        dead_cards = [c for c in spells
                      if c.template.cmc == 0 and Keyword.SUSPEND in c.template.keywords]
        live_spells = [c for c in spells if c not in dead_cards]
        if dead_cards and len(live_spells) < 2 and cards_in_hand >= 6:
            self.last_reason = f"dead card in hand ({dead_cards[0].name}) + too few live spells"
            return False

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
            # Only apply ritual/cantrip/finisher backup check to combo archetype.
            # Bug fix: the previous check `self.archetype in ('storm', 'combo')`
            # compared an ArchetypeStrategy enum against string literals, which
            # always evaluated False — making this entire guardrail dead code.
            if gp.always_early and cards_in_hand >= 7 and self.archetype == ArchetypeStrategy.COMBO:
                # Include only IMMEDIATE cost-reducers: the always_early
                # list (curated per deck — e.g. Ruby Medallion) plus any
                # non-creature cost_reducer-tagged card in hand.
                # Creatures tagged as cost_reducer (e.g. Ral, Monsoon Mage)
                # need to be cast AND flipped/attacked to actually reduce
                # costs — they don't make a ritual-less hand playable.
                # Rule: summoning-sick creatures don't contribute mana on
                # their first turn, so they're not "immediate" mana engines.
                reducer_names = gp.always_early | {
                    n for n in hand_names
                    if any('cost_reducer' in getattr(c.template, 'tags', set())
                           and not c.template.is_creature
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

            # Require creature on curve, with signal-based escape
            # (design: docs/design/ev_correctness_overhaul.md §2.F).
            # The rigid "must have a ≤N-CMC creature" gate ships hands
            # that are anti-matchup-strong (removal + on-curve finishers
            # + artifact hate) back to mulligan.  Relax it: if the hand
            # lacks a curve creature but has enough actionable spells
            # in the N+2 CMC band (interaction, threats, efficient
            # bodies), treat the substitute development as sufficient.
            if gp.mulligan_require_creature_cmc > 0:
                has_creature = any(
                    c.template.is_creature and (c.template.cmc or 0) <= gp.mulligan_require_creature_cmc
                    for c in spells
                )
                if not has_creature and cards_in_hand >= 6:
                    # Actionable spells across the first ~4 turns:
                    # oracle/tag-driven, no card names.
                    max_actionable_cmc = gp.mulligan_require_creature_cmc + 2
                    actionable_spells = [
                        s for s in spells
                        if (s.template.cmc or 0) <= max_actionable_cmc
                        and (
                            s.template.is_creature
                            or bool(getattr(s.template, 'tags', set())
                                    & {'removal', 'counterspell',
                                       'board_wipe', 'threat',
                                       'efficient_threat',
                                       'card_advantage', 'cantrip'})
                        )
                    ]
                    # Rules-constant floor: three actionable spells
                    # cover T1-T3 plays (or T2-T4 if we miss land 1),
                    # matching the "curve-out" expectation the original
                    # rule was enforcing.  A hand with fewer than three
                    # has nothing to substitute for the missing creature.
                    MIN_ACTIONABLE_FOR_CURVE_SUBSTITUTE = 3
                    if len(actionable_spells) < MIN_ACTIONABLE_FOR_CURVE_SUBSTITUTE:
                        self.last_reason = f"no creature with CMC ≤ {gp.mulligan_require_creature_cmc}"
                        return False
                    # Keep path: note the substitute in the reason so
                    # replay logs remain legible.
                    self.last_reason = (
                        f"no creature with CMC ≤ "
                        f"{gp.mulligan_require_creature_cmc} but "
                        f"{len(actionable_spells)} actionable spells "
                        f"≤ CMC {max_actionable_cmc}"
                    )

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

            # Critical-piece check: decks with a big payoff (Amulet Titan →
            # Primeval Titan, Living End → Living End, Storm → Grapeshot) can
            # keep a hand that has the payoff + enough lands to cast it, even
            # without cheap developmental spells. Without this gate, Amulet
            # Titan mulligans 6-card hands like {5 lands, 2 Primeval Titans}
            # as "no castable spells" and bottoms its own win condition
            # (audit F-R5-AM1). Only applies when the deck explicitly
            # declares critical_pieces AND lands can support the payoff's
            # CMC; no speculative keeps.
            if gp.critical_pieces:
                hand_names = {c.name for c in hand}
                found_critical = hand_names & gp.critical_pieces
                if found_critical:
                    # Max CMC of the critical piece(s) the hand contains.
                    crit_cards = [c for c in hand if c.name in found_critical]
                    max_cmc = max((c.template.cmc or 0) for c in crit_cards)
                    # Need enough lands to eventually cast the piece. Ramp
                    # decks cast CMC-6 Titans on 4-5 lands via bounce lands
                    # + Amulet; allow land_count >= max_cmc - 2 as the
                    # floor (lower bound: mulligan_min_lands).
                    land_floor = max(gp.mulligan_min_lands or 2, max_cmc - 2)
                    if land_count >= land_floor:
                        self.last_reason = (
                            f"has critical piece(s): {', '.join(sorted(found_critical))}, "
                            f"{land_count} lands"
                        )
                        return True

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
        from ai.strategy_profile import ArchetypeStrategy

        land_count = len(lands)
        # P1 fix: 0 lands = always mulligan (no free-spell exception needed
        # since decks using Evoke/Living End have gameplans with min_lands)
        if land_count == 0:
            self.last_reason = "0 lands — auto-mulligan"
            return False
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

        # Enforce mulligan_min_lands floor on the KEPT hand. Without this,
        # a 7-card hand with 2 lands + 5 mulligan_key creatures will bottom
        # both lands (scored ~10) below any keyed creature (scored 12+),
        # leaving a 5-card hand with zero lands. Audit F-R5-B1: Boros at
        # seed 55555 bottomed Marsh Flats + Arena of Glory to keep 5
        # creatures, then drew into lands naturally — but for 2 turns had
        # no mana.
        min_lands = 2  # default floor
        if self.goal_engine and self.goal_engine.gameplan:
            min_lands = self.goal_engine.gameplan.mulligan_min_lands or 2
        lands_in_hand = [c for c in hand if c.template.is_land]
        kept_count = len(hand) - count
        # Protect at least min(min_lands, total_lands_available) lands in
        # the kept hand. We can't guarantee a floor if the hand doesn't
        # have enough lands to begin with.
        land_floor = min(min_lands, len(lands_in_hand))

        bottom = [c for c, _ in scored[:count]]
        # Count lands we'd be bottoming; if too many, swap lowest-scored
        # non-land in the kept hand for the lowest-scored land in bottom.
        bottomed_lands = [c for c in bottom if c.template.is_land]
        kept_lands = len(lands_in_hand) - len(bottomed_lands)
        if kept_lands < land_floor and bottomed_lands:
            # Find non-lands in the kept hand, bottom the lowest-scored
            # of them instead to preserve a land in the kept hand.
            kept = [c for c, _ in scored[count:]]  # higher-scored, the keep pile
            kept_nonland_scored = sorted(
                [(c, self.goal_engine.card_keep_score(c, hand)
                  if self.goal_engine else self._card_keep_score(c, hand))
                 for c in kept if not c.template.is_land],
                key=lambda x: x[1]
            )
            needed = land_floor - kept_lands
            # Swap bottom lands for kept non-lands (lowest-scored first)
            for i in range(min(needed, len(kept_nonland_scored),
                               len(bottomed_lands))):
                swap_from_bottom = bottomed_lands[i]
                swap_into_bottom = kept_nonland_scored[i][0]
                bottom.remove(swap_from_bottom)
                bottom.append(swap_into_bottom)
        return bottom

    def _card_keep_score(self, card: "CardInstance", hand: List["CardInstance"]) -> float:
        """Score a card for mulligan bottom. Higher = more valuable to keep."""
        from ai.strategy_profile import ArchetypeStrategy
        from engine.cards import Keyword

        # Suspend-only cards are dead in hand — always bottom
        if card.template.cmc == 0 and Keyword.SUSPEND in card.template.keywords:
            return -100.0

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
