"""A static restriction on when players may cast spells applies as printed
(CR 101.2, 117.1a, 307.1, 604.1).

The rule is read from the card's own text -- the effect grammar types each
printed shape into a STATIC CONTINUOUS PROHIBIT spec -- and enforced through
the one read path for rule effects (`engine/rules_query`), never from a
classifier tag:

* "Your opponents can't cast spells during your turn." -- PROHIBIT cast
  under the TURN condition `your_turn` (as "as long as it's your turn").
* "During your turn, your opponents can't cast spells or activate abilities
  of artifacts, creatures, or enchantments." -- the same, for casting and
  for activating those permanents' abilities.
* "Each opponent can cast spells only any time they could cast a sorcery."
  -- PROHIBIT cast_outside_sorcery_timing.
* "Players can cast spells only during their own turns." -- PROHIBIT
  cast_outside_own_turn.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from engine import rules_audit
from engine.cards import ActivationEffectKind, CardInstance
from engine.effect_model import ModKind, SelectorKind
from engine.effect_spec import ConditionKind, HostKind, Verb, iter_specs
from engine.game_state import GameState, Phase

REPO = Path(__file__).resolve().parent.parent


def _game(active=0, phase=Phase.MAIN1):
    game = GameState(rng=random.Random(0))
    game.current_phase = phase
    game.active_player = active
    return game


def _put(game, card_db, idx, name, zone="battlefield"):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        game.players[idx].battlefield.append(c)
    else:
        game.players[idx].hand.append(c)
    return c


def _bolt_in_hand(game, card_db, idx=1):
    _put(game, card_db, idx, "Mountain")
    return _put(game, card_db, idx, "Lightning Bolt", zone="hand")


def _static_specs(card_db, name):
    t = card_db.get_card(name)
    return [s for h in t.effects.front() if h.kind is HostKind.STATIC
            for s in iter_specs(h.specs) if s.verb is Verb.CONTINUOUS]


def _prohibit(spec):
    return spec.payload.kind is ModKind.PROHIBIT


# ── The grammar types each printed shape ───────────────────────────────

@pytest.mark.parametrize("name", ["Voice of Victory", "Dragonlord Dromoka",
                                  "Kutzil, Malamet Exemplar"])
def test_a_your_turn_cast_prohibition_is_typed_with_its_turn_condition(card_db, name):
    [spec] = [s for s in _static_specs(card_db, name) if _prohibit(s)]
    assert spec.payload.action == "cast"
    assert spec.subject.kind is SelectorKind.OPPONENTS
    assert (spec.condition.kind, spec.condition.pred) == (ConditionKind.TURN,
                                                          "your_turn")


@pytest.mark.parametrize("name", ["Grand Abolisher", "Myrel, Shield of Argive"])
def test_a_leading_during_your_turn_types_cast_and_activate_prohibitions(card_db, name):
    [spec] = [s for s in _static_specs(card_db, name) if _prohibit(s)]
    assert set(spec.payload.get("actions")) == {"cast", "activate"}
    assert set(spec.payload.get("sources")) == {"artifact", "creature",
                                                "enchantment"}
    assert spec.subject.kind is SelectorKind.OPPONENTS
    assert (spec.condition.kind, spec.condition.pred) == (ConditionKind.TURN,
                                                          "your_turn")


@pytest.mark.parametrize("name,action,who", [
    ("Teferi, Time Raveler", "cast_outside_sorcery_timing", SelectorKind.OPPONENTS),
    ("Teferi, Mage of Zhalfir", "cast_outside_sorcery_timing", SelectorKind.OPPONENTS),
    ("Dosan the Falling Leaf", "cast_outside_own_turn", SelectorKind.ALL_PLAYERS),
])
def test_a_cast_timing_restriction_is_typed_from_its_text(card_db, name, action, who):
    [spec] = [s for s in _static_specs(card_db, name) if _prohibit(s)]
    assert spec.payload.action == action
    assert spec.subject.kind is who
    assert spec.condition is None


def test_a_during_phrase_other_than_your_turn_stays_unmodelled():
    """Only "during your turn" is a TURN condition; any other "during ..."
    is refused rather than typed as a broader restriction."""
    from engine.effect_grammar import link
    from engine.effect_grammar.normalize import Facts
    hosts = link.parse_face_hosts(
        "Your opponents can't cast spells during combat.",
        Facts(type_class=frozenset({"creature"})))
    specs = [s for h in hosts for s in iter_specs(h.specs)]
    assert specs and all(s.verb is Verb.UNMODELLED for s in specs)


# ── The engine enforces what the text says ─────────────────────────────

def test_an_opponent_cannot_cast_during_the_turn_of_a_your_turn_restrictions_controller(card_db):
    game = _game(active=0)
    _put(game, card_db, 0, "Voice of Victory")
    bolt = _bolt_in_hand(game, card_db)
    assert not game.can_cast(1, bolt)
    game.active_player = 1
    assert game.can_cast(1, bolt)


def test_a_sorcery_timing_restriction_comes_from_the_text(card_db):
    """Teferi, Mage of Zhalfir carries no classifier tag; its text alone
    restricts its controller's opponents to sorcery timing."""
    game = _game(active=0)
    _put(game, card_db, 0, "Teferi, Mage of Zhalfir")
    bolt = _bolt_in_hand(game, card_db)
    assert not game.can_cast(1, bolt)            # not their turn
    game.active_player = 1
    assert game.can_cast(1, bolt)                # their main phase, empty stack
    game.current_phase = Phase.BEGIN_COMBAT
    assert not game.can_cast(1, bolt)            # not a main phase


