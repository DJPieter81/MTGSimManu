"""The effect dispatcher's executors and condition evaluators, by family.

Design doc: docs/design/2026-09-29_clause_and_trigger_grammar.md, sections 11
and 14. `engine/effect_resolver.py` imports this module last, which registers
each family into its tables.

An executor binds one typed EffectSpec at resolution and performs it through
the owner that already exists -- damage through `engine.damage.deal_damage`
(for a chosen target, through `oracle_resolver.resolve_damage_to_chosen_target`,
the owner of "deal N damage to the chosen target"), life loss through
`engine.damage.lose_life`, life gain through `GameState.gain_life`. It never
writes game state itself, never reads oracle text, and adds no log line of
its own: logging is the owner's.

Every executor and evaluator declares the shapes it binds (`supports`), and
`can_execute` refuses any other shape -- an amount, recipient, source or
predicate nothing here evaluates -- so the carrier keeps its legacy apply
(fail closed, A37).

Family `damage` (E1): DAMAGE to the one creature, planeswalker or player
chosen for a slot, LOSE_LIFE and GAIN_LIFE by acting players, and the STATE
conditions printed upgrades read. A chosen target is re-checked on
resolution (CR 608.2b): an illegal one is not affected and its damage is
never redirected; a slot no target was chosen for reaches the owner unbound
and the owner's own rule decides (A36).

Family `card_flow` (unit E, enter triggers): SURVEIL through
`GameState.surveil`, and MOVE from the controller's graveyard to its hand
through the zone funnel (`ZoneManager.move_card`). A MOVE slot no target was
chosen for is the controller's pick out of the legal cards the legality
owner enumerates (`target_solver.enumerate_legal_targets`), asked through
`callbacks.choose_cards` (A35): the executor validates the answer and never
scores. Unit I (impulse draw): EXILE of the top N cards of the controller's
library through the zone funnel, and the CONTINUOUS PERMIT "you may play
those cards <duration>" through the one rule-effect store
(`continuous_effects.register_effect`, read by `rules_query.play_permitted`).

Family `tokens_counters` (unit D): KEYWORD_ACTION "amass <subtype> N" through
the one amass owner, `GameState.amass` (CR 701.47a).
"""
from __future__ import annotations

import dataclasses
import itertools
from typing import Any, Tuple

from . import effect_conditions as conditions
from .cards import CardType, Keyword
from .effect_resolver import (CONDITION_EVALUATORS, EXECUTOR_FILTER_KEYS,
                              EXECUTORS, Handle, Outcome, Resolution,
                              event_player, handle_of, is_event_player)
from .effect_model import (DurationKind, Modification, ModKind, Selector,
                           SelectorKind)
from .effect_spec import (Amount, AmountKind, CardFilter, Chooser,
                          Condition, ConditionKind, Destination, EffectSpec,
                          KeywordAction, Ref, RefKind, RefPart, TokenSpec,
                          Verb)

FAMILY_DAMAGE = "damage"
FAMILY_CARD_FLOW = "card_flow"
FAMILY_TOKENS_COUNTERS = "tokens_counters"
FAMILY_EDICT = "edict"

# The verbs each family's executors own (the tables' contents, pinned by
# tests/test_effect_resolver_sequencing.py).
FAMILIES = {FAMILY_DAMAGE: frozenset({Verb.DAMAGE, Verb.LOSE_LIFE,
                                      Verb.GAIN_LIFE}),
            FAMILY_CARD_FLOW: frozenset({Verb.SURVEIL, Verb.MOVE, Verb.EXILE,
                                         Verb.CONTINUOUS, Verb.DRAW,
                                         Verb.MILL}),
            FAMILY_TOKENS_COUNTERS: frozenset({Verb.KEYWORD_ACTION,
                                               Verb.CREATE_TOKEN}),
            FAMILY_EDICT: frozenset({Verb.SACRIFICE})}


# ── Binding helpers ───────────────────────────────────────────────────

def _source_object(ctx: Resolution) -> Any:
    """The object the ability's source is: the carrier's own when it passed
    one (a resolving spell is in no zone), else the Handle's object now."""
    if ctx.source_object is not None:
        return ctx.source_object
    return ctx.game.get_card_by_id(ctx.source.instance_id)


def _plain_participants(s: EffectSpec) -> bool:
    """No part of the spec these executors do not bind: no optional choice
    (none of them is valued yet: `ai.resolution_choices.
    perform_optional_effect`), no alternatives, no untargeted filter, no
    simultaneity group, no destination, payload or duration, and the
    controller chooses."""
    return (not s.optional and not s.alternatives and s.filter is None
            and s.group is None and s.dest is None and s.payload is None
            and s.duration is None and s.chooser is Chooser.CONTROLLER)


def result_count(a: Any) -> bool:
    """"That many": the number of objects an earlier instruction of the
    host produced (CR 608.2c) -- THAT_MUCH over that instruction's whole
    RESULT, read from the resolution, never from the game state."""
    r = getattr(a, "ref", None)
    return (isinstance(a, Amount) and a.kind is AmountKind.THAT_MUCH
            and a.quantity is None and a.inner is None and not a.rounding
            and not a.evenly and isinstance(r, Ref)
            and r.kind is RefKind.RESULT and r.index is not None
            and r.part is RefPart.ALL and r.n is None and r.of is None
            and not r.per_actor)


def _amount(ctx: Resolution, s: EffectSpec) -> int:
    if result_count(s.amount):
        # A35: an unperformed instruction's result is empty, so 0.
        return sum(len(v) for v in
                   (ctx.results.get(s.amount.ref.index) or {}).values())
    return conditions.amount_value(ctx.game, ctx.controller, s.amount,
                                   ctx.x_value)


# ── DAMAGE ────────────────────────────────────────────────────────────

# The target types the DAMAGE executor binds: "any target" (a creature,
# player or planeswalker, CR 115.4) and its single-type and paired forms.
_DAMAGE_SLOT_TYPES = frozenset({"any", "creature", "planeswalker", "player"})


