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
                if 'another creature' in oracle and 'enters' in oracle:
                    if 'gain' in oracle and 'life' in oracle:
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
                        player.library.pop(0)
                        top.zone = 'battlefield'
                        top.tapped = True
                        player.battlefield.append(top)
                        game.log.append(
                            f"T{game.display_turn} P{controller+1}: "
                            f"{watcher.name} → {top.name} enters tapped (land)")
                        game._trigger_landfall(controller)
                    else:
                        game.draw_cards(controller, 1)
                        game.log.append(
                            f"T{game.display_turn} P{controller+1}: "
                            f"{watcher.name} → draws a card")


    @staticmethod
    def trigger_attack(game: "GameState", attacker: CardInstance, controller: int):
        """Trigger attack abilities."""
        # Energy on attack: only fire when the "get {E}" clause is actually in
        # the attack sentence. Guide of Souls has "get {E}" in its "whenever
        # another creature enters" clause and a SEPARATE "whenever you attack,
        # you may PAY {E}{E}{E}" clause — the old loose regex matched the
        # former and fired on attacks, giving Boros free energy every swing.
        oracle = (attacker.template.oracle_text or '').lower()
        if '{e}' in oracle and 'attack' in oracle and 'get' in oracle:
            import re
            for m in re.finditer(r'(?:get|gets?)\s+((?:\{e\})+)', oracle):
                # Find this sentence's bounds
                sentence_start = max(
                    oracle.rfind('.', 0, m.start()),
                    oracle.rfind('\n', 0, m.start()),
                    -1
                ) + 1
                sentence_end = m.end()
                # Look for the sentence's full text from start to end
                for term in ('.', '\n'):
                    idx = oracle.find(term, m.end())
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

        # Annihilator
        if Keyword.ANNIHILATOR in attacker.keywords:
            opponent = 1 - controller
            # Parse annihilator amount from oracle text
            import re
            oracle = attacker.template.abilities
            ann_amount = 2  # default
            for ab in oracle:
                m = re.search(r'annihilator\s+(\d+)', ab.description.lower())
                if m:
                    ann_amount = int(m.group(1))
                    break
            # Opponent sacrifices N permanents
            opp = game.players[opponent]
            sacrificed = 0
            # Sacrifice least valuable permanents
            sortable = sorted(opp.battlefield, key=lambda c: c.template.cmc)
            for perm in sortable[:ann_amount]:
                if perm in opp.battlefield:
                    opp.battlefield.remove(perm)
                    perm.zone = "graveyard"
                    game.players[perm.owner].graveyard.append(perm)
                    sacrificed += 1
            if sacrificed:
                game.log.append(f"T{game.display_turn}: Annihilator {ann_amount} - "
                                f"P{opponent+1} sacrifices {sacrificed} permanents")

        # Complex attack-trigger land search (oracle: "search...two land cards")
        oracle = (attacker.template.oracle_text or '').lower()
        if 'attack' in oracle and 'search' in oracle and 'two land' in oracle:
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

    # ─── TRIGGER QUEUE (for ZoneManager integration) ──────────────


    @staticmethod
    def queue_trigger(game: "GameState", trigger_reg):
        """Queue a triggered ability from the event system.

        This bridges the new EventBus trigger system with the existing
        _triggers_queue / process_triggers workflow.
        """
        from .event_system import TriggerRegistration
        if isinstance(trigger_reg, TriggerRegistration):
            # Create a synthetic Ability to wrap the event-based trigger
            ability = Ability(
                ability_type=AbilityType.TRIGGERED,
                description=trigger_reg.description,
                effect=trigger_reg.effect,
            )
            game._triggers_queue.append(
                (ability, trigger_reg.card, trigger_reg.controller)
            )

    # ─── STATE-BASED ACTIONS ─────────────────────────────────────

