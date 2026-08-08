"""ContinuousEffectsManager.recalculate() is idempotent, and a
registered effect retracts when its source leaves the battlefield.

Rule under test
----------------
`recalculate()`'s own docstring claimed "1. Clears all calculated
modifications on all permanents" (CR 613's re-derivation model — a
continuous effect is recomputed from scratch every time, not
accumulated), but the body never did this: it only removed effects
whose SOURCE had left (`_cleanup_stale_effects`), then applied every
remaining effect's `apply` closure, which mutates via `+=`. Calling
`recalculate()` more than once — the manager's own documented usage
("called at key points: after ETB, after spells resolve, before
combat") — would double/triple/N-count every registered effect's
bonus, since nothing ever reset the accumulator between calls.

Fixed by adding dedicated `cem_power_mod`/`cem_toughness_mod`/
`cem_keywords` accumulator fields (CardInstance, engine/cards.py) that
`recalculate()` clears to zero at the start of every call, before
reapplying. Kept separate from `temp_power_mod`/`temp_toughness_mod`/
`temp_keywords` (the pre-existing shared dumping ground for one-shot
pump spells, Dash, etc., cleared only at end-of-turn cleanup) so this
fix cannot interact with any of that unrelated, still-untouched code.

Class size: every effect ever registered through this manager, for
as long as it's active — a foundational correctness property, not a
per-card concern.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.continuous_effects import (
    ContinuousEffect, ContinuousEffectsManager, Layer, PTSublayer,
    create_lord_effect,
)
from engine.game_state import GameState


def _vanilla_template(name, power=1, toughness=1):
    return CardTemplate(
        name=name, card_types=[CardType.CREATURE], mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=["Goblin"], power=power, toughness=toughness,
        loyalty=None, keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False, oracle_text="", tags=set(),
    )


def _put_on_battlefield(game, template, controller):
    card = CardInstance(
        template=template, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


def test_recalculate_is_idempotent_not_cumulative():
    """Calling recalculate() twice must produce the SAME power, not
    double the bonus — the manager's documented usage calls it
    repeatedly across a turn."""
    game = GameState(rng=random.Random(0))
    lord = _put_on_battlefield(game, _vanilla_template("Test Lord"), controller=0)
    goblin = _put_on_battlefield(game, _vanilla_template("Test Goblin", power=1, toughness=1),
                                 controller=0)

    for effect in create_lord_effect(
        source_id=lord.instance_id, source_name="Test Lord",
        affected_fn=lambda g, c: "Goblin" in c.template.subtypes and c is not lord,
        power_bonus=1, toughness_bonus=1,
        description="Other Goblins get +1/+1",
    ):
        game.continuous_effects.register(effect)

    game.continuous_effects.recalculate(game)
    assert goblin.power == 2, f"expected 1+1=2 after first recalculate, got {goblin.power}"

    game.continuous_effects.recalculate(game)
    assert goblin.power == 2, (
        f"expected power to STAY 2 after a second recalculate() call, got "
        f"{goblin.power} — the bonus is being double-counted instead of "
        f"re-derived from scratch each call"
    )

    game.continuous_effects.recalculate(game)
    game.continuous_effects.recalculate(game)
    assert goblin.power == 2, "power drifted after repeated recalculate() calls"


def test_effect_retracts_when_source_leaves_battlefield():
    """A lord's bonus must disappear once the lord itself leaves —
    not persist forever as a stale accumulated delta."""
    game = GameState(rng=random.Random(0))
    lord = _put_on_battlefield(game, _vanilla_template("Test Lord"), controller=0)
    goblin = _put_on_battlefield(game, _vanilla_template("Test Goblin", power=1, toughness=1),
                                 controller=0)

    for effect in create_lord_effect(
        source_id=lord.instance_id, source_name="Test Lord",
        affected_fn=lambda g, c: "Goblin" in c.template.subtypes and c is not lord,
        power_bonus=1, toughness_bonus=1,
        description="Other Goblins get +1/+1",
    ):
        game.continuous_effects.register(effect)

    game.continuous_effects.recalculate(game)
    assert goblin.power == 2

    game.players[0].battlefield.remove(lord)
    game.continuous_effects.recalculate(game)

    assert goblin.power == 1, (
        f"expected the bonus to retract once the lord left the "
        f"battlefield, got power={goblin.power}"
    )


def test_keyword_grant_via_manager_is_visible_to_combat():
    """A keyword granted through the manager must be readable via
    CardInstance.keywords (the property every combat/targeting check
    consults) — not silently discarded the way a bare
    `creature.keywords.add(...)` call is (that property returns a
    fresh union each access; see tests/test_scion_of_draco_*.py for
    the specific audited bug this generalizes)."""
    game = GameState(rng=random.Random(0))
    lord = _put_on_battlefield(game, _vanilla_template("Test Lord"), controller=0)
    goblin = _put_on_battlefield(game, _vanilla_template("Test Goblin"), controller=0)

    for effect in create_lord_effect(
        source_id=lord.instance_id, source_name="Test Lord",
        affected_fn=lambda g, c: "Goblin" in c.template.subtypes and c is not lord,
        power_bonus=0, toughness_bonus=0,
        keyword_grants={Keyword.FLYING},
        description="Other Goblins have flying",
    ):
        game.continuous_effects.register(effect)

    game.continuous_effects.recalculate(game)

    assert Keyword.FLYING in goblin.keywords, (
        "keyword granted via ContinuousEffectsManager is not visible "
        "through CardInstance.keywords"
    )

    game.players[0].battlefield.remove(lord)
    game.continuous_effects.recalculate(game)
    assert Keyword.FLYING not in goblin.keywords, (
        "granted keyword did not retract when its source left"
    )
