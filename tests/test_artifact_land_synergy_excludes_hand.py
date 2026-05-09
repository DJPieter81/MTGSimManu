"""Phase 1B / Phase L E-2 — artifact-land synergy bonus is battlefield-only.

Rule under test
---------------
``ai/ev_player.py:_score_land`` adds an EV bonus when an artifact-typed
land is played AND the player controls cards with artifact-scaling
oracle text ("for each artifact", "metalcraft", "affinity for
artifacts"). The bonus represents the marginal +1 power (or +1 mana,
or -1 cost) the land confers on **active** scaling effects.

The bonus must count **battlefield** scaling cards only. Hand-side
scaling cards (e.g. four Cranial Platings in hand) are scored
separately when the AI considers casting them; counting them here
double-books the same expected value, biasing land-choice toward
artifact lands by `synergy_signals × ARTIFACT_LAND_SYNERGY_BONUS` per
hand-side scaling card. With 4 Plating + 1 Mox Opal in hand and no
scaling cards on the battlefield, the bonus inflates by 5×4.0 = +20
EV per artifact-land play — large enough to bias every T1-T2 land
selection.

Pre-fix behaviour
-----------------
``for c in list(me.hand) + list(me.battlefield):``

Post-fix behaviour
------------------
``for c in me.battlefield:``

This mirrors the symmetry with PR-L1 (artifact lands excluded from
``my_artifact_count``): a land is credited for marginal +1 only on
the *deployed* scaling carriers, not on hand-side intent.

Audit context: ``docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md``
finding E-2; plan ``/root/.claude/plans/now-lets-fix-affinity-keen-penguin.md``
Phase 1B.

Sister test: ``tests/test_evsnapshot_artifact_count_excludes_lands.py``
(PR-L1).
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _put_in_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _put_in_play(game, card_db, name, controller):
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


# ─── Negative cases (hand cards must NOT contribute) ─────────────────


def test_no_synergy_bonus_when_only_hand_has_scaling_cards(card_db):
    """4 Cranial Platings + 1 Mox Opal in hand, NO scaling cards on
    battlefield. The artifact-land synergy bonus must be ZERO — the
    hand-side scaling intent is captured when those cards are cast,
    not when a land is played that "would help them."

    Pre-fix: synergy_signals = 5 (4 Plating + 1 Mox Opal counted from
    hand) → +20 EV per artifact land.
    Post-fix: synergy_signals = 0 → no bonus.
    """
    game = GameState(rng=random.Random(0))
    citadel = _put_in_hand(game, card_db, "Darksteel Citadel", 0)
    spire = _put_in_hand(game, card_db, "Spire of Industry", 0)
    # 4 hand-side Platings + 1 hand-side Mox; nothing on battlefield.
    for _ in range(4):
        _put_in_hand(game, card_db, "Cranial Plating", 0)
    _put_in_hand(game, card_db, "Mox Opal", 0)
    _put_in_hand(game, card_db, "Memnite", 0)  # nontrivial blocker

    me = game.players[0]
    spells = [c for c in me.hand if not c.template.is_land]
    player = EVPlayer(player_idx=0, deck_name="Affinity",
                      rng=random.Random(0))

    citadel_ev = player._score_land(citadel, me, spells, game)
    spire_ev = player._score_land(spire, me, spells, game)

    # Without the bug, citadel and spire score equally (or close —
    # spire has color flexibility, citadel is colorless artifact).
    # The synergy delta in the buggy version is ~+20 in citadel's
    # favor purely from hand-side double-counting.
    delta = citadel_ev - spire_ev
    assert delta < 8.0, (
        f"With NO scaling cards on battlefield, the artifact-land "
        f"synergy bonus must be 0. Pre-fix the bug pads citadel's EV "
        f"by ~+20 over spire purely on hand-side scaling intent. "
        f"Got citadel_ev={citadel_ev:.2f}, spire_ev={spire_ev:.2f}, "
        f"delta={delta:.2f}."
    )


def test_synergy_bonus_present_when_battlefield_has_scaling_card(card_db):
    """1 Cranial Plating ON BATTLEFIELD (no hand-side scaling cards).
    The bonus IS present — 1 active carrier × 4.0 = +4 EV per
    artifact land. Hand cards stay at 0 contribution.

    This is the regression-anchor: post-fix the bonus is not zeroed
    out altogether, just confined to deployed scaling effects.
    """
    game = GameState(rng=random.Random(0))
    citadel = _put_in_hand(game, card_db, "Darksteel Citadel", 0)
    spire = _put_in_hand(game, card_db, "Spire of Industry", 0)
    # 1 Plating on the battlefield (active scaling effect)
    _put_in_play(game, card_db, "Cranial Plating", 0)
    _put_in_play(game, card_db, "Memnite", 0)  # carrier
    # Hand has unrelated cards
    _put_in_hand(game, card_db, "Memnite", 0)

    me = game.players[0]
    spells = [c for c in me.hand if not c.template.is_land]
    player = EVPlayer(player_idx=0, deck_name="Affinity",
                      rng=random.Random(0))

    citadel_ev = player._score_land(citadel, me, spells, game)
    spire_ev = player._score_land(spire, me, spells, game)

    # Citadel must score noticeably higher than spire because of the
    # active battlefield Plating; that's a real synergy.
    delta = citadel_ev - spire_ev
    assert delta > 1.0, (
        f"With 1 active scaling card (Cranial Plating) on the "
        f"battlefield, Darksteel Citadel must score higher than Spire "
        f"of Industry — the artifact-land contributes a marginal +1 "
        f"power to the equipped carrier. Got citadel_ev={citadel_ev:.2f}, "
        f"spire_ev={spire_ev:.2f}, delta={delta:.2f}."
    )


def test_synergy_caps_at_battlefield_count(card_db):
    """Hand cards do NOT add to synergy_signals on top of battlefield.
    With 1 battlefield Plating + 4 hand-side Platings, the bonus is
    1 × ARTIFACT_LAND_SYNERGY_BONUS, not 5 ×.
    """
    game = GameState(rng=random.Random(0))
    citadel = _put_in_hand(game, card_db, "Darksteel Citadel", 0)
    # 1 battlefield Plating + 4 hand Platings
    _put_in_play(game, card_db, "Cranial Plating", 0)
    _put_in_play(game, card_db, "Memnite", 0)
    for _ in range(4):
        _put_in_hand(game, card_db, "Cranial Plating", 0)

    me = game.players[0]
    spells = [c for c in me.hand if not c.template.is_land]
    player = EVPlayer(player_idx=0, deck_name="Affinity",
                      rng=random.Random(0))

    with_5_in_hand = player._score_land(citadel, me, spells, game)

    # Same setup, NO Platings in hand
    game2 = GameState(rng=random.Random(0))
    citadel2 = _put_in_hand(game2, card_db, "Darksteel Citadel", 0)
    _put_in_play(game2, card_db, "Cranial Plating", 0)
    _put_in_play(game2, card_db, "Memnite", 0)

    me2 = game2.players[0]
    spells2 = [c for c in me2.hand if not c.template.is_land]
    only_battlefield = player._score_land(citadel2, me2, spells2, game2)

    # The two scores should be EQUAL (within float tolerance). If
    # the hand cards leak in, with_5_in_hand exceeds only_battlefield
    # by ~16 (4 extra hand Platings × 4.0 EV).
    diff = with_5_in_hand - only_battlefield
    assert diff < 1.0, (
        f"Adding 4 Platings to hand (battlefield unchanged) must NOT "
        f"increase the artifact-land synergy bonus. Got with_5_in_hand="
        f"{with_5_in_hand:.2f}, only_battlefield={only_battlefield:.2f}, "
        f"diff={diff:.2f}. If diff > 1, hand cards are leaking into "
        f"synergy_signals."
    )
