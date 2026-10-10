#!/usr/bin/env python3
"""Per-host resolution harness (design doc 2026-09-29, section 10, A42).

For one host -- a card's SPELL host, one of its modes, an activated ability
or a loyalty line -- and each of six fixed synthetic boards and each seed,
the harness deep-copies the board, resolves the host once per side and
compares:

* the canonical game-state digest (zones in order, life, poison, energy,
  mana pools, card state including counters and damage, the stack, the
  continuous-effect registry and the delayed triggers), and
* the game-log bytes the resolution appended.

The six boards (section 10, "Per-host resolution harness"): ``empty``;
``opponent_creatures`` (mixed mana value and keywords); ``own_creatures``;
``lands`` (basics and nonbasics); ``stack`` (a spell on the stack); and
``graveyards`` (creature cards in both graveyards). Board cards are chosen
from the pool by type and characteristics, in name order -- never by name --
so the boards are the same on every run.

The default run is the harness's self-check, both sides the same apply,
proving the comparison deterministic (exit criterion 5). ``--switched``
compares, for every (handler, host) pair the gate-parity closure puts on the
new path, the switched carrier (which takes `effect_resolver.resolve_ability`
for the host) against its legacy apply (`effect_resolver.legacy_only`), and
requires the dispatcher to have been entered; ``--pool --record`` writes the
proven pairs to ``tools/host_harness_switched.json``, the record gate parity
holds every new-path pair to (A38), and ``--pool --check`` fails when that
record is stale.

Legacy applies by host kind: a SPELL host resolves through the spell
resolution path (`GameState._execute_spell_effects` on a StackItem); a MODE
through `resolve_spell_from_oracle` with the mode's clause; an ACTIVATED host
through `activated_effects.resolve_activated_ability`; a LOYALTY host
through `PlaneswalkerManager._resolve` when its kind is executable; an
enter trigger (a front-face TRIGGERED host whose head is SELF_ENTERS)
through the card's ETB registry handler when it has one, else
`oracle_resolver.resolve_etb_from_oracle`, the enter resolver -- the
engine's order on entry -- with the source on the battlefield and no
targets (the engine resolves enter triggers on entry); a typed draw
trigger (a front-face TRIGGERED host whose head is a DRAW event with a
typed `TriggerHead.draw`) through a draw by the player its head names, the
draw owner running the whole DRAW fan-out. Every other host
kind has no single legacy apply (other
triggered and static abilities resolve through their own carriers) and is
reported as skipped, by kind.

Each apply gets the legacy targets one deterministic rule chooses on that
board from the host's requirements (`legacy_targets`), so a targeted host
does something where the board offers a target. The report says, per host,
whether its resolution changed the state on any board (against the board
with only the source placed); the hosts that change nothing anywhere are
listed (`noop_hosts`), so determinism is never claimed on no-ops alone.

Usage::

    python tools/host_resolution_equivalence.py            # deck MB + SB
    python tools/host_resolution_equivalence.py --pool     # whole pool
    python tools/host_resolution_equivalence.py --card NAME --json
    python tools/host_resolution_equivalence.py --switched          # deck MB + SB
    python tools/host_resolution_equivalence.py --switched --pool --record
    python tools/host_resolution_equivalence.py --switched --pool --check
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import enum
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BOARDS = ("empty", "opponent_creatures", "own_creatures", "lands", "stack",
          "graveyards")
SEEDS = (0, 1)
# Board sizes: enough objects that "each", "up to N" and "target" choices
# have more than one candidate, few enough that a copy stays cheap.
LIBRARY_SIZE = 20
HAND_SIZE = 3
BOARD_CREATURES = 4
BOARD_LANDS = 3
GRAVEYARD_CARDS = 3
MAIN_PHASE_TURN = 5
CONTROLLER = 0


# ── canonical state digest ────────────────────────────────────────────

_ZONES = ("library", "hand", "battlefield", "graveyard", "exile")


def _canon(v: Any, seen: set, depth: int = 0) -> Any:
    """A deterministic, address-free form of any engine value: cards by
    instance id, templates by name, callables by qualified name, enums by
    name, sets sorted, objects by their fields; cycles cut."""
    from engine.cards import CardInstance, CardTemplate
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, enum.Enum):
        return f"{type(v).__name__}.{v.name}"
    if isinstance(v, CardInstance):
        return f"card#{v.instance_id}"
    if isinstance(v, CardTemplate):
        return f"template:{v.name}"
    if callable(v) and not dataclasses.is_dataclass(v):
        fn = getattr(v, "func", v)          # functools.partial
        return f"fn:{getattr(fn, '__qualname__', type(fn).__name__)}"
    if isinstance(v, (set, frozenset)):
        return sorted((_canon(x, seen, depth + 1) for x in v), key=repr)
    if isinstance(v, (list, tuple)):
        return [_canon(x, seen, depth + 1) for x in v]
    if isinstance(v, dict):
        return sorted(([_canon(k, seen, depth + 1), _canon(x, seen, depth + 1)]
                       for k, x in v.items()), key=lambda kv: repr(kv[0]))
    if id(v) in seen or depth > 8:
        return f"<{type(v).__name__}>"
    seen = seen | {id(v)}
    if dataclasses.is_dataclass(v):
        items = [(f.name, getattr(v, f.name, None))
                 for f in dataclasses.fields(v)]
    elif hasattr(v, "__dict__"):
        items = sorted(vars(v).items())
    elif hasattr(v, "__slots__"):
        items = [(s, getattr(v, s, None)) for s in v.__slots__]
    else:
        return f"<{type(v).__name__}>"
    return [type(v).__name__] + [
        [k, _canon(x, seen, depth + 1)] for k, x in items
        if not k.startswith("_game")]


def _card_state(c) -> list:
    out = [c.name, c.instance_id]
    for f in dataclasses.fields(c):
        if f.name in ("template",) or f.name.startswith("_game"):
            continue
        out.append([f.name, _canon(getattr(c, f.name, None), set())])
    return out


def canonical_state(game) -> list:
    """The canonical game state the digest hashes."""
    players = []
    for p in game.players:
        rec = [p.player_idx]
        for f in dataclasses.fields(p):
            if f.name in _ZONES or f.name == "sideboard" or \
                    f.name.startswith("_"):
                continue
            rec.append([f.name, _canon(getattr(p, f.name), set())])
        for z in _ZONES:
            rec.append([z, [_card_state(c) for c in getattr(p, z)]])
        players.append(rec)
    stack = [_canon(item, set()) for item in getattr(game.stack, "items",
                                                     ())]
    return [
        ["players", players],
        ["stack", stack],
        ["continuous_effects", _canon(game.continuous_effects, set())],
        ["delayed_triggers", _canon(game.delayed_triggers, set())],
        ["end_of_turn_exiles", _canon(game._end_of_turn_exiles, set())],
        ["end_of_turn_sacrifices",
         _canon(game._end_of_turn_sacrifices, set())],
        ["flags", [game.game_over, game.winner, game.end_turn_requested,
                   game.turn_number, game.active_player]],
    ]


def state_digest(game) -> str:
    return hashlib.sha256(json.dumps(canonical_state(game), default=repr)
                          .encode()).hexdigest()


# ── the boards ────────────────────────────────────────────────────────

def _types(t) -> set:
    return {getattr(x, "value", str(x)) for x in t.card_types or ()}


def _mv(t) -> int:
    return int(getattr(t, "cmc", 0) or 0)


class BoardPool:
    """The pool cards boards are built from, chosen by characteristics
    in name order (never by name)."""

    def __init__(self, db):
        pool = sorted({id(t): t for t in db.cards.values()}.values(),
                      key=lambda t: t.name)
        vanilla = [t for t in pool if not (t.back_face_oracle or "")
                   and " // " not in t.name]
        # every basic land, one template per name: the libraries cycle
        # through them, so a basic-type search (a fetch land) has its type
        self.basics = list({t.name: t for t in vanilla
                            if "land" in _types(t) and any(
                                getattr(s, "value", s) == "basic"
                                for s in t.supertypes or ())}.values())
        self.nonbasics = [t for t in vanilla if _types(t) == {"land"}
                          and t not in self.basics][:BOARD_LANDS]
        creatures = [t for t in vanilla if "creature" in _types(t)
                     and (t.power or 0) > 0]
        by_mv: Dict[int, Any] = {}
        for t in creatures:
            by_mv.setdefault(_mv(t), t)
        mixed = [by_mv[k] for k in sorted(by_mv)][:BOARD_CREATURES - 1]
        keyworded = next((t for t in creatures if t.keywords
                          and t not in mixed), None)
        self.creatures = mixed + ([keyworded] if keyworded else [])
        self.instant = next((t for t in vanilla if _types(t) == {"instant"}
                             and t.oracle_text), None)
        self.grave = creatures[:GRAVEYARD_CARDS]


def _add(game, template, controller, zone):
    from engine.cards import CardInstance
    c = CardInstance(template=template, owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    if zone != "stack":
        getattr(game.players[controller], zone).append(c)
    return c


def build_board(pool: BoardPool, board: str):
    """A fresh game on `board`, the controller's main phase."""
    from engine.game_state import GameState, Phase
    from engine.stack import StackItem, StackItemType
    game = GameState(rng=random.Random(0))
    game.active_player = CONTROLLER
    game.priority_player = CONTROLLER
    game.current_phase = Phase.MAIN1
    game.turn_number = MAIN_PHASE_TURN
    basic = pool.basics[0] if pool.basics else None
    for p in (0, 1):
        for i in range(LIBRARY_SIZE):
            src = pool.grave if (pool.grave and i % 4 == 3) else pool.basics
            _add(game, src[i % len(src)], p, "library")
        for i in range(HAND_SIZE):
            _add(game, (pool.creatures + [basic])[i % (len(pool.creatures)
                                                       + 1)], p, "hand")
        for i in range(BOARD_LANDS):
            _add(game, pool.basics[i % len(pool.basics)], p, "battlefield")
    if board == "opponent_creatures":
        for t in pool.creatures:
            _add(game, t, 1 - CONTROLLER, "battlefield")
    elif board == "own_creatures":
        for t in pool.creatures:
            _add(game, t, CONTROLLER, "battlefield")
    elif board == "lands":
        for p in (0, 1):
            for t in pool.nonbasics:
                _add(game, t, p, "battlefield")
    elif board == "stack" and pool.instant is not None:
        c = _add(game, pool.instant, 1 - CONTROLLER, "stack")
        game.stack.push(StackItem(item_type=StackItemType.SPELL, source=c,
                                  controller=1 - CONTROLLER))
    elif board == "graveyards":
        for p in (0, 1):
            for t in pool.grave:
                _add(game, t, p, "graveyard")
    return game


