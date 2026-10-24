"""A permanent's cast trigger is performed from its typed host (CR 601.2i,
603.2).

"Whenever <player> casts a <kind> spell, <effect>" triggers when that player
casts a spell of that kind (CR 601.2i), read from the spell as it is cast.
The ability is performed as printed (CR 608.2c).

The engine's rule:
- The grammar types the head from a closed table (`structure._cast_event`,
  `TriggerHead.cast`): who casts, and what kind of spell.
- The cast carrier (`effect_carrier.dispatch_cast_triggers`) performs a
  card's cast triggers when every one of them is in the cast family's
  closed shape (`effect_views.strict_cast_trigger`): a plain draw, or a
  rummage -- "you may discard a card. If you do, draw a card", whose
  discard goes through the discard funnel (`execute_discard`, CR 701.9a).
- A card the carrier takes is not read again by the legacy cast branches.

The defect: the legacy cast resolver read a few printed shapes and nothing
else.
- Artist's Talent's rummage (Ruby Storm x2) never happened.
- "Whenever you cast an instant or sorcery spell, draw a card" drew on every
  spell cast.

The AI's rule (`ai.resolution_choices.perform_optional_effect`): an optional
discard is performed when its "if you do" branch draws at least as many
cards as it discards.

Card names are fixture carriers: each is one member of its class.
"""
from __future__ import annotations

import dataclasses
import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(6):
        _add(game, card_db, "Mountain", 0)
        _add(game, card_db, "Island", 1)
    for p in (0, 1):
        for _ in range(6):
            _add(game, card_db, "Swamp", p, "library")
    return game


def _cast(game, card_db, name, controller=0, targets=None):
    spell = _add(game, card_db, name, controller, "hand")
    assert game.cast_spell(controller, spell, targets or [])
    return spell


# ── The grammar: a closed table of cast heads ─────────────────────────

@pytest.mark.parametrize("name, caster, kind", [
    ("Artist's Talent", "you", "noncreature"),
    ("Archmage of Runes", "you", "instant_or_sorcery"),
    ("Hazoret's Monument", "you", "creature"),
])
def test_a_cast_head_is_typed_by_caster_and_spell_kind(card_db, name, caster,
                                                       kind):
    from engine.effect_spec import EventHint
    (h,) = [h for h in card_db.get_card(name).effects.front()
            if h.trigger is not None
            and EventHint.SPELL_CAST in h.trigger.event_hints]
    assert (h.trigger.cast.caster, h.trigger.cast.spell) == (caster, kind)


@pytest.mark.parametrize("name", ["Ral, Monsoon Mage // Ral, Leyline Prodigy",
                                  "Cori-Steel Cutter"])
def test_a_head_with_a_rider_the_table_does_not_read_is_left_untyped(card_db,
                                                                    name):
    """"... during your turn" and "your second spell each turn"."""
    from engine.effect_spec import EventHint
    for h in card_db.get_card(name).effects.front():
        if h.trigger is not None and EventHint.SPELL_CAST in h.trigger.event_hints:
            assert h.trigger.cast is None


# ── The carrier ────────────────────────────────────────────────────────

def test_a_rummage_trigger_discards_and_draws_on_a_noncreature_spell(card_db):
    game = _game(card_db)
    _add(game, card_db, "Artist's Talent", 0)
    spare = _add(game, card_db, "Forest", 0, "hand")
    game.callbacks.choose_discard = lambda g, p, hand, self_discard: spare
    hand_before = len(game.players[0].hand)
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert spare.zone == "graveyard"
    # Bolt left the hand; the rummage traded the Forest for a draw
    assert len(game.players[0].hand) == hand_before


def test_a_creature_spell_does_not_trigger_a_noncreature_head(card_db):
    game = _game(card_db)
    _add(game, card_db, "Artist's Talent", 0)
    spare = _add(game, card_db, "Forest", 0, "hand")
    _cast(game, card_db, "Memnite")
    assert spare.zone == "hand"


def test_a_head_naming_you_does_not_trigger_on_an_opponents_cast(card_db):
    game = _game(card_db)
    _add(game, card_db, "Artist's Talent", 0)
    spare = _add(game, card_db, "Forest", 0, "hand")
    game.active_player = 1
    _cast(game, card_db, "Opt", controller=1)
    assert spare.zone == "hand"


