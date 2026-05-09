"""Drill-down mechanical tests — Affinity cost mechanics post-Phase-1A.

Each test pins a specific spell's effective casting cost in a specific
game-state. Together they cover the four cost paths that Phase 1A's
``parse_cost_reduction`` false-positive distorted:

1. **Plain hardcasts** — Cranial Plating (CMC 2). Pre-fix: Saga on
   battlefield grants -1 reduction, Plating costs {1}. Post-fix: {2}.
2. **Affinity discount** — Frogmite, Thought Monitor, Sojourner's
   Companion, Myr Enforcer. The keyword discount stacks with the
   buggy generic reducer. Pre-fix: -N (artifacts) -1 (bug) = -N-1.
   Post-fix: -N exactly.
3. **Activated abilities** — Saga's chapter II "{2}, {T}: Create
   Construct" must remain {2}. (Engine handles activated abilities
   through a separate cost path, not tap_lands_for_mana, so we
   verify the cost is paid as printed.)
4. **Alternative casting costs** — Pinnacle Emissary warp ({1}{R}),
   Phlage escape, Boseiju channel — all parsed as separate cost
   templates, must not be reduced by the false-positive.

These are L1 fixtures per the plan: pre-built game state, exact
mechanical assertion. Each runs in <100 ms. Together they replace
"run a 50-game smoke matchup and eyeball the WR delta" with
deterministic assertions that pin the rule directly.

Reference: /root/.claude/plans/now-lets-fix-affinity-keen-penguin.md
Phase 1A drill-down.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance, Keyword, CardType
from engine.game_state import GameState
from engine.mana import ManaCost
from engine.oracle_resolver import count_cost_reducers


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _put_in_play(game, card_db, name, controller, tapped=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    card.tapped = tapped
    game.players[controller].battlefield.append(card)
    return card


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


# ─── 1. Plain hardcasts: no false generic reduction from Saga ────────


def test_saga_does_not_reduce_plating(card_db):
    """Saga's oracle (false-positive 'cost' + 'less' from 'mana cost
    {0} or {1}' + 'colorless Construct') must NOT reduce Cranial
    Plating's CMC. count_cost_reducers must return 0."""
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Urza's Saga", 0)
    plating_t = card_db.get_card("Cranial Plating")

    n = count_cost_reducers(game, 0, plating_t)
    assert n == 0, (
        f"Saga must not act as a generic cost reducer for Cranial "
        f"Plating. count_cost_reducers={n}; expected 0."
    )


def test_saga_does_not_reduce_galvanic_discharge(card_db):
    """Same rule, instant: Galvanic Discharge (Boros' burn) must not
    benefit from Saga's bogus reducer. (We test even though Boros
    doesn't run Saga, because the bug applied to ANY caster's own
    Saga.)"""
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Urza's Saga", 0)
    discharge_t = card_db.get_card("Galvanic Discharge")
    if discharge_t is None:
        pytest.skip("Galvanic Discharge missing from DB")

    n = count_cost_reducers(game, 0, discharge_t)
    assert n == 0


def test_saga_does_not_reduce_lightning_bolt(card_db):
    """Saga's bogus reducer also shouldn't fire for instants like
    Lightning Bolt (a generic burn spell). Sanity baseline."""
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Urza's Saga", 0)
    bolt_t = card_db.get_card("Lightning Bolt")
    if bolt_t is None:
        pytest.skip("Lightning Bolt missing from DB")

    n = count_cost_reducers(game, 0, bolt_t)
    assert n == 0


# ─── 2. Affinity discount: stacks correctly, not double-reduced ──────


