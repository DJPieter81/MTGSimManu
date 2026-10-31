"""An X is the value its card binds: the X chosen for an {X} in its mana
cost (CR 107.3a), or the value a ", where X is <quantity>" definition
gives it (CR 107.3, 608.2h) -- never zero by default. An X nothing binds
is refused, so the instruction keeps the path that does not run it.

Two defects:
- the effect grammar read the mana cost's X from a field mana costs do
  not have (`ManaCost.x_count`), so every {X} spell's X was "unbound" and
  its instruction refused (85 spell hosts: "Blaze deals X damage to any
  target." resolved through no typed instruction);
- the payload leaf typed a token count or P/T "X" as the cost's X however
  X was defined, so "Create X 1/1 green Saproling creature tokens, where X
  is ..." on a card with no {X} created none at all on the dispatcher
  (Fungal Sprouting, Flurry of Wings: 23 switched hosts).

Card names are fixture carriers: 339 pool cards have an {X} in their mana
cost; 57 create X tokens where X is defined in their text.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _spec(card_db, name, verb):
    from engine.effect_spec import Verb, iter_specs
    host = card_db.get_card(name).effects.spell(0)
    return next(s for s in iter_specs(host.specs)
                if s.verb is getattr(Verb, verb))


def test_an_x_in_the_mana_cost_binds_the_x_in_the_text(card_db):
    from engine.effect_spec import AmountKind
    s = _spec(card_db, "Blaze", "DAMAGE")
    assert s.amount.kind is AmountKind.X


def test_a_token_count_x_is_the_cost_x(card_db):
    from engine.effect_spec import AmountKind
    s = _spec(card_db, "Secure the Wastes", "CREATE_TOKEN")
    assert s.amount.kind is AmountKind.X


def _token_count_text(definition: str) -> str:
    return ("Create X 1/1 green Saproling creature tokens" + definition
            + ".")


def test_a_defined_x_is_typed_by_its_definition():
    """", where X is <quantity>" defines X for the whole instruction: the
    count is that quantity, not the cost's X."""
    from engine.effect_grammar import parse_text_effects
    from engine.effect_spec import AmountKind, QuantityKind
    host = parse_text_effects(_token_count_text(
        ", where X is the number of creatures you control")).spell(0)
    (s,) = host.specs
    assert s.amount.kind is AmountKind.X_DEFINED
    assert s.amount.inner.quantity.kind is QuantityKind.COUNT


def test_an_x_nothing_binds_is_refused():
    from engine.effect_grammar import parse_text_effects
    from engine.effect_spec import Verb
    host = parse_text_effects(_token_count_text("")).spell(0)
    assert [s.verb for s in host.specs] == [Verb.UNMODELLED]


def _game(card_db, creatures):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for name in creatures:
        card = CardInstance(template=card_db.get_card(name), owner=0,
                            controller=0, instance_id=game.next_instance_id(),
                            zone="battlefield")
        card._game_state = game
        card.enter_battlefield()
        game.players[0].battlefield.append(card)
    return game


def test_the_defined_count_is_created(card_db):
    from engine import effect_resolver as er
    from engine.effect_grammar import parse_text_effects
    game = _game(card_db, ["Grizzly Bears"] * 3)
    host = parse_text_effects(_token_count_text(
        ", where X is the number of creatures you control")).spell(0)
    source = game.players[0].battlefield[0]
    assert er.can_execute(host, "tokens_counters")
    assert er.resolve_ability(game, er.handle_of(source), 0, host, (),
                              family="tokens_counters",
                              source_object=source)
    tokens = [c for c in game.players[0].battlefield
              if getattr(c, "is_token", False)]
    assert len(tokens) == 3


def test_a_definition_nothing_evaluates_is_not_run_as_zero(card_db):
    """A defined X whose quantity no evaluator owns ("the greatest power
    among creatures you control") is no executable count: the host keeps
    its legacy path rather than creating none."""
    from engine import effect_resolver as er
    host = card_db.get_card("Fungal Sprouting").effects.spell(0)
    assert not er.can_execute(host, "tokens_counters")


# ── A keyword action's count is bound the same way ────────────────────
# "Amass Zombies X, where X is ..." typed its X as the cost's X: on a card
# with no {X} it amassed 0 on the dispatcher (Invade the City, The Mouth of
# Sauron, Commence the Endgame; 25 pool keyword actions count X).

def _keyword_count(definition: str):
    from engine.effect_grammar import parse_text_effects
    host = parse_text_effects("Amass Zombies X" + definition + ".").spell(0)
    (s,) = host.specs
    return host, s


def test_a_keyword_actions_count_x_is_typed_by_its_definition():
    from engine.effect_spec import AmountKind, QuantityKind, Verb
    _host, s = _keyword_count(", where X is the number of creatures you "
                              "control")
    assert s.verb is Verb.KEYWORD_ACTION
    assert s.payload.amount.kind is AmountKind.X_DEFINED
    assert s.payload.amount.inner.quantity.kind is QuantityKind.COUNT


def test_a_keyword_actions_count_x_nothing_binds_is_refused():
    from engine.effect_spec import Verb
    _host, s = _keyword_count("")
    assert s.verb is Verb.UNMODELLED


def test_the_defined_keyword_count_is_amassed(card_db):
    from engine import effect_resolver as er
    game = _game(card_db, ["Grizzly Bears"] * 3)
    host, _s = _keyword_count(", where X is the number of creatures you "
                              "control")
    source = game.players[0].battlefield[0]
    assert er.resolve_ability(game, er.handle_of(source), 0, host, (),
                              family="tokens_counters",
                              source_object=source)
    (army,) = [c for c in game.players[0].battlefield
               if "Army" in (c.effective_subtypes or ())]
    assert army.plus_counters == 3


def test_a_keyword_count_nothing_evaluates_is_not_run_as_zero(card_db):
    """"Amass Zombies X, where X is the number of instant and sorcery cards
    in your graveyard": no evaluator owns that quantity, so the host keeps
    its legacy path rather than amassing 0."""
    from engine import effect_resolver as er
    host = card_db.get_card("Invade the City").effects.spell(0)
    assert not er.can_execute(host, "tokens_counters")
