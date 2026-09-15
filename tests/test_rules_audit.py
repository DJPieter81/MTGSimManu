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
    monkeypatch.setattr(oracle_resolver, "effective_direct_damage",
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
