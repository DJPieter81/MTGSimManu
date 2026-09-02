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

from engine.oracle_clauses import (
    any_ability_with,
    split_abilities,
    split_clauses,
)

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.cards import CardInstance, CardTemplate


# Shared word-form → integer table for oracle-text amount parsing
# ("draw a card", "draw two cards", ...). Single owner for both the
# ETB draw-N branch (`resolve_etb_from_oracle`) and the spell-
# resolution draw-N branch (`resolve_spell_from_oracle`) — both read
# the identical CR 121.1 "draw N cards" phrasing, just from different
# trigger contexts (ETB vs. cast resolution).
_WORD_TO_NUM = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
                'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}


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
        # Attack-trigger amplifiers — typed field set at DB load time by
        # oracle_parser.parse_has_attack_trigger; mirrors BATTLE_CRY_AMPLIFIER_VP
        # used in creature_threat_value (ai/ev_evaluator.py) so engine-level
        # targeting ranks the same "high-threat" creatures as the AI scorer.
        if getattr(c.template, 'has_attack_trigger', False):
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


def resolve_damage_to_chosen_target(
        game: "GameState", source: "CardInstance", controller: int,
        amount: int, targets: Optional[list] = None
) -> Optional["CardInstance"]:
    """Resolve "[source] deals `amount` damage to any target" (CR 601.2c
    target declaration already resolved to `targets`; this is the
    application step, CR 119).

    This is the ONE shared owner for the "fixed/derived-amount damage
    to a chosen target" mechanic shape — every EFFECT_REGISTRY handler
    for a "deal N damage to any target / creature / player" spell
    (Lightning Bolt, Lava Dart, Grapeshot, Unholy Heat's post-delirium
    amount, ...) calls this instead of re-implementing target
    filtering + mutation inline. Class size: ~150+ printed Modern
    "deals N damage" spells share this exact target-resolution shape
    (see `tests/test_burn_targets_planeswalker_when_loyalty_le_damage.py`
    for the fuller enumeration) — the amount itself is card-specific
    (fixed, or computed from a card-specific condition like delirium)
    and stays the caller's responsibility; only the "given an amount
    and a declared target list, apply the damage" step is generic.

    Target resolution mirrors CR 601.2c "any target" (creature,
    player, or planeswalker — battle targets are a future target
    type, not yet modelled):

    - `targets` is walked in order; the first id that resolves to a
      battlefield creature or planeswalker receives the damage and is
      returned.
    - A `None`/`-1` entry (the engine's "AI chose face" sentinel) or
      an exhausted/empty `targets` list falls through to the
      opponent's face — this matches the existing spell-resolution
      contract (an "any target" spell with no legal/declared
      permanent target simply goes face; CR 608.2b's "spell with no
      legal target" case does not apply here since the player is
      always a legal target for "any target").

    Every branch routes through `engine.damage.deal_damage` so the
    deathtouch SBA marker (CR 702.2c) and the state-based-actions
    scheduling hook are correct by construction for every caller — no
    reader of this mechanic reimplements `damage_marked +=` / `life
    -=` by hand (the exact "no single owner" pattern this
    remediation program targets; see
    `docs/design/rules-foundation-sweep-tracker.md`). `deal_damage`'s
    own docstring reserves (but does not yet implement — that is
    separate, in-flight work) a lifelink (CR 702.15) hook; every
    caller of this function inherits that behaviour automatically the
    moment it lands, with zero changes here — which is the entire
    point of routing through the shared primitive instead of each
    handler owning its own mutation.

    Returns the `CardInstance` actually damaged, or `None` when the
    damage went to the opponent's face. Callers that want a log line
    naming the target/face outcome use this return value; this
    function does not log (engine primitives compose freely without
    forcing a specific log shape on every caller — see the two
    different log-message conventions already in `card_effects.py`).
    """
    from .damage import deal_damage
    from .cards import CardType
    opponent = 1 - controller
    if amount <= 0:
        # CR 119.4: 0 (or negative) damage is not dealt. Mirrors the
        # no-op guard `deal_damage` itself applies; checked here too
        # so callers never construct a spurious "hit face for 0" log.
        return None
    for tid in (targets or []):
        if tid is None or tid == -1:
            break  # explicit "go face" sentinel
        target = game.get_card_by_id(tid)
        if (target is not None and target.zone == "battlefield"
                and (target.template.is_creature
                     or CardType.PLANESWALKER in target.template.card_types)):
            deal_damage(source, target, amount)
            # Name the burn's target so a legal kill (e.g. "6 damage
            # kills a 7/7") isn't invisible to a log-only reader.
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{getattr(source, 'name', 'source')} deals {amount} to "
                f"{target.name}")
            return target
    deal_damage(source, game.players[opponent], amount)
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"{getattr(source, 'name', 'source')} deals {amount} to "
        f"P{opponent+1} (face)")
    return None


# ---------------------------------------------------------------------------
# Team pump until end of turn (Overrun shape) — typed-field-gated resolution.
# ONE application for every carrier — a permanent's own ETB trigger
# (Craterhoof Behemoth class), an instant/sorcery (Overrun class) and the
# X-bound creature tutor's "+X/+X and haste" rider — driven by
# CardTemplate.team_pump_data (oracle_parser.parse_team_pump). No oracle
# text is inspected here.
# ---------------------------------------------------------------------------


def _apply_team_pump(game: "GameState", controller: int,
                     source: "CardInstance", power: int, toughness: int,
                     keywords, *, others_only: bool = False) -> None:
    """"[Other] creatures you control get +P/+T [and gain <keywords>] until
    end of turn": the until-EOT P/T and keyword channels (cleared at
    cleanup, CR 514.2) — the same ones the activated pump/haste kinds
    write to."""
    player = game.players[controller]
    granted = set(keywords)
    for creature in player.creatures:
        if others_only and creature is source:
            continue
        creature.temp_power_mod += power
        creature.temp_toughness_mod += toughness
        creature.temp_keywords |= granted
    kw_note = (" and " + ", ".join(sorted(k.value for k in granted))
               if granted else "")
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"{source.name} — {'other ' if others_only else ''}"
                    f"creatures get +{power}/+{toughness}{kw_note}")


def _resolve_team_pump(game: "GameState", card: "CardInstance",
                       controller: int) -> bool:
    """Resolve the parsed team-pump shape carried by ``card``.

    "+X/+X, where X is the number of creatures you control" reads the
    count at resolution (CR 608.2h) — the entering permanent is on the
    battlefield by then, so it counts itself; "the greatest power among
    creatures you control" likewise reads current power.
    """
    from .cards import Keyword

    data = card.template.team_pump_data or {}
    player = game.players[controller]
    scaling = data.get('scaling') or ''
    if scaling == 'creature_count':
        power = toughness = len(player.creatures)
    elif scaling == 'greatest_power':
        power = toughness = max((c.power for c in player.creatures),
                                default=0)
    else:
        power, toughness = int(data['power']), int(data['toughness'])
    _apply_team_pump(game, controller, card, power, toughness,
                     {Keyword(k) for k in data.get('keywords', ())},
                     others_only=bool(data.get('others_only')))
    return True


