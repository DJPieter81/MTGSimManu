"""The modes of a modal instant or sorcery (CR 700.2).

A modal spell's controller chooses its modes as the spell is cast (CR
601.2b), as many as its header allows: "choose one", "choose two", "one or
both", "one or more". A mode that needs a target can be chosen only when a
target is chosen for it (CR 700.2a), and the spell requires only the
targets of the modes chosen (CR 601.2c). On resolution the spell performs
exactly the chosen modes, in printed order.

This module owns those rules. The choice is the controller's
(`ai.modal.select_modal_modes`, asked once, as the spell is cast); the
engine holds it to the legal modes and the printed range and records it
on the stack item (`StackItem.modes_chosen`), which a copy shares (CR
707.10).

What is read, all parsed at load:
- the range and each mode's targets and printed cost come from the typed
  spell host (`AbilityEffects.choose`, `.modes[i].targets`,
  `.modes[i].mode_cost`: the effect grammar's parse);
- each mode's clause and its typed shapes resolve it
  (`CardTemplate.modes[i]`: text, removal, land_destruction).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from .cards import CardInstance, CardTemplate
    from .game_state import GameState

# Keyword abilities that charge a cost for each mode chosen beyond the
# first (CR 702.120a, escalate). The engine pays no such cost, so a spell
# with one is cast with one mode.
_PER_EXTRA_MODE_COST_KEYWORDS = frozenset({"escalate"})
# A mode's own printed cost (tiered, CR 702.183a; spree, CR 702.172a): the
# mode is chosen by paying it. The engine pays none, so only a mode with
# no cost, or a free one, may be chosen.
_FREE_MODE_COSTS = frozenset({"", "{0}"})
# A target requirement any player satisfies ("target player", "any
# target"): a player can always be chosen, so it never makes a mode
# unchoosable.
_PLAYER_TARGET_TYPES = frozenset({"player", "any"})
# A target requirement only a player satisfies ("target player", "target
# opponent"): its player is chosen for the mode as the spell is cast
# (`choose_mode_players`).
_PLAYER_ONLY_TYPES = frozenset({"player"})


def _host(template: "CardTemplate", effects=None):
    """The typed spell host: of `effects` (a parse in hand) or else of
    the template's own parse."""
    if effects is None:
        effects = getattr(template, "effects", None)
    return effects.spell(0) if effects is not None else None


def _typed_modes(template: "CardTemplate", effects=None) -> tuple:
    """The typed spell host's modes, when they are the template's mode
    clauses (the same bullets, in order); else none."""
    host = _host(template, effects)
    clauses = getattr(template, "modes", None) or []
    if host is None or not clauses or len(host.modes) != len(clauses):
        return ()
    return host.modes


def _counters(mode_host) -> bool:
    """The mode counters a spell or ability, typed or refused: the grammar
    keeps a counter it refuses as UNMODELLED with its lemma."""
    from .effect_spec import Verb, iter_specs
    return any(s.verb is Verb.COUNTER
               or (s.verb is Verb.UNMODELLED
                   and getattr(s.payload, "lemma", "") == "counter")
               for s in iter_specs(mode_host.specs))


def in_scope(template: "CardTemplate", effects=None) -> bool:
    """The modal spells whose modes this owner chooses: an instant or
    sorcery whose controller has a choice to make (fewer modes may be
    chosen than are printed, or a range of counts). A spell with a counter
    mode takes the counterspell path (`ResolutionManager.
    _execute_spell_effects`), which resolves its targets per ability.
    `effects` is the template's parse when the caller holds it."""
    if not (template.is_instant or template.is_sorcery) \
            or getattr(template, "is_counterspell", False):
        return False
    modes = _typed_modes(template, effects)
    if not modes or any(_counters(m) for m in modes):
        return False
    lo, hi = _host(template, effects).choose
    return lo < len(modes) or hi < len(modes)


def _charges_per_extra_mode(template: "CardTemplate") -> bool:
    effects = getattr(template, "effects", None)
    return effects is not None and any(
        k.name in _PER_EXTRA_MODE_COST_KEYWORDS
        for h in effects.front() for k in h.keywords)


