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
            base = [list(u) for u in units]
        else:
            produced = ManaPayment.effective_produces_mana(
                game, player_idx, land)
            base = [list(produced)] if produced else []
        # Auras attached to this land grant additional units (CR 303.4).
        # Applied here, in the single per-land unit resolver, so payment and
        # every capacity estimate pick it up without their own special case.
        if base:
            from .permanent_effects import PermanentEffects
            base.extend(PermanentEffects.aura_granted_mana_units(game, land))
        return base

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
        has_improvise = False
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
                    # Improvise (Track H handoff): unlike affinity this
                    # is NOT a static discount — artifacts must be
                    # TAPPED to pay. Handled below after the static
                    # reductions are applied.
                    # Keyword.IMPROVISE populated at DB load via KEYWORD_MAP.
                    has_improvise = Keyword.IMPROVISE in c.template.keywords
                    break
        if reduction > 0:
            from .mana import ManaCost as MC
            new_generic = max(0, cost.generic - reduction)
            cost = MC(
                white=cost.white, blue=cost.blue, black=cost.black,
                red=cost.red, green=cost.green, colorless=cost.colorless,
                generic=new_generic
            )

        # Improvise payment (Track H handoff): tap untapped non-land
        # artifacts to pay {1} of generic each — but only for the
        # shortfall that lands + pool cannot cover, so artifact
        # creatures / mana rocks stay untapped whenever lands suffice.
        if has_improvise and cost.generic > 0:
            capacity = (player.untapped_mana_capacity()
                        + player.mana_pool.total()
                        + player._tron_mana_bonus())
            shortfall = max(0, cost.cmc - capacity)
            if shortfall > 0:
                improvise_payers = [
                    a for a in player.battlefield
                    if CardType.ARTIFACT in a.template.card_types
                    and not a.template.is_land
                    and not a.tapped
                ]
                n_tap = min(len(improvise_payers), shortfall, cost.generic)
                if n_tap > 0:
                    from .mana import ManaCost as MC
                    for a in improvise_payers[:n_tap]:
                        a.tapped = True
                    cost = MC(
                        white=cost.white, blue=cost.blue, black=cost.black,
                        red=cost.red, green=cost.green,
                        colorless=cost.colorless,
                        generic=cost.generic - n_tap,
                    )
                    game.log.append(
                        f"T{game.display_turn} P{player_idx+1}: "
                        f"Improvise — tap {n_tap} artifact(s) for "
                        f"{card_name}")

        # One-shot sacrifice-for-mana permanents (CR 605): Eldrazi Spawn/Scion,
        # Treasure, Lotus-style artifacts — 206 cards in the pool. Spending one
        # CONSUMES it, so (exactly like Improvise above) only ever spend the
        # SHORTFALL that repeatable sources plus the pool cannot already cover;
        # otherwise a deck would eat its own ramp to pay costs its lands
        # already afford. Tapped state is irrelevant — the cost is sacrifice,
        # not {T} — which is why these are collected separately from
        # `untapped` rather than appended to it.
        # Permanents whose sacrifice-for-mana ability has been COMMITTED TO but
        # not yet performed. Filled by the block below, then either performed
        # (`_commit_sacrifices`, on every success path) or abandoned along with
        # the mana it produced (`_refund_sacrifices`, on every failure path).
        pending_sacrifice = []

        def _commit_sacrifices():
            """Perform the deferred sacrifices — payment succeeded."""
            for _perm in pending_sacrifice:
                # NOTE: ZoneManager.move_card does NOT currently dispatch
                # dies/LTB triggers (zone_manager.py) — an earlier revision of
                # this code claimed it did. It is still the sanctioned funnel
                # (single owner of zone mutation), so route through it; when
                # the funnel gains trigger dispatch this call inherits it.
                game.zone_mgr.move_card_to_graveyard(
                    game, _perm, cause=f"sacrificed for mana ({card_name})")
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"sacrifice {_perm.name} for mana ({card_name})")
            pending_sacrifice.clear()

        def _refund_sacrifices():
            """Payment failed: un-add the mana, leave the permanents alone."""
            for _perm in pending_sacrifice:
                for _unit in (_perm.template.sacrifice_mana_units or []):
                    player.mana_pool.remove(_unit[0], 1)
            pending_sacrifice.clear()

        sac_sources = [
            perm for perm in player.battlefield
            if getattr(perm.template, 'sacrifice_mana_units', None)
        ]
        if sac_sources:
            capacity = (player.untapped_mana_capacity()
                        + player.mana_pool.total()
                        + player._tron_mana_bonus())
            shortfall = max(0, cost.cmc - capacity)
            if shortfall > 0:
                spent = 0
                for perm in sac_sources:
                    if spent >= shortfall:
                        break
                    units = perm.template.sacrifice_mana_units or []
                    if not units:
                        continue
                    # DEFERRED: the permanent is NOT moved here. This function
                    # has failure returns further down (an unmeetable coloured
                    # requirement; a generic remainder), and destroying a card
                    # for a spell that then fails to cast is strictly worse
                    # than declining the cast. Record the intent, add the mana
                    # so the solver below can use it, and only actually
                    # sacrifice on a success path (`_commit_sacrifices`).
                    # Rolling back after the move is not an option: a card
                    # returning from the graveyard would be a new object
                    # (CR 400.7) and would re-fire its ETB.
                    pending_sacrifice.append(perm)
                    for unit in units:
                        # A unit lists the colours it could produce; a
                        # single-colour unit is fixed, a multi-colour unit is
                        # the "any colour" shape. Add the first option and let
                        # the pool's own payment logic sort out the rest.
                        player.mana_pool.add(unit[0], 1)
                        spent += 1

        untapped = [l for l in player.lands if not l.tapped]

        # Non-land mana sources: mana rocks (Talisman, Mind Stone) and mana
        # creatures (Birds, Llanowar, Devoted Druid). A permanent whose
        # template exposes a plain "{T}: Add" ability (produces_mana /
        # mana_units populated for non-lands, CR 605) is tappable for mana
        # exactly like a land. Creature mana abilities carry the tap symbol,
        # so summoning sickness gates them (CR 302.6 / 605.3a).
        for perm in player.battlefield:
            if perm.tapped or perm.template.is_land:
                continue
            if not (perm.template.mana_units or perm.template.produces_mana):
                continue
            if (CardType.CREATURE in perm.template.card_types
                    and perm.has_summoning_sickness):
                continue
            untapped.append(perm)

        if not untapped and player.mana_pool.total() == 0:
            _ok = cost.cmc == 0
            _commit_sacrifices() if _ok else _refund_sacrifices()
            return _ok

        # Check if mana pool already has enough (from rituals)
        if player.mana_pool.can_pay(cost):
            _paid = player.mana_pool.pay(cost)
            _commit_sacrifices() if _paid else _refund_sacrifices()
            return _paid

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
                _refund_sacrifices()
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
            _refund_sacrifices()
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
        # Final exit: perform the deferred sacrifices only if we actually paid.
        _commit_sacrifices() if ok else _refund_sacrifices()
        return ok
