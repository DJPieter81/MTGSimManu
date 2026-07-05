"""E4 — tokens cease to exist off the battlefield and are never cast.

Probe evidence (seed 60104, Azorius vs Affinity, G2): T5 a bounce
effect returned a construct token to its owner's HAND; T7 the AI
"Cast Construct Token (0)" as a spell.

Root: SBA 704.5f exists in `sba_manager.py` and filters on
`getattr(card, 'is_token', False)` — but nothing in the engine ever
SETS that flag.  `create_token` builds plain CardInstances, so the
cease-to-exist rule is dead code and tokens survive in hand /
graveyard / exile / library as castable, reanimatable, countable
cards.

Generic fix (no card names, no zone patches):
- `CardInstance.is_token` becomes a real field, set by the single
  token-creation funnel (`PermanentEffects.create_token`).
- SBA 704.5f then works as written for every off-battlefield zone.
- Defense in depth: CR 111.2 — tokens aren't cards and can never be
  cast; `can_cast` / `cast_spell` reject token instances regardless
  of SBA timing.

Class size: every token in the format (constructs, germs, treasures,
goblins, elementals, …).  Card names below are carriers only.
"""
from __future__ import annotations

import random

from engine.game_state import GameState, Phase


def _fresh():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    return game


# ─── The creation funnel marks token-ness ────────────────────────────

def test_created_token_instance_carries_token_flag():
    game = _fresh()
    toks = game.create_token(0, "goblin", count=2)
    assert len(toks) == 2
    assert all(t.is_token for t in toks)


def test_oracle_spec_created_token_carries_token_flag():
    game = _fresh()
    toks = game.create_token(
        0, "construct", count=1,
        source_oracle="Create a 0/0 colorless Construct artifact "
                      "creature token with \"This token gets +1/+1 for "
                      "each artifact you control.\"")
    assert toks and toks[0].is_token


# ─── CR 704.5f: off-battlefield tokens cease to exist ────────────────

def test_token_bounced_to_hand_ceases_to_exist():
    """A token returned to hand must be gone after state-based
    actions — not sit in hand as a castable card."""
    game = _fresh()
    tok = game.create_token(0, "goblin", count=1)[0]
    # Simulate a bounce effect's zone move.
    game.players[0].battlefield.remove(tok)
    tok.zone = "hand"
    game.players[0].hand.append(tok)

    game.check_state_based_actions()

    assert tok not in game.players[0].hand
    assert tok not in game.players[0].battlefield


def test_token_in_graveyard_ceases_after_sba():
    """A dead token must not linger in the graveyard (graveyard-count
    payoffs, reanimation targets)."""
    game = _fresh()
    tok = game.create_token(0, "goblin", count=1)[0]
    game.players[0].battlefield.remove(tok)
    tok.zone = "graveyard"
    game.players[0].graveyard.append(tok)

    game.check_state_based_actions()

    assert tok not in game.players[0].graveyard


def test_token_in_exile_ceases_after_sba():
    game = _fresh()
    tok = game.create_token(0, "goblin", count=1)[0]
    game.players[0].battlefield.remove(tok)
    tok.zone = "exile"
    game.players[0].exile.append(tok)

    game.check_state_based_actions()

    assert tok not in game.players[0].exile


# ─── CR 111.2: tokens are never castable ─────────────────────────────

def test_token_is_never_castable():
    """Even if a token instance is sitting in hand (pre-SBA window,
    replay tooling, whatever), the cast path must reject it."""
    game = _fresh()
    tok = game.create_token(0, "goblin", count=1)[0]
    game.players[0].battlefield.remove(tok)
    tok.zone = "hand"
    game.players[0].hand.append(tok)

    assert not game.can_cast(0, tok)
    assert not game.cast_spell(0, tok)
    assert not game.stack.items
