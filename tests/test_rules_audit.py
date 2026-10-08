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
  unhandled/<timing>     — census: an effect that resolved through no handler.
  606/loyalty_unexecutable_kind — census: a printed loyalty ability the
                           engine refuses (visible-but-inert, never a no-op).
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


def _census(findings):
    return sorted({(f["rule"], f["key"]) for f in findings
                   if f["kind"] == "census"})


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


def test_unhandled_effect_is_folded_into_the_audit_census(audit):
    # An effect that resolved through no handler (recorded in the
    # process-level effect_diagnostics sink) becomes an `unhandled/<timing>`
    # census fact, so a full audited matrix ranks silent no-ops alongside the
    # keyword census.
    from engine import effect_diagnostics
    from engine.rules_audit_census import census_unhandled_effects
    effect_diagnostics.reset()
    try:
        effect_diagnostics.record_unhandled_effect("Foo", "spell")
        census_unhandled_effects()
        assert ("unhandled/spell", "Foo") in _census(audit.drain())
        # dedup: the same (rule, key) is recorded once per process
        census_unhandled_effects()
        assert audit.drain() == []
    finally:
        effect_diagnostics.reset()


def test_unhandled_fold_records_nothing_with_the_flag_off(monkeypatch):
    monkeypatch.delenv("MTG_RULES_AUDIT", raising=False)
    from engine import effect_diagnostics
    from engine.rules_audit_census import census_unhandled_effects
    rules_audit.reset()
    effect_diagnostics.reset()
    try:
        effect_diagnostics.record_unhandled_effect("Foo", "etb")
        census_unhandled_effects()
        assert rules_audit.drain() == []
    finally:
        effect_diagnostics.reset()


def test_empty_library_draw_audit_sees_a_draw_that_did_not_flag_the_loss(audit, monkeypatch):
    # CR 104.3c/704.5c: a draw attempted from an empty library must flag the
    # drawing player to lose. Recreate the defect — the branch returns without
    # flagging the loss — and the auditor must say so.
    game = GameState(rng=random.Random(0))
    game.players[0].library.clear()
    monkeypatch.setattr(GameState, "_lose_from_empty_library",
                        lambda self, idx: None)
    game.draw_cards(0, 1)
    assert "104.3c/empty_library_loss" in _rules(audit.drain())


def test_empty_library_draw_is_silent_when_the_loss_is_flagged(audit):
    game = GameState(rng=random.Random(0))
    game.players[0].library.clear()
    game.draw_cards(0, 1)
    assert game.game_over and game.winner == 1
    assert "104.3c/empty_library_loss" not in _rules(audit.drain())


def test_reduction_pip_audit_sees_a_reducer_eat_a_hybrid_pip(audit, monkeypatch):
    # CR 601.2f: a generic cost reduction shrinks the generic component only;
    # every coloured/colourless/hybrid pip survives. Recreate the defect — the
    # reducer folds a hybrid pip into generic (the old {1}{R/G}-for-free bug) —
    # and the auditor must say so.
    from engine.mana import ManaCost
    from engine.mana_payment import ManaPayment

    def _defective(cost, reduction):
        merged = (cost.generic + cost.non_generic_pips)
        return ManaCost(generic=max(0, merged - reduction))

    monkeypatch.setattr(ManaPayment, "_apply_cost_reduction",
                        staticmethod(_defective))
    before = ManaCost(generic=1, hybrid=[("R", "G")])
    after = ManaPayment._apply_cost_reduction(before, 2)
    rules_audit.check("601.2f/reduction_pips_preserved",
                      after.non_generic_pips == before.non_generic_pips,
                      "hybrid pip folded into generic", game=None)
    assert "601.2f/reduction_pips_preserved" in _rules(audit.drain())


def test_reduction_pip_audit_is_silent_when_only_generic_shrinks(audit):
    from engine.mana import ManaCost
    from engine.mana_payment import ManaPayment
    before = ManaCost(generic=3, red=1, hybrid=[("R", "G")])
    after = ManaPayment._apply_cost_reduction(before, 2)
    rules_audit.check("601.2f/reduction_pips_preserved",
                      after.non_generic_pips == before.non_generic_pips,
                      "", game=None)
    assert "601.2f/reduction_pips_preserved" not in _rules(audit.drain())
    assert after.generic == 1 and after.red == 1 and len(after.hybrid) == 1


