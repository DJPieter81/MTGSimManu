"""Colour-setting continuous effects (CR 105.2b, CR 613 layer 5).

Rule under test
---------------
A permanent whose static ability says "<something> is/are all colors"
SETS the colour of the affected permanents.  This is a layer-5
continuous effect (CR 613.1e): it is re-derived continuously, it
never touches the printed characteristics of the card, and it stops
applying the moment its source leaves the battlefield.

Two scopes exist in the current card pool:

  * mass scope   — "Each nonland permanent you control is all colors"
                   (affects other permanents; lands and the
                   opponent's permanents are OUT of scope)
  * self scope   — "<this permanent> is all colors"
                   (affects only its own source)

Layer ordering is load-bearing here.  A colour-CONDITIONAL ability
grant ("each creature you control has hexproof if it's blue") lives
in layer 6 and must read the colour produced by layer 5, so the
colour-setting effect has to be applied first within one
`recalculate()` pass.  Without that, a creature under a colour-setting
static collects only the keywords matching its PRINTED colours.

Card names below are fixture carriers only — the engine reads the
parsed oracle shape (`CardTemplate.color_setting_scope`), never a
card name.  Class size of "is/are all colors" in ModernAtomic: 5
cards (Leyline of the Guildpact is the only mass-scope one; the other
four are self-scope).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.game_state import GameState
from engine.mana import Color

ALL_COLORS = {Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN}

MASS_SCOPE_ORACLE = "Each nonland permanent you control is all colors."
SELF_SCOPE_ORACLE = "This creature is all colors."


def _template(name, *, colors=frozenset(), card_types=None, oracle_text="",
              power=1, toughness=1):
    return CardTemplate(
        name=name,
        card_types=list(card_types or [CardType.CREATURE]),
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(colors), colors=set(colors),
        produces_mana=[], enters_tapped=False,
        oracle_text=oracle_text, tags=set(),
    )


def _put_on_battlefield(game, template, controller):
    card = CardInstance(
        template=template, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


def _put_named(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return _put_on_battlefield(game, tmpl, controller)


def _mass_source(game, controller, card_db=None):
    """A permanent carrying the mass-scope colour-setting static."""
    tmpl = _template("Test Mass Color Setter",
                     card_types=[CardType.ENCHANTMENT],
                     oracle_text=MASS_SCOPE_ORACLE)
    from engine.oracle_parser import parse_color_setting_scope
    tmpl.color_setting_scope = parse_color_setting_scope(MASS_SCOPE_ORACLE)
    return _put_on_battlefield(game, tmpl, controller)


# ── layer 5: the colour-setting effect itself ──────────────────────

def test_mass_color_setting_static_sets_your_nonland_permanents_to_all_colors():
    game = GameState(rng=random.Random(0))
    _mass_source(game, controller=0)
    creature = _put_on_battlefield(
        game, _template("Test Two Color Creature", colors={Color.RED, Color.GREEN}),
        controller=0)

    game.continuous_effects.recalculate(game)

    assert creature.colors == ALL_COLORS, (
        "a nonland permanent under a mass colour-setting static is all "
        f"five colours, got {creature.colors}")


def test_color_setting_static_does_not_apply_to_lands():
    game = GameState(rng=random.Random(0))
    _mass_source(game, controller=0)
    land = _put_on_battlefield(
        game, _template("Test Land", card_types=[CardType.LAND],
                        power=None, toughness=None),
        controller=0)

    game.continuous_effects.recalculate(game)

    assert land.colors == set(), (
        "the clause reads 'each NONLAND permanent you control' — lands keep "
        f"their printed colours, got {land.colors}")


def test_color_setting_static_does_not_apply_to_opposing_permanents():
    game = GameState(rng=random.Random(0))
    _mass_source(game, controller=0)
    theirs = _put_on_battlefield(
        game, _template("Test Opposing Creature", colors={Color.BLACK}),
        controller=1)

    game.continuous_effects.recalculate(game)

    assert theirs.colors == {Color.BLACK}, (
        "the clause reads 'each nonland permanent YOU control' — the "
        f"opponent's board is untouched, got {theirs.colors}")


def test_color_setting_static_retracts_when_source_leaves_battlefield():
    game = GameState(rng=random.Random(0))
    source = _mass_source(game, controller=0)
    creature = _put_on_battlefield(
        game, _template("Test Two Color Creature", colors={Color.RED, Color.GREEN}),
        controller=0)

    game.continuous_effects.recalculate(game)
    assert creature.colors == ALL_COLORS

    game.players[0].battlefield.remove(source)
    game.continuous_effects.recalculate(game)

    assert creature.colors == {Color.RED, Color.GREEN}, (
        "a layer-5 colour set is continuous — it stops applying as soon as "
        f"its source leaves the battlefield, got {creature.colors}")


def test_color_setting_static_does_not_mutate_the_shared_card_template():
    """Templates are shared database objects; a continuous effect that
    wrote through to one would leak into every other game."""
    game = GameState(rng=random.Random(0))
    _mass_source(game, controller=0)
    tmpl = _template("Test Two Color Creature", colors={Color.RED, Color.GREEN})
    creature = _put_on_battlefield(game, tmpl, controller=0)

    game.continuous_effects.recalculate(game)

    assert creature.colors == ALL_COLORS
    assert tmpl.colors == {Color.RED, Color.GREEN}, (
        "the effect must live on the INSTANCE — the template's printed "
        f"colours stay as printed, got {tmpl.colors}")


def test_self_scope_color_setting_static_applies_only_to_its_own_source():
    from engine.oracle_parser import parse_color_setting_scope

    game = GameState(rng=random.Random(0))
    tmpl = _template("Test Self Color Setter", oracle_text=SELF_SCOPE_ORACLE)
    tmpl.color_setting_scope = parse_color_setting_scope(SELF_SCOPE_ORACLE)
    source = _put_on_battlefield(game, tmpl, controller=0)
    bystander = _put_on_battlefield(
        game, _template("Test Bystander", colors={Color.BLUE}), controller=0)

    game.continuous_effects.recalculate(game)

    assert source.colors == ALL_COLORS, (
        f"'this permanent is all colors' sets its own colour, got {source.colors}")
    assert bystander.colors == {Color.BLUE}, (
        "a self-scope colour set must not spread to other permanents, "
        f"got {bystander.colors}")


# ── layer 5 → layer 6 ordering ─────────────────────────────────────

def test_color_conditional_keyword_grant_reads_colors_set_by_layer_5(card_db):
    """A layer-6 colour-conditional grant must see the layer-5 colour,
    not the printed colour — CR 613.1: layers are applied in order."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming

    game = GameState(rng=random.Random(0))
    _put_named(game, card_db, "Leyline of the Guildpact", controller=0)
    scion = _put_named(game, card_db, "Scion of Draco", controller=0)
    creature = _put_named(game, card_db, "Territorial Kavu", controller=0)

    EFFECT_REGISTRY.execute("Scion of Draco", EffectTiming.ETB,
                            game, scion, controller=0)
    game.continuous_effects.recalculate(game)

    assert creature.colors == ALL_COLORS
    expected = {Keyword.VIGILANCE, Keyword.HEXPROOF, Keyword.LIFELINK,
                Keyword.FIRST_STRIKE, Keyword.TRAMPLE}
    assert expected <= creature.keywords, (
        "every colour-conditional keyword applies once the creature is all "
        f"colours, missing {expected - creature.keywords}")