def resolve_etb_from_oracle(game: "GameState", card: "CardInstance",
                             controller: int) -> bool:
    """Resolve ETB effects via classifier-tag dispatch.

    Returns True when a tag-gated branch fired; False otherwise. The return
    value lets the caller distinguish "no oracle-driven ETB effect" from
    "ETB effect handled here" (used by the silent-miss diagnostic).

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

    Inline oracle substring chains are forbidden by the abstraction
    contract — they are the patchwork pattern Wave 2 will delete
    elsewhere; we don't ADD them here.
    """
    # ── "When this ~ enters, [other] creatures you control get +N/+N
    #     [and gain <kw>] until end of turn" (Overrun shape on a body) ──
    # Typed-field gate, no oracle inspection at resolve time
    # (classification: parse_team_pump). Class: ~30 Modern permanents
    # (Craterhoof Behemoth, End-Raze Forerunners, Inspiring Captain, ...).
    if (card.template.team_pump_data or {}).get('trigger') == 'etb':
        return _resolve_team_pump(game, card, controller)

    oracle = (card.template.oracle_text or '').lower()
    if not oracle:
        return False

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
        return True

    # ── "When this ~ enters, (you may) return target card from your
    #     graveyard to your hand" (Eternal Witness class) ──
    # Class size: every regrowth-on-a-body printing — Eternal Witness,
    # Archaeomancer, Greenwarden of Murasa, Timeless Witness, Scholar
    # of the Ages, ... — plus type-restricted variants ("return target
    # instant or sorcery card ..."), whose restriction is parsed from
    # the same clause. Dispatch is gated by the classifier tag
    # `Tag.ETB_RETURN_FROM_GY_TO_HAND`; the only oracle parse is the
    # targeted clause parse for the optional type restriction
    # (assert-fail on tag/oracle desync, same as the surveil branch).
    if has_tag(card.name, Tag.ETB_RETURN_FROM_GY_TO_HAND):
        m = re.search(
            r'return target ([a-z ]*?)cards? from your graveyard to your hand',
            oracle)
        if m is None:
            raise AssertionError(
                f"{card.name!r} carries Tag.ETB_RETURN_FROM_GY_TO_HAND "
                f"but its oracle text does not match the 'return target "
                f"... card from your graveyard to your hand' shape — "
                f"classifier and oracle are out of sync."
            )
        # Optional type restriction, e.g. "instant or sorcery " → the
        # returned card must have at least one of the listed types.
        # Empty for the unrestricted "target card" shape.
        restriction = {w for w in re.split(r'\s+or\s+|\s+', m.group(1)) if w}
        player = game.players[controller]
        candidates = [
            c for c in player.graveyard
            if not restriction
            or restriction & {t.value for t in c.template.card_types}
        ]
        if not candidates:
            # "You may" / no legal target — the trigger fizzles to a
            # no-op, but the branch owns this card's ETB: report handled
            # so the silent-miss diagnostic doesn't flag it.
            return True
        # Deterministic engine commitment (no AI hook yet — same
        # convention as `game.surveil`'s bin-to-GY policy and the
        # energy-spell min-to-kill spend above): prefer nonland cards
        # (lands are recoverable via fetches/land drops, spells are
        # not), then highest mana value as the largest recurred
        # resource; ties resolve to graveyard order for determinism.
        best = max(
            candidates,
            key=lambda c: (not c.template.is_land, c.template.cmc or 0),
        )
        player.graveyard.remove(best)
        best.zone = "hand"
        player.hand.append(best)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {card.name} returns "
            f"{best.name} from graveyard to hand")
        return True

    # ── "When ~ enters, draw N card(s)" (fixed-amount, unconditional
    #     card draw — CR 121.1) ──
    # Class size: 103 Modern-legal permanents carry an ability
    # paragraph matching this exact template with nothing else in it
    # (grepped `ModernAtomic.json` split into ability paragraphs via
    # `split_abilities`; the raw "enters"+"draw" co-occurrence is
    # 283 cards, but most of those have a rider — see below). Sample:
    # Elvish Visionary, Wall of Omens, Mulldrifter, Silvergill Adept,
    # Skyscanner — plus 3 already registered in this pool's own
    # EFFECT_REGISTRY (Omnath Locus of Creation, Quantum Riddler,
    # Thought Monitor), whose handlers were deleted once this branch
    # was proven to cover them (see
    # `docs/design/rules-foundation-sweep-tracker.md`, Phase 3).
    #
    # `re.fullmatch` on the WHOLE ability paragraph (not a co-occurrence
    # search) is deliberate and stricter than the analogous branch in
    # `resolve_spell_from_oracle` below: an ETB ability with ANY extra
    # clause — a rider cost ("discard a card"), a life-gain rider
    # ("gain 2 life and draw a card" — Cloudblazer, Dovin's Acuity), a
    # conditional amount ("if it was kicked, draw two cards" —
    # Citanul Woodreaders), an oracle-derived count ("draw a card for
    # each ..." — Earthshaker Dreadmaw), or a damage rider ("deals 1
    # damage to you and you draw a card" — Blade Juggler) — must NOT
    # silently drop that rider by matching here; those stay on their
    # own EFFECT_REGISTRY handler (or a future dedicated resolver for
    # that rider's own mechanic shape). `startswith('when ')` (not
    # `'whenever '`) additionally excludes repeatable "whenever
    # [something else] enters, draw a card" watcher triggers (Risen
    # Reef-class), which are not this permanent's own one-shot ETB.
    for ability in split_abilities(oracle):
        ability = ability.strip()
        if not ability.startswith('when '):
            continue
        m = re.fullmatch(
            r"when [^,]+ enters,\s*draw\s+(\w+)\s+cards?\.?", ability)
        if m is None:
            continue
        tok = m.group(1)
        try:
            draw_n = int(tok)
        except ValueError:
            draw_n = _WORD_TO_NUM.get(tok, 0)
        if draw_n <= 0:
            # Unparsed token (e.g. "draw x cards" — an X-cost amount
            # this branch does not resolve, matching the identical
            # limitation `resolve_spell_from_oracle`'s draw-N branch
            # already has for X-cost spells). No-op rather than a
            # wrong guess.
            return False
        drawn = game.draw_cards(controller, draw_n)
        names = ", ".join(c.name for c in drawn) if drawn else ""
        if drawn:
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{card.name} ETB: draw {draw_n} ({names})")
        return True

    # ── "When ~ enters, target opponent reveals their hand. You choose a
    #     [restricted] card ... and exile that card [until this creature
    #     leaves the battlefield]." — ETB hand-disruption on a body ──
    # Class: Thought-Knot Seer, Kitesail Freebooter, Tidehollow Sculler,
    # Brain Maggot, Mesmeric Fiend, ... (~10-15 Modern creatures). This is
    # the same reveal / choose-under-restriction / remove mechanic the
    # spell path already resolves for Thoughtseize & Inquisition
    # (resolve_spell_from_oracle below) — it was simply unreachable from
    # an ETB trigger, so these creatures' disruption was a silent no-op.
    # The choose-clause restriction ("nonland", "noncreature, nonland",
    # any mana-value cap) is honoured via the same
    # _targeted_discard_candidates helper the spell path uses. The card is
    # EXILED (not discarded); a linked "until ~ leaves" / "return the
    # exiled card" LTB (Freebooter/Sculler) gives it back — see
    # resolve_dies_trigger. Permanent-exile shapes (Thought-Knot Seer,
    # whose LTB instead lets the opponent draw) carry no return clause.
    _reveal_exile_ability = next(
        (a for a in split_abilities(oracle)
         if a.strip().startswith('when') and 'enters' in a
         and 'reveals their hand' in a
         and 'exile that card' in a),
        None)
    if _reveal_exile_ability is not None:
        opponent = 1 - controller
        opp = game.players[opponent]
        if opp.hand:
            # Restrict to the CHOOSE clause only — the ability paragraph
            # also says "this creature enters/leaves", whose bare "creature"
            # would otherwise pollute the type allow-list and contradict a
            # "noncreature" restriction (Kitesail Freebooter). Same scoping
            # the spell path applies for Thoughtseize/Inquisition.
            choose_clause = next(
                (c for c in split_clauses(_reveal_exile_ability)
                 if 'choose' in c and 'card' in c), _reveal_exile_ability)
            legal = _targeted_discard_candidates(opp.hand, choose_clause)
            if legal:
                best = max(legal, key=lambda c: (c.template.cmc or 0))
                game.zone_mgr.move_card_to_exile(
                    game, best, cause="etb reveal-hand exile")
                # Linked exile (CR 603.6e): record on the source so a
                # "return the exiled card"/"until this creature leaves"
                # LTB can give it back.
                if not hasattr(card, '_etb_exiled_cards'):
                    card._etb_exiled_cards = []
                card._etb_exiled_cards.append(best)
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: {card.name} "
                    f"ETB: exiles {best.name} from opponent's hand")
        return True

    # ── "When ~ enters, reveal the top N cards of your library. For each
    #     card type, you may put a card of that type ... into your hand.
    #     Put the rest on the bottom of your library ..." (Atraxa, Grand
    #     Unifier / selective reveal-to-hand by card type) ──
    # Class: selective reveal-to-hand grouped by card type. The whole
    # payoff of the reanimator archetype (Atraxa is the shared reanimation
    # target of Goryo's Vengeance and Instant Reanimator). Deterministic
    # engine policy (no AI hook yet, same convention as the GY-return
    # branch above): for each distinct card type present among the
    # revealed cards — in a stable type order — take the highest-value
    # card of that type (nonland first, then highest mana value).
    for ability in split_abilities(oracle):
        ability = ability.strip()
        if not ability.startswith('when '):
            continue
        if 'for each card type' not in ability or 'into your hand' not in ability:
            continue
        m = re.search(r'reveal the top (\w+) cards? of your library', ability)
        if m is None:
            continue
        tok = m.group(1)
        try:
            reveal_n = int(tok)
        except ValueError:
            reveal_n = _WORD_TO_NUM.get(tok, 0)
        if reveal_n <= 0:
            return False
        player = game.players[controller]
        revealed = list(player.library[:reveal_n])
        # Distinct card types present, in a deterministic order.
        present_types = []
        seen = set()
        for c in revealed:
            for ct in c.template.card_types:
                if ct not in seen:
                    seen.add(ct)
                    present_types.append(ct)
        present_types.sort(key=lambda ct: ct.value)
        remaining = list(revealed)
        taken = []
        for ctype in present_types:
            pool = [c for c in remaining if ctype in c.template.card_types]
            if not pool:
                continue
            best = max(
                pool,
                key=lambda c: (not c.template.is_land, c.template.cmc or 0),
            )
            remaining.remove(best)
            taken.append(best)
        # Move taken cards to hand through the zone funnel (single-owner
        # zone-transfer path; the abstraction contract forbids raw .zone=).
        for c in taken:
            game.zone_mgr.move_card_to_hand(game, c, cause=f"{card.name} reveal")
        # Put the rest on the bottom of the library in a random order.
        # move_card appends to the destination list, so a library->library
        # move relocates a revealed (top) card to the bottom.
        game.rng.shuffle(remaining)
        for c in remaining:
            game.zone_mgr.move_card(game, c, "library", "library")
        if taken:
            game.log.append(
                f"T{game.display_turn} P{controller+1}: {card.name} ETB: "
                f"put {len(taken)} to hand "
                f"({', '.join(c.name for c in taken)})")
        return True

    # ── "When ~ enters, mill N cards" (self-mill, CR 701.13) ──
    # Class: ~84 Modern permanents (Armored Skaab, Aftermath Analyst, ...).
    # Also handles the mill-and-select rider "you may put a [type] card from
    # among the cards milled ... into your hand" (Fallaji Archaeologist).
    # Scoped to the permanent's own ETB paragraph and to SELF-mill only —
    # "target player mills" (Hedron Crab landfall) is a different, targeted
    # trigger and is excluded.
    for ability in split_abilities(oracle):
        ability = ability.strip()
        if not ability.startswith('when ') or 'enters' not in ability:
            continue
        if 'target player' in ability or 'target opponent' in ability:
            continue
        m = re.search(r'\bmill\s+(\w+)\s+cards?', ability)
        if m is None:
            continue
        tok = m.group(1)
        try:
            mill_n = int(tok)
        except ValueError:
            mill_n = _WORD_TO_NUM.get(tok, 0)
        if mill_n <= 0:
            return False
        player = game.players[controller]
        milled = []
        for _ in range(min(mill_n, len(player.library))):
            top = player.library[0]
            game.zone_mgr.move_card(game, top, "library", "graveyard",
                                    cause=f"{card.name} mill")
            milled.append(top)
        # Optional select rider: recover one card of the allowed type.
        sel = re.search(
            r'put a ([a-z, ]*?)card from among the cards milled', ability)
        if sel and milled:
            restr = sel.group(1)
            excluded = set()
            if 'noncreature' in restr:
                excluded.add('creature')
            if 'nonland' in restr:
                excluded.add('land')
            cands = [
                c for c in milled
                if c.zone == 'graveyard'
                and not (excluded & {ct.value for ct in c.template.card_types})
            ]
            if cands:
                best = max(cands, key=lambda c: c.template.cmc or 0)
                game.zone_mgr.move_card(game, best, "graveyard", "hand",
                                        cause=f"{card.name} recover")
        if milled:
            game.log.append(
                f"T{game.display_turn} P{controller+1}: {card.name} ETB: "
                f"mill {len(milled)}")
        return True

    return False


