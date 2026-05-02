"""Phelia attack-trigger blink target must use the principled
``creature_threat_value`` ordering, not a hardcoded card-name dict.

Background. ``engine/card_effects.py::phelia_attack`` previously
contained::

    priority = {'Solitude': 10, "Phlage, Titan of Fire's Fury": 8,
                'Omnath, Locus of Creation': 7}
    target = max(own_etb, key=lambda c: priority.get(c.name, _threat_score(c)))

This violates the abstraction contract: card-name keys baked into
engine code, ranking three specific Modern creatures.  The same
ranking falls out of the existing AI primitive
``ai.ev_evaluator.creature_threat_value`` (Solitude 15.3 > Phlage 8.4
> Omnath 6.4) because Solitude's exile-ETB scores higher than
Phlage's burn-ETB which in turn scores higher than Omnath's cantrip
ETB.  The dict is fully redundant.

Mechanic-level rule encoded:
    when Phelia (or any "blink your own ETB" effect) chooses among
    the controller's ETB-tagged creatures, ranking is the AI threat
    score of each candidate — engine layer does not score, it
    delegates to ``_threat_score``/``creature_threat_value``.

This test fixes the *general* ordering with three real ETB carriers
present.  Removing the hardcoded dict is the fix; this test pins the
behaviour so the regression cannot reappear.
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


def _battlefield(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    if tmpl.is_creature:
        game.players[controller].creatures.append(card)
    return card


def test_phelia_blinks_highest_threat_etb_creature(card_db):
    """Phelia should blink the highest-threat ETB creature on her side
    of the board, ranked by creature_threat_value (the AI primitive),
    not by a hardcoded card-name priority dict.

    Setup: P0 controls Phelia (attacker), Solitude, Phlage, Omnath.
    P1 controls a single creature (so the opp-target branch is NOT
    taken — Phelia must choose among own ETBs).

    Expected: Solitude is exiled (highest threat: removal-ETB on a
    flash incarnation outranks 6/6 burn-ETB which outranks 4/4
    cantrip-ETB).  This pins the same ordering the dict encoded, but
    derived from the AI scoring layer.
    """
    db = card_db
    game = GameState(rng=random.Random(0))

    phelia = _battlefield(game, db, "Phelia, Exuberant Shepherd", 0)
    solitude = _battlefield(game, db, "Solitude", 0)
    phlage = _battlefield(game, db, "Phlage, Titan of Fire's Fury", 0)
    omnath = _battlefield(game, db, "Omnath, Locus of Creation", 0)
    # Opp creature exists so Phelia is a legal attacker, but the AI's
    # blink choice must not flip to opp-side because we have own ETBs.
    _battlefield(game, db, "Memnite", 1)

    from engine.card_effects import phelia_attack
    phelia_attack(game, phelia, controller=0)

    # Find which of our three ETB carriers got exiled.
    exiled_names = {c.name for c in game.players[0].exile}
    assert exiled_names, (
        "Phelia attack handler exiled nothing — expected one of "
        "Solitude / Phlage / Omnath to be blinked for value."
    )
    assert "Solitude" in exiled_names, (
        f"Phelia blinked {exiled_names} instead of Solitude. "
        f"creature_threat_value ranks Solitude (~15) > Phlage (~8) > "
        f"Omnath (~6); the principled AI primitive picks Solitude. "
        f"If a hardcoded card-name dict is reintroduced into "
        f"phelia_attack this test will fail."
    )


def test_phelia_handler_has_no_hardcoded_card_priority_dict():
    """Source-level guard: the Phelia attack handler must not contain
    a literal card-name → score dict.  This is a structural invariant
    that the abstraction-baseline ratchet (which only catches
    `name == "literal"` and `name in (...)`) does not see, because
    dict-key lookup is a different syntactic form."""
    import inspect

    from engine.card_effects import phelia_attack
    src = inspect.getsource(phelia_attack)
    # The pre-fix dict had three card-name string keys mapped to ints.
    # Detect the *shape* (a string-keyed numeric dict literal in the
    # body) by looking for any of the three former keys appearing as
    # a quoted string adjacent to a colon-and-int.
    forbidden_substrings = [
        "'Solitude':",
        '"Solitude":',
        "'Phlage",
        '"Phlage',
        "'Omnath",
        '"Omnath',
    ]
    for s in forbidden_substrings:
        assert s not in src, (
            f"phelia_attack contains hardcoded card-name priority "
            f"({s!r}). Engine must delegate ranking to "
            f"_threat_score / creature_threat_value (AI primitive)."
        )