# ── hosts and their legacy applies ────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class HostCase:
    card: str
    host: str          # "<KIND>:<face>:<index>[:mode i]"
    kind: str
    key: Any = None    # mode index / activation index / (face, slot)


def host_cases(template, effects) -> Tuple[List[HostCase], List[HostCase]]:
    """(resolvable cases, skipped cases) of every host of `template`."""
    from engine.cards import ActivationEffectKind, CardType
    from engine.effect_spec import EventHint, HostKind
    from engine.planeswalker_manager import EXECUTABLE_LOYALTY_KINDS
    ok, skipped = [], []
    types = set(template.card_types or ())
    spell_card = bool(types & {CardType.INSTANT, CardType.SORCERY})
    modes = getattr(template, "modes", None) or []
    loyalty = {}
    for face, attr in ((0, "loyalty_abilities"),
                       (1, "back_face_loyalty_abilities")):
        for slot, ab in (getattr(template, attr, None) or {}).items():
            loyalty[(face, slot)] = ab
    acts = {a.index: a for a in template.activated_abilities or ()}
    for h in effects.walk(include_sub=False):
        label = f"{h.kind.name}:{h.face}:{h.index}"
        if h.kind is HostKind.SPELL and spell_card and h.face == 0:
            ok.append(HostCase(template.name, label, "SPELL"))
        elif h.kind is HostKind.MODE and spell_card and \
                0 <= h.mode_index < len(modes):
            ok.append(HostCase(template.name, f"{label}:mode{h.mode_index}",
                               "MODE", h.mode_index))
        elif h.kind is HostKind.ACTIVATED and h.activation_index in acts \
                and acts[h.activation_index].effect_kind not in (
                    None, ActivationEffectKind.UNCLASSIFIED):
            # an UNCLASSIFIED activation is refused by the activation
            # path (its legacy owner, if any, is another carrier: a fetch
            # land's sacrifice-and-search, a land's own manager), so
            # `resolve_activated_ability` is not its apply
            ok.append(HostCase(template.name, label, "ACTIVATED",
                               h.activation_index))
        elif h.kind is HostKind.LOYALTY and \
                (h.face, h.loyalty_slot) in loyalty and \
                loyalty[(h.face, h.loyalty_slot)].effect_kind in \
                EXECUTABLE_LOYALTY_KINDS:
            ok.append(HostCase(template.name, label, "LOYALTY",
                               (h.face, h.loyalty_slot)))
        elif h.kind is HostKind.TRIGGERED and h.face == 0 and \
                h.trigger is not None and (
                    EventHint.SELF_ENTERS in h.trigger.event_hints or (
                        EventHint.DRAW in h.trigger.event_hints
                        and h.trigger.draw is not None)):
            # an enter trigger: its legacy apply is the enter resolver; a
            # typed draw trigger: a draw (a head naming both -- "When ~
            # enters and whenever an opponent draws" -- has both cases)
            if EventHint.SELF_ENTERS in h.trigger.event_hints:
                ok.append(HostCase(template.name, label, "ETB", h.index))
            if EventHint.DRAW in h.trigger.event_hints and \
                    h.trigger.draw is not None:
                ok.append(HostCase(template.name, label, "DRAW", h.index))
        else:
            skipped.append(HostCase(template.name, label, h.kind.name))
    return ok, skipped


