"""Oracle-driven discovery of optional costs.

The engine inspects card templates (which derive their fields from
oracle text at DB-load time via `engine.oracle_parser`) and produces
typed `OptionalCost` descriptors.  The AI consumes these uniformly
via `decide_optional_cost` — there are NO mechanic-named callbacks.

Adding a new optional-cost mechanic means extending this module
(another conditional + apply lambdas), never adding a new callback.

This module is the engine→AI seam for optional payments.  Sites in
`engine/land_manager.py`, `engine/cast_manager.py`, etc. call
`offer_optional_costs(...)` instead of asking AI-specific yes/no
questions per mechanic.
"""
from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from ai.schemas import CostDescriptor, EffectDescriptor, OptionalCost

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot


# ─────────────────────────────────────────────────────────────
# Snapshot deltas — pure functions over EVSnapshot
# ─────────────────────────────────────────────────────────────

def _snap_pay_life(snap: "EVSnapshot", amount: int) -> "EVSnapshot":
    """Subtract life from the controller side of an EVSnapshot."""
    snap.my_life = snap.my_life - amount
    return snap


def _snap_add_untapped_mana(snap: "EVSnapshot",
                             colors: tuple[str, ...]) -> "EVSnapshot":
    """Credit one extra untapped land producing the given colors.

    `evaluate_board` reads `my_mana` via `mana_clock_impact`, so
    incrementing it captures "I have one more mana available this
    turn" in the EV projection.  Per-color count is updated so
    colored-spell castability checks see the new colors too.
    """
    snap.my_mana = snap.my_mana + 1
    snap.my_total_lands = snap.my_total_lands + 1
    if not snap.my_mana_by_color:
        snap.my_mana_by_color = {}
    else:
        # `replace()` shallow-copies dataclass fields; we need a
        # fresh dict to avoid mutating the caller's snapshot
        snap.my_mana_by_color = dict(snap.my_mana_by_color)
    for c in colors:
        snap.my_mana_by_color[c] = snap.my_mana_by_color.get(c, 0) + 1
    return snap


def _snap_tapped_land(snap: "EVSnapshot") -> "EVSnapshot":
    """Credit a tapped land entering — adds total_lands but not mana."""
    snap.my_total_lands = snap.my_total_lands + 1
    return snap


# ─────────────────────────────────────────────────────────────
# Game-state apply functions — engine resolution
# ─────────────────────────────────────────────────────────────

def _game_pay_life(game: "GameState", player_idx: int, amount: int) -> None:
    """Subtract life from the player."""
    game.players[player_idx].life -= amount


def _game_etb_untapped(card: "CardInstance") -> None:
    """Mark the land as entering untapped."""
    card.tapped = False


# ─────────────────────────────────────────────────────────────
# Public discovery API
# ─────────────────────────────────────────────────────────────

def parse_optional_costs(card: "CardInstance",
                          trigger: str) -> list[OptionalCost]:
    """Return optional costs legal for this card under the given trigger.

    Triggers:
      - "etb"  : payments offered as the card enters the battlefield
      - "tap"  : payments offered when the card's mana ability fires
      - "cast" : payments offered as part of casting

    Implementation reads pre-parsed template fields populated by
    `engine.card_database` (which itself parses oracle text at
    DB-load time).  Extending to new mechanics is a matter of
    adding another conditional that builds an `OptionalCost` from
    whatever oracle-derived fields apply — no new callbacks.
    """
    out: list[OptionalCost] = []
    template = card.template

    if trigger == "etb":
        life_cost = getattr(template, "untap_life_cost", 0) or 0
        if life_cost > 0:
            colors = tuple(template.produces_mana or [])
            cost = CostDescriptor(kind="life", amount=life_cost)
            effect = EffectDescriptor(
                kind="etb_untapped", magnitude=1, colors=colors,
            )

            def _to_game(g, p, c=card, l=life_cost):
                _game_pay_life(g, p, l)
                _game_etb_untapped(c)

            def _to_snap(s, l=life_cost, cs=colors):
                _snap_pay_life(s, l)
                _snap_add_untapped_mana(s, cs)
                return s

            out.append(OptionalCost(
                name=f"{template.name}: pay {life_cost} life, ETB untapped",
                cost=cost,
                effect=effect,
                apply_to_game=_to_game,
                apply_to_snap=_to_snap,
            ))

    return out


# ─────────────────────────────────────────────────────────────
# Engine→AI offer channel
# ─────────────────────────────────────────────────────────────

def offer_optional_costs(game: "GameState", player_idx: int,
                          card: "CardInstance", trigger: str) -> None:
    """Discover optional costs for `card` and let the AI decide each.

    Called by engine sites whenever an optional payment becomes
    legal.  The AI's `decide_optional_cost` callback (uniform across
    all mechanics) returns True/False per offered cost; True ones
    are applied to the live game state.
    """
    for opt in parse_optional_costs(card, trigger):
        if game.callbacks.decide_optional_cost(game, player_idx, opt):
            opt.apply_to_game(game, player_idx)


# ─────────────────────────────────────────────────────────────
# Counter tax — "counter target spell unless its controller pays {N}"
# ─────────────────────────────────────────────────────────────
#
# Same shape as any other optional cost ("pay X to gain/avoid Y"),
# just decided by a DIFFERENT player than the source card's own
# controller: the counter's caster controls `source_card`, but the
# decision ("do I pay to save my spell?") belongs to `targeted_card`'s
# controller. `parse_optional_costs`/`offer_optional_costs` above
# assume decision-maker == card's own controller, so this gets its
# own discovery+offer pair — but still routes through the same typed
# `OptionalCost` schema and the same `decide_optional_cost` callback,
# not a new mechanic-named decision channel.

