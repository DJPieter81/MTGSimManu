"""Hybrid mana (CR 107.4e): a hybrid pip is paid with ONE of its colours.

    "{W/U} can be paid with either {W} or {U}."   (CR 107.4e)
    "{2/W} can be paid with either {2} or {W}; its mana value is 2."
                                                   (CR 107.4e / 202.3e)
    "Cost reductions … reduce only the generic component."  (CR 601.2f)

The engine modelled every hybrid pip as GENERIC mana (`parse_mana_cost_
mtgjson`: `# simplified`). Two rules broke, in opposite directions:

1. **A hybrid pip needs a source of one of its colours.**  As generic it
   needed none, so a {R/W}{R/W}{R/W} creature was castable off three
   Islands, and a hybrid card could never be colour-screwed.

2. **A generic cost reduction cannot touch a hybrid pip.**  As generic it
   could: two "cost {1} less" permanents made a {1}{R/G} spell FREE.
   Observed (Ruby Storm vs Azorius Control, seed 50000 game 3): one land
   already tapped, a Ruby Medallion and a Ral on the battlefield, and
   Manamorphose — which adds two mana when it resolves — cast seven times
   in one turn with no mana available, each cast netting +2. A turn-5 kill
   from one land through an infinite-mana engine no printed card provides.

Class size: 545 colour-hybrid and 18 two-brid cards in ModernAtomic; every
generic cost reducer in the pool interacts with all of them. Card names
below are fixture carriers for the cost SHAPES ({1}{C/D}, {C/D}x3,
{1}{C/D}{C/D}, {N/C}x3, {1}{C}) — no card-specific behaviour is asserted.
"""
from __future__ import annotations

import random

from engine.card_database import parse_mana_cost_mtgjson
from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase
from engine.mana import ManaPool


# ─── Cost shapes used as fixtures ────────────────────────────────────
SHAPE_GENERIC_PLUS_HYBRID = "Manamorphose"          # {1}{R/G}  — the exploit shape
SHAPE_ALL_HYBRID = "Boros Reckoner"                  # {R/W}{R/W}{R/W}
SHAPE_GENERIC_PLUS_TWO_HYBRID = "Ashiok, Dream Render"  # {1}{U/B}{U/B}
SHAPE_TWO_BRID = "Spectral Procession"               # {2/W}{2/W}{2/W}
SHAPE_GENERIC_PLUS_COLOUR = "Wrenn's Resolve"        # {1}{R}
GENERIC_REDUCER_RED = "Ruby Medallion"               # "Red spells you cast cost {1} less"


def _game(card_db, lands, spell_name, *, battlefield=()):
    """Board with `lands` untapped, `spell_name` in hand, extra permanents
    from `battlefield`, a creature on each side, main phase."""
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for pidx in (0, 1):
        bear = card_db.get_card("Grizzly Bears")
        inst = CardInstance(template=bear, owner=pidx, controller=pidx,
                            instance_id=game.next_instance_id(),
                            zone="battlefield")
        inst._game_state = game
        game.players[pidx].battlefield.append(inst)
    for name in list(lands) + list(battlefield):
        tmpl = card_db.get_card(name)
        assert tmpl is not None, f"missing card in DB: {name}"
        inst = CardInstance(template=tmpl, owner=0, controller=0,
                            instance_id=game.next_instance_id(),
                            zone="battlefield")
        inst._game_state = game
        inst.tapped = False
        game.players[0].battlefield.append(inst)
    tmpl = card_db.get_card(spell_name)
    assert tmpl is not None, f"missing card in DB: {spell_name}"
    spell = CardInstance(template=tmpl, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="hand")
    spell._game_state = game
    game.players[0].hand.append(spell)
    return game, spell


def _tapped_lands(game):
    return [c for c in game.players[0].battlefield
            if c.template.is_land and getattr(c, "tapped", False)]


# ─── The cost records hybrid pips as pips, not as generic ────────────


def test_hybrid_pips_are_parsed_as_pips_not_generic():
    cost = parse_mana_cost_mtgjson("{R/G}{R/G}")
    assert cost.generic == 0, "a hybrid pip is not generic mana"
    assert cost.hybrid == [("R", "G"), ("R", "G")]
    assert cost.cmc == 2
    assert cost.non_generic_pips == 2

    cost = parse_mana_cost_mtgjson("{1}{U/B}{U/B}")
    assert (cost.generic, cost.hybrid, cost.cmc) == (1, [("U", "B"), ("U", "B")], 3)
    assert cost.non_generic_pips == 2

    # Two-brid: pay {2} or {W}; mana value counts the 2 (CR 202.3e).
    cost = parse_mana_cost_mtgjson("{2/W}")
    assert cost.generic == 0
    assert cost.hybrid == [("W", "2")]
    assert cost.cmc == 2


