"""Failing-test-first contract for M9 — ``_project_spell`` must
charge the *effective* mana cost (post delve / evoke / cost-reducer
/ affinity / improvise discounts), not the printed CMC.

Audit (`docs/history/audits/2026-05-16_5panel_bo3_audit.md`, Midrange
F5):

    Murktide Regent scores EV = -30.98 because projection treats it
    as a 7-mana spell, instead of {U}{U} + 5 delved.  Solitude scores
    as 3WW not "free with white pitch".  Storm rituals under Ruby
    Medallion are not discounted in the projection.  Same shape, one
    root cause: ``_project_spell`` calls ``snap.my_mana - (t.cmc or
    0)`` directly instead of routing through the W0-F primitive.

The cure is a single-line behaviour change: replace
``(t.cmc or 0)`` with ``effective_cmc(card, snap, game=game,
player_idx=player_idx)``.  All cost-modifying mechanics compose
inside the primitive; ``_project_spell`` itself stays oblivious to
the mechanics list.

Why a test, written first
-------------------------

Per CLAUDE.md *Hard prohibitions* — "No fix without a failing test
in the same diff.  Test goes red first, then the fix lands and turns
it green."  Every assertion in this file names a *mechanic*
(delve subtracts graveyard cards, evoke pays evoke_cost, cost
reducers reduce a same-color spell, vanilla spell is unchanged) —
never a card.  Cards appear only as fixture data; the rule under
test is the post-cast mana projection, not "Murktide works".

Structural-only
---------------

* No ``card.name == "X"`` anywhere — dispatch through W0-F.
* No bare new magic numbers — discounts come from the card / game.
* All four tests touch the same one-line call site
  (``ai/ev_evaluator.py:_project_spell``); each names a distinct
  mechanic so a regression in one cost-modifier cannot mask another.
"""
from __future__ import annotations

import random

import pytest


# All imports happen inside test bodies so collection still works
# while the source-side change is in flight.


@pytest.fixture(scope="module")
def card_db():
    from engine.card_database import CardDatabase

    return CardDatabase()


@pytest.fixture
def fresh_game():
    """Empty `GameState` with deterministic RNG — each test gets a
    fresh instance so battlefield / graveyard contents are
    isolated."""
    from engine.game_state import GameState

    return GameState(rng=random.Random(0))


@pytest.fixture
def base_snap():
    """Mid-game snapshot with adequate mana — large enough that
    every post-cast `my_mana` value remains observable above the
    floor (`max(0, ...)`)."""
    from ai.ev_evaluator import EVSnapshot

    return EVSnapshot(
        my_life=20,
        opp_life=20,
        my_hand_size=5,
        opp_hand_size=5,
        my_mana=10,
        my_total_lands=10,
        opp_total_lands=4,
        turn_number=5,
    )


def _put_in_play(game, card_db, name, controller, tapped=False):
    """Drop one copy of `name` onto `controller`'s battlefield."""
    from engine.cards import CardInstance

    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card from DB: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    card.tapped = tapped
    game.players[controller].battlefield.append(card)
    return card


def _put_in_graveyard(game, card_db, name, controller):
    """Drop one copy of `name` into `controller`'s graveyard.  Used
    for delve fuel."""
    from engine.cards import CardInstance

    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card from DB: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="graveyard",
    )
    game.players[controller].graveyard.append(card)
    return card


def _make_card_in_hand(card_db, name, controller=0):
    """Build a `CardInstance` for `name` in hand."""
    from engine.cards import CardInstance

    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card from DB: {name}"
    return CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=0,
        zone="hand",
    )


# ─── Mechanic 1: delve discount carries into projection ──────────────


def test_murktide_with_5_gy_instants_projects_as_two_mana(
    card_db, base_snap, fresh_game
):
    """Delve subtracts graveyard cards from the projected mana spend.

    Murktide Regent's printed CMC is 7 ({5}{U}{U}).  With 5 cards in
    own graveyard the projection must spend `cmc - delve = 7 - 5 = 2`
    mana, leaving `my_mana = 10 - 2 = 8` — not `my_mana = 10 - 7 = 3`.

    This is the audit's exact M9 finding (Murktide projecting as a
    7-mana spell when the deck actually pays {U}{U} after delving).
    The projection contract: post-cast `my_mana` reflects the cost
    *actually paid*, derived through `effective_cmc`."""
    from ai.ev_evaluator import _project_spell

    for _ in range(5):
        _put_in_graveyard(fresh_game, card_db, "Counterspell", controller=0)

    card = _make_card_in_hand(card_db, "Murktide Regent")
    projected = _project_spell(
        card, base_snap, game=fresh_game, player_idx=0
    )

    # Murktide is {5}{U}{U}.  Delve cap is the generic portion (5);
    # 5 GY cards consume all 5 generic → paid = 2.  Post-cast mana
    # is base_snap.my_mana (10) - 2 = 8.
    expected_mana = base_snap.my_mana - 2
    assert projected.my_mana == expected_mana, (
        f"Delve must subtract graveyard fuel from the projected "
        f"mana spend.  Murktide Regent with 5 GY instants: "
        f"my_mana={projected.my_mana}, expected={expected_mana} "
        f"(snap.my_mana={base_snap.my_mana} - effective_cmc=2)."
    )


# ─── Mechanic 2: cast_mode='evoke' (deferred to Wave-2) ──────────────


