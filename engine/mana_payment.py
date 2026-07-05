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

# Metalcraft threshold per CR 702.98: "You have metalcraft as long as
# you control three or more artifacts."  Checked at activation time, so
# Mox Opal's colour production must be evaluated whenever its tap
# ability is invoked, not snapshotted at ETB.
METALCRAFT_THRESHOLD = 3


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
                                 card: "CardInstance") -> list:
        """Return the colors a permanent effectively produces for this
        player right now.

        Accounts for:
          - Leyline of the Guildpact (all lands produce WUBRG)
          - Mox Opal metalcraft (CR 702.98): dynamic artifact-count
            re-evaluation at activation time, not at ETB.  The prior
            implementation mutated ``card.template`` on ETB, which kept
            Mox producing 5 colours after artifact count fell below 3
            and left it dead if metalcraft was gained later.

        ``card`` may be a land or a mana-producing artifact (e.g. Mox
        Opal).  Non-lands only use the metalcraft branch; the Leyline
        branch is gated to lands.
        """
        # Leyline of the Guildpact: only applies to lands.
        if card.template.is_land and ManaPayment.has_leyline_of_guildpact(game, player_idx):
            return ALL_COLORS
        # Metalcraft-gated any-color mana ability (Mox Opal today;
        # any future printing with the same oracle shape automatically).
        # Generic predicate at engine/oracle_parser.py replaces the
        # previous card-name check.
        from .oracle_parser import is_metalcraft_mana_any_color
        if is_metalcraft_mana_any_color(card.template.oracle_text or ""):
            artifact_count = sum(
                1 for c in game.players[player_idx].battlefield
                if CardType.ARTIFACT in c.template.card_types
            )
            if artifact_count >= METALCRAFT_THRESHOLD:
                return list(ALL_COLORS)
            return []
        return card.template.produces_mana

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
    def land_mana_units(game: "GameState", player_idx: int,
                        land) -> list:
        """Mana units for one land: the template's parsed multi-mana
        units (E1) when present, else a single unit whose color
        options are the dynamic `effective_produces_mana` result (so
        Leyline-of-the-Guildpact-style modifiers keep working on
        legacy single-unit lands)."""
        units = getattr(land.template, "mana_units", None)
        if units:
            return [list(u) for u in units]
        produced = ManaPayment.effective_produces_mana(
            game, player_idx, land)
        return [list(produced)] if produced else []

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

        # Routes through `effective_produces_mana` so Leyline of the
        # Guildpact and dynamic mana abilities (E1: Mox Opal metalcraft,
        # CR 702.98) are honoured whenever a source's colours are read.
        def _produces(land):
            return ManaPayment.effective_produces_mana(game, player_idx, land)

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

        # E1 (multi-mana lands): the assignable resource is a mana
        # UNIT, not a land — a karoo's single tap yields a {G} unit
        # AND a {U} unit.  Build (land, unit_idx, color_options)
        # triples; assignment consumes units, tapping consumes lands.
        unit_pool = []  # list of [land, unit_idx, options, assigned]
        for land in untapped:
            for ui, options in enumerate(
                    ManaPayment.land_mana_units(game, player_idx, land)):
                unit_pool.append([land, ui, list(options), None])

        # Assign with re-sorting: most constrained color first each step
        while colors_needed_list:
            # Re-sort by scarcity each step (fixes 4-color dual land issues)
            colors_needed_list.sort(
                key=lambda c: sum(1 for u in unit_pool
                                  if u[3] is None and c in u[2])
            )
            color = colors_needed_list.pop(0)
            # Find least-flexible unassigned unit for this color. Ties
            # broken by preserving held_instant_colors when supplied —
            # a unit on a land that produces a held color is less
            # preferred (we want to leave that land untapped for the
            # opponent's turn).
            best_unit = None
            best_key = (999, 999)
            for unit in unit_pool:
                land, _ui, options, assigned = unit
                if assigned is not None:
                    continue
                if color in options:
                    flex = len(options)
                    # Skip the held-preserve penalty if this land is the
                    # only source of the required color — correctness
                    # (must pay the cost) wins over preservation.
                    produces_held = 1 if any(
                        c in _held and c != color
                        for c in _produces(land)) else 0
                    key = (flex, produces_held)
                    if key < best_key:
                        best_key = key
                        best_unit = unit
            if best_unit is None:
                return False
            best_unit[3] = color

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

        # Generic payment consumes remaining UNASSIGNED units.  Units
        # on a land already committed for a colored pip come first —
        # tapping that land is already paid for, its spare units are
        # free mana (this is exactly the karoo case: {G} pays the pip,
        # the {U} unit covers a generic).  The per-land conditional
        # bonus (Tron rail) applies once, on the land's FIRST used
        # unit.
        committed_lands = {id(u[0]) for u in unit_pool if u[3] is not None}
        bonus_counted = set(committed_lands)

        def _generic_order(unit):
            on_committed = 0 if id(unit[0]) in committed_lands else 1
            return (on_committed, len(unit[2]))

        for unit in sorted((u for u in unit_pool if u[3] is None),
                           key=_generic_order):
            if generic_remaining <= 0:
                break
            land, _ui, options, _a = unit
            if not options:
                continue
            unit[3] = options[0]
            generic_remaining -= 1
            if id(land) not in bonus_counted:
                bonus_counted.add(id(land))
                generic_remaining -= cond_bonus_cache.get(id(land), 0)

        if generic_remaining > 0:
            return False

        # Tap lands and add mana.  A land taps ONCE; every assigned
        # unit on it yields its color.  Unassigned units on a tapped
        # land still produce — that mana floats into the pool (CR
        # 106.4: you can't decline part of a single ability's
        # production), matching the karoo tapped only for its {G}.
        lands_to_tap = []
        seen = set()
        for unit in unit_pool:
            land = unit[0]
            if unit[3] is not None and id(land) not in seen:
                seen.add(id(land))
                lands_to_tap.append(land)

        tapped_names = []
        for land in lands_to_tap:
            land.tap()
            land_units = [u for u in unit_pool if u[0] is land]
            yielded = []
            for _l, _ui, options, assigned in land_units:
                color = assigned if assigned is not None else (
                    options[0] if options else None)
                if color is None:
                    continue
                player.mana_pool.add(color)
                yielded.append(color)
            tapped_names.append(f'{land.name}→{"".join(yielded)}')
            bonus = cond_bonus_cache.get(id(land), 0)
            if bonus > 0:
                player.mana_pool.add("C", bonus)
            # Pain land: self-damage when tapping for colored mana
            if land.template.tap_damage > 0 and any(
                    c != "C" for c in yielded):
                player.life -= land.template.tap_damage

        # Verbose: log which lands were tapped for mana
        if getattr(game, 'verbose', False) and tapped_names and card_name:
            remaining_mana = player.untapped_mana_capacity() + player.mana_pool.total()
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
            game._last_colors_spent = {
                u[3] for u in unit_pool
                if u[3] is not None and id(u[0]) in seen}
            for c in ["W", "U", "B", "R", "G"]:
                if _pre_pool[c] > 0 and player.mana_pool.get(c) < _pre_pool[c]:
                    game._last_colors_spent.add(c)
        return ok
