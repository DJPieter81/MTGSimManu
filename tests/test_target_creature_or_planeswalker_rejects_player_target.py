"""R2 — Generic "target creature or planeswalker" damage spells must
reject player as a legal target, route damage to the chosen
creature or planeswalker (not the player), and bound additional-
{E} damage by the chosen target's actual requirement (no magic
caps).

Audit reference: ``docs/history/audits/2026-05-16_rules_audit.md`` R2.

This file names the *mechanic*, not the card. The same rule applies
to the entire FDN/MH3 energy-damage family — Galvanic Discharge,
Static Discharge, future "you get {E}^n, then you may pay any
amount of {E}; deal that much damage to that permanent" spells.
The handler-delete in ``engine/card_effects.py`` lifts the
correct routing to ``engine/oracle_resolver.py`` (generic
oracle-pattern dispatch) and ``engine/target_solver.py`` (compound
``target creature or planeswalker`` legality, line 155).

Oracle text (current ModernAtomic printing of Galvanic Discharge):
  "Choose target creature or planeswalker. You get {E}{E}{E} …,
   then you may pay any amount of {E}. Galvanic Discharge deals
   that much damage to that permanent."

Note the live DB has the OLDER printing — 3 self-generated energy,
no base damage. The newer FDN printing (base 2 + 2 energy) parses
the same way through the generic resolver; both printings flow
through identical code paths.

Five rule-phrased cases:

1. Casting "target creature or planeswalker" with no creature/PW
   on the battlefield is illegal (CR 601.2c). The deleted handler
   silently re-routed to the opponent's face on missing target.

2. A creature is a legal target; damage marks ``damage_marked``.

3. A planeswalker is a legal target; damage decrements
   ``loyalty_counters`` (CR 119.3).

4. Overkill damage cannot splash the player — "target creature or
   planeswalker" forbids player as target.

5. Generated energy count is oracle-derived, not hardcoded. The
   resolver counts {E} tokens in the "you get …" clause.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardType
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _land(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing land: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _hand(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _battlefield(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _setup_main_phase(game) -> None:
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 3
    game.players[0].lands_played_this_turn = 1


class TestTargetCreatureOrPlaneswalkerRejectsPlayer:
    """A spell whose oracle reads "target creature or planeswalker"
    cannot target a player. CR 601.2c — illegal target makes the
    spell uncastable."""

    def test_galvanic_discharge_cannot_target_player(self, card_db):
        """Empty opposing board: no creature, no planeswalker.
        Galvanic Discharge's oracle restricts to "target creature or
        planeswalker"; with neither present, cast_manager must
        reject the cast.

        The deleted handler in ``engine/card_effects.py`` silently
        routed unblocked casts to the opponent's face, masking the
        cast-time illegality."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _land(game, card_db, "Mountain", 0)
        spell = _hand(game, card_db, "Galvanic Discharge", 0)
        # Opponent board: empty (no creatures, no planeswalkers).
        assert game.can_cast(0, spell) is False, (
            "Galvanic Discharge oracle restricts to 'target creature "
            "or planeswalker'. With an empty opposing board, the "
            "spell has no legal target and must be uncastable per "
            "CR 601.2c. The deleted per-card handler in "
            "engine/card_effects.py let the spell hit the player's "
            "face when no creature/PW existed."
        )

    def test_galvanic_discharge_targets_creature_legally(self, card_db):
        """Regression: Galvanic Discharge IS castable when a
        creature is on the opposing battlefield, and the damage
        marks ``damage_marked`` on that creature (not the player's
        life)."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _land(game, card_db, "Mountain", 0)
        target = _battlefield(game, card_db, "Memnite", 1)  # 1/1
        spell = _hand(game, card_db, "Galvanic Discharge", 0)
        opp_life_before = game.players[1].life

        assert game.can_cast(0, spell) is True, (
            "Galvanic Discharge must be castable with a creature on "
            "the opposing battlefield."
        )
        success = game.cast_spell(0, spell, targets=[target.instance_id])
        assert success
        # Resolve the spell.
        game.resolve_stack()

        # Base damage 2 should kill the 1/1 Memnite (no energy needed).
        assert target.zone == "graveyard", (
            f"Galvanic Discharge base damage (2) should kill the 1/1 "
            f"Memnite — target landed on {target.zone}."
        )
        # The opponent's life must be untouched — damage was routed
        # to the creature target, not the player.
        assert game.players[1].life == opp_life_before, (
            f"Galvanic Discharge damaged the player ({opp_life_before} "
            f"-> {game.players[1].life}) when the oracle directs damage "
            f"to the creature target. The deleted handler had a "
            f"face-fallback bug."
        )

    def test_galvanic_discharge_targets_planeswalker_legally(self, card_db):
        """Regression: planeswalker on opposing battlefield is a
        legal target; damage decrements ``loyalty_counters``ber than
        ``damage_marked`` (per CR 119.3 — damage to a planeswalker
        removes loyalty counters).

        Uses Wrenn and Six (single-faced PW, 3 starting loyalty).
        Galvanic Discharge's base damage is 2. The rule under test
        is *target-type routing*, not the AI's energy-spend
        heuristic; the loyalty after resolution must be reduced by
        at least the base damage (2), proving the spell hit the PW
        and not the player.
        """
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _land(game, card_db, "Mountain", 0)
        pw = _battlefield(game, card_db, "Wrenn and Six", 1)
        # PW templates set loyalty_counters in spell_resolution; for
        # battlefield-spawn we mirror that here.
        if pw.loyalty_counters == 0 and (pw.template.loyalty or 0) > 0:
            pw.loyalty_counters = pw.template.loyalty
        loyalty_before = pw.loyalty_counters
        assert loyalty_before == 3, (
            f"Wrenn and Six should have 3 starting loyalty; got "
            f"{loyalty_before}."
        )
        opp_life_before = game.players[1].life
        spell = _hand(game, card_db, "Galvanic Discharge", 0)

        assert game.can_cast(0, spell) is True, (
            "Galvanic Discharge must be castable with a planeswalker "
            "on the opposing battlefield — 'target creature or "
            "planeswalker' accepts both."
        )
        success = game.cast_spell(0, spell, targets=[pw.instance_id])
        assert success
        game.resolve_stack()

        # Damage must have routed to the planeswalker, not the
        # player. Either: loyalty dropped by at least 2 (PW
        # absorbed the damage), or the PW died and went to
        # graveyard (with enough additional energy damage, it dies
        # outright).
        if pw.zone == "graveyard":
            # PW killed by base + additional damage. Acceptable.
            pass
        else:
            assert pw.loyalty_counters <= loyalty_before - 2, (
                f"Galvanic Discharge (2 base damage) should remove "
                f"at least 2 loyalty from the planeswalker. Loyalty "
                f"went from {loyalty_before} to "
                f"{pw.loyalty_counters}."
            )
        assert game.players[1].life == opp_life_before, (
            f"Galvanic Discharge damaged the player ({opp_life_before} "
            f"-> {game.players[1].life}) when the oracle directs "
            f"damage to the planeswalker target."
        )

    def test_overkill_damage_does_not_splash_player(self, card_db):
        """Bug-fix anchor: a "target creature or planeswalker" spell
        cannot redirect leftover damage to the player. The deleted
        handler had a face-fallback that fired whenever AI target
        selection didn't pin a target — overkill damage on the
        creature must be wasted, not splashed onto the controller's
        opponent.

        Setup: 0/2 Ornithopter target, zero pre-cast energy. Base
        damage 2 kills the Ornithopter; the self-generated {E}{E}
        produces 2 additional damage that the resolver may spend
        on the target. That overkill damage stays with the
        graveyard creature; the player's life total never moves.
        """
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _land(game, card_db, "Mountain", 0)
        target = _battlefield(game, card_db, "Ornithopter", 1)  # 0/2
        spell = _hand(game, card_db, "Galvanic Discharge", 0)
        # Caster starts with zero energy.
        assert game.players[0].energy_counters == 0
        opp_life_before = game.players[1].life

        assert game.can_cast(0, spell) is True
        success = game.cast_spell(0, spell, targets=[target.instance_id])
        assert success
        game.resolve_stack()

        # Base damage 2 alone kills Ornithopter (0/2). The
        # additional damage from {E}{E} is overkill, harmlessly
        # absorbed. The structural invariant: "target creature or
        # planeswalker" never splashes face.
        assert target.zone == "graveyard", (
            "Ornithopter (0/2) should die to base damage."
        )
        assert game.players[1].life == opp_life_before, (
            f"Galvanic Discharge splashed the player ({opp_life_before} "
            f"-> {game.players[1].life}). The 'creature or "
            f"planeswalker' target restriction means overkill damage "
            f"is wasted, not redirected to the player."
        )

    def test_damage_bounded_by_self_generated_energy(self, card_db):
        """The total damage dealt is bounded by ``base + self-
        generated-energy + pre-cast-energy``, and the resolver
        commits no more energy than is needed to kill the chosen
        target (cost minimization — a deterministic rule).

        Setup: 5-toughness vanilla Wall of Mist, zero pre-cast
        energy. The live Galvanic Discharge oracle generates 3
        energy with base 0; the resolver spends only what is
        needed to kill. Since 3 damage < 5 toughness, the kill
        target is unreachable, so the rule "spend zero if
        unkillable" applies — the Wall survives.

        The structural invariant the test names: ``damage_marked
        ≤ base + self_generated_energy`` regardless of pre-cast
        energy. No magic caps (the deleted handler's ``min(energy,
        5)`` is gone).
        """
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _land(game, card_db, "Mountain", 0)
        target = _battlefield(game, card_db, "Wall of Mist", 1)  # 0/5
        spell = _hand(game, card_db, "Galvanic Discharge", 0)

        # Zero pre-cast energy: the spell can only spend energy it
        # generates itself.
        assert game.players[0].energy_counters == 0
        assert target.toughness == 5, (
            f"Test precondition: Wall of Mist must be 0/5; got "
            f"toughness={target.toughness}."
        )

        success = game.cast_spell(0, spell, targets=[target.instance_id])
        assert success
        game.resolve_stack()

        # Damage marked must not exceed base + self-generated energy.
        # For the live oracle ({E}{E}{E} = 3, base 0): cap is 3.
        damage = getattr(target, 'damage_marked', 0)
        assert damage <= 3, (
            f"Galvanic Discharge marked {damage} damage on Wall of "
            f"Mist (0/5) with 0 pre-cast energy. The self-generated "
            f"energy is the only cost-pay available; max damage is "
            f"base(0) + self-gen(3) = 3. The deleted handler's "
            f"magic-5 cap could deliver 5 here."
        )
        # Wall of Mist (0/5) survives — 3 damage < 5 toughness, and
        # the resolver spends zero when the target is unkillable
        # (cost-minimization rule).
        assert target.zone == "battlefield", (
            f"Wall of Mist (0/5) was destroyed. Generic energy-damage "
            f"resolver must not spend energy on an unkillable target. "
            f"Zone: {target.zone}."
        )

    def test_self_generated_energy_derived_from_oracle(self, card_db):
        """The energy generated by "you get {E}^k" is derived by
        counting {E} tokens in the oracle, NOT hardcoded. This is
        the structural invariant that makes the resolver work for
        the entire FDN/MH3 energy-instant family without per-card
        knowledge.

        We verify indirectly: after resolution against a small
        creature that dies to a small energy spend, the leftover
        energy equals ``self_generated - spent``. The leftover
        cannot exceed the oracle's {E}-count, regardless of cast
        choices.

        For Memnite (1/1), the resolver kills it by spending 1
        energy (cost-min rule); leftover = 3 - 1 = 2 (live oracle)
        or 2 - 1 = 1 (FDN oracle). Cap: 2 ≤ self-generated ≤ 3.
        """
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _land(game, card_db, "Mountain", 0)
        target = _battlefield(game, card_db, "Memnite", 1)  # 1/1
        spell = _hand(game, card_db, "Galvanic Discharge", 0)
        assert game.players[0].energy_counters == 0

        game.cast_spell(0, spell, targets=[target.instance_id])
        game.resolve_stack()

        # Compute oracle-expected upper bound dynamically — the test
        # passes for both the live (3) and FDN (2) printings.
        import re as _re
        oracle = (spell.template.oracle_text or '').lower()
        gain_match = _re.search(r'you get\s+((?:\{e\}\s*)+)', oracle)
        self_gen = (
            gain_match.group(1).count('{e}') if gain_match else 0)
        leftover = game.players[0].energy_counters
        assert leftover <= self_gen, (
            f"After resolving Galvanic Discharge with 0 pre-cast "
            f"energy, caster holds {leftover} energy. The oracle "
            f"generates {self_gen} energy ({{E}}-count); leftover "
            f"cannot exceed that. The deleted handler's hardcoded "
            f"``energy_counters += 3`` ignored the oracle and would "
            f"leak energy on any printing with k != 3."
        )
        # Sanity: the resolver did do *something* (Memnite died).
        assert target.zone == "graveyard", (
            f"Memnite (1/1) should have been killed by the energy "
            f"spend. Zone: {target.zone}."
        )