def test_loyalty_refusal_of_an_unexecutable_kind_is_censused(audit):
    # CR 606: a printed loyalty ability whose effect the engine cannot
    # execute is refused before the loyalty is paid; the auditor records the
    # refused kind so a matrix ranks how many printed abilities are inert.
    from engine.cards import (CardInstance, CardTemplate, CardType,
                              LoyaltyAbility, LoyaltyEffectKind, ManaCost)
    from engine.planeswalker_manager import PlaneswalkerManager
    game = GameState(rng=random.Random(0))
    tmpl = CardTemplate(
        name="Test Walker", card_types=[CardType.PLANESWALKER],
        mana_cost=ManaCost(generic=4), supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=3, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
        loyalty_abilities={"plus": LoyaltyAbility(
            slot="plus", cost=1, text="[+1]: Do something unmodelled.",
            effect_kind=LoyaltyEffectKind.UNCLASSIFIED)})
    pw = CardInstance(template=tmpl, owner=0, controller=0,
                      instance_id=game.next_instance_id(), zone="battlefield")
    pw._game_state = game
    game.players[0].battlefield.append(pw)
    activated = PlaneswalkerManager.activate_planeswalker(game, 0, pw, "plus")
    assert activated is False  # refused before loyalty is paid
    assert ("606/loyalty_unexecutable_kind", "UNCLASSIFIED") in _census(
        audit.drain())


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


def _planeswalker(game, controller):
    tmpl = CardTemplate(
        name="Walker", card_types=[CardType.PLANESWALKER], mana_cost=ManaCost(generic=3),
        supertypes=[], subtypes=[], power=None, toughness=None, loyalty=4,
        keywords=set(), abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set())
    pw = CardInstance(template=tmpl, owner=controller, controller=controller,
                      instance_id=game.next_instance_id(), zone="battlefield")
    pw._game_state = game
    game.players[controller].battlefield.append(pw)
    return pw


def test_combat_audit_expects_no_damage_from_an_attacker_whose_planeswalker_left(audit):
    # CR 506.4 / 510.1b: a planeswalker that leaves the battlefield is
    # removed from combat; an unblocked creature attacking it keeps
    # attacking but assigns no combat damage. The engine deals none, and
    # the auditor must not call that a 510.2 miss.
    game = GameState(rng=random.Random(0))
    a = _creature(game, "Vanilla", 1, power=2, toughness=2)
    pw = _planeswalker(game, 0)
    cm = CombatManager()
    cm.declare_attackers(game, [a], active_player=1,
                         attack_targets={a.instance_id: pw})
    cm.declare_blockers(game, {})
    game.players[0].battlefield.remove(pw)
    pw.zone = "exile"
    game.players[0].exile.append(pw)
    life = game.players[0].life
    cm.resolve_combat_damage(game)
    assert game.players[0].life == life, "fixture: no damage redirects to the player"
    assert _rules(rules_audit.drain()) == []


def test_combat_audit_still_expects_damage_at_a_planeswalker_that_stayed(audit, monkeypatch):
    game = GameState(rng=random.Random(0))
    a = _creature(game, "Vanilla", 1, power=2, toughness=2)
    pw = _planeswalker(game, 0)
    cm = CombatManager()
    cm.declare_attackers(game, [a], active_player=1,
                         attack_targets={a.instance_id: pw})
    cm.declare_blockers(game, {})
    # Break the deal: the planeswalker is wrongly read as gone.
    monkeypatch.setattr(CombatManager, "_planeswalker_still_attackable",
                        lambda self, p: False)
    cm.resolve_combat_damage(game)
    assert "510.2/creature_dealt" in _rules(rules_audit.drain())


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


def test_sba_audit_sees_a_zero_toughness_creature_left_on_the_battlefield(audit, monkeypatch):
    game = GameState(rng=random.Random(0))
    _creature(game, "Shrunk", 0, power=1, toughness=0)
    monkeypatch.setattr(GameState, "_check_sba_once", lambda self: False)
    game.check_state_based_actions()
    assert "704.5f/zero_toughness" in _rules(rules_audit.drain())


