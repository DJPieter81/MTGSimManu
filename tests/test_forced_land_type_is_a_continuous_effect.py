""""Nonbasic lands are Mountains" is a continuous effect (CR 611.2a / 613.1d):
it applies to every nonbasic land on EVERY battlefield for as long as its
source is there — lands that enter later included — and the affected lands
lose their other abilities (CR 305.7: a land with a basic type set has
that type's mana ability and nothing else).  The lock's value to the caster
is what it makes uncastable, for as long as the game lasts.

# What the sim did

- The engine handler ran once at ETB, on the OPPONENT's lands only, by
  swapping in a per-instance template with produces_mana=['R'].  Lands
  played afterwards were untouched, the caster's own nonbasics kept their
  colours (the real card is symmetric — a red-white deck plays basics for
  this reason), and a fetchland under the effect could still be cracked.
- The AI's stax scorer valued the lock by a coefficient × nonbasic count
  with a cap, multiplied by a turn-decay table that reaches ZERO on turn 5,
  and the whole overlay was silenced whenever the hand held an instant.
  Boros Ponza vs Domain Zoo s50000: Blood Moon drawn on turn 7 against a
  five-colour deck scored −0.2 and was cast on turn 10, after the game.

Class: the forced-basic-type family (typed `stax_forced_basic` — Blood
Moon, Magus of the Moon, Harbinger of the Seas, …); the layer-4 effect is
the mechanic every land-type-setting static reuses.  Card names below
are fixture carriers only.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.mana_payment import ManaPayment
from ai.ev_evaluator import EVSnapshot


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
    else:
        game.players[controller].hand.append(c)
    return c


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def _colors(game, idx, land):
    return set(ManaPayment.effective_produces_mana(game, idx, land))


# ── Engine: a continuous, symmetric, layer-4 effect ─────────────────


def test_forced_basic_type_applies_to_every_nonbasic_land_on_both_battlefields(card_db):
    game = _game(card_db)
    theirs = _put(game, card_db, "Breeding Pool", 1, "battlefield")     # G/U
    mine = _put(game, card_db, "Sacred Foundry", 0, "battlefield")      # R/W
    basic = _put(game, card_db, "Plains", 1, "battlefield")
    assert _colors(game, 1, theirs) == {"G", "U"}
    moon = _put(game, card_db, "Blood Moon", 0, "battlefield")
    game.continuous_effects.recalculate(game)
    assert _colors(game, 1, theirs) == {"R"}, "opponent's nonbasic is a Mountain"
    assert _colors(game, 0, mine) == {"R"}, "the caster's own nonbasic too"
    assert _colors(game, 1, basic) == {"W"}, "basics are untouched"
    # The effect ends with its source (CR 611.2a).
    game.players[0].battlefield.remove(moon)
    moon.zone = "graveyard"
    game.continuous_effects.recalculate(game)
    assert _colors(game, 1, theirs) == {"G", "U"}


def test_a_nonbasic_land_entering_after_the_source_is_affected(card_db):
    game = _game(card_db)
    _put(game, card_db, "Blood Moon", 0, "battlefield")
    later = _put(game, card_db, "Steam Vents", 1, "battlefield")
    later.tapped = False
    game.continuous_effects.recalculate(game)
    assert _colors(game, 1, later) == {"R"}
    # The untapped-lands colour census reads the same effect.
    assert game.players[1].available_mana_colors()["U"] == 0
    assert game.players[1].available_mana_colors()["R"] == 1


def test_a_fetchland_under_the_effect_has_no_fetch_ability(card_db):
    game = _game(card_db)
    _put(game, card_db, "Blood Moon", 0, "battlefield")
    _put(game, card_db, "Forest", 1, "library")
    fetch = _put(game, card_db, "Misty Rainforest", 1, "battlefield")
    game.continuous_effects.recalculate(game)
    from engine.land_manager import LandManager
    LandManager.crack_fetchland(game, 1, fetch)
    assert fetch in game.players[1].battlefield, "no fetch ability: it stays"
    assert len(game.players[1].library) == 1


def test_forced_basic_family_resolves_through_the_layer_not_a_card_name_handler(card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    assert not EFFECT_REGISTRY.has_handler("Blood Moon", EffectTiming.ETB)
    # A second member of the family gets the same effect for free.
    game = _game(card_db)
    _put(game, card_db, "Magus of the Moon", 0, "battlefield")
    land = _put(game, card_db, "Hallowed Fountain", 1, "battlefield")
    game.continuous_effects.recalculate(game)
    assert _colors(game, 1, land) == {"R"}


# ── AI: the lock is worth what it makes uncastable ──────────────────


def _pool(game, card_db, idx, decklist, hand=()):
    for name, n in decklist.items():
        for _ in range(n):
            _put(game, card_db, name, idx, "library")
    for name in hand:
        _put(game, card_db, name, idx, "hand")


def _lock_ev(card_db, game, snap):
    from ai.stax_ev import stax_lock_ev
    return stax_lock_ev(card_db.get_card("Blood Moon"),
                        game.players[0], game.players[1], snap)


def test_lock_value_counts_the_opponents_cards_it_makes_uncastable(card_db):
    game = _game(card_db)
    _pool(game, card_db, 0, {"Mountain": 20, "Lightning Bolt": 4})
    _pool(game, card_db, 1, {"Flooded Strand": 4, "Hallowed Fountain": 4,
                             "Counterspell": 4, "Supreme Verdict": 4,
                             "Ragavan, Nimble Pilferer": 4})
    snap = EVSnapshot(my_life=20, opp_life=20, my_mana=3, turn_number=3)
    ev = _lock_ev(card_db, game, snap)
    assert ev > 0
    # A one-colour opponent on basics loses nothing.
    game2 = _game(card_db)
    _pool(game2, card_db, 0, {"Mountain": 20})
    _pool(game2, card_db, 1, {"Mountain": 20, "Lightning Bolt": 4,
                              "Monastery Swiftspear": 4})
    assert _lock_ev(card_db, game2, snap) == 0.0


def test_lock_value_does_not_expire_on_a_turn_number_while_dead_cards_remain(card_db):
    game = _game(card_db)
    _pool(game, card_db, 0, {"Mountain": 20})
    _pool(game, card_db, 1, {"Breeding Pool": 4, "Steam Vents": 4,
                             "Scion of Draco": 4, "Psychic Frog": 4,
                             "Territorial Kavu": 4})
    early = EVSnapshot(my_life=20, opp_life=20, my_mana=3, turn_number=3)
    late = EVSnapshot(my_life=20, opp_life=20, my_mana=5, turn_number=7)
    assert _lock_ev(card_db, game, late) > 0
    assert _lock_ev(card_db, game, early) > 0


def test_a_colour_the_opponent_can_still_make_with_basics_is_not_dead(card_db):
    game = _game(card_db)
    _pool(game, card_db, 0, {"Mountain": 20})
    # Blue is dead (no basic Island anywhere); white is not (a Plains
    # sits on the battlefield).
    _put(game, card_db, "Plains", 1, "battlefield")
    _pool(game, card_db, 1, {"Hallowed Fountain": 6, "Counterspell": 4,
                             "Path to Exile": 4})
    snap = EVSnapshot(my_life=20, opp_life=20, my_mana=3, turn_number=3)
    ev_mixed = _lock_ev(card_db, game, snap)
    game2 = _game(card_db)
    _pool(game2, card_db, 0, {"Mountain": 20})
    _put(game2, card_db, "Plains", 1, "battlefield")
    _pool(game2, card_db, 1, {"Hallowed Fountain": 6, "Path to Exile": 8})
    assert _lock_ev(card_db, game2, snap) == 0.0
    assert ev_mixed > 0


def test_the_caster_pays_for_its_own_dead_cards(card_db):
    game = _game(card_db)
    # Caster: only nonbasic duals, white spells with no basic Plains → dead.
    _pool(game, card_db, 0, {"Sacred Foundry": 8, "Path to Exile": 8,
                             "Lightning Bolt": 4})
    _pool(game, card_db, 1, {"Breeding Pool": 4, "Counterspell": 4})
    snap = EVSnapshot(my_life=20, opp_life=20, my_mana=3, turn_number=3)
    ev_self_hurt = _lock_ev(card_db, game, snap)
    game2 = _game(card_db)
    _pool(game2, card_db, 0, {"Plains": 8, "Path to Exile": 8,
                              "Lightning Bolt": 4})
    _pool(game2, card_db, 1, {"Breeding Pool": 4, "Counterspell": 4})
    ev_safe = _lock_ev(card_db, game2, snap)
    assert ev_safe > ev_self_hurt


def test_a_second_land_type_lock_is_worth_nothing_while_one_is_in_play(card_db):
    game = _game(card_db)
    _pool(game, card_db, 0, {"Mountain": 20})
    _pool(game, card_db, 1, {"Breeding Pool": 4, "Steam Vents": 4,
                             "Scion of Draco": 4, "Psychic Frog": 4})
    snap = EVSnapshot(my_life=20, opp_life=20, my_mana=3, turn_number=3)
    assert _lock_ev(card_db, game, snap) > 0
    _put(game, card_db, "Blood Moon", 0, "battlefield")
    assert _lock_ev(card_db, game, snap) == 0.0


def test_a_lock_permanent_carries_a_this_turn_signal_and_is_not_deferred(card_db):
    """A lock restricts the opponent's NEXT turn, so casting it now and
    casting it a turn later are different states; the deferral filter
    must not treat it as freely postponable."""
    from ai.ev_evaluator import _enumerate_this_turn_signals, snapshot_from_game
    game = _game(card_db)
    for _ in range(3):
        _put(game, card_db, "Mountain", 0, "battlefield")
    moon = _put(game, card_db, "Blood Moon", 0, "hand")
    snap = snapshot_from_game(game, 0)
    assert _enumerate_this_turn_signals(moon, snap, game, 0, "midrange")


def test_stax_overlay_is_priced_by_the_holdback_penalty_not_silenced(card_db, monkeypatch):
    """Tapping out for a lock piece forfeits held responses; that cost
    is the signed holdback penalty already in the score.  Silencing the
    overlay on top of it charged the tap-out twice and hid the lock's
    value behind any instant in hand."""
    from ai.ev_player import EVPlayer
    from ai import stax_ev as stax_mod
    from ai.ev_evaluator import snapshot_from_game
    game = _game(card_db)
    for _ in range(4):
        _put(game, card_db, "Mountain", 0, "battlefield")
    moon = _put(game, card_db, "Blood Moon", 0, "hand")
    _put(game, card_db, "Lightning Bolt", 0, "hand")           # held instant
    _pool(game, card_db, 0, {"Mountain": 10, "Lightning Bolt": 4})
    _pool(game, card_db, 1, {"Breeding Pool": 4, "Steam Vents": 4,
                             "Scion of Draco": 4, "Psychic Frog": 4})
    _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")
    for _ in range(3):
        _put(game, card_db, "Steam Vents", 1, "battlefield")
    player = EVPlayer(player_idx=0, deck_name="Boros Ponza",
                      rng=random.Random(0))
    monkeypatch.setattr(EVPlayer, "_holdback_penalty",
                        lambda self, *a, **kw: -1.0)
    snap = snapshot_from_game(game, 0)
    me, opp = game.players[0], game.players[1]
    bonus = stax_mod.stax_lock_ev(moon.template, me, opp, snap)
    assert bonus > 0
    with_overlay = player._score_spell(moon, snap, game, me, opp)
    monkeypatch.setattr(stax_mod, "stax_lock_ev", lambda *a, **kw: 0.0)
    without = player._score_spell(moon, snap, game, me, opp)
    assert with_overlay - without == pytest.approx(bonus, abs=0.05)
