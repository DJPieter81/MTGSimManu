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

from engine.cards import CardInstance
from engine.game_state import GameState


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


_HASTE_LAND = "Hanweir Battlements // Hanweir, the Writhing Township"
# Tier-1 carrier WITHOUT a bounce ETB (karoos bounce each other back to
# hand in a lands-only fixture): the tier-1 predicate `enters_tapped and
# produces_mana` matches this non-bounce tapped producer too — the known
# proxy imprecision noted in _primeval_titan_search itself.
_TAPPED_MANA_LAND = "Crumbling Vestige"


def _to_battlefield(game, card_db, name, controller=0, sick=False):
    card = _to_library(game, card_db, name, controller)
    game.players[controller].library.remove(card)
    card.enter_battlefield()
    card.summoning_sick = sick
    game.players[controller].battlefield.append(card)
    return card


def test_grants_haste_activation_is_a_parse_once_typed_field(card_db):
    """The fetch priority must read a typed field populated from the
    activated-ability classify pass, never re-scan oracle text."""
    assert card_db.get_card(_HASTE_LAND).grants_haste_activation is True
    assert card_db.get_card(_TAPPED_MANA_LAND).grants_haste_activation is False
    assert card_db.get_card("Mountain").grants_haste_activation is False


def test_titan_fetch_prefers_haste_land_over_second_tapped_mana_land(card_db):
    """When the searcher has a summoning-sick would-be attacker and no
    haste source, a haste-GRANTING land outranks the SECOND copy of a
    tapped-mana land in the fetch pair — one tapped-mana land keeps the
    engine going, the haste land converts the attack a turn earlier."""
    game = GameState(rng=random.Random(0))
    _to_battlefield(game, card_db, "Primeval Titan", sick=True)
    _to_library(game, card_db, _TAPPED_MANA_LAND, 0)
    _to_library(game, card_db, _TAPPED_MANA_LAND, 0)
    _to_library(game, card_db, _HASTE_LAND, 0)
    _to_library(game, card_db, "Forest", 0)

    from engine.card_effects import _primeval_titan_search
    _primeval_titan_search(game, controller=0)

    bf = [c.name for c in game.players[0].battlefield]
    assert bf.count(_TAPPED_MANA_LAND) == 1, (
        f"fetched {bf}: the pair must keep ONE tapped-mana land")
    assert _HASTE_LAND in bf, (
        f"fetched {bf}: the second slot must take the haste-granting land "
        f"over a duplicate tapped-mana land")


def test_titan_fetch_keeps_the_pair_when_a_haste_source_is_controlled(card_db):
    """Already controlling a haste source removes the substitution — the
    duplicate tapped-mana land is the better fetch again."""
    game = GameState(rng=random.Random(0))
    _to_battlefield(game, card_db, "Primeval Titan", sick=True)
    _to_battlefield(game, card_db, _HASTE_LAND)
    _to_library(game, card_db, _TAPPED_MANA_LAND, 0)
    _to_library(game, card_db, _TAPPED_MANA_LAND, 0)
    _to_library(game, card_db, _HASTE_LAND, 0)

    from engine.card_effects import _primeval_titan_search
    _primeval_titan_search(game, controller=0)

    fetched = [c.name for c in game.players[0].battlefield
               if c.summoning_sick or c.template.is_land]
    assert sum(1 for c in game.players[0].battlefield
               if c.name == _TAPPED_MANA_LAND) == 2, (
        f"battlefield {fetched}: with a haste source already controlled "
        f"the pair stays two tapped-mana lands")


def test_titan_fetch_keeps_the_pair_without_a_sick_attacker(card_db):
    """No summoning-sick attacker means no attack to convert — the
    substitution must not fire."""
    game = GameState(rng=random.Random(0))
    _to_battlefield(game, card_db, "Primeval Titan", sick=False)
    _to_library(game, card_db, _TAPPED_MANA_LAND, 0)
    _to_library(game, card_db, _TAPPED_MANA_LAND, 0)
    _to_library(game, card_db, _HASTE_LAND, 0)

    from engine.card_effects import _primeval_titan_search
    _primeval_titan_search(game, controller=0)

    assert sum(1 for c in game.players[0].battlefield
               if c.name == _TAPPED_MANA_LAND) == 2


def test_titan_fetch_does_not_displace_a_damage_source_land(card_db):
    """The substitution only outranks a DUPLICATE tapped-mana land: a
    finisher tile (damage-source land) in slot two stays fetched."""
    game = GameState(rng=random.Random(0))
    _to_battlefield(game, card_db, "Primeval Titan", sick=True)
    _to_library(game, card_db, _TAPPED_MANA_LAND, 0)
    _to_library(game, card_db, "Valakut, the Molten Pinnacle", 0)
    _to_library(game, card_db, _HASTE_LAND, 0)

    from engine.card_effects import _primeval_titan_search
    _primeval_titan_search(game, controller=0)

    bf = [c.name for c in game.players[0].battlefield]
    assert "Valakut, the Molten Pinnacle" in bf, (
        f"fetched {bf}: a damage-source land is not a duplicate mana "
        f"land and must not be displaced")


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
