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

## Update 2026-08-27 — the TUTOR_* increment landed

Three commits implement the increment named above (parse: `417c39b`;
engine resolver + rule-9b admission + `choose_tutor_target` callback
seam: `7bef3ae`; delivery-conditioned AI valuation + plan-best choice:
`2d9f0b7`). DB-wide: 37 activated-tutor lines now classify and execute
(4 `TUTOR_CREATURE_TO_BATTLEFIELD`, 33 `TUTOR_TO_HAND`); 117
search-your-library activated lines remain visible-but-refused residue
(union constraints, multi-card searches, rider sentences, non-creature
battlefield destinations — the never-half-execute discipline). Pinned by
`tests/test_activated_tutor_effects.py`. One WR-anchor drift observed
and expected (Eldrazi Tron's 4x Expedition Map line now fires): Eldrazi
Tron vs Amulet Titan seed=50500, winner unchanged, turns 12 → 13 —
snapshot deliberately NOT refreshed in the feature branch; refresh with
the merge. The acceptance re-measurement (Creatures Toolbox field WR)
has NOT been re-run — this doc stays `active` until that number exists.
