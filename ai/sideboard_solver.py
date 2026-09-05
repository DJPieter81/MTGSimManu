"""Oracle-driven sideboard solver.

Computes the expected value of a card against a specific opponent's
deck composition. See docs/proposals/sideboard_solver.md for the
design rationale.

All value formulas compose from existing subsystems — `creature_threat_value`,
`permanent_threat`, `DeckKnowledge`-style densities, `life_as_resource`,
`PERMANENT_VALUE_WINDOW`. No new magic constants.
"""
from __future__ import annotations
import re
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from ai.scoring_constants import (
    CLOCK_IMPACT_LIFE_SCALING,
    SB_GY_FULL_RELIANCE_TARGET,
    SB_EXPECTED_GY_CREATURES_DENIED,
    SB_EXPECTED_CHAIN_SPELLS_DENIED,
    SB_DEFAULT_AVG_CMC,
    SB_SWAP_EPSILON_MANA_FRACTION,
)

if TYPE_CHECKING:
    from engine.cards import CardTemplate
    from engine.card_database import CardDatabase

# Rules constant — shared with ai/ev_evaluator.py (EVSnapshot.urgency_factor).
# Typical deferred-value permanent residency: first payoff T+1 + bulk over ~2
# turns. Using the same number keeps multiple deferred-value subsystems coherent.
PERMANENT_VALUE_WINDOW = 2.0

COLOR_LETTER = {'red': 'R', 'blue': 'U', 'black': 'B', 'white': 'W', 'green': 'G'}


# ─────────────────────────────────────────────────────────────
# Deck-composition helpers — derive densities from template lists
# ─────────────────────────────────────────────────────────────

def _nonland(templates: List["CardTemplate"]) -> List["CardTemplate"]:
    return [t for t in templates if not t.is_land]


def _density(pred: Callable, templates: List["CardTemplate"]) -> float:
    """Fraction of non-land templates matching `pred`."""
    nl = _nonland(templates)
    total = len(nl)
    if total == 0:
        return 0.0
    return sum(1 for t in nl if pred(t)) / total


def _avg_creature_threat(opp_templates: List["CardTemplate"]) -> float:
    """Mean `creature_threat_value` over opp's creature templates.

    Uses the shared `BASELINE_SNAPSHOT` — same snapshot scale the rest of
    the threat-scoring pipeline uses.
    """
    from ai.ev_evaluator import creature_threat_value, BASELINE_SNAPSHOT
    from engine.cards import CardInstance
    creatures = [t for t in opp_templates if t.is_creature]
    if not creatures:
        return 0.0
    total = 0.0
    for t in creatures:
        # Ephemeral CardInstance for oracle-driven threat eval. Controller /
        # owner are irrelevant; creature_threat_value reads template + power.
        inst = CardInstance(template=t, owner=0, controller=0,
                             instance_id=-1, zone="library")
        total += creature_threat_value(inst, BASELINE_SNAPSHOT)
    return total / len(creatures)


def _color_damage_density(color: str,
                           opp_templates: List["CardTemplate"]) -> float:
    """Fraction of opp's non-land templates that can deal damage in this color.

    A template "deals damage in color X" if its mana cost contains X AND its
    oracle references damage (burn spell), or it's a creature with positive
    power that requires X in its cost (attacks for damage).
    """
    letter = COLOR_LETTER.get(color, '')
    if not letter:
        return 0.0

    def pred(t):
        mc = t.mana_cost
        # ManaCost has attributes red/blue/black/white/green; str form also works
        if mc is None or getattr(mc, color, 0) == 0:
            return False
        # Burn / damage spell — typed field parsed once at DB load.
        if getattr(t, 'deals_targeted_damage', False):
            return True
        # Creature attacks — any creature with positive power and the colour
        if t.is_creature and (t.power or 0) > 0:
            return True
        return False

    return _density(pred, opp_templates)


