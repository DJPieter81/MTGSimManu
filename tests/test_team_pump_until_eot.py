"""Overrun-style team pump — "creatures you control get +N/+N [and gain
<keyword>] until end of turn" — as ONE mechanic class, on both carriers:

  * a permanent's own ETB trigger ("When this creature enters, ..." —
    Craterhoof Behemoth, End-Raze Forerunners, Inspiring Captain, ...:
    ~30 Modern permanents), and
  * an instant/sorcery's resolution text (Overrun, Overwhelming Stampede,
    Charge, ...: ~100 Modern spells).

The shape is parsed ONCE at DB load into ``CardTemplate.team_pump_data``
(fixed +N/+M, or +X/+X scaled by the controller's creature count / greatest
power; the granted keywords; the "other creatures" exclusion).  Resolution
keys off that typed field only — no oracle text is inspected at resolve time
— and writes to the until-end-of-turn channels every other pump already
uses (``temp_power_mod`` / ``temp_toughness_mod`` / ``temp_keywords``),
which the cleanup step clears (CR 514.2).

Before this mechanic existed the ETB form was a silent no-op: the class's
biggest payoff attacked as a base 5/5 with neither pump nor trample.

Rules under test are phrased as rules; card names below are fixture
carriers read from the real DB, and the class itself is enumerated from the
DB, never hand-listed.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType, Keyword
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_team_pump
from engine.oracle_resolver import (resolve_etb_from_oracle,
                                    resolve_spell_from_oracle)

_DB = CardDatabase()

# The class, read from the DB.
_ETB_CARRIERS = sorted(
    name for name, tmpl in _DB.cards.items()
    if (getattr(tmpl, 'team_pump_data', None) or {}).get('trigger') == 'etb')
_SPELL_CARRIERS = sorted(
    name for name, tmpl in _DB.cards.items()
    if (getattr(tmpl, 'team_pump_data', None) or {}).get('trigger') == 'spell')


def _mk(game, name, controller, zone):
    tmpl = _DB.get_card(name)
    assert tmpl is not None, f"missing {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    if zone in ("library", "hand", "graveyard", "battlefield"):
        getattr(game.players[controller], zone).append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    return game


# ── Parse-time classification ────────────────────────────────────────

def test_the_class_is_populated_on_both_carriers():
    """The typed field is set for a real population of ETB permanents AND of
    instants/sorceries — this is a mechanic class, not a card patch."""
    assert len(_ETB_CARRIERS) >= 10, _ETB_CARRIERS
    assert len(_SPELL_CARRIERS) >= 10, _SPELL_CARRIERS


def test_creature_count_scaling_is_parsed_from_the_where_x_is_clause():
    data = parse_team_pump(
        "Haste\nWhen this creature enters, creatures you control gain "
        "trample and get +X/+X until end of turn, where X is the number of "
        "creatures you control.")
    assert data == {'trigger': 'etb', 'power': None, 'toughness': None,
                    'scaling': 'creature_count', 'keywords': ['trample'],
                    'others_only': False}


def test_fixed_pump_with_keyword_list_and_others_exclusion_is_parsed():
    data = parse_team_pump(
        "Vigilance, trample, haste\nWhen this creature enters, other "
        "creatures you control get +2/+2 and gain vigilance and trample "
        "until end of turn.")
    assert data == {'trigger': 'etb', 'power': 2, 'toughness': 2,
                    'scaling': '', 'keywords': ['vigilance', 'trample'],
                    'others_only': True}


def test_asymmetric_fixed_pump_is_parsed():
    data = parse_team_pump(
        "When this creature enters, creatures you control get +1/+0 and "
        "gain haste until end of turn.")
    assert data['power'] == 1 and data['toughness'] == 0
    assert data['keywords'] == ['haste']


def test_spell_form_is_parsed_with_reminder_text_stripped():
    data = parse_team_pump(
        "Creatures you control get +3/+3 and gain trample until end of "
        "turn. (Each of those creatures can deal excess combat damage to "
        "the player or planeswalker it's attacking.)")
    assert data == {'trigger': 'spell', 'power': 3, 'toughness': 3,
                    'scaling': '', 'keywords': ['trample'],
                    'others_only': False}


def test_leading_until_end_of_turn_and_greatest_power_scaling_are_parsed():
    data = parse_team_pump(
        "Until end of turn, creatures you control gain trample and get "
        "+X/+X, where X is the greatest power among creatures you control.")
    assert data['trigger'] == 'spell'
    assert data['scaling'] == 'greatest_power'
    assert data['keywords'] == ['trample']


@pytest.mark.parametrize("oracle", [
    # single-target pump — a different mechanic (target, not team)
    "Target creature gets +3/+3 until end of turn.",
    # static anthem — a continuous effect, not an until-EOT pump
    "Other creatures you control get +1/+1.",
    # intervening-if condition the resolver does not evaluate
    "When this creature enters, if it was kicked, creatures you control "
    "get +1/+0 and gain haste until end of turn.",
    # another permanent's ETB (a watcher), not this permanent's own
    "Whenever another creature enters under your control, creatures you "
    "control get +1/+1 until end of turn.",
    # activated ability — owned by the activation path
    "{1}{R}: Creatures you control get +1/+0 and gain haste until end of "
    "turn.",
    # a rider the engine does not execute must refuse the whole card
    "When this creature enters, other creatures you control get +2/+2 and "
    "gain vigilance and menace until end of turn. Damage can't be "
    "prevented this turn.",
    # a keyword outside the until-EOT keyword channel's vocabulary
    "Until end of turn, creatures you control get +1/+1 and gain trample "
    "and infect.",
    # +X/+X with no definition of X
    "When this creature enters, creatures you control get +X/+X until end "
    "of turn.",
    "", None,
])
def test_shapes_outside_the_mechanic_are_refused(oracle):
    assert parse_team_pump(oracle) is None


# ── ETB resolution ───────────────────────────────────────────────────

def test_etb_team_pump_scales_with_creature_count():
    """+X/+X where X is the number of creatures you control: with N
    creatures on the battlefield (the entering one included, CR 603.10 —
    it is on the battlefield when its own trigger resolves) every one of
    them gets +N/+N and the granted keyword."""
    game = _game()
    carrier = next(n for n in _ETB_CARRIERS
                   if _DB.get_card(n).team_pump_data['scaling']
                   == 'creature_count')
    bodies = [_mk(game, "Arboreal Grazer", 0, "battlefield") for _ in range(3)]
    before = [(c.power, c.toughness) for c in bodies]
    hoof = _mk(game, carrier, 0, "battlefield")
    n = len(game.players[0].creatures)
    assert n == 4

    assert resolve_etb_from_oracle(game, hoof, 0) is True

    granted = {Keyword(k) for k in hoof.template.team_pump_data['keywords']}
    for c, (p, t) in zip(bodies, before):
        assert (c.power, c.toughness) == (p + n, t + n), (
            f"{c.name} must get +{n}/+{n}; got {c.power}/{c.toughness}")
        assert granted <= c.keywords
    assert hoof.temp_power_mod == n, "the entering creature pumps itself too"
    assert granted <= hoof.keywords


def test_etb_team_pump_with_fixed_amount_excludes_self_when_printed_other():
    """"other creatures you control get +N/+M" pumps every OTHER creature
    by exactly the printed amounts and grants every printed keyword; the
    source itself is untouched."""
    game = _game()
    carrier = next(n for n in _ETB_CARRIERS
                   if _DB.get_card(n).team_pump_data['others_only']
                   and _DB.get_card(n).team_pump_data['scaling'] == '')
    data = _DB.get_card(carrier).team_pump_data
    body = _mk(game, "Arboreal Grazer", 0, "battlefield")
    p0, t0 = body.power, body.toughness
    source = _mk(game, carrier, 0, "battlefield")

    assert resolve_etb_from_oracle(game, source, 0) is True

    assert (body.power, body.toughness) == (p0 + data['power'],
                                            t0 + data['toughness'])
    assert {Keyword(k) for k in data['keywords']} <= body.keywords
    assert source.temp_power_mod == 0 and source.temp_toughness_mod == 0


def test_etb_team_pump_only_touches_the_controllers_creatures():
    game = _game()
    carrier = _ETB_CARRIERS[0]
    theirs = _mk(game, "Arboreal Grazer", 1, "battlefield")
    p, t = theirs.power, theirs.toughness
    source = _mk(game, carrier, 0, "battlefield")
    resolve_etb_from_oracle(game, source, 0)
    assert (theirs.power, theirs.toughness) == (p, t)
    assert not theirs.temp_keywords


@pytest.mark.parametrize("carrier", _ETB_CARRIERS)
def test_every_etb_carrier_is_claimed_by_the_generic_resolver(carrier):
    """A parsed shape that valuation can see must be delivered by the
    resolver for every carrier — no per-card handler anywhere."""
    game = _game()
    body = _mk(game, "Arboreal Grazer", 0, "battlefield")
    source = _mk(game, carrier, 0, "battlefield")
    assert resolve_etb_from_oracle(game, source, 0) is True
    data = source.template.team_pump_data
    assert body.temp_power_mod > 0 or body.temp_toughness_mod > 0 or (
        data['scaling'] == '' and data['power'] == 0
        and data['toughness'] == 0)


def test_etb_team_pump_fires_through_the_zone_funnel():
    """Entering via the sanctioned zone transfer reaches the ETB fan-out,
    which reaches this resolver: no test-only call path."""
    game = _game()
    body = _mk(game, "Arboreal Grazer", 0, "battlefield")
    p0 = body.power
    carrier = next(n for n in _ETB_CARRIERS
                   if _DB.get_card(n).team_pump_data['scaling']
                   == 'creature_count')
    source = _mk(game, carrier, 0, "hand")
    from engine.zone_transfer import TransferKind, transfer
    transfer(game, source, src_zone="hand", dst_zone="battlefield",
             kind=TransferKind.ETB, controller=0)
    assert source in game.players[0].creatures
    assert body.power == p0 + len(game.players[0].creatures)


# ── Until end of turn ────────────────────────────────────────────────

def test_team_pump_wears_off_at_cleanup():
    """The pump and the granted keyword ride the until-EOT channels, which
    the cleanup step clears (CR 514.2)."""
    game = _game()
    body = _mk(game, "Arboreal Grazer", 0, "battlefield")
    p0, t0 = body.power, body.toughness
    source = _mk(game, _ETB_CARRIERS[0], 0, "battlefield")
    resolve_etb_from_oracle(game, source, 0)
    data = source.template.team_pump_data
    assert body.power > p0 or body.toughness > t0 or (
        data['power'] == 0 and data['toughness'] == 0)

    game.turn_mgr.cleanup_step(game)

    assert (body.power, body.toughness) == (p0, t0)
    assert not body.temp_keywords


# ── Spell-side ───────────────────────────────────────────────────────

def test_spell_team_pump_resolves_from_the_typed_field():
    """An instant/sorcery carrying the shape pumps the caster's team and
    grants the keyword at resolution."""
    game = _game()
    carrier = next(n for n in _SPELL_CARRIERS
                   if _DB.get_card(n).team_pump_data['scaling'] == ''
                   and _DB.get_card(n).team_pump_data['keywords'])
    data = _DB.get_card(carrier).team_pump_data
    body = _mk(game, "Arboreal Grazer", 0, "battlefield")
    p0, t0 = body.power, body.toughness
    spell = _mk(game, carrier, 0, "stack")

    assert resolve_spell_from_oracle(game, spell, 0, None) is True
    assert (body.power, body.toughness) == (p0 + data['power'],
                                            t0 + data['toughness'])
    assert {Keyword(k) for k in data['keywords']} <= body.keywords


def test_x_tutor_team_pump_rider_still_pumps_and_hastes_at_threshold():
    """Regression on the pre-existing spell path: the "if X is N or more,
    creatures you control get +X/+X and gain haste" rider of the X-bound
    creature tutor shares the team-pump application and still fires."""
    game = _game()
    body = _mk(game, "Arboreal Grazer", 0, "battlefield")
    p0 = body.power
    carrier = next(
        name for name, tmpl in _DB.cards.items()
        if (tmpl.x_creature_tutor_data or {}).get('team_pump_haste_at_x'))
    threshold = _DB.get_card(carrier).x_creature_tutor_data[
        'team_pump_haste_at_x']
    _mk(game, "Primeval Titan", 0, "library")
    spell = _mk(game, carrier, 0, "stack")

    assert resolve_spell_from_oracle(game, spell, 0, None,
                                     x_value=threshold) is True
    assert body.power == p0 + threshold
    assert Keyword.HASTE in body.keywords


def test_the_spell_carrier_is_not_also_an_etb_carrier():
    """One typed field, one trigger kind per card: the spell form never
    fires as an ETB and the ETB form never fires as a spell."""
    assert not set(_ETB_CARRIERS) & set(_SPELL_CARRIERS)
    for name in _SPELL_CARRIERS:
        types = _DB.get_card(name).card_types
        assert CardType.INSTANT in types or CardType.SORCERY in types, name
