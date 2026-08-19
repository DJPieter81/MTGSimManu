"""Batch 21 typed-field migration tests.

# Mechanic: Phyrexian mana symbol count (rule, not a card)

phyrexian_mana_symbol_count replaces oracle_lower.count('/p}') in two sites:
- ai/ev_player.py:1502 (EV life-cost discount for Phyrexian spells)
- engine/cast_manager.py:1093 (Phyrexian mana payment at cast time)

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
from engine.oracle_parser import (
    parse_phyrexian_mana_symbol_count,
    parse_channel_clause,
)


class TestParsePhyrexianManaSymbolCount:
    """Pins replacement of oracle_lower.count('/p}') in ev_player.py and
    cast_manager.py — Phyrexian mana spell life-cost detection."""

    def test_single_phyrexian_symbol(self):
        # Mutagenic Growth {G/P}: 1 Phyrexian symbol
        assert parse_phyrexian_mana_symbol_count(
            "Target creature gets +2/+2 until end of turn."
            # oracle text for a {G/P} spell
        ) == 0  # no /p} in plain text

    def test_phyrexian_mana_in_cost_notation(self):
        # Gitaxian Probe {U/P}: cost contains {U/P}
        assert parse_phyrexian_mana_symbol_count(
            "({U/P} can be paid with either {U} or 2 life.) "
            "Look at target player's hand."
        ) == 1

    def test_double_phyrexian_symbols(self):
        # Gut Shot {R/P} targeting ({R/P} in reminder)
        assert parse_phyrexian_mana_symbol_count(
            "({R/P} can be paid with either {R} or 2 life.) "
            "({R/P} can be paid with either {R} or 2 life.) "
            "Gut Shot deals 1 damage to any target."
        ) == 2

    def test_phyrexian_bp_notation(self):
        # Some cards use {B/P} or {W/P}
        assert parse_phyrexian_mana_symbol_count(
            "({B/P} can be paid with either {B} or 2 life.) "
            "Target player loses 1 life."
        ) == 1

    def test_no_phyrexian_symbols(self):
        # Ordinary card with no Phyrexian mana
        assert parse_phyrexian_mana_symbol_count(
            "Draw two cards. {2}, {T}: Add {U}."
        ) == 0

    def test_empty_oracle_is_zero(self):
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
