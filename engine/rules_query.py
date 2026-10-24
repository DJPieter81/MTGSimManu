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

def _covering(game: "GameState", player_idx: int, kind, action):
    """Rule effects of `kind`/`action` (one action, or a tuple of them)
    whose selector covers the player and whose printed condition holds now
    (CR 611.3a) — resolved (stored) and static (derived) alike."""
    from engine.effect_conditions import rule_condition_holds
    actions = action if isinstance(action, tuple) else (action,)
    return [e for e in game.continuous_effects.rule_effects(game)
            if e.modification.kind is kind and e.modification.action in actions
            and e.selector.covers_player(player_idx)
            and rule_condition_holds(game, e.controller, e.condition)]


def spell_kind(template) -> tuple:
    """What a spell is as it is cast, for a cast cap's filter: its card
    types and its subtypes, lowercased (CR 205)."""
    return (frozenset(ct.value for ct in template.card_types),
            frozenset(str(st).lower() for st in template.subtypes or ()))


def cast_filter_admits(spell_filter, kind: tuple) -> bool:
    """Does a cast cap's filter name a spell of this kind (`spell_kind`)?
    The vocabulary: every spell (no filter), noncreature, nonartifact and
    non-Phyrexian spells. An unknown filter names none."""
    types, subtypes = kind
    return (spell_filter is None
            or (spell_filter == "noncreature" and "creature" not in types)
            or (spell_filter == "nonartifact" and "artifact" not in types)
            or (spell_filter == "non_phyrexian" and "phyrexian" not in subtypes))


def cast_prohibited(game: "GameState", player_idx: int, template) -> bool:
    """A cast prohibition covers this spell (CR 101.2): a "can't cast"
    whose filter it matches, "can cast spells only during their own
    turns" on another player's turn, or a turn-scoped cap the spell would
    exceed -- "can't cast more than N <filter> spells each turn", counted
    over the spells the player has cast this turn that its filter names,
    those cast before the cap arrived included."""
    from engine.effect_model import ModKind
    is_creature = template.is_creature
    for e in _covering(game, player_idx, ModKind.PROHIBIT, "cast"):
        f = e.modification.get("filter")
        if (f == "all" or (f == "noncreature" and not is_creature)
                or (f == "creature" and is_creature)):
            return True
    if game.active_player != player_idx and bool(
            _covering(game, player_idx, ModKind.PROHIBIT, "cast_outside_own_turn")):
        return True
    return cast_cap_reached(game, player_idx, template)


def cast_cap_reached(game: "GameState", player_idx: int, template) -> bool:
    """A LIMIT-cast effect covering this player (CR 101.2) names this spell
    and the player has already cast its count of the spells it names this
    turn."""
    from engine.effect_model import ModKind
    kind = spell_kind(template)
    cast = getattr(game.players[player_idx], "spells_cast_this_turn_kinds", ())
    for e in _covering(game, player_idx, ModKind.LIMIT, "cast"):
        f = e.modification.get("filter")
        if cast_filter_admits(f, kind) and \
                sum(cast_filter_admits(f, k) for k in cast) >= e.modification.get("max"):
            return True
    return False


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


def cast_time_restricted(game: "GameState", player_idx: int) -> bool:
    """A printed timing restriction forbids this player casting now (CR
    101.2, 307.1): "can cast spells only any time they could cast a
    sorcery" outside the player's own main phase with an empty stack. Every
    cast route asks it -- a free or alternative cast is still a cast."""
    from engine.game_state import Phase
    if not sorcery_speed_only(game, player_idx):
        return False
    return not (game.active_player == player_idx and game.stack.is_empty
                and game.current_phase in (Phase.MAIN1, Phase.MAIN2))


def permitted_objects(game: "GameState", player_idx: int) -> dict:
    """{instance id: (zone, actions)} for every object a resolved "you may
    play / cast those cards" lets this player play now (CR 305.1, 601.2a)
    -- one pass over the rule effects for a whole zone's query."""
    from engine.effect_model import ModKind
    out: dict = {}
    for e in _covering(game, player_idx, ModKind.PERMIT, ("play", "cast")):
        start = e.modification.get("from_turn")
        if start is not None and game.turn_number < start:
            continue                       # not yet (warp: a later turn)
        zone = e.modification.get("zone")
        for oid in e.modification.get("objects") or ():
            out.setdefault(oid, (zone, set()))[1].add(e.modification.action)
    return out


def play_permitted(game: "GameState", player_idx: int, card,
                   permitted: Optional[dict] = None) -> bool:
    """A permission lets this player play this object where it is (CR
    305.1, 601.2a): a resolved "you may play / cast those cards" naming it,
    while it lasts and while the object is still in the zone it named.
    "Cast" covers spells only; a land needs "play". `permitted` is a
    `permitted_objects` answer the caller already holds."""
    entry = (permitted if permitted is not None
             else permitted_objects(game, player_idx)).get(card.instance_id)
    if entry is None or card.zone != entry[0]:
        return False
    actions = entry[1]
    return "play" in actions or (not card.template.is_land
                                 and "cast" in actions)


