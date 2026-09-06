"""Shared resolver for the "deal N damage to any target" mechanic
(CR 601.2c target declaration / CR 119 damage application).

# Mechanic under test

`engine.card_effects.py` had four independent, near-identical
EFFECT_REGISTRY SPELL_RESOLVE handlers (Lightning Bolt, Lava Dart,
Unholy Heat, Grapeshot) that each re-implemented the same target
filter ("a declared id must resolve to a battlefield creature or
planeswalker, else go face") and damage-application step inline.
Per CLAUDE.md's ABSTRACTION CONTRACT, `EFFECT_REGISTRY.register("Card
Name", ...)` calls are literal per-card-name registrations invisible
to `tools/check_abstraction.py`'s regex, but bespoke logic living
*inside* those handler bodies is the same anti-pattern the ratchet
targets for `card.name ==` checks — this program's remediation plan
explicitly holds registry-handler bodies to the same bar.

`engine.oracle_resolver.resolve_damage_to_chosen_target` is the new
single owner: it walks a declared target list, applies damage via
`engine.damage.deal_damage` (so the deathtouch SBA marker and SBA
scheduling compose correctly, and any future replacement-effect hook
`deal_damage` grows — e.g. the lifelink hook its own docstring already
reserves — is inherited by every caller for free), and falls back to
the opponent's face. All four handlers now call it instead of
re-implementing the filter+mutation.

Two of the four handlers had real, per-card quirks that the shared
resolver does NOT swallow (both stay tested here, on the shrunk
handler, not the resolver):

- **Unholy Heat**: the printed amount depends on delirium (2 damage,
  or 6 if 4+ card types are in the caster's graveyard) — an
  oracle-derived AMOUNT computation, orthogonal to target resolution.
- **Grapeshot**: the pre-fix handler ignored `targets` entirely and
  always mutated `opponent.life` directly, bypassing `deal_damage` —
  a real bug (not just an internal-mechanism difference, unlike the
  behaviour-preserving deathtouch-marker rewrite in the combat-damage
  funnel). Migrating it through the shared resolver both fixes the
  bug and removes the duplicate implementation. The live AI always
  casts Grapeshot with `targets=[-1]` (`ai/ev_player.py` — "Storm
  spells (Grapeshot) deal 1 damage x storm copies — always target
  face"), so this fix does not change any current sim outcome; it
  only makes the engine mechanically correct for any future caller
  (a manual creature-target cast, or a test) that supplies a real
  target id.

Lightning Bolt and Lava Dart have no such per-card quirk — both are a
whole-effect fixed-N burn. Their EFFECT_REGISTRY handlers have since
been DELETED: the shape is now classified once at DB load into
`CardTemplate.direct_damage_data` (oracle_parser.parse_direct_damage_spell)
and dispatched — no oracle inspection at resolve time — through the
typed-field branch of `oracle_resolver.resolve_spell_from_oracle` into
the same shared resolver. So the fixed-N burn spells need no
registration at all (~79 in the DB resolve via the typed path), which
lowered the card-name-registry baseline by 2. See
`tests/test_direct_damage_shared_resolver.py` for the typed-path pins
and the redundancy proof, and this file's `TestLavaDartMigratedHandler`
for the same behaviour asserted through `resolve_spell_from_oracle`.

Card names appear only as fixture carriers (synthetic CardTemplates
for the resolver-level tests; real DB cards for the handler-level
integration tests) per CLAUDE.md's ABSTRACTION CONTRACT — the
mechanic under test is "resolve declared damage to a legal target,
else face", not any specific card.
"""
from __future__ import annotations

import random

import pytest

from engine.card_effects import EFFECT_REGISTRY, EffectTiming
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState
from engine.oracle_resolver import (resolve_damage_to_chosen_target,
                                     resolve_spell_from_oracle)


# ─── synthetic fixtures for resolver-unit tests ────────────────────


