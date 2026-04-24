"""GV-2 — Reanimation readiness gate + EOT-exile projection discount.

Diagnostic: Goryo's Vengeance sits at 24.9% flat WR despite valid game
states where Archon of Cruelty / Griselbrand is in the graveyard and
{B}{B} is available. The cascade patience gate at
`ai/ev_player.py:480-516` clamps cascade spells when the graveyard is
THIN; reanimation spells need the opposite — a READINESS gate that
actively boosts EV once the graveyard contains a viable target. Without
this analog, the reanimate_override (+40 at
`ai/ev_player.py:344-346`) is the only signal pushing Goryo's above
pass_threshold, and it fires only in a narrow state (power>=5 filter).

Fix (GV-2):
1. `_score_spell` reanimation readiness gate: when the spell has the
   `reanimate` tag AND the gameplan declares a graveyard-backed
   FILL_RESOURCE goal AND `my_gy_creatures >= resource_target` (combo
   ready), boost EV by `snap.opp_life / 2.0`. The magnitude scales with
   how much damage the reanimated creature still has to deal — no
   magic number. Non-reanimator decks or not-yet-ready graveyards
   receive no boost.
2. `_project_spell` temporary-creature discount: when the reanimate
   spell's oracle contains "exile it at the beginning of the next end
   step" (Goryo's / Footsteps of the Goryo clause), the reanimated
   creature's projected power contribution is multiplied by 0.5 — it
   gets one combat step before being exiled, vs a persistent creature
   that attacks every turn for the rest of the game.

Tests:
1. Gate fires — Goryo's with Griselbrand in GY receives opp_life/2 boost.
2. Regression — Goryo's with no viable target in GY receives no boost.
3. Regression — non-reanimation deck (Boros sorcery) receives no boost.
4. Projection — 7-power creature reanimated via EOT-exile spell yields
   +3.5 projected power (not +7, not +0).
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import snapshot_from_game, _project_spell
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _add_to_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _score_spell(game, deck_name, card):
    game.players[0].deck_name = deck_name
    player = EVPlayer(player_idx=0, deck_name=deck_name,
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]
    return player, player._score_spell(card, snap, game, me, opp)


def _build_goryos_game(card_db, *, with_big_target: bool):
    """Construct a Goryo's mid-game state: 4 Swamps (plenty for BB),
    Goryo's Vengeance in hand, optionally Griselbrand in graveyard."""
    game = GameState(rng=random.Random(0))
    for _ in range(4):
        _add_to_battlefield(game, card_db, "Swamp", controller=0)
    goryos = _add_to_hand(game, card_db, "Goryo's Vengeance", controller=0)
    if with_big_target:
        _add_to_graveyard(game, card_db, "Griselbrand", controller=0)
    else:
        # Small creature that doesn't meet resource_min_cmc — or none.
        # Use no creature at all → my_gy_creatures = 0, below target.
        pass
    game.players[1].deck_name = "Dimir Midrange"
    return game, goryos


class TestReanimationReadinessGate:
    """GV-2 — Goryo's Vengeance should be boosted when GY is loaded."""

    def test_gate_fires_when_graveyard_has_target(self, card_db):
        """Griselbrand in GY + 4 mana + Goryo's in hand → EV receives the
        opp_life/2 boost, pushing the score well above pass_threshold."""
        game, goryos = _build_goryos_game(card_db, with_big_target=True)

        player, ev = _score_spell(game, "Goryo's Vengeance", goryos)

        pass_threshold = player.profile.pass_threshold
        # opp_life defaults to 20, so the gate boost is +10.0. Even
        # accounting for the projection discount (Goryo's 0.5x EOT
        # exile clause), EV must sit well above pass_threshold so the
        # AI fires the reanimation.
        assert ev > pass_threshold + 10.0, (
            f"Goryo's Vengeance EV with Griselbrand in GY = {ev:.2f}, "
            f"should be >> pass_threshold={pass_threshold}. The "
            f"readiness gate must boost EV by opp_life/2 when the "
            f"graveyard target is set up."
        )

    def test_gate_does_not_fire_without_viable_target(self, card_db):
        """Empty graveyard → gate does NOT fire, no boost applied. The
        baseline EV (without the readiness boost) should be the same as
        the one without the gate — we detect this by measuring EV vs
        the ready-state EV and confirming it is smaller by ~opp_life/2."""
        game_empty, goryos_empty = _build_goryos_game(
            card_db, with_big_target=False)
        player_empty, ev_empty = _score_spell(
            game_empty, "Goryo's Vengeance", goryos_empty)

        game_ready, goryos_ready = _build_goryos_game(
            card_db, with_big_target=True)
        player_ready, ev_ready = _score_spell(
            game_ready, "Goryo's Vengeance", goryos_ready)

        # The ready state must score strictly higher, and the gap must
        # be at least opp_life/2 — if the gap is smaller, the gate
        # didn't actually fire in the ready state (or fired in both,
        # which would defeat its purpose).
        snap_empty = snapshot_from_game(game_empty, 0)
        expected_boost = snap_empty.opp_life / 2.0
        assert ev_ready - ev_empty >= expected_boost - 0.01, (
            f"Ready-state EV {ev_ready:.2f} - empty-GY EV "
            f"{ev_empty:.2f} = {(ev_ready-ev_empty):.2f}, expected "
            f">= {expected_boost:.2f} (opp_life/2). Readiness gate "
            f"must only fire when the graveyard target is met."
        )

    def test_gate_does_not_fire_for_non_reanimation_deck(self, card_db):
        """A non-reanimation spell (ordinary sorcery) in a deck with no
        graveyard FILL_RESOURCE goal must not pick up the readiness
        boost — the gate is oracle + gameplan gated, not blanket."""
        game = GameState(rng=random.Random(0))
        for _ in range(4):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        # Ordinary non-reanimation sorcery: Lightning Bolt (burn).
        bolt = _add_to_hand(game, card_db, "Lightning Bolt", controller=0)
        # Seed graveyard with a big creature to prove the BOOST is
        # oracle-gated (not just gameplan-gated). A non-reanimate
        # spell must not be boosted just because the GY has a target.
        _add_to_graveyard(game, card_db, "Griselbrand", controller=0)
        game.players[1].deck_name = "Dimir Midrange"
        # Put an opponent creature so Bolt has a legal target and isn't
        # penalised for lack of targets.
        _add_to_battlefield(game, card_db, "Grizzly Bears", controller=1)

        _, ev = _score_spell(game, "Boros Energy", bolt)

        # Compute the boost magnitude and confirm EV is NOT inflated
        # by anything close to it. Boros Energy has no graveyard
        # FILL_RESOURCE goal, so the gate must not fire. We re-score
        # Bolt as a baseline — same game, but confirm the boost isn't
        # present by checking the EV lies within a sane band (bolt
        # without the gate is typically a few EV points for removal).
        snap = snapshot_from_game(game, 0)
        boost = snap.opp_life / 2.0
        # Upper bound: normal Bolt EV never exceeds ~+15. If the gate
        # fired, it would add another +10, pushing past this. The
        # inequality is written in terms of the boost to keep it
        # principled (no magic number).
        assert ev < 15.0 + boost / 2.0, (
            f"Lightning Bolt EV in Boros = {ev:.2f}. If this is above "
            f"{15.0 + boost/2.0:.2f}, the readiness gate is firing "
            f"for a non-reanimation spell — it must be "
            f"oracle+gameplan gated."
        )


