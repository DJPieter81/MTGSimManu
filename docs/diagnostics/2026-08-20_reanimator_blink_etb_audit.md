---
title: Reanimator + Jeskai Blink ETB/scoring audit — findings and fixes
status: active
priority: primary
session: 2026-08-20
tags: [ai, engine, etb, blink, saga, reanimator, win-rate]
summary: >
  Three parallel Bo3-replay audits (Goryo's Vengeance, Instant Reanimator,
  Jeskai Blink) found generic engine/AI gaps in ETB payoffs and value-spell
  scoring. Atraxa's reveal-by-type ETB and proactive blink ETB-retrigger are
  FIXED (Instant Reanimator 25->53%, Jeskai 29->43%). Remaining generic items
  are tracked below.
---

# Reanimator + Jeskai Blink audit

## FIXED this session

1. **Atraxa reveal-top-N-by-type ETB** (`engine/oracle_resolver.py`) — was a
   silent no-op; the shared 4-of payoff of both reanimator decks entered as a
   vanilla body. Generic oracle-driven branch. **Impact: Instant Reanimator
   25% → 53.1% (Bo1 n=4).** Also extends `_WORD_TO_NUM` to ten.
2. **Proactive blink credits ETB re-trigger** (`ai/ev_player.py`) — the
   main-phase scorer gave no credit for blinking an on-board `etb_value`
   creature, so flicker decks never cast Ephemerate for value. Wired the
   existing `BLINK_ETB_RETRIGGER_BONUS` into `_score_spell`. **Impact: Jeskai
   Blink 29% → 42.7%.**

## OPEN — generic, ranked by breadth/impact

3. **Saga chapter value is unpriced → Fable / Urza's Saga never cast**
   (`ai/ev_evaluator.py::_project_spell`). Token/ETB-value projection is gated
   behind `if t.is_creature:` (~line 2009); a Saga is an enchantment, so its
   Chapter-I ETB (Fable's 2/2 Treasure-goblin; Urza's Saga's construct/tutor
   plan) is projected as nothing → EV ~0 → never cast. **Broadest open item:**
   Fable (Jeskai Blink, Izzet Prowess), Urza's Saga (Affinity, Pinnacle
   Affinity). Fix must credit chapter-I immediate value for saga enchantments.
   Note: the Kiki-Jiki back-face activated ability ("{1},{T}: copy a
   creature") is also not enumerated as a play — the next blocker once Fable
   is cast.

4. **ETB "mill N"** (`engine/oracle_resolver.py`) — no resolver branch; 84
   Modern permanents carry a "when ~ enters, mill N" ETB (Fallaji
   Archaeologist et al.). Fallaji also has a "you may put a noncreature,
   nonland from the milled into hand" rider (a mill-and-select variant).

5. **Attack-trigger effect class: "opponent sacrifices / discards / loses N
   life"** (`engine/oracle_resolver.py::resolve_attack_trigger`) — only handles
   damage / lifegain / mobilize / token attack clauses. Archon of Cruelty's
   enters-or-attacks drain is dropped on the attack half (ETB half is a
   hardcoded handler). Generic "enters or attacks" repeated-trigger + drain.

6. **Reanimator keep-the-fatty line under-executed** (AI) — Goryo's Vengeance
   reanimates a fatty (now with working ETBs) but must Ephemerate it to clear
   the end-of-turn-exile rider and keep it; the AI under-executes this, so the
   body is exiled after one swing and the deck times out vs control. The blink
   ETB credit (#2) is a step toward it; full fix needs the reanimate→blink
   sequencing to reliably fire. (Goryo's is a 1.5%-meta fragile deck; its Bo1
   field number is high-variance.)

7. **Consign to Memory can't counter triggered abilities**
   (`engine/card_effects.py:1862`) — only pops colorless spells; the "counter
   target triggered ability" half is unmodelled, so vs colored decks it
   counters nothing. Small class but a genuine missing mechanic.

8. **Undying/Persist return does not re-fire ETB**
   (`engine/permanent_effects.py:231-256`) — undying/persist re-entry re-adds
   the instance but never calls `_handle_permanent_etb`, unlike `reanimate()`.
   Generic (every ETB creature × undying/persist); low impact in the audited
   decks.

9. **Ephemerate/self-blink mis-tagged `destroy_target_creature`**
   (`engine/card_database.py:654`) — any parsed effect with
   `target_type=="creature"` adds the removal tag; the self-blink case
   ("exile target creature you control, then return it") is not excluded. Data
   hygiene; consumers currently re-guard by re-reading oracle text.

## Systemic meta-finding

Findings 3/4/5 share a root: **a card with a parsed ETB/attack ability object
but no `EFFECT_REGISTRY` handler and no matching `resolve_*_from_oracle` branch
fails SILENTLY** — the silent-miss diagnostic (`spell_resolution.py:405-419`)
is skipped because an ability object exists. Worth a diagnostic that flags
"ability parsed, effect unimplemented" so future payoff gaps surface loudly.
