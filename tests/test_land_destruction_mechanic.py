"""Land-destruction mechanic — parser typing, resolution funnel, AI valuation.

Class under test (2026-08-27 Dimir root-cause doc, "LD mechanic hole"):
every Modern sorcery/instant whose resolution text is "Destroy target
land" plus a supported rider — the plain form, the artifact-or-land
compound form, the opponent-searches-basic replacement rider, the
conditional damage-to-controller rider, and the caster-draw rider.
15 spells in the loaded DB parse into the class; unsupported riders
refuse the whole card (never half-executed).  Activated / triggered
land destruction (sac-lands, ETB triggers) is a later tranche and must
NOT be classified by the spell-shaped parser.

Rules pinned here:
  * parse-once typing: the oracle clause becomes typed CardTemplate
    fields at load time; no runtime oracle inspection.
  * cast legality: the compound artifact-or-land target requirement is
    castable off either permanent type (CR 601.2c).
  * resolution: destruction routes through the zone funnel
    (battlefield -> graveyard), riders through the library-search and
    damage funnels; indestructible (CR 702.12b) and resolution-time
    target legality (CR 608.2b) are respected.
  * AI: denying the Nth land is valued by the opponent's remaining mana
    development; the chosen target is the opponent's scarcest color
    source.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import (CardInstance, CardTemplate, CardType, Keyword,
                          Supertype)
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.oracle_parser import parse_land_destruction


# ── helpers ──────────────────────────────────────────────────────────────


def _tmpl(name, oracle, types=(CardType.SORCERY,), supertypes=(),
          subtypes=(), produces=(), keywords=(), cost=None):
    return CardTemplate(
        name=name,
        card_types=list(types),
        mana_cost=cost if cost is not None else ManaCost(generic=1, red=1),
        supertypes=list(supertypes),
        subtypes=list(subtypes),
        power=None, toughness=None, loyalty=None,
        keywords=set(keywords),
        abilities=[],
        color_identity=set(),
        produces_mana=list(produces),
        enters_tapped=False,
        oracle_text=oracle,
        tags=set(),
    )


def _fresh_game() -> GameState:
    g = GameState(rng=random.Random(7))
    g.active_player = 0
    g.priority_player = 0
    g.current_phase = Phase.MAIN1
    return g


def _on_battlefield(game, tmpl, controller):
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(),
                        zone="battlefield")
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _land(game, controller, name="Test Peak", produces=("R",),
          basic=False, keywords=()):
    tmpl = _tmpl(name, "", types=(CardType.LAND,),
                 supertypes=([Supertype.BASIC] if basic else []),
                 subtypes=(["Mountain"] if basic else []),
                 produces=produces, keywords=keywords,
                 cost=ManaCost())
    return _on_battlefield(game, tmpl, controller)


# ═════════════════════════════════════════════════════════════════════
# 1. Parser — parse-once typed fields
# ═════════════════════════════════════════════════════════════════════


class TestLandDestructionParsing:

    def test_plain_destroy_target_land_sets_typed_field(self):
        t = _tmpl("LD Plain", "Destroy target land.")
        assert t.destroys_target_land is True
        d = t.land_destruction_data
        assert d is not None
        assert d["can_target_artifact"] is False
        assert d["nonbasic_only"] is False
        assert d["rider_search_basic"] is False
        assert d["rider_damage"] == 0
        assert d["rider_caster_draws"] == 0

    def test_search_basic_and_draw_riders_parse_typed(self):
        t = _tmpl("LD Replace", (
            "Destroy target land. Its controller may search their library "
            "for a basic land card, put it onto the battlefield tapped, "
            "then shuffle.\nDraw a card."))
        d = t.land_destruction_data
        assert d is not None
        assert d["rider_search_basic"] is True
        assert d["rider_search_basic_tapped"] is True
        assert d["rider_caster_draws"] == 1

    def test_search_basic_rider_untapped_variant(self):
        t = _tmpl("LD Replace Untapped", (
            "Destroy target land. Its controller may search their library "
            "for a basic land card, put it onto the battlefield, then "
            "shuffle.\nDraw a card."))
        d = t.land_destruction_data
        assert d is not None
        assert d["rider_search_basic"] is True
        assert d["rider_search_basic_tapped"] is False

    def test_nonbasic_conditional_damage_rider_parses_amount(self):
        t = _tmpl("LD Punish", (
            "Destroy target land. If that land was nonbasic, LD Punish "
            "deals 2 damage to the land's controller."))
        d = t.land_destruction_data
        assert d is not None
        assert d["rider_damage"] == 2
        assert d["rider_damage_nonbasic_only"] is True

    def test_unconditional_damage_rider_parses_amount(self):
        t = _tmpl("LD Sting", (
            "Destroy target land. LD Sting deals 2 damage to that "
            "land's controller."))
        d = t.land_destruction_data
        assert d is not None
        assert d["rider_damage"] == 2
        assert d["rider_damage_nonbasic_only"] is False

    def test_artifact_or_land_compound_mode_parses(self):
        t = _tmpl("LD Compound", (
            "Destroy target artifact or land. It can't be regenerated."))
        d = t.land_destruction_data
        assert d is not None
        assert d["can_target_artifact"] is True

    def test_unsupported_rider_refuses_whole_card(self):
        # "never half-executed": an unknown rider sentence must leave the
        # card entirely unclassified, not destroy-without-rider.
        t = _tmpl("LD Unknown Rider", (
            "Destroy target land. Its controller discards a card."))
        assert t.destroys_target_land is False
        assert t.land_destruction_data is None

    def test_multi_target_form_refused(self):
        t = _tmpl("LD Two Lands", (
            "Destroy target land you control and target land you "
            "don't control."))
        assert t.destroys_target_land is False

    def test_triggered_land_destruction_not_classified_as_spell_ld(self):
        # ETB-trigger LD (creature class) — later tranche, must refuse.
        oracle = ("Flying\nWhen this creature enters, destroy target "
                  "nonbasic land an opponent controls.")
        assert parse_land_destruction(oracle) is None

    def test_activated_land_destruction_not_classified_as_spell_ld(self):
        # Sac-land activated LD (Ghost Quarter class) — later tranche.
        oracle = ("{T}: Add {C}.\n{T}, Sacrifice this land: Destroy "
                  "target land. Its controller may search their library "
                  "for a basic land card, put it onto the battlefield, "
                  "then shuffle.")
        assert parse_land_destruction(oracle) is None

    def test_keyword_cost_line_does_not_refuse_the_spell(self):
        # A keyword ability line (alt-cast cost) is not resolution text;
        # it must not trip the unknown-sentence refusal.
        t = _tmpl("LD Flashback", "Destroy target land.\nFlashback {3}{R}")
        assert t.destroys_target_land is True

    def test_db_class_coverage_includes_replacement_damage_and_compound(self):
        # The three Ponza-relevant subclasses must all be covered by the
        # loaded DB typing (class check, not card behavior).
        from engine.card_database import CardDatabase
        db = CardDatabase()
        shapes = {"search": 0, "damage": 0, "compound": 0, "plain": 0}
        for t in db.cards.values():
            if not getattr(t, "destroys_target_land", False):
                continue
            d = t.land_destruction_data
            if d["rider_search_basic"]:
                shapes["search"] += 1
            if d["rider_damage"] > 0:
                shapes["damage"] += 1
            if d["can_target_artifact"]:
                shapes["compound"] += 1
            if not (d["rider_search_basic"] or d["rider_damage"]
                    or d["can_target_artifact"]):
                shapes["plain"] += 1
        assert shapes["search"] >= 1, "replacement-basic subclass missing"
        assert shapes["damage"] >= 1, "damage-rider subclass missing"
        assert shapes["compound"] >= 1, "artifact-or-land subclass missing"
        assert sum(shapes.values()) >= 10, (
            f"mechanic class too small: {shapes} — the parser regressed")


class TestLandTargetRequirement:

    def test_compound_artifact_or_land_emits_one_requirement(self):
        from engine import target_solver
        reqs = target_solver.parse(
            "Destroy target artifact or land. It can't be regenerated.")
        compound = [r for r in reqs if r.types == frozenset({"artifact",
                                                             "land"})]
        assert len(compound) == 1
        # The single-type artifact/land patterns must not double-emit.
        assert not any(r.types == frozenset({"artifact"}) for r in reqs)
        assert not any(r.types == frozenset({"land"}) for r in reqs)

    def test_compound_spell_castable_off_a_land_alone(self):
        # CR 601.2c: one legal target suffices — a battlefield with only
        # lands must satisfy the artifact-or-land requirement.
        from engine.target_solver import (has_legal_target_for_spell,
                                          parse as parse_targets)
        game = _fresh_game()
        _land(game, 1)
        reqs = parse_targets(
            "Destroy target artifact or land. It can't be regenerated.")
        assert has_legal_target_for_spell(game, 0, reqs) is True

    def test_plain_land_requirement_castable_when_any_land_exists(self):
        from engine.target_solver import (has_legal_target_for_spell,
                                          parse as parse_targets)
        game = _fresh_game()
        _land(game, 1)
        reqs = parse_targets("Destroy target land.")
        assert has_legal_target_for_spell(game, 0, reqs) is True
