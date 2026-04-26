"""Storm Past in Flames must emit a same-turn signal when graveyard
contains flashback-able fuel.

Diagnosis (2026-04-26): Past in Flames' oracle is *"Each instant
and sorcery card in your graveyard gains flashback until end of
turn."*  This is a static/global effect, not a `whenever` trigger,
not an ETB, not card draw (the literal word "draw" is absent from
the oracle).  Signals #1-#16 in `_enumerate_this_turn_signals` all
miss it.  Signal #10 `combo_continuation` only fires when storm > 0
OR a reducer is on board — meaning when PiF is the FIRST spell of
the chain (T4 with rituals in graveyard, no Medallion deployed
yet, storm count zero), there is no signal.

Net effect: PiF returns an empty signal list, the deferral gate
filters it out, and the AI casts a lower-EV cantrip or passes —
even when the graveyard already contains 3+ rituals to flashback
into for free storm count.

This is the third deferral-gate sister bug after PR #192 (Wish
tutor) and PR #194 (Ruby Medallion cost reducer).  Same exact
shape: high EV play, empty signal list, filtered by deferral gate,
chain stalls.

Fix: extend `_enumerate_this_turn_signals` with a signal for cards
that:
1. Have `'flashback'` AND `'combo'` tags (the gameplan-declared
   pattern for graveyard combo enablers)
2. Are cast in a `'storm'` or `'combo'` archetype
3. Have at least one instant/sorcery card in the controller's
   graveyard for the flashback effect to act upon

Casting NOW means we can flashback graveyard fuel THIS turn for
storm count + extra mana.  Casting NEXT TURN means we lose a turn
of chain progress — a real same-turn signal.

Generic by construction:
- `'flashback' in tags AND 'combo' in tags` is the existing
  gameplan tagging convention; no card name hardcoded.
- Same mechanism credits any future flashback-combo card whose
  oracle requires graveyard contents (e.g. Mizzix's Mastery,
  Yawgmoth's Will-style cards).

Today this fix benefits Ruby Storm only (Past in Flames ×3 main +
×1 SB).  Other flashback-tagged cards like Faithful Mending live
in non-storm archetypes (Goryo's, control-shells), where the
graveyard fuel is for reanimation not chain-recasting — those
already have their own signals (`card_draw` for Faithful Mending,
`reanimate` for Unburial Rites).

Pre-fix:
- `_enumerate_this_turn_signals(pif, archetype="storm")` → []
- `decide_main_phase` filters PiF out → AI casts cantrip or passes
Post-fix:
- Signal list includes a `flashback_combo_with_gy_fuel` entry
- `decide_main_phase` returns ('cast_spell', 'Past in Flames', ...)
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import _enumerate_this_turn_signals, snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _build_storm_pif_game(card_db, gy_fuel_count=3, mountains=5):
    """Storm side, T4 main, `mountains` Mountains on battlefield,
    Past in Flames in hand, `gy_fuel_count` instant/sorcery cards
    in graveyard for PiF to flashback.  No reducer deployed yet
    (so combo_continuation signal does NOT fire — isolates the new
    PiF signal)."""
    game = GameState(rng=random.Random(0))
    for _ in range(mountains):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    pif = _add(game, card_db, "Past in Flames", controller=0,
               zone="hand")
    # Graveyard fuel: alternating ritual + cantrip
    fuel_cards = ["Pyretic Ritual", "Manamorphose", "Desperate Ritual",
                  "Reckless Impulse", "Wrenn's Resolve"]
    for i in range(gy_fuel_count):
        _add(game, card_db, fuel_cards[i % len(fuel_cards)],
             controller=0, zone="graveyard")
    _add(game, card_db, "Guide of Souls", controller=1,
         zone="battlefield")
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = 0
    game._global_storm_count = 0
    game.players[0].life = 14
    game.players[1].life = 18
    return game, pif


def _signals(game, card, archetype="storm"):
    snap = snapshot_from_game(game, 0)
    return _enumerate_this_turn_signals(card, snap, game, 0, archetype)


def _decide(game):
    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    chosen = player.decide_main_phase(game)
    if chosen is None:
        return None
    action, card, targets = chosen
    return (action, card.name, targets)


class TestPiFFlashbackSignal:
    """Past in Flames must emit a same-turn signal when its
    flashback effect has graveyard fuel to act upon, so the
    deferral gate does not filter it out."""

    def test_pif_emits_signal_with_gy_fuel(self, card_db):
        """Pre-fix: PiF returns [] from
        `_enumerate_this_turn_signals` because its oracle is a
        static effect with no `whenever`, no ETB, no card draw, no
        haste — and at storm=0 with no reducer on board, the
        combo_continuation signal also doesn't fire."""
        game, pif = _build_storm_pif_game(card_db, gy_fuel_count=3)
        sig = _signals(game, pif, archetype="storm")
        assert sig, (
            f"Past in Flames in storm archetype with 3 instant/"
            f"sorcery cards in graveyard emitted no signals.  Add "
            f"a `flashback_combo_with_gy_fuel` signal in "
            f"`_enumerate_this_turn_signals` for any card with "
            f"'flashback' AND 'combo' tags in 'storm'/'combo' "
            f"archetypes when the graveyard contains ≥1 instant/"
            f"sorcery card.  Sister-fix to PR #192 (Wish) and "
            f"PR #194 (Medallion) — same deferral-gate pattern."
        )

    def test_pif_no_signal_in_empty_graveyard(self, card_db):
        """Regression anchor: with NO graveyard fuel, casting PiF
        is a 5-mana spell that does literally nothing (the granted
        flashback has no targets).  No same-turn signal should
        fire — keep the deferral gate filtering it out."""
        game, pif = _build_storm_pif_game(card_db, gy_fuel_count=0)
        sig = _signals(game, pif, archetype="storm")
        assert 'flashback_combo_with_gy_fuel' not in sig, (
            f"PiF emitted flashback signal with empty graveyard.  "
            f"The signal must be gated on graveyard contents — "
            f"otherwise we'd cast PiF for no effect.  Got "
            f"signals: {sig}"
        )

    def test_pif_no_signal_in_non_combo_archetype(self, card_db):
        """Regression anchor: in a non-combo archetype, granting
        flashback to graveyard cards is a value play but not a
        same-turn combo signal.  The signal is gated on 'storm' /
        'combo' archetypes only."""
        game, pif = _build_storm_pif_game(card_db, gy_fuel_count=3)
        sig = _signals(game, pif, archetype="midrange")
        assert 'flashback_combo_with_gy_fuel' not in sig, (
            f"PiF emitted flashback-combo signal in 'midrange' "
            f"archetype.  Signal must only fire for 'storm' / "
            f"'combo' archetypes (the decks committed to chaining "
            f"around graveyard fuel)."
        )

    def test_pif_chosen_when_only_play_with_gy_fuel(self, card_db):
        """End-to-end: T4, Storm has 5 mana (enough to cast PiF
        for {3}{R}), 3 ritual/cantrip cards in graveyard, PiF the
        only spell in hand.  `decide_main_phase` must return PiF
        — not None (pass).

        Pre-fix: deferral gate filters PiF out → no candidate
        survives → AI passes T4 with full graveyard.  Post-fix:
        PiF emits the signal, clears the gate, gets cast."""
        game, pif = _build_storm_pif_game(card_db, gy_fuel_count=3)
        chosen = _decide(game)
        assert chosen is not None and chosen[1] == "Past in Flames", (
            f"`decide_main_phase` returned {chosen!r}; expected "
            f"('cast_spell', 'Past in Flames', ...).  At T4 with "
            f"5 mana and 3 graveyard fuel cards, PiF is the only "
            f"available chain-restart play.  If filtered out by "
            f"the deferral gate, the AI passes the turn and "
            f"loses a full turn of chain progress."
        )