def _draw_head(template, case: HostCase):
    """The trigger head of a DRAW case's host."""
    from engine.effect_spec import HostKind
    return next(h.trigger for h in template.effects.front()
                if h.kind is HostKind.TRIGGERED and h.index == case.key)


def _activated(template, case: HostCase):
    return next(a for a in template.activated_abilities
                if a.index == case.key)


def legacy_place(game, template, case: HostCase):
    """Put the source where its legacy apply expects it: the stack for a
    spell or mode, the battlefield for an ability (a loyalty source with
    its printed loyalty, transformed for a back-face line)."""
    if case.kind in ("SPELL", "MODE"):
        return _add(game, template, CONTROLLER, "stack")
    card = _add(game, template, CONTROLLER, "battlefield")
    if case.kind == "LOYALTY":
        if case.key[0] == 1:
            card.is_transformed = True
        card.loyalty_counters = template.loyalty or 0
    return card


def legacy_requirements(template, case: HostCase) -> List[Any]:
    """The target requirements legacy chooses for, in its flat order: the
    whole-oracle `target_solver.parse` for a spell, the mode's clause for
    a mode, the ability's parsed `target_requirements` for an activation.
    A loyalty line chooses its own objects at resolution (none here)."""
    from engine import target_solver
    if case.kind == "SPELL":
        return list(target_solver.parse(template.oracle_text or ""))
    if case.kind == "MODE":
        return list(target_solver.parse(
            template.modes[case.key].get("text", "") or ""))
    if case.kind == "ACTIVATED":
        return list(_activated(template, case).target_requirements or ())
    return []


