"""M1-AI — per-spell EV models opponent-static self card-event taxes.

Post-engine-fix evidence (seed 60101 re-run, this session): Storm's
impulse family correctly bypasses on-draw triggers now, but the deck
STILL self-killed on T5 by chaining REAL draws (Manamorphose,
Valakut Awakening) at ≤2 life into two Bowmasters.  The chain
estimator (`_estimate_combo_chain`) already prices `per_draw_tax` /
`per_cast_tax`, but the per-spell projection (`_project_spell` →
`compute_play_ev` → every cast decision) never subtracts them — so
individual casts walk into lethal self-damage the estimator knows
about.

Fix under test:
- `_project_spell` subtracts `opp_static_damage_per_card_event`
  taxes: per-cast, plus per-REAL-draw × projected draw count.
  Impulse-tagged spells (Tag.IMPULSE_DRAW — same single source of
  truth as the engine branch) project ZERO draw tax.
- `compute_play_ev` floors a cast whose projection kills the caster
  (`projected.my_life <= 0 < snap.my_life`) at `-LETHAL_THREAT` —
  the established game-ending sentinel scale; suicide-by-cantrip is
  a game-ending event against us.

Card names are tag-lookup carriers only; assertions target the
mechanic.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState, Phase
from engine.mana import ManaCost

from ai.ev_evaluator import EVSnapshot, _project_spell, compute_play_ev
from ai.scoring_constants import LETHAL_THREAT


def _fresh_game() -> GameState:
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = 0
    g.turn_number = 5
    return g


def _card(game, name, oracle, controller=0, zone="hand",
          types=(CardType.SORCERY,), on_bf=False):
    tmpl = CardTemplate(
        name=name, card_types=list(types), mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[],
        power=1 if CardType.CREATURE in types else None,
        toughness=1 if CardType.CREATURE in types else None,
        loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text=oracle, tags=set(),
    )
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if on_bf:
        c.enter_battlefield()
        c.summoning_sick = False
        game.players[controller].battlefield.append(c)
    else:
        getattr(game.players[controller], zone).append(c)
    return c


def _bowmasters(game, controller=1):
    return _card(
        game, "Orcish Bowmasters",
        "Whenever an opponent draws a card, except the first one they "
        "draw in each of their draw steps, this creature deals 1 damage "
        "to that player.",
        controller=controller, zone="battlefield",
        types=(CardType.CREATURE,), on_bf=True)


def _snap(game, life=20):
    game.players[0].life = life
    return EVSnapshot.from_game(game, 0)


# ─── projection subtracts the taxes ──────────────────────────────────

def test_real_draw_spell_projection_subtracts_per_draw_tax():
    game = _fresh_game()
    _bowmasters(game); _bowmasters(game)
    spell = _card(game, "Manamorphose",
                  "Add two mana in any combination of colors.\n"
                  "Draw a card.")
    snap = _snap(game, life=20)
    projected = _project_spell(spell, snap, None, game, 0)
    assert projected.my_life == snap.my_life - 2, (
        "one real draw into two on-draw-damage sources costs 2 life "
        "in projection")


def test_multi_draw_spell_projection_scales_with_draw_count():
    game = _fresh_game()
    _bowmasters(game)
    spell = _card(game, "Bitter Ordeal Stand-In", "Draw three cards.")
    snap = _snap(game, life=20)
    projected = _project_spell(spell, snap, None, game, 0)
    assert projected.my_life == snap.my_life - 3


def test_impulse_tagged_spell_projects_zero_draw_tax():
    """Same single source of truth as the engine: Tag.IMPULSE_DRAW →
    not a draw → no tax."""
    game = _fresh_game()
    _bowmasters(game); _bowmasters(game)
    spell = _card(game, "Wrenn's Resolve",
                  "Exile the top two cards of your library. Until the "
                  "end of your next turn, you may play those cards.")
    snap = _snap(game, life=20)
    projected = _project_spell(spell, snap, None, game, 0)
    assert projected.my_life == snap.my_life


def test_no_tax_sources_no_life_change():
    game = _fresh_game()
    spell = _card(game, "Plain Cantrip", "Draw a card.")
    snap = _snap(game, life=20)
    projected = _project_spell(spell, snap, None, game, 0)
    assert projected.my_life == snap.my_life


# ─── cast EV floors lethal-to-self ───────────────────────────────────

def test_cast_ev_vetoes_lethal_to_self():
    """At 2 life vs two Bowmasters, a real-draw cantrip projects
    death — its EV must sit at the game-ending sentinel floor and
    below the same cast at healthy life."""
    game = _fresh_game()
    _bowmasters(game); _bowmasters(game)
    spell = _card(game, "Manamorphose",
                  "Add two mana in any combination of colors.\n"
                  "Draw a card.")

    ev_healthy = compute_play_ev(spell, _snap(game, life=20), "combo",
                                 game=game, player_idx=0)
    ev_dying = compute_play_ev(spell, _snap(game, life=2), "combo",
                               game=game, player_idx=0)

    assert ev_dying <= -LETHAL_THREAT, (
        f"suicide-by-cantrip must floor at -LETHAL_THREAT, got {ev_dying}")
    assert ev_dying < ev_healthy
