"""Saga chapters follow the printed timing (CR 714), and a mana-cost
condition is never satisfied by a card that has no mana cost (CR 202.2).

# Mechanics the tests name

1. **"with mana cost {0} or {1}" excludes lands.**  A land has no mana
   cost; its mana value is 0 by convention (CR 202.2, 202.3), but it does
   not HAVE a mana cost of {0}.  The engine narrowed a Saga's final
   chapter search by mana value alone, so an artifact LAND satisfied the
   condition and the tutor put a free land onto the battlefield beside
   the land drop (Pinnacle Affinity vs Boros Ponza s50000: chapter III
   fetched Darksteel Citadel and Silverbluff Bridge).  Class: every
   "with mana cost {N}" / "with mana value N" condition evaluated over a
   library that holds lands — parsed once into `has_mana_cost`.

2. **A Saga enters with its first lore counter and chapter I.**  CR
   714.2a: "As a Saga enters the battlefield, its controller puts a lore
   counter on it."  The engine put the first counter at the controller's
   NEXT upkeep, so every chapter ran a full turn cycle late.

3. **Later lore counters are added as the precombat main phase begins,
   after the draw step** (CR 714.2b) — not in the upkeep.  Instant-speed
   plays in the upkeep and the drawn card both precede the chapter.

Class: every Saga (the subtype), every deck.  Card names below are
fixture carriers only.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


SAGA = "Urza's Saga"      # exemplar three-chapter Saga with a tutor chapter


def _put(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = False
        game.players[controller].battlefield.append(c)
        if tmpl.is_land:
            game.players[controller].lands.append(c)
    elif zone == "library":
        game.players[controller].library.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.turn_number = 3
    return game


# ─── 1. a card without a mana cost never satisfies a mana-cost condition ──


def test_a_land_has_no_mana_cost_while_a_zero_cost_spell_does(card_db):
    assert card_db.get_card("Darksteel Citadel").has_mana_cost is False
    assert card_db.get_card("Silverbluff Bridge").has_mana_cost is False
    assert card_db.get_card("Living End").has_mana_cost is False
    assert card_db.get_card("Memnite").has_mana_cost is True
    assert card_db.get_card("Mox Opal").has_mana_cost is True
    assert card_db.get_card("Cranial Plating").has_mana_cost is True


def test_the_final_chapter_search_excludes_artifact_lands(card_db):
    from engine.game_runner import _saga_iii_eligible_targets
    game = _game(card_db)
    citadel = _put(game, card_db, "Darksteel Citadel", 0, "library")
    bridge = _put(game, card_db, "Silverbluff Bridge", 0, "library")
    memnite = _put(game, card_db, "Memnite", 0, "library")
    mox = _put(game, card_db, "Mox Opal", 0, "library")
    plating = _put(game, card_db, "Cranial Plating", 0, "library")
    eligible = _saga_iii_eligible_targets(game, 0)
    assert memnite in eligible and mox in eligible
    assert citadel not in eligible and bridge not in eligible
    assert plating not in eligible


# ─── 2. a Saga enters with lore 1 and chapter I ────────────────────────


def test_a_saga_enters_with_its_first_lore_counter_and_chapter_one(card_db):
    game = _game(card_db)
    saga = _put(game, card_db, SAGA, 0, "hand")
    game.play_land(0, saga)
    assert saga in game.players[0].battlefield
    assert saga.other_counters.get("lore") == 1
    # Chapter I of the exemplar is an ability grant; it is attached as
    # the Saga enters, not at a later upkeep.
    assert any("{T}: Add {C}" in g for g in saga.granted_abilities)


# ─── 3. chapters advance at the precombat main phase, after the draw ───


def test_chapter_two_lands_on_the_controllers_next_main_phase_and_three_one_turn_later(
        game_runner, card_db):
    from engine.game_runner import AICallbacks
    game = _game(card_db)
    game.callbacks = AICallbacks()
    saga = _put(game, card_db, SAGA, 0, "hand")
    game.play_land(0, saga)
    mox = _put(game, card_db, "Mox Opal", 0, "library")
    citadel = _put(game, card_db, "Darksteel Citadel", 0, "library")
    assert saga.other_counters["lore"] == 1

    # The controller's next turn: precombat main begins → lore 2.
    game.turn_number += 2
    game_runner._process_saga_chapters(game, active=0)
    assert saga.other_counters["lore"] == 2
    assert any("Construct" in g for g in saga.granted_abilities)
    assert saga in game.players[0].battlefield

    # One turn later: lore 3 → the search resolves and the Saga is
    # sacrificed (CR 714.4).
    game.turn_number += 2
    game_runner._process_saga_chapters(game, active=0)
    assert saga not in game.players[0].battlefield
    assert saga in game.players[0].graveyard
    assert mox in game.players[0].battlefield
    assert citadel in game.players[0].library


def test_a_granted_ability_can_be_activated_in_response_to_its_own_final_chapter(
        game_runner, card_db):
    """CR 603.3 / 714.4: the final chapter's ability goes on the stack
    while the Saga is still on the battlefield, so its controller may
    activate what an earlier chapter granted before the sacrifice —
    the token is made AND the chapter resolves AND the Saga leaves."""
    from engine.game_runner import AICallbacks
    game = _game(card_db)
    game.callbacks = AICallbacks()
    saga = _put(game, card_db, SAGA, 0, "battlefield")
    _put(game, card_db, "Darksteel Citadel", 0, "battlefield")
    _put(game, card_db, "Darksteel Citadel", 0, "battlefield")
    mox = _put(game, card_db, "Mox Opal", 0, "library")
    saga.other_counters["lore"] = 1
    game_runner._process_saga_chapters(game, active=0)      # chapter II grant
    assert saga.other_counters["lore"] == 2
    game.turn_number += 2
    game_runner._process_saga_chapters(game, active=0)      # chapter III
    constructs = [c for c in game.players[0].battlefield
                  if "Construct" in (c.template.subtypes or [])
                  or "construct" in c.name.lower()]
    assert len(constructs) == 1
    assert saga in game.players[0].graveyard
    assert mox in game.players[0].battlefield


def test_the_response_activation_needs_its_printed_cost(game_runner, card_db):
    """With no other untapped mana the granted ability cannot be paid;
    the final chapter still resolves and the Saga is still sacrificed."""
    from engine.game_runner import AICallbacks
    game = _game(card_db)
    game.callbacks = AICallbacks()
    saga = _put(game, card_db, SAGA, 0, "battlefield")
    mox = _put(game, card_db, "Mox Opal", 0, "library")
    saga.other_counters["lore"] = 2
    game_runner._process_saga_chapters(game, active=0)
    constructs = [c for c in game.players[0].battlefield
                  if "construct" in c.name.lower()]
    assert constructs == []
    assert saga in game.players[0].graveyard
    assert mox in game.players[0].battlefield


def test_a_saga_placed_without_the_entry_hook_still_starts_at_chapter_one(
        game_runner, card_db):
    """Fixture path: a Saga appended straight to the battlefield (no
    zone transfer) has no lore counter; the first processing gives it
    chapter I and does not skip ahead."""
    game = _game(card_db)
    saga = _put(game, card_db, SAGA, 0, "battlefield")
    saga.other_counters.pop("lore", None)
    game_runner._process_saga_chapters(game, active=0)
    assert saga.other_counters["lore"] == 1
    assert saga in game.players[0].battlefield


@pytest.mark.timeout(60)
def test_lore_counters_are_added_after_the_draw_step_not_in_the_upkeep(
        game_runner):
    """Runner-level: in a verbose game, every chapter line of a Saga
    that entered on an earlier turn appears AFTER that turn's draw
    step and BEFORE the first main-phase play — never inside the
    upkeep.  And chapter II lands exactly one of its controller's
    turns after the Saga was played."""
    from decks.modern_meta import MODERN_DECKS
    from tests.conftest import run_seeded_game
    d1, d2 = "Pinnacle Affinity", "Affinity"
    seen = 0
    for seed in range(50000, 50010):
        random.seed(seed)
        game_runner.rng = random.Random(seed)
        deck1, deck2 = MODERN_DECKS[d1], MODERN_DECKS[d2]
        result = game_runner.run_game(
            d1, deck1["mainboard"], d2, deck2["mainboard"], verbose=True,
            deck1_sideboard=deck1.get("sideboard", {}),
            deck2_sideboard=deck2.get("sideboard", {}))
        log = result.game_log
        # Per player, the display turns of Saga plays not yet matched to
        # a chapter II (FIFO: chapters advance uniformly, so the oldest
        # Saga reaches chapter II first).
        played = {"P1": [], "P2": []}
        for i, line in enumerate(log):
            if f"Play {SAGA}" in line and line.startswith("T"):
                t = int(line.split()[0][1:])
                p = line.split()[1].rstrip(":")
                played[p].append(t)
            if f"{SAGA} Ch.2:" in line:
                seen += 1
                t = int(line.split()[0][1:])
                p = line.split()[1].rstrip(":")
                assert played[p], f"seed {seed}: chapter II with no play"
                entered = played[p].pop(0)
                assert t == entered + 1, (
                    f"seed {seed}: chapter II at T{t} for {p}, "
                    f"Saga played T{entered}")
                # Walk back: the nearest phase marker before this line
                # must be the draw step or the main phase, not the upkeep.
                j = i - 1
                while j >= 0 and not (log[j].strip().startswith("[")):
                    j -= 1
                marker = log[j].strip() if j >= 0 else ""
                assert not marker.startswith("[Upkeep]"), (
                    f"seed {seed}: chapter II fired in the upkeep: {log[j]}")
                assert marker.startswith("[Draw]") or marker.startswith("[Main 1]"), (
                    f"seed {seed}: chapter II fired at {marker!r}")
        if seen >= 3:
            break
    assert seen >= 1, "no Saga chapter II observed in ten seeded games"
