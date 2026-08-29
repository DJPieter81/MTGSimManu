"""Triggers that add extra mana when a permanent is tapped for mana.

DB sizing (22,506 Modern cards, measured 2026-08-29): 39 cards carry a
"whenever … tapped for mana" trigger.  Grouped by the SHAPE of the trigger's
watcher and by whether the rider is additional mana:

  A  "whenever enchanted <type> is tapped for mana, … adds …"      12  BUILT
     (Aura granting to its host — already modelled as `aura_mana_units`;
      this change adds the missing chosen-colour half.)
  B  "whenever you tap a <filter> for mana, add …"                 12  BUILT
     (Leyline of Abundance, Nissa Who Shakes the World, Crypt Ghast,
      Zendikar Resurgent, Kinnan, Mirari's Wake, Nikya, Vorinclex,
      Nirkana Revenant, Badgermole Cub, Groundchuck & Dirtbag, Roxanne.)
  C  "whenever a player taps a <filter> for mana"                   5  SKIPPED
  D  "whenever a <filter> is tapped for mana, its controller adds"  4  SKIPPED
  E  "whenever an opponent taps …" / non-mana riders                7  SKIPPED

Shapes C/D/E are each below the abstraction contract's ~10-card class
threshold, and C/D additionally require modelling mana added to the OPPONENT's
pool — a different engine surface.  They are deliberately not parsed.

Rules modelled:
  * CR 605.1 — a mana ability's resolution puts mana into its controller's
    pool; a triggered ability that triggers off it and adds mana is itself a
    mana ability, so the extra mana is available to the same payment.
  * CR 303.4 — the Aura shape grants to the object it enchants.
  * CR 614/616-style "as this enters, choose a color" — the colour is a
    CHOICE, so it is routed to the AI callback seam, never fixed in engine
    code.

Rule under test (mechanic-phrased, real DB cards used only as fixture
carriers for the oracle shapes):
  1. A tap-for-mana trigger adds its extra mana when a permanent matching its
     watch filter is tapped for mana.
  2. Mana CAPACITY (what the AI plans against) and actual PRODUCTION (what the
     payment path can spend) agree — they must come from one resolver.
  3. A chosen-colour rider produces exactly the chosen colour, and the choice
     comes from the callback seam.
  4. A permanent that does not match the watch filter does not trigger.
  5. End-to-end: a board carrying the accelerant reaches a higher mana total
     on the same turn than the identical board without it.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.mana_payment import ManaPayment
from engine.oracle_parser import (
    parse_aura_mana_color_is_chosen,
    parse_tap_for_mana_trigger,
)
from engine.permanent_effects import PermanentEffects

_DB = CardDatabase()


# ── Parser level: one shape in, one typed field out ───────────────────

def test_controller_tap_trigger_parsed_with_fixed_symbol_rider():
    trig = parse_tap_for_mana_trigger(
        "Whenever you tap a creature for mana, add an additional {G}.")
    assert trig is not None, "the shape must parse into a typed trigger"
    assert trig.watch == "creature"
    assert trig.units == (("G",),)
    assert trig.mirror_source is False


def test_controller_tap_trigger_parsed_with_subtype_filter():
    trig = parse_tap_for_mana_trigger(
        "Whenever you tap a Forest for mana, add an additional {G}.")
    assert trig is not None and trig.watch == "forest"
    assert trig.units == (("G",),)


def test_controller_tap_trigger_parsed_with_mirror_source_rider():
    """"add one mana of any type that <permanent> produced" doubles the
    source rather than adding a fixed colour."""
    trig = parse_tap_for_mana_trigger(
        "Whenever you tap a land for mana, add one mana of any type that "
        "land produced.")
    assert trig is not None and trig.watch == "land"
    assert trig.mirror_source is True
    assert trig.units == ()


def test_controller_tap_trigger_parsed_for_nonland_permanent_filter():
    trig = parse_tap_for_mana_trigger(
        "Whenever you tap a nonland permanent for mana, add one mana of any "
        "type that permanent produced.")
    assert trig is not None and trig.watch == "nonland permanent"
    assert trig.mirror_source is True


def test_multi_symbol_rider_parses_as_multiple_units():
    trig = parse_tap_for_mana_trigger(
        "Whenever you tap a Swamp for mana, add an additional {B}{B}.")
    assert trig is not None and trig.units == (("B",), ("B",))


def test_self_referential_tap_trigger_with_no_mana_rider_is_not_a_trigger():
    """"Whenever you tap THIS creature for mana, it deals 1 damage" is the
    same trigger event with a non-mana rider — outside this class."""
    assert parse_tap_for_mana_trigger(
        "Whenever you tap this creature for mana, it deals 1 damage to each "
        "opponent.") is None


def test_opponent_scoped_tap_trigger_is_not_in_this_class():
    """Shape E: the mana would go to another player's pool. Not modelled."""
    assert parse_tap_for_mana_trigger(
        "Whenever an opponent taps a land for mana, that land doesn't untap "
        "during its controller's next untap step.") is None


