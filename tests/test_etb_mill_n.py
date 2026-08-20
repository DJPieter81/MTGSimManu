"""ETB "mill N cards" (self-mill), plus the mill-and-select rider.

Class: 84 Modern permanents carry a "When ~ enters, mill N cards" ETB
(Armored Skaab, Aftermath Analyst, ...). `resolve_etb_from_oracle` had no
branch, so the self-mill was a silent no-op — a real graveyard enabler for
reanimator/graveyard decks did nothing.

Rules under test:
  - a plain ETB self-mill moves the top N of the controller's library to the
    graveyard;
  - the "you may put a [type] card from among the cards milled ... into your
    hand" rider (Fallaji Archaeologist) recovers one matching card;
  - "target player mills" (Hedron Crab-style, not the permanent's own ETB) is
    NOT matched here.
No card names in the resolver.
"""
from __future__ import annotations

import random

from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.cards import CardInstance


def _mk(game, db, name, owner, zone):
    t = db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    return c


def _game(card_db):
    g = GameState(rng=random.Random(3))
    g.active_player = 0
    g.current_phase = Phase.MAIN1
    g.turn_number = 3
    return g


def test_plain_etb_mill_moves_top_n_to_graveyard(card_db):
    g = _game(card_db)
    p = g.players[0]
    for name in ["Forest"] * 6:
        p.library.append(_mk(g, card_db, name, 0, "library"))
    gy_before = len(p.graveyard)
    lib_before = len(p.library)

    skaab = _mk(g, card_db, "Armored Skaab", 0, "battlefield")  # mill 4
    from engine.oracle_resolver import resolve_etb_from_oracle
    assert resolve_etb_from_oracle(g, skaab, 0) is True
    assert len(p.graveyard) == gy_before + 4, "Armored Skaab mills 4"
    assert len(p.library) == lib_before - 4


def test_etb_mill_and_select_recovers_matching_card(card_db):
    g = _game(card_db)
    p = g.players[0]
    # Top 3: a land, a creature, and a noncreature-nonland spell. Fallaji
    # mills 3 and may put a noncreature, nonland card into hand -> the spell.
    for name in ["Forest", "Devourer of Destiny", "Malevolent Rumble",
                 "Forest", "Forest"]:
        p.library.append(_mk(g, card_db, name, 0, "library"))
    hand_before = len(p.hand)

    fallaji = _mk(g, card_db, "Fallaji Archaeologist", 0, "battlefield")
    from engine.oracle_resolver import resolve_etb_from_oracle
    assert resolve_etb_from_oracle(g, fallaji, 0) is True
    # Three milled; one noncreature-nonland (Malevolent Rumble) recovered.
    assert len(p.hand) == hand_before + 1
    assert any(c.name == "Malevolent Rumble" for c in p.hand)
    # The recovered card is NOT a creature or land.
    for c in p.hand:
        types = {ct.value for ct in c.template.card_types}
        assert "creature" not in types and "land" not in types


def test_target_player_mill_is_not_matched(card_db):
    """Hedron Crab's landfall 'target player mills' is not an own-ETB self
    mill and must not be handled by this branch."""
    g = _game(card_db)
    p = g.players[0]
    for name in ["Forest"] * 4:
        p.library.append(_mk(g, card_db, name, 0, "library"))
    gy_before = len(p.graveyard)
    crab = _mk(g, card_db, "Hedron Crab", 0, "battlefield")
    from engine.oracle_resolver import resolve_etb_from_oracle
    resolve_etb_from_oracle(g, crab, 0)
    assert len(p.graveyard) == gy_before, "Hedron Crab has no ETB self-mill"
