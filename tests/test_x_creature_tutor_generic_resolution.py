"""An X-bound creature tutor delivers a creature, whatever card it is on.

Mechanic under test: "search your library [and/or graveyard] for a creature
card with mana value X or less and put it onto the battlefield".  That shape
is parsed ONCE at DB load into ``CardTemplate.x_creature_tutor_data``.  The
AI's valuation gate (``ai/ev_player.py::_gate_x_tutor_payoff``) and the cast
path's X picker (``engine/cast_manager.py::pick_creature_tutor_x_value``)
both key off that parsed shape — so RESOLUTION must key off the same shape,
for every card carrying it, or the AI pays mana for a delivery the engine
never performs.

Live bug this pins (docs/diagnostics/2026-08-28 Creatures Toolbox replay
diagnosis): valuation generalised to the typed field, resolution did not.
Only one carrier had a hand-written ``EFFECT_REGISTRY`` handler; the others
resolved stack -> graveyard with no creature delivered.  Quoted from the
replays, same game, same turn, same goal — one carrier printed a "finds"
line, another printed none and delivered nothing across 3 casts / 14 mana.

The paired invariant, stronger than any single card: **a valuation gate keyed
on a parsed shape must never outrun the resolver for that shape.**  Every card
whose typed field is set must be delivered by the generic resolver; a card
whose riders the engine cannot execute faithfully must be REFUSED at parse
time (field stays ``None``, no valuation credit, no half-execution) rather
than resolved into a pretend delivery.

Card names below are fixture carriers loaded from the real DB, and the class
membership itself is read from the DB — never hard-coded here.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, Keyword
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase

_DB = CardDatabase()

# The class, read from the DB — every card whose SPELL resolution carries the
# parsed X-bound creature-search shape.  Parametrising over this (rather than
# a hand-written list) is the point: adding a carrier to the pool adds a case.
_CARRIERS = sorted(
    name for name, tmpl in _DB.cards.items()
    if getattr(tmpl, 'x_creature_tutor_data', None)
)

# Fixture library: one green creature at MV 6, one green creature at MV 1,
# one ARTIFACT creature at MV 2 — between them every colour/type constraint
# any carrier in the class can impose has a legal answer.
_LIBRARY = ["Primeval Titan", "Arboreal Grazer", "Steel Overseer"]


def _mk(game, name, controller, zone):
    tmpl = _DB.get_card(name)
    assert tmpl is not None, f"missing {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone in ("library", "hand", "graveyard", "battlefield"):
        if zone == "battlefield":
            card.enter_battlefield()
        getattr(game.players[controller], zone).append(card)
    return card


def _game(library_names=_LIBRARY):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Creatures Toolbox"
    game.players[1].deck_name = "Boros Energy"
    for nm in library_names:
        _mk(game, nm, 0, "library")
    return game


def _resolve_spell(game, spell_name, x_paid):
    """Resolve a tutor spell the way the engine does: the card is on the
    stack (in no zone list) and the paid X rides with the resolution."""
    from engine.oracle_resolver import resolve_spell_from_oracle

    spell = _mk(game, spell_name, 0, "stack")
    claimed = resolve_spell_from_oracle(game, spell, 0, None, x_value=x_paid)
    return spell, claimed


# ── The class-wide delivery invariant ────────────────────────────────

@pytest.mark.parametrize("carrier", _CARRIERS)
def test_an_x_bound_creature_tutor_delivers_a_creature_within_x(carrier):
    """Every carrier of the parsed shape puts a creature onto the
    battlefield whose mana value is within the X actually paid — with no
    per-card handler anywhere in the resolution path."""
    game = _game()
    _spell, claimed = _resolve_spell(game, carrier, 6)

    assert claimed, (
        f"{carrier} carries the X-creature-tutor shape but no resolver "
        f"claimed its resolution — the spell would resolve to nothing")
    delivered = game.players[0].creatures
    assert delivered, (
        f"{carrier} resolved at X=6 over a library holding "
        f"{_LIBRARY} and delivered no creature")
    for c in delivered:
        assert (c.template.cmc or 0) <= 6, (
            f"{carrier} delivered {c.name} (MV {c.template.cmc}) above the "
            f"X=6 that was paid")


@pytest.mark.parametrize("carrier", _CARRIERS)
def test_a_valued_x_tutor_shape_is_never_credited_without_a_resolver(carrier):
    """The paired invariant: the AI's valuation gate credits delivery for
    any card carrying the typed field, so every such card must have a
    resolver.  A shape the engine cannot deliver must be refused at parse
    time, not carried into valuation."""
    from engine.cast_manager import pick_creature_tutor_x_value

    game = _game()
    tmpl = _DB.get_card(carrier)
    _best_x, target, _top = pick_creature_tutor_x_value(game, 0, 6, tmpl)
    assert target is not None, (
        f"the shared X picker promises the AI a delivery for {carrier}; "
        f"it found none over {_LIBRARY}")

    game2 = _game()
    _spell, claimed = _resolve_spell(game2, carrier, 6)
    assert claimed and game2.players[0].creatures, (
        f"the X picker promised {carrier} a delivery but resolution "
        f"delivered nothing — valuation has outrun the resolver")


# ── Search mechanics: triggers, shuffle, bookkeeping ─────────────────

def test_a_library_search_shuffles_and_fires_search_triggers():
    """CR 701.19: a library search shuffles afterwards and is a real
    search — opponents' search-watching triggers must see it."""
    game = _game()
    fired = []
    shuffled = []
    game._trigger_library_search = lambda idx: fired.append(idx)
    real_shuffle = game.rng.shuffle
    game.rng.shuffle = lambda seq: (shuffled.append(len(seq)),
                                    real_shuffle(seq))[1]

    _resolve_spell(game, "Nature's Rhythm", 6)

    assert fired == [0], (
        f"the search must fire the library-search trigger for the searcher; "
        f"got {fired}")
    assert shuffled, "a library search must shuffle the library afterwards"
    assert game.players[0].library_searches_this_game == 1, (
        "the search must be counted in the per-game search bookkeeping")


