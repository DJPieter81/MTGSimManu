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


# ═════════════════════════════════════════════════════════════════════
# 2. Engine resolution — zone funnel, riders, target legality
# ═════════════════════════════════════════════════════════════════════


def _spell_in_hand(game, controller, oracle, name="LD Spell"):
    tmpl = _tmpl(name, oracle)
    card = CardInstance(template=tmpl, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(),
                        zone="hand")
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _library_card(game, owner, name="Test Basic", basic=True,
                  produces=("R",), land=True):
    tmpl = _tmpl(name, "",
                 types=(CardType.LAND,) if land else (CardType.SORCERY,),
                 supertypes=([Supertype.BASIC] if basic else []),
                 subtypes=(["Mountain"] if basic and land else []),
                 produces=produces, cost=ManaCost())
    card = CardInstance(template=tmpl, owner=owner, controller=owner,
                        instance_id=game.next_instance_id(),
                        zone="library")
    card._game_state = game
    game.players[owner].library.append(card)
    return card


def _resolve(game, spell, controller, targets):
    from engine.oracle_resolver import resolve_spell_from_oracle
    return resolve_spell_from_oracle(game, spell, controller, targets)


class TestLandDestructionResolution:

    def test_destroyed_land_moves_battlefield_to_graveyard_via_funnel(self):
        game = _fresh_game()
        land = _land(game, 1)
        spell = _spell_in_hand(game, 0, "Destroy target land.")
        handled = _resolve(game, spell, 0, [land.instance_id])
        assert handled is True
        assert land.zone == "graveyard"
        assert land in game.players[1].graveyard
        assert land not in game.players[1].battlefield

    def test_search_basic_rider_replaces_through_library_search_funnel(self):
        game = _fresh_game()
        land = _land(game, 1, name="Nonbasic Peak")
        basic = _library_card(game, 1)
        # A caster-side permanent watching for opponent library searches
        # proves the rider routes through the search funnel (its trigger
        # must fire), not an ad-hoc library mutation.
        watcher = _on_battlefield(game, _tmpl(
            "Search Watcher",
            "Whenever an opponent searches their library, put a +1/+1 "
            "counter on this creature.",
            types=(CardType.CREATURE,)), 0)
        spell = _spell_in_hand(game, 0, (
            "Destroy target land. Its controller may search their library "
            "for a basic land card, put it onto the battlefield tapped, "
            "then shuffle.\nDraw a card."))
        _library_card(game, 0, name="Caster Deck Card", land=False)
        hand_before = len(game.players[0].hand)
        _resolve(game, spell, 0, [land.instance_id])
        assert land.zone == "graveyard"
        assert basic.zone == "battlefield"
        assert basic in game.players[1].battlefield
        assert basic.tapped is True, "rider says the basic enters tapped"
        assert game.players[1].library_searches_this_game == 1
        assert watcher.plus_counters == 1, (
            "the replacement search must fire opponent search triggers "
            "(library-search funnel, not ad-hoc mutation)")
        # Caster-draw rider went through the draw funnel.
        assert len(game.players[0].hand) == hand_before + 1

    def test_search_rider_basic_enters_untapped_when_text_says_untapped(self):
        game = _fresh_game()
        land = _land(game, 1)
        basic = _library_card(game, 1)
        spell = _spell_in_hand(game, 0, (
            "Destroy target land. Its controller may search their library "
            "for a basic land card, put it onto the battlefield, then "
            "shuffle.\nDraw a card."))
        _library_card(game, 0, name="Caster Deck Card", land=False)
        _resolve(game, spell, 0, [land.instance_id])
        assert basic.zone == "battlefield"
        assert basic.tapped is False

    def test_damage_rider_fires_only_when_destroyed_land_was_nonbasic(self):
        oracle = ("Destroy target land. If that land was nonbasic, LD "
                  "Punish deals 2 damage to the land's controller.")
        # Nonbasic target: rider damage lands on its controller.
        game = _fresh_game()
        nonbasic = _land(game, 1, name="Nonbasic Peak", basic=False)
        spell = _spell_in_hand(game, 0, oracle, name="LD Punish")
        _resolve(game, spell, 0, [nonbasic.instance_id])
        assert game.players[1].life == 18
        # Basic target: condition unmet, no damage.
        game2 = _fresh_game()
        basic = _land(game2, 1, name="Basic Peak", basic=True)
        spell2 = _spell_in_hand(game2, 0, oracle, name="LD Punish")
        _resolve(game2, spell2, 0, [basic.instance_id])
        assert basic.zone == "graveyard"
        assert game2.players[1].life == 20

    def test_indestructible_land_survives_and_riders_do_not_fire(self):
        # CR 702.12b + CR 608.2c: destroy does nothing, and the rider
        # chain must not execute against a surviving land.
        game = _fresh_game()
        land = _land(game, 1, keywords={Keyword.INDESTRUCTIBLE})
        spell = _spell_in_hand(game, 0, (
            "Destroy target land. LD Sting deals 2 damage to that "
            "land's controller."), name="LD Sting")
        handled = _resolve(game, spell, 0, [land.instance_id])
        assert handled is True
        assert land.zone == "battlefield"
        assert game.players[1].life == 20

    def test_hexproof_land_is_not_a_legal_target_for_opponents_spell(self):
        # CR 702.11d via target_solver — the solver owns target legality.
        game = _fresh_game()
        land = _land(game, 1, keywords={Keyword.HEXPROOF})
        spell = _spell_in_hand(game, 0, "Destroy target land.")
        _resolve(game, spell, 0, [land.instance_id])
        assert land.zone == "battlefield"

    def test_target_gone_at_resolution_means_no_effect(self):
        # CR 608.2b analog at the resolver level: a stale instance id
        # must not redirect onto a different land.
        game = _fresh_game()
        survivor = _land(game, 1, name="Survivor Peak")
        spell = _spell_in_hand(game, 0, "Destroy target land.")
        handled = _resolve(game, spell, 0, [987654])
        assert handled is True
        assert survivor.zone == "battlefield"

    def test_engine_fallback_picks_an_opponent_land_never_our_own(self):
        # No cast-time target (synthetic call sites): the deterministic
        # engine fallback denies the opponent, not the caster.
        game = _fresh_game()
        mine = _land(game, 0, name="My Peak")
        theirs = _land(game, 1, name="Their Peak")
        spell = _spell_in_hand(game, 0, "Destroy target land.")
        _resolve(game, spell, 0, [])
        assert mine.zone == "battlefield"
        assert theirs.zone == "graveyard"

    def test_compound_spell_destroys_a_chosen_artifact_target(self):
        # The artifact-or-land form must honor an artifact target through
        # the same funnel.
        game = _fresh_game()
        artifact = _on_battlefield(game, _tmpl(
            "Test Trinket", "", types=(CardType.ARTIFACT,),
            cost=ManaCost(generic=1)), 1)
        spell = _spell_in_hand(game, 0, (
            "Destroy target artifact or land. It can't be regenerated."))
        _resolve(game, spell, 0, [artifact.instance_id])
        assert artifact.zone == "graveyard"


