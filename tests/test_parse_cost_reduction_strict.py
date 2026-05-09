"""Phase 1A — parse_cost_reduction must require an explicit pattern.

Rule under test
---------------
``engine.oracle_parser.parse_cost_reduction`` interprets oracle text
like *"Spells you cast cost {1} less to cast."* into a structured
reduction rule consumed by ``count_cost_reducers`` during mana
payment. The function MUST require an explicit ``cost {N} less``
substring; the mere co-occurrence of ``'cost'`` and ``'less'`` does
not denote a cost-reduction effect.

Root cause of the prior bug
---------------------------
The previous implementation early-returned ``None`` only if either
word was missing. If both were present (anywhere), it defaulted to
``{'target': 'all', 'amount': 1}`` — a generic "all spells cost {1}
less" rule.

The substring ``'less'`` appears inside the word ``'colorless'``.
The substring ``'cost'`` appears in any oracle that says ``mana cost
{N}`` (Saga III's tutor, Pinnacle Emissary's warp, Phlage's escape).
A card like Urza's Saga, whose oracle reads:

    "II — This Saga gains '{2}, {T}: Create a 0/0 colorless Construct
    artifact creature token...'
     III — Search your library for an artifact card with mana cost
    {0} or {1}, put it onto the battlefield..."

contains both 'cost' and 'less' but is not a cost reducer at all.
``parse_cost_reduction`` falsely flagged Saga as granting -1 generic
cost on every spell its controller casts — handing Affinity a free
discount on Cranial Plating, Saga's own Construct token activation,
Frogmite (stacking with Affinity discount), and every other spell.

Scope
-----
A DB-wide audit found 554 cards in `ModernAtomic.json` whose oracle
text contains both ``'cost'`` and ``'less'`` without a strict ``cost
{N} less`` pattern. 20 of those are present in our 16 modern decks
(`Urza's Saga`, `Frogmite`, `Thought Monitor`, `Trinisphere`,
`Boseiju, Who Endures`, `Leyline Binding`, ...). This is a Class A
oracle-parsing bug with broad WR distortion.

Audit context: ``docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md``
finding A-1; plan ``/root/.claude/plans/now-lets-fix-affinity-keen-penguin.md``
Phase 1A.
"""
from __future__ import annotations

import pytest

from engine.oracle_parser import parse_cost_reduction


# ─── Negative cases (must return None) ───────────────────────────────


def test_saga_oracle_is_not_a_cost_reducer():
    """Urza's Saga: 'colorless' contains 'less'; 'mana cost {0}' has
    'cost'. Neither in a cost-reduction phrase."""
    saga_oracle = (
        "(As this Saga enters and after your draw step, add a lore "
        "counter. Sacrifice after III.)\n"
        "I — This Saga gains \"{T}: Add {C}.\"\n"
        "II — This Saga gains \"{2}, {T}: Create a 0/0 colorless "
        "Construct artifact creature token with 'This token gets +1/+1 "
        "for each artifact you control.'\"\n"
        "III — Search your library for an artifact card with mana cost "
        "{0} or {1}, put it onto the battlefield, then shuffle."
    )
    assert parse_cost_reduction(saga_oracle) is None, (
        "Urza's Saga is not a cost reducer. parse_cost_reduction must "
        "not be tricked by 'colorless' (has 'less') or 'mana cost {0}' "
        "(has 'cost')."
    )


def test_colorless_token_oracle_is_not_a_cost_reducer():
    """Any card creating a 'colorless' token. The substring 'less'
    appears, but no cost is reduced."""
    oracle = "Create a 0/0 colorless Construct artifact creature token."
    assert parse_cost_reduction(oracle) is None


def test_mana_cost_search_is_not_a_cost_reducer():
    """Tutor effects often phrase target restriction as 'mana cost
    {N}'. The word 'cost' appears, but no reduction."""
    oracle = "Search your library for a card with mana cost {2} or less."
    # Note: 'less' appears here but in "{2} or less" (a search filter,
    # not a cost reduction). This test pins that a tutor's mana-value
    # filter must not be confused with a cost-reduction effect.
    assert parse_cost_reduction(oracle) is None


def test_warp_alternative_cost_is_not_a_cost_reducer():
    """Warp (Pinnacle Emissary) has 'mana cost' in oracle; not a
    reducer."""
    oracle = (
        "Warp {1}{R} (You may cast this spell from your hand for its "
        "warp cost. If you do, exile it.)\n"
        "When this creature enters, exile target nonlegendary, "
        "nonland permanent."
    )
    assert parse_cost_reduction(oracle) is None


# ─── Positive cases (real cost reducers, must still parse) ───────────


def test_helm_of_awakening_is_parsed():
    """'Spells cost {1} less to cast.' — canonical generic reducer."""
    oracle = "Spells cost {1} less to cast."
    rule = parse_cost_reduction(oracle)
    assert rule is not None
    assert rule['amount'] == 1
    assert rule['target'] == 'all'


def test_goblin_electromancer_is_parsed():
    """'Instant and sorcery spells you cast cost {1} less to cast.'"""
    oracle = "Instant and sorcery spells you cast cost {1} less to cast."
    rule = parse_cost_reduction(oracle)
    assert rule is not None
    assert rule['amount'] == 1
    assert rule['target'] == 'instant_sorcery'


def test_ruby_medallion_is_parsed():
    """'Red spells you cast cost {1} less to cast.' — color filter."""
    oracle = "Red spells you cast cost {1} less to cast."
    rule = parse_cost_reduction(oracle)
    assert rule is not None
    assert rule['amount'] == 1
    assert rule['color'] == 'R'


def test_two_mana_reduction_is_parsed():
    """'Spells cost {2} less to cast.' — amount > 1."""
    oracle = "Creature spells you cast cost {2} less to cast."
    rule = parse_cost_reduction(oracle)
    assert rule is not None
    assert rule['amount'] == 2
    assert rule['target'] == 'creature'


# ─── Boundary: cards present in our 16 modern decks ──────────────────


@pytest.fixture(scope="module")
def card_db():
    from engine.card_database import CardDatabase
    return CardDatabase()


@pytest.mark.parametrize(
    "card_name",
    [
        # All these are in our 16 modern decks AND have both 'cost'
        # and 'less' in oracle but are NOT cost reducers. Pre-fix
        # parse_cost_reduction returned a rule for each. Post-fix
        # returns None for each.
        "Urza's Saga",
        "Frogmite",
        "Thought Monitor",
        "Pinnacle Emissary",
        "Trinisphere",
        "Boseiju, Who Endures",
        "Leyline Binding",
        "Demonic Dread",
        "Shardless Agent",
        "Phlage, Titan of Fire's Fury",
    ],
)
def test_known_non_reducers_parse_to_none(card_db, card_name):
    """Cards from our deck list that contain 'cost' + 'less' in oracle
    but are NOT cost reducers. Pre-fix all returned a generic -1 rule;
    post-fix all return None."""
    tmpl = card_db.get_card(card_name)
    assert tmpl is not None, f"{card_name} missing from DB"
    oracle = (tmpl.oracle_text or '').lower()
    if 'cost' not in oracle or 'less' not in oracle:
        pytest.skip(
            f"{card_name} doesn't contain both 'cost' and 'less' in "
            f"this DB build — test premise no longer applies."
        )
    rule = parse_cost_reduction(oracle)
    assert rule is None, (
        f"{card_name} is not a cost reducer but parse_cost_reduction "
        f"returned {rule!r}. Oracle: {oracle[:200]}..."
    )
