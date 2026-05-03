"""
Mulligan decision logic — extracted from AIPlayer (Phase 4B).

Handles keep/mulligan decisions and card-to-bottom selection.
Uses GoalEngine's gameplan-aware scoring when available,
with heuristic fallbacks for decks without gameplans.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ai.scoring_constants import (
    LEGENDARY_DUPLICATE_PENALTY,
    DEFAULT_MULLIGAN_MIN_LANDS,
    SUSPEND_ONLY_DEAD_PENALTY,
    KEEP_SCORE_LAND_NEEDED,
    KEEP_SCORE_LAND_FLOOD,
    KEEP_SCORE_LAND_FLOOD_THRESHOLD,
    KEEP_SCORE_LAND_PRODUCES_BONUS,
    KEEP_SCORE_CMC_INVERTED_CEIL,
    KEEP_SCORE_REMOVAL_TAG,
    KEEP_SCORE_THREAT_TAG,
    KEEP_SCORE_EARLY_PLAY_AT_HOME,
    KEEP_SCORE_EARLY_PLAY_AWAY,
    KEEP_SCORE_COMBO_AT_HOME,
    KEEP_SCORE_COMBO_AWAY,
    KEEP_SCORE_COUNTERSPELL_AT_HOME,
    KEEP_SCORE_COUNTERSPELL_AWAY,
)

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from ai.gameplan import GoalEngine
    from ai.strategy_profile import ArchetypeStrategy


def _apply_legendary_dedup_penalty(
    scored: List[tuple],
) -> List[tuple]:
    """Subtract a bottom-preference penalty from duplicate legendary copies.

    `scored` is a list of `(card_instance, keep_score)` tuples.  For
    each legendary card name appearing more than once, the first copy
    encountered (in input order) keeps its score and every subsequent
    copy has its score reduced by `LEGENDARY_DUPLICATE_PENALTY` so that
    the bottom-selection sort places duplicates first.

    Pure function over the template's `supertypes` flag — no card names
    are referenced.  Magnitude is calibrated in
    `ai.scoring_constants.LEGENDARY_DUPLICATE_PENALTY` to drop a
    duplicate below the highest-scored normal keep (~27) without
    inverting the relative ranking of the surviving copy.
    """
    from engine.cards import Supertype
    seen_legendary: set = set()
    out: List[tuple] = []
    for c, s in scored:
        is_legendary = Supertype.LEGENDARY in getattr(c.template, "supertypes", ())
        if is_legendary:
            if c.name in seen_legendary:
                out.append((c, s - LEGENDARY_DUPLICATE_PENALTY))
                continue
            seen_legendary.add(c.name)
        out.append((c, s))
    return out


def _dedupe_dead_legendaries(cards: List["CardInstance"]) -> List["CardInstance"]:
    """Filter out duplicate copies of legendary permanents.

    Per the legend rule (CR 704.5j), if a player would control two or
    more legendary permanents with the same name, all but one are put
    into their owners' graveyards.  In hand, every copy beyond the
    first is therefore effectively dead — it cannot generate value.

    Returns a list with at most one copy per legendary card name; non-
    legendary cards are passed through unchanged.  Pure function over
    the template's `supertypes` flag — no card names are referenced.
    """
    from engine.cards import Supertype
    seen_legendary: set = set()
    out: List["CardInstance"] = []
    for c in cards:
        is_legendary = Supertype.LEGENDARY in getattr(c.template, "supertypes", ())
        if is_legendary:
            if c.name in seen_legendary:
                # Excess copy — dead on resolution.
                continue
            seen_legendary.add(c.name)
        out.append(c)
    return out


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
        # Legend rule (CR 704.5j): duplicate copies of the same legendary
        # permanent are dead on resolution — only one survives.  For
        # mulligan evaluation, treat each excess copy as not present.
        # `live_spells` is the spell list with duplicate legendary copies
        # filtered out (one representative copy retained); use it in
        # place of `spells` for keep-quality checks below so that a hand
        # of {3× legendary creature, 2 lands} is correctly recognised as
        # effectively 3 cards rather than 5.
        spells = _dedupe_dead_legendaries(spells)
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

            # Combo sets: need at least 1 card from each required set.
            # Reference: docs/diagnostics/2026-04-28_goryos_combo_mana_mulligan.md
            #
            # Three-part predicate:
            #   (a) at 7 cards, at least ONE declared combo_set must
            #       have >= 2 of its 3 pieces present (Bug #4 fix:
            #       1-of-3 is functionally zero combo progress and
            #       leaves the deck unable to assemble in time).
            #   (b) at <=6 cards, allow keeping a hand missing one
            #       path's piece — the 6-card escape — UNLESS every
            #       declared path is empty in hand, in which case mull
            #       to 5 because keeping a 6 with zero combo cards is
            #       strictly worse than going to 5 to find one.
            #   (c) for combo cards present in hand, the lands must
            #       cover the union of pip requirements (Bug #1 color
            #       check).
            # Typed-path check (preferred when declared) — supersedes
            # the flat ``mulligan_combo_sets`` predicate for decks that
            # have heterogeneous role buckets (enabler / payoff).  Each
            # path is a dict with role-named buckets; the hand
            # satisfies a path when role coverage requirements are met
            # at the current virtual hand size.  Decks without
            # ``mulligan_combo_paths`` fall through to the existing
            # flat-set logic — backward-compatible.
            #
            # Decision rule (mirrors ai/gameplan.py:DeckGameplan docs):
            #   - virtual size 7: keep iff some path has hand-coverage
            #     in BOTH ``enablers`` and ``payoffs``
            #   - virtual size 6: keep iff some path has hand-coverage
            #     in ``enablers`` (payoff is dig-able through the
            #     enabler — Faithful Mending, ritual + cantrip, etc.)
            #   - virtual size <=5: ``mulligan_always_keep`` short-
            #     circuited at the top of decide()
            #
            # Why no ``targets`` bucket: dig-able through enabler.
            # Counting them inflates mulligan rate without information
            # value at standard library densities (4 enablers + 7
            # targets in 60 → P(dig within 3 cantrips) ≈ 0.45).
            if gp.mulligan_combo_paths:
                paths = gp.mulligan_combo_paths
                # Pre-compute per-path coverage flags so failure
                # messages can name the most-covered path.
                best_covered = 0  # number of buckets covered in best path
                best_total = 0    # number of non-empty buckets in best path
                keep_ok = False
                for path in paths:
                    enablers = set(path.get("enablers", []))
                    payoffs = set(path.get("payoffs", []))
                    # Bucket coverage flags.  Empty bucket = vacuously
                    # covered (don't constrain a path that doesn't
                    # declare it).
                    enabler_ok = (not enablers) or bool(hand_names & enablers)
                    payoff_ok = (not payoffs) or bool(hand_names & payoffs)
                    non_empty = sum(1 for b in (enablers, payoffs) if b)
                    covered = sum(
                        1 for ok, b in ((enabler_ok, enablers),
                                        (payoff_ok, payoffs))
                        if ok and b
                    )
                    if covered > best_covered or (
                            covered == best_covered and non_empty > best_total):
                        best_covered = covered
                        best_total = non_empty
                    # Apply per-size keep predicate.
                    if cards_in_hand >= 7:
                        if enabler_ok and payoff_ok:
                            keep_ok = True
                            break
                    else:
                        # Virtual size 6 (5 short-circuited above):
                        # only the enabler bucket is required.  An
                        # ``enablers``-less path passes vacuously,
                        # which is fine — the gameplan author chose
                        # not to declare that role for that path.
                        if enabler_ok:
                            keep_ok = True
                            break
                if not keep_ok:
                    role_required = (
                        "enabler+payoff" if cards_in_hand >= 7
                        else "enabler"
                    )
                    self.last_reason = (
                        f"combo path under-covered in "
                        f"{cards_in_hand}-card hand "
                        f"(best path: {best_covered}/{best_total} "
                        f"role buckets; need {role_required})"
                    )
                    return False
                # Typed-path keep_ok: still run color-soundness below
                # (uses combo_sets if declared; combo_paths-only decks
                # skip it harmlessly).

            elif gp.mulligan_combo_sets:
                # How many pieces from each declared combo path are
                # present in hand?  Used by both the 7-card progress
                # check (b) and the 6-card empty-paths escape (a).
                pieces_per_set = [
                    len(hand_names & s) for s in gp.mulligan_combo_sets
                ]
                max_progress = max(pieces_per_set) if pieces_per_set else 0

                if cards_in_hand >= 7:
                    # 7-card combo decks: scale required progress by
                    # set cardinality.  Combo-set semantics differ:
                    #   n=2  → ANY-style "either of 2 cascade cards
                    #          gets us going" (Living End: Demonic
                    #          Dread or Shardless Agent).  1 of 2
                    #          is enough.
                    #   n=3  → ALL-style "named enabler + target +
                    #          payoff" (Goryo's: Mending + Vengeance/
                    #          Rites + fatty).  Need 2 of 3.
                    #   n>=4 → ANY-style "interchangeable bag"
                    #          (Storm rituals, Pinnacle artifacts,
                    #          Living End cyclers).  1 of N is
                    #          enough — gameplan author wouldn't
                    #          have declared 7 alternatives if 1
                    #          weren't sufficient.
                    # Threshold per path = (1 if n != 3 else 2).
                    # max_acceptable across paths satisfies the keep.
                    keep_ok = False
                    weakest = max_progress
                    for s, prog in zip(gp.mulligan_combo_sets,
                                       pieces_per_set):
                        threshold = 2 if len(s) == 3 else 1
                        if prog >= threshold:
                            keep_ok = True
                            break
                    if not keep_ok:
                        self.last_reason = (
                            f"combo too weak in 7-card hand "
                            f"(max {weakest} pieces; need 2 of 3 "
                            f"or 1 of 2/4+ from at least one path)"
                        )
                        return False
                else:
                    # 6 or fewer cards: original escape — keep if at
                    # least one piece from any path is present, mull
                    # if every path is empty (Bug #3 fix).
                    if max_progress == 0:
                        self.last_reason = (
                            f"every combo path empty in {cards_in_hand}-card hand "
                            f"— mull to {cards_in_hand - 1}"
                        )
                        return False
                    # else: at least 1 piece present → keep (don't
                    # mull-to-oblivion at 6).

            if gp.mulligan_combo_sets:
                # Color-soundness: the kept hand's lands must cover the
                # union of colored pip requirements for the combo cards
                # actually present.  Cardname-only checks miss hands
                # that look complete on paper but cannot be cast.
                #
                # The check runs at every virtual hand size >=5 (not
                # only at 7).  After the first mulligan the engine
                # calls decide() at virtual size 6 with 7 actual cards
                # in hand; gating on ``cards_in_hand >= 7`` skipped
                # the check and let color-broken hands through (replay
                # seed 60100 G1: kept 7 with only Swamp, Mending
                # uncastable for the entire game).  The kept hand's
                # color demand is independent of which single card we
                # plan to bottom; verifying it at every size is sound.
                if cards_in_hand >= 5:
                    missing_colors = self._combo_set_color_gap(
                        hand, lands, gp.mulligan_combo_sets,
                    )
                    if missing_colors:
                        self.last_reason = (
                            f"combo set present but lands miss colors "
                            f"{sorted(missing_colors)}"
                        )
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
            # "Cheap" here means medium-or-better on the gameplan's CMC
            # profile — anything castable by T2-T3 counts as development.
            medium_cmc = gp.mulligan_cmc_profile.get("medium", 3)
            cheap_spells = sum(1 for s in spells if (s.template.cmc or 0) <= medium_cmc)
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
        from ai.gameplan import DEFAULT_MULLIGAN_CMC_PROFILE

        # No-gameplan fallback uses the default CMC profile — same brackets
        # the gp-aware path reads from `gp.mulligan_cmc_profile`.
        cheap_cmc = DEFAULT_MULLIGAN_CMC_PROFILE["cheap"]
        medium_cmc = DEFAULT_MULLIGAN_CMC_PROFILE["medium"]

        land_count = len(lands)
        # P1 fix: 0 lands = always mulligan (no free-spell exception needed
        # since decks using Evoke/Living End have gameplans with min_lands)
        if land_count == 0:
            self.last_reason = "0 lands — auto-mulligan"
            return False
        if land_count == 1 and cards_in_hand == 7:
            if self.archetype == ArchetypeStrategy.AGGRO:
                return sum(1 for s in spells if s.template.cmc <= cheap_cmc) >= 4
            return False
        if land_count >= 5 and cards_in_hand == 7:
            return False
        if self.archetype == ArchetypeStrategy.COMBO:
            has_piece = any("combo" in c.template.tags for c in spells)
            if land_count >= 2 and has_piece:
                return True
            return cards_in_hand <= 6 or land_count >= 2
        if self.archetype == ArchetypeStrategy.AGGRO:
            return 1 <= land_count <= 3 and sum(1 for s in spells if s.template.cmc <= cheap_cmc) >= 2
        if self.archetype == ArchetypeStrategy.CONTROL:
            if land_count >= 3:
                return sum(1 for s in spells
                           if "removal" in s.template.tags or
                           "counterspell" in s.template.tags) >= 1
            return False
        if 2 <= land_count <= 4:
            return sum(1 for s in spells if s.template.cmc <= medium_cmc) >= 2
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
        # Legend-rule dedup: when the hand contains N copies of a
        # legendary permanent, only one resolves (CR 704.5j).  Mark the
        # duplicate copies as preferred-bottom by subtracting a penalty
        # large enough to drop them below any normally-scored keep.
        # Magnitude derivation: the keep-score range for non-land cards
        # tops out near the role+key+cmc cap (~27, see
        # ai/gameplan.py::card_keep_score).  A penalty of 50 ensures
        # duplicate copies sort below every realistic alternative
        # without overflowing into negative-by-design land scores.
        scored = _apply_legendary_dedup_penalty(scored)
        scored.sort(key=lambda x: x[1])

        # Enforce mulligan_min_lands floor on the KEPT hand. Without this,
        # a 7-card hand with 2 lands + 5 mulligan_key creatures will bottom
        # both lands (scored ~10) below any keyed creature (scored 12+),
        # leaving a 5-card hand with zero lands. Audit F-R5-B1: Boros at
        # seed 55555 bottomed Marsh Flats + Arena of Glory to keep 5
        # creatures, then drew into lands naturally — but for 2 turns had
        # no mana.
        min_lands = DEFAULT_MULLIGAN_MIN_LANDS  # default floor
        if self.goal_engine and self.goal_engine.gameplan:
            min_lands = (self.goal_engine.gameplan.mulligan_min_lands
                         or DEFAULT_MULLIGAN_MIN_LANDS)
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

    # ── Color-coverage check for combo mulligans ───────────────────
    # Mechanic: a combo set whose cardnames are all in hand can still
    # be uncastable if the lands don't cover the union of pip
    # requirements.  This applies to every combo deck, not Goryo's
    # specifically (Living End cycler U/B + cascade R, Ruby Storm RR,
    # Niv-Mizzet shells, etc.).  Implementation is oracle-driven:
    #   - lands with `produces_mana` populated → those colors directly
    #   - fetchlands (sac-search basic land types) → colors of the
    #     basic types named in their oracle text, since the deck
    #     necessarily runs duals matching at least one such type
    # Zero hardcoded card names; zero magic numbers.

    _BASIC_TYPE_TO_COLOR = {
        'Plains': 'W', 'Island': 'U', 'Swamp': 'B',
        'Mountain': 'R', 'Forest': 'G',
    }

    @classmethod
    def _land_supplies_colors(cls, land: "CardInstance") -> set:
        """Set of WUBRG colors the land can supply (directly or via
        fetch).  Empty for colorless / Wastes-style lands."""
        t = land.template
        # Direct producers — duals, basics, tri-lands, shock lands.
        direct = {c.upper() for c in (t.produces_mana or [])
                  if c.upper() in 'WUBRG'}
        if direct:
            return direct
        # Fetchland heuristic: oracle text "Search your library for a
        # <basic-type> ... card".  Detected by basic-land-type tokens
        # present in the search clause.  This is a mechanic, not a
        # cardname list — applies to every fetchland printed past or
        # future (Modern fetches, Onslaught fetches, Mirage fetches,
        # surveil lands with fetch riders, etc.).
        oracle = (getattr(t, 'oracle_text', '') or '').lower()
        if 'search your library' not in oracle:
            return set()
        colors = set()
        for basic, color in cls._BASIC_TYPE_TO_COLOR.items():
            if basic.lower() in oracle:
                colors.add(color)
        return colors

    @classmethod
    def _hand_color_supply(cls, lands: List["CardInstance"]) -> set:
        """Union of colors the hand's lands can supply."""
        out: set = set()
        for land in lands:
            out |= cls._land_supplies_colors(land)
        return out

    @staticmethod
    def _card_color_demand(card: "CardInstance") -> set:
        """Colors required to cast this card from hand (its colored
        pips).  Generic / colorless / X costs contribute nothing."""
        cost = getattr(card.template, 'mana_cost', None)
        if cost is None:
            return set()
        # ManaCost has `.colors` returning Color enum members for any
        # non-zero pip count.
        return {c.value for c in cost.colors}

    @classmethod
    def _combo_set_color_gap(cls,
                              hand: List["CardInstance"],
                              lands: List["CardInstance"],
                              combo_sets) -> set:
        """Return the set of colors required by combo cards in hand
        but unsupplied by the hand's lands.  Empty set means the
        kept hand is color-sound for at least one declared combo
        path.

        Decision rule: for each declared combo_set, take the cards in
        hand that belong to it and union their pip demands.  The
        hand is sound iff at least ONE declared combo path's demand
        is fully covered by the hand's color supply.  A 7 with two
        partially-covered paths still mulligans — both paths need
        their missing color, and "two halves" do not make a whole.

        Creature combo entries are excluded from the demand sum.
        Reanimator targets (Griselbrand, Archon of Cruelty), Living-
        End cyclers, and Through-the-Breach fatties live in combo_sets
        as the *payoff*, not the *enabler* — they are discarded /
        cycled / milled into the graveyard, not hard-cast.  Counting
        their {B}{B}{B}{B}-style pips inflates demand and forces
        false-positive mulligans on hands that can actually fire the
        combo through the spell-side path.  This filter is mechanic-
        based (card type, not name) and works for every reanimator-
        / cascade-style archetype."""
        supply = cls._hand_color_supply(lands)
        gaps_per_path = []
        for combo_set in combo_sets:
            in_hand_combo = [c for c in hand if c.name in combo_set]
            if not in_hand_combo:
                continue  # this path isn't even partially present
            demand: set = set()
            for c in in_hand_combo:
                if c.template.is_creature:
                    continue  # discarded/cycled, not cast
                demand |= cls._card_color_demand(c)
            gap = demand - supply
            if not gap:
                return set()  # this path is color-sound — keep
            gaps_per_path.append(gap)
        if not gaps_per_path:
            # No combo path even partially in hand — caller handled
            # this earlier (missing-piece check).  Don't double-fault.
            return set()
        # Every present path has at least one missing color.  Return
        # the union so the log message names them all.
        out: set = set()
        for g in gaps_per_path:
            out |= g
        return out

    def _card_keep_score(self, card: "CardInstance", hand: List["CardInstance"]) -> float:
        """Score a card for mulligan bottom. Higher = more valuable to keep."""
        from ai.strategy_profile import ArchetypeStrategy
        from engine.cards import Keyword

        # Suspend-only cards are dead in hand — always bottom
        if card.template.cmc == 0 and Keyword.SUSPEND in card.template.keywords:
            return SUSPEND_ONLY_DEAD_PENALTY

        from ai.predicates import count_lands
        score = 0.0
        t = card.template
        lands_in_hand = count_lands(hand)

        if t.is_land:
            score += (KEEP_SCORE_LAND_NEEDED
                      if lands_in_hand <= KEEP_SCORE_LAND_FLOOD_THRESHOLD
                      else KEEP_SCORE_LAND_FLOOD)
            if t.produces_mana:
                score += len(t.produces_mana) * KEEP_SCORE_LAND_PRODUCES_BONUS
        else:
            score += max(0, KEEP_SCORE_CMC_INVERTED_CEIL - t.cmc)
            if "removal" in t.tags:
                score += KEEP_SCORE_REMOVAL_TAG
            if "threat" in t.tags:
                score += KEEP_SCORE_THREAT_TAG
            if "early_play" in t.tags:
                score += (KEEP_SCORE_EARLY_PLAY_AT_HOME
                          if self.archetype == ArchetypeStrategy.AGGRO
                          else KEEP_SCORE_EARLY_PLAY_AWAY)
            if "combo" in t.tags:
                score += (KEEP_SCORE_COMBO_AT_HOME
                          if self.archetype == ArchetypeStrategy.COMBO
                          else KEEP_SCORE_COMBO_AWAY)
            if "counterspell" in t.tags:
                score += (KEEP_SCORE_COUNTERSPELL_AT_HOME
                          if self.archetype in (ArchetypeStrategy.CONTROL,
                                                ArchetypeStrategy.TEMPO)
                          else KEEP_SCORE_COUNTERSPELL_AWAY)
        return score

    @staticmethod
    def reason(hand: List["CardInstance"], lands: List["CardInstance"], spells: List["CardInstance"], keep: bool) -> str:
        """Generate a human-readable mulligan reason."""
        from ai.gameplan import DEFAULT_MULLIGAN_CMC_PROFILE
        cheap_cmc = DEFAULT_MULLIGAN_CMC_PROFILE["cheap"]
        land_count = len(lands)
        cheap = sum(1 for s in spells if (s.template.cmc or 0) <= cheap_cmc)
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
