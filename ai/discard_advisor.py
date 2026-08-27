"""
Discard advisor — lifted from engine/game_state.py (Commit 6).

The legacy `_choose_self_discard` method embedded discard-scoring
heuristics directly in the engine layer, violating the CLAUDE.md
"engine never scores" architectural rule. This module implements
the same decisions using the oracle-text + tag signals that were
inside engine/game_state.py, but exposed via the
`callbacks.GameCallbacks.choose_discard` protocol method.

Installed by the AI callbacks wiring at game-runner setup time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ai.scoring_constants import (
    DISCARD_BIG_CREATURE_BASE,
    DISCARD_BIG_CREATURE_CMC_THRESHOLD,
    DISCARD_COMBO_TUTOR_PROTECT,
    DISCARD_COUNTERSPELL_NUDGE,
    DISCARD_FLASHBACK_BONUS,
    DISCARD_LANDS_EXCESS_BONUS,
    DISCARD_LANDS_GLUT_BONUS,
    DISCARD_LANDS_GLUT_THRESHOLD,
    DISCARD_REMOVAL_NUDGE,
)

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState


def choose_discard(game: "GameState", player_idx: int,
                   hand: List["CardInstance"],
                   self_discard: bool) -> Optional["CardInstance"]:
    """Pick the best card to discard.

    self_discard=True means the player chose to discard (Faithful
    Mending, Wrenn's Resolve random discard, etc.) — the player wants
    to maximise the value of the discard by binning cards that either
    belong in the graveyard (flashback, escape, reanimate targets) or
    are excess (flooded lands, redundant copies).

    self_discard=False means an opponent forced the discard
    (Thoughtseize, Grief, Inquisition, Duress). The caster wants to
    strip the most threatening card from the victim's hand. Bug E2:
    we route this through `ai.ev_evaluator.choose_card_to_strip`,
    which uses `creature_threat_value()` for creatures and the
    victim's declared gameplan keystones (critical_pieces /
    always_early / mulligan_keys) plus tag weights for non-creatures.
    That picker filters out lands (Thoughtseize text: "nonland card")
    and returns None for an all-lands hand — the engine loop stops in
    that case.
    """
    if not hand:
        raise ValueError("choose_discard called with empty hand")
    if len(hand) == 1:
        # Single-card hand: if the lone card is a land and this is an
        # opponent-forced (non-self) discard, honour the "nonland" clause
        # by returning None. Otherwise return the only card available.
        only = hand[0]
        if (not self_discard
                and getattr(only.template, 'is_land', False)):
            return None
        return only

    if not self_discard:
        # Bug E2 fix — opponent-forced discard (Thoughtseize / Duress /
        # Inquisition / Grief). Delegate to the AI threat-scoring helper,
        # passing the victim's gameplan so its declared keystones can be
        # consulted. No hardcoded card names here.
        from ai.ev_evaluator import choose_card_to_strip, snapshot_from_game
        opp_gameplan = None
        player = game.players[player_idx]
        deck_name = getattr(player, 'deck_name', '') or ''
        if deck_name:
            try:
                from ai.gameplan import get_gameplan
                opp_gameplan = get_gameplan(deck_name)
            except Exception:
                opp_gameplan = None
        caster_idx = 1 - player_idx
        caster_snap = snapshot_from_game(game, caster_idx)
        # W1b-11 attack-imminence shim:
        #
        # When the discard CASTER (the player who is *not* the
        # hand owner, i.e. ``1 - player_idx``) is in panic-life
        # territory — they die in 2 attack steps to the attacker's
        # current average power — the right rule is "rip the
        # imminent attacker," not the static threat top.
        #
        # The panic condition is a derived rules expression:
        # ``caster_life ≤ 2 × opp_avg_attack``.  ``opp_avg_attack``
        # is ``snap.opp_power / max(1, snap.opp_creature_count)``
        # — the per-attacker damage the caster will eat next turn.
        # The integer ``2`` is the rules-style "turns to lethal"
        # multiplier (combat_clock idiom: two combat steps).
        #
        # The imminence ranking comes from
        # ``ai.bhi.predicted_turn_of_cast`` (composition of
        # ``effective_cmc`` + opp mana availability — no new
        # constants).  In panic mode we sort the hand by
        # ``(predicted_turn_of_cast asc, static_score desc)``: pick
        # the most imminently-castable card; static score is the
        # tie-break.  Outside panic we delegate to the unchanged
        # static-score picker (``choose_card_to_strip``).
        return _choose_for_caster(
            game, victim_idx=player_idx, hand=hand,
            opp_gameplan=opp_gameplan,
        ) or choose_card_to_strip(hand, caster_snap, opp_gameplan)

    # Self-discard: score-based choice, highest score = discard first.
    from ai.predicates import count_lands
    from ai.ev_evaluator import snapshot_from_game
    player = game.players[player_idx]
    lands_on_field = len(player.lands)
    lands_in_hand = count_lands(hand)

    # Race-state context (2026-08-27 reanimator-pair secondary lever):
    # discard choice is evaluated against the live plan and the race,
    # not card-value in isolation.
    snap = snapshot_from_game(game, player_idx)

    # Every "this card is good IN THE GRAVEYARD later" bonus (escape,
    # flashback, reanimation fuel, generic big-creature) prices value
    # that only exists if we get future turns.  `urgency_factor` is the
    # existing EVSnapshot primitive for exactly that fraction (1.0 =
    # safe, 0.0 = dying now) — reuse it as the discount instead of
    # inventing a threshold.
    future_discount = snap.urgency_factor

    # Lethal-range clock: the opponent kills us within two combat
    # steps.  `opp_clock_discrete` is the turns-to-lethal primitive;
    # the 2 is the same "dies in two attack steps" rules multiplier
    # the W1b-11 panic shim documents (EXEMPT_VALUES).
    under_lethal_clock = (snap.opp_power > 0
                          and snap.opp_clock_discrete <= 2)

    def _defensive_retention(card: "CardInstance") -> float:
        """Value forfeited by pitching a deployable blocker while the
        race is lethal-range.  Priced by the existing Phase-2a
        `ai.clock.opportunity_cost` primitive (clock-impact units ×
        CREATURE_VALUE_OUTER_SCALE) — no new scale, no new constants.
        A creature we cannot deploy in time (effective cost above the
        battlefield's mana plus the next land drop, CR 305.1's one
        land per turn) blocks nothing and retains nothing."""
        if not under_lethal_clock or not card.template.is_creature:
            return 0.0
        from ai.clock import opportunity_cost
        from ai.effective_cmc import effective_cmc
        deployable_mana = lands_on_field + 1  # next turn's land drop
        if effective_cmc(card, snap, game=game,
                         player_idx=player_idx) > deployable_mana:
            return 0.0
        return opportunity_cost(card, player, snap)

    # An opposing graveyard-hate permanent (typed field
    # has_graveyard_hate, parsed once at DB load) means the graveyard
    # cannot HOLD the plan's resources: binning a card there loses it
    # instead of relocating it.
    gy_safe = _graveyard_is_safe(game, player_idx)

    # GV-1: Gameplan-aware reanimation-fuel boost.
    #
    # When the controller's deck declares a FILL_RESOURCE goal targeting
    # the graveyard with resource_min_cmc >= 5, creatures at or above
    # that CMC threshold ARE the reanimation targets the deck is trying
    # to bin. They must outrank flashback cantrips, evoke removal bodies,
    # and any other generic "good-to-bin" heuristic — binning the payoff
    # is the entire point of the self-discard in these archetypes.
    #
    # Pure gameplan lookup (no card names). Any deck whose JSON declares
    # the same FILL_RESOURCE / graveyard / min_cmc shape benefits
    # identically.
    reanimation_min_cmc = _reanimation_fuel_min_cmc(game, player_idx)
    keystones = _declared_keystones(game, player_idx)

    def discard_score(card: "CardInstance") -> float:
        t = card.template
        score = 0.0

        # Reanimation fuel — rank above every non-fuel "good-to-bin"
        # bonus (flashback +90, escape +100, removal-creature +95).
        # Uses the gameplan's declared min_cmc so the policy transfers
        # to any reanimator deck that later registers the same shape.
        # The bonus requires the graveyard to actually HOLD the fuel:
        # with an opposing hate permanent up, binning the payoff feeds
        # the hate instead of the plan (IR s60500 G2 replay), so the
        # card falls through to the keystone-protection branch below.
        if (gy_safe
                and reanimation_min_cmc is not None
                and t.is_creature
                and t.cmc >= reanimation_min_cmc):
            # Base clears the flashback/escape ceiling (100) and scales
            # with CMC so the fattest payoff wins ties. CMC 5 -> 105,
            # CMC 8 -> 108. Guarantees Griselbrand (CMC 8) > Faithful
            # Mending flashback (90) and > Solitude removal-creature
            # stacked bonuses (95).
            score += (100 + t.cmc) * future_discount
            # Short-circuit (other bonuses are irrelevant), but the
            # race-state retention still applies: a fuel body that can
            # block is survival first, fuel second.
            return score - _defensive_retention(card)

        # Cards with flashback/escape WANT to be in the graveyard.
        if t.escape_cost is not None:
            score += 100 * future_discount  # Escape (Phlage) — great to discard
        # A plain flashback card is happy in the graveyard — you flash
        # it back later, so binning it to hand size loses nothing.
        # EXCEPT a card that GRANTS flashback to the whole graveyard
        # (Past-in-Flames pattern): its value comes from being CAST
        # FROM HAND to replay the yard as a chain payoff, so binning it
        # discards a live line piece, not fuel.  Detection is the typed
        # field populated at DB load (grants_flashback_to_gy_spells) —
        # no runtime oracle parse, no card names; the class is every
        # card that hands the graveyard back to you.
        if ('flashback' in t.tags
                and not getattr(t, 'grants_flashback_to_gy_spells', False)):
            score += DISCARD_FLASHBACK_BONUS * future_discount

        # High-CMC creatures are reanimation targets (generic fallback
        # for decks that don't declare a FILL_RESOURCE graveyard goal —
        # e.g. a random midrange hand with an accidental fat body).
        # EXCEPT a declared gameplan keystone in a deck with NO
        # graveyard plan: that creature is the payoff the deck exists
        # to CAST, not fuel to bin (Amulet discarded both copies of its
        # 6-drop payoff to hand size — 2026-07-06 diagnostic). Same
        # keystone set the forced-discard picker consults; reanimators
        # never reach here (GV-1 short-circuits above).
        if t.is_creature and t.cmc >= DISCARD_BIG_CREATURE_CMC_THRESHOLD:
            if t.name in keystones:
                score -= DISCARD_COMBO_TUTOR_PROTECT
            else:
                score += (DISCARD_BIG_CREATURE_BASE + t.cmc) * future_discount

        # Excess lands (4+ in hand with 3+ already on battlefield).
        if t.is_land:
            if lands_in_hand > 1 and lands_on_field >= DISCARD_LANDS_GLUT_THRESHOLD:
                score += DISCARD_LANDS_GLUT_BONUS
            elif lands_in_hand > 2:
                score += DISCARD_LANDS_EXCESS_BONUS

        # Protection/reactive spells are lower priority to keep.
        if 'counterspell' in t.tags and not t.is_creature:
            score += DISCARD_COUNTERSPELL_NUDGE

        # Combo pieces and key spells should be kept (lower score).
        # Exception: high-CMC creatures are reanimation targets.
        if any(tag in t.tags for tag in ('combo', 'tutor')):
            if not (t.is_creature and t.cmc >= DISCARD_BIG_CREATURE_CMC_THRESHOLD):
                score -= DISCARD_COMBO_TUTOR_PROTECT

        # Removal is moderately important — slightly prefer to keep.
        if 'removal' in t.tags:
            score += DISCARD_REMOVAL_NUDGE

        # Race state: a deployable blocker's defensive value enters
        # the ranking under a lethal-range clock (0.0 otherwise).
        score -= _defensive_retention(card)

        return score

    # Live-plan role guard: the LAST accessible copy of a role the
    # gameplan requires is never pitched while the plan is live.  A
    # copy that stays role-usable from the graveyard after the discard
    # (safe-graveyard fuel, flashback) is relocated, not lost, and
    # stays pitchable.  If every card in hand is protected we must
    # still discard something — fall back to the full hand.
    protected = _plan_role_protected_ids(game, player_idx, hand, gy_safe)
    eligible = [c for c in hand if id(c) not in protected] or hand

    return max(eligible, key=discard_score)


# Role buckets whose last reachable copy strands the declared plan.
# These are gameplan card_roles KEYS (role vocabulary), not card names:
# payoffs/enablers are the execution conjunction, protection keeps the
# executed payoff alive. Value roles (interaction, engines, rituals,
# removal) are replaceable and stay unguarded.
_PLAN_ROLE_BUCKETS = frozenset({"payoffs", "enablers", "protection"})

# Roles whose absence kills the plan outright (protection is optional
# for execution — a plan without it is worse, not dead).
_PLAN_LIVENESS_ROLES = frozenset({"payoffs", "enablers"})


def _graveyard_is_safe(game: "GameState", player_idx: int) -> bool:
    """False when any opposing battlefield permanent carries the typed
    `has_graveyard_hate` field (parsed once at DB load) — the graveyard
    then cannot hold the plan's resources."""
    opp = game.players[1 - player_idx]
    return not any(
        getattr(perm.template, 'has_graveyard_hate', False)
        for perm in opp.battlefield
    )


