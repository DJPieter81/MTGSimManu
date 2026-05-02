"""Cast-time validation must reject battlefield-target removal
spells with no legal target.

Reference: 2026-05-02 audit on current main (post-merges through
PR #221) — confirmed bug class via direct reproduction:
  - Wear, Wear // Tear, Disenchant, Smelt, Shatter (target artifact[or enchantment])
  - Vindicate (target permanent)
  - Maelstrom Pulse (target nonland permanent)
  - Nature's Claim (target artifact or enchantment)
  - Galvanic Discharge with no creature/planeswalker target

All castable on empty battlefield today — the existing
``cast_manager.can_cast`` validates only ``target creature you
control`` and ``target creature`` (with hand-coded exceptions).
Sister patterns (~250+ Modern-pool cards) get no cast-time check.

Class-of-bug scope: any instant/sorcery whose oracle says
``destroy|exile|return|tap|untap|fight|deal X damage to``
target [supertype]? [type]`` with the type bound to a battlefield
permanent class. Removal-heavy decks (Boros Energy, Azorius
Control, Domain Zoo SB, Dimir Midrange, Living End SB) waste mana
and a card per cast in matchups where the relevant permanent class
is absent from the board.

Architectural note: this is a tactical patch that extends the
existing pattern-by-pattern validation. The medium-term plan is
``docs/proposals/2026-05-02_unified_target_solver.md`` — a unified
``engine/target_solver`` module replacing five scattered
validation paths with one oracle-driven solver. This patch closes
the immediate bug class without depending on that refactor.

The fix lives in ``engine.cast_manager.can_cast`` alongside the
existing ``target creature`` predicate.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _land(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing land: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _hand(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _battlefield(game, card_db, name: str, controller: int) -> CardInstance:
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _setup_main_phase(game) -> None:
    from engine.game_state import Phase
    game.current_phase = Phase.MAIN1
    game.active_player = 0


def _give_lands_for(game, card_db, n: int) -> None:
    """Give P1 lands.  Always provides at least one of each basic
    color so any mono- or two-color spell is payable, then pads
    with extra basics to reach n.  This sidesteps the mana check
    so target validation is the only gating logic under test."""
    base = ("Plains", "Island", "Swamp", "Mountain", "Forest")
    # Always seed all five colors first
    for n_card in base[:min(n, 5)]:
        _land(game, card_db, n_card, 0)
    # Pad remainder with Plains to reach n
    for _ in range(max(0, n - 5)):
        _land(game, card_db, "Plains", 0)
    # If n < 5, ensure all five colors anyway (target tests need
    # mana not to be the rejection reason)
    if n < 5:
        for c in base[n:]:
            _land(game, card_db, c, 0)


class TestTargetArtifactCannotBeCastWithoutTarget:
    """`target artifact` removal (Shatter, Smelt, Wear, Ancient
    Grudge, ...) requires an artifact on the battlefield.  No
    artifact = no legal target = uncastable per CR 601.2c."""

    def test_shatter_uncastable_with_no_artifact(self, card_db):
        """Shatter (1R, "Destroy target artifact") with empty boards
        on both sides cannot be cast.

        Today: cast_manager.can_cast returns True; AI burns 2 mana
        and a card on a silent fizzle at resolution."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=4)

        spell = _hand(game, card_db, "Shatter", 0)
        # No artifacts on either battlefield.
        assert game.can_cast(0, spell) is False, (
            f"Shatter was reported castable with no artifact on any "
            f"battlefield.  Oracle: \"Destroy target artifact\".  "
            f"No artifact = no legal target.  Wasted cast burns 2 "
            f"mana + 1 card."
        )

    def test_shatter_castable_with_own_artifact(self, card_db):
        """Regression: Shatter must be castable when an artifact is
        on the battlefield (own or opponent's — \"target artifact\"
        is unrestricted by controller)."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=4)
        _battlefield(game, card_db, "Mox Opal", 0)  # own artifact
        spell = _hand(game, card_db, "Shatter", 0)
        assert game.can_cast(0, spell) is True, (
            f"Regression: Shatter NOT castable with Mox Opal on own "
            f"battlefield.  \"target artifact\" accepts artifacts "
            f"under any controller's control."
        )

    def test_shatter_castable_with_opponent_artifact(self, card_db):
        """Regression: opponent's artifact is a legal target."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=4)
        _battlefield(game, card_db, "Mox Opal", 1)  # opp's artifact
        spell = _hand(game, card_db, "Shatter", 0)
        assert game.can_cast(0, spell) is True, (
            f"Regression: Shatter NOT castable with opponent's "
            f"Mox Opal on the battlefield."
        )

    def test_artifact_lands_count_as_targets(self, card_db):
        """Class-of-bug check: Tanglepool Bridge is an artifact
        land. Shatter targeting it is legal even with no other
        artifacts on board.  Regression for the type-filter logic
        — must not exclude lands that happen to be artifacts."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=4)
        _battlefield(game, card_db, "Tanglepool Bridge", 1)
        spell = _hand(game, card_db, "Shatter", 0)
        assert game.can_cast(0, spell) is True, (
            f"Regression: Shatter NOT castable with opp's "
            f"Tanglepool Bridge (an artifact land).  Lands with "
            f"the artifact card-type are legal targets for "
            f"\"target artifact\" spells."
        )


class TestTargetArtifactOrEnchantmentCannotBeCastWithoutTarget:
    """`target artifact or enchantment` (Disenchant, Nature's
    Claim, Wear, Altar's Light) requires either type on the
    battlefield."""

    def test_disenchant_uncastable_with_neither(self, card_db):
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=3)
        spell = _hand(game, card_db, "Disenchant", 0)
        assert game.can_cast(0, spell) is False, (
            f"Disenchant was reported castable with no artifact or "
            f"enchantment on any battlefield."
        )

    def test_disenchant_castable_with_artifact_only(self, card_db):
        """Regression: artifact alone satisfies the disjunction."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=3)
        _battlefield(game, card_db, "Mox Opal", 1)
        spell = _hand(game, card_db, "Disenchant", 0)
        assert game.can_cast(0, spell) is True, (
            f"Regression: Disenchant NOT castable with opp's "
            f"Mox Opal on the battlefield."
        )

    def test_disenchant_castable_with_enchantment_only(self, card_db):
        """Regression: enchantment alone satisfies the disjunction."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=3)
        _battlefield(game, card_db, "Leyline Binding", 1)
        spell = _hand(game, card_db, "Disenchant", 0)
        assert game.can_cast(0, spell) is True, (
            f"Regression: Disenchant NOT castable with opp's "
            f"Leyline Binding (an enchantment) on the battlefield."
        )


class TestTargetPermanentCannotBeCastWithoutTarget:
    """`target permanent` (Vindicate, Beast Within, Anguished
    Unmaking, Assassin's Trophy) requires any permanent on the
    battlefield.  Lands count.  This is the broadest type filter
    — only completely empty boards reject the cast."""

    def test_vindicate_uncastable_with_no_permanents(self, card_db):
        """Both players have NO lands and NO permanents — Vindicate
        cannot find a target."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        # Use mana pool to pay for Vindicate (1WB) without lands —
        # avoids the implicit 'lands count as permanents' problem.
        from engine.cards import Color
        game.players[0].mana_pool.add(Color.WHITE.value, 1)
        game.players[0].mana_pool.add(Color.BLACK.value, 1)
        game.players[0].mana_pool.add('C', 1)
        spell = _hand(game, card_db, "Vindicate", 0)
        assert game.can_cast(0, spell) is False, (
            f"Vindicate was reported castable on a fully empty "
            f"battlefield (no lands, no permanents on either side). "
            f"Oracle: \"Destroy target permanent\".  Empty board = "
            f"no legal target = uncastable."
        )

    def test_vindicate_castable_with_just_lands(self, card_db):
        """Regression: lands ARE permanents.  Vindicate targeting a
        land is legal.  Pieter notes this is meaningful in Modern —
        Tron-piece destruction or Urza's Saga removal is a real
        line of play."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        # Give P1 enough mana via lands (own lands ARE legal targets
        # too, but more importantly opp may have lands).
        _give_lands_for(game, card_db, n=3)
        spell = _hand(game, card_db, "Vindicate", 0)
        assert game.can_cast(0, spell) is True, (
            f"Regression: Vindicate NOT castable when lands are on "
            f"the battlefield.  Lands are permanents and are legal "
            f"targets for \"destroy target permanent\" spells."
        )


class TestTargetNonlandPermanentCannotBeCastWithoutTarget:
    """`target nonland permanent` (Maelstrom Pulse, Anguished
    Unmaking) excludes lands from the target pool.  Lands-only
    boards must reject the cast."""

    def test_maelstrom_pulse_uncastable_with_lands_only(self, card_db):
        """Both players have ONLY lands — Maelstrom Pulse cannot
        find a nonland permanent target."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=4)  # P1 lands only
        # P2 also lands only
        for n in ("Plains", "Forest"):
            _land(game, card_db, n, 1)
        spell = _hand(game, card_db, "Maelstrom Pulse", 0)
        assert game.can_cast(0, spell) is False, (
            f"Maelstrom Pulse was reported castable when both "
            f"battlefields contain only lands.  Oracle: \"Destroy "
            f"target nonland permanent\".  Lands-only board has no "
            f"legal target."
        )

    def test_maelstrom_pulse_castable_with_creature_or_artifact(self, card_db):
        """Regression: any nonland permanent is a legal target."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=4)
        _battlefield(game, card_db, "Mox Opal", 1)  # nonland artifact
        spell = _hand(game, card_db, "Maelstrom Pulse", 0)
        assert game.can_cast(0, spell) is True, (
            f"Regression: Maelstrom Pulse NOT castable with opp's "
            f"Mox Opal (a nonland artifact) on the battlefield."
        )


class TestTargetCreatureOrPlaneswalkerCannotBeCastWithoutTarget:
    """`target creature or planeswalker` (Galvanic Discharge,
    Lightning Bolt's modal cousins) requires either type."""

    def test_galvanic_discharge_uncastable_with_no_target(self, card_db):
        """Galvanic Discharge oracle: \"Choose target creature or
        planeswalker.  You get {E}{E}{E}.  Galvanic Discharge deals
        X damage to that permanent...\"  Required choice → must
        have a legal target at announcement."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=2)
        spell = _hand(game, card_db, "Galvanic Discharge", 0)
        assert game.can_cast(0, spell) is False, (
            f"Galvanic Discharge was reported castable with no "
            f"creature or planeswalker on any battlefield.  Oracle "
            f"requires a chosen target."
        )

    def test_galvanic_discharge_castable_with_creature(self, card_db):
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=2)
        _battlefield(game, card_db, "Memnite", 1)
        spell = _hand(game, card_db, "Galvanic Discharge", 0)
        assert game.can_cast(0, spell) is True


class TestUpToTargetIsAlwaysCastable:
    """`up to one target X` and `up to N target Xs` are optional
    targets per CR 114.4 — the spell is castable with zero targets
    chosen.  Solitude is a critical regression case: the entire
    Boros Energy / Azorius / Jeskai sideboard plan relies on
    Solitude evoke being usable as 'pitch a white card for a
    free 4/3 lifelinker' even when no creature is on board."""

    def test_solitude_castable_with_no_target_via_up_to(self, card_db):
        """Solitude oracle: \"When this creature enters, exile up to
        one other target creature.\"  ETB trigger is optional; the
        spell itself is always castable as a creature."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=6)
        spell = _hand(game, card_db, "Solitude", 0)
        assert game.can_cast(0, spell) is True, (
            f"Regression: Solitude NOT castable with empty "
            f"battlefield.  Solitude is a creature spell with an "
            f"OPTIONAL ETB target (\"up to one other target\")."
        )


class TestLightningBoltAnyTargetIsAlwaysCastable:
    """`any target` (Lightning Bolt, Galvanic Blast, etc.) accepts
    a player as a target.  Players are always present.  Always
    castable — no cast-time target check fires."""

    def test_lightning_bolt_castable_on_empty_board(self, card_db):
        """Regression: Lightning Bolt must remain castable on a
        completely empty board — players are valid targets."""
        game = GameState(rng=random.Random(0))
        _setup_main_phase(game)
        _give_lands_for(game, card_db, n=1)
        spell = _hand(game, card_db, "Lightning Bolt", 0)
        assert game.can_cast(0, spell) is True, (
            f"Regression: Lightning Bolt NOT castable on empty "
            f"board.  \"Any target\" includes players."
        )
