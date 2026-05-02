"""Primeval Titan land-search priority must be derived from oracle
text + template fields, not from a substring match on the land's
name.

Background. ``engine/card_effects.py::_primeval_titan_search`` had::

    if "Valakut" in c.name:
        score += 8  # Valakut for damage

The substring check is the same anti-pattern as ``card.name == ...``;
it carries the abstraction-contract spirit even though it slips past
the regex-based ratchet.  The actual mechanic Valakut, the Molten
Pinnacle exposes is a land that *deals damage*: its oracle text
matches the generic phrase "deal[s] N damage to any target" inside
a triggered ability.  Any future land sharing that template should
be picked just as eagerly.

Mechanic-level rule encoded:
    Primeval Titan's land-priority function ranks candidates by
    1) bounce-land status (untap with Amulet) - already oracle-derived,
    2) damage-source status — any land whose oracle text contains a
       "deal N damage" clause is a closer/finisher tile,
    3) combat enabler (gains haste / double strike) — already oracle,
    4) plain mana producer.

The fix is: replace the name substring with an oracle-text check.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _to_library(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="library",
    )
    card._game_state = game
    game.players[controller].library.append(card)
    return card


def test_primeval_titan_prefers_damage_dealing_land_via_oracle(card_db):
    """When Primeval Titan resolves with both Valakut and a vanilla
    Mountain in library, it should fetch Valakut over Mountain (damage
    payoff > pure mana). The choice must come from oracle parsing,
    not a name substring."""
    db = card_db
    game = GameState(rng=random.Random(0))

    # Library: Valakut + 2 plain Mountains.  Titan fetches 2 lands.
    valakut = _to_library(game, db, "Valakut, the Molten Pinnacle", 0)
    m1 = _to_library(game, db, "Mountain", 0)
    m2 = _to_library(game, db, "Mountain", 0)

    from engine.card_effects import _primeval_titan_search
    _primeval_titan_search(game, controller=0)

    bf_names = [c.name for c in game.players[0].battlefield]
    assert "Valakut, the Molten Pinnacle" in bf_names, (
        f"Primeval Titan fetched {bf_names}; Valakut should be one "
        f"of the two lands picked because its oracle text contains a "
        f"'deal N damage to any target' clause (damage-source bonus). "
        f"Did the oracle-text detection regress to a name substring?"
    )


def test_primeval_titan_search_has_no_name_substring_check():
    """Source-level guard: the Titan land-priority body must not
    contain a hard-coded land name. Replace name substring with
    oracle-text or template-field detection."""
    import inspect

    from engine.card_effects import _primeval_titan_search
    src = inspect.getsource(_primeval_titan_search)
    assert '"Valakut"' not in src and "'Valakut'" not in src, (
        "_primeval_titan_search references the literal land name "
        "'Valakut'. Replace with oracle-text detection: any land whose "
        "oracle text contains 'deal[s] N damage to any target' is a "
        "damage-source land regardless of name."
    )
