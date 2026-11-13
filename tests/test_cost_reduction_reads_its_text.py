"""A cost reduction reduces exactly the spells its text names, on the face
and level that print it (CR 601.2f, 611.3a, 712.8e, 716.2).

"<Qualities> spells you cast cost {N} less to cast" reduces the total cost
of a spell that has those qualities (CR 601.2f): a colour, "colorless", a
card type or "non<type>", a supertype, a subtype, or "historic";
"instant and sorcery spells" and "Kithkin spells and Soldier spells" name
either. A static ability functions only while its permanent has it: a
transformed double-faced permanent has only its back face's abilities (CR
712.8e), and a Class has a level's abilities only once it has gained that
level (CR 716.2).

The typed field read the whole oracle text by substring. "Noncreature" was
read as "creature" (it contains "creature spell"); every subject it did not
know reduced every spell; any "cost {N} less" anywhere was a spell
reduction, even of activated abilities or in reminder text, coloured by
any colour word in the card ("reduce" is red); a second reducer sentence
was dropped; the front face's reduction stayed after a transform; and a
Class's level-2 reduction applied from level 1 (Artist's Talent, Ruby
Storm x2).

Card names are fixture carriers: 148 pool templates carried a reducer rule.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(active=0):
    g = GameState(rng=random.Random(0))
    g.active_player = active
    g.current_phase = Phase.MAIN1
    return g


def _put(game, card_db, name, owner=0):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[owner].battlefield.append(c)
    return c


def _delta(game, card_db, spell, player=0):
    from engine import rules_query
    return rules_query.cost_delta(game, player, card_db.get_card(spell))


def test_a_reduction_names_the_spells_it_reduces(card_db):
    cases = {
        "Etherium Sculptor": {"Ornithopter": 1, "Lightning Bolt": 0},
        "Goblin Warchief": {"Goblin Guide": 1, "Grizzly Bears": 0},
        "Blood Funnel": {"Lightning Bolt": 2, "Grizzly Bears": 0},
        "Ballyrush Banneret": {"Thraben Inspector": 1, "Grizzly Bears": 0},
        "Hazoret's Monument": {"Goblin Guide": 1, "Lightning Bolt": 0,
                               "Grizzly Bears": 0},
        "Jhoira's Familiar": {"Ornithopter": 1,
                              "Ragavan, Nimble Pilferer": 1,
                              "Lightning Bolt": 0},
        "Herald of Kozilek": {"Ornithopter": 1, "Lightning Bolt": 0},
        "Ruby Medallion": {"Lightning Bolt": 1, "Counterspell": 0},
    }
    for reducer, spells in cases.items():
        g = _game()
        _put(g, card_db, reducer)
        for spell, expected in spells.items():
            assert _delta(g, card_db, spell) == expected, (reducer, spell)


def test_a_subject_lists_its_alternatives():
    """"X and Y spells", "X spells and Y spells" and "X, Y, and Z spells"
    name either; "black creature spells" needs both qualities."""
    from engine.oracle_parser import parse_cost_reduction
    q = lambda s: parse_cost_reduction(s)['qualities']
    assert q("Instant and sorcery spells you cast cost {1} less to cast.") \
        == (('instant',), ('sorcery',))
    assert q("Kithkin spells and Soldier spells you cast cost {1} less to "
             "cast.") == (('kithkin',), ('soldier',))
    assert q("Artifact, instant, and sorcery spells you cast cost {1} less "
             "to cast.") == (('artifact',), ('instant',), ('sorcery',))
    assert q("Black creature spells you cast cost {1} less to cast.") \
        == (('black', 'creature'),)
    for refused in ("During your turn, spells you cast cost {1} less to cast.",
                    "Face-down creature spells you cast cost {1} less to cast.",
                    "Creature spells you cast with power 4 or greater cost "
                    "{1} less to cast."):
        assert parse_cost_reduction(refused) is None, refused


def test_a_reduction_of_activation_or_keyword_costs_reduces_no_spell(card_db):
    g = _game()
    _put(g, card_db, "Training Grounds")
    for spell in ("Lightning Bolt", "Grizzly Bears", "Ornithopter"):
        assert _delta(g, card_db, spell) == 0, spell


def test_reminder_text_is_no_reduction(card_db):
    g = _game()
    _put(g, card_db, "Sami, Wildcat Captain")
    assert _delta(g, card_db, "Lightning Bolt") == 0


def test_each_reducer_sentence_is_its_own_reduction(card_db):
    """White spells and blue spells each cost {1} less: a white-blue spell
    costs {2} less."""
    g = _game()
    _put(g, card_db, "Grand Arbiter Augustin IV")
    assert _delta(g, card_db, "Supreme Verdict") == 2
    assert _delta(g, card_db, "Path to Exile") == 1
    assert _delta(g, card_db, "Lightning Bolt") == 0


def test_a_reduction_reduces_only_its_controllers_spells(card_db):
    g = _game()
    _put(g, card_db, "Ruby Medallion", owner=0)
    assert _delta(g, card_db, "Lightning Bolt", player=1) == 0


def test_a_reduction_that_names_no_caster_reduces_every_players_spells(
        card_db):
    """"Instant and sorcery spells cost {2} less to cast" (no "you cast")."""
    g = _game()
    _put(g, card_db, "Arcane Melee", owner=0)
    for player in (0, 1):
        assert _delta(g, card_db, "Lightning Bolt", player=player) == 2
        assert _delta(g, card_db, "Grizzly Bears", player=player) == 0


def test_a_transformed_permanent_reduces_by_the_face_it_shows(card_db):
    g = _game()
    ral = _put(g, card_db, "Ral, Monsoon Mage // Ral, Leyline Prodigy")
    assert _delta(g, card_db, "Lightning Bolt") == 1
    ral.is_transformed = True
    assert _delta(g, card_db, "Lightning Bolt") == 0

    g = _game()
    reader = _put(g, card_db, "Curious Homunculus // Voracious Reader")
    assert _delta(g, card_db, "Lightning Bolt") == 0
    reader.is_transformed = True
    assert _delta(g, card_db, "Lightning Bolt") == 1


def test_a_class_has_a_levels_reduction_only_at_that_level(card_db):
    """Level 2 reads "Noncreature spells you cast cost {1} less": at level 1
    neither a noncreature nor a creature spell is reduced."""
    g = _game()
    _put(g, card_db, "Artist's Talent")      # enters at level 1
    assert _delta(g, card_db, "Lightning Bolt") == 0
    assert _delta(g, card_db, "Grizzly Bears") == 0


# ── Auditor (CR 601.2f) ─────────────────────────────────────────────

def test_the_audit_records_a_reduction_applied_to_a_spell_it_does_not_name(
        card_db, monkeypatch):
    from engine import oracle_resolver, rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    g = _game()
    _put(g, card_db, "Etherium Sculptor")
    monkeypatch.setattr(oracle_resolver, "_cost_rule_applies",
                        lambda rule, template: True)
    _delta(g, card_db, "Lightning Bolt")
    assert [f["rule"] for f in rules_audit.drain()] == [
        "601.2f/reduction_names_the_spell"]


def test_the_audit_is_silent_when_each_reduction_names_its_spell(
        card_db, monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    g = _game()
    for reducer in ("Etherium Sculptor", "Goblin Warchief", "Ruby Medallion",
                    "Grand Arbiter Augustin IV", "Jhoira's Familiar"):
        _put(g, card_db, reducer)
    for spell in ("Lightning Bolt", "Ornithopter", "Goblin Guide",
                  "Supreme Verdict", "Ragavan, Nimble Pilferer"):
        _delta(g, card_db, spell)
    assert rules_audit.drain() == []
