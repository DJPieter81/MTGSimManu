"""A spell or ability that targets is aimed only at legal targets, and a
spell with a required target is not cast without one (CR 601.2c,
608.2b, 702.11d) — one rule, owned by `engine/target_solver`, asked at
cast time with the X actually paid and again by every handler that
chooses a target on resolution.

Three ways the rule was broken (2026-09-12 replays):

1. The cast-time gate checked an X-bound requirement ("with mana value
   X or less") against the AFFORDABLE X ceiling, then the cast chose a
   smaller X (converge: the colours actually spent) and resolved into
   nothing — "Cast Prismatic Ending (W) (X=1)" against an MV≥2 board,
   WST v2 replay L468-474. The legality question must be asked at the X
   the cast pays; when the picker finds no target at any payable X the
   cast is refused, not resolved empty.
2. ETB handlers chose their target straight from the opponent's
   creatures with no `can_be_targeted` — Solitude exiled a Scion of
   Draco that was hexproof under Leyline of the Guildpact (Ponza replay
   L360-366). Every handler that picks a target asks the solver.
3. Removal whose mana-value condition is checked on resolution ("destroy
   target creature if it has mana value 2 or less") parsed to no bound at
   all, so the AI aimed it at a 7-drop and the spell resolved doing
   nothing (Dimir vs Tron replay L398-407) — legal, and a wasted card.
   The parser types the conditional bound (with its revolt raise) and
   the AI's target chooser honours it.

Card names are fixture carriers for the three oracle shapes; synthetic
templates carry the keywords.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def _put(game, card_db, name, controller, zone="battlefield"):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = False
        game.players[controller].battlefield.append(c)
    elif zone == "library":
        game.players[controller].library.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _creature(game, name, controller, power=2, toughness=2, cmc=3, keywords=()):
    tmpl = CardTemplate(
        name=name, card_types=[CardType.CREATURE], mana_cost=ManaCost(generic=cmc),
        supertypes=[], subtypes=[], power=power, toughness=toughness, loyalty=None,
        keywords=set(keywords), abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set())
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


# ── 1. the X a converge removal pays counts the colours already spent ──
def test_a_converge_removal_counts_the_colours_its_printed_pips_already_spent(card_db):
    """The printed pips are paid before X is chosen. With W (Fountain) +
    U + U up against an MV-2 creature, the reach is two colours (W already
    spent, U from an Island): X=2, the creature is exiled. The cast-time
    picker counted only the still-untapped sources — one colour after the
    Fountain paid {W} — chose X=0 and the spell resolved doing nothing."""
    game = _game()
    fountain = _put(game, card_db, "Hallowed Fountain", 0)
    _put(game, card_db, "Island", 0)
    _put(game, card_db, "Island", 0)
    ending = _put(game, card_db, "Prismatic Ending", 0, "hand")
    frog = _creature(game, "Two", 1, power=2, toughness=3, cmc=2)
    assert CastManager.can_cast(game, 0, ending)
    assert CastManager.cast_spell(game, 0, ending, [frog.instance_id])
    item = game.stack.top
    assert item.x_value == 2, f"X must reach the MV-2 target (paid X={item.x_value})"
    assert set(item.colors_spent) == {"W", "U"}
    while not game.stack.is_empty:
        game.resolve_stack()
    assert frog not in game.players[1].battlefield, "the reachable target is exiled"


# ── 2. resolution-time target choice goes through the solver ─────────
def test_an_etb_that_targets_cannot_choose_a_hexproof_creature(card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    game = _game()
    sol = _put(game, card_db, "Solitude", 0)
    shielded = _creature(game, "Shielded", 1, power=6, toughness=6,
                         keywords=(Keyword.HEXPROOF,))
    plain = _creature(game, "Plain", 1, power=2, toughness=2)
    assert EFFECT_REGISTRY.execute("Solitude", EffectTiming.ETB, game, sol, 0)
    assert shielded in game.players[1].battlefield, (
        "hexproof: the opponent's ETB cannot target it (CR 702.11d)")
    assert plain not in game.players[1].battlefield, "the legal target is exiled instead"

    game2 = _game()
    sol2 = _put(game2, card_db, "Solitude", 0)
    only = _creature(game2, "Shielded", 1, power=6, toughness=6,
                     keywords=(Keyword.HEXPROOF,))
    life = game2.players[1].life
    EFFECT_REGISTRY.execute("Solitude", EffectTiming.ETB, game2, sol2, 0)
    assert only in game2.players[1].battlefield and game2.players[1].life == life, (
        "with no legal target the ETB does nothing")


def test_every_resolution_time_target_pick_asks_the_solver():
    """Structural pin: no handler in engine/card_effects.py picks a
    target from the opponent's creatures without the solver's
    `can_be_targeted` in the same function body."""
    import ast, pathlib
    src = pathlib.Path("engine/card_effects.py").read_text()
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.get_source_segment(src, node) or ""
        picks = (".creatures" in body) and ("max(" in body or "for c in" in body)
        acts = any(k in body for k in ("_exile_permanent(", "_permanent_destroyed(",
                                        "deal_damage(", "damage_marked +="))
        solver = "can_be_targeted" in body or "legal_targets" in body
        # A choice the OPPONENT makes (sacrifice, "each opponent …") is not
        # targeting (CR 701.17); the handler says so in its own words.
        not_targeting = "Not targeting" in body
        if picks and acts and not solver and not not_targeting:
            offenders.append(node.name)
    assert not offenders, f"handlers choosing targets outside the solver: {offenders}"


# ── 3. conditional mana-value removal is typed and aimed accordingly ──
def test_a_resolution_time_mana_value_condition_is_typed_with_its_revolt_raise(card_db):
    push = card_db.get_card("Fatal Push")
    data = getattr(push, "removal_mv_condition", None) or {}
    assert data.get("mv") == 2, f"the 'if it has mana value 2 or less' condition is the bound: {data}"
    assert data.get("mv_if_permanent_left") == 4, "revolt raises it to 4"
    # The printed-restriction shape is a different field and stays typed there.
    assert card_db.get_card("Fatal Push").targeted_removal_data is None


def test_the_ai_does_not_aim_conditional_removal_where_its_condition_fails(card_db):
    from ai.ev_player import EVPlayer
    game = _game()
    for n in ("Swamp", "Swamp"):
        _put(game, card_db, n, 0)
    push = _put(game, card_db, "Fatal Push", 0, "hand")
    big = _creature(game, "Seven", 1, power=6, toughness=6, cmc=7)
    small = _creature(game, "Two", 1, power=2, toughness=2, cmc=2)
    ai = EVPlayer(player_idx=0, deck_name="Dimir Midrange")
    chosen = ai._choose_targets(game, push)
    assert chosen == [small.instance_id], (
        "the only target the resolution condition reaches is the MV-2 creature")
    game.players[1].battlefield.remove(small)
    assert ai._choose_targets(game, push) == [], (
        "with only an MV-7 creature the spell would resolve doing nothing — not aimed")