def test_ordinary_mana_ability_carries_no_tap_trigger():
    assert parse_tap_for_mana_trigger("{T}: Add {G}.") is None


def test_chosen_colour_aura_rider_is_flagged_as_a_choice():
    assert parse_aura_mana_color_is_chosen(
        "Enchant Forest\nAs this Aura enters, choose a color.\nWhenever "
        "enchanted Forest is tapped for mana, its controller adds an "
        "additional one mana of the chosen color.") is True


def test_any_colour_aura_rider_is_not_a_choice():
    assert parse_aura_mana_color_is_chosen(
        "Enchant land\nWhenever enchanted land is tapped for mana, its "
        "controller adds an additional one mana of any color.") is False


# ── Board fixtures ────────────────────────────────────────────────────

def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 4
    game.players[0].deck_name = "Eldrazi Ramp"
    game.players[1].deck_name = "Dimir Midrange"
    return game


def _put(game, name, controller=0):
    tmpl = _DB.get_card(name)
    assert tmpl is not None, f"fixture card missing from DB: {name}"
    inst = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(),
                        zone="battlefield")
    inst._game_state = game
    inst.enter_battlefield()
    game.players[controller].battlefield.append(inst)
    # Mana dorks are only usable once they have been under their controller's
    # control since their most recent turn began (CR 302.6); the fixture puts
    # them on a board that has already been through an untap step.
    inst.summoning_sick = False
    return inst


def _put_lands(game, name, n, controller=0):
    return [_put(game, name, controller) for _ in range(n)]


# ── 1. The trigger fires and adds the extra mana on tap ───────────────

def test_tap_trigger_adds_extra_unit_to_each_matching_source():
    """Rule 1: every source matching the watch filter taps for one more."""
    game = _game()
    p = game.players[0]
    forests = _put_lands(game, "Forest", 3)
    base = p.untapped_mana_capacity()
    assert base == 3, f"fixture premise: 3 Forests tap for 3, got {base}"

    _put(game, "Zendikar Resurgent")  # "whenever you tap a land for mana…"
    after = p.untapped_mana_capacity()
    assert after == 6, (
        f"a land-watching tap trigger must double each land's output "
        f"({base} -> {after}, expected 6)")
    # And per-source: each Forest exposes its own unit plus the granted one.
    assert len(ManaPayment.land_mana_units(game, 0, forests[0])) == 2


def test_tap_trigger_stacks_when_multiple_copies_are_controlled():
    """Two separate triggers both fire off the same tap event."""
    game = _game()
    p = game.players[0]
    _put_lands(game, "Forest", 2)
    _put(game, "Zendikar Resurgent")
    one = p.untapped_mana_capacity()
    _put(game, "Nikya of the Old Ways")  # same shape, different card
    two = p.untapped_mana_capacity()
    assert two > one, (
        f"a second land-watching tap trigger must add again ({one} -> {two})")


def test_creature_watching_trigger_fires_on_mana_dorks_not_lands():
    """Rule 4 (positive half): Leyline of Abundance watches CREATURES."""
    game = _game()
    p = game.players[0]
    _put_lands(game, "Forest", 2)
    dork = _put(game, "Llanowar Elves")
    before = p.untapped_mana_capacity()
    _put(game, "Leyline of Abundance")
    after = p.untapped_mana_capacity()
    assert after == before + 1, (
        f"exactly the ONE creature mana source gains a unit, not the lands "
        f"({before} -> {after})")
    assert len(ManaPayment.land_mana_units(game, 0, dork)) == 2


# ── 4. A non-watched permanent does not trigger ───────────────────────

def test_non_matching_permanent_does_not_trigger_the_bonus():
    """Rule 4: a Forest-watching trigger ignores a Swamp."""
    game = _game()
    p = game.players[0]
    _put_lands(game, "Swamp", 3)
    before = p.untapped_mana_capacity()
    _put(game, "Nissa, Who Shakes the World")  # watches Forests
    after = p.untapped_mana_capacity()
    assert after == before, (
        f"a subtype-filtered trigger must not fire off a non-matching land "
        f"({before} -> {after})")


def test_trigger_controlled_by_the_opponent_does_not_boost_your_sources():
    game = _game()
    p = game.players[0]
    _put_lands(game, "Forest", 3)
    before = p.untapped_mana_capacity()
    _put(game, "Zendikar Resurgent", controller=1)
    assert p.untapped_mana_capacity() == before, (
        "'whenever YOU tap' is controller-scoped (CR 109.5)")


# ── 2. Capacity and actual production agree ───────────────────────────