def test_a_cost_without_hybrid_pips_records_none(card_db):
    assert card_db.get_card(SHAPE_GENERIC_PLUS_COLOUR).mana_cost.hybrid == []


def test_every_printed_mana_symbol_is_accounted_for(card_db):
    """Pool-wide invariant: the parsed mana value of every card equals
    MTGJSON's own (per-face) mana value.

    This is the guard that would have caught the hybrid defect on day
    one: a symbol class the parser drops or folds wrongly shows up as a
    mana-value mismatch somewhere in 22k cards.  Split cards compare
    against their face value (MTGJSON's card-level value is the sum of
    both halves, CR 709.4b).
    """
    mismatches = []
    checked = 0
    for name, entry in card_db._raw_data.items():
        face = entry[0] if isinstance(entry, list) else entry
        printed = face.get("manaCost")
        expected = face.get("faceManaValue",
                            face.get("manaValue", face.get("convertedManaCost")))
        if printed is None or expected is None:
            continue
        checked += 1
        got = parse_mana_cost_mtgjson(printed).cmc
        if got != int(expected):
            mismatches.append((name, printed, int(expected), got))
    assert checked > 10000, "the invariant must run over the real pool"
    assert not mismatches, (
        f"{len(mismatches)} card(s) whose parsed mana value disagrees with "
        f"MTGJSON — a mana symbol class is being dropped or misvalued: "
        f"{mismatches[:10]}")


# ─── CR 107.4e: a hybrid pip needs a source of ONE of its colours ─────


def test_a_hybrid_pip_needs_a_source_of_one_of_its_colours(card_db):
    game, spell = _game(card_db, ["Plains"] * 3, SHAPE_ALL_HYBRID)
    assert CastManager.can_cast(game, 0, spell), "{R/W}x3 off three Plains"
    game, spell = _game(card_db, ["Mountain", "Plains", "Mountain"], SHAPE_ALL_HYBRID)
    assert CastManager.can_cast(game, 0, spell), "{R/W}x3 off a mix of the two colours"
    game, spell = _game(card_db, ["Island"] * 3, SHAPE_ALL_HYBRID)
    assert not CastManager.can_cast(game, 0, spell), (
        "{R/W}x3 is NOT castable off three Islands — a hybrid pip is a "
        "coloured requirement, not generic mana")


def test_generic_plus_hybrid_needs_the_hybrid_colours_and_any_generic(card_db):
    game, spell = _game(card_db, ["Mountain", "Island", "Swamp"],
                        SHAPE_GENERIC_PLUS_TWO_HYBRID)
    assert CastManager.can_cast(game, 0, spell), "{1}{U/B}{U/B}: R for {1}, U and B for the pips"
    game, spell = _game(card_db, ["Mountain", "Mountain", "Mountain"],
                        SHAPE_GENERIC_PLUS_TWO_HYBRID)
    assert not CastManager.can_cast(game, 0, spell), "three Mountains pay neither pip"
    game, spell = _game(card_db, ["Island", "Mountain"], SHAPE_GENERIC_PLUS_HYBRID)
    assert CastManager.can_cast(game, 0, spell), "{1}{R/G}: Island for {1}, Mountain for the pip"
    game, spell = _game(card_db, ["Island", "Island"], SHAPE_GENERIC_PLUS_HYBRID)
    assert not CastManager.can_cast(game, 0, spell), "two Islands pay the {1} but not {R/G}"


def test_mana_pool_pays_hybrid_with_either_colour():
    cost = parse_mana_cost_mtgjson("{R/G}{R/G}")
    pool = ManaPool(); pool.add("R", 2)
    assert pool.can_pay(cost) and pool.pay(cost) and pool.total() == 0
    pool = ManaPool(); pool.add("G", 1); pool.add("R", 1)
    assert pool.can_pay(cost) and pool.pay(cost) and pool.total() == 0
    pool = ManaPool(); pool.add("U", 2)
    assert not pool.can_pay(cost), "blue mana pays neither {R/G} pip"
    # Generic beside a hybrid pip: the pip claims its colour, the rest is generic.
    cost = parse_mana_cost_mtgjson("{1}{U/B}")
    pool = ManaPool(); pool.add("B", 1); pool.add("R", 1)
    assert pool.can_pay(cost) and pool.pay(cost) and pool.total() == 0
    pool = ManaPool(); pool.add("R", 2)
    assert not pool.can_pay(cost), "two red pay the {1} but not {U/B}"