def _gy_reliance(opp_templates: List["CardTemplate"],
                  opp_gameplan: Optional["object"] = None) -> float:
    """How much opp depends on the graveyard. 0 = none, 1 = combo lives there.

    Primary signal: opp's gameplan has a FILL_RESOURCE goal with
    `resource_zone == "graveyard"`. Secondary signal: fraction of templates
    whose oracle text references graveyard as a resource (cascade-into-GY,
    flashback, escape, delve, reanimate).
    """
    # Primary: gameplan-declared graveyard reliance
    if opp_gameplan is not None:
        from ai.gameplan import GoalType
        for goal in getattr(opp_gameplan, 'goals', []):
            if (getattr(goal, 'goal_type', None) == GoalType.FILL_RESOURCE
                    and getattr(goal, 'resource_zone', '') == 'graveyard'):
                # Declared dependency: weight by target creatures needed.
                # Saturates at SB_GY_FULL_RELIANCE_TARGET — full Living
                # End return = full reliance.
                target = max(1, getattr(goal, 'resource_target', 1))
                return min(1.0, target / SB_GY_FULL_RELIANCE_TARGET)

    # Secondary: oracle-driven — cards that read from / cast from GY, or
    # reanimator targets (large creatures the deck wants in GY).
    def pred(t):
        oracle = (t.oracle_text or '').lower()
        # Keyword abilities that cast/re-cast from GY
        if any(kw in oracle for kw in (
                'flashback', 'escape', 'delve', 'unearth',
                'embalm', 'eternalize', 'threshold', 'delirium',
        )):
            return True
        # Reanimator / return-from-GY — typed field parsed once at DB load.
        if getattr(t, 'has_graveyard_recursion', False):
            return True
        return False

    return _density(pred, opp_templates)


def _chain_reliance(opp_templates: List["CardTemplate"]) -> float:
    """How much opp depends on casting many spells in one turn.
    0 = none, ~0.5 = dedicated storm/chain combo.

    Oracle-driven density of chain components among nonland cards:
    storm-keyword spells, rituals (instants/sorceries that add mana),
    and cost reducers. Mirrors `_gy_reliance`'s secondary signal —
    composition, not deck names.
    """
    def pred(t):
        from engine.cards import Keyword as _Kw
        # Storm payoffs — typed keyword field.
        if _Kw.STORM in getattr(t, 'keywords', set()):
            return True
        # Ritual class — typed field parsed once at DB load.
        if (t.is_instant or t.is_sorcery) and getattr(t, 'ritual_mana', None) is not None:
            return True
        # Cost-reducer class — typed field parsed once at DB load.
        if getattr(t, 'is_cost_reducer', False):
            return True
        return False

    return _density(pred, opp_templates)


# ─────────────────────────────────────────────────────────────
# Clause evaluators — one per card-class pattern
# ─────────────────────────────────────────────────────────────

def _clause_creature_removal(oracle: str,
                              opp_templates: List["CardTemplate"],
                              template: "CardTemplate" = None) -> float:
    """Value of single-target creature removal.

    avg_threat × creature_density × residency
    """
    if not re.search(r'(destroy|exile) target creature', oracle):
        return 0.0
    # A printed mana-value ceiling (typed field, parse-once) limits which
    # creatures the removal can legally hit — count only those.
    data = getattr(template, 'targeted_removal_data', None) if template is not None else None
    ceiling = data['mv'] if data and isinstance(data.get('mv'), int) else None

    def _hittable(t):
        return t.is_creature and (ceiling is None or (t.cmc or 0) <= ceiling)

    creature_density = _density(_hittable, opp_templates)
    if creature_density <= 0:
        return 0.0
    hittable = [t for t in opp_templates if _hittable(t)]
    return _avg_creature_threat(hittable) * creature_density * PERMANENT_VALUE_WINDOW


def _clause_counterspell(template: "CardTemplate",
                          opp_templates: List["CardTemplate"]) -> float:
    """Value of counterspells.

    target_density × avg_cmc × residency — the avg CMC of targetable spells
    proxies the average impact a countered spell would have had. CMC is the
    only principled composition-free signal for "spell bigness" without
    invoking per-card EV evaluation (which is expensive here).
    """
    # template.is_counterspell is the pre-parsed typed field; populated by
    # CardDatabase at load time via oracle_parser.parse_is_counterspell.
    if not template.is_counterspell:
        return 0.0

    # counter_target_kind: "noncreature_spell" / "creature_spell" /
    # "instant_or_sorcery_spell" / "spell" / "" (generic all-spells).
    # Populated by CardDatabase at load time; no runtime oracle inspection.
    kind = template.counter_target_kind
    if kind == 'noncreature_spell':
        target_pred = lambda t: not t.is_creature and not t.is_land
    elif kind == 'creature_spell':
        target_pred = lambda t: t.is_creature
    else:
        # "spell", "instant_or_sorcery_spell", "" — treat as any nonland spell
        target_pred = lambda t: not t.is_land

    targets = [t for t in _nonland(opp_templates) if target_pred(t)]
    if not targets:
        return 0.0
    target_density = len(targets) / len(_nonland(opp_templates))
    avg_cmc = sum(t.cmc or 0 for t in targets) / len(targets)
    return target_density * avg_cmc * PERMANENT_VALUE_WINDOW