def _damage_supported(s: EffectSpec) -> bool:
    """"~ deals <amount> damage to <one target>": the source deals it (CR
    120.1) to the one creature, planeswalker or player chosen for the slot,
    re-checked on resolution (CR 608.2b). A source sacrificed to pay the
    cost deals it as it last existed (CR 608.2h): the carrier's source
    object is that object. "~ deals <amount> damage to that player": to
    the player the trigger event names (CR 603.2)."""
    if s.verb is not Verb.DAMAGE or not _plain_participants(s) or s.flags:
        return False
    src = s.other
    if not (isinstance(src, Ref) and src.kind is RefKind.SELF):
        return False
    if is_event_player(s.ref):
        return (s.target is None and s.target_slot is None
                and s.subject is None and s.actor is None
                and conditions.amount_supported(s.amount))
    req = s.target
    if s.target_slot is None or s.subject is not None or s.ref is not None \
            or s.actor is not None or req is None or req.count_max != 1:
        return False
    if not req.types or not set(req.types) <= _DAMAGE_SLOT_TYPES \
            or req.zone not in ("any", "battlefield") \
            or req.supertype is not None or req.subtype is not None \
            or req.max_mana_value is not None or req.max_mana_value_is_x \
            or req.mode_group is not None:
        return False
    return conditions.amount_supported(s.amount)


def _bind_recipient(ctx: Resolution, req: Any, source: Any, value: Any) -> Any:
    """CR 608.2b for one chosen value of a damage slot: the permanent it
    names if that is still the object chosen (its zone and battlefield
    entry, CR 400.7), may still be targeted by the source, and is still of
    a type the slot admits (its current types: CR 115.4 "any target" is a
    creature, planeswalker or player); the player it names if the slot
    admits players and the controller scope allows. Otherwise None: an
    illegal target is not affected, and nothing is redirected."""
    from .target_solver import (can_be_targeted, slot_admits_permanent,
                                slot_admits_player)
    if isinstance(value, int) and not isinstance(value, bool):
        return (value if slot_admits_player(req, value, ctx.controller)
                else None)
    if not isinstance(value, Handle):
        return None
    card = ctx.game.get_card_by_id(value.instance_id)
    if card is None or card.zone != "battlefield" or value.zone != "battlefield" \
            or card.battlefield_entry_seq != value.entry_seq:
        return None
    if not slot_admits_permanent(req, card, ctx.controller):
        return None
    if not can_be_targeted(card, source, ctx.controller):
        return None
    return card


def _audit_damage_upgrade(ctx: Resolution, source: Any, amount: int) -> None:
    """CR 608.2c, restated from the card's other parse: when the printed
    upgrade condition of a burn spell (`direct_damage_data`, the legacy
    parser's reading of the same text) holds for its controller, the
    damage dealt is the upgrade amount. Observation only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    dd = getattr(getattr(source, "template", None), "direct_damage_data",
                 None) or {}
    up = dd.get("upgrade_amount")
    if up and conditions.direct_damage_condition_met(
            ctx.game, ctx.controller, dd.get("upgrade_condition")):
        rules_audit.check(
            "608.2/damage_upgrade", amount == up,
            f"{getattr(source, 'name', '?')}: {dd.get('upgrade_condition')} "
            f"met but dealt {amount}, not {up}", game=ctx.game)


def execute_damage(ctx: Resolution, s: EffectSpec,
                   actors: Tuple[int, ...]) -> Outcome:
    from .oracle_resolver import deal_damage_to
    amount = _amount(ctx, s)
    source = _source_object(ctx)
    _audit_damage_upgrade(ctx, source, amount)
    if amount <= 0:                 # CR 120.8: no damage is dealt
        return Outcome(False, {})
    if is_event_player(s.ref):      # "to that player" (CR 603.2)
        player = event_player(ctx)
        if player is None:
            return Outcome(False, {})
        deal_damage_to(ctx.game, source, ctx.controller, amount, player)
        return Outcome(True, {ctx.controller: (player,)})
    slot = ctx.chosen[s.target_slot] if s.target_slot < len(ctx.chosen) else ()
    if not slot:
        # A36: a slot no target was chosen for reaches the owner unbound
        # and the owner's own rule decides -- a slot that admits a player
        # goes to the opponent's face, the legacy owner's empty-list rule.
        if not set(s.target.types) & {"any", "player"}:
            return Outcome(False, {})
        face = 1 - ctx.controller
        deal_damage_to(ctx.game, source, ctx.controller, amount, face)
        return Outcome(True, {ctx.controller: (face,)})
    dealt = []
    for value in slot:
        recipient = _bind_recipient(ctx, s.target, source, value)
        if recipient is None:
            continue
        deal_damage_to(ctx.game, source, ctx.controller, amount, recipient)
        dealt.append(recipient if isinstance(recipient, int)
                     else handle_of(recipient))
    return Outcome(bool(dealt), {ctx.controller: tuple(dealt)})


execute_damage.supports = _damage_supported


# ── LOSE_LIFE / GAIN_LIFE ─────────────────────────────────────────────

def _life_supported(s: EffectSpec) -> bool:
    """"<player(s)> lose / gain <amount> life", the acting players bound by
    the dispatcher (the controller, a player set, or a chosen player)."""
    if s.verb not in (Verb.LOSE_LIFE, Verb.GAIN_LIFE) \
            or not _plain_participants(s) or s.flags - {"each"}:
        return False
    if s.subject is not None or s.ref is not None or s.other is not None:
        return False
    if s.target_slot is not None and not (
            isinstance(s.actor, Ref) and s.actor.kind is RefKind.TARGET
            and s.actor.index == s.target_slot):
        return False
    return conditions.amount_supported(s.amount)


def execute_lose_life(ctx: Resolution, s: EffectSpec,
                      actors: Tuple[int, ...]) -> Outcome:
    from .damage import lose_life
    amount = _amount(ctx, s)
    if amount <= 0 or not actors:
        return Outcome(False, {})
    for p in actors:                # CR 119.3: loss of life, not damage
        lose_life(ctx.game, p, amount)
    return Outcome(True, {p: (amount,) for p in actors})


def execute_gain_life(ctx: Resolution, s: EffectSpec,
                      actors: Tuple[int, ...]) -> Outcome:
    amount = _amount(ctx, s)
    if amount <= 0 or not actors:
        return Outcome(False, {})
    source = _source_object(ctx)
    for p in actors:                # CR 119.3
        ctx.game.gain_life(p, amount, getattr(source, "name", ""))
    return Outcome(True, {p: (amount,) for p in actors})


execute_lose_life.supports = _life_supported
execute_gain_life.supports = _life_supported


# ── SURVEIL (family card_flow, E3) ────────────────────────────────────

def _surveil_supported(s: EffectSpec) -> bool:
    """"Surveil <amount>": the acting players (the controller when none is
    printed) each surveil (CR 701.42) through the owner, `GameState.surveil`."""
    if s.verb is not Verb.SURVEIL or not _plain_participants(s) or s.flags:
        return False
    if s.subject is not None or s.ref is not None or s.other is not None \
            or s.target is not None or s.target_slot is not None:
        return False
    return conditions.amount_supported(s.amount)


def execute_surveil(ctx: Resolution, s: EffectSpec,
                    actors: Tuple[int, ...]) -> Outcome:
    amount = _amount(ctx, s)
    if amount <= 0 or not actors:
        return Outcome(False, {})
    for p in actors:
        ctx.game.surveil(p, amount)
    return Outcome(True, {p: (amount,) for p in actors})


execute_surveil.supports = _surveil_supported


# ── MOVE graveyard -> hand (family card_flow, E3) ─────────────────────

# The type words `target_solver` evaluates for a card in a graveyard.
_GRAVEYARD_CARD_TYPES = frozenset({
    "card", "creature", "artifact", "enchantment", "planeswalker", "land",
    "instant", "sorcery", "permanent", "permanent_nonland"})
# The one destination this executor binds: the owner's hand, nothing else
# about it printed (no position, no tapped, no counters, no controller).
_TO_HAND = Destination(zone="hand")


def _acts_as_controller(actor: Any) -> bool:
    """No acting player printed, or "you" (a PLAYER selector the dispatcher
    binds to the controller): the controller chooses and moves."""
    return actor is None or (isinstance(actor, Selector)
                             and actor.kind is SelectorKind.PLAYER
                             and actor.filter is None)


def _move_to_hand_supported(s: EffectSpec) -> bool:
    """"[You may] return target <card> from your graveyard to your hand":
    the cards chosen for the slot, still legal on resolution (CR 608.2b),
    move from their owner's graveyard to its hand through the zone funnel
    (CR 400.7: each is a new object there). "You may" is the dispatcher's
    question (A35), asked before this runs."""
    if s.verb is not Verb.MOVE or s.flags or s.dest != _TO_HAND:
        return False
    if s.alternatives or s.filter is not None or s.group is not None \
            or s.payload is not None or s.duration is not None \
            or s.amount is not None or s.chooser is not Chooser.CONTROLLER:
        return False
    if s.subject is not None or s.ref is not None or s.other is not None \
            or not _acts_as_controller(s.actor):
        return False
    req = s.target
    return (s.target_slot is not None and req is not None
            and req.zone == "graveyard" and req.owner_scope == "you"
            and req.count_max >= 1 and req.mode_group is None
            and not req.max_mana_value_is_x
            and bool(req.types) and set(req.types) <= _GRAVEYARD_CARD_TYPES)


def _legal_graveyard_cards(ctx: Resolution, req: Any, source: Any) -> list:
    """The cards the slot may name now (CR 601.2c at the choice, 608.2b on
    resolution): the legality owner's enumeration."""
    from .target_solver import enumerate_legal_targets
    return enumerate_legal_targets(ctx.game, ctx.controller, req,
                                   source=source)


