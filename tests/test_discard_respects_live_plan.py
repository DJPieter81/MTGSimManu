"""Discard choice must be evaluated against the live plan and race state.

Replay evidence (docs/diagnostics/2026-08-27_reanimator_pair_root_cause.md,
secondary levers):

- IR s60000 G2: the EOT hand-size discard pitched BOTH castable blockers
  while facing lethal-in-2 — the advisor scored them as graveyard fuel
  "value in isolation" with no term for the race state.
- IR s60500 G2: a looter self-discard binned BOTH reanimation payoffs
  with an opposing graveyard-hate permanent already on the battlefield —
  the graveyard was not a zone the plan could get them back from.
- Goryo's s60000 G3: the loot pitched the deck's protection spells.

Mechanic-phrased rules pinned here (no card names in test NAMES; real DB
cards are only fixture carriers):

1. Under a lethal-range opponent clock (turns-to-death <= 2, the same
   turns-to-lethal idiom `EVSnapshot.opp_clock_discrete` encodes), a
   deployable creature's defensive value enters the discard ranking
   (priced by the existing `ai.clock.opportunity_cost` primitive), and
   graveyard-payoff bonuses — value that assumes future turns — are
   discounted by the existing `EVSnapshot.urgency_factor` survival
   fraction.
2. The LAST accessible copy of a role the gameplan requires (payoffs /
   enablers / protection role buckets, plus the derived FILL_RESOURCE
   reanimation-resource role) is never pitched while the plan is live —
   i.e. while every required role still has at least one reachable copy.
   A copy that stays role-usable from the graveyard after the discard
   (fuel binned into a SAFE graveyard, a flashback spell) is being
   relocated, not lost, so it stays pitchable.
3. Negative control: when the plan is dead (a required role has zero
   reachable copies anywhere), the stranded role card may be pitched.
"""
from __future__ import annotations

import random

from ai.discard_advisor import choose_discard
from engine.cards import CardInstance
from engine.game_state import GameState


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    getattr(game.players[controller],
            zone if zone != "battlefield" else "battlefield").append(card)
    return card


def _reanimator_game(card_db, *, my_lands_on_field=5):
    """Bare game with a reanimator-gameplan deck on player 0."""
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Instant Reanimator"
    for _ in range(my_lands_on_field):
        _add(game, card_db, "Island", controller=0, zone="battlefield")
    return game


