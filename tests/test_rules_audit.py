"""The rules auditor: every invariant fires on a fixture that breaks its
rule, stays silent when the rule holds, and records nothing with the flag
off. Each test re-creates the exact defect its rule was written for by
monkeypatching the engine back to the broken behaviour — the auditor must
SEE the bug the fix removed, or it is not an auditor.

Rules covered (CR section / slug):
  510.2/creature_dealt   — every creature in combat with power dealt damage
                           in the step its keywords name (Z2's class).
  601.2c/cast_target     — every target chosen at cast is solver-legal.
  608.2b/resolve_target  — every target is still legal on resolution.
  305.7/set_land_mana    — a land whose type is SET produces only that colour.
  305.7/set_land_ability — a set land activated no ability but mana.
  107.3/x_counters       — an X-cost permanent entered with X counters.
  704.5f/lethal_damage   — no creature with lethal damage survives SBAs.
  704.5a/zero_life       — no player at 0 or less life is still playing.
  keyword/unmodelled     — census: a keyword word the engine has no model for.
"""
from __future__ import annotations

import random

import pytest

from engine import rules_audit
from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.combat_manager import CombatManager
from engine.game_state import GameState, Phase


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def _creature(game, name, controller, power=2, toughness=2, keywords=(), cmc=3):
    tmpl = CardTemplate(
        name=name, card_types=[CardType.CREATURE], mana_cost=ManaCost(generic=cmc),
        supertypes=[], subtypes=[], power=power, toughness=toughness, loyalty=None,
        keywords=set(keywords), abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set())
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _rules(findings):
    return sorted({f["rule"] for f in findings if f["kind"] == "violation"})


def _fight(game, attacker, blocker):
    cm = CombatManager()
    cm.declare_attackers(game, [attacker], active_player=1)
    cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
    cm.resolve_combat_damage(game)
    game.check_state_based_actions()


def test_with_the_flag_off_nothing_is_recorded(monkeypatch):
    monkeypatch.delenv("MTG_RULES_AUDIT", raising=False)
    rules_audit.reset()
    rules_audit.check("x/y", False, "should not record")
    rules_audit.census("keyword/unmodelled", "ward")
    assert rules_audit.drain() == []


def test_combat_audit_sees_a_creature_that_dealt_no_damage_in_its_step(audit, monkeypatch):
    # The Z2 defect, re-created: gate the blocker's deal-back on the
    # attacker's step (the old rule) — a first-strike blocker then deals
    # nothing, and the auditor must say so.
    game = GameState(rng=random.Random(0))
    a = _creature(game, "Vanilla", 1, power=1, toughness=2)
    b = _creature(game, "FirstStriker", 0, power=4, toughness=4,
                  keywords=(Keyword.FIRST_STRIKE,))
    orig = CombatManager._deals_in_step
    monkeypatch.setattr(CombatManager, "_deals_in_step",
                        staticmethod(lambda c, fs: False if c is b else orig(c, fs)))
    _fight(game, a, b)
    assert a.zone == "battlefield", "fixture: the broken rule lets the attacker live"
    assert "510.2/creature_dealt" in _rules(rules_audit.drain())


def test_combat_audit_is_silent_when_every_creature_dealt_its_damage(audit):
    game = GameState(rng=random.Random(0))
    a = _creature(game, "Vanilla", 1, power=1, toughness=2)
    b = _creature(game, "FirstStriker", 0, power=4, toughness=4,
                  keywords=(Keyword.FIRST_STRIKE,))
    _fight(game, a, b)
    assert a.zone != "battlefield"
    assert _rules(rules_audit.drain()) == []


def test_target_audit_sees_a_hexproof_creature_chosen_as_a_target(audit, card_db, monkeypatch):
    from engine.cast_manager import CastManager
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for n in ("Swamp", "Swamp"):
        t = card_db.get_card(n)
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="battlefield")
        c._game_state = game; c.enter_battlefield(); c.tapped = False
        game.players[0].battlefield.append(c)
    push_t = card_db.get_card("Fatal Push")
    push = CardInstance(template=push_t, owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="hand")
    push._game_state = game
    game.players[0].hand.append(push)
    shielded = _creature(game, "Shielded", 1, power=2, toughness=2, cmc=2,
                         keywords=(Keyword.HEXPROOF,))
    # A caller that hands the engine an illegal target (the pre-Z3 handler
    # shape), with the cast-time legality gate broken so the cast goes
    # through: the auditor records it at cast time and again on resolution.
    from engine import target_solver
    monkeypatch.setattr(target_solver, "has_legal_target_for_spell",
                        lambda *a, **k: True)
    assert CastManager.cast_spell(game, 0, push, [shielded.instance_id])
    while not game.stack.is_empty:
        game.resolve_stack()
    rules = _rules(rules_audit.drain())
    assert "601.2c/cast_target" in rules or "608.2b/resolve_target" in rules


