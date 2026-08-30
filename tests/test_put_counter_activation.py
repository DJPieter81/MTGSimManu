"""Put-counter activation: "[Cost]: Put N <kind> counter(s) on <permanent>".

Counter COSTS became payable in tranche 4; the counter EFFECT never did.
Every activated ability that put counters on a permanent landed in
`UNCLASSIFIED`, so rule 9b refused it before any cost was charged — the
permanent was visible-but-inert. 148 abilities across 146 Modern cards now
classify. The shape is one mechanic with two target scopes:

  * SELF   — "Put a +1/+1 counter on this creature" (102 abilities). Not
             targeted at all (CR 115.1), so `targets_required` stays 0 and
             that is the discriminator the resolver reads.
  * TARGET — "Put a +1/+1 counter on target creature" (46 abilities), which
             declares one target like any other targeted ability.

The remainder of the printed put-counter text stays UNCLASSIFIED on purpose:
mass forms ("on each creature you control"), unbounded and X-bound counts,
qualifier-restricted targets ("another", "attacking"), and any trailing
rider are DIFFERENT effects, and executing them as a bare single-recipient
put would silently drop the rest.

Rules pinned here:

  * CR 121.1 — counters go on the permanent through the instance's existing
    counter fields, so a +1/+1 counter moves power AND toughness. There is
    no parallel ledger and no until-end-of-turn expiry: a counter is
    permanent, which is what separates this kind from `PUMP_SELF_UEOT`.
  * CR 608.2b — a declared target that has left the battlefield is skipped,
    never silently redirected onto something else.
  * A counter kind the instance model cannot represent (+2/+2, -0/-1) is
    refused, never mapped onto the nearest available kind — the same rule
    the counter COST parser already enforces.
  * No-free-repeatable (rule 9), extended: a put-counter EFFECT that refills
    the very counter supply its own cost removes does not terminate. The
    cost-side predicate alone cannot see this, because it only reads the
    cost. Measured DB-wide: zero printed cards have that shape today, so
    the guard costs nothing real — it exists so a future DB refresh cannot
    introduce a free loop silently.

Rules-phrased; card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine.activation import ActivationManager
from engine.activated_effects import resolve_activated_ability
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import (classify_activation_effect,
                                  parse_activation_put_counter)

_DB = CardDatabase()


def _add(game, name, controller=0, zone="battlefield"):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game(n_islands=6):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Eldrazi Tron"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_islands):
        _add(game, "Island")
    for _ in range(8):
        _add(game, "Island", 0, "library")
    return game


def _ability(effect_text, *, mana=2, rem_kind=None, rem_n=0):
    """Build the ability the way the DB loader would: classify the effect
    text, then carry the structured shape the resolver dispatches off."""
    kind, amount, p_mod, t_mod = classify_activation_effect(effect_text)
    data = parse_activation_put_counter(effect_text)
    targets_required = 0
    target_requirements: list = []
    if data is not None and not data['self']:
        from engine.target_solver import TargetRequirement
        targets_required = 1
        target_requirements = [TargetRequirement(
            zone="battlefield", types=frozenset(data['types']),
            raw_phrase=effect_text.lower())]
    return ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(generic=mana),
                            remove_counter_kind=rem_kind,
                            remove_counter_amount=rem_n),
        effect_text=effect_text, effect_kind=kind, amount=amount,
        power_mod=p_mod, toughness_mod=t_mod,
        targets_required=targets_required,
        target_requirements=target_requirements,
        put_counter_data=data)


def _host(game, ability, name="Wall of Omens"):
    """Attach one synthetic ability to a fixture carrier. The template is
    COPIED first: templates are shared DB objects and mutating one would
    leak into every other test in the session."""
    perm = _add(game, name)
    perm.template = copy.copy(perm.template)
    perm.template.activated_abilities = [ability]
    return perm


# ── parsing: the sentence becomes a structured shape ──────────────────

def test_put_counter_on_self_parses_kind_amount_and_self_scope():
    spec = parse_activation_put_counter(
        "Put a +1/+1 counter on this creature.")
    assert spec is not None
    assert spec['kind'] == "+1/+1"
    assert spec['amount'] == 1
    assert spec['self'] is True


def test_put_counter_amount_words_parse_as_a_count():
    spec = parse_activation_put_counter(
        "Put two +1/+1 counters on this creature.")
    assert spec is not None and spec['amount'] == 2


def test_put_counter_on_target_parses_the_target_type():
    spec = parse_activation_put_counter(
        "Put a +1/+1 counter on target creature.")
    assert spec is not None
    assert spec['self'] is False
    assert spec['types'] == ['creature']


def test_you_control_narrows_the_target_by_owner_not_by_refusal():
    """"target creature you control" is expressible — `TargetRequirement`
    carries it as `owner_scope`, exactly as the untap parser already does —
    so it must narrow the requirement rather than be refused wholesale."""
    spec = parse_activation_put_counter(
        "Put a +1/+1 counter on target creature you control.")
    assert spec is not None
    assert spec['self'] is False
    assert spec['types'] == ['creature']
    assert spec['owner'] == 'you'
    open_spec = parse_activation_put_counter(
        "Put a +1/+1 counter on target creature.")
    assert open_spec is not None and open_spec['owner'] == 'any'


def test_target_qualifiers_the_schema_cannot_hold_are_still_refused():
    """A qualifier BEFORE the type noun restricts the target in a way the
    requirement schema cannot express — "another" would have to exclude the
    source, and a union ("artifact or creature") is not a single type."""
    for phrase in (
            "Put a +1/+1 counter on another target creature.",
            "Put a +1/+1 counter on target attacking creature.",
            "Put a +1/+1 counter on target artifact or creature you control.",
    ):
        assert parse_activation_put_counter(phrase) is None, phrase


def test_named_counter_kinds_parse_alongside_pt_counters():
    """A neutral counter (charge/page/oil) lives in `other_counters`; it is
    the same mechanic, so it parses rather than being refused."""
    spec = parse_activation_put_counter(
        "Put a charge counter on this artifact.")
    assert spec is not None and spec['kind'] == "charge"


def test_counter_kinds_the_instance_model_cannot_hold_are_refused():
    """+1/+1 and -1/-1 are the only P/T counter kinds the instance model
    represents. A +2/+2 counter would have to move P/T by two without a
    field to hold it — refused, never mapped onto the nearest kind. Same
    rule the counter COST parser already enforces."""
    for phrase in ("Put a +2/+2 counter on this creature.",
                   "Put a -0/-1 counter on target creature."):
        assert parse_activation_put_counter(phrase) is None, phrase


def test_mass_and_ridered_put_counter_sentences_stay_unclassified():
    """ANCHORED to the full sentence, like every other activation
    classifier: a mass shape ("each creature you control") and a trailing
    rider are DIFFERENT effects, and executing them as a bare single-target
    put would silently drop the rest."""
    for phrase in (
            "Put a +1/+1 counter on each creature you control.",
            "Put a +1/+1 counter on target creature. It gains flying "
            "until end of turn.",
            "Put a +1/+1 counter on any number of target creatures.",
            "Put X +1/+1 counters on this creature.",
    ):
        assert parse_activation_put_counter(phrase) is None, phrase
        kind, _, _, _ = classify_activation_effect(phrase)
        assert kind is ActivationEffectKind.UNCLASSIFIED, phrase


def test_classifier_routes_the_two_scopes_to_distinct_kinds():
    self_kind, _, _, _ = classify_activation_effect(
        "Put a +1/+1 counter on this creature.")
    tgt_kind, _, _, _ = classify_activation_effect(
        "Put a +1/+1 counter on target creature.")
    assert self_kind is ActivationEffectKind.PUT_COUNTER_SELF
    assert tgt_kind is ActivationEffectKind.PUT_COUNTER_TARGET


# ── legality: the kind is executable, so rule 9b stops refusing it ────

def test_put_counter_ability_is_legal_to_activate():
    game = _game()
    ab = _ability("Put a +1/+1 counter on this creature.")
    perm = _host(game, ab)
    assert ActivationManager.can_activate(game, 0, perm, ab), (
        "a put-counter effect is executable, so rule 9b must stop refusing "
        "it before the cost is charged")


def test_effect_that_refills_its_own_counter_cost_is_refused():
    """No-free-repeatable, effect side. "Remove a +1/+1 counter: Put a
    +1/+1 counter on this creature" pays with exactly what it produces, so
    the activation never terminates. The cost-side predicate cannot see
    this — it only reads the cost — so the rule belongs with the effect."""
    game = _game()
    ab = _ability("Put a +1/+1 counter on this creature.",
                  mana=0, rem_kind="+1/+1", rem_n=1)
    perm = _host(game, ab)
    perm.adjust_counters("+1/+1", 3)
    assert not ActivationManager.can_activate(game, 0, perm, ab), (
        "an effect that refills the counter supply its own cost removes "
        "is a free loop and must be refused")


def test_refill_guard_does_not_block_an_ability_that_also_spends_mana():
    """The guard is about a loop that consumes NOTHING. A mana cost
    alongside the counter item still depletes, so the same shape stays
    legal — the guard must not over-refuse."""
    game = _game()
    ab = _ability("Put a +1/+1 counter on this creature.",
                  mana=2, rem_kind="+1/+1", rem_n=1)
    perm = _host(game, ab)
    perm.adjust_counters("+1/+1", 3)
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_schema_incoherence_is_refused():
    """A line classified as put-counter with no parsed shape has nothing
    for the resolver to dispatch on — refuse rather than half-execute."""
    game = _game()
    ab = _ability("Put a +1/+1 counter on this creature.")
    ab.put_counter_data = None
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab)


# ── resolution: the counter actually lands, on the right permanent ────

def test_put_counter_on_self_moves_power_and_toughness():
    """CR 121.1 — the counter goes on the permanent through the instance's
    existing counter fields, so a +1/+1 counter moves BOTH characteristics.
    Reading power/toughness (not the counter field) is what proves it is
    not a parallel ledger."""
    game = _game()
    ab = _ability("Put a +1/+1 counter on this creature.")
    perm = _host(game, ab, "Grizzly Bears")
    p0, t0 = perm.power, perm.toughness
    assert resolve_activated_ability(game, perm, 0, ability=ab)
    assert perm.counter_count("+1/+1") == 1
    assert (perm.power, perm.toughness) == (p0 + 1, t0 + 1)


def test_put_counter_is_permanent_not_until_end_of_turn():
    """The distinction from PUMP_SELF_UEOT: a counter survives cleanup."""
    game = _game()
    ab = _ability("Put a +1/+1 counter on this creature.")
    perm = _host(game, ab, "Grizzly Bears")
    p0 = perm.power
    resolve_activated_ability(game, perm, 0, ability=ab)
    perm.cleanup_damage()
    assert perm.power == p0 + 1, (
        "a counter is permanent; only until-end-of-turn modifiers expire")


def test_multi_counter_effect_adds_every_counter():
    game = _game()
    ab = _ability("Put two +1/+1 counters on this creature.")
    perm = _host(game, ab, "Grizzly Bears")
    resolve_activated_ability(game, perm, 0, ability=ab)
    assert perm.counter_count("+1/+1") == 2


def test_minus_counter_effect_shrinks_the_permanent():
    game = _game()
    ab = _ability("Put a -1/-1 counter on target creature.")
    perm = _host(game, ab)
    victim = _add(game, "Grizzly Bears", controller=1)
    t0 = victim.toughness
    assert resolve_activated_ability(game, perm, 0, [victim.instance_id],
                                     ability=ab)
    assert victim.toughness == t0 - 1


def test_put_counter_on_target_lands_on_the_declared_target_only():
    game = _game()
    ab = _ability("Put a +1/+1 counter on target creature.")
    perm = _host(game, ab)
    chosen = _add(game, "Grizzly Bears")
    bystander = _add(game, "Grizzly Bears")
    assert resolve_activated_ability(game, perm, 0, [chosen.instance_id],
                                     ability=ab)
    assert chosen.counter_count("+1/+1") == 1
    assert bystander.counter_count("+1/+1") == 0
    assert perm.counter_count("+1/+1") == 0


def test_targeted_put_counter_fizzles_when_the_target_has_left():
    """CR 608.2b — a target that is gone is skipped, never redirected onto
    another permanent."""
    game = _game()
    ab = _ability("Put a +1/+1 counter on target creature.")
    perm = _host(game, ab)
    chosen = _add(game, "Grizzly Bears")
    other = _add(game, "Grizzly Bears")
    game.players[0].battlefield.remove(chosen)
    chosen.zone = "graveyard"
    assert not resolve_activated_ability(game, perm, 0,
                                         [chosen.instance_id], ability=ab)
    assert other.counter_count("+1/+1") == 0


def test_named_counter_effect_lands_in_the_generic_counter_store():
    game = _game()
    ab = _ability("Put a charge counter on this artifact.")
    perm = _host(game, ab)
    assert resolve_activated_ability(game, perm, 0, ability=ab)
    assert perm.counter_count("charge") == 1


def test_self_scope_does_not_resolve_once_the_source_has_left():
    """The source is only a legal recipient while it is on the battlefield
    — the same rule that makes the untap SELF form stop dead."""
    game = _game()
    ab = _ability("Put a +1/+1 counter on this creature.")
    perm = _host(game, ab, "Grizzly Bears")
    game.players[0].battlefield.remove(perm)
    perm.zone = "graveyard"
    assert not resolve_activated_ability(game, perm, 0, ability=ab)


# ── the AI half: a legal ability the AI never enumerates is still dead ─

def _snap(game, player_idx=0):
    from ai.ev_evaluator import snapshot_from_game
    return snapshot_from_game(game, player_idx)


def test_the_ai_withholds_put_counter_activations_pending_a_card_price():
    """The engine class is complete; the AI enumeration is withheld.

    `EVSnapshot` prices a card in hand at `card_clock_impact` (~0.125)
    against 0.23-4.07 for one +1/+1 counter, and carries no term for card
    QUALITY. Every marginal activation of a card-costed counter ability
    therefore reads positive and the AI repeats it until the hand is empty
    (measured: five cards, two of them Solitude, pitched on turn 3).

    Withholding is the deliberate state, so it is pinned like any other
    behaviour — if a later change makes `activation_candidates` offer these
    again, that change must come with a valuation that prices a card, and
    this test is where it announces itself.
    """
    from ai.activation_ev import activation_candidates
    game = _game()
    ab = _ability("Put a +1/+1 counter on this creature.")
    perm = _host(game, ab, "Grizzly Bears")
    assert not any(c[0] is perm
                   for c in activation_candidates(game, 0, _snap(game)))


def test_the_recipient_chooser_still_follows_the_counters_sign():
    """The beneficiary logic is kept and tested even while enumeration is
    withheld: it is the piece a future valuation will drive, and an
    untested chooser would rot silently until then."""
    from ai.activation_ev import put_counter_beneficiary
    game = _game()
    grow = _ability("Put a +1/+1 counter on target creature.")
    perm = _host(game, grow)
    mine = _add(game, "Grizzly Bears", controller=0)
    theirs = _add(game, "Grizzly Bears", controller=1)
    assert put_counter_beneficiary(game, 0, perm, grow) is mine, (
        "a positive counter belongs on our own board")
    shrink = _ability("Put a -1/-1 counter on target creature.")
    assert put_counter_beneficiary(game, 0, perm, shrink) is theirs, (
        "a negative counter belongs on the opponent's board")


def test_an_owner_restricted_ability_never_aims_outside_its_legal_targets():
    """The parsed owner scope is a LEGALITY bound, not a preference: an
    ability printed "target creature you control" cannot aim at the
    opponent's board however much a -1/-1 counter would prefer to."""
    from ai.activation_ev import put_counter_beneficiary
    game = _game()
    ab = _ability("Put a -1/-1 counter on target creature you control.")
    assert ab.put_counter_data['owner'] == 'you'
    perm = _host(game, ab)
    mine = _add(game, "Grizzly Bears", controller=0)
    _add(game, "Grizzly Bears", controller=1)
    chosen = put_counter_beneficiary(game, 0, perm, ab)
    assert chosen is None or chosen is mine


