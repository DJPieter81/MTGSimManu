"""Failing-test-first contract for the effective-CMC primitive (W0-F).

Per CLAUDE.md `§Hard prohibitions`: "No fix without a failing test in
the same diff.  Test goes red first, then the fix lands and turns it
green.  Both in the same commit."

This file pins the *mechanic* contract for `ai.effective_cmc:
effective_cmc(card, snap, *, game, player_idx, cast_mode)` — every
assertion names a rule ("delve subtracts at most the printed generic
mana from CMC"), never a card.  Card names appear only as fixture
data, never in the test identifier or the assertion message.

Why this primitive exists (audit M9 finding): `_project_spell`
currently charges the printed CMC for every spell, ignoring delve,
evoke, cost reducers on board, kicker, affinity / improvise.  Storm
under Ruby Medallion scores its rituals as un-discounted; Murktide
scores as a 7-mana spell that the deck never pays 7 for.  The cure is
ONE primitive that owns all cost modifications, called once at the
start of `_project_spell` (Wave 1 work) — this file pins its
contract.

Structural-only:
- No `card.name == "X"` checks anywhere — dispatch through the
  W0-A oracle classifier (`has_tag(name, Tag.DELVE)`) and the
  oracle-driven cost-reducer rule already in
  `engine.oracle_resolver.count_cost_reducers` (which itself reads
  `parse_cost_reduction` from `engine.oracle_parser`).
- No new bare numeric literals — delve cap is the card's own
  printed generic-mana count (derived from `card.mana_cost`),
  not a hardcoded `MAX_DELVE = 7`.
"""
from __future__ import annotations

import random

import pytest


# All imports happen inside test bodies so collection still works
# during the RED phase (module-not-yet-existing).  Once
# `ai.effective_cmc` lands the import succeeds and every assertion is
# exercised.


# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def card_db():
    from engine.card_database import CardDatabase

    return CardDatabase()


@pytest.fixture
def fresh_game():
    """Empty `GameState` with deterministic RNG.  Each test gets a
    fresh instance so battlefield contents are isolated."""
    from engine.game_state import GameState

    return GameState(rng=random.Random(0))


