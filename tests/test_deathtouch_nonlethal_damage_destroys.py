"""CR 704.5i — a creature that's been dealt damage by a source with
deathtouch since the last state-based cleanup is destroyed, however
small the damage.

The rule previously existed ONLY in the dead SBAManager loop
(engine/sba_manager.py:140-153, zero live callers), and the marker it
consumed could never even be written: engine/damage.py reads
`source.has_deathtouch` and writes `target._deathtouch_damage`, but
CardInstance defined neither. Live effect on main: a deathtouch source
dealing non-lethal (non-combat) damage NEVER destroyed the creature.

Ported per docs/proposals/resolver_sba_unification.md §5.2: exactly one
implementation, SBAManager.perform_deathtouch_check(game), called from
both the live game_state.check_state_based_actions and the manager's
own _check_and_perform_once. Death routes through game._creature_dies
so Undying/Persist are preserved (the rule that existed ONLY in the
live path).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword
from engine.damage import deal_damage
from engine.game_state import GameState
from engine.mana import ManaCost


# ─── helpers ────────────────────────────────────────────────────────


def _fresh_game() -> GameState:
    return GameState(rng=random.Random(0))


def _creature(game: GameState, name: str, controller: int, *,
              power: int = 1, toughness: int = 1,
              keywords=None) -> CardInstance:
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(keywords or ()), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


# ─── CR 704.5i: non-lethal deathtouch damage destroys ───────────────


def test_nonlethal_damage_from_deathtouch_source_destroys_creature():
    """Toughness 3, 1 marked damage from a deathtouch source → the SBA
    pass destroys the creature."""
    game = _fresh_game()
    source = _creature(game, "Venom Source", 0,
                       keywords={Keyword.DEATHTOUCH})
    victim = _creature(game, "Sturdy Victim", 1, power=2, toughness=3)

    deal_damage(source, victim, 1)
    assert victim.damage_marked == 1
    assert victim.toughness == 3  # non-lethal on its own

    game.check_state_based_actions()

    assert victim.zone == "graveyard"
    assert victim in game.players[1].graveyard
    assert victim not in game.players[1].battlefield


def test_same_damage_from_non_deathtouch_source_does_not_destroy():
    """Negative control: 1 damage from a plain source is non-lethal and
    the creature survives the SBA pass."""
    game = _fresh_game()
    source = _creature(game, "Plain Source", 0)
    victim = _creature(game, "Sturdy Victim", 1, power=2, toughness=3)

    deal_damage(source, victim, 1)
    game.check_state_based_actions()

    assert victim.zone == "battlefield"
    assert victim in game.players[1].battlefield


def test_deathtouch_destruction_routes_through_undying():
    """The shared implementation must route death through
    _creature_dies: an Undying creature destroyed via 704.5i returns to
    the battlefield with a +1/+1 counter instead of staying dead."""
    game = _fresh_game()
    source = _creature(game, "Venom Source", 0,
                       keywords={Keyword.DEATHTOUCH})
    victim = _creature(game, "Persistent Victim", 1,
                       power=2, toughness=3,
                       keywords={Keyword.UNDYING})

    deal_damage(source, victim, 1)
    game.check_state_based_actions()

    assert victim.zone == "battlefield"
    assert victim in game.players[1].battlefield
    assert victim.plus_counters == 1
    # The returned object is a fresh body: no stale damage or marker.
    assert victim.damage_marked == 0
    assert getattr(victim, "_deathtouch_damage", 0) == 0
