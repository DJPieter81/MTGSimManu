"""Generic Oracle Text Effect Resolver.

Parses oracle text into executable effects at card load time.
Replaces per-card hardcoded handlers with pattern-based resolution.

This module handles:
- ETB effects (enters the battlefield)
- Spell resolution effects (instants/sorceries)
- Triggered abilities (whenever, when, at the beginning of)
- Static abilities (cost reduction, etc.)

Design: each pattern is a (regex, handler_function) pair. When oracle text
matches a pattern, the handler is registered for that card. Multiple
patterns can match the same card (e.g., Omnath has ETB + landfall).
"""
from __future__ import annotations
import re
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.cards import CardInstance, CardTemplate


def _pick_damage_target(game: "GameState", controller: int,
                         amount: int) -> Optional["CardInstance"]:
    """Oracle-driven target picker for "deal N damage to any target".

    Returns the best killable opposing creature, or None (meaning
    "go face"). No card names — threat is scored from oracle text
    amplifiers (attack triggers, scaling clauses) plus raw P/T.

    Face is preferred over creature targeting when the face value
    (`amount * FACE_VALUE_PER_DAMAGE`) exceeds the creature's
    threat score. This means:
      * Phlage ETB (3 dmg) will kill a 2/2 Signal Pest because the
        battle-cry amplifier pushes its threat above face value.
      * A spell facing a pure-body 2/2 Grizzly Bear still goes face
        if the face damage is more valuable than trading for a
        vanilla body.
    """
    opp_idx = 1 - controller
    opp = game.players[opp_idx]
    killable = [
        c for c in opp.creatures
        if ((c.toughness or 0) - getattr(c, 'damage_marked', 0)) <= amount
        and (c.toughness or 0) > 0
    ]
    if not killable:
        return None

    def threat_score(c) -> float:
        # Raw body
        val = (c.power or 0) + (c.toughness or 0) * 0.3
        oracle = (c.template.oracle_text or '').lower()
        name = (c.template.name or '').lower().split(' //')[0].strip()
        # Attack-trigger amplifiers (battle cry, self-named attack triggers).
        # +3 matches the BATTLE_CRY_AMPLIFIER_VP convention used in
        # creature_threat_value (ai/ev_evaluator.py) so engine-level
        # targeting picks the same "high-threat" creatures the AI would
        # prioritise for proactive removal.
        if 'whenever this creature attacks' in oracle:
            val += 3.0
        elif name and f'whenever {name} attacks' in oracle:
            val += 3.0
        # Scaling clauses (for each artifact/creature/land/card)
        if re.search(r'for each (artifact|creature|land|card)', oracle):
            val += 3.0
        # Large bodies beyond typical burn range
        val += max(0, (c.power or 0) - 3) * 0.8
        # Overkill waste: damage above what's needed to kill is lost face burn.
        remaining = (c.toughness or 0) - getattr(c, 'damage_marked', 0)
        waste = max(0, amount - remaining)
        val -= waste * 0.8
        return val

    best = max(killable, key=threat_score)
    # Rules constant: face-burn value per damage. 1.0 × amount so
    # "3 damage to face" = 3.0 threat floor. Creatures need genuine
    # ongoing value (Ragavan-class attack triggers, scaling threats,
    # big bodies) to outbid face; 1-toughness battle-cry carriers
    # don't, because the overkill waste matches the amplifier bonus.
    # This matches the pre-refactor Phlage-goes-face default for
    # small aggro boards while still redirecting burn onto real
    # threats (Murktide, Tarmogoyf, Cranial Plating-attached bombs).
    FACE_VALUE_PER_DAMAGE = 1.0
    return best if threat_score(best) > amount * FACE_VALUE_PER_DAMAGE else None


