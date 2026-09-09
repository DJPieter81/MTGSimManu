"""An X-bound or converge-bound removal effect reaches only what its bound
allows — at resolution AND in the AI's choice of target (CR 601.2b/c).

`parse_targeted_removal` types the plain "exile target <type> with mana
value X or less" shape, and the engine's cast-time legality, the AI's
target ceiling (`affordable_x`) and the resolver's bound all read it.  Two
wrappers hid the bound from all three:

1. **Modal** ("Choose two — … • Exile target creature with mana value X or
   less …", Kozilek's Command): the typed field is None for the whole card,
   the modal resolver skipped the card (it gated on synthesized abilities,
   not parsed modes), and the exile resolved with NO mana-value bound —
   Command at X=0 exiled a mana-value-1 creature on turn 2 (Broodscale vs
   Azorius Blink, seed 50000) and at X=1 a mana-value-2 one (vs WST).  Its
   second chosen mode never resolved at all.
2. **Converge** ("Exile target nonland permanent if its mana value is less
   than or equal to the number of colors of mana spent", Prismatic Ending):
   the AI's target choice ignored the reachable mana value the cast-time
   picker already computes, aimed at an unreachable permanent, and the
   spell whiffed (two Endings for four mana and no effect, same replay).

Rules pinned here: a mode's bound is typed once and honoured at resolution;
the AI never targets above the bound it can pay for (X-bound modal mode, or
converge); a spell with no reachable target offers no target, so it is not
cast.  Class: every modal removal mode and converge removal (~100 "mana
value ≤ N/X/colours" shapes).  Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def _resolve_all(game):
    while not game.stack.is_empty:
        game.resolve_stack()
        game.check_state_based_actions()


# ─── Modal: the mode's bound is typed once and honoured at resolution ──


def test_a_modal_mode_carries_its_typed_removal_bound(card_db):
    tmpl = card_db.get_card("Kozilek's Command")
    bounds = [m.get("removal") for m in tmpl.modes]
    assert any(r and r.get("mv") == "x" and r.get("action") == "exile" for r in bounds), (
        f"the 'exile target creature with mana value X or less' mode must be "
        f"typed like a plain X-bound removal spell; modes={tmpl.modes}")


def test_a_modal_x_bound_exile_cannot_exile_above_x_at_resolution(card_db):
    # One Eldrazi Temple = exactly {C}{C}: X is 0.
    game = _game()
    _add(game, card_db, "Eldrazi Temple", 0, "battlefield")
    cmd = _add(game, card_db, "Kozilek's Command", 0, "hand")
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")   # MV 2
    assert CastManager.cast_spell(game, 0, cmd, [bears.instance_id])
    _resolve_all(game)
    assert bears in game.players[1].battlefield, (
        "X=0 cannot exile a mana-value-2 creature — the mode's bound was ignored")

    # Two Temples = {C}{C} + X=2: now it can.
    game = _game()
    for _ in range(2):
        _add(game, card_db, "Eldrazi Temple", 0, "battlefield")
    cmd = _add(game, card_db, "Kozilek's Command", 0, "hand")
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    assert CastManager.cast_spell(game, 0, cmd, [bears.instance_id])
    _resolve_all(game)
    assert bears not in game.players[1].battlefield


# ─── The AI never targets above the bound it can pay for ─────────────


def _ai(idx, deck):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=idx, deck_name=deck, rng=random.Random(0))


def test_ai_target_choice_respects_the_x_ceiling_of_a_modal_mode(card_db):
    # Temple + Mountain = 3 mana: {C}{C} + X=1.
    game = _game()
    _add(game, card_db, "Eldrazi Temple", 0, "battlefield")
    _add(game, card_db, "Mountain", 0, "battlefield")
    cmd = _add(game, card_db, "Kozilek's Command", 0, "hand")
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")   # MV 2, out of reach
    memnite = _add(game, card_db, "Memnite", 1, "battlefield")       # MV 0, in reach
    ai = _ai(0, "Broodscale Bloodchief")
    assert ai._choose_targets(game, cmd) == [memnite.instance_id], (
        "with X=1 affordable, the only legal exile target is the mana-value-0 creature")

    game.players[1].battlefield.remove(memnite)
    assert ai._choose_targets(game, cmd) == [], (
        "nothing reachable at the affordable X → no target, so the spell is not cast")


def test_converge_target_choice_respects_the_reachable_mana_value(card_db):
    # Two Plains: one colour of mana can be spent → reaches mana value ≤ 1.
    game = _game()
    for _ in range(2):
        _add(game, card_db, "Plains", 0, "battlefield")
    ending = _add(game, card_db, "Prismatic Ending", 0, "hand")
    bears = _add(game, card_db, "Grizzly Bears", 1, "battlefield")   # MV 2, unreachable
    ai = _ai(0, "Azorius Control")
    assert ai._choose_targets(game, ending) == [], (
        "a converge removal must not be aimed at a permanent above the "
        "colours this manabase can spend — that cast is a guaranteed whiff")

    memnite = _add(game, card_db, "Memnite", 1, "battlefield")       # MV 0
    assert ai._choose_targets(game, ending) == [memnite.instance_id]
