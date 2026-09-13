"""A soft counter's tax is waived when its printed board condition holds
— the "Ferocious — … counter that spell instead" shape is a HARD counter
when the condition is met (CR 601.2b: the printed condition, not just the
base tax, determines the counter effect).

Stubborn Denial parsed to `counter_tax_amount=1` with no model of its
ferocious clause ("If you control a creature with power 4 or greater,
counter that spell instead"), so it was ALWAYS a soft {1} tax — a Domain
Zoo deck holding a 4-power Territorial Kavu or Scion of Draco (both reach
power 4+ under domain) still let the opponent pay {1} to slip a spell
through, when the counter should have been unconditional. Class: the
"counter that spell instead if <board condition>" family, keyed on the
creature-power condition (the one registered carrier is Stubborn Denial;
the parser is shape-driven, not a name check).

Rules pinned:
1. The parser types the creature-power upgrade condition; a plain counter
   (no upgrade clause) types None.
2. `effective_counter_tax` is 0 when the counter's controller meets the
   condition, the printed tax otherwise — one predicate the engine's
   resolution and the AI's two tax reads all consult.
3. At resolution, a ferocious counter with the condition met counters the
   spell unconditionally: no tax is offered even to an opponent holding
   the mana.
4. Without the condition, the soft tax stands (offered, payable).

Card names are fixture carriers for the oracle shape.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_runner import AICallbacks
from engine.game_state import GameState, Phase
from engine.spell_resolution import ResolutionManager
from engine.stack import StackItem, StackItemType


def _game(callbacks=None):
    game = GameState(rng=random.Random(0), callbacks=callbacks or AICallbacks())
    game.players[0].deck_name = "Domain Zoo"
    game.players[1].deck_name = "Ruby Storm"
    game.active_player = 1  # the taxed spell's controller is on the turn
    game.current_phase = Phase.MAIN1
    return game


def _inst(game, card_db, name, controller, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing card: {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    return c


def _creature(game, controller, power, toughness=4):
    t = CardTemplate(
        name=f"Vanilla {power}/{toughness}", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=power), supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None, keywords=set(),
        abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set())
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _untapped_lands(game, card_db, controller, n, name="Mountain"):
    for _ in range(n):
        land = _inst(game, card_db, name, controller, "battlefield")
        land.enter_battlefield()
        land.tapped = False
        game.players[controller].battlefield.append(land)


def _stack_denial_on_spell(game, card_db, taxed_name="Pyretic Ritual"):
    """A noncreature spell (P0, controller on the turn) with Stubborn
    Denial (P1) on top targeting it."""
    taxed = _inst(game, card_db, taxed_name, 0, "stack")
    assert not taxed.template.is_creature, f"{taxed_name} must be noncreature"
    denial = _inst(game, card_db, "Stubborn Denial", 1, "stack")
    game.stack.push(StackItem(item_type=StackItemType.SPELL, source=taxed,
                              controller=0, targets=[], description=""))
    game.stack.push(StackItem(item_type=StackItemType.SPELL, source=denial,
                              controller=1, targets=[taxed.instance_id],
                              description="Counter target noncreature spell."))
    return taxed, denial


class _AlwaysPay(AICallbacks):
    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        return True


def test_the_parser_types_the_ferocious_creature_power_condition(card_db):
    from engine.oracle_parser import parse_counter_upgrade_condition
    denial = card_db.get_card("Stubborn Denial")
    assert getattr(denial, "counter_upgrade_condition", None) == {"creature_power_at_least": 4}
    assert parse_counter_upgrade_condition(denial.oracle_text) == {"creature_power_at_least": 4}
    # A plain hard/soft counter has no upgrade clause.
    for plain in ("Counterspell", "Spell Pierce"):
        t = card_db.get_card(plain)
        if t is not None:
            assert getattr(t, "counter_upgrade_condition", None) is None, plain


def test_effective_counter_tax_is_zero_only_when_the_condition_holds(card_db):
    from engine.optional_costs import effective_counter_tax
    denial = card_db.get_card("Stubborn Denial")
    game = _game()
    # No 4-power creature: the printed {1} tax stands.
    _creature(game, 1, power=2)
    assert effective_counter_tax(game, 1, denial) == 1
    # A 4-power creature: the counter is hard, tax 0.
    _creature(game, 1, power=4)
    assert effective_counter_tax(game, 1, denial) == 0


def test_a_ferocious_counter_is_hard_when_a_four_power_creature_is_controlled(card_db):
    game = _game(callbacks=_AlwaysPay())          # opponent WOULD pay if asked
    _creature(game, 1, power=4)                    # Denial's controller is ferocious
    _untapped_lands(game, card_db, controller=0, n=4)  # taxed controller could pay {1}
    taxed, denial = _stack_denial_on_spell(game, card_db)
    ResolutionManager.resolve_stack(game)          # resolves the Denial on top
    assert taxed.zone != "stack", (
        "a ferocious counter with a 4-power creature is a HARD counter — "
        "the spell is countered with no tax offered, even to a payer")
    assert any("is countered" in l for l in game.log)


def test_without_a_four_power_creature_the_soft_tax_stands(card_db):
    game = _game(callbacks=_AlwaysPay())
    _creature(game, 1, power=3)                    # not ferocious
    _untapped_lands(game, card_db, controller=0, n=4)
    taxed, denial = _stack_denial_on_spell(game, card_db)
    ResolutionManager.resolve_stack(game)
    assert taxed.zone == "stack", (
        "without a 4-power creature the {1} tax is a real choice — a paying "
        "controller keeps the spell on the stack")