def resolve_etb_from_oracle(game: "GameState", card: "CardInstance",
                             controller: int):
    """Resolve ETB effects via classifier-tag dispatch.

    Scope (R3): the surveil-N land cycle is the audit's named target.
    Card-specific ETB handlers continue to live in `EFFECT_REGISTRY`
    (which `zone_transfer._fire_etb_triggers` invokes BEFORE falling
    through to this resolver). This generic resolver handles only
    oracle patterns the W0-A classifier has been trained to recognise.

    Adding a new ETB shape goes through:
      1. Declare a `Tag.ETB_<SHAPE>` in `ai/oracle_classifier.py`.
      2. Append the shape's description to
         `ai/llm_prompts/classify_oracle_v1.md`.
      3. Run `tools/build_oracle_classifier_cache.py` to populate.
      4. Add a tag-gated branch here whose only oracle parse is
         for the rule's numeric amount (assert-fail on mismatch).

    Inline `if "phrase" in oracle and "other" in oracle: …` chains are
    forbidden by the abstraction contract — they are the patchwork
    pattern Wave 2 will delete elsewhere; we don't ADD them here.
    """
    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return

    # ── "When this ~ enters, surveil N" (CR 701.42) ──
    # Class size: the surveil-dual cycle (Meticulous Archive, Elegant
    # Parlor, Thundering Falls, Hedge Maze, Underground Mortuary,
    # Raucous Theater, Commercial District, Undercity Sewers, Shadowy
    # Backstreet, Lush Portico) plus any future printing with the
    # same ETB shape. The dispatch is gated by the oracle classifier
    # tag `Tag.ETB_SURVEIL_N` — same gated-amount-parse pattern as
    # `zone_transfer._fire_on_draw_triggers` uses for ON_DRAW_DAMAGE:
    # the tag confirms the card has the trigger, then the amount N
    # is parsed targetedly from oracle text. Card-name special cases
    # are explicitly forbidden by the abstraction contract.
    from ai.oracle_classifier import Tag, has_tag
    if has_tag(card.name, Tag.ETB_SURVEIL_N):
        m = re.search(r'surveil\s+(\d+)', oracle)
        if m is None:
            raise AssertionError(
                f"{card.name!r} carries Tag.ETB_SURVEIL_N but its "
                f"oracle text does not match the 'surveil N' shape — "
                f"classifier and oracle are out of sync."
            )
        game.surveil(controller, int(m.group(1)))


