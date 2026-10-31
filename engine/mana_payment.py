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


def _sacrifice_mana_ability(perm):
    """The typed mana ability of `perm` whose cost sacrifices it (CR 605.1a:
    "Sacrifice this token: Add {C}", "{T}, Sacrifice this token: Add one mana
    of any color"), or None."""
    return next((a for a in (perm.template.activated_abilities or ())
                 if a.is_mana_ability and a.cost.sacrifice_self), None)


def _sacrifice_cost_payable(perm, ability) -> bool:
    """Can `ability`'s cost be paid while its controller pays another cost
    (CR 605.3a)? It holds nothing but sacrificing its source and a {T}: a
    cost with mana in it is a conversion that needs the mana it makes, and
    any other item is not paid here. A {T} needs the source untapped (CR
    107.5) and, on a creature, under its controller's control since their
    turn began (CR 302.6)."""
    c = ability.cost
    if c.mana.cmc or c.unpayable or c.x_count or c.life or c.untap_self \
            or c.exile_self or c.sacrifice_another \
            or c.sacrifice_type is not None or c.discard_cards \
            or c.put_counter_kind is not None \
            or c.remove_counter_kind is not None \
            or c.exile_from_graveyard_cards:
        return False
    if c.tap_self and (perm.tapped or (perm.effective_is_creature
                                       and perm.has_summoning_sickness)):
        return False
    return True