def _owner_picks(ctx: Resolution, s: EffectSpec, source: Any) -> list:
    """A36 for an unbound slot: the controller picks out of the legal cards
    (`choose_cards`, A35). Only distinct members of the pool count, at most
    `count_max`; a required target (CR 601.2c, 603.3d) the answer leaves
    short is filled by the engine's default pick."""
    from .callbacks import default_card_pick
    req = s.target
    pool = _legal_graveyard_cards(ctx, req, source)
    n = min(req.count_max, len(pool))
    if n <= 0:
        return []
    picked: list = []
    for c in ctx.game.callbacks.choose_cards(ctx, s, list(pool), n) or ():
        if len(picked) < n and any(c is p for p in pool) \
                and all(c is not x for x in picked):
            picked.append(c)
    short = min(req.count_min, n) - len(picked)
    if short > 0:
        picked += default_card_pick(
            [c for c in pool if all(c is not x for x in picked)], short)
    return picked


def _bound_cards(ctx: Resolution, s: EffectSpec, source: Any,
                 slot: tuple) -> list:
    """CR 608.2b for chosen values: a card binds if it is still the object
    chosen in the graveyard (A34; graveyard identity is the Handle's known
    gap) and still a legal choice for the slot; anything else is not
    affected."""
    legal = _legal_graveyard_cards(ctx, s.target, source)
    out = []
    for value in slot:
        if not isinstance(value, Handle) or value.zone != "graveyard":
            continue
        card = ctx.game.get_card_by_id(value.instance_id)
        if card is not None and card.zone == "graveyard" \
                and any(card is c for c in legal) \
                and all(card is not x for x in out):
            out.append(card)
    return out


def execute_move_to_hand(ctx: Resolution, s: EffectSpec,
                         actors: Tuple[int, ...]) -> Outcome:
    source = _source_object(ctx)
    slot = ctx.chosen[s.target_slot] if s.target_slot < len(ctx.chosen) else ()
    cards = (_bound_cards(ctx, s, source, slot) if slot
             else _owner_picks(ctx, s, source))
    cause = getattr(source, "name", "")
    moved = []
    for card in cards:
        if ctx.game.zone_mgr.move_card(ctx.game, card, "graveyard", "hand",
                                       cause=cause):
            moved.append(handle_of(card))
    return Outcome(bool(moved), {ctx.controller: tuple(moved)})


def _exiled_by_this_resolution(ctx: Resolution, card: Any) -> bool:
    """Did an earlier instruction of this resolution move `card` into
    exile? Only that object is the one "return it" names (CR 400.7)."""
    return any(isinstance(h, Handle) and h.zone == "exile"
               and h.instance_id == card.instance_id
               for per in ctx.results.values() for hs in per.values()
               for h in hs)


def _return_self_transformed(ctx: Resolution, s: EffectSpec) -> Outcome:
    """"Return ~ to the battlefield transformed under its owner's / your
    control": the card this resolution's own exile moved enters with its
    back face up as a new object, through the one return owner
    (`oracle_resolver.return_transformed`)."""
    from .oracle_resolver import return_transformed
    card = _source_object(ctx)
    if card is None or card.zone != "exile" \
            or not _exiled_by_this_resolution(ctx, card):
        return Outcome(False, {})
    controller = card.owner if s.dest.controller == "owner" \
        else ctx.controller
    if not return_transformed(ctx.game, card, controller):
        return Outcome(False, {})
    return Outcome(True, {controller: (handle_of(card),)})


