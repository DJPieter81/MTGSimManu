"""Golden decision positions: the board at a decision point, rebuilt.

Each builder recreates the position a replay exposed (the unit that fixed
the decision names its replay in `labels.json`). A position is asked one
decision of one kind:

- `main`: the main-phase play (`EVPlayer.decide_main_phase`), returning
  `("cast_spell" | "play_land" | ..., card name)` or None for a pass;
- `targets`: the targets a spell's caster announces (`EVPlayer.
  _choose_targets`), as card names, or "self" / "opponent" for a player;
- `land_search`: the lands a search delivers, one at a time (`ai.
  activation_ev._choose_land_delivery`);
- `sideboard`: the default sideboarder's new main deck
  (`engine.sideboard_manager.sideboard`).

Builders take the card database and return what the decision needs. Card
names are fixture carriers; the label names the rule.
"""
from __future__ import annotations

import random
from typing import Any, Callable, Dict, Tuple

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, zone, controller=0, sick=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, name
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = sick
        c.tapped = False
    getattr(game.players[controller], zone).append(c)
    return c


# A rollout lays a position out into a game the runner has already set
# up (`tools/decision_rollout.py`): its libraries are the decks' own,
# shuffled by the seed, so a builder neither creates a game nor fills a
# library while `_INTO` holds one.
_INTO: list = []


def _game(deck: str, opp: str = "Goldfish", phase=Phase.MAIN1,
          turn: int = 5, opp_life: int = 20) -> GameState:
    if _INTO:
        g = _INTO[-1]
        for p in g.players:
            for zone in ("hand", "battlefield", "graveyard", "exile"):
                getattr(p, zone).clear()
        g.players[1].life = opp_life
        g.players[0].lands_played_this_turn = 1
        return g
    g = GameState(rng=random.Random(0))
    g.players[0].deck_name = deck
    g.players[1].deck_name = opp
    g.players[1].life = opp_life
    g.current_phase = phase
    g.active_player = g.priority_player = 0
    g.turn_number = turn
    g.players[0].lands_played_this_turn = 1
    return g


def _library(g, card_db, n=15, name="Island", controller=0):
    if _INTO:
        return
    for _ in range(n):
        _add(g, card_db, name, "library", controller)


def _ai(deck: str):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=0, deck_name=deck, rng=random.Random(0))


# ── main-phase positions ─────────────────────────────────────────────────

def reanimate_with_target_in_graveyard(card_db):
    g = _game("Grixis Reanimator", "Azorius Blink")
    for _ in range(3):
        _add(g, card_db, "Swamp", "battlefield")
    _add(g, card_db, "Persist", "hand")
    _add(g, card_db, "Emperor of Bones", "hand")
    _add(g, card_db, "Archon of Cruelty", "graveyard")
    _library(g, card_db, name="Swamp")
    _library(g, card_db, name="Island", controller=1)
    return g, _ai("Grixis Reanimator")


def storm_closer_with_graveyard_fuel(card_db):
    g = _game("Ruby Storm", turn=8)
    for _ in range(6):
        _add(g, card_db, "Mountain", "battlefield")
    _add(g, card_db, "Grapeshot", "hand")
    for _ in range(2):
        _add(g, card_db, "Desperate Ritual", "graveyard").has_flashback = True
    g.players[0].spells_cast_this_turn = 6
    g._global_storm_count = 6
    _library(g, card_db, name="Mountain")
    return g, _ai("Ruby Storm")


def flashback_grant_with_no_mana_left(card_db):
    g = _game("Ruby Storm", "Boros Energy", turn=4)
    for _ in range(4):
        _add(g, card_db, "Mountain", "battlefield")
    _add(g, card_db, "Past in Flames", "hand")
    for n in ("Desperate Ritual", "Pyretic Ritual"):
        _add(g, card_db, n, "graveyard")
    _library(g, card_db, name="Mountain")
    return g, _ai("Ruby Storm")


