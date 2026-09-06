"""A "target player … discards" spell resolves against the TARGETED player,
including its own caster — and a graveyard-fill deck uses that line.

# Mechanic the tests name

"Target player reveals their hand. You choose a nonland card from it.
That player discards that card." is a targeted effect (CR 115.1): the
caster may target themself, and for a reanimator that is the deck's
second discard outlet — bin the fatty with the hand attack, return it
with the reanimation spell.  The sim could not play that line at all:

- the engine resolved every such spell against the opponent, ignoring
  the chosen target, and picked the discarded card by mana value inside
  the engine (scoring in the rules layer);
- the AI never considered its own hand as the target;
- the keep/mull rule's enabler bucket was a card-name list, so a hand
  holding payoff + hand attack + fatty + lands was shipped ("need
  enabler") every game — Goryo's Vengeance vs Domain Zoo s50000 keeps
  4, 5 and 5 cards (docs/diagnostics/2026-09-05_zoo_band_loop_break.md).

Class: 75 pool cards can target the caster (11 caster-chosen, 62
victim-chosen, 2 random).  The falsified lanes are respected: the
7/6-card typed-path gate still requires enabler AND payoff (RC-3 flat
sets are not reopened); the outlet counts only when the hand holds a
card it can bin that a payoff in hand can return.

Card names below are fixture carriers only.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT, PLAYER_TARGET_SELF
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        game.players[controller].battlefield.append(c)
    elif zone == "library":
        game.players[controller].library.append(c)
    elif zone == "graveyard":
        game.players[controller].graveyard.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    _put(game, card_db, "Swamp", 0, "battlefield")
    return game


def _resolve(game, card_db, name, controller, targets=None):
    spell = CardInstance(template=card_db.get_card(name), owner=controller,
                         controller=controller,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    from engine.oracle_resolver import resolve_spell_from_oracle
    return resolve_spell_from_oracle(game, spell, controller, targets)


def _names(zone):
    return [c.name for c in zone]


# ── Engine: resolution honours the chosen target ─────────────────────


def test_target_player_discard_resolves_against_the_targeted_player_including_its_caster(card_db):
    game = _game(card_db)
    _put(game, card_db, "Griselbrand", 0, "hand")
    _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "hand")
    assert _resolve(game, card_db, "Thoughtseize", 0, [PLAYER_TARGET_SELF])
    assert "Griselbrand" in _names(game.players[0].graveyard)
    assert _names(game.players[1].hand) == ["Ragavan, Nimble Pilferer"]
    assert game.players[0].life == 18, "the caster still pays the life"

    assert _resolve(game, card_db, "Thoughtseize", 0, [PLAYER_TARGET_OPPONENT])
    assert _names(game.players[1].hand) == []
    assert game.players[0].life == 16


def test_target_player_discard_defaults_to_the_opponent_when_no_target_is_chosen(card_db):
    game = _game(card_db)
    _put(game, card_db, "Griselbrand", 0, "hand")
    _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "hand")
    assert _resolve(game, card_db, "Thoughtseize", 0, None)
    assert _names(game.players[1].hand) == []
    assert _names(game.players[0].hand) == ["Griselbrand"]


def test_choose_clause_is_honoured_when_the_caster_targets_itself(card_db):
    game = _game(card_db)
    big = _put(game, card_db, "Griselbrand", 0, "hand")          # MV 8
    _put(game, card_db, "Ragavan, Nimble Pilferer", 0, "hand")    # MV 1
    assert _resolve(game, card_db, "Inquisition of Kozilek", 0,
                    [PLAYER_TARGET_SELF])                        # cap: MV ≤ 3
    assert big in game.players[0].hand
    assert "Ragavan, Nimble Pilferer" in _names(game.players[0].graveyard)


def test_discard_choice_is_delegated_to_the_callback_not_the_engine(card_db):
    """The engine names the legal candidates; WHICH one goes is the
    AI's call (strip ranking against an opponent, reanimation fuel for
    oneself).  A callback that prefers the cheapest card must be
    obeyed over the engine's old max-mana-value pick."""
    game = _game(card_db)
    seen = {}

    class _Cheapest(type(game.callbacks)):
        def choose_discard(self, game_, player_idx, hand, self_discard):
            seen["hand"] = list(hand)
            seen["self_discard"] = self_discard
            return min(hand, key=lambda c: c.template.cmc or 0)

    game.callbacks = _Cheapest()
    _put(game, card_db, "Emrakul, the Aeons Torn", 1, "hand")     # MV 15
    _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "hand")    # MV 1
    _put(game, card_db, "Swamp", 1, "hand")                       # not eligible
    assert _resolve(game, card_db, "Thoughtseize", 0, [PLAYER_TARGET_OPPONENT])
    assert "Ragavan, Nimble Pilferer" in _names(game.players[1].graveyard)
    assert _names(seen["hand"]) == ["Emrakul, the Aeons Torn",
                                    "Ragavan, Nimble Pilferer"]
    assert seen["self_discard"] is False

    _put(game, card_db, "Griselbrand", 0, "hand")
    assert _resolve(game, card_db, "Thoughtseize", 0, [PLAYER_TARGET_SELF])
    assert seen["self_discard"] is True, "own-hand discard is a self-discard"