def _synthetic_creature(game, name, controller, power=2, toughness=2,
                         keywords=None, card_types=None):
    tmpl = CardTemplate(
        name=name, card_types=card_types or [CardType.CREATURE],
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
    game.players[controller].battlefield.append(card)
    return card


def _synthetic_spell_source(game, name, controller, keywords=None):
    """A stack-zone 'spell' card usable as a `deal_damage` source."""
    tmpl = CardTemplate(
        name=name, card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=keywords or set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="stack",
    )
    card._game_state = game
    return card


class TestResolveDamageToChosenTargetUnit:
    """`resolve_damage_to_chosen_target` — the shared owner."""

    def test_declared_creature_target_takes_damage_via_funnel(self):
        game = GameState(rng=random.Random(0))
        source = _synthetic_spell_source(game, "Test Bolt", 0)
        victim = _synthetic_creature(game, "Victim", 1, toughness=4)

        hit = resolve_damage_to_chosen_target(
            game, source, 0, 3, targets=[victim.instance_id])

        assert hit is victim
        assert victim.damage_marked == 3

    def test_face_sentinel_goes_to_opponent_life(self):
        game = GameState(rng=random.Random(0))
        game.players[1].life = 20
        source = _synthetic_spell_source(game, "Test Bolt", 0)

        hit = resolve_damage_to_chosen_target(game, source, 0, 3, targets=[-1])

        assert hit is None
        assert game.players[1].life == 17

    def test_no_declared_targets_goes_to_opponent_face(self):
        """An empty/None target list (spell cast with no legal
        permanent target) still resolves — CR 608.2b does not apply
        since a player is always a legal 'any target' target."""
        game = GameState(rng=random.Random(0))
        game.players[1].life = 20
        source = _synthetic_spell_source(game, "Test Bolt", 0)

        hit = resolve_damage_to_chosen_target(game, source, 0, 2, targets=None)

        assert hit is None
        assert game.players[1].life == 18

    def test_declared_target_must_be_creature_or_planeswalker(self):
        """A declared id that resolves to a non-creature, non-PW
        permanent (e.g. a land) is not a legal 'any target' hit —
        falls through to face, matching CR 601.2c."""
        game = GameState(rng=random.Random(0))
        game.players[1].life = 20
        source = _synthetic_spell_source(game, "Test Bolt", 0)
        land = _synthetic_creature(game, "Test Land", 1,
                                    card_types=[CardType.LAND])

        hit = resolve_damage_to_chosen_target(
            game, source, 0, 3, targets=[land.instance_id])

        assert hit is None
        assert game.players[1].life == 17

    def test_zero_amount_is_a_no_op(self):
        """CR 119.4: 0 damage is not dealt."""
        game = GameState(rng=random.Random(0))
        game.players[1].life = 20
        source = _synthetic_spell_source(game, "Test Bolt", 0)

        hit = resolve_damage_to_chosen_target(game, source, 0, 0, targets=[-1])

        assert hit is None
        assert game.players[1].life == 20

    def test_creature_hit_routes_through_deal_damage_not_raw_mutation(self):
        """Contract pin: the resolver must call `engine.damage.deal_damage`
        for the application step rather than mutating `damage_marked`
        itself (the exact "no single owner" bug this migration fixes —
        see the pre-fix Grapeshot handler, which mutated `opponent.life`
        directly and ignored `deal_damage`/`targets` entirely). Verified
        by monkeypatching the shared primitive and asserting it was
        called with the resolved target — a stronger pin than asserting
        the final `damage_marked` value, which a bespoke reimplementation
        could also produce by coincidence."""
        import engine.oracle_resolver as oracle_resolver_module
        game = GameState(rng=random.Random(0))
        source = _synthetic_spell_source(game, "Test Bolt", 0)
        victim = _synthetic_creature(game, "Victim", 1, toughness=6)

        calls = []
        real_deal_damage = None
        import engine.damage as damage_module
        real_deal_damage = damage_module.deal_damage

        def _spy(src, tgt, amount, **kwargs):
            calls.append((src, tgt, amount))
            return real_deal_damage(src, tgt, amount, **kwargs)

        damage_module.deal_damage = _spy
        try:
            hit = resolve_damage_to_chosen_target(
                game, source, 0, 4, targets=[victim.instance_id])
        finally:
            damage_module.deal_damage = real_deal_damage

        assert hit is victim
        assert calls == [(source, victim, 4)], (
            f"expected exactly one deal_damage(source, victim, 4) call, "
            f"got {calls}"
        )

    def test_face_hit_routes_through_deal_damage_not_raw_mutation(self):
        game = GameState(rng=random.Random(0))
        game.players[1].life = 20
        source = _synthetic_spell_source(game, "Test Bolt", 0)

        import engine.damage as damage_module
        calls = []
        real_deal_damage = damage_module.deal_damage

        def _spy(src, tgt, amount, **kwargs):
            calls.append((src, tgt, amount))
            return real_deal_damage(src, tgt, amount, **kwargs)

        damage_module.deal_damage = _spy
        try:
            hit = resolve_damage_to_chosen_target(game, source, 0, 4, targets=[-1])
        finally:
            damage_module.deal_damage = real_deal_damage

        assert hit is None
        assert calls == [(source, game.players[1], 4)]


# ─── real-DB integration: migrated EFFECT_REGISTRY handlers ────────


def _put_creature_in_play(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card not in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _make_spell(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card not in DB: {name}"
    return CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="stack",
    )


class TestLavaDartMigratedHandler:
    """Lava Dart's fixed-amount (=1) burn now has NO EFFECT_REGISTRY handler
    at all: the shape is classified once at load into
    ``CardTemplate.direct_damage_data`` and dispatched through the typed-field
    branch of ``resolve_spell_from_oracle`` into the same shared resolver.
    These pin that the typed path applies the amount to a declared creature
    and to the face sentinel — the behaviour the deleted handler owned."""

    def test_lava_dart_damages_declared_creature(self, card_db):
        game = GameState(rng=random.Random(0))
        target = _put_creature_in_play(game, card_db, "Tarmogoyf", 1)
        spell = _make_spell(game, card_db, "Lava Dart", 0)

        handled = resolve_spell_from_oracle(
            game, spell, 0, targets=[target.instance_id])

        assert handled
        assert target.damage_marked == 1

    def test_lava_dart_face_sentinel(self, card_db):
        game = GameState(rng=random.Random(0))
        opp = game.players[1]
        life_before = opp.life
        spell = _make_spell(game, card_db, "Lava Dart", 0)

        handled = resolve_spell_from_oracle(game, spell, 0, targets=[-1])

        assert handled
        assert opp.life == life_before - 1


class TestUnholyHeatDeliriumConditionalAmount:
    """Unholy Heat's real per-card quirk: amount is 2, or 6 with
    delirium (4+ card types in the caster's graveyard). The shared
    resolver only owns target application — the amount computation
    stays card-specific, as it must (it is not part of the generic
    'deal N damage to any target' shape)."""

    def test_deals_base_2_damage_without_delirium(self, card_db):
        game = GameState(rng=random.Random(0))
        target = _put_creature_in_play(game, card_db, "Tarmogoyf", 1)
        spell = _make_spell(game, card_db, "Unholy Heat", 0)
        # No graveyard contents — delirium is not active.

        fired = EFFECT_REGISTRY.execute(
            "Unholy Heat", EffectTiming.SPELL_RESOLVE,
            game, spell, 0, targets=[target.instance_id],
        )

        assert fired
        assert target.damage_marked == 2

    def test_deals_6_damage_with_delirium(self, card_db):
        game = GameState(rng=random.Random(0))
        target = _put_creature_in_play(game, card_db, "Tarmogoyf", 1)
        spell = _make_spell(game, card_db, "Unholy Heat", 0)

        # Populate the caster's graveyard with 4 distinct card types.
        gy_cards = [
            ("Lightning Bolt", "hand"),   # instant
            ("Grapeshot", "hand"),        # sorcery
            ("Ornithopter", "hand"),      # artifact creature
            ("Blood Moon", "hand"),       # enchantment
        ]
        for name, _zone in gy_cards:
            tmpl = card_db.get_card(name)
            assert tmpl is not None, f"card not in DB: {name}"
            c = CardInstance(
                template=tmpl, owner=0, controller=0,
                instance_id=game.next_instance_id(), zone="graveyard",
            )
            c._game_state = game
            game.players[0].graveyard.append(c)

        fired = EFFECT_REGISTRY.execute(
            "Unholy Heat", EffectTiming.SPELL_RESOLVE,
            game, spell, 0, targets=[target.instance_id],
        )

        assert fired
        assert target.damage_marked == 6, (
            f"delirium (4+ card types in graveyard) must deal 6, "
            f"got damage_marked={target.damage_marked}"
        )


class TestGrapeshotRespectsDeclaredTarget:
    """Regression: pre-migration, `grapeshot_resolve` ignored
    `targets` entirely and always mutated `opponent.life` directly —
    a real bug (Grapeshot's oracle is "deals 1 damage to any target",
    not "deals 1 damage to each opponent"). The shared resolver fixes
    this: a declared creature target is now actually hit."""

    def test_grapeshot_damages_declared_creature_target(self, card_db):
        game = GameState(rng=random.Random(0))
        target = _put_creature_in_play(game, card_db, "Ornithopter", 1)
        spell = _make_spell(game, card_db, "Grapeshot", 0)

        fired = EFFECT_REGISTRY.execute(
            "Grapeshot", EffectTiming.SPELL_RESOLVE,
            game, spell, 0, targets=[target.instance_id],
        )

        assert fired
        assert target.damage_marked == 1, (
            "Grapeshot must respect a declared creature target instead "
            "of always going face"
        )

    def test_grapeshot_face_sentinel_still_works(self, card_db):
        """Regression anchor: the dominant real-game case (AI always
        casts Grapeshot with targets=[-1], per ai/ev_player.py's
        storm-finisher target policy) must be unchanged."""
        game = GameState(rng=random.Random(0))
        opp = game.players[1]
        life_before = opp.life
        spell = _make_spell(game, card_db, "Grapeshot", 0)

        fired = EFFECT_REGISTRY.execute(
            "Grapeshot", EffectTiming.SPELL_RESOLVE,
            game, spell, 0, targets=[-1],
        )

        assert fired
        assert opp.life == life_before - 1

    def test_grapeshot_no_declared_targets_defaults_to_face(self, card_db):
        game = GameState(rng=random.Random(0))
        opp = game.players[1]
        life_before = opp.life
        spell = _make_spell(game, card_db, "Grapeshot", 0)

        fired = EFFECT_REGISTRY.execute(
            "Grapeshot", EffectTiming.SPELL_RESOLVE,
            game, spell, 0, targets=None,
        )

        assert fired
        assert opp.life == life_before - 1


class TestDeadCardRegistrationRemoved:
    """Phlage, Titan of Fire's Fury was removed from ModernAtomic
    entirely by the MTGJSON refresh that followed its Modern ban
    (commit d02c543, already on main). Its EFFECT_REGISTRY ETB
    handler — one of the "deal N damage to any target" cluster
    surfaced by this migration's research pass — was therefore
    unreachable: no CardInstance can ever carry that literal name.
    Zero blast radius (grepped: no test constructs a card with this
    exact name), so it is deleted rather than migrated, matching the
    class-size/dead-code reasoning this program applies elsewhere
    (see docs/design/rules-foundation-sweep-tracker.md, 0a's
    discard-path and Annihilator-sacrifice findings)."""

    def test_phlage_etb_handler_no_longer_registered(self, card_db):
        assert card_db.get_card("Phlage, Titan of Fire's Fury") is None, (
            "Phlage is expected to be absent from the current "
            "ModernAtomic DB (removed after its Modern ban) — if this "
            "assertion fails, the card has returned to the pool and "
            "the deleted EFFECT_REGISTRY handler needs to be restored "
            "(or replaced by the shared resolver) rather than staying "
            "deleted."
        )
        assert not EFFECT_REGISTRY.has_handler(
            "Phlage, Titan of Fire's Fury", EffectTiming.ETB
        )