def permitted_cards(game: "GameState", player_idx: int) -> list:
    """The cards a permission lets this player play from where they are
    now (CR 305.1, 601.2a), in zone order. Timing and cost are the play's
    own checks (`can_cast`, the land play). An exiled card stays in its
    OWNER's exile (CR 108.3, 400.3) -- "exile the top card of that
    player's library ... you may cast that card" -- so every player's
    exile is read. A card the player owns outside the game -- its
    sideboard (CR 400.11a) -- is read when a permission names it ("you may
    play a card you own from outside the game this turn")."""
    outside = game.continuous_effects.permits_zone("sideboard")
    if not outside and not any(p.exile for p in game.players):
        return []
    permitted = permitted_objects(game, player_idx)
    if not permitted:
        return []
    cards = [c for p in game.players for c in p.exile
             if play_permitted(game, player_idx, c, permitted)]
    if outside:
        cards += [c for c in game.players[player_idx].sideboard
                  if play_permitted(game, player_idx, c, permitted)]
    return cards


def permission_ends_this_turn(game: "GameState", player_idx: int,
                              card) -> bool:
    """Every permission that lets this player play this object ends at
    this turn's cleanup (CR 514.2, 611.2): playing it is now or never.
    Asked of the one expiry predicate, `Duration.expired_by`, with this
    turn's cleanup event."""
    from engine.effect_model import ModKind
    from engine.turn_clock import Clock, ClockEvent
    naming = [e for e in _covering(game, player_idx, ModKind.PERMIT,
                                   ("play", "cast"))
              if card.instance_id in (e.modification.get("objects") or ())]
    cleanup = ClockEvent(Clock.CLEANUP, game.active_player,
                         turn=game.turn_number)
    return bool(naming) and all(e.duration.expired_by(cleanup)
                                for e in naming)


def share_one_play(game: "GameState", player_idx: int, a, b) -> bool:
    """Playing one of these objects spends the play the other needs:
    neither is in a hand, and every permission that lets this player play
    `a` names `b` too and allows a single play of the objects it names
    ("you may play a card you own from outside the game this turn", CR
    400.11a; `ContinuousEffectsManager.spend_play`)."""
    from engine.effect_model import ModKind
    if a is b or a.zone == "hand" or b.zone == "hand":
        return False
    naming = [e for e in _covering(game, player_idx, ModKind.PERMIT,
                                   ("play", "cast"))
              if a.instance_id in (e.modification.get("objects") or ())]
    return bool(naming) and all(
        e.modification.get("plays") == 1   # "a card": one play
        and b.instance_id in (e.modification.get("objects") or ())
        for e in naming)


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
    from engine import rules_audit
    total = 0
    for e in game.continuous_effects.rule_effects(game):
        if (e.modification.kind is ModKind.COST_DELTA
                and e.selector.covers_player(player_idx)):
            rule = dict(e.modification.data)
            if _cost_rule_applies(rule, template):
                total += rule['amount']
                if rules_audit.enabled():
                    _audit_reduction_names_the_spell(game, rule, template)
    return total


_AUDIT_COLOR_WORDS = {'W': 'white', 'U': 'blue', 'B': 'black', 'R': 'red',
                      'G': 'green'}


def _audit_reduction_names_the_spell(game, rule: dict, template) -> None:
    """Audit (CR 601.2f, 105.2, 205): a reduction applied to a spell names
    it -- the spell's printed colours, types, supertypes and subtypes hold
    every quality word of one of the rule's alternatives. Restated from the
    raw printed fields as one set of words, not through the matcher."""
    from engine import rules_audit
    types = {t.value for t in template.card_types}
    words = ({_AUDIT_COLOR_WORDS[c.value] for c in template.colors
              if c.value in _AUDIT_COLOR_WORDS}
             | types | {s.value for s in template.supertypes}
             | {s.lower() for s in template.subtypes})
    if not template.colors:
        words.add('colorless')
    elif len(template.colors) == 1:
        words.add('monocolored')
    else:
        words.add('multicolored')
    if types & {'artifact', 'creature', 'enchantment', 'land',
                'planeswalker', 'battle'}:
        words.add('permanent')
    if 'artifact' in words or 'legendary' in words or 'saga' in words:
        words.add('historic')
    named = any(all((w in words) if not (w.startswith('non') and len(w) > 3)
                    else (w[3:] not in words) for w in alt)
                for alt in rule['qualities'])
    rules_audit.check(
        "601.2f/reduction_names_the_spell", named,
        f"a reduction for {rule['qualities']} applied to {template.name}",
        game=game)


