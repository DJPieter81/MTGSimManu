---
title: Modal-card oracle audit (post-Thraben)
status: active
priority: secondary
session: 2026-05-03
depends_on:
  - docs/diagnostics/2026-04-20_latent_bug_survey.md
tags:
  - engine
  - card-effects
  - audit
  - modal
summary: "Sweep of every modal handler in engine/card_effects.py to find Thraben-style oracle/handler gaps (printed mode missing, fabricated mode). Four modal cards inventoried (Pick Your Poison, Kolaghan's Command, Thraben Charm, Territorial Kavu). One P0 gap fixed this session: Territorial Kavu's modal attack trigger (loot / graveyard-exile) was entirely unimplemented despite being a 4-of mainboard in T1 deck Domain Zoo. Two deferred P1/P2 gaps documented for follow-up."
---

# Modal-card oracle audit — 2026-05-03

## Motivation

PR #244 fixed Thraben Charm: the engine had three modes (destroy
enchantment / exile graveyard / fake −1/−1 to creatures) but the
**actual** card prints (deal 2N damage to a creature / destroy
enchantment / exile graveyards). The engine had **invented** a
non-existent mode and **omitted** the printed damage mode, making
Thraben Charm useless against artifact boards.

The pattern is suspicious: modal handlers in the engine are easy to
write from memory, and from-memory often diverges from oracle text.
This audit checks every modal card registered in
`engine/card_effects.py` against `ModernAtomic.json` oracle text.

## Inventory

Search `engine/card_effects.py` for `EFFECT_REGISTRY.register(...)` →
105 registered effects. Cross-reference against oracle text containing
`Choose one`, `Choose two`, `Choose up to`, `Choose any number`, plus
manual review of split / saga / planeswalker handlers and any handler
whose body branches on a chosen mode.

Modal cards found:

| Card | Modal type | Oracle text |
|---|---|---|
| Pick Your Poison | Choose one (3 modes) | sac artifact / sac enchantment / sac flier |
| Kolaghan's Command | Choose two (4 modes) | return-creature / discard / destroy artifact / 2 dmg to any |
| Thraben Charm | Choose one (3 modes) | 2N damage / destroy enchantment / exile graveyards |
| Territorial Kavu | Attack trigger, Choose one (2 modes) | loot (discard, draw) / exile up to 1 from a graveyard |

Other handlers reviewed and **not modal in the printed-card sense**:

- `Wear // Tear` — fuse split card; engine fuses both halves on resolve, which is fine because all real-world casting paths go through fuse.
- `Ajani, Nacatl Pariah // Ajani, Nacatl Avenger`, `Ral, Monsoon Mage // Ral, Leyline Prodigy` — DFC planeswalkers; not modal spells in the resolver sense.
- `Valakut Awakening // Valakut Stoneforge` — modal DFC, but the spell side is single-mode.
- `Fable of the Mirror-Breaker // Reflection of Kiki-Jiki` — saga with sequential chapters, not a "choose one" spell.
- `Kolaghan's Command` is registered but no current deck plays it (zero live impact).

## Per-card analysis

### Pick Your Poison — P2 (sideboard 3-of in Izzet Prowess)

Oracle:

> Choose one —
> • Each opponent sacrifices an artifact of their choice.
> • Each opponent sacrifices an enchantment of their choice.
> • Each opponent sacrifices a creature with flying of their choice.

Engine handler: collapses the artifact and enchantment modes into a
single "best artifact-or-enchantment" branch (acceptable — the modes
are functionally identical except for the type filter), then falls
through to the flying mode. **Gap:** the final fallback is "opponent
loses 1 life — Toxic 1 mode", which is a fabricated mode that does
**not** appear on Pick Your Poison's oracle text. Same anti-pattern
as the Thraben −1/−1 fabrication.

Severity: **P2**. Triggers only when opponent has no artifact, no
enchantment, and no flier. In practice this is a no-op anyway in
those board states; the fabricated 1-point life loss is small. Fix is
trivial (delete the fallback branch and log a no-op).

### Kolaghan's Command — P2 (no current deck plays it)

Oracle:

> Choose two —
> • Return target creature card from your graveyard to your hand.
> • Target player discards a card.
> • Destroy target artifact.
> • Kolaghan's Command deals 2 damage to any target.

Engine handler: forks into two binary mode-pair branches:

1. `if artifacts: destroy artifact else: deal 2 damage (always to opponent's life)`
2. `if gy_creatures: return creature else: force discard`

