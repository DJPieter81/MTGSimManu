"""GRANT_HASTE_TARGET: "[Cost]: Target creature gains haste until end of
turn" becomes an EXECUTABLE activation, end to end.

Before this tranche the effect kind did not exist: the line parsed as
UNCLASSIFIED, `can_activate` rule 9b refused it, and the haste-granting
utility land a ramp deck fetches was inert — a resolved big creature
always converted one full turn late (the Amulet-Titan secondary root
cause: two replayed losses on a perfect curve died exactly one attack
short; see docs/diagnostics/2026-08-26_amulet_titan_rediagnosis.md).

Rules pinned:
  * CR 702.10 — a creature with haste ignores summoning sickness, so the
    grant lets a creature that entered THIS turn attack this turn.
  * CR 514 / until-end-of-turn duration — the granted haste expires in
    the cleanup step (the `temp_keywords` channel, shared with Dash and
    haste-granting reanimation).
  * CR 601.2c — the activation requires a legal target creature; with no
    creature on any battlefield the activation is not legal.
  * The AI values the grant only on its OWN summoning-sick would-be
    attacker (an engine-legal target on the opponent's side converts no
    attack for the activator), and only pre-combat, where the converted
    attack can actually happen.

Rules-phrased; card names are fixture carriers only.
"""
from __future__ import annotations

import random

import pytest

from engine.activation import ActivationManager
from engine.cards import (ActivationEffectKind, CardInstance, CardType,
                          Keyword)
from engine.game_state import GameState, Phase
from engine.oracle_parser import classify_activation_effect

# Fixture carriers (rule is card-name-free; these merely instantiate it).
_HASTE_LAND = "Hanweir Battlements // Hanweir, the Writhing Township"
_BIG_CREATURE = "Primeval Titan"