def _plan_role_map(game: "GameState", player_idx: int):
    """(role_name -> card-name set) for the plan roles the player's
    gameplan declares across its goals, restricted to
    `_PLAN_ROLE_BUCKETS`.  Empty dict when no gameplan."""
    player = game.players[player_idx]
    deck_name = getattr(player, 'deck_name', '') or ''
    if not deck_name:
        return {}
    try:
        from ai.gameplan import get_gameplan
    except ImportError:
        return {}
    plan = get_gameplan(deck_name)
    if plan is None:
        return {}
    roles: dict = {}
    for goal in plan.goals:
        for role_name, names in (goal.card_roles or {}).items():
            if role_name in _PLAN_ROLE_BUCKETS and names:
                roles.setdefault(role_name, set()).update(names)
    return roles


def _usable_from_graveyard(card: "CardInstance", gy_safe: bool,
                            reanimation_min_cmc) -> bool:
    """Would this card still serve its plan role FROM the graveyard?

    True for the reanimation resource (a creature at or above the
    declared FILL_RESOURCE min CMC — the graveyard is where the plan
    wants it) and for self-recurring spells (flashback / escape), in
    both cases only while the graveyard is safe."""
    if not gy_safe:
        return False
    t = card.template
    if (reanimation_min_cmc is not None and t.is_creature
            and t.cmc >= reanimation_min_cmc):
        return True
    if t.escape_cost is not None or 'flashback' in (t.tags or set()):
        return True
    return False