def test_battlefield_entry_runs_the_etb_fan_out():
    """A tutored creature ENTERS the battlefield — its ETB abilities fan
    out exactly as they would for a cast creature."""
    game = _game()
    etbs = []
    real_etb = game._handle_permanent_etb
    game._handle_permanent_etb = lambda card, ctrl, *a, **k: (
        etbs.append((card.name, ctrl)), real_etb(card, ctrl, *a, **k))[1]

    _resolve_spell(game, "Nature's Rhythm", 6)

    assert etbs and etbs[0][1] == 0, (
        f"battlefield entry must run the ETB fan-out for the controller; "
        f"got {etbs}")
    assert any(c.name == etbs[0][0] for c in game.players[0].creatures)


def test_a_search_that_finds_nothing_still_shuffles_and_delivers_nothing():
    """CR 701.19: a failed search is still a search — it shuffles and
    counts, and it must not fabricate a delivery."""
    game = _game(library_names=["Forest", "Forest"])
    _spell, claimed = _resolve_spell(game, "Nature's Rhythm", 6)

    assert claimed, "the resolver still claims the spell on a whiff"
    assert not game.players[0].creatures, (
        "a search with no legal target must deliver nothing")
    assert game.players[0].library_searches_this_game == 1


# ── Destination variants ─────────────────────────────────────────────

def test_a_to_hand_destination_variant_lands_in_hand_not_on_the_battlefield():
    """The shared delivery seam honours the parsed destination: an X-bound
    creature tutor whose text says "put it into your hand" puts the card in
    hand.  Carrier here is the activated-ability form of the same shape,
    which routes through the SAME delivery helper."""
    from engine.activated_effects import resolve_activated_ability
    from engine.cards import ActivationEffectKind

    game = _game()
    source = _mk(game, "Citanul Flute", 0, "battlefield")
    ability = next(
        (a for a in (source.template.activated_abilities or [])
         if a.effect_kind is ActivationEffectKind.TUTOR_TO_HAND), None)
    assert ability is not None, (
        "Citanul Flute is the DB's to-hand X-bound creature tutor fixture")

    resolve_activated_ability(game, source, 0, None, ability=ability,
                              x_value=6)

    assert any(c.template.is_creature for c in game.players[0].hand), (
        f"a to-hand destination must put the found card in hand; hand is "
        f"{[c.name for c in game.players[0].hand]}")
    assert not [c for c in game.players[0].creatures if c is not source], (
        "a to-hand tutor must not put the found card onto the battlefield")


# ── Colour / type constraints on the search ──────────────────────────

def test_a_colour_constrained_search_only_finds_that_colour():
    """"a green creature card" is a real constraint on the search, not a
    decoration — an off-colour body is not a legal find."""
    game = _game(library_names=["Steel Overseer"])  # colourless artifact
    _spell, claimed = _resolve_spell(game, "Green Sun's Zenith", 6)

    assert claimed
    assert not game.players[0].creatures, (
        "a green-constrained search must not deliver a colourless creature")


def test_a_type_constrained_search_only_finds_that_type():
    """"an artifact creature card" excludes a non-artifact creature."""
    game = _game(library_names=["Primeval Titan"])  # green, not an artifact
    _spell, claimed = _resolve_spell(game, "Vision Quest", 6)

    assert claimed
    assert not game.players[0].creatures, (
        "an artifact-creature-constrained search must not deliver a "
        "non-artifact creature")


