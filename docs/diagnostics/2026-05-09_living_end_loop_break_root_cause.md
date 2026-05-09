---
title: Living End loop break — suspend not enumerated as a legal play
status: active
priority: primary
session: 2026-05-09
supersedes:
  - docs/diagnostics/2026-05-04_living_end_audit.md
depends_on:
  - docs/diagnostics/2026-04-28_living_end_cascade_payoff.md
tags:
  - p0
  - wr-outlier
  - living-end
  - suspend
  - engine
  - get-legal-plays
  - loop-break
summary: |
  Living End vs Affinity 28% Bo3 (n=50, current matrix). Cascade-
  payoff gate, mulligan threshold scaling, Waker oracle data, and
  post-cascade aggression have all shipped — WR unmoved on the
  aggro slice (Affinity 28%, Pinnacle Affinity 32%, Ruby Storm 36%,
  Omnath 36%, Boros 40%, Tron 40%, Zoo 40%). Three+ commits without
  movement triggers the CLAUDE.md loop-break protocol.

  Root cause: engine/game_state.py::get_legal_plays does not
  enumerate suspend. CastManager.can_suspend / suspend_card are
  fully implemented; GameState exposes them. But legal_plays only
  returns land drops, hand-castable spells, flashback/escape, and
  cycling. Suspend is absent. EVPlayer (ai/ev_player.py:348)
  consumes legal_plays directly — so the AI never sees "suspend
  Living End" as an option and cannot score it. In ai/ this is
  visible as: suspend appears only as SUSPEND_ONLY_DEAD_PENALTY
  (ai/scoring_constants.py:3717) and its mulligan use sites
  (ai/mulligan.py:175,769-770). Zero AI play-layer call to
  suspend_card.

  Class size: every Modern suspend card (Living End, Ancestral
  Vision, Crashing Footfalls, Restore Balance, Wheel of Fate,
  Lotus Bloom, future printings). Class > 10, abstraction
  contract satisfied. Fix is engine-layer enumeration plus an
  AI scoring path that reuses existing clock/EV primitives.
notes:
  - "Bo3 is canonical. The 28% number is from metagame_data.jsx (n=50)."
  - "Pre-fix replay replays/affinity_vs_living_end_s60100.txt contains zero `suspend` action lines, confirming the symptom is invariant across all PR-K scoring work because the option was never enumerable."
---

# Living End — engine-layer suspend not enumerated

## Replay anchor

`replays/affinity_vs_living_end_s60100.txt` (3 May 2026, pre-recent
fixes) and the canonical Affinity matchup at seed 50000 — both show
the same trace structure for Living End:

- T1: cycles a creature (Striped Riverwinder / Architects of Will).
- T2: plays land. **Living End sits in hand.** AI does not suspend.
- T3-T4: continues cycling, optionally casts Force of Negation
  reactively. Living End remains in hand.
- T5+: dies to Affinity creatures before drawing a cascade enabler.

`grep "suspend" replays/affinity_vs_living_end_s60100.txt` returns
zero matches. The AI never picks the suspend action — across every
turn of every game in the replay file.

## Code-level evidence

`engine/game_state.py:600-621`:

```python
def get_legal_plays(self, player_idx: int) -> List[CardInstance]:
    player = self.players[player_idx]
    legal = []
    for card in player.hand:
        if card.template.is_land:
            if player.lands_played_this_turn < ... :
                legal.append(card)
        elif self.can_cast(player_idx, card):
            legal.append(card)
    # Include flashback and escape cards from graveyard
    for card in player.graveyard:
        if (card.has_flashback or card.template.escape_cost is not None) and \
           self.can_cast(player_idx, card):
            legal.append(card)
    # Include cycling cards from hand
    for card in player.hand:
        if card not in legal and self.can_cycle(player_idx, card):
            legal.append(card)
    return legal
```

Four sources: lands, casts, flashback/escape, cycling. **Suspend is
not a fifth source.** Living End (CMC 0, suspend-only per
`engine/cast_manager.py:96-100`) is unreachable through the cast
branch — `can_cast` returns False.

