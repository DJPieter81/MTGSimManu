"""Burn spells must enumerate planeswalkers as targets and route
damage to loyalty when the spell can target a planeswalker.

# Mechanic the test names

Burn / "deal N damage to any target" / "deal N damage to creature or
planeswalker" spells must:

1. include opp's planeswalkers in their target-candidate enumeration
   alongside opp's creatures and opp's face,
2. compare PW kills against creature kills and face damage via a
   single scoring formula (no three-branch heuristic — one comparator),
3. apply N damage to a chosen planeswalker target by reducing its
   loyalty counters by N (engine routing), so a 3-loyalty Teferi dies
   to a 3-damage spell.

This is M10 from the 2026-05-16 5-panel Bo3 audit (Aggro Pattern D /
Fix 4): Boros Energy sent Galvanic Discharge to face on a 3-loyalty
Teferi board because the target enumerator iterated `opp.creatures`
only.  The fix lifts the candidate set to `creatures + planeswalkers`
(when the spell can target a PW) and lifts the engine resolver to
route damage to PW loyalty when the chosen target is a PW.

# Class size

Every burn / direct-damage spell that can target "any target" or
"creature or planeswalker": Lightning Bolt, Lightning Helix, Galvanic
Discharge, Galvanic Blast, Unholy Heat, Lava Dart, Roiling Vortex,
Skewer the Critics, Tribal Flames, Boros Charm, Searing Blaze, Fury
ETB, Phlage ETB, Walking Ballista, and every future printing. Modern
pool ~150+ direct-damage spells; class size clears the abstraction
floor (10) by an order of magnitude.

# Generic by oracle

The target enumerator checks oracle text for "planeswalker" or "any
target" — no card-name conditionals.  Damage routing uses the new
`engine.damage.deal_damage` primitive which dispatches by the target
permanent's card type (creature → damage_marked, planeswalker →
loyalty_counters), not by spell name.

# Scoring derivation

PW threat is derived from the already-existing
``PLANESWALKER_BASE_VALUE`` (loss of board threat-of-activation when
the PW dies) and ``PLANESWALKER_LOYALTY_VALUE`` (loss of one future
activation per loyalty knocked off when the PW survives). No new
constants, no card-name overrides.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState


def _add_to_battlefield(game, card_db, name, controller, loyalty=None):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    # Initialise PW loyalty from template if not explicitly overridden.
    from engine.cards import CardType
    if CardType.PLANESWALKER in tmpl.card_types:
        card.loyalty_counters = (loyalty if loyalty is not None
                                  else (tmpl.loyalty or 0))
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


class TestBurnTargetsPlaneswalkerWhenLoyaltyLeDamage:
    """The burn-target enumerator must treat planeswalkers as candidate
    targets when the spell's oracle text permits it."""

    def test_galvanic_discharge_kills_3_loyalty_teferi_over_face(
            self, card_db):
        """Audit scenario: opp at 17 life, Teferi at 3 loyalty, no
        meaningful creatures. Galvanic Discharge for 3 → target Teferi
        (loyalty 3 ≤ damage 3 = lethal kill) rather than 3 face.

        Killing a 3-loyalty Teferi removes a planeswalker engine with
        4 CMC of board value; 3 face damage from 17 → 14 is far less
        impactful in tempo terms. The single scoring formula must
        prefer the PW kill.
        """
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Sacred Foundry", controller=0)
        discharge = _add_to_hand(game, card_db, "Galvanic Discharge",
                                  controller=0)
        # Opp has Teferi at 3 loyalty (one activation already used)
        teferi = _add_to_battlefield(game, card_db, "Teferi, Time Raveler",
                                      controller=1, loyalty=3)
        game.players[1].life = 17
        # Opp side has nothing else worth killing.

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        targets = player._choose_targets(game, discharge)
        assert targets == [teferi.instance_id], (
            f"Galvanic Discharge should target Teferi (loyalty 3, "
            f"killable by 3 damage) rather than going face. "
            f"Got targets={targets}, teferi.instance_id="
            f"{teferi.instance_id}. M10 fix: the target enumerator "
            f"must include opp.planeswalkers when the spell can "
            f"target a planeswalker; the scoring formula must "
            f"compare PW kill value (base + remaining loyalty) "
            f"against face damage value."
        )

    def test_burn_to_face_when_opp_low_life_no_pw_threat(self, card_db):
        """Regression anchor: at low opp life with no PW on board, face
        damage still wins. The PW-extension must not regress the
        existing low-life-go-face heuristic."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Mountain", controller=0)
        discharge = _add_to_hand(game, card_db, "Galvanic Discharge",
                                  controller=0)
        # Opp at 4 life — well within burn-kill range.
        game.players[1].life = 4
        # No creatures, no planeswalkers on opp side.

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        targets = player._choose_targets(game, discharge)
        assert targets == [-1], (
            f"With opp at 4 life and no PW/creature on board, face "
            f"is the only valuable target. Got targets={targets}. "
            f"The PW enumerator extension must not change behaviour "
            f"in the absence of PWs."
        )

    def test_burn_targets_pw_when_advantage_per_turn_exceeds_face_value(
            self, card_db):
        """Generic-formula case: a high-loyalty PW (Teferi at full 4
        loyalty) on an otherwise-empty opp board should still attract
        a burn spell over face, because the PW's base + loyalty value
        outranks 3 face damage from opp life 20.

        This is the formula-level assertion — no special "Teferi" or
        "Galvanic Discharge" branches; the same comparison applies to
        any PW vs any burn spell that can hit a PW.
        """
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Sacred Foundry", controller=0)
        discharge = _add_to_hand(game, card_db, "Galvanic Discharge",
                                  controller=0)
        # Teferi at full loyalty (4) — well above the 3 damage,
        # so it survives but loses 3 of its 4 future activations.
        teferi = _add_to_battlefield(game, card_db, "Teferi, Time Raveler",
                                      controller=1, loyalty=4)
        game.players[1].life = 20

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        targets = player._choose_targets(game, discharge)
        # Burning the PW down to 1 loyalty removes 3 of its 4 future
        # activations. The formula should value that above 3 face
        # damage at 20 life (which barely moves opp's clock).
        assert targets == [teferi.instance_id], (
            f"With opp at 20 life and a 4-loyalty Teferi on board, "
            f"3 damage chipped off loyalty removes 75% of the PW's "
            f"future activations — a bigger swing than 3 face damage. "
            f"Got targets={targets}, teferi={teferi.instance_id}. "
            f"This is the formula-level rule: per-loyalty value "
            f"outranks per-life-point value when the PW is high-impact."
        )

    def test_pw_target_enumerated_alongside_creatures_and_face(
            self, card_db):
        """The candidate enumeration must include all three: face (-1),
        opp creatures, AND opp planeswalkers, when the spell oracle
        permits a PW target. This is the structural assertion that
        the candidate set is correct, independent of which one wins.

        Encodes the rule: "any target" / "creature or planeswalker"
        oracle text → PWs are in the candidate set.
        """
        from ai.ev_player import EVPlayer
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Sacred Foundry", controller=0)
        discharge = _add_to_hand(game, card_db, "Galvanic Discharge",
                                  controller=0)
        # Opp has both a creature AND a planeswalker
        memnite = _add_to_battlefield(game, card_db, "Memnite",
                                       controller=1)
        teferi = _add_to_battlefield(game, card_db, "Teferi, Time Raveler",
                                      controller=1, loyalty=4)
        game.players[1].life = 20

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        candidates = player._enumerate_burn_targets(game, discharge,
                                                    damage=3)
        # Candidate set must contain face, the creature, AND the PW.
        # Each entry is (target_id, value, reason).
        candidate_ids = {entry[0] for entry in candidates}
        assert -1 in candidate_ids, (
            f"Face must be enumerated as a candidate target. "
            f"Got: {candidate_ids}")
        assert memnite.instance_id in candidate_ids, (
            f"Killable creature {memnite.instance_id} (Memnite) must "
            f"be in candidates. Got: {candidate_ids}")
        assert teferi.instance_id in candidate_ids, (
            f"Killable planeswalker {teferi.instance_id} (Teferi, "
            f"loyalty 4 ≥ 3 damage = chip damage value) must be in "
            f"candidates. Got: {candidate_ids}. M10 fix: extend the "
            f"enumerator to iterate opp.planeswalkers."
        )