def resolve_spell_from_oracle(game: "GameState", card: "CardInstance",
                               controller: int, targets: list = None) -> bool:
    """Resolve instant/sorcery effects by parsing oracle text.

    Called when a spell resolves AND no EFFECT_REGISTRY handler took it.
    Returns True when an effect was applied, so callers can skip the
    legacy ability-description fallback.
    """
    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return False

    opponent = 1 - controller
    handled = False

    # ── Energy-damage spells (R2): "target creature or planeswalker.
    #     You get {E}^k, then you may pay any amount of {E}. ~ deals
    #     that much (additional) damage to that permanent."
    #     Class: FDN/MH3 energy-instant family (Galvanic Discharge,
    #     Static Discharge, ...). Oracle-pattern keyed — no card name.
    #     Composes target_solver.py:155 (cast-time legality, CR 601.2c)
    #     and ~~deal_damage~~ direct target mutation (damage_marked /
    #     loyalty_counters). Base damage and self-gen energy count are
    #     both derived from oracle text. Engine commits a deterministic
    #     min-to-kill energy spend (CR 117.2 — cost paid at cast; with
    #     no AI hook yet, this is the engine-rational commitment).
    if (('target creature or planeswalker' in oracle
            or 'choose target creature or planeswalker' in oracle)
            and 'you get {e}' in oracle
            and 'pay any amount of {e}' in oracle
            and 'that much' in oracle and 'damage' in oracle):
        base_match = re.search(
            r'deals?\s+(\d+)\s+damage\s+to\s+target\s+creature\s+or\s+planeswalker',
            oracle)
        base_damage = int(base_match.group(1)) if base_match else 0
        gain_match = re.search(r'you get\s+((?:\{e\}\s*)+)', oracle)
        self_gen_energy = (
            gain_match.group(1).count('{e}') if gain_match else 0)
        chosen = None
        for tid in (targets or []):
            if tid == -1:
                continue  # face-marker — illegal target for this spell
            cand = game.get_card_by_id(tid)
            if (cand is not None and cand.zone == "battlefield"
                    and (cand.template.is_creature
                         or 'planeswalker' in
                         [t.value for t in cand.template.card_types])):
                chosen = cand
                break
        if chosen is None:
            # AI did not nominate a legal creature/PW target.
            # Engine cannot redirect to face (audit R2). Pick the
            # highest-threat opp creature or planeswalker as the
            # default; fizzle only when neither exists.
            opp = game.players[1 - controller]
            opp_pw = [c for c in opp.battlefield
                      if 'planeswalker'
                      in [t.value for t in c.template.card_types]]
            candidates = list(opp.creatures) + opp_pw
            if not candidates:
                return True  # fizzle: no legal target
            chosen = max(candidates,
                         key=lambda c: (c.power or 0)
                         + (c.toughness or 0)
                         + getattr(c, 'loyalty_counters', 0))
        player = game.players[controller]
        player.add_energy(self_gen_energy)
        if chosen.template.is_creature:
            remaining = ((chosen.toughness or 0)
                         - getattr(chosen, 'damage_marked', 0))
        else:
            remaining = chosen.loyalty_counters  # CR 119.3
        need_to_kill = max(0, remaining - base_damage)
        spend = (min(need_to_kill, player.energy_counters)
                 if need_to_kill > 0 else 0)
        if spend > 0:
            player.spend_energy(spend)
        total = base_damage + spend
        if total > 0:
            if chosen.template.is_creature:
                chosen.damage_marked = (
                    getattr(chosen, 'damage_marked', 0) + total)
                if chosen.is_dead:
                    game._creature_dies(chosen)
            else:
                chosen.loyalty_counters = max(
                    0, chosen.loyalty_counters - total)
                game.check_state_based_actions()
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"{card.name} deals {total} to {chosen.name} "
            f"(base {base_damage} + {spend} energy)")
        return True

    # ── "Target opponent reveals their hand. You choose a nonland card
    #     and that player discards it." (Thoughtseize, Inquisition) ──
    if 'reveals' in oracle and 'hand' in oracle and 'discard' in oracle:
        opp = game.players[opponent]
        if opp.hand:
            nonlands = [c for c in opp.hand if not c.template.is_land]
            if nonlands:
                # Choose highest-CMC nonland card
                best = max(nonlands, key=lambda c: (c.template.cmc or 0))
                opp.hand.remove(best)
                best.zone = "graveyard"
                game.players[opponent].graveyard.append(best)
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{card.name} discards {best.name}")
                handled = True
        # Life loss for Thoughtseize
        if 'you lose' in oracle and 'life' in oracle:
            m = re.search(r'lose\s+(\d+)\s+life', oracle)
            if m:
                game.players[controller].life -= int(m.group(1))
                handled = True

    # ── "Return target nonland permanent to its owner's hand" — Sink
    #     into Stupor-class bounce. Picks the highest-threat nonland
    #     permanent opponent controls; lands are never valid targets
    #     (prior handler did not enforce the filter).
    if ('return target' in oracle
            and 'nonland permanent' in oracle
            and "owner's hand" in oracle):
        opp = game.players[opponent]
        from engine.card_effects import _nonland_permanent_threat
        candidates = [c for c in opp.battlefield if not c.template.is_land]
        if candidates:
            best = max(candidates,
                       key=lambda c: _nonland_permanent_threat(c, opp.battlefield))
            opp.battlefield.remove(best)
            best.zone = 'hand'
            best.tapped = False
            opp.hand.append(best)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{card.name} bounces {best.name}")
            handled = True

    # ── "Return target (nonlegendary )?creature card from your graveyard
    #     to the battlefield" — Persist (nonlegendary), Unburial Rites (any).
    #     Goryo's Vengeance is legendary-only with haste + exile-at-EOT and
    #     keeps its dedicated handler.
    #
    #     Uses the unified target solver (Phase 4): the type/supertype
    #     filter and graveyard-zone enumeration both come from
    #     ``engine.target_solver`` rather than re-implementing the
    #     parsing here. See
    #     ``docs/proposals/2026-05-02_unified_target_solver.md``.
    if (re.search(r'return target\s+(\w+\s+)?creature card', oracle)
            and 'graveyard' in oracle
            and 'battlefield' in oracle
            and not re.search(r'return target legendary creature', oracle)):
        from engine.target_solver import (
            enumerate_legal_targets,
            parse as _parse_targets,
        )
        requirements = _parse_targets(card.template.oracle_text or "")
        gy_reqs = [r for r in requirements if r.zone == "graveyard"]
        creatures: list = []
        for req in gy_reqs:
            creatures.extend(
                enumerate_legal_targets(game, controller, req, exclude=card)
            )
        # Restrict to creature-card candidates (the solver may emit
        # broader types if the oracle reads ambiguously).
        creatures = [c for c in creatures if c.template.is_creature]
        if creatures:
            # Pick the biggest body — reanimation's value is in the
            # largest recouped investment.
            best = max(creatures,
                       key=lambda c: (c.template.power or 0)
                       + (c.template.toughness or 0))
            game.reanimate(controller, best)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{card.name} reanimates {best.name}")
            handled = True

    # ── Card draw / impulse-reveal on spell resolution ──
    # Two distinct mechanics share this branch:
    #
    #   * Real card draw (CR 121.1) — "draw a card" / "draw N cards"
    #     / Sleight-of-Hand-style "put one of them into your hand".
    #     These DO fire "whenever you/opponent draws" triggers and
    #     route through `game.draw_cards`.
    #
    #   * Impulse-reveal (CR 121.1c — NOT a draw) — "exile the top N,
    #     you may play those cards". The classifier `Tag.IMPULSE_DRAW`
    #     is the single source of truth; we route these through
    #     `zone_transfer.transfer(..., IMPULSE_REVEAL)` so the on-draw
    #     trigger fan-out is bypassed. This is the R1+M1-engine fix
    #     from the 2026-05-16 audit (storm_vs_dimir G1T4 self-kill).
    #
    # The numerical count is parsed from oracle text in both cases.
    word_to_num = {'a': 1, 'one': 1, 'two': 2, 'three': 3,
                   'four': 4, 'five': 5}

    # Impulse-reveal path — gated by classifier tag, not regex chain.
    from ai.oracle_classifier import Tag, tags_for
    if Tag.IMPULSE_DRAW in tags_for(card.name) and not card.template.x_cost_data:
        from engine.zone_transfer import TransferKind, transfer
        m_exile = re.search(r'exile the top (\w+) cards? of your library',
                            oracle)
        impulse_n = 0
        if m_exile:
            tok = m_exile.group(1)
            try:
                impulse_n = int(tok)
            except ValueError:
                impulse_n = word_to_num.get(tok, 0)
        if impulse_n > 0:
            revealed: list = []
            player = game.players[controller]
            for _ in range(min(impulse_n, len(player.library))):
                top = player.library[0]
                # `transfer` moves library→hand without firing draw
                # triggers (TransferKind.IMPULSE_REVEAL has an empty
                # fan-out). dst="hand" is an approximation of the
                # impulse zone — the card is playable; the trigger
                # fan-out is what mattered for the audit.
                transfer(game, top, src_zone="library", dst_zone="hand",
                         kind=TransferKind.IMPULSE_REVEAL,
                         controller=controller)
                revealed.append(top)
            if revealed:
                names = ", ".join(c.name for c in revealed)
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{card.name} → impulse-reveal {impulse_n} ({names})")
            return True  # impulse-reveal handled; skip real-draw branch

    # Real-draw path — "draw N cards" and look-and-keep variants.
    draw_n = 0
    m_draw = re.search(r'draw\s+(\w+)\s+cards?', oracle)
    if m_draw:
        tok = m_draw.group(1)
        try:
            draw_n = int(tok)
        except ValueError:
            draw_n = word_to_num.get(tok, 0)
    elif 'put one of them into your hand' in oracle:
        # Look-at-top-N keep-1 → draw 1 (Sleight of Hand pattern)
        draw_n = 1
    if draw_n > 0:
        drawn = game.draw_cards(controller, draw_n)
        names = ", ".join(c.name for c in drawn) if drawn else ""
        if drawn:
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{card.name} → draw {draw_n} ({names})")
        handled = True

    return handled