def test_hand_attack_class_resolves_through_the_generic_resolver_not_a_card_name_handler(card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    assert not EFFECT_REGISTRY.has_handler("Thoughtseize",
                                           EffectTiming.SPELL_RESOLVE)


def test_supertypes_come_from_the_printed_type_line_not_a_name_table(card_db):
    """A legendary-only reanimation spell may return only a creature
    whose printed type line says Legendary.  The former name table
    that stamped LEGENDARY on a non-legendary creature so the spell
    could hit it made both the reanimation legality and the legend
    rule wrong for that card."""
    from engine.cards import Supertype
    from engine.target_solver import parse, _matches_supertype
    archon = card_db.get_card("Archon of Cruelty")     # "Creature — Archon"
    assert Supertype.LEGENDARY not in (archon.supertypes or [])
    # Positive control: a printed legendary keeps its supertype, and the
    # type-line fallback still recognises the other supertypes.
    assert Supertype.LEGENDARY in card_db.get_card("Griselbrand").supertypes
    assert Supertype.BASIC in card_db.get_card("Mountain").supertypes
    assert not (card_db.get_card("Memnite").supertypes or [])
    legendary_only = next(
        r for r in parse(card_db.get_card("Goryo's Vengeance").oracle_text)
        if r.zone == "graveyard")
    assert legendary_only.supertype == "legendary"
    inst = CardInstance(template=archon, owner=0, controller=0,
                        instance_id=1, zone="graveyard")
    assert not _matches_supertype(inst, legendary_only.supertype)


# ── AI: the caster targets itself when that is the plan ──────────────


def _goryos_ai():
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name="Goryo's Vengeance",
                    rng=random.Random(0))


def _zoo_opponent(game, card_db):
    for _ in range(2):
        _put(game, card_db, "Stubborn Denial", 1, "hand")
    for _ in range(6):
        _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "library")
    game.players[1].deck_name = "Domain Zoo"


def test_graveyard_fill_deck_targets_itself_when_it_holds_a_binnable_target_and_a_payoff(card_db):
    game = _game(card_db)
    seize = _put(game, card_db, "Thoughtseize", 0, "hand")
    _put(game, card_db, "Griselbrand", 0, "hand")
    _put(game, card_db, "Goryo's Vengeance", 0, "hand")
    _zoo_opponent(game, card_db)
    ai = _goryos_ai()
    assert ai._choose_targets(game, seize) == [PLAYER_TARGET_SELF]


def test_graveyard_fill_deck_targets_the_opponent_once_the_target_is_already_in_the_graveyard(card_db):
    game = _game(card_db)
    seize = _put(game, card_db, "Thoughtseize", 0, "hand")
    _put(game, card_db, "Griselbrand", 0, "graveyard")
    _put(game, card_db, "Atraxa, Grand Unifier", 0, "hand")
    _put(game, card_db, "Goryo's Vengeance", 0, "hand")
    _zoo_opponent(game, card_db)
    ai = _goryos_ai()
    assert ai._choose_targets(game, seize) != [PLAYER_TARGET_SELF]