class TestBlockerDefensiveValueUnderLethalClock:
    """Rule 1 — a blocker's defensive value enters the discard ranking
    under a lethal clock."""

    def test_castable_blocker_retained_under_lethal_clock(self, card_db):
        """Facing death in <= 2 combat steps, a self-discard must not
        pitch a creature it can deploy as a blocker while spare excess
        lands are available: the graveyard-fuel value of the creature
        assumes future turns that only exist if we block."""
        game = _reanimator_game(card_db)
        # Opposing board: 12 power vs 10 life -> dies to one attack.
        for _ in range(2):
            _add(game, card_db, "Primeval Titan", controller=1,
                 zone="battlefield")
        game.players[0].life = 10

        hand = [
            _add(game, card_db, "Quantum Riddler", 0, "hand"),
            _add(game, card_db, "Quantum Riddler", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
        ]

        pick = choose_discard(game, 0, hand, self_discard=True)
        assert pick is not None
        assert pick.name != "Quantum Riddler", (
            f"Discard pitched a castable blocker ({pick.name}) while "
            f"facing a lethal-range clock with excess lands in hand. "
            f"Defensive value must enter the ranking under a lethal "
            f"clock (IR s60000 G2 replay shape)."
        )
        assert pick.template.is_land, (
            f"Expected an excess land to be the discard under a lethal "
            f"clock; got {pick.name}."
        )

    def test_same_hand_without_clock_keeps_fuel_ranking(self, card_db):
        """Control: with no opposing pressure the graveyard-value
        ranking is unchanged — the big creature outranks excess
        lands, exactly as before."""
        game = _reanimator_game(card_db)
        game.players[0].life = 20

        hand = [
            _add(game, card_db, "Quantum Riddler", 0, "hand"),
            _add(game, card_db, "Quantum Riddler", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
        ]

        pick = choose_discard(game, 0, hand, self_discard=True)
        assert pick is not None and pick.name == "Quantum Riddler", (
            f"With no lethal clock the graveyard-value ranking must be "
            f"unchanged; expected the big creature, got {pick.name}."
        )


class TestLastAccessibleRoleCopyProtectedWhilePlanLive:
    """Rule 2 — the last accessible copy of a required role is not
    pitched while the plan is live."""

    def test_last_enabler_copy_not_pitched_while_plan_live(self, card_db):
        """The hand holds the only reachable copy of the plan's
        enabler role (library holds payoff + reanimation resource but
        no other enabler).  Pitching it strands a live plan, no matter
        how the card scores in isolation."""
        game = _reanimator_game(card_db, my_lands_on_field=3)
        # Plan is live: payoff spell + reanimation resource reachable.
        _add(game, card_db, "Goryo's Vengeance", 0, "library")
        _add(game, card_db, "Griselbrand", 0, "library")
        for _ in range(5):
            _add(game, card_db, "Island", 0, "library")

        hand = [
            # Only enabler-role copy anywhere (looter bucket member,
            # not a declared keystone — isolation score is high).
            _add(game, card_db, "Quantum Riddler", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
        ]

        pick = choose_discard(game, 0, hand, self_discard=True)
        assert pick is not None
        assert pick.name != "Quantum Riddler", (
            f"Discard pitched the LAST accessible enabler-role copy "
            f"({pick.name}) while the plan (payoff + resource in "
            f"library) was still live."
        )

    def test_gy_hate_strands_resource_so_last_copy_is_kept(self, card_db):
        """An opposing graveyard-hate permanent makes the graveyard
        unable to hold the reanimation resource: binning the only
        reachable resource creature no longer relocates it — it loses
        it.  The last copy must be kept (IR s60500 G2 replay shape:
        both payoffs binned under an active hate permanent)."""
        game = _reanimator_game(card_db, my_lands_on_field=3)
        # Fixture carrier re-pointed: the rule is "an opposing permanent
        # that stops the graveyard holding the resource". Territorial Kavu
        # used to satisfy it only because the old gate asked the broad
        # `has_graveyard_hate` field — its graveyard exile is a modal ATTACK
        # TRIGGER ("exile up to one target card"), not hate. Leyline of the
        # Void is the literal carrier of the rule: cards never arrive.
        # See tests/test_graveyard_threat_is_an_actual_removal_ability.py.
        _add(game, card_db, "Leyline of the Void", controller=1,
             zone="battlefield")
        # Plan otherwise live: enabler + payoff spell in library.
        _add(game, card_db, "Faithful Mending", 0, "library")
        _add(game, card_db, "Goryo's Vengeance", 0, "library")
        for _ in range(5):
            _add(game, card_db, "Island", 0, "library")

        hand = [
            _add(game, card_db, "Griselbrand", 0, "hand"),
            _add(game, card_db, "Thoughtseize", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
            _add(game, card_db, "Swamp", 0, "hand"),
        ]

        pick = choose_discard(game, 0, hand, self_discard=True)
        assert pick is not None
        assert pick.name != "Griselbrand", (
            f"Discard binned the only reachable reanimation resource "
            f"into a graveyard patrolled by opposing hate; the plan "
            f"dies with it. Picked {pick.name!r}."
        )

    def test_payoff_binned_into_safe_graveyard_stays_pitchable(self, card_db):
        """Regression guard for the GV-1 fuel plan: with NO opposing
        graveyard hate, binning the resource creature relocates it to
        a zone the plan reads from — the guard must not block it."""
        game = _reanimator_game(card_db, my_lands_on_field=3)
        _add(game, card_db, "Faithful Mending", 0, "library")
        _add(game, card_db, "Goryo's Vengeance", 0, "library")
        for _ in range(5):
            _add(game, card_db, "Island", 0, "library")

        hand = [
            _add(game, card_db, "Griselbrand", 0, "hand"),
            _add(game, card_db, "Thoughtseize", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
            _add(game, card_db, "Swamp", 0, "hand"),
        ]

        pick = choose_discard(game, 0, hand, self_discard=True)
        assert pick is not None and pick.name == "Griselbrand", (
            f"Safe graveyard: binning the reanimation resource IS the "
            f"plan (GV-1); expected it as the pick, got {pick.name}."
        )


class TestDeadPlanReleasesStrandedRole:
    """Rule 3 — negative control: a dead plan releases the stranded
    role card for discard."""

    def test_dead_plan_hand_may_pitch_stranded_role(self, card_db):
        """No copy of the payoff role is reachable anywhere (hand,
        library, graveyard) — the plan is dead, so the resource
        creature is not protected and the isolation ranking applies."""
        game = _reanimator_game(card_db, my_lands_on_field=3)
        # Library holds only lands: payoff role unreachable, plan dead.
        for _ in range(5):
            _add(game, card_db, "Island", 0, "library")

        hand = [
            _add(game, card_db, "Griselbrand", 0, "hand"),
            _add(game, card_db, "Thoughtseize", 0, "hand"),
            _add(game, card_db, "Island", 0, "hand"),
            _add(game, card_db, "Swamp", 0, "hand"),
        ]

        pick = choose_discard(game, 0, hand, self_discard=True)
        assert pick is not None and pick.name == "Griselbrand", (
            f"Plan is dead (no payoff-role copy reachable): the "
            f"stranded resource creature may be pitched; got "
            f"{pick.name}."
        )
