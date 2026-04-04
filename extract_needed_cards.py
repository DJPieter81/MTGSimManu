#!/usr/bin/env python3
"""Extract only the cards needed by our 12 decks from a full AtomicCards.json.

Usage (run on YOUR machine where you have the full file):
    python3 extract_needed_cards.py /path/to/AtomicCards.json

Output: ModernAtomic_mini.json (should be <1MB)
Then upload that file to the project.
"""
import json
import sys

# All card names used across the 12 decks
NEEDED_CARDS = {
    "Amulet of Vigor", "Arboreal Grazer", "Arena of Glory", "Arid Mesa",
    "Birthing Ritual", "Blood Crypt", "Bloodstained Mire", "Boseiju, Who Endures",
    "Breach the Multiverse", "Breeding Pool", "Celestial Purge",
    "Cling to Dust", "Consign to Memory", "Consider", "Counterspell",
    "Creeping Tar Pit", "Cultivator Colossus", "Damnation", "Darkslick Shores",
    "Dauthi Voidwalker", "Dismember", "Dress Down", "Drown in the Loch",
    "Eldrazi Temple", "Elegant Parlor", "Emrakul, the Promised End",
    "Endurance", "Engineered Explosives", "Ephemerate", "Eternal Witness",
    "Eye of Ugin", "Fatal Push", "Flooded Strand", "Flusterstorm",
    "Force of Vigor", "Forest", "Galvanic Discharge", "Ghost Quarter",
    "Goblin Electromancer", "Goryo's Vengeance", "Griselbrand",
    "Grumgully, the Generous", "Guide of Souls", "Hallowed Fountain",
    "Hedge Maze", "Imodane's Recruiter", "Island", "Izzet Charm",
    "Jeskai Barricade", "Karn, the Great Creator", "Leyline Binding",
    "Lightning Bolt", "Living End", "Lush Portico", "Mishra's Factory",
    "Misty Rainforest", "Mountain", "Murktide Regent", "Mox Opal",
    "Mystical Dispute", "Nishoba Brawler", "Omnath, Locus of Creation",
    "Orcish Bowmasters", "Orim's Chant", "Ornithopter", "Otawara, Soaring City",
    "Phlage, Titan of Fire's Fury", "Plains", "Polluted Delta",
    "Prismatic Ending", "Psychic Frog", "Quantum Riddler",
    "Ragavan, Nimble Pilferer", "Raugrin Triome", "Ruby Medallion",
    "Sacred Foundry", "Scalding Tarn", "Scion of Draco", "Shelldock Isle",
    "Sheoldred, the Apocalypse", "Simic Growth Chamber",
    "Solitude", "Spell Pierce", "Spirebluff Canal", "Steam Vents",
    "Stock Up", "Stomping Ground", "Street Wraith", "Stubborn Denial",
    "Subtlety", "Surgical Extraction", "Swamp", "Takenuma, Abandoned Mire",
    "Teferi, Time Raveler", "Temple Garden", "Territorial Kavu",
    "Thought-Knot Seer", "Thoughtseize", "Thundering Falls",
    "Tolaria West", "Tormod's Crypt", "Tribal Flames",
    "Underground River", "Ulamog, the Ceaseless Hunger", "Urza's Mine",
    "Urza's Power Plant", "Urza's Tower", "Violent Outburst",
    "Walking Ballista", "Wastes", "Watery Grave", "Wear // Tear",
    "Windswept Heath", "Wrath of the Skies", "Wrenn and Six",
    "Zagoth Triome", "Ziatora's Proving Ground",
    # Add more from the full deck lists
    "Archon of Cruelty", "Archmage's Charm", "Baral, Chief of Compliance",
    "Birgi, God of Storytelling", "Cascade Bluffs", "Cavern of Souls",
    "Chalice of the Void", "Cranial Plating", "Creeping Tar Pit",
    "Darksteel Citadel", "Desperate Ritual", "Expressive Iteration",
    "Faithless Looting", "Fiery Islet", "Force of Negation",
    "Fury", "Galvanic Blast", "Gemstone Caverns", "Grief",
    "Gruul Turf", "Ice-Fang Coatl", "Inkmoth Nexus",
    "Jetmir's Garden", "Jegantha, the Wellspring",
    "Manamorphose", "Matter Reshaper", "Mishra's Bauble",
    "Monastery Swiftspear", "Nihil Spellbomb",
    "Past in Flames", "Primeval Titan", "Pyretic Ritual",
    "Reality Smasher", "Shatterskull Smashing", "Signal Pest",
    "Simian Spirit Guide", "Snapcaster Mage", "Soul-Guide Lantern",
    "Springleaf Drum", "Stoneforge Mystic", "Summoner's Pact",
    "Surgical Extraction", "The One Ring", "Thought Monitor",
    "Thoughtcast", "Through the Breach", "Urborg, Tomb of Yawgmoth",
    "Valakut, the Molten Pinnacle", "Void Mirror",
    "Wish", "Zuran Orb",
}

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 extract_needed_cards.py /path/to/AtomicCards.json")
        print("Output: ModernAtomic_mini.json")
        sys.exit(1)

    input_path = sys.argv[1]
    print(f"Loading {input_path}...")
    with open(input_path, 'r') as f:
        data = json.load(f)

    # AtomicCards format: {"data": {"CardName": [array of printings]}}
    full_data = data.get("data", data)

    mini = {"data": {}}
    found = 0
    missing = []

    # Also get card names from our actual deck files
    sys.path.insert(0, '.')
    try:
        from decks.modern_meta import MODERN_DECKS
        for deck in MODERN_DECKS.values():
            for card in deck.get('mainboard', {}):
                NEEDED_CARDS.add(card)
            for card in deck.get('sideboard', {}):
                NEEDED_CARDS.add(card)
    except:
        pass

    for name in sorted(NEEDED_CARDS):
        if name in full_data:
            mini["data"][name] = full_data[name]
            found += 1
        else:
            missing.append(name)

    # Add meta info
    if "meta" in data:
        mini["meta"] = data["meta"]

    output_path = "ModernAtomic_mini.json"
    with open(output_path, 'w') as f:
        json.dump(mini, f, separators=(',', ':'))

    import os
    size = os.path.getsize(output_path) / 1024
    print(f"Found {found}/{len(NEEDED_CARDS)} cards")
    if missing:
        print(f"Missing: {missing[:10]}{'...' if len(missing) > 10 else ''}")
    print(f"Output: {output_path} ({size:.0f} KB)")

if __name__ == "__main__":
    main()
