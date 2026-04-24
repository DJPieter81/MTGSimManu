"""
Mana payment — extracted from engine/game_state.py.

Owns:
- tap_lands_for_mana: pay a ManaCost from mana pool + untapped lands,
  including cost-reduction sources (Ruby Medallion, Affinity, domain,
  Ral +1 temp reduction). Uses MRV (Most Restricted Variable) ordering
  over colored costs and records colors-of-mana-spent for Converge.
- has_leyline_of_guildpact: oracle-driven "all lands are every basic
  type" detection.
- effective_produces_mana: land colors under Leyline adjustment.
- count_domain: number of basic land types controlled (capped at 5
  under Leyline of the Guildpact).

All methods are static and take `game: GameState` as the first
argument, matching the SBAManager / CombatManager delegation pattern.
"""
from __future__ import annotations

from typing import List, Optional, Set, TYPE_CHECKING

from .cards import CardType, Keyword
from .mana import ManaCost

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState


# Ordered list (matches the legacy GameState.ALL_COLORS class attribute).
ALL_COLORS: List[str] = ["W", "U", "B", "R", "G"]

# Basic land types used by domain counting.
BASIC_TYPES = {"Plains", "Island", "Swamp", "Mountain", "Forest"}


class ManaPayment:
    """Mana cost payment manager. Stateless — the methods operate on a
    GameState passed in as the first argument."""

    @staticmethod
    def has_leyline_of_guildpact(game: "GameState", player_idx: int) -> bool:
        """True if the player controls a permanent that makes lands
        every basic land type. Replaces the old hardcoded Leyline check
        with an oracle-text predicate."""
        return any(
            'lands you control are every basic land type'
            in (c.template.oracle_text or '').lower()
            for c in game.players[player_idx].battlefield
        )

    @staticmethod
    def effective_produces_mana(game: "GameState", player_idx: int,
                                 land: "CardInstance") -> list:
        """Return the colors a land effectively produces for this
        player, accounting for Leyline of the Guildpact."""
        if ManaPayment.has_leyline_of_guildpact(game, player_idx):
            return ALL_COLORS
        return land.template.produces_mana

    @staticmethod
    def count_domain(game: "GameState", player_idx: int) -> int:
        """Count basic land types among lands controlled. Under a
        Leyline-of-the-Guildpact-style effect, returns 5 as long as
        the player controls at least one land."""
        for c in game.players[player_idx].battlefield:
            if ('lands you control are every basic land type'
                    in (c.template.oracle_text or '').lower()):
                if any(l.template.is_land
                       for l in game.players[player_idx].battlefield):
                    return 5
        found = set()
        for land in game.players[player_idx].battlefield:
            if land.template.is_land:
                for st in land.template.subtypes:
                    if st in BASIC_TYPES:
                        found.add(st)
        return len(found)

    @staticmethod
    def tap_lands_for_mana(game: "GameState", player_idx: int,
                           cost: ManaCost,
                           card_name: str = None,
                           held_instant_colors: Optional[Set[str]] = None
                           ) -> bool:
        """Tap lands to pay a mana cost. Returns True if successful.

        Side effect: sets game._last_colors_spent to the set of colors
        of mana spent to pay this cost (for Converge mechanic). Colors
        come from the lands tapped in this call PLUS colors drained
        from the pre-existing mana pool. Empty if cost was 0 or
        payment failed.

        held_instant_colors (Bundle 3 A5): optional set of color codes
        the AI wants preserved (i.e. colors of held instants / flash
        permanents). When supplied, among otherwise-equivalent land
        orderings the engine prefers the one that leaves these colors
        available untapped. Engine stays neutral when `None` — no
        strategic choice without AI input.
        """
        player = game.players[player_idx]
        # Snapshot mana pool BEFORE payment so we can detect which colors
        # were drained from pre-existing ritual/pool mana (Converge rule).
        _pre_pool = {c: player.mana_pool.get(c)
                     for c in ["W", "U", "B", "R", "G", "C"]}
        # Reset the colors-spent tracker. Populated at the end of this call.
        game._last_colors_spent = set()

        # Cost reductions
        reduction = 0
        # Domain cost reduction (from oracle-derived template property)
        # Replaces hardcoded "Scion of Draco" / "Leyline Binding" checks
        if card_name:
            for c in list(game.players[player_idx].hand) + list(game.players[player_idx].graveyard):
                if c.template.name == card_name and c.template.domain_reduction > 0:
                    domain = ManaPayment.count_domain(game, player_idx)
                    reduction += c.template.domain_reduction * domain
                    break
        # Ruby Medallion and Affinity cost reductions
        player = game.players[player_idx]
        if card_name:
            # Check hand, graveyard, and stack for the card (flashback casts are from GY)
            all_cards = list(player.hand) + list(player.graveyard)
            for c in all_cards:
                if c.template.name == card_name:
                    # Generic cost reduction from permanents
                    from .oracle_resolver import count_cost_reducers
                    reduction += count_cost_reducers(game, player_idx, c.template)
                    # Temporary cost reduction (Ral PW +1 "until your next turn")
                    if c.template.is_instant or c.template.is_sorcery:
                        reduction += player.temp_cost_reduction
                    # Affinity for artifacts
                    if Keyword.AFFINITY in c.template.keywords:
                        artifact_count = sum(
                            1 for b in player.battlefield
                            if CardType.ARTIFACT in b.template.card_types
                        )
                        reduction += artifact_count
                    break
        if reduction > 0:
            from .mana import ManaCost as MC
            new_generic = max(0, cost.generic - reduction)
            cost = MC(
                white=cost.white, blue=cost.blue, black=cost.black,
                red=cost.red, green=cost.green, colorless=cost.colorless,
                generic=new_generic
            )
        untapped = [l for l in player.lands if not l.tapped]

        if not untapped and player.mana_pool.total() == 0:
            return cost.cmc == 0

        # Check if mana pool already has enough (from rituals)
        if player.mana_pool.can_pay(cost):
            return player.mana_pool.pay(cost)

        # Leyline of the Guildpact: all lands produce WUBRG
        has_leyline = ManaPayment.has_leyline_of_guildpact(game, player_idx)

        def _produces(land):
            return ALL_COLORS if has_leyline else land.template.produces_mana

        # Sort lands: most restrictive first (fewest colors produced).
        # Secondary key (Bundle 3 A5): when the AI has supplied
        # `held_instant_colors`, lands that produce one of those colors
        # sort LATER — so the MRV walk taps them last, preserving the
        # held-interaction color for the opponent's turn.
        _held = held_instant_colors or set()
        def _sort_key(l):
            lp = _produces(l)
            produces_held = 1 if any(c in _held for c in lp) else 0
            return (produces_held, len(lp))
        untapped.sort(key=_sort_key)

        needed = cost.to_dict()
        lands_to_tap = []

        # Pay colored costs using MRV (Most Constrained Variable) heuristic:
        # Process colors with the FEWEST available land sources first.
        # This prevents greedy misassignment where a dual land is used for
        # a color that has many sources, leaving a color with few sources
        # unable to be paid.
        #
        # Example: Faithful Mending costs WU.
        #   Lands: Hallowed Fountain (W/U), Godless Shrine (W/B), Godless Shrine (W/B)
        #   Fixed order (W first): Fountain→W, then no U source → FAIL
        #   MRV order (U first, only 1 source): Fountain→U, then Shrine→W → SUCCESS

        # First, use mana pool for colored costs
        pool_used = {}
        for color in ["W", "U", "B", "R", "G", "C"]:
            remaining = needed.get(color, 0)
            if remaining > 0:
                pool_avail = player.mana_pool.get(color)
                use_pool = min(pool_avail, remaining)
                pool_used[color] = use_pool
                needed[color] = remaining - use_pool

        # Collect colors that still need land sources
        colors_needed_list = []
        for color in ["W", "U", "B", "R", "G", "C"]:
            for _ in range(needed.get(color, 0)):
                colors_needed_list.append(color)

        # Assign with re-sorting: most constrained color first each step
        used_lands = set()

        while colors_needed_list:
            # Re-sort by scarcity each step (fixes 4-color dual land issues)
            colors_needed_list.sort(
                key=lambda c: sum(1 for l in untapped
                                  if l not in used_lands and c in _produces(l))
            )
            color = colors_needed_list.pop(0)
            # Find least-flexible unused land for this color. Ties broken
            # by preserving held_instant_colors when supplied — a land
            # that produces a held color is less preferred (we want to
            # leave it untapped for the opponent's turn).
            best_land = None
            best_key = (999, 999)
            for land in untapped:
                if land in used_lands:
                    continue
                lp = _produces(land)
                if color in lp:
                    flex = len(lp)
                    # Skip the held-preserve penalty if this land is the
                    # only source of the required color — correctness
                    # (must pay the cost) wins over preservation.
                    produces_held = 1 if any(
                        c in _held and c != color for c in lp) else 0
                    key = (flex, produces_held)
                    if key < best_key:
                        best_key = key
                        best_land = land
            if best_land is None:
                return False
            lands_to_tap.append((best_land, color))
            used_lands.add(best_land)

        # Pay generic
        generic_remaining = needed.get("generic", 0)
        # Use pool first
        pool_total = player.mana_pool.total()
        # Subtract what we already committed from pool for colored
        for color in ["W", "U", "B", "R", "G", "C"]:
            pool_avail = player.mana_pool.get(color)
            use_pool = min(pool_avail, needed.get(color, 0))
            pool_total -= use_pool

        use_pool_generic = min(pool_total, generic_remaining)
        generic_remaining -= use_pool_generic

        # Pre-compute conditional mana bonus for each land
        # (uses the data-driven conditional_mana field parsed from oracle text)
        cond_bonus_cache = player._compute_conditional_bonus_per_land()

        for land in untapped:
            if generic_remaining <= 0:
                break
            if land not in used_lands:
                lp = _produces(land)
                if lp:
                    lands_to_tap.append((land, lp[0]))
                    used_lands.add(land)
                    # Base 1 + any conditional bonus from oracle text
                    mana_from_land = 1 + cond_bonus_cache.get(id(land), 0)
                    generic_remaining -= mana_from_land

        if generic_remaining > 0:
            return False

        # Tap lands and add mana
        tapped_names = []
        for land, color in lands_to_tap:
            land.tap()
            player.mana_pool.add(color)
            tapped_names.append(f'{land.name}→{color}')
            bonus = cond_bonus_cache.get(id(land), 0)
            if bonus > 0:
                player.mana_pool.add("C", bonus)
            # Pain land: self-damage when tapping for colored mana
            if land.template.tap_damage > 0 and color != "C":
                player.life -= land.template.tap_damage

        # Verbose: log which lands were tapped for mana
        if getattr(game, 'verbose', False) and tapped_names and card_name:
            remaining_mana = len(player.untapped_lands) + player.mana_pool.total()
            game.log.append(f'    [Mana] Tap {", ".join(tapped_names)} '
                            f'(paying for {card_name}, {remaining_mana} mana remaining)')

        ok = player.mana_pool.pay(cost)
        if ok:
            # Record colors-of-mana-spent for Converge and similar mechanics.
            # Includes colors tapped from lands in this call + any colors
            # that existed in the pre-call mana pool and were drained below
            # pre-levels (i.e. spent on this cost rather than carried over).
            # Note: _pre_pool doesn't account for any mana the lands_to_tap
            # loop ADDED to the pool before .pay() drained it — that's OK
            # because those colors are captured in the lands_to_tap side.
            game._last_colors_spent = {color for land, color in lands_to_tap}
            for c in ["W", "U", "B", "R", "G"]:
                if _pre_pool[c] > 0 and player.mana_pool.get(c) < _pre_pool[c]:
                    game._last_colors_spent.add(c)
        return ok
