"""An object's colour is its characteristic, never its colour identity
(CR 105.2, 202.2; colour identity, CR 903.4, exists for Commander deck
construction only).

A card's colour comes from the mana symbols in its mana cost, its colour
indicator and its characteristic-defining abilities. Devoid makes a card
colourless (CR 702.114a), and a colourless artifact stays colourless whatever
mana symbols its abilities print. Colour identity also counts the symbols in
rules text, so it is wider:
- Cranial Plating ({2}, equip {B}{B}) has a black identity and no colour;
- Kozilek's Return (devoid, {2}{R}) has a red identity and no colour;
- Blood Crypt (a land) has a black-red identity and no colour.

Every rule that reads "a <colour> card / spell / permanent" or "a colorless
spell" reads the colour: 627 pool cards condition on an object's colour, 22
of them in the registered decks (127 pool cards are devoid). Six engine paths
and two AI paths read the identity instead; the ratchet at the end pins the
count of identity reads in `engine/` and `ai/` at zero.

Card names are fixture carriers; MTGJSON supplies both fields.
"""
from __future__ import annotations

import ast
import pathlib
import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase, StackItem, StackItemType


def _game(active=1):
    g = GameState(rng=random.Random(0))
    g.active_player = active
    g.current_phase = Phase.MAIN1
    return g


