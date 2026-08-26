---
title: Tranche-3 acceptance on Creatures Toolbox — REFUTED; effect-kind whitelist is the next gate
status: active
priority: secondary
session: 2026-08-26
supersedes: []
superseded_by: []
depends_on: []
tags: [activation, tranche3, creatures-toolbox, calibration, acceptance]
summary: >
  Activation tranche 3 (sacrifice-another + discard costs, 358 abilities
  graduated DB-wide) measured against its named acceptance test: Creatures
  Toolbox field WR moved 15.0% -> 14.8% (n=20 Bo3) — the cost-parsing
  hypothesis for THIS deck is refuted. The remaining gate is the
  effect-kind whitelist (activation rule 9b permits only
  DAMAGE_ANY_TARGET / DRAW_N / PUMP_SELF_UEOT); a toolbox deck's
  activations are tutor effects, refused regardless of cost payability.
---

# Tranche-3 acceptance measurement (2026-08-26)

Creatures Toolbox field, n=20 Bo3, idle machine: **14.8%** (baseline from
the same-day n=20 matrix: 15.0%). No movement. The deck goes 0% against six
decks including the whole aggro tier.

The tranche itself stands on its own correctness (full suite 3,547 green,
zero anchor drift, all ratchets clean, 1,647 -> 1,289 blocked abilities
DB-wide) — but for this deck the binding constraint is one layer further:
`engine/activation.py` rule 9b executes only three effect kinds
(damage/draw/pump). Tutor-class activated effects ("search your library
for ... and put it onto the battlefield / into your hand") are refused
before any cost is charged, exactly as designed — the resolver cannot
execute them yet. A toolbox deck IS its tutor activations.

Next increment if pursued: ActivationEffectKind.TUTOR_* with a resolver
routed through the existing library-search + zone funnel, and AI valuation
via the tutor-as-finisher-access path (the generic mechanism from the
Storm/Wish work). Class size: every activated-tutor permanent in Modern.
Until then, Creatures Toolbox's 15% is EXPECTED behaviour, not a
calibration mystery — recorded here so nobody re-diagnoses it from scratch.