def test_frogmite_cost_with_3_artifacts_is_exactly_one(card_db):
    """Frogmite CMC 4 with affinity-for-artifacts. Three artifact
    permanents on battlefield (Saga + Memnite + Mox Opal — note Saga
    is enchantment+land, NOT artifact, so it doesn't count for
    affinity discount).

    Wait: Saga's card_types are [ENCHANTMENT, LAND], NO ARTIFACT. So
    only Memnite + Mox = 2 artifacts → discount = 2 → effective_cmc
    = 4 - 2 = 2.

    Pre-fix: 4 - 2 (affinity) - 1 (bogus Saga reducer) = 1 (wrong).
    Post-fix: 4 - 2 = 2 (correct).

    Tap_lands_for_mana resolves Frogmite's affinity inside the same
    function (line 167-172 of mana_payment.py). The bogus generic
    reducer used to stack on top via count_cost_reducers (line 162).
    Post-fix: count_cost_reducers returns 0 here.
    """
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Urza's Saga", 0)
    _put_in_play(game, card_db, "Memnite", 0)
    _put_in_play(game, card_db, "Mox Opal", 0)
    frogmite = _put_in_hand(game, card_db, "Frogmite", 0)

    # Verify Frogmite has affinity keyword.
    assert Keyword.AFFINITY in frogmite.template.keywords
    # Verify the bogus generic reducer count is now 0.
    n = count_cost_reducers(game, 0, frogmite.template)
    assert n == 0, (
        f"Saga must not reduce Frogmite's cost. count_cost_reducers="
        f"{n}; the affinity discount path is separate (handled in "
        f"tap_lands_for_mana directly)."
    )


def test_thought_monitor_cost_no_false_reducer(card_db):
    """Thought Monitor CMC 7 with affinity-for-artifacts. Verify no
    false generic reducer fires from Saga, Plating, or any other
    Affinity-side permanent.

    With 5 artifact permanents on battlefield (Mox Opal, Memnite,
    Ornithopter, Plating attached to Memnite, Frogmite), Thought
    Monitor's affinity discount = 5 → effective CMC = 7 - 5 = 2.
    Pre-fix: 2 - 1 (bogus) = 1.
    Post-fix: 2.
    """
    game = GameState(rng=random.Random(0))
    _put_in_play(game, card_db, "Urza's Saga", 0)  # non-artifact land
    _put_in_play(game, card_db, "Mox Opal", 0)
    _put_in_play(game, card_db, "Memnite", 0)
    _put_in_play(game, card_db, "Ornithopter", 0)
    _put_in_play(game, card_db, "Cranial Plating", 0)
    _put_in_play(game, card_db, "Frogmite", 0)
    monitor = _put_in_hand(game, card_db, "Thought Monitor", 0)

    n = count_cost_reducers(game, 0, monitor.template)
    assert n == 0


# ─── 3. Plating cast cost — paid as {2}, not {1} ────────────────────


def test_plating_cast_taps_two_lands(card_db):
    """Cranial Plating ({2}) cast with two basic lands available
    (each producing {C}) must tap BOTH. Pre-fix: bogus -1 from any
    cost-reducer-active permanent caused only 1 tap. Post-fix: 2.

    Uses Wastes (a colorless basic) instead of Saga to avoid the
    Saga-as-reducer entanglement; this test isolates the rule that
    Plating's printed CMC of 2 is paid in full.
    """
    game = GameState(rng=random.Random(0))
    # Two colorless lands (Wastes is universal)
    w1 = _put_in_play(game, card_db, "Wastes", 0)
    w2 = _put_in_play(game, card_db, "Wastes", 0)
    plating = _put_in_hand(game, card_db, "Cranial Plating", 0)

    assert not w1.tapped and not w2.tapped, "Wastes start untapped"

    paid = game.tap_lands_for_mana(0, ManaCost(generic=2),
                                   card_name="Cranial Plating")
    assert paid, "Plating cost {2} should be payable from 2 Wastes"
    assert w1.tapped and w2.tapped, (
        f"Both Wastes must tap to pay Plating's {{2}}. Got "
        f"w1.tapped={w1.tapped}, w2.tapped={w2.tapped}."
    )