def test_sba_audit_sees_a_player_at_zero_life_still_in_the_game(audit, monkeypatch):
    game = GameState(rng=random.Random(0))
    game.players[0].life = 0
    monkeypatch.setattr(GameState, "_check_sba_once", lambda self: False)
    game.check_state_based_actions()
    assert "704.5a/zero_life" in _rules(rules_audit.drain())


def test_sba_audit_sees_a_creature_listed_with_a_stale_zone(audit, monkeypatch):
    game = GameState(rng=random.Random(0))
    c = _creature(game, "Ghost", 0, power=2, toughness=2)
    c.zone = "graveyard"  # moved, but still listed on the battlefield
    monkeypatch.setattr(GameState, "_check_sba_once", lambda self: False)
    game.check_state_based_actions()
    assert "zone/list_lag" in _rules(rules_audit.drain())


def test_sba_audit_sees_the_loop_stop_at_its_iteration_cap(audit, monkeypatch):
    game = GameState(rng=random.Random(0))
    # The loop never reaches a fixpoint: every pass reports work done, so it
    # halts at SBA_MAX_ITERATIONS with hit_cap True.
    monkeypatch.setattr(GameState, "_check_sba_once", lambda self: True)
    game.check_state_based_actions()
    assert "704.3/fixpoint_cap" in _rules(rules_audit.drain())


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


def test_counter_upgrade_audit_sees_a_ferocious_counter_left_soft(audit, card_db, monkeypatch):
    """CR 601.2b: a Ferocious counter with a 4-power creature controlled
    is a HARD counter. Re-create the pre-fix engine (the tax stays the
    printed {1} despite the condition) and the auditor must record it;
    with the rule honoured it is silent."""
    from engine.spell_resolution import ResolutionManager
    from engine.stack import StackItem, StackItemType
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0

    def _add(name, controller, zone):
        t = card_db.get_card(name)
        c = CardInstance(template=t, owner=controller, controller=controller,
                         instance_id=game.next_instance_id(), zone=zone)
        c._game_state = game
        return c

    # Denial's controller (P1) has a 4-power creature → ferocious active.
    kavu = _add("Territorial Kavu", 1, "battlefield")
    kavu.enter_battlefield(); kavu.summoning_sick = False
    kavu.temp_power_mod = 4  # force power 4 regardless of domain
    game.players[1].battlefield.append(kavu)
    taxed = _add("Pyretic Ritual", 0, "stack")
    denial = _add("Stubborn Denial", 1, "stack")
    game.stack.push(StackItem(item_type=StackItemType.SPELL, source=taxed,
                              controller=0, targets=[], description=""))
    game.stack.push(StackItem(item_type=StackItemType.SPELL, source=denial,
                              controller=1, targets=[taxed.instance_id],
                              description="Counter target noncreature spell."))
    # Break the rule: the engine keeps the soft {1} tax despite ferocious.
    from engine import optional_costs
    monkeypatch.setattr(optional_costs, "effective_counter_tax",
                        lambda g, c, t: getattr(t, "counter_tax_amount", 0) or 0)
    ResolutionManager.resolve_stack(game)
    assert "601.2b/counter_upgrade" in _rules(rules_audit.drain())


def test_damage_upgrade_audit_sees_a_metalcraft_burn_left_at_base(audit, card_db, monkeypatch):
    """CR 608.2: a metalcraft Galvanic Blast deals 4, not 2. Re-create the
    pre-fix engine (the resolved amount stays base despite metalcraft) and
    the auditor must record it."""
    from engine import oracle_resolver
    from engine.oracle_resolver import resolve_spell_from_oracle
    from engine.cards import CardTemplate, CardType, ManaCost
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(3):
        t = CardTemplate(name="Mox", card_types=[CardType.ARTIFACT], mana_cost=ManaCost(),
                         supertypes=[], subtypes=[], power=None, toughness=None, loyalty=None,
                         keywords=set(), abilities=[], color_identity=set(), produces_mana=[],
                         enters_tapped=False, oracle_text="", tags=set())
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="battlefield")
        c._game_state = game; c.enter_battlefield()
        game.players[0].battlefield.append(c)
    vt = CardTemplate(name="Bear4", card_types=[CardType.CREATURE], mana_cost=ManaCost(generic=4),
                      supertypes=[], subtypes=[], power=1, toughness=4, loyalty=None, keywords=set(),
                      abilities=[], color_identity=set(), produces_mana=[], enters_tapped=False,
                      oracle_text="", tags=set())
    v = CardInstance(template=vt, owner=1, controller=1,
                     instance_id=game.next_instance_id(), zone="battlefield")
    v._game_state = game; v.enter_battlefield(); v.summoning_sick = False
    game.players[1].battlefield.append(v)
    gb = CardInstance(template=card_db.get_card("Galvanic Blast"), owner=0, controller=0,
                      instance_id=game.next_instance_id(), zone="stack")
    gb._game_state = game
    # Break the rule: keep the base amount despite metalcraft.
    from engine import effect_conditions
    monkeypatch.setattr(effect_conditions, "effective_direct_damage",
                        lambda g, c, t: (getattr(t, "direct_damage_data", None) or {}).get("amount", 0))
    resolve_spell_from_oracle(game, gb, 0, [v.instance_id])
    assert "608.2/damage_upgrade" in _rules(rules_audit.drain())


