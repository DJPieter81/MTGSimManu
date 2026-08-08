"""Scion of Draco grants keywords to your creatures based on EACH
CREATURE'S OWN COLOR, continuously — not a one-time ETB grant gated
on an unrelated condition.

Rule under test
----------------
Real oracle text: "Each creature you control has vigilance if it's
white, hexproof if it's blue, lifelink if it's black, first strike if
it's red, and trample if it's green." This is a static, continuously
re-evaluated ability (present tense "has"), gated per-creature by
that creature's own printed color — not by whether the controller
also has a Leyline of the Guildpact in play (a condition invented by
the previous implementation, unrelated to the real card).

The previous handler had two independent bugs, both symptoms of
modeling this as a one-shot ETB event instead of a continuous static
ability:
  1. `creature.keywords.add(...)` is a silent no-op — `keywords` is a
     computed property returning a fresh union each access; `.add()`
     mutates the discarded temporary. Nothing was ever granted,
     regardless of the Leyline condition.
  2. Even if the mutation worked, gating on Leyline-of-the-Guildpact
     and granting ALL FIVE keywords to EVERY creature regardless of
     its actual color is not the card's real rule at all.

Fixed by registering one ContinuousEffect per color→keyword mapping
through ContinuousEffectsManager on ETB, each `affected_fn` checking
the specific creature's own `template.colors` — retracted
automatically by the manager's stale-source cleanup when Scion
leaves the battlefield (see
tests/test_continuous_effects_manager_recalculate.py for that
primitive's own correctness tests).

Class size: this is the "creatures you control have keyword K if
they're color C" mechanic — Scion of Draco is the fixture, but the
same registration pattern generalizes to any future card with this
shape.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.game_state import GameState
from engine.mana import Color


def _creature_template(name, colors):
    return CardTemplate(
        name=name, card_types=[CardType.CREATURE], mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[], power=1, toughness=1, loyalty=None,
        keywords=set(), abilities=[], color_identity=set(colors), colors=set(colors),
        produces_mana=[], enters_tapped=False, oracle_text="", tags=set(),
    )


def _put_template_on_battlefield(game, template, controller):
    card = CardInstance(
        template=template, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


def _put_named_card_on_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return _put_template_on_battlefield(game, tmpl, controller)


def test_grants_color_specific_keyword_not_all_five_to_everyone(card_db):
    """Each creature gets ONLY the keyword matching its own color —
    not all five keywords regardless of color (the previous
    Leyline-gated 'give everyone everything' shape)."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming

    game = GameState(rng=random.Random(0))
    scion = _put_named_card_on_battlefield(game, card_db, "Scion of Draco", controller=0)
    white_creature = _put_template_on_battlefield(
        game, _creature_template("Test White Creature", {Color.WHITE}), controller=0)
    red_creature = _put_template_on_battlefield(
        game, _creature_template("Test Red Creature", {Color.RED}), controller=0)
    colorless_creature = _put_template_on_battlefield(
        game, _creature_template("Test Colorless Creature", set()), controller=0)

    EFFECT_REGISTRY.execute("Scion of Draco", EffectTiming.ETB, game, scion, controller=0)
    game.continuous_effects.recalculate(game)

    assert Keyword.VIGILANCE in white_creature.keywords, (
        "white creature should have vigilance"
    )
    assert Keyword.FIRST_STRIKE not in white_creature.keywords, (
        "white creature should NOT have first strike (that's red's keyword)"
    )
    assert Keyword.FIRST_STRIKE in red_creature.keywords, (
        "red creature should have first strike"
    )
    assert Keyword.VIGILANCE not in red_creature.keywords, (
        "red creature should NOT have vigilance"
    )
    assert colorless_creature.keywords == set(), (
        "colorless creature should get no keywords from this effect at all"
    )


def test_grant_retracts_when_scion_leaves(card_db):
    """The keyword must disappear once Scion leaves the battlefield —
    it's a continuous effect tied to Scion's presence, not a
    permanent stamp."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming

    game = GameState(rng=random.Random(0))
    scion = _put_named_card_on_battlefield(game, card_db, "Scion of Draco", controller=0)
    white_creature = _put_template_on_battlefield(
        game, _creature_template("Test White Creature", {Color.WHITE}), controller=0)

    EFFECT_REGISTRY.execute("Scion of Draco", EffectTiming.ETB, game, scion, controller=0)
    game.continuous_effects.recalculate(game)
    assert Keyword.VIGILANCE in white_creature.keywords

    game.players[0].battlefield.remove(scion)
    game.continuous_effects.recalculate(game)

    assert Keyword.VIGILANCE not in white_creature.keywords, (
        "vigilance grant did not retract when Scion left the battlefield"
    )