@pytest.fixture
def base_snap():
    """Mid-game default snapshot — adequate mana, turn 4, default
    life totals.  Tests that need different values clone via
    `replace(...)`."""
    from ai.ev_evaluator import EVSnapshot

    return EVSnapshot(
        my_life=20,
        opp_life=20,
        my_hand_size=5,
        opp_hand_size=5,
        my_mana=7,
        my_total_lands=7,
        opp_total_lands=4,
        turn_number=4,
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
    for delve / flashback tests."""
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
    """Build a `CardInstance` for `name` in hand (no game attached).
    Sufficient for the "no game, just template" branches of the
    primitive."""
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


# ─── Contract 1: base case ───────────────────────────────────────────


def test_normal_cost_returns_printed_cmc_when_no_modifiers(card_db, base_snap):
    """A spell with no cost-modifying mechanic active returns its
    printed CMC.  Counterspell at CMC 2 with no reducers, no delve,
    no evoke must score as 2 — anything else would mean the primitive
    is adding hidden discounts."""
    from ai.effective_cmc import effective_cmc

    card = _make_card_in_hand(card_db, "Counterspell")
    assert effective_cmc(card, base_snap) == 2, (
        "A vanilla CMC-2 instant with no cost modifiers must return "
        "its printed CMC unchanged."
    )


def test_unknown_card_returns_printed_cmc(card_db, base_snap):
    """A card with no classifier tags (negative example) still
    returns its printed CMC.  The primitive must be safe on cards
    the cache has never seen — empty `tags_for` means 'no
    modifications apply'."""
    from ai.effective_cmc import effective_cmc

    # Galvanic Discharge is in the smoke cache with only
    # TARGET_ANY_DAMAGE — none of the cost-modifying tags.  This
    # exercises the "card known to DB but not a cost-modified spell"
    # branch.
    card = _make_card_in_hand(card_db, "Galvanic Discharge")
    expected = card.template.cmc
    assert effective_cmc(card, base_snap) == expected, (
        "A card with no cost-modifying mechanic must return its "
        "printed CMC; the primitive must not inject hidden bonuses."
    )


# ─── Contract 2: delve ───────────────────────────────────────────────


def test_delve_subtracts_graveyard_count_capped_by_generic_portion(
    card_db, base_snap, fresh_game
):
    """Delve subtracts at most the printed *generic* portion of the
    card's mana cost — colored requirements are not delve-able.

    Murktide Regent's printed cost is {5}{U}{U} (CMC 7, generic
    portion 5).  With 6 instants/sorceries in the graveyard the
    delve reduction is capped at 5, so effective CMC = 7 - 5 = 2
    (the two blue pips that cannot be delved).

    The cap is derived from the card's own oracle / cost — never a
    hardcoded `MAX_DELVE = 7` constant.  Magic's actual rule is
    "exile any number of cards from your graveyard; each card
    exiled pays {1}", which means the cap IS the generic portion of
    the spell's cost, full stop."""
    from ai.effective_cmc import effective_cmc

    # Put 6 instants/sorceries in own graveyard — more than the
    # delve cap (5) so the cap, not the graveyard size, is the
    # limiting factor.
    for _ in range(6):
        _put_in_graveyard(fresh_game, card_db, "Counterspell", controller=0)

    card = _make_card_in_hand(card_db, "Murktide Regent")
    paid = effective_cmc(card, base_snap, game=fresh_game, player_idx=0)
    # Murktide is {5}{U}{U} — 5 generic, 2 colored.  Delve caps at
    # the generic portion (5), so paid = 7 - 5 = 2.
    assert paid == 2, (
        f"Delve must cap at the printed generic-mana portion of the "
        f"spell's cost, not at the graveyard size.  Murktide is "
        f"{{5}}{{U}}{{U}} and graveyard has 6 instants/sorceries, "
        f"so paid = 7 - min(5, 6) = 2.  Got {paid}."
    )


def test_delve_subtracts_only_available_graveyard_size_when_smaller(
    card_db, base_snap, fresh_game
):
    """When the graveyard is smaller than the generic cap, delve
    subtracts the graveyard size, not the cap.  Murktide with 3
    instants/sorceries in GY pays 7 - 3 = 4."""
    from ai.effective_cmc import effective_cmc

    for _ in range(3):
        _put_in_graveyard(fresh_game, card_db, "Counterspell", controller=0)

    card = _make_card_in_hand(card_db, "Murktide Regent")
    paid = effective_cmc(card, base_snap, game=fresh_game, player_idx=0)
    assert paid == 4, (
        f"Delve subtracts min(generic_portion, gy_size) — with 3 "
        f"cards in graveyard, paid = 7 - 3 = 4.  Got {paid}."
    )


# ─── Contract 3: evoke ───────────────────────────────────────────────


def test_evoke_returns_evoke_cost_when_cast_mode_is_evoke(card_db, base_snap):
    """`cast_mode='evoke'` returns the card's alternative evoke
    cost, not its printed cost.  Evoke is a *choice* the caster
    makes at cast time — the primitive's job is to report the cost
    paid given the chosen mode.

    Solitude's evoke cost is "exile a white card from your hand" —
    a non-mana alternative.  The evoke `ManaCost.cmc` for Solitude
    is 0 (the cost is exile-fodder, not mana).  Whether the caster
    has the white card to exile is a separate predicate; the
    primitive only reports the mana paid for the chosen mode."""
    from ai.effective_cmc import effective_cmc

    card = _make_card_in_hand(card_db, "Solitude")
    if card.template.evoke_cost is None:
        pytest.skip(
            "Solitude's evoke_cost not parsed in this DB — skip "
            "(the primitive's contract is still valid; the test "
            "needs a card whose evoke cost is parsed)."
        )

    paid_normal = effective_cmc(card, base_snap)
    paid_evoke = effective_cmc(card, base_snap, cast_mode="evoke")

    assert paid_evoke == card.template.evoke_cost.cmc, (
        f"cast_mode='evoke' must return the card's parsed "
        f"evoke_cost.cmc.  Got paid_evoke={paid_evoke}, "
        f"evoke_cost.cmc={card.template.evoke_cost.cmc}."
    )
    # And the modes must differ — otherwise the primitive isn't
    # actually dispatching on cast_mode.
    assert paid_evoke != paid_normal, (
        "cast_mode='evoke' must yield a different cost than "
        "cast_mode='normal' for an evoke creature — otherwise the "
        "primitive ignored the mode parameter."
    )


# ─── Contract 4: cost reducer on board ───────────────────────────────


def test_cost_reducer_on_board_reduces_cmc(card_db, base_snap, fresh_game):
    """A cost reducer matching the spell's color/type reduces its
    effective CMC by the reducer's amount.

    Ruby Medallion reads "Red spells you cast cost {1} less to
    cast."  Casting a red instant (Lightning Bolt, CMC 1) with
    Medallion on board yields effective CMC = max(0, 1 - 1) = 0.

    Detection routes through `engine.oracle_resolver.
    count_cost_reducers`, which parses "cost {N} less" from oracle
    text via `engine.oracle_parser.parse_cost_reduction` — no card-
    name checks anywhere in the dispatch path."""
    from ai.effective_cmc import effective_cmc

    _put_in_play(fresh_game, card_db, "Ruby Medallion", controller=0)
    card = _make_card_in_hand(card_db, "Lightning Bolt")

    paid = effective_cmc(card, base_snap, game=fresh_game, player_idx=0)
    expected = max(0, card.template.cmc - 1)
    assert paid == expected, (
        f"A red-spell cost reducer on the battlefield must "
        f"discount a red spell by the reducer's amount.  "
        f"Lightning Bolt under Ruby Medallion: paid={paid}, "
        f"expected={expected}."
    )


def test_cost_reducer_only_applies_when_color_matches(
    card_db, base_snap, fresh_game
):
    """A color-gated cost reducer does NOT reduce a spell of the
    wrong color.  Ruby Medallion (red-only) on the battlefield
    must NOT reduce a blue spell's CMC.

    This is the same guard as `parse_cost_reduction`'s color check
    — the primitive must respect it."""
    from ai.effective_cmc import effective_cmc

    _put_in_play(fresh_game, card_db, "Ruby Medallion", controller=0)
    # Counterspell is {U}{U} — pure blue, no red identity.
    card = _make_card_in_hand(card_db, "Counterspell")

    paid = effective_cmc(card, base_snap, game=fresh_game, player_idx=0)
    expected = card.template.cmc
    assert paid == expected, (
        f"A color-gated cost reducer must NOT reduce a wrong-color "
        f"spell.  Counterspell under Ruby Medallion: paid={paid}, "
        f"expected={expected} (printed CMC, no discount)."
    )


# ─── Contract 5: kicker is opt-in, never paid by default ─────────────


def test_kicker_is_opt_in_not_paid_by_default(card_db, base_snap):
    """A card with a kicker cost is paid at its BASE cost by
    default — the kicker is an optional additional cost the caster
    elects.  The primitive's default is `with_kicker=False`,
    matching the engine's "always cast unkicked" simplification
    (engine/card_effects.py:985).

    This guards against an over-eager primitive that adds the
    kicker cost to the base price whenever the classifier flags
    `Tag.KICKER`."""
    from ai.effective_cmc import effective_cmc
    from ai.oracle_classifier import Tag, has_tag

    # Find any DB card carrying KICKER (smoke cache may have none;
    # use a structural-skip if so).  The contract is independent of
    # which card we pick — any kickered spell exercises the rule.
    db = card_db
    kicker_card_name = None
    # Pick from a well-known kicker spell list — only used to look
    # up the template; the assertion is on the *mechanic*, not on
    # any specific card.  These names are fixture data only.
    for candidate in ("Plumb the Forbidden", "Burst Lightning", "Rift Bolt"):
        tmpl = db.get_card(candidate)
        if tmpl is None:
            continue
        # Card must have a kicker-shaped optional cost; we accept
        # either the classifier tag OR an oracle that mentions
        # "kicker" — the classifier may not yet have classified
        # every kicker card, but the mechanic is the same.
        oracle_l = (tmpl.oracle_text or "").lower()
        if has_tag(candidate, Tag.KICKER) or "kicker" in oracle_l:
            kicker_card_name = candidate
            break

    if kicker_card_name is None:
        pytest.skip(
            "No kicker card available in DB / smoke cache — "
            "contract still holds; needs a kicker fixture."
        )

    card = _make_card_in_hand(card_db, kicker_card_name)
    paid = effective_cmc(card, base_snap)
    assert paid == card.template.cmc, (
        f"Kicker is opt-in by default — paid must equal the base "
        f"printed CMC.  {kicker_card_name}: paid={paid}, "
        f"printed={card.template.cmc}."
    )


# ─── Contract 6: free-cast modes (cascade, wish) ─────────────────────


def test_free_cast_mode_returns_zero(card_db, base_snap):
    """`cast_mode='free'` returns 0 — for cascade-cast spells,
    wish-fetched cards, and similar "no mana paid" branches.  This
    is the primitive's contract for the cascade reanimator path
    that Wave 1 will route through `_project_spell`."""
    from ai.effective_cmc import effective_cmc

    card = _make_card_in_hand(card_db, "Counterspell")
    paid = effective_cmc(card, base_snap, cast_mode="free")
    assert paid == 0, (
        f"cast_mode='free' (cascade / wish / suspend resolution) "
        f"must return 0 mana paid.  Got {paid}."
    )


# ─── Contract 7: affinity discount ───────────────────────────────────


def test_affinity_subtracts_artifact_count(card_db, base_snap, fresh_game):
    """Affinity for artifacts subtracts the controller's artifact
    count from the spell's effective CMC.  This mirrors
    `engine.cast_manager`'s rule at the cost-calculation site —
    the primitive must agree with engine on the discount.

    Frogmite's printed cost is {4}; with 2 artifacts on the
    battlefield it costs {2}."""
    from ai.effective_cmc import effective_cmc

    # Two artifacts on board (use a known mana-rock-ish card that's
    # an artifact — Mishra's Bauble or similar).  We use Ruby
    # Medallion (artifact) and any other artifact in the DB.
    _put_in_play(fresh_game, card_db, "Ruby Medallion", controller=0)
    _put_in_play(fresh_game, card_db, "Ruby Medallion", controller=0)

    # Frogmite is the canonical affinity-for-artifacts test card.
    frogmite_tmpl = card_db.get_card("Frogmite")
    if frogmite_tmpl is None:
        pytest.skip("Frogmite not in DB — affinity contract needs the fixture")

    card = _make_card_in_hand(card_db, "Frogmite")
    paid = effective_cmc(card, base_snap, game=fresh_game, player_idx=0)
    # Frogmite has affinity-for-artifacts: pays {generic - artifacts}.
    # Ruby Medallion ALSO contributes to artifact_count (it IS an
    # artifact), so 2 medallions = 2 artifact discount.
    expected = max(0, frogmite_tmpl.cmc - 2)
    assert paid == expected, (
        f"Affinity-for-artifacts must subtract the controller's "
        f"artifact count from the spell's CMC.  Frogmite with 2 "
        f"artifacts on board: paid={paid}, expected={expected}."
    )