def _clause_protection_color(oracle: str,
                              opp_templates: List["CardTemplate"],
                              card_body_power: int = 0) -> float:
    """Value of 'protection from <color>'.

    Formula: body_power × color_damage_density × residency. A body with
    protection is a wall vs that colour — every colour-damage source loses
    a clock turn against it. Body_power proxies the body's clock relevance.
    """
    m = re.search(r'protection from (red|blue|black|white|green)', oracle)
    if not m:
        return 0.0
    color = m.group(1)
    density = _color_damage_density(color, opp_templates)
    if density <= 0:
        return 0.0
    # max(1, power) so 0-power bodies still score via the "can't be targeted"
    # axis (protection from red also prevents targeted red removal).
    return max(1, card_body_power) * density * PERMANENT_VALUE_WINDOW


def _clause_gy_hate(template: "CardTemplate",
                     opp_templates: List["CardTemplate"],
                     opp_gameplan: Optional["object"] = None) -> float:
    """Value of graveyard hate (exile graveyard, can't cast from GY).

    Scales with opp's GY reliance. Against a deck that doesn't use GY at all,
    returns 0. Against Living End / Goryo's / Dredge, high value.

    Detection reads the typed field CardTemplate.has_graveyard_hate parsed
    once at DB load (oracle_parser.parse_has_graveyard_hate). No runtime
    oracle scans.
    """
    if not getattr(template, 'has_graveyard_hate', False):
        return 0.0
    reliance = _gy_reliance(opp_templates, opp_gameplan)
    if reliance <= 0:
        return 0.0
    # Hate card residency × reliance × expected-creatures-denied.
    # Creatures denied = SB_EXPECTED_GY_CREATURES_DENIED (rules constant
    # in scoring_constants — full Living End return).
    return reliance * SB_EXPECTED_GY_CREATURES_DENIED * PERMANENT_VALUE_WINDOW


def _clause_spell_chain_hate(template: "CardTemplate",
                             opp_templates: List["CardTemplate"]) -> float:
    """Value of cast-rate denial vs spell-chain (storm-class) decks.

    Patterns: one-spell-per-turn locks, per-spell surcharges, and
    storm-trigger answers. Scales with the opponent's measured chain
    reliance. Zero against a deck with no chain components.

    Detection reads the typed field CardTemplate.has_spell_chain_hate
    parsed once at DB load (oracle_parser.parse_has_spell_chain_hate).
    No runtime oracle scans.
    """
    if not getattr(template, 'has_spell_chain_hate', False):
        return 0.0
    reliance = _chain_reliance(opp_templates)
    if reliance <= 0:
        return 0.0
    return reliance * SB_EXPECTED_CHAIN_SPELLS_DENIED * PERMANENT_VALUE_WINDOW


def _clause_body_value(template: "CardTemplate") -> float:
    """Intrinsic body value — opponent-independent.

    Creatures: creature_threat_value on the shared default mid-game snap.
    Cascade spells: credit for the free spell they cast (cascade_value).
    Non-creature, non-cascade spells: no intrinsic bonus here; they score
    via their matchup-specific clauses (removal/counter/hate/protection).

    This keeps deck-core cards (cascaders for Living End, finishers for
    Storm, big creatures for reanimator) from scoring 0 and being
    swapped out wholesale.
    """
    if template.is_creature:
        from ai.ev_evaluator import creature_threat_value, BASELINE_SNAPSHOT
        from engine.cards import CardInstance
        inst = CardInstance(template=template, owner=0, controller=0,
                             instance_id=-1, zone="library")
        return creature_threat_value(inst, BASELINE_SNAPSHOT)

    # Cascade spells cast a free spell on resolution. Their body value
    # equals roughly one cast's EV. Approximate via creature_threat_value
    # of an average creature (same mid-game default scale).
    tags = template.tags or set()
    # template.is_cascade is a typed field populated at DB load time by
    # has_cascade() in oracle_parser.py — no runtime oracle inspection.
    if template.is_cascade or 'cascade' in tags:
        from ai.clock import mana_clock_impact
        from ai.ev_evaluator import BASELINE_SNAPSHOT
        # One free cast ≈ cmc-limit worth of mana advantage.
        # mana_clock_impact × CLOCK_IMPACT_LIFE_SCALING converts the
        # opp_life-normalised clock-units back into life-points / turn,
        # then × cmc gives the cascade's free-cast life-equivalent.
        return ((template.cmc or 0)
                * mana_clock_impact(BASELINE_SNAPSHOT)
                * CLOCK_IMPACT_LIFE_SCALING)

    return 0.0


