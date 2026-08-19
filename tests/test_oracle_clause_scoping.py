"""E5 — clause-level scoping of oracle-text predicates.

Finding E5 (docs/diagnostics/2026-07-05_calibration_probe_findings.md):
predicates of the form ``'enters' in text and 'deals N damage' in text``
test the WHOLE oracle text, not the clause containing the trigger. Under
CR 603.1 a triggered ability's condition and effect share one ABILITY
(one oracle paragraph — the effect may span several sentences of it),
so multi-ability cards can false-positive (trigger word in one ability,
effect verb in another) and amount-extraction regexes can read the
number from the wrong clause.

Two scopes are pinned here:
  * ability-paragraph scope (newline-split) for trigger→effect
    predicates — a damage verb on a DIFFERENT line must not satisfy an
    "enters"-trigger predicate, but a multi-sentence effect within one
    paragraph must keep matching;
  * sentence scope (period/newline-split) for amount extraction — with
    two "deals N damage" clauses, the amount comes from the sentence
    carrying the dispatching trigger phrase.

These tests are rule-phrased: every predicate test uses synthetic
oracle text, never a real card name. The shared primitive under test
is ``engine/oracle_clauses.py``; the converted call sites are
``engine/oracle_parser.py`` (legacy tagger), ``engine/oracle_resolver.py``
(spell / attack / dies triggers), ``ai/ev_evaluator.py`` (this-turn-value
fallbacks) and ``engine/zone_transfer.py`` (on-draw amount parse).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState
from engine.mana import ManaCost
from engine.oracle_clauses import (
    ability_containing,
    any_ability_with,
    any_clause_with,
    clause_containing,
    split_abilities,
    split_clauses,
)
from engine.oracle_parser import parse_has_sacrifice_for_damage


# ─── helpers ────────────────────────────────────────────────────────


def _fresh_game() -> GameState:
    return GameState(rng=random.Random(0))


def _make_card(game, name: str, oracle: str, controller: int = 0,
               card_types=None, zone: str = "battlefield",
               power=2, toughness=2) -> CardInstance:
    """Synthetic card whose only identity is its oracle text."""
    tmpl = CardTemplate(
        name=name,
        card_types=card_types or [CardType.CREATURE],
        mana_cost=ManaCost(generic=1),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle,
        tags=set(),
        has_sacrifice_for_damage=parse_has_sacrifice_for_damage(oracle),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    return card


def _stock_library(game, controller: int, n: int = 3):
    for i in range(n):
        game.players[controller].library.append(
            _make_card(game, f"LibraryFiller{i}", "", controller,
                       zone="library"))


# ─── split_clauses primitive ────────────────────────────────────────


def test_split_clauses_splits_on_periods():
    assert split_clauses("First clause. Second clause.") == [
        "First clause", "Second clause"]


def test_split_clauses_splits_on_newlines():
    assert split_clauses("Ability one\nAbility two") == [
        "Ability one", "Ability two"]


def test_split_clauses_collapses_mixed_terminator_runs():
    assert split_clauses("A.\n\nB.C") == ["A", "B", "C"]


def test_split_clauses_empty_and_whitespace_only():
    assert split_clauses("") == []
    assert split_clauses("   \n . ") == []


def test_any_clause_with_requires_single_clause_cooccurrence():
    text = "When this creature enters, scry 1. Whenever you attack, draw a card."
    assert any_clause_with(text, "enters", "scry")
    assert not any_clause_with(text, "enters", "draw")


def test_any_clause_with_is_case_insensitive():
    assert any_clause_with("When this ENTERS, draw a card.", "enters", "draw")


def test_split_abilities_splits_on_newlines_only():
    text = ("When this enters, reveal a card. Exile it.\nFlying\n"
            "Cycling {2}")
    assert split_abilities(text) == [
        "When this enters, reveal a card. Exile it.", "Flying",
        "Cycling {2}"]


def test_any_ability_with_multi_sentence_ability_matches():
    """A trigger and an effect verb in DIFFERENT sentences of the SAME
    paragraph belong to one ability and must co-match."""
    text = ("When this creature enters, target opponent reveals their "
            "hand. You choose a nonland card from it and exile that card.\n"
            "Whenever you cast a spell, scry 1.")
    assert any_ability_with(text, "enters", "exile")
    assert not any_ability_with(text, "enters", "scry")


def test_clause_containing_returns_first_matching_clause():
    text = "When this enters, scry 1. It deals 3 damage to any target."
    assert clause_containing(text, r"deals?\s+\d+\s+damage") == (
        "It deals 3 damage to any target")
    assert clause_containing(text, r"annihilator") is None


def test_ability_containing_returns_first_matching_paragraph():
    text = ("When this enters, scry 1. It deals 3 damage to any target.\n"
            "Flying")
    assert ability_containing(text, r"deals?\s+\d+\s+damage") == (
        "When this enters, scry 1. It deals 3 damage to any target.")
    assert ability_containing(text, r"annihilator") is None


# ─── legacy tagger: etb_value must be clause-scoped ─────────────────


def test_etb_trigger_and_damage_verb_in_separate_clauses_not_tagged_etb_value():
    """'enters' in sentence 1 + a value verb only in sentence 2 →
    the card has NO ETB-value ability; the tag must not be derived."""
    from engine.oracle_parser import derive_tags_from_oracle
    oracle = ("When this creature enters, scry 1.\n"
              "Whenever you cast a noncreature spell, this creature deals "
              "1 damage to each opponent.")
    tags = derive_tags_from_oracle(oracle, set(), set(), set(), power=1)
    assert "etb_value" not in tags


def test_etb_trigger_with_value_verb_in_same_clause_tagged_etb_value():
    from engine.oracle_parser import derive_tags_from_oracle
    oracle = "When this creature enters, it deals 2 damage to any target."
    tags = derive_tags_from_oracle(oracle, set(), set(), set(), power=1)
    assert "etb_value" in tags


def test_etb_value_multi_sentence_ability_keeps_tag():
    """An ETB effect spanning several sentences of ONE paragraph is one
    ability; the value verb in a later sentence still tags."""
    from engine.oracle_parser import derive_tags_from_oracle
    oracle = ("When this creature enters, target opponent reveals their "
              "hand. You choose a nonland card from it and exile that "
              "card.")
    tags = derive_tags_from_oracle(oracle, set(), set(), set(), power=1)
    assert "etb_value" in tags


# ─── dies trigger: dies + draw must share a clause ──────────────────


def test_dies_and_draw_in_separate_clauses_does_not_fire_dies_draw():
    from engine.oracle_resolver import resolve_dies_trigger
    game = _fresh_game()
    _stock_library(game, controller=0)
    card = _make_card(
        game, "SyntheticDiesNoDraw",
        "When this creature dies, exile it.\n"
        "Whenever an opponent casts a spell, draw a card.",
        controller=0, zone="graveyard")
    hand_before = len(game.players[0].hand)
    resolve_dies_trigger(game, card, 0)
    assert len(game.players[0].hand) == hand_before, (
        "dies-trigger draw fired although 'dies' and 'draw' live in "
        "different clauses")


def test_dies_and_draw_in_same_clause_fires_dies_draw():
    from engine.oracle_resolver import resolve_dies_trigger
    game = _fresh_game()
    _stock_library(game, controller=0)
    card = _make_card(
        game, "SyntheticDiesDraw",
        "When this creature dies, draw a card.",
        controller=0, zone="graveyard")
    hand_before = len(game.players[0].hand)
    resolve_dies_trigger(game, card, 0)
    assert len(game.players[0].hand) == hand_before + 1


# ─── attack trigger: attacks + damage must share a clause ───────────


def test_attacks_and_damage_in_separate_clauses_does_not_fire_attack_damage():
    from engine.oracle_resolver import resolve_attack_trigger
    game = _fresh_game()
    attacker = _make_card(
        game, "SyntheticAttackerNoBurn",
        "Whenever this creature attacks, creatures you control get +1/+0 "
        "until end of turn.\n"
        "When this creature enters, it deals 3 damage to any target.",
        controller=0)
    opp_life_before = game.players[1].life
    resolve_attack_trigger(game, attacker, 0)
    assert game.players[1].life == opp_life_before, (
        "attack-trigger damage fired from an ETB clause's damage verb")


def test_attacks_and_damage_in_same_clause_fires_attack_damage():
    from engine.oracle_resolver import resolve_attack_trigger
    game = _fresh_game()
    attacker = _make_card(
        game, "SyntheticAttackerBurn",
        "Whenever this creature attacks, it deals 2 damage to any target.",
        controller=0)
    opp_life_before = game.players[1].life
    resolve_attack_trigger(game, attacker, 0)
    assert game.players[1].life == opp_life_before - 2


def test_attack_lifegain_in_second_sentence_of_same_ability_still_fires():
    """An attack trigger whose effect continues in a second sentence of
    the same paragraph ('… loses 3 life. You gain 3 life.') is ONE
    ability; the lifegain must still fire with its own amount."""
    from engine.oracle_resolver import resolve_attack_trigger
    game = _fresh_game()
    attacker = _make_card(
        game, "SyntheticAttackerDrainer",
        "Whenever this creature attacks, target opponent loses 3 life. "
        "You gain 3 life.",
        controller=0)
    my_life_before = game.players[0].life
    resolve_attack_trigger(game, attacker, 0)
    assert game.players[0].life == my_life_before + 3


# ─── spell resolution: reveal-hand/discard template is window-scoped ─


def test_reveal_and_discard_in_consecutive_sentences_discards():
    """The three-sentence template 'reveals hand / choose / discards'
    is one effect; it must keep firing."""
    from engine.oracle_resolver import resolve_spell_from_oracle
    game = _fresh_game()
    victim_card = _make_card(game, "VictimSpell",
                             "Counter target spell.", controller=1,
                             card_types=[CardType.INSTANT], zone="hand")
    game.players[1].hand.append(victim_card)
    spell = _make_card(
        game, "SyntheticDiscardSpell",
        "Target player reveals their hand. You choose a nonland card "
        "from it. That player discards that card.",
        controller=0, card_types=[CardType.SORCERY], zone="stack")
    assert resolve_spell_from_oracle(game, spell, 0)
    assert victim_card not in game.players[1].hand
    assert victim_card in game.players[1].graveyard


def test_reveal_and_discard_in_separate_abilities_does_not_discard():
    """'reveals … hand' in one ability and 'discard' in a SEPARATE
    ability paragraph is NOT the discard template."""
    from engine.oracle_resolver import resolve_spell_from_oracle
    game = _fresh_game()
    victim_card = _make_card(game, "VictimSpell2",
                             "Counter target spell.", controller=1,
                             card_types=[CardType.INSTANT], zone="hand")
    game.players[1].hand.append(victim_card)
    spell = _make_card(
        game, "SyntheticRevealOnlySpell",
        "Target player reveals their hand. You gain 1 life.\n"
        "Madness {1} (If you discard this card, discard it into exile.)",
        controller=0, card_types=[CardType.SORCERY], zone="stack")
    resolve_spell_from_oracle(game, spell, 0)
    assert victim_card in game.players[1].hand, (
        "discard fired although 'reveals'/'hand' and 'discard' live in "
        "different ability paragraphs")


# ─── amount extraction: parse from the trigger's clause ─────────────


def test_on_draw_amount_parsed_from_draw_clause_not_first_damage_clause():
    """Two 'deals N damage' clauses (ETB clause with one amount, on-draw
    clause with another): the on-draw handler must read the amount from
    the clause carrying the draw-trigger phrase."""
    from engine.zone_transfer import _parse_amount_or_assert
    game = _fresh_game()
    card = _make_card(
        game, "SyntheticTwoDamageClauses",
        "When this creature enters, it deals 3 damage to any target.\n"
        "Whenever an opponent draws a card, this creature deals 1 damage "
        "to that player.",
        controller=0)
    amount = _parse_amount_or_assert(
        card, r"deals?\s+(\d+)\s+damage", "ON_DRAW_DAMAGE",
        trigger_phrase="draw")
    assert amount == 1, (
        f"amount parsed from the wrong clause (got {amount}, want the "
        f"on-draw clause's 1)")


def test_on_draw_amount_single_match_whole_text_fallback_unchanged():
    """Exactly one regex match anywhere keeps the legacy whole-text
    parse — current tagged cards stay byte-identical."""
    from engine.zone_transfer import _parse_amount_or_assert
    game = _fresh_game()
    card = _make_card(
        game, "SyntheticSingleDamageClause",
        "Whenever an opponent draws a card, this creature deals 2 damage "
        "to that player.",
        controller=0)
    amount = _parse_amount_or_assert(
        card, r"deals?\s+(\d+)\s+damage", "ON_DRAW_DAMAGE",
        trigger_phrase="draw")
    assert amount == 2


def test_on_draw_amount_assert_on_desync_contract_kept():
    from engine.zone_transfer import _parse_amount_or_assert
    game = _fresh_game()
    card = _make_card(game, "SyntheticNoDamageClause",
                      "Whenever an opponent draws a card, you gain 1 life.",
                      controller=0)
    with pytest.raises(AssertionError):
        _parse_amount_or_assert(
            card, r"deals?\s+(\d+)\s+damage", "ON_DRAW_DAMAGE",
            trigger_phrase="draw")


# ─── AI this-turn-value fallbacks are clause-scoped ─────────────────


def test_immediate_effect_fallback_ignores_damage_verb_outside_enters_clause():
    """A permanent whose ETB clause is value-free must not be credited
    immediate value because a damage verb appears in a later,
    unrelated activated-ability clause."""
    from ai.ev_evaluator import _has_immediate_effect
    game = _fresh_game()
    card = _make_card(
        game, "SyntheticDelayedEngine",
        "When this enchantment enters, tap target artifact.\n"
        "Sacrifice a creature: this enchantment deals 2 damage to any "
        "target.",
        controller=0, card_types=[CardType.ENCHANTMENT], zone="hand",
        power=None, toughness=None)
    assert not _has_immediate_effect(card), (
        "'enters' + 'deal' matched across clause boundaries; this card "
        "is a delayed-value engine (sacrifice outlet), not ETB value")


def test_immediate_effect_fallback_keeps_same_clause_etb_damage():
    from ai.ev_evaluator import _has_immediate_effect
    game = _fresh_game()
    card = _make_card(
        game, "SyntheticEtbBurnPermanent",
        "When this enchantment enters, it deals 2 damage to any target.\n"
        "Sacrifice a creature: this enchantment deals 2 damage to any "
        "target.",
        controller=0, card_types=[CardType.ENCHANTMENT], zone="hand",
        power=None, toughness=None)
    assert _has_immediate_effect(card)


def test_self_etb_effect_requires_effect_verb_in_trigger_clause():
    from ai.ev_evaluator import _has_self_etb_effect
    oracle = ("when this creature enters, tap up to one target creature.\n"
              "whenever this creature attacks, draw a card.")
    assert not _has_self_etb_effect(oracle), (
        "material-effect keyword found outside the self-ETB clause")


def test_self_etb_effect_same_clause_verb_still_detected():
    from ai.ev_evaluator import _has_self_etb_effect
    assert _has_self_etb_effect(
        "when this creature enters, draw a card.")