def _put(game, card_db, name, owner, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    if zone != "stack":
        getattr(game.players[owner], zone).append(c)
    return c


def _cast_onto_stack(game, card_db, name, owner):
    c = _put(game, card_db, name, owner, "stack")
    item = StackItem(item_type=StackItemType.SPELL, source=c,
                     controller=owner, description=name)
    game.stack.items.append(item)
    return c, item


def test_the_fixture_cards_are_colourless_with_a_coloured_identity(card_db):
    for name in ("Cranial Plating", "Kozilek's Return", "Basking Broodscale",
                 "Haywire Mite", "Blood Crypt"):
        t = card_db.get_card(name)
        assert not t.colors and t.color_identity, name


# ── Engine ──────────────────────────────────────────────────────────

def test_a_colorless_only_counter_counters_a_colorless_spell_of_any_identity(
        card_db):
    """"Counter target ... colorless spell": an artifact whose ability
    prints {B} and a devoid creature are colorless spells."""
    from engine.card_effects import consign_to_memory_resolve
    for name in ("Cranial Plating", "Sowing Mycospawn"):
        g = _game()
        spell, _item = _cast_onto_stack(g, card_db, name, 1)
        consign = _put(g, card_db, "Consign to Memory", 0, "hand")
        consign_to_memory_resolve(g, consign, 0, targets=[spell.instance_id])
        assert not g.stack.items, name
        assert spell in g.players[1].graveyard, name


def test_a_colour_restricted_cost_reduction_reads_the_spells_colour(card_db):
    """"Red spells you cast cost {1} less": a devoid spell with a red
    mana symbol is colorless, so it is not reduced."""
    from engine import rules_query
    g = _game(active=0)
    _put(g, card_db, "Ruby Medallion", 0, "battlefield")
    assert rules_query.cost_delta(g, 0, card_db.get_card("Lightning Bolt")) == 1
    assert rules_query.cost_delta(
        g, 0, card_db.get_card("Kozilek's Return")) == 0


def test_a_search_for_a_coloured_creature_card_never_finds_a_colourless_one(
        card_db):
    from engine.activated_effects import eligible_tutor_targets
    g = _game(active=0)
    lib = [_put(g, card_db, n, 0, "library")
           for n in ("Basking Broodscale", "Haywire Mite", "Primeval Titan")]
    spec = {'types': ['creature'], 'colors': ['G']}
    assert eligible_tutor_targets(lib, spec, x_value=None) == [lib[2]]


def test_a_pact_for_a_green_creature_card_never_finds_a_devoid_one(card_db):
    from engine.card_effects import summoners_pact_resolve
    g = _game(active=0)
    devoid = _put(g, card_db, "Basking Broodscale", 0, "library")
    pact = _put(g, card_db, "Summoner's Pact", 0, "hand")
    summoners_pact_resolve(g, pact, 0)
    assert devoid in g.players[0].library
    assert devoid not in g.players[0].hand


def test_a_red_or_black_permanent_is_one_whose_colour_is_red_or_black(card_db):
    from engine.card_effects import celestial_purge_resolve
    g = _game(active=0)
    plating = _put(g, card_db, "Cranial Plating", 1, "battlefield")
    crypt = _put(g, card_db, "Blood Crypt", 1, "battlefield")
    purge = _put(g, card_db, "Celestial Purge", 0, "hand")
    celestial_purge_resolve(g, purge, 0)
    assert plating in g.players[1].battlefield
    assert crypt in g.players[1].battlefield
    black = _put(g, card_db, "Orcish Bowmasters", 1, "battlefield")
    celestial_purge_resolve(g, purge, 0)
    assert black not in g.players[1].battlefield
    assert plating in g.players[1].battlefield


def test_a_graveyard_sweep_of_black_and_red_cards_reads_their_colour(card_db):
    from engine.card_effects import sanctifier_en_vec_etb
    g = _game(active=0)
    colourless = [_put(g, card_db, n, 1, "graveyard")
                  for n in ("Blood Crypt", "Cranial Plating",
                            "Kozilek's Return")]
    red = _put(g, card_db, "Lightning Bolt", 1, "graveyard")
    sanctifier = _put(g, card_db, "Sanctifier en-Vec", 0, "battlefield")
    sanctifier_en_vec_etb(g, sanctifier, 0)
    assert all(c in g.players[1].graveyard for c in colourless)
    assert red in g.players[1].exile


# ── AI ──────────────────────────────────────────────────────────────

def test_the_ais_pitch_response_reads_the_engines_alternative_cost(card_db):
    """The response enumerator offers a pitch cast exactly when the
    engine's alternative-cost owner has a card to exile: a land is
    colorless whatever its identity, and "if it's not your turn" holds."""
    from ai.response_enumeration import available_responses
    g = _game(active=1)
    fon = _put(g, card_db, "Force of Negation", 0, "hand")
    _put(g, card_db, "Island", 0, "hand")
    _spell, item = _cast_onto_stack(g, card_db, "Thoughtseize", 1)

    def pitched():
        return [c.source for c in available_responses(g, item, controller=0)
                if c.action == "cast_pitch"]

    assert pitched() == []
    _put(g, card_db, "Consign to Memory", 0, "hand")
    assert pitched() == [fon]
    g.active_player = 0
    assert pitched() == []


def test_the_storm_chains_colour_restricted_discount_reads_the_spells_colour(
        card_db):
    from ai.combo_calc import _compute_r_res
    from ai.combo_chain import classify_card
    g = _game(active=0)
    devoid = _put(g, card_db, "Kozilek's Return", 0, "hand")
    red = _put(g, card_db, "Lightning Bolt", 0, "hand")
    assert classify_card(devoid, 5, 1, set()).effective_cost == 3
    assert classify_card(red, 5, 1, set()).effective_cost == 0
    assert _compute_r_res([devoid], 3, 1) == 0


# ── Auditor (CR 105.2) ──────────────────────────────────────────────

def _identity_matcher(real):
    """The defect, restored: a colour-restricted reduction matched on the
    spell's colour identity."""
    def matches(rule, template):
        if rule.get('color'):
            return (any(c.value == rule['color']
                        for c in template.color_identity)
                    and real(dict(rule, color=None), template))
        return real(rule, template)
    return matches


def test_the_audit_records_a_colour_restricted_reduction_on_another_colour(
        card_db, monkeypatch):
    from engine import oracle_resolver, rules_audit, rules_query
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    g = _game(active=0)
    _put(g, card_db, "Ruby Medallion", 0, "battlefield")
    monkeypatch.setattr(oracle_resolver, "_cost_rule_applies",
                        _identity_matcher(oracle_resolver._cost_rule_applies))
    rules_query.cost_delta(g, 0, card_db.get_card("Kozilek's Return"))
    assert [f["rule"] for f in rules_audit.drain()] == [
        "105.2/colour_restricted_reduction"]


def test_the_audit_is_silent_when_the_reduction_reads_the_colour(
        card_db, monkeypatch):
    from engine import rules_audit, rules_query
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    g = _game(active=0)
    _put(g, card_db, "Ruby Medallion", 0, "battlefield")
    for name in ("Lightning Bolt", "Kozilek's Return", "Thoughtseize"):
        rules_query.cost_delta(g, 0, card_db.get_card(name))
    assert rules_audit.drain() == []


# ── Ratchet ─────────────────────────────────────────────────────────

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ALLOW = "color-identity-allow:"


def _identity_reads():
    found = []
    for pkg in ("engine", "ai"):
        for path in sorted((_ROOT / pkg).rglob("*.py")):
            src = path.read_text()
            lines = src.splitlines()
            for node in ast.walk(ast.parse(src)):
                hit = (isinstance(node, ast.Attribute)
                       and node.attr == "color_identity"
                       and isinstance(node.ctx, ast.Load))
                if (isinstance(node, ast.Call)
                        and getattr(node.func, "id", "") in ("getattr",
                                                             "hasattr")
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value == "color_identity"):
                    hit = True
                if hit and _ALLOW not in lines[node.lineno - 1]:
                    found.append(f"{path.relative_to(_ROOT)}:{node.lineno}")
    return found


def test_no_engine_or_ai_rule_reads_colour_identity():
    """Pinned at zero: a colour question reads `colors` (the card's
    characteristic, `CardInstance.colors` on an object). A read that is
    not a colour rule says why on its line (`# color-identity-allow:`)."""
    assert _identity_reads() == []
