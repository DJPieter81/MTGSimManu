"""AI targeting — "target creature or planeswalker" spells must not
enumerate the opponent's face (-1) as a burn-target candidate.

Rule: CR 601.2c — every target of a spell must be a legal target
at the time of casting. When a spell oracle says only "target
creature or planeswalker" it has no authority to target a player.
The AI's candidate enumeration must honour this restriction.

Mechanic contrast:
  "any target" (Lightning Bolt) — face IS a legal candidate.
  "target creature or planeswalker" (energy-damage family) —
      face is NOT a legal candidate, regardless of opp life or the
      computed value of face damage.

Class size: the entire "target creature or planeswalker" oracle
family — every energy-damage spell, every "deal N damage to target
creature" instant, any future card with that wording.

Tests are named for the mechanic, not the fixture card.
The fixture card (Galvanic Discharge) appears only as a comment
explaining why it is the appropriate oracle carrier for this family.
"""
from __future__ import annotations

import random
import pytest

from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _land(game, card_db, name, controller):
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


def _hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _make_player_and_game(card_db):
    game = GameState(rng=random.Random(42))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 3
    game.players[0].lands_played_this_turn = 1
    _land(game, card_db, "Sacred Foundry", controller=0)
    player = EVPlayer(player_idx=0, deck_name="Boros Energy", rng=random.Random(42))
    return game, player


# Fixture card note: Galvanic Discharge oracle reads
# "Choose target creature or planeswalker" — no "any target",
# no "target player / opponent". Represents the entire
# permanent-only damage family.
_FIXTURE_CARD = "Galvanic Discharge"


class TestPermanentOnlyBurnExcludesFaceFromCandidates:
    """The face (-1) sentinel must never appear in the burn-target
    candidate set when the spell oracle restricts targets to
    permanents (creatures / planeswalkers) only."""

    def test_face_absent_from_candidates_when_board_is_empty(self, card_db):
        """Empty opposing board: zero candidates expected.

        The deleted code always prepended face (-1) before checking
        whether the spell permits player targeting. The correct
        implementation gates the face append on an oracle predicate
        ("any target" | "target player" | "target opponent").
        """
        game, player = _make_player_and_game(card_db)
        spell = _hand(game, card_db, _FIXTURE_CARD, controller=0)

        candidates = player._enumerate_burn_targets(game, spell, damage=3)
        candidate_ids = {entry[0] for entry in candidates}

        assert -1 not in candidate_ids, (
            f"Face (-1) must not appear in the burn-target candidate "
            f"set for a spell restricted to 'target creature or "
            f"planeswalker'. Got candidate_ids={candidate_ids}. "
            f"The AI oracle gate must check for 'any target' / "
            f"'target player' / 'target opponent' before adding face."
        )
        assert len(candidates) == 0, (
            f"With an empty opp board and a 'target creature or "
            f"planeswalker' spell, the candidate set must be empty. "
            f"Got {len(candidates)} candidates: {candidates}."
        )

    def test_face_absent_from_candidates_with_low_threat_creature(self, card_db):
        """Low-threat creature on opp side: creature is the ONLY
        candidate; face must be absent.

        Prior code compared face-value against creature threat and
        could choose face as the winning candidate when the creature
        carries minimal EV, routing the spell illegally to the player.
        """
        game, player = _make_player_and_game(card_db)
        creature = _battlefield(game, card_db, "Ornithopter", controller=1)
        spell = _hand(game, card_db, _FIXTURE_CARD, controller=0)

        candidates = player._enumerate_burn_targets(game, spell, damage=3)
        candidate_ids = {entry[0] for entry in candidates}

        assert -1 not in candidate_ids, (
            f"Face (-1) must not appear as a candidate for "
            f"'target creature or planeswalker' spell, even when "
            f"the only creature is a low-threat 0/2 body. "
            f"Got candidate_ids={candidate_ids}."
        )
        assert creature.instance_id in candidate_ids, (
            f"The 0/2 creature (instance_id={creature.instance_id}) "
            f"must appear as the sole burn-target candidate. "
            f"Got candidate_ids={candidate_ids}."
        )

    def test_choose_targets_returns_empty_when_no_legal_permanents(self, card_db):
        """_choose_targets must return [] (not [-1]) when no creature
        or planeswalker is on the opposing battlefield.

        Returning [-1] for a permanent-only spell with an empty board
        is an illegal cast that the engine silently executes as player
        damage. The correct return is [] so the caller's
        _spell_requires_targets gate skips the spell.
        """
        game, player = _make_player_and_game(card_db)
        spell = _hand(game, card_db, _FIXTURE_CARD, controller=0)

        targets = player._choose_targets(game, spell)

        assert -1 not in targets, (
            f"_choose_targets must not return face (-1) for a "
            f"'target creature or planeswalker' spell. "
            f"Got targets={targets}."
        )
        assert targets == [], (
            f"With no creature or planeswalker on the opposing "
            f"board, _choose_targets must return [] for this oracle "
            f"family. Got targets={targets}. "
            f"The prior empty-candidates fallback returned [-1] "
            f"unconditionally."
        )

    def test_choose_targets_returns_creature_not_face_with_low_threat_creature(
            self, card_db):
        """When only a low-threat creature is present, _choose_targets
        must return that creature, not face.

        This is the direct regression for the 'discharge goes face on
        a 0/2' bug: face was compared against creature EV and won when
        the creature had negligible threat value. After the fix, face
        is never in the candidate set so this comparison never occurs.
        """
        game, player = _make_player_and_game(card_db)
        creature = _battlefield(game, card_db, "Ornithopter", controller=1)
        spell = _hand(game, card_db, _FIXTURE_CARD, controller=0)

        targets = player._choose_targets(game, spell)

        assert -1 not in targets, (
            f"_choose_targets must not return face (-1) for a "
            f"'target creature or planeswalker' spell. "
            f"Got targets={targets}."
        )
        assert targets == [creature.instance_id], (
            f"_choose_targets must return the creature (instance_id="
            f"{creature.instance_id}) as the sole target. "
            f"Got targets={targets}. "
            f"Pre-fix: face value (~0.15 at full life, no clock) "
            f"beat 0-power creature threat (~0) — illegal choice."
        )

    def test_face_still_valid_candidate_for_any_target_spell(self, card_db):
        """Regression anchor: a spell with 'any target' or
        'target player' wording MUST still enumerate face as a legal
        candidate.

        The fix gates face on the oracle predicate; it must not
        accidentally suppress face for the correct 'any target'
        family (Lightning Bolt, Lava Spike, Grapeshot, etc.).

        Fixture: Lava Spike ('deals 3 damage to target player or
        planeswalker') explicitly permits player targeting.
        """
        game, player = _make_player_and_game(card_db)
        spell = _hand(game, card_db, "Lava Spike", controller=0)

        candidates = player._enumerate_burn_targets(game, spell, damage=3)
        candidate_ids = {entry[0] for entry in candidates}

        assert -1 in candidate_ids, (
            f"Face (-1) must remain a legal candidate for spells "
            f"with 'target player or planeswalker' wording. "
            f"The permanent-only gate must not suppress face for "
            f"'any target' / 'target player' spells. "
            f"Got candidate_ids={candidate_ids}."
        )