def _add(game, db, name, controller=0, zone="battlefield", sick=False):
    t = db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = sick
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game(db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 7
    game.players[0].deck_name = "Amulet Titan"
    game.players[1].deck_name = "Boros Energy"
    for _ in range(8):
        _add(game, db, "Forest", 0, "library")
    return game


def _haste_ability(db):
    t = db.get_card(_HASTE_LAND)
    grants = [a for a in (t.activated_abilities or [])
              if a.effect_kind is ActivationEffectKind.GRANT_HASTE_TARGET]
    assert grants, (
        "fixture premise: the haste land's grant line must classify as "
        f"GRANT_HASTE_TARGET; got {[a.effect_kind for a in t.activated_abilities]}")
    return grants[0]


# ── parsing ───────────────────────────────────────────────────────────

def test_haste_grant_effect_classifies_from_the_anchored_sentence():
    kind, amount, p, t = classify_activation_effect(
        "Target creature gains haste until end of turn.")
    assert kind is ActivationEffectKind.GRANT_HASTE_TARGET


def test_composite_or_untargeted_grants_stay_unclassified():
    """A grant with riders is a DIFFERENT effect — executing it as a bare
    haste grant would silently drop the riders."""
    for text in (
            "Target creature gets +2/+0 and gains vigilance and haste "
            "until end of turn.",
            "Creatures you control gain haste until end of turn.",
            "Target creature gains haste until end of turn. "
            "Untap that creature."):
        kind, *_ = classify_activation_effect(text)
        assert kind is not ActivationEffectKind.GRANT_HASTE_TARGET, text


def test_parsed_haste_grant_carries_its_target_requirement(card_db):
    """CR 601.2c: the targeted kind must carry a requirement so legality
    can be checked before the cost is charged."""
    ab = _haste_ability(card_db)
    assert ab.targets_required == 1
    assert ab.target_requirements, "requirement list must be populated"
    req = ab.target_requirements[0]
    assert req.zone == "battlefield" and "creature" in req.types
    assert ab.cost.tap_self and ab.cost.mana.cmc == 1


# ── engine execution ──────────────────────────────────────────────────

def test_haste_grant_lets_a_summoning_sick_creature_attack_this_turn(card_db):
    game = _game(card_db)
    land = _add(game, card_db, _HASTE_LAND)
    _add(game, card_db, "Mountain")  # pays the {R}
    titan = _add(game, card_db, _BIG_CREATURE, sick=True)
    assert not titan.can_attack, "fixture premise: summoning-sick"

    ab = _haste_ability(card_db)
    assert ActivationManager.can_activate(game, 0, land, ab)
    assert ActivationManager.activate(game, 0, land, ab,
                                      [titan.instance_id])
    game.resolve_stack()

    assert Keyword.HASTE in titan.keywords
    assert not titan.has_summoning_sickness
    assert titan.can_attack, (
        "the granted haste must let the creature attack the turn it "
        "entered (CR 702.10)")


def test_granted_haste_expires_at_end_of_turn(card_db):
    game = _game(card_db)
    land = _add(game, card_db, _HASTE_LAND)
    _add(game, card_db, "Mountain")
    titan = _add(game, card_db, _BIG_CREATURE, sick=True)
    ab = _haste_ability(card_db)
    assert ActivationManager.activate(game, 0, land, ab,
                                      [titan.instance_id])
    game.resolve_stack()
    assert Keyword.HASTE in titan.keywords

    game.cleanup_step()

    assert Keyword.HASTE not in titan.keywords, (
        "until-end-of-turn grants expire in the cleanup step (CR 514)")
    assert titan.has_summoning_sickness, (
        "with the grant expired the still-this-turn creature is "
        "summoning-sick again")


def test_haste_grant_requires_a_legal_target_creature(card_db):
    """CR 601.2c — no creature on any battlefield, no activation."""
    game = _game(card_db)
    land = _add(game, card_db, _HASTE_LAND)
    _add(game, card_db, "Mountain")
    ab = _haste_ability(card_db)
    assert not ActivationManager.can_activate(game, 0, land, ab), (
        "a required target with no legal choice must refuse the "
        "activation before any cost is charged")


def test_grant_fizzles_when_the_target_left_the_battlefield(card_db):
    """CR 608.2b — the ability resolves against the declared target's
    snapshot; a departed target means no effect, not a crash."""
    game = _game(card_db)
    land = _add(game, card_db, _HASTE_LAND)
    _add(game, card_db, "Mountain")
    titan = _add(game, card_db, _BIG_CREATURE, sick=True)
    ab = _haste_ability(card_db)
    assert ActivationManager.activate(game, 0, land, ab,
                                      [titan.instance_id])
    # Target leaves before resolution.
    game.zone_mgr.move_card_to_graveyard(game, titan, cause="test removal")
    game.resolve_stack()
    assert Keyword.HASTE not in titan.keywords


# ── AI valuation ──────────────────────────────────────────────────────

def _candidates(game, kind=ActivationEffectKind.GRANT_HASTE_TARGET):
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game
    snap = snapshot_from_game(game, 0)
    out = []
    for perm, ab_idx, tgts, ev, reason in activation_candidates(game, 0, snap):
        ab = perm.template.activated_abilities[ab_idx]
        if ab.effect_kind is kind:
            out.append((perm, ab_idx, tgts, ev, reason))
    return out


def test_ai_values_the_grant_on_its_own_summoning_sick_attacker(card_db):
    game = _game(card_db)
    _add(game, card_db, _HASTE_LAND)
    _add(game, card_db, "Mountain")
    titan = _add(game, card_db, _BIG_CREATURE, sick=True)

    cands = _candidates(game)
    assert cands, "the haste grant must be enumerated as a candidate"
    _perm, _idx, tgts, ev, _r = cands[0]
    assert tgts == [titan.instance_id], (
        "the AI targets its OWN summoning-sick creature")
    assert ev > 0.0, "converting an attack this turn has positive EV"


def test_ai_does_not_value_the_grant_without_a_sick_attacker(card_db):
    game = _game(card_db)
    _add(game, card_db, _HASTE_LAND)
    _add(game, card_db, "Mountain")
    _add(game, card_db, _BIG_CREATURE, sick=False)  # already attackable
    assert not _candidates(game), (
        "hasting an already-attackable creature converts nothing")


def test_ai_does_not_value_the_grant_on_the_opponents_creature(card_db):
    game = _game(card_db)
    _add(game, card_db, _HASTE_LAND)
    _add(game, card_db, "Mountain")
    _add(game, card_db, _BIG_CREATURE, controller=1, sick=True)
    assert not _candidates(game), (
        "an opponent's creature is engine-legal but converts no attack "
        "for the activator")


# ── ordering: activation happens pre-combat and converts the attack ──

def test_cast_then_haste_grant_then_attack_happens_in_one_turn(card_db):
    """End-to-end acceptance: cast the big creature, activate the haste
    grant on it, and be attack-ready — all inside the same turn's main
    phase (the main-phase loop runs activations before combat)."""
    from ai.ev_player import EVPlayer

    game = _game(card_db)
    game.verbose = False
    p0 = game.players[0]
    land = _add(game, card_db, _HASTE_LAND)
    _add(game, card_db, "Mountain")
    # The cast is paid from floated mana so payment cannot tap the haste
    # land or its {R} source (in the real line the haste land arrives
    # untapped AFTER the cast, via the fetch trigger + untap watcher).
    for _ in range(6):
        p0.mana_pool.add("G")
    titan = _add(game, card_db, _BIG_CREATURE, zone="hand")

    ai = EVPlayer(player_idx=0, deck_name="Amulet Titan",
                  rng=random.Random(0))
    saw_cast = saw_activation = False
    for _ in range(6):  # bounded main-phase loop, as game_runner runs it
        decision = ai.decide_main_phase(game)
        if decision is None:
            break
        action, card, targets = decision
        if action == "cast_spell":
            assert game.cast_spell(0, card, targets)
            saw_cast = saw_cast or card is titan
        elif action == "activate":
            idx = ai._last_activation_ability_index
            ab = card.template.activated_abilities[idx]
            assert ActivationManager.can_activate(game, 0, card, ab)
            assert ActivationManager.activate(game, 0, card, ab, targets)
            saw_activation = True
        else:
            break
        while not game.stack.is_empty:
            game.resolve_stack()
        if saw_activation:
            break

    assert saw_cast, "the AI must deploy the big creature"
    assert saw_activation, (
        "the AI must activate the haste grant in the same main phase")
    assert titan.zone == "battlefield" and titan.summoning_sick
    assert titan.can_attack, (
        "cast -> activate haste -> attack must convert in ONE turn")
    attackers = ai.decide_attackers(game)
    assert titan in attackers, (
        "the hasted creature must actually be chosen as an attacker")
