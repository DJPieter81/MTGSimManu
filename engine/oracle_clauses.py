"""Clause-level scoping primitives for oracle-text predicates (E5).

Rationale
---------
Oracle text is written as a sequence of *abilities* — one paragraph
(newline-separated block) per ability — each made of one or more
*sentences* (period-separated). Under CR 603.1 a triggered ability's
trigger condition and its effect live in the SAME ability text:
"When/Whenever/At [condition], [effect]." A predicate that tests two
substrings against the WHOLE oracle text ("'enters' in text and
'deals N damage' in text") therefore over-approximates: a card whose
ETB trigger scries and whose *separate* attack trigger deals damage
would falsely classify as "ETB deals damage". Reminder text on an
unrelated line (cycling's "Discard this card: Draw a card.") is a
canonical false-positive source.

Two scopes, two helpers:

* :func:`split_abilities` — paragraph scope (split on newlines).
  The correct unit for pairing a TRIGGER with its EFFECT: an ability's
  effect may span several sentences ("When ~ enters, target opponent
  reveals their hand. You choose a nonland card from it and exile
  that card."), and follow-on sentences ("If you do, …") belong to
  the same ability. Sentence-scoping such predicates produces false
  negatives on multi-sentence abilities.

* :func:`split_clauses` — sentence scope (split on ``.`` / newline,
  the split already proven in ``ai/ev_evaluator._project_token_bonus``).
  The correct unit for AMOUNT EXTRACTION: when a card has two
  "deals N damage" clauses with different amounts, the number must
  come from the sentence carrying the dispatching trigger phrase.
  Conjoined triggers ("When X enters and whenever an opponent draws a
  card, X deals 1 damage…") share one sentence with their effect, so
  sentence scope handles them.

Pure functions, no state. Finding E5 from
``docs/diagnostics/2026-07-05_calibration_probe_findings.md``.
"""
from __future__ import annotations

import re
from typing import List, Optional

# Sentence terminators: period or newline (one or more). Matches the
# clause model used by ai/ev_evaluator._project_token_bonus.
_CLAUSE_SPLIT = re.compile(r'(?:\.|\n)+')

# Ability separators: newline runs. One oracle paragraph = one ability.
_ABILITY_SPLIT = re.compile(r'\n+')


def split_clauses(oracle: str) -> List[str]:
    """Split oracle text into sentence-level clauses on ``.`` / newline
    runs.

    Case-agnostic (the text is returned as given — callers that
    lowercase their oracle get lowercase clauses back). Empty and
    whitespace-only fragments are dropped; surviving clauses are
    stripped of surrounding whitespace.

    >>> split_clauses("First clause. Second clause.\\nThird clause")
    ['First clause', 'Second clause', 'Third clause']
    """
    if not oracle:
        return []
    return [c.strip() for c in _CLAUSE_SPLIT.split(oracle) if c.strip()]


def split_abilities(oracle: str) -> List[str]:
    """Split oracle text into ability paragraphs on newline runs.

    One oracle paragraph = one ability; sentences within a paragraph
    continue the SAME ability (multi-sentence effects, "If you do, …"
    riders). Use this scope for trigger→effect predicates; use
    :func:`split_clauses` when a single sentence must carry the match
    (amount extraction).

    >>> split_abilities("When ~ enters, reveal a card. Exile it.\\nFlying")
    ['When ~ enters, reveal a card. Exile it.', 'Flying']
    """
    if not oracle:
        return []
    return [a.strip() for a in _ABILITY_SPLIT.split(oracle) if a.strip()]


def any_clause_with(oracle: str, *substrings: str) -> bool:
    """True iff a SINGLE sentence-level clause contains ALL
    ``substrings``. Case-insensitive.

    Sentence-scoped co-occurrence — the strictest form. Prefer
    :func:`any_ability_with` for trigger→effect predicates (effects may
    span sentences); prefer this when the phrases are known to share
    one sentence (e.g. a rider like "You lose 2 life").
    """
    return _any_scope_with(split_clauses(oracle), substrings)


def any_ability_with(oracle: str, *substrings: str) -> bool:
    """True iff a SINGLE ability paragraph contains ALL ``substrings``.
    Case-insensitive.

    The clause-scoped replacement for the whole-text conjunction
    anti-pattern ``all(s in oracle for s in substrings)`` on
    trigger→effect predicates: a trigger word and an effect verb must
    co-occur in one ability (CR 603.1) — not merely anywhere on the
    card.
    """
    return _any_scope_with(split_abilities(oracle), substrings)


def _any_scope_with(scopes: List[str], substrings) -> bool:
    if not scopes or not substrings:
        return False
    subs = [s.lower() for s in substrings]
    return any(all(s in scope.lower() for s in subs) for scope in scopes)


def clause_containing(oracle: str, pattern: str) -> Optional[str]:
    """Return the first sentence-level clause with a regex match for
    ``pattern``, or ``None`` when no clause matches.

    Case-insensitive search. Use this to scope a detail/amount
    extraction to the clause that carries the predicate's trigger
    phrase, so a card with two ``deals N damage`` clauses yields the
    amount from the RIGHT one.
    """
    return _scope_containing(split_clauses(oracle), pattern)


def ability_containing(oracle: str, pattern: str) -> Optional[str]:
    """Return the first ability paragraph with a regex match for
    ``pattern``, or ``None`` when no ability matches. Case-insensitive.
    """
    return _scope_containing(split_abilities(oracle), pattern)


def _scope_containing(scopes: List[str], pattern: str) -> Optional[str]:
    if not scopes:
        return None
    compiled = re.compile(pattern, re.IGNORECASE)
    for scope in scopes:
        if compiled.search(scope):
            return scope
    return None