def resolve_attack_trigger(game: "GameState", attacker: "CardInstance",
                            controller: int):
    """Resolve attack triggers by parsing the attacker's oracle text.

    Called when a creature is declared as an attacker.
    """
    oracle = (attacker.template.oracle_text or '').lower()
    if not oracle:
        return

    opponent = 1 - controller

    # Battle cry is handled by CombatManager._apply_battle_cry after all
    # attackers are declared — skipped here to avoid double-application.
    # (oracle_resolver fires per-attacker mid-loop; combat_manager fires once
    # over the complete attacker list, which is the correct timing.)

    # ── "Whenever this creature attacks, deal N damage" ──
    if 'attacks' in oracle and 'damage' in oracle:
        m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
        if m:
            amount = int(m.group(1))
            target = _pick_damage_target(game, controller, amount) \
                if 'any target' in oracle else None
            if target is not None:
                target.damage_marked = getattr(target, 'damage_marked', 0) + amount
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{attacker.name} attack trigger: {amount} damage to {target.name}")
                game.check_state_based_actions()
            else:
                game.players[opponent].life -= amount
                game.players[controller].damage_dealt_this_turn += amount

    # ── "Whenever this creature attacks, gain N life" ──
    if 'attacks' in oracle and 'gain' in oracle and 'life' in oracle:
        m = re.search(r'gain\s+(\d+)\s+life', oracle)
        if m:
            game.gain_life(controller, int(m.group(1)), attacker.name)

    # ── Mobilize: "create N tapped and attacking tokens" ──
    if 'mobilize' in oracle:
        m = re.search(r'mobilize\s+(\d+)', oracle)
        if m:
            count = int(m.group(1))
            game.create_token(controller, "warrior", count=count,
                              power=1, toughness=1)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{attacker.name} mobilize {count} — create {count} 1/1 tokens")

    # ── "Whenever this creature attacks, create a token" ──
    if ('attacks' in oracle and 'create' in oracle and 'token' in oracle
            and 'mobilize' not in oracle):
        m = re.search(r'create\s+(?:a|(\d+))\s+(\d+)/(\d+)', oracle)
        if m:
            count = int(m.group(1) or 1)
            p, t = int(m.group(2)), int(m.group(3))
            game.create_token(controller, "creature", count=count,
                              power=p, toughness=t)