# ---------------------------------------------------------------------------
# X-bound creature tutor (spell tranche) — typed-field-gated resolution.
# ONE resolver for the whole shape, driven by
# CardTemplate.x_creature_tutor_data (oracle_parser.parse_x_creature_tutor),
# sharing its search/choose/deliver machinery with the activated TUTOR_*
# path so valuation, X-selection and resolution all key off one parse.
# ---------------------------------------------------------------------------


def _resolve_x_creature_tutor(game: "GameState", card: "CardInstance",
                              controller: int, x_value: int) -> bool:
    """Resolve an X-bound creature search for whatever card carries the
    shape (CR 701.19 search, CR 603 zone-change triggers).

    Sequence, matching the printed template: search the parsed zone set →
    choose (callback seam, engine default = best legal by mana value) →
    deliver through the zone funnel (battlefield entry gets the ETB
    fan-out) → entry riders → shuffle + search bookkeeping + opponents'
    search triggers → the caster's own shuffle-back rider.
    """
    from .activated_effects import (choose_tutor_delivery,
                                    eligible_tutor_targets,
                                    finish_library_search,
                                    put_tutor_target, tutor_search_pool)
    from .cards import Keyword

    spec = card.template.x_creature_tutor_data or {}
    player = game.players[controller]
    x_value = max(0, int(x_value or 0))

    eligible = eligible_tutor_targets(
        tutor_search_pool(player, spec), spec, x_value)
    found = choose_tutor_delivery(game, controller, card, eligible)
    if found is not None:
        put_tutor_target(
            game, found, controller, spec, cause=f"{card.name} tutor",
            # "…with X additional +1/+1 counters on it" — part of the
            # entry itself (CR 614.1c), so the funnel places them.
            entry_counters=x_value if spec.get('enters_with_x_counters') else 0)
        haste_at = spec.get('haste_at_x')
        if haste_at is not None and x_value >= haste_at:
            found.temp_keywords.add(Keyword.HASTE)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"{card.name} finds {found.name}")
    else:
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"{card.name} — search finds nothing")

    team_at = spec.get('team_pump_haste_at_x')
    if team_at is not None and x_value >= team_at:
        # "creatures you control get +X/+X and gain haste until end of
        # turn" — the Overrun-shape application shared with the ETB and
        # spell carriers of the team-pump mechanic.
        _apply_team_pump(game, controller, card, x_value, x_value,
                         {Keyword.HASTE})

    finish_library_search(game, controller)

    if spec.get('self_shuffle_into_library') and card in player.graveyard:
        # "Shuffle ~ into its owner's library" replaces the card's own
        # trip to the graveyard.  The spell is still ON THE STACK while
        # its effects execute (ResolutionManager moves it off afterwards),
        # so this fires only when the card has already reached the
        # graveyard — the pre-existing semantics of this rider, preserved
        # verbatim.  Making it unconditional needs a zone-replacement hook
        # in the stack-exit path, which is a different subsystem.
        game.zone_mgr.move_card(game, card, "graveyard", "library",
                                cause=f"{card.name} shuffles itself back")
        game.rng.shuffle(player.library)
    return True


# ---------------------------------------------------------------------------
# Land destruction (spell tranche) — typed-field-gated resolution
# (no runtime oracle inspection; classification lives in
# oracle_parser.parse_land_destruction / CardTemplate.land_destruction_data)
# ---------------------------------------------------------------------------


def _ld_is_basic(perm) -> bool:
    from .cards import Supertype
    return Supertype.BASIC in (getattr(perm.template, 'supertypes', None)
                               or [])


def _ld_owner_searches_basic(game: "GameState", owner_idx: int,
                             tapped: bool, cause_name: str) -> None:
    """Replacement-basic rider: the destroyed land's controller searches
    their library for a basic land and puts it onto the battlefield.

    Routes through the same funnel as the fetchland-crack path
    (engine/land_manager.py) so search-watch triggers (CR 603) and
    landfall fire: callbacks-chosen target, zone funnel entry, land ETB
    statics, shuffle, library-search trigger, landfall trigger.

    The rider is a "may"; taking the basic is engine-rational (declining
    free mana development is dominated) — same deterministic-engine
    simplification as the Gifts graveyard-pile choice in card_effects.
    """
    from .land_manager import LandManager
    player = game.players[owner_idx]
    basics = [c for c in player.library
              if c.template.is_land and _ld_is_basic(c)]
    if not basics:
        return  # decline the optional search — nothing to find
    choice = game.callbacks.choose_fetch_target(
        game, owner_idx, None, basics, ['W', 'U', 'B', 'R', 'G'])
    if choice is None:
        choice = basics[0]
    game.zone_mgr.move_card(game, choice, "library", "battlefield",
                            cause=f"{cause_name} basic-land replacement",
                            controller_override=owner_idx)
    choice.tapped = bool(tapped)
    LandManager.apply_land_etb_static(game, choice, owner_idx)
    LandManager.apply_lands_enter_untapped(game, choice, owner_idx)
    game.rng.shuffle(player.library)
    player.library_searches_this_game += 1
    LandManager.trigger_library_search(game, owner_idx)
    LandManager.trigger_landfall(game, owner_idx)
    game.log.append(
        f"T{game.display_turn} P{owner_idx+1}: searches a basic "
        f"({choice.name}, {'tapped' if choice.tapped else 'untapped'}) "
        f"to replace the destroyed land")