_PLAYER_TYPES = frozenset({"any", "player", "opponent"})


def legacy_targets(game, template, case: HostCase, card) -> List[int]:
    """The legacy `targets` list for `case` on this board, by one
    deterministic rule per requirement, in requirement order: the legal
    objects `target_solver.choose_targets` picks (the opponent's first,
    then -- when the opponent has none -- the controller's own), else a
    player sentinel when the requirement admits a player (the
    controller for a "you" scope, otherwise the opponent's face), else
    nothing (the requirement has no legal choice on this board)."""
    from engine import target_solver
    from engine.constants import PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF
    out: List[int] = []
    for req in legacy_requirements(template, case):
        picked: List[Any] = []
        if req.zone != "any":
            for hostile in (True, False):
                picked = [c for c in target_solver.choose_targets(
                    game, CONTROLLER, req, hostile=hostile, exclude=card,
                    source=card) if c.instance_id not in out]
                if picked:
                    break
        if picked:
            out += [c.instance_id for c in picked]
        elif req.zone == "any" or set(req.types) & _PLAYER_TYPES:
            out.append(PLAYER_TARGET_SELF if req.owner_scope == "you"
                       else PLAYER_TARGET_OPPONENT)
    return out


def legacy_apply(game, template, case: HostCase) -> Any:
    """Resolve `case` through its legacy apply on `game`, with the source
    placed (`legacy_place`) and the targets chosen (`legacy_targets`)."""
    from engine.stack import StackItem, StackItemType
    card = legacy_place(game, template, case)
    targets = legacy_targets(game, template, case, card)
    if case.kind == "SPELL":
        return game._execute_spell_effects(StackItem(
            item_type=StackItemType.SPELL, source=card,
            controller=CONTROLLER, targets=list(targets)))
    if case.kind == "MODE":
        from engine.oracle_resolver import resolve_spell_from_oracle
        mode = template.modes[case.key]
        return resolve_spell_from_oracle(
            game, card, CONTROLLER, list(targets),
            oracle_override=mode.get("text", ""),
            removal_data=mode.get("removal"))
    if case.kind == "ACTIVATED":
        from engine.activated_effects import resolve_activated_ability
        return resolve_activated_ability(game, card, CONTROLLER,
                                         list(targets),
                                         ability=_activated(template, case))
    if case.kind == "ETB":
        # the engine's order on entry: a card-name registry handler runs
        # instead of the enter resolver (ResolutionManager.
        # _handle_permanent_etb)
        from engine.card_effects import EFFECT_REGISTRY, EffectTiming
        if EFFECT_REGISTRY.has_handler(template.name, EffectTiming.ETB):
            return EFFECT_REGISTRY.execute(template.name, EffectTiming.ETB,
                                           game, card, CONTROLLER,
                                           targets=None, item=None)
        from engine.oracle_resolver import resolve_etb_from_oracle
        return resolve_etb_from_oracle(game, card, CONTROLLER)
    if case.kind == "DRAW":
        # the draw the head names, by the player it names (an opponent
        # for "an opponent" / "a player"), after the cards an Nth-card
        # head counts; the draw owner runs the whole fan-out
        draw = _draw_head(template, case).draw
        drawer = CONTROLLER if draw.drawer == "you" else 1 - CONTROLLER
        game.players[drawer].cards_drawn_this_turn = \
            (draw.nth - 1) if draw.nth else 0
        return len(game.draw_cards(drawer, 1))
    from engine.planeswalker_manager import PlaneswalkerManager
    face, slot = case.key
    attr = "loyalty_abilities" if face == 0 else \
        "back_face_loyalty_abilities"
    return PlaneswalkerManager._resolve(game, CONTROLLER, card,
                                        getattr(template, attr)[slot])


