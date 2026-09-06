---
title: Creatures Toolbox — replay diagnosis; X-creature-tutor valuation is credited without a resolver, and the list is ~40% inert
status: superseded
priority: primary
session: 2026-08-28
supersedes: []
superseded_by: []
depends_on:
  - docs/diagnostics/2026-08-26_tranche3_toolbox_acceptance.md
tags: [creatures-toolbox, replay-diagnosis, tutor, activation, x-cost, effect-registry, gameplan-coherence, lowered-band]
summary: >
  First evidence-based diagnosis of Creatures Toolbox (both prior hypotheses
  were inferences from activation whitelists and both were falsified). 6 Bo3
  matches / 15 games at seeds 64000/64500/65000 vs Boros Energy (worst, 0%)
  and Goryo's Vengeance (best, 50%). Zero activated tutors fired in 15 games,
  so the post-#563 drop is NOT the deck mispricing its own activated tutors.
  Primary subsystem: ai/ev_player.py::_gate_x_tutor_payoff (landed in #563 as
  880cd84) credits delivery EV for any card carrying the parsed
  CardTemplate.x_creature_tutor_data shape, but RESOLUTION of that shape is
  still card-name-gated in engine/card_effects.py — 7 of the 8 cards in the
  class have no handler. Nature's Rhythm (a 4-of here) resolves to nothing:
  14 mana spent across 3 casts, 0 creatures delivered. The same gate's
  payoff-hold term anchors on Craterhoof Behemoth (MV 8) and froze Green Sun's
  Zenith in hand for 5 turns. Secondary: the list is ~40% inert in this engine
  (Leyline of Abundance, Badgermole Cub, Devoted Druid, Walking Ballista,
  Agatha's Soul Cauldron, Duskwatch Recruiter, Ouroboroid all no-ops) and the
  auto-generated gameplan declares empty engine/enabler roles. VERDICT: fix
  the resolver gap (class-wide, Chord of Calling / Eldritch Evolution too),
  then lower this row's band to ~10-20% until the mana-doubling and
  untap-counter classes exist.
---

**SUPERSEDED (2026-08-31).** §7's primary fix — a generic resolver for
`x_creature_tutor_data` shared by every card carrying the X-bound
creature-tutor shape — landed the same day this doc was written, in
`b9f4010`/PR #565 ("generic X-creature-tutor resolver"). Verified still live
at HEAD: `engine/oracle_resolver.py::_resolve_x_creature_tutor` is the sole
resolver, the Green Sun's Zenith per-card handler is deleted with a comment
pointing at the replacement, and `x_creature_tutor_data` now parses for
Nature's Rhythm, Chord of Calling, Finale of Devastation and Vision Quest
in addition to GSZ. `tests/test_x_creature_tutor_generic_resolution.py` +
`tests/test_x_cost_tutor_uses_paid_x.py` — 26 tests, green.

**The 25-deck matrix run on 2026-08-30 (n=20) measured Creatures Toolbox at
12.9%** — inside this doc's own predicted band (~10-20%) for a deck still
missing the mana-doubling-on-tap and untap-via-counter-cost classes (§5).
Per this doc's own verdict ("stop treating the row as a calibration
mystery"), 12.9% is not re-diagnosed here. The three engine gaps in §5
(Leyline of Abundance / Badgermole Cub mana-doubling — 8 cards; Devoted
Druid untap-via-counter — 4 cards + Vizier; Walking Ballista
remove-counter-as-cost) remain unbuilt and are the next lever if this row
is revisited, not a fresh replay pass.

---

# Creatures Toolbox — replay diagnosis (2026-08-28)

**Protocol note.** `docs/diagnostics/2026-08-26_tranche3_toolbox_acceptance.md`
is `status: falsified` after two successive inferred hypotheses (activation
cost parsing; then the TUTOR_* effect-kind whitelist) each landed and each
failed to move this row — the second made it worse (13.8 → 10.2). Its own
loop-break clause demanded a replay diagnosis before any third mechanism
guess. This is that diagnosis. No code was changed in producing it.

---

## 1. What the deck is trying to do — and what the gameplan says

**The paper deck** (`decks/modern_meta.py`, "Creatures Toolbox"):

| Role | Cards | Count |
|---|---|---|
| Mana dorks | Delighted Halfling 4, Devoted Druid 4, Birds of Paradise 2, Dryad Arbor 2 | 12 |
| Mana **doublers** | Leyline of Abundance 4, Badgermole Cub 4 | 8 |
| Combo | Devoted Druid + Vizier of Remedies 1 (infinite {G}) → Walking Ballista 1 (infinite damage) | 6 |
| Tutors | Green Sun's Zenith 4, Nature's Rhythm 4, Fiend Artisan 1, Duskwatch Recruiter 1 | 10 |
| Payoff | Craterhoof Behemoth 1, Ouroboroid 1 | 2 |
| Glue | Tyvar Jubilant Brawler 4, Agatha's Soul Cauldron 2, Eternal Witness 1, Shang-Chi 1 | 8 |
| Lands | 20 (incl. Dryad Arbor counted above) | — |

The known-correct line: T1-T2 dork; T2-T3 Leyline of Abundance **for free out of
the opening hand** or Badgermole Cub, so every dork taps for two; T3-T4 assemble
Devoted Druid + Vizier of Remedies for arbitrarily large green mana; convert with
Walking Ballista (remove counters to ping) or a huge Green Sun's Zenith /
Nature's Rhythm for Craterhoof Behemoth. The tutors are the redundancy layer —
a 60-card deck with a 1-of Vizier and a 1-of Ballista only functions because 10
cards can find them.

**The gameplan JSON** (`decks/gameplans/creatures_toolbox.json`, auto-generated
by `import_deck.py`) declares:

```json
{"goal_type": "DEPLOY_ENGINE", "card_roles": {"engines": [], "enablers": []}}
{"goal_type": "EXECUTE_PAYOFF", "card_roles": {"payoffs": ["Craterhoof Behemoth","Ouroboroid"], "enablers": []}}
"mulligan_keys": ["Badgermole Cub","Birds of Paradise","Delighted Halfling","Devoted Druid"]
```

**Finding E-1 (gameplan/list incoherence).** `DEPLOY_ENGINE` has an *empty*
engines list and an *empty* enablers list. The deck's actual engine — the
Druid/Vizier pair, the two mana-doubler classes, the tutor suite — is declared
nowhere. `mulligan_keys` names four mana dorks and no combo piece, no tutor,
no payoff, so the keep heuristic optimises for dork density; every observed keep
line reads e.g. `→ P1 KEEPS 7 — has key card(s): Badgermole Cub, Delighted
Halfling, 4 cheap spells`. The AI is playing a green ramp deck that ramps into
nothing. This is a real finding but it is *downstream* of §3: with the engine
cards inert, no honest role declaration exists to write.

**Finding E-2 (stale registration comment).** The deck comment claims
`Shang-Chi, Master of Kung Fu` "is not yet in ModernAtomic — resolves to an
engine placeholder". It is present in the DB at HEAD and was hard-cast in
game s65002. The comment is stale.

---

## 2. Matchups at n=20 (`metagame_results.json`, HEAD)

The committed matrix (`979ee5e`, 2026-08-27T16:15Z) predates the #563 merge
(2026-08-28T03:44Z) — it is the **13.8%** measurement, row average 13.75%.

| Worst | WR | Best | WR |
|---|---|---|---|
| Boros Energy | 0% | Goryo's Vengeance | 50% |
| Dimir Midrange | 0% | Amulet Titan | 30% |
| Domain Zoo | 0% | Ruby Storm | 30% |
| Pinnacle Affinity | 0% | Instant Reanimator | 25% |
| Eldrazi Tron / Izzet Prowess / AzCtrl-WSTv2 | 5% | Hollow One | 25% |

Replays were run against **Boros Energy (0%, worst)** and **Goryo's Vengeance
(50%, best)**.

---

## 3. The replays

Seeds 64000 / 64500 / 65000, Bo3, `run_meta.py --bo3`. 6 matches, **15 games,
zero timeout-truncated** (longest game T12).

```
replays/creatures_toolbox_vs_boros_energy_s64000.txt      Boros Energy 2-0
replays/creatures_toolbox_vs_boros_energy_s64500.txt      Boros Energy 2-0
replays/creatures_toolbox_vs_boros_energy_s65000.txt      Boros Energy 2-1
replays/creatures_toolbox_vs_goryos_vengeance_s64000.txt  Goryo's Vengeance 2-1
replays/creatures_toolbox_vs_goryos_vengeance_s64500.txt  Creatures Toolbox 2-1
replays/creatures_toolbox_vs_goryos_vengeance_s65000.txt  Goryo's Vengeance 2-0
```

Game record 4-11. **Two of the four game wins were `via mill`** — Goryo's
Vengeance decking itself on Griselbrand draw-7s while the Toolbox did nothing
relevant. The deck's own plan won 2 of 15 games.

### 3.1 Tutor-activation quantification (the post-#563 regression suspect)

| Metric | Count across 15 games |
|---|---|
| **Activated** tutor abilities activated (`TUTOR_*` kinds) | **0** |
| Fiend Artisan cast | 1 (as a 2-mana body, T9, never activated) |
| Duskwatch Recruiter cast | 2 (as 2/2 bodies, never activated — its ability parses `UNCLASSIFIED`) |
| Green Sun's Zenith cast | 4 (X=4 ×3 → Ouroboroid; X=2 ×1 → Badgermole Cub) |
| Nature's Rhythm cast | 3 (X=2, X=3, X=3) — **0 creatures delivered** |
| Craterhoof Behemoth cast | **0** |

**The class-(a) hypothesis in the brief is refuted by the evidence: the deck
never activates a tutor at all.** Fiend Artisan is the deck's only card in the
newly-executable activated class (verified against every registered deck: the
whole 25-deck field gained exactly two — Fiend Artisan ×1 here, Expedition Map
×4 in Eldrazi Tron). A 1-of that needs `{X}{B/G}`, a tap, sorcery speed, *and*
another creature to sacrifice never came up. The -3.6pp cannot have come from
this deck paying for activated tutors.

### 3.2 Per-game divergence table

`TB` = Toolbox won the game. "First divergence" = the first play where the AI
departs from the known-correct line in §1. Class letters follow the brief.

| # | Match / game | Result | First divergence | Class |
|---|---|---|---|---|
| 1 | boros s64000 G1 | OPP T7 | T4 casts Agatha's Soul Cauldron (inert artifact, 2 mana); T6 hard-casts Leyline of Abundance for 4 into an empty board while at 8 life | d + e |
| 2 | boros s64000 G2 | OPP T9 | **T4 Devoted Druid AND Vizier of Remedies both on battlefield — infinite mana available in paper, engine offers no line.** T4 the Druid then chump-blocks Ocelot Pride; T5 chump-blocks Ranger-Captain and dies | d |
| 3 | boros s64500 G1 | OPP T8 | T4 Walking Ballista at X=1 (a 1/1 that cannot ping — `remove_counter` is an unpayable cost); T5+T6 two more Devoted Druids as 0/2 blockers | d |
| 4 | boros s64500 G2 | OPP T9 | T5 hard-casts Leyline of Abundance (4 mana, zero effect) while Boros builds a 4-creature board | d |
| 5 | boros s65000 G1 | OPP T12 | T2 and T4 Walking Ballista at X=1 twice; no tutor cast for 12 turns | d |
| 6 | boros s65000 G2 | **TB** T10 | T7 Leyline (4 mana blank) preferred over the T7 Green Sun's Zenith in hand; GSZ finally cast T8 X=4 → **Ouroboroid, a vanilla 1/3 in this engine** — won on dork beatdown, not the plan | d + a′ |
| 7 | boros s65000 G3 | OPP T7 | **T6 Nature's Rhythm X=3 under `→ Goal: execute_payoff`, resolves, delivers nothing**; T7 Leyline; dead T7 | **a′ (primary)** |
| 8 | goryos s64000 G1 | OPP T9 | T6 **and** T7 hard-casts Leyline of Abundance *twice* — 8 mana into two blanks while dying on T9 | d |
| 9 | goryos s64000 G2 | TB T9 `mill` | T4 Druid + Vizier both live again, no combo; opponent decks itself | d |
| 10 | goryos s64000 G3 | OPP T7 | Duskwatch Recruiter cast as a 2/2, never activated (`UNCLASSIFIED`) | d |
| 11 | goryos s64500 G1 | TB T9 | **GSZ drawn T4, held T4–T8 (five turns), Craterhoof drawn T6 and discarded to Inquisition T9; a second GSZ discarded to Thoughtseize T9.** GSZ finally cast T9 X=4 → Ouroboroid; Nature's Rhythm T9 X=2 → nothing | **a′ (primary)** |
| 12 | goryos s64500 G2 | OPP T10 | T7 GSZ X=4 → Ouroboroid; **T8 Nature's Rhythm X=3 → nothing**; T9 Leyline blank | **a′ (primary)** |
| 13 | goryos s64502 G3 | TB T8 `mill` | Opponent mulled to 4 and decked itself; Toolbox cast five dorks | c |
| 14 | goryos s65000 G1 | OPP T9 | T7 GSZ at X=2 → Badgermole Cub (a 2/2). The X picker's "cheapest delivering X" is correct arithmetic on a library whose only large body, Craterhoof, is unreachable | d |
| 15 | goryos s65000 G2 | OPP T7 | T4 Ballista X=1, T5 Tyvar; no tutor, no engine; dead T7 | d |

Legend for the classes actually used: **a′** = tutor cast at the right *time*
for the right *reason* but delivering nothing (a resolver gap, not a targeting
error); **c** = opponent-side execution; **d** = engine/rules gap verified
against oracle text; **e** = gameplan/list incoherence.

---

## 4. Primary responsible subsystem

### `ai/ev_player.py::_gate_x_tutor_payoff` × `engine/card_effects.py::EFFECT_REGISTRY`

**The mechanism.** Commit `880cd84` (in PR #563 — "payoff-aware X for creature
tutors — cheapest delivering X, delivery-conditioned EV, payoff hold")
generalised X-creature-tutor **valuation and X-selection** to a parse-once typed
field, `CardTemplate.x_creature_tutor_data`. Both the engine's cast-time X
picker (`engine/cast_manager.py::pick_creature_tutor_x_value`) and the AI gate
now key off that *shape*:

```python
best_x, target, top = pick_creature_tutor_x_value(game, self.player_idx, x_budget, t)
if target is None:
    return min(ev, PATIENCE_GATE_REJECT_SENTINEL)
delivered_cmc = target.template.cmc or 0
per_mana = mana_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING
ev += (creature_tutor_x_net_value(best_x, delivered_cmc) * mult * per_mana)
```
— `ai/ev_player.py::_gate_x_tutor_payoff`

**Resolution did not generalise.** The shape is resolved only by hand-written
`EFFECT_REGISTRY` entries in `engine/card_effects.py`:

```python
@EFFECT_REGISTRY.register("Green Sun's Zenith", ...)
def green_suns_zenith_resolve(game, card, controller, targets=None, item=None):
    ...
    game.log.append(f"... Green Sun's Zenith finds {best.name}")
```

Class audit against the merged DB: **8 cards carry
"search your library for a … creature card … mana value X or less"; exactly 1
(Green Sun's Zenith) has a handler.** Unhandled: **Chord of Calling, Eldritch
Evolution, Nature's Rhythm, Citanul Flute, Celestial Reunion, Rocco Cabaretti
Caterer** (and Fiend Artisan's spell-shape sibling, now covered on the
activation path only). This is a mechanic class, not a card.

**The evidence, quoted.** Green Sun's Zenith resolving correctly:

```
T9 P1: Cast Green Sun's Zenith (4G) (X=4)
T9: Resolve Green Sun's Zenith
T9 P1: Green Sun's Zenith finds Ouroboroid
```

Nature's Rhythm, the same shape, the same turn, the same goal, in the same game:

```
    → Goal: execute_payoff
    [Mana] Tap Devoted Druid→G, Devoted Druid→G (paying for Nature's Rhythm, 4 mana remaining)
T9 P1: Cast Nature's Rhythm (2GG) (X=2)
    [Priority] P2 passes (no response)
T9: Resolve Nature's Rhythm
T9: Nature's Rhythm moved stack -> graveyard (resolution)
```

No `finds` line. No creature enters. The X was chosen, the mana was charged, the
spell resolved into the graveyard. Oracle text confirms it should have delivered:
*"Search your library for a creature card with mana value X or less, put it onto
the battlefield, then shuffle."* Nature's Rhythm parses as
`x_creature_tutor_data = {'colors': []}` (any colour) — the picker handles the
empty constraint correctly, so the AI is promised a real target every time.

Across the 15 games: **3 casts, 14 mana, 0 creatures delivered**, all three
under `→ Goal: execute_payoff`. Nature's Rhythm is a **4-of** in this list.

**The second term of the same gate — the payoff hold — is the other half.**
It anchors on `top_candidate`, the library's payoff ceiling, which for this deck
is Craterhoof Behemoth at MV 8:

```python
forfeited_gap = top_cmc - delivered_cmc
if forfeited_gap > delivered_cmc:
    ...
    payoff_total_cost = (t.cmc or 0) + top_cmc * mult   # 1 + 8 = 9 mana
    turns_to_afford = max(0, payoff_total_cost - int(snap.my_mana))
    if turns_to_afford <= snap.opp_clock:
        return min(ev, PATIENCE_GATE_REJECT_SENTINEL)
```

Against a slow opponent `snap.opp_clock` is long, so the hold latches. Observed
verbatim in `creatures_toolbox_vs_goryos_vengeance_s64500.txt` G1:

```
 231 [Draw] P1 draws: Green Sun's Zenith          (turn 4)
 357 [Draw] P1 draws: Craterhoof Behemoth         (turn 6)
 512 T9 P2: Inquisition of Kozilek discards Craterhoof Behemoth
 519 T9: Green Sun's Zenith moved hand -> graveyard (forced discard)
 547 T9 P1: Cast Green Sun's Zenith (4G) (X=4)
 550 T9 P1: Green Sun's Zenith finds Ouroboroid
```

Green Sun's Zenith sat in hand from T4 to T9 — through five untapped turns on
which the deck instead cast Devoted Druid (T5), Devoted Druid (T6) and Leyline
of Abundance (T7) — waiting on a 9-mana Craterhoof line the deck cannot reach,
and a second copy plus the Craterhoof itself were stripped by discard first. The
hold's trajectory model (`one land drop per turn`) is sound arithmetic; it is
being applied to a deck whose *actual* acceleration — the two mana-doubler
classes — does not exist in the engine (§5), so the ceiling it waits for is
permanently out of reach.

---

## 5. Why the row is at 10-14% independent of the gate: the list is ~40% inert

Verified against oracle text via the parsed `CardTemplate` at HEAD.

| Card | Copies | Oracle ability | Engine status |
|---|---|---|---|
| Devoted Druid | 4 | "Put a -1/-1 counter on this creature: Untap this creature." | `UNCLASSIFIED` effect **and** `unpayable=('put_counter',)` — the combo cannot be attempted |
| Vizier of Remedies | 1 | -1/-1 replacement static | `activated_abilities: 0`; combo partner dead regardless |
| Walking Ballista | 1 | "Remove a +1/+1 counter: 1 damage to any target" | kind is `DAMAGE_ANY_TARGET` but `unpayable=('remove_counter',)` — **cast 8× in 15 games as a vanilla 1/1** |
| Leyline of Abundance | 4 | free from opening hand; "whenever you tap a creature for mana, add an additional {G}"; `{6}{G}{G}` pump | no "begin the game with" support anywhere in `engine/`; no support for the tap-for-mana trigger; `{6}{G}{G}` is `UNCLASSIFIED`. **A 4-mana blank enchantment — hard-cast 10× (40 mana) across 15 games** |
| Badgermole Cub | 4 | earthbend 1 ETB; same mana-doubling trigger | `triggered_abilities: None`; a vanilla 2/2. Cast 27× — the deck's most-cast card is a bear |
| Agatha's Soul Cauldron | 2 | grants exiled creatures' activated abilities | `UNCLASSIFIED`; inert |
| Duskwatch Recruiter | 1 | `{2}{G}`: look at top three, take a creature | `UNCLASSIFIED` |
| Ouroboroid | 1 | begin-combat: X +1/+1 counters on each creature | `triggered_abilities: None` — attacks as a **1/3 for three consecutive turns** in s65001; it is one of the gameplan's two declared payoffs |
| Craterhoof Behemoth | 1 | the actual payoff | cast **0** times in 15 games |

`grep -rn "additional {G}\|tap a creature for mana\|earthbend" engine/ ai/`
returns nothing. Two mana-doubling classes (8 cards) and an untap-via-counter
class (4 cards + 1 partner) do not exist.

**Mana provably burned on zero-effect spells:** Leyline 10 casts × 4 = 40,
Nature's Rhythm 3 casts = 14, Agatha's Cauldron 2 casts × 2 = 4. **58 mana over
15 games ≈ 3.9 per game**, concentrated on turns 5-8 — the window in which the
deck lost 11 of its 15 games (mean loss turn 8.5).

---

## 6. Answering the brief's question directly

> Why did unlocking tutors LOWER the WR?

**It was not the activated tutors.** They fired zero times in 15 games; the
whole 25-deck field gained exactly two activated-tutor lines (Fiend Artisan ×1
here, Expedition Map ×4 in Eldrazi Tron), and Toolbox already sat at 5% vs
Eldrazi Tron with nowhere to fall.

**It was the other tutor change bundled into the same PR.** `880cd84`
generalised X-creature-tutor *valuation and X-selection* to the parsed shape
while *resolution* stayed card-name-gated. Two consequences land hardest on the
deck holding the most copies of the unresolved shape:

1. **Paid-for nothing.** Nature's Rhythm (4-of) is now confidently cast under
   `Goal: execute_payoff` for 4-5 mana and resolves to nothing. Before the
   commit the shape had no delivery credit and X defaulted to available mana;
   after it, the gate credits `creature_tutor_x_net_value(best_x, delivered_cmc)
   × per_mana` for a delivery that never happens. This is precisely the brief's
   framing — "the AI is now paying for something whose value it mis-prices" —
   but the mis-priced card is a *sorcery* the whitelist work never touched.
2. **Displaced a better line.** The gate's payoff-hold froze Green Sun's Zenith
   in hand for five turns against a Craterhoof ceiling the deck's (nonexistent)
   ramp can never fund.

**Measurement hygiene caveat, stated plainly.** The 13.8 → 10.2 comparison mixes
seed grids: 13.8 is the matrix row average (`seed_start=40000`, `979ee5e`),
10.2 is a field run (`run_field` uses `MATCHUP_SEED_START=50000`). The
field-to-field comparison in the falsified doc is 14.8 → 10.2 = -4.6pp on
n≈480 matches, roughly 2σ. The mechanism above is real and quoted from logs;
the exact magnitude of the drop is not resolvable at n=20.

---

## 7. Verdict

**Both, in order.**

1. **A class-wide engine fix is owed and is not a patch.** Give
   `x_creature_tutor_data` a generic resolver routed through the same
   library-search + zone funnel the activated `TUTOR_CREATURE_TO_BATTLEFIELD`
   kind already uses, so that valuation and resolution key off the same parsed
   shape. Class size 8 in Modern (Chord of Calling and Eldritch Evolution are
   the two that matter outside this deck). Failing test, rule-phrased:
   *"an X-cost creature tutor delivers a creature whose mana value is within X,
   regardless of whether the card has a named effect handler"* — red on
   Nature's Rhythm today, green on Green Sun's Zenith today. The paired
   invariant is stronger and worth pinning: *"a card the AI values through
   `_gate_x_tutor_payoff` must have a resolver"* — a valuation gate keyed on a
   parsed shape must never outrun the resolver for that shape.

2. **Then lower this row's band, and do not re-diagnose it against the current
   list.** Even with the resolver fixed, the deck's engine is three missing
   mechanic classes deep: mana-doubling-on-tap (8 cards), untap-via-counter-cost
   (Devoted Druid, 4 cards, the combo's whole point), and
   remove-counter-as-cost (Walking Ballista, the kill). Until those exist,
   Creatures Toolbox is a green deck of vanilla bears with 8 blank cards in the
   mainboard and a 5-mana do-nothing 4-of, and **~10-20% is the correct expected
   band**, not an outlier. Two of its four game wins in this sample came from the
   opponent decking itself. Record the band; stop treating the row as a
   calibration mystery.

**Explicitly out of scope of the primary claim:** the gameplan JSON's empty
engine/enabler roles (§1, Finding E-1) are a genuine incoherence, but writing
honest roles is impossible while the engine cards are inert — fix the mechanics
first, then regenerate the gameplan and re-measure.

---

## Replay artefacts

| File | Result | Games |
|---|---|---|
| `replays/creatures_toolbox_vs_boros_energy_s64000.txt` | Boros Energy 2-0 | 2 |
| `replays/creatures_toolbox_vs_boros_energy_s64500.txt` | Boros Energy 2-0 | 2 |
| `replays/creatures_toolbox_vs_boros_energy_s65000.txt` | Boros Energy 2-1 | 3 |
| `replays/creatures_toolbox_vs_goryos_vengeance_s64000.txt` | Goryo's Vengeance 2-1 | 3 |
| `replays/creatures_toolbox_vs_goryos_vengeance_s64500.txt` | Creatures Toolbox 2-1 | 3 |
| `replays/creatures_toolbox_vs_goryos_vengeance_s65000.txt` | Goryo's Vengeance 2-0 | 2 |

15 games, none truncated.
