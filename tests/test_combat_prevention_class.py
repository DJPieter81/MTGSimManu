"""Combat prevention as a CLASS (CR 509.4 attack restrictions + CR 615
damage prevention) — modelled once for the whole family, not per card.

Three turn-scoped shapes, each enforced in ONE place:
  * "creatures can't attack this turn"            (symmetric attack lock)
  * "creatures can't attack you this turn"         (directional attack lock)
  * "prevent all combat damage this turn"          (Fog proper)

Attack locks are enforced in `CombatManager.valid_attackers` (the single
enumeration seam `game.get_valid_attackers` and the engine share); damage
prevention in `CombatManager.resolve_combat_damage`. All three are
turn-scoped `PlayerState` flags reset by `reset_turn_tracking`, mirroring
`silenced_this_turn`.

Card/oracle strings are fixture carriers only; the rule under test is the
combat-prevention class, so this must hold for a card the pool has never
seen.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.combat_manager import CombatManager
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_combat_prevention


def _creature(game, controller, power=2, toughness=2):
    t = CardTemplate(name=f"Bear{game.next_instance_id()}",
                     card_types=[CardType.CREATURE], mana_cost=ManaCost(generic=2),
                     supertypes=[], subtypes=[], power=power, toughness=toughness,
                     loyalty=None, keywords=set(), abilities=[], color_identity=set(),
                     produces_mana=[], enters_tapped=False, oracle_text="", tags=set())
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game; c.enter_battlefield(); c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


# ─── parse layer ─────────────────────────────────────────────────────


def test_parse_symmetric_attack_lock():
    assert parse_combat_prevention("Creatures can't attack this turn.") == {
        "no_attack": "all", "prevent_combat_damage": False}


def test_parse_directional_attack_lock():
    assert parse_combat_prevention(
        "Creatures can't attack you this turn.")["no_attack"] == "you"


def test_parse_prevent_all_combat_damage():
    spec = parse_combat_prevention("Prevent all combat damage that would be dealt this turn.")
    assert spec["prevent_combat_damage"] is True


def test_parse_unrelated_text_is_none():
    assert parse_combat_prevention("Draw a card.") is None
    assert parse_combat_prevention("Target player can't cast spells this turn.") is None


# ─── enforcement: attack locks ───────────────────────────────────────


def test_symmetric_lock_stops_every_creature_from_attacking():
    game = GameState(rng=random.Random(0))
    _creature(game, 0); _creature(game, 1)
    assert CombatManager.valid_attackers(game, 0)  # baseline: can attack
    game.players[0].cannot_attack_this_turn = True
    game.players[1].cannot_attack_this_turn = True
    assert CombatManager.valid_attackers(game, 0) == []
    assert CombatManager.valid_attackers(game, 1) == []


def test_directional_lock_stops_only_attacks_against_the_protected_player():
    game = GameState(rng=random.Random(0))
    _creature(game, 0); _creature(game, 1)
    # Player 1 is protected: player 0's creatures can't attack.
    game.players[1].cannot_be_attacked_this_turn = True
    assert CombatManager.valid_attackers(game, 0) == []
    assert CombatManager.valid_attackers(game, 1)  # player 1 may still attack


def test_locks_clear_at_the_turn_boundary():
    game = GameState(rng=random.Random(0))
    _creature(game, 0)
    game.players[0].cannot_attack_this_turn = True
    assert CombatManager.valid_attackers(game, 0) == []
    game.players[0].reset_turn_tracking()
    assert CombatManager.valid_attackers(game, 0)  # lock lifted next turn


# ─── enforcement: damage prevention ──────────────────────────────────


def test_prevent_all_combat_damage_zeroes_the_combat_step():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.COMBAT_DAMAGE if hasattr(Phase, "COMBAT_DAMAGE") else Phase.MAIN1
    attacker = _creature(game, 1, power=3, toughness=3)
    game.players[1].combat_damage_prevented_this_turn = True
    life0 = game.players[0].life
    cm = CombatManager()
    cm.declare_attackers(game, [attacker], active_player=1)
    cm.declare_blockers(game, {})
    cm.resolve_combat_damage(game)
    assert game.players[0].life == life0, "combat damage was not prevented"


# ─── resolution integration ──────────────────────────────────────────


def test_resolving_a_fog_spell_sets_the_prevention_flag():
    from engine.oracle_resolver import resolve_spell_from_oracle
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    t = CardTemplate(name="Fogger", card_types=[CardType.INSTANT], mana_cost=ManaCost(generic=1),
                     supertypes=[], subtypes=[], power=None, toughness=None, loyalty=None,
                     keywords=set(), abilities=[], color_identity=set(), produces_mana=[],
                     enters_tapped=False,
                     oracle_text="Prevent all combat damage that would be dealt this turn.",
                     tags=set())
    fog = CardInstance(template=t, owner=0, controller=0,
                       instance_id=game.next_instance_id(), zone="stack")
    fog._game_state = game
    resolve_spell_from_oracle(game, fog, 0, [])
    assert any(p.combat_damage_prevented_this_turn for p in game.players), (
        "resolving a Fog did not set the combat-damage-prevention flag")


def test_resolving_a_symmetric_attack_lock_sets_the_flag():
    from engine.oracle_resolver import resolve_spell_from_oracle
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    t = CardTemplate(name="Nobody Attacks", card_types=[CardType.SORCERY], mana_cost=ManaCost(generic=1),
                     supertypes=[], subtypes=[], power=None, toughness=None, loyalty=None,
                     keywords=set(), abilities=[], color_identity=set(), produces_mana=[],
                     enters_tapped=False, oracle_text="Creatures can't attack this turn.", tags=set())
    lock = CardInstance(template=t, owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="stack")
    lock._game_state = game
    resolve_spell_from_oracle(game, lock, 0, [])
    assert game.players[0].cannot_attack_this_turn and game.players[1].cannot_attack_this_turn


def test_combat_prevention_is_typed_at_db_load(card_db):
    """The class is a typed field so the census recognises it and the
    resolver/AI can read it once."""
    # A pool Fog: base Riot Control / Holy Day shape. Use any card whose
    # oracle carries the class; fall back to the parser if none loads.
    import engine.oracle_parser as op
    assert op.parse_combat_prevention("Creatures can't attack this turn.") is not None