def _audit_sacrifices(game, pending) -> None:
    """CR 605.3a / 107.5, restated from each sacrificed source's raw ability
    cost and its state before the payment began: a source sacrificed for
    mana was one whose ability's cost could be paid -- no mana in it, and
    untapped if the cost taps it. Observation only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    problems = []
    for perm, _added, _tapped_now, was_tapped in pending:
        ability = _sacrifice_mana_ability(perm)
        if ability is None:
            problems.append(f"{perm.name} has no sacrifice-for-mana ability")
        elif ability.cost.mana.cmc:
            problems.append(f"{perm.name}'s ability costs mana")
        elif ability.cost.tap_self and was_tapped:
            problems.append(f"{perm.name} was tapped for its {{T}} cost")
    rules_audit.check("605.3a/sacrifice_mana_cost_paid", not problems,
                      "; ".join(problems), game=game)


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
        # Layer-4 land-type SET (Blood Moon family, CR 305.7): the land
        # IS the named basic type and has only that type's mana ability.
        # Written by ContinuousEffectsManager.recalculate; checked first
        # because a set type replaces every printed and granted ability.
        # Both layer-4 families (SET and "every basic land type" ADD)
        # resolve through the land's current basic types (CR 305.6: each
        # basic type is one intrinsic mana ability). A set land with no
        # later ADD produces its one colour; a set land with a later ADD
        # produces all five; an unset land with an ADD produces all five.
        forced = getattr(card, 'cem_land_type_set', None)
        added = getattr(card, 'cem_land_types_added', None)
        if card.template.is_land and (forced or added):
            from .constants import BASIC_LAND_TYPE_COLORS
            produced = sorted({BASIC_LAND_TYPE_COLORS[t.lower()]
                               for t in card.current_basic_land_types},
                              key=ALL_COLORS.index)
            if forced and not added:
                # Rules audit (CR 305.7): a set land with no later ADD
                # produces exactly its one colour.
                from .rules_audit import check as _audit_check
                _audit_check("305.7/set_land_mana",
                             produced == [BASIC_LAND_TYPE_COLORS[forced]],
                             f"{card.name} is a {forced.title()} and produces {produced}",
                             game=game)
            return produced
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
        """Count basic land types among lands controlled — from each
        land's CURRENT types (`CardInstance.current_basic_land_types`,
        the continuous-effects layer in timestamp order), never from
        printed subtypes or a flag for the "every basic land type"
        family: a later Blood Moon leaves a Leyline deck at domain 1."""
        found = set()
        for land in game.players[player_idx].battlefield:
            found |= land.current_basic_land_types
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
            # Tap-for-mana triggers (CR 605.1b) — "whenever you tap a <filter>
            # for mana, add …". Resolved here for the same reason the Aura
            # grant is: this is the ONE place a source's units are computed, so
            # the AI's capacity estimate and what the payment path can actually
            # spend are the same number by construction. The trigger sees the
            # source's own units (Aura grant included), which is what the
            # "any type that permanent produced" rider mirrors.
            base.extend(PermanentEffects.tap_trigger_bonus_units(
                game, land, base))
        return base

    @staticmethod
    def sacrifice_mana_sources(game: "GameState", player_idx: int,
                               exclude_instance_id: Optional[int] = None
                               ) -> list:
        """The permanents `player_idx` can sacrifice for mana right now,
        each with the units its ability makes: a mana ability that
        sacrifices its source and whose own cost can be paid during a
        payment (`_sacrifice_cost_payable`). The one owner of that set: the
        payment path spends exactly these, and every estimate of what a
        player can pay counts exactly these. A permanent the tap-for-mana
        path already counts is not counted twice, and none pays for its own
        ability."""
        out = []
        for perm in game.players[player_idx].battlefield:
            units = perm.template.sacrifice_mana_units
            if not units or perm.instance_id == exclude_instance_id \
                    or perm.template.mana_units or perm.template.produces_mana:
                continue
            ability = _sacrifice_mana_ability(perm)
            if ability is None or not _sacrifice_cost_payable(perm, ability):
                continue
            out.append((perm, [list(u) for u in units]))
        return out

    @staticmethod
    def tap_lands_for_mana(game: "GameState", player_idx: int,
                           cost: ManaCost,
                           card_name: str = None,
                           held_instant_colors: Optional[Set[str]] = None,
                           exclude_instance_id: Optional[int] = None
                           ) -> bool:
        """Pay a mana cost, guarding against activation re-entry.

        `game._paying_mana` is raised for the whole payment. CR 605.3 allows
        only MANA abilities to be activated while paying a cost, and this
        engine produces mana through the payment path itself — so any other
        activation attempted mid-payment is refused outright by
        `ActivationManager.can_activate`. That makes the recursion edge
        non-existent rather than merely bounded: a self-untapping mana source
        cannot spin here, because the loop that would spin never opens.

        `exclude_instance_id` keeps a permanent from being tapped to pay for
        its OWN activated ability (the generic form of a guard `game_runner`
        already hand-rolls for granted abilities).
        """
        game._paying_mana = getattr(game, '_paying_mana', 0) + 1
        try:
            return ManaPayment._tap_lands_for_mana_inner(
                game, player_idx, cost, card_name, held_instant_colors,
                exclude_instance_id)
        finally:
            game._paying_mana -= 1

    @staticmethod
    def _run_loop_shortcut(game: "GameState", player_idx: int,
                           cost: ManaCost,
                           exclude_instance_id: Optional[int]) -> None:
        """Fill the mana pool from unbounded engines by exactly the cost's
        shortfall (bounded by LOOP_SHORTCUT_MANA per engine). No-op when
        every other source already covers the cost."""
        from .activation import ActivationManager
        from .constants import LOOP_SHORTCUT_MANA
        player = game.players[player_idx]
        engines = [e for e in ActivationManager.unbounded_mana_engines(
                       game, player_idx)
                   if e.instance_id != exclude_instance_id]
        if not engines:
            return
        other_units = sum(
            len(ManaPayment.land_mana_units(game, player_idx, src))
            for src in player.untapped_mana_sources
            if src.instance_id != exclude_instance_id and src not in engines)
        shortfall = cost.cmc - other_units - player.mana_pool.total()
        if shortfall <= 0:
            return
        needed = cost.to_dict()
        for engine in engines:
            if shortfall <= 0:
                break
            units = ManaPayment.land_mana_units(game, player_idx, engine)
            if not units:
                continue
            ability = ActivationManager.free_self_untap_ability(game, engine)
            iters = 0
            produced = 0
            while shortfall > 0 and iters < LOOP_SHORTCUT_MANA:
                if engine.tapped:
                    # Pay the untap cost through the funnel, then untap.
                    if ability.cost.put_counter_kind is not None:
                        engine.adjust_counters(ability.cost.put_counter_kind,
                                               ability.cost.put_counter_amount)
                    engine.untap()
                engine.tap()
                for options in units:
                    # Prefer a colour the cost still asks for; else the
                    # first printed option.
                    color = next((c for c in options
                                  if needed.get(c, 0) > 0), options[0])
                    if needed.get(color, 0) > 0:
                        needed[color] -= 1
                    player.mana_pool.add(color, 1)
                    produced += 1
                    shortfall -= 1
                iters += 1
            if iters:
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: loops "
                    f"{engine.name} ×{iters} (CR 726 shortcut) — "
                    f"{produced} mana")

    @staticmethod
    def _apply_cost_reduction(cost: ManaCost, reduction: int) -> ManaCost:
        """Apply a generic cost reduction (CR 601.2f): shrink the GENERIC
        component only, leaving every coloured, colourless and hybrid pip
        untouched. Owns the fold so the reduction-audit invariant can
        recompute the surviving non-generic pips independently."""
        from .mana import ManaCost as MC
        return MC(
            white=cost.white, blue=cost.blue, black=cost.black,
            red=cost.red, green=cost.green, colorless=cost.colorless,
            generic=max(0, cost.generic - reduction), hybrid=list(cost.hybrid),
        )

    @staticmethod
    def _record_paid(game: "GameState", units: int) -> None:
        """Count the mana a payment took from a pool (CR 601.2h): the
        monotonic total a cast's mana spent is read from."""
        if units > 0:
            game.mana_paid_total = getattr(game, "mana_paid_total", 0) + units

    @staticmethod
    def _tap_lands_for_mana_inner(game: "GameState", player_idx: int,
                                  cost: ManaCost,
                                  card_name: str = None,
                                  held_instant_colors: Optional[Set[str]] = None,
                                  exclude_instance_id: Optional[int] = None
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
        # The zones a spell is cast from: hand, graveyard (flashback,
        # escape) and exile (a permission to cast it, warp; CR 601.2a) --
        # including a permitted card in another player's exile, where an
        # exiled card stays (CR 400.3). A reduction applies wherever the
        # spell is cast from (CR 601.2f), so the payment reads the same
        # zones `can_cast` admits.
        from . import rules_query as _rq
        cast_zones = (game.players[player_idx].hand,
                      game.players[player_idx].graveyard,
                      game.players[player_idx].exile,
                      _rq.permitted_cards(game, player_idx))
        if card_name:
            for c in [c for z in cast_zones for c in z]:
                if c.template.name == card_name and c.template.domain_reduction > 0:
                    domain = ManaPayment.count_domain(game, player_idx)
                    reduction += c.template.domain_reduction * domain
                    break
        # Ruby Medallion and Affinity cost reductions
        player = game.players[player_idx]
        has_improvise = False
        if card_name:
            # The card in the zone it is cast from (`cast_zones` above).
            for c in [c for z in cast_zones for c in z]:
                if c.template.name == card_name:
                    # Generic cost reduction from permanents
                    from .oracle_resolver import (count_cost_reducers,
                                                  self_cost_reduction)
                    from . import rules_query
                    reduction += rules_query.cost_delta(game, player_idx, c.template)
                    # Self-scaling own-cost reduction (per-turn discard/
                    # cycle count, graveyard card types, ...).
                    reduction += self_cost_reduction(game, player_idx, c.template)
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
            # CR 601.2f: a reduction shrinks the GENERIC component only.
            # Every other pip — coloured, colourless, hybrid — survives
            # untouched (hybrid pips used to be folded into `generic`
            # upstream, which is how two reducers made {1}{R/G} free).
            _ng_before = cost.non_generic_pips
            cost = ManaPayment._apply_cost_reduction(cost, reduction)
            # Audit (observation-only, CR 601.2f): the reduction may not have
            # eaten a coloured/colourless/hybrid pip. Recomputed from the new
            # cost's own pip fields, independently of the fold above.
            from .rules_audit import check as _audit_check
            _audit_check("601.2f/reduction_pips_preserved",
                         cost.non_generic_pips == _ng_before,
                         "a cost reduction shrank a non-generic pip",
                         game=game)

        # Improvise payment (Track H handoff): tap untapped non-land
        # artifacts to pay {1} of generic each — but only for the
        # shortfall that lands + pool cannot cover, so artifact
        # creatures / mana rocks stay untapped whenever lands suffice.
        if has_improvise and cost.generic > 0:
            # a sacrifice source is spent after an artifact taps for improvise
            capacity = (player.untapped_mana_capacity(sacrifice=False)
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

        # One-shot sacrifice-for-mana sources (CR 605.1a): Treasure, Eldrazi
        # Spawn and Scion, Lotus-style artifacts. Each is a mana ability its
        # controller may activate while paying (CR 605.3a), so its units join
        # the solver's unit pool below -- as a coloured pip's source too --
        # but LAST: spending one CONSUMES it, so a repeatable source pays
        # whenever one can, and a deck never eats its own ramp to pay a cost
        # its lands afford. A source is one whose own cost can be paid now
        # (`sacrifice_mana_sources`): a tapped Treasure cannot pay its {T}.
        sac_sources = ManaPayment.sacrifice_mana_sources(
            game, player_idx, exclude_instance_id)
        # The sources whose units the solver used, each with the colours it
        # added, whether it was tapped for its {T}, and whether it was tapped
        # before the payment began: performed only on a success path
        # (`_commit_sacrifices`), abandoned with the mana they produced on a
        # failure path (`_refund_sacrifices`). Destroying a card for a spell
        # that then fails to cast is strictly worse than declining the cast,
        # and a card returning from the graveyard would be a new object (CR
        # 400.7) that re-fires its ETB.
        pending_sacrifice = []

        def _commit_sacrifices():
            """Perform the deferred sacrifices — payment succeeded."""
            _audit_sacrifices(game, pending_sacrifice)
            for _perm, _added, _tapped, _was in pending_sacrifice:
                # A creature sacrificed for mana DIES (CR 700.4): it goes
                # through the death funnel so its own dies clause and every
                # observer ("whenever a creature dies") fire — the bare zone
                # move dispatches no triggers, and a Spawn sacrificed for
                # mana never counted as a creature dying. Non-creatures
                # (Treasure) take the zone move.
                if getattr(_perm, 'effective_is_creature', False):
                    game._creature_dies(_perm)
                else:
                    game.zone_mgr.move_card_to_graveyard(
                        game, _perm, cause=f"sacrificed for mana ({card_name})")
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"sacrifice {_perm.name} for mana ({card_name})")
            pending_sacrifice.clear()

        def _refund_sacrifices():
            """Payment failed: un-add the mana, leave the permanents alone."""
            for _perm, _added, _tapped, _was in pending_sacrifice:
                for _colour in _added:
                    player.mana_pool.remove(_colour, 1)
                if _tapped:
                    _perm.tapped = False
            pending_sacrifice.clear()

        # CR 726.4 loop shortcut. An unbounded mana engine (a mana source
        # whose self-untap cost depletes nothing — see
        # `ActivationManager.unbounded_mana_engines`) is iterated exactly
        # as many times as this cost falls short of every other source:
        # untap (paying the replaced-to-zero counter cost through the
        # counter funnel, so the rules still see each placement), tap for
        # its printed units into the pool, repeat. One log line records the
        # shortcut. The engine ends tapped and so drops out of `untapped`
        # below — no unit is counted twice.
        ManaPayment._run_loop_shortcut(game, player_idx, cost,
                                       exclude_instance_id)

        untapped = [l for l in player.lands if not l.tapped
                    and l.instance_id != exclude_instance_id]

        # Non-land mana sources: mana rocks (Talisman, Mind Stone) and mana
        # creatures (Birds, Llanowar, Devoted Druid). A permanent whose
        # template exposes a plain "{T}: Add" ability (produces_mana /
        # mana_units populated for non-lands, CR 605) is tappable for mana
        # exactly like a land. Creature mana abilities carry the tap symbol,
        # so summoning sickness gates them (CR 302.6 / 605.3a).
        for perm in player.battlefield:
            if perm.tapped or perm.template.is_land:
                continue
            if perm.instance_id == exclude_instance_id:
                continue  # cannot tap a permanent to pay for its own ability
            if not (perm.template.mana_units or perm.template.produces_mana):
                continue
            if (CardType.CREATURE in perm.template.card_types
                    and perm.has_summoning_sickness):
                continue
            untapped.append(perm)

        if not untapped and not sac_sources and player.mana_pool.total() == 0:
            _ok = cost.cmc == 0
            _commit_sacrifices() if _ok else _refund_sacrifices()
            return _ok

        # Check if mana pool already has enough (from rituals)
        if player.mana_pool.can_pay(cost):
            _before = player.mana_pool.total()
            _paid = player.mana_pool.pay(cost)
            ManaPayment._record_paid(game, _before - player.mana_pool.total())
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

        # A creature that produces mana (a mana dork, a creature-land) is a
        # blocker first and a mana source second: among sources that can
        # pay, a non-creature source is tapped first, so the body stays
        # untapped for the opponent's attack. Rules-neutral ordering — it
        # never changes WHETHER a cost is payable, only which source pays.
        from .cards import CardType as _CT

        def _is_creature_source(l) -> int:
            return 1 if _CT.CREATURE in l.template.card_types else 0

        def _sort_key(l):
            lp = _produces(l)
            produces_held = 1 if any(c in _held for c in lp) else 0
            return (produces_held, _is_creature_source(l), len(lp))
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
        #
        # Every pip is an OPTION TUPLE: a fixed colour pip is ("R",); a
        # hybrid pip (CR 107.4e) is its colours, e.g. ("R", "G"); a
        # two-brid pip {2/W} is ("W", "2"), the digit meaning "that much
        # generic instead".  The same MRV walk serves all three.
        pips = []
        for color in ["W", "U", "B", "R", "G", "C"]:
            pips.extend([(color,)] * needed.get(color, 0))
        pips.extend(tuple(p) for p in cost.hybrid)
        generic_needed = needed.get("generic", 0)

        def _digit_option(pip):
            for o in pip:
                if o.isdigit():
                    return int(o)
            return None

        # First, use mana pool for pips (scarcest pip first)
        pool_left = {c: player.mana_pool.get(c)
                     for c in ["W", "U", "B", "R", "G", "C"]}

        def _pool_choices(pip):
            return [o for o in pip if pool_left.get(o, 0) > 0]

        colors_needed_list = []
        for pip in sorted(pips, key=lambda p: len(_pool_choices(p))):
            choices = _pool_choices(pip)
            if choices:
                colour = max(choices, key=lambda o: pool_left[o])
                pool_left[colour] -= 1
            else:
                colors_needed_list.append(pip)

        # E1 (multi-mana lands): the assignable resource is a mana
        # UNIT, not a land — a karoo's single tap yields a {G} unit
        # AND a {U} unit.  Build (land, unit_idx, color_options)
        # triples; assignment consumes units, tapping consumes lands.
        unit_pool = []  # list of [land, unit_idx, options, assigned]
        for land in untapped:
            for ui, options in enumerate(
                    ManaPayment.land_mana_units(game, player_idx, land)):
                unit_pool.append([land, ui, list(options), None])
        # A sacrifice-for-mana source's units are units like any other; it
        # is sacrificed, not tapped, and assigned last (`_is_sacrifice`).
        sac_ids = {id(perm) for perm, _units in sac_sources}
        for perm, units in sac_sources:
            for ui, options in enumerate(units):
                unit_pool.append([perm, ui, list(options), None])

        def _is_sacrifice(source) -> int:
            return 1 if id(source) in sac_ids else 0

        def _unit_matches(pip, options):
            return any(o in options for o in pip)

        # Assign with re-sorting: most constrained pip first each step
        while colors_needed_list:
            # Re-sort by scarcity each step (fixes 4-color dual land issues)
            colors_needed_list.sort(
                key=lambda p: sum(1 for u in unit_pool
                                  if u[3] is None and _unit_matches(p, u[2]))
            )
            pip = colors_needed_list.pop(0)
            # Find least-flexible unassigned unit for this pip. Ties
            # broken by preserving held_instant_colors when supplied —
            # a unit on a land that produces a held color is less
            # preferred (we want to leave that land untapped for the
            # opponent's turn).
            best_unit = None
            best_key = (999, 999)
            best_colour = None
            for unit in unit_pool:
                land, _ui, options, assigned = unit
                if assigned is not None:
                    continue
                if _unit_matches(pip, options):
                    flex = len(options)
                    # Skip the held-preserve penalty if this land is the
                    # only source of the required color — correctness
                    # (must pay the cost) wins over preservation.
                    produces_held = 1 if any(
                        c in _held and c not in pip
                        for c in _produces(land)) else 0
                    key = (_is_sacrifice(land), flex, produces_held,
                           _is_creature_source(land))
                    if key < best_key:
                        best_key = key
                        best_unit = unit
                        best_colour = next(o for o in pip if o in options)
            if best_unit is None:
                # A two-brid pip may still be paid with its generic
                # alternative (CR 107.4e); a colour pip cannot.
                digit = _digit_option(pip)
                if digit is None:
                    _refund_sacrifices()
                    return False
                generic_needed += digit
                continue
            best_unit[3] = best_colour

        # Pay generic
        generic_remaining = generic_needed
        # Use pool first — whatever the pip pass left in it
        pool_total = sum(pool_left.values())

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
            # committed sources' spare units first; then repeatable sources;
            # a sacrifice source last
            on_committed = 0 if id(unit[0]) in committed_lands else \
                1 + _is_sacrifice(unit[0])
            return (on_committed, _is_creature_source(unit[0]), len(unit[2]))

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
            sacrificed = id(land) in sac_ids
            was_tapped = land.tapped
            tapped_for_it = not sacrificed or \
                _sacrifice_mana_ability(land).cost.tap_self
            if tapped_for_it:
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
            if sacrificed:
                # performed once the whole cost is paid (`_commit_sacrifices`)
                pending_sacrifice.append((land, yielded, tapped_for_it,
                                          was_tapped))
                continue
            bonus = cond_bonus_cache.get(id(land), 0)
            if bonus > 0:
                player.mana_pool.add("C", bonus)
            # Pain land: self-damage when tapping for colored mana -- the
            # land deals it to its controller through the damage owner,
            # so it is life lost this turn (CR 120.3a).
            if land.template.tap_damage > 0 and any(
                    c != "C" for c in yielded):
                from .damage import deal_damage
                deal_damage(land, player, land.template.tap_damage)

        _before = player.mana_pool.total()
        ok = player.mana_pool.pay(cost)
        ManaPayment._record_paid(game, _before - player.mana_pool.total())

        # Verbose: log which lands were tapped, and what is left AFTER
        # this cost is paid. Emitted post-payment on purpose: measured
        # before `pay`, the figure still counts the mana earmarked for
        # this very spell, which reads as spare mana the controller does
        # not have (a replay showing "4 mana remaining" while the payer
        # held one untapped land produced a false "unpaid counter-tax"
        # finding in
        # docs/diagnostics/2026-08-27_dimir_overperformance_root_cause.md).
        if getattr(game, 'verbose', False) and tapped_names and card_name:
            # a source this payment sacrifices is mana already spent
            spent = sum(len(perm.template.sacrifice_mana_units or ())
                        for perm, _a, tapped, _w in pending_sacrifice
                        if not tapped)
            remaining_mana = (player.untapped_mana_capacity()
                              + player.mana_pool.total() - spent)
            game.log.append(f'    [Mana] Tap {", ".join(tapped_names)} '
                            f'(paying for {card_name}, {remaining_mana} mana remaining)')

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