def test_a_taken_card_is_not_read_again_by_the_legacy_cast_branches(card_db):
    """"Whenever you cast an instant or sorcery spell, draw a card": no draw
    on a creature spell, and exactly one on an instant."""
    game = _game(card_db)
    _add(game, card_db, "Archmage of Runes", 0)
    library = len(game.players[0].library)
    _cast(game, card_db, "Memnite")
    assert len(game.players[0].library) == library
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert len(game.players[0].library) == library - 1


# ── The AI: a rummage is performed when a draw likely improves the hand ─

def _rummage_spec(card_db):
    (h,) = [h for h in card_db.get_card("Artist's Talent").effects.front()
            if h.trigger is not None and h.trigger.cast is not None]
    return h.specs[0]


class _Ctx:
    def __init__(self, game, controller=0):
        self.game = game
        self.controller = controller


def _rummager(card_db, hand, library):
    """Player 0, six lands on the battlefield, holding `hand` over a
    library of `library`."""
    game = _game(card_db)
    game.players[0].library.clear()
    for name in hand:
        _add(game, card_db, name, 0, "hand")
    for name in library:
        _add(game, card_db, name, 0, "library")
    return game


def _performs(game, spec):
    from ai.resolution_choices import perform_optional_effect
    return perform_optional_effect(game, _Ctx(game), spec)


def test_the_ai_rummages_a_card_most_draws_improve_on(card_db):
    """Two lands in hand beside six on the battlefield: the self-discard
    ranking gives up an excess land first, and every card in the library
    ranks as more worth keeping."""
    game = _rummager(card_db, ["Forest", "Forest"], ["Opt"] * 6)
    assert _performs(game, _rummage_spec(card_db))


def test_the_ai_declines_a_rummage_most_draws_rank_below(card_db):
    """The hand's one card is a combo piece the ranking keeps; a land drawn
    into an empty hand ranks below it, so the draw more likely worsens
    the hand."""
    game = _rummager(card_db, ["Grapeshot"], ["Swamp"] * 6)
    assert not _performs(game, _rummage_spec(card_db))


def test_a_draw_the_ranking_cannot_tell_apart_counts_for_neither(card_db):
    game = _rummager(card_db, ["Opt"], ["Opt"] * 6)
    assert not _performs(game, _rummage_spec(card_db))


def test_a_rummage_compares_the_card_it_gives_up_not_the_whole_hand(card_db):
    """The excess land goes, and the combo piece beside it never enters the
    comparison."""
    game = _rummager(card_db, ["Grapeshot", "Forest", "Forest"],
                     ["Opt"] * 6)
    assert _performs(game, _rummage_spec(card_db))


def test_the_ai_declines_a_discard_that_draws_nothing_back(card_db):
    bare = dataclasses.replace(_rummage_spec(card_db), then=())
    assert not _performs(None, bare)


def test_the_ai_performs_a_discard_whose_branch_draws_more(card_db):
    from engine.effect_spec import Amount, AmountKind
    spec = _rummage_spec(card_db)
    (draw,) = spec.then
    two = Amount(kind=AmountKind.LITERAL, n=2)
    more = dataclasses.replace(
        spec, then=(dataclasses.replace(draw, amount=two),))
    assert _performs(None, more)


# ── The auditor (CR 701.9a) ───────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "701.9a/discarded"]


def test_the_audit_records_a_discard_that_left_the_card_in_hand(
        card_db, audit, monkeypatch):
    game = _game(card_db)
    _add(game, card_db, "Artist's Talent", 0)
    _add(game, card_db, "Forest", 0, "hand")
    monkeypatch.setattr(type(game), "discard_card",
                        lambda self, p, card, cause="": "hand")   # the defect
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert _findings(audit)


def test_the_audit_is_silent_on_a_discard_to_the_graveyard(card_db, audit):
    game = _game(card_db)
    _add(game, card_db, "Artist's Talent", 0)
    _add(game, card_db, "Forest", 0, "hand")
    _cast(game, card_db, "Lightning Bolt", targets=[-2])
    assert not _findings(audit)
