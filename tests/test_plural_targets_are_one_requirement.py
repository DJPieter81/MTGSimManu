"""A target word may take a count (CR 115.1, 601.2c): "up to two target
creatures", "two target permanents", "one or two target creatures", "any
number of target players". It is ONE requirement with a count — the target
solver parses it and every resolver chooses up to that many distinct legal
objects (CR 115.3), resolving on the still-legal ones (CR 608.2b).

Rules pinned:
* plural and counted target phrases parse, with count_min / count_max
  (before this, every plural target parsed to nothing);
* "and/or" type lists are the union of their types;
* a counted destroy takes that many distinct legal objects, honours chosen
  ids and never picks an illegal (hexproof) one;
* the same object is never chosen twice for one target word.
Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine.cards import CardInstance, Keyword
from engine.game_state import GameState, Phase
from engine.target_solver import ANY_NUMBER, choose_targets, parse


def _one(text):
    reqs = [r for r in parse(text) if r.zone in ("battlefield", "graveyard")]
    assert len(reqs) == 1, reqs
    return reqs[0]


def test_counted_target_phrases_parse_with_their_counts():
    r = _one("Tap up to two target creatures.")
    assert (r.types, r.count_min, r.count_max) == (frozenset({"creature"}), 0, 2)
    r = _one("Exile two target permanents.")
    assert (r.types, r.count_min, r.count_max) == (frozenset({"permanent"}), 2, 2)
    r = _one("One or two target creatures each get +2/+2 until end of turn.")
    assert (r.count_min, r.count_max) == (1, 2)
    r = _one("Destroy up to three target nonland permanents.")
    assert (r.types, r.count_max) == (frozenset({"permanent_nonland"}), 3)


def test_a_plain_target_keeps_a_count_of_one():
    r = _one("Destroy target creature.")
    assert (r.count_min, r.count_max) == (1, 1)
    r = _one("Return up to one target creature to its owner's hand.")
    assert (r.count_min, r.count_max) == (0, 1)


def test_an_and_or_type_list_is_the_union_of_its_types():
    r = _one("Destroy up to two target artifacts and/or enchantments.")
    assert (r.types, r.count_max) == (frozenset({"artifact", "enchantment"}), 2)


def test_any_number_of_targets_is_unbounded():
    r = _one("Exile any number of target creatures.")
    assert r.count_min == 0 and r.count_max == ANY_NUMBER


# ── choosing ─────────────────────────────────────────────────────────

def _put(game, card_db, name, controller):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    game.players[controller].battlefield.append(c)
    return c


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    return game


def test_choose_targets_takes_up_to_the_count_of_distinct_legal_objects(card_db):
    game = _game()
    a = _put(game, card_db, "Grizzly Bears", 1)
    b = _put(game, card_db, "Grizzly Bears", 1)
    c = _put(game, card_db, "Grizzly Bears", 1)
    req = _one("Destroy up to two target creatures.")
    chosen = choose_targets(game, 0, req)
    assert len(chosen) == 2 and len(set(map(id, chosen))) == 2
    chosen = choose_targets(game, 0, req, preferred=[c.instance_id, c.instance_id])
    assert chosen[0] is c and len(set(map(id, chosen))) == len(chosen)


def test_choose_targets_never_picks_an_illegal_object(card_db):
    game = _game()
    shy = _put(game, card_db, "Grizzly Bears", 1)
    shy.template = copy.copy(shy.template)
    shy.template.keywords = set(shy.template.keywords) | {Keyword.HEXPROOF}
    ok = _put(game, card_db, "Grizzly Bears", 1)
    req = _one("Destroy up to two target creatures.")
    assert choose_targets(game, 0, req, preferred=[shy.instance_id]) == [ok]


def test_a_counted_destroy_resolves_on_that_many_objects(card_db):
    from engine.oracle_resolver import resolve_spell_from_oracle
    game = _game()
    art = [_put(game, card_db, "Ornithopter", 1) for _ in range(3)]
    tpl = copy.copy(card_db.get_card("Shatter"))
    tpl.oracle_text = "Destroy up to two target artifacts and/or enchantments."
    from engine.oracle_parser import parse_targeted_removal
    tpl.targeted_removal_data = parse_targeted_removal(tpl.oracle_text)
    assert tpl.targeted_removal_data["count"] == 2
    s = CardInstance(template=tpl, owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone="stack")
    s._game_state = game
    resolve_spell_from_oracle(game, s, 0, [])
    assert sum(a.zone != "battlefield" for a in art) == 2


def test_a_cast_trigger_that_exiles_counted_targets_resolves(card_db):
    """'When you cast this spell, exile two target permanents' (CR 601.2i):
    the cast trigger's removal resolves through the typed removal path."""
    from engine.oracle_resolver import resolve_self_cast_trigger
    game = _game()
    a = _put(game, card_db, "Grizzly Bears", 1)
    b = _put(game, card_db, "Ornithopter", 1)
    hunger = CardInstance(template=card_db.get_card("Ulamog, the Ceaseless Hunger"),
                          owner=0, controller=0, instance_id=game.next_instance_id(),
                          zone="stack")
    hunger._game_state = game
    assert resolve_self_cast_trigger(game, 0, hunger) is True
    assert a.zone == "exile" and b.zone == "exile"


def test_distinct_targets_audit_sees_a_repeated_object(card_db, monkeypatch):
    from engine import rules_audit, target_solver
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    game = _game()
    art = _put(game, card_db, "Ornithopter", 1)
    monkeypatch.setattr(target_solver, "choose_targets", lambda *a, **k: [art, art])
    from engine.card_effects import _resolve_nonland_permanent_removal
    _resolve_nonland_permanent_removal(
        game, art, 0, [], None, zone_dest="graveyard",
        types=frozenset({"artifact"}), count=2)
    rules = {f["rule"] for f in rules_audit.drain() if f["kind"] == "violation"}
    rules_audit.reset()
    assert "115.3/distinct_targets" in rules