def resolve_dies_trigger(game: "GameState", card: "CardInstance",
                          controller: int):
    """Resolve dies/leaves-the-battlefield triggers from oracle text.

    Called when a creature dies or leaves the battlefield.
    """
    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return

    # ── "When this creature dies, draw a card" ──
    if 'dies' in oracle and 'draw' in oracle:
        game.draw_cards(controller, 1)

    # ── "When this creature dies, create a token" ──
    if 'dies' in oracle and 'create' in oracle and 'token' in oracle:
        m = re.search(r'create\s+(?:a|(\d+))\s+(\d+)/(\d+)', oracle)
        if m:
            count = int(m.group(1) or 1)
            p, t = int(m.group(2)), int(m.group(3))
            game.create_token(controller, "creature", count=count,
                              power=p, toughness=t)

    # ── "When this creature leaves the battlefield, target opponent draws a card"
    #     (Thought-Knot Seer LTB) ──
    if 'leaves the battlefield' in oracle and 'draw' in oracle:
        opponent = 1 - controller
        if 'opponent' in oracle or 'that player' in oracle:
            game.draw_cards(opponent, 1)
        else:
            game.draw_cards(controller, 1)

    # ── "When this creature dies, return target card from graveyard to hand" ──
    if 'dies' in oracle and 'return' in oracle and 'graveyard' in oracle and 'hand' in oracle:
        player = game.players[controller]
        if player.graveyard:
            # Return the best non-land card
            nonlands = [c for c in player.graveyard if not c.template.is_land
                        and c.instance_id != card.instance_id]
            if nonlands:
                best = max(nonlands, key=lambda c: c.template.cmc or 0)
                player.graveyard.remove(best)
                best.zone = "hand"
                player.hand.append(best)

    # ── Subtype-death transform trigger (Ajani, Nacatl Pariah) ──
    # "Whenever one or more other [Subtype]s you control die, you may
    #  exile [this], then return [it] to the battlefield transformed."
    # Generic: any controller-permanent with this trigger pattern fires
    # when a creature of the matching subtype dies under our control.
    # Tokens may carry the subtype in their name (e.g. "Cat Token"),
    # not in a subtypes list — check both.
    dying_subtypes = [s.lower() for s in (card.template.subtypes or [])]
    dying_name = (card.template.name or '').lower()
    player = game.players[controller]
    for perm in list(player.battlefield):
        if perm.instance_id == card.instance_id:
            continue
        if getattr(perm, 'is_transformed', False):
            continue
        p_oracle = (perm.template.oracle_text or '').lower()
        m = re.search(
            r'whenever one or more other (\w+?)s?\s+you control die',
            p_oracle,
        )
        if not m:
            continue
        subtype = m.group(1)
        if subtype not in dying_subtypes and subtype not in dying_name:
            continue
        # Require the transform clause in the same oracle
        if 'transformed' not in p_oracle or 'exile' not in p_oracle:
            continue
        _transform_permanent(game, perm, controller)


