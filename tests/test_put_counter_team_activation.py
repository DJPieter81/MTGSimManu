"""Team put-counter activation: "[Cost]: Put N <kind> counter(s) on each
[other] [artifact] <permanent-type> [you control]" is one executable
activated-ability class (CR 122.1, 602).

The SELF and TARGET scopes of the put-counter class classify and resolve;
the MASS scope stayed UNCLASSIFIED (22 Modern activated abilities, 12 of them
the plain shape: Gavony Township, Steel Overseer, Leyline of Abundance,
Shalai, Mikaeus, Genku, Katilda, Aron, Durable Handicraft, ...), so the
engine refused it before any cost was charged and the permanent was
visible-but-inert. The payoff-sequencing design
(docs/design/2026-09-16_payoff_sequencing_design.md §5 U1) found that in
every loop-live Creatures Toolbox turn the outlet actually on the
battlefield is exactly this shape, refused.

Rules pinned here (mechanic-phrased; card names are fixture carriers):

  * The sentence is typed once at DB load with its scope: every permanent
    of the named type(s) under the named controller(s), optionally
    excluding the source ("each OTHER creature"). A qualifier the type
    schema cannot hold (a subtype, a colour) is refused, never
    approximated — the same rule the TARGET scope follows.
  * CR 115.1 — the mass form is not targeted: `targets_required` is 0.
  * CR 122.1 — counters land on every recipient through the instance's own
    counter accessor (the single owner), so a +1/+1 counter moves power AND
    toughness and does not expire.
  * The auditor invariant `122/team_counter_placed` restates the recipient
    set independently and records a resolution that misses one.
  * The AI enumeration stays withheld, like the SELF/TARGET scopes, until a
    valuation prices the activation — the design's U2 (AssemblyState) is
    where it is valued; this unit is engine rules-correctness only.
"""
from __future__ import annotations

import copy
import random

import pytest

from engine import rules_audit
from engine.activation import ActivationManager
from engine.activated_effects import resolve_activated_ability
from engine.cards import (COUNTER_KIND_PLUS, ActivatedAbility,
                          ActivationCost, ActivationEffectKind, CardInstance)
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import (classify_activation_effect,
                                  parse_activation_put_counter)


def _add(game, card_db, name, controller=0, zone="battlefield"):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db, n_forests=4):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    for _ in range(n_forests):
        _add(game, card_db, "Forest")
    return game


def _ability(effect_text, *, mana=2):
    kind, amount, p_mod, t_mod = classify_activation_effect(effect_text)
    return ActivatedAbility(
        index=0, cost=ActivationCost(mana=ManaCost(generic=mana)),
        effect_text=effect_text, effect_kind=kind, amount=amount,
        power_mod=p_mod, toughness_mod=t_mod, targets_required=0,
        target_requirements=[],
        put_counter_data=parse_activation_put_counter(effect_text))


def _host(game, card_db, ability, name="Grizzly Bears", controller=0):
    perm = _add(game, card_db, name, controller)
    perm.template = copy.copy(perm.template)
    perm.template.activated_abilities = [ability]
    return perm


# ── parsing: the sentence becomes a structured shape with its scope ───

def test_a_team_put_counter_sentence_is_typed_with_its_scope():
    spec = parse_activation_put_counter(
        "Put a +1/+1 counter on each creature you control.")
    assert spec is not None
    assert spec['kind'] == COUNTER_KIND_PLUS
    assert spec['amount'] == 1
    assert spec['self'] is False
    assert spec['scope'] == 'team'
    assert spec['types'] == ['creature']
    assert spec['owner'] == 'you'
    assert spec['other'] is False


def test_each_other_excludes_the_source_and_each_without_you_control_is_every_player():
    other = parse_activation_put_counter(
        "Put a +1/+1 counter on each other creature you control.")
    assert other['other'] is True and other['owner'] == 'you'
    everyone = parse_activation_put_counter(
        "Put a +1/+1 counter on each creature.")
    assert everyone['owner'] == 'any' and everyone['other'] is False


