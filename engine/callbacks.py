"""
Callback protocol for engine -> AI decisions.

The engine layer must never import from the AI layer directly.
Instead, GameState calls methods on a GameCallbacks instance,
which the GameRunner wires to the appropriate AI implementations.

Decision channels are uniform per *kind*, never per mechanic.
`decide_optional_cost` handles every "pay X to gain Y" decision
(shock lands, painlands, fetchlands, Phyrexian mana, Sylvan
Library, hybrid mana, channel, kicker-with-life, ...) by routing
oracle-derived `OptionalCost` descriptors through a single AI
seam.  Engine call sites use `engine.optional_costs.offer_optional_costs`
to discover and present these costs — no mechanic-named callbacks.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional, Protocol, Sequence

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.card_database import CardInstance
    from ai.schemas import OptionalCost


class GameCallbacks(Protocol):
    """Protocol that the engine calls for strategic decisions."""

    def decide_optional_cost(
        self, game: GameState, player_idx: int, opt: "OptionalCost"
    ) -> bool:
        """Should this optional cost be paid?

        Uniform entry point for every "pay X to gain Y" decision the
        engine may legally offer.  The AI projects the post-payment
        snapshot via `opt.apply_to_snap` and compares against
        skipping; True means pay.
        """
        ...

    def choose_fetch_target(
        self, game: GameState, player_idx: int, fetch_card: CardInstance,
        library: List[CardInstance], fetch_colors: list
    ) -> Optional[CardInstance]:
        """Which land should a fetch land search for?"""
        ...

    def should_evoke(
        self, game: GameState, player_idx: int, card: CardInstance
    ) -> bool:
        """Should this creature be evoked instead of hardcast?"""
        ...

    def choose_exile_from_hand(
        self, game: GameState, player_idx: int, spell: CardInstance,
        candidates: List[CardInstance]
    ) -> Optional[CardInstance]:
        """Which card does a cost that exiles a card from the caster's
        hand take (an evoke cost, CR 702.74a; "exile a blue card from your
        hand rather than pay this spell's mana cost", CR 118.9)? One of
        `candidates`, or None to decline paying the cost."""
        ...

    def should_exile_instead_of_paying(
        self, game: GameState, player_idx: int, card: CardInstance,
        can_pay_mana: bool
    ) -> bool:
        """Pay this spell's alternative cost that exiles a card from hand
        (CR 118.9) rather than its mana cost? `can_pay_mana` says whether
        the mana cost could be paid instead."""
        ...

    def should_kick(
        self, game: GameState, player_idx: int, card: CardInstance
    ) -> int:
        """How many times to kick this spell as it is cast (CR 702.33):
        0 = don't kick, 1 = kicker, N = multikicker. The engine clamps to
        the mana available after the base cost."""
        ...

    def should_dash(
        self, game: GameState, player_idx: int, card: CardInstance,
        can_normal: bool, can_dash: bool
    ) -> bool:
        """Should this creature be dashed instead of hardcast?"""
        ...

    def choose_discard(
        self, game: GameState, player_idx: int,
        hand: List[CardInstance], self_discard: bool
    ) -> CardInstance:
        """Pick the best card to discard.

        self_discard=True: player chose to discard (Faithful Mending).
        self_discard=False: opponent forced discard (Thoughtseize).
        """
        ...

    def decide_offered_cast(
        self, game: GameState, player_idx: int, card: CardInstance
    ) -> bool:
        """Accept an engine-offered "you may cast this" opportunity?

        Uniform per KIND: any resolving effect that lets a player cast a
        specific card now (madness reflexive trigger, rebound, "you may
        cast it without paying its mana cost", …) routes here. The
        engine has already verified the cast is legal and payable
        (`can_cast`); the callback only decides whether to take it.
        Declining means the card goes wherever the effect says it goes
        when not cast (madness: graveyard).
        """
        ...

    def choose_sacrifice(
        self, game: GameState, player_idx: int,
        legal: List[CardInstance],
    ) -> Optional[CardInstance]:
        """Pick which permanent pays a sacrifice cost.

        The engine has already enumerated `legal` — the permanents that
        satisfy the cost's type requirement (CR 601.2h), source excluded
        when the cost says "another". The callback chooses WHICH one is
        given up; returning one of `legal` is the contract. Returning
        None refuses the activation (the engine treats an unmade choice
        as an unpaid cost).
        """
        ...

    def choose_artifact_tutor_target(
        self, game: GameState, player_idx: int,
        eligible: List[CardInstance],
    ) -> Optional[CardInstance]:
        """Pick the best artifact target from the engine-narrowed list.

        The engine has already filtered the controller's library to
        cards that satisfy the rule (artifact, mana_value <= 1, no
        duplicate-legendary collision with the battlefield). The
        callback chooses *which* eligible target serves the deck plan
        best given current board state.

        Returns one of the elements of `eligible`, or None if the
        list is empty (engine handles the no-target case).
        """
        ...

    def choose_mana_color(
        self, game: GameState, player_idx: int, source: CardInstance,
        options: List[str],
    ) -> str:
        """Which colour does an "as this enters, choose a color" permanent pick?

        The engine has already enumerated the legal `options`; the callback
        picks one. Uniform per KIND — any permanent whose entry choice is a
        colour routes here, so no mechanic-named callback is needed for the
        next printing that words it the same way. Returning something outside
        `options` falls back to the engine default.
        """
        ...

    def choose_tutor_target(
        self, game: GameState, player_idx: int, source: CardInstance,
        eligible: List[CardInstance],
    ) -> Optional[CardInstance]:
        """Which card should an activated library tutor deliver?

        The engine has already narrowed the library to the cards that
        satisfy the ability's parsed search constraint (types, subtypes,
        colors, mana-value bound at the chosen X). The callback chooses
        WHICH one serves the plan best; returning one of `eligible` is
        the contract. Returning None (or a non-member) falls back to the
        engine default — highest mana value within the constraint.
        """
        ...

    def choose_trigger_targets(
        self, game: GameState, player_idx: int, source: CardInstance,
        spec: Any, req: Any, players: List[int],
        permanents: List[CardInstance],
    ) -> List[Any]:
        """Which targets does a triggered ability take for one slot as it
        is put on the stack (CR 603.3d)?

        Uniform per KIND: any trigger a carrier puts on the stack with a
        target routes here. The engine has already narrowed the legal
        choices (`target_solver.legal_slot_choices`): `players` (indices)
        and `permanents`. `spec` is the typed EffectSpec the slot belongs
        to, `req` its requirement. Return up to `req.count_max` of them;
        the engine keeps only legal, distinct members and fills a required
        target the answer leaves short (`default_trigger_targets`).
        """
        ...

    # ── Resolution-time choices (design doc 2026-09-29, A35) ──────────
    # One channel per KIND of choice a resolving ability asks of its
    # controller. `ctx` is the resolution context, `spec` the typed
    # EffectSpec asking. The effect dispatcher asks `choose_optional_effect`
    # and its card-flow executors ask `choose_cards` (unit E, enter
    # triggers); `choose_amount` and `choose_division` have no caller yet
    # and their default raises rather than guessing an answer
    # (tests/test_effect_resolver_sequencing.py pins the callers).

    def choose_optional_effect(self, ctx: Any, spec: Any) -> bool:
        """Perform this optional ("you may") effect? True = perform."""
        raise NotImplementedError

    def choose_amount(self, ctx: Any, spec: Any, lo: int, hi: int,
                      remaining_specs: Sequence[Any]) -> int:
        """Pick a variable amount in [lo, hi] ("any number", "up to N",
        pay-X-at-resolution). `remaining_specs` are the specs that resolve
        after this one, so the answer can see what the amount feeds."""
        raise NotImplementedError

    def choose_cards(self, ctx: Any, spec: Any, pool: Sequence[Any],
                     n: int) -> List[Any]:
        """Pick up to `n` cards out of `pool`, the engine-enumerated legal
        choices. Returning a non-member, a duplicate or more than `n` is
        outside the contract: the engine keeps only the distinct members,
        at most `n`, and a required choice the answer leaves short is
        filled by `default_card_pick`."""
        raise NotImplementedError

    def choose_division(self, ctx: Any, spec: Any, slots: Sequence[Any],
                        total: int) -> List[int]:
        """Divide `total` among `slots` (CR 601.2d: fixed as targets are
        chosen); one non-negative share per slot, summing to `total`."""
        raise NotImplementedError


class DefaultCallbacks:
    """Safe defaults: always tapped, first legal target, no evoke, no dash."""

    def decide_optional_cost(
        self, game: GameState, player_idx: int, opt
    ) -> bool:
        return False

    def choose_fetch_target(
        self, game: GameState, player_idx: int, fetch_card: CardInstance,
        library: List[CardInstance], fetch_colors: list
    ) -> Optional[CardInstance]:
        fetchable = [c for c in library if c.template.is_land]
        return fetchable[0] if fetchable else None

    def should_evoke(
        self, game: GameState, player_idx: int, card: CardInstance
    ) -> bool:
        return False

    def choose_exile_from_hand(
        self, game: GameState, player_idx: int, spell: CardInstance,
        candidates: List[CardInstance]
    ) -> Optional[CardInstance]:
        """Default: the first card the cost may take, in hand order."""
        return candidates[0] if candidates else None

    def should_exile_instead_of_paying(
        self, game: GameState, player_idx: int, card: CardInstance,
        can_pay_mana: bool
    ) -> bool:
        """Default: only when the mana cost cannot be paid."""
        return not can_pay_mana

    def should_kick(
        self, game: GameState, player_idx: int, card: CardInstance
    ) -> int:
        return 0

    def should_dash(
        self, game: GameState, player_idx: int, card: CardInstance,
        can_normal: bool, can_dash: bool
    ) -> bool:
        return can_dash and not can_normal

    def choose_discard(
        self, game: GameState, player_idx: int,
        hand: List[CardInstance], self_discard: bool
    ) -> CardInstance:
        """Default: discard highest-CMC card (least-mana-efficient to
        re-cast). This matches the legacy non-AI forced-discard
        behaviour at the pre-refactor GameState._force_discard (sort
        by CMC desc, take head). AI callback implementations should
        override this with a proper discard-scoring strategy."""
        return max(hand, key=lambda c: c.template.cmc or 0)

    def decide_offered_cast(
        self, game: GameState, player_idx: int, card: CardInstance
    ) -> bool:
        """Default: take every legal offered cast. The engine has
        already checked payability; the alternative to casting is
        losing the card (madness → graveyard), so accepting is the
        rules-neutral choice. AI implementations may decline when a
        plan wants the card elsewhere."""
        return True

    def choose_sacrifice(
        self, game: GameState, player_idx: int,
        legal: List[CardInstance],
    ) -> Optional[CardInstance]:
        """Default: lowest-value legal victim, from printed card data only
        (least board power, then least mana investment). AI callback
        implementations override with position-aware scoring."""
        if not legal:
            return None
        return min(legal, key=lambda c: (
            (c.power or 0) if c.effective_is_creature else 0,
            c.template.cmc or 0))

    def choose_artifact_tutor_target(
        self, game: GameState, player_idx: int,
        eligible: List[CardInstance],
    ) -> Optional[CardInstance]:
        """Default heuristic — oracle-driven, no card names.

        Phase 1D ranking:
          1. Mana producers (oracle has "{T}: Add" or "add one mana")
             come first — acceleration is universally valuable.
          2. Equipment with artifact-scaling (oracle has "+N/+M for
             each artifact you control" + an equip cost) come next.
          3. Otherwise the highest-CMC eligible artifact.

        AI callback implementations may override with state-aware
        scoring (e.g. demote redundant equipment when no creatures
        are deployed, demote a second mana rock when on-curve mana
        is already sufficient).
        """
        if not eligible:
            return None

        def _rank(c: CardInstance) -> tuple:
            is_mana = bool(c.template.produces_mana)
            is_artifact_scaler = (
                c.template.has_artifact_synergy
                and c.template.equip_cost is not None
            )
            cmc = c.template.cmc or 0
            return (is_mana, is_artifact_scaler, cmc)

        return max(eligible, key=_rank)

    def choose_mana_color(
        self, game: GameState, player_idx: int, source: CardInstance,
        options: List[str],
    ) -> str:
        """Default: the colour the controller's own cards demand most.

        Printed card data only, in keeping with the other defaults here — the
        coloured pips of every card the player owns are counted, and the most
        demanded colour among `options` wins. That is a deterministic reading
        of the deck, not a strategic read of the board; AI implementations
        override it with `analyze_mana_needs`, which additionally knows which
        colours the battlefield already covers.

        Ties break on the option order the engine supplied, so the choice is
        reproducible for a given seed.
        """
        if not options:
            return ""
        player = game.players[player_idx]
        demand = {c: 0 for c in options}
        color_attrs = {"W": "white", "U": "blue", "B": "black",
                       "R": "red", "G": "green"}
        for zone in (player.hand, player.library, player.battlefield):
            for card in zone:
                cost = getattr(card.template, "mana_cost", None)
                if cost is None:
                    continue
                for code in demand:
                    demand[code] += getattr(cost, color_attrs.get(code, ""), 0)
        return max(options, key=lambda c: (demand[c], -options.index(c)))

    def choose_tutor_target(
        self, game: GameState, player_idx: int, source: CardInstance,
        eligible: List[CardInstance],
    ) -> Optional[CardInstance]:
        """Default: highest mana value within the constraint, P/T
        tie-break — the engine's own delivery ranking (see
        `engine.activated_effects.default_tutor_rank`). AI callback
        implementations override with plan-aware scoring."""
        from .activated_effects import default_tutor_rank
        if not eligible:
            return None
        return max(eligible, key=default_tutor_rank)

    def choose_trigger_targets(self, game, player_idx, source, spec, req,
                               players, permanents):
        """Default: `default_trigger_targets`, printed data only."""
        return default_trigger_targets(player_idx, req, players, permanents)

    # Resolution-time choices (A35).
    def choose_optional_effect(self, ctx, spec) -> bool:
        """Default: perform. The controller takes what the text offers --
        what every legacy resolver did with the optional effects it
        resolved. AI implementations decline where performing would hurt."""
        return True

    def choose_amount(self, ctx, spec, lo, hi, remaining_specs) -> int:
        raise NotImplementedError

    def choose_cards(self, ctx, spec, pool, n):
        """Default: `default_card_pick`, printed card data only."""
        return default_card_pick(pool, n)

    def choose_division(self, ctx, spec, slots, total):
        raise NotImplementedError


def default_trigger_targets(player_idx: int, req: Any, players: Sequence[int],
                            permanents: Sequence[Any]) -> List[Any]:
    """The engine's default trigger target pick for one slot: the
    opponent's face when the slot admits that player (the legacy owners'
    rule for a player-admitting slot, A36), then the opponents'
    permanents, then the controller's own, each in battlefield order, up
    to the slot's count (at least one). No scoring: a deterministic
    reading, never a strategic one."""
    n = max(1, int(getattr(req, "count_max", 1) or 1))
    opp = [p for p in players if p != player_idx]
    order = opp + [c for c in permanents if c.controller != player_idx] + \
        [c for c in permanents if c.controller == player_idx] + \
        [p for p in players if p == player_idx]
    return order[:n]


def default_card_pick(pool: Sequence[Any], n: int) -> List[Any]:
    """The engine's default card choice: up to `n` of `pool` by its own
    delivery ranking (`activated_effects.default_tutor_rank`, the ranking
    `DefaultCallbacks.choose_tutor_target` delivers by), highest first, ties
    in pool order. Printed card data only -- a deterministic reading, never
    a strategic one."""
    from .activated_effects import default_tutor_rank
    if n <= 0:
        return []
    return sorted(pool, key=default_tutor_rank, reverse=True)[:n]
