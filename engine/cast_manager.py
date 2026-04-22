"""
Cast manager — extracted from engine/game_state.py.

Owns spell-casting pre-checks and post-cast trigger fan-out:
- can_cast: full legality check for a spell from hand or graveyard.
  Covers flashback, escape, suspend, Canonist, phase gating, target
  validation, cost reductions (domain/generic/Affinity/delve/Phyrexian),
  evoke/dash/warp/improvise alternatives, colored-mana feasibility
  via MRV greedy, and Blink target gating.
- _handle_storm: create storm copies equal to spells-cast-this-turn-1
  by re-executing the spell's effect for each copy.
- _handle_cascade: exile top of library until a cheaper spell is
  found, cast it for free (or resolve Living End mass-reanimate),
  bottom remaining in random order.

cast_spell itself will move here in a follow-up commit (Commit 4b).

Methods are static and take game: GameState as the first argument,
matching the manager pattern established in combat_manager.py and
mana_payment.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .cards import CardType, Keyword
from .mana_payment import ALL_COLORS
from .stack import StackItem

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState, Phase


class CastManager:
    """Cast-time legality + special-case handlers. Stateless."""

    @staticmethod
    def can_cast(game: "GameState", player_idx: int,
                 card: "CardInstance") -> bool:
        """Check if a player can cast a card."""
        # Late import to avoid a cycle: Phase is defined in game_state.py.
        from .game_state import Phase

        player = game.players[player_idx]
        template = card.template

        if card.zone != "hand" and card.zone != "graveyard":
            return False

        # Graveyard casting: Flashback or Escape
        if card.zone == "graveyard":
            # Escape: can cast from graveyard if we have enough mana AND
            # enough other cards in graveyard to exile
            if template.escape_cost is not None:
                other_gy_cards = sum(1 for c in player.graveyard if c != card)
                if other_gy_cards < template.escape_exile_count:
                    return False  # Not enough cards to exile
                # Check mana for escape cost
                untapped_lands = player.untapped_lands
                total_mana = (len(untapped_lands) + player.mana_pool.total()
                              + player._tron_mana_bonus())
                if total_mana < template.escape_cost:
                    return False
                return True  # Can escape
            elif not card.has_flashback:
                return False  # No flashback, no escape — can't cast from GY
            else:
                # Generic flashback-with-additional-cost parsing.
                # "Flashback—Sacrifice a {subtype}." (Lava Dart pattern)
                # If the printed flashback cost requires sacrificing a
                # land subtype, ensure such a land is available.
                import re as _re_fb
                fb_oracle = (template.oracle_text or '').lower()
                m = _re_fb.search(
                    r'flashback\s*[—\-:]\s*sacrifice a (\w+)', fb_oracle)
                if m:
                    needed = m.group(1).strip()
                    # Match by subtype (Mountain/Island/etc.) or name token
                    matches = [
                        l for l in player.lands
                        if needed in [s.lower() for s in (l.template.subtypes or [])]
                        or needed in (l.template.name or '').lower()
                    ]
                    if not matches:
                        return False  # cannot pay flashback sacrifice cost
        # Cards with no mana cost cannot be cast from hand (CR 202.1a).
        # Covers suspend-only cards (Living End, Ancestral Vision, etc.)
        # that can only be cast via cascade, suspend, or other special
        # means. Detection: has Suspend keyword AND CMC == 0.
        if (card.zone == "hand" and template.cmc == 0
                and Keyword.SUSPEND in template.keywords):
            return False

        if template.is_land:
            max_lands = 1 + player.extra_land_drops
            return player.lands_played_this_turn < max_lands

        # Ethersworn Canonist: block nonartifact spells if one was already cast
        if CardType.ARTIFACT not in template.card_types:
            canonist_active = any(
                "canonist_active" in c.instance_tags
                for p in game.players for c in p.battlefield
            )
            if (canonist_active
                    and player.nonartifact_spells_cast_this_turn >= 1):
                return False

        is_main_phase = game.current_phase in (Phase.MAIN1, Phase.MAIN2)
        is_active = game.active_player == player_idx

        if template.is_instant or template.has_flash:
            pass
        elif template.is_creature or template.is_sorcery or \
                CardType.ENCHANTMENT in template.card_types or \
                CardType.ARTIFACT in template.card_types or \
                CardType.PLANESWALKER in template.card_types:
            if not (is_main_phase and is_active and game.stack.is_empty):
                return False

        # Target validation (CR 601.2c): a spell with a required target
        # cannot be cast if no legal target exists.
        if template.is_instant or template.is_sorcery:
            oracle_l = (template.oracle_text or "").lower()
            if 'target creature you control' in oracle_l:
                if not player.creatures:
                    return False
            elif ('target creature' in oracle_l
                    and 'target creature or planeswalker' not in oracle_l
                    and 'up to' not in oracle_l.split('target creature')[0][-20:]):
                # "target creature" (any controller) — need at least one
                # creature on board
                opp = game.players[1 - player_idx]
                if not player.creatures and not opp.creatures:
                    return False

        # Check mana (pool + untapped lands + Tron bonus)
        untapped_lands = player.untapped_lands
        total_mana = (len(untapped_lands) + player.mana_pool.total()
                      + player._tron_mana_bonus())

        # X-cost spells: require minimum mana to cast meaningfully
        if template.x_cost_data:
            x_info = template.x_cost_data
            min_mana = x_info["multiplier"] * max(x_info["min_x"], 1)
            if total_mana < min_mana:
                return False

        # Cost reductions
        effective_cmc = template.mana_cost.cmc
        # Domain cost reduction (oracle-derived template property)
        if template.domain_reduction > 0:
            domain = game._count_domain(player_idx)
            effective_cmc = max(
                0, effective_cmc - template.domain_reduction * domain)
        # Generic cost reduction from permanents on battlefield
        from .oracle_resolver import count_cost_reducers
        generic_reduction = count_cost_reducers(game, player_idx, template)
        if generic_reduction > 0:
            effective_cmc = max(0, effective_cmc - generic_reduction)
        # Affinity for artifacts
        if Keyword.AFFINITY in template.keywords:
            artifact_count = sum(
                1 for c in player.battlefield
                if CardType.ARTIFACT in c.template.card_types
            )
            effective_cmc = max(0, effective_cmc - artifact_count)
        # Delve
        if template.has_delve:
            gy_count = len(player.graveyard)
            colored_cost = (template.mana_cost.white + template.mana_cost.blue
                            + template.mana_cost.black
                            + template.mana_cost.red
                            + template.mana_cost.green)
            generic_portion = max(0, effective_cmc - colored_cost)
            delve_reduction = min(gy_count, generic_portion)
            effective_cmc = max(colored_cost, effective_cmc - delve_reduction)

        # Phyrexian mana: 2 life per Phyrexian symbol instead of mana
        oracle = (template.oracle_text or '')
        if '/P}' in oracle or '/p}' in oracle.lower():
            phyrexian_count = oracle.lower().count('/p}')
            life_cost = phyrexian_count * 2
            if player.life > life_cost:
                effective_cmc = max(0, effective_cmc - phyrexian_count)

        # Evoke as alternative cost (Solitude, Endurance, Grief, etc.)
        can_evoke = False
        if (template.evoke_cost is not None
                and total_mana < effective_cmc):
            exile_candidates = [
                c for c in player.hand
                if c != card
                and not c.template.is_land
                and c.template.color_identity & template.color_identity
            ]
            if exile_candidates:
                can_evoke = True
                # Target validation: don't allow evoke if the card needs
                # a target and no valid target exists
                from decks.card_knowledge_loader import requires_target as _req_target
                oracle_lower = (template.oracle_text or "").lower()
                needs_target = (
                    _req_target(template.name)
                    or ('target creature' in oracle_lower)
                    or ('creature spell' in oracle_lower
                        and 'removal' not in (template.tags or set()))
                )
                if needs_target:
                    opp_idx = 1 - player_idx
                    if not game.players[opp_idx].creatures:
                        can_evoke = False  # No targets for evoke
                if can_evoke:
                    can_evoke = game.callbacks.should_evoke(
                        game, player_idx, card)

        if can_evoke:
            return True  # Can cast via evoke

        # Dash alternative cost
        if (template.dash_cost is not None
                and total_mana >= template.dash_cost):
            return True

        # Warp alternative cost (Pinnacle Emissary)
        oracle = (template.oracle_text or "").lower()
        if "warp" in oracle:
            has_artifact = any(
                'Artifact' in str(getattr(c.template, 'card_types', []))
                for c in player.battlefield
            )
            if has_artifact and total_mana >= 1:
                return True

        # Improvise: tap artifacts to pay generic (Kappa Cannoneer, etc.)
        if "improvise" in oracle:
            untapped_artifacts = sum(
                1 for c in player.battlefield
                if hasattr(c, 'template')
                and 'Artifact' in str(getattr(c.template, 'card_types', []))
                and not c.template.is_land
                and not getattr(c, 'tapped', False)
                and c != card
            )
            improvise_cmc = max(0, effective_cmc - untapped_artifacts)
            if total_mana >= improvise_cmc:
                return True

        # Force alternate cost: "exile a [color] card from your hand
        # rather than pay this spell's mana cost" — only on opp's turn
        oracle_lower = (template.oracle_text or '').lower()
        if 'exile a' in oracle_lower and 'rather than pay' in oracle_lower:
            if game.active_player != player_idx:
                import re
                m = re.search(
                    r'exile an? (\w+) card from your hand', oracle_lower)
                if m:
                    color_word = m.group(1)
                    color_map = {'blue': 'U', 'green': 'G', 'red': 'R',
                                 'white': 'W', 'black': 'B'}
                    req_color = color_map.get(color_word, '')
                    if req_color:
                        from .cards import Color
                        color_enum = {'U': Color.BLUE, 'G': Color.GREEN,
                                      'R': Color.RED,
                                      'W': Color.WHITE,
                                      'B': Color.BLACK}.get(req_color)
                        has_exile_target = any(
                            c != card
                            and color_enum in c.template.color_identity
                            for c in player.hand
                        )
                        if has_exile_target:
                            return True  # Can cast for free

        if total_mana < effective_cmc:
            return False

        # Detailed color check using greedy constraint solving (MRV).
        cost = template.mana_cost
        color_needs = []
        for color, needed in [("W", cost.white), ("U", cost.blue),
                              ("B", cost.black), ("R", cost.red),
                              ("G", cost.green), ("C", cost.colorless)]:
            for _ in range(needed):
                color_needs.append(color)

        has_leyline = game._has_leyline_of_guildpact(player_idx)
        all_colors_set = set(ALL_COLORS)
        sources = []
        for land in untapped_lands:
            sources.append(
                all_colors_set if has_leyline
                else set(land.template.produces_mana))
        # Mana pool as fixed-color sources
        for color in ["W", "U", "B", "R", "G", "C"]:
            pool_amount = player.mana_pool.get(color)
            for _ in range(pool_amount):
                sources.append({color})

        if len(sources) < effective_cmc:
            return False

        # Color assignment: greedy with re-sorting after each step.
        used = [False] * len(sources)

        remaining_needs = list(color_needs)
        while remaining_needs:
            # Re-sort by scarcity
            remaining_needs.sort(
                key=lambda c: sum(
                    1 for i, s in enumerate(sources)
                    if c in s and not used[i])
            )
            c = remaining_needs.pop(0)
            # Find least-flexible unused source
            best_idx = -1
            best_flex = 999
            for i, s in enumerate(sources):
                if not used[i] and c in s:
                    flex = len(s)
                    if flex < best_flex:
                        best_flex = flex
                        best_idx = i
            if best_idx == -1:
                return False
            used[best_idx] = True

        # Check total mana (generic portion)
        remaining_sources = sum(1 for u in used if not u)
        generic_needed = effective_cmc - len(color_needs)
        if remaining_sources < generic_needed:
            return False

        # Blink spells require a friendly creature target
        if 'blink' in (template.tags or set()):
            if not player.creatures:
                return False

        return True

    @staticmethod
    def _handle_storm(game: "GameState", item: StackItem) -> None:
        """Create storm copies. Storm count = spells cast this turn - 1."""
        copies = game._global_storm_count - 1
        if copies <= 0:
            return

        controller = item.controller
        card = item.source
        game.log.append(f"T{game.display_turn}: Storm copies: {copies}")

        for i in range(copies):
            game._execute_spell_effects(item)
            if game.game_over:
                return

    @staticmethod
    def _handle_cascade(game: "GameState", item: StackItem) -> None:
        """Cascade: exile from top until CMC < cascade spell, cast free,
        rest on bottom (random order)."""
        controller = item.controller
        cascade_cmc = item.source.template.cmc
        player = game.players[controller]
        exiled = []
        found_card = None

        game.log.append(f"T{game.display_turn}: Cascade (CMC < {cascade_cmc})")

        while player.library:
            top = player.library.pop(0)
            top.zone = "exile"
            player.exile.append(top)
            exiled.append(top)

            if top.template.is_spell and top.template.cmc < cascade_cmc:
                found_card = top
                break

        if found_card:
            game.log.append(
                f"T{game.display_turn}: Cascade hits {found_card.name}")

            # Detect "exile all creatures + return from GY" effects
            # (Living End and similar). Oracle pattern: 'all creature
            # cards' AND 'graveyard' AND a return-to-battlefield effect.
            found_oracle = (found_card.template.oracle_text or '').lower()
            is_mass_reanimate = (
                'all creature cards' in found_oracle
                and 'graveyard' in found_oracle
                and 'battlefield' in found_oracle
            )
            if is_mass_reanimate:
                game._resolve_living_end(controller)
                found_card.zone = "graveyard"
                if found_card in player.exile:
                    player.exile.remove(found_card)
                player.graveyard.append(found_card)
            else:
                # Cast the found card for free
                if found_card in player.exile:
                    player.exile.remove(found_card)
                found_card.zone = "hand"
                player.hand.append(found_card)
                found_card._free_cast_opportunity = True
                game.cast_spell(controller, found_card, free_cast=True)
                # Resolve immediately
                while not game.stack.is_empty:
                    game.resolve_stack()
                    game.check_state_based_actions()
                    if game.game_over:
                        return

        # Put remaining exiled cards on bottom in random order
        remaining = [c for c in exiled if c != found_card]
        game.rng.shuffle(remaining)
        for c in remaining:
            if c in player.exile:
                player.exile.remove(c)
            c.zone = "library"
            player.library.append(c)