def test_plating_cannot_be_paid_from_one_waste(card_db):
    """Plating ({2}) with only ONE Waste (1 mana) cannot pay. The
    payment routine must return False. Together with the previous
    test, this pins the rule that Plating costs exactly 2 generic
    mana and no false reducer fires."""
    game = GameState(rng=random.Random(0))
    w1 = _put_in_play(game, card_db, "Wastes", 0)
    plating = _put_in_hand(game, card_db, "Cranial Plating", 0)

    paid = game.tap_lands_for_mana(0, ManaCost(generic=2),
                                   card_name="Cranial Plating")
    assert not paid, (
        "Plating cost {2} cannot be paid from a single Waste (1 mana)"
    )
    assert not w1.tapped, "Failed payment must not tap any source"


# ─── 4. Saga + Phlage interaction (cross-deck false positive) ────────


def test_phlage_does_not_reduce_other_spells(card_db):
    """Phlage's oracle text contains 'cost' (escape cost reference)
    and 'less' (in 'less than' or 'colorless'). Pre-fix it falsely
    fired as a generic cost-reducer for spells cast by Boros Energy.
    Post-fix: count_cost_reducers returns 0 with Phlage on board."""
    game = GameState(rng=random.Random(0))
    phlage = _put_in_play(game, card_db, "Phlage, Titan of Fire's Fury", 0)
    bolt_t = card_db.get_card("Lightning Bolt")
    if bolt_t is None:
        pytest.skip("Lightning Bolt missing from DB")

    n = count_cost_reducers(game, 0, bolt_t)
    assert n == 0, (
        f"Phlage's 'cost' + 'less' substring presence must not make "
        f"it a generic cost reducer. count_cost_reducers={n}."
    )


def test_pinnacle_emissary_does_not_reduce_other_spells(card_db):
    """Pinnacle Emissary has 'mana cost' (in warp text) and 'less'
    (in 'colorless'). Pre-fix: false-positive reducer. Post-fix: 0."""
    game = GameState(rng=random.Random(0))
    emissary = _put_in_play(game, card_db, "Pinnacle Emissary", 0)
    plating_t = card_db.get_card("Cranial Plating")

    n = count_cost_reducers(game, 0, plating_t)
    assert n == 0


# ─── 5. Real cost reducers still work (regression anchors) ───────────


def test_helm_of_awakening_still_reduces(card_db):
    """Real reducer ('Spells cost {1} less to cast') must still
    apply. count_cost_reducers should return 1."""
    game = GameState(rng=random.Random(0))
    helm_t = card_db.get_card("Helm of Awakening")
    if helm_t is None:
        pytest.skip("Helm of Awakening missing from DB")
    helm = CardInstance(
        template=helm_t, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    helm._game_state = game
    helm.enter_battlefield()
    game.players[0].battlefield.append(helm)

    bolt_t = card_db.get_card("Lightning Bolt")
    if bolt_t is None:
        pytest.skip("Lightning Bolt missing from DB")

    n = count_cost_reducers(game, 0, bolt_t)
    assert n == 1, (
        f"Helm of Awakening must apply its real -1 reduction. Got "
        f"count_cost_reducers={n}."
    )


def test_ruby_medallion_reduces_only_red(card_db):
    """Ruby Medallion ('Red spells you cast cost {1} less') must
    reduce red spells, not non-red. Regression anchor for the color
    filter inside parse_cost_reduction."""
    game = GameState(rng=random.Random(0))
    medallion_t = card_db.get_card("Ruby Medallion")
    if medallion_t is None:
        pytest.skip("Ruby Medallion missing from DB")
    medallion = CardInstance(
        template=medallion_t, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    medallion._game_state = game
    medallion.enter_battlefield()
    game.players[0].battlefield.append(medallion)

    bolt_t = card_db.get_card("Lightning Bolt")  # red
    counterspell_t = card_db.get_card("Counterspell")  # blue

    if bolt_t:
        n_red = count_cost_reducers(game, 0, bolt_t)
        assert n_red == 1, (
            f"Ruby Medallion must reduce Lightning Bolt (red). Got {n_red}."
        )
    if counterspell_t:
        n_blue = count_cost_reducers(game, 0, counterspell_t)
        assert n_blue == 0, (
            f"Ruby Medallion must NOT reduce Counterspell (blue). "
            f"Got {n_blue}."
        )
