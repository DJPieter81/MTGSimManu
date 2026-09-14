"""A land's basic types are whatever the continuous-effects layer says
they are right now — never its printed types, never a shortcut flag.

Two families write layer 4 (CR 613.2b) on lands: the SET family
("Nonbasic lands are Mountains" — Blood Moon, Magus, Harbinger …) and the
ADD family ("Lands you control are every basic land type in addition to
their other types" — Leyline of the Guildpact, Prismatic Omen, Dryad of
the Ilysian Grove …). Within the layer the later timestamp wins
(CR 613.7): a SET applied after an ADD leaves the land with the one set
type and nothing else; an ADD applied after a SET adds the five types
back. Every reader of a land's types — domain (`ManaPayment.count_domain`,
`CardInstance._get_domain_count`), the colours it produces, and whether a
fetchland still has its search — must read that resolved state.

The bug: `count_domain` returned 5 whenever an ADD-family permanent was on
the battlefield and otherwise read `template.subtypes`; both ignore the
layer. Under a resolved Blood Moon a Leyline deck kept domain 5 — Scion of
Draco for {2} instead of {10}, Leyline Binding for {W}, a 5/5 Kavu —
while its lands correctly produced only red (replays s60303 L1419-1450,
s60304 L1538-1573, 2026-09-12). And the engine's residual sacrifice
heuristic activated a fetchland's search that the fetch path had just
refused ("has no fetch ability (its land type is set)", s60303
L1571-1574): a land whose type is set has no activated ability but its
mana ability (CR 305.7).

Card names are fixture carriers for the two oracle shapes.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.mana_payment import ManaPayment


def _put(game, card_db, name, controller, zone="battlefield"):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        game.players[controller].battlefield.append(c)
    elif zone == "library":
        game.players[controller].library.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 1
    return game


def _zoo_lands(game, card_db, idx=1):
    """Three nonbasic lands covering four printed basic types, no basic."""
    return [_put(game, card_db, n, idx) for n in
            ("Thundering Falls", "Indatha Triome", "Breeding Pool")]


def test_domain_counts_the_lands_current_types_not_their_printed_ones(card_db):
    # ADD then SET: the later SET leaves every nonbasic land a Mountain only.
    game = _game()
    _zoo_lands(game, card_db)
    _put(game, card_db, "Leyline of the Guildpact", 1)
    game.continuous_effects.recalculate(game)
    assert ManaPayment.count_domain(game, 1) == 5
    _put(game, card_db, "Blood Moon", 0)
    game.continuous_effects.recalculate(game)
    assert ManaPayment.count_domain(game, 1) == 1, (
        "under a later Blood Moon every nonbasic land is a Mountain and "
        "nothing else — domain is 1, not the Leyline's 5")
    # A basic land is untouched by the SET family, so the ADD still
    # applies to it: one Plains under Leyline + Moon is all five types
    # (the real-Magic line a Leyline deck plays against a Moon).
    _put(game, card_db, "Plains", 1)
    game.continuous_effects.recalculate(game)
    assert ManaPayment.count_domain(game, 1) == 5

    # SET then ADD: the later ADD puts the five types back.
    game2 = _game()
    _zoo_lands(game2, card_db)
    _put(game2, card_db, "Blood Moon", 0)
    game2.continuous_effects.recalculate(game2)
    assert ManaPayment.count_domain(game2, 1) == 1
    _put(game2, card_db, "Leyline of the Guildpact", 1)
    game2.continuous_effects.recalculate(game2)
    assert ManaPayment.count_domain(game2, 1) == 5


def test_a_domain_scaled_body_reads_the_same_resolved_types(card_db):
    """The CardInstance-side reader (`_get_domain_count`, Kavu / Scion
    cost) is the same rule, not a second copy of the shortcut."""
    game = _game()
    _zoo_lands(game, card_db)
    _put(game, card_db, "Leyline of the Guildpact", 1)
    kavu = _put(game, card_db, "Territorial Kavu", 1)
    game.continuous_effects.recalculate(game)
    assert kavu.power == 5
    _put(game, card_db, "Blood Moon", 0)
    game.continuous_effects.recalculate(game)
    assert kavu.power == 1, f"Kavu is {kavu.power}/{kavu.toughness} under Blood Moon"


def test_the_colours_a_land_produces_follow_the_same_resolved_types(card_db):
    game = _game()
    falls, _tri, _pool = _zoo_lands(game, card_db)
    _put(game, card_db, "Blood Moon", 0)
    game.continuous_effects.recalculate(game)
    assert set(ManaPayment.effective_produces_mana(game, 1, falls)) == {"R"}
    # The ADD family applied later gives the five colours back.
    _put(game, card_db, "Leyline of the Guildpact", 1)
    game.continuous_effects.recalculate(game)
    assert set(ManaPayment.effective_produces_mana(game, 1, falls)) == {"W", "U", "B", "R", "G"}
    # The untapped-land colour census reads the same resolved types
    # (these lands enter tapped; untap them for the census).
    for land in (falls, _tri, _pool):
        land.tapped = False
    assert game.players[1].available_mana_colors()["U"] >= 1


def test_a_type_setting_effect_removes_a_fetchlands_search_from_every_activation_path(card_db):
    from engine.game_runner import GameRunner
    game = _game()
    _put(game, card_db, "Leyline of the Guildpact", 1)
    strand = _put(game, card_db, "Flooded Strand", 1)
    _put(game, card_db, "Hallowed Fountain", 1, "library")
    # An expensive spell in hand is what makes the heuristic want to search.
    _put(game, card_db, "Scion of Draco", 1, "hand")
    _put(game, card_db, "Blood Moon", 0)
    game.turn_number = 5
    game.continuous_effects.recalculate(game)
    GameRunner(card_db=card_db)._activate_sacrifice_abilities(game, 1)
    assert strand in game.players[1].battlefield, (
        "a land whose type is set has no activated ability but its mana "
        "ability (CR 305.7) — the sacrifice heuristic activated its search")
    assert len(game.players[1].library) == 1
