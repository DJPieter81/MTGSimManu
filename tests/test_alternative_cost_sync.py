"""Structural guard: can_cast and cast_spell must stay in sync for every
alternative-cost mechanic (CR 118.8).

Rule: if can_cast approves a card for casting via an alternative cost
(Warp, Dash, Evoke, Escape, Improvise, Delve, Phyrexian, …), then
cast_spell MUST have a corresponding payment branch for that same cost.
A mechanic present in can_cast but absent from cast_spell creates a
silent infinite loop where the AI repeatedly selects a card that can_cast
approves but cast_spell silently rejects — consuming the entire main phase
without advancing board state.

This is a structural/source-inspection test. It reads cast_manager.py
directly, extracts the body of each function, and asserts both functions
reference the same CardTemplate field for each registered mechanic.

Adding a new alternative-cost mechanic requires updating ALTERNATIVE_COST_FIELDS
in this test at the same time — CI will fail otherwise, making the
synchronization requirement explicit.
"""

import re
import pathlib

import pytest

_CAST_MANAGER = pathlib.Path(__file__).parent.parent / "engine" / "cast_manager.py"

# For each mechanic: the CardTemplate field name that both can_cast and
# cast_spell must reference. The name is the *field* (not the oracle word),
# because can_cast must gate on the parsed template property, not on a raw
# oracle-text substring (which was the original bug: "warp" in oracle → True
# in can_cast, but no warp-payment in cast_spell → infinite loop).
ALTERNATIVE_COST_FIELDS = [
    "evoke_cost",
    "dash_cost",
    "warp_cost",
    "escape_cost",
]


def _extract_function_body(source: str, func_name: str) -> str:
    """Return the raw text of the named function/method from source.

    Works for both top-level functions (``def foo``) and class methods
    (``    def foo``) — captures text from the def line to the next def or
    class at the same or lower indentation level.
    """
    # Find the function at any indentation level
    m_start = re.search(
        rf"^( *)def {re.escape(func_name)}\b", source, re.MULTILINE
    )
    if m_start is None:
        raise AssertionError(
            f"Function/method '{func_name}' not found in cast_manager.py — "
            f"update this test if the function was renamed."
        )
    indent = m_start.group(1)
    start = m_start.start()
    # Find the next def or class at the same (or less) indentation
    end_pattern = rf"^(?:{indent}def |{indent}class |{indent[:-4] if len(indent) >= 4 else ''}def |{indent[:-4] if len(indent) >= 4 else ''}class )"
    m_end = re.search(end_pattern, source[m_start.end():], re.MULTILINE)
    if m_end:
        end = m_start.end() + m_end.start()
    else:
        end = len(source)
    return source[start:end]


@pytest.fixture(scope="module")
def cast_manager_source():
    return _CAST_MANAGER.read_text()


@pytest.fixture(scope="module")
def can_cast_body(cast_manager_source):
    return _extract_function_body(cast_manager_source, "can_cast")


@pytest.fixture(scope="module")
def cast_spell_body(cast_manager_source):
    return _extract_function_body(cast_manager_source, "cast_spell")


class TestAlternativeCostSynchronization:
    """Every alternative-cost field in can_cast must appear in cast_spell too.

    This ensures that whenever can_cast returns True via an alternative-cost
    path, cast_spell has a corresponding payment branch — preventing the
    silent-return-False loop that collapsed Affinity's win rate from ~55% to
    ~24% when the Warp mechanic was added to can_cast without a matching
    payment path in cast_spell.
    """

    @pytest.mark.parametrize("field", ALTERNATIVE_COST_FIELDS)
    def test_field_present_in_can_cast(self, field, can_cast_body):
        assert field in can_cast_body, (
            f"Alternative-cost field '{field}' is not referenced in can_cast. "
            f"Either add the can_cast legality check or remove '{field}' from "
            f"ALTERNATIVE_COST_FIELDS in this test."
        )

    @pytest.mark.parametrize("field", ALTERNATIVE_COST_FIELDS)
    def test_field_present_in_cast_spell(self, field, cast_spell_body):
        assert field in cast_spell_body, (
            f"Alternative-cost field '{field}' is checked in can_cast but "
            f"MISSING from cast_spell. This creates a silent loop: the AI "
            f"selects the card (can_cast → True), cast_spell rejects it with "
            f"no payment path (→ False), the card stays in hand, and the AI "
            f"retries indefinitely. Add the payment branch to cast_spell."
        )

    @pytest.mark.parametrize("field", ALTERNATIVE_COST_FIELDS)
    def test_flag_tracking_in_cast_spell(self, field, cast_spell_body):
        """cast_spell must set card._X to track the alternative cast mode.

        The tracking flag is read by turn_manager (end-of-turn exile for Warp,
        return-to-hand for Dash) and by oracle_resolver (sacrifice after ETB
        for Evoke). A missing flag means post-cast effects never fire.
        """
        mechanic = field.replace("_cost", "")  # e.g. "warp_cost" → "warp"
        flag = f"card._{mechanic}"             # e.g. "card._warp"
        assert flag in cast_spell_body, (
            f"cast_spell does not set '{flag}' after a {mechanic} cast. "
            f"Without this flag, end-of-turn effects for {mechanic} "
            f"(exile, return-to-hand, sacrifice) will never fire."
        )