def execute_move(ctx: Resolution, s: EffectSpec,
                 actors: Tuple[int, ...]) -> Outcome:
    if self_return_transformed(s):
        return _return_self_transformed(ctx, s)
    return execute_move_to_hand(ctx, s, actors)


execute_move.supports = (lambda s: _move_to_hand_supported(s)
                         or self_return_transformed(s))
# An unbound slot is the controller's choice through `choose_cards`, so a
# carrier with no targets to bind (the enter-trigger carrier) may take it.
execute_move.picks_unbound_slots = True


# ── EXILE the top N cards of your library; PERMIT to play them
#    (family card_flow, unit I: impulse draw) ────────────────────────────

# The exiled object groups this executor binds: the top of one player's
# library (the filter leaf's library-position row) -- the controller's own,
# or the trigger event's player's ("the top card of that player's library",
# R2). The executor evaluates these filter entries itself, by taking the
# top N of that library.
_LIBRARY_TOP = CardFilter(zone="library", owner="you", position="top")
_EVENT_PLAYER = Ref(RefKind.EVENT_PLAYER)
_LIBRARY_OWNERS = ("you", _EVENT_PLAYER)


def _is_library_top(f: Any) -> bool:
    return isinstance(f, CardFilter) and f.owner in _LIBRARY_OWNERS \
        and f == dataclasses.replace(_LIBRARY_TOP, owner=f.owner, raw=f.raw)


def _library_owner(ctx: Resolution, f: CardFilter):
    """Whose library the position reads: the controller's ("your"), or
    the player the trigger event names ("that player's"; None with no
    event player, and nothing is exiled)."""
    if f.owner == "you":
        return ctx.controller
    return event_player(ctx)


def _plain_exile(s: EffectSpec) -> bool:
    """An exile with nothing the executor does not perform: no choice, no
    branch, no destination or payload, the controller moves the cards."""
    return not (s.flags or s.optional or s.alternatives
                or s.group is not None or s.dest is not None
                or s.payload is not None or s.duration is not None
                or s.chooser is not Chooser.CONTROLLER
                or s.ref is not None or s.other is not None
                or s.target is not None or s.target_slot is not None
                or not _acts_as_controller(s.actor))


def _exile_top_supported(s: EffectSpec) -> bool:
    """"Exile the top <N> cards of your library" (or "of that player's
    library"): that library's top N cards move to exile through the zone
    funnel, face up (CR 406), into their owner's exile (CR 400.3); no draw
    (CR 121.1c)."""
    if s.verb is not Verb.EXILE or not _is_library_top(s.filter):
        return False
    if not _plain_exile(s) or s.subject is not None:
        return False
    return conditions.amount_supported(s.amount)


# "Exile all [the] cards from your hand" and "exile your hand": the whole of
# the controller's hand. The grammar types the quantifier two ways: as the
# spec's subject, a filter over every card it names ("all cards from your
# hand"), or as the zone taken whole ("your hand").
_YOUR_HAND = CardFilter(zone="hand", owner="you")
_YOUR_HAND_ENTRIES = (("zone", "hand"), ("owner", "you"))


def _is_your_hand(f: Any) -> bool:
    return isinstance(f, CardFilter) and \
        f == dataclasses.replace(_YOUR_HAND, raw=f.raw)


def _whole_hand(s: EffectSpec) -> bool:
    a, subj = s.amount, s.subject
    if subj is None:
        return (isinstance(a, Amount) and a.kind is AmountKind.WHOLE_ZONE
                and a == Amount(AmountKind.WHOLE_ZONE))
    return (a is None and isinstance(subj, Selector)
            and subj.kind is SelectorKind.FILTER and subj.player is None
            and subj.obj is None
            and sorted(subj.filter or ()) == sorted(_YOUR_HAND_ENTRIES))


def _exile_hand_supported(s: EffectSpec) -> bool:
    """"Exile all cards from your hand" / "exile your hand": every card in
    the controller's hand moves to exile through the zone funnel, face up
    (CR 406). Face down, a part of the hand, or another player's hand is
    refused."""
    return (s.verb is Verb.EXILE and _is_your_hand(s.filter)
            and _plain_exile(s) and _whole_hand(s))


# "Exile ~, then return ~ to the battlefield transformed [under its owner's
# / your control]" (CR 400.7, 712): the ability's own permanent leaves and
# a new object enters with its back face up. The two specs' shapes, read
# by the executors that run them and by the carriers that take them.
_RETURN_CONTROLLERS = ("owner", "you")


def _self_object(r: Any) -> bool:
    """"~": the ability's own object, whole and as it is now."""
    return (isinstance(r, Ref) and r.kind is RefKind.SELF and r.index is None
            and r.part is RefPart.ALL and r.n is None and r.of is None
            and not r.lki and not r.per_actor)


def _names_only_its_ref(s: Any) -> bool:
    """The spec names its object only through `ref`: no target, filter,
    subject, amount, payload, duration, condition, branch or choice, and
    the controller acts."""
    return (s.target is None and s.target_slot is None and s.filter is None
            and s.subject is None and s.other is None and s.amount is None
            and s.payload is None and s.duration is None
            and s.condition is None and not s.replaces and not s.flags
            and not s.optional and not s.alternatives and s.group is None
            and not s.then and not s.otherwise and not s.residue
            and s.chooser is Chooser.CONTROLLER and s.actor is None)


def self_exile(s: Any) -> bool:
    """"Exile ~": the ability's own permanent moves to exile."""
    return (s.verb is Verb.EXILE and _self_object(s.ref) and s.dest is None
            and _names_only_its_ref(s))


def self_return_transformed(s: Any) -> bool:
    """"Return ~ to the battlefield transformed under its owner's / your
    control": the card the ability's own exile moved enters with its back
    face up."""
    d = s.dest
    return (s.verb is Verb.MOVE and _self_object(s.ref)
            and _names_only_its_ref(s) and isinstance(d, Destination)
            and d.controller in _RETURN_CONTROLLERS
            and d == Destination(zone="battlefield", transformed=True,
                                 controller=d.controller))


def _exile_supported(s: EffectSpec) -> bool:
    """The top N of a library, the controller's whole hand, or the
    ability's own permanent ("exile ~"). Any other exile is refused."""
    return (_exile_top_supported(s) or _exile_hand_supported(s)
            or self_exile(s))


