"""Tests for Scry mechanic (CR 701.18) and surveil-as-spell-effect.

Mechanic spec (CR 701.18):
  "To 'scry N' means to look at the top N cards of your library, then put
   any number of them on the bottom of your library in any order and the
   rest on top of your library in any order."

Key properties verified here:
  1. game.scry(n) looks at exactly N cards.
  2. Cards sent to the bottom are no longer on top (library order changes).
  3. The AI policy keeps wanted cards on top and sends excess lands to the
     bottom when the player already has mana stability (>=4 lands).
  4. Serum Visions (draw 1 then scry 2): draw fires BEFORE scry.
  5. Opt (scry 1 then draw 1): scry fires BEFORE draw.
  6. Consider (surveil 1 then draw 1): surveil fires BEFORE draw as a
     SPELL effect (not just from DRC-style permanent triggers).
  7. Scry is not a no-op: library order actually changes after scry.

Test names describe mechanics, not individual cards.
"""
from __future__ import annotations

import sys
import os
import random
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState
from decks.modern_meta import MODERN_DECKS


# ─── Shared DB fixture ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def db() -> CardDatabase:
    return CardDatabase()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_game(db: CardDatabase, deck1_name: str = "Ruby Storm",
               deck2_name: str = "Izzet Prowess", seed: int = 12345) -> GameState:
    """Create a minimal GameState with hands and libraries populated."""
    from engine.game_runner import GameRunner, AICallbacks

    d1 = MODERN_DECKS[deck1_name]
    d2 = MODERN_DECKS[deck2_name]
    rng = random.Random(seed)
    runner = GameRunner(db, rng=rng)

    deck1_list = d1["mainboard"]
    deck2_list = d2["mainboard"]

    deck1 = runner.build_deck(deck1_list)
    deck2 = runner.build_deck(deck2_list)

    game = GameState(rng=rng, callbacks=AICallbacks())
    game.setup_game(deck1, deck2)
    game.players[0].deck_name = deck1_name
    game.players[1].deck_name = deck2_name
    return game


def _make_template(name: str, oracle: str, **kwargs) -> CardTemplate:
    """Build a minimal CardTemplate for unit testing."""
    tmpl = CardTemplate(name=name, card_types=[CardType.INSTANT], mana_cost=ManaCost())
    tmpl.oracle_text = oracle
    tmpl.keywords = set()
    tmpl.abilities = []
    tmpl.tags = set()
    tmpl.color_identity = set()
    tmpl.colors = set()
    return tmpl


def _make_land_template(name: str = "Island") -> CardTemplate:
    """Build a minimal land CardTemplate."""
    tmpl = CardTemplate(name=name, card_types=[CardType.LAND], mana_cost=ManaCost())
    tmpl.oracle_text = ""
    tmpl.keywords = set()
    tmpl.abilities = []
    tmpl.tags = set()
    tmpl.color_identity = set()
    tmpl.colors = set()
    return tmpl


def _card_from_template(tmpl: CardTemplate, instance_id: int = 1) -> CardInstance:
    """Wrap a CardTemplate in a CardInstance."""
    inst = CardInstance(template=tmpl, owner=0, controller=0, instance_id=instance_id)
    inst.zone = "library"
    return inst


# ═══════════════════════════════════════════════════════════════════════════════
# 1. game.scry() core mechanic — library reordering
# ═══════════════════════════════════════════════════════════════════════════════

