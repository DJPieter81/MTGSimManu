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
from .stack import StackItem, StackItemType

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
        # Evoke is independent of the hardcast path: it is a *choice*
        # the caster makes, not a fallback for when mana is short. The
        # evoke branch is available whenever the evoke cost is payable
        # (mana portion of evoke_cost + exile fodder + valid target).
        # `can_cast` returns True if EITHER mode is payable; the AI
        # layer decides which mode to use at resolution time.
        #
        # Bug E3 (pre-fix gate `total_mana < effective_cmc`): with
        # five untapped Mountains and a white card in hand, Solitude
        # reported uncastable — total_mana met the CMC, so the evoke
        # branch was skipped, and the colour check then failed because
        # no white source was on the battlefield. Jeskai Blink relied
        # on Solitude as a free evoke removal response in opponent
        # windows; the gate masked it.
        can_evoke = False
        if template.evoke_cost is not None:
            # Evoke cost may itself include a mana component (most
            # evoke creatures do not, but the engine permits it).
            # Verify the caster has enough total mana to cover the
            # evoke cost; the colour check for the evoke cost itself
            # is handled at resolution. No magic number: falls back
            # to zero for the common pitch-evoke pattern.
            evoke_mana_needed = template.evoke_cost.cmc
            if total_mana >= evoke_mana_needed:
                exile_candidates = [
                    c for c in player.hand
                    if c != card
                    and not c.template.is_land
                    and c.template.color_identity & template.color_identity
                ]
                if exile_candidates:
                    can_evoke = True
                    # Target validation: don't allow evoke if the card
                    # needs a target and no valid target exists
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

        # Routes through `_effective_produces_mana` so Leyline of the
        # Guildpact and dynamic mana abilities (E1: Mox Opal metalcraft,
        # CR 702.98) contribute the right colour set for the feasibility
        # solver.
        sources = []
        for land in untapped_lands:
            sources.append(set(game._effective_produces_mana(player_idx, land)))
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

    @staticmethod
    def cast_spell(game: "GameState", player_idx: int, card: "CardInstance",
                   targets=None, free_cast: bool = False) -> bool:
        """Cast a spell: pay costs and put on stack. free_cast skips mana payment."""
        player = game.players[player_idx]
        template = card.template

        if not free_cast and not game.can_cast(player_idx, card):
            return False

        # Pay mana cost (unless free cast)
        evoked = False
        dashed = False
        if not free_cast:
            untapped = len(player.untapped_lands) + player.mana_pool.total() + player._tron_mana_bonus()

            # Decide whether to use Dash (e.g., Ragavan)
            # Dash strategy: use Dash when...
            #   1) We can't afford the normal cost but can afford Dash
            #   2) Opponent has removal-heavy hand (we want to protect Ragavan)
            #   3) We want haste for an immediate attack
            # Don't Dash when...
            #   1) We want a permanent body and opponent has few threats
            #   2) We're low on mana and Dash costs more than normal
            if template.dash_cost is not None:
                can_normal = untapped >= template.mana_cost.cmc
                can_dash = untapped >= template.dash_cost

                if not can_dash and not can_normal:
                    return False

                dashed = game.callbacks.should_dash(game, player_idx, card, can_normal, can_dash)

            # Check if we should cast via Escape (from graveyard)
            escaped = False
            if card.zone == "graveyard" and template.escape_cost is not None:
                # Exile other cards from graveyard as additional cost
                exile_targets = [c for c in player.graveyard if c != card]
                if len(exile_targets) >= template.escape_exile_count:
                    # Exile the least valuable cards
                    exile_targets.sort(key=lambda c: c.template.cmc)
                    for i in range(template.escape_exile_count):
                        ex = exile_targets[i]
                        player.graveyard.remove(ex)
                        ex.zone = "exile"
                        player.exile.append(ex)
                    escaped = True
                    game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                                   f"Escape {card.name} (exile {template.escape_exile_count} cards)")
                else:
                    return False

            # Check if we should evoke instead of paying mana
            # Unified board evaluation: evoke when the body isn't worth waiting for
            should_evoke = (
                not dashed and not escaped
                and template.evoke_cost is not None
                and untapped < template.mana_cost.cmc
                and game.callbacks.should_evoke(game, player_idx, card)
            )
            # Target validation: don't evoke if the card needs a target and none exists
            if should_evoke:
                from decks.card_knowledge_loader import requires_target as _requires_target
                oracle_lower = (template.oracle_text or "").lower()
                needs_target = (
                    _requires_target(template.name)
                    or ('target creature' in oracle_lower)
                    or ('creature spell' in oracle_lower and 'removal' not in (template.tags or set()))
                )
                if needs_target:
                    opp_idx = 1 - player_idx
                    if not game.players[opp_idx].creatures:
                        should_evoke = False  # No targets, skip evoke
            if should_evoke:
                # Evoke: exile a card from hand that shares a color
                exile_candidates = [
                    c for c in player.hand
                    if c != card 
                    and not c.template.is_land  # Lands are colorless, can't be exiled for evoke
                    and c.template.color_identity & template.color_identity
                ]
                if exile_candidates:
                    # Generic evoke exile scoring — no hardcoded card names.
                    # Uses tag-based heuristics (combo pieces > threats > filler).
                    # Reanimate decks: big creatures are irreplaceable combo targets
                    deck_has_reanimate = any(
                        'reanimate' in (h.template.tags or set())
                        for h in player.hand
                    ) or any(
                        'reanimate' in (h.template.tags or set())
                        for h in player.graveyard
                    )
                    def exile_priority(c):
                        """Lower score = more willing to exile this card."""
                        score = c.template.cmc or 0  # prefer exiling cheap cards
                        tags = c.template.tags or set()
                        # Planeswalkers are sticky card-advantage engines —
                        # never pitch them to evoke. Observed: 4c Omnath was
                        # pitching Wrenn and Six to Endurance.
                        if CardType.PLANESWALKER in c.template.card_types:
                            score += 50
                        # Tag-based protection
                        if any(t in tags for t in ('combo', 'finisher')):
                            score += 50  # never exile combo pieces
                        if Keyword.STORM in c.template.keywords:
                            score += 50
                        if Keyword.CASCADE in c.template.keywords:
                            score += 40  # cascade spells are critical
                        # Reanimate targets: big creatures in a reanimate deck
                        if (deck_has_reanimate and c.template.is_creature
                                and (c.template.power or 0) >= 5):
                            score += 50  # irreplaceable reanimate target
                        if any(t in tags for t in ('threat', 'removal', 'board_wipe')):
                            score += 10
                        if any(t in tags for t in ('ritual', 'cost_reducer', 'ramp')):
                            score += 15  # enablers are important
                        if any(t in tags for t in ('cantrip', 'cycling')):
                            score += 5  # replaceable card draw
                        # Duplicate protection: if we have 2+ copies, one is expendable
                        dupes = sum(1 for h in player.hand
                                    if h.name == c.name and h != c)
                        if dupes > 0:
                            score -= 20  # redundant copy is safe to exile
                        return score

                    exile_candidates.sort(key=exile_priority)
                    best_exile = exile_candidates[0]
                    # Don't exile if the best candidate is a critical piece
                    if exile_priority(best_exile) >= 40:
                        return False  # all candidates are too important
                    # Lethal check: allow exiling important pieces under pressure
                    if exile_priority(best_exile) >= 20:
                        opp_idx = 1 - player_idx
                        opp_power = sum(
                            (c.power or c.template.power or 0)
                            for c in game.players[opp_idx].creatures
                        )
                        if opp_power < player.life:
                            return False  # not under pressure, keep synergy piece
                    player.hand.remove(best_exile)
                    best_exile.zone = "exile"
                    player.exile.append(best_exile)
                    evoked = True
                    game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                                   f"Evoke {card.name} (exile {best_exile.name})")
                else:
                    return False

            # Delve: exile cards from graveyard to reduce generic mana cost
            delve_exiled = 0
            if template.has_delve and not evoked and not dashed and not escaped:
                colored_cost = (template.mana_cost.white + template.mana_cost.blue +
                               template.mana_cost.black + template.mana_cost.red +
                               template.mana_cost.green)
                generic_portion = max(0, template.mana_cost.cmc - colored_cost)
                exile_targets = [c for c in player.graveyard if c != card]
                delve_exiled = min(len(exile_targets), generic_portion)
                # Exile least valuable cards first
                exile_targets.sort(key=lambda c: c.template.cmc)
                delved_spells = 0
                for i in range(delve_exiled):
                    ex = exile_targets[i]
                    player.graveyard.remove(ex)
                    ex.zone = "exile"
                    player.exile.append(ex)
                    if ex.template.is_instant or ex.template.is_sorcery:
                        delved_spells += 1
                # Store count for Murktide Regent ETB (+1/+1 per delved spell)
                card._delved_spells = delved_spells
                if delve_exiled > 0:
                    game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                                   f"Delve {delve_exiled} cards for {card.name}")

            # Pay mana
            if escaped:
                # Pay escape cost instead of normal cost
                from .mana import ManaCost
                # Escape cost for Phlage: {R}{R}{W}{W} = 4 CMC (2R + 2W)
                escape_mana = ManaCost(red=2, white=2)  # Phlage-specific
                if not game.tap_lands_for_mana(player_idx, escape_mana,
                                                 card_name=template.name):
                    return False
            elif dashed:
                # Pay Dash cost instead of normal cost
                from .mana import ManaCost
                dash_mana = ManaCost(generic=template.dash_cost - 1, red=1)  # {1}{R} for Ragavan
                if not game.tap_lands_for_mana(player_idx, dash_mana,
                                                 card_name=template.name):
                    return False
            elif not evoked:
                # Force alternate cost: exile a card from hand instead of mana
                oracle_lower = (template.oracle_text or '').lower()
                force_cast = False
                if ('exile a' in oracle_lower and 'rather than pay' in oracle_lower
                        and game.active_player != player_idx):
                    import re
                    m = re.search(r'exile an? (\w+) card from your hand', oracle_lower)
                    if m:
                        color_word = m.group(1)
                        color_map = {'blue': 'U', 'green': 'G', 'red': 'R',
                                     'white': 'W', 'black': 'B'}
                        req_color = color_map.get(color_word, '')
                        if req_color:
                            from .cards import Color
                            color_enum = {'U': Color.BLUE, 'G': Color.GREEN, 'R': Color.RED,
                                          'W': Color.WHITE, 'B': Color.BLACK}.get(req_color)
                            exile_candidates = [
                                c for c in player.hand
                                if c != card and color_enum in c.template.color_identity
                            ]
                            if exile_candidates:
                                # Exile the least valuable card
                                exile_candidates.sort(key=lambda c: c.template.cmc or 0)
                                exiled = exile_candidates[0]
                                player.hand.remove(exiled)
                                exiled.zone = "exile"
                                player.exile.append(exiled)
                                force_cast = True
                                game.log.append(
                                    f"T{game.display_turn} P{player_idx+1}: "
                                    f"Pay alternate cost: exile {exiled.name} for {template.name}")

                if not force_cast:
                    # Delve: pay reduced cost if we exiled cards
                    if delve_exiled > 0:
                        from .mana import ManaCost
                        reduced_generic = max(0, template.mana_cost.generic - delve_exiled)
                        delve_cost = ManaCost(
                            white=template.mana_cost.white,
                            blue=template.mana_cost.blue,
                            black=template.mana_cost.black,
                            red=template.mana_cost.red,
                            green=template.mana_cost.green,
                            generic=reduced_generic,
                        )
                        if not game.tap_lands_for_mana(player_idx, delve_cost,
                                                         card_name=template.name):
                            return False
                    else:
                        # Phyrexian mana: pay 2 life per Phyrexian symbol instead of colored mana
                        oracle_lower = (template.oracle_text or '').lower()
                        phyrexian_count = oracle_lower.count('/p}')
                        if phyrexian_count > 0 and player.life > phyrexian_count * 2:
                            life_cost = phyrexian_count * 2
                            player.life -= life_cost
                            # Reduce the effective cost — Mutagenic Growth {G/P} becomes free
                            remaining_cmc = max(0, template.mana_cost.cmc - phyrexian_count)
                            if remaining_cmc > 0:
                                from .mana import ManaCost
                                phyrexian_cost = ManaCost(generic=remaining_cmc)
                                if not game.tap_lands_for_mana(player_idx, phyrexian_cost,
                                                                 card_name=template.name):
                                    player.life += life_cost  # refund
                                    return False
                            game.log.append(
                                f"T{game.display_turn} P{player_idx+1}: "
                                f"Pay {life_cost} life (Phyrexian mana) for {template.name}")
                        elif not game.tap_lands_for_mana(player_idx, template.mana_cost,
                                                         card_name=template.name):
                            return False

        # Remove from zone and track cast-from-graveyard for flashback exile
        cast_with_flashback = False
        if card in player.hand:
            player.hand.remove(card)
        elif card in player.graveyard:
            player.graveyard.remove(card)
            # If cast from GY via flashback (not escape), mark for exile after resolution
            if card.has_flashback and not (escaped if not free_cast else False):
                cast_with_flashback = True
                # Pay flashback additional cost (sacrifice a {subtype}).
                # can_cast already guarantees a matching land exists.
                import re as _re_fbc
                fb_oracle_c = (template.oracle_text or '').lower()
                m_fb = _re_fbc.search(
                    r'flashback\s*[—\-:]\s*sacrifice a (\w+)', fb_oracle_c)
                if m_fb:
                    needed = m_fb.group(1).strip()
                    sac = next((
                        l for l in player.lands
                        if needed in [s.lower() for s in (l.template.subtypes or [])]
                        or needed in (l.template.name or '').lower()
                    ), None)
                    if sac is not None:
                        if sac in player.battlefield:
                            player.battlefield.remove(sac)
                        sac.zone = 'graveyard'
                        player.graveyard.append(sac)
                        game.log.append(
                            f"T{game.display_turn} P{player_idx+1}: "
                            f"Flashback {template.name} — sacrifice {sac.name}")
        card.zone = "stack"
        card._cast_with_flashback = cast_with_flashback
        card._evoked = evoked  # Track for sacrifice after ETB
        card._dashed = dashed  # Track for haste + return to hand at end of turn
        card._escaped = getattr(card, '_escaped', False) or (escaped if not free_cast else False)  # Track for sacrifice-unless-escaped

        # Calculate X value for X-cost spells
        x_value = 0
        if template.x_cost_data and not free_cast and not evoked:
            x_info = template.x_cost_data
            # X = (total mana available) / multiplier
            # For XX spells, X = mana / 2; for X spells, X = mana
            available_for_x = len(player.untapped_lands) + player.mana_pool.total() + player._tron_mana_bonus()
            x_value = available_for_x // x_info["multiplier"]
            # AI chooses optimal X based on oracle text:
            oracle = (template.oracle_text or '').lower()
            if 'charge counter' in oracle and 'whenever' in oracle:
                # Hate permanent (Chalice-style): pick X to maximize NET
                # disruption = opp_count(X) − my_count(X). Counting only
                # opp's CMCs (audit F-R3-1's first pass) picks the CMC
                # with the most opp spells, even when that CMC is also
                # where our own deck lives. Azorius vs Boros at X=2
                # locks 12 Boros spells but also all 13 of Azorius's
                # own counters — net −1. The symmetric formulation
                # charges both sides and picks the CMC that costs them
                # more than it costs us.
                #
                # Our side: library + hand (what we still might cast).
                # Opp side: library only (we don't see their hand).
                opp = game.players[1 - player_idx]
                opp_cmcs = {}
                for c in opp.library:
                    if not c.template.is_land:
                        cm = c.template.cmc or 0
                        opp_cmcs[cm] = opp_cmcs.get(cm, 0) + 1
                my_cmcs = {}
                for zone in (player.library, player.hand):
                    for c in zone:
                        if c.instance_id == card.instance_id:
                            continue
                        if not c.template.is_land:
                            cm = c.template.cmc or 0
                            my_cmcs[cm] = my_cmcs.get(cm, 0) + 1
                # Candidate X values: union of both sides' CMCs, capped
                # at available mana. X=0 is always castable.
                candidate_cmcs = (set(opp_cmcs) | set(my_cmcs))
                candidates = [
                    (opp_cmcs.get(cm, 0) - my_cmcs.get(cm, 0), cm)
                    for cm in candidate_cmcs if cm <= x_value
                ]
                if candidates:
                    # max net; tiebreak by lower CMC (cheaper for us to
                    # float mana around a low-X lock).
                    best_net, best_cmc = max(candidates,
                                              key=lambda nc: (nc[0], -nc[1]))
                    x_value = best_cmc
                elif x_value >= 1:
                    x_value = 1  # fallback when no data
            elif ('destroy each' in oracle
                  and 'mana value less than or equal to' in oracle):
                # Scaling board-wipe-by-X (Wrath of the Skies pattern):
                # "Destroy each artifact, creature, and enchantment with
                # mana value less than or equal to the amount of {E} paid
                # this way." Pick X to maximize (opp_kills − my_kills)
                # over opp permanents at CMC ≤ X. Without this, X
                # defaulted to max available mana — firing X=0 on T2
                # with zero available_for_x wipes only tokens and wastes
                # the card (audit F-Wrath-X).
                opp = game.players[1 - player_idx]
                me_bf = game.players[player_idx].battlefield
                def _is_wipe_target(c):
                    return (CardType.CREATURE in c.template.card_types
                            or CardType.ARTIFACT in c.template.card_types
                            or CardType.ENCHANTMENT in c.template.card_types)
                opp_kills_by_x = {}
                my_kills_by_x = {}
                for c in opp.battlefield:
                    if _is_wipe_target(c):
                        cm = c.template.cmc or 0
                        opp_kills_by_x[cm] = opp_kills_by_x.get(cm, 0) + 1
                for c in me_bf:
                    if _is_wipe_target(c):
                        cm = c.template.cmc or 0
                        my_kills_by_x[cm] = my_kills_by_x.get(cm, 0) + 1
                # For each candidate X ≤ available, compute cumulative kills.
                best_score = -1
                best_x = 0
                for X in range(0, int(x_value) + 1):
                    opp_hit = sum(n for cm, n in opp_kills_by_x.items()
                                   if cm <= X)
                    my_hit = sum(n for cm, n in my_kills_by_x.items()
                                  if cm <= X)
                    score = opp_hit - my_hit
                    # Strict improvement: only prefer larger X if it
                    # adds net kills.
                    if score > best_score:
                        best_score = score
                        best_x = X
                x_value = best_x
            # +1/+1 counter creatures: use max mana (Ballista-style)
            # (default x_value is already max)
            # Pay the actual X cost
            actual_cost = x_value * x_info["multiplier"]
            remaining = actual_cost
            # Pay from mana pool first
            from_pool = min(player.mana_pool.total(), remaining)
            if from_pool > 0:
                to_remove = from_pool
                for attr in ["colorless", "green", "red", "black", "blue", "white"]:
                    avail = getattr(player.mana_pool, attr)
                    take = min(avail, to_remove)
                    if take > 0:
                        setattr(player.mana_pool, attr, avail - take)
                        to_remove -= take
                    if to_remove <= 0:
                        break
                remaining -= from_pool
            # Pay rest from lands. For Converge-style spells (oracle references
            # "colors of mana spent"), greedily pick lands that contribute a
            # NEW color first, maximising distinct colors paid → maximising X.
            # For non-Converge X-spells this reduces to arbitrary selection
            # (same as the old behavior because set-difference is 0-or-more).
            xpay_colors = set(getattr(game, '_last_colors_spent', set()))
            oracle = (template.oracle_text or '').lower()
            is_converge = 'converge' in oracle or 'colors of mana spent' in oracle
            lands_pool = list(player.untapped_lands)
            while remaining > 0 and lands_pool:
                if is_converge:
                    # MRV-style: prefer lands that produce a color we haven't
                    # spent yet.  Routes through `_effective_produces_mana` so
                    # Leyline / dynamic mana abilities (E1: Mox Opal
                    # metalcraft) feed Converge correctly.
                    lands_pool.sort(
                        key=lambda l: -len(
                            set(game._effective_produces_mana(player_idx, l) or []) - xpay_colors
                        )
                    )
                land = lands_pool.pop(0)
                land.tapped = True
                remaining -= 1
                produced = list(game._effective_produces_mana(player_idx, land) or [])
                if is_converge:
                    # Pick a new color if possible, else any produced color
                    new_cols = [c for c in produced if c not in xpay_colors]
                    pick = new_cols[0] if new_cols else (produced[0] if produced else 'C')
                else:
                    pick = produced[0] if produced else 'C'
                if pick and pick != 'C':
                    xpay_colors.add(pick)
            # Surface the updated color set for the stack item / Converge resolvers
            game._last_colors_spent = xpay_colors

        stack_item = StackItem(
            item_type=StackItemType.SPELL,
            source=card,
            controller=player_idx,
            targets=targets or [],
            x_value=x_value,
            # Snapshot the colors actually spent for Converge ("number of
            # colors of mana spent to cast this spell"). Populated by the
            # most recent tap_lands_for_mana() call; empty for free casts.
            colors_spent=set(getattr(game, '_last_colors_spent', set())),
        )

        # ── Splice onto Arcane: when casting an Arcane spell, splice cards
        # from hand that have splice_cost. Pay splice cost, add their effects,
        # spliced card stays in hand. ──
        if 'Arcane' in template.subtypes and not free_cast:
            from .oracle_resolver import count_cost_reducers
            for sc in list(player.hand):
                if sc.instance_id == card.instance_id:
                    continue
                splice = sc.template.splice_cost
                if not splice:
                    continue
                # splice is total CMC (int) — apply cost reduction
                reduction = count_cost_reducers(game, player_idx, sc.template)
                reduction += player.temp_cost_reduction
                effective_splice = max(0, splice - reduction)
                available_mana = player.mana_pool.total() + len(player.untapped_lands)
                if available_mana >= effective_splice:
                    # Pay splice cost from mana pool/lands
                    from .mana import ManaCost as MC
                    # Splice for rituals: {1}{R} = generic + 1 red
                    red_portion = min(1, effective_splice)
                    generic_portion = max(0, effective_splice - red_portion)
                    splice_mc = MC(generic=generic_portion, red=red_portion)
                    if not game.tap_lands_for_mana(player_idx, splice_mc,
                                                   sc.template.name):
                        continue
                    stack_item.spliced.append(sc.template)
                    game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                                   f"  Splice {sc.name} onto {card.name}")

        game.stack.push(stack_item)
        player.spells_cast_this_turn += 1
        if CardType.ARTIFACT not in template.card_types:
            player.nonartifact_spells_cast_this_turn += 1
        game._global_storm_count += 1

        # ── Chalice of the Void check ──
        # If opponent controls Chalice with charge counters == spell's CMC, counter it
        opp_idx = 1 - player_idx
        opp = game.players[opp_idx]
        # Generic "counter spell with mana value equal to charge counters" check
        for perm in opp.battlefield:
            perm_oracle = (perm.template.oracle_text or '').lower()
            if 'charge counter' in perm_oracle and 'mana value' in perm_oracle and 'counter' in perm_oracle:
                charge = perm.other_counters.get("charge", 0)
                if charge == template.cmc and template.cmc >= 0:
                    game.stack.pop()
                    card.zone = "graveyard"
                    player.graveyard.append(card)
                    game.log.append(
                        f"T{game.display_turn} P{opp_idx+1}: "
                        f"{perm.name} (X={charge}) counters {card.name}")
                    return True

        dash_label = " (Dash)" if dashed else ""
        x_label = f" (X={x_value})" if x_value > 0 else ""
        cost_parts = []
        mc = card.template.mana_cost
        if x_value > 0:
            x_info = template.x_cost_data or {}
            actual_paid = x_value * x_info.get("multiplier", 1)
            cost_parts.append(str(actual_paid))
        elif mc.generic > 0:
            cost_parts.append(str(mc.generic))
        cost_parts.extend('W' * mc.white + 'U' * mc.blue + 'B' * mc.black + 'R' * mc.red + 'G' * mc.green)
        cost_str = ''.join(cost_parts) if cost_parts else '0'
        game.log.append(f"T{game.display_turn} P{player_idx+1}: Cast {card.name} ({cost_str}){dash_label}{x_label}")

        # ── Prowess and prowess-like triggers (generic from oracle) ──
        if not template.is_creature:
            for creature in player.creatures:
                # Standard prowess keyword
                if Keyword.PROWESS in creature.keywords:
                    creature.temp_power_mod += 1
                    creature.temp_toughness_mod += 1
                    continue
                # Oracle-based prowess variants:
                # "Whenever you cast a noncreature spell, this creature gets +N/+M"
                c_oracle = (creature.template.oracle_text or '').lower()
                if 'noncreature spell' not in c_oracle and 'instant or sorcery' not in c_oracle:
                    continue
                import re
                pump = re.search(r'gets?\s+\+(\d+)/\+(\d+)', c_oracle)
                if pump:
                    creature.temp_power_mod += int(pump.group(1))
                    creature.temp_toughness_mod += int(pump.group(2))
                elif re.search(r'gets?\s+\+(\d+)/\+0', c_oracle):
                    m = re.search(r'gets?\s+\+(\d+)/\+0', c_oracle)
                    creature.temp_power_mod += int(m.group(1))
                # Delirium — check actual GY card types via _has_delirium()
                # _dynamic_base_power() already scales to 3 with delirium; we also
                # need to grant FLYING as a keyword so combat logic sees it.
                if 'delirium' in c_oracle and hasattr(creature, '_has_delirium'):
                    if creature._has_delirium():
                        if Keyword.FLYING not in creature.keywords:
                            creature.keywords.add(Keyword.FLYING)

                # Surveil 1: always bin the top card to GY (AI choice: maximise delirium)
                if 'surveil' in c_oracle and player.library:
                    top = player.library.pop(0)
                    top.zone = 'graveyard'
                    player.graveyard.append(top)
                    game.log.append(
                        f"T{game.display_turn} P{player_idx+1}: "
                        f"{creature.name} surveil 1 → {top.name} to GY")

        # Generic oracle-text-based spell-cast triggers
        from .oracle_resolver import resolve_spell_cast_trigger
        resolve_spell_cast_trigger(game, player_idx, card)

        return True