@pytest.mark.parametrize("accelerant,n_lands,land,expected", [
    ("Zendikar Resurgent", 4, "Forest", 8),
    ("Nissa, Who Shakes the World", 4, "Forest", 8),
    ("Crypt Ghast", 4, "Swamp", 8),
])
def test_capacity_equals_what_the_payment_path_can_actually_spend(
        accelerant, n_lands, land, expected):
    """Rule 2 — the contract that keeps the fix from being inert.

    If capacity over-reports, the AI plans spells it cannot cast; if it
    under-reports, the AI never plans to use the accelerant at all. Both come
    from `ManaPayment.land_mana_units`, so this pins that they agree.
    """
    game = _game()
    p = game.players[0]
    _put_lands(game, land, n_lands)
    _put(game, accelerant)
    capacity = p.untapped_mana_capacity()
    assert capacity == expected, (
        f"{accelerant} over {n_lands} {land}s should report {expected} mana, "
        f"got {capacity}")
    # Actual production: paying a generic cost of exactly `capacity` succeeds…
    assert ManaPayment.tap_lands_for_mana(
        game, 0, ManaCost(generic=capacity)), (
        f"the payment path must be able to spend the full reported "
        f"capacity of {capacity}")


@pytest.mark.parametrize("accelerant,n_lands,land", [
    ("Zendikar Resurgent", 3, "Forest"),
    ("Crypt Ghast", 3, "Swamp"),
])
def test_capacity_is_not_over_reported_beyond_what_can_be_paid(
        accelerant, n_lands, land):
    """The other half of rule 2: one more than capacity must NOT be payable."""
    game = _game()
    p = game.players[0]
    _put_lands(game, land, n_lands)
    _put(game, accelerant)
    capacity = p.untapped_mana_capacity()
    assert not ManaPayment.tap_lands_for_mana(
        game, 0, ManaCost(generic=capacity + 1)), (
        f"capacity {capacity} must be an upper bound on what is payable")


def test_fixed_colour_rider_is_payable_as_that_colour():
    """Crypt Ghast's extra {B} must be spendable on a black pip, so the
    trigger's colour information survives into payment."""
    game = _game()
    p = game.players[0]
    _put_lands(game, "Swamp", 2)
    _put(game, "Crypt Ghast")
    assert ManaPayment.tap_lands_for_mana(game, 0, ManaCost(black=4)), (
        "two Swamps under a Swamp-doubler must pay {B}{B}{B}{B}")


# ── 3. The chosen colour is honoured ──────────────────────────────────

class _ColorChooser:
    """Minimal callbacks stub exercising the choice seam."""

    def __init__(self, pick):
        self.pick = pick
        self.seen = []

    def choose_mana_color(self, game, player_idx, source, options):
        self.seen.append((source.name, tuple(options)))
        return self.pick


def test_chosen_colour_aura_grants_only_the_chosen_colour():
    """Rule 3: 'adds one mana of the chosen color' is exactly one colour —
    modelling it as any-colour would hand the deck free colour fixing."""
    game = _game()
    game.callbacks = _ColorChooser("U")
    _put_lands(game, "Forest", 1)
    aura = _put(game, "Utopia Sprawl")
    host = PermanentEffects.attach_aura(game, aura, 0)
    assert host is not None, "Utopia Sprawl must enchant the Forest"
    assert aura.chosen_color == "U", (
        "the colour chosen as the Aura entered must be recorded on the "
        "instance")
    units = ManaPayment.land_mana_units(game, 0, host)
    granted = [u for u in units if u != ["G"]]
    assert granted == [["U"]], (
        f"the granted unit must be exactly the chosen colour, got {granted}")


def test_chosen_colour_decision_is_offered_to_the_callback_seam():
    game = _game()
    chooser = _ColorChooser("R")
    game.callbacks = chooser
    _put_lands(game, "Forest", 1)
    aura = _put(game, "Utopia Sprawl")
    PermanentEffects.attach_aura(game, aura, 0)
    assert chooser.seen, (
        "the engine must ASK for the colour rather than fixing one itself")
    name, options = chooser.seen[0]
    assert name == "Utopia Sprawl"
    assert set(options) == {"W", "U", "B", "R", "G"}


def test_engine_default_colour_choice_needs_no_ai_and_is_producible():
    """DefaultCallbacks must still make a legal, sane choice (engine may run
    headless in fixtures and tools)."""
    game = _game()
    _put_lands(game, "Forest", 1)
    aura = _put(game, "Utopia Sprawl")
    host = PermanentEffects.attach_aura(game, aura, 0)
    assert aura.chosen_color in {"W", "U", "B", "R", "G"}
    units = ManaPayment.land_mana_units(game, 0, host)
    assert len(units) == 2, f"host taps for its own unit plus one, got {units}"


def test_any_colour_aura_is_unaffected_by_the_choice_machinery():
    """Fertile Ground says 'any color' — it keeps the full option set."""
    game = _game()
    _put_lands(game, "Forest", 1)
    aura = _put(game, "Fertile Ground")
    host = PermanentEffects.attach_aura(game, aura, 0)
    assert aura.chosen_color is None
    units = ManaPayment.land_mana_units(game, 0, host)
    granted = [u for u in units if u != ["G"]]
    assert len(granted) == 1 and set(granted[0]) == {"W", "U", "B", "R", "G"}