def _plan_role_protected_ids(game: "GameState", player_idx: int,
                              hand: List["CardInstance"],
                              gy_safe: bool) -> set:
    """ids of hand cards that are the LAST accessible copy of a
    required plan role while the plan is still live.

    Accessible pool per role = hand + own library (the pilot knows
    their decklist) + graveyard copies that are still role-usable from
    there (`_usable_from_graveyard`).  The reanimation resource is a
    DERIVED role — any creature at/above the gameplan's FILL_RESOURCE
    min CMC — so no card names are consulted for it.

    Plan liveness: every declared `_PLAN_LIVENESS_ROLES` bucket, plus
    the derived resource role when declared, has >= 1 accessible copy.
    A dead plan protects nothing (the stranded role card may be
    pitched — the negative control in the paired test file)."""
    roles = _plan_role_map(game, player_idx)
    reanimation_min_cmc = _reanimation_fuel_min_cmc(game, player_idx)
    if not roles and reanimation_min_cmc is None:
        return set()

    player = game.players[player_idx]

    def _pool():
        for c in hand:
            yield c, 'hand'
        for c in player.library:
            yield c, 'library'
        for c in player.graveyard:
            yield c, 'graveyard'

    def _accessible(card, zone) -> bool:
        if zone == 'graveyard':
            return _usable_from_graveyard(card, gy_safe,
                                          reanimation_min_cmc)
        return True

    # Count accessible copies per role (names) and for the derived
    # resource role (predicate).
    role_counts = {r: 0 for r in roles}
    resource_count = 0
    per_card_roles: dict = {}  # id(card) -> set of role keys it fills
    for card, zone in _pool():
        if not _accessible(card, zone):
            continue
        t = card.template
        for r, names in roles.items():
            if card.name in names:
                role_counts[r] += 1
                if zone == 'hand':
                    per_card_roles.setdefault(id(card), set()).add(r)
        if (reanimation_min_cmc is not None and t.is_creature
                and t.cmc >= reanimation_min_cmc):
            resource_count += 1
            if zone == 'hand':
                per_card_roles.setdefault(id(card), set()).add('_resource')

    # Liveness: every required role reachable.
    for r in _PLAN_LIVENESS_ROLES:
        if r in roles and role_counts[r] == 0:
            return set()
    if reanimation_min_cmc is not None and resource_count == 0:
        return set()

    protected: set = set()
    for card in hand:
        card_roles = per_card_roles.get(id(card))
        if not card_roles:
            continue
        if _usable_from_graveyard(card, gy_safe, reanimation_min_cmc):
            # Discarding relocates it within the plan's reach.
            continue
        for r in card_roles:
            count = (resource_count if r == '_resource'
                     else role_counts[r])
            if count <= 1:  # this card IS the last accessible copy
                protected.add(id(card))
                break
    return protected