`ai/ev_player.py:348`:

```python
legal = game.get_legal_plays(self.player_idx)
```

The AI's input set never contains a suspend action.

`grep -rn "suspend\|SUSPEND" ai/ --include='*.py'` (excluding tests):

- `ai/scoring_constants.py:3717: SUSPEND_ONLY_DEAD_PENALTY: float = -100.0`
- `ai/mulligan.py:15: from ... import SUSPEND_ONLY_DEAD_PENALTY`
- `ai/mulligan.py:175: ... if c.template.cmc == 0 and Keyword.SUSPEND in c.template.keywords`
- `ai/mulligan.py:769-770: if card.template.cmc == 0 and Keyword.SUSPEND ... return SUSPEND_ONLY_DEAD_PENALTY`

All three matches are in the mulligan layer, none in the play layer.
The mulligan code treats suspend-only cards as a -100 penalty —
correct under the current world (because the AI cannot ever play
them) but actively wrong once suspend becomes a legal play.

## Why the prior PRs didn't move WR

| PR | What it changed | Why it could not help vs aggro |
|---|---|---|
| Cascade-as-payoff (2026-04-28) | `_payoff_reachable_this_turn` now sees `is_cascade` in hand | Gates cycler scoring; only fires when cascade enabler is in hand. Hands without the enabler are unaffected. |
| Mulligan threshold scaling | size-2 sets get threshold 1 | Reduces mulligans; doesn't help the kept hands without an enabler. |
| Waker oracle data (PR #287) | +2 cyclers | Adds graveyard fuel; useless if Living End never resolves. |
| Post-cascade aggression (PR #302) | Combat presses on PUSH_DAMAGE goal | Fires only post-resolution. Living End never resolves on these games. |

Every shipped fix is downstream of the cascade-resolution event.
None of them touch the path where Living End must come down without
a cascade in hand. That path is suspend, and suspend is not in
`legal_plays`.

## Class size

| Card | Suspend N | Mana cost | In current 16 decks? |
|---|---:|---|---|
| Living End | 3 | {2}{B}{B} | yes |
| Ancestral Vision | 4 | {U} | no |
| Crashing Footfalls | 4 | {1}{G} | no |
| Restore Balance | 6 | {W} | no |
| Wheel of Fate | 4 | {2}{R} | no |
| Lotus Bloom | 3 | {0} | no |
| Greater Gargadon | 10 | {1}{R} | no |

>10 unique Modern-legal printings, more than satisfying CLAUDE.md
class-size threshold. The fix is mechanic-driven: any future
suspend-keyword printing will route through the same path.

## Fix shape

### Engine (`engine/game_state.py`)

Extend `get_legal_plays` with a fifth source: hand cards where
`CastManager.can_suspend(self, player_idx, card)` returns True and
the card is not already in the legal list. Suspend is a special
action distinct from casting — the executor needs to know which
action to run. Approach: introduce a `LegalPlay` namedtuple with
`(card, action_kind)` where `action_kind` ∈
`{'cast', 'land', 'flashback', 'escape', 'cycle', 'suspend'}`, or
keep returning `CardInstance` and route via a sibling lookup
(`get_legal_actions` returning `Dict[CardInstance, List[ActionKind]]`).

The minimal disruption: extend `get_legal_plays` to return a list of
`(CardInstance, ActionKind)` tuples and update the two call sites
(`ai/ev_player.py:348`, anywhere else) to unpack the tuple. All
existing actions get explicit kind labels too — clarifies the
existing implicit dispatch in `EVPlayer`.

### AI (`ai/ev_player.py`)

When evaluating a suspend Play:

```
ev = cascade_target_resolution_ev × P(survive_to_resolution_turn)
     − mana_clock_impact(snap) × suspend_cost
```

- `cascade_target_resolution_ev` — already computed by
  `ai/combo_calc.py` for the cascade reanimation payoff.
- `P(survive_to_resolution_turn)` — `ai/clock.py` opponent-clock
  projection with horizon = `suspend_counters + 1`.
- `mana_clock_impact` — existing `ai/clock.py` primitive, same as
  used in `ai/ev_evaluator._compute_exposure_cost`
  (`ai/ev_evaluator.py:1275-1305`).

Gate the score so suspend only competes when:
1. `_payoff_reachable_this_turn(card, snap, game, player_idx)`
   returns False — i.e. no cascade enabler in hand.
2. `suspend_resolution_turn ≤ opponent_turns_to_lethal + 1` — there
   is time for the suspend to matter.

Both gates are derived from existing primitives; no magic numbers.

### Mulligan (`ai/mulligan.py`)

Once suspend is a legal play, the `SUSPEND_ONLY_DEAD_PENALTY` is no
longer correct as a blanket. Replace its use sites with a
conditional: penalty applies only if the kept hand cannot pay the
suspend cost in the first 2 turns. The penalty constant stays in
`scoring_constants.py` to avoid a magic-number violation.

### Gameplan (`decks/gameplans/living_end.json`)

`critical_pieces` entry `"Violent Outburst"` (line 112) is stale —
the card is not in the decklist. Remove it. Optional: add a
`suspend_payoffs: ["Living End"]` role for the EXECUTE_PAYOFF goal.

## Tests (rule-phrased)

`tests/test_suspend_legal_play_enumeration.py`:
1. `test_suspend_keyword_card_with_mana_is_legal_play` — Living End
   in hand + 4 lands MAIN1 → returned as a suspend Play. Red
   pre-fix.
2. `test_suspend_keyword_card_without_mana_not_legal` — 1 land →
   not returned. Regression guard.
3. `test_already_suspended_card_not_re-enumerated` — already in
   exile with counters → not returned.
4. `test_ancestral_vision_suspend_legal_uses_keyword` — proves the
   fix is generic across the suspend class.

`tests/test_suspend_payoff_ev_gate.py`:
5. `test_ai_suspends_living_end_with_no_cascade_enabler_in_hand` —
   Living End in hand, no Shardless Agent / Demonic Dread, T2 with
   4 mana → AI's chosen play is the suspend.
6. `test_ai_does_not_suspend_when_cascade_enabler_in_hand` —
   cascade enabler present → AI prefers cascade, suspend deferred.

## Validation

- `python -m pytest tests/ -q` — full suite passes including new
  tests.
- `python tools/check_abstraction.py` — no card-name strings or
  deck-name gates added.
- `python tools/check_magic_numbers.py` — no new bare literals.
- Bo3 spot-check vs Affinity (seed 50000) shows AI suspending
  Living End on T2 in the verbose log.
- `run_meta.py --field "Living End" -n 30` aggregate WR enters
  50–55% band (currently 53.3%, expected to widen on aggro slice).

## Loop-break compliance

This document satisfies CLAUDE.md's loop-break protocol:
- Three+ commits had targeted Living End without WR movement on the
  failing matchups (PR #287, PR #288, PR #302, plus 2026-04-28
  cascade-payoff fix).
- `run_meta.py --bo3` log was inspected (existing
  `replays/affinity_vs_living_end_s60100.txt`, freshly attempted
  Bo3 hit a separate prerequisite bug — see below — but the
  pre-fix replay's zero-suspend-lines is sufficient evidence).
- The exact turn EV diverges from correct play is named: T2 of
  Game 1 vs Affinity, when no cascade enabler is in hand.
- The responsible subsystem is named in writing (this doc):
  `engine/game_state.py::get_legal_plays`, with secondary AI
  scoring work in `ai/ev_player.py`.
- Frontmatter declares `status: active, priority: primary`.

Code resumes after this doc lands.

## Prerequisite bug noted (separate track)

`run_meta.py --bo3 "Living End" "Affinity" -s 50000` enters an
infinite loop: "Loaded 48 cards (0 errors) / DB too small —
auto-running merge_db.py" repeats indefinitely even though
`ModernAtomic.json` contains all 21795 cards. The 48 likely refers
to deck-unique-card count being mistaken for the DB threshold. This
blocks fresh diagnostic Bo3 runs but does not block the engine
suspend fix or its tests. Track separately.