def test_ordinal_cast_audit_sees_an_over_triggered_ordinal(audit, card_db, monkeypatch):
    """CR 603.2: an ordinal cast trigger fires only on the Nth spell of the
    turn. Re-create an over-trigger (the gate fires regardless of the
    per-turn count) and the auditor must record it on the FIRST spell,
    when the count (1) does not equal the ordinal (2)."""
    from engine import oracle_resolver
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    monk = CardInstance(template=card_db.get_card("Monk of the Open Hand"),
                        owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="battlefield")
    monk._game_state = game; monk.enter_battlefield(); monk.summoning_sick = False
    game.players[0].battlefield.append(monk)
    # Break the rule: the ordinal gate fires on every cast, not just the Nth.
    monkeypatch.setattr(oracle_resolver, "_ordinal_cast_trigger_fires",
                        lambda g, p, c: True)
    spell = CardInstance(
        template=CardTemplate(
            name="S1", card_types=[CardType.INSTANT], mana_cost=ManaCost(generic=0),
            supertypes=[], subtypes=[], power=None, toughness=None, loyalty=None,
            keywords=set(), abilities=[], color_identity=set(), produces_mana=[],
            enters_tapped=False, oracle_text="", tags=set()),
        owner=0, controller=0, instance_id=game.next_instance_id(), zone="hand")
    spell._game_state = game
    game.players[0].hand.append(spell)
    game.cast_spell(0, spell, free_cast=True)  # first spell: count 1 vs ordinal 2
    assert "603.2/ordinal_cast" in _rules(rules_audit.drain())


def test_combat_prevention_audit_sees_an_attack_under_a_lock(audit):
    """CR 509.4: no creature attacks while a "creatures can't attack this
    turn" lock is set. Force an attacker in past the enumeration gate and
    the auditor must record the illegal attack."""
    game = GameState(rng=random.Random(0))
    a = _creature(game, "Attacker", 1, power=2, toughness=2)
    _creature(game, "Blocker", 0, power=1, toughness=1)
    # Lock set on the attacker's controller, but an attacker slips through.
    game.players[1].cannot_attack_this_turn = True
    cm = CombatManager()
    cm.declare_attackers(game, [a], active_player=1)
    cm.declare_blockers(game, {})
    cm.resolve_combat_damage(game)
    assert "509/no_attacks" in _rules(rules_audit.drain())


def test_attacker_legality_audit_sees_a_tapped_attacker(audit):
    """CR 508.1a: a declared attacker was untapped and not summoning-sick.
    Force a tapped creature in as an attacker; the auditor must record it."""
    game = GameState(rng=random.Random(0))
    a = _creature(game, "TappedAttacker", 1, power=2, toughness=2)
    a.tapped = True
    cm = CombatManager()
    cm.declare_attackers(game, [a], active_player=1)
    assert "508.1a/attacker_legal" in _rules(rules_audit.drain())


def test_attacker_legality_audit_sees_a_summoning_sick_attacker(audit):
    game = GameState(rng=random.Random(0))
    a = _creature(game, "SickAttacker", 1, power=2, toughness=2)
    a.summoning_sick = True  # no haste, not dashed
    cm = CombatManager()
    cm.declare_attackers(game, [a], active_player=1)
    assert "508.1a/attacker_legal" in _rules(rules_audit.drain())