def _exile_self(ctx: Resolution) -> Outcome:
    """"Exile ~" (CR 400.7): the source, still on the battlefield as the
    object the ability came from -- not one that has since left and come
    back -- moves to exile through the zone funnel."""
    card, src = _source_object(ctx), ctx.source
    if card is None or card.zone != "battlefield" or src is None \
            or src.zone != "battlefield" \
            or card.battlefield_entry_seq != src.entry_seq:
        return Outcome(False, {})
    if not ctx.game.zone_mgr.move_card(ctx.game, card, "battlefield",
                                       "exile", cause=card.name):
        return Outcome(False, {})
    return Outcome(True, {ctx.controller: (handle_of(card),)})


def _audit_hand_exiled(ctx: Resolution, player: Any) -> None:
    """CR 406, restated from the zone itself: after "exile all cards from
    your hand" no card is left in that hand. Observation only."""
    from . import rules_audit
    if rules_audit.enabled():
        rules_audit.check(
            "406/exile_all_cards_from_hand", not player.hand,
            f"{len(player.hand)} card(s) left in hand after "
            f"{getattr(_source_object(ctx), 'name', '?')} exiled it",
            game=ctx.game)


def _exile_hand(ctx: Resolution) -> Outcome:
    player = ctx.game.players[ctx.controller]
    cause = getattr(_source_object(ctx), "name", "")
    moved = []
    for card in list(player.hand):
        if ctx.game.zone_mgr.move_card(ctx.game, card, "hand", "exile",
                                       cause=cause):
            moved.append(handle_of(card))
    _audit_hand_exiled(ctx, player)
    return Outcome(bool(moved), {ctx.controller: tuple(moved)})


def execute_exile(ctx: Resolution, s: EffectSpec,
                  actors: Tuple[int, ...]) -> Outcome:
    if self_exile(s):
        return _exile_self(ctx)
    if _is_your_hand(s.filter):
        return _exile_hand(ctx)
    n = _amount(ctx, s)
    owner = _library_owner(ctx, s.filter)
    if owner is None:
        return Outcome(False, {})
    cards = list(ctx.game.players[owner].library[:max(0, n)])
    cause = getattr(_source_object(ctx), "name", "")
    moved = []
    for card in cards:
        if ctx.game.zone_mgr.move_card(ctx.game, card, "library", "exile",
                                       cause=cause):
            moved.append(handle_of(card))
    return Outcome(bool(moved), {ctx.controller: tuple(moved)})


execute_exile.supports = _exile_supported

# The durations a permission to play is bound with: "this turn" and "until
# the end of your next turn" (bound to the controller and the turn now).
_PERMIT_DURATIONS = frozenset({DurationKind.THIS_TURN,
                               DurationKind.UNTIL_END_OF_YOUR_NEXT_TURN})


def _permit_supported(s: EffectSpec) -> bool:
    """"You may play / cast those cards <duration>" (CR 305.1, 601.2a): the
    controller may play (or cast) the objects an earlier spec of the host
    produced, while the bound duration lasts, through the one rule-effect
    owner (`continuous_effects.register_effect`, read by
    `rules_query.play_permitted`)."""
    m = s.payload
    if s.verb is not Verb.CONTINUOUS or not isinstance(m, Modification) \
            or m.kind is not ModKind.PERMIT or m.action not in ("play", "cast") \
            or m.data:
        return False
    ref = s.ref
    if not (isinstance(ref, Ref) and ref.kind is RefKind.RESULT
            and isinstance(ref.index, int) and ref.of is None
            and ref.n is None and ref.part is RefPart.ALL):
        return False
    if s.duration is None or s.duration.kind not in _PERMIT_DURATIONS:
        return False
    if s.flags or s.optional or s.alternatives or s.filter is not None \
            or s.group is not None or s.dest is not None \
            or s.amount is not None or s.chooser is not Chooser.CONTROLLER:
        return False
    return (s.subject is None and s.other is None and s.target is None
            and s.target_slot is None and _acts_as_controller(s.actor))


def execute_permit(ctx: Resolution, s: EffectSpec,
                   actors: Tuple[int, ...]) -> Outcome:
    from .effect_model import THIS_TURN as _THIS_TURN
    from .effect_model import permit_play, until_end_of_your_next_turn
    produced = [v for per in (ctx.results.get(s.ref.index) or {}).values()
                for v in per]
    objects = []
    for h in produced:
        if not isinstance(h, Handle) or h.zone != "exile":
            continue
        # CR 608.2b: an exiled card that has since left exile is not "those
        # cards" any more.
        card = ctx.game.get_card_by_id(h.instance_id)
        if card is not None and card.zone == "exile":
            objects.append(card.instance_id)
    if not objects:
        return Outcome(False, {})
    duration = (_THIS_TURN if s.duration.kind is DurationKind.THIS_TURN
                else until_end_of_your_next_turn(ctx.controller,
                                                 ctx.game.turn_number))
    ctx.game.continuous_effects.register_effect(permit_play(
        ctx.controller, objects, s.payload.action, duration,
        source_id=ctx.source.instance_id))
    return Outcome(True, {ctx.controller: tuple(objects)})


execute_permit.supports = _permit_supported


# ── DRAW (family card_flow, R2) ───────────────────────────────────────

def _draw_supported(s: EffectSpec) -> bool:
    """"Draw <N> cards" by the controller: through the one draw owner,
    `GameState.draw_cards` (CR 121.1), which counts the draw and fans out
    its triggers. Any other drawer ("target player", "that player", "each
    player"), an optional draw, or any other shape is refused."""
    if s.verb is not Verb.DRAW:
        return False
    if s.flags or s.optional or s.alternatives or s.filter is not None \
            or s.group is not None or s.dest is not None \
            or s.payload is not None or s.duration is not None \
            or s.chooser is not Chooser.CONTROLLER:
        return False
    if s.subject is not None or s.ref is not None or s.other is not None \
            or s.target is not None or s.target_slot is not None \
            or not _acts_as_controller(s.actor):
        return False
    return conditions.amount_supported(s.amount) or result_count(s.amount)


def execute_draw(ctx: Resolution, s: EffectSpec,
                 actors: Tuple[int, ...]) -> Outcome:
    n = _amount(ctx, s)
    if n <= 0:
        return Outcome(False, {})
    drawn = ctx.game.draw_cards(ctx.controller, n)
    return Outcome(bool(drawn),
                   {ctx.controller: tuple(handle_of(c) for c in drawn)})


execute_draw.supports = _draw_supported


# ── CREATE_TOKEN (family tokens_counters, R2; printed tokens, unit TK) ─

