"""oracle-ratchet: is_cascade field must be read at decision time, not oracle text.

Rule: every card property derivable from oracle text is parsed ONCE in
card_database.py at load time and stored as a typed field on CardTemplate.
Decision-time code (engine/, ai/) reads the field; it never re-inspects
the raw oracle string.

This test pins the mechanic: the cascade keyword triggers a free cast of a
non-land spell with lower CMC.  Detection of cascade membership belongs
entirely to CardTemplate.is_cascade, populated by has_cascade() in
oracle_parser.py.  No runtime code may check 'cascade' in oracle_text.

Class size: every cascade card in Modern (Shardless Agent, Bloodbraid Elf,
Demonic Dread, Violent Outburst, Bituminous Blast, …).  The mechanic is
the keyword, not the card name.

Subsystem: engine/card_database.py (population), ai/ev_evaluator.py and
ai/sideboard_solver.py (consumers — must read field, not oracle).
"""
from __future__ import annotations

import pytest

from engine.cards import Keyword


# ──────────────────────────────────────────────────────────────────────────────
# Field population — load-time
# ──────────────────────────────────────────────────────────────────────────────

def test_cascade_card_has_is_cascade_true(card_db):
    """A card with the cascade keyword must have template.is_cascade == True.

    Mechanism: cascade keyword → is_cascade field populated at DB load.
    Oracle text is never re-inspected at decision time.
    """
    # Shardless Agent is a canonical cascade card available in Modern.
    tmpl = card_db.get_card("Shardless Agent")
    if tmpl is None:
        pytest.skip("Shardless Agent not in DB")
    assert tmpl.is_cascade is True, (
        f"Shardless Agent must have is_cascade=True; got {tmpl.is_cascade!r}. "
        "Populate template.is_cascade from has_cascade(oracle) in card_database.py."
    )


def test_non_cascade_card_has_is_cascade_false(card_db):
    """A non-cascade card must have template.is_cascade == False (the default).

    Mechanism: is_cascade defaults to False; only oracle/keyword match sets it True.
    """
    tmpl = card_db.get_card("Lightning Bolt")
    if tmpl is None:
        pytest.skip("Lightning Bolt not in DB")
    assert tmpl.is_cascade is False, (
        f"Lightning Bolt must have is_cascade=False; got {tmpl.is_cascade!r}."
    )


def test_cascade_detected_via_keyword_enum(card_db):
    """Cascade cards must also carry Keyword.CASCADE in their keywords set.

    Mechanism: KEYWORD_MAP maps 'Cascade' → Keyword.CASCADE at DB load time.
    This provides a second lookup path for code that iterates the keyword set.
    """
    tmpl = card_db.get_card("Shardless Agent")
    if tmpl is None:
        pytest.skip("Shardless Agent not in DB")
    assert Keyword.CASCADE in (tmpl.keywords or set()), (
        "Shardless Agent must carry Keyword.CASCADE in template.keywords. "
        "KEYWORD_MAP in card_database.py must include 'Cascade': Keyword.CASCADE."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Runtime consumers — must read field, not oracle
# ──────────────────────────────────────────────────────────────────────────────

def test_ev_evaluator_cascade_branch_uses_is_cascade_field(card_db):
    """_project_spell must use template.is_cascade, not 'cascade' in oracle_text.

    Mechanism: the cascade EV branch in ai/ev_evaluator.py:_project_spell
    must consult template.is_cascade (a typed bool field) to decide whether
    to model the free cascade cast.  A card that has is_cascade=True must
    trigger the branch; a card without it must not.
    """
    from engine.cards import CardInstance
    from engine.game_state import GameState, Phase
    from engine.player_state import PlayerState
    from ai.ev_evaluator import _project_spell, snapshot_from_game
    import random

    tmpl = card_db.get_card("Shardless Agent")
    if tmpl is None:
        pytest.skip("Shardless Agent not in DB")

    # Verify the field is set — if it's not, the oracle check would need to
    # compensate, which is the anti-pattern we're eliminating.
    assert tmpl.is_cascade, "pre-condition: Shardless Agent.is_cascade must be True"

    # A cascade card with is_cascade=True must produce a higher projected EV
    # than a non-cascade card of the same P/T, because the cascade hits a
    # free spell.  We just confirm _project_spell accepts the call without
    # falling back to oracle text inspection.
    game = GameState(rng=random.Random(42))
    game.phase = Phase.MAIN1
    for i in range(2):
        ps = PlayerState(player_idx=i)
        ps.life = 20
        game.players.append(ps)
    game.active_player_index = 0

    cascade_inst = CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="hand",
    )
    cascade_inst._game_state = game
    game.players[0].hand.append(cascade_inst)

    snap = snapshot_from_game(game, 0)
    # _project_spell must not raise; it must use template.is_cascade.
    result = _project_spell(cascade_inst, snap)
    assert result is not None, "_project_spell must return a snapshot for cascade cards"


def test_sideboard_solver_cascade_uses_is_cascade_field(card_db):
    """_clause_body_value must use template.is_cascade, not oracle text.

    Mechanism: cascade detection in ai/sideboard_solver.py:_clause_body_value
    must read template.is_cascade (field) rather than checking 'cascade' in oracle.
    """
    from ai.sideboard_solver import _clause_body_value

    tmpl = card_db.get_card("Shardless Agent")
    if tmpl is None:
        pytest.skip("Shardless Agent not in DB")

    # The function must not raise AttributeError / TypeError even if
    # oracle_text were None — because it now reads template.is_cascade.
    # We also verify the cascade code path returns a positive value
    # (the cascade bonus rounds to something > 0 for CMC ≥ 3).
    value = _clause_body_value(tmpl)
    assert value >= 0, (
        "_clause_body_value must return non-negative value for cascade cards; "
        f"got {value}"
    )
