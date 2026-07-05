"""CR 608.2b — a spell with targets checks them again on resolution;
if ALL its targets are now illegal, it doesn't resolve (fizzles) and
goes to the graveyard with no effect.

The rule previously existed ONLY in the dead legacy resolver
(engine/stack.py:144-156, zero callers). The live
ResolutionManager.resolve_stack never re-checked targets, so a spell
whose only target left its zone still resolved — and oracle-resolver
handlers that re-pick targets (e.g. the nonland-bounce branch) would
resolve the spell against a DIFFERENT permanent than the one targeted.

Ported per docs/proposals/resolver_sba_unification.md §5.1. Target
validity is judged against the zone snapshot taken at cast time
(StackItem.target_zones): battlefield for removal, stack for
counterspells, graveyard for reanimation. Player-target markers
(negative ids) are always valid.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState
from engine.mana import ManaCost


# ─── helpers ────────────────────────────────────────────────────────


def _fresh_game() -> GameState:
    return GameState(rng=random.Random(0))


def _template(name: str, card_types, oracle: str = "",
              power=None, toughness=None) -> CardTemplate:
    return CardTemplate(
        name=name,
        card_types=card_types,
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle,
        tags=set(),
    )


def _creature_on_battlefield(game: GameState, name: str, controller: int,
                             power: int = 2, toughness: int = 2) -> CardInstance:
    tmpl = _template(name, [CardType.CREATURE], power=power, toughness=toughness)
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _spell_in_hand(game: GameState, name: str, controller: int,
                   oracle: str) -> CardInstance:
    tmpl = _template(name, [CardType.SORCERY], oracle=oracle)
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _kill(game: GameState, creature: CardInstance):
    """Remove a creature from the battlefield in response (dies)."""
    owner = game.players[creature.controller]
    owner.battlefield.remove(creature)
    creature.zone = "graveyard"
    game.players[creature.owner].graveyard.append(creature)


BOUNCE_ORACLE = "Return target nonland permanent to its owner's hand."


# ─── CR 608.2b: all targets illegal → fizzle, NO effect ─────────────


def test_spell_fizzles_when_its_only_target_left_the_battlefield():
    """Targeted spell whose sole target died in response must fizzle:
    graveyard, no effect executed (nothing else gets bounced)."""
    game = _fresh_game()
    target = _creature_on_battlefield(game, "Alpha Threat", 1)
    bystander = _creature_on_battlefield(game, "Beta Threat", 1,
                                         power=5, toughness=5)
    spell = _spell_in_hand(game, "Test Bounce", 0, BOUNCE_ORACLE)

    assert game.cast_spell(0, spell, targets=[target.instance_id],
                           free_cast=True)
    assert not game.stack.is_empty

    _kill(game, target)  # response removes the only target
    game.resolve_stack()

    # Spell fizzled to the graveyard...
    assert spell.zone == "graveyard"
    assert spell in game.players[0].graveyard
    # ...with NO effect: the bystander was not re-targeted/bounced.
    assert bystander in game.players[1].battlefield
    assert bystander.zone == "battlefield"
    assert bystander not in game.players[1].hand
    # And the log names the fizzle.
    assert any("fizzle" in line.lower() for line in game.log), (
        "expected a fizzle log line (CR 608.2b), got:\n"
        + "\n".join(game.log[-5:]))


# ─── negative: one of two targets still legal → resolves normally ───


def test_spell_resolves_when_one_of_two_targets_still_valid():
    """Fizzle requires ALL targets illegal (CR 608.2b). One survivor
    means the spell resolves normally."""
    game = _fresh_game()
    dead_target = _creature_on_battlefield(game, "Alpha Threat", 1)
    live_target = _creature_on_battlefield(game, "Beta Threat", 1,
                                           power=5, toughness=5)
    spell = _spell_in_hand(game, "Test Bounce", 0, BOUNCE_ORACLE)

    assert game.cast_spell(
        0, spell,
        targets=[dead_target.instance_id, live_target.instance_id],
        free_cast=True)

    _kill(game, dead_target)
    game.resolve_stack()

    assert not any("fizzle" in line.lower() for line in game.log)
    # The spell executed: the surviving nonland permanent got bounced.
    assert live_target in game.players[1].hand
    assert spell.zone == "graveyard"


# ─── negative: no-target spell resolves normally ────────────────────


def test_spell_without_targets_resolves_normally():
    """The re-check only applies when item.targets is non-empty."""
    game = _fresh_game()
    player = game.players[0]
    for i in range(3):
        tmpl = _template(f"Library Filler {i}", [CardType.CREATURE],
                         power=1, toughness=1)
        c = CardInstance(template=tmpl, owner=0, controller=0,
                         instance_id=game.next_instance_id(),
                         zone="library")
        c._game_state = game
        player.library.append(c)
    spell = _spell_in_hand(game, "Test Draw", 0, "Draw two cards.")

    assert game.cast_spell(0, spell, targets=[], free_cast=True)
    game.resolve_stack()

    assert not any("fizzle" in line.lower() for line in game.log)
    assert len(player.hand) == 2
    assert spell.zone == "graveyard"


# ─── negative: stack-zone target (counterspell) is not a false fizzle ─


def test_counterspell_target_on_stack_does_not_false_fizzle():
    """A counterspell's target lives on the STACK, not the battlefield.
    The cast-time zone snapshot must judge it against 'stack', so a
    naive battlefield-only check may not fizzle it."""
    game = _fresh_game()
    # Opponent's spell sits on the stack.
    threat = _spell_in_hand(game, "Opposing Sorcery", 1, "Draw two cards.")
    assert game.cast_spell(1, threat, targets=[], free_cast=True)
    assert not game.stack.is_empty

    counter = _spell_in_hand(game, "Test Counter", 0,
                             "Counter target spell.")
    assert game.cast_spell(0, counter, targets=[threat.instance_id],
                           free_cast=True)

    game.resolve_stack()  # resolves the counterspell (top of stack)

    assert not any("fizzle" in line.lower() for line in game.log), (
        "counterspell wrongly fizzled against a valid stack target:\n"
        + "\n".join(game.log[-5:]))
    assert counter.zone == "graveyard"