def test_a_card_type_qualifier_narrows_the_recipients():
    spec = parse_activation_put_counter(
        "Put a +1/+1 counter on each artifact creature you control.")
    assert spec['scope'] == 'team'
    assert spec['types'] == ['artifact', 'creature']


def test_a_qualifier_the_type_schema_cannot_hold_is_refused():
    for phrase in (
            "Put a +1/+1 counter on each Sliver you control.",
            "Put a +1/+1 counter on each green creature you control.",
            "Put a +1/+1 counter on each attacking creature.",
            "Put a +1/+1 counter on each creature you control. "
            "They gain trample until end of turn.",
    ):
        assert parse_activation_put_counter(phrase) is None, phrase
        kind, _, _, _ = classify_activation_effect(phrase)
        assert kind is ActivationEffectKind.UNCLASSIFIED, phrase


def test_the_classifier_routes_the_mass_scope_to_its_own_kind():
    kind, amount, _, _ = classify_activation_effect(
        "Put two +1/+1 counters on each creature you control.")
    assert kind is ActivationEffectKind.PUT_COUNTER_TEAM
    assert amount == 2


# ── legality: the kind is executable, so rule 9b stops refusing it ────

def test_a_team_put_counter_ability_is_legal_with_its_mana_and_refused_without(card_db):
    t = card_db.get_card("Leyline of Abundance")           # {6}{G}{G}
    ab = next(a for a in t.activated_abilities
              if a.effect_kind is ActivationEffectKind.PUT_COUNTER_TEAM)
    assert ab.targets_required == 0, "the mass form is not targeted (CR 115.1)"
    assert ab.put_counter_data is not None
    game = _game(card_db, n_forests=8)
    leyline = _add(game, card_db, "Leyline of Abundance")
    assert ActivationManager.can_activate(game, 0, leyline, ab)
    short = _game(card_db, n_forests=7)
    leyline2 = _add(short, card_db, "Leyline of Abundance")
    assert not ActivationManager.can_activate(short, 0, leyline2, ab)


# ── resolution: every recipient in scope, nothing outside it ──────────

def test_resolution_puts_one_counter_on_every_creature_its_controller_controls(card_db):
    game = _game(card_db)
    ab = _ability("Put a +1/+1 counter on each creature you control.")
    host = _host(game, card_db, ab)                        # 2/2
    mine = _add(game, card_db, "Wall of Omens")            # 0/4
    rock = _add(game, card_db, "Mind Stone")               # artifact, not a creature
    theirs = _add(game, card_db, "Grizzly Bears", 1)
    assert resolve_activated_ability(game, host, 0, [], ability=ab)
    assert (host.power, host.toughness) == (3, 3)
    assert (mine.power, mine.toughness) == (1, 5)
    assert rock.counter_count(COUNTER_KIND_PLUS) == 0
    assert (theirs.power, theirs.toughness) == (2, 2)


def test_each_other_creature_leaves_the_source_untouched(card_db):
    game = _game(card_db)
    ab = _ability("Put a +1/+1 counter on each other creature you control.")
    host = _host(game, card_db, ab)
    mine = _add(game, card_db, "Wall of Omens")
    assert resolve_activated_ability(game, host, 0, [], ability=ab)
    assert host.counter_count(COUNTER_KIND_PLUS) == 0
    assert mine.counter_count(COUNTER_KIND_PLUS) == 1


def test_each_creature_without_you_control_reaches_every_players_creatures(card_db):
    game = _game(card_db)
    ab = _ability("Put a +1/+1 counter on each creature.")
    host = _host(game, card_db, ab)
    theirs = _add(game, card_db, "Grizzly Bears", 1)
    assert resolve_activated_ability(game, host, 0, [], ability=ab)
    assert host.counter_count(COUNTER_KIND_PLUS) == 1
    assert theirs.counter_count(COUNTER_KIND_PLUS) == 1


