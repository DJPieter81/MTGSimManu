"""A land search takes the haste land that converts this turn's attack.

Which lands a library search finds is its searcher's choice (CR 701.19a),
made by the AI's one land valuation (`ai.activation_ev.
_choose_land_delivery`). A land whose own activated ability grants a
creature haste (`CardTemplate.grants_haste_activation`, CR 702.10) is worth,
on top of what it adds as a land, the attack it converts THIS turn. That
holds when:
- the searcher is in its precombat main phase with a summoning-sick
  creature whose attack improves its clock (the same valuation the
  activation scorer gives the ability once the land is in play,
  `haste_grant_gain`);
- it controls no other haste-granting land, and the search has not already
  found one;
- the land will be untapped (it does not enter tapped, or an untap-on-enter
  watcher untaps it, CR 603.6a), and the searcher has the mana for the
  ability.

Otherwise the pick is the land valuation's own.

The defect: unit TU retired the engine's name-keyed Primeval Titan handler,
which had held this preference, and the AI's land valuation never took it
over. Amulet Titan's goldfish (s70002) fetched bounce lands and Urza's Saga
beside a sick Titan for three turns while the haste land stayed in the
library; its median kill turn was 8. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase

TITAN = "Primeval Titan"
HASTE_LAND = "Hanweir Battlements // Hanweir, the Writhing Township"
TAPPED_LAND = "Crumbling Vestige"
WATCHER = "Amulet of Vigor"


def _game():
    from engine.game_runner import AICallbacks
    game = GameState(rng=random.Random(0), callbacks=AICallbacks())
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0, sick=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, name
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = sick
    getattr(game.players[idx], zone).append(c)
    return c


def _board(game, card_db, watcher=True, sick=True, haste_in_play=False):
    if watcher:
        _put(game, card_db, WATCHER, "battlefield")
    _put(game, card_db, "Mountain", "battlefield")        # pays {R}
    _put(game, card_db, TITAN, "battlefield", sick=sick)
    if haste_in_play:
        _put(game, card_db, HASTE_LAND, "battlefield")
    return [_put(game, card_db, n, "library")
            for n in (TAPPED_LAND, TAPPED_LAND, HASTE_LAND, "Forest")]


def _picks(game, lands, n=2, enters_tapped=True, haste_field=True):
    from ai.activation_ev import _choose_land_delivery
    lands, picked = list(lands), []
    for _ in range(n):
        land = _choose_land_delivery(game, 0, lands, batch=tuple(picked),
                                     enters_tapped=enters_tapped)
        picked.append(land)
        lands = [c for c in lands if c is not land]
    return [c.name for c in picked]


def _without_haste_value(game, lands, monkeypatch, **kw):
    """The land valuation's own pick: the haste gain read as none."""
    import ai.activation_ev as ae
    with monkeypatch.context() as m:
        m.setattr(ae, "haste_grant_gain", lambda snap, me: None)
        return _picks(game, lands, **kw)


def test_the_haste_land_is_taken_when_a_watcher_untaps_it(card_db):
    game = _game()
    lib = _board(game, card_db)
    assert HASTE_LAND in _picks(game, lib)


def test_the_haste_land_is_taken_when_it_enters_untapped(card_db):
    game = _game()
    lib = _board(game, card_db, watcher=False)
    assert HASTE_LAND in _picks(game, lib, enters_tapped=False)


def test_a_haste_land_put_tapped_with_no_watcher_converts_nothing(
        card_db, monkeypatch):
    game = _game()
    lib = _board(game, card_db, watcher=False)
    assert _picks(game, lib) == _without_haste_value(game, lib, monkeypatch)


def test_with_no_sick_attacker_the_pick_is_the_land_valuations_own(
        card_db, monkeypatch):
    game = _game()
    lib = _board(game, card_db, sick=False)
    assert _picks(game, lib) == _without_haste_value(game, lib, monkeypatch)


def test_a_second_haste_land_adds_nothing(card_db, monkeypatch):
    game = _game()
    lib = _board(game, card_db, haste_in_play=True)
    assert _picks(game, lib) == _without_haste_value(game, lib, monkeypatch)


def test_after_combat_the_pick_is_the_land_valuations_own(
        card_db, monkeypatch):
    game = _game()
    lib = _board(game, card_db)
    game.current_phase = Phase.MAIN2
    assert _picks(game, lib) == _without_haste_value(game, lib, monkeypatch)


def test_the_titans_own_search_finds_the_haste_land(card_db):
    """The real path: Primeval Titan cast beside a watcher, a red source
    left untapped; its enter search finds the haste land."""
    game = _game()
    _put(game, card_db, WATCHER, "battlefield")
    _put(game, card_db, "Mountain", "battlefield")
    for _ in range(6):
        _put(game, card_db, "Forest", "battlefield")
    lib = [_put(game, card_db, n, "library")
           for n in (TAPPED_LAND, TAPPED_LAND, HASTE_LAND, "Forest")]
    titan = _put(game, card_db, TITAN, "hand")
    forests = [c for c in game.players[0].battlefield if c.name == "Forest"]
    for f in forests:
        f.tapped = True
        game.players[0].mana_pool.add("G", 1)
    assert game.cast_spell(0, titan, targets=[])
    while game.stack.items:
        game.resolve_stack()
    assert any(c.name == HASTE_LAND and c.zone == "battlefield" for c in lib)
