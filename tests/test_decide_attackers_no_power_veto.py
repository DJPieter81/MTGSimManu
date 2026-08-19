"""Phase 3 — retire power-zero categorical veto in decide_attackers.

Rule (mechanic-phrased): the attack-candidate selection MUST NOT exclude
a creature from the attacker pool on the basis of ``power == 0`` alone.
Candidacy is determined by scoring and combat risk, not a categorical
power gate.

Two cases are pinned here:

1. **Evasion removes combat risk** — a creature with evasion (e.g. flying)
   that has no legal blockers is a risk-free attacker.  The current
   ``deals_damage`` gate classifies it into ``non_free`` because its
   power is 0, and it is never included in the attack set.  After
   removing the gate, an evasive creature is classified as a free
   attacker regardless of power: the score decides, not a veto.

2. **On-attack triggers carry independent value** — a creature with
   ``has_attack_trigger=True`` fires its trigger on declaration, before
   any blocking.  The fallback non-free loop in ``decide_attackers``
   checks only ``has_combat_damage_player_trigger`` and requires
   ``power > 0``.  A creature with an on-attack trigger and 0 power is
   never included by the fallback.  After the fix, the fallback also
   evaluates ``has_attack_trigger`` and uses ``opportunity_cost`` (from
   ``ai.clock``) instead of a bare ``power > 0`` gate.

Subsystem: ``ai/ev_player.py::decide_attackers`` (free-attacker
classification and fallback non-free loop).

Knowledge location: ``opportunity_cost`` lives in ``ai/clock.py`` and
is already used in the blocking decision (Phase 2b).  No card names
appear in these tests; all fixtures are synthetic.

Failing-first record: before the fix, the first test fails because the
0-power flyer is placed in ``non_free`` (``deals_damage=False``) and
never added to the attacker set.  The second fails because the fallback
checks only ``has_combat_damage_player_trigger`` — the ``has_attack_trigger``
field is never consulted.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.game_state import GameState, Phase


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_game(my_life: int = 20, opp_life: int = 20,
               my_deck: str = "Boros Energy",
               opp_deck: str = "Dimir Midrange") -> GameState:
    game = GameState(rng=random.Random(42))
    game.current_phase = Phase.MAIN1
    game.turn_number = 4
    game.active_player_idx = 0
    game.players[0].life = my_life
    game.players[1].life = opp_life
    game.players[0].deck_name = my_deck
    game.players[1].deck_name = opp_deck
    return game


def _synthetic_creature(game, name: str, controller: int,
                        power: int = 1, toughness: int = 1,
                        keywords: set | None = None,
                        has_attack_trigger: bool = False,
                        has_combat_damage_player_trigger: bool = False) -> CardInstance:
    """Synthetic creature with no real-card DB dependency.

    ``has_attack_trigger`` and ``has_combat_damage_player_trigger`` are
    set directly on the template (bypassing oracle parsing) so tests
    control exactly which typed fields are present.
    """
    kw_set = set(keywords or set())
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=max(1, power + toughness)),
        power=power,
        toughness=toughness,
        keywords=kw_set,
        oracle_text="",
        tags=set(),
    )
    # Typed fields controlled by test parameters — oracle text is blank so
    # the parser would set these to False; override directly.
    tmpl.has_attack_trigger = has_attack_trigger
    tmpl.has_combat_damage_player_trigger = has_combat_damage_player_trigger

    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _player(deck: str = "Boros Energy") -> EVPlayer:
    return EVPlayer(player_idx=0, deck_name=deck, rng=random.Random(0))


# ─── tests ───────────────────────────────────────────────────────────────────

class TestZeroPowerNoVeto:
    """Power-zero is not a legitimate veto; the score decides inclusion."""

    def test_zero_power_evasive_attacker_is_not_excluded_by_power_gate(self):
        """Evasion removes combat risk; a 0-power flyer with no legal blockers
        must be treated as a free attacker, not categorical-vetoed.

        Setup: player 0 has a 3/3 ground creature (safe attacker) and a 0/2
        flying creature (no triggers).  Opponent has one 2/2 ground creature
        that CANNOT legally block the flyer (no flying/reach).

        The 3/3 is a free attacker (can't die to the 2/2).  The 0/2 flyer
        is equally risk-free — no blocker can legally block it — but the
        current ``deals_damage`` gate (power > 0 OR trigger) puts it into
        ``non_free`` because its power is 0 and it has no triggers.  The
        fallback path never adds 0-power, 0-trigger creatures in non_free
        to the safe list.

        After the fix, the evasion check classifies the flyer as a free
        attacker without requiring ``deals_damage``, so it rides along when
        the 3/3 attacks.
        """
        game = _make_game()
        # Player 0: a safe ground attacker that the planner can include,
        # and a 0-power flyer that the power-gate currently vetoes.
        _synthetic_creature(game, "SafeGround", controller=0,
                            power=3, toughness=3)
        flyer = _synthetic_creature(game, "SilentFlyer", controller=0,
                                    power=0, toughness=2,
                                    keywords={Keyword.FLYING})
        # Opponent: one 2/2 ground creature.  It cannot legally block the
        # flyer, but the ``can_die_to_block`` check (power >= toughness)
        # would rate it as a theoretical kill threat for the 0/2 (2 >= 2).
        # The fix must override this by consulting ``is_evasive``, not
        # just ``deals_damage``.
        _synthetic_creature(game, "GroundBlocker", controller=1,
                            power=2, toughness=2)

        attacker_ids = {c.instance_id
                        for c in _player().decide_attackers(game)}

        assert flyer.instance_id in attacker_ids, (
            "0-power flying creature with no legal ground blockers was "
            "excluded from the attack by the power > 0 veto in "
            "decide_attackers.  A creature with evasion that cannot die "
            "to any legal block is a risk-free attacker; the score — not "
            "a categorical power gate — must decide inclusion."
        )

    def test_attack_candidate_selection_uses_opportunity_cost_not_power_gate(self):
        """has_attack_trigger carries on-declaration value; the fallback path
        must evaluate it via opportunity_cost, not a bare power > 0 gate.

        Setup: player 0 has one 0/1 creature with ``has_attack_trigger=True``
        (but NOT ``has_combat_damage_player_trigger``).  Opponent has a 3/3
        that can kill our creature.  No other positive-power creatures exist,
        so the CombatPlanner returns an empty plan and the fallback path runs.

        Current code: the fallback loop checks ``has_combat_damage_player_trigger``
        only, AND gates on ``power > 0``.  A creature with only
        ``has_attack_trigger`` is never added to the safe list, so ``decide_attackers``
        returns [].

        After the fix: the fallback also evaluates ``has_attack_trigger`` and
        uses ``opportunity_cost`` from ``ai.clock`` to decide whether the
        permanent's future-board value is low enough to justify attacking.
        A 0/1 creature with no keywords or activated abilities has
        opportunity_cost ≈ 1.0 (toughness-only blocker value), which is
        below the ``ATTACK_TRIGGER_OC_MAX`` threshold, so the creature IS
        included.
        """
        game = _make_game()
        # Player 0: one 0/1 creature with has_attack_trigger ONLY.
        trigger_card = _synthetic_creature(
            game, "AttackTriggerSource", controller=0,
            power=0, toughness=1,
            has_attack_trigger=True,
            has_combat_damage_player_trigger=False,
        )
        # Opponent: 3/3 kills our 0/1.  This ensures the creature is in
        # non_free (can_die_to_block=True, no evasion) and the planner
        # skips it (power > 0 gate in plan_attack).
        _synthetic_creature(game, "OppKiller", controller=1,
                            power=3, toughness=3)

        attacker_ids = {c.instance_id
                        for c in _player().decide_attackers(game)}

        assert trigger_card.instance_id in attacker_ids, (
            "A creature with has_attack_trigger=True (but power=0 and no "
            "has_combat_damage_player_trigger) was excluded from the attacker "
            "set by the fallback power > 0 gate.  The fallback path must "
            "evaluate has_attack_trigger via opportunity_cost so on-attack "
            "trigger sources are considered when their board value is low."
        )
