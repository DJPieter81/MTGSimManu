---
title: Izzet "Cutter" Prowess — list fidelity + per-card effect-coverage audit; Equipment never entered play unattached, so no equip was ever enumerated
status: active
priority: primary
session: 2026-08-30
supersedes: []
superseded_by: []
depends_on: []
tags: [izzet-prowess, cori-steel-cutter, equipment, coverage-audit, effect-registry, activation, oracle-parser, plot, phyrexian-mana, egress-blocked]
summary: >
  Card-by-card coverage audit of the registered "Izzet Prowess" (UR Cutter
  Prowess) deck: all 26 distinct cards are in the DB, none is a missing-card
  hole. One mechanic-class bug was found and FIXED: CR 301.5c ("an Equipment
  enters the battlefield unattached") was implemented as a single card-name
  EFFECT_REGISTRY entry, so every other Equipment in the format resolved
  without the `equipment_unattached` tag and `ai/ev_player.py::_consider_equip`
  could never enumerate an equip play for it. Four further gaps are real but
  land in files owned by other concurrent agents and are written up here
  instead of patched: Cori-Steel Cutter's Flurry trigger fires on the wrong
  condition (matched off the token's prowess REMINDER text), Slickshot
  Show-Off's plot is entirely unimplemented, Mishra's Bauble's activated
  ability is refused by `can_activate` as UNCLASSIFIED so 4 maindeck slots are
  permanently inert, and Pick Your Poison has an invented fourth mode.
  Tournament-list verification was EGRESS-BLOCKED — every decklist host is
  refused by the session proxy.
---

# Izzet "Cutter" Prowess — list fidelity + card-effect coverage audit

## Part 1 — list fidelity: verification was egress-blocked

**No tournament decklist source could be reached.** Every host tried returned
`EGRESS_BLOCKED` from the session proxy:

| Host | Result |
|---|---|
| `decklistdata.com` | EGRESS_BLOCKED |
| `www.mtggoldfish.com` | EGRESS_BLOCKED |
| `magic.wizards.com` | EGRESS_BLOCKED |
| `mtgdecks.net` | EGRESS_BLOCKED |
| `www.mtgtop8.com` | EGRESS_BLOCKED |
| `aetherhub.com` | EGRESS_BLOCKED |
| `www.moxgate.com` | EGRESS_BLOCKED |

This is the same class of block already recorded for `mtgjson.com` in
CLAUDE.md. **I could not identify "the MTGO Challenge 16" the request
referred to** — that phrase is ambiguous (a 16-player Challenge, Challenge
#16, or a date) and nothing I could reach resolves it. Web *search* snippets
mentioned an Izzet Prowess Top-16 (LucasG1ggs, MTGO Modern Challenge,
2026-05-23) and an "Izzet Steel-Cutter" 4th place at Modern Challenge 32
(2026-04-05), but the snippets never carried a decklist and the pages are
blocked. **No decklist below is quoted from a tournament page fetched this
session.**

### What IS sourced: the repo's own fetched lists

`data/tier1_decklists/2026-08-08/` holds two "UR Aggro" lists pulled from
mtgtop8 by this repo's own fetcher (`tools/fetch_tier1_decklists.py`, run via
GitHub Actions egress, which is not subject to this session's proxy). They
carry mtgtop8 event/deck IDs but **no event name, date, or placing** — and
mtgtop8 is blocked, so those IDs cannot be resolved to "Challenge, date,
1st place" from here.

Diff of the registered list vs `ur_aggro__89331_877945.txt` (the closer of
the two):

| Slot | Registered | mtgtop8 89331 | Δ |
|---|---|---|---|
| Mountain | 2 | 3 | +1 |
| Steam Vents | 2 | 3 | +1 |
| Thundering Falls | 2 | 1 | −1 |
| Stomping Ground | 1 | 0 | −1 |
| Unholy Heat (MB) | 2 | 1 | −1 |
| Violent Urge | 0 | 1 | +1 |

Everything else in the 60 is identical (4 DRC / 4 Swiftspear / 4 Slickshot /
4 Bolt / 4 Lava Dart / 4 Mutagenic Growth / 4 Bauble / 4 Expressive Iteration
/ 4 Preordain / 4 Cori-Steel Cutter, same fetch package otherwise). The
structural difference is that **89331 is straight UR while the registered list
splashes green** (1 Stomping Ground maindeck to support 3 Pick Your Poison in
the board).

Sideboard diff (registered → 89331): −3 Pick Your Poison, −2 Murktide Regent,
−2 Surgical Extraction, Spell Pierce 2→1, Spell Snare 1→2, +1 End the
Festivities, +1 Mystical Dispute, +2 Tormod's Crypt, +3 Unholy Heat.

The second fetched list (`ur_aggro__89319_877798.txt`) disagrees with the
first substantially — 2 Assault Strobe, 2 Monstrous Rage, only 3 Expressive
Iteration, no maindeck Unholy Heat, 3 Mutagenic Growth — so there is no single
"the" list even inside the sourced cohort.

**`decks/modern_meta.py` was NOT changed.** Three reasons: (1) the requested
event could not be identified, so no list can be called "the winner"; (2) the
two sourced lists conflict, so choosing one is arbitrary; (3) swapping a
registered decklist moves every WR number in the matrix and cannot be
validated without a sweep, which is barred while three agents are running.
The recommended follow-up is to re-run `tools/fetch_tier1_decklists.py` from
CI egress with event metadata captured, then decide the swap with a WR
re-baseline in the same PR.

## Part 2 — per-card effect coverage

All 26 distinct cards (21 mainboard, 7 sideboard, 2 shared) resolve in
`CardDatabase().get_card()`. **There is no missing-card hole in this deck.**

Legend: **Parsed** = abilities landed in typed `CardTemplate` fields rather
than an unclassified bucket. **Executes** = the effect actually changes game
state. **AI uses** = observed in play or reachable from the AI's enumeration.

### Mainboard

| Card | Parsed | Executes | AI uses | Verdict |
|---|---|---|---|---|
| Dragon's Rage Channeler | `has_surveil`, `has_delirium`, `power_scales_with=delirium`, `has_noncreature_spell_cast_trigger` | yes — surveil fires per noncreature spell, delirium scales P/T and grants flying | yes | **OK** (see delirium caveat below) |
| Monastery Swiftspear | `Keyword.PROWESS`, `HASTE` | yes — `cast_manager.py:1545` +1/+1 | yes | **OK** |
| Slickshot Show-Off | flying/haste + `has_pump_grant`; **plot NOT parsed** | +2/+0 pump fires correctly (the `+N/+0` regex branch); **plot never castable** | hard-cast only | **PARTIAL** — the free-cast half of the card does not exist |
| Lightning Bolt | registry `lightning_bolt_resolve` | yes | yes | **OK** |
| Lava Dart | registry `lava_dart_resolve`; flashback-sacrifice handled explicitly in `cast_manager.py:275,1276` | yes, incl. GY recast for "Sacrifice a Mountain" | yes | **OK** |
| Unholy Heat | registry; `has_delirium`, `power_scales_with=delirium` | yes — 2 or 6 damage | yes | **OK** |
| Mutagenic Growth | registry; `phyrexian_pip_count=1` | yes — **the engine CAN pay Phyrexian mana with life** (`cast_manager.py:1240-1255`, 2 life/pip, gated on `player.life > 2*pips`) | yes | **OK** — this was the biggest suspected hole and it is not one |
| Mishra's Bauble | activated ability parsed but `effect_kind=UNCLASSIFIED` | **NO** — `can_activate` rule 9b refuses every UNCLASSIFIED kind; also no delayed-upkeep-draw machinery exists at all | cast (so it counts for prowess/storm) but **never sacrificed** | **INERT** — 4 slots that cast and then sit on the battlefield forever |
| Expressive Iteration | registry `expressive_iteration_resolve` | yes | yes | **OK** |
| Preordain | `has_scry`, `has_draw_effect` | yes — observed `scry 2 → draw 1` | yes | **OK** |
| Cori-Steel Cutter | `equip_cost=1`, `has_token_effect`, `cast_trigger_token={'noncreature'}` | Monk tokens ARE created — but off the **wrong trigger** (see below). Equip now enumerable after this session's fix | tokens yes; equip enumerated but currently outbid | **PARTIAL** |
| Fetchlands (Scalding Tarn / Wooded Foothills / Arid Mesa / Bloodstained Mire) | `is_tutor`; generic activated ability is UNCLASSIFIED and refused | yes — fetching runs through `LandManager`, not `can_activate`; observed `Crack Scalding Tarn (pay 1 life) -> Steam Vents` | yes | **OK** |
| Steam Vents / Stomping Ground | `untap_life_cost=2` | yes (shock-or-tapped choice) | yes | **OK** |
| Fiery Islet | ability[0] mana (UNCLASSIFIED, but mana abilities bypass `can_activate`), ability[1] `DRAW_N` → `can_activate=True` | yes, both halves | yes | **OK** |
| Thundering Falls | `enters_tapped=True`, `has_surveil`, `has_self_trigger` | yes | yes | **OK** |
| Mountain | basic | yes | yes | **OK** |

### Sideboard

| Card | Parsed | Executes | AI uses | Verdict |
|---|---|---|---|---|
| Consign to Memory | `is_counterspell=False`, `counter_target_kind=''` — the generic counter parser does not claim it; a registry handler covers it | yes — counters a triggered ability or a colorless spell | reachable via the registry handler; **replicate {1} is dropped** | **PARTIAL** (documented simplification) |
| Pick Your Poison | `is_modal=False`, `modes=[]`, **`tags=set()`** — no `removal`/`interaction` tag at all | registry handler picks artifact/enchantment, else a flier — **but its `else` branch invents a fourth mode: "opponent loses 1 life"**, which the real card does not have | scoring sees an untagged sorcery | **BUG** — fabricated mode; should fizzle |
| Murktide Regent | registry ETB (delve + counters) | yes | yes | **OK** |
| Spell Pierce | `is_counterspell`, `counter_target_kind=noncreature_spell`, `counter_tax_amount=2` | yes | yes | **OK** |
| Surgical Extraction | `phyrexian_pip_count=1`, `graveyard_hate` tag | Phyrexian payment works; graveyard-hate resolution is generic | yes | **OK** |
| Meltdown | registry `meltdown_resolve`, `x_cost_data` | yes | yes | **OK** |
| Spell Snare | `is_counterspell`, `counter_target_kind=spell` | counters, but **the "mana value 2" restriction is not in the typed field** — `counter_target_kind` is bare `spell` | yes | **PARTIAL** — strictly stronger than the real card |

### Delirium caveat (interaction between two gaps)

DRC's delirium wants four card types in the graveyard. This deck's natural
artifact source is Mishra's Bauble — which, because its ability is refused,
**never reaches the graveyard**. It sits on the battlefield for the whole
game (observed across three verbose games: `Other: Mishra's Bauble` from
turn 1 to the end). So the Bauble bug does not just cost 4 cantrips; it
also removes the deck's most reliable path to the artifact card type, which
is what turns DRC into a 3/3 flier and Unholy Heat into a 6-damage removal
spell. These are the two cards the archetype is built around.

## Part 3 — the fix that landed

### CR 301.5c — an Equipment enters the battlefield unattached

**Before:** the `equipment_unattached` instance tag — the *sole* gate on
`ai/ev_player.py::_consider_equip` enumerating an equip play, and on
`ai/permanent_threat.py` valuing a stranded equipment — was written by exactly
one place in the codebase: a card-name-keyed `EFFECT_REGISTRY.register(
"Cranial Plating", EffectTiming.ETB)` handler whose entire body was
`card.instance_tags.add("equipment_unattached")` plus a log line.

Every other Equipment in the format therefore resolved onto the battlefield
without the tag and **could never be equipped for the rest of the game**. This
is the "registered but inert" shape exactly: the engine's `equip_creature` is
correct and complete, `game_runner.py:1258` dispatches the `equip` action
correctly, `_consider_equip` is called on every main phase — and the play was
simply never in the candidate list.

Note the asymmetry that made this survive: the *mirror* half of the mechanic
(re-marking an equipment unattached when its bearer leaves the battlefield)
was already generic, in `engine/permanent_effects.py:477`. Only the "enters"
half was card-name gated.

**After:** the rule lives in `engine/triggers.py::TriggerManager.trigger_etb`,
keyed off the typed `CardTemplate.equip_cost` field (parsed once at DB load)
plus the Equipment subtype, guarded so an Equipment that attaches itself on
entry keeps its attachment. The Cranial Plating registry entry was deleted and
`tools/card_name_registry_baseline.json` lowered 96 → 95 in the same commit.

Contract check: class size = every Equipment printing in Modern (hundreds);
owning subsystem = the ETB trigger fan-out, one module; failing test first,
rule-phrased (`tests/test_equipment_enters_unattached.py`, four cases, all red
before the fix and green after, selecting Equipment *from the DB by mechanic*
so it covers whatever the pool contains); no card name appears in the rule or
in the assertions.

**Measured effect in play.** Instrumented run, `Izzet Prowess` vs
`Dimir Midrange`, seed 50500: `_consider_equip` called 50 times, equipment on
battlefield in 6 of them, all 6 correctly tagged, and **4 equip plays
enumerated** (`Equip Cori-Steel Cutter to Monk Token`, EV 2.1–2.7). Before the
fix that count was structurally 0. The equip play is still outbid by casts in
the planner — that is an AI *valuation* question in `ai/ev_player.py`
(`_estimate_equip_bonus`), not a missing mechanic, and it is on the
forbidden-file list this session.

## Part 4 — gaps blocked by the forbidden-file list

These are precise enough to sequence directly once the concurrent agents land.

### B1 — Cori-Steel Cutter's Flurry fires on the wrong condition

* **File / function:** `engine/oracle_parser.py::parse_cast_trigger_token`
  (line ~2331), dispatched by
  `engine/oracle_resolver.py::resolve_spell_cast_trigger` (line ~1570).
* **What is wrong:** the parser's regex is
  `cast (?:a|an|your first|another) ([a-z /]*?)spell`. Cutter's actual trigger
  is *"Flurry — Whenever you cast your **second** spell each turn, create a
  1/1 white Monk creature token with prowess"*, which that regex does not
  match. What it matches instead is the **token's prowess reminder text** at
  the end of the oracle — *"(Whenever you cast a noncreature spell, the token
  gets +1/+1 until end of turn.)"* — yielding
  `{'spell_types': frozenset({'noncreature'}), 'count': 1}`. The right answer
  is produced from the wrong sentence, and two behaviours are wrong as a
  result:
  1. **Over-triggers.** Flurry fires once per turn, on the second spell. The
     current code fires on *every* noncreature spell. Observed: three Monk
     tokens created in a single turn (seed 50500, T9).
  2. **Under-triggers.** Flurry counts *all* spells, creature spells included.
     The current code fires only on noncreature spells, so a turn of
     Swiftspear + Slickshot makes no token.
* **Fix shape:** a new typed field for the "Nth spell each turn" trigger
  ordinal — e.g. `nth_spell_cast_trigger = {'ordinal': 2, ...}` parsed once at
  DB load — dispatched against the already-tracked
  `player.spells_cast_this_turn`, with an equality (not `>=`) test so it fires
  exactly once. Class: every Flurry card plus every "whenever you cast your
  second/third spell" printing. Touches `oracle_parser.py`, `cards.py`,
  `card_database.py` (all forbidden), then `oracle_resolver.py` (allowed).
* **Also missing:** *"You may attach this Equipment to it."* The token is
  created and never attached, so Cutter's `+1/+1`, trample and **haste** are
  dead on the turn the token arrives — which is the entire point of the card.
  This belongs in the same dispatch branch once the trigger is correct.

### B2 — Mishra's Bauble is permanently inert (4 maindeck slots)

* **Files / functions:** classification in
  `engine/oracle_parser.py` (`ActivationEffectKind` assignment, ~line 1094
  falls through to `K.UNCLASSIFIED`); refusal in
  `engine/activation.py::ActivationManager.can_activate` rule **9b** (~line 94)
  — an explicit allowlist of eight kinds, anything else returns `False`
  *before* any cost is charged; the enum itself in `engine/cards.py` (~line
  162). All three are forbidden this session.
* **Confirmed empirically:** `can_activate(...) == False` for
  `Mishra's Bauble ability[0] kind=UNCLASSIFIED`, and across three verbose
  games the Bauble is cast on turn 1 and still on the battlefield at the end.
* **Two mechanics are missing, not one:**
  1. A classified kind for the "look at a library's top card" + sacrifice
     shape, so `can_activate` stops refusing it.
  2. **Delayed one-shot triggers keyed to a future upkeep** — grep for
     `next turn's upkeep` / `delayed` across `engine/` returns only an
     end-of-turn exile rider (`game_state.py:134`) and Ragavan's
     "may cast this turn" (`turn_manager.py:185`). There is no
     "at the beginning of the next turn's upkeep, do X" queue. Class size is
     large (Bauble, Street Wraith-likes, every "draw at the beginning of the
     next end step" rider), so this is a real mechanic, not a patch.
* **Blast radius beyond the cantrip:** see the delirium caveat above.

### B3 — Plot is entirely unimplemented

* `grep -ri plot engine/ ai/ --include=*.py` returns **zero** hits. Slickshot
  Show-Off's `Plot {1}{R}` alternative cost does not exist, so the card is
  only ever hard-cast as a 1/2 flier. Fix shape mirrors the existing
  `evoke_cost` / `dash_cost` / `spectacle_cost` typed-field pattern in
  `engine/cards.py` (forbidden) plus a cast route in `cast_manager.py`.
  Class: every Plot card (Outlaws of Thunder Junction onward).

### B4 — Spell Snare loses its mana-value restriction

* `counter_target_kind` parses to bare `spell` with no MV bound, so the
  engine's Spell Snare counters anything. That is strictly stronger than the
  real card and inflates the sideboard. Fix: an MV-bound field alongside
  `counter_target_kind` in `engine/oracle_parser.py` / `engine/cards.py`
  (both forbidden). Class: Spell Snare, Mystical Dispute's tax variant,
  Dovin's Veto shapes, anything with "with mana value N".

### B5 — Pick Your Poison invents a fourth mode (fixable, low risk, deferred)

* `engine/card_effects.py::pick_your_poison_resolve` (line ~1372) ends with an
  `else` branch that drains 1 life, labelled `# Fallback: opponent loses 1
  life (Toxic 1 mode)`. The card has three modes and none of them is that;
  with no legal sacrifice the spell should simply do nothing. `card_effects.py`
  is editable this session, but the change is behavioural on a live sideboard
  card and was left out of this commit so the diff stays one mechanic wide.
* Related: Pick Your Poison's `tags` set is **empty** — no `removal`, no
  `interaction` — which is a tag-derivation gap in `engine/oracle_parser.py`
  and likely depresses its score wherever tags drive valuation.

## Verification run this session

Six ratchets, all green after the change:

```
check_abstraction.py            OK
check_magic_numbers.py          OK — total = 13 (baseline 13)
check_zone_mutation.py          OK — total = 82 (baseline 82)
check_doc_hygiene.py            OK
check_oracle_runtime_parse.py   OK — total = 0 (baseline 0)
check_card_name_registry.py     OK — total = 95 (baseline lowered 96 → 95)
```

Targeted tests (the full suite was NOT run — three agents were running
concurrently and games are wall-clock sensitive):

```
tests/test_equipment_enters_unattached.py          4 passed  (red before the fix)
tests/test_blocking_equipment_aware.py             passed
tests/test_chump_block_plating_when_lethal_range.py passed
tests/test_card_name_registry_ratchet.py           passed
tests/test_abstraction_contract.py                 passed
tests/test_no_silent_unhandled_effects.py          passed
pytest -k "equip or plating or affinity or artifact_land or nettlecyst"  exit 0
```

Plus four verbose single games (seeds 50000 / 50500 / 51000, one instrumented
re-run at 50500) for the in-play evidence quoted above.
