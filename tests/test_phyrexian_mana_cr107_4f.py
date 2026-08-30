"""Phyrexian mana (CR 107.4f): 2 life instead of one mana of a pip's colour.

    "{C/P} can be paid with either {C} or 2 life."

Two rules follow, and the engine broke both:

1. **A pip that life pays is not a coloured requirement.**  The cast-legality
   colour solver demanded a source of the pip's colour anyway, so a cost with
   Phyrexian pips was uncastable in a deck that lacked that colour — which is
   the entire reason such cards are played.  Isolating one variable (same
   board, same card, only the land type changed) showed the colour of the
   land, not life, deciding legality.

2. **The pip count must come from the MANA COST.**  It was counted in the
   ORACLE text, whose reminder clause names the symbol exactly ONCE however
   many pips the cost carries ({1}{B/P}{B/P} reads "({B/P} can be paid with
   either {B} or 2 life.)").  The count was therefore right only for
   single-pip costs, and would read 0 if reminder text were ever stripped.

Both rules are colour-sensitive in opposite directions: waiving a pip must
excuse its OWN colour and nothing else, so a {U}{G/P}-shaped cost stays
uncastable without a blue source however much life is available.

Class size: 28 Modern cards carry a Phyrexian pip in their printed mana cost;
7 of them carry more than one.  Card names below are fixture carriers for the
cost SHAPES ({C/P}, {1}{C/P}{C/P}, {2}{C}{C/P}, {C}{C}{C/P}{C}{C}) — no
card-specific behaviour is asserted.
"""
from __future__ import annotations

import random

from engine.card_database import parse_mana_cost_mtgjson
from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase


# ─── Cost shapes used as fixtures ────────────────────────────────────
# {C/P}                     — single pip, no other requirement
SHAPE_LONE_PIP = "Mutagenic Growth"
# {1}{C/P}{C/P}             — two pips of one colour plus generic
SHAPE_TWO_PIPS = "Dismember"
# {4}{C/P}{C/P}{C/P}        — three pips of one colour plus generic
SHAPE_THREE_PIPS = "K'rrik, Son of Yawgmoth"
# {2}{C}{C/P}               — a pip beside a HARD pip of the same colour
SHAPE_HARD_PIP_SAME_COLOUR = "Jace, the Perfected Mind"
# {C}{C}{C/P}{C}{C}         — a pip beside hard pips of OTHER colours
SHAPE_HARD_PIPS_OTHER_COLOURS = "Omnath, Locus of All"
# no pip at all             — negative pin
SHAPE_NO_PIP = "Lightning Bolt"


def _game(card_db, lands, spell_name, life=20):
    """Board with `lands` untapped, `spell_name` in hand, a creature on each
    side (so target-requiring spells have a legal target), main phase."""
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.players[0].life = life
    for pidx in (0, 1):
        bear = card_db.get_card("Grizzly Bears")
        inst = CardInstance(template=bear, owner=pidx, controller=pidx,
                            instance_id=game.next_instance_id(),
                            zone="battlefield")
        inst._game_state = game
        game.players[pidx].battlefield.append(inst)
    for land_name in lands:
        tmpl = card_db.get_card(land_name)
        assert tmpl is not None, f"missing land in DB: {land_name}"
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


# ─── Rule 2: the pip count is a property of the MANA COST ────────────


def test_pip_count_is_read_from_the_mana_cost_not_the_reminder_text(card_db):
    """A two-pip cost reports two pips even though its reminder text names
    the symbol once."""
    tmpl = card_db.get_card(SHAPE_TWO_PIPS)
    assert tmpl.phyrexian_pip_count == 2, (
        f"{SHAPE_TWO_PIPS} is {{1}}{{B/P}}{{B/P}} — two pips. Counting the "
        f"reminder clause (which names {{B/P}} once) reports 1.")


def test_pip_count_scales_with_the_number_of_pips_in_the_cost(card_db):
    """Three printed pips means three, not one and not two."""
    tmpl = card_db.get_card(SHAPE_THREE_PIPS)
    assert tmpl.phyrexian_pip_count == 3


