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
# July 2026 refresh — mtgdecks.net Modern meta (90-day window, retrieved
# 2026-07-05), post May-18-2026 B&R (Phlage, Titan of Fire's Fury and
# Lotus Field banned in Modern effective 2026-05-19).
# Top-10 real shares mapped onto registered decks; decks outside the
# real top-10 carry their observed standing from the mtgdecks 2-month
# meta table (Eldrazi Tron 4.78, Living End 3.47, Domain Aggro 2.78,
# Dimir Frog 2.14, Azorius Control 2.13) or a small residual share.
# "Izzet Prowess" carries the real "UR Cutter Prowess" share; "Jeskai
# Blink" carries the "Jeskai Control" share (same Jeskai bucket);
# "4/5c Control" carries the "4/5c Aggro" bucket (closest registered
# archetype). Raw percentages, not normalized to 100 — same convention
# as before (weighting normalizes by the sum). NOTE (2026-08-08): the
# mtgtop8 "4/5c Aggro" decklist pulled for this same date is actually a
# near-exact match for our registered "Domain Zoo" (Ragavan/Territorial
# Kavu/Leyline Binding core), not "4/5c Control" (an Omnath/Wrath shell
# with zero card overlap) — the July mapping above was likely wrong.
# Not corrected here; flagging for a follow-up share rebalance.
#
# Aug 2026 addition — mtgtop8.com Modern top-16 breakdown (retrieved
# 2026-08-08 via tools/fetch_tier1_decklists.py), a second, differently-
# sourced cohort layered onto the July mtgdecks.net-based numbers above.
# Five archetypes with real meta share had no registered deck at all;
# import_deck.py auto-generated their gameplans. A sixth (Hollow One,
# ~3%) was blocked at the time: both fetched Aug 2026 lists run
# Hardened Academic (4x) and Practiced Offense (2x) as core pieces,
# neither of which was in ModernAtomic. Unblocked 2026-08-08 via the
# new .github/workflows/refresh_card_db.yml (GitHub Actions infra, not
# subject to this session's mtgjson.com egress block) and registered
# below.
METAGAME_SHARES = {
    "Boros Energy": 15.93,
    "Affinity": 10.16,
    "Instant Reanimator": 4.88,
    "Amulet Titan": 4.81,
    "Eldrazi Tron": 4.78,
    "Ruby Storm": 3.99,
    "Izzet Prowess": 3.99,
    "Jeskai Blink": 3.80,
    "Living End": 3.47,
    "4/5c Control": 3.29,
    "Domain Zoo": 2.78,
    "Boros Ponza": 2.75,
    "Dimir Midrange": 2.14,
    "Azorius Control": 2.13,
    "Goryo's Vengeance": 1.50,
    "4c Omnath": 1.50,
    "Pinnacle Affinity": 1.50,
    "Azorius Control (WST)": 0.0,
    "Azorius Control (WST v2)": 0.0,
    "Eldrazi Ramp": 7.0,
    "Broodscale Bloodchief": 7.0,
    "Azorius Blink": 5.0,
    "Creatures Toolbox": 5.0,
    "Grixis Reanimator": 4.0,
    "Hollow One": 3.0,
}

