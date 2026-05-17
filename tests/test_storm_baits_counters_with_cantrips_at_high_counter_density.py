"""Storm cantrips must remain castable as counter-bait at high opp counter density.

Surfaced from the integration test of #314 + #319 + #321 + #322 + #323 vs
Dimir Midrange (BO1 n=50 s=50000): Storm 38% post-merge vs ~50% pre-session,
a real ~10–12pp regression. Trace of `--trace storm dimir -s 51000` showed
the failure mode:

  T5 Ruby Storm  | life=17  mana=2  hand=5+0L  gy=1
    Hand: ['Reckless Impulse', 'Grapeshot', 'Manamorphose',
           'Past in Flames', 'Reckless Impulse']
    Permanents: ['Ruby Medallion']
    EV scores:
       -15.0  cast_spell: Reckless Impulse  [h=-15.0 la=-15.0 ctr=60%]
       -15.0  cast_spell: Reckless Impulse  [h=-15.0 la=-15.0 ctr=60%]
       -23.3  cast_spell: Grapeshot       [h=-23.3 la=-23.3 ctr=60%]
       -33.3  cast_spell: Manamorphose    [h=-33.3 la=-33.3 ctr=60%]
    >>> PASS (threshold=-5.0)

Storm passed every turn from T5 through T11 with hand growing 5 → 8 cards,
never starting the chain, and lost to Dimir's clock.

# Mechanic the test names

A cheap cantrip (1–2 CMC, draws ≥2 cards on resolution) is the canonical
bait against counter-density: even when countered, it trades 1-for-1
(our cantrip ↔ their counter), and the resolved cantrip yields net card
advantage. Real Storm pilots play through counters by *forcing opp to
spend counters on cheap baits* before the lethal closer is cast. The
EV of "cast the bait" is therefore positive at every counter density
the opp can muster — `pass_threshold` must not exceed it.

Generic by construction: the rule applies to any cantrip in any combo
deck (Manamorphose, Reckless Impulse, Wrenn's Resolve, Consider, Sleight
of Hand, Opt) when the opponent has live counter density. No card
names; detection is via `_is_real_dig` (oracle-text-driven) which
already lives in `ai/ev_evaluator.py`.

# Class size

Every combo deck declaring a cantrip enabler hits this rule:
- Storm (Reckless Impulse, Manamorphose, Wrenn's Resolve, Glimpse the
  Impossible, March of Reckless Joy, Valakut Awakening)
- Living End (cycling cards as bait)
- Goryo's Vengeance (Faithful Mending, Consider)
- Future combo decks with cantrips

Several non-combo decks also benefit (Izzet Prowess cantrips, etc.),
but the rule is most load-bearing for combo where the alternative is
indefinite stalling and clock-loss.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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


def _build_storm_vs_counter_density_game(card_db, counter_density=0.13,
                                          opp_hand_size=5):
    """Storm side: Ruby Medallion in play (cost reducer active), 2
    untapped Mountains, hand of cantrips + ritual + finisher. Opp side:
    drawer Dimir-class hand size + counter density set on the player
    state directly (mirrors `engine/game_runner.py:395-410`'s scan).

    Storm is at storm_count=0 (chain not started) and life>0, with
    library full of chain fuel (so the storm-zero-no-chain hold gate
    does NOT fire on the cantrip directly — the cantrip is not a
    STORM-keyword closer, it's a fuel card).

    The configuration mirrors the seed-51000 T5 trace exactly: 2 mana
    floating, Reckless Impulse + Manamorphose + Grapeshot + Past in
    Flames in hand, Ruby Medallion deployed.
    """
    game = GameState(rng=random.Random(0))
    _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    _add(game, card_db, "Ruby Medallion", controller=0,
         zone="battlefield")

    reckless = _add(game, card_db, "Reckless Impulse", controller=0,
                    zone="hand")
    _add(game, card_db, "Manamorphose", controller=0, zone="hand")
    _add(game, card_db, "Grapeshot", controller=0, zone="hand")
    _add(game, card_db, "Past in Flames", controller=0, zone="hand")
    _add(game, card_db, "Reckless Impulse", controller=0, zone="hand")

    # Library: ample fuel so storm-zero-no-chain hold gate is off-topic;
    # the rule under test is purely about counter-discount on cantrips.
    for _ in range(20):
        _add(game, card_db, "Mountain", controller=0, zone="library")

    # Opp side: Dimir-class with counter density and enough untapped
    # mana to actually CAST a counter. The ev_evaluator path gates the
    # counter probability on `projected.opp_mana >= COUNTER_ESTIMATED_COST`
    # — without enough opp lands the discount is skipped (`can_counter`
    # is False) and the test reduces to a no-disruption scenario,
    # masking the bug.
    for _ in range(3):  # 3 lands ⇒ opp_mana ≥ 2 = COUNTER_ESTIMATED_COST
        _add(game, card_db, "Underground River", controller=1,
             zone="battlefield")
    # Pad opp hand with the kinds of cards Dimir actually holds so
    # `snap.opp_hand_size` matches reality and BHI's prior reflects
    # a Dimir-class disruption density. Mix of 2 Counterspell + filler
    # so `prior(counters/total)` is in the natural Dimir band, not the
    # degenerate 100%-counters case.
    for _ in range(2):
        _add(game, card_db, "Counterspell", controller=1, zone="hand")
    for _ in range(opp_hand_size - 2):
        _add(game, card_db, "Drown in the Loch", controller=1, zone="hand")
    game.players[1].counter_density = counter_density
    game.players[1].removal_density = 0.0
    game.players[1].exile_density = 0.0

    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Dimir Midrange"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 5
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = 0
    game._global_storm_count = 0
    game.players[0].life = 17
    game.players[1].life = 20

    return game, reckless


class TestCantripsScoreAboveThresholdAtHighCounterDensity:
    """A cantrip in hand must remain castable when the opp's counter
    density is in the typical Modern range (0.10 – 0.20). The rule:
    `_score_spell(cantrip, ...) > profile.pass_threshold`."""

    def test_reckless_impulse_castable_at_dimir_counter_density(
            self, card_db):
        """Counter density 0.13 (typical Dimir: 4 Counterspell + 4
        Force of Negation in a 60-card deck) with opp hand=5. Reckless
        Impulse (1R cantrip with Medallion=R, draws 2) must score above
        the Storm pass_threshold."""
        game, reckless = _build_storm_vs_counter_density_game(
            card_db, counter_density=0.13, opp_hand_size=5)
        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        # Mimic live state: BHI initialized from the opp's library
        # (this is what `engine/game_runner.py` does at game start
        # and what `--trace storm dimir` actually exercises).
        player.bhi.initialize_from_game(game)
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(reckless, snap, game, me, opp)

        # M3: pass_threshold field deleted; the M3 gate is
        # `best.ev >= PLAY_VALUE_FLOOR` (-5.0) at `decide_main_phase`.
        # Cantrips must score above the floor under counter-bait
        # pressure — the M3 signed-cost model only deepens the bonus
        # vs the original gate, never raises the bar.
        from ai.scoring_constants import PLAY_VALUE_FLOOR
        threshold = PLAY_VALUE_FLOOR
        assert ev > threshold, (
            f"Reckless Impulse scored EV={ev:.2f} at "
            f"counter_density=0.13 (Dimir-class) — below PLAY_VALUE_FLOOR"
            f"={threshold}. A 1-mana cantrip that draws "
            f"2 cards is the canonical counter-bait: even when "
            f"countered, it forces a 1-for-1 trade and depletes opp's "
            f"counter density for the lethal closer. Refusing to cast "
            f"it strands Storm in indefinite stalling (seed 51000 T5–"
            f"T11 trace shows hand growing 5→8 cards with zero spells "
            f"cast). The fix must prevent the BHI counter discount "
            f"from compounding with combo-modifier holds in a way "
            f"that pushes cheap cantrips below pass_threshold."
        )

    def test_reckless_impulse_castable_at_low_counter_density(
            self, card_db):
        """Regression anchor: at counter_density=0.05 (light counters,
        e.g. one-of in opp deck), Reckless Impulse must of course
        also score above pass_threshold. Anchors that the test
        passes pre-fix and post-fix when density is low."""
        game, reckless = _build_storm_vs_counter_density_game(
            card_db, counter_density=0.05, opp_hand_size=5)
        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        player.bhi.initialize_from_game(game)
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(reckless, snap, game, me, opp)

        # M3: pass_threshold field deleted; the M3 gate is
        # `best.ev >= PLAY_VALUE_FLOOR` (-5.0) at `decide_main_phase`.
        from ai.scoring_constants import PLAY_VALUE_FLOOR
        threshold = PLAY_VALUE_FLOOR
        assert ev > threshold, (
            f"Reckless Impulse scored EV={ev:.2f} at low "
            f"counter_density=0.05 — below PLAY_VALUE_FLOOR={threshold}. "
            f"This regression anchor must hold across the fix; if it "
            f"fails the test scaffold is broken (not the rule)."
        )