def test_a_type_qualified_team_effect_skips_creatures_of_the_wrong_type(card_db):
    game = _game(card_db)
    ab = _ability("Put a +1/+1 counter on each artifact creature you control.")
    host = _host(game, card_db, ab, name="Ornithopter")    # artifact creature
    bears = _add(game, card_db, "Grizzly Bears")
    assert resolve_activated_ability(game, host, 0, [], ability=ab)
    assert host.counter_count(COUNTER_KIND_PLUS) == 1
    assert bears.counter_count(COUNTER_KIND_PLUS) == 0


def test_the_counters_are_permanent_not_until_end_of_turn(card_db):
    game = _game(card_db)
    ab = _ability("Put a +1/+1 counter on each creature you control.")
    host = _host(game, card_db, ab)
    assert resolve_activated_ability(game, host, 0, [], ability=ab)
    host.cleanup_damage()
    assert (host.power, host.toughness) == (3, 3)


# ── the auditor sees a recipient the resolution missed ────────────────

@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def _rules(findings):
    return sorted({f["rule"] for f in findings if f["kind"] == "violation"})


def test_team_counter_audit_sees_a_recipient_left_without_its_counter(audit, card_db, monkeypatch):
    from engine import activated_effects
    game = _game(card_db)
    ab = _ability("Put a +1/+1 counter on each creature you control.")
    host = _host(game, card_db, ab)
    _add(game, card_db, "Wall of Omens")
    real = activated_effects._team_counter_recipients

    def _drops_one(*a, **k):
        return real(*a, **k)[:-1]

    monkeypatch.setattr(activated_effects, "_team_counter_recipients", _drops_one)
    resolve_activated_ability(game, host, 0, [], ability=ab)
    assert "122/team_counter_placed" in _rules(rules_audit.drain())


def test_team_counter_audit_is_silent_when_every_recipient_got_its_counter(audit, card_db):
    game = _game(card_db)
    ab = _ability("Put a +1/+1 counter on each creature you control.")
    host = _host(game, card_db, ab)
    _add(game, card_db, "Wall of Omens")
    _add(game, card_db, "Grizzly Bears", 1)
    resolve_activated_ability(game, host, 0, [], ability=ab)
    assert "122/team_counter_placed" not in _rules(rules_audit.drain())


# ── the AI still withholds the class pending a valuation ──────────────

def test_the_ai_withholds_team_put_counter_activations_pending_a_valuation(card_db):
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game
    game = _game(card_db, n_forests=6)
    game.players[0].deck_name = "Creatures Toolbox"
    game.players[1].deck_name = "Dimir Midrange"
    ab = _ability("Put a +1/+1 counter on each creature you control.")
    host = _host(game, card_db, ab)
    _add(game, card_db, "Wall of Omens")
    snap = snapshot_from_game(game, 0)
    assert not any(c[0] is host
                   for c in activation_candidates(game, 0, snap))


# ── class size ────────────────────────────────────────────────────────

def test_the_team_put_counter_class_is_broadly_executable_across_the_pool(card_db):
    hits = [
        (n, ab)
        for n, t in card_db.cards.items()
        for ab in (t.activated_abilities or [])
        if ab.effect_kind is ActivationEffectKind.PUT_COUNTER_TEAM
    ]
    # 22 printed mass put-counter activations; 12 are the plain shape this
    # class executes. The other 10 carry a restriction the card-type
    # vocabulary cannot hold — "with flying/menace/trample/…" (the Mentor
    # cycle), "that entered this turn" (Shaile, Novijen, Raucous
    # Entertainer), "creature token" (Sandstorm Salvager), "and/or Vehicle"
    # (Iron Spider) — and are refused whole rather than half-run.
    assert len(hits) >= 10, (
        f"only {len(hits)} abilities classified as team put-counter; the "
        f"plain printed class is 12 Modern activated abilities")
    assert all(ab.put_counter_data is not None
               and ab.put_counter_data.get('scope') == 'team'
               and ab.targets_required == 0 for _, ab in hits)
