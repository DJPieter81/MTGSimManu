"""Blink timing must respect a pending attack (CR 400.7 × combat).

Mechanic: a blink returns the permanent as a NEW object — summoning-sick,
carrying only its printed keywords. Therefore a pre-combat blink of a
creature that would otherwise attack THIS turn (untapped, not sick or
temporarily hasty) forfeits that attack. The scorer must charge the
blink the attack it forfeits — the creature's expected combat
contribution this turn, power-derived via the existing clock primitives
(lifelink's survival swing included) — not a flat nudge. When the
forfeit charge exceeds the protection/value terms, the blink waits for
Main 2, which the play loop re-enumerates after combat.

Root cause: docs/diagnostics/2026-08-27_reanimator_pair_root_cause.md —
in 4/4 observed assembled reanimation lines the blink resolved in
Main 1 under the protection goal, the temporary haste grant died with
the old object, and the reanimated body's whole swing was forfeited
(once with lethal on board). The old flat BLINK_M1_HOLD_PENALTY (2.0)
was empirically never decisive.

Class size: every blink/flicker spell × every attack-capable creature
(temporary-haste reanimation riders, freshly-hasted threats, any
EOT-exile-rider shape — Goryo's Vengeance / Sneak Attack / Through the
Breach class). Fixture carriers are real-DB cards; the implementation
names none of them.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import creature_threat_value, snapshot_from_game
from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.permanent_effects import PermanentEffects

DECK = "Goryo's Vengeance"     # gameplan flags its blink as reactive_only
BLINK = "Ephemerate"           # oracle-derived 'blink' tag, {W} instant
FATTY = "Griselbrand"          # 7/7 flying lifelink — no printed haste
WHITE_SOURCE = "Plains"
OPP_BLOCKER = "Ocelot Pride"   # small real-DB creature for opposing board


def _make_game():
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = DECK
    game.players[1].deck_name = "Boros Energy"
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "graveyard":
        game.players[controller].graveyard.append(card)
    return card


def _setup(card_db, with_rider: bool = True):
    """Untapped white source + blink in hand + a big creature that was
    reanimated with temporary haste under a live EOT-exile rider (the
    assembled reanimation line), or plainly on the battlefield."""
    game = _make_game()
    land = _add(game, card_db, WHITE_SOURCE, 0, "battlefield")
    land.tapped = False
    blink = _add(game, card_db, BLINK, 0, "hand")
    assert 'blink' in getattr(blink.template, 'tags', set())

    if with_rider:
        body = _add(game, card_db, FATTY, 0, "graveyard")
        PermanentEffects.reanimate(
            game, 0, body, exile_at_eot=True, give_haste=True)
        assert body.zone == "battlefield"
        assert body.can_attack, "temp-hasted rider must be attack-capable"
        assert game._end_of_turn_exiles, "rider was not registered"
    else:
        body = _add(game, card_db, FATTY, 0, "battlefield")

    return game, blink, body


def _score(player, game, blink):
    snap = snapshot_from_game(game, 0)
    return player._score_spell(
        blink, snap, game, game.players[0], game.players[1]), snap


class TestBlinkTimingRespectsPendingAttack:

    def test_precombat_blink_is_charged_the_attack_it_forfeits(
            self, card_db):
        """The Main-1 score must sit BELOW the Main-2 score by at least
        the rider's threat credit PLUS the forfeited-attack charge —
        and the charge itself must be the clock-derived price of one
        combat step (power fraction of a kill + lifelink swing), not a
        flat nudge."""
        from ai.clock import forfeited_attack_clock_impact
        from ai.scoring_constants import CLOCK_IMPACT_LIFE_SCALING

        game, blink, body = _setup(card_db, with_rider=True)
        player = EVPlayer(player_idx=0, deck_name=DECK,
                          rng=random.Random(0))

        game.current_phase = Phase.MAIN2
        ev_main2, _ = _score(player, game, blink)

        game.current_phase = Phase.MAIN1
        ev_main1, snap1 = _score(player, game, blink)

        credit = creature_threat_value(body, snap1)
        kws = {str(getattr(k, 'value', k)).lower() for k in body.keywords}
        charge = (forfeited_attack_clock_impact(body.power or 0, kws, snap1)
                  * CLOCK_IMPACT_LIFE_SCALING)
        assert charge > 0, "an attack-capable body must have a real charge"
        assert ev_main2 - ev_main1 >= credit + charge - 1e-6, (
            f"pre-combat blink must be charged the attack it forfeits "
            f"(withheld credit {credit:.2f} + forfeit charge {charge:.2f}); "
            f"got MAIN1={ev_main1:.2f} vs MAIN2={ev_main2:.2f} "
            f"(gap {ev_main2 - ev_main1:.2f})"
        )

    def test_forfeit_charge_derives_from_power_and_lifelink_not_a_flat_nudge(
            self, card_db):
        """Derivation pin: the charge scales with the forfeited combat
        step — a 7-power lifelink attack into a low life total prices
        near a full kill, far above any flat tie-breaker nudge."""
        from ai.clock import forfeited_attack_clock_impact
        from ai.scoring_constants import CLOCK_IMPACT_LIFE_SCALING

        game, _blink, body = _setup(card_db, with_rider=True)
        game.players[1].life = 3  # replayed failure: opp at 3, attack lethal
        snap = snapshot_from_game(game, 0)
        kws = {str(getattr(k, 'value', k)).lower() for k in body.keywords}
        charge = (forfeited_attack_clock_impact(body.power or 0, kws, snap)
                  * CLOCK_IMPACT_LIFE_SCALING)
        # A lethal attack forfeited = a full kill fraction — at least the
        # full life-scaling unit, dwarfing the old 2.0 flat penalty.
        assert charge >= CLOCK_IMPACT_LIFE_SCALING, (
            f"forfeiting a lethal attack must price at a full kill "
            f"fraction; got {charge:.2f}"
        )

    def test_blink_of_hasty_attacker_waits_for_post_combat(self, card_db):
        """Decision layer: with the line assembled in Main 1 (temp-hasted
        rider + blink + open mana), the chosen play must NOT be the
        blink — it waits for Main 2."""
        game, blink, _body = _setup(card_db, with_rider=True)
        game.current_phase = Phase.MAIN1
        player = EVPlayer(player_idx=0, deck_name=DECK,
                          rng=random.Random(0))

        decision = player.decide_main_phase(game)

        if decision is not None:
            action, card, _targets = decision
            assert not (action == "cast_spell"
                        and card.instance_id == blink.instance_id), (
                "pre-combat blink of a temporarily hasty attacker was "
                "chosen in Main 1 — it forfeits the attack (CR 400.7 new "
                "object, no haste) and must wait for post-combat"
            )

    def test_post_combat_blink_keeps_its_protection_value(self, card_db):
        """Main 2, rider tapped from its attack: the blink must still be
        credited the saved body's threat value and be chosen — the
        forfeit charge never applies post-combat."""
        game, blink, body = _setup(card_db, with_rider=True)
        body.tapped = True  # attacked in combat
        game.current_phase = Phase.MAIN2
        player = EVPlayer(player_idx=0, deck_name=DECK,
                          rng=random.Random(0))

        ev_with, snap = _score(player, game, blink)

        game_n, blink_n, _ = _setup(card_db, with_rider=False)
        game_n.current_phase = Phase.MAIN2
        player_n = EVPlayer(player_idx=0, deck_name=DECK,
                            rng=random.Random(0))
        ev_without, _ = _score(player_n, game_n, blink_n)

        credit = creature_threat_value(body, snap)
        assert credit > 0
        assert ev_with >= ev_without + credit, (
            f"post-combat blink lost its protection value: "
            f"with-rider={ev_with:.2f} vs no-rider={ev_without:.2f}, "
            f"expected credit ≥ {credit:.2f}"
        )

        decision = player.decide_main_phase(game)
        assert decision is not None
        action, card, _targets = decision
        assert action == "cast_spell" and card.instance_id == blink.instance_id, (
            f"Main-2 blink must be chosen to keep the body; got "
            f"{action}: {getattr(card, 'name', card)}"
        )

    def test_end_step_blink_on_opponents_turn_is_unaffected(self, card_db):
        """On the opponent's turn there is no attack of ours to forfeit:
        the blink keeps its full rider-clearance credit and no
        forfeit charge applies — even in the opponent's Main 1."""
        game, blink, body = _setup(card_db, with_rider=True)
        game.active_player = 1
        player = EVPlayer(player_idx=0, deck_name=DECK,
                          rng=random.Random(0))

        game_n, blink_n, _ = _setup(card_db, with_rider=False)
        game_n.active_player = 1

        player_n = EVPlayer(player_idx=0, deck_name=DECK,
                            rng=random.Random(0))

        for phase in (Phase.END_STEP, Phase.MAIN1):
            game.current_phase = phase
            ev_with, snap = _score(player, game, blink)
            game_n.current_phase = phase
            ev_without, _ = _score(player_n, game_n, blink_n)
            credit = creature_threat_value(body, snap)
            assert ev_with >= ev_without + credit, (
                f"[{phase}] opponent-turn blink was charged or lost its "
                f"credit: with-rider={ev_with:.2f} vs "
                f"no-rider={ev_without:.2f}, expected credit ≥ {credit:.2f}"
            )

    def test_reanimated_hasty_body_attacks_then_blink_is_cast_post_combat_same_turn(
            self, card_db):
        """End-to-end pin across one turn's phases: the reanimated
        temp-hasted body is (1) NOT blinked in Main 1, (2) declared as
        an attacker, and (3) the blink IS offered and chosen in Main 2
        of the SAME turn — the play loop re-enumerates it post-combat."""
        game, blink, body = _setup(card_db, with_rider=True)
        player = EVPlayer(player_idx=0, deck_name=DECK,
                          rng=random.Random(0))

        # (1) Main 1: the blink is not the chosen play.
        game.current_phase = Phase.MAIN1
        decision = player.decide_main_phase(game)
        if decision is not None:
            action, card, _targets = decision
            assert not (action == "cast_spell"
                        and card.instance_id == blink.instance_id), (
                "blink chosen in Main 1 — forfeits the reanimated swing")

        # (2) Combat: the temp-hasted body attacks.
        game.current_phase = Phase.DECLARE_ATTACKERS
        attackers = player.decide_attackers(game)
        assert body in attackers, (
            f"the temp-hasted reanimated body must swing; attackers: "
            f"{[c.name for c in attackers]}"
        )
        body.tapped = True  # declared attacker taps

        # (3) Main 2, same turn: the blink is offered and chosen.
        game.current_phase = Phase.MAIN2
        decision = player.decide_main_phase(game)
        cast_names = [p.card.name for p in player._last_candidates
                      if p.action == "cast_spell"]
        assert blink.name in cast_names, (
            f"blink not re-offered post-combat; candidates: {cast_names}")
        assert decision is not None
        action, card, _targets = decision
        assert action == "cast_spell" and card.instance_id == blink.instance_id, (
            f"post-combat blink must be cast to keep the body past the "
            f"EOT-exile rider; got {action}: {getattr(card, 'name', card)}"
        )

    def test_assembled_line_with_lethal_on_board_does_not_blink_in_main_one(
            self, card_db):
        """Reconstructed replayed failure (worst case): opponent at 3
        life, their blockers tapped, our temp-hasted rider's attack is
        lethal — the chosen Main-1 play must NOT be the blink."""
        game, blink, _body = _setup(card_db, with_rider=True)
        game.players[1].life = 3
        for _ in range(3):
            blocker = _add(game, card_db, OPP_BLOCKER, 1, "battlefield")
            blocker.tapped = True  # tapped out from their own attack
        game.current_phase = Phase.MAIN1
        player = EVPlayer(player_idx=0, deck_name=DECK,
                          rng=random.Random(0))

        decision = player.decide_main_phase(game)

        if decision is not None:
            action, card, _targets = decision
            assert not (action == "cast_spell"
                        and card.instance_id == blink.instance_id), (
                "with on-board lethal available, Main 1 blink was still "
                "chosen — the forfeit charge must dominate every value "
                "term when the forfeited attack is lethal"
            )
