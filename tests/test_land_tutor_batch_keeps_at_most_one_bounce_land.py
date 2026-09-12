"""A mass land search takes at most one bounce land per batch.

Every bounce land's entry trigger returns a land its controller controls
(CR 603: each trigger resolves even after its source has left), so two
bounce lands fetched together return each other — or two co-entrants —
and the batch nets one land fewer per extra bounce land. An untapped-entry
watcher changes the mana the batch makes, not the lands it keeps.
Observed (Domain Zoo vs Amulet Titan s50000, post iteration 1): Scapeshift
for seven with Amulet of Vigor in play fetched Simic Growth Chamber and
Gruul Turf together; each returned the other and the base went 7 → 5.

Rules pinned (card names are fixture carriers):

1. The delivery seam sees the picks already made in the same batch, and a
   bounce land ranks last once the batch already holds one — with or
   without a watcher.
2. The engine's land-sacrifice tutor, resolved through the AI callbacks,
   fetches exactly one bounce land from a library that offers two.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 8
    game.players[0].deck_name = "Amulet Titan"
    game.players[1].deck_name = "Domain Zoo"
    for _ in range(5):
        _add(game, card_db, "Forest", 0, "battlefield")
    _add(game, card_db, "Amulet of Vigor", 0, "battlefield")
    return game


def test_a_bounce_land_ranks_last_once_the_batch_already_holds_one(card_db):
    from ai.activation_ev import choose_tutor_delivery
    game = _game(card_db)
    chamber = _add(game, card_db, "Simic Growth Chamber", 0, "library")
    turf = _add(game, card_db, "Gruul Turf", 0, "library")
    forest = _add(game, card_db, "Forest", 0, "library")
    tutor = _add(game, card_db, "Scapeshift", 0, "hand")
    # Empty batch, watcher in play: the bounce land is the declared top pick.
    tutor._tutor_batch = []
    assert choose_tutor_delivery(game, 0, [forest, turf], source=tutor) is turf
    # One bounce land already in the batch: the next bounce land ranks last.
    tutor._tutor_batch = [chamber]
    assert choose_tutor_delivery(game, 0, [forest, turf], source=tutor) is forest


def test_the_land_sacrifice_tutor_fetches_at_most_one_bounce_land_per_batch(card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    from engine.game_runner import AICallbacks
    game = _game(card_db)
    game.callbacks = AICallbacks()
    me = game.players[0]
    _add(game, card_db, "Simic Growth Chamber", 0, "library")
    _add(game, card_db, "Gruul Turf", 0, "library")
    for _ in range(3):
        _add(game, card_db, "Urza's Saga", 0, "library")
    for _ in range(4):
        _add(game, card_db, "Forest", 0, "library")
    tutor = _add(game, card_db, "Scapeshift", 0, "hand")
    lands_before = sum(1 for c in me.battlefield if c.template.is_land)   # 5
    EFFECT_REGISTRY.execute("Scapeshift", EffectTiming.SPELL_RESOLVE, game,
                            tutor, 0, targets=None, item=None)
    lands_after = [c for c in me.battlefield if c.template.is_land]
    bounce = [c for c in lands_after if c.template.etb_return_land]
    returned = [c for c in me.hand if c.template.is_land]
    assert len(bounce) + len(returned) <= 1 or len(bounce) == 1, (
        f"two bounce lands were fetched in one batch: on battlefield "
        f"{[c.name for c in lands_after]}, returned {[c.name for c in returned]}")
    assert len(lands_after) == lands_before - 1, (
        f"a batch with one bounce land nets exactly one land fewer; got "
        f"{len(lands_after)} from {lands_before}")
