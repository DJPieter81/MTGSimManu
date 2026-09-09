"""A gameplan's mulligan land bounds follow its manabase.

`ai/mulligan.py` rejects a seven whose land count exceeds
`mulligan_max_lands` BEFORE the scored gate — a hard reject, distinct from
the real flood signal (five-plus lands AND fewer than two spells) a few
lines above it.  Nine gameplans declared a cap of 3, so a completely
standard four-land, three-spell seven was mulliganed every time.  Sampled
through the real decider on 600 natural sevens per deck, the cap alone
threw back 5–14% of ALL sevens (Boros Energy 14%, Creatures Toolbox 12%,
Grixis Reanimator 12%, Domain Zoo 9%), and the hands it threw back are
the keeps any pilot makes: Ajani + two removal spells + four lands;
Swiftspear + two Bolts + four lands; Medallion + ritual + Wish + four
lands.  A four-land seven is 22% of all sevens in a 22-land deck.

Rules pinned:

1. The cap admits one land above the deck's EXPECTED seven-card land
   count (`ceil(7·L/60) + 1`, L = mainboard lands) — the mechanic the flat
   per-deck integer encodes, derived from the decklist so the knob cannot
   drift back below it.  Precedent: Pinnacle Affinity's cap was raised 3→4
   for the same defect (`tests/test_saga_engine_land_gameplan_config.py`).
2. A standard four-land seven with real spells is kept, and never for the
   reason "too many lands".
3. The floor rejects a one-land seven in a manabase whose curve cannot be
   played off one land (18 lands, three-drop curve, no free spells) — one
   such keep decided a replay (Azorius Blink vs Broodscale, 2026-09-08).

Card names are fixture carriers for hand shapes; the deck names are the
data instances the rule is checked against.
"""
from __future__ import annotations

import math

import pytest

from ai.gameplan import create_goal_engine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.cards import CardInstance


def _mainboard_names(deck_name: str):
    from decks.modern_meta import MODERN_DECKS
    deck = MODERN_DECKS[deck_name]
    main = deck["mainboard"] if isinstance(deck, dict) else deck.mainboard
    items = main.items() if isinstance(main, dict) else main
    out = []
    for it in items:
        name, qty = it if isinstance(it, tuple) else (it[0], it[1])
        out.extend([name] * qty)
    return out


def _land_count(card_db, deck_name: str) -> int:
    n = 0
    for name in _mainboard_names(deck_name):
        t = card_db.get_card(name)
        assert t is not None, f"missing card: {name}"
        n += 1 if t.is_land else 0
    return n


def _hand(card_db, names):
    cards = []
    for i, name in enumerate(names):
        t = card_db.get_card(name)
        assert t is not None, f"missing card: {name}"
        cards.append(CardInstance(template=t, owner=0, controller=0,
                                  instance_id=i + 1, zone="hand"))
    return cards


def _decider(deck_name: str) -> MulliganDecider:
    goal = create_goal_engine(deck_name)
    assert goal is not None and goal.gameplan is not None, deck_name
    arch = getattr(goal.gameplan, "archetype", None)
    arch = arch.value if hasattr(arch, "value") else str(arch or "aggro")
    strat = next((m for m in ArchetypeStrategy
                  if m.value == arch or m.name.lower() == arch.lower()),
                 ArchetypeStrategy.AGGRO)
    return MulliganDecider(strat, goal)


# ─── 1. The cap follows the manabase ──────────────────────────────────


def test_land_cap_admits_one_land_above_the_decks_expected_seven_card_land_count(card_db):
    from decks.modern_meta import MODERN_DECKS
    offenders = []
    checked = 0
    for deck_name in MODERN_DECKS:
        goal = create_goal_engine(deck_name)
        if goal is None or goal.gameplan is None:
            continue
        lands = _land_count(card_db, deck_name)
        expected = 7 * lands / 60
        floor = math.ceil(expected) + 1
        cap = goal.gameplan.mulligan_max_lands
        checked += 1
        if cap < floor:
            offenders.append(f"{deck_name}: {lands} lands → expected "
                             f"{expected:.2f} in seven, cap {cap} < {floor}")
    assert checked > 0
    assert not offenders, (
        "a cap below one-above-the-expected-land-count mulligans standard "
        "sevens:\n  " + "\n  ".join(offenders))


# ─── 2. A standard four-land seven is kept ────────────────────────────


@pytest.mark.parametrize("deck_name,names", [
    ("Boros Energy", ["Ajani, Nacatl Pariah // Ajani, Nacatl Avenger",
                      "Galvanic Discharge", "Galvanic Discharge",
                      "Arena of Glory", "Flooded Strand", "Flooded Strand",
                      "Sacred Foundry"]),
    ("Izzet Prowess", ["Monastery Swiftspear", "Lightning Bolt", "Lightning Bolt",
                       "Fiery Islet", "Steam Vents", "Bloodstained Mire",
                       "Wooded Foothills"]),
    ("Ruby Storm", ["Ruby Medallion", "Pyretic Ritual", "Wish",
                    "Elegant Parlor", "Scalding Tarn", "Scalding Tarn",
                    "Thundering Falls"]),
])
def test_standard_four_land_seven_is_kept_by_a_cap_declaring_gameplan(card_db, deck_name, names):
    decider = _decider(deck_name)
    keep = decider.decide(_hand(card_db, names), cards_in_hand=7)
    assert keep, (f"{deck_name} mulliganed a standard four-land seven: "
                  f"{decider.last_reason!r}")
    assert not decider.last_reason.startswith("too many lands")


# ─── 3. The floor rejects a one-lander a three-drop curve cannot play ──


def test_one_land_seven_is_mulliganed_by_a_three_drop_curve_manabase(card_db):
    decider = _decider("Azorius Blink")
    hand = _hand(card_db, ["Plains", "Solitude", "Phelia, Exuberant Shepherd",
                           "Ephemerate", "Ephemerate",
                           "March of Otherworldly Light", "Momo, Friendly Flier"])
    keep = decider.decide(hand, cards_in_hand=7)
    assert not keep, "an 18-land three-drop-curve deck kept a one-land seven"
    assert decider.last_reason.startswith("too few lands"), decider.last_reason


# ─── 4. The edited gameplans round-trip through the loader ────────────


def test_gameplans_round_trip_through_the_loader():
    for deck in ("Affinity", "Azorius Blink", "Boros Energy", "Creatures Toolbox",
                 "Domain Zoo", "Grixis Reanimator", "Hollow One", "Izzet Prowess",
                 "Ruby Storm"):
        engine = create_goal_engine(deck)
        assert engine is not None and engine.gameplan is not None, deck
        assert engine.gameplan.mulligan_max_lands >= 4, deck