# ── Drawing ──────────────────────────────────────────────────────────

def draw_limit(game: "GameState", player_idx: int) -> Optional[int]:
    """The most cards this player may draw this turn, or None: the
    tightest LIMIT-draw effect covering the player (CR 101.2) — statics
    and resolved effects alike."""
    from engine.effect_model import ModKind
    caps = [e.modification.get("max")
            for e in _covering(game, player_idx, ModKind.LIMIT, "draw")]
    return min(caps) if caps else None


def draw_replacements(game: "GameState", player_idx: int) -> list:
    """Every draw replacement covering this player's draws (CR 614.1a), as
    its typed spec (`parse_draw_replacement`), one per effect: each
    applies once to an event (CR 614.5) and all of them apply (CR 616.1)."""
    from engine.effect_model import ModKind
    return [dict(e.modification.data)
            for e in _covering(game, player_idx, ModKind.REPLACE_DRAW, "draw")]


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


# ── Countering ───────────────────────────────────────────────────────

# The spell descriptions a "can't be countered" prohibition is read over: a
# spell (zone stack) of any of `types` or none of `not_types` (CR 205.2a),
# controlled by the prohibition's controller ("you control") or anyone.
COUNTER_FILTER_KEYS = frozenset({"zone", "types", "not_types", "controller"})


def counter_text(template) -> tuple:
    """`template`'s counter text: ``(prints_uncounterable, grants)``, where
    `grants` holds, per face, the spell descriptions its printed statics say
    "can't be countered" (`continuous_effects._face_counter_grants`). Read
    once per parse of the card: memoised on the template against its
    parsed-effects object, which is taken without re-deriving its parse key
    -- a card's printed text is fixed during a game, and these are read on
    every cast the AI weighs."""
    effects = getattr(template, "_effects", None)
    if effects is None:
        effects = getattr(template, "effects", None)
    hit = getattr(template, "_effects_counter_text", None)
    if hit is not None and hit[0] is effects:
        return hit[1]
    faces = getattr(effects, "faces", None) or ()
    from engine.continuous_effects import _face_counter_grants
    from engine.effect_spec import HostKind
    text = (bool(faces) and any(
                h.kind is HostKind.STATIC and h.from_zone == "stack"
                and "uncounterable" in h.flags for h in faces[0]),
            tuple(_face_counter_grants(face) for face in faces))
    try:
        # A memo of the parse, in the template's `_effects*` memo family:
        # it derives from `_effects` alone and never changes a field.
        template._effects_counter_text = (effects, text)
    except AttributeError:       # a stand-in that takes no attribute
        pass
    return text


def prints_uncounterable(template) -> bool:
    """The spell's own "This spell can't be countered" (CR 101.2): a static
    ability that functions on the stack (CR 113.6), parsed as a STATIC host
    from the stack whose flags hold `uncounterable`. A conditional one ("If
    X is 5 or more, ...") is not parsed as one, so it is never read."""
    return counter_text(template)[0]


def _spell_described(effect, controller: int, template) -> bool:
    """Does a "can't be countered" prohibition's description name a spell
    of `template` that `controller` controls? Read over the vocabulary in
    `COUNTER_FILTER_KEYS`; a description with anything else names none
    (fail closed)."""
    f = dict(effect.selector.filter or ())
    if set(f) - COUNTER_FILTER_KEYS or f.get("zone") != "stack":
        return False
    if f.get("controller") not in (None, "you") or (
            f.get("controller") == "you" and controller != effect.controller):
        return False
    types = {ct.value for ct in template.card_types}
    if f.get("types") and not types & set(f["types"]):
        return False
    return not (f.get("not_types") and types & set(f["not_types"]))


def spell_can_be_countered(game: "GameState", controller: int,
                           template) -> bool:
    """Can a spell of `template` that `controller` controls be countered
    (CR 101.2, 701.6a)? Not when it prints "This spell can't be countered"
    (`prints_uncounterable`). Nor while a "can't be countered" prohibition
    whose description names it applies: a permanent's "<kind> spells [you
    control] can't be countered", derived while the permanent is there, or
    one a resolved effect left. Every counter reads this, and so does the
    AI."""
    if prints_uncounterable(template):
        return False
    from engine.continuous_effects import counter_prohibitions
    from engine.effect_model import SelectorKind
    return not any(e.selector.kind is SelectorKind.FILTER
                   and _spell_described(e, controller, template)
                   for e in counter_prohibitions(game))


def can_be_countered(game: "GameState", item) -> bool:
    """`spell_can_be_countered` for a stack object. An ability can always be
    countered: nothing the read path takes says otherwise."""
    from engine.stack import StackItemType
    if item.item_type is not StackItemType.SPELL or item.source is None:
        return True
    return spell_can_be_countered(game, item.controller, item.source.template)