def _parse_count_threshold(oracle: str) -> Optional[int]:
    """Parse "(two|three|four|five|N) or more" threshold from oracle.
    Returns None if no numeric threshold is present.
    """
    m = re.search(r'(two|three|four|five|six|seven|\d+)\s+or\s+more', oracle)
    if not m:
        return None
    word_map = {'two': 2, 'three': 3, 'four': 4, 'five': 5,
                'six': 6, 'seven': 7}
    raw = m.group(1)
    if raw.isdigit():
        return int(raw)
    return word_map.get(raw)


def _handle_coin_flip_transform(game: "GameState", controller: int,
                                 creature: "CardInstance") -> None:
    """Coin-flip transform handler (Ral, Monsoon Mage shape).

    Win → transform with loyalty = back_face_loyalty +
    spells_cast_this_turn. Lose → the source permanent takes 1
    damage from itself (Oracle: "<this> deals 1 damage to it" / self-
    damage clause). The 1 is a rule constant straight from the
    Oracle clause, not a magic threshold.

    R6 fix: route the lose branch through the W0-D `deal_damage`
    primitive with target=source (the permanent), not raw-mutate
    `player.life`. The primitive marks `damage_marked` on the
    creature; SBAs (704.5h) destroy it later if marked damage >=
    toughness. New self-damage cards composed from the same shape
    inherit correctness.
    """
    # Late import: avoid module-cycle risk between oracle_resolver
    # (which game_state imports indirectly) and damage. The damage
    # primitive depends on no engine modules; importing here is safe.
    from .damage import deal_damage

    player = game.players[controller]
    result = game.rng.choice(["win", "lose"])
    if result == "lose":
        # Oracle clause: "deals 1 damage to it" — the literal 1 is
        # a rule constant from card text (CLAUDE.md §Hard prohibitions:
        # rule constants are allowed). Damage routes through the W0-D
        # primitive so triggers/SBA semantics fire correctly.
        deal_damage(source=creature, target=creature, amount=1)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"{creature.name} — lost coin flip, takes 1 damage")
        return
    spells_this_turn = player.spells_cast_this_turn
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"{creature.name} — won coin flip!")
    _transform_permanent(game, creature, controller,
                          extra_loyalty=spells_this_turn)


