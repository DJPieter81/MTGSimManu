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
    hates_gy = ('exile' in oracle and 'graveyard' in oracle) \
               or "can't cast spells from" in oracle \
               or 'leyline of the void' in oracle
    if not hates_gy:
        return 0.0
    reliance = _gy_reliance(opp_templates, opp_gameplan)
    if reliance <= 0:
        return 0.0
    # Hate card residency × reliance × expected-creatures-denied
    # Creatures denied ≈ opp's average creatures-in-GY at combo time (~5).
    EXPECTED_GY_CREATURES_DENIED = 5.0  # rules constant: full Living End return
    return reliance * EXPECTED_GY_CREATURES_DENIED * PERMANENT_VALUE_WINDOW


def _clause_artifact_removal(oracle: str,
                              opp_templates: List["CardTemplate"]) -> float:
    """Value of single-target artifact removal.

    avg_artifact_cost × artifact_density × residency. Uses CMC as a proxy
    for artifact strategic value (equipment / mana rocks / planeswalker-
    adjacent artifacts tend to be CMC 2+).
    """
    from engine.cards import CardType
    if not re.search(r'(destroy|exile) (target )?artifact', oracle):
        return 0.0
    artifacts = [t for t in _nonland(opp_templates)
                 if CardType.ARTIFACT in (t.card_types or []) and not t.is_creature]
    if not artifacts:
        return 0.0
    density = len(artifacts) / len(_nonland(opp_templates))
    avg_cmc = sum(t.cmc or 0 for t in artifacts) / len(artifacts)
    return density * avg_cmc * PERMANENT_VALUE_WINDOW


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
    value += _clause_creature_removal(oracle, opp_templates)
    value += _clause_counterspell(oracle, opp_templates)
    value += _clause_protection_color(oracle, opp_templates, body_power)
    value += _clause_gy_hate(oracle, opp_templates, opp_gameplan)
    value += _clause_artifact_removal(oracle, opp_templates)

    return value


def plan_sideboard(
    my_main: Dict[str, int],
    my_sb: Dict[str, int],
    opp_deck_name: str,
    card_db: "CardDatabase",
    opp_mainboard: Optional[Dict[str, int]] = None,
    opp_gameplan_loader: Optional[Callable] = None,
) -> Tuple[Dict[str, int], Dict[str, int], List[str]]:
    """Plan Bo3 sideboard swaps using oracle-driven values.

    Compares each SB card's value vs the opponent to each main card's
    value vs the opponent; swaps SB→main while the SB card's value exceeds
    the weakest main card's value.

    Caller supplies `opp_mainboard` (dict of name→count) — the SB plan
    depends on opp's real deck composition, not the deck name alone.

    Returns (new_main, new_sb, rationale_log).
    """
    if not my_sb or not opp_mainboard:
        return dict(my_main), dict(my_sb), []

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

        # Swap only when the SB card's value exceeds the main card's value.
        if sb_val <= main_val:
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
            f"swap: -{main_name} (v={main_val:.2f}) +{sb_name} (v={sb_val:.2f})"
        )

    return new_main, new_sb, log
