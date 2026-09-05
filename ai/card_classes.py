"""Central card-class registry.

Structural finding #2 (docs/proposals/2026-07-09_structural_findings.md):
scorers kept private allowlists of "cards that count" — the holdback
pricer matched removal|counterspell but not the cast-lock class, the
sideboard scorer had no cast-rate-denial family, the improvise gate
trusted a cache with silent gaps. Each miss shipped as a bug.

This module is the single home for class-membership predicates.
Membership derives from tags (which are themselves oracle-derived) and
card properties — never card names. Consumers import the predicate;
adding a mechanic class to the game is ONE edit here.

Growth path (add as consumers convert): hate-vs-mechanic classes
(chain / graveyard / artifact), chain-component class, blink class.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.cards import CardTemplate

# Tags whose holders are worth keeping mana open for on the
# opponent's turn: counterspells, removal, and turn-scoped cast-locks
# ('silence' — set from the "can't cast spells this turn" oracle
# clause). One set, every holdback/response consumer.
_HELD_INTERACTION_TAGS = frozenset({"counterspell", "removal", "silence"})


def is_held_interaction(template: "CardTemplate") -> bool:
    """True when the card is instant-speed interaction — something a
    player holds open mana to cast on the opponent's turn.

    "Instant-speed" is castability, not card type: a plain instant
    (`is_instant`) OR any card with flash (`has_flash`) can be held up.
    Gating on `is_instant` alone silently dropped flash removal that is
    not a plain instant — evoke elementals (Solitude / Subtlety /
    Endurance) and flash enchantment removal (Leyline Binding) — so the
    holdback pricer tapped out the mana needed to cast them on the
    opponent's turn. (Root cause: docs/diagnostics/
    2026-08-20_domain_zoo_overperformance_root_cause.md.)
    """
    instant_speed = (getattr(template, "is_instant", False)
                     or getattr(template, "has_flash", False))
    if not instant_speed:
        return False
    tags = getattr(template, "tags", None) or set()
    return bool(_HELD_INTERACTION_TAGS & set(tags))


def self_discard_outlet_targets(template: "CardTemplate", hand,
                                gameplan) -> list:
    """The cards in ``hand`` this spell could bin for its CASTER's own
    graveyard plan — the "target player … discards" self-outlet line.

    Membership is derived, never named:
      * the spell is a hand attack whose target may be its caster
        (typed ``hand_attack_data``: target 'player'; a "target
        opponent" wording never qualifies);
      * the deck's gameplan declares a graveyard FILL_RESOURCE goal
        (`ai.combo_calc._find_resource_zone`) — a deck with no
        graveyard plan has nothing to fill;
      * the card is a creature the spell's choose clause admits (the
        engine's own revealed-hand restriction filter — mana-value cap,
        type words, nonland) AND some payoff in hand can return it from
        the graveyard (the payoff's parsed graveyard target requirement:
        legendary / any creature / mana-value ceiling).

    Returns the binnable cards (empty when the line does not exist).
    Consumers: the target chooser (self vs opponent), the spell scorer,
    and the keep/mull rule's enabler coverage.
    """
    data = getattr(template, 'hand_attack_data', None) or {}
    if data.get('target') != 'player':
        return []
    if gameplan is None:
        return []
    from ai.combo_calc import _find_resource_zone
    from engine.oracle_resolver import _targeted_discard_candidates

    class _Plan:  # _find_resource_zone reads goal_engine.gameplan
        pass
    _ge = _Plan()
    _ge.gameplan = gameplan
    zone, _target, _min_cmc = _find_resource_zone(_ge)
    if zone != "graveyard":
        return []

    # A payoff in hand must be able to return the binned card
    # (`deck_can_return`: the payoff's parsed graveyard requirement).
    payoffs = [c for c in hand if c.template is not template]

    def _returnable(card) -> bool:
        return deck_can_return(card.template, payoffs)

    legal = _targeted_discard_candidates(
        [c for c in hand if c.template is not template],
        data.get('choose_clause') or '')
    return [c for c in legal if _returnable(c)]


def deck_can_return(template: "CardTemplate", returners) -> bool:
    """True when some card among ``returners`` (templates or instances)
    can put ``template`` from a graveyard onto the battlefield.

    Derived, never named:
      * a TARGETED returner ("return target [legendary] creature card
        [with mana value N or less] from your graveyard to the
        battlefield") counts when its parsed graveyard requirement
        admits the card — creature type, supertype, mana-value ceiling
        (`engine.target_solver.parse` / `_matches_supertype`);
      * an UNTARGETED mass return ("each player … puts all cards they
        exiled this way onto the battlefield", Living End shape) counts
        for any creature card;
      * a returner that only returns ITSELF ("return this card from your
        graveyard to the battlefield" — the Vengevine / Phoenix shape) is
        no path for anything else.

    One owner for "is this card reanimation equity in this deck":
    the cycling scorer and the self-discard-outlet line both read it.
    """
    from engine.target_solver import _matches_supertype, parse

    if not getattr(template, 'is_creature', False):
        return False

    class _Probe:  # `_matches_supertype` reads card.template.supertypes
        pass
    probe = _Probe()
    probe.template = template

    for r in returners:
        rt = getattr(r, 'template', r)
        oracle = (getattr(rt, 'oracle_text', '') or '').lower()
        if not oracle or rt is template:
            continue
        targeted = False
        for req in parse(oracle):
            if req.zone != "graveyard" or "creature" not in req.types:
                continue
            targeted = True
            if not _matches_supertype(probe, req.supertype):
                continue
            mv = req.max_mana_value
            if mv is not None and (template.cmc or 0) > mv:
                continue
            return True
        if targeted:
            continue
        # Untargeted: a mass return of creature cards from graveyards
        # onto the battlefield (typed `mass_graveyard_return`, parse-once;
        # self-returns excluded there).
        if getattr(rt, 'mass_graveyard_return', False):
            return True
    return False
