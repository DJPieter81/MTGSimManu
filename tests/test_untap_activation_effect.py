"""Tranche 4b: UNTAP_TARGET_PERMANENT becomes an executable effect kind.

Measured DB-wide before this tranche: 78 parsed activated abilities have an
effect sentence beginning "Untap ...", ALL of them UNCLASSIFIED — the
mana-untapper class (Arbor Elf, Voyaging Satyr, Blossom Dryad) plus the
self-untappers (Devoted Druid, Barrenton Medic) were parsed, refused, and
never executed.

Rules pinned:
  * CR 602 — the effect untaps its declared target; a self-untap ("Untap
    this creature") has no target and untaps its own source.
  * CR 601.2c — a targeted untap needs a legal target to be activated; the
    requirement (type, supertype, subtype) is parsed ONCE at load time into
    `ActivatedAbility.target_requirements` and answered by the shared
    target solver.
  * CR 608.2b — a target that has left the battlefield is skipped; the
    ability simply does nothing rather than untapping something else.
  * No-free-repeatable (rule 9): a tap cost normally terminates a loop
    because the source stays tapped. It does NOT when the ability untaps
    its own source — that shape is refused, because nothing depletes.
  * The composition of tranche 4a and 4b ("Put a -1/-1 counter on this
    creature: Untap this creature") is a genuine Magic loop. It terminates
    on the counter cost, exactly as paper Magic does: toughness reaches 0
    and the zero-toughness SBA (704.5g) removes the source.

Rules-phrased; card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

import pytest

from engine.activation import ActivationManager
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import (classify_activation_effect,
                                  parse_activated_abilities)

_DB = CardDatabase()
_K = ActivationEffectKind


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


def _game(n_forests=3, n_hand=0):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 6
    game.players[0].deck_name = "Amulet Titan"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_forests):
        _add(game, "Forest")
    for _ in range(n_hand):
        _add(game, "Forest", 0, "hand")
    for _ in range(8):
        _add(game, "Forest", 0, "library")
    return game


def _abilities(name):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    return t.activated_abilities or []


def _untap_ability(name):
    got = [a for a in _abilities(name)
           if a.effect_kind is _K.UNTAP_TARGET_PERMANENT]
    assert got, (f"fixture premise: {name} has no classified untap line, "
                 f"got {[(a.effect_text, a.effect_kind) for a in _abilities(name)]}")
    return got[0]


# ── classification ────────────────────────────────────────────────────

def test_targeted_untap_sentence_classifies_as_an_untap_effect():
    kind, _amount, _p, _t = classify_activation_effect("Untap target land.")
    assert kind is _K.UNTAP_TARGET_PERMANENT


def test_self_untap_sentence_classifies_as_an_untap_effect():
    kind, _a, _p, _t = classify_activation_effect("Untap this creature.")
    assert kind is _K.UNTAP_TARGET_PERMANENT


def test_targeted_untap_carries_its_target_requirement_from_load_time():
    ab = _untap_ability("Blossom Dryad")   # {T}: Untap target land.
    assert ab.targets_required == 1
    assert len(ab.target_requirements) == 1
    req = ab.target_requirements[0]
    assert req.zone == "battlefield"
    assert "land" in req.types


def test_self_untap_declares_no_target():
    """'Untap this creature' is not a targeted ability (CR 115.1) — the
    source is the object untapped, so no target is declared."""
    ab = _untap_ability("Devoted Druid")
    assert ab.targets_required == 0
    assert ab.target_requirements == []


def test_subtype_restricted_untap_parses_the_subtype_into_the_requirement():
    """'Untap target Forest' restricts by land SUBTYPE — the restriction
    belongs in the target requirement, not in a per-card branch."""
    ab = _untap_ability("Arbor Elf")
    req = ab.target_requirements[0]
    assert req.subtype == "forest"


def test_untap_shapes_the_schema_cannot_express_stay_unclassified():
    """Each refused shape means something a bare untap would get WRONG:
    'another' excludes the source (untapping the source is a loop the card
    does not have), an Aura reference needs attachment, and mass/variable
    untaps are a different effect."""
    for sentence in ("Untap another target permanent.",
                     "Untap enchanted creature.",
                     "Untap all lands you control.",
                     "Untap two target lands.",
                     "Untap X target lands.",
                     "Untap this creature. Put a +1/+1 counter on it."):
        kind, *_ = classify_activation_effect(sentence)
        assert kind is _K.UNCLASSIFIED, (
            f"{sentence!r} must stay visible-but-refused, got {kind}")


# ── legality ──────────────────────────────────────────────────────────

def test_untap_is_refused_without_a_legal_target():
    """CR 601.2c — no legal target, no activation."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    elf = _add(game, "Arbor Elf")
    ab = _untap_ability("Arbor Elf")
    assert not ActivationManager.can_activate(game, 0, elf, ab), (
        "no Forest on the battlefield — nothing to target")
    _add(game, "Forest")
    assert ActivationManager.can_activate(game, 0, elf, ab)