# The predefined tokens (CR 111.10) the token owner defines: Treasure,
# Food and Clue (`player_state.TOKEN_DEFS`). Any other is refused.
_PREDEFINED_TOKENS = frozenset({"treasure", "food", "clue"})
# The printed player sets an instruction names as its acting players (CR
# 111.2 for tokens, CR 701.17a for a mill): each opponent, each player, or
# "you".
_PLAYER_SETS = frozenset({SelectorKind.OPPONENTS, SelectorKind.ALL_PLAYERS,
                          SelectorKind.PLAYER})


def _named_players_supported(actor: Any) -> bool:
    """The controller, a printed player set, or the player chosen for a
    target slot ("target player creates ...", "target player mills ...")."""
    return (_acts_as_controller(actor)
            or (isinstance(actor, Selector) and actor.kind in _PLAYER_SETS
                and actor.filter is None)
            or (isinstance(actor, Ref) and actor.kind is RefKind.TARGET))


def _create_token_supported(s: EffectSpec) -> bool:
    """"<Players> create(s) <N> <token>": each player the spec names
    creates them through the one token owner, `GameState.create_token` (CR
    111.2: they own and control them). The token is a predefined one the
    owner defines (CR 111.10), or one whose printed characteristics the
    owner builds from the typed spec (`permanent_effects.printed_token`,
    CR 111.3). A copy, or a token the owner does not build, is refused."""
    from .permanent_effects import printed_token
    p = s.payload
    if s.verb is not Verb.CREATE_TOKEN or not isinstance(p, TokenSpec):
        return False
    if p.predefined is not None:
        if p.predefined not in _PREDEFINED_TOKENS \
                or p != TokenSpec(predefined=p.predefined):
            return False
    elif printed_token(p) is None:
        return False
    if s.flags or s.optional or s.alternatives or s.filter is not None \
            or s.group is not None or s.dest is not None \
            or s.duration is not None or s.chooser is not Chooser.CONTROLLER:
        return False
    if s.subject is not None or s.ref is not None or s.other is not None \
            or s.target is not None or s.target_slot is not None \
            or not _named_players_supported(s.actor):
        return False
    return conditions.amount_supported(s.amount)


def execute_create_token(ctx: Resolution, s: EffectSpec,
                         actors: Tuple[int, ...]) -> Outcome:
    n = _amount(ctx, s)
    if n <= 0:
        return Outcome(False, {})
    p = s.payload
    created = []
    for player in actors:
        if p.predefined is not None:
            tokens = ctx.game.create_token(player, p.predefined, count=n)
        else:
            tokens = ctx.game.create_token(player, "", count=n, token_spec=p)
        created.append((player, tuple(tokens)))
    made = {player: tokens for player, tokens in created if tokens}
    _audit_tokens(ctx, s, made)
    return Outcome(bool(made), {player: tuple(handle_of(t) for t in tokens)
                                for player, tokens in made.items()})


def _audit_tokens(ctx: Resolution, s: EffectSpec, made: dict) -> None:
    """CR 111.2 / 111.3, restated from the spec's raw fields (not from the
    owner's builder): a token is controlled by the player who created it,
    and has the power, toughness, card types, subtypes and colours its
    creator printed. Observation only."""
    from . import rules_audit
    p = s.payload
    if not rules_audit.enabled() or p.predefined is not None:
        return
    problems = []
    for player, tokens in made.items():
        for t in tokens:
            tt = t.template
            if t.controller != player or t.owner != player:
                problems.append(f"{t.name} controlled by P{t.controller + 1}, "
                                f"created by P{player + 1}")
            if p.power is not None and (tt.power, tt.toughness) != \
                    (p.power.n, p.toughness.n):
                problems.append(f"{t.name} is {tt.power}/{tt.toughness}")
            if {c.value for c in tt.card_types} != set(p.types):
                problems.append(f"{t.name} has types {tt.card_types}")
            if [x.lower() for x in tt.subtypes] != list(p.subtypes):
                problems.append(f"{t.name} has subtypes {tt.subtypes}")
            if {c.value for c in tt.colors} != set(p.colors):
                problems.append(f"{t.name} has colours {tt.colors}")
    source = _source_object(ctx)
    rules_audit.check("111.3/token_characteristics", not problems,
                      f"{getattr(source, 'name', '?')}: {'; '.join(problems)}",
                      game=ctx.game)


execute_create_token.supports = _create_token_supported


# ── KEYWORD_ACTION: amass (family tokens_counters, unit D) ────────────

def _amass_supported(s: EffectSpec) -> bool:
    """"Amass <subtype> N" (CR 701.47a), performed by its controller
    through the one owner, `GameState.amass`. Any other keyword action is
    refused."""
    p = s.payload
    if s.verb is not Verb.KEYWORD_ACTION or not isinstance(p, KeywordAction) \
            or p.name != "amass" or not p.subtype or p.expansion:
        return False
    if s.flags or s.optional or s.alternatives or s.filter is not None \
            or s.group is not None or s.dest is not None \
            or s.duration is not None or s.amount is not None \
            or s.chooser is not Chooser.CONTROLLER:
        return False
    if s.subject is not None or s.ref is not None or s.other is not None \
            or s.actor is not None or s.target is not None \
            or s.target_slot is not None:
        return False
    return conditions.amount_supported(p.amount)


def execute_keyword_action(ctx: Resolution, s: EffectSpec,
                           actors: Tuple[int, ...]) -> Outcome:
    n = conditions.amount_value(ctx.game, ctx.controller, s.payload.amount,
                                ctx.x_value)
    if n <= 0:
        return Outcome(False, {})
    army = ctx.game.amass(ctx.controller, n, s.payload.subtype)
    return Outcome(True, {ctx.controller: (handle_of(army),)})


execute_keyword_action.supports = _amass_supported


# ── CONTINUOUS characteristic changes on a chosen object (layers 4, 6,
#    7c): "target creature becomes a <type> in addition to its other
#    types / gains <keywords> / gets +N/+M until end of turn" ────────────

_CHARACTERISTIC_KINDS = frozenset({ModKind.ADD_TYPES, ModKind.ADD_KEYWORDS,
                                   ModKind.MODIFY_PT})
_CARD_TYPE_NAMES = frozenset(t.value for t in CardType)
_KEYWORD_NAMES = frozenset(k.value for k in Keyword)


def _literal_n(a: Any):
    return (a.n if isinstance(a, Amount) and a.kind is AmountKind.LITERAL
            and a.quantity is None and a.ref is None and a.inner is None
            else None)


