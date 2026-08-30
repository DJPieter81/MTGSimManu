"""
Triggers — extracted from engine/game_state.py (Commit 5c).

Owns trigger fan-out:
- trigger_etb: generic enter-the-battlefield trigger dispatch for
  permanents entering the battlefield (oracle-text-driven + tag-based).
- trigger_attack: attack-trigger fan-out (Goldspan-style, etc.).
- process_triggers: drain the queued-triggers list and resolve each.
- queue_trigger: append a trigger to the internal queue.

Static methods; take game: GameState as first arg.
"""
from __future__ import annotations

import re
import random
from typing import TYPE_CHECKING, List

from .cards import CardInstance, CardType, Keyword, Supertype, Ability, AbilityType
from .card_effects import EFFECT_REGISTRY, EffectTiming
from .stack import StackItem, StackItemType
from .mana import ManaCost

if TYPE_CHECKING:
    from .game_state import GameState


class TriggerManager:
    """ETB + attack + queued-trigger dispatcher. Stateless."""

    @staticmethod
    def trigger_etb(game: "GameState", card: CardInstance, controller: int):
        # CR 301.5c / 702.6 — an Equipment can become attached only via an
        # effect that says so (normally its own equip ability), so every
        # Equipment that enters the battlefield enters UNATTACHED. The
        # simulator records that as the `equipment_unattached` instance tag,
        # which is the sole gate on `ai/ev_player.py::_consider_equip`
        # enumerating an equip play and on `ai/permanent_threat.py` valuing a
        # stranded equipment. Without it an Equipment resolves and can never
        # be equipped for the rest of the game — legal in the engine, absent
        # from the AI's play space.
        #
        # Class: every Equipment printing (hundreds), keyed off the typed
        # `equip_cost` field parsed once at DB load plus the Equipment
        # subtype — no card name is consulted. The mirror-image half of this
        # mechanic (re-marking an equipment unattached when its bearer leaves)
        # already lives generically in engine/permanent_effects.py; this is
        # the "enters" half, which used to be a card-name registry entry.
        #
        # Runs before the fan-out below and AFTER any card-specific ETB
        # handler (spell_resolution calls the registry first), so an
        # Equipment that attaches itself on entry keeps its attachment.
        if (getattr(card.template, 'equip_cost', None) is not None
                and "Equipment" in (card.template.subtypes or [])
                and "equipment_attached" not in card.instance_tags):
            card.instance_tags.add("equipment_unattached")

        # Elesh Norn / Panharmonicon family: detect any controller-side permanent
        # whose oracle says "triggers an additional time". Each such permanent
        # causes ETB-induced triggers to fire one extra time. Generic — no
        # hardcoding. Excludes the entering card itself (it can double its own
        # triggers only once it has fully entered, which is fine in this impl).
        doublers = sum(
            1 for c in game.players[controller].battlefield
            if c.instance_id != card.instance_id
            and 'triggers an additional time' in (c.template.oracle_text or '').lower()
        )
        trigger_multiplier = 1 + doublers

        for ability in card.template.abilities:
            if ability.ability_type == AbilityType.ETB:
                for _ in range(trigger_multiplier):
                    game._triggers_queue.append((ability, card, controller))
        # Generic "whenever another creature enters" triggers from oracle
        if card.template.is_creature:
            player = game.players[controller]
            for c in player.battlefield:
                if c.instance_id == card.instance_id:
                    continue
                oracle = (c.template.oracle_text or '').lower()
                if c.template.has_another_creature_enters_trigger:
                    if c.template.has_another_creature_enters_lifegain:
                        import re
                        m = re.search(r'gain\s+(\d+)\s+life', oracle)
                        gain = int(m.group(1)) if m else 1
                        for _ in range(trigger_multiplier):
                            game.gain_life(controller, gain, c.name)
                    # Energy trigger (Guide of Souls: "get {E}" after life gain).
                    # The parse_energy_production static was stripped of this
                    # clause to stop Guide auto-producing energy on its own
                    # ETB — re-wire the trigger here so the proper CR behavior
                    # still applies: energy lands when another creature enters.
                    if '{e}' in oracle:
                        import re
                        em = re.search(r'(?:get|gets?)\s+((?:\{e\})+)', oracle)
                        if em:
                            amt = em.group(1).count('{e}')
                            for _ in range(trigger_multiplier):
                                game.produce_energy(controller, amt, c.name)

        # Generic "whenever this creature or another [Subtype] you control enters"
        # Covers Risen Reef (Elemental) and any future cards with this pattern.
        # Crucially: the watcher CAN be the entering card itself ("whenever THIS
        # creature ... enters" means it triggers on its own ETB too).
        import re as _re
        entering_subtypes = {s.lower() for s in (card.template.subtypes or [])}
        player = game.players[controller]
        for watcher in list(player.battlefield):
            w_oracle = (watcher.template.oracle_text or '').lower()
            # Detect pattern: "whenever this creature or another [Subtype] you control enters"
            m = _re.search(
                r'whenever this creature or another (\w+) you control enters',
                w_oracle
            )
            if not m:
                continue
            required_subtype = m.group(1).lower()
            # Fire if the entering card has the required subtype
            if required_subtype not in entering_subtypes:
                continue
            # Skip if the watcher is NOT the entering card but also lacks the subtype
            # (guards against non-Elemental watchers firing on Elemental entries)
            watcher_subtypes = {s.lower() for s in (watcher.template.subtypes or [])}
            if watcher.instance_id != card.instance_id and required_subtype not in watcher_subtypes:
                continue
            # Execute the "look at top card → land to battlefield tapped / else to hand" effect.
            # Elesh Norn family: resolve once per trigger_multiplier.
            for _ in range(trigger_multiplier):
                if ('top card' in w_oracle or 'top of your library' in w_oracle) and player.library:
                    top = player.library[0]
                    if top.template.is_land:
                        game.zone_mgr.move_card(
                            game, top, "library", "battlefield",
                            cause=f"{watcher.name} → {top.name} enters tapped (land)",
                            controller_override=controller,
                        )
                        top.tapped = True
                        game._trigger_landfall(controller)
                    else:
                        game.draw_cards(controller, 1)
                        game.log.append(
                            f"T{game.display_turn} P{controller+1}: "
                            f"{watcher.name} → draws a card")

        # Generic "whenever this creature or another [Type] you control
        # enters, put a +1/+1 counter on it [and it can't be blocked this
        # turn]" (CR 603 permanent-enters trigger, by card TYPE). Covers
        # Kappa Cannoneer (artifact) and any future card with the same
        # shape. Mirrors the subtype scan above; the condition + counter
        # amount + unblockable clause are parsed once at DB load into
        # `template.enters_type_counter`. The self-ETB counts EXACTLY once
        # (watcher IS the entering card) — a single OR guard fires the
        # trigger once per qualifying ETB, never double-crediting.
        entering_types = {t.value for t in card.effective_card_types}
        for watcher in list(player.battlefield):
            spec = watcher.template.enters_type_counter
            if not spec:
                continue
            required_type = spec['permanent_type']
            if not (watcher.instance_id == card.instance_id
                    or required_type in entering_types):
                continue
            for _ in range(trigger_multiplier):
                watcher.plus_counters += spec['counter_power']
                if spec.get('unblockable_this_turn'):
                    watcher.cannot_be_blocked_this_turn = True
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{watcher.name} gets +{spec['counter_power']}/"
                f"+{spec['counter_toughness']} counter "
                f"({card.name} entered) → {watcher.power}/{watcher.toughness}")

        # Generic "whenever a[n] [nontoken] [type] you control enters,
        # create … token" watcher trigger (CR 603.6).
        #
        # Pattern: any permanent on the controller's battlefield that watches
        # for other permanents of a specific type entering and creates tokens.
        # Covers Weapons Manufacturing (nontoken artifact → Munitions token),
        # and every future card with the same oracle trigger shape.
        #
        # The dispatch is type-driven: it matches the entering permanent's card
        # types against the required type in the watcher's oracle text — no
        # card names are compared.  The "nontoken" qualifier gates on whether
        # the entering card is a token (card.is_token).  Token creation
        # delegates to game.create_token so TOKEN_DEFS / parse_token_spec
        # determine the actual token shape.
        is_entering_token = getattr(card, 'is_token', False)
        # entering_types already computed above for enters_type_counter
        _NUM_TO_INT = {"a": 1, "an": 1, "one": 1, "two": 2,
                       "three": 3, "four": 4}
        for watcher in list(player.battlefield):
            if watcher.instance_id == card.instance_id:
                continue  # self-watching handled separately above
            w_oracle = (watcher.template.oracle_text or '').lower()
            if 'whenever' not in w_oracle or 'enters' not in w_oracle:
                continue
            if 'create' not in w_oracle or 'token' not in w_oracle:
                continue
            # Match "whenever a[n] [nontoken] <type> you control enters"
            # _re is always bound at function scope (import re as _re, line ~80)
            m_watcher = _re.search(
                r'whenever an? (nontoken\s+)?(\w+) you control enters',
                w_oracle,
            )
            if not m_watcher:
                continue
            nontoken_only = bool(m_watcher.group(1))
            req_type = m_watcher.group(2)
            # Filter: nontoken condition
            if nontoken_only and is_entering_token:
                continue
            # Filter: permanent type condition
            if req_type not in entering_types:
                continue
            # Count tokens to create
            count_m = _re.search(
                r'create (a|an|one|two|three|four|\d+)\b', w_oracle)
            count = 1
            if count_m:
                tok = count_m.group(1)
                count = int(tok) if tok.isdigit() else _NUM_TO_INT.get(tok, 1)
            # Determine token type: prefer artifact creature tokens when the
            # watcher oracle mentions "artifact token" so the token counts
            # toward Improvise and metalcraft; otherwise default to creature.
            token_type = "drone" if "artifact token" in w_oracle else "creature"
            # Fire trigger (respects Panharmonicon-style trigger doublers)
            for _ in range(trigger_multiplier):
                game.create_token(controller, token_type, count=count,
                                  source_oracle=watcher.template.oracle_text)
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{watcher.name} trigger → create {count} token(s) "
                f"({card.name} entered as nontoken={not is_entering_token} "
                f"{req_type})")


    @staticmethod
    def trigger_attack(game: "GameState", attacker: CardInstance, controller: int):
        """Trigger attack abilities."""
        # Energy on attack: only fire when the "get {E}" clause is actually in
        # the attack sentence. Guide of Souls has "get {E}" in its "whenever
        # another creature enters" clause and a SEPARATE "whenever you attack,
        # you may PAY {E}{E}{E}" clause — the old loose regex matched the
        # former and fired on attacks, giving Boros free energy every swing.
        oracle = (attacker.template.oracle_text or '').lower()
        if '{e}' in oracle and attacker.template.has_attack_trigger:
            for m in re.finditer(r'(?:get|gets?)\s+((?:\{e\})+)', oracle):
                # Find this sentence's bounds
                sentence_start = max(
                    oracle.rfind('.', 0, m.start()),
                    oracle.rfind('\n', 0, m.start()),
                    -1
                ) + 1
                sentence_end = m.end()
                # Look for the sentence's full text from start to end
                tail = oracle[m.end():]
                for term in ('.', '\n'):
                    raw = tail.find(term)
                    idx = (m.end() + raw) if raw != -1 else -1
                    if idx != -1:
                        sentence_end = min(sentence_end if sentence_end > m.end() else idx, idx)
                        break
                clause = oracle[sentence_start:m.end()]
                # Fire only if this clause is an attack trigger, not an
                # "enters"/"dies"/other trigger.
                if 'attack' in clause and 'whenever' in clause:
                    # Also skip if the clause contains "may pay" (it's a payment
                    # opportunity, not a production).
                    if 'may pay' in clause or 'pay {' in clause:
                        continue
                    energy_count = m.group(1).count('{e}')
                    game.produce_energy(controller, energy_count, f"{attacker.name} attack")
                    break

        # Annihilator (CR 702.86)
        if Keyword.ANNIHILATOR in attacker.keywords:
            opponent = 1 - controller
            # Parse annihilator N from oracle text (CR 702.86a).
            #
            # The former code searched template.abilities[*].description for
            # "annihilator N", but no Ability description ever contains that
            # text -- Ability objects carry strings like "Attack trigger".  The
            # search always missed and fell back to the hardcoded default of 2,
            # producing wrong behaviour for every card whose N != 2
            # (e.g., Emrakul the Aeons Torn = 6, Kozilek = 4, Ulamog = 4,
            # Pathrazer of Ulamog = 3, Nulldrifter = 1).
            #
            # oracle_text is the authoritative source.  Fallback to 1 on a
            # malformed template (safest under-estimate).
            m_ann = re.search(
                r'annihilator\s+(\d+)',
                (attacker.template.oracle_text or '').lower(),
            )
            ann_amount = int(m_ann.group(1)) if m_ann else 1
            # Opponent sacrifices N permanents
            opp = game.players[opponent]
            sacrificed = 0
            # Sacrifice least valuable permanents
            sortable = sorted(opp.battlefield, key=lambda c: c.template.cmc)
            for perm in sortable[:ann_amount]:
                if perm in opp.battlefield:
                    game.zone_mgr.move_card(game, perm, "battlefield", "graveyard",
                                            cause=f"Annihilator {ann_amount}")
                    sacrificed += 1
            if sacrificed:
                game.log.append(f"T{game.display_turn}: Annihilator {ann_amount} - "
                                f"P{opponent+1} sacrifices {sacrificed} permanents")

        # Complex attack-trigger land search (oracle: "search...two land cards")
        if attacker.template.has_attack_trigger and getattr(attacker.template, 'has_dual_land_search', False):
            from .card_effects import _primeval_titan_search
            _primeval_titan_search(game, controller)

        # Generic oracle-text-based attack triggers (handles ALL cards)
        # Phlage, Ocelot Pride, battle cry, etc. all resolved from oracle text
        from .oracle_resolver import resolve_attack_trigger
        resolve_attack_trigger(game, attacker, controller)

        # Card-specific ATTACK handlers (e.g. Phelia blink-on-attack)
        EFFECT_REGISTRY.execute(
            attacker.template.name, EffectTiming.ATTACK, game, attacker, controller
        )

        # Generic attack triggers from ability objects
        for ability in attacker.template.abilities:
            if ability.ability_type == AbilityType.ATTACK:
                game._triggers_queue.append((ability, attacker, controller))


    @staticmethod
    def process_triggers(game: "GameState"):
        while game._triggers_queue:
            ability, source, controller = game._triggers_queue.pop(0)
            stack_item = StackItem(
                item_type=StackItemType.TRIGGERED_ABILITY,
                source=source,
                controller=controller,
                ability=ability,
                description=ability.description,
            )
            game.stack.push(stack_item)

    # ─── STATE-BASED ACTIONS ─────────────────────────────────────

