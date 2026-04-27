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

    Uses the shared `_DEFAULT_SNAP` — same snapshot scale the rest of
    the threat-scoring pipeline uses.
    """
    from ai.ev_evaluator import creature_threat_value, _DEFAULT_SNAP
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
        total += creature_threat_value(inst, _DEFAULT_SNAP)
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
        oracle = (t.oracle_text or '').lower()
        # Burn / damage spell
        if 'damage' in oracle and ('deal' in oracle or 'dealt' in oracle):
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
                # Declared dependency: weight by target creatures needed
                target = max(1, getattr(goal, 'resource_target', 1))
                return min(1.0, target / 5.0)  # 5 GY creatures = full reliance

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
        # Reanimator / return-from-GY / graveyard-as-resource patterns
        if 'from your graveyard' in oracle:
            return True
        if 'from their graveyard' in oracle and 'battlefield' in oracle:
            return True  # Living End's own oracle
        if 'from a graveyard' in oracle and 'battlefield' in oracle:
            return True
        return False

    return _density(pred, opp_templates)


# ─────────────────────────────────────────────────────────────
# Clause evaluators — one per card-class pattern
# ─────────────────────────────────────────────────────────────

def _clause_creature_removal(oracle: str,
                              opp_templates: List["CardTemplate"]) -> float:
    """Value of single-target creature removal.

    avg_threat × creature_density × residency
    """
    if not re.search(r'(destroy|exile) target creature', oracle):
        return 0.0
    creature_density = _density(lambda t: t.is_creature, opp_templates)
    if creature_density <= 0:
        return 0.0
    return _avg_creature_threat(opp_templates) * creature_density * PERMANENT_VALUE_WINDOW


def _clause_counterspell(oracle: str,
                          opp_templates: List["CardTemplate"]) -> float:
    """Value of counterspells.

    target_density × avg_cmc × residency — the avg CMC of targetable spells
    proxies the average impact a countered spell would have had. CMC is the
    only principled composition-free signal for "spell bigness" without
    invoking per-card EV evaluation (which is expensive here).
    """
    if 'counter target' not in oracle:
        return 0.0

    if 'counter target noncreature spell' in oracle:
        target_pred = lambda t: not t.is_creature and not t.is_land
    elif 'counter target creature spell' in oracle:
        target_pred = lambda t: t.is_creature
    elif 'counter target spell' in oracle:
        target_pred = lambda t: not t.is_land
    else:
        return 0.0

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


def _clause_gy_hate(oracle: str,
                     opp_templates: List["CardTemplate"],
                     opp_gameplan: Optional["object"] = None) -> float:
    """Value of graveyard hate (exile graveyard, can't cast from GY).

    Scales with opp's GY reliance. Against a deck that doesn't use GY at all,
    returns 0. Against Living End / Goryo's / Dredge, high value.
    """
    # True GY hate: exile opponent's GY or shut off GY-casting mechanics.
    # Must NOT match cards that MOVE cards out of GY onto the battlefield
    # (Living End, reanimation spells — those are reanimator enablers).
    hates_gy = False
    if re.search(r"exile [\w\s']*?graveyard", oracle) \
            and 'onto the battlefield' not in oracle:
        hates_gy = True
    if re.search(r'exile all (cards from )?graveyards?', oracle):
        hates_gy = True
    if re.search(r"can'?t (be )?cast (spells )?from", oracle) and 'graveyard' in oracle:
        hates_gy = True
    if 'if a card would be put into' in oracle and 'graveyard' in oracle and 'exile' in oracle:
        hates_gy = True
    if not hates_gy:
        return 0.0
    reliance = _gy_reliance(opp_templates, opp_gameplan)
    if reliance <= 0:
        return 0.0
    # Hate card residency × reliance × expected-creatures-denied
    # Creatures denied ≈ opp's average creatures-in-GY at combo time (~5).
    EXPECTED_GY_CREATURES_DENIED = 5.0  # rules constant: full Living End return
    return reliance * EXPECTED_GY_CREATURES_DENIED * PERMANENT_VALUE_WINDOW


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
        from ai.ev_evaluator import creature_threat_value, _DEFAULT_SNAP
        from engine.cards import CardInstance
        inst = CardInstance(template=template, owner=0, controller=0,
                             instance_id=-1, zone="library")
        return creature_threat_value(inst, _DEFAULT_SNAP)

    # Cascade spells cast a free spell on resolution. Their body value
    # equals roughly one cast's EV. Approximate via creature_threat_value
    # of an average creature (same mid-game default scale).
    oracle = (template.oracle_text or '').lower()
    tags = template.tags or set()
    if 'cascade' in oracle or 'cascade' in tags:
        from ai.clock import mana_clock_impact
        from ai.ev_evaluator import _DEFAULT_SNAP
        # One free cast ≈ cmc-limit worth of mana advantage.
        # Using mana_clock_impact × 20 (unit conversion) × cmc of cascade
        # floor ≈ creature_clock_impact × average_hit_cmc.
        return (template.cmc or 0) * mana_clock_impact(_DEFAULT_SNAP) * 20.0

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
    value += _clause_creature_removal(oracle, opp_templates)
    value += _clause_counterspell(oracle, opp_templates)
    value += _clause_protection_color(oracle, opp_templates, body_power)
    value += _clause_gy_hate(oracle, opp_templates, opp_gameplan)
    value += _clause_artifact_removal(template, opp_templates)

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
    my_avg_cmc = (my_nonland_cmc_total / my_nonland_count) if my_nonland_count else 2.5

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
        from ai.ev_evaluator import _DEFAULT_SNAP
        mana_unit = mana_clock_impact(_DEFAULT_SNAP) * 20.0  # ~1.0
        sb_cmc = sb_tmpl.cmc or 0
        main_cmc = main_tmpl.cmc or 0
        cmc_floor = max(main_cmc, my_avg_cmc)
        tempo_cost = max(0.0, sb_cmc - cmc_floor) * mana_unit * PERMANENT_VALUE_WINDOW

        # ε-threshold gate: only commit swaps where net gain exceeds half a
        # mana-unit. Prevents churn from marginal-delta swaps that won't
        # meaningfully change the matchup.
        epsilon = mana_unit * 0.5
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