def test_pip_colour_is_recorded_per_colour_not_as_a_bare_total(card_db):
    """Which colour a pip waives is load-bearing, so the cost stores the
    breakdown, not just a count."""
    tmpl = card_db.get_card(SHAPE_HARD_PIPS_OTHER_COLOURS)
    assert tmpl.mana_cost.phyrexian == {"B": 1}, (
        "a {W}{U}{B/P}{R}{G} cost has exactly one BLACK Phyrexian pip; the "
        "four other coloured pips are hard requirements")
    assert tmpl.phyrexian_pip_count == 1


def test_a_pip_still_counts_toward_the_costs_colour_and_cmc(card_db):
    """CR 107.4f only adds a payment option: the pip is still a coloured
    symbol of its colour and still contributes to mana value."""
    cost = parse_mana_cost_mtgjson("{1}{B/P}{B/P}")
    assert cost.black == 2
    assert cost.cmc == 3
    assert cost.phyrexian == {"B": 2}


def test_a_cost_without_pips_records_none(card_db):
    tmpl = card_db.get_card(SHAPE_NO_PIP)
    assert tmpl.mana_cost.phyrexian == {}
    assert tmpl.phyrexian_pip_count == 0


# ─── Rule 1: a life-payable pip is not a coloured requirement ────────


def test_pip_is_castable_without_a_source_of_its_colour(card_db):
    """The defining property of Phyrexian mana: no source of that colour is
    needed when life can pay."""
    game, spell = _game(card_db, ["Mountain"], SHAPE_LONE_PIP)
    assert CastManager.can_cast(game, 0, spell), (
        "a {G/P} cost must be castable off a red source — life pays the pip")


def test_a_lone_pip_needs_no_mana_source_at_all(card_db):
    """{C/P} alone is payable entirely with life."""
    game, spell = _game(card_db, [], SHAPE_LONE_PIP)
    assert CastManager.can_cast(game, 0, spell)


def test_multi_pip_cost_is_castable_without_its_colour(card_db):
    """{1}{B/P}{B/P} off a single non-black source: 4 life + {1}."""
    game, spell = _game(card_db, ["Mountain"], SHAPE_TWO_PIPS)
    assert CastManager.can_cast(game, 0, spell)


def test_waived_pip_also_drops_one_mana_from_the_quantity(card_db):
    """Life replaces the mana, so the remaining quantity shrinks by one per
    waived pip — {1}{B/P}{B/P} needs ONE untapped source, not three."""
    game, spell = _game(card_db, [], SHAPE_TWO_PIPS)
    assert not CastManager.can_cast(game, 0, spell), (
        "the generic {1} is not waivable — with zero sources the cost is "
        "still unpayable")
    game, spell = _game(card_db, ["Mountain"], SHAPE_TWO_PIPS)
    assert CastManager.can_cast(game, 0, spell)


# ─── Colour discipline: a waiver excuses its OWN colour only ─────────


def test_a_hard_pip_of_another_colour_is_still_required(card_db):
    """{W}{U}{B/P}{R}{G} in a mono-black deck stays uncastable: the pip's
    life payment excuses black, never the four hard pips."""
    game, spell = _game(card_db, ["Swamp"] * 5,
                        SHAPE_HARD_PIPS_OTHER_COLOURS)
    assert not CastManager.can_cast(game, 0, spell)


def test_the_same_cost_is_castable_when_only_the_pips_colour_is_missing(card_db):
    """The other way round: every hard pip covered, only the Phyrexian
    colour absent — life closes the gap."""
    game, spell = _game(card_db, ["Plains", "Island", "Mountain", "Forest"],
                        SHAPE_HARD_PIPS_OTHER_COLOURS)
    assert CastManager.can_cast(game, 0, spell)


