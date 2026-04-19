"""Invariant test suite.

Each invariant here is a property that must hold across the entire
engine — independent of any single card. Invariants are graduated
from targeted bug-fix tests once the pattern is clearly reusable.

Current invariants:
  * target_fidelity — declared target == resolved target (Bug 1 graduate).
"""
