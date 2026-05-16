"""W0-C — zone_transfer primitive: one entry point for card-between-zone
movements with a per-kind trigger fan-out registry.

Why this exists
---------------
The audit `docs/history/audits/2026-05-16_5panel_bo3_audit.md` (finding
M1+R1) identifies a structural defect: `engine/game_state.py:draw_cards`
is the single drain for **both** real CR-121 draws and impulse-style
reveals (Reckless Impulse / Wrenn's Resolve / Glimpse the Impossible,
which exile-with-may-play). Routing impulse-reveals through `draw_cards`
fires "whenever you draw" / "whenever an opponent draws" triggers in
violation of CR 121.1c, and produced the storm_vs_dimir G1T4 self-kill
(Storm 10→0 from its own Bowmasters/Sheoldred triggers).

The structural fix: every card-between-zone movement names a
`TransferKind`. Each kind has its own registered fan-out. DRAW fires
"whenever you draw"; IMPULSE_REVEAL does not. ETB is uniform across
permanent types (creature, land, artifact, enchantment, PW) — the
audit's R3 finding.

Scope of this commit (W0-C, pure addition)
------------------------------------------
This module exists; callers do not yet use it. Wave 1a-1 will migrate
`engine/oracle_resolver.py:431-471` (the impulse-draw approximation)
to call `transfer(..., kind=IMPULSE_REVEAL, ...)`. Wave 1a-3 will
migrate land-ETB. Wave 2 will delete the ad-hoc trigger fan-out inside
`engine/game_state.py:draw_cards` and route the real-draw path through
`transfer(..., kind=DRAW, ...)`.

Design notes (abstraction contract)
-----------------------------------
* The fan-out is a **dict[TransferKind, list[Callable]]**, not an
  if/elif chain. New kinds register by extending the dict, never by
  branching on the value.
* The fan-out functions delegate to existing oracle-driven detectors
  on the battlefield (the same loop `draw_cards` already runs). They
  do not introduce new oracle-string matches.
* Unknown kind raises `ValueError`. Silent fallthrough was what
  allowed the impulse-draw bug to live; the primitive is loud about
  contracts.
"""
from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState
    from .game_state import PlayerState


# ─── TransferKind ──────────────────────────────────────────────────


class TransferKind(Enum):
    """Names the *kind* of zone transfer so the dispatch table can
    pick the correct trigger fan-out.

    The set is intentionally minimal — exactly the categories the
    audit's findings identified as needing distinct fan-outs. New
    kinds (Surveil, Madness, Adventure-into-exile, …) extend the
    enum and register their fan-out in `_TRIGGER_FANOUT` below.
    """
    DRAW = "draw"
    """Library top → hand. Fires 'whenever you draw' / 'whenever an
    opponent draws' triggers. CR 121.1."""

    IMPULSE_REVEAL = "impulse_reveal"
    """Library top → exile (face-up, may play this turn). Reckless
    Impulse / Wrenn's Resolve / Glimpse the Impossible pattern. CR
    121.1c — NOT a draw event; does NOT fire draw triggers."""

    EXILE_AND_MAY_PLAY = "exile_and_may_play"
    """Any zone → exile (face-up, may play). Wish targets, Ragavan
    treasure-cast, cascade-revealed cards. Distinct from
    IMPULSE_REVEAL only in source zone — same trigger profile (no
    draw triggers)."""

    ETB = "etb"
    """Any → battlefield. Fires ETB triggers via the existing
    `EFFECT_REGISTRY` (timing=ETB). Uniform across permanent types —
    a land's ETB fan-out is the same primitive as a creature's. The
    audit's R3 finding rests on this uniformity."""

    LTB = "ltb"
    """Battlefield → graveyard/exile/hand/library. 'Leaves the
    battlefield' triggers. Sub-destination is encoded in
    `dst_zone`; fan-out reads the destination to dispatch
    death-vs-bounce-vs-exile triggers via the registry."""

    GY_DISCARD = "gy_discard"
    """Hand → graveyard via discard. Fires discard triggers
    ('whenever you discard a card'), distinct from death triggers."""

    GY_FROM_BATTLEFIELD = "gy_from_battlefield"
    """Battlefield → graveyard (creature death, planeswalker
    sacrifice, etc.). Fires 'dies' / 'put into graveyard from the
    battlefield' triggers — distinct from discard."""


# ─── trigger fan-out implementations ───────────────────────────────


def _is_free_first_draw_of_step(game: "GameState",
                                 player: "PlayerState") -> bool:
    """Predicate: is THIS draw the free first draw of the draw step?

    Per Bowmasters' Oracle ("whenever an opponent draws a card except
    the first one they draw in each of their draw steps"), the
    triggered ability skips the draw step's free draw. The "first draw
    of the draw step" is the one drawn by the turn-based action in
    untap/upkeep/draw (CR 504); the active player's `cards_drawn_this_turn`
    counter reads 1 right after that draw. Higher values mean the
    player has drawn additional cards via spells/abilities; those DO
    trigger Bowmasters.

    Lives here (not as a magic literal `<= 1`) so the rule is named
    once and reused. The existing inline check at
    `engine/game_state.py:260-261` is the duplicate that Wave 1a-1
    (R1+M1-engine) will collapse onto this predicate.
    """
    from .game_state import Phase
    return (game.current_phase == Phase.DRAW
            and player.cards_drawn_this_turn <= FIRST_DRAW_STEP_COUNT)