def test_attacker_legality_is_silent_for_a_legal_attacker(audit):
    game = GameState(rng=random.Random(0))
    a = _creature(game, "GoodAttacker", 1, power=2, toughness=2)  # untapped, not sick
    cm = CombatManager()
    cm.declare_attackers(game, [a], active_player=1)
    assert "508.1a/attacker_legal" not in _rules(rules_audit.drain())


def test_blocker_legality_audit_sees_an_illegal_recorded_block(audit, monkeypatch):
    """CR 509.1b: a recorded blocker legally blocks. Force an illegal block
    (a non-flyer blocking a flyer) past _can_block and the auditor must
    record it."""
    game = GameState(rng=random.Random(0))
    flyer = _creature(game, "Flyer", 1, power=2, toughness=2,
                      keywords=(Keyword.FLYING,))
    ground = _creature(game, "Grounded", 0, power=1, toughness=1)
    monkeypatch.setattr(CombatManager, "_can_block",
                        staticmethod(lambda a, b: True))
    cm = CombatManager()
    cm.declare_attackers(game, [flyer], active_player=1)
    cm.declare_blockers(game, {flyer.instance_id: [ground.instance_id]})
    assert "509.1a/blocker_legal" in _rules(rules_audit.drain())


def test_blocker_legality_is_silent_for_a_legal_block(audit):
    game = GameState(rng=random.Random(0))
    a = _creature(game, "Attacker", 1, power=2, toughness=2)
    b = _creature(game, "Blocker", 0, power=1, toughness=2)
    cm = CombatManager()
    cm.declare_attackers(game, [a], active_player=1)
    cm.declare_blockers(game, {a.instance_id: [b.instance_id]})
    assert "509.1a/blocker_legal" not in _rules(rules_audit.drain())


def test_kicked_cost_paid_audit_sees_a_kicker_not_added(audit, card_db, monkeypatch):
    """CR 702.33: a kicked spell pays base + kicker. Re-create the pre-fix
    behaviour (the kicker is not added to the paid cost) and the auditor
    must record it."""
    from engine.cast_manager import CastManager
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(2):
        f = CardInstance(template=card_db.get_card("Forest"), owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="battlefield")
        f._game_state = game; f.tapped = False
        game.players[0].battlefield.append(f)
    t = CardTemplate(name="Kick Probe", card_types=[CardType.INSTANT],
                     mana_cost=ManaCost(green=1), supertypes=[], subtypes=[],
                     power=None, toughness=None, loyalty=None, keywords=set(),
                     abilities=[], color_identity=set(), produces_mana=[],
                     enters_tapped=False, oracle_text="Kicker {G}\nDraw a card.", tags=set())
    t.kicker_cost = ManaCost(green=1); t.multikicker = False; t.kicked_clause = "draw a card"
    spell = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="hand")
    spell._game_state = game
    game.players[0].hand.append(spell)
    game.callbacks.should_kick = lambda g, p, c: 1
    # Break the rule: the "combined" cost is the base — the kicker vanished.
    monkeypatch.setattr(CastManager, "_add_kicker",
                        staticmethod(lambda base, kicker, times: base))
    game.cast_spell(0, spell)
    assert "702.33/kicked_cost_paid" in _rules(rules_audit.drain())


