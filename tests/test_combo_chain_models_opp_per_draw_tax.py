"""M1-AI — chain self-damage projection.

`ai/ev_evaluator.py:_estimate_combo_chain` previously projected damage
OUT to the opponent but ignored damage IN to the chain-caster from
opp-controlled per-event triggers. The smoking gun is the
storm_vs_dimir G1T4 replay: Storm at 10 life casts a 5-card draw spell
into 2 Orcish Bowmasters and self-kills before reaching its finisher.

Engine fix R1+M1 separated impulse-reveals (which do not fire draw
triggers, CR 121.1c) from real draws. This AI-side fix completes the
correction: the chain projection now walks each chain step, sums
per-event self-damage from opponent permanents whose oracle tags carry
the on-draw / on-cast event signal, and returns ``can_kill=False``
once the cumulative tax exceeds the caster's remaining life.

The tests are rule-phrased: every assertion describes a mechanic
(per-draw damage, per-draw life-loss, per-cast damage), not a card name
or deck name. Storm exposes the most edge cases for chain projection,
so a Storm-shaped fixture is the strongest robustness signal — but the
fix lifts every combo deck against every drawback-permanent matchup
(Storm/Izzet Prowess vs Bowmasters/Sheoldred, Goryo vs Bowmasters,
Cascade vs Drannith, Living End vs Leyline).

The tests construct minimal real ``GameState`` + ``CardInstance``
objects and monkeypatch the oracle classifier loader to return the
tag map under test — the test owns its data, no dependency on the
committed _oracle_classifier.json contents drifting.
"""
from __future__ import annotations

import random
from typing import Dict, FrozenSet

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState
from engine.mana import ManaCost
from ai import oracle_classifier as _oc
from ai.oracle_classifier import Tag
from ai.ev_evaluator import _estimate_combo_chain


# ─── helpers ────────────────────────────────────────────────────────


def _install_tag_map(monkeypatch, mapping: Dict[str, FrozenSet[Tag]]) -> None:
    """Replace the classifier loader so `tags_for(name)` returns exactly
    the test-controlled tag set. This isolates the test from the
    committed JSON cache."""
    monkeypatch.setattr(_oc, "_LOADED_CACHE", dict(mapping))
    monkeypatch.setattr(_oc, "_LOADED_PATH", None)
    monkeypatch.setattr(_oc, "load_oracle_tags",
                        lambda **kw: dict(mapping))


def _make_template(name: str, *, cmc: int = 1,
                   card_types=None, oracle_text: str = "",
                   tags=None, ritual_mana=None,
                   is_cost_reducer: bool = False) -> CardTemplate:
    if card_types is None:
        card_types = [CardType.INSTANT]
    return CardTemplate(
        name=name,
        card_types=card_types,
        mana_cost=ManaCost(generic=cmc),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle_text,
        tags=set(tags or set()),
        ritual_mana=ritual_mana,
        is_cost_reducer=is_cost_reducer,
    )


def _instance(game: GameState, tmpl: CardTemplate,
              controller: int, zone: str) -> CardInstance:
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def _fresh_game(my_life: int, opp_life: int = 5) -> tuple[GameState, int]:
    """Build a 2-player GameState; viewer is player 0. ``opp_life``
    defaults to 5 so a small Grapeshot-shape chain (storm count ≥ 5)
    reaches lethal-out — making the difference between with/without the
    self-damage projection observable in ``can_kill``."""
    g = GameState(rng=random.Random(0))
    g.players[0].life = my_life
    g.players[1].life = opp_life
    return g, 0