def test_a_counter_with_no_power_toughness_meaning_has_no_recipient():
    """A charge/oil/page counter moves no projected field, so there is no
    recipient that turns it into position — the honest answer is None
    rather than an invented value."""
    from ai.activation_ev import put_counter_beneficiary
    game = _game()
    ab = _ability("Put a charge counter on this artifact.")
    perm = _host(game, ab)
    assert put_counter_beneficiary(game, 0, perm, ab) is None


def test_the_cost_and_effect_sides_price_counters_through_one_mapping():
    """Structural: the COST-side P/T charge and the EFFECT-side projection
    must not be able to disagree about what a counter kind is worth."""
    from ai.activation_ev import _counter_cost_pt_delta, counter_pt_delta
    from engine.cards import ActivationCost
    assert counter_pt_delta("+1/+1", 2) == 2
    assert counter_pt_delta("-1/-1", 2) == -2
    assert counter_pt_delta("charge", 5) == 0
    cost = ActivationCost(remove_counter_kind="+1/+1",
                          remove_counter_amount=2)
    assert _counter_cost_pt_delta(cost) == -counter_pt_delta("+1/+1", 2)


# ── class coverage: the mechanic, not the fixtures ────────────────────

def test_the_put_counter_class_is_broadly_executable_across_the_pool():
    """Class-size guard. This is a MECHANIC, not a patch: the shape is
    printed on well over a hundred Modern cards. If a future parser change
    narrows the match to a handful, that is a regression in genericity even
    though every behaviour test above still passes."""
    hits = [
        (n, ab)
        for n, t in _DB.cards.items()
        for ab in (t.activated_abilities or [])
        if ab.effect_kind in (ActivationEffectKind.PUT_COUNTER_SELF,
                              ActivationEffectKind.PUT_COUNTER_TARGET)
    ]
    assert len(hits) >= 100, (
        f"only {len(hits)} abilities classified as put-counter; the printed "
        f"class is 148 across 146 cards")
    assert all(ab.put_counter_data is not None for _, ab in hits), (
        "every classified put-counter ability must carry its structured "
        "shape — the resolver has nothing to dispatch on without it")