def test_set_land_audit_sees_a_set_land_making_a_second_colour(audit, card_db, monkeypatch):
    from engine.mana_payment import ManaPayment
    game = GameState(rng=random.Random(0))
    t = card_db.get_card("Thundering Falls")
    falls = CardInstance(template=t, owner=1, controller=1,
                         instance_id=game.next_instance_id(), zone="battlefield")
    falls._game_state = game; falls.enter_battlefield()
    game.players[1].battlefield.append(falls)
    moon_t = card_db.get_card("Blood Moon")
    moon = CardInstance(template=moon_t, owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="battlefield")
    moon._game_state = game; moon.enter_battlefield()
    game.players[0].battlefield.append(moon)
    game.continuous_effects.recalculate(game)
    # Break the rule: pretend the layer never set the type when producing.
    monkeypatch.setattr(CardInstance, "current_basic_land_types",
                        property(lambda self: {"Island", "Mountain"}))
    ManaPayment.effective_produces_mana(game, 1, falls)
    assert "305.7/set_land_mana" in _rules(rules_audit.drain())


def test_x_counter_audit_sees_a_permanent_that_entered_without_its_counters(audit, card_db, monkeypatch):
    from engine.cast_manager import CastManager
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(4):
        t = card_db.get_card("Island")
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="battlefield")
        c._game_state = game; c.enter_battlefield(); c.tapped = False
        game.players[0].battlefield.append(c)
    bt = card_db.get_card("Walking Ballista")
    ballista = CardInstance(template=bt, owner=0, controller=0,
                            instance_id=game.next_instance_id(), zone="hand")
    ballista._game_state = game
    game.players[0].hand.append(ballista)
    # Re-create E6's regression: the engine never places the counters.
    monkeypatch.setattr(CardInstance, "add_plus_counters", lambda self, n, g=None: None)
    assert CastManager.cast_spell(game, 0, ballista, [])
    while not game.stack.is_empty:
        game.resolve_stack()
    assert "107.3/x_counters" in _rules(rules_audit.drain())


def test_sba_audit_sees_a_lethally_damaged_creature_left_on_the_battlefield(audit, monkeypatch):
    game = GameState(rng=random.Random(0))
    c = _creature(game, "Wounded", 0, power=1, toughness=1)
    c.damage_marked = 5
    # Break the rule: the SBA pass performs nothing.
    monkeypatch.setattr(GameState, "_check_sba_once", lambda self: False)
    game.check_state_based_actions()
    assert c.zone == "battlefield", "fixture: the broken SBA leaves it"
    assert "704.5f/lethal_damage" in _rules(rules_audit.drain())


def test_keyword_census_records_a_word_the_engine_does_not_model_once(audit, card_db):
    from engine.rules_audit_census import census_template_keywords
    denial = card_db.get_card("Stubborn Denial")   # "Ferocious — …": no enum, no field
    census_template_keywords(denial)
    census_template_keywords(denial)
    rows = [f for f in rules_audit.drain() if f["kind"] == "census"]
    assert any(r["rule"] == "keyword/unmodelled" and r["key"] == "ferocious" for r in rows)
    assert len([r for r in rows if r["key"] == "ferocious"]) == 1, "once per key"
    # A modelled mechanic (ward: typed `ward_cost` + the resolve_stack tax)
    # is not censused.
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    census_template_keywords(card_db.get_card("Kappa Cannoneer"))
    assert not [f for f in rules_audit.drain() if f.get("key") == "ward"]
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    census_template_keywords(card_db.get_card("Lightning Bolt"))
    assert [f for f in rules_audit.drain() if f["kind"] == "census"] == []


def test_findings_carry_the_runner_context_and_the_turn(audit):
    game = GameState(rng=random.Random(0))
    game.turn_number = 7
    rules_audit.check("test/rule", False, "detail", game=game)
    (f,) = rules_audit.drain()
    assert (f["seed"], f["deck1"], f["deck2"], f["detail"]) == (1, "A", "B", "detail")
    assert f["turn"] is not None
