"""W0-D — Damage primitive (`engine/damage.py:deal_damage`).

Rule-phrased tests for the generic damage-resolution helper. These
encode CR 119 (damage), CR 120 (lifelink/deathtouch hooks),
CR 704.5g/h (state-based actions after damage), and the routing
rule: "damage is dealt to the *target object*". The source object
never silently swaps with the controlling player.

Each test names a *mechanic*, not a card. The R6 case
(`test_self_damage_routes_to_source_as_target`) is rule-phrased
as "source-as-target marks damage on the source object, not on
the source's controller". It happens to fail today for Ral's
coin-flip (`engine/oracle_resolver.py:638` deducts player.life
when oracle says the planeswalker takes the damage), but the rule
holds for any future card with self-damage too.

Failing-test-first protocol — these go red before
`engine/damage.py` exists.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.card_database import CardDatabase
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _put_creature_in_play(game, card_db, name, controller):
    """Helper: build a CardInstance for `name` and put it on the
    controller's battlefield. Mirrors the convention in
    `tests/test_artifact_land_ordering.py::_put_in_play`.
    """
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card not in DB: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _put_planeswalker_in_play(game, card_db, name, controller):
    """Helper: build a PW CardInstance and seed loyalty_counters
    to template.loyalty (mirrors `spell_resolution.py:147`).
    """
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card not in DB: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    # Planeswalker loyalty initialisation (engine/spell_resolution.py:147)
    card.loyalty_counters = tmpl.loyalty or 0
    game.players[controller].battlefield.append(card)
    return card


def _find_any_creature_with_toughness(card_db, min_t: int, max_t: int = 99):
    """Return a creature template with toughness in [min_t, max_t].
    No card-name dependency — picks the first match from the DB.
    """
    for name, tmpl in card_db.cards.items():
        if CardType.CREATURE in tmpl.card_types:
            t = tmpl.toughness or 0
            if min_t <= t <= max_t:
                return name
    pytest.skip(f"no creature in DB with toughness in [{min_t},{max_t}]")


def _find_any_planeswalker(card_db, min_loyalty: int = 1):
    """Return any PW template with starting loyalty >= min_loyalty."""
    for name, tmpl in card_db.cards.items():
        if CardType.PLANESWALKER in tmpl.card_types:
            if (tmpl.loyalty or 0) >= min_loyalty:
                return name
    pytest.skip(f"no planeswalker in DB with loyalty >= {min_loyalty}")


# ─── Tests ───────────────────────────────────────────────────────────


def test_deal_damage_to_creature_marks_damage_not_life(card_db):
    """Rule: damage to a creature marks damage on that creature; it
    does NOT decrement the controller's life total.

    CR 119.3: damage dealt to a creature/planeswalker/battle marks
    damage on it; damage dealt to a player causes that player to
    lose that much life.
    """
    from engine.damage import deal_damage

    game = GameState(rng=random.Random(0))
    # Pick a creature with toughness >= 3 so it survives the 2 damage
    name = _find_any_creature_with_toughness(card_db, min_t=3)
    target = _put_creature_in_play(game, card_db, name, controller=0)
    # Source: any other creature; just need a DamageSource-shaped object.
    source = _put_creature_in_play(game, card_db, name, controller=1)
    life_before = game.players[0].life

    deal_damage(source, target, 2)

    assert target.damage_marked == 2, (
        "creature target must accrue damage_marked, not its controller's life"
    )
    assert game.players[0].life == life_before, (
        "creature damage must NOT decrement controller life"
    )


def test_deal_damage_to_planeswalker_decrements_loyalty(card_db):
    """Rule: damage to a planeswalker decrements its loyalty
    counters; it does NOT decrement the controller's life. This is
    the foundation of M10 (burn-to-PW): if the damage primitive
    routes correctly, the enumeration layer just has to include
    PWs in the candidate set.
    """
    from engine.damage import deal_damage

    game = GameState(rng=random.Random(0))
    pw_name = _find_any_planeswalker(card_db, min_loyalty=3)
    pw = _put_planeswalker_in_play(game, card_db, pw_name, controller=0)
    creature_name = _find_any_creature_with_toughness(card_db, min_t=1)
    source = _put_creature_in_play(game, card_db, creature_name, controller=1)
    loyalty_before = pw.loyalty_counters
    life_before = game.players[0].life
    # Pick damage strictly less than starting loyalty so PW survives.
    dmg = max(1, loyalty_before - 1)

    deal_damage(source, pw, dmg)

    assert pw.loyalty_counters == loyalty_before - dmg, (
        "planeswalker damage must decrement loyalty_counters"
    )
    assert game.players[0].life == life_before, (
        "planeswalker damage must NOT decrement controller life"
    )


def test_deal_damage_to_player_decrements_life(card_db):
    """Rule: damage to a player causes them to lose that much life
    (CR 119.3). This is the only target type where life moves.
    """
    from engine.damage import deal_damage

    game = GameState(rng=random.Random(0))
    creature_name = _find_any_creature_with_toughness(card_db, min_t=1)
    source = _put_creature_in_play(game, card_db, creature_name, controller=0)
    target_player = game.players[1]
    life_before = target_player.life
    dmg = 3

    deal_damage(source, target_player, dmg)

    assert target_player.life == life_before - dmg, (
        "player damage must decrement life by exactly the damage amount"
    )


def test_self_damage_routes_to_source_as_target(card_db):
    """R6 rule-phrased: when a source deals damage to *itself* (oracle
    text "<this> deals N damage to it" referring to the same object,
    or any self-targeting effect), the damage marks on the source
    object — not on the source's controller's life total.

    Today, `engine/oracle_resolver.py:638` for Ral's lost coin flip
    does `player.life -= 1` despite oracle saying Ral takes the
    damage. The damage primitive must not silently swap target=source
    for target=controller.

    Phrased without naming Ral: any DamageTarget passed as `target`
    receives the damage, regardless of whether it is the source.
    """
    from engine.damage import deal_damage

    game = GameState(rng=random.Random(0))
    # Use a planeswalker so the "loyalty decremented, life untouched"
    # signal is cleanest; the rule applies equally to creatures.
    pw_name = _find_any_planeswalker(card_db, min_loyalty=2)
    self_target = _put_planeswalker_in_play(game, card_db, pw_name, controller=0)
    loyalty_before = self_target.loyalty_counters
    life_before = game.players[0].life

    # The source IS the target: the PW deals 1 to itself (Ral lost-flip shape).
    deal_damage(self_target, self_target, 1)

    assert self_target.loyalty_counters == loyalty_before - 1, (
        "self-damage on a planeswalker target must mark on the PW"
    )
    assert game.players[0].life == life_before, (
        "self-damage must not deduct life from the source's controller "
        "(R6: Ral's lost coin-flip damages Ral, not Ral's controller)"
    )


def test_lethal_damage_to_creature_schedules_destroy(card_db):
    """Rule: when a creature has been dealt damage >= its toughness,
    the next state-based-actions check destroys it (CR 704.5h).

    The damage primitive itself only marks damage and signals an
    SBA pass; it does not perform the destroy inline. After
    `deal_damage` plus a SBA check, the creature is in the
    graveyard.
    """
    from engine.damage import deal_damage

    game = GameState(rng=random.Random(0))
    # Pick the smallest creature available (toughness 1).
    name = _find_any_creature_with_toughness(card_db, min_t=1, max_t=1)
    target = _put_creature_in_play(game, card_db, name, controller=0)
    source_name = _find_any_creature_with_toughness(card_db, min_t=1)
    source = _put_creature_in_play(game, card_db, source_name, controller=1)

    deal_damage(source, target, 1)

    # Primitive marks lethal damage.
    assert target.damage_marked >= target.toughness, (
        "lethal damage must accrue on target before SBAs run"
    )
    # SBA pass destroys it (CR 704.5h).
    game.check_state_based_actions()
    assert target.zone == "graveyard", (
        "after SBAs, a creature dealt lethal damage must be in graveyard"
    )
    assert target not in game.players[0].battlefield
