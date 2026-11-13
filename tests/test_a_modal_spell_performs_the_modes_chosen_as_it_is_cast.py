"""A modal spell's controller chooses its modes as it is cast, as many as
its header allows; a mode whose targets cannot be chosen cannot be chosen,
and the spell requires only the targets of the modes chosen; on resolution
it performs exactly those modes (CR 601.2b, 601.2c, 700.2, 700.2a).

"Choose one or both -- ~ deals 3 damage to each creature; destroy target
land. Its controller may search ...": its controller may choose either
mode or both. Cast with no land target, the land mode cannot be chosen and
the sweep alone resolves.

The defects:
- modes were chosen on resolution, not on casting;
- "one or both" resolved as "both": the modal resolver's gate counted the
  range as two of two, so both synthesized abilities ran through the
  legacy description loop;
- "one or more" parsed as "one", and a tier's unpaid cost chose nothing;
- castability asked a modal spell for a target even when a mode needs
  none;
- the AI asked every modal spell with a targeted mode for a target and
  dropped it when it had none. Avengers Disassembled (Boros Ponza MB x3)
  was never cast, even facing three 2/2s.

Card names are fixture carriers: 394 pool instants and sorceries are
modal; 111 have both targeted and untargeted modes.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase

AVENGERS = "Avengers Disassembled"


def _game(active=0):
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = active
    g.turn_number = 7
    return g


def _put(game, card_db, name, zone, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _mountains(game, card_db, n=3):
    for _ in range(n):
        _put(game, card_db, "Mountain", "battlefield")


# ── Typed at load ──────────────────────────────────────────────────────

def test_the_choose_range_is_read_from_the_typed_header(card_db):
    from engine.modal_spell import choose_range
    assert choose_range(card_db.get_card(AVENGERS)) == (1, 2)
    assert choose_range(card_db.get_card("Collective Brutality"))[1] == 1, \
        "escalate's extra modes cost what the engine does not pay"
    assert choose_range(card_db.get_card("Casualties of War")) == (1, 5)
    assert choose_range(card_db.get_card("Abrade")) == (1, 1)
    assert choose_range(card_db.get_card("Kozilek's Command")) == (2, 2)


def test_the_legacy_and_typed_modes_line_up_across_the_pool(card_db):
    """Every modal instant or sorcery's mode clauses and its typed modes
    are the same bullets, in order: the range and targets one reads index
    the clauses the other resolves."""
    for t in card_db.cards.values():
        if not (t.is_modal and (t.is_instant or t.is_sorcery)):
            continue
        host = t.effects.spell(0)
        assert host is not None and len(host.modes) == len(t.modes), t.name


# ── CR 700.2a: a mode needing a target is choosable only with one ──────

def test_a_targeted_mode_without_a_chosen_target_cannot_be_chosen(card_db):
    from engine.modal_spell import legal_modes
    game = _game()
    spell = _put(game, card_db, AVENGERS, "hand")
    forest = _put(game, card_db, "Forest", "battlefield", 1)
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    assert legal_modes(game, spell, 0, []) == [0]
    assert legal_modes(game, spell, 0, [forest.instance_id]) == [0, 1]
    assert legal_modes(game, spell, 0, [bears.instance_id]) == [0], \
        "a creature is no target for the land mode"


def test_a_mode_targeting_only_a_player_is_always_choosable(card_db):
    from engine.modal_spell import legal_modes
    game = _game()
    spell = _put(game, card_db, "Kozilek's Command", "hand")
    assert 0 in legal_modes(game, spell, 0, [])


def test_a_mode_whose_additional_cost_is_unpaid_cannot_be_chosen(card_db):
    """Tiered (CR 702.183a): a tier is chosen by paying its cost; the
    engine pays none, so only the free tier may be chosen."""
    from engine.modal_spell import legal_modes
    game = _game()
    spell = _put(game, card_db, "Fire Magic", "hand")
    assert legal_modes(game, spell, 0, []) == [0]


def test_an_untargeted_mode_makes_the_spell_castable_without_targets(card_db):
    """CR 601.2c: a spell requires a mode's targets only if that mode is
    chosen. With no creature of power or toughness 1 or less and no
    sorcery on the stack, the token mode is still castable."""
    game = _game()
    for _ in range(3):
        _put(game, card_db, "Wastes", "battlefield")
    wail = _put(game, card_db, "Warping Wail", "hand")
    assert game.can_cast(0, wail)


# ── CR 601.2b: chosen as it is cast; performed exactly ─────────────────

def test_the_modes_are_chosen_as_the_spell_is_cast(card_db):
    game = _game()
    _mountains(game, card_db)
    for _ in range(3):
        _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    spell = _put(game, card_db, AVENGERS, "hand")
    assert game.cast_spell(0, spell, targets=[])
    assert game.stack.items[-1].modes_chosen == [0]


def test_one_or_both_performs_each_chosen_mode(card_db):
    """"Exile target artifact; exile target enchantment", one or both:
    aimed at one of each, both modes are chosen and performed."""
    game = _game()
    for _ in range(4):
        _put(game, card_db, "Plains", "battlefield")
    art = _put(game, card_db, "Ornithopter", "battlefield", 1)
    ench = _put(game, card_db, "Rest in Peace", "battlefield", 1)
    spell = _put(game, card_db, "Crush Contraband", "hand")
    assert game.cast_spell(0, spell, targets=[art.instance_id,
                                              ench.instance_id])
    assert game.stack.items[-1].modes_chosen == [0, 1]
    game.resolve_stack()
    assert art.zone == "exile" and ench.zone == "exile"


def test_a_mode_not_chosen_is_not_performed(card_db, monkeypatch):
    from ai import modal
    monkeypatch.setattr(modal, "select_modal_modes",
                        lambda *a, **k: [1])
    game = _game()
    _mountains(game, card_db)
    mine = _put(game, card_db, "Grizzly Bears", "battlefield", 0)
    art = _put(game, card_db, "Ornithopter", "battlefield", 1)
    end = _put(game, card_db, "Brotherhood's End", "hand")
    assert game.cast_spell(0, end, targets=[])
    game.resolve_stack()
    game.check_state_based_actions()
    assert art.zone == "graveyard", "the chosen mode is performed"
    assert mine.zone == "battlefield" and mine.damage_marked == 0, \
        "the mode not chosen is not"


def test_the_engine_holds_a_choice_to_the_printed_range(card_db, monkeypatch):
    """However many modes the chooser names, a "choose one" spell is
    cast with one legal mode."""
    from ai import modal
    monkeypatch.setattr(modal, "select_modal_modes",
                        lambda *a, **k: [0, 1])
    game = _game()
    _mountains(game, card_db)
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    end = _put(game, card_db, "Brotherhood's End", "hand")
    assert game.cast_spell(0, end, targets=[])
    assert len(game.stack.items[-1].modes_chosen) == 1


# ── The AI ─────────────────────────────────────────────────────────────

def _ai(deck="Boros Ponza"):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name=deck, rng=random.Random(0))


def test_the_ai_casts_a_modal_spell_for_its_untargeted_mode(card_db):
    """Facing three 2/2s and no land to aim at, the sweep alone is worth
    casting: no target is required for it."""
    game = _game()
    _mountains(game, card_db)
    for _ in range(3):
        _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    spell = _put(game, card_db, AVENGERS, "hand")
    _put(game, card_db, "Mountain", "library")
    decision = _ai().decide_main_phase(game)
    assert decision is not None and decision[0] == "cast_spell"
    assert decision[1] is spell and decision[2] == []


def test_a_mode_is_chosen_exactly_when_it_is_worth_something(card_db):
    """"One or more": every mode its controller values above nothing is
    chosen. Exiling all artifacts would take only the caster's own, so
    against creatures alone the creature mode is chosen alone; an
    opposing enchantment adds the enchantment mode."""
    from ai.modal import select_modal_modes
    game = _game()
    _put(game, card_db, "Ornithopter", "battlefield", 0)
    for _ in range(2):
        _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    spell = _put(game, card_db, "Farewell", "hand")
    assert select_modal_modes(game, spell, 0, []) == [1]
    _put(game, card_db, "Rest in Peace", "battlefield", 1)
    assert select_modal_modes(game, spell, 0, []) == [1, 2]


def test_the_ai_holds_a_modal_spell_whose_untargeted_modes_do_nothing(card_db):
    """With nothing to aim its targeted modes at, a modal spell is worth
    only its untargeted modes; an empty board sweep is worth nothing."""
    game = _game()
    _mountains(game, card_db)
    _put(game, card_db, AVENGERS, "hand")
    _put(game, card_db, "Mountain", "library")
    decision = _ai().decide_main_phase(game)
    assert decision is None or decision[0] != "cast_spell"


def test_a_modal_counter_counters_only_what_its_counter_mode_targets(card_db):
    """"Counter target sorcery spell" is the counter mode: a creature
    spell is no target for it, so the modal spell is no counter for a
    creature spell -- cast at one, only its token mode could be chosen."""
    from ai.response import ResponseDecider
    from engine.stack import StackItem, StackItemType
    game = _game()
    wail = _put(game, card_db, "Warping Wail", "hand", 1)

    def spell(name):
        c = _put(game, card_db, name, "hand")
        return StackItem(item_type=StackItemType.SPELL, source=c,
                         controller=0)
    assert not ResponseDecider._counter_can_target(wail, spell("Grizzly Bears"))
    assert ResponseDecider._counter_can_target(wail, spell("Rift Bolt"))


# ── Auditor (CR 700.2, 700.2a) ─────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_a_mode_performed_without_its_target(
        card_db, audit, monkeypatch):
    from engine import modal_spell
    monkeypatch.setattr(modal_spell, "legal_modes",       # the defect
                        lambda game, card, controller, targets:
                        list(range(len(card.template.modes))))
    from ai import modal
    monkeypatch.setattr(modal, "select_modal_modes",
                        lambda *a, **k: [0, 1])
    game = _game()
    _mountains(game, card_db)
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    spell = _put(game, card_db, AVENGERS, "hand")
    assert game.cast_spell(0, spell, targets=[])
    game.resolve_stack()
    assert "700.2a/modes_chosen" in [f["rule"] for f in audit.drain()]


def test_the_audit_is_silent_when_the_chosen_modes_are_legal(card_db, audit):
    game = _game()
    _mountains(game, card_db)
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    forest = _put(game, card_db, "Forest", "battlefield", 1)
    spell = _put(game, card_db, AVENGERS, "hand")
    assert game.cast_spell(0, spell, targets=[forest.instance_id])
    game.resolve_stack()
    assert "700.2a/modes_chosen" not in [f["rule"] for f in audit.drain()]