def placed_digest(base, template, case: HostCase) -> str:
    """The digest of `base` with only the source placed: what a
    resolution that changes nothing leaves."""
    game = copy.deepcopy(base, _memo_for(base))
    legacy_place(game, template, case)
    return state_digest(game)


def _memo_for(game) -> dict:
    """A deepcopy memo that shares every template (immutable card data)
    instead of copying the pool."""
    memo = {}
    for p in game.players:
        for z in _ZONES + ("sideboard",):
            for c in getattr(p, z):
                memo[id(c.template)] = c.template
    for item in getattr(game.stack, "items", ()):
        t = getattr(item.source, "template", None)
        if t is not None:
            memo[id(t)] = t
    return memo


@dataclasses.dataclass(frozen=True)
class Outcome:
    digest: str
    log: str
    result: str


def resolve_once(base, template, case: HostCase, seed: int,
                 apply: Callable = legacy_apply) -> Outcome:
    """One resolution of `case` on a deep copy of `base` with `seed`."""
    game = copy.deepcopy(base, _memo_for(base))
    game.rng.seed(seed)
    random.seed(seed)
    start = len(game.log)
    try:
        r = apply(game, template, case)
        result = repr(r) if isinstance(r, (bool, int, type(None))) else \
            type(r).__name__
    except Exception as e:                  # the outcome is compared too
        result = f"raised {type(e).__name__}: {e}"
    log = "\n".join(game.log[start:])
    return Outcome(state_digest(game), log, result)