# Full decklists: mainboard + sideboard
MODERN_DECKS: Dict[str, Dict[str, Dict[str, int]]] = {
    "Boros Energy": {
        # Aug 2026 refresh (base: mtgtop8.com Modern event=89283
        # deck=877557, retrieved 2026-08-08 via tools/fetch_tier1_decklists.py
        # / .github/workflows/weekly.yml — GitHub Actions egress, this
        # session's proxy blocks mtgtop8/mtggoldfish/mtgdecks directly).
        # Supersedes the July 2026 post-ban refresh (base: rarakkyo,
        # Modern Challenge 32, Apr 18 2026). Deltas vs that list:
        #   - Ranger-Captain of Eos 1→3, Voice of Victory 2→3
        #   - Fable of the Mirror-Breaker 3→1, The Legend of Roku 2→1
        #     (both trimmed as the deck leans harder on Ranger-Captain
        #     tutoring + a wider disruption suite instead of value engines)
        #   - NEW: Mana Tithe 2x (tempo counterspell, not previously played)
        #   - Blood Moon cut from MB (SB-only now); fetch package moved
        #     off Windswept Heath onto Flooded Strand (fetches Plains same
        #     as before — same on-color capability, different card)
        # A second fresh list (event=89319 deck=877794, same date) shows
        # an alternate direction with Den of the Bugbear, Haliya Guided
        # by Light, Reckless Pyrosurfer, and maindeck Lightning Bolt —
        # worth another pass if that build's share grows.
        "mainboard": {
            "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger": 4,
            "Guide of Souls": 4,
            "Ocelot Pride": 4,
            "Ragavan, Nimble Pilferer": 4,
            "Ranger-Captain of Eos": 3,
            "Seasoned Pyromancer": 3,
            "Voice of Victory": 3,
            "Galvanic Discharge": 4,
            "Mana Tithe": 2,
            "Thraben Charm": 2,
            "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki": 1,
            "Goblin Bombardment": 3,
            "The Legend of Roku": 1,
            "Arena of Glory": 1,
            "Arid Mesa": 4,
            "Dalkovan Encampment": 2,
            "Elegant Parlor": 2,
            "Flooded Strand": 4,
            "Marsh Flats": 4,
            "Plains": 2,
            "Sacred Foundry": 3,
        },
        "sideboard": {
            "Blood Moon": 2,
            "High Noon": 2,
            "Obsidian Charmaw": 2,
            "Orim's Chant": 2,
            "Surgical Extraction": 1,
            "The Legend of Roku": 1,
            "Vexing Bauble": 2,
            "Wear // Tear": 1,
            "Wrath of the Skies": 2,
        },
    },
    "Jeskai Blink": {
        # July 2026 post-ban refresh (base: Spellyp — 5-0, Modern League,
        # April 5 2026). Phlage banned 2026-05-19. Real-meta Jeskai now
        # tracks as "Jeskai Control" (~3.8%); this entry keeps the blink
        # shell (the surviving Jeskai build per post-ban Moxfield lists)
        # with the Phlage slots redistributed to control elements:
        #   - -4 Phlage → +1 Fable (3→4), +1 Wrath of the Skies (1→2),
        #     +1 Prismatic Ending (2→3), +1 Witch Enchanter (1→2)
        "mainboard": {
            # Creatures (18)
            "Phelia, Exuberant Shepherd": 4,
            "Quantum Riddler": 4,
            "Ragavan, Nimble Pilferer": 4,
            "Solitude": 4,
            "Witch Enchanter": 2,
            # Instants + Sorceries (15)
            "Consign to Memory": 4,
            "Ephemerate": 2,
            "Galvanic Discharge": 4,
            "Prismatic Ending": 3,
            "Wrath of the Skies": 2,
            # Other spells (4)
            "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki": 4,
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
            "Glimpse the Impossible": 2,
            # Card selection
            "Valakut Awakening // Valakut Stoneforge": 2,
            # Rebuy + Finisher access
            "Past in Flames": 2,
            "Wish": 2,
            # Storm finisher — 3 MB ensures opener-7 access ~30%
            # (Phase K PR-K2: was 1, audit showed chains of 11+ ending
            # without finisher in 68% of games)
            "Grapeshot": 3,
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
        # Aug 2026 refresh (base: mtgtop8.com Modern event=89263
        # deck=877433, retrieved 2026-08-08 via tools/fetch_tier1_decklists.py
        # / .github/workflows/weekly.yml). Replaces the classic "robots"
        # build (Mox Opal/Ornithopter/Memnite/Cranial Plating/Nettlecyst)
        # wholesale: two independent tournament results on the same date
        # (event=89263 and event=89289) both field the Kappa Cannoneer /
        # Pinnacle Emissary UR shell with ZERO overlap against the old
        # artifact-creature core. The classic build appears to no longer
        # be what's winning under the "Affinity" archetype tag. NOTE:
        # this now largely duplicates the separately-registered "Pinnacle
        # Affinity" entry below (same shell, same description at
        # registration time) — needs a human call on whether to merge/
        # dedupe the two registrations or keep them as build variants.
        "mainboard": {
            "Fiery Islet": 4,
            "Island": 2,
            "Shivan Reef": 2,
            "Spirebluff Canal": 4,
            "Steam Vents": 1,
            "Urza's Saga": 4,
            "Emry, Lurker of the Loch": 2,
            "Kappa Cannoneer": 4,
            "Pinnacle Emissary": 4,
            "Metallic Rebuke": 3,
            "Preordain": 2,
            "Claws of Gix": 3,
            "Engineered Explosives": 4,
            "Mishra's Bauble": 4,
            "Mox Opal": 4,
            "Pithing Needle": 1,
            "Shadowspear": 1,
            "Skateboard": 1,
            "Tormod's Crypt": 4,
            "Weapons Manufacturing": 4,
            "Welding Jar": 2,
        },
        "sideboard": {
            "Consign to Memory": 4,
            "Damping Sphere": 2,
            "Galvanic Blast": 2,
            "Mystical Dispute": 2,
            "Shattering Spree": 2,
            "Vexing Bauble": 1,
            "Whipflare": 2,
        },
    },
    "Eldrazi Tron": {
        # Aug 2026 refresh (base: mtgtop8.com Modern "UrzaTron"
        # event=89331 deck=877957, retrieved 2026-08-08 via
        # tools/fetch_tier1_decklists.py / .github/workflows/weekly.yml).
        # Wholesale rebuild vs the prior list — current stock trades the
        # classic Eldrazi-creature beatdown plan (Reality Smasher,
        # Eldrazi Mimic, Matter Reshaper, Walking Ballista, Endbringer,
        # Cavern of Souls, Ghost Quarter, Blast Zone — all cut) for a
        # planeswalker/artifact-ramp shell: Karn, the Great Creator (4x)
        # and Ugin, Eye of the Storms (4x) as the new finisher package,
        # Devourer of Destiny + Glaring Fleshraker as the creature suite,
        # Ugin's Labyrinth as a 4th land-slot addition, and maindeck
        # Trinisphere/Mind Stone/Dismember. Thought-Knot Seer survives
        # at a reduced count (4→3); old Ugin, the Spirit Dragon is gone.
        "mainboard": {
            "Eldrazi Temple": 4,
            "Swamp": 1,
            "Ugin's Labyrinth": 4,
            "Urza's Mine": 4,
            "Urza's Power Plant": 4,
            "Urza's Tower": 4,
            "Wastes": 1,
            "Devourer of Destiny": 4,
            "Glaring Fleshraker": 3,
            "Thought-Knot Seer": 3,
            "All Is Dust": 1,
            "Dismember": 3,
            "Kozilek's Command": 4,
            "Chalice of the Void": 3,
            "Expedition Map": 4,
            "Karn, the Great Creator": 4,
            "Mind Stone": 4,
            "Relic of Progenitus": 1,
            "Ugin, Eye of the Storms": 4,
        },
        "sideboard": {
            "Cityscape Leveler": 1,
            "Disruptor Flute": 2,
            "Ensnaring Bridge": 1,
            "Extinguisher Battleship": 1,
            "Grafdigger's Cage": 2,
            "Liquimetal Coating": 1,
            "The Filigree Sylex": 1,
            "The Stone Brain": 1,
            "Tormod's Crypt": 1,
            "Torpor Orb": 2,
            "Trinisphere": 1,
            "Walking Ballista": 1,
        },
    },
    "Amulet Titan": {
        # Aug 2026 refresh (base: mtgtop8.com Modern event=89330
        # deck=877937, retrieved 2026-08-08 via tools/fetch_tier1_decklists.py
        # / .github/workflows/weekly.yml). Supersedes the July 2026
        # post-ban list (base: Juintatz, Modern Challenge 64, Apr 4 2026).
        # Deltas: NEW Malevolent Rumble (2x, land-into-hand selection —
        # missing from the prior list entirely); NEW The Mycosynth
        # Gardens (1x); Primeval Titan 4→3 (fewer copies now that
        # Malevolent Rumble adds another way to assemble the bounceland
        # chain without drawing the Titan itself); Vexing Bauble moved
        # SB-only; Zuran Orb added as a 1-of. Core bounceland/Saga shell
        # unchanged.
        "mainboard": {
            "Primeval Titan": 3,
            "Arboreal Grazer": 4,
            "Cultivator Colossus": 1,
            "Aftermath Analyst": 1,
            "Dryad Arbor": 1,
            "Amulet of Vigor": 4,
            "Spelunking": 4,
            "Green Sun's Zenith": 4,
            "Malevolent Rumble": 2,
            "Scapeshift": 3,
            "Summoner's Pact": 2,
            "Zuran Orb": 1,
            "Gruul Turf": 3,
            "Urza's Saga": 4,
            "Crumbling Vestige": 4,
            "Simic Growth Chamber": 4,
            "Boseiju, Who Endures": 3,
            "Forest": 3,
            "Echoing Deeps": 1,
            "Hanweir Battlements // Hanweir, the Writhing Township": 1,
            "Mirrorpool": 1,
            "Otawara, Soaring City": 1,
            "Shifting Woodland": 1,
            "The Mycosynth Gardens": 1,
            "Tolaria West": 1,
            "Urza's Cave": 1,
            "Vesuva": 1,
        },
        "sideboard": {
            "Trinisphere": 3,
            "Dismember": 1,
            "Force of Vigor": 2,
            "Stock Up": 2,
            "Bojuka Bog": 1,
            "Collector Ouphe": 1,
            "Firespout": 2,
            "Icetill Explorer": 1,
            "Six": 1,
            "Vexing Bauble": 1,
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
        #
        # 2026-04-26 (later): replace 3× Persist with 3× Inquisition of
        # Kozilek.  Persist returns NONLEGENDARY creature cards only;
        # the deck's reanimation targets (Griselbrand, Archon) are
        # legendary, so Persist could only return Solitude — a value
        # play, not a combo win.  Inquisition strips opp's CMC≤3 cards
        # (Memnite, Mox Opal, Cranial Plating, Frogmite, Springleaf
        # Drum, Bauble) — denies Affinity's whole curve and routes
        # cleanly into the existing 4 Thoughtseize (7 disruption
        # spells total).  Total stays at 60.
        "mainboard": {
            "Goryo's Vengeance": 4,
            "Griselbrand": 4,
            "Atraxa, Grand Unifier": 4,
            "Archon of Cruelty": 3,
            "Ephemerate": 4,
            "Faithful Mending": 4,
            "Thoughtseize": 4,
            "Inquisition of Kozilek": 3,
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
        # July 2026 post-ban refresh: Phlage banned 2026-05-19.
        # -4 Phlage → +4 Wild Nacatl (turn-1 3/3 under Leyline of the
        # Guildpact; the aggro slot post-ban "Domain Aggro" lists lean on).
        "mainboard": {
            "Ragavan, Nimble Pilferer": 4,
            "Doorkeeper Thrull": 4,
            "Territorial Kavu": 4,
            "Wild Nacatl": 4,
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
        # July 2026 refresh — real meta tracks this as "UR Cutter Prowess"
        # (~3.99%): stock June-2026 builds run the full 4 Cori-Steel Cutter
        # (mtgdecks/mtggoldfish archetype pages, June 2026).
        # +2 Cori-Steel Cutter (2→4), -2 Violent Urge.
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
            "Mishra's Bauble": 4,
            "Expressive Iteration": 4,
            "Preordain": 4,
            "Cori-Steel Cutter": 4,
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
            # Creatures (14) — July 2026 post-ban refresh: Phlage banned
            # 2026-05-19; -2 Phlage → +1 Wrath of the Skies (2→3),
            # +1 Stock Up (2→3).
            "Eternal Witness": 2,
            "Omnath, Locus of Creation": 3,
            "Quantum Riddler": 4,
            "Solitude": 4,
            # Spells (23)
            "Ephemerate": 3,
            "Galvanic Discharge": 3,
            "Orim's Chant": 4,
            "Prismatic Ending": 2,
            "Stock Up": 3,
            "Teferi, Time Raveler": 3,
            "Wrath of the Skies": 3,
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
    "Instant Reanimator": {
        # Aug 2026 refresh (base: mtgtop8.com Modern event=89283
        # deck=877556, retrieved 2026-08-08 via tools/fetch_tier1_decklists.py
        # / .github/workflows/weekly.yml — the mtgdecks.net egress block
        # noted in the July 2026 entry is resolved for this pipeline
        # since it runs on GitHub's own infra, not this session's proxy).
        # Deltas vs the July list: NEW Fallaji Archaeologist (2x, self-mill
        # + graveyard fuel — missing before entirely); NEW March of
        # Otherworldly Light (1x removal, from the Marvel Super Heroes
        # set that ModernAtomic now carries); Force of Negation 3→2.
        # Core Goryo's/Ephemerate/Atraxa shell unchanged. A second fresh
        # list (event=89301 deck=877684, same date) cuts Quantum Riddler
        # entirely for a 4th Fallaji Archaeologist plus Superior
        # Spider-Man and Otherworldly Gaze — a build worth another look
        # if that direction's share grows.
        "mainboard": {
            "Breeding Pool": 1,
            "Flooded Strand": 4,
            "Godless Shrine": 1,
            "Hallowed Fountain": 1,
            "Island": 1,
            "Marsh Flats": 2,
            "Meticulous Archive": 1,
            "Misty Rainforest": 1,
            "Plains": 1,
            "Polluted Delta": 4,
            "Shadowy Backstreet": 1,
            "Swamp": 1,
            "Undercity Sewers": 1,
            "Watery Grave": 1,
            "Atraxa, Grand Unifier": 4,
            "Fallaji Archaeologist": 2,
            "Griselbrand": 1,
            "Psychic Frog": 4,
            "Quantum Riddler": 4,
            "Solitude": 4,
            "Ephemerate": 4,
            "Faithful Mending": 4,
            "Force of Negation": 2,
            "Goryo's Vengeance": 4,
            "March of Otherworldly Light": 1,
            "Prismatic Ending": 2,
            "Thoughtseize": 3,
        },
        "sideboard": {
            "Celestial Purge": 1,
            "Consign to Memory": 4,
            "Mystical Dispute": 3,
            "Spell Snare": 2,
            "Surgical Extraction": 1,
            "Teferi, Time Raveler": 1,
            "Wrath of the Skies": 3,
        },
    },
    "Boros Ponza": {
        # July 2026 meta addition (~2.75%). Base: Milos Mrkic — 2nd
        # (6-1-2), Modern Destination Qualifier EUL Premier @ MOLE,
        # May 27 2026 (mtgdecks.net / unityleague.gg). Land-denial shell:
        # Blood Moon + Cleansing Wildfire/Pillage/Obsidian Charmaw with an
        # Ephemerate blink package (Phelia/Solitude/Seasoned Pyromancer).
        # 47 of 60 MB slots confirmed from the source; remaining land
        # slots and sideboard reconstructed from archetype staples
        # (deck sites were egress-blocked this session — see PR body).
        "mainboard": {
            "Arena of Glory": 1,
            "Arid Mesa": 4,
            "Blood Moon": 4,
            "Cleansing Wildfire": 4,
            "Elegant Parlor": 1,
            "Ephemerate": 4,
            "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki": 1,
            "Flagstones of Trokair": 2,
            "Inspiring Vantage": 2,
            "Lightning Bolt": 4,
            "Marsh Flats": 4,
            "Mountain": 3,
            "Obsidian Charmaw": 3,
            "Phelia, Exuberant Shepherd": 3,
            "Pillage": 3,
            "Plains": 4,
            "Ragavan, Nimble Pilferer": 3,
            "Sacred Foundry": 2,
            "Seasoned Pyromancer": 4,
            "Solitude": 3,
            "Sunbaked Canyon": 1,
        },
        "sideboard": {
            "Celestial Purge": 2,
            "Damping Sphere": 2,
            "Kor Firewalker": 2,
            "Magus of the Moon": 1,
            "Molten Rain": 2,
            "Rest in Peace": 2,
            "Sanctifier en-Vec": 2,
            "Wear // Tear": 2,
        },
    },
    "Eldrazi Ramp": {
        # Aug 2026 addition (base: mtgtop8.com Modern event=89301
        # deck=877685, retrieved 2026-08-08 via
        # tools/fetch_tier1_decklists.py / .github/workflows/weekly.yml).
        # 7% meta share, previously unregistered — distinct from
        # "Eldrazi Tron" (no Urza's Tower/Mine/Power Plant here; this is
        # a green Ugin's Labyrinth / Eldrazi Temple ramp shell built
        # around Sowing Mycospawn + Malevolent Rumble + Ugin, Eye of the
        # Storms). Gameplan auto-generated via import_deck.py. Fetched
        # list ran 1x World Breaker; swapped for a 4th Emrakul, the
        # Promised End — World Breaker's "{2}{C}, sacrifice a land:
        # return this from graveyard to hand" is a genuine unhandled
        # activated ability (test_oracle_validation.py caught it; the
        # existing generic sacrifice framework only covers self-
        # sacrifice "Sacrifice this:" patterns, not sac-a-different-
        # permanent costs). Implementing graveyard activation properly
        # is real engine work, out of scope for this decklist PR.
        "mainboard": {
            "Commercial District": 1,
            "Devourer of Destiny": 3,
            "Eldrazi Temple": 4,
            "Emrakul, the Promised End": 4,
            "Forest": 3,
            "Grove of the Burnwillows": 3,
            "Herigast, Erupting Nullkite": 1,
            "Icetill Explorer": 3,
            "Kozilek's Command": 4,
            "Kozilek's Return": 3,
            "Malevolent Rumble": 4,
            "Sanctum of Ugin": 1,
            "Shifting Woodland": 1,
            "Sire of Seven Deaths": 2,
            "Sowing Mycospawn": 4,
            "Stomping Ground": 2,
            "Talisman of Impulse": 4,
            "Ugin's Labyrinth": 4,
            "Ugin, Eye of the Storms": 2,
            "Utopia Sprawl": 4,
            "Wooded Foothills": 3,
        },
        "sideboard": {
            "Blasphemous Act": 2,
            "Bojuka Bog": 1,
            "Grafdigger's Cage": 1,
            "Nature's Claim": 2,
            "Soulless Jailer": 1,
            "Surgical Extraction": 2,
            "Trinisphere": 3,
            "Unholy Heat": 3,
        },
    },
    "Broodscale Bloodchief": {
        # Aug 2026 addition (base: mtgtop8.com Modern event=89330
        # deck=877939, retrieved 2026-08-08 via
        # tools/fetch_tier1_decklists.py / .github/workflows/weekly.yml).
        # 7% meta share, previously unregistered. Gruul Eldrazi-adjacent
        # midrange: Urza's Saga + Ancient Stirrings for consistency,
        # Basking Broodscale / Glaring Fleshraker / Sowing Mycospawn as
        # the creature suite, Malevolent Rumble for card selection,
        # Blade of the Bloodchief as the payoff. Gameplan auto-generated
        # via import_deck.py.
        "mainboard": {
            "Ancient Stirrings": 4,
            "Basking Broodscale": 4,
            "Blade of the Bloodchief": 3,
            "Boseiju, Who Endures": 2,
            "Delighted Halfling": 2,
            "Eldrazi Temple": 4,
            "Emrakul, the Promised End": 3,
            "Forest": 3,
            "Glaring Fleshraker": 3,
            "Grove of the Burnwillows": 4,
            "Haywire Mite": 1,
            "Kozilek's Command": 4,
            "Malevolent Rumble": 4,
            "Misty Rainforest": 1,
            "Shifting Woodland": 1,
            "Soul-Guide Lantern": 1,
            "Sowing Mycospawn": 4,
            "Springleaf Drum": 1,
            "Stomping Ground": 1,
            "The Mycosynth Gardens": 1,
            "Unholy Heat": 2,
            "Urza's Saga": 4,
            "Verdant Catacombs": 1,
            "Vexing Bauble": 2,
        },
        "sideboard": {
            "Cavern of Souls": 1,
            "Damping Sphere": 1,
            "Gemstone Caverns": 1,
            "Grafdigger's Cage": 1,
            "Nature's Claim": 2,
            "Pithing Needle": 1,
            "Sire of Seven Deaths": 1,
            "Soulless Jailer": 1,
            "Thief of Existence": 2,
            "Unholy Heat": 2,
            "Vexing Bauble": 1,
            "Warping Wail": 1,
        },
    },
    "Creatures Toolbox": {
        # Aug 2026 addition (base: mtgtop8.com Modern event=89319
        # deck=877792, retrieved 2026-08-08 via
        # tools/fetch_tier1_decklists.py / .github/workflows/weekly.yml).
        # 5% meta share, previously unregistered. Green creature-combo
        # toolbox: Devoted Druid + Vizier of Remedies (infinite mana),
        # Green Sun's Zenith / Fiend Artisan to tutor pieces, Craterhoof
        # Behemoth as the payoff. Gameplan auto-generated via
        # import_deck.py. KNOWN GAP: "Shang-Chi, Master of Kung Fu" (a
        # 1-of in both fetched lists for this archetype) is not yet in
        # ModernAtomic — resolves to an engine placeholder until
        # update_modern_atomic.py can run from an environment that can
        # reach mtgjson.com.
        "mainboard": {
            "Agatha's Soul Cauldron": 2,
            "Badgermole Cub": 4,
            "Birds of Paradise": 2,
            "Boseiju, Who Endures": 1,
            "Craterhoof Behemoth": 1,
            "Delighted Halfling": 4,
            "Devoted Druid": 4,
            "Dryad Arbor": 2,
            "Duskwatch Recruiter": 1,
            "Eternal Witness": 1,
            "Fiend Artisan": 1,
            "Forest": 3,
            "Green Sun's Zenith": 4,
            "Leyline of Abundance": 4,
            "Misty Rainforest": 2,
            "Nature's Rhythm": 4,
            "Nurturing Peatland": 1,
            "Ouroboroid": 1,
            "Overgrown Tomb": 2,
            "Shang-Chi, Master of Kung Fu": 1,
            "Temple Garden": 1,
            "Tyvar, Jubilant Brawler": 4,
            "Underground Mortuary": 1,
            "Verdant Catacombs": 3,
            "Vizier of Remedies": 1,
            "Walking Ballista": 1,
            "Windswept Heath": 2,
            "Wooded Foothills": 2,
        },
        "sideboard": {
            "Collector Ouphe": 1,
            "Drannith Magistrate": 1,
            "Endurance": 2,
            "Fatal Push": 2,
            "Force of Vigor": 2,
            "Grist, the Hunger Tide": 1,
            "Keen-Eyed Curator": 1,
            "Outland Liberator": 1,
            "Suncleanser": 1,
            "Vexing Bauble": 2,
        },
    },
    "Grixis Reanimator": {
        # Aug 2026 addition (base: mtgtop8.com Modern "Reanimator"
        # event=89271 deck=877501, retrieved 2026-08-08 via
        # tools/fetch_tier1_decklists.py / .github/workflows/weekly.yml).
        # ~4% meta share, previously unregistered under this name — the
        # diff tool's naive substring match had flagged it "yes" against
        # "Instant Reanimator," but the two share zero cards. This is a
        # Persist/Unearth reanimation shell (Archon of Cruelty, Emperor
        # of Bones, Abhorrent Oculus) with self-mill (Faithless Looting,
        # Thought Scour), distinct from Instant Reanimator's Esper
        # Goryo's Vengeance/Ephemerate combo and from the standalone
        # "Goryo's Vengeance" registration. Gameplan auto-generated via
        # import_deck.py.
        "mainboard": {
            "Abhorrent Oculus": 4,
            "Archon of Cruelty": 4,
            "Blood Crypt": 1,
            "Bloodstained Mire": 4,
            "Cling to Dust": 1,
            "Darkslick Shores": 1,
            "Emperor of Bones": 3,
            "Faithless Looting": 4,
            "Fatal Push": 4,
            "Inquisition of Kozilek": 1,
            "Island": 1,
            "Persist": 4,
            "Polluted Delta": 4,
            "Psychic Frog": 4,
            "Raucous Theater": 1,
            "Scalding Tarn": 1,
            "Spell Pierce": 1,
            "Steam Vents": 1,
            "Swamp": 2,
            "Thought Scour": 3,
            "Thoughtseize": 4,
            "Undercity Sewers": 1,
            "Unearth": 4,
            "Verdant Catacombs": 1,
            "Watery Grave": 1,
        },
        "sideboard": {
            "Consign to Memory": 3,
            "Harbinger of the Seas": 2,
            "Meltdown": 2,
            "Mystical Dispute": 2,
            "Nihil Spellbomb": 2,
            "Pyroclasm": 2,
            "Vexing Bauble": 2,
        },
    },
    "Azorius Blink": {
        # Aug 2026 addition (base: mtgtop8.com Modern "Blink"
        # event=89319 deck=877791, retrieved 2026-08-08 via
        # tools/fetch_tier1_decklists.py / .github/workflows/weekly.yml).
        # ~5% meta share, previously unregistered under this name —
        # distinct from the 3-color "Jeskai Blink" (Ragavan/Fable/Wrath
        # of the Skies shell): this is a 2-color WU blink build around
        # Phelia/Witch Enchanter/Ephemerate with Ocelot Pride/Guide of
        # Souls energy support and no red at all. Gameplan
        # auto-generated via import_deck.py.
        "mainboard": {
            "Aang, Swift Savior": 2,
            "Arid Mesa": 2,
            "Consign to Memory": 2,
            "Ephemerate": 4,
            "Flooded Strand": 4,
            "Guide of Souls": 4,
            "Haliya, Guided by Light": 1,
            "Hallowed Fountain": 4,
            "March of Otherworldly Light": 2,
            "Marsh Flats": 2,
            "Meticulous Archive": 2,
            "Momo, Friendly Flier": 3,
            "Ocelot Pride": 4,
            "Phelia, Exuberant Shepherd": 4,
            "Plains": 3,
            "Quantum Riddler": 4,
            "Solitude": 4,
            "Starfield Shepherd": 4,
            "Windswept Heath": 1,
            "Witch Enchanter": 4,
        },
        "sideboard": {
            "Clarion Conqueror": 3,
            "Consign to Memory": 2,
            "Rest in Peace": 1,
            "Sanctifier en-Vec": 3,
            "Spell Pierce": 2,
            "White Orchid Phantom": 4,
        },
    },
    "Hollow One": {
        # Aug 2026 addition (base: mtgtop8.com Modern event=89330
        # deck=877940, retrieved 2026-08-08 via
        # tools/fetch_tier1_decklists.py / .github/workflows/weekly.yml).
        # 3% meta share; blocked at initial registration (PR #486) on
        # two missing cards (Hardened Academic, Practiced Offense),
        # unblocked by .github/workflows/refresh_card_db.yml pulling a
        # fresh ModernAtomic from mtgjson.com. Classic graveyard-fuel
        # aggro: discard the hand with Burning Inquiry/Faithless
        # Looting to power out Hollow One + Vengevine for free.
        # Gameplan auto-generated via import_deck.py.
        "mainboard": {
            "Arena of Glory": 1,
            "Arid Mesa": 1,
            "Blazing Rootwalla": 4,
            "Bloodstained Mire": 3,
            "Burning Inquiry": 4,
            "Detective's Phoenix": 4,
            "Faithless Looting": 4,
            "Hardened Academic": 4,
            "Hollow One": 4,
            "Lightning Bolt": 4,
            "Marauding Mako": 4,
            "Mountain": 4,
            "Ox of Agonas": 1,
            "Practiced Offense": 2,
            "Sacred Foundry": 4,
            "Street Wraith": 4,
            "Vengevine": 4,
            "Wooded Foothills": 4,
        },
        "sideboard": {
            "Blood Moon": 2,
            "Brotherhood's End": 1,
            "Damping Sphere": 2,
            "Meltdown": 2,
            "Obsidian Charmaw": 2,
            "Pithing Needle": 1,
            "Pyroclasm": 2,
            "Surgical Extraction": 2,
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