def _clause_artifact_removal(card: "CardTemplate",
                              opp_templates: List["CardTemplate"]) -> float:
    """Value of artifact removal — single-target, X-target, or mass.

    Detection uses the parsed effect tags populated by
    `engine.card_database.OracleParser` at DB-load time.  No regex
    in the SB scorer — adding a new oracle pattern means extending
    `DESTROY_PATTERNS` / `EXILE_PATTERNS` in the parser, not
    editing this consumer.

    Tags consumed (set in `classify_card_role`):
      * `destroy_target_artifact`   — single-target / X-target
      * `destroy_target_permanent`  — universal targeted removal
                                       (Prismatic Ending, Beast Within)
      * `destroy_all_artifacts`     — mass removal (Shatterstorm)
      * `destroy_all_nonland`       — mass non-land sweeper

    avg_artifact_cost × artifact_density × residency × mass_multiplier.
    Uses CMC as a proxy for artifact strategic value.
    """
    from engine.cards import CardType
    tags = getattr(card, 'tags', set()) or set()
    is_mass = ('destroy_all_artifacts' in tags
               or 'destroy_all_nonland' in tags)
    is_targeted = ('destroy_target_artifact' in tags
                   or 'destroy_target_permanent' in tags)
    if not (is_mass or is_targeted):
        return 0.0
    artifacts = [t for t in _nonland(opp_templates)
                 if CardType.ARTIFACT in (t.card_types or []) and not t.is_creature]
    if not artifacts:
        return 0.0
    density = len(artifacts) / len(_nonland(opp_templates))
    avg_cmc = sum(t.cmc or 0 for t in artifacts) / len(artifacts)
    base = density * avg_cmc * PERMANENT_VALUE_WINDOW
    # Mass removal scales by the number of artifacts it'd destroy —
    # Shatterstorm vs an N-artifact board is worth ~N× a single-
    # target Wear // Tear.  No upper cap is needed: the more
    # artifacts the opponent runs, the higher the mass-wipe should
    # rank, all the way up to N = total nonland count (a deck where
    # every nonland is an artifact, e.g. mono-affinity).
    if is_mass:
        return base * len(artifacts)
    return base


def _clause_permanent_type_removal(card: "CardTemplate",
                                   opp_templates: List["CardTemplate"]) -> float:
    """Value of removal by the opponent's density of the permanent TYPES
    it can hit — read from the typed removal fields (parse-once):

      * `targeted_removal_data`      — the spell class ("destroy/exile
                                       target <types> [MV ≤ N|X]")
      * `etb_targeted_removal_data`  — the enters-the-battlefield class
                                       ("When this ~ enters, destroy/exile
                                       target <types> …")

    plus the tag-based mass / artifact detection `_clause_artifact_removal`
    already covered (kept as the fallback for cards without typed data).
    Creatures are NOT counted here — `_clause_creature_removal` prices
    creature removal on threat, and pricing them twice would double-count.

    density(matching nonland, within the printed MV ceiling) × avg CMC of
    the matched permanents × residency — the artifact clause's own shape,
    generalised; no new constants.
    """
    from engine.cards import CardType
    fallback = _clause_artifact_removal(card, opp_templates)
    data = (getattr(card, 'targeted_removal_data', None)
            or getattr(card, 'etb_targeted_removal_data', None))
    if not data:
        return fallback
    types = set(data.get('types') or ())
    if types & {'permanent', 'permanent_nonland'}:
        wanted = {CardType.ARTIFACT, CardType.ENCHANTMENT, CardType.PLANESWALKER}
    else:
        wanted = set()
        if 'artifact' in types:
            wanted.add(CardType.ARTIFACT)
        if 'enchantment' in types:
            wanted.add(CardType.ENCHANTMENT)
        if 'planeswalker' in types:
            wanted.add(CardType.PLANESWALKER)
    if not wanted:
        return fallback
    mv = data.get('mv')
    ceiling = mv if isinstance(mv, int) else None
    nonland = _nonland(opp_templates)
    if not nonland:
        return fallback
    hits = [t for t in nonland
            if not t.is_creature
            and any(ct in (t.card_types or []) for ct in wanted)
            and (ceiling is None or (t.cmc or 0) <= ceiling)]
    if not hits:
        return fallback
    density = len(hits) / len(nonland)
    avg_cmc = sum(t.cmc or 0 for t in hits) / len(hits)
    return max(fallback, density * avg_cmc * PERMANENT_VALUE_WINDOW)