def parse_counter_tax_cost(source_card: "CardInstance",
                            targeted_card: "CardInstance"
                            ) -> Optional[OptionalCost]:
    """Build the OptionalCost for `source_card`'s counter-tax clause,
    from `targeted_card`'s controller's perspective. None if
    `source_card` is a hard (unconditional) counter."""
    amount = getattr(source_card.template, "counter_tax_amount", 0) or 0
    if amount <= 0:
        return None

    cost = CostDescriptor(kind="mana", amount=amount)
    effect = EffectDescriptor(kind="counter_target", magnitude=1)

    def _to_game(g, p, amt=amount):
        from .mana import ManaCost
        from .mana_payment import ManaPayment
        return ManaPayment.tap_lands_for_mana(g, p, ManaCost(generic=amt))

    def _to_snap(s, tc=targeted_card, amt=amount):
        from ai.ev_evaluator import project_counter_tax_payment
        return project_counter_tax_payment(tc, s, amt)

    return OptionalCost(
        name=(f"{source_card.template.name}: pay {amount} to save "
              f"{targeted_card.template.name} from being countered"),
        cost=cost, effect=effect,
        apply_to_game=_to_game, apply_to_snap=_to_snap,
    )


def offer_counter_tax(game: "GameState", source_card: "CardInstance",
                       targeted_card: "CardInstance") -> bool:
    """Ask `targeted_card`'s controller whether to pay `source_card`'s
    counter tax. Returns True iff paid (the spell is saved, not
    countered).

    Affordability is an engine-side rules gate, not a strategic
    choice: if the controller cannot produce the mana, no decision is
    offered at all — the spell is simply countered, matching a real
    game where an unpayable "unless" clause never triggers a choice.
    """
    opt = parse_counter_tax_cost(source_card, targeted_card)
    if opt is None:
        return False
    targeted_player_idx = targeted_card.controller
    player = game.players[targeted_player_idx]
    if player.available_mana_estimate < opt.cost.amount:
        return False
    if not game.callbacks.decide_optional_cost(game, targeted_player_idx, opt):
        return False
    return bool(opt.apply_to_game(game, targeted_player_idx))


# ─────────────────────────────────────────────────────────────
# Ward tax — "whenever this permanent becomes the target of a spell
# or ability an opponent controls, counter that spell or ability
# unless its controller pays [cost]" (CR 702.21a)
# ─────────────────────────────────────────────────────────────
#
# Mirror image of the counter-tax pair above: there, a counterSPELL
# (`source_card`) taxes the TARGETED spell's controller. Here, the
# TARGETED PERMANENT (`warded_card` — plays the `source_card` role,
# since it's the thing carrying the tax-imposing ability) taxes the
# controller of whichever spell/ability chose it as a target
# (`casting_card` — plays the `targeted_card` role, since it's the
# stack item at risk of being countered). Same typed `OptionalCost`
# schema, same `decide_optional_cost` callback — only the discovery
# site differs (`engine.spell_resolution.resolve_stack`'s per-item
# target scan, not a counterspell's own resolution dispatch), because
# ward isn't a property of a specific spell — it can be triggered by
# ANY spell or ability that targets a warded permanent.

def parse_ward_tax_cost(warded_card: "CardInstance",
                         casting_card: "CardInstance"
                         ) -> Optional[OptionalCost]:
    """Build the OptionalCost for `warded_card`'s Ward tax, from
    `casting_card`'s controller's perspective (the caster whose
    spell/ability targeted `warded_card` and now risks having it
    countered). None if `warded_card` has no (mana-shaped) ward."""
    amount = getattr(warded_card.template, "ward_cost", 0) or 0
    if amount <= 0:
        return None

    cost = CostDescriptor(kind="mana", amount=amount)
    effect = EffectDescriptor(kind="counter_target", magnitude=1)

    def _to_game(g, p, amt=amount):
        from .mana import ManaCost
        from .mana_payment import ManaPayment
        return ManaPayment.tap_lands_for_mana(g, p, ManaCost(generic=amt))

    def _to_snap(s, cc=casting_card, amt=amount):
        from ai.ev_evaluator import project_ward_tax_payment
        return project_ward_tax_payment(cc, s, amt)

    return OptionalCost(
        name=(f"{warded_card.template.name}: pay {amount} to save "
              f"{casting_card.template.name} from being countered "
              f"by ward"),
        cost=cost, effect=effect,
        apply_to_game=_to_game, apply_to_snap=_to_snap,
    )


def offer_ward_tax(game: "GameState", warded_card: "CardInstance",
                    casting_card: "CardInstance",
                    casting_player_idx: int) -> bool:
    """Ask `casting_card`'s controller (the player whose spell/ability
    targeted `warded_card`) whether to pay `warded_card`'s Ward tax.
    Returns True iff paid (the spell/ability survives, not countered).

    Affordability is an engine-side rules gate, not a strategic
    choice: if the caster cannot produce the mana, no decision is
    offered at all — the spell/ability is simply countered, matching
    a real game where an unpayable "unless" clause never triggers a
    choice.
    """
    opt = parse_ward_tax_cost(warded_card, casting_card)
    if opt is None:
        return False
    player = game.players[casting_player_idx]
    if player.available_mana_estimate < opt.cost.amount:
        return False
    if not game.callbacks.decide_optional_cost(game, casting_player_idx, opt):
        return False
    return bool(opt.apply_to_game(game, casting_player_idx))
