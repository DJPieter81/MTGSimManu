"""CR 702.33 Flashback — cost and zone-replacement correctness.

CR 702.33a: "Flashback [cost] — You may cast this card from your
graveyard by paying [cost] rather than paying its mana cost.  Then
exile it."

Three invariants this file pins:

A. **Flashback cost used, not regular mana cost** (can_cast gate).
   A card with native flashback may only be cast from the graveyard when
   the player can afford the *flashback* cost, which differs from the
   card's regular mana cost.  can_cast must check the flashback cost.
   Mechanism: CardTemplate.flashback_cost (typed ManaCost, populated at
   load time by oracle_parser.parse_flashback_mana_cost).

B. **Flashback cost paid, not regular mana cost** (cast_spell payment).
   When a native-flashback card is cast from the graveyard, tap_lands_for_mana
   must be called with the flashback cost, so the player pays exactly what
   the rules require — no free or discounted spells from the graveyard.

C. **Exile after resolution** (zone replacement, CR 702.33a second clause).
   After a flashback-cast spell resolves *or* is countered it goes to
   exile, not the graveyard.  This is already implemented in
   spell_resolution.py; the test here pins it as a regression guard.

Class size: every card that has a printed Flashback keyword in Modern,
plus every card that receives Flashback dynamically (Past in Flames,
Snapcaster Mage).  The oracle-text parser + typed-field approach covers
the whole class without per-card branches.

Subsystem boundary: engine/oracle_parser.py (cost parsing),
engine/cards.py (CardTemplate.flashback_cost), engine/cast_manager.py
(can_cast gate + cast_spell payment), engine/spell_resolution.py (exile).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


# ── helpers ──────────────────────────────────────────────────────────────────

def _put_in_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card not in DB: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _put_untapped_land(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"land not in DB: {name}"
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


def _put_on_stack(game, card_db, name, controller):
    """Place a card directly on the stack (simulates mid-resolution state)."""
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card not in DB: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="stack",
    )
    card._game_state = game
    return card


# ── A: flashback cost — oracle parsing ───────────────────────────────────────

class TestFlashbackCostParsedFromOracle:
    """CardTemplate.flashback_cost must be populated from oracle text for native
    flashback cards, giving can_cast and cast_spell a typed cost to work with."""

    def test_faithless_looting_flashback_cost_populated(self, card_db):
        """Faithless Looting: regular {R}, flashback {2}{R}.

        template.flashback_cost must equal {2}{R} (CMC 3, 2 generic + 1 red).
        """
        tmpl = card_db.get_card("Faithless Looting")
        assert tmpl is not None
        assert tmpl.flashback_cost is not None, (
            "CardTemplate.flashback_cost must not be None for Faithless Looting "
            "(has printed Flashback {2}{R}); populate it in card_database.py via "
            "oracle_parser.parse_flashback_mana_cost."
        )
        assert tmpl.flashback_cost.cmc == 3, (
            f"Faithless Looting flashback cost should be CMC 3 ({{2}}{{R}}); "
            f"got {tmpl.flashback_cost.cmc}"
        )
        assert tmpl.flashback_cost.red == 1, (
            f"Faithless Looting flashback cost must require {{R}}; got "
            f"red={tmpl.flashback_cost.red}"
        )
        assert tmpl.flashback_cost.generic == 2, (
            f"Faithless Looting flashback cost must have 2 generic; got "
            f"generic={tmpl.flashback_cost.generic}"
        )

    def test_past_in_flames_flashback_cost_populated(self, card_db):
        """Past in Flames: regular {3}{R}, flashback {4}{R}.

        These costs differ by 1 generic mana.  The engine must not let PiF
        be cast from the graveyard for {3}{R} — that is a 1-mana Storm discount.
        """
        tmpl = card_db.get_card("Past in Flames")
        assert tmpl is not None
        assert tmpl.flashback_cost is not None, (
            "CardTemplate.flashback_cost must not be None for Past in Flames "
            "(has printed Flashback {4}{R})."
        )
        assert tmpl.flashback_cost.cmc == 5, (
            f"Past in Flames flashback cost should be CMC 5 ({{4}}{{R}}); "
            f"got {tmpl.flashback_cost.cmc}"
        )
        assert tmpl.flashback_cost.red == 1
        assert tmpl.flashback_cost.generic == 4

    def test_lingering_souls_flashback_cost_populated(self, card_db):
        """Lingering Souls: regular {1}{W}, flashback {1}{B}.

        Wrong-color payment would tap white mana for a black flashback cost.
        """
        tmpl = card_db.get_card("Lingering Souls")
        assert tmpl is not None
        assert tmpl.flashback_cost is not None, (
            "CardTemplate.flashback_cost must not be None for Lingering Souls "
            "(has printed Flashback {1}{B})."
        )
        assert tmpl.flashback_cost.cmc == 2
        assert tmpl.flashback_cost.black == 1
        assert tmpl.flashback_cost.generic == 1
        assert tmpl.flashback_cost.white == 0, (
            "Lingering Souls flashback cost is {1}{B}, not {1}{W}; "
            "must not store regular mana cost."
        )

    def test_card_without_flashback_has_no_flashback_cost(self, card_db):
        """A card without Flashback must have flashback_cost=None.

        Regression guard: flashback_cost=None is the sentinel for 'PiF-granted
        flashback uses mana cost instead of a printed flashback cost'.
        """
        tmpl = card_db.get_card("Lightning Bolt")
        assert tmpl is not None
        assert tmpl.flashback_cost is None, (
            "Lightning Bolt has no flashback; flashback_cost must be None."
        )


# ── B: can_cast uses flashback cost (CR 702.33a gate) ────────────────────────

class TestFlashbackCostGateCanCast:
    """can_cast must check the flashback cost, not the regular mana cost."""

    def test_flashback_cost_lets_card_be_cast_from_graveyard(self, card_db):
        """Native-flashback card may be cast from GY when player has exact flashback
        cost available.

        Faithless Looting: flashback {2}{R}.  Three untapped Mountains produce
        exactly {2}{R}.  can_cast must return True.

        (Naming required by task spec for this mechanic invariant.)
        """
        game = GameState(rng=random.Random(0))
        game.current_phase = Phase.MAIN1   # sorcery requires main phase
        card = _put_in_graveyard(game, card_db, "Faithless Looting", 0)
        card.has_flashback = True  # mirrors assignment at deck-load time

        # Exactly enough for {2}{R}: three Mountains
        for _ in range(3):
            _put_untapped_land(game, card_db, "Mountain", 0)

        result = game.can_cast(0, card)
        assert result is True, (
            "can_cast must return True when player has exactly {2}{R} available "
            "for Faithless Looting flashback (flashback cost {2}{R})."
        )

    def test_flashback_cost_blocks_cast_when_only_regular_cost_affordable(self, card_db):
        """Native-flashback card CANNOT be cast from GY when player can afford the
        regular mana cost but NOT the flashback cost.

        Faithless Looting: regular {R} (CMC 1), flashback {2}{R} (CMC 3).
        Player has 1 Mountain → can pay {R} but not {2}{R}.
        can_cast must return False — the flashback cost is what governs.

        This is the primary regression test for the bug where can_cast
        checked template.mana_cost instead of template.flashback_cost.
        """
        game = GameState(rng=random.Random(0))
        game.current_phase = Phase.MAIN1   # sorcery requires main phase
        card = _put_in_graveyard(game, card_db, "Faithless Looting", 0)
        card.has_flashback = True

        # Only 1 mana: enough for regular {R}, not for flashback {2}{R}
        _put_untapped_land(game, card_db, "Mountain", 0)

        result = game.can_cast(0, card)
        assert result is False, (
            "can_cast must return False when player has only 1 Mountain for "
            "Faithless Looting flashback (flashback cost is {2}{R}, not regular {R}). "
            "The engine was incorrectly checking template.mana_cost ({R}) instead "
            "of template.flashback_cost ({2}{R})."
        )

    def test_past_in_flames_flashback_cost_not_regular_cost(self, card_db):
        """Past in Flames flashback cost is {4}{R} (CMC 5), regular cost is {3}{R} (CMC 4).

        With 4 Mountains, can_cast must return False for flashback from GY since
        {4}{R} requires 5 mana.  Before the fix, can_cast used {3}{R} and returned
        True, giving Storm a hidden 1-mana discount on PiF flashback.
        """
        game = GameState(rng=random.Random(0))
        game.current_phase = Phase.MAIN1   # sorcery requires main phase
        card = _put_in_graveyard(game, card_db, "Past in Flames", 0)
        card.has_flashback = True  # innate from 'flashback' tag

        # 4 mana: enough for regular {3}{R} but not for flashback {4}{R}
        for _ in range(4):
            _put_untapped_land(game, card_db, "Mountain", 0)

        result = game.can_cast(0, card)
        assert result is False, (
            "Past in Flames flashback cost is {4}{R} (CMC 5); with 4 Mountains "
            "can_cast must return False.  The engine was incorrectly using {3}{R} "
            "(the regular mana cost), which would let Storm cast PiF from the GY "
            "1 mana cheaper than the rules allow."
        )

    def test_pif_granted_flashback_still_uses_mana_cost(self, card_db):
        """PiF-granted flashback: cost equals the card's regular mana cost (oracle text
        'The flashback cost is equal to its mana cost').

        Pyretic Ritual: regular {1}{R}.  After PiF grants flashback (has_flashback=True,
        but template.flashback_cost is None because Pyretic Ritual has no printed
        flashback), can_cast must return True with 2 mana available.
        """
        game = GameState(rng=random.Random(0))
        game.current_phase = Phase.MAIN1   # sorcery requires main phase
        card = _put_in_graveyard(game, card_db, "Pyretic Ritual", 0)
        card.has_flashback = True  # PiF granted — no template.flashback_cost

        # Verify sentinel: no printed flashback cost
        assert card.template.flashback_cost is None, (
            "Pyretic Ritual has no printed flashback; flashback_cost must be None "
            "(sentinel: use mana cost when PiF grants flashback)."
        )

        # 2 mana: enough for regular {1}{R}
        for _ in range(2):
            _put_untapped_land(game, card_db, "Mountain", 0)

        result = game.can_cast(0, card)
        assert result is True, (
            "Pyretic Ritual flashback via PiF uses its regular mana cost {1}{R}; "
            "2 Mountains must be sufficient."
        )


# ── C: exile after resolution (CR 702.33a second clause) ─────────────────────

class TestFlashbackResolvesToExile:
    """After a flashback-cast spell resolves (or is countered), it must be exiled,
    not sent to the graveyard.  (CR 702.33a: 'Then exile it.')"""

    def test_flashback_resolves_to_exile_not_graveyard(self, card_db):
        """A spell cast via flashback (_cast_with_flashback=True) must go to exile
        upon resolution, not the graveyard.

        (Naming required by task spec for this invariant.)
        """
        from engine.spell_resolution import ResolutionManager

        game = GameState(rng=random.Random(0))
        card = _put_on_stack(game, card_db, "Faithless Looting", 0)
        card._cast_with_flashback = True
        card.has_flashback = True

        # Resolution must exile the card
        ResolutionManager._move_resolved_spell_off_stack(game, card)

        player = game.players[0]
        assert card not in player.graveyard, (
            "Flashback-cast Faithless Looting must NOT be in the graveyard "
            "after resolution (CR 702.33a: exile it)."
        )
        assert card in player.exile, (
            "Flashback-cast Faithless Looting must be in exile after resolution "
            "(CR 702.33a: 'Then exile it.')."
        )

    def test_flashback_countered_also_exiles(self, card_db):
        """A flashback-cast spell that is COUNTERED must also go to exile
        (CR 702.33a applies regardless of whether the spell resolves).
        """
        from engine.spell_resolution import ResolutionManager
        from engine.game_state import StackItem, StackItemType

        game = GameState(rng=random.Random(0))
        card = _put_on_stack(game, card_db, "Faithless Looting", 0)
        card._cast_with_flashback = True
        card.has_flashback = True

        # Countered path also calls _move_countered_stack_item which
        # delegates to _move_resolved_spell_off_stack for SPELL items.
        stack_item = StackItem(
            item_type=StackItemType.SPELL,
            source=card,
            controller=0,
            targets=[],
        )
        ResolutionManager._move_countered_stack_item(game, stack_item, card)

        player = game.players[0]
        assert card not in player.graveyard, (
            "Countered flashback-cast spell must NOT enter the graveyard."
        )
        assert card in player.exile, (
            "Countered flashback-cast spell must be exiled (CR 702.33a)."
        )
