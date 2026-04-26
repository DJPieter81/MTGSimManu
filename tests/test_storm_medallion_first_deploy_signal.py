"""Storm vs Affinity s=60000 reproduction — first deploy of a
combo-archetype cost reducer must emit a same-turn signal.

Diagnosis (2026-04-26): Ruby Medallion's oracle is a STATIC ability
("Red spells you cast cost {1} less to cast.") with no `whenever`
or `at the beginning of` trigger word, so the existing
`recurring_engine_trigger` signal (Phase 9b, ai/ev_evaluator.py:786)
does NOT fire for it.  The existing `combo_continuation` signal
(line 720) only fires when `storm_count > 0` OR a reducer is
ALREADY on the battlefield — meaning the FIRST deploy of the
engine never registers a same-turn signal.

Net effect: `_enumerate_this_turn_signals` returns `[]` for a
freshly-drawn Ruby Medallion at storm=0, the deferral gate at
`ev_player.py::decide_main_phase` filters it out as "no this-turn
value", and the AI passes the turn or casts a lower-value cantrip
instead.

In Storm vs Affinity s=60000, the trace shows Medallion scoring
+9.9 EV at T3 with `<--` arrow — but the AI cast Wrenn's Resolve
(−3.6 EV) because Medallion got filtered.  Storm field N=150
diagnostic showed Ruby Medallion never deploys in 29% of games
despite being drawn — same shape as the Wish-deferral bug fixed
in PR #192.

Fix surface: extend `_enumerate_this_turn_signals` with a new
signal for cost-reducer cards in combo/storm archetypes.  Casting
NOW puts the engine online a turn earlier; casting NEXT TURN
delays the chain by a full turn.  That IS a same-turn signal — the
deck commits to chaining around the reducer at reduced cost.

Generic by construction:
- Detection is `'cost_reducer' in tags` AND `archetype in
  ('storm', 'combo')`.  No card-name hardcoding.
- Same mechanism would credit any future combo-archetype cost
  reducer with the `cost_reducer` tag.

In today's metagame the only deck affected is Ruby Storm (Ruby
Medallion ×4, March of Reckless Joy ×1).  Other tagged cards
(Frogmite, Boseiju, Leyline Binding, Scion of Draco) live in
non-combo archetypes (`aggro`, `ramp`, `control`) and are
correctly excluded by the archetype gate.

Pre-fix:
- `_enumerate_this_turn_signals(medallion, archetype="storm")` → []
- `decide_main_phase` filters Medallion out, casts a cantrip or passes.
Post-fix:
- Signal list includes a `cost_reducer_combo_engine` entry.
- `decide_main_phase` returns ('cast_spell', 'Ruby Medallion', ...).
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


def _build_storm_t2_game(card_db, mountains=2):
    """Storm side, T2 main, `mountains` Mountains on battlefield,
    Ruby Medallion in hand.  Opp on 20 life with a vanilla blocker
    so combat isn't interfering."""
    game = GameState(rng=random.Random(0))
    for _ in range(mountains):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    medallion = _add(game, card_db, "Ruby Medallion", controller=0,
                     zone="hand")
    _add(game, card_db, "Guide of Souls", controller=1,
         zone="battlefield")
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 2
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = 0
    game._global_storm_count = 0
    game.players[0].life = 20
    game.players[1].life = 20
    return game, medallion


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


