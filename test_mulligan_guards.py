"""Test mulligan hard floor / soft ceiling guards."""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from decks.modern_meta import MODERN_DECKS
from ai.gameplan import GoalEngine, get_gameplan, create_goal_engine
from ai.strategy_profile import ArchetypeStrategy, DECK_ARCHETYPE_OVERRIDES
from ai.mulligan import MulliganDecider


def get_decider(deck_name):
    ge = create_goal_engine(deck_name)
    arch_str = DECK_ARCHETYPE_OVERRIDES.get(deck_name, "midrange")
    arch = ArchetypeStrategy(arch_str) if isinstance(arch_str, str) else arch_str
    return MulliganDecider(arch, ge)


def simulate_hands(deck_name, db, n=100, seed=42):
    rng = random.Random(seed)
    deck_data = MODERN_DECKS[deck_name]
    mainboard = deck_data["mainboard"]

    templates = []
    for card_name, count in mainboard.items():
        tmpl = db.get_card(card_name)
        if tmpl:
            for _ in range(count):
                templates.append(tmpl)

    decider = get_decider(deck_name)
    results = {"total": n, "keeps": 0, "mulls": 0,
               "zero_land_keeps": 0, "five_plus_land_keeps": 0, "land_dist": {}}

    for i in range(n):
        pool = list(templates)
        rng.shuffle(pool)
        # Create CardInstances for the hand
        hand = [CardInstance(template=t, owner=0, controller=0, instance_id=i*7+j, zone="hand")
                for j, t in enumerate(pool[:7])]

        keep = decider.decide(hand, 7)
        lands = sum(1 for c in hand if c.template.is_land)
        results["land_dist"].setdefault(lands, {"keep": 0, "mull": 0})

        if keep:
            results["keeps"] += 1
            results["land_dist"][lands]["keep"] += 1
            if lands == 0:
                results["zero_land_keeps"] += 1
                names = [c.name for c in hand]
                print(f"  [0-LAND KEEP] {deck_name}: {names}")
                print(f"    Reason: {decider.last_reason}")
            if lands >= 5:
                results["five_plus_land_keeps"] += 1
                names = [c.name for c in hand]
                spells = [c.name for c in hand if not c.template.is_land]
                print(f"  [5+LAND KEEP] {deck_name}: {lands} lands, {len(spells)} spells: {names}")
                print(f"    Reason: {decider.last_reason}")
        else:
            results["mulls"] += 1
            results["land_dist"][lands]["mull"] += 1

    return results


def main():
    db = CardDatabase()

    print("=" * 70)
    print("MULLIGAN GUARD TEST — 100 hands per deck")
    print("=" * 70)

    all_results = {}
    for deck_name in ["Izzet Prowess", "Living End", "Affinity", "Amulet Titan"]:
        print(f"\n{'─' * 50}")
        print(f"  {deck_name}")
        print(f"{'─' * 50}")

        r = simulate_hands(deck_name, db, n=100, seed=42)
        all_results[deck_name] = r

        print(f"  Keeps: {r['keeps']}/100  Mulls: {r['mulls']}/100")
        print(f"  0-land keeps: {r['zero_land_keeps']}")
        print(f"  5+-land keeps: {r['five_plus_land_keeps']}")
        print(f"  Land distribution:")
        for lands in sorted(r["land_dist"].keys()):
            d = r["land_dist"][lands]
            print(f"    {lands} lands: {d['keep']} keep / {d['mull']} mull")

    print(f"\n{'=' * 70}")
    print("ASSERTIONS")
    print(f"{'=' * 70}")

    r = all_results["Izzet Prowess"]
    assert r["zero_land_keeps"] == 0, f"FAIL: Prowess kept {r['zero_land_keeps']} 0-land hands"
    print("✓ Prowess: 0 zero-land keeps")

    r = all_results["Living End"]
    pct = r["five_plus_land_keeps"]
    assert pct < 5, f"FAIL: Living End kept {pct} 5+-land hands (want <5)"
    print(f"✓ Living End: {pct} five+-land keeps (<5)")

    r = all_results["Affinity"]
    print(f"✓ Affinity: {r['zero_land_keeps']} zero-land keeps (allowed for mana artifact hands)")

    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()