def choose_range(template: "CardTemplate") -> Tuple[int, int]:
    """(fewest, most) modes the controller may choose (CR 700.2), from
    the typed header; a spell charging for each extra mode is held to one
    (`_PER_EXTRA_MODE_COST_KEYWORDS`)."""
    modes = _typed_modes(template)
    if not modes:
        return (0, 0)
    lo, hi = _host(template).choose
    hi = min(hi, len(modes))
    if _charges_per_extra_mode(template):
        hi = min(hi, 1)
    return (min(lo, hi), hi)


def _needs_an_object(req) -> bool:
    """A requirement a mode cannot be chosen without: a non-optional
    target that is an object, not a player."""
    return (not req.is_optional and req.count_min > 0
            and not (req.types & _PLAYER_TARGET_TYPES))


def has_untargeted_mode(template: "CardTemplate") -> bool:
    """Some mode needs no object target and no unpaid cost: chosen alone,
    the spell requires no target (CR 601.2c)."""
    return any(m.mode_cost in _FREE_MODE_COSTS
               and not any(_needs_an_object(r) for r in m.targets)
               for m in _typed_modes(template))


def required_targets(template: "CardTemplate", requirements: list) -> list:
    """The target requirements that can stop the spell's cast (CR
    601.2c): a spell requires a mode's targets only if that mode is
    chosen, so with a mode that needs none, its modes' requirements (the
    target solver's `mode_group`) never do."""
    if not has_untargeted_mode(template):
        return requirements
    return [r for r in requirements if r.mode_group is None]


def has_targeted_mode(template: "CardTemplate") -> bool:
    return any(any(_needs_an_object(r) for r in m.targets)
               for m in _typed_modes(template))


def counter_mode_targets(template: "CardTemplate") -> Optional[tuple]:
    """The stack-zone target requirements of a modal spell's counter
    modes; None for a spell with no typed modes. A modal spell counters a
    spell only through a counter mode that may target it (CR 700.2a)."""
    modes = _typed_modes(template)
    if not modes:
        return None
    return tuple(r for m in modes if _counters(m)
                 for r in m.targets if r.zone == "stack")


def _chosen_for(game: "GameState", controller: int, req, targets: Sequence,
                source: "CardInstance") -> bool:
    """Some chosen target is a legal choice for `req` (the target
    solver's legality, CR 115.1, 601.2c)."""
    from .target_solver import enumerate_legal_targets
    legal = {c.instance_id for c in enumerate_legal_targets(
        game, controller, req, exclude=source, source=source)}
    return any(isinstance(t, int) and t in legal for t in targets or ())


def legal_modes(game: "GameState", card: "CardInstance", controller: int,
                targets: Sequence) -> List[int]:
    """The modes that may be chosen with these targets (CR 700.2a): each
    object a mode targets is among the chosen targets, and the mode has no
    unpaid cost of its own."""
    return [i for i, m in enumerate(_typed_modes(card.template))
            if m.mode_cost in _FREE_MODE_COSTS
            and all(not _needs_an_object(r)
                    or _chosen_for(game, controller, r, targets, card)
                    for r in m.targets)]


def choose_modes(game: "GameState", card: "CardInstance", controller: int,
                 targets: Sequence, x_value: int = 0) -> List[int]:
    """The modes the controller chooses as the spell is cast (CR 601.2b):
    the controller's answer, held to the legal modes and the printed
    range. Short of the printed minimum, the lowest legal modes complete
    it (a deterministic engine completion, as the land-destruction
    resolver's no-target fallback is). Returned in printed order."""
    from ai import modal as chooser
    lo, hi = choose_range(card.template)
    legal = legal_modes(game, card, controller, targets)
    picked = chooser.select_modal_modes(game, card, controller, list(targets),
                                        x_value, legal=legal, choose=(lo, hi))
    chosen: List[int] = []
    for i in picked:
        if i in legal and i not in chosen and len(chosen) < hi:
            chosen.append(i)
    for i in legal:
        if len(chosen) >= lo:
            break
        if i not in chosen:
            chosen.append(i)
    return sorted(chosen)


def _player_only(req) -> bool:
    return bool(req.types) and set(req.types) <= _PLAYER_ONLY_TYPES