Gaps:
- The "deal 2 damage" mode targets opponent's life only — oracle says
  "any target", so the engine cannot kill an opposing 2-toughness
  creature or push damage to a planeswalker.
- "Choose two" can legally pick e.g. (deal 2 + destroy artifact) or
  (return-creature + deal 2). The engine forces "destroy artifact"
  whenever a target artifact exists, which suppresses the "deal 2 to
  any target" mode in many board states. (Closest example: Storm
  with a [2/1 Ragavan + opposing Welding Jar] — the engine destroys
  the Welding Jar and pings the player for 2 instead of killing the
  Ragavan.)

Severity: **P2** because no registered Modern deck currently runs
this card in its main or sideboard list. Tracked here for the day a
Jund/Mardu list reintroduces it.

### Thraben Charm — fixed in PR #244

Mode list, mode selection now derive from oracle text and use
`creature_threat_value` for damage-mode target picking. See
`tests/test_thraben_charm_damage_mode.py`.

### Territorial Kavu — P0 (Domain Zoo 4-of mainboard) — **fixed this session**

Oracle:

> Domain — Territorial Kavu's power and toughness are each equal to
> the number of basic land types among lands you control.
> Whenever this creature attacks, choose one —
> • Discard a card. If you do, draw a card.
> • Exile up to one target card from a graveyard.

Engine handler:

```python
@EFFECT_REGISTRY.register("Territorial Kavu", EffectTiming.ETB, ...)
def territorial_kavu_etb(game, card, controller, ...):
    # Power/toughness handled by CardInstance._dynamic_base_power
    # which checks domain. Just log.
    ...
```

The ETB-timing handler only logs P/T. **No `EffectTiming.ATTACK`
handler is registered.** The oracle attack trigger (the entire modal
ability) is silently dropped on every attack of every Territorial
Kavu in every game. This is the highest-impact gap found by the
audit:

- Domain Zoo plays 4 Territorial Kavu mainboard.
- Kavu typically attacks 3–6 times per game when it survives.
- Each missed trigger costs Domain Zoo either a card-selection loot
  or a graveyard-hate point against Goryo's, Living End, Murktide.

Severity: **P0**. Affects T1 Domain Zoo's match WR vs graveyard
decks, and even in non-graveyard matchups removes a free loot per
attack.

#### Generic mechanic, not card-specific

The fix is a modal "loot vs. graveyard hate" attack trigger — the same
template applies to any future card with a modal attack trigger of
the form (loot | exile-from-graveyard). Mode selection is principled:

- Loot mode value = expected card EV swap (use `_threat_score` proxy:
  any non-land in hand is worth at least 1 mana-clock unit).
- Graveyard-exile mode value = the targeted card's CMC if it's a
  reanimation/flashback target, else 0. Mirrors the `_exile_graveyard`
  pattern already used in Thraben Charm and Relic of Progenitus.

Tie-break: prefer graveyard exile when an opposing graveyard target
has CMC ≥ 1 *and* the controller has no non-land hand to discard
(sequencing: take the free disruption when the loot is hollow).
Otherwise prefer the loot.

#### Test (rule-phrased, not card-named)

`tests/test_modal_attack_trigger_loot_vs_gy_hate.py` covers:

1. *Loot mode fires when no opposing graveyard targets exist* — with
   a non-land in hand and opponent's graveyard empty, the trigger
   discards a card and draws one. Hand size unchanged, but the top
   discard moves to the graveyard.
2. *Graveyard exile mode fires when opponent has a high-CMC GY target
   and controller has no useful loot* — opposing graveyard contains
   a 5-CMC card (e.g. Murktide Regent), controller's hand is all
   lands; the trigger exiles the GY target.
3. *No-op when both modes are dead* — empty hand, empty opposing
   graveyard → trigger logs and returns without crashing.

These tests name the **rule**, not Territorial Kavu. The same handler
template would catch any future modal-attack-trigger card.

## Deferred items

- **P2 Pick Your Poison toxic-mode fabrication** — fix when a deck
  loses a measurable number of games to the 1-point fallback or
  during the next P0/P1 sweep. Fix is one-line: replace fallback
  branch with no-op log.
- **P2 Kolaghan's Command** — fix when a deck registers it. Restore
  "deal 2 damage to any target" mode and let the chooser pick any two
  modes by EV instead of two binary fallbacks.

## Outcome

- Inventoried 4 modal cards.
- Found 1 P0 gap (Territorial Kavu) → fixed this session with a
  rule-phrased test and a generic modal-trigger handler.
- 2 P2 gaps documented above for future cleanup.
- 1 already-fixed (Thraben Charm).