def _transform_permanent(game: "GameState", perm: "CardInstance",
                          controller: int, extra_loyalty: int = 0) -> None:
    """Generic DFC transform: exile the permanent's front face and return
    it as its back face (marked `is_transformed = True`).

    Loyalty is set to `back_face_loyalty + extra_loyalty` when the back
    face is a planeswalker. Damage clears on transform. ETB triggers
    on the transformed side fire via `_handle_permanent_etb`.

    No card names. Callers detect the transform condition; this helper
    just executes the state transition consistently.
    """
    player = game.players[controller]
    if perm in player.battlefield:
        player.battlefield.remove(perm)

    perm.is_transformed = True
    perm.damage_marked = 0

    back_loyalty = getattr(perm.template, 'back_face_loyalty', 0) or 0
    if back_loyalty > 0:
        perm.loyalty_counters = back_loyalty + extra_loyalty

    perm.zone = "battlefield"
    player.battlefield.append(perm)

    loy_str = (f" (loyalty: {perm.loyalty_counters})"
               if back_loyalty > 0 else "")
    extra_str = (f" [+{extra_loyalty} extra]" if extra_loyalty else "")
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"{perm.template.name} transforms!{loy_str}{extra_str}")

    # Fire ETB triggers for the transformed (back) face
    if hasattr(game, '_handle_permanent_etb'):
        game._handle_permanent_etb(perm, controller)


def resolve_spell_cast_trigger(game: "GameState", caster_idx: int,
                                spell_cast: "CardInstance"):
    """Resolve "whenever you cast a spell" triggers for all permanents.

    Called after a spell is successfully cast (on the stack).
    Handles triggers beyond prowess (which is in game_state.py).
    """
    player = game.players[caster_idx]
    opponent = 1 - caster_idx

    for permanent in list(player.battlefield):  # copy: battlefield may change (transform)
        oracle = (permanent.template.oracle_text or '').lower()
        if not oracle or 'whenever' not in oracle:
            continue

        # ── "Whenever you cast a noncreature spell, you get {E}" ──
        # Matches Ocelot Pride and any future card with this exact trigger.
        if ('noncreature spell' in oracle and 'you get' in oracle
                and '{e}' in oracle and not spell_cast.template.is_creature
                and permanent.controller == caster_idx
                and 'create' not in oracle):  # exclude token-creators to avoid double-fire
            import re as _re
            m = _re.search(r'you get\s+((?:\{e\})+)', oracle)
            energy_count = m.group(1).count('{e}') if m else 1
            game.produce_energy(caster_idx, energy_count, permanent.name)

        # ── "Whenever you cast a noncreature spell, create a token" ──
        if ('noncreature spell' in oracle and 'create' in oracle
                and 'token' in oracle and not spell_cast.template.is_creature):
            m = re.search(r'create\s+(?:a|(\d+))\s+(\d+)/(\d+)', oracle)
            if m:
                count = int(m.group(1) or 1)
                p, t = int(m.group(2)), int(m.group(3))
                game.create_token(caster_idx, "creature", count=count,
                                  power=p, toughness=t)

        # ── "Whenever you cast a spell, [scry/surveil/draw]" ──
        if ('cast a spell' in oracle or 'cast an instant or sorcery' in oracle):
            if 'draw a card' in oracle and 'noncreature' not in oracle:
                game.draw_cards(caster_idx, 1)

        # ── "Whenever you cast a noncreature spell, surveil N" ──
        # Class size: Dragon's Rage Channeler, Lightshell Duo, Garland
        # Knight of Cornelia, Cruel Witness, and any future printing
        # with the same trigger shape. Single dispatch via oracle-text
        # patterns + targeted N parse — same shape as the other
        # noncreature-spell-cast branches above (energy, token).
        # This replaces the deleted cast_manager:1198-1205 surveil
        # special case; per R3 in
        # docs/history/audits/2026-05-16_rules_audit.md, the surveil
        # mechanic now has a single dispatch path used by both
        # spell-cast-triggered permanents AND land/permanent ETBs.
        if ('noncreature spell' in oracle and 'surveil' in oracle
                and not spell_cast.template.is_creature
                and permanent.controller == caster_idx):
            m = re.search(r'surveil\s+(\d+)', oracle)
            n = int(m.group(1)) if m else 1
            game.surveil(caster_idx, n)

        # ── Transform-on-cast trigger ──
        # Two patterns, both oracle-driven:
        #   a) "flip a coin, if you win, exile ..., return transformed" (Ral)
        #   b) "if you've cast N or more ... spells, exile ..., return
        #      transformed" (deterministic variant; no card names).
        if ((spell_cast.template.is_instant or spell_cast.template.is_sorcery)
                and permanent.template.is_creature
                and 'transformed' in oracle
                and not getattr(permanent, 'is_transformed', False)
                and ('instant or sorcery' in oracle
                     or 'instant and/or sorcery' in oracle
                     or 'instant and sorcery' in oracle)):
            if 'flip a coin' in oracle:
                _handle_coin_flip_transform(game, caster_idx, permanent)
            else:
                threshold = _parse_count_threshold(oracle)
                if (threshold is not None
                        and player.spells_cast_this_turn >= threshold):
                    _transform_permanent(game, permanent, caster_idx,
                                          extra_loyalty=player.spells_cast_this_turn)

        # ── "Whenever an opponent draws a card" (Orcish Bowmasters) ──
        # Already handled by EFFECT_REGISTRY — skip to avoid double-fire

    # Check OPPONENT's permanents for "whenever an opponent casts" triggers
    opp_player = game.players[opponent]
    for permanent in opp_player.battlefield:
        oracle = (permanent.template.oracle_text or '').lower()
        if not oracle or 'whenever' not in oracle:
            continue

        # ── "Whenever an opponent casts a spell, [effect]" ──
        if 'opponent casts' in oracle:
            if 'damage' in oracle:
                m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
                if m:
                    game.players[caster_idx].life -= int(m.group(1))


