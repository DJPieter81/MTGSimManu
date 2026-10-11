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
# Blink" carries the "Jeskai Control" share (same Jeskai bucket).
# Raw percentages, not normalized to 100 — same convention as before
# (weighting normalizes by the sum).
#
# RESOLVED (2026-08-09): "4/5c Control" originally carried the
# mtgdecks.net "4/5c Aggro" bucket (3.29%) as its "closest registered
# archetype" stand-in — but the mtgtop8 "4/5c Aggro" decklist pulled
# 2026-08-08 is a near-exact card match for our registered "Domain
# Zoo" (Ragavan/Territorial Kavu/Leyline Binding core), not "4/5c
# Control" (an Omnath/Wrath shell with zero card overlap). "4/5c
# Aggro" and "Domain Aggro" (the bucket Domain Zoo already carries,
# 2.78%) both look like community aliases for the same Naya/Domain
# beatdown archetype, so Domain Zoo now carries the combined 6.07%
# (2.78 + 3.29) and "4/5c Control" drops to the same small-residual
# share already used for Goryo's Vengeance/4c Omnath/Pinnacle
# Affinity (1.50%) — it has no confirmed real-world bucket of its own.
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
    # Sep 2026 refresh — mtgtop8.com Modern metagame breakdown (top 20,
    # retrieved 2026-09-27 via tools/fetch_tier1_decklists.py;
    # data/tier1_decklists/2026-09-27/DIFF_REPORT.md). Buckets mapped to
    # registered decks by card overlap: UR Aggro -> Izzet Prowess (the bucket
    # also holds UR Murktide, unregistered), UrzaTron -> Eldrazi Tron, Dimir
    # Control -> Dimir Midrange, Boros Aggro -> Boros Energy, 4/5c Aggro ->
    # Domain Zoo, UW Control -> Azorius Control, Blink -> Azorius Blink.
    # Registered decks absent from the top 20 carry a 0.5% residual;
    # the two WST lists stay at 0. Unregistered top-20 buckets: Allosaurus
    # Combo 2%, Landless 2%, Red Deck Wins 1%. Raw percentages.
    "Broodscale Bloodchief": 19.0,
    "Izzet Prowess": 12.0,
    "Azorius Blink": 7.0,
    "Instant Reanimator": 6.0,
    "Creatures Toolbox": 6.0,
    "Affinity": 5.0,
    "Eldrazi Tron": 5.0,
    "Dimir Midrange": 4.0,
    "Boros Energy": 3.0,
    "Ruby Storm": 3.0,
    "Living End": 3.0,
    "Domain Zoo": 3.0,
    "Boros Ponza": 3.0,
    "Azorius Control": 3.0,
    "Eldrazi Ramp": 3.0,
    "Amulet Titan": 2.0,
    "Hollow One": 0.8,
    "Jeskai Blink": 0.5,
    "4/5c Control": 0.5,
    "Goryo's Vengeance": 0.5,
    "4c Omnath": 0.5,
    "Pinnacle Affinity": 0.5,
    "Grixis Reanimator": 0.5,
    "Azorius Control (WST)": 0.0,
    "Azorius Control (WST v2)": 0.0,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91251 deck=893023,
        # bucket "Boros Aggro" 3.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger": 4,
            "Arena of Glory": 2,
            "Arid Mesa": 4,
            "Dalkovan Encampment": 1,
            "Elegant Parlor": 2,
            "Flooded Strand": 4,
            "Galvanic Discharge": 4,
            "Goblin Bombardment": 3,
            "Guide of Souls": 4,
            "Mana Tithe": 2,
            "Marsh Flats": 4,
            "Mountain": 1,
            "Ocelot Pride": 4,
            "Plains": 2,
            "Ragavan, Nimble Pilferer": 4,
            "Ranger-Captain of Eos": 2,
            "Sacred Foundry": 2,
            "Seasoned Pyromancer": 4,
            "Static Prison": 1,
            "Thraben Charm": 2,
            "Voice of Victory": 4,
        },
        "sideboard": {
            "Celestial Purge": 1,
            "Clarion Conqueror": 1,
            "High Noon": 1,
            "Obsidian Charmaw": 4,
            "Orim's Chant": 1,
            "Rest in Peace": 1,
            "Sanctifier en-Vec": 1,
            "Surgical Extraction": 1,
            "Wear // Tear": 2,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91250 deck=893013,
        # bucket "Ruby Storm" 3.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Arid Mesa": 2,
            "Artist's Talent": 2,
            "Bloodstained Mire": 2,
            "Desperate Ritual": 4,
            "Elegant Parlor": 1,
            "Gemstone Caverns": 1,
            "Grapeshot": 1,
            "Hex Magic": 4,
            "Manamorphose": 4,
            "Mountain": 4,
            "Past in Flames": 3,
            "Pyretic Ritual": 4,
            "Ral, Monsoon Mage // Ral, Leyline Prodigy": 4,
            "Raucous Theater": 1,
            "Reckless Impulse": 4,
            "Ruby Medallion": 4,
            "Sacred Foundry": 1,
            "Scalding Tarn": 3,
            "Sunbaked Canyon": 1,
            "Valakut Awakening // Valakut Stoneforge": 2,
            "Wish": 2,
            "Wooded Foothills": 2,
            "Wrenn's Resolve": 4,
        },
        "sideboard": {
            "Empty the Warrens": 1,
            "Grapeshot": 1,
            "Orim's Chant": 4,
            "Past in Flames": 1,
            "Prismatic Ending": 4,
            "Untimely Malfunction": 1,
            "Vandalblast": 1,
            "Wear // Tear": 2,
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
        # be what's winning under the "Affinity" archetype tag.
        #
        # RESOLVED (2026-08-09): full card-level diff against "Pinnacle
        # Affinity" below shows these are two genuinely distinct builds,
        # not duplicates — no merge needed. Shared payoff core (Kappa
        # Cannoneer, Pinnacle Emissary, Urza's Saga, Emry, Mox Opal,
        # Mishra's Bauble, Tormod's Crypt, Metallic Rebuke), but this
        # entry is a spells/tempo shell (Preordain, Claws of Gix, extra
        # cantrips/counters, zero cheap-artifact-creature package) while
        # "Pinnacle Affinity" hybridizes the same payoffs with the
        # classic robots creature base (Ornithopter, Memnite, Cranial
        # Plating, Springleaf Drum). Different game plans, same core.
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91282 deck=893223,
        # bucket "Affinity" 5.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Claws of Gix": 3,
            "Emry, Lurker of the Loch": 3,
            "Engineered Explosives": 4,
            "Fiery Islet": 4,
            "Galvanic Blast": 2,
            "Island": 2,
            "Kappa Cannoneer": 4,
            "Mishra's Bauble": 4,
            "Mox Opal": 4,
            "Pinnacle Emissary": 4,
            "Pithing Needle": 1,
            "Salvage Titan": 2,
            "Shadowspear": 1,
            "Shivan Reef": 2,
            "Skateboard": 1,
            "Spirebluff Canal": 4,
            "Steam Vents": 1,
            "Tormod's Crypt": 4,
            "Urza's Saga": 4,
            "Weapons Manufacturing": 4,
            "Welding Jar": 2,
        },
        "sideboard": {
            "Abrade": 1,
            "Blood Moon": 2,
            "Consign to Memory": 3,
            "Cursed Totem": 2,
            "Metallic Rebuke": 3,
            "Mystical Dispute": 1,
            "Swan Song": 1,
            "Vexing Bauble": 1,
            "Whipflare": 1,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91253 deck=893047,
        # bucket "UrzaTron" 5.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Abstergo Entertainment": 1,
            "All Is Dust": 1,
            "Chalice of the Void": 2,
            "Devourer of Destiny": 4,
            "Dismember": 3,
            "Eldrazi Temple": 4,
            "Emrakul, the Promised End": 2,
            "Expedition Map": 4,
            "Karn, the Great Creator": 4,
            "Kozilek's Command": 4,
            "Mind Stone": 3,
            "Swamp": 2,
            "Trinisphere": 2,
            "Ugin's Labyrinth": 4,
            "Ugin, Eye of the Storms": 4,
            "Ulamog, the Ceaseless Hunger": 1,
            "Urza's Mine": 4,
            "Urza's Power Plant": 4,
            "Urza's Tower": 4,
            "Vexing Bauble": 1,
            "Warping Wail": 2,
        },
        "sideboard": {
            "Cityscape Leveler": 1,
            "Cursed Totem": 1,
            "Disruptor Flute": 1,
            "Ensnaring Bridge": 1,
            "Extinguisher Battleship": 1,
            "Grafdigger's Cage": 1,
            "Liquimetal Coating": 1,
            "Oblivion Stone": 1,
            "Pithing Needle": 1,
            "The Filigree Sylex": 1,
            "The Stone Brain": 1,
            "Tormod's Crypt": 1,
            "Torpor Orb": 1,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91038 deck=891429,
        # bucket "Amulet Titan" 2.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Aftermath Analyst": 1,
            "Amulet of Vigor": 4,
            "Arboreal Grazer": 4,
            "Boseiju, Who Endures": 3,
            "Crumbling Vestige": 4,
            "Cultivator Colossus": 1,
            "Dryad of the Ilysian Grove": 1,
            "Echoing Deeps": 1,
            "Forest": 3,
            "Green Sun's Zenith": 3,
            "Gruul Turf": 4,
            "Hanweir Battlements // Hanweir, the Writhing Township": 1,
            "Mirrorpool": 1,
            "Primeval Titan": 3,
            "Sakura-Tribe Scout": 2,
            "Scapeshift": 4,
            "Shifting Woodland": 1,
            "Simic Growth Chamber": 3,
            "Spelunking": 4,
            "Summoner's Pact": 2,
            "The Mycosynth Gardens": 3,
            "Tolaria West": 1,
            "Urza's Saga": 4,
            "Valakut, the Molten Pinnacle": 1,
            "Vesuva": 1,
        },
        "sideboard": {
            "Bojuka Bog": 1,
            "Boseiju, Who Endures": 1,
            "Cursed Totem": 2,
            "Dismember": 3,
            "Elvish Reclaimer": 1,
            "Endurance": 2,
            "Fire Magic": 1,
            "Force of Vigor": 1,
            "Tireless Tracker": 1,
            "Vexing Bauble": 2,
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
        # Aug 2026 — the real mtgtop8 "4/5c Aggro" list (event 89330,
        # deck 877936), fetched by tools/fetch_tier1_decklists.py on
        # 2026-08-08. Adopted 2026-08-30 together with that bucket's real
        # 4.0% share; see the METAGAME_SHARES note above for why the two
        # must move together.
        #
        # Supersedes an older approximation whose 20-card domain core
        # (Ragavan / Scion of Draco / Territorial Kavu / both Leylines)
        # matched, but whose spells did not: it ran 4 Lightning Bolt,
        # 4 Wild Nacatl and 4 Doorkeeper Thrull where the real deck runs
        # 4 Psychic Frog, 3 Quantum Riddler, 3 Fatal Push and 2 Wrath of
        # the Skies. The real deck is midrange with spot removal and a
        # sweeper; the approximation was pure beatdown with neither.
        #
        # Measured before adopting (n=6 Bo3, 24 opponents, same tree):
        # the real list scores HIGHER, 84.7% vs 79.2%. It is adopted for
        # fidelity, not to move a number — the flat WR goes UP while the
        # weighted contribution goes DOWN with the share correction. The
        # decklist-is-the-cause hypothesis is falsified in
        # docs/diagnostics/2026-08-30_zoo_decklist_hypothesis_falsified.md.
        #
        # Side effect worth recording: the dropped 4 Doorkeeper Thrull
        # were a 4-of NO-OP. Its Torpor Orb clause writes
        # `game._etb_suppressed` and nothing in engine/ or ai/ ever reads
        # that flag. The class (Torpor Orb, Hushbringer, Hushwing Gryff,
        # Tocatli Honor Guard, Doorkeeper Thrull) is 5 cards, below the
        # ~10-card build threshold, and after this change no registered
        # deck plays any of them — so it is recorded here rather than
        # built.
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91253 deck=893050,
        # bucket "4/5c Aggro" 3.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Arid Mesa": 4,
            "Blood Crypt": 1,
            "Breeding Pool": 1,
            "Fatal Push": 4,
            "Flooded Strand": 4,
            "Godless Shrine": 1,
            "Hallowed Fountain": 1,
            "Indatha Triome": 1,
            "Leyline Binding": 4,
            "Leyline of the Guildpact": 4,
            "Meticulous Archive": 1,
            "Plains": 1,
            "Practiced Offense": 2,
            "Psychic Frog": 4,
            "Quantum Riddler": 4,
            "Ragavan, Nimble Pilferer": 4,
            "Scion of Draco": 4,
            "Steam Vents": 1,
            "Stubborn Denial": 4,
            "Temple Garden": 1,
            "Territorial Kavu": 4,
            "Thundering Falls": 1,
            "Wooded Foothills": 4,
        },
        "sideboard": {
            "Consign to Memory": 4,
            "Containment Priest": 2,
            "Doorkeeper Thrull": 2,
            "Mystical Dispute": 1,
            "Obsidian Charmaw": 1,
            "Spell Snare": 2,
            "Wrath of the Skies": 3,
        },
    },
    "Living End": {
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91250 deck=893015,
        # bucket "Living End" 3.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Breeding Pool": 1,
            "Colossal Skyturtle": 1,
            "Commercial District": 1,
            "Endurance": 4,
            "Force of Negation": 4,
            "Forest": 1,
            "Generous Ent": 4,
            "Hedge Maze": 1,
            "Island": 1,
            "Living End": 3,
            "Misty Rainforest": 4,
            "Oliphaunt": 3,
            "Sacred Foundry": 1,
            "Shardless Agent": 4,
            "Sink into Stupor // Soporific Springs": 1,
            "Steam Vents": 1,
            "Stomping Ground": 1,
            "Street Wraith": 4,
            "Striped Riverwinder": 3,
            "Subtlety": 4,
            "Temple Garden": 1,
            "Thundering Falls": 1,
            "Valakut Awakening // Valakut Stoneforge": 1,
            "Violent Outburst": 4,
            "Waker of Waves": 2,
            "Wistfulness": 4,
        },
        "sideboard": {
            "Brotherhood's End": 1,
            "Clarion Conqueror": 4,
            "Force of Vigor": 2,
            "Inevitable Betrayal": 2,
            "Mystical Dispute": 4,
            "Teferi, Time Raveler": 2,
        },
    },
    "Izzet Prowess": {
        # Aug 2026 — the "UR Cutter Prowess" MTGO Challenge-winning list,
        # supplied verbatim by the project owner. Plan, in the pilot's own
        # words: close out games quickly through a combination of creature
        # damage and direct burn spells.
        #
        # Replaces the June-2026 archetype-page approximation. The two
        # structural corrections are worth naming, because both change how
        # the deck must be modelled rather than just its counts:
        #   * NO green splash. Stomping Ground and the 3 SB Pick Your Poison
        #     are gone, so this is straight UR and its mana solver no longer
        #     has to find G. Independently corroborated by the mtgtop8 list
        #     this repo's own fetch_tier1_decklists.py pulled on 2026-08-08.
        #   * The pump suite is wider and cheaper: Expressive Iteration 4→2
        #     and Mutagenic Growth 4→3 pay for 2 Monstrous Rage and 2 Violent
        #     Urge, both of which are combat tricks rather than card
        #     advantage — the list leans harder on connecting than on
        #     refuelling.
        # Unholy Heat moves mostly to the board (MB 2→1, SB 0→3), the
        # standard concession to matchups where a 6-damage delirium mode is
        # dead. Murktide Regent and Spell Snare are out entirely.
        #
        # Registering this list is what exposed the Phyrexian-mana gap, and
        # the note is kept because it explains a WR discontinuity in this
        # deck's history: with Stomping Ground gone there are ZERO green
        # sources, and `CastManager.can_cast` used to refuse {G/P} without a
        # green source, making the 3 Mutagenic Growth dead cards. The old
        # approximation hid it by running a green source. FIXED — coloured
        # pips payable with life (CR 107.4f) are no longer treated as
        # colour requirements, so numbers from before that fix understate
        # this deck.
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91251 deck=893024,
        # bucket "UR Aggro" 12.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Arid Mesa": 3,
            "Assault Strobe": 1,
            "Bloodstained Mire": 3,
            "Cori-Steel Cutter": 4,
            "Dragon's Rage Channeler": 4,
            "Expressive Iteration": 3,
            "Fiery Islet": 1,
            "Lava Dart": 4,
            "Lightning Bolt": 4,
            "Mishra's Bauble": 4,
            "Monastery Swiftspear": 4,
            "Mountain": 3,
            "Mutagenic Growth": 4,
            "Preordain": 4,
            "Scalding Tarn": 3,
            "Slickshot Show-Off": 4,
            "Steam Vents": 3,
            "Thundering Falls": 1,
            "Unholy Heat": 1,
            "Violent Urge": 1,
            "Wooded Foothills": 1,
        },
        "sideboard": {
            "Consign to Memory": 3,
            "Into the Flood Maw": 1,
            "Meltdown": 1,
            "Rough // Tumble": 1,
            "Spell Pierce": 2,
            "Spell Snare": 2,
            "Tormod's Crypt": 2,
            "Unholy Heat": 3,
        },
    },
    "Dimir Midrange": {
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91253 deck=893048,
        # bucket "Dimir Control" 4.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Bloodstained Mire": 1,
            "Consider": 2,
            "Counterspell": 4,
            "Darkslick Shores": 1,
            "Drown in the Loch": 2,
            "Fatal Push": 4,
            "Flooded Strand": 1,
            "Force of Negation": 3,
            "Gloomlake Verge": 1,
            "Island": 3,
            "Kaito, Bane of Nightmares": 2,
            "Marsh Flats": 1,
            "Murktide Regent": 3,
            "Otawara, Soaring City": 1,
            "Polluted Delta": 4,
            "Psychic Frog": 4,
            "Scalding Tarn": 1,
            "Sheoldred's Edict": 1,
            "Sink into Stupor // Soporific Springs": 2,
            "Snapcaster Mage": 2,
            "Spell Snare": 2,
            "Subtlety": 2,
            "Swamp": 1,
            "Tamiyo, Inquisitive Student // Tamiyo, Seasoned Scholar": 3,
            "Thoughtseize": 4,
            "Undercity Sewers": 2,
            "Watery Grave": 3,
        },
        "sideboard": {
            "Consign to Memory": 4,
            "Engineered Explosives": 2,
            "Harbinger of the Seas": 2,
            "Mystical Dispute": 2,
            "Nihil Spellbomb": 2,
            "Requiting Hex": 2,
            "Toxic Deluge": 1,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91252 deck=893042,
        # bucket "UW Control" 3.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Arid Mesa": 3,
            "Consult the Star Charts": 2,
            "Day's Undoing": 3,
            "Flooded Strand": 4,
            "Force of Negation": 1,
            "Geier Reach Sanitarium": 1,
            "Godless Shrine": 1,
            "Hallowed Fountain": 2,
            "Island": 2,
            "Isochron Scepter": 1,
            "Lórien Revealed": 1,
            "March of Otherworldly Light": 1,
            "Meticulous Archive": 2,
            "Monumental Henge": 1,
            "Mystic Gate": 1,
            "Narset, Parter of Veils": 4,
            "Orim's Chant": 4,
            "Otawara, Soaring City": 1,
            "Plains": 2,
            "Polluted Delta": 1,
            "Prismatic Ending": 2,
            "Silence": 3,
            "Sink into Stupor // Soporific Springs": 2,
            "Solitude": 4,
            "Spell Snare": 2,
            "Supreme Verdict": 2,
            "Teferi, Time Raveler": 4,
            "Thundering Falls": 1,
            "Wrath of the Skies": 2,
        },
        "sideboard": {
            "Beza, the Bounding Spring": 1,
            "Consign to Memory": 4,
            "High Noon": 3,
            "Kaheera, the Orphanguard": 1,
            "Mystical Dispute": 3,
            "Spell Snare": 1,
            "Surgical Extraction": 1,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91252 deck=893041,
        # bucket "Instant Reanimator" 6.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Atraxa, Grand Unifier": 4,
            "Breeding Pool": 1,
            "Consign to Memory": 1,
            "Ephemerate": 4,
            "Faithful Mending": 3,
            "Fallaji Archaeologist": 2,
            "Flooded Strand": 4,
            "Force of Negation": 2,
            "Godless Shrine": 1,
            "Goryo's Vengeance": 4,
            "Griselbrand": 1,
            "Hallowed Fountain": 1,
            "Island": 1,
            "Marsh Flats": 4,
            "Meticulous Archive": 1,
            "Plains": 1,
            "Polluted Delta": 4,
            "Prismatic Ending": 2,
            "Psychic Frog": 4,
            "Quantum Riddler": 2,
            "Shadowy Backstreet": 1,
            "Solitude": 4,
            "Swamp": 1,
            "Teferi, Time Raveler": 1,
            "Thoughtseize": 3,
            "Undercity Sewers": 1,
            "Watery Grave": 1,
            "Wrath of the Skies": 1,
        },
        "sideboard": {
            "Ashiok, Dream Render": 1,
            "Clarion Conqueror": 2,
            "Consign to Memory": 3,
            "Fatal Push": 2,
            "Mystical Dispute": 2,
            "Nihil Spellbomb": 1,
            "Teferi, Time Raveler": 1,
            "Thoughtseize": 1,
            "Wrath of the Skies": 2,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91252 deck=893033,
        # bucket "Boros Ponza" 3.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Avengers Disassembled": 3,
            "Castle Ardenvale": 1,
            "Cleansing Wildfire": 4,
            "Cori Mountain Monastery": 4,
            "Demolition Field": 4,
            "Erode": 4,
            "Field of Ruin": 4,
            "Galvanic Discharge": 3,
            "High Noon": 1,
            "Mountain": 2,
            "Path to Exile": 4,
            "Plains": 4,
            "Price of Freedom": 4,
            "Relic of Progenitus": 2,
            "Sacred Foundry": 3,
            "Solitude": 4,
            "Sunken Citadel": 3,
            "The Legend of Roku // Avatar Roku": 1,
            "The Stone Brain": 1,
            "Wrath of the Skies": 4,
        },
        "sideboard": {
            "Avengers Disassembled": 1,
            "Chandra, Awakened Inferno": 1,
            "Cursed Totem": 1,
            "High Noon": 3,
            "Kaheera, the Orphanguard": 1,
            "Rest in Peace": 2,
            "Rule of Law": 1,
            "Surgical Extraction": 2,
            "The Stone Brain": 1,
            "Wear // Tear": 1,
            "Wrath of God": 1,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91252 deck=893036,
        # bucket "Eldrazi Ramp" 3.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Bojuka Bog": 1,
            "Devourer of Destiny": 3,
            "Eldrazi Temple": 4,
            "Emrakul, the Promised End": 3,
            "Fatal Push": 3,
            "Forest": 4,
            "Ghost Quarter": 1,
            "Icetill Explorer": 2,
            "Karn, the Great Creator": 3,
            "Kozilek's Command": 4,
            "Malevolent Rumble": 4,
            "Misty Rainforest": 1,
            "Overgrown Tomb": 2,
            "Sanctum of Ugin": 1,
            "Sire of Seven Deaths": 2,
            "Sowing Mycospawn": 4,
            "Talisman of Resilience": 4,
            "Ugin's Labyrinth": 4,
            "Ugin, Eye of the Storms": 2,
            "Underground Mortuary": 1,
            "Utopia Sprawl": 4,
            "Verdant Catacombs": 1,
            "Windswept Heath": 1,
            "Wooded Foothills": 1,
        },
        "sideboard": {
            "Chalice of the Void": 1,
            "Cursed Totem": 1,
            "Grafdigger's Cage": 1,
            "Liquimetal Coating": 1,
            "Sheoldred's Edict": 2,
            "Thoughtseize": 3,
            "Tormod's Crypt": 1,
            "Toxic Deluge": 2,
            "Trinisphere": 3,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91251 deck=893025,
        # bucket "Broodscale Bloodchief" 19.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Ancient Stirrings": 4,
            "Basking Broodscale": 4,
            "Blade of the Bloodchief": 3,
            "Boseiju, Who Endures": 1,
            "Devourer of Destiny": 3,
            "Dismember": 2,
            "Eldrazi Temple": 4,
            "Emrakul, the Promised End": 4,
            "Forest": 7,
            "Gemstone Caverns": 1,
            "Glaring Fleshraker": 1,
            "Haywire Mite": 1,
            "Kozilek's Command": 4,
            "Malevolent Rumble": 4,
            "Shifting Woodland": 1,
            "Soul-Guide Lantern": 1,
            "Sowing Mycospawn": 4,
            "Springleaf Drum": 1,
            "Ugin's Labyrinth": 4,
            "Urza's Saga": 4,
            "Vexing Bauble": 1,
            "Yavimaya, Cradle of Growth": 1,
        },
        "sideboard": {
            "Dismember": 1,
            "Grafdigger's Cage": 2,
            "Nature's Claim": 2,
            "Pithing Needle": 1,
            "Sire of Seven Deaths": 2,
            "Thought-Knot Seer": 3,
            "Trinisphere": 3,
            "Vexing Bauble": 1,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91251 deck=893028,
        # bucket "Creatures Toolbox" 6.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Badgermole Cub": 4,
            "Beastrider Vanguard": 1,
            "Birds of Paradise": 2,
            "Boseiju, Who Endures": 2,
            "Craterhoof Behemoth": 1,
            "Delighted Halfling": 4,
            "Devoted Druid": 4,
            "Dryad Arbor": 2,
            "Fiend Artisan": 2,
            "Forest": 2,
            "Formidable Speaker": 1,
            "Green Sun's Zenith": 4,
            "Horizon Canopy": 1,
            "Leyline of Abundance": 4,
            "Nature's Rhythm": 4,
            "Nurturing Peatland": 1,
            "Overgrown Tomb": 1,
            "Shang-Chi, Master of Kung Fu": 1,
            "Temple Garden": 2,
            "Thoughtseize": 1,
            "Tyvar, Jubilant Brawler": 4,
            "Tyvar, the Pummeler": 1,
            "Underground Mortuary": 1,
            "Verdant Catacombs": 4,
            "Vizier of Remedies": 1,
            "Windswept Heath": 3,
            "Wooded Foothills": 2,
        },
        "sideboard": {
            "Chalice of the Void": 2,
            "Doorkeeper Thrull": 1,
            "Endurance": 1,
            "Fatal Push": 1,
            "Force of Vigor": 1,
            "Grist, the Hunger Tide": 1,
            "Guerrilla Gorilla": 1,
            "Icetill Explorer": 1,
            "Keen-Eyed Curator": 1,
            "Kraul Harpooner": 1,
            "Prismatic Ending": 2,
            "Thoughtseize": 1,
            "Vexing Bauble": 1,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91253 deck=893049,
        # bucket "Blink" 7.0%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Arid Mesa": 4,
            "Ephemerate": 3,
            "Fatal Push": 4,
            "Godless Shrine": 2,
            "Guide of Souls": 4,
            "Haliya, Guided by Light": 2,
            "Hallowed Fountain": 2,
            "Marsh Flats": 4,
            "Meticulous Archive": 1,
            "Ocelot Pride": 4,
            "Phelia, Exuberant Shepherd": 3,
            "Plains": 3,
            "Quantum Riddler": 4,
            "Shadowy Backstreet": 1,
            "Solitude": 4,
            "Springleaf Drum": 3,
            "Starfield Shepherd": 3,
            "Teferi, Time Raveler": 2,
            "Thoughtseize": 4,
            "Witch Enchanter // Witch-Blessed Meadow": 3,
        },
        "sideboard": {
            "Clarion Conqueror": 3,
            "Consign to Memory": 4,
            "Rest in Peace": 2,
            "Sanctifier en-Vec": 2,
            "Spider-Sense": 1,
            "White Orchid Phantom": 2,
            "Wrath of the Skies": 1,
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
        # Sep 2026 refresh (base: mtgtop8.com Modern event=91283 deck=893233,
        # bucket "Hollow One" 0.8%, retrieved 2026-09-27 via
        # tools/fetch_tier1_decklists.py; data/tier1_decklists/2026-09-27/).
        # Earlier provenance notes above describe the list this replaced.
        "mainboard": {
            "Blazing Rootwalla": 4,
            "Bloodstained Mire": 4,
            "Burning Inquiry": 4,
            "Detective's Phoenix": 4,
            "Elegant Parlor": 1,
            "Enter the Avatar State": 2,
            "Faithless Looting": 4,
            "Hardened Academic": 4,
            "Hollow One": 4,
            "Lorehold Charm": 1,
            "Marauding Mako": 4,
            "Mountain": 5,
            "Oliphaunt": 1,
            "Ox of Agonas": 1,
            "Practiced Offense": 2,
            "Sacred Foundry": 3,
            "Street Wraith": 4,
            "Vengevine": 4,
            "Wooded Foothills": 4,
        },
        "sideboard": {
            "Curse of Shaken Faith": 2,
            "Faerie Macabre": 2,
            "Fire Magic": 1,
            "Prismatic Ending": 2,
            "Rough // Tumble": 2,
            "Thraben Charm": 2,
            "Vexing Bauble": 2,
            "Wear // Tear": 2,
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
