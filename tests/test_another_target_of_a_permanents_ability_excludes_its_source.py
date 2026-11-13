"""A permanent's ability that targets "another" <object> may not target its
own source (CR 115.1, 601.2c, 603.3d).

"Whenever this creature attacks, another target creature you control gets
+2/+0 and gains trample until end of turn": the target is a creature other
than the one whose ability it is. With no such creature the ability has no
legal target and is removed from the stack (CR 603.3d).

"Another" is read against the source only where the ability names one
target. Where it names two ("target creature you control fights another
target creature"), "another" sets the second target apart from the first,
which no requirement carries yet: those hosts stay refused, with their
residue.

The defect: the target leaf read "another" / "other" and kept it only as
the residue `target.exclude_source`, so every such host was refused by
every carrier and stayed on its legacy pass. "Up to two other target
creatures" also lost its count (the count reader stopped at "other").

Class: 233 single-target abilities of permanents print "another" or
"other" before "target" (159 triggered, 67 activated, 4 loyalty, 3
chapter). Registered: Oliphaunt, Phelia, Exuberant Shepherd, Solitude,
Formidable Speaker. Card names are fixture carriers.
"""
from __future__ import annotations

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


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.DECLARE_ATTACKERS
    game.turn_number = 5
    return game


def _attack(game, attackers):
    from engine.combat_manager import CombatManager
    CombatManager().declare_attackers(game, list(attackers), 0)


def _requirements(text):
    from engine.target_solver import parse_spans
    return [r for r, _s, _e in parse_spans(text)]


# ── Typed at load ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text, excludes, count", [
    ("Whenever this creature attacks, another target creature you control "
     "gets +2/+0 until end of turn.", True, (1, 1)),
    ("When this creature enters, exile up to one other target nonland "
     "permanent.", True, (0, 1)),
    ("When this creature enters, put a +1/+1 counter on each of up to two "
     "other target creatures.", True, (0, 2)),
    ("{1}, {T}: Untap another target permanent.", True, (1, 1)),
    ("Target creature gets +2/+0 until end of turn.", False, (1, 1)),
])
def test_another_is_typed_on_the_requirement_with_its_count(text, excludes,
                                                            count):
    (req,) = _requirements(text)
    assert req.excludes_source is excludes
    assert (req.count_min, req.count_max) == count


def test_another_beside_a_second_target_is_not_read_as_the_source():
    """With two targets "another" sets the second apart from the first."""
    reqs = _requirements("Target creature you control fights another "
                         "target creature.")
    assert [r.excludes_source for r in reqs] == [False, False]


def test_the_residue_stays_only_where_another_names_the_other_target(
        card_db):
    from engine.effect_spec import iter_specs

    def residue(name):
        return {code for face in card_db.get_card(name).effects.faces
                for h in face for s in iter_specs(h.specs)
                for code in s.residue}
    assert "target.exclude_source" not in residue("Oliphaunt")
    assert "target.exclude_source" in residue("Ulvenwald Tracker")


# ── The solver and the carrier ─────────────────────────────────────────

def test_the_source_is_no_legal_choice_for_another_target(card_db):
    from engine.target_solver import legal_slot_choices
    game = _game()
    olip = _add(game, card_db, "Oliphaunt", 0)
    bears = _add(game, card_db, "Grizzly Bears", 0)
    (h,) = [h for h in olip.template.effects.front()
            if h.trigger is not None and h.trigger.attack is not None]
    _players, permanents = legal_slot_choices(game, 0, h.targets[0],
                                              source=olip)
    assert permanents == [bears]


def test_an_attack_trigger_aims_another_target_at_another_creature(card_db):
    game = _game()
    olip = _add(game, card_db, "Oliphaunt", 0)
    bears = _add(game, card_db, "Grizzly Bears", 0)
    base_olip, base_bears = olip.power, bears.power
    _attack(game, [olip])
    assert bears.power == base_bears + 2
    assert olip.power == base_olip


def test_with_no_other_legal_target_the_trigger_is_removed(card_db):
    game = _game()
    olip = _add(game, card_db, "Oliphaunt", 0)
    base = olip.power
    _attack(game, [olip])
    assert olip.power == base


# ── Auditor (CR 115.1, 601.2c) ─────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_another_target_that_is_its_source(card_db, audit,
                                                             monkeypatch):
    from engine import target_solver
    monkeypatch.setattr(target_solver, "excluded_as_source",
                        lambda req, card, source: False)       # the defect
    game = _game()
    olip = _add(game, card_db, "Oliphaunt", 0)
    _attack(game, [olip])
    assert "115.1/another_target_not_source" in _rules(audit)


def test_the_audit_is_silent_when_another_target_is_another(card_db, audit):
    game = _game()
    olip = _add(game, card_db, "Oliphaunt", 0)
    _add(game, card_db, "Grizzly Bears", 0)
    _attack(game, [olip])
    assert "115.1/another_target_not_source" not in _rules(audit)
