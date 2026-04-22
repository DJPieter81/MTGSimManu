"""
Permanent effects — extracted from engine/game_state.py (Commit 5b).

Lifecycle helpers for permanents + simple resource changes:
- reanimate: graveyard → battlefield under controller's control,
  with optional exile-at-EOT and haste (Goryo's, Persist).
- create_token: spawn tokens from TOKEN_DEFS or construct them from
  token_type/P/T/keywords with Affinity-aware auto-scaling.
- _creature_dies: death trigger fan-out + undying/persist/dredge
  checks + LTB-to-graveyard zone move.
- _permanent_destroyed: non-creature destruction path.
- _exile_permanent: move a permanent to exile.
- _bounce_permanent: move a permanent to owner's hand.
- gain_life: life gain with lifegain-trigger fan-out.
- produce_energy: add energy counters.
- spend_energy_for_effect: spend energy counters.

Methods are static and take game: GameState as the first argument,
matching the manager pattern used across engine/*.py.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, List

from .cards import (
    CardInstance, CardTemplate, CardType, Keyword, Supertype, Color,
)
from .card_effects import EFFECT_REGISTRY, EffectTiming
from .mana import ManaCost
from .player_state import TOKEN_DEFS

if TYPE_CHECKING:
    from .game_state import GameState


class PermanentEffects:
    """Stateless lifecycle + resource helpers for permanents."""

    @staticmethod
    def reanimate(game: "GameState", controller: int, target_card: CardInstance,
                  exile_at_eot: bool = False, give_haste: bool = False):
        """Put a creature from graveyard onto the battlefield."""
        player = game.players[controller]
        if target_card not in player.graveyard:
            return

        player.graveyard.remove(target_card)
        target_card.controller = controller
        target_card.enter_battlefield()
        if give_haste:
            target_card.temp_keywords.add(Keyword.HASTE)
        player.battlefield.append(target_card)

        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"Reanimate {target_card.name}")

        if exile_at_eot:
            game._end_of_turn_exiles.append((target_card, controller))

        # Trigger ETB
        game._handle_permanent_etb(target_card, controller)

    # ─── TOKEN GENERATION ────────────────────────────────────────


    @staticmethod
    def create_token(game: "GameState", controller: int, token_type: str,
                     count: int = 1, power: int = None, toughness: int = None,
                     extra_keywords: Set[Keyword] = None) -> List[CardInstance]:
        """Create token creatures on the battlefield."""
        tokens = []
        token_def = TOKEN_DEFS.get(token_type)
        if not token_def:
            # Generic token
            token_def = (token_type.title(), [CardType.CREATURE], power or 1, toughness or 1, set())

        t_name, t_types, t_power, t_toughness, t_keywords = token_def
        if power is not None:
            t_power = power
        if toughness is not None:
            t_toughness = toughness
        kw_set = set(t_keywords)
        if extra_keywords:
            kw_set |= extra_keywords

        # Oracle text on the generated template so _dynamic_base_power's
        # regex can find the scaling pattern. Without this, Construct tokens
        # from Urza's Saga Ch II have no oracle_text, the regex
        # `\+\d+/\+\d+ for each artifact you control` doesn't fire, and they
        # stay 0/0 → die immediately to state-based actions. Root-caused from
        # verbose vs Affinity: "T4: Construct Token dies" on Ch II resolution.
        TOKEN_ORACLES = {
            "construct": "This creature gets +1/+1 for each artifact you control.",
        }
        token_oracle = TOKEN_ORACLES.get(token_type, "")

        for _ in range(count):
            template = CardTemplate(
                name=f"{t_name} Token",
                card_types=list(t_types),
                mana_cost=ManaCost(),
                power=t_power,
                toughness=t_toughness,
                keywords=kw_set,
                tags={"token", "creature"},
                oracle_text=token_oracle,
            )
            instance = CardInstance(
                template=template,
                owner=controller,
                controller=controller,
                instance_id=game.next_instance_id(),
                zone="battlefield",
            )
            instance._game_state = game
            instance.enter_battlefield()
            game.players[controller].battlefield.append(instance)
            tokens.append(instance)

        if count > 0:
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                            f"Create {count}x {t_name} token(s)")
        return tokens

    # ─── PLANESWALKER ABILITIES ──────────────────────────────────


    @staticmethod
    def _creature_dies(game: "GameState", creature: CardInstance):
        """Handle a creature dying."""
        owner = creature.owner
        controller = creature.controller

        if creature in game.players[controller].battlefield:
            game.players[controller].battlefield.remove(creature)

        # Undying: return with +1/+1 counter
        if Keyword.UNDYING in creature.keywords and creature.plus_counters == 0:
            creature.zone = "graveyard"
            creature.reset_combat()
            creature.cleanup_damage()
            # Return to battlefield with +1/+1 counter
            creature.controller = controller
            creature.enter_battlefield()
            creature.plus_counters += 1
            game.players[controller].battlefield.append(creature)
            game.log.append(f"T{game.display_turn}: {creature.name} returns (undying)")
            return

        # Persist: return with -1/-1 counter
        if Keyword.PERSIST in creature.keywords and creature.minus_counters == 0:
            creature.zone = "graveyard"
            creature.reset_combat()
            creature.cleanup_damage()
            creature.controller = controller
            creature.enter_battlefield()
            creature.minus_counters += 1
            game.players[controller].battlefield.append(creature)
            game.log.append(f"T{game.display_turn}: {creature.name} returns (persist)")
            return

        # Equipment falls off: when equipped creature dies, mark equipment
        # as unattached so the AI must pay to re-equip
        equip_tags_on_creature = [
            t for t in creature.instance_tags
            if t.startswith("equipped_")
        ]
        if equip_tags_on_creature:
            for tag in equip_tags_on_creature:
                # Parse the equipment instance_id from the tag
                try:
                    equip_iid = int(tag[len("equipped_"):])
                    equip_perm = game.get_card_by_id(equip_iid)
                    if equip_perm:
                        equip_perm.instance_tags.discard("equipment_attached")
                        equip_perm.instance_tags.add("equipment_unattached")
                        game.log.append(
                            f"T{game.display_turn}: {equip_perm.template.name} falls off "
                            f"{creature.name} (unattached)")
                except (ValueError, AttributeError):
                    pass

        creature.zone = "graveyard"
        creature.reset_combat()
        creature.cleanup_damage()
        creature._dashed = False  # Clear Dash flag on death
        creature._evoked = False  # Clear Evoke flag on death
        game.players[owner].graveyard.append(creature)
        game.players[controller].creatures_died_this_turn += 1

        # Generic oracle-text-based dies triggers
        if creature.template.name not in EFFECT_REGISTRY._handlers:
            from .oracle_resolver import resolve_dies_trigger
            resolve_dies_trigger(game, creature, controller)

        game.log.append(f"T{game.display_turn}: {creature.name} dies")


    @staticmethod
    def _permanent_destroyed(game: "GameState", permanent: CardInstance):
        if permanent.template.is_creature:
            game._creature_dies(permanent)
        else:
            game.zone_mgr.move_card(
                game, permanent, "battlefield", "graveyard",
                cause="destroyed"
            )


    @staticmethod
    def _exile_permanent(game: "GameState", permanent: CardInstance):
        game.zone_mgr.move_card(
            game, permanent, "battlefield", "exile",
            cause="exiled"
        )


    @staticmethod
    def _bounce_permanent(game: "GameState", permanent: CardInstance):
        game.zone_mgr.move_card(
            game, permanent, "battlefield", "hand",
            cause="bounced"
        )


    @staticmethod
    def gain_life(game: "GameState", player_idx: int, amount: int, source: str = ""):
        """Centralized lifegain with triggers (Ocelot Pride, etc.)."""
        if amount <= 0:
            return
        player = game.players[player_idx]
        player.life += amount
        player.life_gained_this_turn += amount
        game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                        f"Gain {amount} life from {source} (life: {player.life})")
        # Generic "whenever you gain life" triggers from oracle
        for creature in list(player.creatures):
            oracle = (creature.template.oracle_text or '').lower()
            if 'whenever you gain life' in oracle and 'create' in oracle and 'token' in oracle:
                # Parse token type from oracle if possible
                token_type = "cat" if "cat" in oracle else "creature"
                game.create_token(player_idx, token_type, count=1)
                break  # once per lifegain event

    # ─── SPELL EFFECTS ───────────────────────────────────────────


    @staticmethod
    def produce_energy(game: "GameState", player_idx: int, amount: int, source_name: str = ""):
        """Add energy counters to a player."""
        game.players[player_idx].add_energy(amount)
        game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                        f"+{amount} energy from {source_name} "
                        f"(total: {game.players[player_idx].energy_counters})")


    @staticmethod
    def spend_energy_for_effect(game: "GameState", player_idx: int, amount: int,
                                 effect_type: str = "") -> bool:
        """Spend energy for an effect. Returns True if successful."""
        if game.players[player_idx].spend_energy(amount):
            game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                            f"Spend {amount} energy for {effect_type}")
            return True
        return False

