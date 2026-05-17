"""parse_x_cost must read only the printed mana_cost {X}, not oracle body X.

Rule (CR 107.3): X in a spell's *mana cost* is the variable amount paid as
the spell is cast. X in the *oracle body* (e.g. "where X is the number of
lands you control") is a derived value computed at resolution; it has
nothing to do with the cast-time cost choice. The two namespaces are
independent.

The pre-fix `engine/oracle_parser.py::parse_x_cost` conflated them: it
keyed on *any* `X` token in oracle body. Cards like Consult the Star
Charts (mana_cost {5}{U}; oracle "Look at the top X cards … where X is
the number of lands you control") were mis-tagged as X-cost spells.
Downstream the engine then asked the player to pay X mana for them and
they resolved silently because the cast manager set their X to 0 / no
effect was registered for the look-at-N path.

The rule each test name describes is mechanical, not card-specific:

* `test_x_in_mana_cost_returns_x_cost_data` — generic positive case
* `test_x_only_in_oracle_body_returns_none` — generic negative case
* `test_no_x_anywhere_returns_none` — generic baseline
* `test_consult_the_star_charts_is_not_an_x_cost_spell` — class-D
  regression: "look at top X … where X is …" with fixed mana cost
* `test_briber_purse_is_not_an_x_cost_spell` — same family,
  artifact-side: pay {X}: gain something X-derived from another body
  signal, but the printed activation cost is the relevant cost
* `test_fireball_is_an_x_cost_spell` — class-A positive control
"""
from __future__ import annotations

from engine.oracle_parser import parse_x_cost


# ---- Generic mechanism tests ---------------------------------------------


def test_x_in_mana_cost_returns_x_cost_data():
    """{X} in printed mana cost ⇒ X-cost spell, regardless of oracle body."""
    result = parse_x_cost(
        oracle="Deal X damage to any target.",
        name="Generic X-Damage Spell",
        mana_cost_str="{X}{R}",
    )
    assert result is not None
    assert result["multiplier"] == 1


def test_x_only_in_oracle_body_returns_none():
    """X only in oracle body ⇒ NOT an X-cost spell (per CR 107.3)."""
    result = parse_x_cost(
        oracle="Look at the top X cards of your library, where X is the "
               "number of lands you control. Put one into your hand and "
               "the rest on the bottom of your library.",
        name="Generic Look-At-N Spell",
        mana_cost_str="{4}{U}",
    )
    assert result is None


def test_no_x_anywhere_returns_none():
    """Baseline: no X in cost and no X in body ⇒ None."""
    result = parse_x_cost(
        oracle="Draw a card.",
        name="Generic Cantrip",
        mana_cost_str="{1}{U}",
    )
    assert result is None


def test_xx_in_mana_cost_returns_multiplier_two():
    """{X}{X} ⇒ multiplier=2 (Chalice-of-the-Void style)."""
    result = parse_x_cost(
        oracle="Counter target spell with mana value X.",
        name="Generic XX-Counter",
        mana_cost_str="{X}{X}",
    )
    assert result is not None
    assert result["multiplier"] == 2
    assert result["min_x"] == 1


# ---- Class-D regression tests (the bug R5 fixes) -------------------------


def test_consult_the_star_charts_is_not_an_x_cost_spell():
    """Class-D regression: "where X is the number of lands you control"
    oracle body must NOT make the spell read as an X-cost spell when its
    printed mana cost has no {X}.
    """
    result = parse_x_cost(
        oracle="Look at the top X cards of your library, where X is the "
               "number of lands you control. Put one of those cards into "
               "your hand and the rest on the bottom of your library in "
               "a random order.",
        name="Consult the Star Charts",
        mana_cost_str="{5}{U}",
    )
    assert result is None


def test_briber_purse_is_not_an_x_cost_spell():
    """Briber's Purse: ETB with X charge counters as printed mana cost is
    {X}; but the spell-side parse is keyed on the printed cost. This test
    documents that a card whose oracle mentions X in body but whose mana
    cost lacks {X} must NOT be tagged.

    (Briber's Purse itself prints {X} so it IS a positive case; the
    purpose of this test is to enforce the rule for any card in the same
    "X is the number of …" oracle family whose printed cost is fixed.)
    """
    # Synthetic card in the Briber's-Purse family but with fixed cost:
    result = parse_x_cost(
        oracle="When CARDNAME enters, choose a number X. {T}, Sacrifice "
               "CARDNAME: Target creature can't attack you this turn if "
               "its power is greater than X.",
        name="Synthetic Briber's-Purse Variant",
        mana_cost_str="{2}",
    )
    assert result is None


def test_fireball_is_an_x_cost_spell():
    """Class-A positive control: {X}{R}, X damage divided as you choose."""
    result = parse_x_cost(
        oracle="Fireball deals X damage divided as you choose among any "
               "number of targets.",
        name="Fireball",
        mana_cost_str="{X}{R}",
    )
    assert result is not None
    assert result["multiplier"] == 1
