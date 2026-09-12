"""A land-sacrifice tutor fetches what the plan values, and an ability-land
in the library is a payoff it can reach.

Two halves of one defect (Domain Zoo vs Amulet Titan, s50000, traced):
Amulet held Scapeshift for four turns on eight lands with Amulet of Vigor
in play, every main phase scoring it at the patience sentinel, because the
payoff gate (`_overlay_land_sacrifice_fizzle`) recognised only a payoff
ROLE card in hand — and this list's Scapeshift payoff is the lands it
fetches (Urza's Saga's Constructs and tutor). Had it fired, the engine's
own fetch order (bounce-with-watcher, then duals, then basics) would never
have chosen a Saga: the engine ranked the library by a heuristic of its own
instead of asking the AI, and the AI's tutor delivery valued every land at
zero.

Rules pinned (no card names in code; the names below are fixture carriers):

1. A land in the library whose ability makes tokens or deals damage (typed
   `has_token_effect` / `deals_targeted_damage`) is a reachable payoff for
   a land-sacrifice tutor — the gate does not clamp it for lack of a hand
   payoff. A library of mana lands alone still clamps (the 2026-08-26
   blind-ramp pins stay green).
2. Tutor delivery ranks lands by the AI's ONE land valuation — the
   land-drop scorer (`EVPlayer._score_land`: Tron-set completion, the
   gameplan's declared `land_priorities`, colour needs, landfall) — and
   ranks a bounce land with no untapped-entry watcher last (its entry
   bounces a co-entrant — the retention rule the gate already encodes).
3. The engine's land-sacrifice tutor asks the delivery seam for every pick
   instead of ranking the library itself; a caller that always names the
   ability-land gets every copy.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from ai.scoring_constants import PATIENCE_GATE_REJECT_SENTINEL


def _add(game, card_db, name, controller, zone):
    t = card_db.get_card(name)
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


def _game(card_db, n_forests, watcher, library_payoff_lands):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 8
    me, opp = game.players[0], game.players[1]
    me.deck_name = "Amulet Titan"
    opp.deck_name = "Domain Zoo"
    me.life = 8
    opp.life = 18
    for _ in range(n_forests):
        _add(game, card_db, "Forest", 0, "battlefield")
    if watcher:
        _add(game, card_db, "Amulet of Vigor", 0, "battlefield")
    tutor = _add(game, card_db, "Scapeshift", 0, "hand")
    for _ in range(8):
        _add(game, card_db, "Forest", 0, "library")
    for _ in range(2):
        _add(game, card_db, "Gruul Turf", 0, "library")
    for _ in range(library_payoff_lands):
        _add(game, card_db, "Urza's Saga", 0, "library")
    _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    for _ in range(3):
        _add(game, card_db, "Mountain", 1, "battlefield")
    return game, tutor


def _score(game, tutor):
    ai = EVPlayer(player_idx=0, deck_name="Amulet Titan", rng=random.Random(0))
    ai._init_deck_knowledge(game)
    if ai.goal_engine:
        ai.goal_engine.check_transition(game, 0)
    snap = snapshot_from_game(game, 0)
    return ai._score_spell(tutor, snap, game, game.players[0], game.players[1])


# ─── 1. An ability-land in the library is a reachable payoff ─────────


def test_an_ability_land_in_the_library_is_a_reachable_payoff_for_a_land_tutor(card_db):
    game, tutor = _game(card_db, n_forests=8, watcher=True, library_payoff_lands=4)
    ev = _score(game, tutor)
    assert ev > PATIENCE_GATE_REJECT_SENTINEL, (
        f"tutor clamped at {ev:.2f} with a watcher in play and four "
        f"token-making lands to fetch — the fetched lands ARE the payoff")


def test_a_library_of_mana_lands_alone_still_clamps_the_tutor(card_db):
    game, tutor = _game(card_db, n_forests=8, watcher=True, library_payoff_lands=0)
    ev = _score(game, tutor)
    assert ev <= PATIENCE_GATE_REJECT_SENTINEL, (
        f"tutor scored {ev:.2f} with nothing but mana lands to fetch and no "
        f"payoff in hand — blind ramp (2026-08-26) must stay clamped")


# ─── 2. Delivery ranks lands by the plan ─────────────────────────────


def test_land_tutor_delivery_is_the_land_drop_valuation_with_bounce_last_without_a_watcher(card_db):
    from ai.activation_ev import choose_tutor_delivery
    game, _ = _game(card_db, n_forests=6, watcher=False, library_payoff_lands=1)
    me = game.players[0]
    forest = next(c for c in me.library if c.name == "Forest")
    saga = next(c for c in me.library if c.name == "Urza's Saga")
    turf = next(c for c in me.library if c.name == "Gruul Turf")
    # No watcher: the bounce land's entry bounces a co-entrant — last,
    # whatever the drop scorer says. Between the rest, the drop scorer
    # decides (Amulet Titan's data ranks Urza's Saga above Forest).
    assert choose_tutor_delivery(game, 0, [forest, turf, saga]) is saga
    assert choose_tutor_delivery(game, 0, [forest, turf]) is forest


def test_land_tutor_delivery_completes_a_declared_land_set(card_db):
    """The delivery is the land-drop valuation, so a tutor in a deck that
    assembles a land set fetches the piece that completes it, not the
    land its priority data lists highest for ordinary drops."""
    from ai.activation_ev import choose_tutor_delivery
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Eldrazi Tron"
    game.players[1].deck_name = "Amulet Titan"
    _add(game, card_db, "Urza's Power Plant", 0, "battlefield")
    temple = _add(game, card_db, "Eldrazi Temple", 0, "library")   # declared 10.0
    tower = _add(game, card_db, "Urza's Tower", 0, "library")      # completes 2 of 3
    plant = _add(game, card_db, "Urza's Power Plant", 0, "library")
    assert choose_tutor_delivery(game, 0, [temple, plant, tower]) is tower


# ─── 3. The engine asks the seam for every pick ──────────────────────


def test_the_land_sacrifice_tutor_asks_the_delivery_seam_for_every_pick(card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    game, tutor = _game(card_db, n_forests=4, watcher=False, library_payoff_lands=4)
    me = game.players[0]
    picks = []
    base = game.callbacks

    def _choose(g, player_idx, source, eligible):
        picks.append([c.name for c in eligible])
        sagas = [c for c in eligible if c.name == "Urza's Saga"]
        return sagas[0] if sagas else eligible[0]

    base.choose_tutor_target = _choose
    EFFECT_REGISTRY.execute("Scapeshift", EffectTiming.SPELL_RESOLVE, game,
                            tutor, 0, targets=None, item=None)
    fetched = [c for c in me.battlefield if c.template.is_land]
    assert len(picks) == 4, f"one seam call per fetched land, got {len(picks)}"
    assert sum(1 for c in fetched if c.name == "Urza's Saga") == 4, (
        f"the seam named the ability-land every time; fetched {[c.name for c in fetched]}")