def _resolve_destroy_target_land(game: "GameState", card: "CardInstance",
                                 controller: int, targets: list) -> bool:
    """Resolve a classified destroy-target-land spell.

    Destruction routes through game._permanent_destroyed (zone funnel:
    battlefield -> graveyard via zone_mgr); riders execute afterwards in
    printed order — replacement-basic search (library-search funnel),
    damage to the land's controller (damage funnel), caster draw (draw
    funnel).  CR 702.12b (indestructible) and CR 608.2b/608.2c
    (resolution-time legality / unmet conditions) are enforced here;
    hexproof legality comes from target_solver enumeration.
    """
    from .cards import Keyword
    from .target_solver import TargetRequirement, enumerate_legal_targets

    data = card.template.land_destruction_data or {}
    types = (frozenset({"artifact", "land"})
             if data.get('can_target_artifact') else frozenset({"land"}))
    req = TargetRequirement(zone="battlefield", types=types,
                            owner_scope="any")
    legal = {c.instance_id: c
             for c in enumerate_legal_targets(game, controller, req)}

    def _eligible(perm) -> bool:
        if (data.get('nonbasic_only') and perm.template.is_land
                and _ld_is_basic(perm)):
            return False  # CR 608.2c: nonbasic-only condition unmet
        if (data.get('opponent_controls_only')
                and perm.controller == controller):
            return False
        if Keyword.INDESTRUCTIBLE in perm.keywords:
            return False  # CR 702.12b: destroy does nothing
        return True

    chosen = None
    if targets:
        for tid in targets:
            cand = legal.get(tid)
            if cand is None:
                continue  # CR 608.2b: illegal at resolution — try next id
            if not _eligible(cand):
                return True  # condition unmet — no effect, no retarget
            chosen = cand
            break
        if chosen is None:
            return True  # every chosen id illegal — no effect
    else:
        # No cast-time target (dies-trigger fan-out / synthetic call
        # sites): deterministic engine fallback — deny the OPPONENT's
        # mana, categorical preference for nonbasic then color breadth.
        # Strategic target choice belongs to the AI at cast time
        # (ai/land_denial.py); this mirrors the Gifts-style engine
        # simplification, not a scoring decision.
        cands = [c for c in legal.values()
                 if c.controller != controller and c.template.is_land
                 and _eligible(c)]
        if not cands:
            return True
        chosen = max(cands, key=lambda c: (
            not _ld_is_basic(c),
            len(c.template.produces_mana or []),
            -c.instance_id))

    owner_idx = chosen.controller
    was_land = chosen.template.is_land
    was_nonbasic = was_land and not _ld_is_basic(chosen)
    game._permanent_destroyed(chosen)
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"{card.name} destroys {chosen.name}")

    # Riders, in printed order (search -> damage -> draw). Land-keyed
    # riders apply only when the destroyed permanent was a land (the
    # artifact mode of the compound form has no riders in the class).
    if was_land and data.get('rider_search_basic'):
        _ld_owner_searches_basic(
            game, owner_idx, data.get('rider_search_basic_tapped', False),
            card.name)
    dmg = data.get('rider_damage', 0)
    if (was_land and dmg > 0
            and (not data.get('rider_damage_nonbasic_only')
                 or was_nonbasic)):
        from .damage import deal_damage
        deal_damage(card, game.players[owner_idx], dmg)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"{card.name} deals {dmg} damage to "
                        f"P{owner_idx+1} (destroyed-land rider)")
    draws = data.get('rider_caster_draws', 0)
    if draws > 0:
        game.draw_cards(controller, draws)
    return True


def _targeted_discard_candidates(hand, choose_restriction: str) -> list:
    """Filter a revealed hand to the cards a "you choose a <restricted>
    card from it" clause may LEGALLY take, per the restriction the
    clause itself states.

    The "target player reveals their hand, you choose a card, that
    player discards it" class is large in Modern (Inquisition of
    Kozilek, Despise, Divest, Duress, Distress, Harsh Scrutiny, ...)
    and the restriction differs per card — a mana-value cap ("with
    mana value N or less") and/or a card-type filter ("creature or
    planeswalker", "artifact or creature", "noncreature, nonland").
    The generic resolver previously took the highest-mana-value
    nonland unconditionally, honoring neither — so Inquisition (cap 3)
    could discard an above-cap bomb and Duress could take a creature.

    ``choose_restriction`` is the choose clause (already lowercased by
    the caller); this parses the cap and type predicate from it. No
    card names — the restriction is read from the text.
    """
    from engine.cards import CardType
    r = choose_restriction or ''
    m = re.search(r'mana value (\d+) or less', r)
    max_mv = int(m.group(1)) if m else None
    type_words = {
        'creature': CardType.CREATURE,
        'planeswalker': CardType.PLANESWALKER,
        'artifact': CardType.ARTIFACT,
        'enchantment': CardType.ENCHANTMENT,
        'instant': CardType.INSTANT,
        'sorcery': CardType.SORCERY,
        'land': CardType.LAND,
    }
    # A word preceded by "non" is a forbidden type ("noncreature",
    # "nonland"); the same word standing alone is a member of the
    # allow-list ("creature or planeswalker" → must be one of these).
    forbidden = set()
    allowed = set()
    for word, ctype in type_words.items():
        for occ in re.finditer(re.escape(word), r):
            if r[max(0, occ.start() - 3):occ.start()] == 'non':
                forbidden.add(ctype)
            else:
                allowed.add(ctype)
    # Every card in this class is at minimum "nonland" — a plain
    # "nonland card" clause, and even a positive-list clause like
    # "creature or planeswalker card", never takes a land.
    forbidden.add(CardType.LAND)

    def _legal(card) -> bool:
        types = set(card.template.card_types)
        if types & forbidden:
            return False
        if allowed and not (types & allowed):
            return False
        if max_mv is not None and (card.template.cmc or 0) > max_mv:
            return False
        return True

    return [c for c in hand if _legal(c)]


def _mass_scope_players(game, controller: int, clause: str) -> list:
    """Which players' permanents a mass effect touches, from its clause.
    Default is symmetric (both) — "each creature", "all artifacts".
    """
    opp = 1 - controller
    if 'you control' in clause and 'opponent' not in clause:
        return [game.players[controller]]
    if ('opponent' in clause or 'defending player' in clause
            or 'that player' in clause):
        return [game.players[opp]]
    return [game.players[controller], game.players[opp]]


def _resolve_mass_mode_clause(game, card, controller: int, clause: str) -> bool:
    """Resolve a mass-sweep / typed mass-destroy clause off its REAL
    text (so the permanent TYPE and any mana-value cap survive).

    Handles two shapes the whole-card resolver never carried a branch
    for, because non-modal mass wipes are EFFECT_REGISTRY-registered:
      * "deals N damage to each creature [and each planeswalker]"
      * "destroy/exile all <type> [with mana value M or less]"
    Both honor the owner scope stated in the clause (default symmetric).
    Returns True iff it resolved the clause.
    """
    from .cards import CardType, Keyword
    from .damage import deal_damage

    # ── mass damage sweep ──
    m_dmg = re.search(r'deals?\s+(\d+)\s+damage\s+to\s+each\s+creature', clause)
    if m_dmg:
        amount = int(m_dmg.group(1))
        also_pw = 'planeswalker' in clause
        for pl in _mass_scope_players(game, controller, clause):
            for perm in list(pl.battlefield):
                is_creature = CardType.CREATURE in perm.template.card_types
                is_pw = CardType.PLANESWALKER in perm.template.card_types
                if is_creature or (also_pw and is_pw):
                    deal_damage(card, perm, amount)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"{card.name} deals {amount} to each creature"
            f"{' and planeswalker' if also_pw else ''}")
        return True

    # ── typed mass destroy / exile ──
    m_all = re.search(r'\b(destroy|exile)\s+all\s+'
                      r'(artifacts?|creatures?|enchantments?|permanents?)', clause)
    if m_all:
        verb, noun = m_all.group(1), m_all.group(2)
        type_map = {
            'artifact': CardType.ARTIFACT, 'creature': CardType.CREATURE,
            'enchantment': CardType.ENCHANTMENT,
        }
        want = type_map.get(noun.rstrip('s'))  # None ⇒ "permanents" (any nonland)
        cap_m = re.search(r'mana value (\d+) or less', clause)
        max_mv = int(cap_m.group(1)) if cap_m else None

        def _matches(perm) -> bool:
            if perm.template.is_land:
                return False
            if want is not None and want not in perm.template.card_types:
                return False
            if max_mv is not None and (perm.template.cmc or 0) > max_mv:
                return False
            return True

        for pl in _mass_scope_players(game, controller, clause):
            for perm in list(pl.battlefield):
                if not _matches(perm):
                    continue
                if verb == 'exile':
                    game._exile_permanent(perm)
                elif Keyword.INDESTRUCTIBLE not in perm.keywords:
                    game._permanent_destroyed(perm)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"{card.name} {verb}s all {noun}"
            f"{f' with mana value {max_mv} or less' if max_mv is not None else ''}")
        return True

    return False


