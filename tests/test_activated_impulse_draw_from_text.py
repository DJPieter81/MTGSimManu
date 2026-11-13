"""An activated ability the legacy classifier does not read resolves from
its typed text (CR 602.2, 605; design doc 2026-09-29 section 11).

"{3}{R}, {T}: Exile the top card of your library. Until the end of your
next turn, you may play that card." (Cori Mountain Monastery, Boros Ponza
x4; 23 pool cards print an activated or loyalty impulse draw). The legacy
classifier leaves the ability UNCLASSIFIED, which has no legacy apply: the
engine resolved nothing and the AI never activated it. Its typed host is
the card-flow impulse pair the dispatcher already executes (unit I), so it
resolves through the dispatcher -- an UNCLASSIFIED ability only, so no
classified ability changes path -- and the AI values it as the cards it
lets the player play past this turn.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _impulse_ability(card):
    from engine.cards import ActivationEffectKind
    (ab,) = [a for a in card.template.activated_abilities
             if a.effect_kind is ActivationEffectKind.UNCLASSIFIED
             and a.cost.mana.cmc > 0]
    return ab


def test_an_unclassified_activation_resolves_its_typed_impulse_draw(card_db):
    from engine import rules_query
    from engine.activated_effects import resolve_activated_ability
    game = _game()
    cori = _put(game, card_db, "Cori Mountain Monastery", "battlefield")
    top = _put(game, card_db, "Lightning Bolt", "library")
    below = _put(game, card_db, "Mountain", "library")
    assert resolve_activated_ability(game, cori, 0, [],
                                     ability=_impulse_ability(cori))
    assert top in game.players[0].exile and below in game.players[0].library
    assert rules_query.permitted_cards(game, 0) == [top]
    assert not rules_query.permission_ends_this_turn(game, 0, top)


def test_a_classified_activation_keeps_its_derivation_family(card_db):
    """The UNCLASSIFIED route never re-homes a classified ability."""
    from engine.effect_carrier import activation_family
    from engine.cards import ActivationEffectKind
    t = card_db.get_card("Cori Mountain Monastery")
    ab = _impulse_ability(_put(_game(), card_db,
                               "Cori Mountain Monastery", "battlefield"))
    assert activation_family(ab, t.effects.activated(ab.index)) == "card_flow"
    bolt_rod = next(n for n in ("Mogg Fanatic", "Prodigal Pyromancer",
                                "Prodigal Sorcerer")
                    if card_db.get_card(n) is not None)
    pinger = card_db.get_card(bolt_rod)
    (ping,) = [a for a in pinger.activated_abilities
               if a.effect_kind is ActivationEffectKind.DAMAGE_ANY_TARGET]
    assert activation_family(ping, pinger.effects.activated(ping.index)) \
        == "damage"


def test_the_ai_offers_an_impulse_activation_it_can_afford(card_db):
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game
    game = _game()
    cori = _put(game, card_db, "Cori Mountain Monastery", "battlefield")
    cori.tapped = False      # it enters tapped without a Plains or Island
    for _ in range(4):
        _put(game, card_db, "Mountain", "battlefield")      # {3}{R}
    _put(game, card_db, "Lightning Bolt", "library")
    snap = snapshot_from_game(game, 0)
    ab = _impulse_ability(cori)
    found = [(perm, idx, ev) for perm, idx, _t, ev, _r in
             activation_candidates(game, 0, snap)
             if perm is cori and idx == ab.index]
    assert found and found[0][2] > 0