def test_subtype_restriction_filters_the_legal_targets():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    elf = _add(game, "Arbor Elf")
    _add(game, "Island")   # a land, but not a Forest
    ab = _untap_ability("Arbor Elf")
    assert not ActivationManager.can_activate(game, 0, elf, ab), (
        "'target Forest' must not accept an arbitrary land")


def test_self_untapping_ability_paid_only_by_tapping_itself_is_refused():
    """Tap-then-untap-yourself depletes nothing: the no-free-repeatable
    rule must not accept a tap cost as the terminator when the ability
    untaps its own source."""
    game = _game()
    ab = ActivatedAbility(
        index=0, cost=ActivationCost(mana=ManaCost(), tap_self=True),
        effect_text="Untap this creature.",
        effect_kind=_K.UNTAP_TARGET_PERMANENT)
    perm = _add(game, "Devoted Druid")
    perm.template = copy.copy(perm.template)
    perm.template.activated_abilities = [ab]
    assert not ActivationManager.can_activate(game, 0, perm, ab)


# ── resolution ────────────────────────────────────────────────────────

def test_untap_resolution_untaps_the_declared_target():
    game = _game()
    elf = _add(game, "Arbor Elf")
    forest = game.players[0].lands[0]
    forest.tapped = True
    ab = _untap_ability("Arbor Elf")
    assert ActivationManager.activate(game, 0, elf, ab, [forest.instance_id])
    assert elf.tapped, "the tap half of the cost is charged at activation"
    assert forest.tapped, "the untap happens on RESOLUTION, not on activation"
    game.resolve_stack()
    assert not forest.tapped


def test_self_untap_resolution_untaps_the_source():
    game = _game()
    druid = _add(game, "Devoted Druid")
    druid.tapped = True
    ab = _untap_ability("Devoted Druid")
    assert ActivationManager.activate(game, 0, druid, ab, [])
    game.resolve_stack()
    assert not druid.tapped
    assert druid.minus_counters == 1, "the -1/-1 counter cost was charged"


def test_untap_of_a_target_that_left_the_battlefield_does_nothing():
    """CR 608.2b — an illegal target on resolution is skipped."""
    game = _game()
    elf = _add(game, "Arbor Elf")
    forest = game.players[0].lands[0]
    forest.tapped = True
    other = game.players[0].lands[1]
    other.tapped = True
    ab = _untap_ability("Arbor Elf")
    assert ActivationManager.activate(game, 0, elf, ab, [forest.instance_id])
    game.zone_mgr.move_card(game, forest, "battlefield", "graveyard",
                            cause="test")
    game.resolve_stack()
    assert other.tapped, (
        "a gone target must not silently redirect the untap elsewhere")


# ── AI valuation ──────────────────────────────────────────────────────

def _snap(game):
    from ai.ev_evaluator import snapshot_from_game
    return snapshot_from_game(game, 0)