@dataclasses.dataclass
class Divergence:
    card: str
    host: str
    board: str
    seed: int
    what: str


def compare_host(boards: Dict[str, Any], template, case: HostCase, *,
                 seeds: Iterable[int] = SEEDS,
                 left: Callable = legacy_apply,
                 right: Callable = legacy_apply) -> List[Divergence]:
    """Every (board, seed) where the two sides differ."""
    out = []
    for name, base in boards.items():
        for seed in seeds:
            a = resolve_once(base, template, case, seed, left)
            b = resolve_once(base, template, case, seed, right)
            for what in ("digest", "log", "result"):
                if getattr(a, what) != getattr(b, what):
                    out.append(Divergence(case.card, case.host, name, seed,
                                          what))
    return out


def self_check(db, templates: Iterable[Any], *, seeds=SEEDS,
               boards: Iterable[str] = BOARDS) -> dict:
    """E0: legacy against legacy on every resolvable host of `templates`."""
    from engine.effect_grammar import parse_template
    pool = BoardPool(db)
    built = {b: build_board(pool, b) for b in boards}
    hosts = skipped = 0
    divergences: List[Divergence] = []
    raised = 0
    noop: List[List[str]] = []
    skipped_by_kind: Dict[str, int] = {}
    t0 = time.process_time()
    for t in templates:
        ok, skip = host_cases(t, parse_template(t))
        skipped += len(skip)
        for c in skip:
            skipped_by_kind[c.kind] = skipped_by_kind.get(c.kind, 0) + 1
        for case in ok:
            hosts += 1
            divergences += compare_host(built, t, case, seeds=seeds)
            # one outcome sample per host for the report's raise count
            o = resolve_once(built["empty"], t, case, 0)
            raised += o.result.startswith("raised")
            # does the resolution change state on any board (first seed)?
            if not any(resolve_once(b, t, case, seeds[0]).digest !=
                       placed_digest(b, t, case) for b in built.values()):
                noop.append([case.card, case.host])
    return {"hosts": hosts, "skipped": skipped,
            "skipped_by_kind": dict(sorted(skipped_by_kind.items())),
            "boards": list(built), "seeds": list(seeds),
            "raised_on_empty": raised,
            "state_changing_hosts": hosts - len(noop),
            "noop_hosts": sorted(noop),
            "divergences": [dataclasses.asdict(d) for d in divergences],
            "cpu_s": round(time.process_time() - t0, 2)}


SWITCHED_RECORD_PATH = REPO / "tools" / "host_harness_switched.json"


# The host case a carrier's pairs resolve through, where one host has two
# (a head naming both an enter and a draw event).
_HANDLER_CASE_KIND = {"etb:dispatch": "ETB", "draw:dispatch": "DRAW"}


def legacy_only_apply(game, template, case: HostCase) -> Any:
    """`legacy_apply` with every switched carrier on its legacy apply
    (`effect_resolver.legacy_only`): the legacy side of a switched host."""
    from engine.effect_resolver import legacy_only
    with legacy_only():
        return legacy_apply(game, template, case)


