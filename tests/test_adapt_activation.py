"""Adapt (CR 702.132) as an executable activated-ability kind.

"[Cost]: Adapt N." — if this creature has no +1/+1 counters on it, put N
+1/+1 counters on it. 23 Modern cards carry the activated form (Basking
Broodscale, Growth-Chamber Guardian, Incubation Druid, Zegana, ...).
Before this change the line parsed as UNCLASSIFIED: `can_activate` rule 9b
refused it, so the creature never received its counters.

Rules pinned:
  * The effect is typed at LOAD: kind ADAPT, `amount` = N, the printed
    mana cost on `cost`. Reminder text is stripped before the scan.
  * CR 702.132a — resolution puts N +1/+1 counters ONLY when the creature
    has none; with any +1/+1 counter already there it does nothing.
  * The no-counters condition is a RESOLUTION condition, not a legality
    one: activating on an already-adapted creature is legal (the cost is
    paid, the ability resolves as a no-op). Declining that is the AI's
    judgment (it emits no candidate), not a rule.
  * CR 602.2 — the cost is paid at activation, before resolution.
  * Counters land on the instance's own `plus_counters` through the
    instance counter funnel, so `power`/`toughness` move.
  * A line with a second sentence the schema cannot hold ("This ability
    costs {1} less ...") stays UNCLASSIFIED — never half-executed.

Rules-phrased; card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from ai.activation_ev import activation_candidates
from ai.ev_evaluator import snapshot_from_game
from engine.activation import ActivationManager
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance,
                          COUNTER_KIND_PLUS)
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import (classify_activation_effect,
                                  parse_activated_abilities)

_DB = CardDatabase()

_ADAPT_LINE = ("{1}{G}: Adapt 1. (If this creature has no +1/+1 counters "
               "on it, put a +1/+1 counter on it.)")
_ADAPT_TWO_LINE = ("{2}{G}: Adapt 2. (If this creature has no +1/+1 counters "
                   "on it, put two +1/+1 counters on it.)")
_ADAPT_WITH_REDUCTION = (
    "{7}{U}: Adapt 4. This ability costs {1} less to activate for each "
    "instant and sorcery card in your graveyard. (If this creature has no "
    "+1/+1 counters on it, put four +1/+1 counters on it.)")


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


def _game(n_forests=4):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Domain Zoo"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_forests):
        _add(game, "Forest")
    for _ in range(3):
        _add(game, "Forest", 0, "hand")
    for _ in range(8):
        _add(game, "Forest", 0, "library")
    # An opposing body so the position has a combat race to price.
    _add(game, "Grizzly Bears", controller=1)
    return game


def _adapt_ability(n=1, mana=2):
    return ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(generic=mana)),
        effect_text=f"Adapt {n}.",
        effect_kind=ActivationEffectKind.ADAPT, amount=n)


def _host(game, ability, name="Grizzly Bears"):
    """Attach one synthetic ability to a fixture carrier. The template is
    COPIED first: templates are shared DB objects."""
    perm = _add(game, name)
    perm.template = copy.copy(perm.template)
    perm.template.activated_abilities = [ability]
    return perm


# ── parsing: adapt N is a typed effect kind, cost and amount carried ───

def test_adapt_line_classifies_with_its_amount():
    kind, amount, p, t = classify_activation_effect("Adapt 1.")
    assert kind is ActivationEffectKind.ADAPT
    assert amount == 1 and p == 0 and t == 0
    kind, amount, _, _ = classify_activation_effect("Adapt 4")
    assert kind is ActivationEffectKind.ADAPT and amount == 4


def test_adapt_line_parses_into_typed_activated_ability_with_cost():
    """Reminder text stripped; the printed mana cost is the cost."""
    for line, n, cmc in ((_ADAPT_LINE, 1, 2), (_ADAPT_TWO_LINE, 2, 3)):
        abilities = parse_activated_abilities(line)
        assert len(abilities) == 1, line
        ab = abilities[0]
        assert ab.effect_kind is ActivationEffectKind.ADAPT
        assert ab.amount == n
        assert ab.cost.mana.cmc == cmc
        assert ab.cost.unpayable == ()
        assert ab.targets_required == 0
        assert not ab.is_mana_ability


def test_adapt_with_an_unmodelled_cost_reduction_rider_stays_unclassified():
    """A second sentence the schema cannot hold must not be dropped —
    the whole line is refused rather than executed at the printed cost."""
    abilities = parse_activated_abilities(_ADAPT_WITH_REDUCTION)
    assert len(abilities) == 1
    assert abilities[0].effect_kind is ActivationEffectKind.UNCLASSIFIED


def test_every_plain_adapt_line_in_the_pool_is_typed():
    """Pool-wide: every card whose activated line is exactly
    '[Cost]: Adapt N.' carries a typed ADAPT ability after load."""
    import re
    typed = 0
    for tmpl in _DB.cards.values():
        oracle = tmpl.oracle_text or ''
        for ab in (tmpl.activated_abilities or []):
            if re.fullmatch(r'adapt \d+\.?', ab.effect_text.lower()):
                assert ab.effect_kind is ActivationEffectKind.ADAPT, (
                    f"{tmpl.name}: {ab.effect_text!r} → {ab.effect_kind}")
                assert ab.amount > 0
                typed += 1
        if re.search(r'\badapt \d', oracle, re.I):
            assert any(a.effect_kind is ActivationEffectKind.ADAPT
                       or a.effect_kind is ActivationEffectKind.UNCLASSIFIED
                       for a in tmpl.activated_abilities), tmpl.name
    assert typed >= 20, f"only {typed} typed adapt lines in the pool"


# ── legality ──────────────────────────────────────────────────────────

def test_adapt_is_a_legal_activation_when_the_cost_is_covered():
    game = _game()
    ab = _adapt_ability()
    perm = _host(game, ab)
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_adapt_stays_legal_on_an_already_adapted_creature():
    """CR 702.132a conditions the EFFECT, not the activation."""
    game = _game()
    ab = _adapt_ability()
    perm = _host(game, ab)
    perm.adjust_counters(COUNTER_KIND_PLUS, 1)
    assert ActivationManager.can_activate(game, 0, perm, ab)


def test_adapt_is_refused_when_the_mana_is_not_there():
    game = _game(n_forests=1)
    ab = _adapt_ability(mana=2)
    perm = _host(game, ab)
    assert not ActivationManager.can_activate(game, 0, perm, ab)


# ── resolution ────────────────────────────────────────────────────────

def test_adapt_puts_n_counters_on_a_creature_with_none():
    for n in (1, 2, 4):
        game = _game()
        ab = _adapt_ability(n=n)
        perm = _host(game, ab)
        base_p, base_t = perm.power, perm.toughness
        assert perm.plus_counters == 0
        assert ActivationManager.activate(game, 0, perm, ab)
        assert not game.stack.is_empty, "adapt uses the stack (CR 602.2)"
        game.resolve_stack()
        assert perm.plus_counters == n
        assert perm.power == base_p + n and perm.toughness == base_t + n, (
            "counters must reach the instance's P/T, not a side ledger")


def test_adapt_does_nothing_on_a_creature_that_already_has_a_plus_counter():
    """The no-counters condition (CR 702.132a): one existing +1/+1 counter
    — even fewer than N — means nothing is added."""
    game = _game()
    ab = _adapt_ability(n=3)
    perm = _host(game, ab)
    perm.adjust_counters(COUNTER_KIND_PLUS, 1)
    assert ActivationManager.activate(game, 0, perm, ab)
    game.resolve_stack()
    assert perm.plus_counters == 1, (
        f"adapt on an adapted creature must add nothing, got "
        f"{perm.plus_counters}")


def test_adapt_cannot_be_stacked_by_repeated_activation():
    """Two activations resolve to N counters, not 2N — the second
    resolves against a creature that already has counters."""
    game = _game(n_forests=6)
    ab = _adapt_ability(n=2)
    perm = _host(game, ab)
    assert ActivationManager.activate(game, 0, perm, ab)
    game.resolve_stack()
    assert ActivationManager.activate(game, 0, perm, ab)
    game.resolve_stack()
    assert perm.plus_counters == 2


def test_adapt_cost_is_paid_at_activation():
    """CR 602.2 — the mana is charged when the ability is put on the
    stack, before it resolves."""
    game = _game(n_forests=4)
    ab = _adapt_ability(mana=2)
    perm = _host(game, ab)
    untapped_before = sum(1 for l in game.players[0].lands if not l.tapped)
    assert ActivationManager.activate(game, 0, perm, ab)
    untapped_after = sum(1 for l in game.players[0].lands if not l.tapped)
    assert untapped_before - untapped_after == 2, (
        "two mana of cost must tap two lands")
    assert perm.plus_counters == 0, "nothing lands before resolution"
    game.resolve_stack()
    assert perm.plus_counters == 1


# ── AI enumeration ────────────────────────────────────────────────────

def _adapt_cands(game, perm):
    snap = snapshot_from_game(game, 0)
    return [c for c in activation_candidates(game, 0, snap)
            if c[0] is perm]


def test_adapt_is_enumerated_for_the_ai_as_a_permanent_self_pump():
    game = _game()
    ab = _adapt_ability(n=2)
    perm = _host(game, ab)
    cands = _adapt_cands(game, perm)
    assert cands, "an unadapted creature with the mana up must be offered"
    _perm, idx, targets, ev, reason = cands[0]
    assert idx == ab.index and targets == []
    assert ev > 0.0
    assert "adapt" in reason.lower()


def test_ai_does_not_offer_adapt_on_an_already_adapted_creature():
    """Engine-legal but a paid no-op — the AI emits no candidate."""
    game = _game()
    ab = _adapt_ability(n=2)
    perm = _host(game, ab)
    perm.adjust_counters(COUNTER_KIND_PLUS, 1)
    assert not _adapt_cands(game, perm)


def test_adapt_ev_is_the_position_delta_of_the_plus_n_board():
    """The credited value is derived, not tuned: exactly the
    `position_value` gain of the snapshot with N added to both power and
    toughness — the printed N is the only magnitude, and it is the same
    projection for every N."""
    from ai.clock import position_value
    for n in (1, 2, 4):
        game = _game()
        perm = _host(game, _adapt_ability(n=n))
        snap = snapshot_from_game(game, 0)
        expected = (position_value(snap.fast_replace(
            my_power=snap.my_power + n,
            my_toughness=snap.my_toughness + n)) - position_value(snap))
        cands = _adapt_cands(game, perm)
        if expected <= 0.0:
            assert not cands, f"n={n}: a non-improving adapt is not offered"
            continue
        assert cands, f"n={n}: an improving adapt must be offered"
        assert abs(cands[0][3] - expected) < 1e-9, (n, cands[0][3], expected)