def test_deck_without_a_graveyard_plan_never_targets_itself(card_db):
    from ai.ev_player import EVPlayer
    game = _game(card_db)
    seize = _put(game, card_db, "Thoughtseize", 0, "hand")
    _put(game, card_db, "Griselbrand", 0, "hand")
    _zoo_opponent(game, card_db)
    ai = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                  rng=random.Random(0))
    assert ai._choose_targets(game, seize) != [PLAYER_TARGET_SELF]


def test_self_target_line_is_worth_casting(card_db):
    """The cast that sets up the reanimation scores positive even when
    the opponent's hidden hand holds nothing worth taking."""
    game = _game(card_db)
    seize = _put(game, card_db, "Thoughtseize", 0, "hand")
    _put(game, card_db, "Griselbrand", 0, "hand")
    _put(game, card_db, "Goryo's Vengeance", 0, "hand")
    for _ in range(8):
        _put(game, card_db, "Plains", 1, "library")
    ai = _goryos_ai()
    from ai.ev_evaluator import snapshot_from_game
    snap = snapshot_from_game(game, 0)
    assert ai._score_spell(seize, snap, game, game.players[0],
                           game.players[1]) > 0


# ── Mulligan: a self-targetable outlet covers the enabler bucket ─────


def _decider():
    from ai.gameplan import create_goal_engine
    from ai.mulligan import MulliganDecider
    from ai.strategy_profile import ArchetypeStrategy
    return MulliganDecider(ArchetypeStrategy.COMBO,
                           create_goal_engine("Goryo's Vengeance"))


def _hand(card_db, names):
    out = []
    for i, n in enumerate(names, 1):
        t = card_db.get_card(n)
        assert t is not None, f"missing card in DB: {n}"
        out.append(CardInstance(template=t, owner=0, controller=0,
                                instance_id=i, zone="hand"))
    return out


def test_self_targetable_outlet_that_can_bin_a_returnable_target_covers_the_enabler_bucket(card_db):
    d = _decider()
    hand = _hand(card_db, ["Thoughtseize", "Marsh Flats", "Goryo's Vengeance",
                           "Griselbrand", "Flooded Strand", "Swamp",
                           "Inquisition of Kozilek"])
    assert d.decide(hand, 7), d.last_reason


def test_outlet_whose_restriction_cannot_take_the_target_does_not_count(card_db):
    d = _decider()
    hand = _hand(card_db, ["Inquisition of Kozilek", "Marsh Flats",
                           "Goryo's Vengeance", "Griselbrand",
                           "Flooded Strand", "Swamp", "Undying Evil"])
    assert not d.decide(hand, 7), d.last_reason


def test_outlet_counts_only_when_a_payoff_in_hand_can_return_the_binned_card(card_db):
    d = _decider()
    # Goryo's Vengeance returns a LEGENDARY creature; Archon is not.
    hand = _hand(card_db, ["Thoughtseize", "Marsh Flats", "Goryo's Vengeance",
                           "Archon of Cruelty", "Flooded Strand", "Swamp",
                           "Undying Evil"])
    assert not d.decide(hand, 7), d.last_reason
    # Unburial Rites returns any creature card.
    hand = _hand(card_db, ["Thoughtseize", "Marsh Flats", "Unburial Rites",
                           "Archon of Cruelty", "Flooded Strand", "Swamp",
                           "Undying Evil"])
    assert d.decide(hand, 7), d.last_reason


def test_outlet_without_a_binnable_target_leaves_the_rule_unchanged(card_db):
    d = _decider()
    hand = _hand(card_db, ["Thoughtseize", "Marsh Flats", "Goryo's Vengeance",
                           "Unburial Rites", "Flooded Strand", "Swamp",
                           "Ephemerate"])
    assert not d.decide(hand, 7), d.last_reason


def test_outlet_makes_the_goal_conjunction_reachable_at_the_keep_floor(card_db):
    d = _decider()
    hand = _hand(card_db, ["Thoughtseize", "Griselbrand", "Goryo's Vengeance",
                           "Swamp", "Marsh Flats"])
    assert not d.conjunction_unreachable(hand)
