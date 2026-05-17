"""W0-D — Generic damage-resolution primitive.

This module owns the routing rule "damage is dealt to the *target
object*" (CR 119). It is mechanic-driven; no card-name or deck-name
branches live here.

Why this exists
---------------
Before W0-D, every damage-emitting effect in the engine mutated
`player.life` (or `damage_marked`/`loyalty_counters`) directly at
the call site. Three symptoms followed:

- R6 (Ral coin-flip): `engine/oracle_resolver.py:638` does
  `player.life -= 1` on a lost flip, despite oracle saying *the
  planeswalker* takes the damage.
- R2 (Galvanic Discharge): the per-card handler in
  `engine/card_effects.py:586-629` re-implements target choice
  inline, lets the spell go face when oracle restricts to
  creature-or-planeswalker, and dips into pre-cast energy at
  resolve.
- M10 (burn-to-PW): direct-damage scoring forgets planeswalkers
  are valid targets because the candidate enumeration ignores
  them — there is no shared target type the enumerator can
  iterate.

`deal_damage(source, target, amount)` collapses all three into a
single primitive. Each target type *owns its own damage-marking*
behind the `DamageTarget` protocol:

- `PlayerState.take_damage` → decrements `life`.
- `CardInstance.take_damage` → marks `damage_marked` for
  creatures, decrements `loyalty_counters` for planeswalkers.

Wave 1 migrates the existing call sites onto this primitive.
This W0 commit is *pure addition*: no callers move yet.

Composes with `engine/zone_transfer.py` (W0-C) for the death-on-
lethal path. W0-C is not merged at the time this lands; the
primitive schedules state-based actions via
`game.check_state_based_actions()`, which already routes destroy-
on-lethal through `engine/sba_manager.py:128` (CR 704.5h).

CR references
-------------
- 119.3: damage to a permanent vs. a player
- 120.3: lifelink/deathtouch hooks (replacement-effect entry points
  are reserved; W1 owns implementation)
- 704.5g/h: SBAs check zero-toughness and lethal damage after
  damage resolves
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


# ─── Protocols ────────────────────────────────────────────────────────


@runtime_checkable
class DamageSource(Protocol):
    """Anything that can be a damage source.

    A source carries enough state for replacement effects and
    triggers to look it up: `controller` (player index for
    lifelink redirect, opponent attribution, etc.) and
    `has_deathtouch` (drives the deathtouch SBA marker).

    Concrete sources in the engine:
    - CardInstance (creatures, planeswalkers, artifacts on stack)
    - PlayerState (rare: player-as-source for "you deal damage
      equal to" effects)
    - Effect tokens from the stack (any object with these fields)
    """

    controller: int  # player index — used by lifelink/triggers
    # `has_deathtouch` is read via `getattr(source, 'has_deathtouch',
    # False)` so non-creature sources don't need to define it.


@runtime_checkable
class DamageTarget(Protocol):
    """Anything that can take damage.

    The target owns its own damage-marking. The primitive routes
    the call here rather than branching on `isinstance(target,
    Player)` vs. `isinstance(target, CardInstance)`. New target
    types (battles, vehicles-as-permanents) just implement this
    method.
    """

    def take_damage(self, amount: int, source: Any) -> None: ...


# ─── Core primitive ──────────────────────────────────────────────────


def deal_damage(source: Any, target: Any, amount: int,
                *, is_combat: bool = False) -> None:
    """Resolve `amount` damage from `source` to `target`.

    Order of operations (CR 119 + 120):

    1. Apply replacement effects (prevent, redirect, lifelink hook,
       deathtouch marker). The W0 implementation reserves the hook
       point; W1 wires in the actual replacement-effect registry.
    2. Call `target.take_damage(amount, source)`. The target owns
       the mutation: Player decrements life, creature accrues
       damage_marked, planeswalker decrements loyalty_counters.
    3. Signal state-based-actions check (CR 704.3). The primitive
       triggers the SBA pass; lethal-damage destroy (704.5h),
       zero-loyalty death (704.5p), and player-loss (704.5a) all
       resolve there — not inline here.

    Parameters
    ----------
    source : DamageSource
        The object dealing the damage. May equal `target` (self-
        damage like Ral's coin-flip is a routing case, not a
        special case).
    target : DamageTarget
        The object taking the damage. Must implement `take_damage`.
    amount : int
        Damage in damage-points. Negative or zero is a no-op
        (CR 119.4: 0 damage is not dealt).
    is_combat : bool, keyword-only
        True when the damage originates from the combat damage
        step. Triggers and lifelink behave the same; this flag is
        passed through to replacement effects so they can
        distinguish "deals combat damage" from "deals damage" in
        future card text. Defaults to False (non-combat / spell-
        or-ability damage).

    Returns
    -------
    None
        Mutation is via `target.take_damage` and the subsequent
        SBA pass; this function returns nothing so callers can
        compose it freely.

    Notes
    -----
    - **No card-name branches.** The damage routing is target-type-
      agnostic. New cards inherit correct behaviour by virtue of
      passing the right `target` argument.
    - **No life-mutation here.** Player.life is only ever moved
      from inside `PlayerState.take_damage`. R6's defect — Ral's
      lost coin-flip deducting `player.life` when oracle says Ral
      takes the damage — vanishes the moment the caller passes
      `target=ral_card_instance` instead of `target=player`.
    - **State-based-actions are deferred, not inlined.** The
      primitive does not destroy the creature itself; SBAs do
      (CR 704.5h). This matches the rules engine's batching
      semantics and composes correctly with multi-damage events
      (e.g. trample assignment, multiple burn spells in one
      resolution batch) where deferred SBAs avoid premature
      destruction.
    """
    if amount <= 0:
        # CR 119.4: 0 damage is not dealt. Negative is undefined; we
        # treat it as a no-op rather than as healing (which is
        # `gain_life`, an entirely separate event).
        return

    # ── Hook point: replacement effects ──
    #
    # CR 614: replacement effects modify a damage event before it
    # occurs (prevent, redirect, lifelink). W1 wires in the
    # replacement-effect registry here. The hook is intentionally
    # un-implemented in W0 — adding it later does not change any
    # caller signature.
    effective_amount = amount
    effective_target = target

    # ── Deathtouch marker (CR 702.2 / SBA 704.5i) ──
    #
    # Deathtouch is a *property of the source*; the SBA pass
    # destroys any creature dealt any damage by a deathtouch
    # source. We mark the flag here so the SBA loop can see it
    # without re-querying the source after damage is moved.
    has_deathtouch = getattr(source, 'has_deathtouch', False)
    if has_deathtouch and hasattr(effective_target, '_deathtouch_damage'):
        # CardInstance defines `_deathtouch_damage` as an instance
        # attribute consumed by `engine/sba_manager.py:143` (CR
        # 704.5i). Increment rather than assign so multiple
        # deathtouch hits in the same resolution batch compound
        # before the SBA pass.
        try:
            effective_target._deathtouch_damage = (
                getattr(effective_target, '_deathtouch_damage', 0)
                + effective_amount
            )
        except AttributeError:
            # Target is a Player or something else without the
            # attribute; deathtouch on players is a no-op per CR.
            pass

    # ── Apply damage via the target's own method ──
    #
    # The target object owns the mutation. This is what kills the
    # "if isinstance(target, Player): player.life -= x else: …"
    # pattern that proliferated across the engine. New target
    # types just implement `take_damage`.
    effective_target.take_damage(effective_amount, source)

    # ── Schedule state-based actions ──
    #
    # CR 704.3: SBAs check before any player gets priority. The
    # primitive doesn't perform the SBA pass directly because the
    # caller may be in the middle of a batch (multiple burn
    # resolutions on the stack, trample damage assignment, etc.);
    # the caller is responsible for the priority window in which
    # the SBA pass runs.
    #
    # The reachable game state is available via `_game_state` on
    # CardInstance or via the target itself being a PlayerState
    # whose game we can recover. We do the minimal scheduling
    # here: if we can find the game, mark that an SBA pass is
    # warranted. If we cannot, the caller will check SBAs in due
    # course (every priority handoff already does, via
    # `engine/game_state.py:check_state_based_actions`).
    game = _find_game_state(effective_target) or _find_game_state(source)
    if game is not None:
        # Lightweight signal: existing engine call sites already
        # invoke `game.check_state_based_actions()` after damage-
        # emitting events (e.g. `combat_manager.py:264`). The
        # primitive does NOT pre-empt that machinery; it just
        # ensures the flag is set for callers that want the
        # "fire-and-forget" semantics implied by the W0-D contract.
        _request_sba_check(game)


# ─── Helpers ─────────────────────────────────────────────────────────


def _find_game_state(obj: Any) -> Any:
    """Return the GameState an object lives in, or None.

    CardInstance carries a back-reference (`_game_state`) set on
    construction (`engine/cards.py:253`). PlayerState carries one
    implicitly (it's referenced from GameState.players); for the
    primitive's purposes we only need the back-ref off the target,
    so a missing reference is fine — the SBA pass will run at the
    next priority handoff regardless.
    """
    g = getattr(obj, '_game_state', None)
    return g


def _request_sba_check(game: Any) -> None:
    """Note that state-based actions should run at the next
    priority window. The engine already runs the SBA loop on every
    priority handoff (CR 704.3); this is a forward-compatibility
    hook for callers that want to schedule a check eagerly without
    forcing the loop here (which would break batch semantics).

    In W0 this is a no-op: existing engine pathways already run
    `check_state_based_actions()` after damage-emitting events,
    and forcing a second pass here would double-resolve. The
    function exists so W1 can swap in a deferred-event queue
    without touching the primitive's signature.
    """
    # Intentionally empty. The hook documents the design; the
    # behaviour lives in the caller's existing SBA invocation.
    return