def resolve_spell_from_oracle(game: "GameState", card: "CardInstance",
                               controller: int, targets: list = None,
                               *, x_value: int = 0,
                               oracle_override: str = None) -> bool:
    """Resolve instant/sorcery effects by parsing oracle text.

    Called when a spell resolves AND no EFFECT_REGISTRY handler took it.
    Returns True when an effect was applied, so callers can skip the
    legacy ability-description fallback.

    ``x_value`` is the X actually paid, carried on the stack item — the
    same value every other X-consuming resolver reads.

    ``oracle_override`` lets a caller resolve ONE clause instead of the
    card's whole oracle text — used by modal spell resolution to run
    exactly the chosen mode's real clause (the synthesized per-mode
    ability description is lossy). The X-tutor short-circuit is bypassed
    when an override is supplied so it applies to the whole card only.
    """
    # ── X-bound creature tutor — typed-field gate, no oracle inspection
    #    at resolve time (classification: parse_x_creature_tutor).
    if oracle_override is None and getattr(card.template, 'x_creature_tutor_data', None):
        return _resolve_x_creature_tutor(game, card, controller, x_value)

    # ── "Creatures you control get +N/+N [and gain <kw>] until end of
    #    turn" (Overrun class, ~100 Modern instants/sorceries) — the same
    #    typed gate and application as the ETB form above.
    if (card.template.team_pump_data or {}).get('trigger') == 'spell':
        return _resolve_team_pump(game, card, controller)

    oracle = (oracle_override
              if oracle_override is not None
              else (card.template.oracle_text or '')).lower()
    if not oracle:
        return False

    opponent = 1 - controller
    handled = False

    # ── Modal mode: mass sweep / typed mass-destroy ─────────────────
    # These shapes ("deals N damage to each creature ...", "destroy all
    # <type> [with mana value M or less]") are the mode clauses of
    # modal wipes that the whole-card resolver never needed a branch
    # for (mass wipes are EFFECT_REGISTRY-registered or hit the legacy
    # loop). Gated on oracle_override so ONLY a routed single mode
    # clause reaches it — whole-card resolution is unchanged.
    if oracle_override is not None and \
            _resolve_mass_mode_clause(game, card, controller, oracle):
        return True

    # ── Generic combat trick: "target creature gets +N/+M until end of
    #    turn [and gains <keyword>]" — typed fields (parse_pump_spell),
    #    no oracle inspection here. Reached only when no EFFECT_REGISTRY
    #    handler took the spell, so bespoke pumps (Mutagenic Growth,
    #    Violent Urge) never double-apply. Applies to the chosen target,
    #    else the controller's best creature.
    _pp = getattr(card.template, 'pump_spell_power', 0)
    _pt = getattr(card.template, 'pump_spell_toughness', 0)
    if oracle_override is None and (_pp or _pt):
        from engine.cards import Keyword as _KW
        me = game.players[controller]
        tgt = None
        for tid in (targets or []):
            if isinstance(tid, int) and tid > 0:
                c = game.get_card_by_id(tid)
                if (c is not None and c.zone == 'battlefield'
                        and c.template.is_creature):
                    tgt = c
                    break
        if tgt is None and me.creatures:
            tgt = max(me.creatures, key=lambda c: c.power or 0)
        if tgt is not None:
            tgt.temp_power_mod += _pp
            tgt.temp_toughness_mod += _pt
            _kw = getattr(card.template, 'pump_spell_keyword', '')
            if _kw:
                _kwe = getattr(_KW, _kw.upper().replace(' ', '_'), None)
                if _kwe is not None:
                    tgt.temp_keywords.add(_kwe)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{card.name} gives {tgt.name} +{_pp}/+{_pt}"
                f"{' and ' + _kw if _kw else ''}")
            return True

    # Clause-scoped predicates (E5): a spell effect's verbs live in one
    # ability paragraph (a spell's resolution text; an added ability
    # like flashback or suspend sits on its own line). Whole-text
    # conjunctions false-positive on multi-ability cards, so each
    # branch below tests its substrings against individual paragraphs.
    abilities = split_abilities(oracle)

    # ── Mass-reanimate (Living End shape) ──
    # "Exile all creature cards from graveyards ... return them to the
    # battlefield." cast_manager._handle_cascade detects this and calls
    # _resolve_living_end; spells reaching the normal resolution path
    # (suspend-cast, hard-cast) must trigger the same effect or they
    # silently no-op. Oracle-pattern keyed — the exile/return effect is
    # one paragraph, so all three phrases must share it.
    if any('all creature cards' in a
           and 'graveyard' in a
           and 'battlefield' in a
           for a in abilities):
        game._resolve_living_end(controller)
        return True

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
    if getattr(card.template, 'has_energy_damage_target', False):
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

    # ── Land destruction (spell tranche) — typed-field gate, no oracle
    #    inspection at resolve time. Classification + rider data come from
    #    oracle_parser.parse_land_destruction at DB load.
    if getattr(card.template, 'destroys_target_land', False):
        return _resolve_destroy_target_land(game, card, controller, targets)

    # ── Fixed-amount face-legal burn ("deals N damage to any target") —
    #    typed-field gate, no oracle inspection at resolve time
    #    (classification: parse_direct_damage_spell). Routes through the
    #    single shared damage owner resolve_damage_to_chosen_target, exactly
    #    as every per-card burn EFFECT_REGISTRY handler did by hand; the
    #    typed field retires those handlers and covers unregistered burn too.
    _dd = getattr(card.template, 'direct_damage_data', None)
    if oracle_override is None and _dd:
        resolve_damage_to_chosen_target(
            game, card, controller, _dd['amount'], targets)
        return True

    # ── Symmetric board sweep ("destroy all creatures") — typed-field gate,
    #    no oracle inspection at resolve time (classification:
    #    parse_board_sweep). Routes through the shared board-sweep resolver,
    #    exactly as the per-card wrath handlers did by hand.
    _bs = getattr(card.template, 'board_sweep_data', None)
    if oracle_override is None and _bs:
        from engine.card_effects import _resolve_board_sweep
        _resolve_board_sweep(
            game, card, controller, targets, item=None,
            action=_bs['action'], types=frozenset(_bs['types']))
        return True

    # ── Targeted removal ("destroy/exile target <permanent> [MV <= N|X]") —
    #    typed-field gate, no oracle inspection at resolve time
    #    (classification: parse_targeted_removal). Routes through the shared
    #    _resolve_nonland_permanent_removal, exactly as the per-card removal
    #    handlers did by hand; owner_scope is the opponent (the sim's removal
    #    convention). A large correctness fix too — before this, the ~90
    #    unregistered removal spells of this shape resolved to nothing.
    _rm = getattr(card.template, 'targeted_removal_data', None)
    if oracle_override is None and _rm:
        from engine.card_effects import _resolve_nonland_permanent_removal
        _mv = _rm.get('mv')
        if _mv is None:
            _mv_fn = None
        elif _mv == 'x':
            _mv_fn = lambda g, c, ctl, it, _x=x_value: _x
        else:
            _mv_fn = lambda g, c, ctl, it, _n=_mv: _n
        _exile = _rm['action'] == 'exile'
        _resolve_nonland_permanent_removal(
            game, card, controller, targets, None,
            zone_dest='exile' if _exile else 'graveyard',
            types=frozenset(_rm['types']),
            mv_max_fn=_mv_fn,
            log_verb='exiles' if _exile else 'destroys')
        return True

    # ── "Target opponent reveals their hand. You choose a nonland card
    #     and that player discards it." (Thoughtseize, Inquisition) ──
    # The template spans several sentences of ONE paragraph (reveal /
    # choose / discard), so the co-occurrence scope is the ability
    # paragraph — local enough to reject a 'discard' verb from a
    # separate ability while keeping the multi-sentence template intact.
    if any_ability_with(oracle, 'reveals', 'hand', 'discard'):
        opp = game.players[opponent]
        if opp.hand:
            # Honor the choose-clause's stated restriction (mana-value
            # cap and/or card-type filter) instead of taking the
            # highest-mana-value nonland unconditionally. The choose
            # clause is the sentence that names what may be chosen.
            choose_clause = next(
                (c for c in split_clauses(oracle)
                 if 'choose' in c and 'card' in c), '')
            legal = _targeted_discard_candidates(opp.hand, choose_clause)
            if legal:
                # Among the legal candidates, take the best (highest
                # mana value) — the existing heuristic, now applied to
                # the correct pool.
                best = max(legal, key=lambda c: (c.template.cmc or 0))
                game.zone_mgr.move_card(game, best, "hand", "graveyard",
                                        cause="discard")
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{card.name} discards {best.name}")
                handled = True
        # Life loss for Thoughtseize — the "You lose N life" rider is
        # its own sentence; parse the amount from that clause.
        loss_clause = next(
            (c for c in split_clauses(oracle)
             if 'you lose' in c and 'life' in c), None)
        if loss_clause is not None:
            m = re.search(r'lose\s+(\d+)\s+life', loss_clause)
            if m:
                game.players[controller].life -= int(m.group(1))
                handled = True

    # ── "Return target nonland permanent to its owner's hand" — Sink
    #     into Stupor-class bounce. Picks the highest-threat nonland
    #     permanent opponent controls; lands are never valid targets
    #     (prior handler did not enforce the filter).
    if any('return target' in a
           and 'nonland permanent' in a
           and "owner's hand" in a
           for a in abilities):
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
    _reanimate_ability = next(
        (a for a in abilities
         if re.search(r'return target\s+(\w+\s+)?creature card', a)
         and 'graveyard' in a and 'battlefield' in a
         and not re.search(r'return target legendary creature', a)),
        None)
    if _reanimate_ability is not None:
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
    word_to_num = _WORD_TO_NUM

    # Impulse-reveal path — gated by classifier tag, not regex chain.
    from ai.oracle_classifier import Tag, tags_for
    if Tag.IMPULSE_DRAW in tags_for(card.name):
        from engine.zone_transfer import TransferKind, transfer
        m_exile = re.search(r'exile the top (\w+) cards? of your library',
                            oracle)
        # Play-cap sub-shape: "exile the top X … you may play up to
        # TWO of those cards" — the cap is the card-advantage value;
        # X itself is an exile count (often an additional-cost count),
        # not castable value. This is the only X-form the impulse
        # branch handles; other X-impulses stay excluded.
        m_cap = re.search(r'you may play up to (\w+)', oracle)
        impulse_n = 0
        if m_exile:
            tok = m_exile.group(1)
            try:
                impulse_n = int(tok)
            except ValueError:
                impulse_n = word_to_num.get(tok, 0)
        if impulse_n == 0 and m_cap:
            tok = m_cap.group(1)
            try:
                impulse_n = int(tok)
            except ValueError:
                impulse_n = word_to_num.get(tok, 0)
        if card.template.x_cost_data and not m_cap:
            impulse_n = 0
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

    # ── Scry N (CR 701.18) and Surveil N (CR 701.42) as spell effects ──
    #
    # Class size for scry-as-spell: dozens of blue cantrips and modal
    # spells — Opt (scry 1), Serum Visions (scry 2), Deliberate (scry
    # 2), Telling Time (scry 1), Omen of the Sea (scry 2), and any
    # future printing with the scry keyword in its resolution text.
    #
    # Class size for surveil-as-spell: Consider (surveil 1), Thought
    # Erasure (surveil 1), Sinister Sabotage (surveil 1), and any
    # future instant/sorcery with a surveil clause in its own oracle
    # text (distinct from the permanent cast-trigger surveil handled in
    # the triggered-ability fan-out above).
    #
    # Ordering: CR 601.2 requires effects to resolve in oracle-text
    # order. Opt is "Scry 1. Draw a card." (scry first); Serum Visions
    # is "Draw a card. Scry 2." (draw first). We dispatch in oracle-text
    # position order so the right card ends up on top before the draw.
    #
    # These branches are gated by typed fields (has_scry, has_surveil)
    # so the regex only runs on cards that actually have the keyword —
    # no false matches on unrelated "scry" mentions (e.g. Scavenging
    # Ooze's oracle has no "scry" token; Sylvan Scrying's oracle
    # also lacks the keyword). Card names are never tested here.

    # Parse scry count and oracle-text position
    scry_n = 0
    scry_pos = len(oracle)  # sentinel: no scry → sort last
    if getattr(card.template, 'has_scry', False):
        m_scry = re.search(r'\bscry\s+(\d+)', oracle)
        if m_scry:
            try:
                scry_n = int(m_scry.group(1))
            except ValueError:
                scry_n = 0
            scry_pos = m_scry.start()

    # Parse surveil count and oracle-text position (spell effect only —
    # not the permanent cast-trigger path, which fires separately).
    surveil_spell_n = 0
    surveil_spell_pos = len(oracle)  # sentinel
    if getattr(card.template, 'has_surveil', False):
        m_surv = re.search(r'\bsurveil\s+(\d+)', oracle)
        if m_surv:
            try:
                surveil_spell_n = int(m_surv.group(1))
            except ValueError:
                surveil_spell_n = 0
            surveil_spell_pos = m_surv.start()

    # Parse draw count and oracle-text position
    #
    # Reminder text (anything in parentheses — CR glossary) is
    # explanatory, never part of the resolving effect. Matching
    # against the whole oracle string reads a keyword inside another
    # ability's reminder text as this spell's own effect — Unearth's
    # normal cast ("Return target creature card with mana value 3 or
    # less...") has no draw clause, but its Cycling ability's reminder
    # text ("({2}, Discard this card: Draw a card.)") does, so the
    # unscoped search drew a phantom extra card on every normal cast.
    # `oracle_parser.strip_reminder_text` removes the text outright,
    # which would shift every other detector's `.start()` offset in
    # this shared `oracle` string out of alignment — masking with
    # spaces instead keeps positions comparable while making reminder
    # text unmatchable.
    draw_n = 0
    draw_pos = len(oracle)  # sentinel
    oracle_no_reminder = re.sub(r'\([^()]*\)', lambda m: ' ' * len(m.group(0)),
                                oracle)
    m_draw = re.search(r'draw\s+(\w+)\s+cards?', oracle_no_reminder)
    if m_draw:
        tok = m_draw.group(1)
        try:
            draw_n = int(tok)
        except ValueError:
            draw_n = word_to_num.get(tok, 0)
        draw_pos = m_draw.start()
    elif getattr(card.template, 'has_look_hand_selection', False):
        # Look-at-top-N keep-1 → draw 1 (Sleight of Hand pattern).
        # Position: treat as occurring at the start of the oracle text
        # so it fires before any other effect (look-and-keep cards
        # typically have no separate scry clause).
        draw_n = 1
        draw_pos = 0

    # Build ordered effect list — fire in oracle-text position order.
    effects: list = []
    if scry_n > 0:
        effects.append((scry_pos, 'scry', scry_n))
    if surveil_spell_n > 0:
        effects.append((surveil_spell_pos, 'surveil', surveil_spell_n))
    if draw_n > 0:
        effects.append((draw_pos, 'draw', draw_n))
    effects.sort(key=lambda x: x[0])

    for _, effect_kind, count in effects:
        if effect_kind == 'scry':
            game.scry(controller, count)
            handled = True
        elif effect_kind == 'surveil':
            game.surveil(controller, count)
            handled = True
        elif effect_kind == 'draw':
            drawn = game.draw_cards(controller, count)
            if drawn:
                names = ", ".join(c.name for c in drawn)
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{card.name} → draw {count} ({names})")
            handled = True

    # ── "Create [N] <P>/<T> … creature token[s] [with '<ability>']" ──
    # CR 111: a spell whose own effect creates a token. Token creation was
    # already wired for ATTACK and DIES triggers but had NO spell-resolution
    # branch, so 522 Modern instants/sorceries carrying this clause resolved
    # to a complete no-op. For ramp shells the omission is load-bearing: the
    # created body often carries a mana ability, so a missing token is missing
    # ramp.
    #
    # Safe to run generically here: `resolve_spell_from_oracle` is only
    # reached after EFFECT_REGISTRY has declined the card, so a card-specific
    # handler can never double-fire with this branch. `create_token` does the
    # P/T + subtype + inner-quoted-ability parsing via `parse_token_spec`, so
    # the only parse here is the COUNT.
    if not handled and (card.template.is_instant or card.template.is_sorcery):
        for clause in split_abilities(oracle):
            m = re.search(
                r'\bcreate\s+(a|an|one|two|three|four|five|\d+)\s+'
                r'(\d+)/(\d+)\b[^.]*?\btokens?\b', clause)
            if m is None:
                continue
            tok = m.group(1)
            try:
                count = int(tok)
            except ValueError:
                count = 1 if tok in ('a', 'an', 'one') else _WORD_TO_NUM.get(tok, 0)
            if count <= 0:
                continue
            created = game.create_token(
                controller, "creature", count=count,
                power=int(m.group(2)), toughness=int(m.group(3)),
                source_oracle=clause)
            # `create_token` already emits its own log line; don't duplicate it.
            if created:
                handled = True
            break

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

    # Clause-scoped (E5): an attack trigger's effect lives in the same
    # ability paragraph as its "attacks" condition (CR 603.1). Each
    # branch below locates its own paragraph and runs the detail
    # regexes against it, so a separate ability's damage verb can't
    # feed the attack branch.
    abilities = split_abilities(oracle)

    # Battle cry is handled by CombatManager._apply_battle_cry after all
    # attackers are declared — skipped here to avoid double-application.
    # (oracle_resolver fires per-attacker mid-loop; combat_manager fires once
    # over the complete attacker list, which is the correct timing.)

    # ── "Whenever this creature attacks, deal N damage" ──
    _dmg_ability = next(
        (a for a in abilities if 'attacks' in a and 'damage' in a), None)
    if _dmg_ability is not None:
        m = re.search(r'deals?\s+(\d+)\s+damage', _dmg_ability)
        if m:
            amount = int(m.group(1))
            target = _pick_damage_target(game, controller, amount) \
                if 'any target' in _dmg_ability else None
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
    _life_ability = next(
        (a for a in abilities
         if 'attacks' in a and 'gain' in a and 'life' in a
         # The drain class ("... loses N life. You gain N life.") owns its
         # own life-gain below — don't double-apply it here.
         and not ('sacrifices' in a and 'loses' in a)), None)
    if _life_ability is not None:
        m = re.search(r'gain\s+(\d+)\s+life', _life_ability)
        if m:
            game.gain_life(controller, int(m.group(1)), attacker.name)

    # ── Mobilize: "create N tapped and attacking tokens" ──
    # CR 702 Mobilize: tokens enter tapped and attacking; sacrifice at
    # the beginning of the next end step.
    if getattr(attacker.template, 'has_mobilize', False):
        m = re.search(r'mobilize\s+(\d+)', oracle)
        if m:
            count = int(m.group(1))
            tokens = game.create_token(controller, "warrior", count=count,
                                       power=1, toughness=1)
            for tok in tokens:
                tok.tapped = True          # enters tapped-and-attacking
                game.register_end_of_turn_sacrifice(tok)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{attacker.name} mobilize {count} — create {count} 1/1 tokens")

    # ── "Whenever this creature enters or attacks, target opponent
    #     sacrifices a creature or planeswalker, then discards a card, then
    #     loses N life. You draw a card and gain N life." (Archon of
    #     Cruelty / attack-drain class) ──
    # The ETB half is handled by a dedicated ETB handler; this is the
    # ATTACK half. Scoped to the paragraph that has both 'attacks' and the
    # 'sacrifices' + 'loses ... life' drain shape.
    _drain_ability = next(
        (a for a in abilities
         if 'attacks' in a and 'target opponent' in a
         and 'sacrifices' in a and 'loses' in a and 'life' in a), None)
    if _drain_ability is not None:
        opp_player = game.players[opponent]
        # 1. Opponent sacrifices a creature or planeswalker (they choose
        #    their least-valuable: lowest power, then lowest mana value).
        if 'creature or planeswalker' in _drain_ability or 'creature' in _drain_ability:
            from engine.cards import CardType as _CT
            sac_pool = [
                c for c in opp_player.battlefield
                if _CT.CREATURE in c.template.card_types
                or _CT.PLANESWALKER in c.template.card_types
            ]
            if sac_pool:
                worst = min(sac_pool,
                            key=lambda c: ((c.power or 0), c.template.cmc or 0))
                game.zone_mgr.move_card(game, worst, "battlefield", "graveyard",
                                        cause=f"{attacker.name} attack: sacrifice")
        # 2. Opponent discards a card.
        if 'discards' in _drain_ability:
            game._force_discard(opponent, 1, self_discard=False)
        # 3. Opponent loses N life; controller draws a card and gains N life.
        m = re.search(r'loses?\s+(\d+)\s+life', _drain_ability)
        n_life = int(m.group(1)) if m else 0
        if n_life > 0:
            opp_player.life -= n_life
        if 'draw a card' in _drain_ability or 'draw' in _drain_ability:
            game.draw_cards(controller, 1)
        m_gain = re.search(r'gain\s+(\d+)\s+life', _drain_ability)
        if m_gain:
            game.gain_life(controller, int(m_gain.group(1)), attacker.name)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: "
            f"{attacker.name} attack drain — opp loses {n_life} life")
        game.check_state_based_actions()

    # ── "Whenever this creature attacks, create a token" ──
    # The mobilize exclusion stays whole-text: mobilize's reminder text
    # ("Create N tapped and attacking 1/1 …") shares a paragraph with
    # the attack wording and is already handled by the branch above.
    _token_ability = next(
        (a for a in abilities
         if 'attacks' in a and 'create' in a and 'token' in a), None)
    if _token_ability is not None and 'mobilize' not in oracle:
        m = re.search(r'create\s+(?:a|(\d+))\s+(\d+)/(\d+)', _token_ability)
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
    from engine.cards import Keyword, CardType  # noqa: PLC0415 (lazy to avoid cycle)

    oracle = (card.template.oracle_text or '').lower()

    # ── Modular (CR 702.43): "When it dies, you may put its +1/+1 counters
    #    on target artifact creature." ──
    # Keyed on Keyword.MODULAR (populated at DB load from KEYWORD_MAP / oracle
    # word-boundary scan) — no card name involved.  The AI always takes the
    # optional transfer when a valid target exists (unconditionally beneficial).
    if Keyword.MODULAR in card.template.keywords and card.plus_counters > 0:
        _artifact_creatures = [
            c for c in game.players[controller].battlefield
            if CardType.CREATURE in c.effective_card_types
            and CardType.ARTIFACT in c.effective_card_types
        ]
        if _artifact_creatures:
            # Heuristic: transfer to the artifact creature with the highest
            # current power (maximises board presence).
            target = max(_artifact_creatures, key=lambda c: c.power)
            target.add_plus_counters(card.plus_counters, game, source=card)
            game.log.append(
                f"T{game.display_turn}: {card.name} modular — "
                f"move {card.plus_counters} counter(s) to {target.name}"
            )
            card.plus_counters = 0

    if not oracle:
        return

    # Clause-scoped (E5): a dies/LTB trigger's effect shares an ability
    # paragraph with its condition (CR 603.1); detail regexes run on
    # that paragraph.
    abilities = split_abilities(oracle)

    # ── Linked-exile return (CR 603.6e): cards this permanent exiled on
    #    ETB via "exile that card until this creature leaves the
    #    battlefield" / a "return the exiled card to its owner's hand" LTB
    #    (Kitesail Freebooter, Tidehollow Sculler, Brain Maggot) come back
    #    to their owner's hand now. Permanent-exile shapes (Thought-Knot
    #    Seer — "opponent draws a card" instead) carry no return clause and
    #    fall through. Keyed on the source's own recorded linkage, not any
    #    card name. ──
    _exiled = getattr(card, '_etb_exiled_cards', None)
    if _exiled and getattr(card.template, 'etb_exile_returns_on_leave', False):
        for exiled_card in list(_exiled):
            owner = game.players[exiled_card.owner]
            if exiled_card in owner.exile:
                game.zone_mgr.move_card_to_hand(
                    game, exiled_card, cause="linked exile returns on leave")
                game.log.append(
                    f"T{game.display_turn}: {card.name} leaves — "
                    f"{exiled_card.name} returns to its owner's hand")
        card._etb_exiled_cards = []

    # ── "When this creature dies, draw a card" ──
    if any('dies' in a and 'draw' in a for a in abilities):
        game.draw_cards(controller, 1)

    # ── "When this creature dies, create a token" ──
    _token_ability = next(
        (a for a in abilities
         if 'dies' in a and 'create' in a and 'token' in a), None)
    if _token_ability is not None:
        m = re.search(r'create\s+(?:a|(\d+))\s+(\d+)/(\d+)', _token_ability)
        if m:
            count = int(m.group(1) or 1)
            p, t = int(m.group(2)), int(m.group(3))
            game.create_token(controller, "creature", count=count,
                              power=p, toughness=t)

    # ── "When this creature leaves the battlefield, target opponent draws a card"
    #     (Thought-Knot Seer LTB) ──
    _ltb_ability = next(
        (a for a in abilities
         if 'leaves the battlefield' in a and 'draw' in a), None)
    if _ltb_ability is not None:
        opponent = 1 - controller
        if 'opponent' in _ltb_ability or 'that player' in _ltb_ability:
            game.draw_cards(opponent, 1)
        else:
            game.draw_cards(controller, 1)

    # ── "When this creature dies, return target card from graveyard to hand" ──
    if any('dies' in a and 'return' in a and 'graveyard' in a
           and 'hand' in a for a in abilities):
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