# ── Riders the engine executes, and riders it refuses ────────────────

def test_a_search_zone_rider_reaches_the_graveyard_as_well_as_the_library():
    """"search your library and/or graveyard" widens the search zone —
    a creature sitting only in the graveyard is a legal find."""
    game = _game(library_names=["Forest"])
    _mk(game, "Primeval Titan", 0, "graveyard")

    _spell, claimed = _resolve_spell(game, "Finale of Devastation", 6)

    assert claimed
    assert any(c.name == "Primeval Titan" for c in game.players[0].creatures), (
        f"the graveyard is inside this search's zone set; battlefield is "
        f"{[c.name for c in game.players[0].creatures]}")


def test_an_enters_with_x_counters_rider_puts_the_counters_on():
    """"put it onto the battlefield with X additional +1/+1 counters on it"
    is executed, not dropped."""
    game = _game(library_names=["Steel Overseer"])
    _resolve_spell(game, "Vision Quest", 3)

    found = [c for c in game.players[0].creatures]
    assert found and found[0].plus_counters == 3, (
        f"X=3 must add 3 +1/+1 counters to the delivered creature; got "
        f"{[(c.name, c.plus_counters) for c in found]}")


def test_an_x_threshold_haste_rider_fires_only_at_or_above_its_threshold():
    """"If X is 4 or greater, it gains haste" — executed at the threshold,
    not below it."""
    below = _game(library_names=["Steel Overseer"])
    _resolve_spell(below, "Vision Quest", 3)
    assert Keyword.HASTE not in below.players[0].creatures[0].keywords, (
        "below the printed threshold the haste rider must not fire")

    at = _game(library_names=["Steel Overseer"])
    _resolve_spell(at, "Vision Quest", 4)
    assert Keyword.HASTE in at.players[0].creatures[0].keywords, (
        "at the printed threshold the haste rider must fire")


def test_a_tutor_clause_inside_an_activated_ability_is_not_a_spell_shape():
    """The clause lives on an ACTIVATED ability, so casting the card is a
    plain permanent spell — the spell-side field must stay unset (the
    activation path owns that clause) or every cast of the body would be
    valued as a tutor."""
    tmpl = _DB.get_card("Fiend Artisan")
    assert getattr(tmpl, 'x_creature_tutor_data', None) is None, (
        "a clause inside an activated ability is not the spell's own "
        "resolution text and must not set the spell-side tutor field")
    from engine.cards import ActivationEffectKind
    kinds = [a.effect_kind for a in (tmpl.activated_abilities or [])]
    assert ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD in kinds, (
        "the activation path still owns that clause")


def test_a_rider_the_engine_cannot_execute_is_refused_not_half_executed():
    """A cast-conditioned triggered search ("if you cast it") depends on
    HOW the permanent entered — a fact the engine does not track.  The
    shape is refused at parse time: no typed field, so no valuation credit
    and no pretend delivery."""
    tmpl = _DB.get_card("Rocco, Cabaretti Caterer")
    assert getattr(tmpl, 'x_creature_tutor_data', None) is None, (
        "a rider the engine cannot execute faithfully must be refused, "
        "leaving the card unclassified rather than half-executed")

    game = _game()
    body = _mk(game, "Rocco, Cabaretti Caterer", 0, "battlefield")
    game._handle_permanent_etb(body, 0)
    assert len(game.players[0].creatures) == 1, (
        f"the refused shape must resolve as a no-op — nothing tutored; "
        f"battlefield is {[c.name for c in game.players[0].creatures]}")


# ── The X picker still picks the cheapest delivering X ───────────────

def test_the_chosen_x_is_the_cheapest_that_delivers_through_the_generic_path():
    """Pinned elsewhere for the single handled card; assert it still holds
    now that every carrier shares one resolver and one picker."""
    from engine.cast_manager import pick_creature_tutor_x_value

    game = _game(library_names=["Arboreal Grazer", "Primeval Titan"])
    tmpl = _DB.get_card("Nature's Rhythm")

    best_x, target, _top = pick_creature_tutor_x_value(game, 0, 8, tmpl)
    assert (target.template.cmc or 0) == 6 and best_x == 6, (
        f"budget 8 over a 1-drop and a 6-drop must pick X=6 for the 6-drop; "
        f"got X={best_x} for {target.name if target else None}")

    best_x, target, _top = pick_creature_tutor_x_value(game, 0, 4, tmpl)
    assert (target.template.cmc or 0) == 1 and best_x == 1, (
        f"budget 4 reaches only the 1-drop, so X must be 1; got X={best_x} "
        f"for {target.name if target else None}")