# ═════════════════════════════════════════════════════════════════════
# 3. AI valuation — mana-denial EV and scarcest-source targeting
# ═════════════════════════════════════════════════════════════════════


def _snap(game, idx):
    from ai.ev_evaluator import snapshot_from_game
    return snapshot_from_game(game, idx)


class TestLandDenialValuation:

    def test_target_selection_prefers_opponents_sole_color_source(self):
        from ai.land_denial import choose_land_denial_target
        game = _fresh_game()
        _land(game, 1, name="Red Source A", produces=("R",))
        _land(game, 1, name="Red Source B", produces=("R",))
        sole_black = _land(game, 1, name="Sole Black Source",
                           produces=("B",))
        tmpl = _tmpl("LD Plain", "Destroy target land.")
        chosen = choose_land_denial_target(tmpl, game, 0, _snap(game, 0))
        assert chosen is sole_black, (
            "denying the opponent's only source of a color maximizes "
            "their deficit — a redundant source must not be picked")

    def test_denial_value_declines_as_opponent_mana_exceeds_curve(self):
        from ai.land_denial import land_denial_value
        tmpl = _tmpl("LD Plain", "Destroy target land.")

        def _state(n_lands):
            game = _fresh_game()
            for i in range(n_lands):
                _land(game, 1, name=f"Peak {i}", produces=("R",))
            # Opponent deck curve tops at 4 — mana is a binding
            # constraint only while their lands sit below that.
            _library_card(game, 1, name="Opp Curve Top", land=False)
            game.players[1].library[-1].template.mana_cost = ManaCost(
                generic=3, red=1)
            return game

        developing = _state(2)
        flooded = _state(8)
        v_developing = land_denial_value(tmpl, developing, 0,
                                         _snap(developing, 0))
        v_flooded = land_denial_value(tmpl, flooded, 0,
                                      _snap(flooded, 0))
        assert v_developing > v_flooded, (
            "denying the Nth land matters by what it delays — a flooded "
            "opponent loses nothing")
        assert v_flooded == pytest.approx(0.0), (
            "past the curve top the tempo term must vanish (identical "
            "redundant sources leave no scarcity premium either)")

    def test_ld_spell_requires_a_target_to_be_enumerated(self):
        from ai.ev_player import EVPlayer
        game = _fresh_game()
        spell = _spell_in_hand(game, 0, "Destroy target land.")
        ai = EVPlayer(player_idx=0, deck_name="Boros Ponza",
                      rng=random.Random(0))
        assert ai._spell_requires_targets(spell) is True

    def test_ld_spell_counts_as_immediate_interaction_signal(self):
        # The deferral gate must not hold LD spells forever — that is
        # the original dead-card bug in different clothes.
        from ai.ev_evaluator import _is_immediate_interaction
        tmpl = _tmpl("LD Plain", "Destroy target land.")
        assert _is_immediate_interaction(
            tmpl.oracle_text.lower(), set(), tmpl) is True


