"""Shared resolver for the fixed-amount "deals N damage to any target"
instant/sorcery mechanic (CR 601.2c target declaration + CR 119 damage).

# Mechanic under test

`engine/card_effects.py` registered per-card EFFECT_REGISTRY SPELL_RESOLVE
handlers (Lightning Bolt, Lava Dart, …) whose bodies were a single
`resolve_damage_to_chosen_target(game, card, controller, N, targets)` call —
the same shared owner, differing only in the fixed literal N. Per CLAUDE.md's
ABSTRACTION CONTRACT that duplication is the same smell the ratchet targets
for `card.name ==` checks, even though `EFFECT_REGISTRY.register(...)` is
invisible to `tools/check_abstraction.py`'s regex.

The whole "deals N damage to <face-legal target spec>" shape is now parsed
once at DB load by `oracle_parser.parse_direct_damage_spell` into the typed
field `CardTemplate.direct_damage_data` and dispatched (no oracle inspection
at resolve time) through the single shared owner
`oracle_resolver.resolve_damage_to_chosen_target` — exactly the call every
per-card handler made by hand. ~79 Modern instants/sorceries share the shape,
so the field is a genuine mechanic class, not a single-card carrier. Having
proven the two registered burn handlers redundant with the typed path (the
integration tests below), their registrations are deleted.

Only the AMOUNT is card-specific and it is a printed LITERAL here; scaled
amounts (delirium/domain/storm) are a different mechanic and are deliberately
NOT matched — they keep their own handlers. Card names appear only as fixture
carriers for the real-DB integration tests; the parser-unit tests use
synthetic oracle strings. The mechanic under test is "a spell whose entire
resolution is dealing a fixed amount of damage to a face-legal chosen
target", not any specific card.
"""
from __future__ import annotations

import random

import pytest

from engine.card_effects import EFFECT_REGISTRY, EffectTiming
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState
from engine.oracle_parser import parse_direct_damage_spell
from engine.oracle_resolver import resolve_spell_from_oracle


class TestParseDirectDamageSpell:
    """Parser-level: the shape is recognised, and everything that is NOT a
    pure fixed-N face-legal burn is refused (never half-executed)."""

    @pytest.mark.parametrize("oracle,amount", [
        ("Bolt deals 3 damage to any target.", 3),
        ("Zap deals 1 damage to any target.", 1),
        ("Strike deals 2 damage to target creature or player.", 2),
        ("Wind deals 10 damage to any target.", 10),
        # A keyword-ability cost line (flashback's alternative cost) is not a
        # resolution rider and must not disqualify the burn.
        ("Dart deals 1 damage to any target.\nFlashback—Sacrifice a Mountain.", 1),
    ])
    def test_fixed_amount_face_legal_burn_is_recognised(self, oracle, amount):
        assert parse_direct_damage_spell(oracle) == {"amount": amount}

    @pytest.mark.parametrize("oracle,why", [
        ("Helix deals 3 damage to any target. You gain 3 life.", "lifegain rider"),
        ("Bolt deals 3 damage to any target. Draw a card.", "draw rider"),
        ("Slash deals 4 damage to target creature.", "creature-only (no face)"),
        ("Arc deals 3 damage divided as you choose among any number of targets.",
         "divided damage"),
        ("Heat deals 2 damage to any target. Delirium — deals 6 instead.",
         "delirium-scaled amount"),
        ("Volley deals damage to any target equal to the number of Mountains "
         "you control.", "derived amount, no literal"),
    ])
    def test_non_pure_fixed_burn_is_refused(self, oracle, why):
        assert parse_direct_damage_spell(oracle) is None, why


def _game_for_burn():
    game = GameState(rng=random.Random(0))
    # Give the caster a small library so nothing SBA-loses mid-test.
    filler = CardTemplate(
        name="Filler", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=1, toughness=1, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    game.players[0].library = [
        CardInstance(template=filler, owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone="library")
        for _ in range(5)
    ]
    return game


def _spell(game, tmpl, controller=0):
    return CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="stack")


class TestRegisteredBurnHandlersRetired:
    """Real-DB integration: the burn spells whose dedicated SPELL_RESOLVE
    handlers were deleted must still resolve correctly purely through the
    typed-field generic path — proving the deletion was safe."""

    @pytest.mark.parametrize("card_name,amount", [
        ("Lightning Bolt", 3),
        ("Lava Dart", 1),
    ])
    def test_no_dedicated_handler_remains(self, card_name, amount):
        assert not EFFECT_REGISTRY.has_handler(
            card_name, EffectTiming.SPELL_RESOLVE), (
            f"{card_name!r} still has a dedicated SPELL_RESOLVE handler — the "
            f"redundancy this file pins no longer holds, or the handler was "
            f"not deleted")

    @pytest.mark.parametrize("card_name,amount", [
        ("Lightning Bolt", 3),
        ("Lava Dart", 1),
    ])
    def test_burn_goes_face_via_generic_path(self, card_db, card_name, amount):
        game = _game_for_burn()
        card = _spell(game, card_db.get_card(card_name))
        game.players[1].life = 20
        handled = resolve_spell_from_oracle(game, card, 0, targets=None)
        assert handled is True
        assert game.players[1].life == 20 - amount

    def test_burn_kills_a_chosen_creature_via_generic_path(self, card_db):
        game = _game_for_burn()
        victim_tmpl = CardTemplate(
            name="Test Victim 3/3", card_types=[CardType.CREATURE],
            mana_cost=ManaCost(generic=3), supertypes=[], subtypes=[],
            power=3, toughness=3, loyalty=None, keywords=set(), abilities=[],
            color_identity=set(), produces_mana=[], enters_tapped=False,
            oracle_text="", tags=set(),
        )
        victim = CardInstance(template=victim_tmpl, owner=1, controller=1,
                              instance_id=game.next_instance_id(),
                              zone="battlefield")
        game.players[1].battlefield.append(victim)
        bolt = _spell(game, card_db.get_card("Lightning Bolt"))
        handled = resolve_spell_from_oracle(game, bolt, 0, targets=[victim.instance_id])
        assert handled is True
        # Damage is marked by the shared resolver (via deal_damage); lethal
        # death is a state-based action (CR 704.5g), run after resolution —
        # the same order the real spell-resolution flow uses.
        game.check_state_based_actions()
        assert victim not in game.players[1].battlefield, "3 damage should kill a 3/3"
