"""A permanent transformed to a CREATURE back face is classified and
valued as a creature — not a planeswalker, and not still its
front-face printed P/T.

Rule under test
----------------
`PlayerState.creatures`/`.planeswalkers` (engine/player_state.py)
hardcoded "transformed ⇒ became a planeswalker" — true only for the
one DFC shape (Ral, Monsoon Mage) the original transform code was
built against (docs/history/plans/TRANSFORM_FIX_PLAN.md). Any
creature-backed transform DFC (Fable of the Mirror-Breaker →
Reflection of Kiki-Jiki; 242 of 689 multi-face cards in the DB have a
creature back face) was:
  1. Removed from `.creatures` (excluded from combat, can't attack/
     block, invisible to combat-class code).
  2. Added to `.planeswalkers` (wrongly subject to the 704.5p
     zero-loyalty SBA — since a creature back face has no loyalty
     data, `loyalty_counters` stays at its default 0, so `<= 0` fires
     and the SBA destroys it the instant it transforms).

This is the confirmed root cause of the Fable → Kiki-Jiki
self-destruct-on-transform bug found in a Jeskai Blink vs Eldrazi Tron
Bo3 audit (`python run_meta.py --bo3 "Jeskai Blink" "Eldrazi Tron" -s
55502`).

Root cause: `engine/card_database.py` only captured back-face
type/P-T/subtype data when the back face was a Planeswalker
(`back_face_oracle`/`back_face_loyalty` only) — 97.2% of DFC back
faces were discarded at load. Fixed by capturing full back-face
characteristics for every multi-face card and adding
`CardInstance.effective_card_types`/`effective_is_creature`/
`effective_is_planeswalker` as the single accessor for "what type is
this permanent right now", consulted by `.creatures`/`.planeswalkers`
instead of each guessing independently.

Class size: every creature-backed (or land-backed, artifact-backed,
etc.) transform DFC in the card pool — 670 of 689 multi-face cards
had their back face discarded before this fix, not just Kiki-Jiki.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState


FABLE_NAME = "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki"
RAL_NAME = "Ral, Monsoon Mage // Ral, Leyline Prodigy"


def _put_on_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def test_transform_to_creature_back_face_is_a_creature_not_a_planeswalker(card_db):
    """Kiki-Jiki fixture: after transforming, the permanent must
    appear in .creatures, NOT .planeswalkers, and must report the
    back face's printed 2/2 — not be destroyed by the zero-loyalty SBA
    (which should never apply to it at all, since it has no loyalty)."""
    from engine.oracle_resolver import _transform_permanent

    game = GameState(rng=random.Random(0))
    fable = _put_on_battlefield(game, card_db, FABLE_NAME, controller=0)

    _transform_permanent(game, fable, controller=0)

    assert fable in game.players[0].creatures, (
        "transformed creature-backed permanent is missing from "
        ".creatures — combat/attack/block code can't see it"
    )
    assert fable not in game.players[0].planeswalkers, (
        "transformed creature-backed permanent is wrongly classified "
        "as a planeswalker — subject to the 704.5p zero-loyalty SBA, "
        "which destroys it the instant it transforms (the audited bug)"
    )
    assert fable.power == 2 and fable.toughness == 2, (
        f"expected back face's printed 2/2, got "
        f"{fable.power}/{fable.toughness} — still reading the front "
        f"face's (Saga, no P/T) printed stats"
    )
    # The zero-loyalty SBA must not apply — verify it survives an SBA pass.
    game.check_state_based_actions()
    assert fable in game.players[0].battlefield, (
        "transformed creature-backed permanent was destroyed by the "
        "704.5p zero-loyalty SBA, which should never apply to a "
        "non-planeswalker permanent"
    )


def test_transform_does_not_refire_front_faces_own_etb_handler(card_db):
    """Transforming must not fire the FRONT face's own registered ETB
    handler (EFFECT_REGISTRY keys on the literal card name, which is
    identical for both faces of a DFC — so a front-face ETB handler
    unconditionally re-fires on the exile-then-return that a transform
    like Fable Ch.III performs, even though a transform's 'return' is
    the BACK face entering, not the front face). Concretely: Fable's
    own Chapter I ETB handler ('create a 2/2 Goblin Shaman token')
    must NOT create a second, spurious token when Chapter III
    transforms Fable into Reflection of Kiki-Jiki — Kiki-Jiki has no
    ETB effect of its own.

    This is the second half of the audited Fable → Kiki-Jiki bug
    (the first half — misclassification as a planeswalker — is
    covered above)."""
    from engine.oracle_resolver import _transform_permanent

    game = GameState(rng=random.Random(0))
    fable = _put_on_battlefield(game, card_db, FABLE_NAME, controller=0)

    _transform_permanent(game, fable, controller=0)

    tokens = [c for c in game.players[0].battlefield if c.is_token]
    assert tokens == [], (
        f"transforming spawned {len(tokens)} unexpected token(s) "
        f"({[t.name for t in tokens]}) — the front face's own ETB "
        f"handler (Chapter I: create a Goblin token) fired on the "
        f"back face's transform-return"
    )


def test_transform_to_planeswalker_back_face_is_still_a_planeswalker(card_db):
    """Regression guard: the case the original transform code WAS
    built for (Ral, Monsoon Mage → a planeswalker back face) must
    keep working — this fix must not flip that classification."""
    from engine.oracle_resolver import _transform_permanent

    game = GameState(rng=random.Random(0))
    ral = _put_on_battlefield(game, card_db, RAL_NAME, controller=0)

    _transform_permanent(game, ral, controller=0)

    assert ral in game.players[0].planeswalkers, (
        "transformed planeswalker-backed permanent is missing from "
        ".planeswalkers — the previously-working case regressed"
    )
    assert ral not in game.players[0].creatures, (
        "transformed planeswalker-backed permanent is still counted "
        "as a creature"
    )
    assert ral.loyalty_counters > 0, (
        "transformed planeswalker's starting loyalty was not set"
    )