def test_two_brid_pays_with_the_colour_or_the_generic_alternative(card_db):
    game, spell = _game(card_db, ["Plains"] * 3, SHAPE_TWO_BRID)
    assert CastManager.can_cast(game, 0, spell), "{2/W}x3 off three Plains"
    game, spell = _game(card_db, ["Mountain"] * 6, SHAPE_TWO_BRID)
    assert CastManager.can_cast(game, 0, spell), "{2/W}x3 off six Mountains ({2} each)"
    game, spell = _game(card_db, ["Mountain"] * 5, SHAPE_TWO_BRID)
    assert not CastManager.can_cast(game, 0, spell), "five Mountains pay only two pips"
    game, spell = _game(card_db, ["Plains", "Mountain", "Mountain", "Mountain", "Mountain"],
                        SHAPE_TWO_BRID)
    assert CastManager.can_cast(game, 0, spell), "W + {2} + {2} mixes the two modes"


# ─── CR 601.2f: a generic cost reduction never reaches a hybrid pip ───


def test_a_generic_cost_reduction_cannot_reduce_a_hybrid_pip(card_db):
    """The observed exploit: two 'cost {1} less' permanents, no untapped
    source, and a {1}{R/G} spell was castable for nothing."""
    two_reducers = [GENERIC_REDUCER_RED, GENERIC_REDUCER_RED]
    game, spell = _game(card_db, [], SHAPE_GENERIC_PLUS_HYBRID, battlefield=two_reducers)
    assert not CastManager.can_cast(game, 0, spell), (
        "the reductions eat the {1}; the {R/G} pip still needs a source — "
        "with none the spell is uncastable however many reducers are in play")
    game, spell = _game(card_db, ["Island"], SHAPE_GENERIC_PLUS_HYBRID, battlefield=two_reducers)
    assert not CastManager.can_cast(game, 0, spell), "an Island cannot pay {R/G}"
    game, spell = _game(card_db, ["Mountain"], SHAPE_GENERIC_PLUS_HYBRID, battlefield=two_reducers)
    assert CastManager.can_cast(game, 0, spell)
    # And the payment itself taps the source for the pip.
    assert CastManager.cast_spell(game, 0, spell)
    assert len(_tapped_lands(game)) == 1, "the Mountain pays the {R/G} pip"

    # All-hybrid shape: three pips, three sources, whatever the reductions.
    game, spell = _game(card_db, ["Mountain", "Plains"], SHAPE_ALL_HYBRID, battlefield=two_reducers)
    assert not CastManager.can_cast(game, 0, spell), "two sources pay two of three pips"
    game, spell = _game(card_db, ["Mountain", "Plains", "Mountain"], SHAPE_ALL_HYBRID,
                        battlefield=two_reducers)
    assert CastManager.can_cast(game, 0, spell)
    assert CastManager.cast_spell(game, 0, spell)
    assert len(_tapped_lands(game)) == 3, "all three sources are tapped"


def test_quantity_gate_never_drops_below_the_non_generic_pips(card_db):
    """{1}{R} under two generic reducers still needs a red source: the
    reductions eat the {1} and stop there."""
    two_reducers = [GENERIC_REDUCER_RED, GENERIC_REDUCER_RED]
    game, spell = _game(card_db, ["Island"], SHAPE_GENERIC_PLUS_COLOUR,
                        battlefield=two_reducers)
    assert not CastManager.can_cast(game, 0, spell)
    game, spell = _game(card_db, ["Mountain"], SHAPE_GENERIC_PLUS_COLOUR,
                        battlefield=two_reducers)
    assert CastManager.can_cast(game, 0, spell)


def test_ai_reducible_portion_excludes_hybrid_pips(card_db):
    """The AI's mana estimate must agree with the engine: the reducible
    portion of a cost is its generic component only."""
    from ai.effective_cmc import _generic_portion
    assert _generic_portion(card_db.get_card(SHAPE_ALL_HYBRID)) == 0
    assert _generic_portion(card_db.get_card(SHAPE_GENERIC_PLUS_HYBRID)) == 1
    assert _generic_portion(card_db.get_card(SHAPE_GENERIC_PLUS_TWO_HYBRID)) == 1
    assert _generic_portion(card_db.get_card(SHAPE_GENERIC_PLUS_COLOUR)) == 1