def _permanent_is_colored(perm: "CardInstance") -> bool:
    """True when a permanent is one or more colors (CR 105.2a).

    Uses the object's COLORS, never its color identity — a devoid
    creature and a colorless land each have a non-empty color identity
    but are colorless, so they are not legal targets for a "target
    permanent that's one or more colors" effect.
    """
    return bool(getattr(perm.template, 'colors', None))


def resolve_self_cast_trigger(game: "GameState", caster_idx: int,
                              spell_cast: "CardInstance") -> bool:
    """Resolve the SPELL'S OWN "When you cast this spell, <effect>" triggers
    (CR 601.2i cast triggers on the object being cast).

    This is distinct from `resolve_spell_cast_trigger`, which fires "whenever
    you cast a spell" watcher triggers on OTHER permanents. A spell's own
    on-cast trigger previously had no dispatch and was a silent no-op — this
    blanked the Eldrazi ramp engine (Sowing Mycospawn's land search) and the
    Eldrazi interaction suite (Devourer of Destiny / Ugin exile a colored
    permanent on cast). Oracle-shape-gated; no card names.

    Returns True when at least one self-cast clause was recognised (so the
    caller can distinguish "handled" from "no such trigger").
    """
    oracle = (spell_cast.template.oracle_text or '').lower()
    if 'when you cast this spell' not in oracle:
        return False

    from engine.cards import CardType as _CT
    handled = False
    player = game.players[caster_idx]
    opponent = 1 - caster_idx

    for clause in split_abilities(oracle):
        clause = clause.strip()
        if not clause.startswith('when you cast this spell'):
            continue
        # Conditional riders we don't model (kicker/other cast conditions):
        # skip the effect but the clause is still "recognised".
        if 'if it was kicked' in clause or 'if this spell was kicked' in clause:
            handled = True
            continue

        # ── "search your library for a land card, put it onto the
        #     battlefield" (Sowing Mycospawn ramp) ──
        if 'search your library for a land' in clause \
                and 'onto the battlefield' in clause:
            lands = [c for c in player.library
                     if _CT.LAND in c.template.card_types]
            if lands:
                # Deterministic: fetch the highest-output land (Eldrazi
                # Temple over a basic), tie-break by name.
                best = max(lands, key=lambda c: (
                    getattr(c.template, 'mana_count', 0) or 0, c.name))
                game.zone_mgr.move_card(game, best, "library", "battlefield",
                                        cause=f"{spell_cast.name} cast: land search")
                game.log.append(
                    f"T{game.display_turn} P{caster_idx+1}: {spell_cast.name} "
                    f"cast — search land ({best.name})")
            handled = True
            continue

        # ── "exile [up to one] target permanent that's one or more colors"
        #     (Devourer of Destiny / Ugin, Eye of the Storms) ──
        if 'exile' in clause and 'permanent' in clause \
                and 'one or more colors' in clause:
            targets = [c for c in game.players[opponent].battlefield
                       if _permanent_is_colored(c)]
            if targets:
                # Exile the opponent's most threatening colored permanent
                # (highest power, then mana value).
                worst = max(targets, key=lambda c: (
                    (c.power or 0) if _CT.CREATURE in c.template.card_types else 0,
                    c.template.cmc or 0))
                game.zone_mgr.move_card(game, worst, "battlefield", "exile",
                                        cause=f"{spell_cast.name} cast: exile")
                game.log.append(
                    f"T{game.display_turn} P{caster_idx+1}: {spell_cast.name} "
                    f"cast — exile {worst.name}")
            handled = True
            continue

    return handled


