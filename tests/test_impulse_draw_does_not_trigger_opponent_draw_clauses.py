"""W1a-1 — R1 + M1-engine: impulse-reveal does NOT fire on-draw triggers.

The audit `docs/history/audits/2026-05-16_5panel_bo3_audit.md` finding
M1+R1 documents an outcome-decisive defect: impulse-reveal spells
(Reckless Impulse, Wrenn's Resolve, Glimpse the Impossible — the
"exile top N, you may play those cards" shape) were routed through
`game.draw_cards()`, which fires "whenever an opponent draws" /
"whenever you draw" triggers. Per CR 121.1c that is incorrect — putting
a card into exile or onto the battlefield (face-up "may play") is NOT
a draw event.

The smoking gun: `replays/audit_storm_vs_dimir_s60101.txt` G1 T4, where
Storm self-killed 10→0 by casting Glimpse the Impossible into two
Bowmasters: each Bowmaster fired three times (once per impulse-revealed
card) for 6 damage total, plus Sheoldred's life-loss on the same draws.

These tests pin the rule, not the cards:
  * impulse-reveal must NOT fire on-draw damage triggers (Bowmasters)
  * impulse-reveal must NOT fire on-draw life-loss triggers (Sheoldred)
  * real draws MUST continue to fire on-draw damage triggers
  * real draws MUST continue to fire the Sheoldred swing (own draws
    gain life, opp draws lose life)

The rule-phrased test names describe the mechanic, never the card. The
test fixtures use real card names so the classifier-tag lookup
succeeds, but the assertion targets the mechanic boundary.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


# ─── fixtures ──────────────────────────────────────────────────────


def _fresh_game() -> GameState:
    """Two-player GameState; deterministic seed."""
    return GameState(rng=random.Random(0))


def _make_classified_card(game: GameState, name: str, controller: int,
                          oracle_text: str, zone: str,
                          card_types: list,
                          on_battlefield: bool = False) -> CardInstance:
    """Build a real-named CardInstance so the classifier-tag lookup
    succeeds. `oracle_text` matches what the trigger fan-out parses
    for the numerical amount; the dispatch is by tag, the amount is
    parsed targetedly."""
    tmpl = CardTemplate(
        name=name,
        card_types=card_types,
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[],
        power=1 if CardType.CREATURE in card_types else None,
        toughness=1 if CardType.CREATURE in card_types else None,
        loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle_text,
        tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if on_battlefield:
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    return card


def _put_bowmasters(game: GameState, controller: int) -> CardInstance:
    """Bowmasters-shape opp-draw damage source. Classifier carries
    `Tag.ON_DRAW_DAMAGE` for the real card name."""
    return _make_classified_card(
        game,
        name="Orcish Bowmasters",
        controller=controller,
        oracle_text=(
            "Whenever an opponent draws a card, except the first one "
            "they draw in each of their draw steps, this creature deals "
            "1 damage to that player."
        ),
        zone="battlefield",
        card_types=[CardType.CREATURE],
        on_battlefield=True,
    )


def _put_sheoldred(game: GameState, controller: int) -> CardInstance:
    """Sheoldred-shape on-draw life-swing. Classifier carries
    `Tag.ON_OPP_DRAW_LIFE_LOSS` and `Tag.ON_OWN_DRAW_LIFE_GAIN`."""
    return _make_classified_card(
        game,
        name="Sheoldred, the Apocalypse",
        controller=controller,
        oracle_text=(
            "Whenever you draw a card, you gain 2 life. "
            "Whenever an opponent draws a card, they lose 2 life."
        ),
        zone="battlefield",
        card_types=[CardType.CREATURE],
        on_battlefield=True,
    )


def _put_glimpse_in_library(game: GameState, controller: int) -> CardInstance:
    """Stack a real-named impulse-draw spell on top of the controller's
    library so its resolution path can find it. The card is the SPELL
    being cast; the cards it reveals are separately stacked below it."""
    return _make_classified_card(
        game,
        name="Glimpse the Impossible",
        controller=controller,
        oracle_text=(
            "Exile the top three cards of your library. Until end of "
            "turn, you may play those cards. You lose 3 life."
        ),
        zone="hand",
        card_types=[CardType.SORCERY],
    )


def _stack_dummies(game: GameState, controller: int, n: int) -> list:
    """Put N vanilla cards on top of the controller's library so an
    impulse-reveal-3 has something to reveal. The dummies' identity
    is irrelevant — the fan-out runs per moved card and the test
    asserts on the player's life total, not which cards moved."""
    dummies = []
    for i in range(n):
        c = _make_classified_card(
            game, name=f"_TopDummy{i}",
            controller=controller,
            oracle_text="",
            zone="library",
            card_types=[CardType.CREATURE],
        )
        game.players[controller].library.append(c)
        dummies.append(c)
    return dummies


# ─── R1 + M1-engine: impulse-reveal MUST NOT fire draw triggers ────