# ─────────────────────────────────────────────────────────────
# Main API
# ─────────────────────────────────────────────────────────────

def sb_value(template: "CardTemplate",
             opp_templates: List["CardTemplate"],
             opp_gameplan: Optional["object"] = None) -> float:
    """Expected value of `template` against an opponent running `opp_templates`.

    Sums clause-value contributions from each applicable oracle pattern. Pure
    function; no game-state dependency.
    """
    oracle = (template.oracle_text or '').lower()
    if not oracle:
        return 0.0

    body_power = template.power or 0

    value = 0.0
    value += _clause_body_value(template)
    value += _clause_creature_removal(oracle, opp_templates, template)
    value += _clause_counterspell(template, opp_templates)
    value += _clause_protection_color(oracle, opp_templates, body_power)
    value += _clause_gy_hate(template, opp_templates, opp_gameplan)
    value += _clause_permanent_type_removal(template, opp_templates)
    value += _clause_spell_chain_hate(template, opp_templates)

    return value


def _critical_pieces(gameplan) -> set:
    """Cards that are off-limits for swapping out: combo cores, mulligan
    keys, finishers declared in the gameplan JSON. Returns a set of names.
    """
    protected = set()
    if gameplan is None:
        return protected
    for key in ('mulligan_keys', 'critical_pieces', 'always_early'):
        vals = getattr(gameplan, key, None) or []
        protected.update(vals)
    for combo_set in getattr(gameplan, 'mulligan_combo_sets', []) or []:
        protected.update(combo_set)
    return protected