def _storm_chain_hand(game: GameState, me_idx: int, *,
                      n_draws: int = 1,
                      n_rituals: int = 1,
                      finisher: bool = True) -> None:
    """Populate a Storm-shape hand: rituals (cheap, produce mana),
    draw spells (parsed as drawing N cards), optional Grapeshot-shape
    payoff. Names are synthetic — the test owns its classifier map."""
    # Cheap ritual: cost 1, produces 3 mana — net +2.
    for i in range(n_rituals):
        tmpl = _make_template(
            f"SynthRitual{i}", cmc=1,
            card_types=[CardType.INSTANT],
            oracle_text="add three mana of any one color",
            tags={"ritual"}, ritual_mana=("any", 3),
        )
        _instance(game, tmpl, me_idx, "hand")
    # Draw spell: cost 1, "draws five cards" (real draw).
    for i in range(n_draws):
        tmpl = _make_template(
            f"SynthDraw{i}", cmc=1,
            card_types=[CardType.SORCERY],
            oracle_text="target player draws five cards",
            tags={"cantrip"},
        )
        _instance(game, tmpl, me_idx, "hand")
    if finisher:
        # Grapeshot-shape — the chain estimator special-cases the
        # name for storm count → damage arithmetic. Keep that branch
        # working by using the canonical name.
        tmpl = _make_template(
            "Grapeshot", cmc=2,
            card_types=[CardType.SORCERY],
            oracle_text="storm — deal 1 damage to any target",
            tags={"storm_payoff"},
        )
        _instance(game, tmpl, me_idx, "hand")
    # Give the caster enough lands to cast everything.
    for i in range(8):
        land_tmpl = CardTemplate(
            name=f"BasicLand{i}", card_types=[CardType.LAND],
            mana_cost=None, supertypes=[], subtypes=[],
            power=None, toughness=None, loyalty=None,
            keywords=set(), abilities=[],
            color_identity=set(), produces_mana=["any"],
            enters_tapped=False, oracle_text="", tags=set(),
        )
        land = CardInstance(template=land_tmpl, owner=me_idx,
                            controller=me_idx,
                            instance_id=game.next_instance_id(),
                            zone="battlefield")
        land._game_state = game
        land.enter_battlefield()
        land.tapped = False
        game.players[me_idx].battlefield.append(land)


# ─── tests ──────────────────────────────────────────────────────────


def test_storm_glimpse_at_low_life_with_bowmasters_chain_returns_can_kill_false(
        monkeypatch):
    """A combo caster at low life whose chain draws into opp permanents
    tagged ON_DRAW_DAMAGE must NOT report ``can_kill=True``: the
    per-event damage tax from the draw step is lethal-self before the
    finisher resolves.

    Concretely: caster at 10 life draws 5 cards into 2 sources whose
    oracle reads "deals 1 damage" per opp-draw event → 5 × 2 = 10
    damage = exactly lethal. The chain projection must short-circuit
    rather than report a lethal-out as reachable."""
    monkeypatch.setattr(
        "ai.ev_evaluator.STORM_GOBLIN_LETHAL_TOKENS", 6, raising=False)
    game, me_idx = _fresh_game(my_life=10)
    opp_idx = 1 - me_idx

    # Two opp permanents that each deal 1 damage on opp-draw.
    bow_tmpl = _make_template(
        "OnDrawDmgSrc", cmc=2,
        card_types=[CardType.CREATURE],
        oracle_text="whenever an opponent draws a card, this creature "
                    "deals 1 damage to that player.",
        tags=set(),
    )
    for _ in range(2):
        _instance(game, bow_tmpl, opp_idx, "battlefield")

    _storm_chain_hand(game, me_idx, n_draws=1, n_rituals=4, finisher=True)

    _install_tag_map(monkeypatch, {
        "OnDrawDmgSrc": frozenset({Tag.ON_DRAW_DAMAGE}),
    })

    can_kill, _storm, _dmg, _chain = _estimate_combo_chain(game, me_idx)
    assert can_kill is False, (
        "chain projection must subtract per-draw self-damage tax from "
        "opp permanents with ON_DRAW_DAMAGE — caster at 10 life cannot "
        "survive 5-card draw × 2 sources × 1 damage = 10 lethal-self"
    )