def test_a_hard_pip_of_the_pips_own_colour_is_still_required(card_db):
    """{2}{C}{C/P}: the hard pip needs a real source even though its twin is
    waivable."""
    game, spell = _game(card_db, ["Mountain"] * 3,
                        SHAPE_HARD_PIP_SAME_COLOUR)
    assert not CastManager.can_cast(game, 0, spell)
    game, spell = _game(card_db, ["Island", "Mountain", "Mountain"],
                        SHAPE_HARD_PIP_SAME_COLOUR)
    assert CastManager.can_cast(game, 0, spell), (
        "one blue source covers the hard {U}; life covers the {U/P}")


# ─── The life threshold: legality and payment must agree exactly ─────


def test_life_must_exceed_two_per_waived_pip(card_db):
    """Paying to exactly 0 is lethal (CR 104.3b), so the rule is strict:
    life > 2 x pips.  Two pips need 5 life, not 4."""
    game, spell = _game(card_db, ["Mountain"], SHAPE_TWO_PIPS, life=4)
    assert not CastManager.can_cast(game, 0, spell)
    game, spell = _game(card_db, ["Mountain"], SHAPE_TWO_PIPS, life=5)
    assert CastManager.can_cast(game, 0, spell)


def test_legality_never_approves_a_payment_that_then_refuses(card_db):
    """The gate and the payment share one rule, so every life total that
    passes can_cast also completes the cast."""
    for life in range(1, 12):
        game, spell = _game(card_db, ["Mountain"], SHAPE_TWO_PIPS, life=life)
        if CastManager.can_cast(game, 0, spell):
            assert game.cast_spell(0, spell), (
                f"can_cast approved at {life} life but payment refused")


# ─── Payment: waive the fewest pips, keep the rest of the cost ───────


def test_payment_spends_no_life_when_mana_can_pay_the_pips(card_db):
    """A deck that has the colour must not bleed life for nothing."""
    game, spell = _game(card_db, ["Swamp"] * 3, SHAPE_TWO_PIPS)
    assert game.cast_spell(0, spell)
    assert game.players[0].life == 20


def test_payment_waives_only_the_pips_mana_cannot_cover(card_db):
    """One black source and one other: waive one pip (2 life), pay {B} and
    the generic with the two lands."""
    game, spell = _game(card_db, ["Swamp", "Mountain"], SHAPE_TWO_PIPS)
    assert game.cast_spell(0, spell)
    assert game.players[0].life == 18, (
        "exactly one pip needed waiving — waiving both would cost 4 life")


def test_payment_charges_two_life_per_waived_pip(card_db):
    game, spell = _game(card_db, ["Mountain"] * 3, SHAPE_TWO_PIPS)
    assert game.cast_spell(0, spell)
    assert game.players[0].life == 16


def test_payment_still_taps_for_the_non_waived_coloured_pips(card_db):
    """Waiving the {B/P} of {W}{U}{B/P}{R}{G} must not collapse the rest of
    the cost into generic — WURG still gets tapped for."""
    game, spell = _game(card_db, ["Plains", "Island", "Mountain", "Forest"],
                        SHAPE_HARD_PIPS_OTHER_COLOURS)
    assert game.cast_spell(0, spell)
    assert game.players[0].life == 18
    assert all(c.tapped for c in game.players[0].battlefield
               if c.template.is_land), "all four coloured sources are spent"


def test_a_cost_without_pips_spends_no_life(card_db):
    game, spell = _game(card_db, ["Mountain"], SHAPE_NO_PIP)
    assert game.cast_spell(0, spell)
    assert game.players[0].life == 20


def test_a_template_without_a_mana_cost_still_constructs():
    """`CardTemplate.mana_cost` is Optional — lands and many test fixtures
    build a template with no cost at all.

    Deriving `phyrexian_pip_count` from the COST rather than from oracle
    reminder text is what makes it correct for multi-pip cards, but the
    derivation runs in `__post_init__`, so it must tolerate a cost-less
    template instead of raising at construction. Regression: the first cut
    of that derivation did `self.mana_cost.phyrexian.values()` unguarded and
    took out every fixture that omits the cost.
    """
    from engine.cards import CardTemplate, CardType

    t = CardTemplate(name="fixture", card_types=[CardType.ARTIFACT],
                     mana_cost=None)
    assert t.phyrexian_pip_count == 0