def check_static_ability(game: "GameState", card: "CardInstance",
                          controller: int, event_type: str, **kwargs):
    """Check if a permanent's static/triggered ability fires for an event.

    event_type: 'spell_cast', 'land_enter', etc.
    """
    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return False
    return False


def count_cost_reducers(game, player_idx: int, card_template) -> int:
    """Count how many cost reducers on the battlefield apply to a given spell.

    Generic replacement for hardcoded Ruby Medallion / Ral checks.
    Parses each permanent's oracle text for "cost {N} less" patterns
    and checks if the spell being cast matches the reduction criteria.
    """
    from engine.oracle_parser import parse_cost_reduction
    from engine.cards import CardType, Color
    template = card_template
    player = game.players[player_idx]
    reduction = 0

    for perm in player.battlefield:
        oracle = (perm.template.oracle_text or '').lower()
        if 'cost' not in oracle or 'less' not in oracle:
            continue

        rule = parse_cost_reduction(oracle)
        if not rule:
            continue

        matches = False
        if rule['target'] == 'all':
            matches = True
        elif rule['target'] == 'instant_sorcery':
            matches = template.is_instant or template.is_sorcery
        elif rule['target'] == 'creature':
            matches = template.is_creature
        elif rule['target'] == 'noncreature':
            matches = not template.is_creature

        # Check color restriction
        if matches and rule.get('color'):
            color_map = {'R': Color.RED, 'U': Color.BLUE, 'B': Color.BLACK,
                         'W': Color.WHITE, 'G': Color.GREEN}
            required = color_map.get(rule['color'])
            if required and required not in template.color_identity:
                matches = False

        if matches:
            reduction += rule['amount']

    return reduction
