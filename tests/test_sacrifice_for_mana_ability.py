"""A permanent that sacrifices itself for mana is a real mana source.

Root cause (Eldrazi ramp mana audit, 2026-08-25): 207 Modern cards create or
carry a "Sacrifice this <thing>: Add <mana>" ability — Eldrazi Spawn/Scion,
Treasure, Lotus-style one-shots. `create_token` builds the token's template
without any mana fields, and nothing anywhere in the engine recognised the
ability, so every one of them contributed ZERO mana. Measured on a live board:
adding an Eldrazi Spawn token to three Forests left capacity at 3.

This is one-shot mana (it consumes the permanent), which is structurally the
same shape the engine already models for Improvise: spend a permanent to cover
the part of a cost that lands and pool cannot.

Rule under test: the "Sacrifice this …: Add <mana>" ability parses into mana
units, so a permanent carrying it is counted as a (one-shot) mana source.
Mechanic-driven (oracle ability), no card names asserted.
"""
from __future__ import annotations

from engine.oracle_parser import parse_sacrifice_mana_units


def test_single_colorless_sacrifice_ability():
    units = parse_sacrifice_mana_units('Sacrifice this token: Add {C}.')
    assert units == [['C']], f"expected one colorless unit, got {units}"


def test_repeated_symbol_run_counts_each_unit():
    units = parse_sacrifice_mana_units('Sacrifice this creature: Add {C}{C}.')
    assert len(units) == 2, f"expected two units, got {units}"
    assert all(u == ['C'] for u in units)


def test_any_color_sacrifice_ability_offers_every_color():
    units = parse_sacrifice_mana_units(
        'Sacrifice this artifact: Add one mana of any color.')
    assert len(units) == 1, f"expected one unit, got {units}"
    assert set(units[0]) == {'W', 'U', 'B', 'R', 'G'}


def test_multi_mana_of_any_one_color():
    units = parse_sacrifice_mana_units(
        'Sacrifice this artifact: Add three mana of any one color.')
    assert len(units) == 3, f"expected three units, got {units}"


def test_tap_only_mana_ability_is_not_a_sacrifice_ability():
    # A plain "{T}: Add {G}" source is already modelled elsewhere; this parser
    # must not claim it, or the permanent would be double-counted.
    assert parse_sacrifice_mana_units('{T}: Add {G}.') is None


def test_sacrifice_for_a_non_mana_effect_is_not_claimed():
    assert parse_sacrifice_mana_units(
        'Sacrifice this creature: Draw a card.') is None


def test_sacrificing_a_different_permanent_is_not_claimed():
    # "Sacrifice a creature: Add {B}" is a cost paid with OTHER permanents —
    # not this permanent turning itself into mana. Different mechanic.
    assert parse_sacrifice_mana_units(
        'Sacrifice a creature: Add {B}.') is None


def test_created_token_exposes_its_sacrifice_mana_units():
    """End-to-end: a token created with the quoted ability is a mana source."""
    import random
    from engine.game_state import GameState, Phase
    from engine.card_database import CardDatabase
    from engine.cards import CardInstance
    from engine.oracle_resolver import resolve_spell_from_oracle

    db = CardDatabase()
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 3
    p = game.players[0]
    p.deck_name = "Eldrazi Ramp"
    game.players[1].deck_name = "Dimir Midrange"
    for nm in ("Forest", "Forest", "Forest", "Forest", "Forest"):
        t = db.get_card(nm)
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="library")
        c._game_state = game
        p.library.append(c)
    t = db.get_card("Malevolent Rumble")
    spell = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    resolve_spell_from_oracle(game, spell, 0, None)

    tokens = [c for c in p.battlefield if getattr(c, "is_token", False)]
    assert tokens, "precondition: the spell created its token"
    units = getattr(tokens[0].template, "sacrifice_mana_units", None)
    assert units, (
        "a token created with a quoted sacrifice-for-mana ability must expose "
        "it as mana units; otherwise it is a dead body instead of ramp")


def _board_with_sac_sources(n_forests, n_tokens):
    """A board with `n_forests` untapped Forests and `n_tokens` sac-for-mana
    tokens, plus a spell to pay for."""
    import random
    from engine.game_state import GameState, Phase
    from engine.card_database import CardDatabase
    from engine.cards import CardInstance
    from engine.permanent_effects import PermanentEffects

    db = CardDatabase()
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    p = game.players[0]
    p.deck_name = "Eldrazi Ramp"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(n_forests):
        t = db.get_card("Forest")
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(),
                         zone="battlefield")
        c._game_state = game
        c.enter_battlefield()
        p.battlefield.append(c)
    if n_tokens:
        PermanentEffects.create_token(
            game, 0, "creature", count=n_tokens, power=0, toughness=1,
            source_oracle=('Create a 0/1 colorless Eldrazi Spawn creature '
                           'token with "Sacrifice this token: Add {C}."'))
    return game, p


def test_sacrifice_mana_covers_a_shortfall_lands_cannot():
    """The whole point: one-shot mana makes an unaffordable spell castable."""
    from engine.mana_payment import ManaPayment
    from engine.mana import ManaCost

    # 3 Forests + 2 sac tokens vs a generic-5 cost: lands alone cannot pay.
    game, p = _board_with_sac_sources(3, 2)
    before_tokens = len([c for c in p.battlefield
                         if getattr(c, "is_token", False)])
    assert before_tokens == 2, "fixture premise: two sac sources on board"

    paid = ManaPayment.tap_lands_for_mana(
        game, 0, ManaCost(generic=5), "TestSpell")
    after_tokens = len([c for c in p.battlefield
                        if getattr(c, "is_token", False)])
    assert paid, "3 lands + 2 one-shot sources should cover a generic-5 cost"
    assert after_tokens < before_tokens, (
        "paying with a sacrifice ability must consume the permanent")


def test_sacrifice_mana_is_not_spent_when_lands_already_suffice():
    """Regression: never eat your own ramp for a cost the lands cover."""
    from engine.mana_payment import ManaPayment
    from engine.mana import ManaCost

    game, p = _board_with_sac_sources(4, 2)
    before = len([c for c in p.battlefield if getattr(c, "is_token", False)])
    ManaPayment.tap_lands_for_mana(game, 0, ManaCost(generic=2), "TestSpell")
    after = len([c for c in p.battlefield if getattr(c, "is_token", False)])
    assert after == before, (
        "a cost the untapped lands already afford must not consume one-shot "
        "mana sources")
