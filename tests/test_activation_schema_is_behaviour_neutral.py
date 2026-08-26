"""The activated-ability schema must not change any existing behaviour.

This is the invariant that lets the schema commit be verified by bit-identity
rather than by fuzzy comparison, and it guards a specific, non-obvious trap.

`ai/evaluator._ability_bonus` adds `ABILITY_TYPE_ACTIVATED` for every
`AbilityType.ACTIVATED` entry in `CardTemplate.abilities`. That bonus feeds
`estimate_permanent_value`, which `ai/response.py` uses as the SORT KEY when
choosing instant-speed removal targets. The live census of
`CardTemplate.abilities` contains **zero** ACTIVATED entries, so that code path
is currently dark.

If the activated-ability data were emitted into `CardTemplate.abilities`
instead of its own field, thousands of templates would light that bonus up at
once — silently changing which permanents the AI removes, i.e. a real win-rate
change from a commit containing no AI code at all. That is why
`activated_abilities` is a NEW field and `abilities` is untouched.

**If you are "tidying" these two fields together: don't.** This test is the
reason. Rules-phrased; the card counts are the mechanism, not magic numbers.
"""
from __future__ import annotations

import collections

from engine.card_database import CardDatabase

_DB = CardDatabase()


def _ability_type_census():
    census = collections.Counter()
    for template in _DB.cards.values():
        for ability in (getattr(template, "abilities", None) or []):
            kind = getattr(ability, "ability_type", None)
            if kind is None and isinstance(ability, tuple) and ability:
                kind = ability[0]
            census[str(kind).split(".")[-1]] += 1
    return census


def test_ability_type_census_contains_no_activated_entries():
    census = _ability_type_census()
    assert census.get("ACTIVATED", 0) == 0, (
        f"CardTemplate.abilities must contain no ACTIVATED entries — emitting "
        f"them lights up ABILITY_TYPE_ACTIVATED in ai/evaluator, which feeds "
        f"estimate_permanent_value, which is the removal-target sort key in "
        f"ai/response.py. That would be a win-rate change from a schema-only "
        f"commit. Found {census.get('ACTIVATED')}."
    )


def test_activated_abilities_live_on_their_own_field():
    """The parsed data must actually be somewhere — just not in `abilities`."""
    populated = [
        t for t in _DB.cards.values()
        if getattr(t, "activated_abilities", None)
    ]
    assert populated, (
        "the schema is pointless if nothing is populated; expected many "
        "templates to carry parsed activated abilities")


def test_mana_abilities_are_not_double_counted_as_mana_sources():
    """A `{T}: Add` line is flagged, and the existing mana fields are intact.

    Mana production is owned by `mana_units` / `sacrifice_mana_units`. If the
    activated-ability parse also fed the mana path, every mana source would
    count twice.
    """
    forest = _DB.get_card("Forest")
    assert forest is not None
    assert forest.mana_units or forest.produces_mana, (
        "the existing mana representation must be unchanged")
    for ability in (getattr(forest, "activated_abilities", None) or []):
        assert ability.is_mana_ability, (
            "a basic land's tap ability must be flagged as a mana ability so "
            "the enumerator skips it")