def blink_before_combat_on_a_hasty_attacker(card_db):
    g = _game("Jeskai Blink", "Broodscale Bloodchief")
    for n in ("Hallowed Fountain", "Elegant Parlor"):
        _add(g, card_db, n, "battlefield")
    ragavan = _add(g, card_db, "Ragavan, Nimble Pilferer", "battlefield",
                   sick=True)
    ragavan._dashed = True
    _add(g, card_db, "Ephemerate", "hand")
    _library(g, card_db, name="Plains")
    _library(g, card_db, name="Forest", controller=1)
    return g, _ai("Jeskai Blink")




# ── target positions ─────────────────────────────────────────────────────

def hand_attack_with_nothing_to_bin(card_db):
    g = _game("Grixis Reanimator", "Azorius Control", turn=7)
    for _ in range(3):
        _add(g, card_db, "Swamp", "battlefield")
    seize = _add(g, card_db, "Thoughtseize", "hand")
    for n in ("Cling to Dust", "Fatal Push"):
        _add(g, card_db, n, "hand")
    for n in ("Island", "Island", "Plains"):
        _add(g, card_db, n, "hand", controller=1)
    _library(g, card_db, name="Swamp")
    _library(g, card_db, name="Island", controller=1)
    return g, _ai("Grixis Reanimator"), seize


def reanimation_beside_a_controlled_legend(card_db):
    g = _game("Goryo's Vengeance")
    for _ in range(3):
        _add(g, card_db, "Swamp", "battlefield")
    _add(g, card_db, "Atraxa, Grand Unifier", "battlefield")
    goryo = _add(g, card_db, "Goryo's Vengeance", "hand")
    for n in ("Atraxa, Grand Unifier", "Griselbrand"):
        _add(g, card_db, n, "graveyard")
    return g, _ai("Goryo's Vengeance"), goryo


# ── land search positions ────────────────────────────────────────────────

def titan_search_beside_an_untap_watcher(card_db):
    from engine.game_runner import AICallbacks
    g = _game("Amulet Titan")
    g.callbacks = AICallbacks()
    _add(g, card_db, "Amulet of Vigor", "battlefield")
    _add(g, card_db, "Mountain", "battlefield")
    _add(g, card_db, "Primeval Titan", "battlefield", sick=True)
    lib = [_add(g, card_db, n, "library")
           for n in ("Crumbling Vestige", "Crumbling Vestige",
                     "Hanweir Battlements // Hanweir, the Writhing Township",
                     "Forest")]
    return g, lib


# ── sideboard positions ──────────────────────────────────────────────────

def zoo_boards_against_red_storm(card_db):
    from decks.modern_meta import MODERN_DECKS
    zoo = MODERN_DECKS["Domain Zoo"]
    return (dict(zoo["mainboard"]), dict(zoo["sideboard"]),
            "Domain Zoo", "Ruby Storm")


POSITIONS: Dict[str, Tuple[str, Callable[[Any], Any]]] = {
    "reanimate_with_target_in_graveyard":
        ("main", reanimate_with_target_in_graveyard),
    "storm_closer_with_graveyard_fuel":
        ("main", storm_closer_with_graveyard_fuel),
    "flashback_grant_with_no_mana_left":
        ("main", flashback_grant_with_no_mana_left),
    "blink_before_combat_on_a_hasty_attacker":
        ("main", blink_before_combat_on_a_hasty_attacker),
    "hand_attack_with_nothing_to_bin":
        ("targets", hand_attack_with_nothing_to_bin),
    "reanimation_beside_a_controlled_legend":
        ("targets", reanimation_beside_a_controlled_legend),
    "titan_search_beside_an_untap_watcher":
        ("land_search", titan_search_beside_an_untap_watcher),
    "zoo_boards_against_red_storm":
        ("sideboard", zoo_boards_against_red_storm),
}