def choose_mode_players(game: "GameState", card: "CardInstance",
                        controller: int, chosen: Sequence[int],
                        x_value: int = 0) -> dict:
    """The players chosen for each chosen mode's player-only target
    requirements as the spell is cast (CR 601.2c): mode index -> one tuple
    of player indices per such requirement, in printed order. Each pick is
    the controller's (`callbacks.choose_trigger_targets`, the slot-pick
    seam) out of the players the requirement admits
    (`target_solver.legal_slot_choices`); only legal, distinct players
    count, and a required player the answer leaves short is filled by the
    engine's default (`callbacks.default_trigger_targets`)."""
    from .callbacks import default_trigger_targets
    from .effect_carrier import chosen_mode_host, slot_spec
    from .target_solver import legal_slot_choices
    out = {}
    for i in chosen:
        host = chosen_mode_host(card.template, i)
        if host is None:
            continue
        picks = []
        for k, req in enumerate(host.targets):
            if not _player_only(req):
                continue
            players, _ = legal_slot_choices(game, controller, req,
                                            source=card)
            n = max(0, int(req.count_max or 0))
            asked = game.callbacks.choose_trigger_targets(
                game, controller, card, slot_spec(host, k), req,
                list(players), [], x_value=x_value) if n and players else []
            got = []
            for p in asked or ():
                if isinstance(p, int) and not isinstance(p, bool) \
                        and p in players and p not in got and len(got) < n:
                    got.append(p)
            need = min(int(req.count_min or 0), n, len(players))
            if len(got) < need:
                rest = [p for p in players if p not in got]
                got += default_trigger_targets(controller, req, rest,
                                               [])[:need - len(got)]
            picks.append(tuple(got))
        if picks:
            out[i] = tuple(picks)
    return out


def mode_targets(game: "GameState", card: "CardInstance", controller: int,
                 targets: Sequence, index: int,
                 mode_players: Optional[dict]) -> list:
    """The cast-time targets one chosen mode reads. A mode whose every
    requirement only a player satisfies reads the players chosen for it
    (`choose_mode_players`), as the engine's player markers; any other
    mode reads the spell's target list, with those players in front when
    it also has such a requirement."""
    from .constants import PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF
    from .effect_carrier import chosen_mode_host
    host = chosen_mode_host(card.template, index)
    if host is None or not any(_player_only(r) for r in host.targets):
        return list(targets)
    players = [p for slot in (mode_players or {}).get(index, ())
               for p in slot]
    markers = [PLAYER_TARGET_SELF if p == controller
               else PLAYER_TARGET_OPPONENT for p in players]
    if all(_player_only(r) for r in host.targets):
        return markers
    return markers + list(targets)


def resolve_mode(game: "GameState", card: "CardInstance", controller: int,
                 targets: Sequence, index: int, x_value: int = 0) -> bool:
    """Perform one chosen mode through the clause owner, with its own
    typed shapes; a mode a landed effect family runs is dispatched there
    (`clause_resolver`'s dispatched carrier). True when something was
    performed."""
    from .oracle_resolver import resolve_spell_from_oracle
    return resolve_spell_from_oracle(game, card, controller, list(targets),
                                     x_value=x_value,
                                     mode=card.template.modes[index])


def perform_chosen(game: "GameState", card: "CardInstance", controller: int,
                   targets: Sequence, chosen: Sequence[int],
                   x_value: int = 0, mode_players: Optional[dict] = None
                   ) -> None:
    """Perform the chosen modes in printed order (CR 700.2, 608.2c), each
    with the targets chosen for it (`mode_targets`). A mode no owner
    performs is recorded for the unhandled-effect census, as a whole
    spell's silent miss is."""
    before = _damage_snapshot(game, controller, targets)
    for idx in chosen:
        if not resolve_mode(game, card, controller,
                            mode_targets(game, card, controller, targets, idx,
                                         mode_players), idx,
                            x_value=x_value):
            from .effect_diagnostics import record_unhandled_effect
            record_unhandled_effect(card.name, "mode")
    _audit_mode_damage(game, card, controller, chosen, targets, before)


def _audit_mode_players(game: "GameState", card: "CardInstance",
                        chosen: Sequence[int],
                        mode_players: Optional[dict]) -> None:
    """CR 601.2c, restated from the raw requirements: each required
    target of a chosen mode that only a player satisfies has a player
    chosen for it as the spell was cast."""
    from . import rules_audit
    if not rules_audit.enabled():
        return
    host = _host(card.template)
    if host is None:
        return
    problems = []
    for i in chosen:
        if not 0 <= i < len(host.modes):
            continue
        required = [r for r in host.modes[i].targets
                    if r.types and set(r.types) <= {"player"}
                    and not r.is_optional and r.count_min > 0]
        picked = list((mode_players or {}).get(i, ()))
        for k, _ in enumerate(required):
            if k >= len(picked) or not picked[k]:
                problems.append(f"mode {i} performed with no player chosen")
    rules_audit.check("601.2c/mode_player_chosen", not problems,
                      f"{card.name}: {'; '.join(problems)}", game=game)


