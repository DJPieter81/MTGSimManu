---
title: "Clause and trigger grammar: one typed effect model, parsed once at load"
status: active
priority: primary
session: 2026-09-29
depends_on: [docs/design/2026-09-28_temporal_state_model.md, docs/design/rules-foundation-sweep-tracker.md]
generated: [docs/design/effect_grammar_census.md]
tags: [engine, oracle, grammar, effects, triggers, architecture]
summary: >
  One grammar (parse_effects) turns oracle text into typed, frozen EffectSpecs
  on CardTemplate.effects at load, and one dispatcher (engine/effect_resolver)
  executes them through the owners that already exist; TriggerSpec and the
  event bus follow later. E0 ships schema, grammar, views, census, tooling and
  a caller-less dispatcher skeleton and changes no resolution behaviour
  (seeded digest, neutrality tests, pool-wide unchanged-output tests).
  Families then switch per host with legacy fallback (E1-E7). This version
  carries the round-1 fixes F1-F11, grafts G1-G17, the round-2 amendments
  A1-A43 (keyword/alternative-cost hosts, located target parse, typed residue
  with polarity, frozen CostSnapshot, sub-abilities, resolution-choice
  callbacks, per-host switching, the per-host harness and Bo3 digest, the
  pool-CPU load budget) and the modifications M1-M9.
---

# Clause and trigger grammar: one typed effect model, parsed once at load (E0 specification, amended after round-2 review)

## 0. Scope, decision, and what E0 changes

**The approved plan.** The user asked to "generalize significantly", then chose "both, clause first". The work has two parts:
- **Clause grammar first (E0 to E7).** One grammar, `parse_effects`, turns oracle text into typed `EffectSpec`s held on `CardTemplate.effects`. One dispatcher, `engine/effect_resolver.py`, executes those specs by calling the owners that already exist.
- **TriggerSpec later (T-series).** Section 15 covers it: trigger events, the event bus on `engine/turn_clock.py`, real trigger stack items, APNAP ordering and intervening-if.

**The base design.** This spec starts from the design the judges selected, the Layered Clause Cascade (LCC; chosen 2 of 3). Section 1 lists:
- the round-1 fixes (F1–F11);
- the ideas grafted from the other two designs (G1–G17);
- the round-2 amendments (A1–A43), made after three refutations of the round-1 spec named about 50 registered-deck cards it would mis-parse or mis-resolve;
- the round-2 items this spec modifies or rejects (M1–M9), each with its reason.

**What E0 ships.** E0 adds data and tooling only:
- the schema (`engine/effect_spec.py`);
- the grammar package (`engine/effect_grammar/`);
- `CardTemplate.effects`, parsed lazily per template on first access and memoised (section 12; nothing parses during `CardDatabase` load);
- one derivation per legacy per-shape field (`engine/effect_views.py`), masked to the legacy domain;
- a dispatcher skeleton with empty executor tables and no callers (`engine/effect_resolver.py`), plus resolution-choice callback declarations that nothing calls yet;
- the UNMODELLED and residue census, the pool-wide equivalence tool (with gate parity and verb-closure reports), the seeded-game digest tool, and the `check_effect_parsers` ratchet;
- `tools/host_resolution_equivalence.py`, a per-host resolution harness. It resolves a host through the legacy path and the new path on fixed synthetic boards and compares the resulting state and log bytes. In E0 it runs a legacy-against-legacy self-check. It gates every family switch (A42);
- `tests/fixtures/effect_grammar_witnesses.json`, which holds every registered-deck card named in the round-2 review together with the spec chain it must parse to (section 18.1).

**E0 changes no resolution behaviour.** Four things prove it:
- **An AST test.** Nothing outside `engine/effect_*`, `engine/cards.py`, `engine/card_database.py` and `tools/` reads `CardTemplate.effects`.
- **An in-suite test.** Two seeded games, one of them a post-sideboard game, run once with populated effects and once with `EMPTY_EFFECTS`, and the logs are compared byte for byte.
- **The seeded digest.** It covers 16 seeded Bo1 games and 4 seeded Bo3 matches, so at least 24 games including post-sideboard games. Every game must be byte-identical to the parent commit.
- **Pool-wide unchanged-output tests for the three refactors E0 makes to existing modules:**
  - the located target parse in `target_solver`;
  - the shared loyalty slot owner in `oracle_parser`;
  - the new callback declarations in `callbacks`.

**Abstract-first answers (CLAUDE.md contract).**

| Question | Answer |
|---|---|
| Class size | Every oracle clause in the pool: 23,276 faces with text; 43,362 ability lines (25,775 unique after self-name normalisation); 51,813 sentences (27,622 unique); about 41.5k effect sentences. The five family surveys found roughly 36k family clauses. |
| Subsystem | `engine/effect_grammar/` owns clause parsing, once, at load. `engine/target_solver` owns TargetRequirements. `engine/effect_model` owns Selector, Duration and Modification. `engine/effect_resolver` owns sequencing. The existing owners own execution. |
| Failing tests first | The rule-phrased E0 test list (section 18.2) and the witness fixture (section 18.1). E0 fixes no behaviour. Every legacy defect the equivalence tool or the harness surfaces is fixed later: failing test, fix and rules-audit invariant land together, each in its own commit (section 18.3 lists the known ones). |
| Knowledge location | Oracle text (ModernAtomic via `card_database`), the MTGJSON per-face `keywords` field (data), and CR rules tables keyed by mechanic (CR 701, 702, 111.10), never by card. Self-names come from template data. `named X` spans are data. Card names appear only in the test fixture, never in `engine/`. |

---

## 1. Changes against the selected design

### 1.1 Round-1 fixes (F1–F11), as amended

- **F1. Spell text is one host.** Every spell-ability paragraph of an instant or sorcery face resolves as one ability, in printed order (CR 113.3a, 608.2c). The structure layer merges those paragraphs into one `SPELL` host, and linking runs over the merged host, so cross-paragraph links such as Revolt's "… instead" or Delirium's "deals 6 damage instead" find their antecedent. The following stay separate hosts and are never merged:
  - keyword lines (CR 702, classified by A1);
  - alternative costs (A2);
  - additional costs;
  - spell statics (A3);
  - cast triggers.

  A delay-prefixed paragraph on a spell is a delayed sub-ability inside the SPELL host (A5). `AbilityEffects.paragraphs` records where each part came from.
- **F2. One `mode_group` per modal block.** Every target requirement inside a modal block gets `mode_group=1`, as legacy `target_solver._detect_mode_group` does. This keeps the CR 700.2 rule "at least one mode's targets legal" an OR. Mode identity lives on the MODE host (`mode_index`).
- **F3. Targets are parsed on the whole clause.** `target_solver.parse_located(text)` returns `(req, start)` for each requirement. The start comes from `parse()`'s own claimed-span bookkeeping, the dict `where`, never from a second search (A20). Spans are mapped back to the clause text through the offset map of a located `_singularize_targets`. `parse()` is re-expressed as `[r for r, _ in parse_located(text)]` and its output is unchanged, which a pool test pins. `parse_spans` is a thin wrapper that adds the end offset. The grammar keeps the requirements whose span lies inside the verb's object slot.
- **F4 (amended by A21). Target residue is typed, visible and gated.** A printed qualifier that the solver dropped stays on the still-typed spec as a residue code.
  - Every code has a polarity in `RESIDUE_CODES`:
    - WIDENING: the requirement admits objects the text excludes;
    - NARROWING: the requirement excludes objects the text admits;
    - UNPARSED: slot tokens that were not consumed.
  - `effect_resolver.can_execute` refuses a residue-bearing spec unless each of its codes is in `LEGACY_RESIDUE_TOLERATED[family]`.
  - A WIDENING code enters that set only with RESIDUE_WIDENING evidence, and a NARROWING code only with RESIDUE_NARROWING evidence. Evidence means the equivalence tool shows the legacy handler executes the same requirement.
  - UNPARSED codes are never tolerable.
  - Codes enter only in the family's switch commit. After that the set only shrinks, one code per `target_solver` feature commit.
- **F5 (amended by A22). Filter entries are refused unless something evaluates them.** `effect_model.SUPPORTED_FILTER_VALUES` is value-typed. It is `{('controller', 'opponents'), ('without_keyword', ANY_KEYWORD)}`, exactly what `Selector.covers_object` evaluates today; `controller='you'`, a Ref controller and type keys are not in it. `ANY_KEYWORD` admits exactly the `cards.Keyword` values that covers_object compares (`'first_strike'`). A printed keyword (`'first strike'`), a misspelt one or one the enum lacks is refused, so the filter sub-grammar must type keyword exclusions as `cards.Keyword` values for them to execute. A behavioural test builds each supported entry, runs it against objects that should and should not be covered, and requires every other entry to make the spec non-executable. An entry is executable only if it is supported or its verb's executor passes it to an owner that evaluates it (`EXECUTOR_FILTER_KEYS[verb]`).
- **F6. No new `DurationKind` in E0.** A duration that `effect_model` cannot expire becomes `UNMODELLED(DURATION)`, never `duration=None`. Examples:
  - "until the end of your next turn";
  - "during its controller's next untap step";
  - "until end of combat";
  - "for as long as <cond>".

  Each new kind lands with its `expired_by` test in the family that first dispatches it (E5 for impulse).
- **F7 (amended by A37 and A38). A family switch keeps `clause_resolver.HANDLERS` order and each handler's legacy gate.**
  - The handler's gate is unchanged, and its gate reads the template field, which is now a legacy-masked view.
  - Its apply becomes: if the host matches the family's strict shape view and `can_execute(host, family)` holds, call `resolve_ability`; otherwise call the legacy apply for that host.
  - No host falls through to a later handler because of the switch, so first-gate-wins ordering and early returns stay byte-identical.
  - The registry collapses only in E7, which is a measured behaviour change.
- **F8 (amended by A42). The neutrality proof uses the seeded digest** (16 Bo1 games plus 4 Bo3 matches), the cheap two-game in-suite test and, from E1 on, the per-host resolution harness.
- **F9 (amended by A30). Intervening-if lives on a trigger head**, including the head of a reflexive sub-ability ("When you do, if …") (CR 603.4). It is never a spec condition.
- **F10. The hash-seed test compares `effect_spec.canonical()`**, which sorts frozensets, never `repr()`.
- **F11 (amended by A20). Each printed target word must produce exactly one requirement.** Noun uses of "target" do not count:
  - "the target of", "a single target", "new targets", "its targets";
  - "target" inside a quoted granted ability, which is counted in the granted host.

  A clause whose count still differs is `UNMODELLED(TARGET_COUNT)`.

### 1.2 Grafted ideas (G1–G17), as amended

- **G1.** `LoyaltyAbility.clause.effects` is a slice of the line's LOYALTY host, re-kinded `SPELL`, taken from the face the engine activates: face 0 for `loyalty_abilities`, face 1 for `back_face_loyalty_abilities`. It is never a re-parse, and clause templates are never parsed themselves (A12). The loyalty slot rule is factored into one shared function.
- **G2.** Legacy fields are sorted into three tiers:
  - Tier A: shape fields that resolvers consume; they gate a switch exactly.
  - Tier B: substring booleans; advisory.
  - Tier C: trigger-head, replacement and static-head fields; payload only until TriggerSpec or ReplacementSpec exists.
- **G3.** Adapter step: `parse_X(oracle) = view_X(parse_text_effects(oracle))` for the remaining text callers, before `parse_X` is deleted.
- **G4.** Runtime-shape equivalence covers:
  - every `clause_resolver` static shape function evaluated on a game-less `ClauseContext` for the SPELL text, every mode, and the kicked and channel overrides;
  - the head-stripped cast-trigger bodies that `oracle_resolver.resolve_self_cast_trigger` passes as `oracle_override` (A41).
- **G5.** The `spell_resolution` `Ability.description` substring interpreter (the third interpreter) and the `OracleTextParser` → `OracleEffect` → `_build_abilities` / `classify_card_role` pipeline (the fourth) are carriers in the equivalence tool, the census and ratchet count (b) (A41).
- **G6.** `CardEffects.verbs` is a frozenset index for O(1) view short-circuits.
- **G7 (replaced by A29).** May-scope nests only the followers that depend on the optional head.
- **G8 (amended by A33).** When the actor is a multi-player selector, RESULT refs bind per actor. Choices are made in APNAP order (CR 101.4), and the action is one simultaneous event performed once by the owner.
- **G9.** An `instead` sibling inherits the arguments it does not restate. A restated target is an alternative slot (`target_alts`). This covers the leading forms too (A15).
- **G10.** Legacy snapshots freeze each field's values when it migrates. `GRAMMAR_VERSION` is stamped into every report.
- **G11.** `tools/seeded_game_digest.py` sets `MTG_LLM_DECISION_SCORER_OFFLINE=1` and neutralises the game deadline through the shared `_ANCHOR_TIMEOUT_SECONDS`. Its pair table includes Bo3 matches, so sideboard cards are exercised.
- **G12.** `engine/effect_grammar/keywords.py` holds:
  - the CR 702 keyword-line table L1 uses (A1);
  - the CR 701 and CR 702 rules-English expansions, parsed by the same grammar; a test requires none of them to parse to UNMODELLED.
- **G13.** Residue codes rank `target_solver` extensions in the census.
- **G14.** `oracle_parser._DELAY_TIMING_PHRASES` stays the single source of delay phrases.
- **G15.** The `cast_targets` pseudo-field compares SPELL host targets with `target_solver.parse(whole oracle)`.
- **G16.** `TRIGGER_EMBEDDED` covers "whenever" inside an effect body. `ITERATION` covers "for each X, <effect>" only when the body refers to the element (A16).
- **G17.** Kept from the base design, with amendments:
  - an AST completeness scan, now over every `template.<attr> =` assignment and every lazy field (A41);
  - diff classes decided by typed predicates;
  - fake-executor sequencing tests;
  - the class-size ≥ 10 witness rule for pattern rows;
  - a no-card-name AST scan.

### 1.3 Round-2 amendments (A1–A43)

The refutations named the witness cards in brackets; section 18.1 turns each witness into a fixture row.

