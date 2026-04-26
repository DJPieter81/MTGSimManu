"""
Modern Metagame Deck Database
Contains current top-tier Modern decklists based on April 2026 metagame data.
Each deck is a dict with mainboard (60 cards) and sideboard (15 cards).

Card names use MTGJSON naming convention:
- Double-faced cards: "Front // Back"
- Split cards: "Left // Right"

This module is the source of truth for MODERN_DECKS (full decklists) and
METAGAME_SHARES (tournament weights). `decks/metagame.json` is a JSON mirror
of METAGAME_SHARES, regenerated from this module; keep it in sync whenever
METAGAME_SHARES changes. Gameplans live as JSON per-deck under
`decks/gameplans/<slug>.json`.
"""
from typing import Dict, List, Tuple

# Metagame share data for weighting in simulations
METAGAME_SHARES = {
    "Boros Energy": 21.1,
    "Jeskai Blink": 9.2,
    "Eldrazi Tron": 7.1,
    "Ruby Storm": 6.2,
    "Affinity": 6.1,
    "Izzet Prowess": 4.9,
    "Amulet Titan": 4.1,
    "Goryo's Vengeance": 3.6,
    "Living End": 3.6,
    "Domain Zoo": 2.9,
    "Dimir Midrange": 2.8,
    "4c Omnath": 3.5,
    "4/5c Control": 3.5,
    "Pinnacle Affinity": 5.7,
    "Azorius Control": 2.5,
    "Azorius Control (WST)": 0.0,
    "Azorius Control (WST v2)": 0.0,
}