# CR 504.3 — the turn-based draw-step action draws exactly one card.
# `cards_drawn_this_turn` reads this value after that action; values
# above this represent spell/ability-induced draws which DO trigger
# "whenever you draw" abilities.  Named so the predicate above doesn't
# carry a bare `1`.
FIRST_DRAW_STEP_COUNT = 1


def _fire_on_draw_triggers(game: "GameState", card: "CardInstance",
                           controller: int) -> None:
    """Fan-out for `TransferKind.DRAW`.

    Structural design: each opp permanent whose oracle classifier
    bears `Tag.ON_DRAW_DAMAGE` fires its damage trigger here. The
    amount parse is gated by the classifier tag — we ONLY look for
    `deals N damage` after the tag confirms the card is an
    on-draw-damage source, so a card that incidentally has those
    words elsewhere in its oracle can't false-positive.

    Sheoldred-style on-draw life-gain (own) / life-loss (opp) shapes
    need their own classifier tags (`Tag.ON_OWN_DRAW_LIFE_GAIN`,
    `Tag.ON_OPP_DRAW_LIFE_LOSS`).  Both are declared in
    `ai/oracle_classifier.py` but not yet populated in the smoke
    cache. Until they are, this fan-out only handles ON_DRAW_DAMAGE.
    Wave 1a-1 (R1+M1-engine) will extend coverage when it migrates
    `draw_cards` callers; until then, real-draw triggers continue
    flowing through `engine/game_state.py:draw_cards` (which still
    contains the legacy regex; that's Wave 2's deletion target).

    Assertion contract: if a card carries `Tag.ON_DRAW_DAMAGE` but
    its oracle has no parseable "deals N damage", we raise.  Silent
    fallthrough was a W0-C bug; loud is correct.
    """
    import re

    from ai.oracle_classifier import Tag, tags_for

    opp = game.players[1 - controller]
    player = game.players[controller]
    free_first = _is_free_first_draw_of_step(game, player)

    for c in opp.battlefield:
        if Tag.ON_DRAW_DAMAGE not in tags_for(c.name):
            continue
        if free_first:
            continue  # Bowmasters' "except the first one in draw step"

        oracle = (c.template.oracle_text or "").lower()
        m = re.search(r"deals?\s+(\d+)\s+damage", oracle)
        if m is None:
            raise AssertionError(
                f"{c.name!r} carries Tag.ON_DRAW_DAMAGE but its oracle "
                f"text does not match the 'deals N damage' shape — "
                f"classifier and oracle are out of sync."
            )
        dmg = int(m.group(1))
        player.life -= dmg
        opp.damage_dealt_this_turn += dmg


def _fire_etb_triggers(game: "GameState", card: "CardInstance",
                       controller: int) -> None:
    """Fan-out for `TransferKind.ETB`.

    Delegates to the existing `EFFECT_REGISTRY` (timing=ETB) so the
    decorator-registered handlers (`@EFFECT_REGISTRY.register("X",
    EffectTiming.ETB)`) fire for every permanent type. This is the
    uniformity the audit's R3 finding requires: a land ETB and a
    creature ETB go through the same dispatch.
    """
    from .card_effects import EFFECT_REGISTRY, EffectTiming

    EFFECT_REGISTRY.execute(card.name, EffectTiming.ETB,
                            game, card, controller)


# ─── dispatch table ────────────────────────────────────────────────
#
# `_TRIGGER_FANOUT` IS the dispatch. The whole point of this module is
# that adding a new kind is "extend the enum and add an entry here",
# never "add a branch". Callers wishing to register additional fan-outs
# for a kind (e.g. instrumentation, replay log hooks) use
# `register_fanout(kind, fn)`.

FanoutFn = Callable[["GameState", "CardInstance", int], None]

_TRIGGER_FANOUT: Dict[TransferKind, List[FanoutFn]] = {
    TransferKind.DRAW:                  [_fire_on_draw_triggers],
    TransferKind.IMPULSE_REVEAL:        [],   # CR 121.1c — NOT a draw
    TransferKind.EXILE_AND_MAY_PLAY:    [],   # same trigger profile
    TransferKind.ETB:                   [_fire_etb_triggers],
    # LTB / GY_DISCARD / GY_FROM_BATTLEFIELD are stubs in this wave —
    # Wave 1 migrations will register the appropriate fan-outs. The
    # entries exist (empty list) so the kinds are recognised and
    # `transfer` does not raise; Wave 1 will fill them.
    TransferKind.LTB:                   [],
    TransferKind.GY_DISCARD:            [],
    TransferKind.GY_FROM_BATTLEFIELD:   [],
}