def switched_check(db, templates: Iterable[Any], *, seeds=SEEDS,
                   boards: Iterable[str] = BOARDS) -> dict:
    """A38: every (handler, host) pair of `templates` the closure puts on
    the new path, resolved through its switched carrier against its legacy
    apply on every board and seed. A pair is proven when no board diverges
    (digest, log bytes, result) and the carrier entered the dispatcher on
    some board, so the proof is about the new path, not a silent fallback."""
    if str(REPO / "tools") not in sys.path:
        sys.path.insert(0, str(REPO / "tools"))
    import effect_spec_equivalence as eq
    from engine import effect_resolver as er
    templates = list(templates)
    effects = eq.parse_effects_of(templates)
    pairs = [p for p in eq.closure(templates, effects) if p.new_path]
    by_name = {t.name: t for t in templates}
    pool = BoardPool(db)
    built = {b: build_board(pool, b) for b in boards}
    proven: List[List[str]] = []
    divergences: List[Divergence] = []
    undispatched: List[List[str]] = []
    no_case: List[List[str]] = []
    entered = [0]
    real = er.resolve_ability

    def counting(*a, **k):
        entered[0] += 1
        return real(*a, **k)

    t0 = time.process_time()
    er.resolve_ability = counting
    try:
        for p in pairs:
            key = [p.handler, p.card, p.host]
            t = by_name[p.card]
            ok, _ = host_cases(t, effects[p.card])
            want = _HANDLER_CASE_KIND.get(p.handler)
            case = next((c for c in ok if c.host == p.host
                         and (want is None or c.kind == want)), None)
            if case is None:
                no_case.append(key)
                continue
            before = entered[0]
            divs = compare_host(built, t, case, seeds=seeds,
                                left=legacy_only_apply, right=legacy_apply)
            divergences += divs
            if entered[0] == before:
                undispatched.append(key)
            elif not divs:
                proven.append(key)
    finally:
        er.resolve_ability = real
    diverging = sorted({(d.card, d.host) for d in divergences})
    by_card_host = {(k[1], k[2]): k for k in
                    [[p.handler, p.card, p.host] for p in pairs]}
    return {"pairs": len(pairs), "proven": sorted(proven),
            "diverging": sorted(list(by_card_host[k]) for k in diverging),
            "boards": list(built), "seeds": list(seeds),
            "divergences": [dataclasses.asdict(d) for d in divergences],
            "undispatched": sorted(undispatched), "no_case": sorted(no_case),
            "cpu_s": round(time.process_time() - t0, 2)}


