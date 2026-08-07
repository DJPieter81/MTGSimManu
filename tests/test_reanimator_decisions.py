"""Reanimator-decision fixes — mulligan payload protection + pay-life guard.

Probe evidence (seed 60110, Goryo's vs Boros, integration branch):

1. Mulligan bottomed BOTH Griselbrands (G1) and Atraxa (G2).  Root:
   the critical-piece keep-floor requires ``same_copies_in_hand <= 1``
   — with TWO copies neither is protected, and the legendary-dedup
   penalty then buries copy #2.  Net: two copies = zero protection,
   which is strictly wrong for EVERY deck (Storm with two Grapeshots
   would bottom both).  Additionally, for graveyard-resource decks
   (``resource_zone == 'graveyard'``), the second copy is fuel, not a
   dead card — the dedup penalty itself doesn't apply.

2. T4 Griselbrand double-activation 20→6 life vs a developing aggro
   board; the reanimated body exiles at EOT (no blocker) and the
   opponent attacks for 8 next turn → dead.  Root: the runner guard
   checks ``life >= safe_life`` BEFORE paying (can end far below the
   threshold) against CURRENT opp power only (no development, no
   they-untap accounting).

Fixes under test (all derived from game state / gameplan data — no
card names in code, cards below are fixture carriers):
- Critical floor protects exactly ONE copy per critical name,
  independent of copy count.
- Legendary-dedup penalty skipped for critical pieces of
  graveyard-resource decks.
- ``ai.clock.opp_one_turn_damage`` (the M4-spec primitive): opp
  on-board power (they untap) + one average-deployment increment.
- Pay-life guard: activate only while
  ``life - life_cost > opp_one_turn_damage`` (POST-payment
  survival), lifelink projection preserved.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _mk(game, card_db, name, controller, zone, tapped=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = tapped
        game.players[controller].battlefield.append(c)
    else:
        getattr(game.players[controller], zone).append(c)
    return c


def _mulligan_decider(deck_name="Goryo's Vengeance"):
    from ai.mulligan import MulliganDecider
    from ai.gameplan import GoalEngine
    from ai.strategy_profile import ArchetypeStrategy
    from decks.gameplan_loader import load_gameplan
    gp = load_gameplan(deck_name)
    assert gp is not None
    return MulliganDecider(ArchetypeStrategy.COMBO, GoalEngine(gp))


def _hand(game, card_db, names):
    return [_mk(game, card_db, n, 0, "hand") for n in names]


# ─── mulligan: payload protection ────────────────────────────────────

def test_critical_floor_protects_one_copy_despite_duplicates(card_db):
    """Two copies of a critical piece in hand → bottoming two cards
    must keep at least one copy (the old guard protected NEITHER)."""
    game = GameState(rng=random.Random(0))
    hand = _hand(game, card_db, [
        "Marsh Flats", "Faithful Mending", "Thoughtseize", "Swamp",
        "Watery Grave", "Griselbrand", "Griselbrand"])
    d = _mulligan_decider()
    bottomed = d.choose_cards_to_bottom(hand, 2)
    bottomed_grisel = sum(1 for c in bottomed if c.name == "Griselbrand")
    assert bottomed_grisel <= 1, (
        f"bottomed both copies of a critical piece: "
        f"{[c.name for c in bottomed]}")


def test_graveyard_resource_deck_does_not_dedup_critical_duplicates(
        card_db):
    """resource_zone == 'graveyard': the duplicate critical copy is
    FUEL — it must not be auto-ranked below ordinary spells by the
    legendary-dedup penalty.  Bottoming one card from a hand holding
    2× payload + genuine chaff must pick the chaff."""
    game = GameState(rng=random.Random(0))
    hand = _hand(game, card_db, [
        "Godless Shrine", "Watery Grave", "Swamp", "Marsh Flats",
        "Faithful Mending", "Griselbrand", "Griselbrand"])
    d = _mulligan_decider()
    bottomed = d.choose_cards_to_bottom(hand, 1)
    assert bottomed and bottomed[0].name != "Griselbrand", (
        f"dedup penalty bottomed the payload duplicate below an "
        f"excess land: {[c.name for c in bottomed]}")


# ─── clock primitive ─────────────────────────────────────────────────

def test_opp_one_turn_damage_counts_untapping_board_plus_development(
        card_db):
    from ai.clock import opp_one_turn_damage
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    # Opp board: 2 + 2 power, one TAPPED (they untap → still counts).
    _mk(game, card_db, "Seasoned Pyromancer", 1, "battlefield")
    _mk(game, card_db, "Seasoned Pyromancer", 1, "battlefield",
        tapped=True)
    dmg = opp_one_turn_damage(game, 0)
    # 4 on-board + one average deployment (4/2 = 2) = 6
    assert dmg == 6


def test_opp_one_turn_damage_zero_on_empty_board(card_db):
    from ai.clock import opp_one_turn_damage
    game = GameState(rng=random.Random(0))
    assert opp_one_turn_damage(game, 0) == 0


# ─── pay-life guard: post-payment survival ───────────────────────────

def _runner(card_db):
    from engine.game_runner import GameRunner
    return GameRunner(card_db)


def _pay_life_board(game, card_db, my_life, opp_names):
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.players[0].life = my_life
    grisel = _mk(game, card_db, "Griselbrand", 0, "battlefield")
    for _ in range(10):
        _mk(game, card_db, "Swamp", 0, "library")
    for n in opp_names:
        _mk(game, card_db, n, 1, "battlefield")
    return grisel


def _strip_lifelink(creature):
    """GV2-6 pattern: Humility-like keyword strip via subclass property."""
    base = set(creature.template.keywords) - {__import__("engine.cards", fromlist=["Keyword"]).Keyword.LIFELINK}
    cls = type(creature)
    stripped_cls = type(cls.__name__ + "NoLL", (cls,),
                        {"keywords": property(
                            lambda self: base | self.temp_keywords)})
    creature.__class__ = stripped_cls
    return creature


def test_pay_life_stops_when_post_payment_life_within_opp_reach(card_db):
    """No lifelink: 20 life, opp one-turn damage ≈ 6 (4 board + 2
    development): first activation → 13 (>6, allowed); second would
    land on 6 (NOT > 6) → must stop at one.  The old pre-payment
    guard allowed two."""
    game = GameState(rng=random.Random(1))
    grisel = _pay_life_board(game, card_db, 20,
                             ["Seasoned Pyromancer", "Seasoned Pyromancer"])
    _strip_lifelink(grisel)
    r = _runner(card_db)
    r._activate_pay_life_draw(game, 0)
    pays = [l for l in game.log if "pay 7 life" in l]
    assert len(pays) == 1, f"expected exactly 1 activation, got {pays}"
    assert game.players[0].life == 13


def test_pay_life_lifelink_attacker_single_activation_under_hand_cap(card_db):
    """WITH lifelink at 20 life the payment is clearly survivable (the
    clock guard would permit a second draw), but the RC-4 MAX_HAND_SIZE
    cap (merged from main) bounds an empty-hand Griselbrand to a single
    draw-7 (0→7): the loop stops before a second activation that would
    only feed cleanup discards.  The lifelink survival offset itself is
    pinned by tests/test_griselbrand_lifelink_activation.py."""
    game = GameState(rng=random.Random(1))
    _pay_life_board(game, card_db, 20,
                    ["Seasoned Pyromancer", "Seasoned Pyromancer"])
    r = _runner(card_db)
    r._activate_pay_life_draw(game, 0)
    pays = [l for l in game.log if "pay 7 life" in l]
    assert len(pays) == 1, f"expected 1 activation under hand cap, got {pays}"


def test_pay_life_single_activation_capped_by_hand_size(card_db):
    """No lifelink, 40 life vs the same board: life alone would allow a
    second draw-7 (40→33→26 both clear the clock), but the RC-4
    MAX_HAND_SIZE cap stops after the first draw-7 fills the hand (0→7).
    Paying life for cards discarded at cleanup is pure loss (CR 514.1);
    the clock guard bounds death, the hand cap bounds waste."""
    game = GameState(rng=random.Random(1))
    grisel = _pay_life_board(game, card_db, 40,
                             ["Seasoned Pyromancer", "Seasoned Pyromancer"])
    _strip_lifelink(grisel)
    r = _runner(card_db)
    r._activate_pay_life_draw(game, 0)
    pays = [l for l in game.log if "pay 7 life" in l]
    assert len(pays) == 1