def _declared_keystones(game: "GameState", player_idx: int) -> set:
    """The player's own gameplan-declared keystone card names
    (critical_pieces / mulligan_keys / always_early) — the same
    keystone fields the forced-discard picker and the mulligan
    bottoming protection consult. Empty set when no gameplan."""
    player = game.players[player_idx]
    deck_name = getattr(player, 'deck_name', '') or ''
    if not deck_name:
        return set()
    try:
        from ai.gameplan import get_gameplan
    except ImportError:
        return set()
    plan = get_gameplan(deck_name)
    if plan is None:
        return set()
    names: set = set()
    for field in ('critical_pieces', 'mulligan_keys', 'always_early'):
        names.update(getattr(plan, field, None) or [])
    return names


def _reanimation_fuel_min_cmc(game: "GameState",
                              player_idx: int) -> Optional[int]:
    """Return the CMC threshold of the player's declared reanimation plan,
    or None if the gameplan has no FILL_RESOURCE / graveyard goal.

    Looks for a goal with goal_type=FILL_RESOURCE,
    resource_zone="graveyard", and resource_min_cmc >= 5 — the canonical
    "fill the graveyard with a fat creature to reanimate" shape used by
    Goryo's Vengeance and any future reanimator archetype. No card names
    are consulted.
    """
    player = game.players[player_idx]
    deck_name = getattr(player, 'deck_name', '') or ''
    if not deck_name:
        return None
    try:
        from ai.gameplan import get_gameplan, GoalType
    except ImportError:
        return None
    plan = get_gameplan(deck_name)
    if plan is None:
        return None
    # Reanimation plan: FILL_RESOURCE -> graveyard -> min_cmc >= 5.
    # The >=5 floor avoids false positives from generic graveyard-value
    # decks (e.g. delirium, escape fuel) where binning a 2-drop is
    # fine. Reanimator payoffs (Goryo's / Persist / Unburial Rites)
    # target 5+ CMC creatures; anything cheaper can be hard-cast
    # normally and doesn't need the self-discard chute.
    # Floor is shared with DISCARD_BIG_CREATURE_CMC_THRESHOLD — same
    # rules-derived "5+ CMC = reanimation target" definition.
    REANIMATION_FUEL_FLOOR = DISCARD_BIG_CREATURE_CMC_THRESHOLD
    for goal in plan.goals:
        if goal.goal_type != GoalType.FILL_RESOURCE:
            continue
        if getattr(goal, 'resource_zone', None) != 'graveyard':
            continue
        min_cmc = getattr(goal, 'resource_min_cmc', 0) or 0
        if min_cmc >= REANIMATION_FUEL_FLOOR:
            return min_cmc
    return None