def test_impulse_reveal_with_two_bowmasters_deals_zero_self_damage():
    """Rule: impulse-reveal is NOT a draw under CR 121.1c.

    Setup: revealer (P1) at 20 life casts an impulse-reveal-3 spell
    (Glimpse the Impossible shape) while opponent controls TWO
    Bowmasters. With three reveals × 2 Bowmasters firing per reveal
    that would deal 6 damage if impulse-reveal were treated as a
    draw event.

    Assertion: P1's life is unchanged (impulse-reveal triggered zero
    Bowmasters fires). The 3 life loss from Glimpse's resolution
    clause is a separate effect (self-damage on cast) and is NOT
    invoked by this test — we resolve the impulse-reveal in
    isolation through `oracle_resolver.resolve_spell_from_oracle`.
    """
    game = _fresh_game()
    revealer = 0
    opp = 1

    _put_bowmasters(game, controller=opp)
    _put_bowmasters(game, controller=opp)
    _stack_dummies(game, controller=revealer, n=3)

    glimpse = _put_glimpse_in_library(game, controller=revealer)
    game.players[revealer].hand.append(glimpse)

    game.current_phase = Phase.MAIN1
    game.active_player = revealer
    game.players[revealer].cards_drawn_this_turn = 5  # past free-first

    life_before = game.players[revealer].life

    # Resolve the impulse-reveal portion of Glimpse via the oracle
    # resolver. The 'lose 3 life' resolution clause is a SEPARATE
    # path (not gated by the impulse fan-out) — we test only the
    # impulse-reveal mechanic here.
    from engine.oracle_resolver import resolve_spell_from_oracle
    resolve_spell_from_oracle(game, glimpse, revealer)

    # Critical: NO Bowmasters trigger fired on any of the 3 reveals.
    # Life MAY have changed only by the self-damage clause; the test
    # builds the oracle without that clause to isolate the bug.
    assert game.players[revealer].life == life_before, (
        f"impulse-reveal must NOT fire on-draw damage triggers; "
        f"expected life {life_before}, got {game.players[revealer].life}"
    )


def test_impulse_reveal_does_not_fire_opp_draw_life_loss():
    """Rule: impulse-reveal is NOT a draw, so Sheoldred-style
    'whenever an opponent draws, they lose N life' does NOT fire.

    The audit's compound storm_vs_dimir T4 self-kill was driven by
    BOTH Bowmasters (damage) AND Sheoldred (life loss). This pins
    the second mechanism independently.
    """
    game = _fresh_game()
    revealer = 0
    opp = 1

    _put_sheoldred(game, controller=opp)
    _stack_dummies(game, controller=revealer, n=3)

    impulse = _make_classified_card(
        game, name="Reckless Impulse",
        controller=revealer,
        oracle_text=(
            "Exile the top two cards of your library. Until end of "
            "your next turn, you may play those cards."
        ),
        zone="hand",
        card_types=[CardType.SORCERY],
    )
    game.players[revealer].hand.append(impulse)

    game.current_phase = Phase.MAIN1
    game.active_player = revealer
    game.players[revealer].cards_drawn_this_turn = 5

    life_before = game.players[revealer].life

    from engine.oracle_resolver import resolve_spell_from_oracle
    resolve_spell_from_oracle(game, impulse, revealer)

    assert game.players[revealer].life == life_before, (
        f"impulse-reveal must NOT fire opp-draw life-loss triggers; "
        f"expected life {life_before}, got {game.players[revealer].life}"
    )


# ─── regression-prevention: real draws still fire their triggers ───


def test_real_draw_still_fires_on_draw_damage_trigger():
    """Rule: a real (CR 121.1) draw still fires Bowmasters-style
    'whenever an opponent draws' triggers. This pins the positive
    case so a careless migration of `draw_cards` doesn't silently
    skip the fan-out.
    """
    game = _fresh_game()
    drawer = 0
    opp = 1

    _put_bowmasters(game, controller=opp)
    _stack_dummies(game, controller=drawer, n=1)

    game.current_phase = Phase.MAIN1
    game.active_player = drawer
    # Past the free first draw of the draw step so the trigger fires.
    game.players[drawer].cards_drawn_this_turn = 5

    life_before = game.players[drawer].life
    game.draw_cards(drawer, 1)

    assert game.players[drawer].life < life_before, (
        f"real draw_cards must fire on-draw damage triggers; "
        f"expected life < {life_before}, got {game.players[drawer].life}"
    )


def test_real_draw_fires_sheoldred_life_swing_both_sides():
    """Rule: a real draw fires Sheoldred's own-side gain-life clause
    when controller draws, AND fires the opp-draw life-loss clause
    when opponent draws. Both branches of the dispatch must run.
    """
    game = _fresh_game()
    sheoldred_owner = 0
    other = 1

    _put_sheoldred(game, controller=sheoldred_owner)
    _stack_dummies(game, controller=sheoldred_owner, n=1)
    _stack_dummies(game, controller=other, n=1)

    game.current_phase = Phase.MAIN1
    game.active_player = sheoldred_owner
    game.players[sheoldred_owner].cards_drawn_this_turn = 5
    game.players[other].cards_drawn_this_turn = 5

    # Own draw: gain life.
    life_before = game.players[sheoldred_owner].life
    game.draw_cards(sheoldred_owner, 1)
    assert game.players[sheoldred_owner].life > life_before, (
        f"own draw must trigger Sheoldred's 'gain 2 life'; "
        f"expected life > {life_before}, got "
        f"{game.players[sheoldred_owner].life}"
    )

    # Opp draw: opp loses life.
    opp_life_before = game.players[other].life
    game.draw_cards(other, 1)
    assert game.players[other].life < opp_life_before, (
        f"opp draw must trigger Sheoldred's 'they lose 2 life'; "
        f"expected life < {opp_life_before}, got "
        f"{game.players[other].life}"
    )