def _characteristic_data(m: Modification) -> bool:
    d = dict(m.data)
    if m.kind is ModKind.ADD_TYPES:
        types, subs = d.get("types", ()), d.get("subtypes", ())
        return (not set(d) - {"types", "subtypes"} and bool(types or subs)
                and all(t in _CARD_TYPE_NAMES for t in types)
                and all(isinstance(st, str) and st for st in subs))
    if m.kind is ModKind.ADD_KEYWORDS:
        kws = d.get("keywords", ())
        return (not set(d) - {"keywords"} and bool(kws)
                and all(param is None and kw in _KEYWORD_NAMES
                        for kw, param in kws))
    return (not set(d) - {"power", "toughness"}
            and all(_literal_n(d.get(k)) is not None
                    for k in ("power", "toughness") if k in d)
            and bool(d))


def _characteristic_supported(s: EffectSpec) -> bool:
    """A type, keyword or P/T change on the object one target slot names,
    until end of turn (CR 611.2a/c, 613.1d/f, 613.4c): the chosen object
    only, through the layer system's one-shot effects."""
    m = s.payload
    if s.verb is not Verb.CONTINUOUS or not isinstance(m, Modification) \
            or m.kind not in _CHARACTERISTIC_KINDS \
            or not _characteristic_data(m):
        return False
    if s.duration is None or s.duration.kind is not DurationKind.THIS_TURN:
        return False
    req = s.target
    if s.target_slot is None or req is None or req.zone != "battlefield" \
            or req.count_max != 1 or req.mode_group is not None:
        return False
    return (not s.optional and not s.alternatives and s.filter is None
            and s.group is None and s.dest is None and s.amount is None
            and s.subject is None and s.other is None and s.ref is None
            and not s.flags and s.chooser is Chooser.CONTROLLER
            and _acts_as_controller(s.actor))


def _characteristic_effects(ctx: Resolution, m: Modification, source: Any,
                            card: Any) -> list:
    from .continuous_effects import (create_pump_spell_effect,
                                     create_type_adding_effect)
    sid = getattr(source, "instance_id", 0) or 0
    name = getattr(source, "name", "") or ""
    d = dict(m.data)
    seq = card.battlefield_entry_seq
    if m.kind is ModKind.ADD_TYPES:
        return create_type_adding_effect(
            sid, name, card.instance_id, seq,
            types={CardType(t) for t in d.get("types", ())},
            subtypes={st[:1].upper() + st[1:] for st in d.get("subtypes", ())})
    if m.kind is ModKind.ADD_KEYWORDS:
        return create_pump_spell_effect(
            sid, name, card.instance_id,
            keyword_grants={Keyword(kw) for kw, _ in d["keywords"]},
            controller=ctx.controller, target_seq=seq)
    return create_pump_spell_effect(
        sid, name, card.instance_id,
        power_bonus=_literal_n(d.get("power")) or 0,
        toughness_bonus=_literal_n(d.get("toughness")) or 0,
        controller=ctx.controller, target_seq=seq)


def _characteristic(ctx: Resolution, s: EffectSpec) -> Outcome:
    source = _source_object(ctx)
    slot = ctx.chosen[s.target_slot] if s.target_slot < len(ctx.chosen) else ()
    changed = []
    for value in slot:
        card = _bind_recipient(ctx, s.target, source, value)
        if card is None or isinstance(card, int):
            continue                       # CR 608.2b: not affected
        for effect in _characteristic_effects(ctx, s.payload, source, card):
            ctx.game.continuous_effects.register(effect)
        changed.append(handle_of(card))
    if changed:
        ctx.game.continuous_effects.recalculate(ctx.game)
    return Outcome(bool(changed), {ctx.controller: tuple(changed)})


def execute_continuous(ctx: Resolution, s: EffectSpec,
                       actors: Tuple[int, ...]) -> Outcome:
    if _characteristic_supported(s):
        return _characteristic(ctx, s)
    return execute_permit(ctx, s, actors)


execute_continuous.supports = (lambda s: _permit_supported(s)
                               or _characteristic_supported(s))
# A characteristic change runs only in the type-change family (unit AT):
# the pump family's keyword and P/T hosts stay on their legacy appliers
# until that family's switch is proven host by host.
execute_continuous.allowed_families = (
    lambda s: frozenset({"type_change"}) if _characteristic_supported(s)
    else None)


# ── SACRIFICE: an edict (family edict, unit ED) ───────────────────────
#
# "<players> sacrifice(s) <N> <permanents> [of their choice]" (CR 701.21a):
# each player the actor names sacrifices N permanents they control that
# match the filter, each chosen by the player who sacrifices it (the
# `choose_sacrifice` callback, the seam a sacrifice cost's choice uses);
# the choices are made in APNAP order and the permanents then leave
# together (CR 101.4). A player with fewer sacrifices every one.

_EDICT_ACTORS = frozenset({SelectorKind.OPPONENTS, SelectorKind.ALL_PLAYERS,
                           SelectorKind.PLAYER})
_CARD_TYPE_VALUES = frozenset(t.value for t in CardType)


def _edict_filter(f: Any) -> bool:
    """A battlefield filter of card types (any of them; none: any
    permanent) and, optionally, token or nontoken."""
    if not isinstance(f, CardFilter) or f.zone != "battlefield":
        return False
    entries = dict(f.as_tuple())
    if not set(entries) <= {"types", "token"}:
        return False
    return set(entries.get("types", ())) <= _CARD_TYPE_VALUES


def _sacrifice_supported(s: EffectSpec) -> bool:
    """An edict: the player set or the chosen player sacrifices a printed
    number of filtered permanents of their choice, unconditionally."""
    a, actor = s.amount, s.actor
    actor_ok = ((isinstance(actor, Selector) and actor.kind in _EDICT_ACTORS
                 and actor.filter is None)
                or (isinstance(actor, Ref) and actor.kind is RefKind.TARGET))
    return (s.verb is Verb.SACRIFICE and actor_ok
            and s.chooser is Chooser.PARTICIPANT
            and isinstance(a, Amount) and a.kind is AmountKind.LITERAL
            and isinstance(a.n, int) and a.n >= 1
            and _edict_filter(s.filter)
            and s.target is None and s.target_slot is None
            and s.subject is None and s.ref is None and s.payload is None
            and s.dest is None and s.duration is None and s.group is None
            and not s.optional and not s.alternatives and not s.flags)


