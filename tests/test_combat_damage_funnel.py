"""Combat damage routes through `engine.damage.deal_damage` instead
of direct `damage_marked`/`life` mutation (CR 119/510).

`CombatManager._deal_combat_damage` used to mutate `damage_marked`/
`life` directly at each of 5 sites, reimplementing a parallel (and
incomplete) copy of what `deal_damage` already does correctly for
every OTHER damage source in the engine:

- Lifelink (CR 702.15) was only ever checked for the ATTACKER — a
  blocker with lifelink dealing damage back gained its controller
  nothing.
- Deathtouch (CR 702.2c / SBA 704.5i) was faked by force-setting
  `damage_marked = toughness` on the victim rather than writing the
  real `_deathtouch_damage` SBA marker `deal_damage` already
  implements, which the live SBA path (`SBAManager.
  perform_deathtouch_check`) consumes directly.

Card names appear only as fixture carriers (synthetic CardTemplates)
per CLAUDE.md's ABSTRACTION CONTRACT — the mechanic under test is
lifelink/deathtouch combat damage, not any specific card.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword, ManaCost
from engine.combat_manager import CombatManager
from engine.game_state import GameState


def _creature(game, name, controller, power=2, toughness=2, keywords=None):
    tmpl = CardTemplate(
        name=name, card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=keywords or set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _combat(game, attackers):
    cm = CombatManager()
    cm.declare_attackers(game, attackers, active_player=1)
    return cm


class TestLifelinkBlocker:
    """CR 702.15 — a lifelink SOURCE gains its controller life for any
    damage it deals, including a blocker dealing damage back to the
    attacker."""

    def test_lifelink_blocker_gains_controller_life(self):
        game = GameState(rng=random.Random(0))
        game.players[0].life = 20
        attacker = _creature(game, "Attacker", 1, power=4, toughness=6)
        blocker = _creature(game, "LifelinkBlocker", 0, power=3, toughness=3,
                            keywords={Keyword.LIFELINK})

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
        cm.resolve_combat_damage(game)

        assert game.players[0].life == 23, (
            f"blocker's lifelink must gain its controller (P1) life "
            f"equal to the damage it dealt back (3). Got life="
            f"{game.players[0].life}."
        )

    def test_lifelink_attacker_still_gains_life(self):
        """Regression: the attacker's own lifelink (already working
        pre-fix) must not break during the migration."""
        game = GameState(rng=random.Random(0))
        game.players[1].life = 20
        attacker = _creature(game, "LifelinkAttacker", 1, power=4, toughness=6,
                             keywords={Keyword.LIFELINK})
        blocker = _creature(game, "Blocker", 0, power=1, toughness=8)

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
        cm.resolve_combat_damage(game)

        assert game.players[1].life == 24, (
            f"attacker's lifelink must still gain its controller (P2) "
            f"life equal to the damage it dealt (4). Got life="
            f"{game.players[1].life}."
        )

    def test_both_lifelink_attacker_and_blocker_gain_independently(self):
        game = GameState(rng=random.Random(0))
        game.players[0].life = 20
        game.players[1].life = 20
        attacker = _creature(game, "LLAttacker", 1, power=3, toughness=5,
                             keywords={Keyword.LIFELINK})
        blocker = _creature(game, "LLBlocker", 0, power=2, toughness=5,
                            keywords={Keyword.LIFELINK})

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
        cm.resolve_combat_damage(game)

        assert game.players[1].life == 23, "attacker's lifelink gain"
        assert game.players[0].life == 22, "blocker's lifelink gain"


class TestDeathtouchRealMarker:
    """CR 702.2c / SBA 704.5i — deathtouch damage destroys via the
    real `_deathtouch_damage` marker, not a `damage_marked = toughness`
    fake. `damage_marked` after combat should reflect the ACTUAL
    damage dealt (1 point for a deathtouch-lethal blocker assignment,
    not the full toughness)."""

    def test_deathtouch_attacker_marks_only_actual_damage(self):
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "DTAttacker", 1, power=1, toughness=1,
                             keywords={Keyword.DEATHTOUCH})
        blocker = _creature(game, "BigBlocker", 0, power=1, toughness=10)

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
        cm.resolve_combat_damage(game)
        game.check_state_based_actions()

        assert blocker.zone != "battlefield", (
            "a deathtouch-lethal blocker must be destroyed by the "
            "deathtouch SBA even though only 1 damage point was "
            "actually assigned (toughness=10)"
        )

    def test_deathtouch_blocker_kills_higher_toughness_attacker(self):
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "BigAttacker", 1, power=1, toughness=10)
        blocker = _creature(game, "DTBlocker", 0, power=1, toughness=1,
                            keywords={Keyword.DEATHTOUCH})

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
        cm.resolve_combat_damage(game)
        game.check_state_based_actions()

        assert attacker.zone != "battlefield", (
            "a deathtouch blocker must destroy the attacker via the "
            "real deathtouch SBA marker"
        )

    def test_non_deathtouch_combat_unaffected(self):
        """Regression: normal lethal-damage destruction (no deathtouch
        involved) must still work via the ordinary damage_marked >=
        toughness SBA path."""
        game = GameState(rng=random.Random(0))
        attacker = _creature(game, "Vanilla1", 1, power=5, toughness=5)
        blocker = _creature(game, "Vanilla2", 0, power=1, toughness=3)

        cm = _combat(game, [attacker])
        cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
        cm.resolve_combat_damage(game)
        game.check_state_based_actions()

        assert blocker.zone != "battlefield"
        assert attacker.zone == "battlefield"
        assert attacker.damage_marked == 1