def _damage_snapshot(game: "GameState", controller: int,
                     targets: Sequence) -> dict:
    """What each chosen target was before the modes: a permanent's damage,
    loyalty and battlefield entry; a player's life."""
    from . import rules_audit
    if not rules_audit.enabled():
        return {}
    from .target_solver import player_index_for_target
    out = {}
    for t in targets or ():
        p = player_index_for_target(game, controller, t)
        if p is not None:
            out[t] = ("player", p, game.players[p].life)
            continue
        obj = game.get_card_by_id(t) if isinstance(t, int) else None
        if obj is not None and obj.zone == "battlefield":
            out[t] = ("permanent", obj, obj.damage_marked,
                      obj.loyalty_counters, obj.battlefield_entry_seq)
    return out


def _audit_mode_damage(game: "GameState", card: "CardInstance",
                       controller: int, chosen: Sequence[int],
                       targets: Sequence, before: dict) -> None:
    """CR 120.3, restated from the typed modes' raw specs: a performed
    mode that deals a printed amount of damage to its target dealt it --
    the target creature was marked that much more (or left the
    battlefield, its damage lethal), a planeswalker lost that much
    loyalty, a player that much life. A target illegal on resolution (CR
    608.2b) is skipped. Observation only."""
    from . import rules_audit
    if not rules_audit.enabled() or len(before) != 1:
        return
    from .effect_spec import AmountKind, Verb, iter_specs
    from .target_solver import can_be_targeted
    modes = card.template.effects.modes(0)
    amounts = [s.amount.n for i in chosen if 0 <= i < len(modes)
               for s in iter_specs(modes[i].specs)
               if s.verb is Verb.DAMAGE and s.target_slot == 0
               and s.amount is not None
               and s.amount.kind is AmountKind.LITERAL]
    if len(amounts) != 1:
        return
    n = amounts[0]
    (kind, *rest), = before.values()
    if kind == "player":
        p, life = rest
        dealt = life - game.players[p].life
    else:
        obj, marked, loyalty, entry = rest
        if not can_be_targeted(obj, card, controller):
            return
        if obj.zone != "battlefield" or obj.battlefield_entry_seq != entry:
            return                    # left: lethal, or a replacement
        dealt = max(obj.damage_marked - marked, loyalty - obj.loyalty_counters)
    rules_audit.check("120.3/chosen_mode_damage", dealt >= n,
                      f"{card.name}: a chosen mode dealt {dealt}, not {n}",
                      game=game)


def audit_chosen(game: "GameState", card: "CardInstance",
                 chosen: Sequence[int], targets: Sequence,
                 mode_players: Optional[dict] = None) -> None:
    """CR 700.2 / 700.2a / 601.2c, restated from the typed host's raw
    fields (not from `legal_modes` or `choose_mode_players`): a resolving
    modal spell performs a number of modes its header allows, every mode
    it performs that targets an object had a chosen target in that
    object's zone, and every mode it performs that targets a player had a
    player chosen for it. Observation only."""
    _audit_mode_players(game, card, chosen, mode_players)
    from . import rules_audit
    if not rules_audit.enabled():
        return
    host = _host(card.template)
    if host is None:
        return
    lo, hi = host.choose
    problems = []
    if not lo <= len(chosen) <= hi:
        problems.append(f"{len(chosen)} modes, header allows {lo}-{hi}")
    zones = set()
    for t in targets or ():
        obj = game.get_card_by_id(t) if isinstance(t, int) and t > 0 else None
        if obj is not None:
            zones.add(obj.zone)
    for i in chosen:
        if not 0 <= i < len(host.modes):
            problems.append(f"mode {i} not printed")
            continue
        for r in host.modes[i].targets:
            if (not r.is_optional and r.count_min > 0
                    and not r.types & {"player", "any"}
                    and r.zone not in zones):
                problems.append(f"mode {i} performed with no {r.zone} target")
    rules_audit.check("700.2a/modes_chosen", not problems,
                      f"{card.name}: {'; '.join(problems)}", game=game)
