---
title: Living End 27% underperformance — EV divergence diagnostic
status: active
priority: primary
session: 2026-04-21
depends_on:
  - docs/experiments/2026-04-20_phase11_n50_matrix_validation.md
tags:
  - p0
  - wr-outlier
  - living-end
  - diagnostic
  - cascade
  - phase-12
summary: "Living End posts 26.8% flat / 23.2% weighted at N=50. Two replays (vs Boros s60110, vs Jeskai s60111) show two divergences: (1) cascade-fires-without-GY-asymmetry-check — the AI casts Demonic Dread a second time when opp's graveyard has 5 creatures and own has 1, reanimating the opp board; (2) Subtlety deployed sorcery-speed on own turn as a 3/3 body when its highest EV is as an instant response to opp's evoke Solitude or Ragavan cast."
---

# P0 WR outlier diagnostic — Living End 27% flat / 23.2% weighted

## Headline

Living End's key decision is *when* to cascade into Living End. The
engine fires the cascade correctly; the AI fires it **regardless of
which player's graveyard contains more value**. A Living End cast
while opp has 5 creatures in GY and Living End has 1 is a net loss.

## Evidence (seed 60110 — Living End vs Boros, G1)

Replay: `replays/living_end_vs_boros_energy_s60110.txt`
HTML: `replays/replay_living_end_vs_boros_energy_s60110.html`

First Living End (line 217-227, T3):
```
T3 P1: Cast Demonic Dread (1BR)
T3: Cascade hits Living End
T3: Living End resolves!
T3 P1: Architects of Will ETB: draw 1 (Living End)
T3: Living End returns Architects of Will for P1
T3: Living End returns Subtlety for P1
T3: Living End returns Guide of Souls for P2
```

First cascade: net +1 to Living End player (2 creatures returned
for P1, 1 for P2). Reasonable.

Second Living End (line 363-380, T5):
```
T5 P1: Cast Demonic Dread (1BR)          ← P1 fires a second cascader
T5: Cascade hits Living End
T5: Living End resolves!
T5: Living End returns Architects of Will for P1     ← 1 creature for P1
T5: Living End returns Ragavan for P2
T5: Living End returns Voice of Victory for P2
T5: Living End returns Guide of Souls for P2
T5: Living End returns Seasoned Pyromancer for P2
T5: Living End returns Elemental Token for P2        ← 5 creatures for P2
```

**Graveyard asymmetry at the moment of cast:** Living End has 1
creature (the first Architects, already back on board), opp GY has
5 creatures killed in combat since T3. Second Living End reanimates
opp's T3-T5 board as a 7+/6+ army and kills Living End on T6.

The AI's decision to cast the second Demonic Dread was made **without
checking graveyard composition**. By any reasonable EV calculation,
the cascade here is negative — firing it reassembles the opp's
board from the graveyard.

## Evidence (seed 60111 — Living End vs Jeskai, G1)

Replay: `replays/living_end_vs_jeskai_blink_s60111.txt`

Subtlety mis-cast (line 393-396):
```
T6 P1: Cast Subtlety (2UU)            ← Living End turn, sorcery speed
T6: Resolve Subtlety
T6 P1: Subtlety enters (no creature/PW spell on stack to target)
```

Subtlety's oracle: "Evoke {1}{U}. When Subtlety enters the battlefield,
you may return target creature or planeswalker spell to its owner's
hand." Cast on own turn with no spell on the stack, the ETB trigger
fizzles and Subtlety enters as a plain 3/3 evoke-eligible body.

Earlier in the same game (line 287-293, T5), Jeskai evoked Solitude
exiling Striped Riverwinder — **that was the moment Subtlety should
have been cast as an instant response**, re-casting Solitude would
cost the opponent an extra 5 mana and save Living End's only
reanimated creature.

## Diagnosis — AI layer

### (A) Cascade fires without GY asymmetry check

The "cast Demonic Dread / Shardless Agent" decision in
`ai/ev_player.py` scores the cascader as the payoff trigger for
`execute_payoff` goal. The scoring does not compare opp GY creature
count vs own GY creature count. A correct EV term:
```
living_end_ev = sum(threat(c) for c in own_gy)
              - sum(threat(c) for c in opp_gy)
```
This would naturally gate the second cast when opp GY outstrips
own GY.

### (B) Reactive creatures cast sorcery-speed

Subtlety (and by extension Solitude, Grief, Fury, Endurance — the
evoke-elemental package shared by multiple decks) has its highest
EV as an instant response, not as a deployed threat on own turn.
`ai/response.py` should hold these in hand unless a specific trigger
fires, or a clock pressure gate requires deploying the body.

Currently the deck runs only 1 Subtlety, but the same pattern
affects Goryo's (Grief) and potentially Azorius (Solitude). The
fix is not deck-specific.

## Candidate fix locations

Not fix proposals — diagnostic only.

- `ai/ev_player.py` — `_score_spell()` for cascade spells must
  consume a `cascade_target_ev()` subsystem that compares projected
  reanimation asymmetry. Use same primitives from
  `ai/ev_evaluator.creature_threat_value()` applied to both
  graveyards.
- `ai/response.py` — detect "evoke-elemental" class by oracle tag
  (`'when ~ enters the battlefield'` + `'target … spell'`) and gate
  cast-on-own-turn through the clock; default is hold for response
  window.

## Sideboard observation

Living End's SB (`decks/modern_meta.py` Living End block) has Force
of Negation and Endurance; neither fires in these replays. That's
consistent with the response-gate issue flagged in the Affinity/AzCon
diagnostics — low-cost interaction is never cast at the right
priority window. The fix is likely shared.

## Non-negotiables

- Option C: failing test for "cast Demonic Dread when GY asymmetry
  is unfavourable" before any scoring change.
- Oracle-driven detection of evoke elementals (`'evoke'` keyword +
  `'target creature or planeswalker spell'` in oracle), no
  hardcoded card names.
- N=50 matrix validation before merge.