def _ordinal_cast_trigger_fires(game: "GameState", permanent: "CardInstance",
                                caster_idx: int) -> bool:
    """Does an ORDINAL cast trigger (CR 603.2) meet its condition now?

    "Whenever you cast your second spell each turn" is satisfied by exactly
    one spell per turn: the one that brings the relevant player's per-turn
    spell count to N.  `spells_cast_this_turn` is incremented by
    `CastManager.cast_spell` BEFORE cast triggers run, so the spell now
    being cast is already counted and the test is an equality — an
    inequality would re-fire on every later spell of the turn.

    The counter is per player and reset by `reset_turn_tracking`, so "each
    turn" (CR 500.8) holds without any extra state here.  `caster_scope`
    decides whose count is read: "you" (the permanent's controller must be
    the caster), "an opponent" (must not be), or "a player" (either).

    Permanents with no ordinal trigger are unaffected — the caller only
    consults this when the typed field is present.
    """
    spec = permanent.template.ordinal_cast_trigger
    scope = spec["caster_scope"]
    if scope == "you" and permanent.controller != caster_idx:
        return False
    if scope == "opponent" and permanent.controller == caster_idx:
        return False
    return (game.players[caster_idx].spells_cast_this_turn
            == spec["ordinal"])


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

        # ── Ordinal cast triggers (CR 603.2) gate the WHOLE permanent ──
        # "Whenever you cast your Nth spell each turn" is the trigger
        # condition for every effect the ability grants — token, counter,
        # damage, draw.  Checking it once here means no effect branch below
        # can fire on a non-Nth spell.  Gating the permanent (rather than
        # each branch) is safe because no card in the pool carries an
        # ordinal cast trigger AND a plain "whenever you cast a <type>
        # spell" trigger — measured 0 of 22,506.
        if (permanent.template.ordinal_cast_trigger is not None
                and not _ordinal_cast_trigger_fires(game, permanent,
                                                     caster_idx)):
            continue

        # ── "Whenever you cast a noncreature spell, you get {E}" ──
        # Matches Ocelot Pride and any future card with this exact trigger.
        if (permanent.template.has_noncreature_spell_cast_trigger
                and getattr(permanent.template, 'has_energy_production', False)
                and not spell_cast.template.is_creature
                and permanent.controller == caster_idx
                and 'create' not in oracle):  # exclude token-creators to avoid double-fire
            import re as _re
            m = _re.search(r'you get\s+((?:\{e\})+)', oracle)
            energy_count = m.group(1).count('{e}') if m else 1
            game.produce_energy(caster_idx, energy_count, permanent.name)

        # ── "Whenever you cast a[n] <type> spell, create a token" ──
        # CR 603 cast-triggered token, generalised across spell TYPE.
        # The spell-type condition + count are parsed once at DB load into
        # `template.cast_trigger_token` (a set of qualifying types plus a
        # count); this dispatch fires the token when the cast spell
        # matches. Serves Pinnacle Emissary (artifact-cast → Drone),
        # Monastery Mentor (noncreature-cast → Monk), Young Pyromancer and
        # Talrand (instant/sorcery-cast). The token's P/T/subtype/keywords
        # come from `source_oracle` via `create_token`'s parse_token_spec,
        # so this branch owns only the "does the cast qualify?" decision.
        cast_spec = permanent.template.cast_trigger_token
        if cast_spec and permanent.controller == caster_idx:
            spell_types = cast_spec['spell_types']
            if 'any' in spell_types:
                # Ordinal trigger: the condition names no card type, so any
                # spell type qualifies once the ordinal gate above passed.
                qualifies = True
            elif 'noncreature' in spell_types:
                qualifies = not spell_cast.template.is_creature
            else:
                cast_types = {t.value for t in spell_cast.template.card_types}
                qualifies = bool(spell_types & cast_types)
            if qualifies:
                tokens = game.create_token(
                    caster_idx, "creature", count=cast_spec['count'],
                    source_oracle=(cast_spec.get('clause')
                                   or permanent.template.oracle_text))
                # "… then attach this Equipment to it" — the attachment is
                # part of the trigger's effect, so it costs nothing and needs
                # no equip activation.  Attaching through the engine's one
                # owner keeps the `equipment_unattached` flag (which the AI's
                # equip planner reads) consistent with the equip path.
                if cast_spec.get('attach_source') and tokens:
                    if game.attach_equipment(caster_idx, permanent, tokens[-1]):
                        game.log.append(
                            f"T{game.display_turn} P{caster_idx+1}: "
                            f"{permanent.name} attaches to {tokens[-1].name}")

        # ── "Whenever you cast a spell, draw a card" ──
        if getattr(permanent.template, 'has_cast_spell_draw', False):
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
        if (permanent.template.has_noncreature_spell_cast_trigger
                and getattr(permanent.template, 'has_surveil', False)
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
                and getattr(permanent.template, 'has_transform_effect', False)
                and not getattr(permanent, 'is_transformed', False)
                and getattr(permanent.template, 'has_instant_or_sorcery_reference', False)):
            if getattr(permanent.template, 'has_coin_flip', False):
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

        # Same ordinal gate as the controller loop above (CR 603.2).
        if (permanent.template.ordinal_cast_trigger is not None
                and not _ordinal_cast_trigger_fires(game, permanent,
                                                     caster_idx)):
            continue

        # ── "Whenever an opponent casts a spell, deal damage" ──
        if getattr(permanent.template, 'has_opponent_cast_damage', False):
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