def test_permanent_entering_after_both_statics_gains_every_conditional_keyword(card_db):
    """The grant is continuous, not an entry stamp: a permanent that
    arrives after both statics picks them up on the next SBA pass."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming

    game = GameState(rng=random.Random(0))
    _put_named(game, card_db, "Leyline of the Guildpact", controller=0)
    scion = _put_named(game, card_db, "Scion of Draco", controller=0)
    EFFECT_REGISTRY.execute("Scion of Draco", EffectTiming.ETB,
                            game, scion, controller=0)
    game.continuous_effects.recalculate(game)

    latecomer = _put_on_battlefield(
        game, _template("Test Green Creature", colors={Color.GREEN}), controller=0)
    game._check_sba_once()

    assert latecomer.colors == ALL_COLORS
    assert Keyword.HEXPROOF in latecomer.keywords, (
        "a creature entering under both statics is blue (layer 5) and so "
        "has hexproof (layer 6)")


def test_opposing_creature_gains_no_color_conditional_keywords(card_db):
    """Neither half leaks across the table."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming

    game = GameState(rng=random.Random(0))
    _put_named(game, card_db, "Leyline of the Guildpact", controller=0)
    scion = _put_named(game, card_db, "Scion of Draco", controller=0)
    EFFECT_REGISTRY.execute("Scion of Draco", EffectTiming.ETB,
                            game, scion, controller=0)
    theirs = _put_on_battlefield(
        game, _template("Test Opposing Creature", colors={Color.WHITE}),
        controller=1)

    game.continuous_effects.recalculate(game)

    assert theirs.colors == {Color.WHITE}
    assert theirs.keywords == set(), (
        f"opponent's creature gains nothing, got {theirs.keywords}")


# ── the parse itself ───────────────────────────────────────────────

@pytest.mark.parametrize("oracle,expected", [
    ("Each nonland permanent you control is all colors.", "your_nonland_permanents"),
    ("Transguild Courier is all colors.", "self"),
    ("This creature is all colors.", "self"),
    ("Sphinx of the Guildpact is all colors.\nFlying", "self"),
    ("Add one mana of any color.", ""),
    ("", ""),
])
def test_color_setting_scope_is_parsed_from_oracle_text(oracle, expected):
    from engine.oracle_parser import parse_color_setting_scope
    assert parse_color_setting_scope(oracle) == expected


def test_every_all_colors_card_in_the_pool_parses_to_a_scope(card_db):
    """Whole-class check: no card carrying the clause is left unparsed."""
    from engine.oracle_parser import parse_color_setting_scope
    unparsed = []
    for name, tmpl in card_db.cards.items():
        oracle = tmpl.oracle_text or ""
        lowered = oracle.lower()
        if "is all colors" in lowered or "are all colors" in lowered:
            if not parse_color_setting_scope(oracle):
                unparsed.append(name)
            # the template field is what the engine reads at runtime
            assert tmpl.color_setting_scope == parse_color_setting_scope(oracle), (
                f"{name}: CardDatabase did not populate color_setting_scope")
    assert unparsed == [], f"unparsed colour-setting cards: {unparsed}"
