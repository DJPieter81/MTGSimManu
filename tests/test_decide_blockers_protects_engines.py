"""Phase 3 — RC-4 of the block strategy audit.

Emergency-path selection picks the smallest-creature_value chump, but
creature_value alone is not enough to protect structurally-valuable
pieces: Phlage (escape), Ajani-like planeswalkers, and battle-cry /
attack-trigger sources all show up as chump candidates. _is_protected_piece
wraps these in an oracle/tag-driven filter that applies to both the
emergency and non-emergency paths.

Reference: audits/BLOCK_STRATEGY_AUDIT.md §Phase 3.
"""
from __future__ import annotations

import random
import pytest

from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _mk_player():
    return EVPlayer(player_idx=0, deck_name="Boros Energy",
                    rng=random.Random(0))


class TestProtectedPieceHelper:
    def test_planeswalker_is_protected(self, card_db):
        """Unit test on _is_protected_piece for a real planeswalker."""
        tmpl = card_db.get_card("Liliana of the Veil")
        assert tmpl is not None
        card = CardInstance(template=tmpl, owner=0, controller=0,
                             instance_id=0, zone="battlefield")
        assert _mk_player()._is_protected_piece(card) is True

    def test_escape_creature_is_protected(self, card_db):
        """Phlage has 'Escape—' in oracle (em-dash). Must be protected."""
        tmpl = card_db.get_card("Phlage, Titan of Fire's Fury")
        assert tmpl is not None
        card = CardInstance(template=tmpl, owner=0, controller=0,
                             instance_id=0, zone="battlefield")
        assert _mk_player()._is_protected_piece(card) is True, (
            "Phlage has 'Escape—' in oracle; must be protected."
        )

    def test_attack_trigger_source_is_protected(self, card_db):
        """Voice of Victory has 'whenever this creature attacks' via
        Mobilize — must be protected."""
        tmpl = card_db.get_card("Voice of Victory")
        assert tmpl is not None
        card = CardInstance(template=tmpl, owner=0, controller=0,
                             instance_id=0, zone="battlefield")
        assert _mk_player()._is_protected_piece(card) is True

    def test_vanilla_creature_not_protected(self, card_db):
        """Memnite is a 1/1 vanilla artifact creature — not protected."""
        tmpl = card_db.get_card("Memnite")
        assert tmpl is not None
        card = CardInstance(template=tmpl, owner=0, controller=0,
                             instance_id=0, zone="battlefield")
        assert _mk_player()._is_protected_piece(card) is False


class TestPhlageNotChumpedWhenTokenAvailable:
    def test_memnite_blocks_instead_of_phlage_at_healthy_life(self, card_db):
        """Me: Phlage (6/6) + Memnite (1/1) chump fodder. Opp: Sojourner's
        Companion temp-boosted to 10/10. Life=17. Non-emergency path.

        Neither blocker can kill the 10/10. Memnite should be the chosen
        blocker (or no block); Phlage is protected (escape) and must
        not be selected."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 17
        game.players[1].life = 20

        phlage = _add_to_battlefield(game, card_db,
                                      "Phlage, Titan of Fire's Fury",
                                      controller=0)
        memnite = _add_to_battlefield(game, card_db, "Memnite", controller=0)
        attacker = _add_to_battlefield(game, card_db,
                                        "Sojourner's Companion",
                                        controller=1)
        attacker.temp_power_mod = 6
        attacker.temp_toughness_mod = 6
        assert attacker.power == 10, "setup: expected 10/10 attacker"

        player = _mk_player()
        blocks = player.decide_blockers(game, [attacker])

        # Phlage must not be used as a chump.
        all_blocker_ids = [b for ids in blocks.values() for b in ids]
        assert phlage.instance_id not in all_blocker_ids, (
            f"Phlage (escape creature) must not chump. Got {blocks}."
        )


class TestPhlageMayChumpIfOnlyOptionAndLethal:
    def test_phlage_chumps_when_lethal_and_only_blocker(self, card_db):
        """Me: Phlage only on board. Opp: Sojourner's Companion (4/4).
        Life=4 — incoming lethal. Emergency path.

        Phlage is protected, but the fallback in _blocker_candidates
        must still return Phlage when no alternative exists."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 4
        game.players[1].life = 20

        phlage = _add_to_battlefield(game, card_db,
                                      "Phlage, Titan of Fire's Fury",
                                      controller=0)
        attacker = _add_to_battlefield(game, card_db,
                                        "Sojourner's Companion",
                                        controller=1)

        player = _mk_player()
        blocks = player.decide_blockers(game, [attacker])
        assert attacker.instance_id in blocks, (
            f"at life=4 vs lethal incoming and Phlage is the only blocker, "
            f"Phlage must block (survival beats protection). Got {blocks}."
        )
        assert phlage.instance_id in blocks[attacker.instance_id], (
            f"Phlage must be assigned when it's the only blocker. Got "
            f"{blocks}."
        )


class TestBattleCrySourceProtectedInBothPaths:
    def test_voice_of_victory_not_chumped_when_token_available(self, card_db):
        """Me: Voice of Victory (1/3 attack-trigger source) + Memnite (1/1).
        Opp: attacker 4/4 at life=20. Non-emergency path. Voice was
        already filtered by the pre-existing is_battle_cry clause; this
        is a regression anchor that it still works after _is_protected_piece
        refactor."""
        game = GameState(rng=random.Random(0))
        game.players[0].life = 20
        game.players[1].life = 20

        voice = _add_to_battlefield(game, card_db, "Voice of Victory",
                                     controller=0)
        _add_to_battlefield(game, card_db, "Memnite", controller=0)
        attacker = _add_to_battlefield(game, card_db,
                                        "Sojourner's Companion",
                                        controller=1)

        player = _mk_player()
        blocks = player.decide_blockers(game, [attacker])
        all_blocker_ids = [b for ids in blocks.values() for b in ids]
        assert voice.instance_id not in all_blocker_ids, (
            f"Voice of Victory is a battle-cry-source; must not chump. "
            f"Got {blocks}."
        )
