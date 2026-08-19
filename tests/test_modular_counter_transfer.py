"""Modular keyword mechanic — ETB counter placement and death counter transfer.

# Mechanic under test (CR 702.43)

Modular N: this permanent enters with N +1/+1 counters on it. When it dies,
you may put its +1/+1 counters on target artifact creature.

Two invariants:
  1. ETB: modular_n counters placed on entry, regardless of cast vs reanimate.
  2. DIES: when a modular permanent leaves the battlefield to the graveyard,
     all its +1/+1 counters transfer to the best available artifact creature
     under the same controller's control.

Implementation:
  - Keyword.MODULAR in engine/cards.py Keyword enum.
  - CardTemplate.modular_n: int populated at DB load from oracle text
    `r'(?:^|\\n)modular\\s+(\\d+)'`.
  - ETB counter placement in engine/spell_resolution.py
    ResolutionManager._handle_permanent_etb (before EFFECT_REGISTRY dispatch,
    parallel to planeswalker loyalty / energy production placement).
  - Death trigger in engine/oracle_resolver.resolve_dies_trigger (keyed on
    Keyword.MODULAR in template.keywords, not on card name).

Fixture note:
  The card names used below (Arcbound Hybrid, Arcbound Worker, Ornithopter)
  are *exemplar* DB fixtures only — chosen because they are Modern-legal and
  carry the modular keyword. The engine logic does not reference any card name;
  it keys solely on Keyword.MODULAR and template.modular_n. Any future card
  with "Modular N" in its oracle text will receive the same treatment.

Class size: 23+ modular cards in MTGJSON Modern (Arcbound Bruiser, Condor,
Crusher, Fiend, Hybrid, Prototype, Ravager, Slasher, Worker, Wanderer,
Scrapyard Recombiner, Zabaz, …). The mechanic owns every one.

Subsystem: engine/cards.py (Keyword enum + CardTemplate field),
engine/card_database.py (KEYWORD_MAP + oracle parse → modular_n),
engine/spell_resolution.py (ETB counter placement),
engine/oracle_resolver.py (death trigger counter transfer).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType, Keyword
from engine.game_state import GameState
from engine.mana import ManaCost


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_artifact_creature(name: str = "Dummy Artifact Creature",
                             power: int = 1, toughness: int = 1) -> CardTemplate:
    """Synthetic 1/1 artifact creature with no abilities (target for modular transfer)."""
    return CardTemplate(
        name=name,
        card_types=[CardType.ARTIFACT, CardType.CREATURE],
        mana_cost=ManaCost(),
        power=power,
        toughness=toughness,
    )


def _make_modular_artifact_creature(modular_n: int = 2,
                                     name: str = "Test Modular Creature") -> CardTemplate:
    """Synthetic 0/0 artifact creature with Modular N.

    The name is arbitrary — engine logic never inspects it.
    The oracle_text carries the standard Modular reminder text so that
    CardTemplate.__post_init__ can derive Keyword.MODULAR and modular_n
    for templates constructed outside CardDatabase.
    """
    return CardTemplate(
        name=name,
        card_types=[CardType.ARTIFACT, CardType.CREATURE],
        mana_cost=ManaCost(),
        power=0,
        toughness=0,
        oracle_text=(
            f"Modular {modular_n} (This creature enters with "
            f"{modular_n} +1/+1 counter(s) on it. When it dies, you may put "
            f"its +1/+1 counters on target artifact creature.)"
        ),
    )


def _place_on_battlefield(game: GameState, tmpl: CardTemplate,
                           controller: int) -> CardInstance:
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _etb(game: GameState, tmpl: CardTemplate, controller: int) -> CardInstance:
    """Place a card on the battlefield and fire the full ETB pipeline
    (including _handle_permanent_etb for modular counter placement)."""
    from engine.spell_resolution import ResolutionManager
    card = _place_on_battlefield(game, tmpl, controller)
    ResolutionManager._handle_permanent_etb(game, card, controller)
    return card


# ─── Keyword enum ────────────────────────────────────────────────────────────

class TestModularKeywordEnum:

    def test_keyword_modular_enum_entry_exists(self):
        """Keyword.MODULAR must be a member of the Keyword enum.

        Mechanism: MODULAR = 'modular' must be declared in engine/cards.py so
        KEYWORD_MAP can map the MTGJSON keyword string → enum member at load time,
        and so runtime code can check ``Keyword.MODULAR in creature.keywords``.
        """
        assert hasattr(Keyword, "MODULAR"), (
            "Keyword enum must have a MODULAR member. "
            "Add MODULAR = 'modular' to the Keyword enum in engine/cards.py."
        )
        assert Keyword.MODULAR.value == "modular", (
            "Keyword.MODULAR must have value 'modular'."
        )


# ─── DB-level detection ───────────────────────────────────────────────────────

class TestModularKeywordDetectedInDB:

    def test_arcbound_hybrid_has_keyword_modular(self, card_db):
        """A real modular card must have Keyword.MODULAR in template.keywords.

        Mechanism: KEYWORD_MAP maps 'Modular' → Keyword.MODULAR at DB load;
        the oracle word-boundary scan adds it for cards whose MTGJSON keywords
        list it explicitly as 'Modular'.
        Arcbound Hybrid (oracle: 'Modular 2 (...)') is the exemplar fixture.
        """
        tmpl = card_db.get_card("Arcbound Hybrid")
        if tmpl is None:
            pytest.skip("Arcbound Hybrid not in DB")
        assert Keyword.MODULAR in (tmpl.keywords or set()), (
            "Arcbound Hybrid must carry Keyword.MODULAR in template.keywords. "
            "Add 'Modular': Keyword.MODULAR to KEYWORD_MAP in card_database.py."
        )

    def test_arcbound_hybrid_has_correct_modular_n(self, card_db):
        """A modular card must have template.modular_n set to the correct count.

        Mechanism: after KEYWORD_MAP adds Keyword.MODULAR, the oracle-derived
        properties section in CardDatabase parses r'(?:^|\\n)modular\\s+(\\d+)'
        and stores the integer N in CardTemplate.modular_n.
        Arcbound Hybrid has 'Modular 2', so modular_n must be 2.
        """
        tmpl = card_db.get_card("Arcbound Hybrid")
        if tmpl is None:
            pytest.skip("Arcbound Hybrid not in DB")
        assert hasattr(tmpl, "modular_n"), (
            "CardTemplate must have a modular_n field. "
            "Add modular_n: int = 0 to CardTemplate in engine/cards.py."
        )
        assert tmpl.modular_n == 2, (
            f"Arcbound Hybrid modular_n should be 2, got {tmpl.modular_n}. "
            "Parse r'(?:^|\\n)modular\\s+(\\d+)' from oracle text in card_database.py."
        )

    def test_arcbound_worker_has_modular_n_one(self, card_db):
        """Arcbound Worker ('Modular 1') must have modular_n == 1.

        Mechanism: same oracle parse path as above; N=1 edge case.
        """
        tmpl = card_db.get_card("Arcbound Worker")
        if tmpl is None:
            pytest.skip("Arcbound Worker not in DB")
        assert getattr(tmpl, "modular_n", 0) == 1, (
            f"Arcbound Worker modular_n should be 1, got {getattr(tmpl, 'modular_n', 0)}."
        )

    def test_non_modular_card_has_modular_n_zero(self, card_db):
        """A non-modular card must have modular_n == 0 (the default).

        Mechanism: no 'Modular N' in oracle text → modular_n stays at
        its zero default; the parse only fires when Keyword.MODULAR is present.
        """
        tmpl = card_db.get_card("Ornithopter")
        if tmpl is None:
            pytest.skip("Ornithopter not in DB")
        assert getattr(tmpl, "modular_n", 0) == 0, (
            "Ornithopter (no modular) must have modular_n == 0."
        )


# ─── ETB counter placement ───────────────────────────────────────────────────

class TestModularEntersBattlefieldCounters:

    def test_modular_creature_enters_with_n_plus1_counters(self):
        """A Modular N creature enters the battlefield with exactly N +1/+1 counters.

        Mechanism: ResolutionManager._handle_permanent_etb checks
        Keyword.MODULAR in template.keywords and places template.modular_n
        counters via card.plus_counters += template.modular_n.
        """
        game = GameState(rng=random.Random(0))
        tmpl = _make_modular_artifact_creature(modular_n=2)
        card = _etb(game, tmpl, 0)
        assert card.plus_counters == 2, (
            f"A Modular 2 creature must enter with 2 +1/+1 counters, "
            f"got {card.plus_counters}."
        )

    def test_modular_one_creature_enters_with_one_counter(self):
        """A Modular 1 creature enters with exactly 1 +1/+1 counter."""
        game = GameState(rng=random.Random(0))
        tmpl = _make_modular_artifact_creature(modular_n=1)
        card = _etb(game, tmpl, 0)
        assert card.plus_counters == 1, (
            f"A Modular 1 creature must enter with 1 counter, got {card.plus_counters}."
        )

    def test_modular_counters_increase_power_and_toughness(self):
        """Modular ETB counters must be reflected in the creature's P/T.

        Mechanism: CardInstance.power/toughness properties add plus_counters
        to the base values. A 0/0 Modular 3 creature becomes 3/3.
        """
        game = GameState(rng=random.Random(0))
        tmpl = _make_modular_artifact_creature(modular_n=3)
        card = _etb(game, tmpl, 0)
        assert card.power == 3, (
            f"Modular 3 creature (base 0/0) must be 3/* after ETB, got {card.power}."
        )
        assert card.toughness == 3, (
            f"Modular 3 creature (base 0/0) must be */3 after ETB, got {card.toughness}."
        )

    def test_non_modular_creature_does_not_gain_counters(self):
        """A creature without modular must NOT gain +1/+1 counters on ETB.

        Mechanism: the counter placement block is gated on
        ``Keyword.MODULAR in template.keywords`` — false for non-modular cards.
        """
        game = GameState(rng=random.Random(0))
        tmpl = _make_artifact_creature()
        card = _etb(game, tmpl, 0)
        assert card.plus_counters == 0, (
            f"Non-modular creature must enter with 0 counters, got {card.plus_counters}."
        )


# ─── Death trigger counter transfer ──────────────────────────────────────────

class TestModularCountersTransferToArtifactCreatureOnDeath:

    def test_modular_counters_transfer_to_artifact_creature_on_death(self):
        """When a modular creature with counters dies, its +1/+1 counters
        transfer to a target artifact creature under the same controller.

        This is the canonical test described by CR 702.43b:
        'When [modular creature] dies, you may put its +1/+1 counters
        on target artifact creature.'

        Mechanism: resolve_dies_trigger (engine/oracle_resolver.py) checks
        Keyword.MODULAR in creature.template.keywords, reads creature.plus_counters,
        finds the best artifact-creature target on the controller's battlefield,
        and moves the counters to it.

        Exemplar fixture: a synthetic Modular 2 artifact creature dies;
        a synthetic 1/1 artifact creature receives 2 +1/+1 counters → becomes 3/3.
        Card names are arbitrary — the mechanic is name-free.
        """
        game = GameState(rng=random.Random(0))

        # Target: a 1/1 artifact creature already on the battlefield
        target_tmpl = _make_artifact_creature(name="Target Artifact Creature",
                                              power=1, toughness=1)
        target = _place_on_battlefield(game, target_tmpl, 0)
        assert target.plus_counters == 0

        # Source: a Modular 2 artifact creature that enters with 2 counters
        source_tmpl = _make_modular_artifact_creature(modular_n=2,
                                                       name="Dying Modular Creature")
        source = _etb(game, source_tmpl, 0)
        assert source.plus_counters == 2, "source must have 2 counters before death"

        # Kill the modular creature
        game._creature_dies(source)

        # The target must have received the 2 counters
        assert target.plus_counters == 2, (
            f"Target artifact creature must receive 2 +1/+1 counters from modular "
            f"death trigger, got {target.plus_counters}. "
            "Add modular death trigger handling to resolve_dies_trigger in "
            "engine/oracle_resolver.py."
        )

    def test_modular_death_counters_increase_target_power_toughness(self):
        """Transferred modular counters must be reflected in the target's P/T."""
        game = GameState(rng=random.Random(0))
        target_tmpl = _make_artifact_creature(power=1, toughness=2)
        target = _place_on_battlefield(game, target_tmpl, 0)

        source_tmpl = _make_modular_artifact_creature(modular_n=3)
        source = _etb(game, source_tmpl, 0)

        game._creature_dies(source)

        assert target.power == 4, (
            f"Target (base 1/*) must be 4/* after receiving 3 modular counters, "
            f"got {target.power}."
        )
        assert target.toughness == 5, (
            f"Target (base */2) must be */5 after receiving 3 modular counters, "
            f"got {target.toughness}."
        )

    def test_modular_death_with_no_artifact_creature_target_does_not_crash(self):
        """When no artifact creature is available to receive counters, the
        death trigger resolves harmlessly (the effect is optional).

        Mechanism: resolve_dies_trigger skips counter transfer when
        no valid artifact-creature target exists on the battlefield.
        """
        game = GameState(rng=random.Random(0))

        # No artifact creature on the battlefield
        source_tmpl = _make_modular_artifact_creature(modular_n=2)
        source = _etb(game, source_tmpl, 0)

        # Death should not raise — the trigger is optional, no valid targets → fizzle
        game._creature_dies(source)
        # If we reach here without exception, the test passes.

    def test_modular_counters_do_not_transfer_to_opponent_artifact_creature(self):
        """Modular counter transfer is limited to the dying creature's controller.

        Mechanism: resolve_dies_trigger checks ``game.players[controller].battlefield``
        — only the controller's permanents are eligible targets.
        """
        game = GameState(rng=random.Random(0))

        # Opponent's artifact creature (controller=1)
        opp_target_tmpl = _make_artifact_creature(name="Opponent Artifact Creature")
        opp_target = _place_on_battlefield(game, opp_target_tmpl, 1)

        # Controller's modular creature (controller=0)
        source_tmpl = _make_modular_artifact_creature(modular_n=2)
        source = _etb(game, source_tmpl, 0)

        game._creature_dies(source)

        assert opp_target.plus_counters == 0, (
            "Modular death trigger must not place counters on the opponent's creatures."
        )

    def test_modular_death_with_zero_counters_does_not_transfer(self):
        """If a modular creature has had its counters removed before death,
        the death trigger moves zero counters (no net change on target).

        Mechanism: resolve_dies_trigger reads creature.plus_counters at death
        time; 0 counters means nothing to transfer.
        """
        game = GameState(rng=random.Random(0))
        target_tmpl = _make_artifact_creature(power=1, toughness=1)
        target = _place_on_battlefield(game, target_tmpl, 0)

        source_tmpl = _make_modular_artifact_creature(modular_n=2)
        source = _place_on_battlefield(game, source_tmpl, 0)
        # Simulate counters being removed (e.g., by Hex Parasite)
        source.plus_counters = 0

        game._creature_dies(source)

        # Target should still have 0 counters — nothing transferred
        assert target.plus_counters == 0, (
            "Zero modular counters at death time must result in no counter transfer."
        )