def test_a_classifier_tag_alone_never_restricts_casting(card_db, monkeypatch):
    """A tag with no matching printed restriction changes nothing."""
    import ai.oracle_classifier as oc
    monkeypatch.setattr(oc, "tags_for",
                        lambda name: frozenset({oc.Tag.SORCERY_SPEED_LOCKOUT}))
    game = _game(active=0)
    _put(game, card_db, 0, "Memnite")
    bolt = _bolt_in_hand(game, card_db)
    assert game.can_cast(1, bolt)


def test_an_own_turn_only_restriction_stops_casting_on_another_players_turn(card_db):
    game = _game(active=0, phase=Phase.BEGIN_COMBAT)
    _put(game, card_db, 0, "Dosan the Falling Leaf")
    bolt = _bolt_in_hand(game, card_db)
    assert not game.can_cast(1, bolt)
    game.active_player = 1
    assert game.can_cast(1, bolt)                # their own turn, any step
    own = _bolt_in_hand(game, card_db, idx=0)
    assert not game.can_cast(0, own)             # it binds its controller too


def test_a_your_turn_activation_restriction_stops_an_opponents_creature_ability(card_db):
    from engine.activation import ActivationManager
    game = _game(active=0)
    _put(game, card_db, 0, "Grand Abolisher")
    pinger = _put(game, card_db, 1, "Prodigal Pyromancer")
    ability = next(a for a in pinger.template.activated_abilities
                   if a.effect_kind is ActivationEffectKind.DAMAGE_ANY_TARGET)
    assert not ActivationManager.can_activate(game, 1, pinger, ability)
    game.active_player = 1
    assert ActivationManager.can_activate(game, 1, pinger, ability)


def test_one_owner_decides_cast_timing():
    """The runner's instant windows ask the rule-effect read path; no second
    reader of a printed timing restriction exists."""
    for rel in ("engine/game_runner.py", "engine/cards.py",
                "engine/card_database.py", "engine/oracle_parser.py"):
        assert "limits_opponent_spell_timing" not in (REPO / rel).read_text(), rel
    assert "_sorcery_speed_lockout_set" not in (
        REPO / "engine/game_state.py").read_text()


# ── The auditor ────────────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def _violations():
    return {f["rule"] for f in rules_audit.drain() if f["kind"] == "violation"}


def test_the_cast_timing_audit_sees_a_spell_cast_against_a_printed_restriction(audit, card_db, monkeypatch):
    """CR 101.2, restated from the battlefield's typed statics: silent for a
    cast the restriction allows; it fires when the gate is broken back to
    ignoring the restriction."""
    from engine import rules_query
    from engine.cast_manager import CastManager
    game = _game(active=1)
    _put(game, card_db, 0, "Voice of Victory")
    bolt = _bolt_in_hand(game, card_db)
    assert CastManager.cast_spell(game, 1, bolt, [-1])
    assert "101.2/cast_timing" not in _violations()
    game.stack.items.clear()
    game.active_player = 0
    bolt2 = _bolt_in_hand(game, card_db)
    monkeypatch.setattr(rules_query, "cast_prohibited", lambda *a, **k: False)
    assert CastManager.cast_spell(game, 1, bolt2, [-1])
    assert "101.2/cast_timing" in _violations()