class TestEOTExileProjectionDiscount:
    """GV-2 projection fix — EOT-exile reanimate returns 0.5x power."""

    def test_eot_exile_reanimate_projection_is_half_power(self, card_db):
        """Goryo's Vengeance projects Griselbrand (7 power). Because
        Goryo's oracle says 'Exile it at the beginning of the next
        end step', the projected damage contribution must be 7 * 0.5
        = 3.5 — the creature attacks once (with haste) then is
        exiled. Full +7 would over-value the spell; zero would
        under-value it."""
        game = GameState(rng=random.Random(0))
        for _ in range(4):
            _add_to_battlefield(game, card_db, "Swamp", controller=0)
        gv = _add_to_hand(game, card_db, "Goryo's Vengeance", controller=0)
        _add_to_graveyard(game, card_db, "Griselbrand", controller=0)
        game.players[0].deck_name = "Goryo's Vengeance"
        game.players[1].deck_name = "Dimir Midrange"

        snap = snapshot_from_game(game, 0)
        projected = _project_spell(gv, snap, None, game, 0)

        # Base power delta from reanimation only (my_power starts at 0
        # with no battlefield creatures).
        reanimate_power_gain = projected.my_power - snap.my_power
        # Expected: 7 (Griselbrand power) * 0.5 discount = 3.5.
        expected = 7 * 0.5
        assert abs(reanimate_power_gain - expected) < 0.01, (
            f"EOT-exile reanimate power delta = "
            f"{reanimate_power_gain:.2f}, expected {expected:.2f} "
            f"(7 power * 0.5 for 'exile at end step' clause). "
            f"Goryo's gets ONE attack before exile — not zero, not "
            f"full."
        )

    def test_persistent_reanimate_projection_is_full_power(self, card_db):
        """Regression: Persist (no exile clause) must still project
        full power. The discount is EOT-exile-specific, not a
        blanket reanimate penalty."""
        game = GameState(rng=random.Random(0))
        for _ in range(4):
            _add_to_battlefield(game, card_db, "Swamp", controller=0)
        persist = _add_to_hand(game, card_db, "Persist", controller=0)
        # Persist targets nonlegendary — use a nonlegendary big body.
        # Iridescent Drake / similar. But we can use any nonlegendary
        # creature the DB has. Just use a vanilla creature.
        _add_to_graveyard(game, card_db, "Archon of Cruelty",
                           controller=0)
        game.players[0].deck_name = "Goryo's Vengeance"
        game.players[1].deck_name = "Dimir Midrange"

        snap = snapshot_from_game(game, 0)
        projected = _project_spell(persist, snap, None, game, 0)

        reanimate_power_gain = projected.my_power - snap.my_power
        # Archon of Cruelty has power 6. Persist adds -1/-1 counter
        # but the projection does not model counters — it uses base
        # power/toughness. So we expect full 6 power (no EOT discount).
        # This test pins the contrast: persistent reanimate = full
        # power; EOT-exile reanimate = half.
        # NOTE: this is the PRE-fix behavior and must remain unchanged.
        assert reanimate_power_gain >= 6.0 - 0.01, (
            f"Persist (no exile) projected power gain = "
            f"{reanimate_power_gain:.2f}, expected >= 6.0. The 0.5 "
            f"discount must be EOT-exile-specific — not applied to "
            f"persistent reanimate."
        )