**Structure (L0 and L1)**
- **A1. Keyword lines.** A paragraph is a KEYWORD host when it is a list of CR 702 keyword abilities. Each item takes one of these forms: keyword, keyword N, keyword {cost}, keyword—non-mana cost (verbs allowed, e.g. "Flashback—Sacrifice a Mountain", "Escape—{3}{B}, Exile five other cards from your graveyard"), a multi-word keyword ("splice onto Arcane {1}{R}"), or a noun-parameter keyword ("gift a tapped Fish", tiered, web-slinging, harmonize, warp, plot, station).
  - Candidates come from the face's MTGJSON `keywords` field intersected with the CR 702 table in `keywords.py` (M3). Synthetic templates, which have no keyword data, use the table alone.
  - `cards.Keyword` is not the vocabulary: it lacks kicker, flashback, splice, rebound, escape, replicate, overload, gift, cycling, typecycling, fuse and tiered.
  - A keyword line never merges into a SPELL host.
  - Its cost is typed through `parse_activation_cost` on the printed span when it is a non-mana cost, and as a mana cost otherwise.
  - [Lava Dart, Cling to Dust, Desperate Ritual, Goryo's Vengeance, Into the Flood Maw, Unburial Rites, Faithless Looting, Ephemerate, Consult the Star Charts, Orim's Chant, Consign to Memory, Vandalblast, Solitude, Subtlety, Street Wraith, Endurance, Detective's Phoenix.]
- **A2. Alternative costs (CR 118.9).** `HostKind.ALTERNATIVE_COST` covers "rather than pay this spell's mana cost" and "you may cast this spell for …". The host carries `cost` and `cost_condition` (for example `TURN not_your_turn`) and holds no specs. It is lifted out of the SPELL host like ADDITIONAL_COST. [Force of Negation, Force of Vigor.]
- **A3. Spell statics on every face type.** "This spell costs …", "This spell can't be countered" and similar statics become `STATIC(from_zone='stack')` hosts on permanent faces as well as spell faces (CR 601.2f, 113.6). [Emrakul, the Promised End; Scion of Draco; Leyline Binding; Hollow One; Obsidian Charmaw; Chandra, Awakened Inferno.]
- **A4. Modal structure is detected before reminder text is stripped.** L0 records the reminder spans it removes.
  - A face whose `keywords` include Tiered or Spree, or whose removed reminder holds a choose header, is modal.
  - Tiered bullets `• <Name> — {c} — text` give MODE hosts with `mode_cost`, choose (1,1).
  - Spree bullets `+ {c} — text` give choose (1, n).
  - The grammar's own header table is a superset of legacy `_MODAL_HEADER_RE`: it adds "one or more" and "any number". Legacy views mask the difference.
  - A modal TRIGGERED header ("Whenever ~ attacks, choose one —") keeps its TriggerHead.
  - [Fire Magic, Territorial Kavu.]
- **A5. A delay-prefixed paragraph on an instant or sorcery is not a triggered ability.** A paragraph that begins with a `_DELAY_TIMING_PHRASES` prefix becomes a `CREATE_TRIGGER(DELAYED)` spec inside the SPELL host (CR 603.7). On a permanent face, a paragraph-initial "next …" delay phrase is `UNMODELLED(STRUCTURE)` (M7). [Summoner's Pact.]
- **A6. Mana abilities follow CR 605.1a.**
  - An activated, non-loyalty host with no target anywhere (sub-abilities included) that contains ADD_MANA anywhere is MANA_ABILITY. Riders are allowed: painland and talisman damage, haste riders, "instead" upgrades.
  - A TRIGGERED host whose head is a mana-ability event ("is tapped for mana", "you tap … for mana"), which has no target and contains ADD_MANA, carries host flag `mana_ability` (CR 605.1b), so T2 never puts it on the stack.
  - [Shivan Reef, Talisman of Resilience, Arena of Glory, Gemstone Caverns, Utopia Sprawl, Badgermole Cub, Leyline of Abundance.]
- **A7. Costs, riders and loyalty costs are parsed from printed text.** L0 keeps an offset map from normalised to printed text for the length of one parse call. `parse_activation_cost`, `split_activation_riders` and the loyalty cost read the printed span, because `parse_activation_cost` recognises "sacrifice this …" and "exile this …" but not "~" (verified: `'{t}, sacrifice ~'` is unpayable). The face memo key includes those printed spans (A32). [Flooded Strand and every fetchland, Mishra's Bauble, Expedition Map.]
- **A8. Cost modifiers are absorbed, never effects.**
  - "This ability costs {N} less/more to activate …" becomes a COST_DELTA `Modification` in `AbilityEffects.cost_modifiers` (CR 602.2b applying 601.2f). 41 pool faces have it.
  - "The flashback cost is equal to its mana cost" becomes the cost parameter of the granted flashback keyword.
  - [Boseiju, Who Endures; Otawara, Soaring City; Past in Flames; Snapcaster Mage.]
- **A9. Self-pronouns are normalised by grammatical case.** This applies only on planeswalker and legendary-character faces:
  - object him/her become `~`;
  - possessive his/her become `~'s` (M1);
  - subject he/she become `~`;
  - he's/she's become `~ is`.

  Participant and destination skeletons accept `~'s owner's`/`~'s controller's`. [Tamiyo, Inquisitive Student; Ajani, Nacatl Pariah; Ral, Monsoon Mage; Kaito, Bane of Nightmares.]
- **A10. Nested quotes.** Inside a double-quoted span, a single quote opens a nested quote only immediately after `with `, `gains `, `gain `, `has ` or `have `, and only before a capital letter or `{`. It closes at the last `'` before the enclosing `"` that is not between two letters. Apostrophes (owner's, can't, owners') never open or close. [Urza's Saga chapter II and the 184 double-quoted spans that contain apostrophes.]
- **A11. Disjunctive trigger heads.** `TriggerHead.event_hints` (and later `TriggerSpec.events`) is a tuple sharing one body (CR 603.2). 179 pool cards have such heads. [Primeval Titan, Archon of Cruelty, Orcish Bowmasters, Cityscape Leveler.]
- **A12. Faces and loyalty.**
  - Face 1 is parsed before `_type_loyalty_clauses` runs for `back_face_loyalty_abilities`.
  - Loyalty clause templates are built by `_type_loyalty_clauses(..., walker=, face=)` and their `effects` is a slice of the LOYALTY host of the face the engine activates, re-cut whenever the walker's effects object changes (section 12). They are never parsed under their synthetic `<walker> (slot)` name.
  - Face-1 LOYALTY hosts are Tier A against `back_face_loyalty_abilities`.
  - `[+X]` and `[−X]` lines are LOYALTY hosts with `loyalty_cost = Amount(X, n=±1)`. X is bound from the paid loyalty cost (CR 107.3, 606.4) and never falls through to ACTIVATED.
  - An `[+X]` or `[−X]` line takes no loyalty slot in E0. `oracle_parser.loyalty_slot_for`, the one owner of the slot rule, gives a variable-cost line `""` and lets it take no slot from a later line. Those are the slots of legacy's line set (`_LOYALTY_LINE_PATTERN` reads fixed costs only), so the grammar, which passes the printed superset, and `parse_loyalty_abilities`, which passes fixed lines only, agree on every fixed line, and `CardEffects.loyalty(slot)` finds the host legacy's slot names. A pool test pins that agreement for every face. Giving X lines slots is a behaviour change for its own measured commit: those lines become activatable, and minus/ult move on the 9 pool walkers whose X line precedes a fixed negative line (Ashiok, Nightmare Weaver; Chandra Nalaar; Chandra, Chill of Compliance; Kasmina, Enigma Sage; Liliana, Defiant Necromancer; Sorin, Grim Nemesis; Tamiyo, Compleated Sage; Tezzeret the Seeker; Ugin, the Spirit Dragon). None of them is in a registered deck.
  - [Ajani, Nacatl Avenger; Ral, Leyline Prodigy; Tamiyo, Seasoned Scholar; Chandra, Awakened Inferno; Grist, the Hunger Tide.]

**Clauses (L2 and L3)**
- **A13. Serial lists and gapping.** L3 splits at a depth-0 `, <lemma>` when both of these hold:
  - the token after the comma is an inflected lexicon verb;
  - the text before the comma already holds its own verb.

  It never splits inside a type list. Elided subjects and actors are inherited. Gapping copies a put or return verb into `and <count-or-REST NP> <destination PP>` ("put two of them into your hand and the rest on the bottom"). [Every fetchland, Path to Exile, Summoner's Pact, Scapeshift, Expressive Iteration, Archon of Cruelty, Faithful Mending, Green Sun's Zenith, Nature's Rhythm, Primeval Titan, Stock Up, Waker of Waves.]
- **A14. Connective scope.**
  - "If you do" / "If they do" gates every clause of its sentence.
  - "If you don't <VP>," is the negated performed-test of the nearest earlier spec whose verb and object the VP names. The VP is consumed as a frame token; if no spec matches, the result is `UNMODELLED(REFERENCE)`.
  - "When you do" opens a reflexive sub-ability that runs to the end of the ability, or to the end of the current mode in a modal host (M8) (A30).
  - [Risen Reef, Formidable Speaker, Guide of Souls.]
- **A15. `instead` forms.**
  - Leading "If <COND>, instead <VP>" and "instead <VP>" (139 pool cards) are replacing siblings.
  - "If <ref> is <verb>ed this way, <move ref> instead of putting it into <zone>" folds into the named spec as a destination override: `dest=Destination(zone, instead_of=<zone>)`. It applies only when that spec is performed, and it is never a sibling that replaces the counter itself (CR 701.5a).
  - A replacing sibling replaces the nearest earlier spec that prints its verb, a refused one included. Only when no earlier spec prints that verb at all does it replace the nearest earlier spec of another verb ("deals 2 damage to target creature ... destroy that creature instead"). When the same-verb antecedent is refused, the replaced action is unknown: the clause is `UNMODELLED(REFERENCE, 'no_antecedent')`, never rewired. A leading "if ... would ..." is a REPLACEMENT refusal (CR 614), never a replacing sibling, and carries no `replaces` (L5 review, 2026-10-01).
  - A replacing spec's condition is evaluated lazily, once, at its first victim's position (A33).
  - [Force of Negation (22 pool counters), Into the Flood Maw, Gemstone Caverns.]
- **A16. Leading "For each <Q>, <counted VP>".** When the body has no anaphor to the element, this is the amount `FOR_EACH(Q)`. It is `UNMODELLED(ITERATION)` only when the body refers to the element ("a copy of it", "of that type"). [Seasoned Pyromancer.]
- **A17. Recipient unions.** "damage to each opponent and each planeswalker you don't control" splits into DAMAGE siblings sharing one simultaneity `group`. [Omnath, Locus of Creation.]
- **A18. Shuffling an object into a library.** "shuffle(s) <object> into <library>" is `MOVE(dest=Destination('library', position='shuffle'))`. Only "shuffle (your|their) library" is SHUFFLE. [Green Sun's Zenith, Day's Undoing.]
- **A19. New phrase types.**
  - "any amount of" is ANY_NUMBER.
  - "your choice of X or Y", "a Food token or a Treasure token" and similar alternatives become `EffectSpec.alternatives`, chosen at resolution by `chooser`.
  - `CardFilter.classes` holds derived object classes from a closed, CR-defined vocabulary (historic CR 700.6, colored, multicolored, monocolored).
  - [Galvanic Discharge, Practiced Offense, Monumental Henge.]

**Targets**
- **A20. Located parse and noun uses.** See F3 and F11. The requirement placements now come from `parse()`'s own bookkeeping, so 44 pool sentences whose requirements are out of printed order map correctly. [Practiced Offense, Kozilek's Command, Warping Wail, Untimely Malfunction.]
- **A21. Target-slot consumption.**
  - Every token of a target slot must be consumed by the solver's `raw_phrase` or by a recognised feature. Leftover tokens give the non-tolerable residue `target.unparsed`.
  - New codes:
    - `target.colored` ("one or more colors"), WIDENING;
    - `target.nontoken`, WIDENING;
    - `target.historic`, WIDENING;
    - `target.zone_union` (spell or permanent), UNPARSED, for the case where the solver keeps a different member of the union than the text's primary object.
  - The same full-consumption rule applies to CardFilter for untargeted choices and searches; leftovers there give `UNMODELLED(FILTER)`.
  - [Ugin, Eye of the Storms; Devourer of Destiny; Sink into Stupor.]
- **A22. Value-typed filter support.** See F5. [Violent Outburst, and every "creatures you control" subject.]

**References (L5)**
- **A23. Rule 0.** A pronoun inside a spec's own frame modifier (if, unless, equal to, with, where, as long as) binds first to that spec's own principal. If the principal is a quantified subject, the pronoun binds to `Ref(MEMBER)`, the member being evaluated. [Fatal Push, Prismatic Ending, Spell Pierce, Mana Tithe, Stubborn Denial, Mystical Dispute, Metallic Rebuke, Flusterstorm, Drown in the Loch, Scion of Draco.]
- **A24. Every earlier participant mention is an antecedent.** This includes principal Refs (SELF, ATTACHED, EVENT_OBJECT, LINKED), `other`, `actor` and condition subjects, not only targets and results. [Kappa Cannoneer, Blade of the Bloodchief.]
- **A25. After a zone-changing spec, a reference to the moved object binds to RESULT, the new object (CR 400.7).** TARGET and RESULT of the same spec are one candidate, never an ambiguity. [Goryo's Vengeance.]
- **A26. "The exiled card(s)".** It binds to an EXILE result in the same ability first. LINKED (CR 607) applies only when there is no such result and the object has an exiling linked ability. [Expressive Iteration, as against the Isochron Scepter shape.]
- **A27. Last-known information for the source.**
  - `Ref(SELF, lki=True)` wherever the host's cost sacrifices, exiles, returns or discards the source.
  - `Ref(SELF | EVENT_OBJECT, lki=True)` in dies and leaves-the-battlefield bodies (CR 608.2h).
  - [Engineered Explosives, The Filigree Sylex.]
- **A28. REST and OTHER.** REST of RESULT(k) is RESULT(k) minus every object consumed by a later spec that references it, including a replacing `instead` sibling. It is computed at resolution, after those specs ran. [Consult the Star Charts (kicked).]
- **A29. May-scope by dependency.** A follower nests into the optional head's `then` (flag `may_scope`) only in one of two cases:
  - it references the head's RESULT, directly or through a pronoun;
  - it is a SHUFFLE of the library the head searched.

  Any other follower is a sibling. [Path to Exile, as against Zimone's Experiment.]
- **A30. Sub-abilities.**
  - `Verb.CREATE_TRIGGER` carries `SubAbility(kind=REFLEXIVE|DELAYED, timing, host)`.
  - The nested host owns its specs, its targets (chosen when the sub-ability is created or triggers, never with the parent, CR 603.12 and 603.7), its own TriggerHead with any intervening-if (CR 603.4), and every later sentence that binds to its results.
  - Its targets are excluded from the parent's `AbilityEffects.targets`, from cast and activation legality, and from the parent's fizzle check.
  - `EffectSpec.delay` no longer exists.
  - [Guide of Souls; Ajani, Nacatl Avenger [0]; Grist −2; Phelia, Exuberant Shepherd; Goryo's Vengeance; Summoner's Pact.]

**Schema**
- **A31. Costs are frozen snapshots.** `ActivationCost` and `ManaCost` are mutable, unhashable dataclasses (verified: `hash(ActivationCost())` raises TypeError). Hosts and conditions hold `CostSnapshot` (frozen, hashable), and `thaw()` returns a fresh `ActivationCost` for each caller.
- **A32. The face memo key is complete.** It includes every fact the structure and linker layers read: normalised text, card-type class (creature, artifact, land, planeswalker, …), `is_spell`, `is_legendary`, `has_x`, the face's CR 702 keyword set, and the printed cost, rider and loyalty spans.

**Dispatcher**
- **A33. One simultaneous action.**
  - Executors receive the whole APNAP-ordered actor tuple and perform one simultaneous action through the owner. Choices are gathered in APNAP order; the physical order inside the owner stays as legacy does it (CR 101.4).
  - Simultaneous multi-player zone moves go through one owner that defers enter-the-battlefield handling until every player's move is done. That is `zone_transfer.move_simultaneously`, extracted from legacy `_resolve_living_end` phases 1–3 in E2.
  - Universal "triggers wait until the ability finishes" (CR 603.3) lands with T2 (M5).
  - A replacing spec's condition is evaluated lazily, at the victim's position, exactly once.
  - [Living End.]
- **A34. Bindings are zone-change handles.** Results and snapshots hold `Handle(instance_id, zone, entry_seq)` (CR 400.7). An executor that finds the object no longer in that zone with that entry does nothing (CR 603.7c), which matches legacy `register_end_of_turn_exile`. [Goryo's Vengeance + Ephemerate.]
- **A35. Resolution-time choices go through callbacks.** The callbacks are `choose_optional_effect`, `choose_amount(ctx, spec, lo, hi, remaining_specs)`, `choose_cards(ctx, spec, pool, n)` and `choose_division(ctx, spec, slots, total)`. Division is fixed when targets are chosen (CR 601.2d); an illegal target's share is not dealt. THAT_MUCH or RESULT of a declined or unperformed spec is 0 or empty. The AI answers; the engine never scores. [Galvanic Discharge, Ral, Leyline Prodigy −2.]
- **A36. The binding helper never picks targets.**
  - An unbound slot is passed to the owner as None, so the owner's existing picker (threat key plus condition prefilter) decides, exactly as legacy does.
  - Each owner's legacy illegal-target semantics are kept until that family's behaviour-change commit: the any-target face fallback, the energy-damage re-pick, immediate `_creature_dies`.
  - `chosen_from_legacy(ability, item_targets)` maps the legacy flat list (category order, −1 face sentinel) to per-slot tuples.
  - [Witch Enchanter, Practiced Offense.]
- **A37. `resolve_ability` checks `can_execute` itself and returns False without raising.** A switched handler keeps its gate and falls back to its legacy apply for any host that is not executable or not in its strict shape (F7).

**Migration and tooling**
- **A38. Per-host switching.**
  - The unit of switching is the host, not the handler.
  - The equivalence tool's `--closure` report lists, per handler, the verb closure of every host the legacy gate accepts, split into deck (MB and SB) and pool.
  - GATE_PARITY: every host the legacy gate accepts either takes the new path (strict view, executable, harness byte-identical) or is counted as legacy fallback. Ratchet count (f) may only shrink.
  - A legacy apply is deleted when its fallback count reaches zero pool-wide.
  - Strict shape views are fixed per family, so adding a later family's executors never silently switches an earlier family's host. [Cleansing Wildfire, Galvanic Discharge, Ajani [0].]
- **A39. View domain masks.**
  - In a switch commit, the field view must equal legacy on the whole pool. Growth is masked by a `_legacy_domain_*` predicate, counted by ratchet count (c).
  - Removing a mask is its own measured behaviour-change commit.
  - Tier A derivations reject specs that carry a duration, a sub-ability, a head intervening-if or residue, unless legacy reproduces them.
  - [Wistfulness, Leyline Binding.]
- **A40. `kicked_clause` is the exact printed span.** It runs from the end of the kicked frame to the end of its sentence, with reminder text stripped and printed case kept (including "instead"). It is recomputed at load through `effect_grammar.printed_span`, and includes SELF_CAST triggers whose intervening-if is CAST_FACT kicked. [Consult the Star Charts, Sowing Mycospawn.]
- **A41. Carriers and completeness.**
  - `oracle_resolver.resolve_self_cast_trigger` is a runtime carrier, and `host_for_override` indexes head-stripped TRIGGERED bodies.
  - The `OracleTextParser` pipeline is a carrier.
  - The completeness scan covers all 193 `template.<attr> =` assignments (142 are `parse_*` calls), the `__post_init__` lazy fields, and the `ActivatedAbility` and `LoyaltyAbility` fields.
  - [Ulamog, the Ceaseless Hunger; Devourer of Destiny; Sowing Mycospawn.]
- **A42. The per-host resolution harness and the Bo3 digest.** For every registered-deck card (mainboard and sideboard) whose host a switched handler takes, the harness resolves legacy and new paths on fixed synthetic boards and seeds, and compares state and log bytes. It is a gate of every E_k. [Into the Flood Maw, Wistfulness, Price of Freedom.]
- **A43. The load budget is gated on pool CPU.** The budget test measures the process time of the whole pool parse against the stated budget (3.0 s, raised to 4.0 s on 2026-10-01; section 12). The earlier 260 µs × 23.5k sample bound allowed about 6.1 s and is dropped.

### 1.4 Round-2 items modified or rejected (M1–M9)

- **M1. Possessive self-pronouns become `~'s`, not `its`.** One review asked for `its`. That would send a self-reference through pronoun binding, where "its" can bind to a nearer target ("return target creature … under her owner's control" would bind to the target). `~'s` keeps the self-reference exact, and the skeletons accept `~'s owner's`. The other review asked for `~'s`, and this spec follows it.
- **M2. A mixed self-or-other trigger head keeps EVENT_OBJECT as the fallback antecedent.** The review asked that "it" never default to EVENT_OBJECT. A24 fixes the witnessed defect: an earlier mention of `~` now wins (Kappa Cannoneer). With no earlier mention, "it" after "Whenever ~ or another creature enters" denotes the object that entered, and that is `~` when `~` triggered. EVENT_OBJECT is the correct binding there (CR 603.2).
- **M3. MTGJSON `keywords` are filtered through the CR 702 table.** That field also lists CR 701 keyword actions (Scry, Mill, Investigate, Proliferate, Surveil). Using it raw would turn effect paragraphs such as "Scry 2." into KEYWORD hosts.
- **M4. NARROWING residue is tolerable with narrowing evidence rather than never.** The review said widening tolerance must not cover narrowing residue, and A21 keeps them separate. Refusing all narrowing codes would break byte-identity for cards that legacy resolves narrowed today, and a narrowed requirement never makes an illegal target legal. Zone unions where the solver keeps a different union member (Sink into Stupor) are not narrowing: they are the non-tolerable `target.zone_union`, and legacy keeps the card through fallback.
- **M5. Trigger deferral becomes universal only at T2.** One review asked resolve_ability to defer every ETB and dies event to the end of the ability. Today single-object reanimation (Goryo's Vengeance) logs its ETB inline, so universal deferral in an E-step would break byte-identity. It is the CR 603.3 behaviour change that belongs with real stack items. E2 extracts the one owner that already defers (the legacy Living End phases).
- **M6. The family order stays verb-family based, with per-host switching and legacy fallback.** Both reviews offered "land the verb closure, or move the handler". Per-host fallback does both without making families large, and the closure report decides when a legacy apply may be deleted.
- **M7. Delay-prefixed paragraphs on permanent faces are `UNMODELLED(STRUCTURE)`.** The review specified only the spell case. A printed triggered ability never says "the next", so this shape is not a TRIGGERED host either.
- **M8. "When you do" scope stops at the end of the current mode in a modal host.** Mode bullets are separate instructions (CR 700.2).
- **M9. Galvanic Discharge is not E1's byte-identity witness.** Its host needs CHOOSE, PLAYER_COUNTERS and PAY. Legacy writes `damage_marked` directly, kills immediately and re-picks the target. It moves to E4.b as a measured behaviour change. The `choose_amount` callback it needs (A35) is accepted as specified.

---

## 2. The model (`engine/effect_spec.py`, schema only)

Every class is `@dataclass(frozen=True, slots=True)`: hashable, picklable, and shareable across the memo. No field holds a mutable object (A31). The module imports:
- `TargetRequirement` from `target_solver`;
- `Selector`, `SelectorKind`, `Duration`, `Modification` from `effect_model`;
- `DelayedTriggerTiming` from `delayed_triggers`.

It does no parsing and has no game access.

```python
GRAMMAR_VERSION = 1

class Verb(Enum):
    # zone (CR 701.8, 701.13, 701.21, 400.7, 701.24)
    DESTROY; EXILE; SACRIFICE; MOVE; SHUFFLE     # MOVE = return/put/shuffle <object> to|onto|into <dest>;
                                                 # SHUFFLE = a player shuffles their library
    DAMAGE; FIGHT; LOSE_LIFE; GAIN_LIFE; SET_LIFE; EXCHANGE_LIFE
    DRAW; DISCARD; MILL; SCRY; SURVEIL; LOOK; REVEAL; REVEAL_UNTIL; SEARCH; CHOOSE; CAST_FREE
    CREATE_TOKEN; PUT_COUNTERS; REMOVE_COUNTERS; MOVE_COUNTERS; DOUBLE_COUNTERS; PLAYER_COUNTERS
    KEYWORD_ACTION                               # CR 701 action; payload KeywordAction(expansion)
    CONTINUOUS                                   # CR 611-613; payload effect_model.Modification
    TAP; UNTAP; TRANSFORM; ATTACH
    COUNTER; ADD_MANA; PAY; COPY; CHANGE_TARGETS; CREATE_EMBLEM; EXTRA_TURN; END_TURN; SKIP
    CREATE_TRIGGER                               # delayed (CR 603.7) or reflexive (CR 603.12) sub-ability
    UNMODELLED

class HostKind(Enum):
    SPELL; MODE; ACTIVATED; MANA_ABILITY; LOYALTY; TRIGGERED; CHAPTER; STATIC
    KEYWORD; ADDITIONAL_COST; ALTERNATIVE_COST; REPLACEMENT; GRANTED; UNKNOWN

class RefKind(Enum):
    SELF; TARGET; RESULT; EVENT_OBJECT; EVENT_PLAYER; LINKED; ATTACHED
    CONTROLLER_OF; OWNER_OF; CHOSEN; DEFENDING_PLAYER
    MEMBER                           # the member of this spec's own quantified subject being evaluated (A23)

class RefPart(Enum): ALL; ONE; REST; OTHER; EACH

class Ref:
    kind: RefKind
    index: Optional[int] = None      # TARGET: owning host's targets[k]; RESULT: producing spec.seq; LINKED: host index
    part: RefPart = RefPart.ALL
    n: Optional["Amount"] = None
    of: Optional["Ref"] = None       # operand of CONTROLLER_OF / OWNER_OF
    lki: bool = False                # CR 608.2h (A27)
    per_actor: bool = False          # RESULT bound per actor of a multi-player spec
    noun: str = ""

Participant = Union[TargetRequirement, Selector, Ref]

class CardFilter:                    # untargeted / hidden-zone object description; never a target
    zone: str = "battlefield"
    types / all_types / not_types: FrozenSet[str]
    supertypes / not_supertypes / subtypes / not_subtypes / colors / not_colors: FrozenSet[str]
    classes: FrozenSet[str] = frozenset()      # closed CR-defined classes: historic, colored, multicolored, monocolored (A19)
    colorless: Optional[bool] = None
    controller: Union[str, Ref] = "any"
    owner: Union[str, Ref] = "any"
    other: bool = False
    token: Optional[bool] = None
    stat_bounds: Tuple[Tuple[str, str, "Amount"], ...] = ()
    with_keywords / without_keywords: FrozenSet[str]
    state: FrozenSet[str] = frozenset()
    counters: Tuple[Tuple[str, bool], ...] = ()
    named: Union[None, str, Ref] = None
    position: Optional[str] = None
    different_names: bool = False
    raw: str = ""
    def as_tuple(self) -> Tuple[Tuple[str, Any], ...]: ...
    def as_selector(self) -> Selector: ...

class AmountKind(Enum):
    LITERAL; X; X_DEFINED; EQUAL_TO; FOR_EACH; THAT_MUCH; ALL; ANY_NUMBER
    UP_TO; HALF; MULTIPLY; PLUS; DIVIDED; WHOLE_ZONE

class Amount:
    kind: AmountKind
    n: int = 0          # LITERAL signed | X: sign (+1/-1, loyalty costs) | FOR_EACH per unit | MULTIPLY factor | PLUS addend | UP_TO bound
    quantity: Optional["Quantity"] = None
    ref: Optional[Ref] = None
    inner: Optional["Amount"] = None
    rounding: Optional[str] = None
    evenly: bool = False

class QuantityKind(Enum):
    COUNT; CARDS_IN; POWER; TOUGHNESS; MANA_VALUE; COUNTERS_ON; LIFE_TOTAL; DEVOTION
    BASIC_LAND_TYPES; COLORS_SPENT; CARD_TYPES_IN_GRAVEYARD; GREATEST; TOTAL; TIMES_KICKED
    RESULT_SIZE; HISTORY

class Quantity:
    kind: QuantityKind
    filter: Optional[CardFilter] = None
    player: Union[str, Ref] = "you"
    ref: Optional[Ref] = None        # COUNTERS_ON(Ref(SELF, lki=True)) after a sacrifice-as-cost (A27)
    stat: Optional[str] = None
    counter_kind: Optional[str] = None
    event: Optional[str] = None      # HISTORY closed vocabulary
    raw: str = ""

class CostSnapshot:                  # frozen image of ActivationCost (A31)
    items: Tuple[Tuple[str, Any], ...]      # every ActivationCost field; ManaCost flattened; lists/dicts -> sorted tuples
    def thaw(self) -> "ActivationCost": ... # a fresh mutable object per call; never shared
def freeze_cost(cost: "ActivationCost") -> CostSnapshot: ...

class ConditionKind(Enum):
    STATE; OBJECT; CAST_FACT; TURN; HISTORY; RESOLUTION_ORDINAL; UNLESS; ALL_OF; ANY_OF; NOT

class Condition:
    kind: ConditionKind
    pred: str = ""
    ref: Optional[Ref] = None        # OBJECT subject (Ref(TARGET k) / Ref(MEMBER) by rule 0) / UNLESS payer
    payer: Optional[Selector] = None
    filter: Optional[CardFilter] = None
    op: str = ">="
    n: Optional[Amount] = None
    cost: Optional[CostSnapshot] = None
    children: Tuple["Condition", ...] = ()
    label: str = ""                  # ability word (CR 207.2c): informational only
    raw: str = ""

class Destination:
    zone: str
    position: Optional[str] = None   # top | bottom | shuffle | nth
    nth: Optional[int] = None
    order: Optional[str] = None
    tapped / attacking / transformed / face_down: bool = False
    controller: str = "default"      # 'you' | 'owner' | 'default'
    entry_counters: Tuple[Tuple[str, Amount], ...] = ()
    attached_to: Optional[Ref] = None
    instead_of: Optional[str] = None # the zone this destination replaces ('graveyard'), A15

class CounterSpec:  kinds: Tuple[str, ...]; choice: bool = False
class ManaSpec:     symbols: Tuple[str, ...] = (); choice: Tuple[str, ...] = (); any_color / one_color /
                    combination / chosen_color: bool = False; mirror: Optional[Ref] = None; restriction: str = ""
class KeywordSpec:  name: str; n: Optional[int] = None; cost: Optional[str] = None
                    cost_snapshot: Optional[CostSnapshot] = None; param: Optional[str] = None
                    cost_rule: str = ""                         # 'mana_cost' for 'flashback cost is equal to its mana cost' (A8)
class KeywordAction: name: str; amount: Optional[Amount] = None; subtype: Optional[str] = None
                     expansion: Tuple["EffectSpec", ...] = ()
class Granted:      hosts: Tuple["AbilityEffects", ...]
class TokenSpec:
    power / toughness: Optional[Amount] = None
    colors: FrozenSet[str] = frozenset(); types / subtypes / supertypes: Tuple[str, ...] = ()
    keywords: Tuple[Tuple[str, Optional[str]], ...] = ()
    name: Optional[str] = None; predefined: Optional[str] = None
    granted: Tuple["AbilityEffects", ...] = ()
    copy_of: Optional[Participant] = None; copy_except: Tuple["EffectSpec", ...] = ()

class SubAbilityKind(Enum): DELAYED; REFLEXIVE
class SubAbility:                    # A30
    kind: SubAbilityKind
    timing: Optional[DelayedTriggerTiming]   # DELAYED only; CR 603.7
    host: "AbilityEffects"                   # owns its specs, targets and TriggerHead (intervening_if)

class Stage(Enum):
    STRUCTURE; SPLIT; NO_LEMMA; CLAUSE; TARGET; TARGET_COUNT; FILTER; AMOUNT; QUANTITY
    CONDITION; DURATION; DELAY; REFERENCE; ITERATION; TRIGGER_EMBEDDED
    RECOGNIZED_UNSUPPORTED; REPLACEMENT; INVALID

class Unmodelled: stage: Stage; lemma: str = ""; detail: str = ""

Payload = Union[Modification, TokenSpec, CounterSpec, ManaSpec, KeywordAction, Granted,
                CostSnapshot, SubAbility, Unmodelled]

class Chooser(Enum): CONTROLLER; PARTICIPANT; OPPONENT; RANDOM

class EffectSpec:
    verb: Verb
    target: Optional[TargetRequirement] = None   # ONLY from target_solver; == owning host.targets[target_slot]
    target_slot: Optional[int] = None
    subject: Optional[Selector] = None
    ref: Optional[Ref] = None
    other: Optional[Participant] = None
    actor: Optional[Participant] = None
    filter: Optional[CardFilter] = None
    amount: Optional[Amount] = None
    chooser: Chooser = Chooser.CONTROLLER
    dest: Optional[Destination] = None
    payload: Optional[Payload] = None
    duration: Optional[Duration] = None          # effect_model.Duration, kind only
    condition: Optional[Condition] = None
    optional: bool = False
    then: Tuple["EffectSpec", ...] = ()          # iff THIS spec was performed / its optional choice accepted
    otherwise: Tuple["EffectSpec", ...] = ()     # iff not performed, declined, or the condition was false
    replaces: Tuple[int, ...] = ()               # 'instead': seqs of earlier siblings replaced when the condition holds
    alternatives: Tuple["EffectSpec", ...] = ()  # 'your choice of X or Y' / 'a Food or a Treasure' (A19)
    group: Optional[int] = None                  # simultaneity group
    flags: FrozenSet[str] = frozenset()          # closed: reflexive, may_scope, if_you_do, reveal, no_regeneration,
                                                 #   at_random, each, gapped, dest_override
    residue: Tuple[str, ...] = ()                # RESIDUE_CODES, each with a polarity (A21)
    seq: int = 0
    span: Tuple[int, int] = (0, 0)               # into the owning AbilityEffects.text (normalised)
    raw: str = ""
    @property
    def mod_kind(self): return self.payload.kind if isinstance(self.payload, Modification) else None

class EventHint(Enum):
    SELF_ENTERS; SELF_DIES; SELF_LEAVES; SELF_ATTACKS; SELF_CAST; OTHER_ENTERS; OTHER_DIES
    ATTACKS_OTHER; SPELL_CAST; BEGINNING_OF; COMBAT_DAMAGE_TO_PLAYER; LANDFALL; COUNTERS_PUT
    CYCLE; TAPPED_FOR_MANA; REFLEXIVE; DELAYED; OTHER

class TriggerHead:
    event_hints: Tuple[EventHint, ...]         # disjunctive heads keep every hint (A11)
    raw: str
    step: str = ""
    intervening_if: Optional[Condition] = None # CR 603.4; also on reflexive sub-ability heads
    once_each_turn: bool = False
    frequency_raw: str = ""
    names_player: bool = False                   # the head's event names a player (participant leaf, at L1)
    names_object: bool = False                   # ... an object besides the source; L5's host antecedent reads these

class AbilityEffects:
    kind: HostKind
    face: int
    index: int
    paragraphs: Tuple[int, ...] = ()
    text: str = ""
    specs: Tuple[EffectSpec, ...] = ()
    targets: Tuple[TargetRequirement, ...] = ()        # this host's own targets only; sub-ability targets live in the sub host
    target_alts: Tuple[Tuple[int, int], ...] = ()
    trigger: Optional[TriggerHead] = None
    cost: Optional[CostSnapshot] = None                # parse_activation_cost(PRINTED head) (A7)
    cost_modifiers: Tuple[Modification, ...] = ()      # COST_DELTA from 'This ability costs …' (A8)
    cost_condition: Optional[Condition] = None         # ALTERNATIVE_COST condition (A2)
    activation_index: Optional[int] = None
    loyalty_cost: Optional[Amount] = None              # LITERAL signed, or X with sign (A12)
    loyalty_slot: str = ""
    chapters: Tuple[int, ...] = ()
    modes: Tuple["AbilityEffects", ...] = ()
    choose: Tuple[int, int] = (0, 0)
    mode_index: int = -1
    mode_cost: str = ""                                # spree '+ {1}', tiered '{2}' (A4)
    label: str = ""
    keywords: Tuple[KeywordSpec, ...] = ()
    from_zone: str = "battlefield"
    flags: FrozenSet[str] = frozenset()                # uncounterable, sorcery_speed, mana_ability (A6)
    restrictions: Tuple[str, ...] = ()

class CardEffects:
    faces: Tuple[Tuple[AbilityEffects, ...], ...] = ()
    verbs: FrozenSet[Verb] = frozenset()
    def front(self): ...
    def spell(self, face=0): ...
    def modes(self, face=0): ...
    def loyalty(self, slot, face=0): ...
    def activated(self, index, face=0): ...
    def with_face(self, i, hosts): ...
    def walk(self, include_granted=False, include_sub=True): ...   # pre-order incl. sub-ability hosts
    def unmodelled(self): ...

EMPTY_EFFECTS = CardEffects()
RESIDUE_CODES: Mapping[str, str]   # code -> 'WIDENING' | 'NARROWING' | 'UNPARSED'; a family declared
                                   # '<prefix>:*' resolves every '<prefix>:<param>' through in / [] / get
def canonical(obj) -> str: ...
def validate_spec(spec, host, parents=()) -> Optional[str]: ...   # parents: the hosts that created a sub-ability host
```

**Invariants.** `validate_spec` checks these. A violation lowers the spec to `UNMODELLED(INVALID, detail=<rule>)` and never raises.
1. At most one principal participant is set. Actor-only verbs have none.
2. `target` is set exactly when `target_slot` is set, and `owning_host.targets[target_slot] is target`, where the owning host is the innermost host, including a sub-ability host.
3. Every `Ref(RESULT).index` and every `replaces` seq is lower than the spec's own seq. Refs never cross hosts, except LINKED and refs from a sub-ability host to its parent's specs; the latter are captured in the snapshot (A34). Precisely:
   - a `replaces` seq names a spec of the same host;
   - a RESULT index names a spec of the same host or of a host that created this sub-ability host, transitively; a MODE host shares its modal host's creators, and a GRANTED host (an ability of its own) has none;
   - a TARGET index names one of the owning host's own `targets`;
   - `validate_spec(spec, host, parents)` takes the creating hosts, and a host-relative ref with no host is a violation (`ref_order:no_host`).
4. UNMODELLED has an `Unmodelled` payload and non-empty `raw`.
5. CONTINUOUS has a `Modification` payload. CREATE_TRIGGER has a `SubAbility` payload and no duration.
6. `duration` is set only on CONTINUOUS, on EXILE with UNTIL_LEAVES, or on PLAYER_COUNTERS.
7. Every `residue` code is in `RESIDUE_CODES`.
8. `hash(card_effects)` succeeds for every template. No mutable object is reachable.

---

## 3. The grammar algorithm (`engine/effect_grammar/`, L0–L5)

**Entry points.**
- `parse_template(template, facts) -> CardEffects`.
- `parse_face(text, facts) -> Tuple[AbilityEffects, ...]`.
- `parse_effects(text, host=HostKind.SPELL) -> Tuple[EffectSpec, ...]`.
- `parse_text_effects(oracle) -> CardEffects`.
- `printed_span(oracle, facts, face, host_index, span) -> str`: recomputes the L0 offset map for load-time views (A40).
- `clear_caches()`.

**Facts.** `facts = (names, type_class, is_spell, is_legendary, is_planeswalker, has_x_cost, keywords702)`. `card_database` builds them from entry data:
- `names`: the full name, faceName, both halves of "A // B", and the legendary short name;
- `type_class`: the face's card types;
- `keywords702`: the face's MTGJSON `keywords` ∩ the CR 702 table.

The parse is a pure function of `(text, facts)`.

**L0, normalise** (`normalize.py`). The steps run in this order:
1. Apply `oracle_parser.strip_reminder_text`, recording each removed reminder span per paragraph (A4).
2. Mask `named <Name>` spans into `⟨Nk⟩`.
3. Replace self-forms with `~`, longest first, word-bounded on printed case:
   - the full name, the face names, and "this <noun>" for the object nouns in `engine.effect_grammar.sub.SELF_NOUNS` (creature, artifact, enchantment, land, planeswalker, permanent, battle, spell, card, equipment, aura, vehicle, token). "this ability" names the ability (CR 113.1) and stays;
   - the legendary short name, gated as before;
   - on planeswalker and legendary-character faces, pronouns by case (A9): object him/her → `~`, possessive his/her → `~'s`, subject he/she → `~`, he's/she's → `~ is`.

   A name that collides with a lexicon word is rewritten only where the lexicon cannot read it as that word, and never at clause start.
4. Mask each top-level double-quoted span into `⟨Qk⟩`, with nested single quotes under the A10 delimiter rule to depth 3.
5. Unify dashes, unify curly apostrophes (U+2019) to `'`, collapse whitespace and lowercase for matching. The steps above keep breakpoints `(norm_offset, printed_offset)` for the duration of the call. The map is never stored.

**L1, structure** (`structure.py`). Paragraphs are split with the `oracle_clauses.split_abilities` semantics. Each paragraph is classified with first-match precedence:
1. **LOYALTY.** `[±N]:` or `[±X]:`, using the grammar's own loyalty-line pattern, a superset of `_LOYALTY_LINE_PATTERN` (A12). The cost comes from the printed span; `loyalty_slot` comes from the shared slot function, which gives an X line none (A12).
2. **CHAPTER.** `^[ivx]+(, [ivx]+)* —`.
3. **KEYWORD** (A1). A list of CR 702 keyword abilities in any of the A1 forms. Its KeywordSpecs carry cost snapshots parsed from the printed span. This rule runs before the ability-word strip, so "Flashback—" and "Escape—" are never labels.
4. **Ability-word strip.** A `'<ability word> — '` prefix is recorded as `label` (CR 207.2c), and classification continues on the remainder. `channel` also sets `from_zone='hand'`.
5. **ALTERNATIVE_COST** (A2).
6. **Modal** (A4). A header in the text, or Tiered/Spree via face keywords or the removed reminder. The host kind is the header's own kind; MODE children carry `mode_index` and `mode_cost`; `mode_group=1` goes on every requirement in the block.
7. **Delay-prefixed paragraph** (A5). On an instant or sorcery it becomes a `CREATE_TRIGGER(DELAYED)` spec at its position in the SPELL host. On a permanent it is `UNMODELLED(STRUCTURE)`.
8. **TRIGGERED.** `^(when|whenever|at)\b`:
   - the head runs to the first depth-0 comma after which the remainder parses as a sentence;
   - `event_hints` is the tuple of every disjunct (A11);
   - a leading `if <cond>,` becomes the intervening-if;
   - `once_each_turn` and `frequency_raw` are recorded as before;
   - the `mana_ability` flag is set per A6.
9. **ACTIVATED.** A colon at quote depth 0 before the first sentence end:
   - `cost = freeze_cost(parse_activation_cost(printed head))` (A7);
   - restrictions come from `split_activation_riders` on the printed body;
   - `cost_modifiers` absorbs "This ability costs …" (A8);
   - `activation_index` follows the `parse_activated_abilities` ordinal rule, which skips lines holding a quote;
   - the host becomes MANA_ABILITY under A6.
10. **ADDITIONAL_COST.** `as an additional cost …`.
11. **Spell statics on any face** (A3). These become `STATIC(from_zone='stack')`.
12. **REPLACEMENT.** As before, with one `UNMODELLED(REPLACEMENT)` spec.
13. **UNKNOWN.** Level-up, class levels and d20 tables, with `UNMODELLED(STRUCTURE)`.
14. **Anything else.** SPELL on an instant or sorcery face, STATIC otherwise.

**F1 merge.** SPELL-classified paragraphs of an instant or sorcery face merge into one SPELL host in printed order. Label-prefixed paragraphs and A5 delayed specs merge as well. Hosts from rules 1–5 and 9–11 never merge.

**L2, sentence frames** (`clauses.py`; L1 stays in `structure.py`). A sentence ends at a `.` at quote depth 0. Each sentence becomes a Frame by consuming leading and trailing phrases.

- **Leading:**
  - connectives, applying to the whole sentence (A14): `then`; `if you do|if they do|if a player does`; `if you don't|if they don't|if no one does`; `if you don't <VP>,`; `otherwise`. `when you do[, if <COND>]` opens a reflexive sub-ability;
  - `if <COND>,`;
  - `if <COND>, instead`, and a bare `instead` (A15);
  - kicked frames: `if this spell|it was kicked,`;
  - duration prefixes;
  - delay prefixes, which open a delayed sub-ability (A30);
  - `for each <Q>,`, which becomes a FOR_EACH amount on the counted verb unless the body is anaphoric to the element (A16).
- **Trailing:**
  - durations;
  - ` instead[ if <COND>]`;
  - ` instead of putting it into <zone>` (A15, a destination override);
  - ` unless <PLAYER> pays <COST>` (cost parsed from the printed span);
  - ` if <COND>`;
  - `, where x is <QUANTITY>`.
- **Absorbed riders** (a closed list):
  - "can't be regenerated" becomes `no_regeneration` (CR 701.15);
  - "it's still a land" goes into the type-change Modification;
  - "~ can't be countered" becomes the `uncounterable` flag;
  - "Activate only …" becomes restrictions;
  - "Spend this mana only …" becomes `ManaSpec.restriction`;
  - "This ability costs … to activate" becomes `cost_modifiers` (A8);
  - "The flashback cost is equal to its mana cost" becomes `KeywordSpec.cost_rule` (A8);
  - "If <ref> is <verb>ed this way," before an instead-of form is consumed into the dest override (A15).

**L3, clauses** (`clauses.py`). The frame body is split at depth 0 on:
- `, then `;
- `; `;
- `, and <lemma>` and ` and <lemma>`;
- `, <lemma>`, lemma-gated, only when the text before the comma holds its own verb, and never inside a type list (A13);
- gapped ` and <count|REST NP> <destination PP>` after a put or return clause (A13);
- ` and <AMOUNT> damage to ` (the elliptical recipient);
- ` and each <FILTER>` after a DAMAGE recipient (A17).

Each of the last three yields a sibling in the same `group`. A clause with no subject before its lemma inherits the previous clause's subject or actor.

**L4, pattern cascade** (`lexicon.py`, `patterns.py`, `sub/*`). As before: participant sub-grammar, lemma buckets, bounded slot regexes, typed sub-grammar results, `UNMODELLED(deepest failure)`. The rows are verb families, not per-phrase regexes (section 4, "Pattern table").

**Spine vocabulary (L2-L4 review, 2026-10-01).** The spine keeps no participant vocabulary of its own: the subject of a connective or opener ("if <player> do(es)", "if <player> don't <VP>", "when <player> do(es)"), of a coordinated clause ("..., and <subject> <verb>") and of an absorbed rider ("<object> can't be regenerated") is whatever the participant (or target) leaf reads. Rules the review pinned, each with a rule-phrased test: a where-X definition ends where its expression ends, and a clause printed after it is split like any other (the definition is a hole in the frame body); a leading "if ... would ..." is tested before "this way" and refused as `REPLACEMENT`; "if <subject> <VP> this way," is the A14 performed test only when the subject is a player, and an object-qualified result test is refused (`CONDITION`); only a "may" the clause prints makes it optional, never one inherited with the antecedent's subject (CR 608.2d; L5 nests the followers, A29); an unprinted sacrifice or discard choice belongs to the acting player (CR 701.21a, 701.8a); "target player's <zone>" as an object is one target requirement through the target leaf; control of a player is refused (CR 722), as is a "during ..." duration the duration leaf does not read; a verb's default zone applies only when no source ("from among them") is printed; an instead clause that restates only its amount inherits its recipient (A15).

**The leaf contract** (`sub/__init__.py`, pinned by `tests/test_effect_grammar_leaf_contract.py`). Every sub-grammar leaf follows one contract, so the spine calls every leaf the same way and the coverage invariant below can be checked uniformly:
- a slot parser takes `(host, span, *, lemma="")`: `host` is the whole normalised host text, `span` the slot inside it, and every returned span indexes `host`;
- it returns the one `SlotResult` (`value`, `unmodelled`, `span`, `rest_spans`, `flags`, `pending`, `amount`, `alternatives`, `object_span`); on failure `span` is the whole trimmed slot;
- the unconsumed rest is `rest_spans` into `host`, never a rewritten string, so a later leaf's spans compose with an earlier one's;
- `Unmodelled.lemma` is the caller's printed lemma, never a leaf default; `Unmodelled.detail` is `<leaf>.<code>[:<param>]` with `<code>` from the leaf's closed `DETAIL_CODES` and `<param>` one word; the census groups by `(stage, lemma, <leaf>.<code>)`;
- every leaf reads L0 output (the L0 steps above) and none re-normalises it;
- memo caches are bounded (`CACHE_SIZE`), every leaf has `clear_caches()`, and the package `clear_caches()` clears them all (tools call it after a pool pass; it does not touch the per-template `CardTemplate.effects` memos, which live as long as their template and are cleared by `set_effects(None)`);
- a leaf imports another only along `LEAF_EDGES`: destination reads entry counters through payload's counter parser (one count table, one kind vocabulary), and payload reads the duration boundary `DURATION_START` from duration (one duration table). `match_clause(text, host_class, has_x)` is memoised. Target slots follow section 5 (located parse, consumption and residue polarity); CardFilter slots require full consumption (A21).

**L5, link** (`link.py`). This runs once per host over the merged host text, in this order:
1. Assign `seq` in pre-order.
2. **Sub-abilities first (A30).** Each reflexive or delayed opener absorbs the rest of its sentence, then every later sentence of the ability:
   - A reflexive sub-ability absorbs all of them, up to the end of the ability or of the current mode (M8).
   - A delayed sub-ability absorbs a later sentence only when that sentence's refs or conditions bind to the delayed specs' results. If a sentence both binds to those results and to the immediate part, the result is `UNMODELLED(DELAY)`.

   Each sub host is linked recursively and owns its targets.
3. Collect each host's own targets in printed order, stamp `mode_group`, and set `target_slot`.
4. Bind pronouns and anaphors (section 7).
5. Nest connective children (A14), sentence-wide. `If you don't <VP>` attaches to the named spec's `otherwise`.
6. May-scope by dependency (A29).
7. Resolve `instead`: `replaces` with argument inheritance and `target_alts` (G9). Fold instead-of forms into `dest` (A15).
8. Resolve RESULT refs. Set `per_actor` for multi-player actors. REST and OTHER are resolution-time set differences (A28).
9. Parse each `⟨Qk⟩` recursively as a GRANTED host.
10. Mark last-known information (A27).
11. Run `validate_spec`; a linking failure becomes `UNMODELLED(REFERENCE)` for that clause only.

**Coverage invariant.** Every character span of an effect-bearing host is covered by one of: a spec span, a consumed connective, frame, label or rider token, or a sub-ability span. A pool-wide test enforces this.

**Traces**
- "Its controller may search their library for a basic land card, put that card onto the battlefield tapped, then shuffle." (after an EXILE of T0) gives:
  - `SEARCH(optional, actor=CONTROLLER_OF(Ref(TARGET,0), lki=True), filter=basic land)`;
  - `then` (flag `may_scope`) = `(MOVE(Ref(RESULT,search)) to the battlefield tapped, SHUFFLE)`.

  The split comes from A13, and `may_scope` from the RESULT dependency and the searched library (A29).
- A Fatal-Push-shaped spell gives:
  - s0 `DESTROY(target=T0, condition=OBJECT(ref=Ref(TARGET,0), mv<=2))` (rule 0);
  - s1 `DESTROY(ref=Ref(TARGET,0), condition=ALL_OF(OBJECT(Ref(TARGET,0)) mv<=4, HISTORY permanent_left), replaces=(0,), label 'revolt')`.
- "Counter target noncreature spell unless its controller pays {2}." gives `COUNTER(target=T0, condition=UNLESS(payer=CONTROLLER_OF(Ref(TARGET,0)), cost={2}))` (rule 0).
- "Counter target noncreature spell. If that spell is countered this way, exile it instead of putting it into its owner's graveyard." gives `COUNTER(target=T0, dest=Destination('exile', instead_of='graveyard'), flags={dest_override})`.
- "Whenever you attack, you may pay {E}{E}{E}. When you do, put two +1/+1 counters and a flying counter on target attacking creature. It becomes an Angel in addition to its other types." gives:
  - s0 `PAY(optional, CostSnapshot energy 3)`;
  - `then` = `(CREATE_TRIGGER(SubAbility(REFLEXIVE, host=H')),)`, where H' has specs `[PUT_COUNTERS(target=T0'), CONTINUOUS ADD_TYPES(ref=Ref(TARGET,0))]` and its own `targets=(T0',)`. The parent has no targets.
- "Return target legendary creature card from your graveyard to the battlefield. That creature gains haste. Exile it at the beginning of the next end step." gives:
  - s0 `MOVE(target=T0 graveyard, dest battlefield)`;
  - s1 `CONTINUOUS ADD_KEYWORDS haste (ref=Ref(RESULT,0), THIS_TURN)` (A25);
  - s2 `CREATE_TRIGGER(SubAbility(DELAYED, NEXT_END_STEP, host=[EXILE(ref=Ref(RESULT,0))]))`, whose binding is a Handle, so a blinked object is not exiled (A34).
- "Each player exiles all creature cards from their graveyard, then sacrifices all creatures they control, then puts all cards they exiled this way onto the battlefield." gives three specs with `actor=ALL_PLAYERS`, and s2 is `MOVE(ref=Ref(RESULT,0, per_actor=True))`. The dispatcher runs each spec as one simultaneous action (A33).

---

## 4. Verb lexicon (`lexicon.py`)

`VERB_LEXICON` maps each lemma and inflection to an entry with these fields:
- `lemma` (the printed lemma, passed to every sub-grammar leaf: the payload leaf builds A19 option prefixes and re-joins a consumed keyword-action lemma from it, and every leaf stamps it on its `Unmodelled`);
- `verb`;
- `family`;
- `roles`;
- `other_role`;
- `mod_kind`;
- `hostile`;
- `owner` (documentation only).

The participant is the scope; `filter` plus `amount` is the selection within it, chosen by `chooser`.

| Lemmas | Verb | Roles and slots | Executing owner (E_k) |
|---|---|---|---|
| destroy(s) | DESTROY | target / ref / subject FILTER (+ALL) | `card_effects._resolve_nonland_permanent_removal`, `_resolve_board_sweep` |
| exile(s) | EXILE | object; or player scope + CardFilter + amount; duration UNTIL_LEAVES | `GameState._exile_permanent`, `zone_manager.move_card` |
| sacrifice(s) | SACRIFICE | SELF/ref; or scope + filter + amount; chooser PARTICIPANT for "of their choice" | `activation.legal_sacrifice_victims` + `callbacks.choose_sacrifice`; sweep path |
| return(s), put(s) … to/onto/into; shuffle(s) <object> into <library> (A18) | MOVE | + dest; source zone from the object span | `clause_resolver.resolve_bounce`, `GameState.reanimate`, `zone_manager.move_card` / `zone_transfer`; `zone_transfer.move_simultaneously` for multi-actor moves (A33) |
| shuffle(s) (your/their) library | SHUFFLE | player | library shuffle owner |
| deal(s) … damage to | DAMAGE | principal = recipient; `other` = source | `damage.deal_damage` |
| fight(s) | FIGHT | principal B, other A | `damage.deal_damage` ×2 |
| lose(s) N life | LOSE_LIFE | player | `damage.lose_life` |
| gain(s) N life | GAIN_LIFE | player | `GameState.gain_life` |
| life total becomes / exchange | SET_LIFE / EXCHANGE_LIFE | player | none yet, so refused |
| draw(s) | DRAW | player | `GameState.draw_cards` |
| discard(s) | DISCARD | player + filter/amount/chooser, or ref | `GameState._force_discard` |
| mill(s) | MILL | player | `zone_manager.move_card` |
| scry, surveil | SCRY / SURVEIL | player | `GameState.scry` / `surveil` |
| look at | LOOK | produces RESULT | binding only |
| reveal(s) | REVEAL / REVEAL_UNTIL | produces RESULT | binding plus the legacy reveal log line |
| search(es) | SEARCH | player + filter + amount | `oracle_resolver._resolve_x_creature_tutor` / activation tutor / `land_manager` fetch |
| choose(s) | CHOOSE | target declaration, or a card from a set (`choose_cards`) | binding only |
| cast/play … without paying | CAST_FREE | ref | `cast_manager` free-cast path |
| create(s) | CREATE_TOKEN | amount + TokenSpec (+ `alternatives`) | `GameState.create_token` |
| put N counter(s) on / remove / move / double | PUT_ / REMOVE_ / MOVE_ / DOUBLE_COUNTERS | object + CounterSpec | `CardInstance.add_plus_counters` / `adjust_counters` |
| get(s) {E} / poison / experience | PLAYER_COUNTERS | player + CounterSpec | `Player.add_energy` etc. |
| pay any amount of {E} / pay <cost> | PAY | CostSnapshot or Amount (ANY_NUMBER via `choose_amount`) | energy owner (E4), `mana_payment` (E6) |
| investigate, proliferate, amass, … | KEYWORD_ACTION | KeywordAction with expansion | the expansion's owners |
| gets ±N/±N | CONTINUOUS MODIFY_PT | target / subject / ref / MEMBER-conditional | `continuous_effects.register_effect`, `create_pump_spell_effect` |
| gain(s)/has/have <kw>; gains your choice of <kw> or <kw> | CONTINUOUS ADD_KEYWORDS (+ `alternatives`) | | same |
| gain(s)/has ⟨Q⟩ | CONTINUOUS GRANT_ABILITY | | refused until E5 |
| lose(s) <kw> / all abilities | REMOVE_KEYWORDS / REMOVE_ALL_ABILITIES | | |
| has base P/T, becomes/is a | SET_BASE_PT / SET_TYPES / ADD_TYPES / SET_COLORS | | |
| switch P/T | SWITCH_PT | | refused |
| can't <action> | PROHIBIT | | |
| attacks/blocks each combat if able | REQUIRE | | refused until wired |
| as though it had flash / may play … from | PERMIT | | |
| can't … more than N | LIMIT | | |
| cost(s) {N} less/more (static host only) | COST_DELTA | | |
| prevent | PREVENT_DAMAGE | | |
| gain(s) control of | SET_CONTROLLER | | refused |
| tap / untap | TAP / UNTAP | object | `CardInstance.tap` / `untap` |
| transform, attach | TRANSFORM / ATTACH | | existing owners |
| counter target <spell> | COUNTER | stack target, UNLESS condition, dest override | `spell_resolution` counter path |
| add <mana> | ADD_MANA | ManaSpec | mana pool |
| copy, change the target | COPY / CHANGE_TARGETS | | none yet |
| get(s) an emblem with ⟨Q⟩ | CREATE_EMBLEM | Granted | `planeswalker_manager` emblem path |
| take an extra turn, end the turn, skip | EXTRA_TURN / END_TURN / SKIP | | `GameState.end_the_turn` |
| (frame) when you do / at the beginning of the next … | CREATE_TRIGGER | SubAbility | `delayed_triggers` registration; reflexive queue |

The action vocabulary for PROHIBIT/PERMIT/REQUIRE is unchanged. Recognised-but-unsupported lemmas are unchanged: vote, roll, flip, venture, "the ring tempts you", manifest, cloak, phase out, regenerate, win/lose the game, clash, learn, villainous choice, "separate into piles", "name a card".

**Disambiguation.** Bucket order is table order, most specific first:
- `shuffle`: `<object> into <library>` (MOVE), then `(your|their) library` (SHUFFLE).
- `put`: counters on …, then onto the battlefield, then on top/bottom of … library, then into … hand/graveyard.
- `gain`: N life, then control of, then ⟨Q⟩, then your choice of <kw>, then keywords.
- `lose`: N life, then all abilities, then keywords.
- `get`: ±N/±N, then an emblem, then {E} / poison.
- `return`: the source zone comes from the object span, never from verb plus destination.

**Pattern table and class-size rule.** As built (E0 step 11), the pattern table is nine verb-family rows in `patterns.ROWS`, disjoint by verb, so no row shadows another: each row is the slot skeleton of one family, and the slot phrases (targets, participants, filters, amounts, destinations, durations, payloads) are the leaves' tables, not row regexes. This replaces the planned ~200 per-phrase rows, which would have re-implemented leaf tables in the spine. The class-size rule applies per row: at least ten typed witness clauses in the pool, pinned by `tests/test_effect_grammar_patterns_pool.py`. Witnesses (pool pass, 2026-10-01, after the L2-L4 review): continuous 7,810; object 5,500; actor_amount 5,201; payload 4,810; move 3,579; counters 1,935; damage 1,867; fixed 815; search 578. A verb with no row is `UNMODELLED(CLAUSE)` `patterns.no_row:<verb>`.

---

## 5. Targets, subjects and choices

**TargetRequirement production.** `target_solver` is the single owner. For a slot that contains a counted "target" word, `sub/target.py` calls `target_solver.parse_spans(clause_text)`. Spans come from `parse()`'s own placement (`parse_located`) mapped through the singularisation offset map (A20). The grammar keeps the requirements whose span lies inside the slot, unmodified. It sets only `mode_group=1` inside modal blocks. A pool test asserts that every other field equals the solver's output, and that `parse()` is unchanged.

**Acceptance.**
- **(a) Count.** Target words minus noun uses (F11) must equal the number of requirements in the slot. None found gives `UNMODELLED(TARGET)`; a different count gives `UNMODELLED(TARGET_COUNT)`.
- **(b) Consumption and residue (A21).** The NP feature reader must consume every token of the slot. A feature the requirement does not carry becomes a residue code with a polarity:

  | Code | Polarity |
  |---|---|
  | `target.scope:opponent` | WIDENING |
  | `target.scope:not_you` | WIDENING |
  | `target.exclude_source` | WIDENING |
  | `target.keyword:<kw>` | WIDENING |
  | `target.state:<s>` | WIDENING |
  | `target.color:<c>` | WIDENING |
  | `target.colored` | WIDENING |
  | `target.nontoken` | WIDENING |
  | `target.historic` | WIDENING |
  | `target.stat:<power/toughness>` | WIDENING |
  | `target.single_graveyard` | WIDENING |
  | `target.dependent_controller` | WIDENING |
  | `target.total_mv` | WIDENING |
  | `target.conjunctive_types` | WIDENING |
  | `target.union:<type>` | NARROWING |
  | `target.zone_union` | UNPARSED |
  | `target.unparsed` | UNPARSED |

  UNPARSED codes are never tolerable. Tolerance by polarity is described in F4 and M4.
- **(c) Solver gaps.** "one or two targets" and other forms the solver returns nothing for give `UNMODELLED(TARGET)`. A `target_solver` feature commit fixes them (Ral, Leyline Prodigy −2).

**Whole-oracle callers are untouched.** The `cast_targets` pseudo-field (G15) reports differences, including sub-ability targets that legacy counts as cast-time targets (A30). They are fixed when cast legality moves onto `AbilityEffects.targets` (E7).

**Legacy target adapter (A36).** `effect_resolver.chosen_from_legacy(ability, item_targets)` maps the legacy flat `item.targets` (whole-oracle category order, −1 face sentinel) onto per-slot tuples:
1. `parse_located(whole oracle)` gives each legacy index a printed position;
2. each position is matched to the host slot whose span contains it;
3. the face sentinel maps to the player slot's controller-chosen face.

A pool test runs it on every SPELL host whose `parse()` order differs from printed order.

**Subjects.** They are `effect_model.Selector` with `player=None`, bound by the dispatcher.
- Player sets are unchanged.
- Object groups become `CardFilter` and then `as_selector()`.
- A FILTER subject is executable only for entries in `SUPPORTED_FILTER_VALUES` or `EXECUTOR_FILTER_KEYS[verb]` (A22). For "creatures you control", `controller='you'` is not supported today, so the spec is typed but non-executable until E5 extends `covers_object`.
- CR 611.2c against 611.3a is unchanged: resolved hosts lock the affected set; STATIC hosts re-evaluate.

**Untargeted choices.** Unchanged: scope + filter + amount + chooser (CR 115.1, 701.21a), plus full CardFilter consumption (A21).

**Actor against principal.** Unchanged. The one addition: for a CONTINUOUS spec over a quantified subject, a condition on each member is `Condition(OBJECT, ref=Ref(MEMBER))` (A23). `can_execute` refuses MEMBER conditions until an executor evaluates them per member (E5).

---

## 6. Amounts, quantities, conditions, durations and delays

**Amount.** As before, with these additions:
- "any amount of" gives ANY_NUMBER (A19);
- a leading "for each <Q>," with no element anaphor gives `FOR_EACH(Q)` on the counted verb (A16);
- a loyalty X is `Amount(X, n=±1)` (A12);
- X is bound from the loyalty cost paid, from `{X}` in a mana cost, or from `where X is`; otherwise the clause is `UNMODELLED(AMOUNT)`.

**Quantity.** As before, with two additions:
- `RESULT_SIZE(ref, filter)` counts a filtered result: "each nonland card discarded this way".
- `COUNTERS_ON`, `POWER` and similar on `Ref(SELF, lki=True)` read last-known information whenever the source left as part of the cost (A27, CR 608.2h).

An unknown phrase makes the clause `UNMODELLED(QUANTITY)`, never zero.

**Condition.** The closed predicate table is unchanged, and a pronoun inside a condition binds by rule 0 (A23). These are never conditions:
- `instead`, which sets `replaces`;
- performed-gating, which is structural;
- "countered this way", which is a dest override;
- trigger-side filters, which stay in `TriggerHead.frequency_raw`;
- "When you do, if …", which is the sub-ability head's `intervening_if` (F9).

An unknown condition gives `UNMODELLED(CONDITION)`.

**Duration.** Unchanged (F6). A duration on EXILE (UNTIL_LEAVES) makes the Tier A ETB-removal derivation reject the host (A39). "for the rest of the game" is PERMANENT (CR 611.2a: the effect has no end); "this combat" is `UNMODELLED(DURATION)`. "this turn" is history only when the last history frame or event before it (condition, quantity, relative clause, "that <verb>ed") is not followed by the main predicate (can't, can, may, must, gets, gains, loses): in "creatures dealt damage this way can't block this turn" the phrase closes the prohibition. A "this turn" inside a delay phrase belongs to the delay.

**Delay** (CR 603.7). A delay is not a spec field any more. A delay prefix or suffix opens a `CREATE_TRIGGER(DELAYED, timing)` sub-ability (A30), with timing from the reused phrase table. "at end of combat" is still `UNMODELLED(DELAY)`.

---

## 7. References, links and riders

**Reference surface forms.** As before, with these changes:
- `~'s <noun>` is OWNER_OF, CONTROLLER_OF or a possessive of SELF (A9).
- "the exiled card(s)" follows A26.
- `Ref(MEMBER)` is the member of the spec's own quantified subject (A23).

**Pronoun precedence.** The first rule that yields exactly one compatible antecedent wins; compatibility checks noun and number.

0. **Own frame (A23).** A pronoun inside a frame modifier of the spec being built (if, unless, equal to, with, where, as long as) binds to that spec's principal: `Ref(TARGET,k)`, a Ref principal, or `Ref(MEMBER)` for a quantified subject.
1. **Same sentence (A24, A25).** The nearest earlier mention in the same sentence. A mention is any participant slot of an earlier spec: principal (target, subject, SELF/ATTACHED/EVENT_OBJECT/LINKED ref), `other`, `actor`, or a condition subject.
   - A mention that a zone-changing spec moved is represented by that spec's RESULT, which is the new object (CR 400.7). TARGET and RESULT of one spec are one candidate.
   - The noun "card" prefers non-battlefield results.
2. **Earlier sentences.** The same search over earlier sentences of the host, nearest first. It crosses SPELL paragraphs (F1). A sub-ability searches its own text, then its parent's.
3. **Host antecedent:**
   - self-only trigger head gives SELF;
   - other-only object head gives EVENT_OBJECT;
   - mixed "~ or another …" head gives EVENT_OBJECT, which is `~` when `~` triggered (M2);
   - player head gives EVENT_PLAYER;
   - an ACTIVATED or STATIC "it" whose noun fits the card gives SELF.

   SPELL hosts have no host antecedent.
4. **Otherwise** `UNMODELLED(REFERENCE)`. Two equally near candidates not related by A25 give `UNMODELLED(REFERENCE, 'ambiguous')`.

**"That player".** It binds to the nearest target player, then a single-player subject, then CONTROLLER_OF the nearest object antecedent (lki when that object has left), then EVENT_PLAYER. A refused (UNMODELLED) clause's result is an object set of unknown kind, never a player candidate; a number-only actor pronoun ("they") binds the nearest player mention first -- a target player, or a multi-player subject read per player (CR 101.4) -- and with only a refused result left it is `UNMODELLED(REFERENCE, 'unbound')` (L5 review, 2026-10-01).

**Links.**
- `, then` and a sentence-initial `Then` are text order only (CR 608.2c).
- "If you do" / "If they do" nest every clause of the sentence into `prev.then` with flag `if_you_do` (A14).
- "If you don't <VP>" and "Otherwise" nest into the named spec's `otherwise`.
- "When you do[, if C]" opens a REFLEXIVE sub-ability (A30) placed in `prev.then`, with head intervening-if C.
- May-scope follows the dependency rule (A29). "Performed" is defined per verb: SEARCH is performed when the search happened, even if it found nothing.
- `instead` follows G9 and A15. One `instead` may replace a whole group.
- The instead-of form is a dest override.
- `unless` is `Condition(UNLESS)`.
- The elliptical recipient and recipient unions are group siblings.

**REST and OTHER (A28).** They are computed at resolution as set differences over the producing RESULT, after every consuming spec, including replacing siblings, has run.

**Absorbed riders** (a closed list): `no_regeneration`, retained types, `uncounterable`, `reveal`, `at_random`, activation restrictions, mana spend restrictions, `once_each_turn`, activation `cost_modifiers`, flashback `cost_rule`, and the "countered this way" dest override.

---

## 8. Carriers

- **Instants and sorceries.** One merged SPELL host holding only resolution text. It includes delayed sub-abilities (A5) and never keyword, alternative-cost, additional-cost or spell-static lines (A1–A3).
- **Modal.** As before, plus tiered and spree detection and mode costs (A4), and modal TRIGGERED headers.
- **Kicker.** A KEYWORD host holds `KeywordSpec('kicker', cost)`. Payoff specs carry `CAST_FACT kicked` (plus `replaces`). A SELF_CAST trigger with a kicked intervening-if is TRIGGERED. The `kicked_clause` view is the printed span (A40).
- **Alternative and additional costs.** ALTERNATIVE_COST hosts (A2) and ADDITIONAL_COST hosts hold no specs.
- **Channel.** An ACTIVATED host with `from_zone='hand'`, a printed-span cost and `cost_modifiers` (A8). The `channel_clause` view is the printed host text.
- **Loyalty.** LOYALTY hosts per face (A12). `loyalty_cost` is an Amount. Clause templates hold slices of the face the engine activates and are never parsed.
- **Triggered.** TRIGGERED hosts with `event_hints` tuples (A11). Triggered mana abilities carry flag `mana_ability` (A6). Cast triggers are resolved by host index, including through `resolve_self_cast_trigger` (A41).
- **Activated and mana.** ACTIVATED and MANA_ABILITY hosts under the CR 605.1a rule (A6), with printed-span costs (A7).
- **Sub-abilities.** REFLEXIVE and DELAYED hosts nested in CREATE_TRIGGER payloads (A30). They are walked by `CardEffects.walk(include_sub=True)` and counted by the census.
- **Saga chapters, statics, replacement statics, granted abilities.** As before. Spell statics are lifted on every face (A3).
- **Back faces.** Face 1 is parsed before back-face loyalty typing. Face-1 LOYALTY hosts are Tier A against `back_face_loyalty_abilities`; other face-1 hosts are growth.

---

## 9. UNMODELLED and residue census

As before, with these changes:
- The walk includes sub-ability hosts.
- Residue rows are grouped by code and polarity.
- `tools/effect_census.py` also reports:
  - the `may_scope` nestings, one row per shape, for review before E3 executes them (A29);
  - the sub-ability shapes (REFLEXIVE and DELAYED, with and without intervening-if);
  - the keyword-line classifications, so a keyword line that falls to SPELL on any face is visible;
  - the `cost_modifiers` absorptions.
- `tools/effect_census_baseline.json` pins totals per stage, per residue code, per polarity, and on deck cards, and the typed shares (pool, per host kind, deck cards). `--check` fails on growth of a total or a fall of a share. `--update` also generates `docs/design/effect_grammar_census.md` from that baseline (landed 2026-10-02; the name replaces the earlier `effect_unmodelled_baseline.json`).
- The runtime census (`rules_audit.census('608.2/unmodelled_effect', …)`) is unchanged.

**Expected E0 magnitudes.** These are estimates, replaced by the first census:
- TARGET about 1.2k–1.7k clauses;
- residue about 500 WIDENING, plus NARROWING and UNPARSED codes;
- REPLACEMENT about 450–500;
- RECOGNIZED_UNSUPPORTED a few hundred;
- ITERATION falls sharply under A16;
- typed coverage about 75–85% of effect clauses, now that A1, A3, A13 and A23 are in.

---

## 10. The equivalence tool, gate parity and the per-host harness

**Derivations** (`engine/effect_views.py`). `DERIVATIONS` holds one record per legacy field with these fields:
- `field`, `family`, `scope`, `tier`, `estep`;
- `derive`;
- `strict` (the family's strict shape predicate, used by the switched handler; A38);
- `legacy`, `compare`, `partial`.

Named predicates, counted by ratchet (c), come in two kinds:
- `_legacy_*` quirk predicates;
- `_legacy_domain_*` masks (A39).

`NON_EFFECT_FIELDS` maps every other field to a reason.

**Carriers compared:**
- template attributes, `modes[i]`, `ActivatedAbility` fields and `LoyaltyAbility` fields (face 0 against `loyalty_abilities`, face 1 against `back_face_loyalty_abilities`), and the loyalty clause templates;
- runtime shapes (G4) on a game-less `ClauseContext` for the SPELL text, every mode, and the kicked, channel and head-stripped cast-trigger overrides (A41);
- the description interpreter (G5, third interpreter);
- the `OracleTextParser` → `OracleEffect` → `_build_abilities` pipeline (`Ability.description`, `is_counterspell`) and `classify_card_role` (fourth interpreter, A41): the verb set and role each derives, against the spec verbs;
- `cast_targets` (G15).

**Known legacy-side disagreements** (seed rows for `tools/effect_spec_equivalence_allowlist.json`, step 18). The grammar is the rules-correct side; the legacy fix is a separate `target_solver` unit, not E0:
- "target nonland permanent card from <zone>" is a battlefield requirement in `target_solver._PERMANENT_PATTERN`; the destination leaf reads the graveyard (CR 115.1: the target is the card in that zone). "target permanent card from ..." without "nonland" is already a graveyard requirement. 9 of the 19 "return target ..." pool disagreements are this shape.
- "target spell or creature": legacy `reqs[0]` is the stack only; the grammar gives the union {stack, battlefield} (A21 `target.zone_union`).
- "nonland permanent or suspended card": the grammar gives {battlefield, exile} (CR 702.62a).
- a graveyard-or-exile object: the grammar gives {graveyard, exile}.

Further seed rows from the E0 integration review (2026-10-01; legacy-wrong, grammar rules-correct unless noted):
- **Printed counts above ten.** `target_solver._NUMBER_WORDS` stops at ten, so "up to eleven target creatures" is one requirement with `count_max` 1. The grammar reads the count from the leaves' one count table (`sub.NUMBER_WORDS`, through twenty-nine) and refuses the slot as `target.count_unread:<word>` rather than editing the owner's requirement.
- **Token subtypes, keyword scope and slot identity** (`oracle_parser.parse_token_spec`). Legacy keeps only the last creature subtype ('rogue' for "faerie rogue", 'spawn' for "eldrazi spawn"; CR 111.4 gives a token every printed subtype), lets its keyword window run into other sentences ('flying' from "Vehicles you control have flying."; 'indestructible' granted to a target, not the token), and can read a different token than the create slot (amass's Army instead of the Treasure). 250 of the 1,120 single-create slots both sides type disagree; no case was found where legacy was right. The grammar also types "land creature" token types that legacy drops.
- **Ritual mana** (`CardTemplate.ritual_mana`). `parse_ritual_mana` counts the activation cost's pips with the produced mana ('{R}, Sacrifice: Add {R}{R}{R}' -> ('R', 4); grammar ManaSpec {R}x3), reads "add an additional {C}" as two (grammar {C}x1), and collapses multi-colour adds to one colour. 74 of 101 coloured legacy rituals agree. `ritual_mana` is not the reference value for a ManaSpec on a cost-bearing mana ability.
- **Activation tutor filter** (`parse_activation_tutor`). A relative clause becomes subtypes ("card with the same name as that ..." -> subtypes ['card', 'with', ...]); the grammar refuses it (`filter.unparsed:with`). The other 73 tutor filters and all 75 destinations agree.
- **Keyword-scoped cost reductions** (`CardTemplate.cost_reduction_rule`). "Equip / Dash / Unlock costs you pay cost {N} less" and "Plotting cards from your hand costs {2} less" are stored as `{'target': 'all', ...}`, a reduction of every spell; rules-correct they are scoped to that keyword's cost or special action (CR 601.2f, 118.7). The grammar refuses them (`payload.cost_delta_subject`). Pool class: 5 faces.

Lines checked and found in full agreement: loot draw/discard (174), put-counter kind and amount (135), pump P/T (290), direct-damage amount (79), the soft-counter condition, and delayed timing (102 agree, grammar strictly broader).

**Diff classes.** As before (REMINDER_TEXT, UNMODELLED_CLAUSE, RESIDUE_WIDENING, LEGACY_* quirk classes, DERIVED_COVERAGE_GROWTH, SEMANTIC_FIX, UNEXPLAINED), plus three:
- `RESIDUE_NARROWING`: the legacy handler typed the same narrowed requirement;
- `MASKED_GROWTH`: derived growth hidden by a `_legacy_domain_*` mask, so the field value equals legacy;
- `SUB_ABILITY_SCOPE`: legacy flattened a reflexive or delayed sub-ability.

**Gate parity and verb closure (A38).**
- `--closure` lists each handler (clause_resolver, planeswalker_manager branches, ETB carriers, `resolve_self_cast_trigger`, activation effect kinds) with the verb set and sub-ability kinds of every host its legacy gate accepts, split into registered-deck mainboard, sideboard and pool. For each host it gives the earliest step at which all of those verbs have executors, and whether the host matches the family's strict view.
- `--gate-parity` fails if a host the legacy gate accepts is on the new path without being strict, executable and harness-identical. Every remaining host counts toward ratchet (f).

**Per-host resolution harness (A42)** (`tools/host_resolution_equivalence.py`).
- **Input.** A host and a fixed synthetic board family. There are six boards: empty; opponent creatures of mixed mana value and keywords; own creatures; lands and nonbasics; a stack with a spell; graveyards with creature cards.
- **What it does.** For each board and seed it deep-copies the game, resolves once through the legacy apply, with the legacy targets one deterministic rule chooses on that board from the host's requirements, and once through `resolve_ability` (the same targets mapped through `chosen_from_legacy`, callbacks deterministic), and compares:
  - the canonical game-state digest (zones, life, counters, damage, continuous effects, delayed triggers);
  - the game-log bytes.
- **Scope.** Registered-deck mainboard and sideboard hosts run in the family's in-suite test. The pool runs as a tool before each switch commit.
- **In E0.** It runs legacy against legacy to prove itself deterministic.

**Tool usage.** `python tools/effect_spec_equivalence.py` prints the per-field table and exits 1 in any of these cases:
- UNEXPLAINED grows;
- AGREE falls;
- an allowlist row is stale;
- a SEMANTIC_FIX row has no test;
- a snapshot mismatches;
- a switched field differs from legacy on any template (A39);
- gate parity fails, or its legacy-fallback count grows (`--check` always runs the closure; a pair is switched when its legacy apply -- clause_resolver handler, planeswalker branch, activation path, ETB carrier or `resolve_self_cast_trigger` -- reaches `resolve_ability` directly or through any helper on its static call graph, so a carrier never stays on fallback by a constant);
- on a full run, the baseline is stale: UNEXPLAINED fell, AGREE rose or the legacy-fallback count fell without `--update` in the same commit.

An allowlist row with `"always": true` classes every comparison of its cards, an equal one included. It exists for a legacy value that is no comparison: `pump_keyword_set_order` (class LEGACY_HASH_ORDER, 13 pool cards) covers `pump_spell_keyword` on pump spells whose "gains/has" window names two or more keywords, where `oracle_parser._pt_mod_keyword` returns the first member of a set literal found there, so the hash seed picks the keyword. Without the row the pinned counts moved by 3 between processes (follow-up review, 2026-10-02); the legacy fix changes game behaviour and is a legacy unit.

Flags: `--field/--list/--class/--decks/--patterns/--timing/--json/--update/--closure/--gate-parity`.

**Completeness (A41).** An AST scan finds every `template.<attr> = …` assignment in `engine/card_database.py` (193 today, 142 of them `parse_*` calls). It adds the lazy fields set in `CardTemplate.__post_init__`, every `ActivatedAbility` and `LoyaltyAbility` field, and the OracleTextParser outputs. Each must appear in DERIVATIONS or NON_EFFECT_FIELDS.

**Migration protocol for family commit E_k** (A38, A39):
1. **Executors.** The family's executors land (EXECUTORS, EXECUTOR_FILTER_KEYS), with fake-board unit tests.
2. **Tool gate.** The tool shows UNEXPLAINED = 0 for the family's Tier A fields. Every derived value equals legacy pool-wide after `_legacy_domain_*` masks.
3. **Prior fixes.** Every SEMANTIC_FIX row has already landed as its own commit.
4. **Snapshots.** Freeze `tools/effect_legacy_snapshots/<field>.json`.
5. **Fields become views.** `card_database` assigns `template.f = effect_views.f(template.effects)`. `parse_f` becomes the G3 adapter and is then deleted.
6. **Handlers switch in place (F7).** Each handler keeps its gate and position. Apply takes the new path for strict and executable hosts and falls back to legacy for the rest.
7. **Harness.** It shows zero divergence on every registered-deck MB and SB host that takes the new path, and on the pool run.
8. **Baselines.** Lower the `check_effect_parsers` baselines (a), (b), and (f) where they fell.
9. **Digest and suite.** `tools/seeded_game_digest.py --check` is byte-identical, and the full suite is green.
10. **Behaviour changes.** They follow as separate E_k.b commits, each with a failing test, a rules-audit invariant, and a Bo3 measurement when deck cards are affected:
    - mask removals;
    - single-owner retirements;
    - CR 608.2b re-checks.

### 10.1 Per-field derivation table

`H` = the host named in the Scope column. Derivations read specs only unless noted. Every Tier A derivation rejects hosts that carry a duration, a sub-ability, a head intervening-if or residue, unless the row says legacy reproduces it (A39).

| Field (scope) | Tier | Step | Derivation from `effects` |
|---|---|---|---|
| `direct_damage_data` (card) | A | E1 | The first face-0 SPELL host. Its only non-rider spec is DAMAGE with source SELF, a slot of types `{any}` or `{player}` and count 1, LITERAL n, and no condition, optional or then. Keyword and cost lines are separate hosts (A1), so flashback no longer affects it. `_legacy_prefix_window` and `_legacy_rider_tokens` reproduce the refusals. An INSTEAD sibling DAMAGE(LITERAL m) labelled delirium or metalcraft gives `upgrade_amount/upgrade_condition`. |
| `effective_direct_damage`, `ai.card_classes.burn_damage` | B | E1 | As before. |
| `ActivatedAbility.effect_kind == DAMAGE_ANY_TARGET` (+amount) | A | E1 | The ACTIVATED host at the same index, after dropping LOOK specs and unwrapping one `CREATE_TRIGGER(DELAYED)`, is exactly one DAMAGE(SELF, `{any}`, LITERAL n). |
| `LoyaltyAbility.effect_kind` DAMAGE / GAIN_LIFE_AND_DRAW (face 0 and face 1) | A | E1 | The field view reproduces legacy through `_legacy_loyalty_damage_word`, the case-sensitive "damage" anywhere, sub-abilities included. The strict view for the handler switch is exactly one top-level DAMAGE and no other spec. Ajani [0] (token plus reflexive damage) therefore stays on legacy fallback until its own E4.b behaviour change. |
| `tap_damage` | A | E1 | A land's MANA_ABILITY host (A6) with DAMAGE(recipient you, LITERAL n) gives n. `_legacy_tap_damage_one`. |
| `has_energy_damage_target` + runtime `_a_energy_damage` | A | E1 view, E4.b switch | View: CHOOSE(target creature or planeswalker), then PLAYER_COUNTERS(energy), then PAY(energy, ANY_NUMBER, optional), then DAMAGE(ref TARGET, THAT_MUCH(RESULT pay)). The switch is a behaviour change (M9). |
| `has_x_damage`; Tier B damage predicates | B | E1 | As before. |
| Tier C damage payloads (`ordinal_cast_trigger.effect`, `creature_dies_observer`, `attack_observer`, cycling watch, landfall, opponent-cast damage, enters-lifegain) | C | E1 payload, T head | As before. `landfall_first_life_gain` is RESOLUTION_ORDINAL(1). |
| `targeted_removal_data` | A | E2 | SPELL specs are exactly [s]: s.verb in {DESTROY, EXILE}, a battlefield slot whose types are one of the 9 removal typespecs, owner in {any, opponent}, no supertype or subtype, residue within tolerance, and no condition, optional, then, filter or duration. The flicker class is a SEMANTIC_FIX. Path to Exile (+SEARCH) is legacy fallback until E3. |
| `modes[i]['removal']` | A | E2 | The same rule over MODE host i. |
| `etb_targeted_removal_data` | A | E2 | A TRIGGERED host whose `event_hints == (SELF_ENTERS,)`, whose head has no intervening-if, and whose specs are exactly [DESTROY or EXILE with a typespec slot and an integer MV bound], with no duration. Wistfulness (intervening-if) and Leyline Binding (UNTIL_LEAVES) are masked to legacy None (A39). |
| `removal_mv_condition` | A | E2 | [s1 target with condition OBJECT(Ref(TARGET,0)) mv≤N; s2 ref TARGET 0, replaces s1, condition ALL_OF(OBJECT mv≤M, HISTORY permanent_left)]. |
| `board_sweep_data` | A | E2 | SPELL specs exactly [DESTROY subject FILTER types {creature}], `no_regeneration` allowed. |
| `bounce_target` | A | E2 | The first MOVE with dest hand and a battlefield slot. Leading `instead` siblings supply `target_alts` (Into the Flood Maw). A `target.zone_union` code gives UNMODELLED_CLAUSE (Sink into Stupor, legacy fallback). |
| `land_destruction_data` / `destroys_target_land` | A | E2 view, E3 switch | Exactly one DESTROY with slot types {land} or {artifact, land}, plus the listed riders. The riders need SEARCH, MOVE, SHUFFLE and DRAW, so the switch waits for E3. |
| `mass_graveyard_return`, `has_symmetric_reanimation` | A/B | E2 | As before; per-actor chains (Living End) execute through `zone_transfer.move_simultaneously` (A33). |
| `ActivatedAbility.graveyard_exile_data` | A | E2 | As before. |
| Loyalty RETURN_TO_HAND / TUCK_TARGET_INTO_LIBRARY / EMBLEM_EXILE_PERMANENT | A | E2 | As before, face-aware (A12). |
| Runtime `_reanimate_ability`, `_g_mass_reanimate`, `_bounce_shape`, `_resolve_mass_mode_clause`, `resolve_self_cast_trigger` bodies | A | E2 | Runtime-shape carrier (G4, A41). A reanimation with a haste grant (CONTINUOUS) stays legacy fallback until E5. |
| `etb_exile_returns_on_leave` | A | E2 | As before. |
| Tier B removal predicates | B | E2 | As before. |
| Replacement fields | deferred | ReplacementSpec | NON_EFFECT_FIELDS. |
| `loot_data` | A | E3 | As before, over serial-split clauses (A13). |
| `hand_attack_data` | A | E3 | As before. |
| `library_dig_data` | A | E3 | LOOK/REVEAL top N, then MOVE part ONE (filter, including `classes` such as historic) to hand, then MOVE REST (A28) to the bottom of the library or the graveyard. The kicked "instead" count is a replacing sibling. |
| `hand_refill` | A | E3 view, E6 switch | MOVE of the hand (and graveyard) to library shuffle (A18), DRAW N, [END_TURN if your_turn]. |
| `x_creature_tutor_data`, tutor kinds, `fetchland`, `is_land_sacrifice_tutor`, DRAW_N | A | E3 | SEARCH, then MOVE(RESULT), then SHUFFLE (A13). The cost comes from the printed span (A7). `self_shuffle_into_library` comes from MOVE(SELF, dest library/shuffle) (A18). |
| Runtime `_card_flow_effects`, `_impulse_count`, `_a_hand_attack` | A | E3 | Runtime-shape carrier. Impulse is legacy fallback until E5 (PERMIT plus the next-turn duration). |
| Tier B card-flow predicates; `cycling_variant_data` | B/C | E3 | As before. |
| Runtime `parse_token_spec` / `_token_clause` | A | E4 | CREATE_TOKEN with TokenSpec and `alternatives`. |
| Counter fields | A | E4 | As before. |
| Tier B/C token and counter fields | B/C | E4 / T | As before. |
| `pump_spell_*`, `team_pump_data`, `next_turn_effect`, restriction fields | A | E5 | As before. FILTER subjects need the E5 `covers_object` extension (A22). |
| Runtime `_combat_prevention_shape`, `_object_restriction_shape`, `attack_observer` apply | A | E5 | Runtime-shape carrier. |
| Equip/team grants, PUMP/ANIMATE_SELF_UEOT, GRANT_HASTE_TARGET, UNTAP_TARGET_PERMANENT, DRAW_AND_UNTAP_LANDS | A | E5 | As before. |
| `is_counterspell`, `counter_target_kind`, `counter_tax_amount`, `counter_upgrade_condition`, `counters_colorless_only` | A | E6 | COUNTER with a stack slot, UNLESS (payer by rule 0), dest override (A15) and INSTEAD sibling. |
| `ritual_mana`, `mana_units`, `sacrifice_mana_units`, `conditional_mana` | A | E6 | ADD_MANA with a ManaSpec. |
| `cost_reduction_rule`, `self_cost_reduction_*`, `domain_reduction` | A | E6 | STATIC(from_zone='stack') COST_DELTA on every face type (A3). |
| `kicked_clause`, `channel_clause` | A | E6 | Printed spans (A40). |
| `cast_targets` | A (report) | E6, then E7 | SPELL host targets (sub-ability targets excluded) against whole-oracle parse. |
| Description interpreter and OracleTextParser pipeline | A (report) | E7 | Per-verb reach; each family deletes its branch at zero reach. |
| `aura_mana_units`, `tap_for_mana_trigger` | C | E6 / T | Triggered mana abilities (A6). |

**Views landed (E0, 2026-10-02; `engine/effect_views.py`, no caller).** 146 `FieldDerivation` records cover every template-field row above, plus the Tier B AI predicates, the stage-T head predicates and `etb_return_land` (a bounce land's entry trigger, CR 603.6a: TRIGGERED(SELF_ENTERS) with a mandatory MOVE of a land you control to hand; Tier C, E2; equal to legacy on all 37 pool cards printing the phrase). The runtime rows (`effective_direct_damage`, `ai.card_classes.burn_damage`, the runtime-shape carriers, `cast_targets` as `target_solver.parse`, and the OracleTextParser pipeline) are not template fields; they are the 17 `RUNTIME_CARRIERS` entries, each at its row's step, and a test asserts every runtime row is listed and names a callable that exists. The remaining 115 legacy fields are named in `NON_EFFECT_FIELDS` with a reason. The completeness test covers every public field of `CardTemplate`, `ActivatedAbility` and `LoyaltyAbility` and every `template.<attr> =` assignment. Scoped carriers are keyed as `ActivatedAbility.effect_kind[KIND]`, `LoyaltyAbility.effect_kind[KIND]` (key `(face, slot)`) and `modes[removal]`.

Named predicates (ratchet (c)) number 15 today (`LEGACY_PREDICATES`):
- quirks: `_legacy_prefix_window`, `_legacy_rider_tokens`, `_legacy_loyalty_damage_word`, `_legacy_tap_damage_one`, `_legacy_when_head`, `_legacy_removal_scope_residue`, `_legacy_colorless_counter`, `_legacy_counter_kind`, `_legacy_draw_word_limit`, `_legacy_equip_grantable`, `_legacy_ritual_cost_pips`, `_legacy_basic_land_type_words`, `_legacy_channel_to_face_end`;
- masks: `_legacy_domain_etb_removal` (Wistfulness and Leyline Binding are its two registered-deck hits) and `_legacy_domain_single_kicker` (13 pool hits: and/or and non-mana kickers, another spell's kick).

`kicked_clause` and `channel_clause` equal legacy on the whole pool after the kicker mask. On registered-deck cards, every non-partial Tier A view equals legacy except 9 named (field, card) pairs. Each pair is pinned with the side that is wrong in `tests/test_effect_spec_equivalence_tool.py`. Pool-wide Tier A disagreement counts are the equivalence tool's to pin. At landing they ran from 0 (`removal_mv_condition`, `is_land_sacrifice_tutor`, `counter_upgrade_condition`, the cost-reduction views) to 233 (`ritual_mana`, whose legacy parser also reads reminder and granted text).

---

## 11. The dispatcher (`engine/effect_resolver.py`; skeleton in E0, no callers)

```python
@dataclass(frozen=True)
class Handle:                                   # CR 400.7: an object, not a card
    instance_id: int
    zone: str
    entry_seq: int                              # battlefield_entry_seq on the battlefield, zone-entry ordinal elsewhere
    lki: Optional[object] = None                # characteristics snapshot taken when it left (CR 608.2h)

@dataclass
class Resolution:                               # runtime binding; never reads oracle text
    game; source: Handle; controller: int
    ability: AbilityEffects
    chosen: Tuple[Tuple[Union[Handle, int], ...], ...]   # per slot of THIS host (never sub-ability slots)
    division: Dict[int, Tuple[int, ...]] = field(default_factory=dict)   # CR 601.2d
    x_value: int = 0
    event: Optional[object] = None
    cast_facts: FrozenSet[str] = frozenset()
    modes: Tuple[int, ...] = ()
    results: Dict[int, Dict[int, tuple]] = field(default_factory=dict)   # seq -> actor -> Handles/ints; {} if not performed
    performed: Dict[int, bool] = field(default_factory=dict)
    instead_holds: Dict[int, bool] = field(default_factory=dict)         # replacing seq -> evaluated once
    pending_reflexive: List[Tuple[SubAbility, "Snapshot"]] = field(default_factory=list)

Outcome = namedtuple('Outcome', 'performed result')   # result: actor -> tuple
Executor = Callable[[Resolution, EffectSpec, Tuple[int, ...]], Outcome]  # actors APNAP; ONE simultaneous action
EXECUTORS: Dict[Verb, Executor] = {}
EXECUTOR_FILTER_KEYS: Dict[Verb, FrozenSet[Tuple[str, Any]]] = {}
LEGACY_RESIDUE_TOLERATED: Dict[str, FrozenSet[str]] = {}               # family -> codes (F4, M4)

def can_execute(ability, family=None) -> bool:
    # every spec in walk(include_sub=True, excluding granted payload hosts):
    #   verb in EXECUTORS and not UNMODELLED; no Ref to an UNMODELLED spec;
    #   residue codes: none UNPARSED, all in LEGACY_RESIDUE_TOLERATED.get(family, ∅);
    #   duration kind in effect_model.CLOCKED_DURATIONS; mod_kind in APPLIED_MODKINDS;
    #   FILTER entries ⊆ SUPPORTED_FILTER_VALUES ∪ EXECUTOR_FILTER_KEYS[verb];
    #   no Ref(MEMBER) condition unless the verb's executor evaluates members;
    #   sub-ability hosts recursively executable

def resolve_ability(game, source, controller, ability, chosen, *, family=None, x_value=0, event=None,
                    cast_facts=frozenset(), modes=(), division=None) -> bool:
    if not can_execute(ability, family):          # never raises; the carrier falls back to its legacy apply (A37)
        return False
    ctx = Resolution(...)
    specs = ability.specs + tuple(s for i in modes for s in ability.modes[i].specs)
    by_victim = {v: r for r in specs for v in r.replaces}     # structural only; no condition read up front
    for s in specs:                                # CR 608.2c printed order
        if s.replaces:
            continue                               # a replacing spec runs only at its first victim's position
        r = by_victim.get(s.seq)
        if r is not None:
            if r.seq not in ctx.instead_holds:     # lazily, once: may read earlier results; UNLESS pays once
                ctx.instead_holds[r.seq] = holds(ctx, r.condition)
            if ctx.instead_holds[r.seq]:
                if r.seq not in ctx.performed:     # a group victim set runs the replacement once
                    _run(ctx, r, condition_checked=True)
                continue
        _run(ctx, s)
    for sub, snap in ctx.pending_reflexive:        # CR 603.12: inline after the parent until T2, then stack items
        resolve_sub_ability(game, sub, snap)       # chooses sub-host targets NOW, re-checks its intervening-if
    return any(ctx.performed.values())

def _run(ctx, s, *, condition_checked=False):
    if s.verb is Verb.CREATE_TRIGGER:
        sub, snap = s.payload, ctx.snapshot()      # Handles, never objects (A34)
        if sub.kind is SubAbilityKind.DELAYED:     # CR 603.7; 603.7c: a moved object is not acted on
            ctx.game.register_delayed_trigger(DelayedTrigger(timing=sub.timing, controller=ctx.controller,
                effect=partial(resolve_sub_ability, ctx.game, sub, snap), description=s.raw,
                created_turn=ctx.game.turn_number))
        else:
            ctx.pending_reflexive.append((sub, snap))
        ctx.performed[s.seq] = True; ctx.results[s.seq] = {}
        return
    if not condition_checked and s.condition is not None and not holds(ctx, s.condition):
        ctx.performed[s.seq] = False; ctx.results[s.seq] = {}
        return _branch(ctx, s.otherwise)
    if s.optional and not ctx.game.callbacks.choose_optional_effect(ctx, s):
        ctx.performed[s.seq] = False; ctx.results[s.seq] = {}          # THAT_MUCH of a declined spec = 0 (A35)
        return _branch(ctx, s.otherwise)
    actors = _actors_apnap(ctx, s)                 # choices in APNAP order (CR 101.4) ...
    out = EXECUTORS[s.verb](ctx, s, actors)        # ... then ONE simultaneous action through the owner (A33)
    ctx.results[s.seq] = out.result; ctx.performed[s.seq] = out.performed
    _branch(ctx, s.then if out.performed else s.otherwise)

def chosen_from_legacy(ability, item_targets) -> Tuple[Tuple[Union[Handle, int], ...], ...]: ...   # A36
```

**Binding helpers.** All delegate to existing owners:
- **Target slot.** Chosen handles are resolved to objects only if still in their zone with the same entry (A34).
  - Until the family's E_k.b commit, the executor passes them to the owner exactly as the legacy apply did, keeping the owner's legacy illegal-target semantics (A36).
  - The CR 608.2b re-check through `can_be_targeted` lands as that behaviour-change commit.
  - **An unbound slot is passed to the owner as None, and the owner's own picker decides.** `target_solver.choose_targets` is used only where the legacy apply used it.
- **Subject.** `dataclasses.replace(selector, player=controller)`; members come only from supported entries.
- **Ref.** Looked up in results, event, source or attachments. `per_actor` selects the actor's own result. MEMBER is bound per member by the executor. `lki=True` reads the Handle's LKI snapshot.
- **Amount.** Quantity evaluators, created by moving `clause_resolver._scaler_count` and `oracle_resolver._direct_damage_condition_met` into `engine/effect_conditions.py` in E1. Where the text asks for a choice, ANY_NUMBER, UP_TO and DIVIDED go through `choose_amount` and `choose_division` (A35).
- **Duration.** Bound with player and object as before.
- **Simultaneous zone moves.** Multi-actor MOVE, EXILE and SACRIFICE call one owner per spec, never one call per actor. In E2, `zone_transfer.move_simultaneously(game, moves, defer_etb=True)` is extracted from the legacy Living End phases 1–3 and keeps the legacy player-index physical order, so the E2 switch stays byte-identical (A33). Universal deferral is T2 (M5).

**Callbacks declared in E0, not wired.** `choose_optional_effect(ctx, spec)`, `choose_amount(ctx, spec, lo, hi, remaining_specs)`, `choose_cards(ctx, spec, pool, n)`, `choose_division(ctx, spec, slots, total)`. The default implementations raise `NotImplementedError`; nothing calls them in E0. AI-choice heuristics embedded in legacy handlers move into these callbacks unchanged at switch time:
- the reanimate max P+T pick;
- the energy spend `min(toughness remaining, energy)`, where `remaining_specs` lets the callback see the DAMAGE target.

**Carrier switch (F7, A37, A38).**
- Each switched handler keeps its gate and registry position.
- Its apply becomes: `if strict(ctx.ability) and can_execute(ctx.ability, family): resolve_ability(...) else legacy_apply(ctx)`.
- `ClauseContext.ability` is resolved statically: the SPELL host, or `effect_views.host_for_override(template, override)`. That is a load-built table keyed on the normalised text of MODE, kicked, channel and head-stripped TRIGGERED bodies (A41); a lookup, never a parse. A host's printed body wins over a body that only starts at another trigger's first spec (one dropping a "for each" frame or an intervening-if). A text two hosts print is ambiguous and names no host, so the handler stays on legacy, unless the handler passes the trigger event it resolves for (`event=`), which keeps that event's hosts only. Pool scan 2026-10-02: 9,661 TRIGGERED bodies, 0 resolve to another host, 57 are ambiguous without an event (Ugin, Eye of the Storms prints one body under its SELF_CAST and its colorless-cast trigger).
- `planeswalker_manager`, the ETB carriers and `resolve_self_cast_trigger` switch the same way.
- E7 collapses the registry.

**Constraints.** `effect_resolver.py` is not in the parse-once exclusions. It writes no life, damage, counters or zones directly (`check_single_owner`, `check_zone_mutation`).

**As built in E0 (2026-10-02).** The skeleton follows the sketch above, with the refinements below, each fail-closed, and one recorded gap (the last item):
- **Condition evaluators.** `CONDITION_EVALUATORS: Dict[ConditionKind, evaluator]` ships empty beside `EXECUTORS`. `holds` evaluates ALL_OF, ANY_OF and NOT itself and sends each leaf to its evaluator, so `can_execute` refuses a spec, or any host's intervening-if, with a leaf no evaluator owns. The CR 603.4 resolution recheck of an intervening-if is the dispatcher's for every host it resolves, the top-level TRIGGERED host (`resolve_ability`) and a delayed or reflexive sub-host (`resolve_sub_ability`) alike; false, the host does nothing and the call returns False. The trigger-time check stays with the carrier's trigger queue (review fix, 2026-10-02). A per-member condition is not evaluated up front; the executor evaluates it, declared by `evaluates_members = True` on the executor.
- **Actors.** `can_execute` refuses an actor `_actors_apnap` cannot bind. The bindable shapes are: no actor (the controller), a player-set Selector bound to the controller through `covers_player`, and a `Ref(TARGET)` player slot.
- **Replacements and CREATE_TRIGGER.** A replacing spec's victims must be its siblings in the same sequence, or `can_execute` refuses. A CREATE_TRIGGER spec passes the same condition and optional checks as any other spec before it registers its sub-ability.
- **Instead groups and optional replacements (review fix, 2026-10-02).** `by_victim` maps each victim to every replacer that names it, in printed order. When a victim is reached, each replacer decides once (`_instead_applies`) whether it applies: its condition holds AND, for an optional replacer ("you may ... instead"), the controller accepts through `choose_optional_effect`. If any replacer applies, the victim is not performed and every applying replacer runs there, in printed order ("X and Y instead": 16 pool cards, e.g. Bold Defense). If none applies, the victim runs, so a declined "you may ... instead" (Expel from Orazca, Sweep Away) leaves the replaced effect to happen. A victim with several replacers, any of them optional, is refused by `can_execute`: "you may X and Y instead" is one choice, not one per clause.
- **The delayed effect.** It is `partial(resolve_sub_ability, sub=sub, snap=snap)`. `DelayedTriggerQueue` calls `effect(game)`, so the game is the one the queue passes at fire time, not one bound at creation.
- **The legacy adapter.** Its signature is `chosen_from_legacy(ability, item_targets, positions, *, slot_spans, game, face)`. The caller supplies each legacy entry's printed position (from `parse_located`) and each host slot's printed `[start, end)` span (from `parse_spans`), which keeps the dispatcher free of text reads. Each in-host position goes to the slot whose span holds it (A36 step 2); an in-host position no span (or two spans) holds raises `ValueError`. The -1 sentinel becomes `face`. (Review fix, 2026-10-02: the first build mapped the k-th distinct in-host position to slot k, which shifted every later target into an earlier slot left unchosen.)
- **Open item for E1: non-battlefield object identity.** `Handle.entry_seq` is `CardInstance.battlefield_entry_seq`, the only zone-entry ordinal the engine keeps, and it increments on battlefield entry only. The sketch's "zone-entry ordinal elsewhere" does not exist yet: a card that leaves a non-battlefield zone and returns to it (graveyard -> exile -> graveyard) gets an equal Handle, so the CR 400.7 / 603.7c identity check holds on the battlefield only. Before any executor binds a Handle outside the battlefield, CardInstance needs a generic zone-entry ordinal (one counter, bumped on every zone entry by the zone-transfer owner).

---

## 12. Load time, memory and determinism

**Where parsing runs** (A12; superseded by the 2026-10-01 lazy decision below, as landed in step 13). Nothing parses during `CardDatabase` load. `CardTemplate.effects` is a property over `_effects` that parses on first access, per template, through `engine.effect_grammar.parse_template(t)` -- the same call the eager tools' path `parse_pool` makes -- and memoises:
- the face facts (names, type_class, is_spell, is_legendary, is_planeswalker, has_x, keywords702) are computed lazily by `template_facts` from fields the load already sets (`printed_keywords` is the MTGJSON keyword list), so the load builds nothing new;
- face 1 is parsed with the back face's facts; the back-face attachment block now sets the back face's types, subtypes, P/T and keywords BEFORE it calls `_type_loyalty_clauses` for `back_face_loyalty_abilities`, so those facts are complete whenever a clause slice is taken (the A12 order, kept under laziness);
- loyalty clause templates (`_type_loyalty_clauses(..., walker=, face=)`) never parse: `_effects_slice = (walker, face, slot)` makes their `effects` the walker's LOYALTY host for that slot on the face the engine activates (`loyalty_abilities` face 0, `back_face_loyalty_abilities` face 1) -- the very host object, sliced from the walker's current memo. A legacy line read from inside a quoted granted ability (67 of ~900 typed lines, e.g. a token's or an anthem's granted loyalty lines) has no LOYALTY host on its face and gets `EMPTY_EFFECTS`; so does a clause typed with no walker;
- `_effects_key` is the complete parse input the memo was parsed from (`effect_grammar.template_inputs`: name, both faces' text and every face's `template_facts`): a template copied and re-printed, or re-typed (types, supertypes, subtypes, X cost, printed keywords, back-face types), parses again. A memo hit recomputes the facts (about 9 µs). A clause template's key is the walker's effects object its slice was cut from, so the slice follows a re-pinned or re-parsed walker; it is never a stale memo. `set_effects` pins a value for the current parse input; `parse_pool(db, populate=True)` pins every template's eager result for tools. The per-template memos live as long as their templates: `clear_caches` clears only the module memos, `set_effects(None)` clears one template's.

The name is distinct from `CardDatabase.get_effects`, which returns `List[OracleEffect]`, and the neutrality AST test keys on template attribute access only. Every legacy field is unchanged (seeded digest byte-identical).

**Measured** (unchanged): full DB load 19.6 s idle; L0–L3 prototype 0.84 s over 23,276 faces; D2 skeleton with target_solver 1.02 s CPU; `target_solver.parse` 36–38 µs per unique targeted sentence; 51,813 sentences, 27,622 unique.

**Budget:**
- ≤ 4.0 s process CPU for the whole pool (raised from 3.0 s on 2026-10-01: the leaves and L0 measured 2.9 s before L1-L5 existed) on an idle 4-core box (`POOL_PARSE_CPU_BUDGET_S`);
- ≤ 40 MB extra RSS (tracemalloc) was the design memo budget; revised in the E0 integration review (2026-10-01) to the measured whole-grammar figure, gated at `POOL_L0_L5_MEMO_BUDGET_MB` = 68 MB (see "Memo budget revised" below).

**Leaf measurements (E0 integration review, 2026-10-01).** Process CPU over each leaf's own pool slots, caches cleared, best of two, quiet 4-core box: target 0.45 s, lexicon 0.38, dest 0.29, duration 0.28, condition 0.27, keywords 0.14, payload 0.11, amount 0.11, participant 0.09, quantity 0.06, filter 0.03 -- leaves 2.2 s, plus L0 0.72 s: about 2.9 s of the 3.0 s budget before any L1-L5 work (3.2 s before the review's lexicon index, duration pre-gate and memo trimming). `tests/test_effect_grammar_leaf_budget.py` pins each leaf to a share of `POOL_PARSE_CPU_BUDGET_S` at about twice its measurement, so a regressing leaf is named by its own test. Memo caches held after one full pass of every leaf: 58.5 MB (tracemalloc; normalize's face cache is the largest), cleared by `engine.effect_grammar.clear_caches`. Settled 2026-10-01: the CPU budget is raised to 4.0 s (leaf shares rescaled so each leaf keeps its absolute ceiling), and `CardTemplate.effects` parses lazily per template, so `CardDatabase()` load time is unchanged and a game parses only the cards it touches; the pool budget gates the eager pool path that tools use. The 40 MB memo budget was kept at that point; the whole-grammar figure after L5 exceeds it and it is revised below. The on-disk cache stays the fallback.

**L1 measurement (E0 stage 3, 2026-10-01).** `structure.parse_face_structure` over 23,204 distinct faces (41,399 hosts), caches cleared, quiet 4-core box: 3.5 s process CPU for L0 and L1 together (L0 alone 0.84 s), about 150 µs per face. Of the L1 share, about 0.5 s is leaf work the grammar needs anyway (keyword lines, intervening-if conditions, cost deltas, the A6 target and ADD_MANA tests) and about 1.2 s is L1's own cascade, merge and ordinal pass. L1 runs L0 once per face through `normalize.normalize_mapped`, which returns the printed-span map with the L0 output, so A7 printed costs need no second L0 pass; each distinct printed cost is parsed once (`structure._cost`). **L1 review (2026-10-01).** The face memo was `CACHE_SIZE`-bounded, above the pool's 23,204 faces, so it never evicted and kept every `FaceStructure` with its L0 output: 73 MB after a pool pass (tracemalloc, leaf memos cleared, results dropped) -- nearly twice the 40 MB memo budget on its own -- and the cyclic collector re-walked it, about 1 s of the pass. It is now bounded at `structure.FACE_CACHE_SIZE` = 512 (a pool pass has 0 hits; the faces in play repeat, and the per-template `CardTemplate.effects` memo holds the rest): 1.8 MB after a pool pass, pinned at a 0.1 share of `POOL_PARSE_MEMO_BUDGET_MB`. Re-measured with the card DB frozen out of the collector (the L0 methodology) and no results retained: 2.8-2.9 s for L0 + L1. `tests/test_effect_grammar_structure_pool.py` now pins L0 + L1 at a share of `POOL_PARSE_CPU_BUDGET_S` -- 1.0, the subset may not exceed the whole -- instead of the free-standing 7.0 s ceiling that sat above the whole-grammar budget. Open, for L2-L5 (unchanged in kind, smaller in size): L0 + L1 take about 2.9 s of the 4.0 s budget, leaving about 1.1 s for L2-L5 on the eager pool path, below L4's leaf work (about 2.2 s); the lazy per-template `CardTemplate.effects` keeps `CardDatabase()` load time unchanged either way, and the decision on the pool tools' eager path (revise the budget, or take the on-disk cache) must be taken before L4 lands.

**L2-L4 measurement (E0 stage 3, 2026-10-01).** `patterns.match_host` (L2 frames, L3 clause split, L4 patterns) over every L1 host and mode of the pool -- 42,663 hosts, 41.9k clause specs -- caches cleared, card DB frozen out of the collector, quiet 4-core box: 4.5-4.6 s process CPU, of which L2/L3 are about 1.3 s and L4 the rest, mostly leaf work. 76.8% of clause specs are typed; the refusals by stage are CLAUSE 1.9k (the payload leaf's unknown continuous predicates; verbs with no row, such as attach), FILTER 1.6k, REFERENCE 1.3k (library positions, "the top card of your library"), NO_LEMMA 1.2k, TARGET 1.1k, CONDITION 0.8k, AMOUNT 0.7k and RECOGNIZED_UNSUPPORTED 0.5k. Every host is covered (no character outside an L1 span, a frame token or refusal, or a clause span) and every typed clause consumed every printed word. The clause memo is bounded at `patterns.CLAUSE_CACHE_SIZE` = 4096: unbounded it kept all ~17k distinct clauses, 25 MB of specs, for no measurable CPU gain; bounded it holds 7-8 MB after a pool pass. A per-sentence memo was tried and dropped: re-placing a memoised frame at its host offset cost more than the repeats saved (5.4 s against 4.5 s). `tests/test_effect_grammar_patterns_pool.py` pins the census floor (73% typed), the coverage invariant, a ten-witness class per pattern row, and the pass at a regression ceiling of 2.0 x `POOL_PARSE_CPU_BUDGET_S`. **L2-L4 review (2026-10-01).** Re-measured back to back on one quiet box: 5.1 s for the stage-3 commit and 5.3 s after the review fixes (the participant-leaf subject checks cost about 0.15 s); 76.6% typed. The regression ceiling is now 1.7 x `POOL_PARSE_CPU_BUDGET_S` (6.8 s, about 1.3x the measurement), so the test names an L2-L4 regression instead of absorbing it into headroom; the 4.0 s whole-pool budget still does not hold for the eager path, and that decision stays open below. **Open (the eager pool budget):** the eager pool pass of L0-L4 is about 7.5-8.2 s (L0 + L1 2.9 s, L2-L4 4.6-5.3 s) against the 4.0 s budget, before L5. Lazy per-template `CardTemplate.effects` (the settled decision) keeps `CardDatabase()` load time unchanged; the whole-pool budget test (`effect_parse_fits_the_load_budget`, steps 13 and 22) must revise the eager budget or adopt the on-disk cache.

**L5 measurement (E0 stage 3, 2026-10-01).** `link.parse_face_hosts` (L0-L5) over the whole pool through the eager `parse_pool` path (22,738 templates, 44.3k specs including sub-ability hosts), caches cleared, card DB frozen out of the collector, quiet 4-core box: 19.4-19.5 s process CPU, of which L0-L4 is about 9 s and L5 about 5 s -- about half of it `validate_spec` over every spec (its immutability walk and deep hash; `find_mutable` / `_refs` were made allocation-free, unchanged in result) -- plus collector time over the retained output. One template parses lazily in about 2 ms. Typed share after L5 by host kind: MANA_ABILITY 95%, ACTIVATED 79%, MODE 77%, TRIGGERED 77%, LOYALTY 75%, CHAPTER 74%, SPELL 73%, STATIC 61%; 72% overall. Linking refusals: `link.unbound` 396 (mostly references the earlier layers left untyped), `link.no_antecedent` 69 (instead / connective frames with no earlier spec, replacement-shaped), `link.target` 11 (a "target ..." possessive inside a quantity with no requirement of its own), `link.ambiguous` 5. `tests/test_effect_grammar_pool_invariants.py` pins the typed-share floors per host kind; the CPU gates are the decision below. The witness fixture (`tests/fixtures/effect_grammar_witnesses.json`, 123 rows) holds 23 strict-xfail rows, each naming the leaf or L1-L4 refusal that keeps the card from its section-18.1 chain (most often `participant.library_position` for "look at the top N cards").

**L5 review (2026-10-01).** Re-measured after the review fixes (lazy and eager paths now read one keyword source; refused antecedents are never bound; uncovered target words refuse their clause): eager `parse_pool` 19.8 s process CPU (best of two, quiet 4-core box, card DB frozen out of the collector, caches cleared); the lazy `parse_template` of every registered-deck card (359 templates, the cards a game can touch) 0.30-0.39 s cold, mean about 1 ms, max 4-6 ms. Linking refusals: `link.unbound` 245, `link.no_antecedent` 17 (a refused instead clause now keeps its own L4 refusal instead of being relabelled), `link.ambiguous` 4; `patterns.uncovered_target` 91 (the former `link.target` cases among them).

**Decision (L5 review, 2026-10-01): the eager budget is revised, the on-disk cache is not adopted.** `POOL_PARSE_CPU_BUDGET_S` (4.0 s) stays the design budget the leaf and L0-L1 shares are scaled from. Two budgets gate the whole grammar, both in `tests/test_effect_grammar_pool_invariants.py`:
- `effect_parse_fits_the_load_budget` clears the caches, parses the whole pool through the eager tools' path and asserts `process_time ≤ POOL_L0_L5_EAGER_CPU_BUDGET_S` = 30 s (about 1.5x the 19.8 s measurement, so a slower CI core passes while a layer that regresses by half is named). Games never run this path.
- `the_lazy_per_template_parse_of_every_card_a_game_can_touch_fits_its_budget` parses every registered-deck template cold through `parse_template` (the path `CardTemplate.effects` takes) and asserts the total `≤ DECK_CARDS_LAZY_CPU_BUDGET_S` = 1.0 s and each template `≤ TEMPLATE_LAZY_CPU_MAX_S` = 50 ms (single-template times are noisy; a collector pass can land in any one).

Both carry `@pytest.mark.timeout(N)` with the measurement in a comment. Wall-clock is never measured. `CardDatabase()` load time is unchanged because `CardTemplate.effects` is lazy. The on-disk cache keyed on `(GRAMMAR_VERSION, sha256(text), facts key)` stays the fallback if the eager tools' path becomes a bottleneck.

**Memo budget revised (E0 integration review, 2026-10-01).** Only the per-layer memo shares (L1 face memo, L4 clause memo) had been gated; nothing gated the whole grammar. After one eager L0-L5 pool pass with its results dropped, the module memos hold 51-52 MB (tracemalloc; 111 MB with the results kept): `sub/filter` 8.6 MB, `keywords` 7.9, `patterns` 6.1, `effect_spec` 4.3, `lexicon` 4.2, `normalize` 4.1, `sub/target` 3.8, `sub/condition` 2.9, every other module under 1.5. `clear_caches` returns them to 0.1 MB. Every memo is bounded, a game parses only the cards it touches (lazy `CardTemplate.effects`), and no game code reads `.effects` in E0, so the cost falls on the tools' eager path only. Decision: the design's 40 MB is revised to the measurement, and `test_the_whole_grammar_memos_after_a_pool_pass_fit_their_budget` gates it at `POOL_L0_L5_MEMO_BUDGET_MB` = 68 MB (about 1.3x, so a memo that grows by a third is named) and pins that `clear_caches` leaves at most 5% of the budget behind. Shrinking the leaf memos (filter, keywords, lexicon, target, condition) below the pool's distinct-input count stays open; it trades CPU on the eager path, which is already over its design budget, for memory the games never pay.

**How the budget is met.** As before: lemma buckets, bounded regexes, patterns compiled at import, and `lru_cache` on clauses, spans and faces. The face key is the complete fact set (A32). A test perturbs each fact and asserts that the key changes whenever the output does. The L0 offset map lives only for the call; `printed_span` recomputes it for the few load-time views that need it.

**Determinism.** As before, including the `PYTHONHASHSEED` 0 against 1 subprocess test on `canonical`. `CostSnapshot` sorts its items.

**Behaviour neutrality in E0.**
- `test_populating_effects_changes_no_seeded_game_log`: two games (one pre-sideboard, one post-sideboard) run with effects and with `EMPTY_EFFECTS`.
- The AST test on `.effects` reads.
- `tools/seeded_game_digest.py --check`: 16 Bo1 games and 4 Bo3 matches against the parent-commit baseline.
- Unchanged-output pool tests for `target_solver.parse` (after the `parse_located` refactor) and `loyalty_abilities` (after the slot-owner refactor).

---

## 13. Contract compliance and ratchets

**Card and deck names.** The grammar, lexicon, patterns, views and dispatcher are generic. `test_effect_grammar_holds_no_card_names` scans string literals as before. Card names appear only in `tests/fixtures/effect_grammar_witnesses.json`. `check_abstraction` and `check_card_name_registry` are unchanged in E0.

**Single owner.**
- The grammar is the only new reader of oracle text, and only through the parse-once `CardTemplate.effects` memo (lazy: the first access, which may fall mid-game, parses; every later one reads the memo), never at resolution. `engine/effect_grammar/` joins `check_oracle_runtime_parse._EXCLUDED` through new directory-prefix support.
- TargetRequirements come only from `target_solver`. `parse_located` is the one placement owner, and `parse()` is re-expressed over it.
- Costs come from `parse_activation_cost` on printed spans.
- Delays come from `_DELAY_TIMING_PHRASES`, and the loyalty slot rule has one owner.
- The grammar's modal-header and loyalty-line tables are supersets of legacy regexes during the strangler. The tool reports their difference, and ownership moves to the grammar when the legacy parser is deleted.
- Simultaneous zone moves get one owner (`zone_transfer.move_simultaneously`, E2).

**`tools/check_effect_parsers.py`** with `tools/effect_parsers_baseline.json`. It is wired into the workflow after "Assemble card DB" and into `tests/test_abstraction_contract.py`. Its counts may only shrink:
- **(a)** `card_database` `template.<f> = …` assignments whose field is in DERIVATIONS and is still populated by a legacy parser.
- **(b)** Runtime oracle reads inside resolution handlers. The scanned modules are `clause_resolver`, `oracle_resolver` (including `resolve_self_cast_trigger`), `spell_resolution`, `planeswalker_manager`, and the OracleTextParser description consumers. `host_for_override` lookups are counted too.
- **(c)** `_legacy_*` quirk predicates and `_legacy_domain_*` masks in `effect_views.py`.
- **(d)** The total of `LEGACY_RESIDUE_TOLERATED` codes. It may grow only in a family switch commit, with RESIDUE_WIDENING or RESIDUE_NARROWING evidence.
- **(e)** A new `def parse_*` whose result is assigned to a field in neither DERIVATIONS nor NON_EFFECT_FIELDS fails.
- **(f)** (Handler, host) pairs on legacy fallback (A38), as reported by `--gate-parity`.

**Other ratchets.** The census and equivalence baselines may only shrink, strictly: as in `check_effect_parsers.py`, an improvement fails `--check` as a stale baseline until the same commit locks it in with `--update`, so a later regression cannot refill the ceiling. `check_narrow_typed_fields` must not flag `effects`. `check_magic_numbers` is untouched, because E0 edits no `ai/*.py`; the budget constant lives in the test. `check_doc_hygiene` covers this doc and the census doc.

**Tests before fixes.** E0 has no behaviour fix. Each SEMANTIC_FIX, mask removal and E_k.b row lands later as its own commit: failing rule-phrased test, fix and rules-audit invariant.

---

## 14. Family migration order

Executors land by verb family. Hosts switch per host when strict, executable and harness-identical (A38). Each handler's legacy apply is deleted when its fallback count reaches zero.

1. **E0.** Schema, grammar, census, equivalence (with closure and gate parity), per-host harness, digest, ratchet, witness fixture. No behaviour change.
2. **T-solver** (parallel, after E0). `target_solver` features ranked by the residue census, one behaviour-change commit each:
   - not-you and opponent scope after permanent/nonland;
   - `exclude_source`;
   - keyword, state and colour filters, including colored;
   - nontoken and historic;
   - player-or-planeswalker and complete unions;
   - conjunctive types;
   - single-graveyard;
   - stack-ability targets;
   - "one or two targets";
   - zone unions ("spell or nonland permanent").
3. **E1: damage/life** (DAMAGE, LOSE_LIFE, GAIN_LIFE, plus the binding-only CHOOSE and LOOK). Strict hosts: fixed-N burn, activated DAMAGE_ANY_TARGET, one-DAMAGE loyalty lines, painland damage riders. E1 moves `_scaler_count` and `_direct_damage_condition_met` into `effect_conditions.py`. E1.b behaviour changes: single-owner retirements of direct `life -=` and `damage_marked` writes, and the CR 608.2b re-check.
4. **E2: removal/zone-move** (DESTROY, EXILE, SACRIFICE, MOVE, SHUFFLE). E2 extracts `zone_transfer.move_simultaneously`, adds EXECUTOR_FILTER_KEYS for sweeps, and admits the first tolerated residue codes. Strict hosts: single removal, ETB removal, sweeps, plain bounce (including cost-modified channel bounce), mass reanimate, graveyard exile. Gate: at least 85% of deck removal clauses are typed and executable-or-tolerated.
5. **E3: card flow** (DRAW, DISCARD, MILL, SCRY, SURVEIL, REVEAL, SEARCH, CAST_FREE). Newly executable hosts: loot, hand attack, dig, tutors, fetchlands, and the E2-deferred land destruction and Path hosts. E3 retires the name-keyed impulse gate only once E5 executes impulse.
6. **E4: tokens/counters** (CREATE_TOKEN with a typed TokenSpec, counter verbs, PLAYER_COUNTERS, PAY for energy, KEYWORD_ACTION). E4.b: the energy-damage switch (M9) and Ajani [0], a reflexive sub-ability that turns into a behaviour change.
7. **E5: continuous.** First extend `covers_object` (value-typed) and MEMBER evaluation, and add the new DurationKinds with `expired_by` tests ("until the end of your next turn"). Then switch:
   - the pump, restriction, prohibition, prevention and observer handlers;
   - the reanimation-with-haste hosts;
   - impulse;
   - Scion-of-Draco-shaped member grants;
   - Guide-of-Souls-shaped reflexive grants.
8. **E6: stack/mana/rules** (COUNTER with dest override, ADD_MANA including triggered mana abilities, PAY for mana, COST_DELTA statics and activation cost modifiers, END_TURN, delayed PAY). Fields: `kicked_clause` and `channel_clause` as printed spans. Callers move to host indices.
9. **E7: carrier collapse**, a measured behaviour change (Bo3 n ≥ 20 per affected deck, offline scorer, quiet box):
   - HANDLERS become spec-order dispatch;
   - the description interpreter and the OracleTextParser pipeline fold;
   - `planeswalker_manager` per-kind branches fold, and `LoyaltyEffectKind` becomes a view of `can_execute`;
   - cast legality moves onto `AbilityEffects.targets` plus `target_alts`, with sub-ability targets excluded.
10. **E-AI.** `_project_spell` projects per verb over `effects`.
11. **T1–T4: TriggerSpec** (section 15), then **R: ReplacementSpec**.

**Handler switch schedule** (closure of registered-deck hosts; the `--closure` report replaces this table once it exists):

| Handler | Strict shapes switch in | Hosts on legacy fallback until | Why |
|---|---|---|---|
| direct_damage | E1 | none expected | Lava Dart's flashback is a KEYWORD host (A1). |
| energy_damage | E4.b | E4.b | Needs CHOOSE, PLAYER_COUNTERS and PAY(energy); legacy semantics differ (M9). |
| land_destruction | E3 | E3 | DESTROY + SEARCH/MOVE/SHUFFLE/DRAW riders. |
| board_sweep | E2 | none expected | EXECUTOR_FILTER_KEYS types. |
| targeted_removal | E2 | E3 (Path-shaped) | SEARCH rider. |
| bounce | E2 | T-solver (zone union) | Sink into Stupor. |
| reanimate_target | E2 | E5 (haste grant) | Goryo's Vengeance. |
| mass_reanimate | E2 | none expected | `move_simultaneously`. |
| mass_mode_clause | E2 | by closure | |
| library_dig | E3 | none expected | REST and instead (A28). |
| hand_attack | E3 | none expected | LOSE_LIFE from E1. |
| hand_refill_wheel | E3 | E6 | END_TURN. |
| card_flow | E3 | by closure | |
| impulse_reveal | E5 | E5 | PERMIT + next-turn duration. |
| create_token | E4 | E5 (granted abilities) | |
| targeted_pump, object_restriction, group_restriction, until_next_turn, cast_prohibition, combat_prevention, attack_observer | E5 | by closure | |

---

## 15. Later stage: TriggerSpec, the event bus and real trigger stack items

The problem and T0–T4 are as before, with these amendments:
- **T0.** `TriggerSpec.events: Tuple[EventKind, ...]` with per-disjunct subject filters (A11). The T0 fields are:
  - `event`/`events`, `subject`, `player`, `zone_from`, `zone_to`, `step`, `frequency`;
  - `intervening_if`, `once_each_turn`, `look_back`;
  - `mana_ability: bool` (A6, CR 605.1b), so such a trigger resolves immediately and never uses the stack;
  - `host` (the same host parsed in E0).
- **T1.** Unchanged: the GameEvent bus on `turn_clock` with emitters at the single owners, as a pure refactor.
- **T2.** Pending triggers go on the stack the next time a player would receive priority, in APNAP order (CR 603.3b), with targets chosen when each is put on the stack (CR 603.3d) and the intervening-if re-checked (CR 603.4). In addition:
  - Universal event deferral: every trigger event during a resolution is collected and released after the ability finishes (CR 603.3). `move_simultaneously`'s local deferral becomes a special case (M5).
  - Reflexive sub-abilities become stack items whose targets are chosen when created (CR 603.12). Delayed sub-abilities fire into the same pending list with Handle snapshots (CR 603.7c).
  - Rules-audit invariants: `603.3/triggered_ability_used_the_stack` and `603.12/reflexive_targets_chosen_after_parent_action`.
- **T3 and T4.** As before.
- **R.** As before.

---

## 16. Risks

- **The solver is the binding constraint.** It is made visible by TARGET, TARGET_COUNT and polarity-typed residue. Each solver feature is its own behaviour change.
- **Residue tolerance could become permanent.** It is counted and shrinks per feature; UNPARSED is never tolerable.
- **Pronoun mis-binding.** Mitigations: rule 0, mention-based antecedents, A25 tie-breaks, UNMODELLED on ambiguity, and witness fixture rows for every refuted shape.
- **Sub-ability scope** (how far "When you do" or a delay extends). Mitigations: the census lists every shape, and a delayed sub-ability absorbs only dependent sentences, with UNMODELLED(DELAY) on mixed dependence.
- **Keyword-line classification depends on MTGJSON keyword data.** Mitigations: the CR 702 table fallback for synthetic templates, and a pool test that no deck spell merges a keyword or cost line.
- **Self-name collisions and pronoun case.** Mitigations: A9 normalisation tests and the witness rows.
- **Load time and memory.** Mitigations: the pool CPU gate, `--timing` per commit, and the cache fallback.
- **Byte-identity at switch commits.** Mitigations:
  - legacy gates kept;
  - per-host fallback;
  - strict views that later executors cannot silently widen;
  - owner pickers for unbound slots;
  - legacy illegal-target semantics until the .b commits;
  - the harness on deck MB and SB hosts;
  - the Bo3 digest.
- **Silent coverage growth through legacy fields.** Mitigations: domain masks, the rule that a switched field equals legacy on the whole pool, and mask removal only as a measured commit.
- **Third and fourth interpreters outliving their families.** Mitigation: they are carriers with reach counts, plus ratchet (b).
- **Scope creep into triggers and replacements in E0.** Mitigation: heads stay raw with hint tuples; replacements are explicit UNMODELLED; sub-abilities are schema only.

---

## 17. E0 exit criteria and measurement record

E0 is complete when:
1. The suite is green, including every E0 test, the witness fixture test and the ratchet tests.
2. `tools/seeded_game_digest.py --check` reports every game (16 Bo1 plus 4 Bo3 matches) byte-identical to the parent commit.
3. The census is generated and pinned, and the deck-card typed share is recorded. E2 does not start while fewer than 85% of registered-deck removal-family clauses are typed and executable-or-tolerable.
4. The equivalence baseline is pinned, the completeness scan passes over all 193 assignments plus the lazy fields, every Tier A field is registered, and the `--closure` report is committed (`tools/effect_closure_report.json`, written by `--update`; a full `--check` fails when it is stale).
5. The harness self-check (legacy against legacy) is deterministic on every registered-deck MB and SB host.
6. `--timing` figures are recorded on a quiet box, and the pool CPU is ≤ 4.0 s.
7. The frontmatter of this doc links the generated census.

**Load CPU after step 13 (2026-10-01).** `process_time` of `CardDatabase()` on the full pool (23,481 entries), quiet 4-core box (loadavg 0.7), parent commit `fbd4d3e` in a pinned worktree against step 13, interleaved, 3 runs each: parent 18.29 / 17.52 / 17.13 s (mean 17.65), step 13 17.85 / 17.47 / 18.54 s (mean 17.95) -- within run-to-run noise (spread about 1.1 s each side). After a full load, 0 templates and 0 loyalty clause templates hold effects. Tests: `test_no_template_parses_its_effects_while_the_database_loads` (parse calls counted over a fixture load), `test_the_lazy_effects_property_and_the_eager_pool_path_give_identical_specs` (every registered-deck card plus every 97th pool template).

**Steps 9-13 integration record (2026-10-01).** Measured on this branch after the integration fixes (granted costs read from print, the meld layout fact, the steps 9-13 tests, the memo budget), quiet 4-core box (loadavg 0.5-1.0), process CPU, caches cleared, card DB frozen out of the collector, best of two:

| Pass | Input | CPU |
|---|---|---|
| L0 (`normalize.normalize`) | 23,204 distinct faces | 0.77 s |
| L0 + L1 (`structure.parse_face_structure`) | same faces | 3.03 s (L1 about 2.26 s) |
| L2-L4 (`patterns.match_host`, from L1 hosts) | 42,663 hosts and modes | 5.23 s |
| L0-L5 eager (`parse_pool`) | 22,738 templates | 18.30 s |

L5 is the remainder, about 10 s of the eager pass: linking, granted hosts (now one extra L0 run per face that has quotes, for their printed maps), schema lowering with `validate_spec` over every spec, freezing, and collector time over the retained output. The eager gate is `POOL_L0_L5_EAGER_CPU_BUDGET_S` = 30 s; the lazy per-template path is gated separately (section 12).

Typed share after L5 (non-UNMODELLED specs, sub-ability hosts included), 44,316 specs, 72.3% overall: MANA_ABILITY 95.0% (1,593), TRIGGERED 78.4% (15,272), ACTIVATED 78.4% (6,634), MODE 76.8% (1,758), LOYALTY 75.3% (1,302), CHAPTER 74.0% (699), SPELL 72.5% (9,992), STATIC 60.8% (5,376); REPLACEMENT (1,439), UNKNOWN (226), ALTERNATIVE_COST (16) and KEYWORD (9) specs are refusals by construction (0%). Registered-deck cards: 870 specs, 73.9% typed.

`CardDatabase()` load, `process_time` on the full pool (23,481 entries), the pre-integration commit `418acde` in a worktree against this branch, interleaved, 6 runs each with the order reversed for the second three: before 18.35 / 18.27 / 19.26 / 18.52 / 20.23 / 18.07 s (mean 18.78), after 18.05 / 19.64 / 19.91 / 19.47 / 18.82 / 19.02 s (mean 19.15) -- a 0.37 s difference inside the run-to-run spread (about 2 s each side). The only load-path change is `CardTemplate.layout` read from the MTGJSON entry; nothing parses at load.

Integration review findings (2026-10-01): (1) granted hosts read their activation costs from the L0 quote text, so "sacrifice this creature" inside a quote was "sacrifice ~" and unpayable on 21 pool hosts -- fixed, the granted L1 parse now reads the printed quote through `normalize.quote_printers`; (2) the production face facts never carried the meld layout fact (CR 712.4) while the pool pins built it themselves -- fixed through `CardTemplate.layout`, and the structure / normalize pool fixtures now call `template_facts`; (3)-(5) the steps 9-13 e0_tests missing or partial (post-L5 coverage, reference order, mode / back-face loyalty / activation-cost alignment, frozen shared parses, the card-name scan, the full hashable check, the three restated-instead forms) -- added under their spec names, the 233 activation-cost divergences seeded as `tests/fixtures/effect_grammar_activation_cost_divergences.json`, every one classified `grammar_reads_more`; (6) the whole-grammar memo budget -- revised and gated (section 12).

Measured values (filled in 2026-10-02, follow-up review; 4-core box, load average 3-4 from a concurrent matrix run, process CPU): eager pool parse (`parse_pool`) 18.32 s CPU over 22,738 templates, mean 806 µs per template; RSS 609 MB after the card DB load and 716 MB after the eager parse (peak 717 MB, so the retained parse costs about 107 MB); typed share 72.3% (pool) / 73.9% (deck cards); UNMODELLED by stage, residue by code and polarity: the generated census `docs/design/effect_grammar_census.md` (pinned in `tools/effect_census_baseline.json`); equivalence per field, Tier A included: `tools/effect_spec_equivalence_baseline.json`; legacy-fallback pairs per handler: the committed closure report `tools/effect_closure_report.json` (47 handlers, 5,223 pairs; `mass_mode_clause` 1,501, `card_flow` 906, `create_token` 422, `targeted_pump` 404 lead). E2 gate (criterion 3), now reported by the census: 259 registered-deck removal-family (zone-verb) clauses, 216 typed (83.4%), 0 executable or tolerable (0.0%) -- no executor and no tolerated residue exist in E0, so the gate is not met; the typed share alone is also below 85%.

**Tools landed (2026-10-02).** Measured with the four tools of sections 9, 10 and 13, process CPU, on a 4-core box that was NOT quiet (a concurrent 4-worker matrix run held the load average at 4-5), so the `--timing` figures of criterion 6 still need a quiet-box re-run:
- `tools/effect_census.py` (criterion 3, generated `docs/design/effect_grammar_census.md`, pinned in `tools/effect_census_baseline.json`): 44,316 specs over 22,738 templates, 72.3% typed; registered-deck cards 73.9% of 867 specs. UNMODELLED 12,262: CLAUSE 1,934, FILTER 1,617, REPLACEMENT 1,543, REFERENCE 1,524, NO_LEMMA 1,190, TARGET 1,188, CONDITION 1,164, AMOUNT 779, RECOGNIZED_UNSUPPORTED 478, STRUCTURE 231, TRIGGER_EMBEDDED 165, QUANTITY 133, ITERATION 110, DURATION 108, TARGET_COUNT 82, DELAY 16. Residue 1,547 codes: WIDENING 865, UNPARSED 390, NARROWING 292. Census pass about 36 s wall including the 16 s DB load.
- `tools/effect_spec_equivalence.py --timing` (criterion 4, pinned in `tools/effect_spec_equivalence_baseline.json`): DB load 15.9 s, eager parse of every template 17.7-20.1 s, every derivation on every template 22 s (re-measured after the follow-up review: the 42 s first recorded here, with stack_mana at 19.6 s, was mostly a second pool parse -- the kicked and channel printed-span views read `template.effects` instead of the supplied parse and pinned the memo on every template; they now take the supplied `CardEffects`, stack_mana is 3.6 s, and a run pins nothing). 2,849,603 comparisons over 147 records (the 146 FieldDerivations plus the tool's `cast_targets` carrier): AGREE 2,813,152, DERIVED_COVERAGE_GROWTH 7,842, UNMODELLED_CLAUSE 10,361, UNEXPLAINED 17,949, LEGACY_HASH_ORDER 13 (UNMODELLED_CLAUSE now blames only a refusal in a host the derivation read, traced per comparison: 2,299 comparisons whose only refusal sat on another host moved to UNEXPLAINED; the 13 hash-order `pump_spell_keyword` comparisons are classed by their `always` row, so the counts no longer depend on PYTHONHASHSEED; almost all Tier B/C presence predicates; `has_recurring_trigger` 3,475 and `requires_creature_target` 2,313 lead), MASKED_GROWTH 68, and the allowlist classes REMINDER_TEXT 190, LEGACY_TARGET_ZONE 14, LEGACY_COST_REDUCTION_SCOPE 13, LEGACY_HASH_ORDER 13, LEGACY_RITUAL_MANA 1. Four section-10 seed rows surface in no field comparison and are listed as `unsurfaced` with their reason (counts above ten, token-spec subtypes, ritual cost pips, the tutor relative-clause filter).
- `--closure` / `--gate-parity`: 5,223 (handler, host) pairs pool-wide, 147 on registered-deck cards; 0 on the new path, so all are on legacy fallback (ratchet (f)); closure 0.9 s after the parse.
- `tools/host_resolution_equivalence.py` (criterion 5): 175 registered-deck MB and SB hosts with a legacy apply (SPELL, MODE, classified ACTIVATED, executable LOYALTY), 6 boards x 2 seeds, legacy against legacy: 0 divergences in digest, log bytes or result, about 53 s CPU. Each legacy apply gets the targets one deterministic rule chooses on that board from the host's requirements (`legacy_targets`: `target_solver.choose_targets`, the opponent's objects first, then the controller's, else a player sentinel); the first landing passed no targets, so 99 of its 234 hosts were silent no-ops and the self-check proved little (follow-up review, 2026-10-02). 135 hosts now change state on at least one board; the 40 that change none (no legal object on any board for the pick, or no legacy apply for the clause, such as an each-opponent sacrifice) are pinned in `tests/fixtures/host_harness_noop_hosts.json`. UNCLASSIFIED activations (fetch lands and other abilities `resolve_activated_ability` refuses) are no longer counted as resolvable. 558 hosts are skipped and reported by kind (TRIGGERED 119, STATIC 73, KEYWORD 138, MANA_ABILITY 67, ACTIVATED 61, REPLACEMENT 58, other 42): **criterion 5 does not yet cover triggered or static hosts**, which have no single legacy apply in E0.
- Suite cost of the E0 tool tests (follow-up review, 2026-10-02; this box, load average 3-4, not a GitHub runner -- the runner delta is still unmeasured): the census, equivalence and harness test files took 167 s at landing; they now take 130 s, because the census and equivalence pool tests share one session eager parse (the `pool_effects` fixture in `tests/conftest.py`, about 21 s here once per session) and the derivations no longer re-parse the pool (22 s instead of 42 s). Remaining: the shared card DB load (about 18 s, paid by any DB test first in the process), the equivalence pool test 26 s, the harness self-check 56 s. The CI step `check_effect_parsers.py --pool` adds about 35 s in its own process (DB load, eager parse, closure); it is the binding (f) check outside pytest and stays.
- `tools/check_effect_parsers.py` (`tools/effect_parsers_baseline.json`): (a) 116, (b) 106 (the four planeswalker_manager `LoyaltyAbility.text` reads counted since the follow-up review), (c) 15, (d) 0, (e) 0, (f) 5,223.

---

## 18. Tests

### 18.1 Witness fixture (`tests/fixtures/effect_grammar_witnesses.json`)

Each row names a card, a face, a host selector and the expected canonical spec-chain shape, recorded from the card's DB oracle text. It is asserted by `test_registered_deck_witness_cards_parse_to_their_expected_spec_chains`. Card names live only here.

| Defect class (amendment) | Witness rows | Expected parse |
|---|---|---|
| Serial and gapped clauses (A13) | Misty Rainforest, Flooded Strand, Path to Exile, Erode, Cleansing Wildfire, Price of Freedom, Summoner's Pact, Scapeshift, Expedition Map, Expressive Iteration, Archon of Cruelty, Faithful Mending, Green Sun's Zenith, Nature's Rhythm, Primeval Titan, Stock Up, Waker of Waves | Full sibling chains (SEARCH, then MOVE(RESULT), then SHUFFLE; SACRIFICE / DISCARD / LOSE_LIFE with actor Ref(TARGET,0)); gapped MOVE flagged `gapped` |
| Keyword lines (A1, M3) | Lava Dart, Cling to Dust, Desperate Ritual, Goryo's Vengeance, Into the Flood Maw, Unburial Rites, Faithless Looting, Past in Flames, Ephemerate, Consult the Star Charts, Orim's Chant, Consign to Memory, Vandalblast, Solitude, Subtlety, Street Wraith, Endurance, Detective's Phoenix | KEYWORD hosts with typed costs; the SPELL host holds only resolution specs |
| Alternative costs (A2) | Force of Negation, Force of Vigor | ALTERNATIVE_COST host with `cost_condition`; no spec in SPELL |
| Spell statics on permanents (A3) | Emrakul, the Promised End; Scion of Draco; Leyline Binding; Hollow One; Obsidian Charmaw; Chandra, Awakened Inferno | STATIC(from_zone='stack') COST_DELTA / PROHIBIT be_countered |
| Modal in reminder text (A4) | Fire Magic, Territorial Kavu | Three MODE hosts with mode costs; modal TRIGGERED head |
| Delay paragraph on a spell (A5) | Summoner's Pact | CREATE_TRIGGER(DELAYED YOUR_NEXT_UPKEEP) holding PAY with otherwise = RECOGNIZED_UNSUPPORTED |
| Mana abilities (A6) | Shivan Reef, Talisman of Resilience, Arena of Glory, Gemstone Caverns, Utopia Sprawl, Badgermole Cub, Leyline of Abundance | MANA_ABILITY with riders; TRIGGERED with flag `mana_ability` |
| Printed-text costs (A7) | Flooded Strand, Mishra's Bauble, Expedition Map | host cost == ActivatedAbility.cost (`sacrifice_self`) |
| Cost modifiers (A8) | Boseiju, Who Endures; Otawara, Soaring City; Past in Flames; Snapcaster Mage | `cost_modifiers` COST_DELTA; specs contain no COST_DELTA; flashback `cost_rule='mana_cost'` |
| Pronoun case (A9) | Tamiyo, Inquisitive Student; Ajani, Nacatl Pariah; Ral, Monsoon Mage; Kaito, Bane of Nightmares | EXILE(SELF) then MOVE(SELF, transformed, controller owner); SET_TYPES / ADD_KEYWORDS on SELF |
| Nested quotes (A10) | Urza's Saga | GRANTED ACTIVATED host whose TokenSpec.granted holds STATIC MODIFY_PT FOR_EACH(artifact you control) |
| Disjunctive heads (A11) | Primeval Titan, Archon of Cruelty, Orcish Bowmasters, Cityscape Leveler | `event_hints` tuples of length 2 |
| Faces and loyalty (A12) | Ajani, Nacatl Avenger; Ral, Leyline Prodigy; Tamiyo, Seasoned Scholar; Chandra, Awakened Inferno; Grist, the Hunger Tide | Face-1 LOYALTY hosts align with `back_face_loyalty_abilities`; `[−X]` is LOYALTY with Amount(X, −1) |
| Rule 0 (A23) | Fatal Push, Prismatic Ending, Spell Pierce, Mana Tithe, Stubborn Denial, Mystical Dispute, Metallic Rebuke, Flusterstorm, Drown in the Loch | Condition refs = Ref(TARGET,0); UNLESS payer CONTROLLER_OF(Ref(TARGET,0)) |
| Member conditions (A23) | Scion of Draco | Five ADD_KEYWORDS over FILTER(creatures you control), each with OBJECT colour on Ref(MEMBER) |
| Reflexive sub-abilities (A30) | Guide of Souls; Ajani, Nacatl Avenger [0]; Grist −2 | CREATE_TRIGGER(REFLEXIVE) owning its targets and intervening-if; parent targets empty |
| Mentions (A24, M2) | Kappa Cannoneer, Blade of the Bloodchief | PROHIBIT be_blocked on SELF; PUT_COUNTERS(ATTACHED, 2) replaces s0 |
| Delay scope and RESULT (A25, A30, A34) | Phelia, Exuberant Shepherd; Goryo's Vengeance | Delayed sub-host holding MOVE and the dependent conditional PUT_COUNTERS; haste and delayed EXILE on Ref(RESULT,0) |
| "The exiled card" (A26) | Expressive Iteration | PERMIT on Ref(RESULT, exile seq), not LINKED |
| Last-known information (A27) | Engineered Explosives, The Filigree Sylex | COUNTERS_ON(Ref(SELF, lki=True)) |
| Target residue (A21) | Ugin, Eye of the Storms; Devourer of Destiny; Sink into Stupor | `target.colored` (WIDENING); UNMODELLED(TARGET) `target.zone_union` |
| Cast-trigger carriers (A41, A40) | Ulamog, the Ceaseless Hunger; Sowing Mycospawn | TRIGGERED(SELF_CAST) hosts indexed by `host_for_override`; `kicked_clause` = printed span |
| Instead forms (A15, A28) | Force of Negation, Into the Flood Maw, Gemstone Caverns, Consult the Star Charts | dest override; `target_alts`; leading-instead sibling; kicked REST |
| For-each amounts (A16) | Seasoned Pyromancer | CREATE_TOKEN amount FOR_EACH(RESULT_SIZE(discard, nonland)) |
| Recipient unions (A17) | Omnath, Locus of Creation | Two DAMAGE siblings in one group |
| Connectives (A14) | Risen Reef, Formidable Speaker | `otherwise` on the named MOVE; sentence-wide `if_you_do` |
| Resolution choices (A35, A19) | Galvanic Discharge; Ral, Leyline Prodigy −2 | PAY ANY_NUMBER then DAMAGE THAT_MUCH; DIVIDED (UNMODELLED(TARGET) until the solver feature) |
| Shuffle into (A18) | Green Sun's Zenith, Day's Undoing | MOVE(dest library/shuffle) |
| Filter classes (A19) | Monumental Henge | CardFilter.classes = {historic} |
| Target order (A20, A36) | Practiced Offense, Kozilek's Command, Warping Wail, Untimely Malfunction | Printed-order slots; `chosen_from_legacy` round-trips |
| May-scope (A29) | Zimone's Experiment | REST clause is a sibling, not nested |
| Simultaneous actors (A33) | Living End | Three ALL_PLAYERS specs; RESULT per actor |
| Domain masks (A39) | Wistfulness, Leyline Binding | Strict ETB view None; field masked to legacy None |
| Value-typed filters (A22) | Violent Outburst | FILTER controller 'you' non-executable in E0 |
| Owner pickers (A36) | Witch Enchanter | Unbound slot; strict ETB removal (harness row in E2) |

### 18.2 E0 test files

The E0 tests are the `e0_tests` output of this synthesis. Their files:
- `tests/test_effect_grammar_structure.py`
- `tests/test_effect_grammar_participants.py`
- `tests/test_effect_grammar_amounts_conditions.py`
- `tests/test_effect_grammar_linking.py`
- `tests/test_effect_grammar_pool_invariants.py`
- `tests/test_effect_resolver_sequencing.py`
- `tests/test_effect_spec_equivalence_tool.py`
- `tests/test_effect_neutrality.py`

Tests that read real cards use the session `card_db` fixture; the pool-wide tests carry `@pytest.mark.timeout(N)` with the measurement recorded. Workflow placement (2026-10-02): the files that need no card DB (`test_effect_grammar_amounts_conditions.py`, `test_effect_grammar_linking.py`, `test_effect_resolver_sequencing.py`; 177 tests, 2.3 s after the dispatcher skeleton landed) are added to the abstraction-contract pytest step. The shared-DB files (`test_effect_grammar_structure.py`, `test_effect_grammar_participants.py`, `test_effect_grammar_pool_invariants.py`, `test_effect_spec_equivalence_tool.py`) run only in the full-suite step: the session DB load alone takes about 17 s, past that step's 10 s per-file budget (the equivalence-tool file measured 20.8 s, of which about 4 s is test bodies), and the full suite already runs them on every PR. `tests/test_effect_neutrality.py` has not landed yet. The tool tests (2026-10-02): `tests/test_effect_parsers_ratchet.py` reads sources only and runs in the abstraction-contract pytest step; `tests/test_effect_census_tool.py`, `tests/test_host_resolution_harness.py` and the tool section of `tests/test_effect_spec_equivalence_tool.py` hold the pool-wide checks and run in the full-suite step.

### 18.3 Later-family regression tests (written red in the named step)

- **E1:**
  - `tests/test_effect_family_damage.py::test_a_fixed_burn_spell_with_a_flashback_line_resolves_only_its_damage`
  - `::test_an_instead_damage_upgrade_keeps_the_base_target`
  - `::test_a_one_damage_loyalty_line_switches_but_a_token_then_reflexive_damage_line_stays_legacy`
- **E1.b:**
  - `::test_damage_to_a_creature_is_dealt_through_the_damage_owner_and_death_waits_for_state_based_actions` (CR 704.5g)
- **E2:**
  - `tests/test_effect_family_zone.py::test_a_simultaneous_multi_player_zone_move_releases_enter_handling_after_every_player_has_moved` (CR 101.4)
  - `::test_a_delayed_exile_does_nothing_to_an_object_that_left_and_returned` (CR 400.7, 603.7c)
  - `::test_an_etb_exile_until_the_source_leaves_is_not_permanent_removal` (CR 610.3)
  - `::test_an_intervening_if_on_an_etb_head_gates_its_removal` (CR 603.4)
  - `::test_a_cast_trigger_resolves_from_its_host_not_an_override_string`
  - `::test_a_restated_instead_target_is_an_alternative_slot_at_resolution`
  - `::test_an_unbound_removal_slot_uses_the_owners_existing_picker`
  - `::test_a_host_with_an_unexecutable_verb_falls_back_to_its_legacy_apply_in_place`
  - `::test_a_sweep_keyed_on_counters_of_a_sacrificed_source_reads_last_known_information` (CR 608.2h)
  - `::test_later_executors_never_switch_a_host_outside_its_familys_strict_shape`
- **E3:**
  - `tests/test_effect_family_card_flow.py::test_a_declined_optional_search_skips_its_put_and_shuffle`
  - `::test_an_accepted_search_that_finds_nothing_still_shuffles`
  - `::test_if_you_dont_names_the_prior_action_by_its_verb_phrase`
  - `::test_rest_excludes_cards_taken_by_an_instead_sibling`
  - `::test_a_kicked_dig_takes_the_kicked_count`
  - `::test_a_land_destruction_spell_with_search_and_draw_riders_resolves_all_of_them`
- **E4:**
  - `tests/test_effect_family_tokens_counters.py::test_for_each_discarded_nonland_card_counts_the_result_of_the_discard`
  - `::test_a_token_offered_as_alternatives_is_chosen_at_resolution`
- **E4.b:**
  - `::test_an_energy_spend_amount_is_chosen_by_the_callback_with_the_damage_target_in_view`
  - `::test_a_reflexive_damage_after_token_creation_targets_after_the_token_exists` (CR 603.12)
- **E5:**
  - `tests/test_effect_family_continuous.py::test_a_per_member_conditional_grant_evaluates_each_member`
  - `::test_a_reflexive_trigger_chooses_its_targets_after_the_parent_action` (CR 603.12)
  - `::test_a_pronoun_after_a_self_counter_placement_binds_to_the_source`
  - `::test_impulse_permission_lasts_until_the_end_of_your_next_turn`
  - `::test_a_creatures_you_control_filter_covers_only_the_controllers_creatures`
- **E6:**
  - `tests/test_effect_family_stack_mana.py::test_countered_this_way_exiles_instead_of_the_graveyard` (CR 701.5a)
  - `::test_an_alternative_cost_static_never_resolves_as_an_effect` (CR 118.9)
  - `::test_an_activation_cost_reducer_modifies_the_cost_not_the_resolution` (CR 601.2f)
  - `::test_divided_damage_is_fixed_when_targets_are_chosen_and_an_illegal_share_is_not_dealt` (CR 601.2d, 608.2b)
  - `::test_a_mana_ability_with_a_rider_does_not_use_the_stack` (CR 605.3)
  - `::test_a_delayed_payment_from_a_spell_triggers_at_the_next_upkeep` (CR 603.7)
  - `::test_a_cost_reduction_static_applies_while_the_permanent_spell_is_cast` (CR 601.2f)
- **T2:**
  - `tests/test_trigger_stack.py::test_a_triggered_mana_ability_never_uses_the_stack` (CR 605.1b)
  - `::test_a_disjunctive_trigger_head_fires_on_each_event` (CR 603.2)
  - `::test_a_reflexive_trigger_with_an_intervening_if_is_rechecked_on_resolution` (CR 603.4)
  - `::test_enter_triggers_during_a_resolution_wait_until_the_ability_finishes` (CR 603.3)