def register_fanout(kind: TransferKind, fn: FanoutFn) -> None:
    """Register an additional fan-out function for `kind`. The
    primary fan-outs ship pre-registered; this hook is for future
    instrumentation (replay logs, telemetry, AI hooks).
    """
    if kind not in _TRIGGER_FANOUT:
        raise ValueError(
            f"unknown TransferKind {kind!r}; extend the enum first"
        )
    _TRIGGER_FANOUT[kind].append(fn)


# ─── zone movement helpers ─────────────────────────────────────────


_ZONE_TO_ATTR = {
    "library": "library",
    "hand": "hand",
    "battlefield": "battlefield",
    "graveyard": "graveyard",
    "exile": "exile",
}


def _remove_from_zone(game: "GameState", card: "CardInstance",
                      controller: int, src_zone: str) -> None:
    """Detach `card` from its source zone list. No-op if not present —
    callers may have already moved it (e.g. cast-from-hand path moved
    the card to the stack before resolution).
    """
    if src_zone == "stack":
        # Stack removal is handled elsewhere; treat as already detached.
        return
    attr = _ZONE_TO_ATTR.get(src_zone)
    if attr is None:
        raise ValueError(f"unknown src_zone {src_zone!r}")
    bucket = getattr(game.players[controller], attr)
    if card in bucket:
        bucket.remove(card)


def _append_to_zone(game: "GameState", card: "CardInstance",
                    controller: int, dst_zone: str) -> None:
    """Append `card` to its destination zone list and update
    `card.zone`. For battlefield destinations, call `enter_battlefield`
    so the card's per-instance ETB bookkeeping fires (summoning sick,
    tapped state for lands, etc.) before the fan-out runs.
    """
    if dst_zone == "stack":
        # Stack placement is owned by the cast pipeline; we only mark
        # the zone string.
        card.zone = "stack"
        return
    attr = _ZONE_TO_ATTR.get(dst_zone)
    if attr is None:
        raise ValueError(f"unknown dst_zone {dst_zone!r}")
    if dst_zone == "battlefield":
        # `enter_battlefield()` sets zone, summoning_sick, and the
        # land-enters-tapped flag. We then append to the battlefield
        # list; the registry-driven ETB fan-out runs afterwards.
        card.enter_battlefield()
    else:
        card.zone = dst_zone
    bucket = getattr(game.players[controller], attr)
    bucket.append(card)


# ─── the primitive ─────────────────────────────────────────────────


def transfer(game: "GameState", card: "CardInstance",
             src_zone: str, dst_zone: str,
             kind: TransferKind,
             *,
             controller: Optional[int] = None) -> None:
    """Move `card` from `src_zone` to `dst_zone` and dispatch the
    trigger fan-out registered for `kind`.

    Parameters
    ----------
    game : GameState
        The active game; players' zone lists are mutated in place.
    card : CardInstance
        The card to move. Its `.zone` attribute is updated.
    src_zone : str
        One of "library" | "hand" | "battlefield" | "graveyard" |
        "exile" | "stack". A `card` not present in `src_zone` is
        silently tolerated (the cast pipeline may have moved it).
    dst_zone : str
        One of the same. For battlefield destinations,
        `CardInstance.enter_battlefield()` is invoked before the
        fan-out runs.
    kind : TransferKind
        Names the transfer kind; the fan-out registered for `kind` is
        invoked in registration order.
    controller : int, optional
        Player index whose zones are touched. Defaults to
        `card.controller`. Required to be a valid index (0 or 1) at
        call time — passing a `card` with no controller raises.

    Raises
    ------
    ValueError
        If `kind` is not a member of `TransferKind` (including the
        sentinel case of an integer or unrelated enum). This is loud
        on purpose — silent fall-through is the failure mode this
        primitive exists to eliminate.
    """
    # Loud failure on unknown kind. The dispatch is by enum identity;
    # callers that pass an int or unrelated value raise here rather
    # than silently skipping the fan-out (the original bug shape).
    if not isinstance(kind, TransferKind):
        raise ValueError(
            f"transfer(kind=) must be a TransferKind, got {type(kind).__name__}"
            f" value={kind!r}"
        )
    if kind not in _TRIGGER_FANOUT:
        raise ValueError(
            f"no fan-out registered for {kind!r}; "
            f"extend _TRIGGER_FANOUT in engine/zone_transfer.py"
        )

    ctl = controller if controller is not None else card.controller

    # Move the card. Zone mutations happen BEFORE the fan-out so that
    # any trigger that reads game state sees the post-move world (CR
    # 603.6 — the event is the state change; the trigger is generated
    # after it).
    _remove_from_zone(game, card, ctl, src_zone)
    _append_to_zone(game, card, ctl, dst_zone)

    # Fan-out. Run each registered handler; exceptions propagate so
    # bugs are not masked.
    for fn in _TRIGGER_FANOUT[kind]:
        fn(game, card, ctl)
