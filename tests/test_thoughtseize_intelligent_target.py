"""Engine bug E2 — Thoughtseize/Duress/Inquisition must pick the
scariest card to strip, not the highest-CMC one.

Prior behaviour: `_force_discard(self_discard=False)` sorted by
`template.cmc` descending and took [0]. Against Affinity that strips
Sojourner's Companion (printed CMC 7, effective 2 with affinity cost
reduction) while leaving the real payoffs — Cranial Plating (the pump
engine) and Mox Opal (the ramp enabler) — in hand. Those cards are
what carry the deck; Sojourner's is a replaceable cantrip-body.

Fix: pick by threat-to-the-caster, derived from:
  * creature_threat_value() for creatures (oracle-driven, already in ai/)
  * gameplan critical_pieces / always_early / mulligan_keys for
    non-creatures (opponent's deck knowledge, not card names in code)
  * tag-based fallback (combo / payoff / mana_source / cost_reducer)

No hardcoded card names in the fix. The scoring primitives must work
against any opponent deck — these tests just pin the three cases
described in the E2 report.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.game_runner import AICallbacks


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _make_game():
    """Construct a GameState wired to AICallbacks so the discard path
    routes through ai.discard_advisor (and thus the E2 threat-scoring
    helper). The default callbacks would still use a plain CMC sort —
    the refactor (PR #141) moved discard scoring from engine to the
    callback; we must opt in to the AI implementation here.
    """
    return GameState(rng=random.Random(0), callbacks=AICallbacks())


def _make_card(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    card._game_state = game
    return card


def _build_hand(game, card_db, player_idx, card_names, deck_name):
    """Stock a player's hand with the named cards and set the deck_name
    so the engine can consult that deck's gameplan.
    """
    player = game.players[player_idx]
    player.deck_name = deck_name
    player.hand = [_make_card(game, card_db, n, player_idx) for n in card_names]
    return player.hand


class TestThoughtseizeIntelligentTarget:
    """Bug E2 — `_force_discard` must evaluate threat, not CMC."""

    def test_affinity_strips_payoff_or_enabler_not_sojourners(self, card_db):
        """Against Affinity, Thoughtseize must pick Cranial Plating
        (payoff) or Mox Opal (ramp enabler) — not Sojourner's Companion
        (printed CMC 7 but effective CMC ~2 with affinity cost reduction,
        easily replaced)."""
        game = _make_game()
        hand = _build_hand(
            game, card_db, player_idx=0,
            card_names=[
                "Sojourner's Companion",  # printed CMC 7 — the RED HERRING
                "Cranial Plating",        # real payoff
                "Mox Opal",               # ramp enabler
                "Arid Mesa",              # land (filtered out)
            ],
            deck_name="Affinity",
        )
        assert len(hand) == 4

        game._force_discard(0, 1)

        discarded = game.players[0].graveyard
        assert len(discarded) == 1, f"expected 1 discard, got {[c.name for c in discarded]}"
        picked = discarded[0].name

        # Must not strip the land, and must not strip the CMC-7 decoy.
        assert picked != "Arid Mesa", (
            f"Thoughtseize landed on a land (should never happen): {picked}"
        )
        assert picked != "Sojourner's Companion", (
            "Thoughtseize picked the highest-CMC card (Sojourner's Companion) "
            "instead of a real threat. E2 regression: scoring fell back to "
            "raw printed CMC."
        )
        assert picked in ("Cranial Plating", "Mox Opal"), (
            f"Thoughtseize picked {picked!r}; expected Cranial Plating or "
            f"Mox Opal (the genuine Affinity payoff/enabler)."
        )

    def test_storm_strips_payoff_or_engine_not_a_generic_ritual(self, card_db):
        """Against Ruby Storm, Thoughtseize must pick Grapeshot (finisher)
        or Ruby Medallion (cost-reducer engine). Desperate Ritual is a
        4-of ritual — one copy is replaceable; the finisher and the
        medallion are the keystones."""
        game = _make_game()
        hand = _build_hand(
            game, card_db, player_idx=0,
            card_names=[
                "Grapeshot",         # finisher — critical_piece
                "Desperate Ritual",  # replaceable ritual
                "Ruby Medallion",    # cost-reducer engine — always_early
                "Mountain",          # land (filtered out)
            ],
            deck_name="Ruby Storm",
        )
        assert len(hand) == 4

        game._force_discard(0, 1)

        discarded = game.players[0].graveyard
        assert len(discarded) == 1
        picked = discarded[0].name

        assert picked != "Mountain", (
            f"Thoughtseize discarded a land: {picked}"
        )
        assert picked != "Desperate Ritual", (
            "Thoughtseize picked a replaceable ritual over the finisher / "
            "engine. E2 regression: threat scoring ignored gameplan roles."
        )
        assert picked in ("Grapeshot", "Ruby Medallion"), (
            f"Thoughtseize picked {picked!r}; expected Grapeshot (finisher) "
            f"or Ruby Medallion (engine) against Ruby Storm."
        )

    def test_lands_plus_one_spell_still_picks_the_spell(self, card_db):
        """Regression: hand of all lands + exactly one non-land. Even when
        every non-land card scores zero on threat heuristics, the picker
        must land on the non-land (never a land, and never fail)."""
        game = _make_game()
        hand = _build_hand(
            game, card_db, player_idx=0,
            card_names=[
                "Mountain",
                "Arid Mesa",
                "Mountain",
                "Memnite",  # the only non-land — must be picked
            ],
            deck_name="Affinity",
        )
        assert len(hand) == 4

        game._force_discard(0, 1)

        discarded = game.players[0].graveyard
        assert len(discarded) == 1
        picked = discarded[0].name

        assert picked == "Memnite", (
            f"Thoughtseize must pick the lone non-land ({picked!r}). "
            f"Lands should never be picked for opponent-forced discard."
        )
