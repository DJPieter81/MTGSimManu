"""Land-denial valuation — mana-development EV for land-destruction spells.

The strategic half of the LD mechanic class (parser + resolution live in
``engine/oracle_parser.py`` / ``engine/oracle_resolver.py``; see
docs/diagnostics/2026-08-27_dimir_overperformance_root_cause.md, "LD
mechanic hole").  Board projection sees no delta from destroying a land
(lands carry no power/toughness), so the entire value of the spell lives
in the OPPONENT's mana development — the mirror image of the attacker-side
pip reasoning in ``ai.mana_planner.analyze_mana_needs``.

Two derived terms, both in the clock-delta units the rest of
``ai/ev_player`` scoring uses; zero underived constants:

* **tempo term** — while the opponent's land count sits BELOW their
  deck's curve top (max effective CMC among their unplayed nonland
  cards), each land they lose delays every future play by a land-drop
  turn.  Value per constrained turn is ``mana_clock_impact(snap)``
  (ai/clock.py: the clock change one point of mana advantage buys).  A
  flooded opponent (lands >= curve top) loses nothing: the term is
  exactly zero.  A replacement-basic rider (Cleansing-Wildfire class)
  hands the land back, so the tempo term is zero for that subclass and
  the value flows through the scarcity premium alone.

* **scarcity premium** — destroying the opponent's LAST remaining
  source of a color their unplayed spells demand strands those pips;
  each stranded pip is a spell they effectively cannot cast, valued at
  ``card_clock_impact(snap)`` (ai/clock.py: the clock change one card
  buys).  Redundant sources leave no premium.  Under a
  replacement-basic rider a color restorable by a basic land in their
  library is not stranded.

Target choice is the opponent's scarcest color source: among legal
land targets (``engine.target_solver`` enumeration — hexproof
respected), minimize the count of their other sources for the
scarcest color the land produces.

Knowledge premise: the opponent's unplayed curve / pips / basics are
read from their hand + library — the same public-decklist premise
``ai.bhi`` rests on (sims run known-list mirrors of real Modern
metagames).  All card properties come from typed ``CardTemplate``
fields; no oracle text is inspected at runtime.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from ai.clock import card_clock_impact, mana_clock_impact
from ai.mana_planner import COLOR_MAP, effective_cmc

if TYPE_CHECKING:
    from engine.cards import CardInstance, CardTemplate
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot


def _opponent_unplayed_demand(game: "GameState",
                              opp_idx: int) -> Tuple[int, Dict[str, int]]:
    """(curve_top, max_pip_by_color) over the opponent's unplayed
    nonland cards (hand + library).

    ``curve_top`` is the max effective CMC — the land count past which
    additional lands stop enabling anything new.  ``max_pip_by_color``
    mirrors ``analyze_mana_needs``: the DEEPEST single-spell colored-pip
    requirement per color (casting one {B}{B} spell needs two black
    sources simultaneously; two {B} spells do not).
    """
    opp = game.players[opp_idx]
    curve_top = 0
    pips: Dict[str, int] = {}
    for zone in (opp.hand, opp.library):
        for c in zone:
            t = c.template
            if t.is_land:
                continue
            cmc = effective_cmc(c, opp)
            if cmc is not None and cmc > curve_top:
                curve_top = cmc
            mc = t.mana_cost
            for code, attr in COLOR_MAP.items():
                n = getattr(mc, attr, 0)
                if n > pips.get(code, 0):
                    pips[code] = n
    return curve_top, pips


def _opponent_color_sources(game: "GameState",
                            opp_idx: int) -> Dict[str, int]:
    """Color code -> count of opponent battlefield lands producing it.

    A multi-colored land contributes to EACH color it produces — the
    same convention as ``EVSnapshot.my_mana_by_color``.
    """
    counts: Dict[str, int] = {}
    for perm in game.players[opp_idx].battlefield:
        if not perm.template.is_land:
            continue
        for code in (perm.template.produces_mana or []):
            counts[code] = counts.get(code, 0) + 1
    return counts


def _opponent_basic_replacement_colors(game: "GameState",
                                       opp_idx: int) -> frozenset:
    """Colors the opponent can restore by searching a basic land from
    their library — relevant only under the replacement-basic rider."""
    colors = set()
    from engine.cards import Supertype
    for c in game.players[opp_idx].library:
        t = c.template
        if t.is_land and Supertype.BASIC in getattr(t, 'supertypes', []):
            colors.update(t.produces_mana or [])
    return frozenset(colors)


def _legal_opponent_land_targets(data: dict, game: "GameState",
                                 caster_idx: int) -> List["CardInstance"]:
    """Opponent-controlled lands this LD spell may usefully target.

    Legality comes from ``target_solver`` enumeration (hexproof
    respected); the nonbasic-only condition and indestructibility
    (CR 702.12b — destroying does nothing) are filtered here because a
    target the spell cannot affect carries no denial value.  Own lands
    are excluded: denial targets the opponent's development (the
    engine's no-target fallback shares this preference).
    """
    from engine.cards import Keyword, Supertype
    from engine.target_solver import TargetRequirement, enumerate_legal_targets

    types = (frozenset({"artifact", "land"})
             if data.get('can_target_artifact') else frozenset({"land"}))
    req = TargetRequirement(zone="battlefield", types=types,
                            owner_scope="any")
    out: List["CardInstance"] = []
    for cand in enumerate_legal_targets(game, caster_idx, req):
        if cand.controller == caster_idx:
            continue
        if not cand.template.is_land:
            continue  # artifact mode of the compound form: valued by the
            #           removal/permanent-threat path, not mana denial
        if (data.get('nonbasic_only')
                and Supertype.BASIC in getattr(cand.template,
                                               'supertypes', [])):
            continue  # CR 608.2c: condition unmet — no effect
        if Keyword.INDESTRUCTIBLE in cand.keywords:
            continue  # CR 702.12b: destroy does nothing
        out.append(cand)
    return out


def _scarcity(land: "CardInstance", source_counts: Dict[str, int]) -> int:
    """How scarce this land is as a color source: the count of the
    opponent's sources for the SCARCEST color it produces.  A land
    producing no colored mana is never scarce — sentinel is the
    opponent's total source count (any colored source sorts below it).
    """
    codes = [c for c in (land.template.produces_mana or [])
             if c in COLOR_MAP]
    if not codes:
        return sum(source_counts.values()) + 1
    return min(source_counts.get(c, 0) for c in codes)


def choose_land_denial_target(tmpl: "CardTemplate", game: "GameState",
                              caster_idx: int,
                              snap: "EVSnapshot") -> Optional["CardInstance"]:
    """A land-destruction spell's target (`choose_land_denial_target_for`
    over its `land_destruction_data`)."""
    return choose_land_denial_target_for(tmpl.land_destruction_data or {},
                                         game, caster_idx, snap)


def choose_land_denial_target_for(data: dict, game: "GameState",
                                  caster_idx: int, snap: "EVSnapshot"
                                  ) -> Optional["CardInstance"]:
    """Pick the opponent land whose loss maximizes their mana deficit:
    the scarcest color source.  Ties break toward the land producing
    more colors (fixing lands cover more of the curve), then lowest
    instance id for determinism.  Returns None when no affectable
    opponent land exists.  `data` is the land-destruction shape.
    """
    cands = _legal_opponent_land_targets(data, game, caster_idx)
    if not cands:
        return None
    counts = _opponent_color_sources(game, 1 - caster_idx)
    return min(cands, key=lambda l: (
        _scarcity(l, counts),
        -len(l.template.produces_mana or []),
        l.instance_id,
    ))


def land_denial_value(tmpl: "CardTemplate", game: "GameState",
                      caster_idx: int, snap: "EVSnapshot") -> float:
    """A land-destruction spell's EV (`land_denial_value_for` over its
    `land_destruction_data`)."""
    return land_denial_value_for(tmpl.land_destruction_data or {}, game,
                                 caster_idx, snap)


def land_denial_value_for(data: dict, game: "GameState", caster_idx: int,
                          snap: "EVSnapshot") -> float:
    """EV of destroying the chosen (scarcest-source) target with this
    land-destruction shape — tempo term + scarcity premium, both derived
    from ai/clock primitives.  See module docstring for the rule each term
    encodes.
    """
    opp_idx = 1 - caster_idx
    target = choose_land_denial_target_for(data, game, caster_idx, snap)
    if target is None:
        return 0.0
    curve_top, pips = _opponent_unplayed_demand(game, opp_idx)
    counts = _opponent_color_sources(game, opp_idx)
    opp_lands = sum(1 for p in game.players[opp_idx].battlefield
                    if p.template.is_land)

    # Tempo: one land-drop turn of delay per land they still needed to
    # reach their curve top.  Zero when flooded; zero when the rider
    # hands back a replacement basic (no land-count setback).
    # The land's controller replaces it with a library basic under the
    # replacement rider -- its own ("its controller may search") or every
    # player's ("each player searches").
    replaced = bool(data.get('rider_search_basic')
                    or data.get('rider_each_player_search_basic'))
    constrained_turns = max(0, curve_top - opp_lands)
    if replaced:
        constrained_turns = 0
    tempo = mana_clock_impact(snap) * constrained_turns

    # Scarcity: pips of demanded colors the opponent can no longer
    # produce once this source is gone (and, under the replacement
    # rider, cannot restore with a library basic).
    if replaced:
        restorable = _opponent_basic_replacement_colors(game, opp_idx)
    else:
        restorable = frozenset()
    stranded_pips = 0
    for code in set(target.template.produces_mana or []):
        if code not in COLOR_MAP or code in restorable:
            continue
        need = pips.get(code, 0)
        remaining = counts.get(code, 0) - 1
        if need > remaining:
            stranded_pips += need - remaining
    premium = card_clock_impact(snap) * stranded_pips

    return tempo + premium


def host_land_destruction_data(host) -> Optional[dict]:
    """The land-destruction shape of a typed host whose first instruction
    destroys a target land (the removal family's strict shape, unit LA):
    its target's qualifiers and the basic-land searches after it, by the
    land's controller, by each player or by you. None for any other
    host."""
    from engine.effect_model import Selector, SelectorKind
    from engine.effect_spec import Ref, RefKind, Verb, iter_specs
    specs = list(iter_specs(getattr(host, "specs", ()) or ()))
    head = specs[0] if specs else None
    if head is None or head.verb is not Verb.DESTROY or head.target is None \
            or "land" not in head.target.types:
        return None
    req = head.target
    data = {'can_target_artifact': "artifact" in req.types,
            'nonbasic_only': req.supertype == "nonbasic",
            'opponent_controls_only': req.owner_scope == "opponent",
            'rider_search_basic': False,
            'rider_search_basic_tapped': False,
            'rider_each_player_search_basic': False,
            'rider_you_search_basic': False,
            'rider_damage': 0, 'rider_damage_nonbasic_only': False,
            'rider_caster_draws': 0}
    for s in specs[1:]:
        if s.verb is not Verb.SEARCH:
            continue
        a = s.actor
        if isinstance(a, Selector) and a.kind is SelectorKind.ALL_PLAYERS:
            data['rider_each_player_search_basic'] = True
        elif isinstance(a, Selector) and a.kind is SelectorKind.PLAYER:
            data['rider_you_search_basic'] = True
        elif isinstance(a, Ref) and a.kind is RefKind.CONTROLLER_OF:
            data['rider_search_basic'] = True
    return data


def own_land_loss_value(game: "GameState", player_idx: int,
                        land: "CardInstance", snap: "EVSnapshot") -> float:
    """What losing ``land`` costs its OWN controller — the same two terms
    the opponent-side valuation charges, applied to oneself: a land-drop
    turn of tempo while still below the curve top (``mana_clock_impact``
    per constrained turn), plus the pips of a demanded colour that are
    stranded when this was the last source (``card_clock_impact`` per
    stranded pip).  Used to price an additional cost that sacrifices a
    land (a Flashback "Sacrifice a Mountain" rider): the mana part of the
    cost is already in the effective cost; this is the land itself.
    """
    curve_top, pips = _opponent_unplayed_demand(game, player_idx)
    counts = _opponent_color_sources(game, player_idx)
    me = game.players[player_idx]
    my_lands = sum(1 for p in me.battlefield if p.template.is_land)
    # A land given up below the curve top is a mana short on every
    # following turn until a replacement is drawn: the expected wait is
    # the reciprocal of the library's land density (a derived draw
    # horizon, capped by the library), not one land-drop turn.
    constrained_turns = 0
    if my_lands - 1 < curve_top:
        library = list(me.library)
        land_density = (sum(1 for c in library if c.template.is_land)
                        / len(library)) if library else 0.0
        constrained_turns = (min(len(library), 1.0 / land_density)
                             if land_density > 0 else len(library))
    tempo = mana_clock_impact(snap) * constrained_turns
    stranded_pips = 0
    for code in set(land.template.produces_mana or []):
        if code not in COLOR_MAP:
            continue
        need = pips.get(code, 0)
        remaining = counts.get(code, 0) - 1
        if need > remaining:
            stranded_pips += need - remaining
    premium = card_clock_impact(snap) * stranded_pips
    return tempo + premium


def flashback_sacrifice_land(game: "GameState", player_idx: int,
                             tmpl: "CardTemplate") -> Optional["CardInstance"]:
    """The land the cast path will sacrifice for this spell's Flashback
    additional cost (first land carrying the typed subtype — the same
    choice ``engine/cast_manager`` makes), or None when the cost is mana
    only or no such land is on the battlefield."""
    needed = getattr(tmpl, 'flashback_sacrifice_subtype', None)
    if not needed:
        return None
    for l in game.players[player_idx].battlefield:
        if not l.template.is_land:
            continue
        if (needed in [s.lower() for s in (l.template.subtypes or [])]
                or needed in (l.template.name or '').lower()):
            return l
    return None