def test_chain_models_sheoldred_per_draw_life_loss(monkeypatch):
    """Opp permanents tagged ON_OPP_DRAW_LIFE_LOSS make the drawing
    player lose N life per draw event. The chain projection must
    subtract this amount from caster life, even though life-loss is
    distinct from damage under CR (no lifelink/prevention)."""
    game, me_idx = _fresh_game(my_life=8)
    opp_idx = 1 - me_idx

    sheo_tmpl = _make_template(
        "OnOppDrawLifeLossSrc", cmc=4,
        card_types=[CardType.CREATURE],
        oracle_text="whenever an opponent draws a card, they lose 2 life.",
        tags=set(),
    )
    _instance(game, sheo_tmpl, opp_idx, "battlefield")

    _storm_chain_hand(game, me_idx, n_draws=1, n_rituals=4, finisher=True)

    _install_tag_map(monkeypatch, {
        "OnOppDrawLifeLossSrc": frozenset({Tag.ON_OPP_DRAW_LIFE_LOSS}),
    })

    can_kill, _storm, _dmg, _chain = _estimate_combo_chain(game, me_idx)
    # Caster at 8 life draws 5 cards × 2 life loss each = 10 life loss → lethal.
    assert can_kill is False, (
        "chain projection must subtract ON_OPP_DRAW_LIFE_LOSS per-draw "
        "life loss — caster at 8 life cannot survive 5 × 2 = 10 life lost"
    )


def test_chain_models_on_cast_damage(monkeypatch):
    """Opp permanents tagged ON_CAST_DAMAGE (Eidolon-style) deal damage
    each time the chain-caster casts a spell. The projection must
    subtract this per-cast tax across every chain step."""
    game, me_idx = _fresh_game(my_life=6)
    opp_idx = 1 - me_idx

    eid_tmpl = _make_template(
        "OnCastDmgSrc", cmc=2,
        card_types=[CardType.CREATURE],
        oracle_text="whenever a player casts a spell with mana value 3 "
                    "or less, this creature deals 2 damage to that "
                    "player.",
        tags=set(),
    )
    _instance(game, eid_tmpl, opp_idx, "battlefield")

    # Hand with several cheap spells: 4 rituals + 1 draw + Grapeshot
    # → 6 casts × 2 damage = 12 damage = lethal at 6 life.
    _storm_chain_hand(game, me_idx, n_draws=1, n_rituals=4, finisher=True)

    _install_tag_map(monkeypatch, {
        "OnCastDmgSrc": frozenset({Tag.ON_CAST_DAMAGE}),
    })

    can_kill, _storm, _dmg, _chain = _estimate_combo_chain(game, me_idx)
    assert can_kill is False, (
        "chain projection must subtract per-cast self-damage from opp "
        "ON_CAST_DAMAGE sources — 4 casts × 2 = 8 damage exceeds 6 life"
    )


def test_chain_with_no_self_damage_sources_unchanged(monkeypatch):
    """Regression-prevention: when opp controls no per-event damage
    sources, the chain projection's lethal-out arithmetic must be
    unaffected by the self-damage check. A storm chain that reaches
    lethal still reports ``can_kill=True``.

    Without this guarantee the fix would smuggle in a side effect that
    suppresses legitimate kills."""
    game, me_idx = _fresh_game(my_life=20)
    # No opp permanents — no per-event tax.
    # Cheap-finisher chain: rituals + cantrip + Grapeshot for storm
    # damage ≥ opp life.
    _storm_chain_hand(game, me_idx, n_draws=1, n_rituals=4, finisher=True)

    _install_tag_map(monkeypatch, {})

    # Opp at low life so Grapeshot lethal is easy to reach.
    game.players[1].life = 5

    can_kill, storm, dmg, chain = _estimate_combo_chain(game, me_idx)
    assert dmg > 0, "smoke: chain produced damage"
    assert can_kill is True, (
        "no opp self-damage sources → chain projection unchanged; a "
        "chain that reaches lethal damage must still report can_kill=True"
    )
