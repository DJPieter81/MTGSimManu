"""Activated tutor effects (CR 602 + CR 701.19): "[Cost]: Search your
library for a ... card, put it onto the battlefield / into your hand,
then shuffle."

The tranche-3 acceptance doc (docs/diagnostics/2026-08-26_...) named the
effect-kind whitelist as the binding gate for toolbox decks: a toolbox
deck IS its tutor activations. This file pins the mechanic class — every
activated-tutor permanent in Modern — never a single card.

Rules pinned:
  * Classification is ANCHORED to the full sentence: composite effects,
    union type constraints, multi-card searches and unsupported riders
    stay UNCLASSIFIED (the tranche discipline: never half-execute).
  * The search constraint (card type / subtype / supertype / color /
    mana-value bound) parses into structured data on the ability.
  * An {X} pip in an activation cost is a chargeable COUNT exactly when
    the effect binds X ("mana value X or less"); a hybrid pip charges as
    one generic mana — the caster picks the colour — matching the
    spell-side convention in `_parse_mana_symbols_to_cost`.
  * CR 701.19b — a search may fail: a tutor with no legal candidate in
    the library is STILL a legal activation; not paying for a whiff is
    the AI's judgment, not a legality question.
  * Resolution routes through the shared library-search machinery:
    opponents' search triggers fire, the found card moves through the
    zone funnel (battlefield entry gets ETB fan-out), the library is
    shuffled.
  * The library CHOICE is strategic: the engine default is the highest
    mana value satisfying the constraint; the AI callback picks the
    plan-best target.

Card names appearing in test BODIES are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.activation import ActivationManager
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import (classify_activation_effect,
                                  parse_activated_abilities,
                                  parse_activation_cost,
                                  parse_activation_tutor)

_DB = CardDatabase()

_BF_SENTENCE = ("Search your library for a creature card with mana value X "
                "or less, put it onto the battlefield, then shuffle")
_HAND_SENTENCE = ("Search your library for a Sliver card, reveal it, put it "
                  "into your hand, then shuffle")


# ── classification: the two executable shapes ─────────────────────────

def test_battlefield_tutor_sentence_classifies_with_x_bound_constraint():
    kind, *_ = classify_activation_effect(_BF_SENTENCE + ".")
    assert kind is ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD
    data = parse_activation_tutor(_BF_SENTENCE)
    assert data is not None
    assert data['dest'] == 'battlefield'
    assert 'creature' in data['types']
    assert data['mv_bound_is_x'] is True
    assert data['max_mv'] is None


def test_hand_tutor_sentence_classifies_with_and_without_reveal():
    kind, *_ = classify_activation_effect(_HAND_SENTENCE + ".")
    assert kind is ActivationEffectKind.TUTOR_TO_HAND
    data = parse_activation_tutor(_HAND_SENTENCE)
    assert data is not None and data['dest'] == 'hand'
    assert 'sliver' in data['subtypes']
    plain = ("Search your library for a land card, put it into your hand, "
             "then shuffle")
    kind2, *_ = classify_activation_effect(plain + ".")
    assert kind2 is ActivationEffectKind.TUTOR_TO_HAND


def test_constraint_parses_supertype_subtype_color_and_fixed_mv_bound():
    data = parse_activation_tutor(
        "Search your library for a green creature card with mana value 3 "
        "or less, put it onto the battlefield, then shuffle")
    assert data is not None
    assert data['colors'] == ['G']
    assert data['max_mv'] == 3 and data['mv_bound_is_x'] is False

    # A tapped battlefield-entry rider is captured, not dropped.
    data2 = parse_activation_tutor(
        "Search your library for a legendary creature card, put it onto "
        "the battlefield tapped, then shuffle")
    assert data2 is not None
    assert 'legendary' in data2['supertypes']
    assert data2['tapped'] is True

    # Negated type constraint.
    data3 = parse_activation_tutor(
        "Search your library for a nonland creature card, put it onto the "
        "battlefield, then shuffle")
    assert data3 is not None and 'land' in data3['not_types']


def test_unsupported_shapes_stay_unclassified():
    """The tranche discipline: a rider the resolver cannot execute
    faithfully refuses the whole line, never half-executes it."""
    refused = [
        # Union type constraint — a choice shape the schema cannot hold.
        "Search your library for an artifact or creature card, put it "
        "onto the battlefield, then shuffle",
        # Multi-card search.
        "Search your library for two creature cards, put them onto the "
        "battlefield, then shuffle",
        # Trailing rider sentence after the shuffle.
        "Search your library for a creature card, put it onto the "
        "battlefield, then shuffle. It gains haste until end of turn",
        # A "with ..." rider that is not a mana-value bound.
        "Search your library for a land card with a basic land type, put "
        "it onto the battlefield, then shuffle",
        # Non-creature battlefield destination is outside the two shapes.
        "Search your library for a basic land card, put it onto the "
        "battlefield tapped, then shuffle",
    ]
    for sentence in refused:
        kind, *_ = classify_activation_effect(sentence + ".")
        assert kind is ActivationEffectKind.UNCLASSIFIED, (
            f"must stay visible-but-refused: {sentence!r} -> {kind}")


# ── cost: X and hybrid pips ───────────────────────────────────────────

def test_x_pip_in_activation_cost_parses_as_a_chargeable_count():
    cost = parse_activation_cost("{X}{B/G}, {T}, Sacrifice another creature")
    assert cost is not None
    assert cost.x_count == 1
    # Hybrid pip charges one generic (caster picks the colour) — the
    # spell-side convention of _parse_mana_symbols_to_cost.
    assert cost.mana.generic == 1 and cost.mana.cmc == 1
    assert cost.tap_self is True
    assert cost.sacrifice_type == "creature" and cost.sacrifice_another
    assert cost.unpayable == ()


def test_hybrid_pip_charges_one_generic_like_the_spell_side_convention():
    cost = parse_activation_cost("{2}{G/U}")
    assert cost is not None
    assert cost.mana.generic == 3 and cost.mana.cmc == 3
    assert cost.x_count == 0
    assert cost.unpayable == ()


# ── full-line parse: constraint data rides on the ability ─────────────

def test_full_line_parse_attaches_structured_tutor_data():
    oracle = ("{X}{B/G}, {T}, Sacrifice another creature: " + _BF_SENTENCE
              + ". Activate only as a sorcery.")
    abilities = parse_activated_abilities(oracle)
    assert len(abilities) == 1
    ab = abilities[0]
    assert ab.effect_kind is ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD
    assert ab.sorcery_speed_only is True
    assert ab.cost.x_count == 1
    assert ab.tutor_data is not None
    assert ab.tutor_data['mv_bound_is_x'] is True
    assert 'creature' in ab.tutor_data['types']


# ── engine execution: legality, X charge, resolution ──────────────────

def _add(game, name, controller=0, zone="battlefield"):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game(n_lands=4, n_hand=2):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Eldrazi Tron"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_lands):
        _add(game, "Forest")
    for _ in range(n_hand):
        _add(game, "Forest", 0, "hand")
    return game


def _tutor_ability(mana=1, tap=True, x_count=0, dest="battlefield",
                   mv_bound_is_x=False, max_mv=None, types=("creature",),
                   subtypes=(), tapped=False):
    return ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(generic=mana), tap_self=tap,
                            x_count=x_count),
        effect_text="Search your library ...",
        effect_kind=(ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD
                     if dest == "battlefield"
                     else ActivationEffectKind.TUTOR_TO_HAND),
        tutor_data={
            'dest': dest, 'types': list(types), 'not_types': [],
            'supertypes': [], 'subtypes': list(subtypes), 'colors': [],
            'max_mv': max_mv, 'mv_bound_is_x': mv_bound_is_x,
            'tapped': tapped,
        })


def _host(game, ability, name="Wall of Omens"):
    perm = _add(game, name)
    perm.template = perm.template.__class__(**{
        **{f: getattr(perm.template, f)
           for f in perm.template.__dataclass_fields__},
        'activated_abilities': [ability]})
    return perm


def test_tutor_kinds_pass_the_effect_kind_whitelist():
    """Rule 9b previously refused every tutor before any cost check —
    the binding gate the tranche-3 acceptance doc named."""
    game = _game()
    _add(game, "Craterhoof Behemoth", 0, "library")
    ab = _tutor_ability()
    perm = _host(game, ab)
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_search_may_whiff_and_remains_a_legal_activation():
    """CR 701.19b — a search may fail. Legality does not depend on a
    deliverable target existing; not paying for a whiff is AI judgment."""
    game = _game()
    for _ in range(3):
        _add(game, "Forest", 0, "library")  # no creature to find
    ab = _tutor_ability()
    perm = _host(game, ab)
    assert ActivationManager.can_activate(game, 0, perm, ab)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    game.resolve_stack()
    assert game.players[0].library_searches_this_game == 1, (
        "a failed search is still a search — bookkeeping and triggers fire")
    assert all(c.zone == "library" for c in game.players[0].library)


def test_x_bound_tutor_requires_an_x_pip_and_an_unbound_x_is_refused():
    game = _game()
    _add(game, "Craterhoof Behemoth", 0, "library")
    # "mana value X or less" with no {X} pip to bind — incoherent, refuse.
    ab_no_pip = _tutor_ability(x_count=0, mv_bound_is_x=True)
    host1 = _host(game, ab_no_pip)
    assert not ActivationManager.can_activate(game, 0, host1, ab_no_pip)
    # An {X} pip on an effect that does not bind X — the engine cannot
    # know what X buys; refuse rather than silently charge X=0.
    ab_unbound = ActivatedAbility(
        index=0, cost=ActivationCost(mana=ManaCost(), tap_self=True,
                                     x_count=1),
        effect_text="Draw a card.",
        effect_kind=ActivationEffectKind.DRAW_N, amount=1)
    host2 = _host(game, ab_unbound)
    assert not ActivationManager.can_activate(game, 0, host2, ab_unbound)


def test_found_card_enters_via_the_zone_funnel_with_etb_fanout():
    game = _game()
    finder = _add(game, "Wall of Omens", 0, "library")  # ETB: draw a card
    for _ in range(3):
        _add(game, "Forest", 0, "library")  # so the ETB draw has fuel
    ab = _tutor_ability()
    perm = _host(game, ab)
    hand_before = len(game.players[0].hand)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    game.resolve_stack()
    assert finder.zone == "battlefield"
    assert finder in game.players[0].battlefield
    assert len(game.players[0].hand) == hand_before + 1, (
        "battlefield entry must get the ETB fan-out — the found card's "
        "enter trigger draws")


def test_search_fires_opponent_library_search_triggers():
    game = _game()
    _add(game, "Craterhoof Behemoth", 0, "library")
    watcher = _add(game, "Wall of Omens", 1)
    watcher.template = watcher.template.__class__(**{
        **{f: getattr(watcher.template, f)
           for f in watcher.template.__dataclass_fields__},
        'has_library_search_opponent_trigger': True})
    ab = _tutor_ability()
    perm = _host(game, ab)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    game.resolve_stack()
    assert game.players[0].library_searches_this_game == 1
    assert watcher.plus_counters == 1, (
        "the tutor must route through the shared search machinery so "
        "search-watching triggers keep firing")


def test_hand_destination_delivers_the_card_to_hand():
    game = _game()
    target = _add(game, "Craterhoof Behemoth", 0, "library")
    ab = _tutor_ability(dest="hand")
    perm = _host(game, ab)
    hand_before = len(game.players[0].hand)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    game.resolve_stack()
    assert target.zone == "hand"
    assert target in game.players[0].hand
    assert len(game.players[0].hand) == hand_before + 1
    assert target not in game.players[0].battlefield


def test_chosen_x_bounds_the_search_and_is_charged_as_mana():
    """The engine picks the cheapest X that delivers the best fetchable
    target (the pick_creature_tutor_x_value discipline) and charges
    fixed + X at payment time."""
    game = _game(n_lands=4)
    cheap = _add(game, "Wall of Omens", 0, "library")     # mv 2
    _add(game, "Craterhoof Behemoth", 0, "library")       # mv 8, over budget
    ab = _tutor_ability(mana=1, x_count=1, mv_bound_is_x=True)
    perm = _host(game, ab)
    untapped_before = len(game.players[0].untapped_lands)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    game.resolve_stack()
    assert cheap.zone == "battlefield", (
        "within budget (capacity 4 - fixed 1 = X<=3) only the mv-2 body "
        "is deliverable")
    # Fixed {1} + chosen X=2 -> 3 mana; the host's own tap is separate.
    assert untapped_before - len(game.players[0].untapped_lands) == 3, (
        "the chosen X is part of the COST — fixed + X mana must be paid")


def test_engine_default_choice_is_highest_mana_value_within_constraint():
    game = _game(n_lands=6)
    _add(game, "Birds of Paradise", 0, "library")         # mv 1
    big = _add(game, "Craterhoof Behemoth", 0, "library")  # mv 8
    ab = _tutor_ability(mana=0, tap=True)
    perm = _host(game, ab)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    game.resolve_stack()
    assert big.zone == "battlefield", (
        "with no AI callback preference, the engine default delivers the "
        "highest-mana-value candidate — the GSZ resolver's ranking")


def test_subtype_constraint_narrows_the_search():
    game = _game()
    _add(game, "Craterhoof Behemoth", 0, "library")
    bird = _add(game, "Birds of Paradise", 0, "library")
    ab = _tutor_ability(subtypes=("bird",))
    perm = _host(game, ab)
    assert ActivationManager.activate(game, 0, perm, ab, [])
    game.resolve_stack()
    assert bird.zone == "battlefield", (
        "only the subtype-matching candidate is deliverable")


def test_db_toolbox_carrier_card_parses_to_the_battlefield_tutor_kind():
    """The mechanic must light up for a real DB card carrying it —
    fixture carrier from the Creatures Toolbox list."""
    t = _DB.get_card("Fiend Artisan")
    assert t is not None
    tutors = [a for a in (t.activated_abilities or [])
              if a.effect_kind
              is ActivationEffectKind.TUTOR_CREATURE_TO_BATTLEFIELD]
    assert tutors, "the DB carrier's activated tutor line must classify"
    ab = tutors[0]
    assert ab.tutor_data['mv_bound_is_x'] is True
    assert ab.cost.x_count == 1
    assert ab.cost.sacrifice_type == "creature" and ab.cost.sacrifice_another
    assert ab.cost.unpayable == ()
    assert ab.sorcery_speed_only is True
