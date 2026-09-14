"""An "any target" burn spell with a printed conditional upgrade deals its
UPGRADED amount when the condition holds (CR 608.2: the resolving spell
checks its own "deals M instead if <condition>" clause).

Galvanic Blast ("deals 2 damage to any target; Metalcraft — deals 4
instead if you control three or more artifacts") parsed to
`direct_damage_data=None` — the direct-damage parser refused the "instead"
rider — so it ALWAYS dealt 2, never 4 with metalcraft, and could not kill
a 3–4 toughness creature even with a full board of artifacts.

Rules pinned:
1. The parser types the base amount, the upgrade amount, and the
   condition (`metalcraft` / `delirium`) into `direct_damage_data`.
2. `effective_direct_damage` is the base unless the condition holds for
   the caster, then the upgrade — one evaluator the resolution and the
   AI's `burn_damage` accessor share.
3. At resolution, a metalcraft Galvanic Blast deals 4 (kills a
   4-toughness creature); without three artifacts, 2.

Card names are fixture carriers for the "deals N to any target; <cond> —
deals M instead" shape. (Creature-targeted conditional burn — Unholy
Heat's delirium — is a different, already-handled shape and is out of
this any-target parser's scope.)
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def _inst(game, card_db, name, controller, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing card: {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    return c


def _artifacts(game, controller, n):
    for _ in range(n):
        t = CardTemplate(name="Mox", card_types=[CardType.ARTIFACT],
                         mana_cost=ManaCost(), supertypes=[], subtypes=[],
                         power=None, toughness=None, loyalty=None, keywords=set(),
                         abilities=[], color_identity=set(), produces_mana=[],
                         enters_tapped=False, oracle_text="", tags=set())
        c = CardInstance(template=t, owner=controller, controller=controller,
                         instance_id=game.next_instance_id(), zone="battlefield")
        c._game_state = game; c.enter_battlefield()
        game.players[controller].battlefield.append(c)


def _vanilla_creature(game, controller, toughness):
    t = CardTemplate(name=f"Bear {toughness}", card_types=[CardType.CREATURE],
                     mana_cost=ManaCost(generic=toughness), supertypes=[], subtypes=[],
                     power=1, toughness=toughness, loyalty=None, keywords=set(),
                     abilities=[], color_identity=set(), produces_mana=[],
                     enters_tapped=False, oracle_text="", tags=set())
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game; c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def test_the_parser_types_the_conditional_damage_upgrade(card_db):
    gb = card_db.get_card("Galvanic Blast")
    dd = getattr(gb, "direct_damage_data", None)
    assert dd and dd.get("amount") == 2 and dd.get("upgrade_amount") == 4
    assert dd.get("upgrade_condition") == "metalcraft"
    # A plain fixed burn keeps a plain (no-upgrade) record.
    bolt = card_db.get_card("Lightning Bolt")
    if bolt is not None:
        dd = getattr(bolt, "direct_damage_data", None)
        assert dd and dd.get("amount") == 3 and not dd.get("upgrade_amount")


def test_effective_damage_applies_the_upgrade_only_when_the_condition_holds(card_db):
    from engine.oracle_resolver import effective_direct_damage
    game = _game()
    gb = card_db.get_card("Galvanic Blast")
    assert effective_direct_damage(game, 0, gb) == 2      # no artifacts
    _artifacts(game, 0, 3)
    assert effective_direct_damage(game, 0, gb) == 4      # metalcraft


def test_burn_damage_accessor_is_game_aware_and_defaults_to_base(card_db):
    from ai.card_classes import burn_damage
    gb = card_db.get_card("Galvanic Blast")
    assert burn_damage(gb) == 2                            # no game: base
    game = _game()
    _artifacts(game, 0, 3)
    assert burn_damage(gb, game, 0) == 4


def test_metalcraft_galvanic_blast_kills_a_four_toughness_creature(card_db):
    from engine.oracle_resolver import resolve_spell_from_oracle
    game = _game()
    _artifacts(game, 0, 3)
    victim = _vanilla_creature(game, 1, toughness=4)
    gb = _inst(game, card_db, "Galvanic Blast", 0, "stack")
    resolve_spell_from_oracle(game, gb, 0, [victim.instance_id])
    game.check_state_based_actions()
    assert victim.zone != "battlefield", "metalcraft Galvanic Blast deals 4, killing the 4-toughness creature"


def test_without_metalcraft_it_deals_only_two(card_db):
    from engine.oracle_resolver import resolve_spell_from_oracle
    game = _game()
    _artifacts(game, 0, 2)                                 # one short of metalcraft
    victim = _vanilla_creature(game, 1, toughness=4)
    gb = _inst(game, card_db, "Galvanic Blast", 0, "stack")
    resolve_spell_from_oracle(game, gb, 0, [victim.instance_id])
    game.check_state_based_actions()
    assert victim.zone == "battlefield", "without metalcraft it deals 2, not enough for a 4-toughness creature"