def test_untapping_a_tapped_mana_source_is_enumerated_as_a_candidate():
    """The value of an untap is the mana it returns this turn — priced on
    the same per-mana clock scale every other activation candidate uses."""
    from ai.activation_ev import activation_candidates

    game = _game()
    elf = _add(game, "Arbor Elf")
    forest = game.players[0].lands[0]
    forest.tapped = True
    cands = activation_candidates(game, 0, _snap(game))
    picked = [c for c in cands if c[0].instance_id == elf.instance_id]
    assert picked, "an untappable tapped mana source must be enumerated"
    assert picked[0][2] == [forest.instance_id], (
        "the chosen target is the tapped mana source, not an untapped one")


def test_untapping_nothing_useful_is_not_enumerated():
    """Every Forest already untapped — the activation returns no mana, so
    the AI does not pay for it."""
    from ai.activation_ev import activation_candidates

    game = _game()
    elf = _add(game, "Arbor Elf")
    cands = activation_candidates(game, 0, _snap(game))
    assert not [c for c in cands if c[0].instance_id == elf.instance_id]


def test_ai_never_untaps_the_source_of_a_tap_cost_activation():
    """Tapping a permanent to untap itself is engine-legal and strategically
    empty — and repeatable. The chooser must never pick the source."""
    from ai.activation_ev import activation_candidates

    game = _game()
    dryad = _add(game, "Blossom Dryad")   # {T}: Untap target land.
    land = game.players[0].lands[0]
    land.tapped = True
    for _p, _idx, tgts, _ev, _r in activation_candidates(game, 0, _snap(game)):
        assert dryad.instance_id not in tgts


# ── composition: counter cost + self-untap (the infinite-mana shape) ──

def test_self_untap_paid_with_minus_counters_terminates_on_the_sba():
    """The classic infinite-mana engine. It is bounded in paper Magic by
    the creature's toughness, and it must be bounded here by the SAME
    mechanism: each activation adds a -1/-1 counter, toughness reaches 0,
    and SBA 704.5g removes the source, which ends the loop.
    """
    game = _game()
    druid = _add(game, "Devoted Druid")
    base_toughness = druid.toughness
    ab = _untap_ability("Devoted Druid")

    fired = 0
    while ActivationManager.can_activate(game, 0, druid, ab) and fired < 50:
        druid.tapped = True          # tapped for mana between activations
        assert ActivationManager.activate(game, 0, druid, ab, [])
        game.resolve_stack()
        game.check_state_based_actions()
        fired += 1

    assert fired == base_toughness, (
        f"the loop runs exactly as many times as the creature has toughness "
        f"to spend (base {base_toughness}, fired {fired})")
    assert druid.zone == "graveyard", (
        "the zero-toughness SBA is what terminates the loop")


@pytest.mark.timeout(60)
def test_main_phase_terminates_with_a_repeatable_self_untap_on_board():
    """A hang here is the failure mode this whole tranche risks: the AI's
    main-phase loop re-picking a profitable, repeatable untap forever. The
    action bound plus the depleting counter cost must close it.
    """
    from engine.game_runner import GameRunner
    from ai.ev_player import EVPlayer

    game = _game(n_forests=4)
    druid = _add(game, "Devoted Druid")
    druid.tapped = True
    base_toughness = druid.toughness

    ai0 = EVPlayer(player_idx=0, deck_name="Amulet Titan",
                   rng=random.Random(0))
    ai1 = EVPlayer(player_idx=1, deck_name="Dimir Midrange",
                   rng=random.Random(0))
    runner = GameRunner.__new__(GameRunner)
    runner._execute_main_phase(game, ai0, ai1)

    assert game.stack.is_empty
    assert getattr(game, '_activation_depth', 0) == 0
    assert druid.minus_counters <= base_toughness, (
        f"the repeatable untap must stop at the creature's toughness "
        f"({druid.minus_counters} counters on a {base_toughness}-toughness "
        f"body means the depleting cost did not bound the loop)")
