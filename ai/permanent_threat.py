"""Marginal-contribution threat scoring.

    threat(P) = V_O(B) - V_O(B \\ {P})

where V_O is `ai.clock.position_value` evaluated from the owning
player's perspective.  The "threat" of a permanent is the drop in
its controller's position value that occurs when it is removed —
exactly the quantity that a targeted removal or burn spell is
trying to take off the board.

Scaling mechanics (equipment `for each artifact`, creature
`+N/+N for each ...`, domain, delirium, graveyard scalers) fall out
of this formula automatically.  They are all already reflected in
`CardInstance.power` / `.toughness`, which recompute dynamically
from the live battlefield.  Briefly removing the card from the
owner's battlefield (and restoring it via `try`/`finally`) is
enough to re-trigger every dependent computation — no per-pattern
bolt-on is required.

The marginal formula has one structural blind spot, addressed by
``_equipment_ceiling_for_creature`` and ``_equipment_threat_when_unattached``
below: an UNATTACHED equipment with a `gets +N/+M …` oracle modifier
contributes zero to the dynamic P/T of any creature today, so the
marginal-contribution answer for both the equipment and its potential
recipients is mechanically zero.  Strategically, however, the equipment
is itself a finisher — its value is the option-to-attach next turn.
The two helpers below project that ceiling.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState, PlayerState


# Oracle pattern for the static equipment buff: `gets +N/+M ...`.
# Non-greedy to allow trailing text ("for each artifact", "and has
# trample", "until end of turn", etc.) — we only need the +N/+M
# numerals.  Lower-cased oracle text is required at the call site.
_EQUIPMENT_BUFF_RE = re.compile(r'gets\s+\+(\d+)/\+(\d+)')

# Match the scaler clause when the buff is `+N/+M for each <type>`.
# Only the head of the type-noun matters for selecting the right
# counter helper.
_EQUIPMENT_FOR_EACH_RE = re.compile(
    r'gets\s+\+\d+/\+\d+\s+for\s+each\s+'
    r'(artifact\s+and/?or\s+enchantment'
    r'|enchantment\s+and/?or\s+artifact'
    r'|artifact'
    r'|creature'
    r'|enchantment'
    r'|land)'
)


def _is_equipment(card: "CardInstance") -> bool:
    """True iff the card has the Equipment subtype."""
    subtypes = getattr(card.template, 'subtypes', ()) or ()
    # Subtype enum values are case-sensitive in the DB ("Equipment").
    return any(str(st) == 'Equipment' for st in subtypes)


def _equipment_unattached(equipment: "CardInstance") -> bool:
    """True iff the equipment is on the battlefield and not currently
    bound to a creature.  Mirrors the instance-tag convention used
    by ``GameState.equip_creature``: attaching adds
    ``equipment_attached`` and removes ``equipment_unattached``."""
    tags = getattr(equipment, 'instance_tags', set()) or set()
    if 'equipment_attached' in tags:
        return False
    return True


def _equipped_creature_id(equipment: "CardInstance",
                           owner_battlefield) -> Optional[int]:
    """Return the instance_id of the creature this equipment is
    currently attached to, or None if unattached.  We check by
    scanning owner's battlefield for an `equipped_<iid>` tag whose
    iid matches ``equipment.instance_id``.  Mirrors the rebind
    semantics already used by ``CardInstance._dynamic_base_power``.
    """
    tag = f"equipped_{equipment.instance_id}"
    for c in owner_battlefield:
        tags = getattr(c, 'instance_tags', set()) or set()
        if tag in tags:
            return c.instance_id
    return None


def _count_for_each_target(controller: "PlayerState", scaler_key: str) -> int:
    """Count permanents matching the `for each <scaler_key>` clause
    in equipment oracle text.  Mirrors the engine-side counter logic
    in ``CardInstance._get_artifact_count`` /
    ``_get_artifact_or_enchantment_count``: artifact-typed lands
    are part of the mana base and do *not* contribute to the
    scaler count.
    """
    from engine.cards import CardType

    bf = controller.battlefield
    if scaler_key.startswith('artifact and/or enchantment') or \
       scaler_key.startswith('enchantment and/or artifact'):
        n = 0
        for c in bf:
            types = c.template.card_types
            if (CardType.ARTIFACT in types or
                    CardType.ENCHANTMENT in types):
                n += 1
        return n
    if scaler_key == 'artifact':
        return sum(1 for c in bf
                   if CardType.ARTIFACT in c.template.card_types)
    if scaler_key == 'enchantment':
        return sum(1 for c in bf
                   if CardType.ENCHANTMENT in c.template.card_types)
    if scaler_key == 'creature':
        return sum(1 for c in bf
                   if CardType.CREATURE in c.template.card_types)
    if scaler_key == 'land':
        return sum(1 for c in bf
                   if CardType.LAND in c.template.card_types)
    return 0


def _parse_equipment_buff(equipment: "CardInstance",
                            controller: "PlayerState") -> Optional[int]:
    """Project the power bonus this equipment would grant a creature
    if equipped now.  Combines the static `gets +N/+M` numeral with
    any `for each <type>` scaler.  Returns ``None`` if the oracle
    text has no static `+N/+M` clause (e.g. Lightning Greaves —
    haste/shroud only) so the caller can skip the ceiling lift.
    """
    oracle = (equipment.template.oracle_text or '').lower()
    m_buff = _EQUIPMENT_BUFF_RE.search(oracle)
    if not m_buff:
        return None
    flat_power = int(m_buff.group(1))
    m_scaler = _EQUIPMENT_FOR_EACH_RE.search(oracle)
    if not m_scaler:
        # Flat-modifier equipment (Colossus Hammer, Bonesplitter,
        # Sword cycle) — no `for each` multiplier.
        return flat_power
    scaler_key = m_scaler.group(1)
    count = _count_for_each_target(controller, scaler_key)
    # `+N/+M for each X` reads as N × X — the printed +N is the per-X
    # increment, not a flat baseline (Cranial Plating's printed +1/+0
    # is per-artifact).
    return flat_power * count


def _ceiling_per_turn_lift(equipment: "CardInstance",
                             creature: "CardInstance",
                             controller: "PlayerState") -> float:
    """Per-turn clock lift on `creature` from attaching `equipment`.

    Computes the delta `creature_clock_impact(p+buff, …) -
    creature_clock_impact(p, …)` and converts it to the same
    threat-value scale used elsewhere in the targeting pipeline
    (`creature_threat_value` / Goblin Guide ≈ 8.3).  Returns 0.0 if
    the equipment is non-pump or `creature` is not a legal target.
    """
    if not creature.template.is_creature:
        return 0.0
    if creature not in controller.battlefield:
        return 0.0

    buff = _parse_equipment_buff(equipment, controller)
    if buff is None or buff <= 0:
        return 0.0

    from ai.clock import creature_clock_impact
    from ai.ev_evaluator import CREATURE_VALUE_OUTER_SCALE, _DEFAULT_SNAP

    # Use the same context-free snapshot as `creature_threat_value`
    # so the ceiling-lift scale is comparable to the baseline threat
    # values produced by the rest of the threat-targeting pipeline
    # (Goblin Guide ≈ 8.3, Memnite-vanilla ≈ 1.15).
    snap = _DEFAULT_SNAP

    p_now = creature.power or 0
    tough = creature.toughness or 0
    kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
           for kw in getattr(creature.template, 'keywords', set())}

    base = creature_clock_impact(p_now, tough, kws, snap)
    lifted = creature_clock_impact(p_now + buff, tough, kws, snap)
    return (lifted - base) * CREATURE_VALUE_OUTER_SCALE


def _equipment_ceiling_for_creature(creature: "CardInstance",
                                      controller: "PlayerState",
                                      game: "GameState") -> float:
    """Return the maximum equipment-ceiling threat lift `creature`
    receives from any unattached/rebindable equipment on
    ``controller``'s battlefield.

    Class size: applies to every Modern Equipment with a `gets
    +N/+M …` oracle — Cranial Plating, Nettlecyst, Bonesplitter,
    Colossus Hammer, Embercleave, Sword cycle, future printings.
    Detection is purely oracle + subtype; no card names appear in
    code paths.

    Discount semantics (re-attach feasibility):
      * unattached equipment → full ceiling lift
      * attached to *another* creature → discounted lift, since
        re-binding costs the equip cost in mana again. Discount
        magnitude = ``EQUIPMENT_CEILING_REATTACH_DISCOUNT``.
      * attached to *this* creature → no marginal lift; the buff
        is already in the dynamic P/T.
    """
    from ai.scoring_constants import EQUIPMENT_CEILING_REATTACH_DISCOUNT

    if not creature.template.is_creature:
        return 0.0
    bf = controller.battlefield
    if creature not in bf:
        return 0.0

    best = 0.0
    for perm in bf:
        if not _is_equipment(perm):
            continue
        attached_to = _equipped_creature_id(perm, bf)
        if attached_to == creature.instance_id:
            # Already attached to this creature — buff already in
            # dynamic P/T; no ceiling on top.
            continue
        lift = _ceiling_per_turn_lift(perm, creature, controller)
        if attached_to is not None:
            # Equipment committed to a different creature; rebind
            # feasibility discount.
            lift *= EQUIPMENT_CEILING_REATTACH_DISCOUNT
        if lift > best:
            best = lift
    return best


def _equipment_threat_when_unattached(equipment: "CardInstance",
                                        owner: "PlayerState",
                                        game: "GameState") -> float:
    """Strategic threat of an unattached equipment with a
    `gets +N/+M …` modifier.

    Marginal-contribution returns 0.0 here (see module docstring),
    but strategically the equipment is itself a finisher — its value
    is the maximum ceiling-lift it would deliver to its best legal
    equip target on owner's side, projected across the residency
    window.  Returns 0.0 if the equipment has no static +N/+M
    oracle clause or if owner controls no creatures.

    Scale note: this routine uses cumulative residency (per-turn
    lift × ``EQUIPMENT_RESIDENCY_TURNS``) because the equipment
    itself, as a removal target, represents its multi-turn finisher
    value.  ``_equipment_ceiling_for_creature`` uses only the
    per-turn lift because the threat lift on a *creature* describes
    how dangerous it is to leave that creature alive on a single
    swing — the equipment's residency value is captured separately
    in the equipment's own permanent_threat.
    """
    from ai.scoring_constants import (
        EQUIPMENT_CEILING_NO_TARGET_FALLBACK,
        EQUIPMENT_RESIDENCY_TURNS,
    )

    if not _is_equipment(equipment):
        return 0.0
    if not _equipment_unattached(equipment):
        return 0.0
    buff = _parse_equipment_buff(equipment, owner)
    if buff is None:
        # No `gets +N/+M` static clause — utility equipment
        # (Lightning Greaves, Mask of Memory). The marginal-
        # contribution path handles those correctly.
        return 0.0

    best_per_turn = 0.0
    for c in owner.battlefield:
        if not c.template.is_creature:
            continue
        lift = _ceiling_per_turn_lift(equipment, c, owner)
        if lift > best_per_turn:
            best_per_turn = lift
    if best_per_turn <= 0.0:
        return EQUIPMENT_CEILING_NO_TARGET_FALLBACK
    # Project across the residency window — the equipment is itself
    # a finisher whose threat is its cumulative damage contribution.
    return best_per_turn * EQUIPMENT_RESIDENCY_TURNS


def permanent_threat(card: "CardInstance", owner: "PlayerState",
                     game: "GameState") -> float:
    """Marginal contribution of `card` to `owner`'s position value.

    Returns ``V_owner(battlefield) - V_owner(battlefield \\ {card})``.
    A higher value means removing `card` is a bigger swing against
    `owner`, so the caller (a removal / burn targeter) should
    prefer it.

    `owner` is the player whose board `card` is on — i.e. for an
    opponent's threat we pass `game.players[1 - my_idx]`.  The
    snapshot is built from that player's perspective, so `my_*`
    fields in the snapshot refer to `owner`'s side.

    Returns 0.0 when `card` is not currently on `owner`'s
    battlefield; callers should filter to on-battlefield targets.

    KEY FIX (Bug A): count-based artifact/enchantment fields must be
    frozen between full and partial snapshots. When we pop a card,
    snapshot_from_game will recompute artifact_count from the live
    battlefield, creating state drift. The marginal contribution formula
    is correct in principle (V_full - V_partial), but we must adjust
    counts manually instead of letting them recompute.

    PR-L3 (Phase L follow-up): an UNATTACHED Equipment with a
    `gets +N/+M …` oracle clause gets a special-case ceiling lift
    via ``_equipment_threat_when_unattached``.  The marginal formula
    correctly sees no current dynamic-P/T contribution, but
    strategically the equipment is itself a finisher (its value is
    the option-to-attach to a creature next turn).  See
    `docs/diagnostics/2026-05-04_affinity_plating_threat_undervaluation_audit.md`.
    """
    from ai.ev_evaluator import snapshot_from_game
    from ai.clock import position_value
    from engine.cards import CardType

    bf = owner.battlefield
    idx = -1
    for i, c in enumerate(bf):
        if c is card:
            idx = i
            break
    if idx < 0:
        return 0.0

    owner_idx = owner.player_idx

    full_snap = snapshot_from_game(game, owner_idx)
    v_full = position_value(full_snap)

    removed = bf.pop(idx)
    try:
        partial_snap = snapshot_from_game(game, owner_idx)

        # CRITICAL: Adjust count fields to reflect the removed card's type.
        # snapshot_from_game recomputes counts from the current (popped) state.
        # We want to compare V(full board) - V(board \\ {card}), but the
        # count fields in position_value create state drift: removing an
        # artifact decreases artifact_count, which improves the owner's
        # position_value through the artifact_value term (line 384 in clock.py).
        # This is backward — removing a mana rock should hurt, not help.
        #
        # Solution: restore counts to match the full_snap state, so both
        # snapshots have consistent count-based terms.
        # PR-L1: mirror the snapshot-level rule that artifact lands
        # do NOT contribute to artifact_count.  If we don't gate on
        # ``CardType.LAND not in card_types`` here, popping an
        # artifact land from the battlefield would leave partial_snap
        # with the same artifact_count as full_snap (because the
        # snapshot recompute already excluded lands), and then we'd
        # increment it by 1, creating a phantom +1 delta.
        card_types = card.template.card_types
        is_non_land_artifact = (
            CardType.ARTIFACT in card_types
            and CardType.LAND not in card_types
        )
        if owner_idx == 0:
            # Popped from my side
            if is_non_land_artifact:
                partial_snap.my_artifact_count += 1
            if CardType.ENCHANTMENT in card_types:
                partial_snap.my_enchantment_count += 1
        else:
            # Popped from opp side
            if is_non_land_artifact:
                partial_snap.opp_artifact_count += 1
            if CardType.ENCHANTMENT in card_types:
                partial_snap.opp_enchantment_count += 1

        v_partial = position_value(partial_snap)
    finally:
        bf.insert(idx, removed)

    marginal = v_full - v_partial

    # PR-L3: equipment-ceiling fallback for unattached pump equipment.
    # The marginal formula returns ~0.0 when the equipment is unattached
    # because removing it doesn't change any creature's dynamic P/T.
    # Project the option-to-attach value as the maximum ceiling-lift on
    # any of owner's creatures.  Take the max of the two so the existing
    # marginal-based threat is never *weakened* — only lifted when the
    # ceiling exceeds it.
    if _is_equipment(card) and _equipment_unattached(card):
        ceiling = _equipment_threat_when_unattached(card, owner, game)
        if ceiling > marginal:
            return ceiling

    return marginal
