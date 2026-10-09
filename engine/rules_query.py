"""The one read path for rule-modifying effects (CR 611 / 101.2).

Every gate that asks "may this happen?", "at what cost?" or "how many?"
asks here — cast legality, cast timing, cost, draws, attacks, combat
damage — instead of reading effect state itself. Answers are defined by the
effect model (engine/effect_model.py); a family's internals move onto
registered/derived `Effect` records without its gates changing.

Internals move family by family onto the effect registry
(`ContinuousEffectsManager.rule_effects`): the cast, cost, draw and
combat families all read it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from engine.game_state import GameState


# ── Casting ──────────────────────────────────────────────────────────

def _covering(game: "GameState", player_idx: int, kind, action: str):
    """Rule effects of `kind`/`action` whose selector covers the player and
    whose printed condition holds now (CR 611.3a) — resolved (stored) and
    static (derived) alike."""
    from engine.effect_conditions import rule_condition_holds
    return [e for e in game.continuous_effects.rule_effects(game)
            if e.modification.kind is kind and e.modification.action == action
            and e.selector.covers_player(player_idx)
            and rule_condition_holds(game, e.controller, e.condition)]


def cast_prohibited(game: "GameState", player_idx: int, template) -> bool:
    """A cast prohibition covers this spell (CR 101.2): a "can't cast"
    whose filter it matches, or "can cast spells only during their own
    turns" on another player's turn."""
    from engine.effect_model import ModKind
    is_creature = template.is_creature
    for e in _covering(game, player_idx, ModKind.PROHIBIT, "cast"):
        f = e.modification.get("filter")
        if (f == "all" or (f == "noncreature" and not is_creature)
                or (f == "creature" and is_creature)):
            return True
    return (game.active_player != player_idx and bool(
        _covering(game, player_idx, ModKind.PROHIBIT, "cast_outside_own_turn")))


def activation_prohibited(game: "GameState", player_idx: int, perm) -> bool:
    """A prohibition covers this player activating this permanent's
    abilities (CR 101.2, 602.5): one naming any of its current types."""
    from engine.effect_model import ModKind
    types = {t.value for t in perm.effective_card_types}
    return any(types & set(e.modification.get("sources") or ())
               for e in _covering(game, player_idx, ModKind.PROHIBIT,
                                  "activate"))


def sorcery_speed_only(game: "GameState", player_idx: int) -> bool:
    """An effect restricts this player to casting at sorcery speed."""
    from engine.effect_model import ModKind
    return bool(_covering(game, player_idx, ModKind.PROHIBIT,
                          "cast_outside_sorcery_timing"))


def cast_as_though_flash(game: "GameState", player_idx: int, template) -> bool:
    """This player may cast this spell as though it had flash (CR 702.8d)."""
    from engine.effect_model import ModKind
    types = {t for e in _covering(game, player_idx, ModKind.PERMIT, "cast_as_flash")
             for t in (e.modification.get("types") or ())}
    return (('sorcery' in types and template.is_sorcery)
            or ('creature' in types and template.is_creature))


def cost_delta(game: "GameState", player_idx: int, template) -> int:
    """Total generic cost reduction for this spell (CR 601.2f): every
    COST_DELTA effect covering the player whose rule matches the spell —
    permanents' statics and resolved "until your next turn" rules alike,
    through the one matcher."""
    from engine.effect_model import ModKind
    from engine.oracle_resolver import _cost_rule_applies
    total = 0
    for e in game.continuous_effects.rule_effects(game):
        if (e.modification.kind is ModKind.COST_DELTA
                and e.selector.covers_player(player_idx)):
            rule = dict(e.modification.data)
            if _cost_rule_applies(rule, template):
                total += rule['amount']
    return total


# ── Drawing ──────────────────────────────────────────────────────────

def draw_limit(game: "GameState", player_idx: int) -> Optional[int]:
    """The most cards this player may draw this turn, or None: the
    tightest LIMIT-draw effect covering the player (CR 101.2) — statics
    and resolved effects alike."""
    from engine.effect_model import ModKind
    caps = [e.modification.get("max")
            for e in _covering(game, player_idx, ModKind.LIMIT, "draw")]
    return min(caps) if caps else None


# ── Combat ───────────────────────────────────────────────────────────

def attack_prohibited(game: "GameState", player_idx: int) -> bool:
    """This player's creatures can't attack (CR 508.1c)."""
    from engine.effect_model import ModKind
    return bool(_covering(game, player_idx, ModKind.PROHIBIT, "attack"))


def attacking_player_prohibited(game: "GameState", defender_idx: int) -> bool:
    """Creatures can't attack this player (CR 508.1c)."""
    from engine.effect_model import ModKind
    return bool(_covering(game, defender_idx, ModKind.PROHIBIT, "be_attacked"))


def combat_damage_prevented(game: "GameState") -> bool:
    """All combat damage is prevented (CR 615, Fog class)."""
    from engine.effect_model import ModKind
    return any(e.modification.kind is ModKind.PREVENT_DAMAGE
               and e.modification.action == "combat"
               for e in game.continuous_effects.rule_effects(game))


def object_prohibited(game: "GameState", card, action: str) -> bool:
    """A PROHIBIT effect on this object (or on a class it is in) forbids `action` ('attack' /
    'block') (CR 508.1c / 509.1b). Object-scoped prohibitions come only
    from resolved effects, so only the stored ones are read (this gate runs
    in every attack/block enumeration)."""
    from engine.effect_model import ModKind, SelectorKind
    for e in game.continuous_effects._rule_effects:
        if (e.selector.kind in (SelectorKind.OBJECT, SelectorKind.FILTER)
                and e.modification.kind is ModKind.PROHIBIT
                and e.modification.action == action
                and e.selector.covers_object(card)):
            return True
    return False