# Full decklists: mainboard + sideboard
MODERN_DECKS: Dict[str, Dict[str, Dict[str, int]]] = {
    "Boros Energy": {
        # rarakkyo — 5-8, Modern Challenge 32 (April 18, 2026)
        # Identical 75 to Rashek's Apr-16 Challenge 64 list — the consensus
        # current tuning. Key shifts from the RandomOctopus (Apr 4) list:
        #   - Orim's Chant moved MB (2) → SB (2)   [aligns with bug 2 fix:
        #     unkicked Chant no longer Time-Walks, so it's correctly SB-only]
        #   - Static Prison cut (1 → 0)
        #   - Flooded Strand (4) → Windswept Heath (4)
        #   - +1 Ranger-Captain of Eos MB (silence effect vs combo)
        #   - +1 Blood Moon MB (proactive vs greedy manabases)
        #   - +1 The Legend of Roku MB (recursive threat)
        #   - +1 Dalkovan Encampment (manland)
        #   - Sideboard retooled: -Wrath, -Charmaw ratios, +Surgical, +Vexing
        #     Bauble, +Damping Sphere, +Orim's Chant, +Celestial Purge single
        "mainboard": {
            "Ragavan, Nimble Pilferer": 4,
            "Ocelot Pride": 4,
            "Guide of Souls": 4,
            "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger": 4,
            "Phlage, Titan of Fire's Fury": 4,
            "Seasoned Pyromancer": 3,
            "Voice of Victory": 2,
            "Ranger-Captain of Eos": 1,
            "Galvanic Discharge": 4,
            "Thraben Charm": 2,
            "Blood Moon": 1,
            "Goblin Bombardment": 3,
            "The Legend of Roku": 1,
            "Arid Mesa": 4,
            "Windswept Heath": 4,
            "Marsh Flats": 3,
            "Arena of Glory": 3,
            "Sacred Foundry": 3,
            "Elegant Parlor": 2,
            "Dalkovan Encampment": 1,
            "Plains": 2,
            "Mountain": 1,
        },
        "sideboard": {
            "Blood Moon": 1,
            "Celestial Purge": 1,
            "Damping Sphere": 1,
            "High Noon": 1,
            "Obsidian Charmaw": 2,
            "Orim's Chant": 2,
            "Surgical Extraction": 1,
            "The Legend of Roku": 1,
            "Vexing Bauble": 1,
            "Wear // Tear": 2,
            "Wrath of the Skies": 2,
        },
    },
    "Jeskai Blink": {
        # Spellyp — 5-0, Modern League (April 5, 2026) — updated
        "mainboard": {
            # Creatures (21)
            "Phelia, Exuberant Shepherd": 4,
            "Phlage, Titan of Fire's Fury": 4,
            "Quantum Riddler": 4,
            "Ragavan, Nimble Pilferer": 4,
            "Solitude": 4,
            "Witch Enchanter": 1,
            # Instants + Sorceries (13)
            "Consign to Memory": 4,
            "Ephemerate": 2,
            "Galvanic Discharge": 4,
            "Prismatic Ending": 2,
            "Wrath of the Skies": 1,
            # Other spells (3)
            "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki": 3,
            # Lands (23)
            "Arena of Glory": 2,
            "Arid Mesa": 4,
            "Elegant Parlor": 1,
            "Flooded Strand": 4,
            "Hallowed Fountain": 1,
            "Island": 1,
            "Meticulous Archive": 1,
            "Mountain": 1,
            "Plains": 1,
            "Sacred Foundry": 1,
            "Scalding Tarn": 4,
            "Steam Vents": 1,
            "Thundering Falls": 1,
        },
        "sideboard": {
            "Ashiok, Dream Render": 1,
            "Clarion Conqueror": 1,
            "High Noon": 2,
            "Mystical Dispute": 1,
            "Obsidian Charmaw": 1,
            "Wear // Tear": 2,
            "Surgical Extraction": 1,
            "Teferi, Time Raveler": 1,
            "White Orchid Phantom": 2,
            "Wrath of the Skies": 3,
        },
    },
    "Ruby Storm": {
        "mainboard": {
            # Creatures / Cost Reducers
            "Ral, Monsoon Mage // Ral, Leyline Prodigy": 4,
            "Ruby Medallion": 4,
            # Rituals (net +2R each with Medallion)
            "Pyretic Ritual": 4,
            "Desperate Ritual": 4,
            "Manamorphose": 4,
            # Draw-2 spells (R each with Medallion — the combo engine)
            "Reckless Impulse": 4,
            "Wrenn's Resolve": 4,
            "Glimpse the Impossible": 3,
            # Card selection
            "Valakut Awakening // Valakut Stoneforge": 2,
            # Rebuy + Finisher access
            "Past in Flames": 3,
            "Wish": 2,
            # Storm finisher (1 main, extras in sideboard for Wish)
            "Grapeshot": 1,
            # Lands (18)
            "Scalding Tarn": 3,
            "Arid Mesa": 3,
            "Bloodstained Mire": 2,
            "Wooded Foothills": 2,
            "Mountain": 4,
            "Thundering Falls": 1,
            "March of Reckless Joy": 1,
            "Sacred Foundry": 1,
            "Sunbaked Canyon": 1,
            "Elegant Parlor": 2,
            "Gemstone Caverns": 1,
        },
        "sideboard": {
            "Grapeshot": 1,
            "Empty the Warrens": 1,
            "Past in Flames": 1,
            "Blood Moon": 1,
            "Meltdown": 1,
            "Orim's Chant": 4,
            "Prismatic Ending": 3,
            "Wear // Tear": 2,
            "Brotherhood's End": 1,
        },
    },
    "Affinity": {
        "mainboard": {
            "Mox Opal": 4,
            "Ornithopter": 4,
            "Springleaf Drum": 4,
            "Signal Pest": 4,
            "Memnite": 4,
            "Thought Monitor": 4,
            "Cranial Plating": 4,
            "Sojourner's Companion": 4,
            "Nettlecyst": 2,
            "Engineered Explosives": 2,
            "Darksteel Citadel": 4,
            "Treasure Vault": 2,
            "Urza's Saga": 4,
            "Silverbluff Bridge": 2,
            "Razortide Bridge": 2,
            "Tanglepool Bridge": 2,
            "Mistvault Bridge": 2,
            "Island": 1,
            "Spire of Industry": 3,
            "Frogmite": 2,
        },
        "sideboard": {
            "Metallic Rebuke": 2,
            "Haywire Mite": 2,
            "Dispatch": 2,
            "Ethersworn Canonist": 2,
            "Relic of Progenitus": 2,
            "Hurkyl's Recall": 2,
            "Spell Pierce": 2,
            "Torpor Orb": 1,
        },
    },
    "Eldrazi Tron": {
        "mainboard": {
            "Thought-Knot Seer": 4,
            "Reality Smasher": 4,
            "Endbringer": 2,
            "Walking Ballista": 2,
            "Matter Reshaper": 4,
            "Eldrazi Mimic": 4,
            "Chalice of the Void": 4,
            "Expedition Map": 4,
            "All Is Dust": 2,
            "Warping Wail": 2,
            "Ugin, the Spirit Dragon": 2,
            "Kozilek's Command": 2,
            "Urza's Tower": 4,
            "Urza's Mine": 4,
            "Urza's Power Plant": 4,
            "Eldrazi Temple": 4,
            "Cavern of Souls": 2,
            "Blast Zone": 1,
            "Wastes": 3,
            "Ghost Quarter": 2,
        },
        "sideboard": {
            "Ratchet Bomb": 2,
            "Warping Wail": 2,
            "Spatial Contortion": 2,
            "Relic of Progenitus": 3,
            "Trinisphere": 2,
            "Wurmcoil Engine": 2,
            "Pithing Needle": 2,
        },
    },
    "Amulet Titan": {
        # Juintatz — Modern Challenge 64 (April 4, 2026)
        "mainboard": {
            "Primeval Titan": 4,
            "Arboreal Grazer": 4,
            "Cultivator Colossus": 1,
            "Aftermath Analyst": 1,
            "Dryad Arbor": 1,
            "Amulet of Vigor": 4,
            "Spelunking": 4,
            "Green Sun's Zenith": 4,
            "Scapeshift": 3,
            "Summoner's Pact": 2,
            "Vexing Bauble": 1,
            "Gruul Turf": 4,
            "Urza's Saga": 4,
            "Crumbling Vestige": 4,
            "Simic Growth Chamber": 3,
            "Boseiju, Who Endures": 3,
            "Forest": 3,
            "Lotus Field": 2,
            "Echoing Deeps": 1,
            "Hanweir Battlements // Hanweir, the Writhing Township": 1,
            "Mirrorpool": 1,
            "Otawara, Soaring City": 1,
            "Shifting Woodland": 1,
            "Tolaria West": 1,
            "Urza's Cave": 1,
            "Vesuva": 1,
        },
        "sideboard": {
            "Trinisphere": 3,
            "Dismember": 2,
            "Force of Vigor": 2,
            "Stock Up": 2,
            "Vampires' Vengeance": 2,
            "Bojuka Bog": 1,
            "Collector Ouphe": 1,
            "Six": 1,
            "Skyline Cascade": 1,
        },
    },
    "Goryo's Vengeance": {
        # Decklist construction fix (2026-04-26): the gameplan declares
        # Unburial Rites as a payoff (decks/gameplans/goryos_vengeance.json
        # card_priorities + critical_pieces) but the original list only
        # included 1×.  Meanwhile 4× Unmarked Grave was a near-dead slot
        # because it puts a NONLEGENDARY card in graveyard — the only
        # legal grab in this deck is Solitude (CMC 5), which the deck's
        # primary reanimator (Goryo's Vengeance, legendary-only) cannot
        # then target.  Replacing with 4× Unburial Rites (any creature,
        # incl. Griselbrand and Archon) gives the deck a real second
        # reanimation path and matches the gameplan declaration.
        # Mainboard count balanced by +1× Archon of Cruelty (more
        # legendary reanimation targets, valid for both Goryo's and
        # Unburial Rites).  Net: -4 Unmarked Grave, +3 Unburial Rites
        # (1→4), +1 Archon of Cruelty (2→3).  Total stays at 60.
        "mainboard": {
            "Goryo's Vengeance": 4,
            "Griselbrand": 4,
            "Archon of Cruelty": 3,
            "Solitude": 4,
            "Ephemerate": 4,
            "Faithful Mending": 4,
            "Thoughtseize": 4,
            "Persist": 3,
            "Undying Evil": 2,
            "Marsh Flats": 4,
            "Godless Shrine": 2,
            "Watery Grave": 1,
            "Hallowed Fountain": 1,
            "Silent Clearing": 2,
            "Flooded Strand": 4,
            "Swamp": 2,
            "Plains": 1,
            "Island": 1,
            "Concealed Courtyard": 4,
            "Leyline of Sanctity": 2,
            "Unburial Rites": 4,
        },
        "sideboard": {
            "Leyline of the Void": 4,
            "Flusterstorm": 2,
            "Wear // Tear": 2,
            "Teferi, Time Raveler": 2,
            "Prismatic Ending": 2,
            "Force of Negation": 2,
            "Rest in Peace": 1,
        },
    },
    "Domain Zoo": {
        # Mariscal — 5-0, Modern League (April 5, 2026)
        "mainboard": {
            "Ragavan, Nimble Pilferer": 4,
            "Doorkeeper Thrull": 4,
            "Territorial Kavu": 4,
            "Phlage, Titan of Fire's Fury": 4,
            "Scion of Draco": 4,
            "Teferi, Time Raveler": 1,
            "Lightning Bolt": 4,
            "Consign to Memory": 1,
            "Stubborn Denial": 2,
            "Leyline Binding": 4,
            "Leyline of the Guildpact": 4,
            "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki": 1,
            "The Legend of Roku": 1,
            "Arid Mesa": 4,
            "Flooded Strand": 4,
            "Wooded Foothills": 3,
            "Arena of Glory": 2,
            "Steam Vents": 2,
            "Blood Crypt": 1,
            "Temple Garden": 1,
            "Indatha Triome": 1,
            "Lush Portico": 1,
            "Thundering Falls": 1,
            "Mountain": 1,
            "Plains": 1,
        },
        "sideboard": {
            "Consign to Memory": 2,
            "Damping Sphere": 2,
            "Mystical Dispute": 2,
            "Obsidian Charmaw": 2,
            "Wear // Tear": 2,
            "Wrath of the Skies": 2,
            "Clarion Conqueror": 1,
            "Nihil Spellbomb": 1,
            "Surgical Extraction": 1,
        },
    },
    "Living End": {
        "mainboard": {
            "Living End": 4,
            "Shardless Agent": 4,
            "Demonic Dread": 4,
            "Force of Negation": 4,
            "Subtlety": 4,
            "Street Wraith": 4,
            "Striped Riverwinder": 4,
            "Architects of Will": 4,
            "Curator of Mysteries": 2,
            "Waker of Waves": 2,
            "Misty Rainforest": 4,
            "Verdant Catacombs": 4,
            "Breeding Pool": 1,
            "Watery Grave": 1,
            "Overgrown Tomb": 1,
            "Blood Crypt": 1,
            "Forest": 2,
            "Island": 2,
            "Swamp": 1,
            "Zagoth Triome": 1,
            "Ketria Triome": 1,
            "Indatha Triome": 1,
            "Raugrin Triome": 1,
            "Blooming Marsh": 2,
            "Botanical Sanctum": 1,
        },
        "sideboard": {
            "Foundation Breaker": 3,
            "Endurance": 3,
            "Mystical Dispute": 2,
            "Force of Vigor": 2,
            "Leyline of the Void": 3,
            "Boseiju, Who Endures": 2,
        },
    },
    "Izzet Prowess": {
        "mainboard": {
            # Creatures (12)
            "Dragon's Rage Channeler": 4,
            "Monastery Swiftspear": 4,
            "Slickshot Show-Off": 4,
            # Spells (30)
            "Lightning Bolt": 4,
            "Lava Dart": 4,
            "Unholy Heat": 2,
            "Mutagenic Growth": 4,
            "Violent Urge": 2,
            "Mishra's Bauble": 4,
            "Expressive Iteration": 4,
            "Preordain": 4,
            "Cori-Steel Cutter": 2,
            # Lands (18)
            "Scalding Tarn": 3,
            "Wooded Foothills": 3,
            "Arid Mesa": 2,
            "Bloodstained Mire": 2,
            "Steam Vents": 2,
            "Stomping Ground": 1,
            "Fiery Islet": 1,
            "Thundering Falls": 2,
            "Mountain": 2,
        },
        "sideboard": {
            "Consign to Memory": 4,
            "Pick Your Poison": 3,
            "Murktide Regent": 2,
            "Spell Pierce": 2,
            "Surgical Extraction": 2,
            "Meltdown": 1,
            "Spell Snare": 1,
        },
    },
    "Dimir Midrange": {
        "mainboard": {
            "Orcish Bowmasters": 4,
            "Psychic Frog": 4,
            "Subtlety": 2,
            "Murktide Regent": 4,
            "Thoughtseize": 4,
            "Fatal Push": 4,
            "Counterspell": 4,
            "Drown in the Loch": 2,
            "Consider": 4,
            "Spell Pierce": 2,
            "Archmage's Charm": 2,
            "Polluted Delta": 4,
            "Scalding Tarn": 2,
            "Watery Grave": 2,
            "Darkslick Shores": 4,
            "Island": 3,
            "Swamp": 1,
            "Otawara, Soaring City": 1,
            "Takenuma, Abandoned Mire": 1,
            "Underground River": 2,
            "Shelldock Isle": 1,
            "Creeping Tar Pit": 2,
            "Dauthi Voidwalker": 1,
        },
        "sideboard": {
            "Flusterstorm": 2,
            "Engineered Explosives": 1,
            "Mystical Dispute": 2,
            "Dress Down": 2,
            "Cling to Dust": 1,
            "Sheoldred, the Apocalypse": 2,
            "Nihil Spellbomb": 2,
            "Damnation": 1,
            "Tormod's Crypt": 2,
        },
    },
    "4c Omnath": {
        "mainboard": {
            # Lands (23)
            "Boseiju, Who Endures": 1,
            "Flooded Strand": 3,
            "Forest": 1,
            "Hallowed Fountain": 1,
            "Hedge Maze": 1,
            "Indatha Triome": 1,
            "Island": 1,
            "Lush Portico": 1,
            "Misty Rainforest": 3,
            "Overgrown Tomb": 1,
            "Plains": 1,
            "Raugrin Triome": 1,
            "Steam Vents": 1,
            "Stomping Ground": 1,
            "Temple Garden": 1,
            "Undercity Sewers": 1,
            "Windswept Heath": 3,
            # Creatures (20)
            "Elesh Norn, Mother of Machines": 1,
            "Endurance": 1,
            "Omnath, Locus of Creation": 4,
            "Orcish Bowmasters": 2,
            "Phelia, Exuberant Shepherd": 2,
            "Quantum Riddler": 4,
            "Risen Reef": 2,
            "Solitude": 4,
            # Instants + Sorceries (8)
            "Ephemerate": 3,
            "Lightning Bolt": 2,
            "Prismatic Ending": 2,
            "Supreme Verdict": 1,
            # Other spells (9)
            "Leyline Binding": 4,
            "Teferi, Time Raveler": 2,
            "Wrenn and Six": 3,
        },
        "sideboard": {
            "Ashiok, Dream Render": 1,
            "Boseiju, Who Endures": 1,
            "Consign to Memory": 3,
            "Endurance": 1,
            "Force of Negation": 2,
            "Force of Vigor": 2,
            "Obsidian Charmaw": 3,
            "Supreme Verdict": 1,
            "Surgical Extraction": 1,
        },
    },
    "4/5c Control": {
        "mainboard": {
            # Lands (23) — shadow438 mtgtop8 list
            "Arena of Glory": 1,
            "Breeding Pool": 1,
            "Elegant Parlor": 1,
            "Flooded Strand": 4,
            "Hallowed Fountain": 1,
            "Hedge Maze": 1,
            "Island": 1,
            "Lush Portico": 1,
            "Misty Rainforest": 3,
            "Plains": 1,
            "Sacred Foundry": 1,
            "Steam Vents": 1,
            "Stomping Ground": 1,
            "Temple Garden": 1,
            "Thundering Falls": 1,
            "Windswept Heath": 3,
            # Creatures (16)
            "Eternal Witness": 2,
            "Omnath, Locus of Creation": 3,
            "Phlage, Titan of Fire's Fury": 2,
            "Quantum Riddler": 4,
            "Solitude": 4,
            # Spells (21)
            "Ephemerate": 3,
            "Galvanic Discharge": 3,
            "Orim's Chant": 4,
            "Prismatic Ending": 2,
            "Stock Up": 2,
            "Teferi, Time Raveler": 3,
            "Wrath of the Skies": 2,
            "Wrenn and Six": 3,
        },
        "sideboard": {
            "Boseiju, Who Endures": 1,
            "Celestial Purge": 2,
            "Consign to Memory": 3,
            "Mystical Dispute": 4,
            "Surgical Extraction": 2,
            "Wear // Tear": 3,
        },
    },
    "Azorius Control (WST)": {
        # Wan Shi Tong draw-go control — Chalice of the Void maindeck package
        "mainboard": {
            "Wan Shi Tong, Librarian": 4,
            "March of Otherworldly Light": 4,
            "Chalice of the Void": 4,
            "Wrath of the Skies": 4,
            "Counterspell": 4,
            "Prismatic Ending": 4,
            "Supreme Verdict": 3,
            "Teferi, Time Raveler": 3,
            "Sanctifier en-Vec": 3,
            "Dovin's Veto": 2,
            "Flooded Strand": 4,
            "Polluted Delta": 4,
            "Hallowed Fountain": 4,
            "Meticulous Archive": 1,
            "Island": 7,
            "Plains": 5,
        },
        "sideboard": {
            "Subtlety": 3,
            "Damping Sphere": 3,
            "Rest in Peace": 2,
            "Engineered Explosives": 2,
            "Consign to Memory": 2,
            "Dovin's Veto": 1,
            "Force of Negation": 1,
            "Celestial Purge": 1,
        },
    },

    "Azorius Control (WST v2)": {
        # v2 — Chalice + Solitude build. Structural aggro-defense upgrade
        # over v1 (which had zero MB blockers, 31% weighted WR).
        # Delta from v1: +4 Solitude MB, -3 Sanctifier (→SB), -1 Supreme
        # Verdict (redundant with Wrath of the Skies). SB: +3 Sanctifier,
        # -1 Subtlety, -1 Damping Sphere.
        "mainboard": {
            "Wan Shi Tong, Librarian": 4,
            "Solitude": 4,
            "March of Otherworldly Light": 4,
            "Chalice of the Void": 4,
            "Wrath of the Skies": 4,
            "Counterspell": 4,
            "Prismatic Ending": 4,
            "Supreme Verdict": 2,
            "Teferi, Time Raveler": 3,
            "Dovin's Veto": 2,
            "Flooded Strand": 4,
            "Polluted Delta": 4,
            "Hallowed Fountain": 4,
            "Meticulous Archive": 1,
            "Island": 7,
            "Plains": 5,
        },
        "sideboard": {
            "Sanctifier en-Vec": 3,
            "Subtlety": 2,
            "Damping Sphere": 2,
            "Rest in Peace": 2,
            "Engineered Explosives": 2,
            "Consign to Memory": 2,
            "Dovin's Veto": 1,
            "Force of Negation": 1,
        },
    },

    "Pinnacle Affinity": {
        # UR Affinity with Pinnacle Emissary + Kappa Cannoneer
        "mainboard": {
            "Pinnacle Emissary": 4,
            "Kappa Cannoneer": 4,
            "Ornithopter": 4,
            "Memnite": 4,
            "Emry, Lurker of the Loch": 2,
            "Thought Monitor": 2,
            "Mox Opal": 4,
            "Mishra's Bauble": 4,
            "Springleaf Drum": 4,
            "Cranial Plating": 4,
            "Tormod's Crypt": 3,
            "Lavaspur Boots": 1,
            "Metallic Rebuke": 3,
            "Sink into Stupor // Soporific Springs": 2,
            "Urza's Saga": 4,
            "Darksteel Citadel": 4,
            "Silverbluff Bridge": 2,
            "Spire of Industry": 3,
            "Island": 1,
            "Mountain": 1,
        },
        "sideboard": {
            "Haywire Mite": 2,
            "Spell Pierce": 2,
            "Relic of Progenitus": 2,
            "Blood Moon": 2,
            "Ethersworn Canonist": 2,
            "Hurkyl's Recall": 2,
            "Force of Negation": 2,
            "Torpor Orb": 1,
        },
    },
    "Azorius Control": {
        # Yuri Anichini — 1st Place, Modern Monster @ Dungeon Street (Pisa, Italy), 22/02/2026
        # Isochron Scepter + Orim's Chant lock package, Solitude creature suite
        # Session 3 phase 6 tuning: added 3 Sanctifier en-Vec mainboard
        # (protection from red+black — specifically strong vs Boros Energy's
        # red creatures/burn and Dimir's black removal). Cut 1 Subtlety and
        # 2 Consult the Star Charts for the slots. Addresses the "0 mainboard
        # blockers" structural gap that kept the deck at 7.9% matrix-v3 WR.
        "mainboard": {
            "Solitude": 4,
            "Sanctifier en-Vec": 3,
            "Consult the Star Charts": 2,
            "Counterspell": 4,
            "Lórien Revealed": 2,
            "Orim's Chant": 4,
            "Prismatic Ending": 4,
            "Stock Up": 2,
            "Supreme Verdict": 2,
            "Wrath of the Skies": 2,
            "Isochron Scepter": 2,
            "Teferi, Hero of Dominaria": 2,
            "Teferi, Time Raveler": 4,
            "Arid Mesa": 2,
            "Demolition Field": 2,
            "Flooded Strand": 4,
            "Hall of Storm Giants": 1,
            "Hallowed Fountain": 2,
            "Island": 3,
            "Meticulous Archive": 2,
            "Monumental Henge": 1,
            "Mystic Gate": 1,
            "Otawara, Soaring City": 1,
            "Plains": 2,
            "Steam Vents": 1,
            "Thundering Falls": 1,
        },
        "sideboard": {
            "Kaheera, the Orphanguard": 1,
            "Consign to Memory": 4,
            "Mystical Dispute": 2,
            "Wear // Tear": 2,
            "Damping Sphere": 1,
            "High Noon": 2,
            "Celestial Purge": 1,
            "Rest in Peace": 1,
            "Wrath of the Skies": 1,
        },
    },
}


def get_deck_list(deck_name: str) -> dict:
    """Get a deck by name. Returns dict with 'mainboard' and 'sideboard'."""
    return MODERN_DECKS.get(deck_name, {})


def get_all_deck_names() -> list:
    """Get all available deck names."""
    return list(MODERN_DECKS.keys())


def get_metagame_weights() -> dict:
    """Get metagame share percentages for weighting simulations."""
    return METAGAME_SHARES.copy()


def validate_deck(deck: dict) -> Tuple[bool, str]:
    """Validate a deck has 60 mainboard and 15 sideboard cards."""
    mainboard_count = sum(deck.get("mainboard", {}).values())
    sideboard_count = sum(deck.get("sideboard", {}).values())

    if mainboard_count < 60:
        return False, f"Mainboard has {mainboard_count} cards (need 60)"
    if sideboard_count > 15:
        return False, f"Sideboard has {sideboard_count} cards (max 15)"
    return True, "OK"