def count_graveyard_card_types(game, player_idx: int) -> int:
    """Number of DISTINCT card types (CR 205.2a) among cards in the
    player's graveyard — the live unit for the "for each card type among
    cards in your graveyard" self-scaling reduction (and any other
    "card types in your graveyard" count, e.g. delirium thresholds)."""
    player = game.players[player_idx]
    return len({t for c in player.graveyard for t in c.template.card_types})


def self_cost_reduction(game, player_idx: int, card_template) -> int:
    """Generic mana the SPELL ITSELF discounts via "This spell costs {N}
    less to cast for each <unit>" (CR 601.2f).

    Reads the typed `self_cost_reduction_amount` / `_unit` fields
    (parsed once at DB load — no oracle inspection here) and multiplies
    by the live unit count.  Capped at the printed generic portion:
    cost reductions never touch coloured pips.  Returns 0 when the card
    has no (modelled) self-scaling reduction.
    """
    from engine.oracle_parser import (SELF_COST_UNIT_DISCARDED_OR_CYCLED,
                                      SELF_COST_UNIT_GRAVEYARD_CARD_TYPES)
    template = card_template
    amount = template.self_cost_reduction_amount
    if amount <= 0:
        return 0
    unit = template.self_cost_reduction_unit
    if unit == SELF_COST_UNIT_DISCARDED_OR_CYCLED:
        count = game.players[player_idx].cards_discarded_or_cycled_this_turn
    elif unit == SELF_COST_UNIT_GRAVEYARD_CARD_TYPES:
        count = count_graveyard_card_types(game, player_idx)
    else:
        return 0  # unmodelled unit — refused outright at parse time too
    return min(amount * count, max(0, template.mana_cost.generic))


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