class TestMedallionFirstDeploySignal:
    """A cost-reducer card in a combo/storm archetype must emit a
    same-turn signal so the deferral gate does not filter the
    engine deploy out.  Sister-fix of the Wish tutor signal in
    PR #192."""

    def test_medallion_emits_a_this_turn_signal(self, card_db):
        """Pre-fix: Ruby Medallion has only a static ability, so
        signals #1-#16 all miss it; the signal list is empty.  This
        is the diagnostic predicate — the deferral gate filters
        empty-signal plays out before EV is even compared."""
        game, medallion = _build_storm_t2_game(card_db)
        sig = _signals(game, medallion, archetype="storm")
        assert sig, (
            f"Ruby Medallion (Storm archetype, T2, no reducer on "
            f"board yet) emitted no this-turn signals; the "
            f"deferral gate will filter it out of "
            f"`decide_main_phase` before EV is compared.  Add a "
            f"`cost_reducer_combo_engine` signal in "
            f"`_enumerate_this_turn_signals` for any "
            f"`cost_reducer`-tagged card cast in a "
            f"`('storm', 'combo')` archetype.  Symmetric with the "
            f"Wish-pattern fix in PR #192."
        )

    def test_medallion_signal_combo_archetype(self, card_db):
        """Same predicate, archetype='combo' (not 'storm') — both
        archetypes must emit the signal because both build their
        plan around chaining at the discounted cost."""
        game, medallion = _build_storm_t2_game(card_db)
        sig = _signals(game, medallion, archetype="combo")
        assert sig, (
            f"Ruby Medallion in 'combo' archetype emitted no "
            f"signals; the gate must include both 'storm' and "
            f"'combo' archetypes."
        )

    def test_medallion_no_signal_in_non_combo_archetype(self, card_db):
        """Regression anchor: in a non-combo archetype (e.g.
        midrange), a cost reducer is NOT an engine piece and should
        NOT emit the signal.  Only fire the signal when the deck
        commits to chaining around the reducer."""
        game, medallion = _build_storm_t2_game(card_db)
        sig = _signals(game, medallion, archetype="midrange")
        assert 'cost_reducer_combo_engine' not in sig, (
            f"Cost reducer must only emit the combo-engine signal "
            f"in 'storm' or 'combo' archetypes.  Other archetypes "
            f"(midrange, aggro, control) do not chain rituals so "
            f"the signal would be a false positive."
        )

    def test_medallion_chosen_at_t2_when_only_play(self, card_db):
        """End-to-end: Storm T2 with 2 mountains and only Ruby
        Medallion in hand.  `decide_main_phase` must return
        Medallion — not None (pass the turn).

        Pre-fix: the deferral gate filters Medallion out because it
        has no this-turn signal.  Post-fix: Medallion clears the
        gate and is cast.
        """
        game, medallion = _build_storm_t2_game(card_db)
        chosen = _decide(game)
        assert chosen is not None and chosen[1] == "Ruby Medallion", (
            f"`decide_main_phase` returned {chosen!r}; expected "
            f"('cast_spell', 'Ruby Medallion', ...).  At T2 with 2 "
            f"mountains and Medallion as the only spell, the AI "
            f"must deploy the engine.  If Medallion is filtered "
            f"out by the deferral gate, the AI passes T2 and "
            f"loses a turn of cost reduction on the actual combo "
            f"turn — observed in 29% of Storm field games."
        )

    def test_medallion_chosen_over_cantrip_at_t2(self, card_db):
        """Sequencing: hand has Ruby Medallion + Reckless Impulse.
        At T2 with 2 mountains both are castable.  Real-world
        Storm philosophy is to deploy the engine first (T2
        Medallion, T3 chain).  Medallion's high score (+9.9 in the
        s=60000 trace) should win once it clears the deferral
        gate."""
        game, medallion = _build_storm_t2_game(card_db)
        # Add a Reckless Impulse to compete for the cast slot.
        _add(game, card_db, "Reckless Impulse", controller=0,
             zone="hand")
        chosen = _decide(game)
        assert chosen is not None and chosen[1] == "Ruby Medallion", (
            f"`decide_main_phase` returned {chosen!r}; with "
            f"Medallion + Reckless Impulse both castable at T2, "
            f"Medallion must out-score the cantrip — its EV in "
            f"the s=60000 trace was +9.9 vs the cantrip's −3.6.  "
            f"If Medallion is being filtered (no signal), the "
            f"cantrip wins by default."
        )
