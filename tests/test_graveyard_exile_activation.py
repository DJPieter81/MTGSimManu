"""Graveyard-exile activations (CR 602 + CR 406): "[Cost]: Exile target
player's graveyard / all graveyards / N target cards from a graveyard."

This is an EFFECT-kind gap, not a cost gap: the costs these permanents
charge ({T}, sacrifice-self, mana) have been payable since tranches 1-3,
so every one of them was already reaching `can_activate` and being
refused by the effect-kind whitelist. 33 Modern cards carry one of the
five sentence shapes classified here — the single largest cluster left
in the unclassified activated-ability histogram (the plain "Exile target
card from a graveyard" line alone is on 15 of them).

Rules pinned:
  * classification is ANCHORED to the full sentence — every rider
    ("… Draw a card.", "… If it was a creature card, …") leaves the line
    UNCLASSIFIED rather than half-executing;
  * the parsed shape rides on the ability as structured data
    (`graveyard_exile_data`), parsed ONCE at DB load;
  * a card-targeting shape carries a real graveyard-zone
    TargetRequirement, so CR 601.2c refuses the activation when no
    graveyard holds a legal card;
  * every exile is a ZONE CHANGE and goes through the zone funnel, so
    leaves-the-graveyard triggers and replacements see it;
  * the whole-graveyard shapes differ in WHOSE graveyards they clear —
    "all graveyards" is symmetric and takes the activator's own, "each
    opponent's" is one-sided;
  * CR 608.2b — a declared target that has already left the graveyard is
    skipped, not silently redirected.

Card names in test bodies are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.activated_effects import resolve_activated_ability
from engine.activation import ActivationManager
from engine.card_database import CardDatabase
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import (classify_activation_effect,
                                  parse_activated_abilities,
                                  parse_activation_graveyard_exile)

_DB = CardDatabase()


# ── classification: the five executable shapes ────────────────────────

def test_whole_graveyard_shapes_classify_with_their_scope():
    for sentence, scope in (
            ("Exile target player's graveyard", 'target_player'),
            ("Exile all graveyards", 'all'),
            ("Exile all cards from all graveyards", 'all'),
            ("Exile each opponent's graveyard", 'each_opponent')):
        kind, *_ = classify_activation_effect(sentence + ".")
        assert kind is ActivationEffectKind.EXILE_FROM_GRAVEYARD, sentence
        spec = parse_activation_graveyard_exile(sentence.lower())
        assert spec is not None and spec['scope'] == scope, sentence


def test_card_targeting_shapes_parse_count_owner_and_type_filter():
    plain = parse_activation_graveyard_exile(
        "exile target card from a graveyard")
    assert plain == {'scope': 'cards', 'count': 1, 'up_to': False,
                     'types': [], 'owner': 'any', 'single_graveyard': False}

    typed = parse_activation_graveyard_exile(
        "exile target creature card from a graveyard")
    assert typed['types'] == ['creature'] and typed['count'] == 1

    mine = parse_activation_graveyard_exile(
        "exile target creature card from your graveyard")
    assert mine['owner'] == 'you'

    upto = parse_activation_graveyard_exile(
        "exile up to two target cards from a single graveyard")
    assert upto['count'] == 2 and upto['up_to'] is True
    assert upto['single_graveyard'] is True

    theirs = parse_activation_graveyard_exile(
        "exile two target cards from an opponent's graveyard")
    assert theirs['count'] == 2 and theirs['owner'] == 'opponent'
    assert theirs['up_to'] is False


def test_riders_and_unbounded_counts_stay_unclassified():
    """The tranche discipline: never half-execute. A trailing clause is a
    second effect; an X-bound count is a number the engine cannot charge
    honestly."""
    refused = [
        "Exile target card from a graveyard. Draw a card",
        "Exile all graveyards. Draw a card",
        "Exile target card from a graveyard. You gain 1 life",
        "Exile up to X target cards from a single graveyard",
        "Target player exiles a card from their graveyard",
        "Exile target creature card from a graveyard. Create a 2/2 black "
        "Zombie creature token",
        "Put target card from a graveyard on the bottom of its owner's "
        "library",
    ]
    for sentence in refused:
        kind, *_ = classify_activation_effect(sentence + ".")
        assert kind is ActivationEffectKind.UNCLASSIFIED, sentence


def test_parsed_shape_rides_on_the_ability_as_structured_data():
    abilities = parse_activated_abilities(
        "{T}, Sacrifice this artifact: Exile target player's graveyard.")
    assert len(abilities) == 1
    ab = abilities[0]
    assert ab.effect_kind is ActivationEffectKind.EXILE_FROM_GRAVEYARD
    assert ab.graveyard_exile_data['scope'] == 'target_player'
    assert ab.cost.tap_self and ab.cost.sacrifice_self
    assert ab.cost.unpayable == ()
    # A whole-graveyard shape declares no card target.
    assert ab.targets_required == 0

    carded = parse_activated_abilities(
        "{T}: Exile target card from a graveyard.")[0]
    assert carded.targets_required == 1
    req = carded.target_requirements[0]
    assert req.zone == "graveyard" and "card" in req.types


# ── engine execution ──────────────────────────────────────────────────

def _add(game, name, controller=0, zone="battlefield"):
    tmpl = _DB.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    for _ in range(4):
        _add(game, "Swamp")
    return game


def _ability(scope, count=1, up_to=False, types=(), owner='any',
             single=False, index=0):
    return ActivatedAbility(
        index=index,
        cost=ActivationCost(mana=ManaCost(), tap_self=True),
        effect_text="Exile ...",
        effect_kind=ActivationEffectKind.EXILE_FROM_GRAVEYARD,
        graveyard_exile_data={'scope': scope, 'count': count,
                              'up_to': up_to, 'types': list(types),
                              'owner': owner, 'single_graveyard': single})


def test_targeted_player_scope_clears_that_players_whole_graveyard():
    game = _game()
    src = _add(game, "Tormod's Crypt")
    for _ in range(3):
        _add(game, "Swamp", 1, "graveyard")
    mine = _add(game, "Swamp", 0, "graveyard")

    assert resolve_activated_ability(
        game, src, 0, [], ability=_ability('target_player')) is True
    assert game.players[1].graveyard == []
    assert len(game.players[1].exile) == 3
    assert all(c.zone == "exile" for c in game.players[1].exile)
    # One-sided: the activator's own graveyard is untouched.
    assert mine in game.players[0].graveyard


def test_symmetric_scope_clears_both_graveyards_including_the_activators():
    game = _game()
    src = _add(game, "Sentinel Totem")
    _add(game, "Swamp", 0, "graveyard")
    _add(game, "Swamp", 1, "graveyard")

    assert resolve_activated_ability(
        game, src, 0, [], ability=_ability('all')) is True
    assert game.players[0].graveyard == []
    assert game.players[1].graveyard == []
    assert len(game.players[0].exile) == 1
    assert len(game.players[1].exile) == 1


def test_each_opponent_scope_is_one_sided():
    game = _game()
    src = _add(game, "Soul-Guide Lantern")
    mine = _add(game, "Swamp", 0, "graveyard")
    _add(game, "Swamp", 1, "graveyard")

    assert resolve_activated_ability(
        game, src, 0, [], ability=_ability('each_opponent')) is True
    assert game.players[1].graveyard == []
    assert game.players[0].graveyard == [mine]


def test_card_scope_exiles_only_the_declared_targets():
    game = _game()
    src = _add(game, "Withered Wretch")
    keep = _add(game, "Swamp", 1, "graveyard")
    hit = _add(game, "Swamp", 1, "graveyard")

    assert resolve_activated_ability(
        game, src, 0, [hit.instance_id], ability=_ability('cards')) is True
    assert game.players[1].graveyard == [keep]
    assert hit.zone == "exile" and hit in game.players[1].exile


def test_a_target_that_left_the_graveyard_is_skipped_not_redirected():
    """CR 608.2b — the ability does not silently pick a new victim."""
    game = _game()
    src = _add(game, "Withered Wretch")
    gone = _add(game, "Swamp", 1, "graveyard")
    other = _add(game, "Swamp", 1, "graveyard")
    game.zone_mgr.move_card(game, gone, "graveyard", "hand", cause="test")

    applied = resolve_activated_ability(
        game, src, 0, [gone.instance_id], ability=_ability('cards'))
    assert applied is False
    assert game.players[1].graveyard == [other]


def test_activation_is_illegal_when_no_graveyard_holds_a_legal_card():
    """CR 601.2c — a required target must have a legal choice."""
    game = _game()
    src = _add(game, "Withered Wretch")
    abilities = parse_activated_abilities(
        "{B}: Exile target card from a graveyard.")
    ab = abilities[0]
    assert ActivationManager.can_activate(game, 0, src, ab) is False
    _add(game, "Swamp", 1, "graveyard")
    assert ActivationManager.can_activate(game, 0, src, ab) is True


def test_effect_kind_passes_the_activation_whitelist():
    """The whitelist was the binding gate: the costs were already
    payable, so classifying the effect is the whole unlock."""
    game = _game()
    src = _add(game, "Tormod's Crypt")
    _add(game, "Swamp", 1, "graveyard")
    ab = parse_activated_abilities(
        "{T}, Sacrifice this artifact: Exile target player's "
        "graveyard.")[0]
    assert ActivationManager.can_activate(game, 0, src, ab) is True


def test_real_pool_cards_now_classify_instead_of_being_unhandled():
    """DB-wide sizing check: the class is a class, not a card."""
    hits = [n for n, t in _DB.cards.items()
            if any(a.effect_kind is ActivationEffectKind.EXILE_FROM_GRAVEYARD
                   for a in (t.activated_abilities or []))]
    assert len(hits) >= 25, len(hits)
    for name in ("Tormod's Crypt", "Nihil Spellbomb", "Withered Wretch",
                 "Soul-Guide Lantern", "Thraben Heretic",
                 "Unlicensed Hearse", "Remorseful Cleric"):
        assert name in hits, name
