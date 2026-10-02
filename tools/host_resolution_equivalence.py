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

**In E0** no executor exists, so both sides are the legacy apply: the run
is the harness's self-check, proving the comparison deterministic (exit
criterion 5). A family's switch commit replaces one side with
`effect_resolver.resolve_ability` (``--side new``, once executors land).

Legacy applies by host kind: a SPELL host resolves through the spell
resolution path (`GameState._execute_spell_effects` on a StackItem); a MODE
through `resolve_spell_from_oracle` with the mode's clause; an ACTIVATED host
through `activated_effects.resolve_activated_ability`; a LOYALTY host
through `PlaneswalkerManager._resolve` when its kind is executable. Every
other host kind has no single legacy apply in E0 (triggered and static
abilities resolve through their own carriers) and is reported as skipped.

Usage::

    python tools/host_resolution_equivalence.py            # deck MB + SB
    python tools/host_resolution_equivalence.py --pool     # whole pool
    python tools/host_resolution_equivalence.py --card NAME --json
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
        vanilla = [t for t in pool if not (t.back_face_oracle or "")]
        self.basics = [t for t in vanilla if "land" in _types(t)
                       and any(getattr(s, "value", s) == "basic"
                               for s in t.supertypes or ())]
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
            src = pool.grave if (pool.grave and i % 4 == 3) else [basic]
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
    from engine.cards import CardType
    from engine.effect_spec import HostKind
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
                and acts[h.activation_index].effect_kind is not None:
            ok.append(HostCase(template.name, label, "ACTIVATED",
                               h.activation_index))
        elif h.kind is HostKind.LOYALTY and \
                (h.face, h.loyalty_slot) in loyalty and \
                loyalty[(h.face, h.loyalty_slot)].effect_kind in \
                EXECUTABLE_LOYALTY_KINDS:
            ok.append(HostCase(template.name, label, "LOYALTY",
                               (h.face, h.loyalty_slot)))
        else:
            skipped.append(HostCase(template.name, label, h.kind.name))
    return ok, skipped


def legacy_apply(game, template, case: HostCase) -> Any:
    """Resolve `case` through its legacy apply on `game`; the source is
    placed where the apply expects it (the stack for a spell or mode, the
    battlefield for an ability)."""
    from engine.stack import StackItem, StackItemType
    if case.kind in ("SPELL", "MODE"):
        card = _add(game, template, CONTROLLER, "stack")
        if case.kind == "SPELL":
            return game._execute_spell_effects(StackItem(
                item_type=StackItemType.SPELL, source=card,
                controller=CONTROLLER))
        from engine.oracle_resolver import resolve_spell_from_oracle
        mode = template.modes[case.key]
        return resolve_spell_from_oracle(
            game, card, CONTROLLER, [], oracle_override=mode.get("text", ""),
            removal_data=mode.get("removal"))
    card = _add(game, template, CONTROLLER, "battlefield")
    if case.kind == "ACTIVATED":
        from engine.activated_effects import resolve_activated_ability
        ab = next(a for a in template.activated_abilities
                  if a.index == case.key)
        return resolve_activated_ability(game, card, CONTROLLER, [],
                                         ability=ab)
    from engine.planeswalker_manager import PlaneswalkerManager
    face, slot = case.key
    attr = "loyalty_abilities" if face == 0 else \
        "back_face_loyalty_abilities"
    if face == 1:
        card.is_transformed = True
    card.loyalty_counters = template.loyalty or 0
    ab = getattr(template, attr)[slot]
    return PlaneswalkerManager._resolve(game, CONTROLLER, card, ab)


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
    t0 = time.process_time()
    for t in templates:
        ok, skip = host_cases(t, parse_template(t))
        skipped += len(skip)
        for case in ok:
            hosts += 1
            divergences += compare_host(built, t, case, seeds=seeds)
            # one outcome sample per host for the report's raise count
            o = resolve_once(built["empty"], t, case, 0)
            raised += o.result.startswith("raised")
    return {"hosts": hosts, "skipped": skipped, "boards": list(built),
            "seeds": list(seeds), "raised_on_empty": raised,
            "divergences": [dataclasses.asdict(d) for d in divergences],
            "cpu_s": round(time.process_time() - t0, 2)}


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
    rep = self_check(db, templates, seeds=tuple(args.seeds))
    if args.json:
        print(json.dumps(rep, indent=1, sort_keys=True))
    else:
        print(f"{rep['hosts']} hosts x {len(rep['boards'])} boards x "
              f"{len(rep['seeds'])} seeds (legacy against legacy); "
              f"{rep['skipped']} hosts with no legacy apply skipped; "
              f"{len(rep['divergences'])} divergences; "
              f"{rep['cpu_s']} s CPU")
        for d in rep["divergences"][:20]:
            print(f"  {d}")
    return 1 if rep["divergences"] else 0


if __name__ == "__main__":
    sys.exit(main())
