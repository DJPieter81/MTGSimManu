"""A resolving modal spell performs each chosen mode's instructions (CR
700.2, 608.2c): a chosen mode is performed by the owner that performs that
shape on a whole spell. An amount "equal to the number of <permanents>
you control" is that number when the spell resolves (CR 608.2h).

"Choose one -- ~ deals 3 damage to target creature; destroy target
artifact": cast at a creature, its controller chose the damage mode, and
the creature is dealt 3 damage. "~ deals damage equal to twice the number
of creatures you control to target creature": with three creatures, 6.

The defects:
- the modal path resolved each chosen mode through the clause handlers
  only; no handler performs an aimed-damage mode, so it resolved to
  nothing (Abrade, Affinity SB; Thraben Charm, Boros Energy MB x2);
- the damage executor refused any target requirement in a mode group, so
  even a chosen mode's own slot could not be bound;
- no amount owner evaluated a count of permanents, so 15 whole spells
  dealing damage "equal to the number of lands / creatures / Mountains
  you control" dealt nothing;
- the modal path ran before the card-effect registry, so Thraben Charm's
  name-keyed handler (which wrote damage and moved graveyards outside
  their owners) never ran in a real cast;
- a chosen mode that resolved to nothing left no trace.

Card names are fixture carriers: 28 pool modes are aimed-damage modes the
damage family runs; 67 pool cards print a count amount.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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


def _lands(game, card_db, name, n=3, idx=0):
    for _ in range(n):
        _put(game, card_db, name, "battlefield", idx)


def _cast(game, card_db, name, targets, idx=0):
    spell = _put(game, card_db, name, "hand", idx)
    assert game.cast_spell(idx, spell, targets=list(targets))
    game.resolve_stack()
    game.check_state_based_actions()
    return spell


# ── An aimed-damage mode ───────────────────────────────────────────────

def test_a_chosen_damage_mode_deals_its_damage_to_its_target(card_db):
    game = _game()
    _lands(game, card_db, "Mountain", 2)
    victim = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast(game, card_db, "Abrade", [victim.instance_id])
    assert victim.zone == "graveyard"


def test_a_chosen_mode_s_target_is_rechecked_on_resolution(card_db):
    """CR 608.2b: the mode's target, gone before it resolves, is dealt
    nothing, and nothing else is."""
    game = _game()
    _lands(game, card_db, "Mountain", 2)
    victim = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    other = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    spell = _put(game, card_db, "Abrade", "hand")
    assert game.cast_spell(0, spell, targets=[victim.instance_id])
    game.zone_mgr.move_card(game, victim, "battlefield", "hand")
    game.resolve_stack()
    assert other.damage_marked == 0


# ── Amounts equal to a count ───────────────────────────────────────────

def test_damage_equal_to_twice_a_count_is_twice_that_count(card_db):
    game = _game()
    _lands(game, card_db, "Plains", 2)
    for _ in range(3):
        _put(game, card_db, "Ornithopter", "battlefield", 0)
    big = _put(game, card_db, "Craw Wurm", "battlefield", 1)   # 6/4
    _cast(game, card_db, "Thraben Charm", [big.instance_id])
    assert big.zone == "graveyard", "2 x 3 creatures = 6 damage"


def test_a_count_is_taken_when_the_spell_resolves(card_db):
    """CR 608.2h: with one creature, twice the count is 2."""
    game = _game()
    _lands(game, card_db, "Plains", 2)
    _put(game, card_db, "Ornithopter", "battlefield", 0)
    target = _put(game, card_db, "Hill Giant", "battlefield", 1)   # 3/3
    _cast(game, card_db, "Thraben Charm", [target.instance_id])
    assert target.zone == "battlefield" and target.damage_marked == 2


def test_a_whole_spell_deals_damage_equal_to_the_lands_its_caster_controls(
        card_db):
    game = _game()
    _lands(game, card_db, "Mountain", 5)
    target = _put(game, card_db, "Colossal Dreadmaw", "battlefield", 1)
    _cast(game, card_db, "Stonefury", [target.instance_id])
    assert target.damage_marked == 5, "five lands: 5 damage"


def test_a_count_of_a_land_subtype_counts_that_subtype(card_db):
    game = _game()
    _lands(game, card_db, "Mountain", 2)
    _lands(game, card_db, "Plains", 2)
    target = _put(game, card_db, "Hill Giant", "battlefield", 1)
    _cast(game, card_db, "Seismic Strike", [target.instance_id])
    assert target.damage_marked == 2, "two Mountains, not four lands"


def test_count_amounts_are_typed_and_evaluated_by_the_one_owner(card_db):
    from engine import effect_conditions as ec
    from engine.effect_spec import iter_specs
    mode = card_db.get_card("Thraben Charm").effects.spell(0).modes[0]
    amount = next(iter_specs(mode.specs)).amount
    assert ec.amount_supported(amount)
    game = _game()
    for _ in range(2):
        _put(game, card_db, "Ornithopter", "battlefield", 0)
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    assert ec.amount_value(game, 0, amount) == 4


# ── The modes' mass shapes, typed at load ──────────────────────────────

def test_a_mode_s_mass_shape_is_typed_at_load(card_db):
    avengers = card_db.get_card("Avengers Disassembled").modes
    assert avengers[0]["mass"] == {"kind": "damage", "amount": 3,
                                   "planeswalkers": False, "scope": "all"}
    assert avengers[1]["mass"] is None
    end = card_db.get_card("Brotherhood's End").modes
    assert end[0]["mass"]["planeswalkers"]
    assert end[1]["mass"] == {"kind": "destroy", "type": "artifact",
                              "max_mv": 3, "scope": "all"}
    assert card_db.get_card("Abrade").modes[0]["mass"] is None


# ── The registry no longer holds a modal spell's modes ────────────────

def test_no_card_name_handler_resolves_a_modal_spells_modes(card_db):
    from engine import modal_spell
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    held = [t.name for t in card_db.cards.values()
            if modal_spell.in_scope(t)
            and EFFECT_REGISTRY.has_handler(t.name,
                                            EffectTiming.SPELL_RESOLVE)]
    assert "Thraben Charm" not in held


# ── A chosen mode no owner performs is recorded ───────────────────────

def test_a_chosen_mode_that_resolves_to_nothing_is_recorded(card_db):
    from engine import effect_diagnostics
    effect_diagnostics.reset()
    game = _game()
    _lands(game, card_db, "Swamp", 2)
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast(game, card_db, "Sheoldred's Edict", [])
    assert ("Sheoldred's Edict", "mode") in \
        effect_diagnostics.unhandled_effects()
    effect_diagnostics.reset()


# ── Auditor (CR 120.3) ─────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_a_damage_mode_that_dealt_no_damage(
        card_db, audit, monkeypatch):
    from engine import modal_spell
    monkeypatch.setattr(modal_spell, "resolve_mode",          # the defect
                        lambda *a, **k: False)
    game = _game()
    _lands(game, card_db, "Mountain", 2)
    victim = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast(game, card_db, "Abrade", [victim.instance_id])
    assert "120.3/chosen_mode_damage" in [f["rule"] for f in audit.drain()]


def test_the_audit_is_silent_when_the_damage_mode_deals_its_damage(
        card_db, audit):
    game = _game()
    _lands(game, card_db, "Mountain", 2)
    victim = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast(game, card_db, "Abrade", [victim.instance_id])
    assert "120.3/chosen_mode_damage" not in [f["rule"] for f in audit.drain()]