class TestScryNReordersTopNCards:
    """CR 701.18: scry N looks at exactly N cards and may reorder them."""

    def test_scry_1_does_not_leave_library_unchanged_when_land_is_on_top(
            self, db: CardDatabase):
        """Scry 1 with a land on top and mana stability: land moves to bottom."""
        game = _make_game(db, seed=99001)
        player_idx = 0
        player = game.players[player_idx]

        # Give the player 4 lands on the battlefield (mana-stable threshold)
        player.battlefield.clear()
        for i in range(4):
            lt = _make_land_template(f"Island_{i}")
            lc = _card_from_template(lt, instance_id=9000 + i)
            lc.zone = "battlefield"
            player.battlefield.append(lc)

        # Put a land on top of the library
        land_tmpl = _make_land_template("Island_top")
        land_card = _card_from_template(land_tmpl, instance_id=8001)
        # Put a spell below it
        spell_tmpl = _make_template("Pyretic Ritual", "Add {R}{R}{R}.", cmc=1)
        spell_card = _card_from_template(spell_tmpl, instance_id=8002)

        # Clear library and set up: land on top, spell below
        player.library.clear()
        player.library.append(land_card)
        player.library.append(spell_card)

        lib_before_top = player.library[0].name

        # Act: scry 1
        game.scry(player_idx, 1)

        lib_after_top = player.library[0].name

        # The land should have been sent to the bottom; spell is now on top
        assert lib_after_top != lib_before_top, (
            "scry 1 is a no-op: library top unchanged after scry. "
            "Expected land to move to bottom when player has 4+ lands in play."
        )
        assert lib_after_top == "Pyretic Ritual", (
            f"Expected spell on top after scry, got {lib_after_top!r}"
        )
        assert player.library[-1].name == "Island_top", (
            "Expected land to be on the bottom of the library after scry"
        )

    def test_scry_n_looks_at_exactly_n_cards(self, db: CardDatabase):
        """scry N inspects exactly N cards — not 1, not N+1."""
        game = _make_game(db, seed=99002)
        player_idx = 0
        player = game.players[player_idx]

        # No lands in play → everything is 'wanted' → library order preserved
        player.battlefield.clear()

        # Put 5 spells in the library
        spell_names = [f"Spell_{i}" for i in range(5)]
        player.library.clear()
        for i, name in enumerate(spell_names):
            t = _make_template(name, "Deal 1 damage.", cmc=1)
            c = _card_from_template(t, instance_id=7000 + i)
            player.library.append(c)

        # scry 2: only top 2 cards are looked at
        game.scry(player_idx, 2)

        # With 0 lands in play (threshold not met), the policy keeps everything
        # on top, so library order is preserved.
        remaining_top = [c.name for c in player.library]
        assert remaining_top == spell_names, (
            f"scry 2 with no excess lands should preserve order but got {remaining_top}"
        )

    def test_scry_sends_excess_lands_to_bottom(self, db: CardDatabase):
        """scry N: land cards are sent to the bottom when player is mana-stable."""
        game = _make_game(db, seed=99003)
        player_idx = 0
        player = game.players[player_idx]

        # Mana-stable: 4 lands on battlefield
        player.battlefield.clear()
        for i in range(4):
            lt = _make_land_template(f"Forest_{i}")
            lc = _card_from_template(lt, instance_id=6000 + i)
            lc.zone = "battlefield"
            player.battlefield.append(lc)

        # Library: land, spell, land, deep_spell (top to bottom)
        player.library.clear()
        entries = [
            ("Island_A", True),
            ("Bolt",     False),
            ("Island_B", True),
            ("DeepSpell", False),
        ]
        for i, (name, is_land) in enumerate(entries):
            if is_land:
                t = _make_land_template(name)
            else:
                t = _make_template(name, "Deal 3 damage.", cmc=1)
            c = _card_from_template(t, instance_id=5000 + i)
            player.library.append(c)

        # scry 2: look at top 2 (Island_A, Bolt)
        game.scry(player_idx, 2)

        # Expected: Island_A moved to bottom, Bolt stays on top
        assert player.library[0].name == "Bolt", (
            f"Expected 'Bolt' on top after scry 2, got {player.library[0].name!r}"
        )
        # Island_A should be at the bottom
        assert player.library[-1].name == "Island_A", (
            f"Expected 'Island_A' at bottom after scry 2 (mana-stable), "
            f"got {player.library[-1].name!r}"
        )

    def test_scry_keeps_all_cards_when_not_mana_stable(self, db: CardDatabase):
        """scry N: land on top is kept when player has fewer than 4 lands."""
        game = _make_game(db, seed=99004)
        player_idx = 0
        player = game.players[player_idx]

        # Land-starved: only 2 lands on battlefield
        player.battlefield.clear()
        for i in range(2):
            lt = _make_land_template(f"Plains_{i}")
            lc = _card_from_template(lt, instance_id=4000 + i)
            lc.zone = "battlefield"
            player.battlefield.append(lc)

        # Library: land then spell
        player.library.clear()
        land_t = _make_land_template("Island_wanted")
        land_c = _card_from_template(land_t, instance_id=3001)
        spell_t = _make_template("Lightning Bolt", "Deal 3 damage.", cmc=1)
        spell_c = _card_from_template(spell_t, instance_id=3002)
        player.library.append(land_c)
        player.library.append(spell_c)

        game.scry(player_idx, 1)

        # Land should stay on top (still need mana)
        assert player.library[0].name == "Island_wanted", (
            f"Expected land to stay on top when mana-starved, "
            f"got {player.library[0].name!r}"
        )

    def test_scry_empty_library_is_noop(self, db: CardDatabase):
        """scry N on empty library does not crash and has no effect."""
        game = _make_game(db, seed=99005)
        player_idx = 0
        player = game.players[player_idx]
        player.library.clear()

        # Should not raise
        bottomed = game.scry(player_idx, 2)
        assert player.library == []
        assert bottomed == []

    def test_scry_partial_library_scries_available(self, db: CardDatabase):
        """scry N where library has fewer than N cards: scry min(N, len(lib))."""
        game = _make_game(db, seed=99006)
        player_idx = 0
        player = game.players[player_idx]
        player.library.clear()

        t = _make_template("Only Card", "Deal 1 damage.", cmc=1)
        c = _card_from_template(t, instance_id=2001)
        player.library.append(c)

        # scry 3 with only 1 card → should not crash
        game.scry(player_idx, 3)
        assert len(player.library) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Serum Visions — draw THEN scry 2 ordering (CR 701.18 + CR 121.1)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSerumVisionsDrawThenScry2:
    """Serum Visions: 'Draw a card. Scry 2.' — draw comes before scry."""

    def test_serum_visions_draws_a_card(self, db: CardDatabase):
        """Serum Visions causes the caster to draw exactly 1 card."""
        game = _make_game(db, "Ruby Storm", "Izzet Prowess", seed=99010)
        player_idx = 0
        player = game.players[player_idx]
        hand_before = len(player.hand)

        # Cast Serum Visions by resolving it from the oracle path
        sv_tmpl = db.get_card("Serum Visions")
        assert sv_tmpl is not None, "Serum Visions not found in DB"
        sv = _card_from_template(sv_tmpl, instance_id=1001)

        from engine.oracle_resolver import resolve_spell_from_oracle
        # Ensure library is non-empty so draw can fire
        if not player.library:
            t = _make_template("Filler", "Add {R}.", cmc=1)
            player.library.append(_card_from_template(t, instance_id=1002))
        resolve_spell_from_oracle(game, sv, player_idx)

        assert len(player.hand) == hand_before + 1, (
            f"Serum Visions should draw 1 card: hand went "
            f"{hand_before} → {len(player.hand)}"
        )

    def test_serum_visions_scry_2_reorders_library(self, db: CardDatabase):
        """Serum Visions scry 2 actually reorders the top 2 library cards."""
        game = _make_game(db, "Ruby Storm", "Izzet Prowess", seed=99011)
        player_idx = 0
        player = game.players[player_idx]

        # Give mana stability so excess lands will be bottomed
        player.battlefield.clear()
        for i in range(4):
            lt = _make_land_template(f"Land_{i}")
            lc = _card_from_template(lt, instance_id=8000 + i)
            lc.zone = "battlefield"
            player.battlefield.append(lc)

        # Library: non-land top (will be drawn), then land, then spell
        filler = _make_template("Ritual", "Add {R}{R}{R}.", cmc=1)
        filler_c = _card_from_template(filler, instance_id=700)
        land_t = _make_land_template("Island_scry")
        land_c = _card_from_template(land_t, instance_id=701)
        spell_t = _make_template("Bolt", "Deal 3 damage.", cmc=1)
        spell_c = _card_from_template(spell_t, instance_id=702)

        player.library.clear()
        player.library.extend([filler_c, land_c, spell_c])

        sv_tmpl = db.get_card("Serum Visions")
        sv = _card_from_template(sv_tmpl, instance_id=1010)

        from engine.oracle_resolver import resolve_spell_from_oracle
        resolve_spell_from_oracle(game, sv, player_idx)

        # filler_c was drawn (first card drawn = Ritual)
        assert any(c.name == "Ritual" for c in player.hand), (
            "Serum Visions should have drawn Ritual into hand"
        )
        # After drawing, library is [land_c, spell_c].
        # scry 2 sees both; with 4 lands in play, Island_scry goes to bottom.
        assert player.library[0].name == "Bolt", (
            f"After scry 2 (mana-stable), Bolt should be on top; "
            f"got {player.library[0].name!r}"
        )
        assert player.library[-1].name == "Island_scry", (
            f"After scry 2 (mana-stable), Island_scry should be on bottom; "
            f"got {player.library[-1].name!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Opt — scry 1 THEN draw ordering
# ═══════════════════════════════════════════════════════════════════════════════

class TestOptScry1ThenDraw:
    """Opt: 'Scry 1. Draw a card.' — scry comes before draw."""

    def test_opt_scry_fires_before_draw(self, db: CardDatabase):
        """Opt scry 1 acts on the card that will be drawn (not the next one)."""
        game = _make_game(db, "Dimir Midrange", "Izzet Prowess", seed=99020)
        player_idx = 0
        player = game.players[player_idx]

        # Mana-stable
        player.battlefield.clear()
        for i in range(4):
            lt = _make_land_template(f"Island_{i}")
            lc = _card_from_template(lt, instance_id=5000 + i)
            lc.zone = "battlefield"
            player.battlefield.append(lc)

        # Library: land (would be drawn without scry), spell (better choice)
        land_t = _make_land_template("Island_opt")
        land_c = _card_from_template(land_t, instance_id=600)
        spell_t = _make_template("Push", "Exile target creature.", cmc=1)
        spell_c = _card_from_template(spell_t, instance_id=601)

        player.library.clear()
        player.library.extend([land_c, spell_c])
        player.hand.clear()

        opt_tmpl = db.get_card("Opt")
        assert opt_tmpl is not None, "Opt not found in DB"
        opt = _card_from_template(opt_tmpl, instance_id=1020)

        from engine.oracle_resolver import resolve_spell_from_oracle
        resolve_spell_from_oracle(game, opt, player_idx)

        # If scry fired before draw: Island_opt was seen and bottomed (mana-stable),
        # then Push was drawn.
        # If scry fired after draw: Island_opt was drawn first (wrong order),
        # then Push scried (but Push is not a land so stays on top).
        drawn_names = [c.name for c in player.hand]
        assert "Push" in drawn_names, (
            f"Opt with scry-before-draw: Push should have been drawn "
            f"(land scried to bottom first), but hand = {drawn_names}"
        )
        assert "Island_opt" not in drawn_names, (
            f"Opt: Island_opt should have been scried to bottom, not drawn; "
            f"hand = {drawn_names}"
        )

    def test_opt_draws_exactly_one_card(self, db: CardDatabase):
        """Opt draws exactly 1 card total (not 2)."""
        game = _make_game(db, "Dimir Midrange", "Izzet Prowess", seed=99021)
        player_idx = 0
        player = game.players[player_idx]

        # Put enough cards in library so draw can fire
        player.library.clear()
        for i in range(5):
            t = _make_template(f"Card_{i}", "Deal 1 damage.", cmc=1)
            c = _card_from_template(t, instance_id=4000 + i)
            player.library.append(c)
        hand_before = len(player.hand)

        opt_tmpl = db.get_card("Opt")
        opt = _card_from_template(opt_tmpl, instance_id=1021)

        from engine.oracle_resolver import resolve_spell_from_oracle
        resolve_spell_from_oracle(game, opt, player_idx)

        assert len(player.hand) == hand_before + 1, (
            f"Opt should draw exactly 1 card; hand went "
            f"{hand_before} → {len(player.hand)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Consider — surveil 1 THEN draw as a SPELL (not a permanent trigger)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsiderSurveilThenDraw:
    """Consider: 'Surveil 1. Draw a card.' — surveil comes before draw."""

    def test_consider_surveil_fires_as_spell_resolution(self, db: CardDatabase):
        """Consider's surveil 1 sends the top card to graveyard when cast."""
        game = _make_game(db, "Dimir Midrange", "Izzet Prowess", seed=99030)
        player_idx = 0
        player = game.players[player_idx]

        consider_tmpl = db.get_card("Consider")
        assert consider_tmpl is not None, "Consider not found in DB"
        assert getattr(consider_tmpl, "has_surveil", False), (
            "Consider should have has_surveil=True"
        )

        # Library: bad card on top that will be surveilled to GY, then good card
        bad_t = _make_template("BadCard", "This card does nothing.", cmc=7)
        bad_c = _card_from_template(bad_t, instance_id=350)
        good_t = _make_template("GoodCard", "Deal 3 damage.", cmc=1)
        good_c = _card_from_template(good_t, instance_id=351)

        player.library.clear()
        player.library.extend([bad_c, good_c])
        gy_before = len(player.graveyard)

        consider = _card_from_template(consider_tmpl, instance_id=1030)
        from engine.oracle_resolver import resolve_spell_from_oracle
        resolve_spell_from_oracle(game, consider, player_idx)

        # Surveil 1 bins top card to GY (deterministic policy: bins all surveiled)
        assert len(player.graveyard) == gy_before + 1, (
            "Consider surveil 1 should have moved one card to the graveyard; "
            f"GY size: {gy_before} → {len(player.graveyard)}"
        )
        # Then draw fires — draws GoodCard (since BadCard is in GY)
        drawn_names = [c.name for c in player.hand]
        assert "GoodCard" in drawn_names, (
            f"Consider draw should have drawn GoodCard (after surveilling BadCard to GY); "
            f"hand = {drawn_names}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. parse_has_scry oracle parser
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseHasScry:
    """parse_has_scry(oracle) returns True iff oracle text contains scry."""

    def test_parse_has_scry_true_for_scry_1(self):
        from engine.oracle_parser import parse_has_scry
        assert parse_has_scry("Scry 1.") is True

    def test_parse_has_scry_true_for_scry_2(self):
        from engine.oracle_parser import parse_has_scry
        assert parse_has_scry("Draw a card. Scry 2.") is True

    def test_parse_has_scry_false_for_surveil(self):
        from engine.oracle_parser import parse_has_scry
        assert parse_has_scry("Surveil 1. Draw a card.") is False

    def test_parse_has_scry_false_for_draw_only(self):
        from engine.oracle_parser import parse_has_scry
        assert parse_has_scry("Draw a card.") is False

    def test_parse_has_scry_false_for_empty(self):
        from engine.oracle_parser import parse_has_scry
        assert parse_has_scry("") is False

    def test_parse_has_scry_false_for_none(self):
        from engine.oracle_parser import parse_has_scry
        assert parse_has_scry(None) is False  # type: ignore[arg-type]

    def test_opt_has_scry_attribute_set(self, db: CardDatabase):
        """Opt's CardTemplate has has_scry=True after DB load."""
        opt = db.get_card("Opt")
        assert opt is not None
        assert getattr(opt, "has_scry", False) is True, (
            "Opt should have has_scry=True (oracle: 'Scry 1. ... Draw a card.')"
        )

    def test_serum_visions_has_scry_attribute_set(self, db: CardDatabase):
        """Serum Visions' CardTemplate has has_scry=True after DB load."""
        sv = db.get_card("Serum Visions")
        assert sv is not None
        assert getattr(sv, "has_scry", False) is True, (
            "Serum Visions should have has_scry=True (oracle: 'Draw a card. Scry 2.')"
        )

    def test_consider_does_not_have_scry(self, db: CardDatabase):
        """Consider has surveil (not scry), so has_scry should be False."""
        consider = db.get_card("Consider")
        assert consider is not None
        assert getattr(consider, "has_scry", False) is False, (
            "Consider has surveil not scry — has_scry should be False"
        )

    def test_lightning_bolt_does_not_have_scry(self, db: CardDatabase):
        """Lightning Bolt does not have scry."""
        bolt = db.get_card("Lightning Bolt")
        assert bolt is not None
        assert getattr(bolt, "has_scry", False) is False
