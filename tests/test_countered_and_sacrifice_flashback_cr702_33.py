"""Flashback (CR 702.33) under two engine paths that ignored it.

  702.33a: "… you may cast the card from your graveyard by paying [cost]
  rather than paying its mana cost. If the flashback cost is paid, …
  If it would leave the stack, exile it instead of putting it anywhere
  else."

Two defects, both observed in Izzet Prowess vs Azorius Control (WST) /
Eldrazi Tron replays (2026-09-08), both on a spell whose printed flashback
cost is a land sacrifice with no mana component:

1. **A spell countered on cast went to the graveyard, not exile.**
   `cast_spell`'s Chalice-family lock block appended the card straight to
   the graveyard, bypassing `_move_countered_stack_item` (the one owner of
   the "would leave the stack" replacement).  The same Lava Dart was then
   flashed back THREE times into one Chalice, sacrificing three lands.
   Class: 156 flashback cards, every counter.

2. **A sacrifice-only flashback cost was charged the printed mana cost
   too.**  `flashback_cost is None` was read as "granted flashback, pay the
   printed cost" — the Past in Flames case — but a printed "Flashback—
   Sacrifice a Mountain" also parses to None (there is no mana to record),
   so the caster tapped a land AND sacrificed one.  Class: every printed
   flashback whose cost has no mana component.

Card names are fixture carriers for the shapes: a sacrifice-only printed
flashback spell of mana value 1; a charge-counter lock; a printed flashback
with a mana cost (guard).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if "flashback" in tmpl.tags:
        card.has_flashback = True   # innate flashback, as setup_game flags it
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "graveyard":
        game.players[controller].graveyard.append(card)
    return card


def _game(card_db, *, mountains=2, chalice_x=None):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(mountains):
        _add(game, card_db, "Mountain", 0, "battlefield")
    dart = _add(game, card_db, "Lava Dart", 0, "graveyard")   # Flashback—Sacrifice a Mountain
    _add(game, card_db, "Grizzly Bears", 1, "battlefield")     # a legal target
    lock = None
    if chalice_x is not None:
        lock = _add(game, card_db, "Chalice of the Void", 1, "battlefield")
        lock.other_counters["charge"] = chalice_x
    return game, dart, lock


def test_a_sacrifice_only_flashback_cost_taps_no_mana(card_db):
    game, dart, _ = _game(card_db, mountains=2)
    game.verbose = True
    assert CastManager.can_cast(game, 0, dart)
    assert CastManager.cast_spell(game, 0, dart, [])
    lands = [c for c in game.players[0].battlefield if c.template.is_land]
    assert len(lands) == 1, "exactly one Mountain is sacrificed for the flashback cost"
    assert not any(getattr(c, "tapped", False) for c in lands), (
        "the remaining Mountain stays untapped — a sacrifice-only flashback "
        "cost has no mana component")
    assert not any("paying for Lava Dart" in line for line in game.log), (
        "no land is tapped for a cost that has no mana component "
        f"(log: {[l for l in game.log if 'Lava Dart' in l]})")


def test_a_sacrifice_only_flashback_is_castable_with_no_untapped_mana(card_db):
    """Both Mountains tapped: no mana anywhere, but a Mountain to sacrifice.
    The spell is castable — the only cost is the sacrifice — and casting
    it pays exactly that.  (`can_cast` demanded the printed {R} the payment
    path never charges, so a tapped-out caster was refused the flashback.)"""
    game, dart, _ = _game(card_db, mountains=2)
    for land in [c for c in game.players[0].battlefield if c.template.is_land]:
        land.tapped = True
    assert CastManager.can_cast(game, 0, dart)
    assert CastManager.cast_spell(game, 0, dart, [])
    assert len([c for c in game.players[0].battlefield if c.template.is_land]) == 1


def test_a_countered_flashback_spell_is_exiled_not_returned_to_graveyard(card_db):
    game, dart, lock = _game(card_db, mountains=2, chalice_x=1)   # Lava Dart is MV 1
    assert CastManager.cast_spell(game, 0, dart, [])
    assert dart in game.players[0].exile, (
        "CR 702.33a: a flashbacked spell that leaves the stack — countered "
        "included — is exiled")
    assert dart not in game.players[0].graveyard


def test_a_printed_flashback_with_a_mana_cost_still_pays_it(card_db):
    """Guard: 'Flashback {G}' taps a green source."""
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    forest = _add(game, card_db, "Forest", 0, "battlefield")
    grudge = _add(game, card_db, "Ancient Grudge", 0, "graveyard")   # {1}{R}, Flashback {G}
    artifact = _add(game, card_db, "Chalice of the Void", 1, "battlefield")  # a target
    assert CastManager.can_cast(game, 0, grudge)
    assert CastManager.cast_spell(game, 0, grudge, [artifact.instance_id])
    assert getattr(forest, "tapped", False), "the Forest pays the printed flashback cost"