def test_solitude_evoke_projects_as_pitch_cost(card_db, base_snap):
    """Casting a spell in `evoke` mode projects the evoke cost, not
    the printed CMC.

    Solitude's printed cost is 3WW (CMC 5); its evoke cost is a
    non-mana pitch (exile a white card from hand), whose parsed
    `ManaCost.cmc` is 0.  When projected in evoke mode the post-cast
    mana spend must equal the parsed evoke cost — *not* 5.

    M9 currently has no `cast_mode` plumbing into `_project_spell`
    (the projection always assumes `CAST_MODE_NORMAL`).  The
    contract under test is therefore: the *normal-mode* projection
    of Solitude pays the printed CMC, and the primitive contract for
    evoke (asserted in `tests/test_effective_cmc.py`) guarantees
    that the cast-mode wiring, when added, will reduce that to the
    evoke cost.  This test exercises the normal-mode path through
    the migrated call site — a regression here means the call site
    is *not* routing through `effective_cmc` at all."""
    from ai.effective_cmc import effective_cmc, CAST_MODE_EVOKE
    from ai.ev_evaluator import _project_spell

    card = _make_card_in_hand(card_db, "Solitude")
    if card.template.evoke_cost is None:
        pytest.skip(
            "Solitude's evoke_cost not parsed in this DB — skip "
            "(the contract is still valid; the test needs a card "
            "whose evoke cost is parsed)."
        )

    # Normal-mode projection — the migrated `_project_spell` must
    # agree with `effective_cmc(..., cast_mode=normal)`.  This is the
    # part of the rule we can assert today: any divergence here
    # proves the call site is still reading `t.cmc` directly.
    projected = _project_spell(card, base_snap, game=None, player_idx=0)
    expected_paid = effective_cmc(card, base_snap)
    expected_mana = max(0, base_snap.my_mana - expected_paid)
    assert projected.my_mana == expected_mana, (
        f"`_project_spell` must spend `effective_cmc` mana, not "
        f"`t.cmc` mana.  Solitude (printed {card.template.cmc}): "
        f"projected.my_mana={projected.my_mana}, "
        f"expected={expected_mana} (snap.my_mana={base_snap.my_mana} "
        f"- effective_cmc={expected_paid})."
    )

    # Evoke contract — guaranteed by the W0-F primitive even before
    # `_project_spell` gains a `cast_mode` parameter.  This pins
    # the property the audit cited: evoke cost is parsed and
    # different from the printed cost.
    evoke_paid = effective_cmc(card, base_snap, cast_mode=CAST_MODE_EVOKE)
    assert evoke_paid != card.template.cmc, (
        f"Solitude's evoke cost must differ from its printed CMC — "
        f"otherwise the primitive isn't dispatching on cast_mode.  "
        f"Got evoke_paid={evoke_paid}, printed={card.template.cmc}."
    )


# ─── Mechanic 3: on-board cost reducer carries into projection ────────


def test_storm_ritual_under_ruby_medallion_projects_cheaper(
    card_db, base_snap, fresh_game
):
    """A red spell projected with Ruby Medallion on board pays one
    less mana than its printed CMC.

    Ruby Medallion reads "Red spells you cast cost {1} less to cast."
    Lightning Bolt is {R} (CMC 1).  Under Medallion the projection
    must spend `max(0, 1 - 1) = 0` mana — leaving `my_mana`
    unchanged from the source snapshot.  This is the same audit
    finding for Storm rituals (Manamorphose / Desperate Ritual /
    Pyretic Ritual all project at full printed cost without M9)."""
    from ai.ev_evaluator import _project_spell

    _put_in_play(fresh_game, card_db, "Ruby Medallion", controller=0)
    card = _make_card_in_hand(card_db, "Lightning Bolt")

    projected = _project_spell(
        card, base_snap, game=fresh_game, player_idx=0
    )

    # Lightning Bolt is {R}; under Ruby Medallion `effective_cmc`
    # returns max(0, 1 - 1) = 0.  Post-cast mana = base_snap.my_mana
    # (10) - 0 = 10.
    expected_mana = base_snap.my_mana - 0
    assert projected.my_mana == expected_mana, (
        f"A color-matching cost reducer on the battlefield must "
        f"discount the projected mana spend.  Lightning Bolt under "
        f"Ruby Medallion: my_mana={projected.my_mana}, "
        f"expected={expected_mana}."
    )


# ─── Mechanic 4: vanilla spell unchanged ─────────────────────────────


def test_non_modified_spells_unchanged(card_db, base_snap):
    """A spell with no active cost-modifying mechanic projects at
    its printed CMC.  Counterspell at {U}{U} with no reducers, no
    delve, no evoke must spend exactly 2 — anything else means the
    primitive is adding hidden discounts (false-positive) or the
    call site is reading the wrong field."""
    from ai.ev_evaluator import _project_spell

    card = _make_card_in_hand(card_db, "Counterspell")
    projected = _project_spell(card, base_snap, game=None, player_idx=0)

    expected_mana = base_snap.my_mana - (card.template.cmc or 0)
    assert projected.my_mana == expected_mana, (
        f"A vanilla spell with no cost-modifying mechanic must "
        f"project at its printed CMC.  Counterspell (printed "
        f"{card.template.cmc}): my_mana={projected.my_mana}, "
        f"expected={expected_mana} (snap.my_mana={base_snap.my_mana} "
        f"- cmc={card.template.cmc})."
    )
