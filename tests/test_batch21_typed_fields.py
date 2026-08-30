"""Batch 21 typed-field migration tests.

# Mechanic: Phyrexian mana symbol count (rule, not a card)

The pip count feeding ai/ev_player.py (EV life-cost discount) and
engine/cast_manager.py (Phyrexian payment at cast) is parsed from the printed
MANA COST into ManaCost.phyrexian, not from oracle text: the reminder clause
names the symbol exactly ONCE however many pips the cost carries (CR 107.4f),
so an oracle-derived count is wrong for every multi-pip cost.  The mechanic
itself is pinned by tests/test_phyrexian_mana_cr107_4f.py; what is pinned here
is that the count is a property of the cost.

# Mechanic: channel clause (rule, not a card)

channel_clause replaces two oracle.find() calls in ai/response_enumeration.py:232
that locate the Channel — / Channel - marker to extract the channel clause text.

# Structural: target_solver.py exclusion

engine/target_solver.py is an oracle-parsing module equivalent in role to
oracle_parser.py — its parse() function receives raw oracle text strings and
extracts TargetRequirement objects. It is called at cast time because targeting
decisions happen at cast time; the raw oracle text it works with includes
dynamically-created token templates that bypass card_database.py. Adding
target_solver.py to the ratchet's _EXCLUDED set is the correct disposition
(same rationale as oracle_parser.py's exclusion).

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.card_database import parse_mana_cost_mtgjson
from engine.oracle_parser import parse_channel_clause


def parse_phyrexian_mana_symbol_count(mana_cost: str) -> int:
    """Total Phyrexian pips in a printed MANA COST (CR 107.4f)."""
    return sum(parse_mana_cost_mtgjson(mana_cost).phyrexian.values())


class TestParsePhyrexianManaSymbolCount:
    """The pip count is a property of the printed cost, not of the text."""

    def test_single_phyrexian_symbol(self):
        assert parse_phyrexian_mana_symbol_count("{G/P}") == 1

    def test_reminder_text_is_not_the_source(self):
        """A cost with TWO pips reports two, even though its reminder clause
        names the symbol once."""
        assert parse_phyrexian_mana_symbol_count("{1}{B/P}{B/P}") == 2

    def test_three_pips(self):
        assert parse_phyrexian_mana_symbol_count("{4}{B/P}{B/P}{B/P}") == 3

    def test_hard_pip_beside_a_phyrexian_pip_is_not_counted(self):
        assert parse_phyrexian_mana_symbol_count("{2}{U}{U/P}") == 1

    def test_no_phyrexian_symbols(self):
        assert parse_phyrexian_mana_symbol_count("{2}{U}{U}") == 0

    def test_empty_cost_is_zero(self):
        assert parse_phyrexian_mana_symbol_count("") == 0
        assert parse_phyrexian_mana_symbol_count(None) == 0


class TestParseChannelClause:
    """Pins replacement of oracle.find('channel —') in response_enumeration.py —
    channel ability clause extraction (Otawara, Boseiju, Sokenzan, etc.)."""

    def test_channel_emdash_clause(self):
        # Otawara, Soaring City: Channel — {1}{U}, Discard Otawara ...
        result = parse_channel_clause(
            "({T}: Add {U}.)\n"
            "Channel — {1}{U}, Discard Otawara: Return up to one target "
            "noncreature, nonland permanent to its owner's hand."
        )
        assert result.startswith("channel —")

    def test_channel_ascii_dash_clause(self):
        # Some cards use ASCII hyphen
        result = parse_channel_clause(
            "({T}: Add {G}.)\n"
            "Channel - {3}{G}, Discard this land: Search your library for "
            "up to four basic lands."
        )
        assert result.startswith("channel -")

    def test_no_channel_returns_empty(self):
        # Ordinary land with no channel ability
        result = parse_channel_clause("{T}: Add {U}.")
        assert result == ""

    def test_channel_at_start_of_oracle(self):
        result = parse_channel_clause(
            "Channel — {2}{R}, Discard ~: ~ deals 3 damage to any target."
        )
        assert result.startswith("channel —")

    def test_empty_oracle_returns_empty(self):
        assert parse_channel_clause("") == ""
        assert parse_channel_clause(None) == ""
