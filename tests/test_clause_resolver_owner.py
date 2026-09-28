"""Every effect clause resolves through one owner (engine/clause_resolver.py).

Rules pinned here (CR 608.2 — an effect's text is executed by one resolver,
whatever carries it):

* The registry's gates are pure functions of a clause's static facts
  (template, oracle text, routed override, removal data). Running every gate
  over every card in the pool with no game object must not raise — that is
  what lets ``clause_is_executable`` answer "can this clause run?" at load,
  before a loyalty cost is paid.
* The handler order is the order the branches had inline in
  ``resolve_spell_from_oracle`` (early returns and fall-throughs depend on it).
* ``resolve_spell_from_oracle`` is a delegation to the owner, not a second
  implementation.
"""
from __future__ import annotations

import inspect

from engine import clause_resolver, oracle_resolver
from engine.clause_resolver import (HANDLERS, PRE_ORACLE_HANDLERS,
                                    clause_is_executable)


def test_every_gate_is_static_over_the_whole_pool(card_db):
    from engine.cards import CardInstance
    executable = 0
    for tpl in card_db.cards.values():
        card = CardInstance(template=tpl, owner=0, controller=0,
                            instance_id=1, zone="stack")
        # No game attached: a gate that reads game state raises here.
        if clause_is_executable(card):
            executable += 1
    assert executable > 1000, executable


def test_handler_order_is_the_inline_branch_order():
    assert [h.name for h in PRE_ORACLE_HANDLERS] == ["x_creature_tutor", "team_pump"]
    assert [h.name for h in HANDLERS] == [
        "combat_prevention", "hand_refill_wheel", "cast_prohibition",
        "until_next_turn", "mass_mode_clause", "targeted_pump", "mass_reanimate", "energy_damage",
        "land_destruction", "direct_damage", "board_sweep", "targeted_removal",
        "library_dig", "hand_attack", "bounce_nonland", "reanimate_target",
        "impulse_reveal", "card_flow", "create_token",
    ]


def test_the_spell_resolver_delegates_to_the_clause_owner():
    src = inspect.getsource(oracle_resolver.resolve_spell_from_oracle)
    assert "resolve_clause(" in src
    # No branch bodies left behind in the old function.
    assert "game.draw_cards" not in src and "create_token" not in src


def test_executability_matches_shapes(card_db):
    from engine.cards import CardInstance

    def ex(name):
        c = CardInstance(template=card_db.get_card(name), owner=0, controller=0,
                         instance_id=1, zone="stack")
        return clause_is_executable(c)

    assert ex("Lightning Bolt")          # fixed burn
    assert ex("Thoughtseize")            # hand attack
    assert ex("Silence")                 # cast prohibition
    assert ex("Day's Undoing")           # hand-refill wheel
    assert not ex("Plains")              # no clause
