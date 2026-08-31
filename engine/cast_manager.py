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


def pick_wipe_x_value(game: "GameState", player_idx: int,
                      x_budget: int) -> "tuple[int, float, int]":
    """Engine-side X picker for "destroy each artifact/creature/
    enchantment with mana value ≤ X" spells (Wrath of the Skies pattern).

    Mirrors the logic in `CastManager.cast_spell` lines 987-1035: scores
    each candidate X in [0, x_budget] by `permanent_threat`-weighted
    opp value minus my collateral, then returns the X that maximises
    the net value.

    This function exists as a module-level helper so the AI scoring
    layer (`ai/ev_player.py::_score_spell` X-cost board-wipe gate) can
    consult the SAME picker that the engine uses at resolution time —
    eliminating the AI/engine "what X would I pick / what X did I pick"
    inconsistency flagged in `docs/diagnostics/2026-05-16_wrath_enumeration_gate.md`.

    Args:
        game: GameState — needed to evaluate `permanent_threat`.
        player_idx: int — the controller of the wipe.
        x_budget: int — max X the controller can afford.

    Returns:
        (best_x, best_score, kill_count): the chosen X value, the net
        opp-minus-my permanent_threat value at that X, and the count of
        opponent permanents (creatures + artifacts + enchantments)
        destroyed at that X. Returns (0, 0.0, 0) for non-positive
        x_budget with no zero-MV opp targets.
    """
    # Imported lazily because permanent_threat lives in the AI layer
    # and the engine package must not eagerly depend on it. The
    # function call shape mirrors the inline picker in `cast_spell`.
    from ai.permanent_threat import permanent_threat

    opp = game.players[1 - player_idx]
    me_bf = game.players[player_idx].battlefield

    def _is_wipe_target(c) -> bool:
        return (CardType.CREATURE in c.template.card_types
                or CardType.ARTIFACT in c.template.card_types
                or CardType.ENCHANTMENT in c.template.card_types)

    opp_value_by_cmc: "dict[int, float]" = {}
    opp_count_by_cmc: "dict[int, int]" = {}
    my_value_by_cmc: "dict[int, float]" = {}
    for c in opp.battlefield:
        if _is_wipe_target(c):
            cm = c.template.cmc or 0
            v = permanent_threat(c, opp, game)
            opp_value_by_cmc[cm] = opp_value_by_cmc.get(cm, 0.0) + v
            opp_count_by_cmc[cm] = opp_count_by_cmc.get(cm, 0) + 1
    for c in me_bf:
        if _is_wipe_target(c):
            cm = c.template.cmc or 0
            v = permanent_threat(c, game.players[player_idx], game)
            my_value_by_cmc[cm] = my_value_by_cmc.get(cm, 0.0) + v

    best_score = -1.0  # sentinel: every real X has score ≥ 0 (an empty
    # board scores 0 at X=0; -1.0 ensures the X=0 candidate beats it).
    best_x = 0
    best_kill_count = 0
    for X in range(0, max(0, int(x_budget)) + 1):
        opp_hit = sum(v for cm, v in opp_value_by_cmc.items() if cm <= X)
        my_hit = sum(v for cm, v in my_value_by_cmc.items() if cm <= X)
        score = opp_hit - my_hit
        if score > best_score:
            best_score = score
            best_x = X
            best_kill_count = sum(
                n for cm, n in opp_count_by_cmc.items() if cm <= X
            )

    return best_x, max(0.0, best_score), best_kill_count


def creature_tutor_x_net_value(x: int, delivered_cmc: int) -> int:
    """Net cast value, in mana units, of an X-cost creature tutor cast at
    X=``x`` when the best deliverable target at that X costs
    ``delivered_cmc``.

    Both terms are mana: the delivered creature's headline size proxy is
    its mana value (the same proxy the resolvers rank fetch candidates
    by), and every point of X above it is spent mana buying nothing —
    charged 1:1 because it is literally the same resource.

    This is the value function `pick_creature_tutor_x_value` maximises;
    its argmax is the cheapest X that still delivers the best fetchable
    target (X above the target's cost only subtracts).
    """
    return delivered_cmc - (x - delivered_cmc)


