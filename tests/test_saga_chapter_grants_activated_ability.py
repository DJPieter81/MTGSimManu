"""Saga chapters that GRANT an activated ability must not auto-execute it.

Rule (CR 714 + granted-ability semantics): a chapter whose effect reads
``This Saga gains "<cost>: <effect>"`` gives the permanent an activated
ability. The quoted effect happens only when its controller activates
the ability and pays the printed cost (mana + {T}). It is NOT a
do-something-now trigger.

Chapters that are plain one-shot effects (e.g. a final-chapter library
search) still auto-execute when their lore counter lands, and the saga
is sacrificed after its final chapter resolves.

Fidelity gap this pins: probe s60104 showed the Urza's-Saga-pattern
chapter II auto-creating the Construct token for free on the upkeep,
instead of granting the "{2}, {T}: Create ..." ability.

Exemplar oracle shape (Urza's Saga — the registered pool's instance of
the mechanic; the engine code under test is name-free and keys on the
chapter text shape only).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardType
from engine.game_state import GameState
from engine.oracle_parser import parse_saga_chapters, extract_granted_ability


SAGA_NAME = "Urza's Saga"  # exemplar card; engine logic stays name-free


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


def _put_in_library(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="library",
    )
    game.players[controller].library.append(card)
    return card


def _tokens(game, controller):
    return [c for c in game.players[controller].battlefield
            if "token" in c.template.tags]


# ─── Oracle-parser layer: chapter structure + grant detection ─────────


def test_saga_oracle_parses_into_numbered_chapters():
    oracle = (
        "(As this Saga enters and after your draw step, add a lore "
        "counter. Sacrifice after III.)\n"
        'I — This Saga gains "{T}: Add {C}."\n'
        'II — This Saga gains "{2}, {T}: Create a 0/0 colorless '
        "Construct artifact creature token with 'This token gets "
        "+1/+1 for each artifact you control.'\"\n"
        "III — Search your library for an artifact card with mana "
        "cost {0} or {1}, put it onto the battlefield, then shuffle."
    )
    chapters = parse_saga_chapters(oracle)
    assert set(chapters) == {1, 2, 3}
    assert chapters[1].startswith("This Saga gains")
    assert "Search your library" in chapters[3]


def test_grant_chapter_text_yields_quoted_activated_ability():
    text = ('This Saga gains "{2}, {T}: Create a 0/0 colorless '
            "Construct artifact creature token with 'This token gets "
            "+1/+1 for each artifact you control.'\"")
    granted = extract_granted_ability(text)
    assert granted is not None
    assert granted.startswith("{2}, {T}:")
    assert "token" in granted.lower()


def test_plain_effect_chapter_text_is_not_a_grant():
    text = ("Search your library for an artifact card with mana cost "
            "{0} or {1}, put it onto the battlefield, then shuffle.")
    assert extract_granted_ability(text) is None


def test_multi_chapter_roman_numeral_line_applies_to_each_chapter():
    oracle = (
        "I, II — Create a 1/1 white Soldier creature token.\n"
        "III — Draw a card."
    )
    chapters = parse_saga_chapters(oracle)
    assert chapters[1] == chapters[2]
    assert "Soldier" in chapters[1]
    assert "Draw" in chapters[3]


# ─── Engine layer: grant instead of auto-execute ──────────────────────


def test_ability_grant_chapter_does_not_auto_execute_effect(
        card_db, game_runner):
    """When the lore counter lands on a gains-"<cost>: <effect>" chapter,
    NO token is created; the permanent gains the quoted ability."""
    game = GameState(rng=random.Random(0))
    saga = _put_in_play(game, card_db, SAGA_NAME, controller=0)
    saga.other_counters["lore"] = 1  # chapter II fires on next upkeep tick

    game_runner._process_saga_chapters(game, active=0)

    assert saga.other_counters["lore"] == 2
    assert _tokens(game, 0) == [], (
        "chapter II is an ability GRANT — the token must not be "
        "auto-created on the upkeep trigger")
    assert any(":" in g and "{t}" in g.lower()
               for g in saga.granted_abilities), (
        "the saga must gain the quoted activated ability")


def test_granted_ability_activation_pays_printed_cost_and_creates_token(
        card_db, game_runner):
    """Activating the granted "{2}, {T}: Create ..." ability taps the
    source, taps two lands for the generic cost, and creates the token
    with its printed properties (artifact creature, dynamic P/T)."""
    game = GameState(rng=random.Random(0))
    saga = _put_in_play(game, card_db, SAGA_NAME, controller=0)
    saga.other_counters["lore"] = 1
    pay1 = _put_in_play(game, card_db, "Island", controller=0)
    pay2 = _put_in_play(game, card_db, "Island", controller=0)

    game_runner._process_saga_chapters(game, active=0)  # grant ch II
    game_runner._activate_tap_abilities(game, active=0)

    toks = _tokens(game, 0)
    assert len(toks) == 1, "granted ability should fire exactly once"
    tok = toks[0]
    assert CardType.ARTIFACT in tok.template.card_types
    assert CardType.CREATURE in tok.template.card_types
    # 0/0 base + "gets +1/+1 for each artifact you control" (the token
    # itself is the only artifact) → 1/1.
    assert tok.power == 1 and tok.toughness == 1
    assert saga.tapped, "the {T} part of the cost must tap the source"
    assert pay1.tapped and pay2.tapped, (
        "the {2} part of the cost must actually be paid")


def test_granted_ability_not_activated_without_enough_mana(
        card_db, game_runner):
    """With fewer untapped lands than the generic cost, the ability
    cannot be activated — no token, source stays untapped."""
    game = GameState(rng=random.Random(0))
    saga = _put_in_play(game, card_db, SAGA_NAME, controller=0)
    saga.other_counters["lore"] = 1
    _put_in_play(game, card_db, "Island", controller=0, tapped=True)
    _put_in_play(game, card_db, "Island", controller=0, tapped=True)

    game_runner._process_saga_chapters(game, active=0)
    game_runner._activate_tap_abilities(game, active=0)

    assert _tokens(game, 0) == []
    assert not saga.tapped


def test_granted_ability_usable_again_on_later_turns_while_saga_remains(
        card_db, game_runner):
    """The grant is persistent: after untapping, the ability can be
    activated again on a later turn while the saga is still on the
    battlefield."""
    game = GameState(rng=random.Random(0))
    saga = _put_in_play(game, card_db, SAGA_NAME, controller=0)
    saga.other_counters["lore"] = 1
    lands = [_put_in_play(game, card_db, "Island", controller=0)
             for _ in range(2)]

    game_runner._process_saga_chapters(game, active=0)
    game_runner._activate_tap_abilities(game, active=0)
    assert len(_tokens(game, 0)) == 1

    # Next turn: untap everything, activate again.
    saga.tapped = False
    for land in lands:
        land.tapped = False
    game_runner._activate_tap_abilities(game, active=0)
    assert len(_tokens(game, 0)) == 2


def test_plain_effect_final_chapter_still_auto_executes_and_sacrifices(
        card_db, game_runner):
    """A chapter that is a plain one-shot effect (final-chapter library
    search) still auto-executes: the artifact is put onto the
    battlefield and the saga is sacrificed after its final chapter.
    No free token is created by the final chapter."""
    game = GameState(rng=random.Random(0))
    saga = _put_in_play(game, card_db, SAGA_NAME, controller=0)
    saga.other_counters["lore"] = 2  # chapter III fires on next tick
    drum = _put_in_library(game, card_db, "Springleaf Drum", controller=0)

    game_runner._process_saga_chapters(game, active=0)

    assert drum.zone == "battlefield", (
        "final-chapter search is a plain effect — it must auto-execute")
    assert saga.zone == "graveyard", (
        "the saga is sacrificed after its final chapter resolves")
    assert _tokens(game, 0) == [], (
        "the final chapter of this oracle shape creates no token — the "
        "old code wrongly auto-created a second free Construct here")


def test_granted_abilities_cleared_when_saga_leaves_battlefield(
        card_db, game_runner):
    """Sacrificing the saga removes its granted abilities — they must
    not leak to the graveyard instance."""
    game = GameState(rng=random.Random(0))
    saga = _put_in_play(game, card_db, SAGA_NAME, controller=0)
    saga.other_counters["lore"] = 1
    game_runner._process_saga_chapters(game, active=0)  # grant ch II
    assert saga.granted_abilities

    game_runner._process_saga_chapters(game, active=0)  # ch III + sac
    assert saga.zone == "graveyard"
    assert saga.granted_abilities == []
