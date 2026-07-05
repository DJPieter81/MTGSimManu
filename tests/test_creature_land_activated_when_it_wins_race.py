"""Track H (2026-07-05 calibration wave) — activated win-condition
lines enter main-phase scoring.

Mechanic under test: a land with an activated animate ability
("{cost}: … this land becomes an N/M … creature … until end of turn.
It's still a land.") is a win condition the AI must ENUMERATE as a
Play candidate in the main-phase decision and ACTIVATE when the clock
math says attacking wins the race.

Rules, phrased on the mechanic (no card names):

1. **Animation enumerated when the attack wins the race** — with the
   activation affordable and the resulting clock beating the
   opponent's clock, the main-phase decision returns the
   ``activate_ability`` play even with an empty hand.
2. **Animation withheld when the race is lost** — when the opponent's
   clock is faster than the post-animation clock, the line is not
   enumerated (mana stays available for defense).
3. **Summoning sickness respected** — a land that entered the
   battlefield this turn cannot profitably animate (it could not
   attack), so the line is not enumerated.
4. **Engine executes the activation as a rules action** — cost paid by
   tapping other lands, the permanent joins the combat class (appears
   in ``player.creatures``, has the printed P/T, ``can_attack``), and
   the effect wears off at cleanup.

Layering (per CLAUDE.md): the CHOICE lives in
``ai/activation_ev.py`` + ``ai/ev_player.py``'s ACTIVATION region;
``engine`` only validates and executes (``GameState.animate_land``).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState, Phase


def _land_template(name: str, *, oracle_text: str = "",
                   produces_mana=None) -> CardTemplate:
    return CardTemplate(
        name=name,
        card_types=[CardType.LAND],
        mana_cost=ManaCost(),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(),
        produces_mana=list(produces_mana or ["C"]),
        enters_tapped=False,
        oracle_text=oracle_text,
        tags=set(),
    )


def _creature_template(name: str, power: int, toughness: int) -> CardTemplate:
    return CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=power),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )


# Synthetic animate line — the modern oracle template shared by the
# whole creature-land class ("It's still a land").
_ANIMATE_ORACLE = (
    "{T}: Add {U}.\n"
    "{2}{U}: Until end of turn, this land becomes a 5/5 blue "
    "Elemental creature. It's still a land."
)
_ANIMATE_COST_SYMBOLS = 3  # {2} counts 2 generic + {U} = 3 total mana


def _instance(game: GameState, tmpl: CardTemplate, controller: int,
              zone: str, *, summoning_sick: bool = False) -> CardInstance:
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = summoning_sick
        card.entered_battlefield_this_turn = summoning_sick
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    else:
        game.players[controller].library.append(card)
    return card


def _fresh_game() -> GameState:
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "SyntheticControl"
    game.players[1].deck_name = "SyntheticOpponent"
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 7
    return game


def _board_with_animatable_land(game: GameState, *,
                                payer_lands: int,
                                land_summoning_sick: bool = False):
    """Battlefield: one animatable land + `payer_lands` plain lands."""
    manland = _instance(
        game, _land_template("Synthetic Animate Land",
                             oracle_text=_ANIMATE_ORACLE,
                             produces_mana=["U"]),
        0, "battlefield", summoning_sick=land_summoning_sick)
    for i in range(payer_lands):
        _instance(game, _land_template(f"Synthetic Plain Land {i}"),
                  0, "battlefield")
    return manland


def _ai(player_idx: int = 0):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx, "SyntheticControl",
                    rng=random.Random(0))


# ─────────────────────────────────────────────────────────────
# 1. Enumerated + chosen when attacking wins the race
# ─────────────────────────────────────────────────────────────

def test_animation_activated_when_clock_math_says_attack_wins_race():
    """Opponent at 8 life with only a 1/1 (20-turn clock against us).
    Animating gives a 5/5 — a 2-turn clock. The race is won by
    attacking; the main-phase decision must return the
    ``activate_ability`` play even though the hand is empty (there is
    no other legal play, and pre-fix the decision returned None)."""
    game = _fresh_game()
    manland = _board_with_animatable_land(
        game, payer_lands=_ANIMATE_COST_SYMBOLS)
    game.players[1].life = 8
    _instance(game, _creature_template("Synthetic Chip Attacker", 1, 1),
              1, "battlefield")

    decision = _ai().decide_main_phase(game)
    assert decision is not None, (
        "activated win-condition line must be enumerated in the "
        "main-phase decision when attacking wins the race"
    )
    action, card, _targets = decision
    assert action == "activate_ability", (
        f"expected the animate activation, got {action!r} "
        f"({getattr(card, 'name', card)!r})"
    )
    assert card is manland


# ─────────────────────────────────────────────────────────────
# 2. Withheld when the race is lost
# ─────────────────────────────────────────────────────────────

def test_animation_withheld_when_opponent_clock_is_faster():
    """Opponent has lethal-next-turn pressure (three 6/6s vs 6 life =
    1-turn clock) and enough toughness on board that our 5/5 cannot
    win the race. Animating wastes the mana we need for defense — the
    line must NOT be enumerated."""
    game = _fresh_game()
    _board_with_animatable_land(game, payer_lands=_ANIMATE_COST_SYMBOLS)
    game.players[0].life = 6
    game.players[1].life = 20
    for i in range(3):
        _instance(game, _creature_template(f"Synthetic Fatty {i}", 6, 6),
                  1, "battlefield")

    decision = _ai().decide_main_phase(game)
    assert decision is None or decision[0] != "activate_ability", (
        "animation must be withheld when the post-animation clock "
        "does not beat the opponent's clock"
    )


# ─────────────────────────────────────────────────────────────
# 3. Summoning sickness respected
# ─────────────────────────────────────────────────────────────

def test_animation_withheld_when_land_entered_this_turn():
    """A land that entered this turn would be a summoning-sick
    creature after animation — it cannot attack, so the activation
    buys nothing and must not be enumerated."""
    game = _fresh_game()
    _board_with_animatable_land(
        game, payer_lands=_ANIMATE_COST_SYMBOLS, land_summoning_sick=True)
    game.players[1].life = 8
    _instance(game, _creature_template("Synthetic Chip Attacker", 1, 1),
              1, "battlefield")

    decision = _ai().decide_main_phase(game)
    assert decision is None or decision[0] != "activate_ability", (
        "a summoning-sick animated land cannot attack; the activation "
        "must not be enumerated"
    )


# ─────────────────────────────────────────────────────────────
# 4. Engine executes the activation as a rules action
# ─────────────────────────────────────────────────────────────

def test_engine_animation_joins_combat_class_and_wears_off():
    game = _fresh_game()
    manland = _board_with_animatable_land(
        game, payer_lands=_ANIMATE_COST_SYMBOLS)

    ok = game.animate_land(0, manland)
    assert ok, "engine must execute an affordable animate activation"

    me = game.players[0]
    # Cost paid: all payer lands tapped, the source stays untapped so
    # it can attack.
    tapped_payers = [l for l in me.lands
                     if l is not manland and l.tapped]
    assert len(tapped_payers) == _ANIMATE_COST_SYMBOLS
    assert not manland.tapped

    # Joins the combat class with printed P/T.
    assert manland in me.creatures
    assert manland.power == 5 and manland.toughness == 5
    assert manland.can_attack

    # Wears off at cleanup ("until end of turn").
    manland.cleanup_damage()
    assert manland not in me.creatures
    assert manland.power == 0


def test_engine_rejects_unaffordable_animation():
    game = _fresh_game()
    manland = _board_with_animatable_land(
        game, payer_lands=_ANIMATE_COST_SYMBOLS - 1)

    ok = game.animate_land(0, manland)
    assert not ok, (
        "engine must reject the activation when the other untapped "
        "lands cannot pay the printed cost"
    )
    assert manland not in game.players[0].creatures