def plan_sideboard(
    my_main: Dict[str, int],
    my_sb: Dict[str, int],
    opp_deck_name: str,
    card_db: "CardDatabase",
    opp_mainboard: Optional[Dict[str, int]] = None,
    opp_gameplan_loader: Optional[Callable] = None,
    my_deck_name: Optional[str] = None,
) -> Tuple[Dict[str, int], Dict[str, int], List[str]]:
    """Plan Bo3 sideboard swaps using oracle-driven values.

    Compares each SB card's value vs the opponent to each main card's
    value vs the opponent; swaps SB→main while the SB card's value exceeds
    the weakest main card's value.

    Caller supplies `opp_mainboard` (dict of name→count) — the SB plan
    depends on opp's real deck composition, not the deck name alone.

    Cards declared as combo-critical / mulligan-key in the caller's own
    gameplan JSON are protected from being swapped OUT. A Living End
    cascader (Shardless Agent) or a Storm finisher (Grapeshot) never
    scores high on opponent-facing clauses, but the deck bricks without
    them — the critical-piece list preserves deck identity.

    Returns (new_main, new_sb, rationale_log).
    """
    if not my_sb or not opp_mainboard:
        return dict(my_main), dict(my_sb), []

    # Load our own gameplan to protect combo pieces.
    my_protected: set = set()
    if opp_gameplan_loader is not None and my_deck_name is not None:
        try:
            my_gp = opp_gameplan_loader(my_deck_name)
            my_protected = _critical_pieces(my_gp)
        except Exception:
            my_protected = set()

    # Build opp's template list (for density math).
    opp_templates: List = []
    for name, count in opp_mainboard.items():
        tmpl = card_db.get_card(name)
        if tmpl is None:
            continue
        for _ in range(count):
            opp_templates.append(tmpl)

    # Optional: load opp's gameplan (richer GY-reliance signal).
    opp_gameplan = None
    if opp_gameplan_loader is not None:
        try:
            opp_gameplan = opp_gameplan_loader(opp_deck_name)
        except Exception:
            opp_gameplan = None

    # Compute my deck's own avg CMC across non-land cards. Serves as the
    # tempo-cost floor: a swap that stays at-or-below my avg CMC doesn't
    # disrupt my curve (fast decks have low avg CMC → any high-CMC swap
    # hurts; control decks already have high avg CMC → high-CMC swaps are
    # part of the plan).
    my_nonland_cmc_total = 0
    my_nonland_count = 0
    for name, count in my_main.items():
        tmpl = card_db.get_card(name)
        if tmpl is None or tmpl.is_land:
            continue
        my_nonland_cmc_total += (tmpl.cmc or 0) * count
        my_nonland_count += count
    my_avg_cmc = ((my_nonland_cmc_total / my_nonland_count)
                  if my_nonland_count else SB_DEFAULT_AVG_CMC)

    # Score every card in main + sb against this opponent.
    def _score(name: str) -> float:
        tmpl = card_db.get_card(name)
        if tmpl is None:
            return 0.0
        return sb_value(tmpl, opp_templates, opp_gameplan)

    main_scored = sorted(
        ((name, _score(name)) for name in my_main),
        key=lambda x: x[1],
    )  # ascending — weakest first
    sb_scored = sorted(
        ((name, _score(name)) for name in my_sb),
        key=lambda x: -x[1],
    )  # descending — strongest first

    new_main = dict(my_main)
    new_sb = dict(my_sb)
    log: List[str] = []

    main_idx = 0
    sb_idx = 0
    while main_idx < len(main_scored) and sb_idx < len(sb_scored):
        main_name, main_val = main_scored[main_idx]
        sb_name, sb_val = sb_scored[sb_idx]

        # Skip cards exhausted in either zone.
        if new_main.get(main_name, 0) == 0:
            main_idx += 1
            continue
        if new_sb.get(sb_name, 0) == 0:
            sb_idx += 1
            continue
        # Don't swap lands into main via SB or vice versa — caller should
        # have separated them, but defense in depth.
        main_tmpl = card_db.get_card(main_name)
        sb_tmpl = card_db.get_card(sb_name)
        if main_tmpl is None or sb_tmpl is None:
            # Unknown card — skip.
            if main_tmpl is None:
                main_idx += 1
            if sb_tmpl is None:
                sb_idx += 1
            continue
        if main_tmpl.is_land or sb_tmpl.is_land:
            if main_tmpl.is_land:
                main_idx += 1
            if sb_tmpl.is_land:
                sb_idx += 1
            continue

        # Protect combo pieces and mulligan keys from being swapped out.
        if main_name in my_protected:
            main_idx += 1
            continue

        # Archetype-scaled tempo cost: swapping in a high-CMC SB card is a
        # tempo loss only to the extent it overshoots our deck's own curve.
        # Floor = max(main_cmc, my_avg_cmc). Against fast decks (Boros avg
        # ≈1.8) a 3-CMC SB card costs 1.2 mana-units × residency; against
        # control (Azorius avg ≈3.0) a 3-CMC SB card is free tempo-wise.
        # Replaces Phase 2.5's uniform (sb_cmc − main_cmc), which
        # over-penalized control-deck curve-upgrades (Sheoldred, finishers).
        from ai.clock import mana_clock_impact
        from ai.ev_evaluator import BASELINE_SNAPSHOT
        # mana_unit ≈ 1.0 — clock-impact × CLOCK_IMPACT_LIFE_SCALING
        # reverses the opp_life normalisation in mana_clock_impact.
        mana_unit = mana_clock_impact(BASELINE_SNAPSHOT) * CLOCK_IMPACT_LIFE_SCALING
        sb_cmc = sb_tmpl.cmc or 0
        main_cmc = main_tmpl.cmc or 0
        cmc_floor = max(main_cmc, my_avg_cmc)
        tempo_cost = max(0.0, sb_cmc - cmc_floor) * mana_unit * PERMANENT_VALUE_WINDOW

        # ε-threshold gate: only commit swaps where net gain exceeds the
        # SB_SWAP_EPSILON_MANA_FRACTION (½ mana-unit by default). Prevents
        # churn from marginal-delta swaps that won't meaningfully change
        # the matchup.
        epsilon = mana_unit * SB_SWAP_EPSILON_MANA_FRACTION
        net_gain = (sb_val - tempo_cost) - main_val
        if net_gain <= epsilon:
            break  # no further profitable swaps

        # Execute one swap.
        new_main[main_name] = new_main[main_name] - 1
        if new_main[main_name] == 0:
            del new_main[main_name]
        new_main[sb_name] = new_main.get(sb_name, 0) + 1

        new_sb[sb_name] = new_sb[sb_name] - 1
        if new_sb[sb_name] == 0:
            del new_sb[sb_name]
        new_sb[main_name] = new_sb.get(main_name, 0) + 1

        log.append(
            f"swap: -{main_name} (v={main_val:.2f}) +{sb_name} "
            f"(v={sb_val:.2f}, tempo={tempo_cost:+.2f}, net={net_gain:+.2f})"
        )

    return new_main, new_sb, log
