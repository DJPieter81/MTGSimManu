"""Closed sub-grammars of the clause grammar (design doc 2026-09-29, L4):
target, participant, filter, amount, quantity, condition, duration,
destination and payload. Each is a closed table over normalised clause
text; an unknown phrase yields a typed UNMODELLED stage, never a guess.
"""