def _choose_for_caster(game: "GameState", victim_idx: int,
                       hand: List["CardInstance"],
                       opp_gameplan) -> Optional["CardInstance"]:
    """Apply the attack-imminence shim (W1b-11).

    Returns the imminent-attacker pick when the discard CASTER is in
    panic life and the victim's hand has at least one card whose
    ``predicted_turn_of_cast`` is finite.  Returns ``None`` when the
    static-score ranking should win (no panic, no creatures pressuring,
    all-lands hand).

    No card-name or deck-name conditionals.  Panic is derived from
    snapshot fields; imminence is derived from the W0-F effective_cmc
    primitive via ``bhi.predicted_turn_of_cast``.
    """
    # Imports deferred to keep the discard path cheap when the
    # caster never reaches panic life (the common case).
    from ai.bhi import predicted_turn_of_cast
    from ai.ev_evaluator import (
        score_card_for_opponent_strip,
        snapshot_from_game,
    )

    caster_idx = 1 - victim_idx
    snap = snapshot_from_game(game, caster_idx)

    # Panic condition: caster's life ≤ 2 × per-attacker damage.
    # Both operands derive from snapshot fields:
    #   * ``snap.my_life`` — the caster's life (snap is from the
    #     caster's perspective).
    #   * ``snap.opp_power / max(1, snap.opp_creature_count)`` —
    #     the average attacker's power (rules-derived: total power
    #     divided by attacker count).
    # The literal ``2`` is the standard "turns-to-lethal"
    # multiplier the combat_clock layer already uses (life_as_resource
    # treats ``life / incoming_power`` as turns of survival; ``2``
    # = "dies in two attack steps").  Exempt under the magic-number
    # ratchet's EXEMPT_VALUES.
    opp_attackers = max(1, int(snap.opp_creature_count or 0))
    opp_avg_attack = float(snap.opp_power or 0) / opp_attackers
    if opp_avg_attack <= 0:
        # No attacking pressure → no panic, no override.
        return None
    panic = snap.my_life <= 2 * opp_avg_attack
    if not panic:
        return None

    nonland = [c for c in hand
               if not getattr(c.template, 'is_land', False)]
    if not nonland:
        return None

    # Rank by (predicted_turn_of_cast asc, static_score desc, idx asc).
    # Lowest predicted turn wins (most imminent); static score is the
    # tie-break so a vanilla cantrip doesn't beat a 1-mana threat at
    # the same turn-of-cast.  Stable order on idx for determinism.
    victim_player = game.players[victim_idx]

    def _key(idx_card):
        idx, c = idx_card
        turn = predicted_turn_of_cast(c, snap,
                                       victim_idx=victim_idx,
                                       victim_player=victim_player)
        score = score_card_for_opponent_strip(c, snap, opp_gameplan)
        return (turn, -score, idx)

    ranked = sorted(enumerate(nonland), key=_key)
    return ranked[0][1]