def _record(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


def load_switched_record(path: Path = SWITCHED_RECORD_PATH) -> set:
    """The (handler, card, host) pairs the committed record proves
    harness-identical."""
    return {tuple(k) for k in _record(path).get("pairs", ())}


def load_intended_changes(path: Path = SWITCHED_RECORD_PATH) -> Dict[tuple, str]:
    """(handler, card, host) -> why its new path differs from its legacy
    apply: a behaviour change made on purpose, each with its reason."""
    return {tuple(e["pair"]): e["reason"]
            for e in _record(path).get("intended", ())}


def switched_record_json(rep: dict, intended: Dict[tuple, str]) -> str:
    return json.dumps({
        "description": (
            "Switched (handler, card, host) pairs: `pairs` proven "
            "harness-identical -- the switched carrier against its legacy "
            "apply on every board and seed, the dispatcher entered -- and "
            "`intended`, the pairs whose new path differs from the legacy "
            "apply on purpose, each with its reason (a behaviour-change "
            "commit). tools/host_resolution_equivalence.py --switched --pool "
            "--record writes `pairs` and keeps `intended`; design doc "
            "2026-09-29, section 10, A38. Gate parity holds every pair on "
            "the new path to this record."),
        "boards": rep["boards"], "seeds": rep["seeds"],
        "pairs": rep["proven"],
        "intended": [{"pair": list(k), "reason": intended[k]}
                     for k in sorted(intended)]},
        indent=1, sort_keys=True) + "\n"


def deck_templates(db) -> List[Any]:
    from decks.modern_meta import MODERN_DECKS
    names = {c for d in MODERN_DECKS.values()
             for part in ("mainboard", "sideboard")
             for c in (d.get(part) or {})}
    out = {id(t): t for n in names for t in (db.cards.get(n),)
           if t is not None}
    return sorted(out.values(), key=lambda t: t.name)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pool", action="store_true")
    ap.add_argument("--card", action="append")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    ap.add_argument("--switched", action="store_true",
                    help="each pair on the new path: switched carrier "
                         "against its legacy apply")
    ap.add_argument("--record", action="store_true",
                    help="with --switched --pool: write the proven pairs "
                         "to " + SWITCHED_RECORD_PATH.name)
    ap.add_argument("--check", action="store_true",
                    help="with --switched --pool: fail unless the proven "
                         "pairs equal the committed record")
    args = ap.parse_args(argv)
    import contextlib
    import io
    import logging
    from engine.card_database import CardDatabase
    with contextlib.redirect_stdout(io.StringIO()):
        db = CardDatabase()
    logging.disable(logging.CRITICAL)
    if args.card:
        templates = [db.cards[n] for n in args.card]
    elif args.pool:
        templates = sorted({id(t): t for t in db.cards.values()}.values(),
                           key=lambda t: t.name)
    else:
        templates = deck_templates(db)
    if args.switched:
        rep = switched_check(db, templates, seeds=tuple(args.seeds))
        intended = load_intended_changes()
        diverging = {tuple(k) for k in rep["diverging"]}
        unexplained = sorted(diverging - set(intended))
        failed = bool(unexplained or rep["undispatched"] or rep["no_case"])
        if args.json:
            print(json.dumps(rep, indent=1, sort_keys=True))
        else:
            print(f"{rep['pairs']} pairs on the new path x "
                  f"{len(rep['boards'])} boards x {len(rep['seeds'])} seeds "
                  f"(switched against legacy): {len(rep['proven'])} proven, "
                  f"{len(rep['divergences'])} divergences, "
                  f"{len(rep['undispatched'])} never dispatched, "
                  f"{len(rep['no_case'])} with no harness case; "
                  f"{rep['cpu_s']} s CPU")
            print(f"  {len(diverging)} diverging pairs, "
                  f"{len(diverging) - len(unexplained)} of them intended "
                  f"changes with a recorded reason")
            for k in unexplained[:20]:
                print(f"  UNEXPLAINED divergence {list(k)}")
            for k in rep["undispatched"][:20] + rep["no_case"][:20]:
                print(f"  {k}")
        if args.record and args.pool and not failed:
            kept = {k: r for k, r in intended.items() if k in diverging}
            SWITCHED_RECORD_PATH.write_text(switched_record_json(rep, kept))
        if args.check and args.pool:
            recorded = load_switched_record()
            proven = {tuple(k) for k in rep["proven"]}
            for k in sorted(proven ^ recorded):
                print(f"  record {'lacks' if k in proven else 'is stale for'}"
                      f" {list(k)}")
            stale_intended = sorted(set(intended) - diverging)
            for k in stale_intended:
                print(f"  intended change no longer diverges: {list(k)}")
            failed |= proven != recorded or bool(stale_intended)
        return 1 if failed else 0
    rep = self_check(db, templates, seeds=tuple(args.seeds))
    if args.json:
        print(json.dumps(rep, indent=1, sort_keys=True))
    else:
        print(f"{rep['hosts']} hosts x {len(rep['boards'])} boards x "
              f"{len(rep['seeds'])} seeds (legacy against legacy); "
              f"{rep['state_changing_hosts']} change state on some board, "
              f"{len(rep['noop_hosts'])} on none; "
              f"{rep['skipped']} hosts with no legacy apply skipped "
              f"{rep['skipped_by_kind']}; "
              f"{len(rep['divergences'])} divergences; "
              f"{rep['cpu_s']} s CPU")
        for d in rep["divergences"][:20]:
            print(f"  {d}")
    return 1 if rep["divergences"] else 0


if __name__ == "__main__":
    sys.exit(main())