class TestLandDenialEndToEnd:

    def test_denial_spell_is_cast_on_curve_in_a_denial_deck_state(self):
        """Seeded Ponza-style state: the LD spell must actually be cast,
        and at the opponent's scarcest color source."""
        from engine.card_database import CardDatabase
        from ai.ev_player import EVPlayer
        db = CardDatabase()

        def _real(game, name, controller, zone="battlefield",
                  untap=True):
            t = db.get_card(name)
            assert t is not None, f"missing {name}"
            c = CardInstance(template=t, owner=controller,
                             controller=controller,
                             instance_id=game.next_instance_id(),
                             zone=zone)
            c._game_state = game
            if zone == "battlefield":
                c.enter_battlefield()
                c.summoning_sick = False
                if untap:
                    c.tapped = False
                game.players[controller].battlefield.append(c)
            else:
                getattr(game.players[controller], zone).append(c)
            return c

        game = _fresh_game()
        game.turn_number = 5   # engine turn counter (both players)
        me, opp = game.players[0], game.players[1]
        me.deck_name = "Boros Ponza"
        opp.deck_name = "Dimir Midrange"
        for i in range(3):
            _real(game, "Mountain", 0)
        spell = _real(game, "Cleansing Wildfire", 0, zone="hand")
        assert spell.template.destroys_target_land is True
        # Opponent developing: two redundant U sources and a sole
        # B source, with unreached curve toppers still in the deck.
        _real(game, "Island", 1)
        _real(game, "Island", 1)
        sole_b = _real(game, "Watery Grave", 1)
        for name in ("Murktide Regent", "Counterspell", "Psychic Frog"):
            _real(game, name, 1, zone="library")
        for _ in range(4):
            _real(game, "Mountain", 0, zone="library")

        ai = EVPlayer(player_idx=0, deck_name="Boros Ponza",
                      rng=random.Random(0))
        decision = ai.decide_main_phase(game)
        assert decision is not None, (
            "a castable land-destruction spell with real denial value "
            "must produce a play, not a pass")
        action, card, targets = decision
        assert action == "cast_spell" and card.instance_id == spell.instance_id, (
            f"the denial spell must be cast on curve, got {action} "
            f"{getattr(card, 'name', card)}")
        assert targets == [sole_b.instance_id], (
            "the chosen target must be the opponent's scarcest color "
            "source (their only B source)")
