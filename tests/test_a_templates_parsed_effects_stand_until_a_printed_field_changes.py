"""A template's parsed effects stand until a field the parse reads changes.

`CardTemplate.effects` memoises its parse on the complete parse input
(`effect_grammar.template_inputs`: name, face texts and each face's facts).
Those facts are a pure function of a dozen printed fields, so re-deriving
them on every access only re-confirms the memo. On a board of 60 tokens
that re-derivation (`rule_effects` reads every permanent's effects on every
query) took ~70% of a game's time and pushed games past the wall-clock
budget, which aborts them as draws (Ruby Storm vs Boros Energy s50000,
game 1). The memo now stands while those fields are unchanged and is
checked again when any of them changes. Card names are fixture carriers.
"""
from __future__ import annotations

import dataclasses


def _counting(monkeypatch):
    from engine import effect_grammar
    calls = []
    real = effect_grammar.template_inputs

    def counted(template):
        calls.append(template.name)
        return real(template)
    monkeypatch.setattr(effect_grammar, "template_inputs", counted)
    return calls


def test_an_unchanged_template_does_not_rederive_its_parse_input(
        card_db, monkeypatch):
    t = dataclasses.replace(card_db.get_card("Grizzly Bears"))
    first = t.effects
    calls = _counting(monkeypatch)
    assert t.effects is first
    assert t.effects is first
    assert calls == []


def test_a_changed_printed_field_parses_again(card_db, monkeypatch):
    t = dataclasses.replace(card_db.get_card("Grizzly Bears"))
    before = t.effects
    calls = _counting(monkeypatch)
    t.oracle_text = "Flying"
    after = t.effects
    assert calls and after is not before
    t.subtypes.append("Bird")
    t.effects
    assert len(calls) == 2


def test_a_pinned_parse_is_checked_against_its_input_once(card_db,
                                                          monkeypatch):
    from engine.effect_spec import EMPTY_EFFECTS
    t = dataclasses.replace(card_db.get_card("Grizzly Bears"))
    t.set_effects(EMPTY_EFFECTS)
    assert t.effects is EMPTY_EFFECTS
    calls = _counting(monkeypatch)
    assert t.effects is EMPTY_EFFECTS
    assert calls == []