def test_sba_audit_is_silent_when_a_transformed_creature_face_dies_to_lethal_damage(audit, card_db):
    # CR 711.8: the transformed face's creature-ness is what the SBA reads.
    # Before the fix the front-face gate left it alive and this recorded
    # 704.5f/lethal_damage (24 findings in the 2026-09-27 audited matrix).
    from engine.cards import CardInstance
    from engine.oracle_resolver import _transform_permanent
    game = GameState(rng=random.Random(0))
    tmpl = card_db.get_card("Fable of the Mirror-Breaker // Reflection of Kiki-Jiki")
    c = CardInstance(template=tmpl, owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    game.players[0].battlefield.append(c)
    _transform_permanent(game, c, controller=0)
    c.damage_marked = c.toughness
    game.check_state_based_actions()
    assert "704.5f/lethal_damage" not in _rules(rules_audit.drain())
    assert c not in game.players[0].battlefield


def _cast_bolt(game, card_db, player_idx, free_cast=False):
    from engine.cast_manager import CastManager
    for _ in range(2):
        land = CardInstance(template=card_db.get_card("Mountain"), owner=player_idx,
                            controller=player_idx, instance_id=game.next_instance_id(),
                            zone="battlefield")
        land._game_state = game
        game.players[player_idx].battlefield.append(land)
    bolt = CardInstance(template=card_db.get_card("Lightning Bolt"), owner=player_idx,
                        controller=player_idx, instance_id=game.next_instance_id(),
                        zone="hand")
    bolt._game_state = game
    game.players[player_idx].hand.append(bolt)
    return CastManager.cast_spell(game, player_idx, bolt,
                                  targets=[-1 - (1 - player_idx)], free_cast=free_cast)


def test_cast_prohibition_audit_sees_a_spell_cast_through_the_prohibition(audit, card_db, monkeypatch):
    # Break the rule: the one read path lets everything through.
    from engine import rules_query
    monkeypatch.setattr(rules_query, "cast_prohibited",
                        lambda game, player_idx, template: False)
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    from engine.effect_model import THIS_TURN, prohibit_cast
    game.continuous_effects.register_effect(prohibit_cast(0, "noncreature", THIS_TURN))
    _cast_bolt(game, card_db, 0, free_cast=True)
    assert "101.2/cast_prohibition" in _rules(rules_audit.drain())


def test_cast_prohibition_audit_is_silent_without_a_prohibition(audit, card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    _cast_bolt(game, card_db, 0)
    assert "101.2/cast_prohibition" not in _rules(rules_audit.drain())


def test_turn_end_audit_sees_a_spell_cast_after_the_turn_ended(audit, card_db):
    # Break the rule: the turn has ended, but a cast still goes through.
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.end_turn_requested = True
    _cast_bolt(game, card_db, 0, free_cast=True)
    assert "723.1/cast_after_turn_end" in _rules(rules_audit.drain())


def test_turn_end_audit_is_silent_during_a_normal_turn(audit, card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    _cast_bolt(game, card_db, 0)
    assert "723.1/cast_after_turn_end" not in _rules(rules_audit.drain())


def test_attack_target_audit_sees_an_attack_assigned_to_a_non_planeswalker(audit):
    game = GameState(rng=random.Random(0))
    atk = _creature(game, "Attacker", 0, power=2, toughness=2)
    not_a_pw = _creature(game, "Bystander", 1, power=1, toughness=1)
    CombatManager().declare_attackers(game, [atk], active_player=0,
                                      attack_targets={atk.instance_id: not_a_pw})
    assert "508.1b/attack_target_legal" in _rules(rules_audit.drain())


def test_attack_target_audit_is_silent_for_player_attacks(audit):
    game = GameState(rng=random.Random(0))
    atk = _creature(game, "Attacker", 0, power=2, toughness=2)
    CombatManager().declare_attackers(game, [atk], active_player=0)
    assert "508.1b/attack_target_legal" not in _rules(rules_audit.drain())


def test_loyalty_clause_audit_sees_a_clause_line_that_resolved_nothing(audit, card_db, monkeypatch):
    from engine import clause_resolver
    from engine.planeswalker_manager import PlaneswalkerManager
    from engine.cards import CardInstance
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    walker = CardInstance(template=card_db.get_card("Grist, the Hunger Tide"), owner=0,
                          controller=0, instance_id=game.next_instance_id(), zone="battlefield")
    walker._game_state = game
    walker.enter_battlefield()
    walker.loyalty_counters = 3
    game.players[0].battlefield.append(walker)
    # Break the rule: the clause owner applies nothing.
    monkeypatch.setattr(clause_resolver, "resolve_clause", lambda *a, **k: False)
    PlaneswalkerManager.activate_planeswalker(game, 0, walker, "plus")
    assert "606/loyalty_clause_resolved" in _rules(rules_audit.drain())


def test_loyalty_clause_audit_is_silent_when_the_clause_resolved(audit, card_db):
    from engine.planeswalker_manager import PlaneswalkerManager
    from engine.cards import CardInstance
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    walker = CardInstance(template=card_db.get_card("Grist, the Hunger Tide"), owner=0,
                          controller=0, instance_id=game.next_instance_id(), zone="battlefield")
    walker._game_state = game
    walker.enter_battlefield()
    walker.loyalty_counters = 3
    game.players[0].battlefield.append(walker)
    PlaneswalkerManager.activate_planeswalker(game, 0, walker, "plus")
    assert "606/loyalty_clause_resolved" not in _rules(rules_audit.drain())


def test_next_turn_audit_sees_an_effect_that_survived_its_controllers_untap(audit, card_db, monkeypatch):
    from engine.continuous_effects import ContinuousEffectsManager
    from engine.effect_model import cost_delta_effect, until_your_next_turn
    game = GameState(rng=random.Random(0))
    game.continuous_effects.register_effect(cost_delta_effect(
        0, {'target': 'all', 'amount': 1, 'color': None}, until_your_next_turn(0)))
    # Break the rule: the one expiry path lets the effect live on.
    monkeypatch.setattr(ContinuousEffectsManager, "expire_rule_effects",
                        lambda self, event: None)
    monkeypatch.setattr(ContinuousEffectsManager, "cleanup_until_next_turn",
                        lambda self, idx: None)
    game.active_player = 0
    game.untap_step(0)
    assert "611.2b/until_next_turn_expired" in _rules(rules_audit.drain())


def test_next_turn_audit_is_silent_when_the_effects_end(audit):
    from engine.effect_model import (cost_delta_effect, permit_cast_as_flash,
                                     until_your_next_turn)
    game = GameState(rng=random.Random(0))
    game.continuous_effects.register_effect(cost_delta_effect(
        0, {'target': 'all', 'amount': 1, 'color': None}, until_your_next_turn(0)))
    game.continuous_effects.register_effect(
        permit_cast_as_flash(0, ("sorcery",), until_your_next_turn(0)))
    assert game.players[0].temp_cost_rules and game.players[0].flash_permission_types
    game.active_player = 0
    game.untap_step(0)
    assert "611.2b/until_next_turn_expired" not in _rules(rules_audit.drain())


def _grant_game(card_db):
    from engine.cards import CardInstance
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    bear = CardInstance(template=card_db.get_card("Grizzly Bears"), owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="battlefield")
    bear._game_state = game
    bear.enter_battlefield()
    game.players[0].battlefield.append(bear)
    spell = CardInstance(template=card_db.get_card("Assault Strobe"), owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    return game, bear, spell


def test_keyword_grant_audit_sees_a_grant_that_did_not_land(audit, card_db, monkeypatch):
    from engine.clause_resolver import resolve_clause
    game, bear, spell = _grant_game(card_db)
    # Break the rule: the temporary keyword set swallows additions.
    class _Sink(set):
        def add(self, _):
            pass
    bear.temp_keywords = _Sink()
    resolve_clause(game, spell, 0, [bear.instance_id])
    assert "613.1f/keyword_granted" in _rules(rules_audit.drain())


def test_keyword_grant_audit_is_silent_when_the_grant_lands(audit, card_db):
    from engine.clause_resolver import resolve_clause
    game, bear, spell = _grant_game(card_db)
    resolve_clause(game, spell, 0, [bear.instance_id])
    assert "613.1f/keyword_granted" not in _rules(rules_audit.drain())


# ── CR 400.7: an effect on a chosen object does not follow the card ──

def _pump_on(game, bear, match_card_only):
    from engine.continuous_effects import create_pump_spell_effect
    for ce in create_pump_spell_effect(0, "Pump", bear.instance_id, 3, 3,
                                       target_seq=bear.battlefield_entry_seq):
        if match_card_only:   # the defect: the card, not the object
            ce.affected = lambda g, c, _id=bear.instance_id: c.instance_id == _id
        game.continuous_effects.register(ce)


def test_object_identity_audit_sees_an_effect_that_followed_a_blinked_card(audit):
    game = GameState(rng=random.Random(0))
    bear = _creature(game, "Bear", 0)
    _pump_on(game, bear, match_card_only=True)
    bear.battlefield_entry_seq += 1      # left and returned: a new object
    game.continuous_effects.recalculate(game)
    assert "400.7/effect_follows_old_object" in _rules(rules_audit.drain())


def test_object_identity_audit_is_silent_when_the_effect_stays_with_its_object(audit):
    game = GameState(rng=random.Random(0))
    bear = _creature(game, "Bear", 0)
    _pump_on(game, bear, match_card_only=False)
    game.continuous_effects.recalculate(game)
    assert bear.power == 5
    bear.battlefield_entry_seq += 1
    game.continuous_effects.recalculate(game)
    assert bear.power == 2
    assert "400.7/effect_follows_old_object" not in _rules(rules_audit.drain())


def _exiled_walker(game, loyalty=4):
    tmpl = CardTemplate(
        name="Walker", card_types=[CardType.PLANESWALKER], mana_cost=ManaCost(generic=3),
        supertypes=[], subtypes=[], power=None, toughness=None, loyalty=loyalty,
        keywords=set(), abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set())
    pw = CardInstance(template=tmpl, owner=0, controller=0,
                      instance_id=game.next_instance_id(), zone="exile")
    game.players[0].exile.append(pw)
    return pw


def test_entry_audit_sees_a_planeswalker_entering_without_its_printed_loyalty(audit, monkeypatch):
    # The pre-fix defect, re-created: entry leaves loyalty at the zeroed
    # leave-battlefield value.
    game = GameState(rng=random.Random(0))
    pw = _exiled_walker(game)
    orig = CardInstance.enter_battlefield

    def _old_entry(self):
        orig(self)
        self.loyalty_counters = 0
    monkeypatch.setattr(CardInstance, "enter_battlefield", _old_entry)
    game.zone_mgr.move_card(game, pw, "exile", "battlefield")
    assert "306.5b/entry_loyalty" in _rules(rules_audit.drain())


def test_entry_audit_is_silent_when_a_planeswalker_enters_at_printed_loyalty(audit):
    game = GameState(rng=random.Random(0))
    pw = _exiled_walker(game)
    game.zone_mgr.move_card(game, pw, "exile", "battlefield")
    assert _rules(rules_audit.drain()) == []


def _life_ward_scenario(game, life=3):
    import copy
    from tests.test_ward_framework import _push_ward_scenario
    warded, removal = _push_ward_scenario(game, ward_cost=0)
    warded.template = copy.copy(warded.template)
    warded.template.ward_life_cost = life
    warded.template.oracle_text = "Ward—Pay %d life." % life
    return warded, removal


def test_ward_audit_sees_a_spell_resolving_through_an_unpaid_ward(audit, monkeypatch):
    # The pre-fix defect, re-created: the resolution scan recognises only
    # a mana ward, so a life ward is never offered and the spell resolves.
    from engine import optional_costs
    from tests.test_ward_framework import _NeverPayCallbacks
    monkeypatch.setattr(optional_costs, "ward_owed",
                        lambda t: (getattr(t, "ward_cost", 0) or 0) > 0)
    game = GameState(rng=random.Random(0), callbacks=_NeverPayCallbacks())
    warded, _ = _life_ward_scenario(game)
    game.resolve_stack()
    assert warded.zone != "battlefield", "fixture: the broken scan lets it resolve"
    assert "702.21a/ward_paid" in _rules(rules_audit.drain())


def test_ward_audit_is_silent_when_the_ward_cost_is_paid(audit):
    from tests.test_ward_framework import _AlwaysPayCallbacks
    game = GameState(rng=random.Random(0), callbacks=_AlwaysPayCallbacks())
    _life_ward_scenario(game)
    game.resolve_stack()
    assert _rules(rules_audit.drain()) == []


def test_ward_audit_sees_an_etb_trigger_resolving_through_an_unpaid_ward(audit, card_db, monkeypatch):
    # The pre-fix defect, re-created: the targeted ETB trigger never meets
    # ward (the gate waves everything through).
    from engine import optional_costs
    from tests.test_ward_on_etb_triggers import _setup
    from tests.test_ward_framework import _NeverPayCallbacks
    monkeypatch.setattr(optional_costs, "ward_gate",
                        lambda game, src, ctrl, tids, what="": (True, set()))
    game, binding, warded = _setup(card_db, _NeverPayCallbacks())
    game.resolve_stack()
    assert warded.zone == "exile", "fixture: the broken gate lets it resolve"
    assert "702.21a/ward_paid" in _rules(rules_audit.drain())


def test_ward_audit_is_silent_when_an_etb_trigger_is_countered_by_ward(audit, card_db):
    from tests.test_ward_on_etb_triggers import _setup
    from tests.test_ward_framework import _NeverPayCallbacks
    game, binding, warded = _setup(card_db, _NeverPayCallbacks())
    game.resolve_stack()
    assert warded.zone == "battlefield"
    assert _rules(rules_audit.drain()) == []