def edict_victims(game: Any, player: int, f: CardFilter) -> list:
    """The permanents `player` controls that an edict's filter admits, by
    their current types (CR 613)."""
    types = set(f.types or ())
    out = []
    for perm in game.players[player].battlefield:
        if types and not any(t.value in types
                             for t in perm.effective_card_types):
            continue
        if f.token is not None and bool(perm.is_token) != f.token:
            continue
        out.append(perm)
    return out


def _sacrifice(ctx: Resolution, perm: Any) -> None:
    """CR 701.21a: its controller moves it to its owner's graveyard,
    through the zone funnel (a creature dies, CR 700.4)."""
    source = getattr(_source_object(ctx), "name", "an edict")
    ctx.game.zone_mgr.move_card_to_graveyard(
        ctx.game, perm, cause=f"sacrifice ({source})")


def execute_sacrifice(ctx: Resolution, s: EffectSpec,
                      actors: Tuple[int, ...]) -> Outcome:
    n = s.amount.n
    choices = []
    for player in actors:                        # choices in APNAP order
        legal = edict_victims(ctx.game, player, s.filter)
        picks = []
        while legal and len(picks) < n:
            pick = ctx.game.callbacks.choose_sacrifice(ctx.game, player,
                                                       list(legal))
            if pick is None or pick not in legal:
                pick = legal[0]                  # the choice is not optional
            picks.append(pick)
            legal.remove(pick)
        choices.append((player, picks))
    chosen = dict(choices)
    before = {p: len(edict_victims(ctx.game, p, s.filter)) for p in actors}
    for player in actors:                        # then one simultaneous move
        for perm in chosen[player]:
            _sacrifice(ctx, perm)
    _audit_edict(ctx, s, actors, before)
    return Outcome(any(chosen.values()),
                   {p: tuple(handle_of(c) for c in v)
                    for p, v in chosen.items() if v})


def _audit_edict(ctx: Resolution, s: EffectSpec, actors: Tuple[int, ...],
                 before: dict) -> None:
    """CR 701.21a, restated from the battlefield's raw printed types: each
    player the edict named controls N fewer permanents it matches, or
    none. Observation only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    f = s.filter
    types = set(f.types or ())

    def count(player):
        return sum(1 for p in ctx.game.players[player].battlefield
                   if (not types or any(t.value in types
                                        for t in p.template.card_types))
                   and (f.token is None or bool(p.is_token) == f.token))
    for player in actors:
        want = max(0, before[player] - s.amount.n)
        rules_audit.check("701.21a/edict", count(player) <= want,
                          f"P{player + 1} controls {count(player)} matching "
                          f"permanents after an edict, more than {want}",
                          game=ctx.game)


execute_sacrifice.supports = _sacrifice_supported
# An edict runs only in its own family (unit ED): the removal family's
# other sacrifices (costs, "sacrifice ~") stay with their carriers.
execute_sacrifice.allowed_families = lambda s: frozenset({FAMILY_EDICT})


# ── Conditions ────────────────────────────────────────────────────────

def evaluate_state(ctx: Resolution, cond: Condition) -> bool:
    return conditions.state_condition_holds(ctx.game, ctx.controller, cond)


evaluate_state.supports = conditions.state_condition_supported


# ── MILL (family card_flow, unit ML) ──────────────────────────────────

def _mill_supported(s: EffectSpec) -> bool:
    """"<Players> mill(s) <N> cards": each player the spec names -- the
    controller, a printed player set, or the player chosen for a target --
    puts that many cards from the top of their library into their graveyard
    (CR 701.17a) through the one mill owner, `GameState.mill`."""
    if s.verb is not Verb.MILL or not _plain_participants(s) or s.flags:
        return False
    if s.subject is not None or s.ref is not None or s.other is not None \
            or s.target is not None or s.target_slot is not None \
            or not _named_players_supported(s.actor):
        return False
    return conditions.amount_supported(s.amount)


def _audit_mill(ctx: Resolution, player: int, before: int, n: int) -> None:
    """CR 701.17a/b, restated from the library's size: a mill of N moves
    min(N, library size) cards out of that player's library. Observation
    only."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    left = before - len(ctx.game.players[player].library)
    rules_audit.check(
        "701.17a/mill", left == min(n, before),
        f"{getattr(_source_object(ctx), 'name', '?')}: P{player + 1} milled "
        f"{left} of {n} with {before} in library", game=ctx.game)


def execute_mill(ctx: Resolution, s: EffectSpec,
                 actors: Tuple[int, ...]) -> Outcome:
    n = _amount(ctx, s)
    if n <= 0 or not actors:
        return Outcome(False, {})
    cause = getattr(_source_object(ctx), "name", "")
    milled = {}
    for p in actors:
        before = len(ctx.game.players[p].library)
        cards = ctx.game.mill(p, n, cause=cause)
        _audit_mill(ctx, p, before, n)
        milled[p] = tuple(handle_of(c) for c in cards)
    return Outcome(any(milled.values()), milled)


execute_mill.supports = _mill_supported


# ── Registration ──────────────────────────────────────────────────────

EXECUTORS[Verb.DAMAGE] = execute_damage
EXECUTORS[Verb.LOSE_LIFE] = execute_lose_life
EXECUTORS[Verb.GAIN_LIFE] = execute_gain_life
EXECUTORS[Verb.SURVEIL] = execute_surveil
EXECUTORS[Verb.MOVE] = execute_move
EXECUTORS[Verb.KEYWORD_ACTION] = execute_keyword_action
EXECUTORS[Verb.EXILE] = execute_exile
EXECUTORS[Verb.CONTINUOUS] = execute_continuous
EXECUTORS[Verb.DRAW] = execute_draw
EXECUTORS[Verb.CREATE_TOKEN] = execute_create_token
EXECUTORS[Verb.SACRIFICE] = execute_sacrifice
EXECUTORS[Verb.MILL] = execute_mill
EXECUTOR_FILTER_KEYS[Verb.SACRIFICE] = frozenset(
    ("types", combo) for k in (1, 2, 3)
    for combo in itertools.combinations(sorted(_CARD_TYPE_VALUES), k)) | {
    ("token", True), ("token", False)}
EXECUTOR_FILTER_KEYS[Verb.EXILE] = frozenset(_LIBRARY_TOP.as_tuple()) | {
    ("owner", _EVENT_PLAYER)} | frozenset(_YOUR_HAND_ENTRIES)
CONDITION_EVALUATORS[ConditionKind.STATE] = evaluate_state
