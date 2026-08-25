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
            game.register_end_of_turn_exile(target_card, controller)

        # Trigger ETB
        game._handle_permanent_etb(target_card, controller)

    # ─── LAND ANIMATION (Track H) ────────────────────────────────

    @staticmethod
    def animate_land(game: "GameState", controller: int,
                     land: CardInstance) -> bool:
        """Execute an activated land-animation line ("{cost}: … this
        land becomes an N/M … creature … until end of turn").

        Rules enforcement only — no scoring.  The cost is paid by
        tapping OTHER untapped lands (generic-count payment, the same
        model the granted-ability dispatch uses); the source stays
        untapped so it can attack.  Returns True when the activation
        was legal and executed.
        """
        from .oracle_parser import parse_land_animation
        player = game.players[controller]
        if land not in player.battlefield or not land.template.is_land:
            return False
        if land.is_animated:
            return False  # animating twice this turn adds nothing here
        spec = parse_land_animation(land.template.oracle_text or '')
        if spec is None:
            return False
        payers = [l for l in player.untapped_lands if l is not land]
        if len(payers) < spec['cost']:
            return False
        for payer in payers[:spec['cost']]:
            payer.tapped = True
        land.is_animated = True
        land.temp_power_mod += spec['power']
        land.temp_toughness_mod += spec['toughness']
        kw_by_value = {k.value: k for k in Keyword}
        for kw in spec['keywords']:
            kw_enum = kw_by_value.get(kw.replace(' ', '_'))
            if kw_enum is not None:
                land.temp_keywords.add(kw_enum)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {land.name} becomes "
            f"a {spec['power']}/{spec['toughness']} creature until end "
            f"of turn (pays {spec['cost']} mana)")
        return True

    # ─── TOKEN GENERATION ────────────────────────────────────────


    @staticmethod
    def create_token(game: "GameState", controller: int, token_type: str,
                     count: int = 1, power: int = None, toughness: int = None,
                     extra_keywords: Set[Keyword] = None,
                     source_oracle: str = None) -> List[CardInstance]:
        """Create token creatures on the battlefield.

        Generic-first design (post-Phase-1C-followup): when
        ``source_oracle`` is provided, the spawning card's oracle
        text is parsed via ``engine.oracle_parser.parse_token_spec``
        to extract P/T, types, and keywords directly. This avoids
        hardcoding card-name-specific token registrations in
        TOKEN_DEFS.

        TOKEN_DEFS is preserved as a fallback for the small set of
        canonical resource tokens (Treasure, Food, Clue, Goblin,
        Soldier) where oracle text is not always passed by the
        caller. Future cleanup will migrate every caller to pass
        source_oracle so TOKEN_DEFS can be retired.
        """
        tokens = []
        # Generic path: parse from the spawning card's oracle text.
        spec = None
        if source_oracle:
            from .oracle_parser import parse_token_spec
            spec = parse_token_spec(source_oracle)

        if spec is not None:
            from .cards import Keyword as _Kw
            kw_lookup = {k.value: k for k in _Kw}
            type_lookup = {
                "artifact": CardType.ARTIFACT,
                "creature": CardType.CREATURE,
                "enchantment": CardType.ENCHANTMENT,
            }
            t_name = spec["subtype"]
            t_types = [type_lookup[t] for t in spec["types"]
                       if t in type_lookup]
            if CardType.CREATURE not in t_types:
                t_types.append(CardType.CREATURE)
            t_power = spec["power"]
            t_toughness = spec["toughness"]
            kw_set = {kw_lookup[w.replace(" ", "_")]
                      for w in spec["keywords"]
                      if w.replace(" ", "_") in kw_lookup}
        else:
            token_def = TOKEN_DEFS.get(token_type)
            if not token_def:
                token_def = (token_type.title(), [CardType.CREATURE],
                             power or 1, toughness or 1, set())
            t_name, t_types, t_power, t_toughness, t_keywords = token_def
            kw_set = set(t_keywords)

        if power is not None:
            t_power = power
        if toughness is not None:
            t_toughness = toughness
        if extra_keywords:
            kw_set |= extra_keywords

        # Oracle text on the generated template — when source_oracle
        # carries a "with 'gets +N/+N for each artifact ...'" clause,
        # _dynamic_base_power picks up the scaling regex. We extract
        # the inner-quoted text from source_oracle when present.
        token_oracle = ""
        if source_oracle:
            import re as _re
            inner = _re.search(
                r"token\s+with\s+['\"]([^'\"]+)['\"]",
                source_oracle, flags=_re.IGNORECASE,
            )
            if inner:
                token_oracle = inner.group(1)
        if not token_oracle and token_type == "construct":
            # Legacy fallback for callers that don't pass oracle.
            token_oracle = (
                "This creature gets +1/+1 for each artifact you control."
            )

        from .oracle_parser import parse_has_artifact_count_scaling as _parse_art_scale
        _art_scale = _parse_art_scale(token_oracle)
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
            template.has_artifact_count_scaling = _art_scale
            # A token created "with '<ability>'" carries that ability. When it
            # is a sacrifice-for-mana ability (Eldrazi Spawn, Treasure), record
            # the one-shot units so the token counts as ramp rather than as a
            # dead 0/1 body.
            from .oracle_parser import (
                parse_sacrifice_mana_units as _parse_sac_mana)
            template.sacrifice_mana_units = (
                _parse_sac_mana(token_oracle) or [])
            instance = CardInstance(
                template=template,
                owner=controller,
                controller=controller,
                instance_id=game.next_instance_id(),
                zone="battlefield",
            )
            instance.is_token = True  # CR 111 — see cards.py field doc
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

        # Replacement effects: counters must be readable BEFORE any cleanup.
        # Undying (CR 702.94): return to battlefield with +1/+1 counter if no +1/+1 counter.
        if Keyword.UNDYING in creature.keywords and creature.plus_counters == 0:
            if creature in game.players[controller].battlefield:
                game.players[controller].battlefield.remove(creature)
            creature.zone = "graveyard"  # transitional; CR 701.12 replacement redirects to BTL
            creature.reset_combat()
            creature.cleanup_damage()
            creature.controller = controller
            creature.enter_battlefield()
            creature.plus_counters += 1
            game.players[controller].battlefield.append(creature)
            game.log.append(f"T{game.display_turn}: {creature.name} returns (undying)")
            # CR 603.6a: the returned creature is a NEW object entering the
            # battlefield — its ETB triggers fire again (mirror reanimate()).
            game._handle_permanent_etb(creature, controller)
            return

        # Persist (CR 702.78): return to battlefield with -1/-1 counter if no -1/-1 counter.
        if Keyword.PERSIST in creature.keywords and creature.minus_counters == 0:
            if creature in game.players[controller].battlefield:
                game.players[controller].battlefield.remove(creature)
            creature.zone = "graveyard"  # transitional; CR 701.12 replacement redirects to BTL
            creature.reset_combat()
            creature.cleanup_damage()
            creature.controller = controller
            creature.enter_battlefield()
            creature.minus_counters += 1
            game.players[controller].battlefield.append(creature)
            game.log.append(f"T{game.display_turn}: {creature.name} returns (persist)")
            # CR 603.6a: the returned creature is a NEW object entering the
            # battlefield — its ETB triggers fire again (mirror reanimate()).
            game._handle_permanent_etb(creature, controller)
            return

        # Equipment falls off: read instance_tags BEFORE move_card clears them.
        equip_tags_on_creature = [
            t for t in creature.instance_tags
            if t.startswith("equipped_")
        ]
        if equip_tags_on_creature:
            for tag in equip_tags_on_creature:
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

        # Modular (CR 702.43): capture +1/+1 counters BEFORE zone_mgr.move_card
        # clears them via _cleanup_leaving_battlefield.  The triggered ability
        # fires after the creature is in the graveyard, but the counter count
        # must be read while the creature is still on the battlefield.
        # Keyed on Keyword.MODULAR — no card name involved.
        _modular_counters = 0
        if Keyword.MODULAR in creature.keywords and creature.plus_counters > 0:
            _modular_counters = creature.plus_counters

        # Route zone mutation through the funnel: single owner of battlefield→graveyard
        # list mutation, zone attribute, and leaving-battlefield cleanup.
        game.zone_mgr.move_card(game, creature, "battlefield", "graveyard")
        game.players[controller].creatures_died_this_turn += 1

        # Modular death trigger: transfer captured counters to best artifact creature.
        # The AI always takes the optional transfer when a valid target exists
        # (unconditionally beneficial). Runs after zone move so the dead creature
        # is no longer a valid target for itself.
        if _modular_counters > 0:
            _artifact_creatures = [
                c for c in game.players[controller].battlefield
                if CardType.CREATURE in c.effective_card_types
                and CardType.ARTIFACT in c.effective_card_types
            ]
            if _artifact_creatures:
                _target = max(_artifact_creatures, key=lambda c: c.power)
                _target.plus_counters += _modular_counters
                game.log.append(
                    f"T{game.display_turn}: {creature.name} modular — "
                    f"move {_modular_counters} counter(s) to {_target.name}"
                )

        # Dies triggers: a registered EffectTiming.DIES handler owns
        # this card's dies behavior (mirrors the ETB execute-then-
        # fallback pattern at card_effects.py:69); only fall back to
        # the generic oracle-derived path when no DIES-specific
        # handler is registered.
        if not EFFECT_REGISTRY.execute(creature.template.name, EffectTiming.DIES,
                                       game, creature, controller):
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
        # Generic "whenever you gain life, create a token" triggers — typed field,
        # no runtime oracle-text inspection (see CardTemplate.has_lifegain_token_trigger).
        for creature in list(player.creatures):
            if creature.template.has_lifegain_token_trigger:
                game.create_token(player_idx, creature.template.lifegain_token_type, count=1)
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