def pick_creature_tutor_x_value(
        game: "GameState", player_idx: int, x_budget: int,
        template) -> "tuple[int, object, object]":
    """Engine-side X picker for "search your library for a creature card
    with mana value X or less, put it onto the battlefield" spells (the
    Green Sun's Zenith shape, `CardTemplate.x_creature_tutor_data`).

    Mirrors `pick_wipe_x_value` above: a module-level helper so the AI
    scoring layer (`ai/ev_player.py::_gate_x_tutor_payoff`) consults the
    SAME picker the engine uses at cast time — the cast EV is conditioned
    on exactly what the chosen X will deliver, and the chosen X is the
    cheapest one that delivers it (never X=4 for a 1-drop).

    Candidate ranking within an X budget matches the resolver: highest
    mana value first, P/T tie-break (several premium targets are 0/0 with
    a characteristic-defining ability).

    Args:
        game: GameState.
        player_idx: controller of the tutor.
        x_budget: max X the controller can afford (after base cost).
        template: the tutor's CardTemplate (color constraint + min_x).

    Returns:
        (best_x, best_target, top_candidate):
        - best_x: the chosen X (0 when nothing is fetchable).
        - best_target: the CardInstance the resolver will deliver at
          best_x, or None when no candidate fits any affordable X.
        - top_candidate: the best fetchable candidate ignoring the
          budget — the library's payoff ceiling, which the AI's
          hold/patience gate compares against best_target.
    """
    from .activated_effects import (default_tutor_rank,
                                    eligible_tutor_targets,
                                    tutor_search_pool)

    spec = getattr(template, 'x_creature_tutor_data', None)
    if not spec:
        return 0, None, None  # not this shape — nothing to price
    player = game.players[player_idx]
    _rank = default_tutor_rank

    # Same predicate and same zone set the resolver searches — the X the
    # AI prices is the X the engine charges for the card it will deliver.
    candidates = eligible_tutor_targets(
        tutor_search_pool(player, spec), spec, x_value=None)
    if not candidates:
        return 0, None, None
    top_candidate = max(candidates, key=_rank)

    min_x = int((template.x_cost_data or {}).get('min_x', 0) or 0)
    best_x = min_x
    best_net: "float | None" = None
    best_target = None
    for X in range(min_x, max(min_x, int(x_budget)) + 1):
        affordable = [c for c in candidates if (c.template.cmc or 0) <= X]
        if not affordable:
            continue
        target = max(affordable, key=_rank)
        net = creature_tutor_x_net_value(X, target.template.cmc or 0)
        # Strict > keeps the LOWEST X among equal nets (cheapest first).
        if best_net is None or net > best_net:
            best_net = net
            best_x = X
            best_target = target
    return best_x, best_target, top_candidate


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

        # CR 111.2 — a token isn't a card and can never be cast,
        # regardless of what zone a stale instance sits in.
        if getattr(card, 'is_token', False):
            return False

        # Turn-scoped cast-lock ("[target player] can't cast spells
        # this turn" — the silence class).  The effect layer sets
        # `silenced_this_turn` from the oracle clause on resolution;
        # the cast gate enforces it here for the rest of the turn.
        # Applies to every cast route (hand, flashback, escape).
        if getattr(player, 'silenced_this_turn', False):
            return False

        # Warp: previously warped permanents may be cast again from exile.
        if card.zone == "exile" and getattr(card, '_warped', False):
            has_artifact = any(
                CardType.ARTIFACT in c.template.card_types
                for c in player.battlefield
            )
            if (template.warp_cost is not None
                    and has_artifact):
                total_mana = (player.untapped_mana_capacity()
                              + player.mana_pool.total()
                              + player._tron_mana_bonus())
                if (total_mana >= template.warp_cost.cmc
                        and CastManager._can_pay_colored_pips(
                            game, player_idx, player.untapped_lands,
                            template.warp_cost)):
                    return True
            return False  # in exile but not a re-castable warp card

        if card.zone != "hand" and card.zone != "graveyard":
            return False

        # Grafdigger's Cage (and functional reprints): "Players can't
        # cast spells from graveyards or libraries." Oracle-driven gate;
        # applies to flashback/escape and any future graveyard-cast
        # route. Hand-cast is untouched.
        if card.zone in ("graveyard", "library"):
            if game._gy_library_cast_hate_source() is not None:
                return False

        # Graveyard casting: Flashback or Escape
        if card.zone == "graveyard":
            # Escape: can cast from graveyard if we have enough mana AND
            # enough other cards in graveyard to exile
            if template.escape_cost is not None:
                other_gy_cards = sum(1 for c in player.graveyard if c != card)
                if other_gy_cards < template.escape_exile_count:
                    return False  # Not enough cards to exile
                # Check mana for escape cost — quantity then colour (CR 702.19)
                untapped_lands = player.untapped_lands
                total_mana = (player.untapped_mana_capacity() + player.mana_pool.total()
                              + player._tron_mana_bonus())
                if total_mana < template.escape_cost.cmc:
                    return False
                return CastManager._can_pay_colored_pips(
                    game, player_idx, untapped_lands, template.escape_cost)
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

        # R4: sorcery-speed-lockout static abilities (Teferi, Time
        # Raveler; Grand Abolisher; Conqueror's Flail; ...) collapse
        # the instant/flash exemption for opponents who are in the
        # per-game lockout registry. Registry is rebuilt on demand
        # from ``Tag.SORCERY_SPEED_LOCKOUT``-tagged permanents — no
        # card-name branches, no oracle-text parse at runtime.
        sorcery_locked = player_idx in game._sorcery_speed_lockout_set()

        if (template.is_instant or template.has_flash) and not sorcery_locked:
            pass
        elif template.is_creature or template.is_sorcery or \
                CardType.ENCHANTMENT in template.card_types or \
                CardType.ARTIFACT in template.card_types or \
                CardType.PLANESWALKER in template.card_types or \
                sorcery_locked:
            if not (is_main_phase and is_active and game.stack.is_empty):
                return False

        # Target validation (CR 601.2c): a spell with a required target
        # cannot be cast if no legal target exists. The unified target
        # solver replaces five scattered ``oracle_l`` substring checks
        # (graveyard-target, "target creature", "target creature you
        # control", _battlefield_legal_targets, evoke-target) with one
        # parser → legality-query pipeline. See
        # docs/proposals/2026-05-02_unified_target_solver.md.
        #
        # The exclude=card argument enforces CR 601.2c — a spell can
        # never target itself in its source zone. Relevant for graveyard-
        # cast spells (Persist) where the card is still in the
        # graveyard list at can_cast time.
        if template.is_instant or template.is_sorcery:
            from .target_solver import (has_legal_target_for_spell,
                                        parse as _parse_targets)
            requirements = _parse_targets(template.oracle_text or "")
            if not has_legal_target_for_spell(
                    game, player_idx, requirements, exclude=card):
                return False

        # Check mana (pool + untapped lands + non-land mana sources + Tron bonus)
        untapped_lands = player.untapped_mana_sources
        total_mana = (player.untapped_mana_capacity() + player.mana_pool.total()
                      + player._tron_mana_bonus())

        # X-cost spells: require minimum mana to cast meaningfully
        if template.x_cost_data:
            x_info = template.x_cost_data
            min_mana = x_info["multiplier"] * max(x_info["min_x"], 1)
            if total_mana < min_mana:
                return False

        # Cost reductions
        # For graveyard casting via native flashback the player pays the printed
        # flashback cost, NOT the regular mana cost (CR 702.33a).  Cards granted
        # flashback by Past in Flames / Snapcaster Mage have flashback_cost=None
        # and continue to pay their regular mana_cost (oracle: "flashback cost is
        # equal to its mana cost").  Lava Dart's flashback cost is sacrifice-only
        # (flashback_cost=None); no mana is needed and the sacrifice is already
        # checked above in the GY-casting block.
        _using_flashback_cost = (
            card.zone == "graveyard"
            and card.has_flashback
            and template.flashback_cost is not None
        )
        effective_cmc = (template.flashback_cost.cmc
                         if _using_flashback_cost
                         else template.mana_cost.cmc)
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
                    needs_target = (
                        _req_target(template.name)
                        or getattr(template, 'requires_creature_target', False)
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

        # Spectacle alternative cost (CR 702.131): may cast for spectacle cost
        # if an opponent lost life this turn — quantity then colour check.
        if template.spectacle_cost is not None:
            opp_lost_life = any(
                game.players[i].life_lost_this_turn > 0
                for i in range(len(game.players))
                if i != player_idx
            )
            if (opp_lost_life
                    and total_mana >= template.spectacle_cost.cmc
                    and CastManager._can_pay_colored_pips(
                        game, player_idx, untapped_lands, template.spectacle_cost)):
                return True

        # Dash alternative cost — verify quantity then colour (CR 702.32)
        if template.dash_cost is not None:
            if (total_mana >= template.dash_cost.cmc
                    and CastManager._can_pay_colored_pips(
                        game, player_idx, untapped_lands, template.dash_cost)):
                return True

        # Warp alternative cost — check payability against parsed warp_cost,
        # not just "total_mana >= 1" (the old check caused infinite loops when
        # the warp cost could be quoted as castable but the normal-cost payment
        # path failed for color reasons inside cast_spell).
        oracle = (template.oracle_text or "").lower()
        if template.warp_cost is not None:
            has_artifact = any(
                CardType.ARTIFACT in c.template.card_types
                for c in player.battlefield
            )
            if (has_artifact and total_mana >= template.warp_cost.cmc
                    and CastManager._can_pay_colored_pips(
                        game, player_idx, player.untapped_lands,
                        template.warp_cost)):
                return True

        # Improvise: tap artifacts to pay generic. Keyword.IMPROVISE is
        # populated at DB load via KEYWORD_MAP — no runtime oracle
        # inspection.  Improvise pays GENERIC only, so the colored portion
        # of the cost is a floor the artifact taps cannot reduce.
        if Keyword.IMPROVISE in template.keywords:
            untapped_artifacts = sum(
                1 for c in player.battlefield
                if CardType.ARTIFACT in c.template.card_types
                and not c.template.is_land
                and not getattr(c, 'tapped', False)
                and c is not card
            )
            colored_floor = (template.mana_cost.white + template.mana_cost.blue
                             + template.mana_cost.black + template.mana_cost.red
                             + template.mana_cost.green)
            improvise_cmc = max(colored_floor,
                                effective_cmc - untapped_artifacts)
            if total_mana >= improvise_cmc:
                effective_cmc = improvise_cmc
                # Improvise reduces generic only (CR 702.125a); coloured pips
                # still require real coloured sources — fall through to MRV.

        # Force alternate cost: "exile a [color] card from your hand
        # rather than pay this spell's mana cost" — only on opp's turn
        oracle_lower = (template.oracle_text or '').lower()
        if getattr(template, 'has_alternate_exile_cost', False):
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
        # Use flashback cost when casting a native-flashback card from GY.
        cost = (template.flashback_cost
                if _using_flashback_cost
                else template.mana_cost)
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
        # E1: one source per mana UNIT — a multi-mana land contributes
        # one entry per unit (fixed karoo units stay single-color).
        from .mana_payment import ManaPayment as _MP
        sources = []
        for land in untapped_lands:
            for options in _MP.land_mana_units(game, player_idx, land):
                sources.append(set(options))
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

    # ─── Shared colour-verification helper ────────────────────────────

    @staticmethod
    def _can_pay_colored_pips(game: "GameState", player_idx: int,
                              untapped_lands, cost: "ManaCost") -> bool:
        """Return True iff lands + mana pool can satisfy cost's coloured pips.

        Used by alternative-cost mechanics (dash, escape) that replace the
        normal mana cost with a different ManaCost object.  Applies the same
        MRV (minimum-remaining-values) greedy algorithm as the main can_cast
        colour check.  Does NOT verify total quantity — callers confirm
        ``total_mana >= cost.cmc`` first.
        """
        from .mana_payment import ManaPayment as _MP
        player = game.players[player_idx]
        sources = []
        for land in untapped_lands:
            for options in _MP.land_mana_units(game, player_idx, land):
                sources.append(set(options))
        for color in ["W", "U", "B", "R", "G", "C"]:
            pool_amount = player.mana_pool.get(color)
            for _ in range(pool_amount):
                sources.append({color})

        color_needs = []
        for color, needed in [("W", cost.white), ("U", cost.blue),
                              ("B", cost.black), ("R", cost.red),
                              ("G", cost.green), ("C", cost.colorless)]:
            for _ in range(needed):
                color_needs.append(color)

        if not color_needs:
            return True  # No coloured pips — quantity check already done

        used = [False] * len(sources)
        remaining_needs = list(color_needs)
        while remaining_needs:
            remaining_needs.sort(
                key=lambda c: sum(
                    1 for i, s in enumerate(sources)
                    if c in s and not used[i])
            )
            c = remaining_needs.pop(0)
            best_idx = -1
            best_flex = 999
            for i, s in enumerate(sources):
                if not used[i] and c in s:
                    if len(s) < best_flex:
                        best_flex = len(s)
                        best_idx = i
            if best_idx == -1:
                return False
            used[best_idx] = True

        return True

    # ─── Suspend (LE-E2) ─────────────────────────────────────────────
    # Parse "Suspend N—{cost}" from oracle text. Returns (N, ManaCost) or
    # None if the card has no suspend clause. Kept local to the cast path
    # because no other subsystem needs these primitives.
    @staticmethod
    def _parse_suspend_clause(template) -> "tuple | None":
        """Parse 'Suspend N—{cost}' from oracle text.

        Returns (counter_count, ManaCost) on match, else None. Uses the
        oracle text as the single source of truth; no card-name tables.
        """
        import re as _re_s
        from .mana import ManaCost
        oracle = (template.oracle_text or "")
        # Accept em-dash, en-dash, or hyphen after the N.
        m = _re_s.search(r"Suspend\s+(\d+)\s*[—\-–]\s*([^\n(.,]+)", oracle)
        if not m:
            return None
        n = int(m.group(1))
        cost_str = m.group(2).strip()
        # Parse the mana portion using the existing MTGJSON parser.
        from .card_database import parse_mana_cost_mtgjson
        try:
            cost = parse_mana_cost_mtgjson(cost_str)
        except Exception:
            return None
        return n, cost

    @staticmethod
    def can_suspend(game: "GameState", player_idx: int,
                    card: "CardInstance") -> bool:
        """Legality check for paying a card's suspend cost from hand.

        Requirements: card is in hand, has SUSPEND keyword, has a parseable
        Suspend clause, and the controller can pay the suspend mana cost
        from untapped lands + mana pool.
        """
        if card.zone != "hand":
            return False
        template = card.template
        if Keyword.SUSPEND not in template.keywords:
            return False
        parsed = CastManager._parse_suspend_clause(template)
        if parsed is None:
            return False
        _, cost = parsed

        player = game.players[player_idx]
        untapped_lands = player.untapped_lands
        total_mana = (player.untapped_mana_capacity() + player.mana_pool.total()
                      + player._tron_mana_bonus())
        if total_mana < cost.cmc:
            return False

        # Colored-mana feasibility via the same MRV solver the normal
        # cast path uses. Keeps the suspend gate honest for cards like
        # Ancestral Vision ({U}).
        color_needs = []
        for color, needed in [("W", cost.white), ("U", cost.blue),
                              ("B", cost.black), ("R", cost.red),
                              ("G", cost.green), ("C", cost.colorless)]:
            for _ in range(needed):
                color_needs.append(color)

        # E1: one source per mana UNIT — a multi-mana land contributes
        # one entry per unit (fixed karoo units stay single-color).
        from .mana_payment import ManaPayment as _MP
        sources = []
        for land in untapped_lands:
            for options in _MP.land_mana_units(game, player_idx, land):
                sources.append(set(options))
        for color in ["W", "U", "B", "R", "G", "C"]:
            pool_amount = player.mana_pool.get(color)
            for _ in range(pool_amount):
                sources.append({color})

        if len(sources) < cost.cmc:
            return False

        used = [False] * len(sources)
        remaining_needs = list(color_needs)
        while remaining_needs:
            remaining_needs.sort(
                key=lambda c: sum(
                    1 for i, s in enumerate(sources)
                    if c in s and not used[i])
            )
            c = remaining_needs.pop(0)
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

        remaining_sources = sum(1 for u in used if not u)
        generic_needed = cost.cmc - len(color_needs)
        return remaining_sources >= generic_needed

    @staticmethod
    def suspend_card(game: "GameState", player_idx: int,
                     card: "CardInstance") -> bool:
        """Pay the suspend cost, exile the card with time counters.

        Returns True on success. On failure (costs un-payable, bad state,
        no suspend clause) returns False without mutating state.
        """
        if not CastManager.can_suspend(game, player_idx, card):
            return False
        parsed = CastManager._parse_suspend_clause(card.template)
        n, cost = parsed  # parsed is guaranteed non-None by can_suspend

        player = game.players[player_idx]
        # Pay the suspend mana cost by tapping lands / draining pool.
        if not game.tap_lands_for_mana(player_idx, cost,
                                        card_name=card.template.name):
            return False

        # Move from hand to exile; mark as suspended with N counters.
        if card in player.hand:
            player.hand.remove(card)
        card.zone = "exile"
        card.suspended = True
        card.suspend_counters = n
        player.exile.append(card)
        game.log.append(
            f"T{game.display_turn} P{player_idx+1}: Suspend {card.template.name} "
            f"({n} time counter{'s' if n != 1 else ''})")
        return True

    @staticmethod
    def tick_suspend_upkeep(game: "GameState", player_idx: int) -> None:
        """Remove one time counter from each suspended card controlled by
        player_idx. When the last counter is removed, cast the spell for
        free via the standard cast_spell free-cast path so existing
        cascade / trigger wiring applies uniformly.
        """
        player = game.players[player_idx]
        # Copy the list — suspend resolution mutates exile.
        to_tick = [c for c in list(player.exile)
                   if getattr(c, "suspended", False)
                   and c.controller == player_idx]
        for card in to_tick:
            card.suspend_counters = max(0, card.suspend_counters - 1)
            if card.suspend_counters > 0:
                continue
            # Last counter removed: cast for free.
            card.suspended = False
            if card in player.exile:
                player.exile.remove(card)
            # Route through the standard free-cast path so cascade /
            # storm / ETB triggers fire via the normal resolver.
            card.zone = "hand"
            player.hand.append(card)
            card._free_cast_opportunity = True
            ok = game.cast_spell(player_idx, card, free_cast=True)
            if not ok:
                # Graceful fallback: if the free cast can't proceed (no
                # legal targets, etc.), leave the card in graveyard.
                if card in player.hand:
                    player.hand.remove(card)
                card.zone = "graveyard"
                player.graveyard.append(card)
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Suspend {card.template.name} fizzles (no cast)")
                continue
            # Resolve the stack immediately so the suspend cast fully
            # completes before the rest of the upkeep processes.
            while not game.stack.is_empty:
                game.resolve_stack()
                game.check_state_based_actions()
                if game.game_over:
                    return

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

        # CR 111.2 — tokens are never castable, free_cast included.
        if getattr(card, 'is_token', False):
            return False

        if not free_cast and not game.can_cast(player_idx, card):
            return False

        # Pay mana cost (unless free cast)
        evoked = False
        dashed = False
        warped = False
        spectacled = False
        if not free_cast:
            untapped = player.untapped_mana_capacity() + player.mana_pool.total() + player._tron_mana_bonus()

            # Warp: cast from hand for cheaper alternative cost; creature exiles
            # at beginning of the next end step.  Use Warp when we have an
            # artifact on the battlefield AND cannot afford the normal cost
            # (or prefer the temporary body).  The warp_cost was parsed at
            # load time, so no oracle-substring re-parsing here.
            if (template.warp_cost is not None
                    and card.zone == "hand"
                    and not dashed):
                has_artifact = any(
                    CardType.ARTIFACT in c.template.card_types
                    for c in player.battlefield
                )
                can_normal = untapped >= template.mana_cost.cmc
                can_warp = has_artifact and untapped >= template.warp_cost.cmc
                if not can_warp and not can_normal:
                    return False
                # Prefer Warp only when normal cost is unaffordable
                if can_warp and not can_normal:
                    warped = True

            # Spectacle (CR 702.131): use spectacle cost when an opponent lost
            # life this turn and the player cannot afford the normal cost —
            # or when spectacle is strictly cheaper than the normal cost.
            if template.spectacle_cost is not None and not warped:
                opp_lost_life_s = any(
                    game.players[i].life_lost_this_turn > 0
                    for i in range(len(game.players))
                    if i != player_idx
                )
                can_normal_sp = untapped >= template.mana_cost.cmc
                can_spectacle = (
                    opp_lost_life_s
                    and untapped >= template.spectacle_cost.cmc
                    and CastManager._can_pay_colored_pips(
                        game, player_idx, player.untapped_lands,
                        template.spectacle_cost)
                )
                if can_spectacle and not can_normal_sp:
                    spectacled = True
                elif (can_spectacle and can_normal_sp
                      and template.spectacle_cost.cmc < template.mana_cost.cmc):
                    spectacled = True

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
                can_dash = untapped >= template.dash_cost.cmc

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
                not dashed and not escaped and not spectacled
                and template.evoke_cost is not None
                and untapped < template.mana_cost.cmc
                and game.callbacks.should_evoke(game, player_idx, card)
            )
            # Target validation: don't evoke if the card needs a target and none exists
            if should_evoke:
                from decks.card_knowledge_loader import requires_target as _requires_target
                needs_target = (
                    _requires_target(template.name)
                    or getattr(template, 'requires_creature_target', False)
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
            if spectacled:
                # Pay Spectacle cost (CR 702.131) instead of normal cost
                if not game.tap_lands_for_mana(player_idx, template.spectacle_cost,
                                                 card_name=template.name):
                    return False
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Spectacle {card.name} (pays {template.spectacle_cost})")
            elif warped:
                # Pay Warp cost instead of normal cost
                if not game.tap_lands_for_mana(player_idx, template.warp_cost,
                                                 card_name=template.name):
                    return False
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Warp {card.name} (pays {template.warp_cost})")
            elif escaped:
                # Pay escape cost using the template's ManaCost directly —
                # no per-card hardcoding; colour pips verified by can_cast.
                if not game.tap_lands_for_mana(player_idx, template.escape_cost,
                                                 card_name=template.name):
                    return False
            elif dashed:
                # Pay dash cost using the template's ManaCost directly —
                # no per-card hardcoding; colour pips verified by can_cast.
                if not game.tap_lands_for_mana(player_idx, template.dash_cost,
                                                 card_name=template.name):
                    return False
            elif not evoked:
                # Force alternate cost: exile a card from hand instead of mana
                oracle_lower = (template.oracle_text or '').lower()
                force_cast = False
                if (getattr(template, 'has_alternate_exile_cost', False)
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
                        phyrexian_count = getattr(template, 'phyrexian_pip_count', 0)
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
        if card in player.exile:
            player.exile.remove(card)
        elif card in player.hand:
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
        # CR 702.88a (and every other "if this spell was cast from your
        # X" replacement): the zone a spell was cast FROM is part of how
        # it was cast, so record it before the stack move overwrites it.
        card._cast_from_zone = card.zone
        card.zone = "stack"
        card._cast_with_flashback = cast_with_flashback
        card._evoked = evoked  # Track for sacrifice after ETB
        card._dashed = dashed  # Track for haste + return to hand at end of turn
        card._escaped = getattr(card, '_escaped', False) or (escaped if not free_cast else False)  # Track for sacrifice-unless-escaped
        card._warped = warped  # Track for exile at beginning of next end step
        # Turn-scoped budget for `_eval_evoke`: each removal-class
        # evoke ramps the cost of the next one. Increment at cast
        # time (not on resolve) — even a fizzling evoke pitches the
        # support card, so the budget should be debited regardless.
        if evoked and 'removal' in getattr(template, 'tags', set()):
            player.removal_evokes_resolved_this_turn += 1

        # Calculate X value for X-cost spells
        x_value = 0
        if template.x_cost_data and not free_cast and not evoked:
            x_info = template.x_cost_data
            # X = (total mana available) / multiplier
            # For XX spells, X = mana / 2; for X spells, X = mana
            available_for_x = player.untapped_mana_capacity() + player.mana_pool.total() + player._tron_mana_bonus()
            x_value = available_for_x // x_info["multiplier"]
            # AI chooses optimal X based on typed fields:
            if getattr(template, 'stax_class', None) == 'chalice':
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
            elif getattr(template, 'has_mana_value_wipe', False):
                # Scaling board-wipe-by-X (Wrath of the Skies pattern):
                # "Destroy each artifact, creature, and enchantment with
                # mana value less than or equal to the amount of {E} paid
                # this way." Delegate to the module-level
                # `pick_wipe_x_value` helper so the AI scoring layer can
                # consult the SAME picker via the same code path. Score
                # by VALUE, not count — killing Cranial Plating (CMC 2)
                # is worth ~10× killing Memnite (CMC 0); see
                # `pick_wipe_x_value` docstring + audit F-Wrath-X.
                best_x, _, _ = pick_wipe_x_value(
                    game, player_idx, int(x_value)
                )
                x_value = best_x
            elif getattr(template, 'x_creature_tutor_data', None):
                # X-cost creature tutor (Green Sun's Zenith shape): the
                # resolver fetches the highest-mana-value candidate within
                # X, so every point of X above the best fetchable target's
                # cost is mana buying nothing. Delegate to the module-level
                # `pick_creature_tutor_x_value` — the AI scoring layer
                # (`ai/ev_player.py::_gate_x_tutor_payoff`) consults the
                # SAME picker — which chooses the cheapest X that still
                # delivers the best target (never X=4 for a 1-drop).
                best_x, _target, _top = pick_creature_tutor_x_value(
                    game, player_idx, int(x_value), template
                )
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
            is_converge = getattr(template, 'has_converge', False)
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
                # E1: one tap yields every unit the land produces.
                from .mana_payment import ManaPayment as _MP
                for options in _MP.land_mana_units(game, player_idx, land):
                    if remaining <= 0:
                        break
                    remaining -= 1
                    if is_converge:
                        new_cols = [c for c in options
                                    if c not in xpay_colors]
                        pick = new_cols[0] if new_cols else (
                            options[0] if options else 'C')
                    else:
                        pick = options[0] if options else 'C'
                    if pick and pick != 'C':
                        xpay_colors.add(pick)
            # Surface the updated color set for the stack item / Converge resolvers
            game._last_colors_spent = xpay_colors

        # CR 608.2b support: snapshot each card-target's zone at cast
        # time. ResolutionManager re-checks target legality on
        # resolution against this snapshot — battlefield for removal,
        # stack for counterspells, graveyard for reanimation. Player-
        # target markers (negative ids) have no zone to snapshot.
        target_zones = {}
        for _tid in (targets or []):
            if isinstance(_tid, int) and _tid > 0:
                _tc = game.get_card_by_id(_tid)
                if _tc is not None:
                    target_zones[_tid] = _tc.zone

        stack_item = StackItem(
            item_type=StackItemType.SPELL,
            source=card,
            controller=player_idx,
            targets=targets or [],
            target_zones=target_zones,
            x_value=x_value,
            # Propagate the evoke flag so StackItem.evoked mirrors
            # card._evoked — replay logging and any future code that
            # reads item.evoked (e.g. triggered-ability dispatch) sees
            # True when the spell was cast for its evoke cost.
            evoked=evoked,
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
                # splice is a ManaCost — apply cost reduction to generic portion
                reduction = count_cost_reducers(game, player_idx, sc.template)
                reduction += player.temp_cost_reduction
                from .mana import ManaCost as MC
                effective_splice = MC(
                    generic=max(0, splice.generic - reduction),
                    white=splice.white, blue=splice.blue, black=splice.black,
                    red=splice.red, green=splice.green, colorless=splice.colorless,
                )
                available_mana = player.mana_pool.total() + player.untapped_mana_capacity()
                if available_mana >= effective_splice.cmc:
                    if not game.tap_lands_for_mana(player_idx, effective_splice,
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
                # Anchor the pump search to the actual "whenever you cast
                # ... spell" triggered-ability CLAUSE (up to the next
                # sentence boundary) rather than the creature's whole
                # oracle text. A creature can print a "+N/+N" elsewhere for
                # an unrelated reason (a delirium condition, an oil-counter
                # static, an anthem for another creature type) alongside a
                # real cast-trigger whose own effect has no P/T component
                # (e.g. Dragon's Rage Channeler's trigger only surveils —
                # its "+2/+2" belongs to a separate Delirium static). Class:
                # >=11 Modern creatures share this "unrelated pump text
                # co-present with a cast trigger" shape; searching the
                # whole blob re-applies that unrelated bonus on every spell
                # cast instead of leaving it to the static/condition that
                # actually grants it.
                trigger_clause = next(
                    (m.group(0) for m in re.finditer(
                        r'whenever you cast[^.]*?'
                        r'(?:noncreature spell|instant or sorcery)[^.]*\.',
                        c_oracle,
                    )),
                    None,
                )
                if trigger_clause is None:
                    continue
                pump = re.search(r'gets?\s+\+(\d+)/\+(\d+)', trigger_clause)
                if pump:
                    creature.temp_power_mod += int(pump.group(1))
                    creature.temp_toughness_mod += int(pump.group(2))
                elif re.search(r'gets?\s+\+(\d+)/\+0', trigger_clause):
                    m = re.search(r'gets?\s+\+(\d+)/\+0', trigger_clause)
                    creature.temp_power_mod += int(m.group(1))
                # Delirium — check actual GY card types via _has_delirium()
                # _dynamic_base_power() already scales to 3 with delirium; we also
                # need to grant FLYING as a keyword so combat logic sees it.
                if getattr(creature.template, 'has_delirium', False) and hasattr(creature, '_has_delirium'):
                    if creature._has_delirium():
                        if Keyword.FLYING not in creature.keywords:
                            creature.keywords.add(Keyword.FLYING)

                # Surveil-on-noncreature-spell-cast (DRC, Lightshell Duo,
                # Garland) moved to resolve_spell_cast_trigger so the same
                # generic dispatch handles spell-cast surveil AND land-ETB
                # surveil. See R3 in
                # docs/history/audits/2026-05-16_rules_audit.md.

        # Generic oracle-text-based spell-cast triggers (incl. surveil)
        from .oracle_resolver import resolve_spell_cast_trigger
        resolve_spell_cast_trigger(game, player_idx, card)

        # The spell's OWN "When you cast this spell, <effect>" trigger
        # (CR 601.2i) — distinct from the watcher triggers above. Powers the
        # Eldrazi ramp/interaction suite (Sowing Mycospawn land search,
        # Devourer/Ugin exile a colored permanent on cast).
        from .oracle_resolver import resolve_self_cast_trigger
        resolve_self_cast_trigger(game, player_idx, card)

        return True


