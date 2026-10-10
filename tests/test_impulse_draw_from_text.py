"""Impulse draw is read from the card's text (CR 401.5, 406, 601.2, 305.1).

"Exile the top N cards of your library. Until the end of your next turn,
you may play those cards." -- the exiled cards go to exile, not to hand,
and may be played from there until the permission expires. The effect
grammar types each part; the engine performs them through its owners. A
classifier tag neither adds nor removes the effect.

The grammar steps type the library position, the permission and its
duration; the engine steps perform them: the card-flow family's EXILE and
CONTINUOUS PERMIT executors, and the one read path every play gate asks
(`rules_query.play_permitted`).
"""
from __future__ import annotations

import pytest


def _specs(card_db, name):
    from engine.effect_spec import HostKind, iter_specs
    t = card_db.get_card(name)
    return [s for h in t.effects.walk(include_sub=False)
            if h.kind in (HostKind.SPELL, HostKind.TRIGGERED,
                          HostKind.ACTIVATED)
            for s in iter_specs(h.specs)]


# ── The library position (CR 401.5) ────────────────────────────────────

@pytest.mark.parametrize("name,n", [("Reckless Impulse", 2),
                                    ("Wrenn's Resolve", 2),
                                    ("Glimpse the Impossible", 3)])
def test_the_top_n_cards_of_your_library_is_a_typed_exile_object(card_db, name, n):
    """The object of "exile the top N cards of your library" is the
    controller's own library, from the top, N cards."""
    from engine.effect_spec import AmountKind, Verb
    exile = next(s for s in _specs(card_db, name) if s.verb is Verb.EXILE)
    f = exile.filter
    assert (f.zone, f.owner, f.position) == ("library", "you", "top")
    assert exile.amount.kind is AmountKind.LITERAL and exile.amount.n == n


def test_the_top_x_cards_is_counted_by_x(card_db):
    from engine.effect_spec import AmountKind, Verb
    exile = next(s for s in _specs(card_db, "Culmination of Studies")
                 if s.verb is Verb.EXILE)
    assert exile.filter.position == "top"
    assert exile.amount.kind is AmountKind.X


def test_a_library_position_another_verb_reads_stays_refused(card_db):
    """Staged by verb: "look at the top N cards of your library" keeps the
    participant leaf's refusal until its executor lands."""
    from engine.effect_spec import Verb
    looks = [s for s in _specs(card_db, "Consult the Star Charts")
             if s.verb is Verb.UNMODELLED
             and s.payload.detail == "participant.library_position"]
    assert looks


def test_another_players_library_position_stays_refused():
    from engine.effect_grammar.sub import participant
    for text in ("exile the top card of target player's library",
                 "exile the top two cards of each player's library",
                 "exile the bottom card of your library"):
        r = participant.parse_participant(text, (len("exile "), len(text)),
                                          lemma="exile")
        assert r.value is None, text


# ── The permission to play (CR 305.1, 601.2) ───────────────────────────

def test_you_may_play_those_cards_is_a_permission_over_the_exiled_cards(card_db):
    """"You may play those cards this turn": a PERMIT to the controller
    (the actor), over the cards the exile before it produced (the linker's
    RESULT), for the printed duration. The "may" is the grant: the spec is
    not an optional effect."""
    from engine.effect_model import DurationKind, ModKind, SelectorKind
    from engine.effect_spec import RefKind, Verb
    specs = _specs(card_db, "Glimpse the Impossible")
    exile = next(s for s in specs if s.verb is Verb.EXILE)
    permit = next(s for s in specs if s.verb is Verb.CONTINUOUS)
    assert permit.payload.kind is ModKind.PERMIT
    assert permit.payload.action == "play"
    assert permit.actor.kind is SelectorKind.PLAYER
    assert permit.ref.kind is RefKind.RESULT and permit.ref.index == exile.seq
    assert permit.duration.kind is DurationKind.THIS_TURN
    assert not permit.optional


def test_a_free_cast_and_a_flash_permission_are_no_play_permission(card_db):
    """"You may cast it without paying its mana cost" stays a free cast,
    optional; "you may cast spells as though they had flash" never reads as
    a permission to play named objects."""
    from engine.effect_model import ModKind
    from engine.effect_spec import Verb
    free = [s for s in _specs(card_db, "Hidetsugu and Kairi")
            if s.verb is Verb.CAST_FREE]
    assert free and free[0].optional
    t = card_db.get_card("Vedalken Orrery")
    specs = [s for h in t.effects.walk(include_sub=False) for s in h.specs]
    assert not any(s.verb is Verb.CONTINUOUS
                   and s.payload.kind is ModKind.PERMIT
                   and s.payload.action in ("play", "cast") for s in specs)


def test_until_the_end_of_your_next_turn_types_the_impulse_permission(card_db):
    from engine.effect_model import DurationKind, ModKind
    from engine.effect_spec import Verb
    permit = next(s for s in _specs(card_db, "Reckless Impulse")
                  if s.verb is Verb.CONTINUOUS)
    assert permit.payload.kind is ModKind.PERMIT
    assert permit.duration.kind is DurationKind.UNTIL_END_OF_YOUR_NEXT_TURN


def test_a_clock_event_carries_the_turn_it_happens_in():
    """The clock stamps each event with the game turn, so a duration that
    counts turns can end."""
    import random
    from engine.game_state import GameState
    from engine.turn_clock import Clock, ClockEvent, emit, _SUBSCRIBERS
    seen = []
    game = GameState(rng=random.Random(0))
    game.turn_number = 7
    _SUBSCRIBERS[Clock.END_STEP].append(("t", lambda g, e: seen.append(e.turn)))
    try:
        emit(game, ClockEvent(Clock.END_STEP, 0))
    finally:
        _SUBSCRIBERS[Clock.END_STEP].pop()
    assert seen == [7]


# ── The engine: exile, then a permission to play (CR 406, 305.1, 601.2a,
#    400.7). The card-flow family's EXILE and CONTINUOUS PERMIT executors
#    perform the typed specs through their owners: the zone funnel moves
#    the cards, the rule-effect store holds the permission, and every gate
#    that asks "may this player play this card?" asks `rules_query`. ──

def _game(turn=5, active=0):
    import random
    from engine.game_state import GameState, Phase
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = active
    game.turn_number = turn
    return game


def _put(game, card_db, idx, name, zone):
    from engine.cards import CardInstance
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _library(game, card_db, names, idx=0):
    """`names[0]` is the top card."""
    return [_put(game, card_db, idx, n, "library") for n in names]


def _resolve(game, card_db, name, controller=0):
    """Resolve the card's spell host through the card-flow family."""
    from engine import effect_resolver as er
    from engine.cards import CardInstance
    src = CardInstance(template=card_db.get_card(name), owner=controller,
                       controller=controller,
                       instance_id=game.next_instance_id(), zone="stack")
    host = src.template.effects.spell(0)
    assert er.resolve_ability(game, er.handle_of(src), controller, host, (),
                              family="card_flow", source_object=src)
    return src


def _permitted(game, idx=0):
    from engine import rules_query
    return set(rules_query.permitted_objects(game, idx))


def test_exiling_the_top_n_cards_of_your_library_is_not_a_draw(card_db):
    """The top N cards go to exile, face up, through the zone funnel; the
    hand and the draw count are untouched (CR 121.1c: no draw)."""
    game = _game()
    top = _library(game, card_db, ["Lightning Bolt", "Mountain", "Island"])
    _resolve(game, card_db, "Reckless Impulse")
    p = game.players[0]
    assert p.exile == top[:2] and all(c.zone == "exile" for c in top[:2])
    assert p.library == top[2:]
    assert p.hand == [] and p.cards_drawn_this_turn == 0


def test_those_cards_and_only_those_may_be_played(card_db):
    game = _game()
    top = _library(game, card_db, ["Lightning Bolt", "Mountain", "Island"])
    _put(game, card_db, 0, "Shock", "exile")       # exiled some other way
    _resolve(game, card_db, "Reckless Impulse")
    assert _permitted(game) == {c.instance_id for c in top[:2]}
    assert _permitted(game, 1) == set()


def test_a_permitted_exiled_spell_is_cast_with_normal_cost_and_timing(card_db):
    """The permission changes only where the spell may be cast from (CR
    601.2a): the mana cost is still paid and a sorcery-speed card still
    waits for its controller's main phase with an empty stack."""
    from engine.game_state import Phase
    game = _game()
    bolt, bears = _library(game, card_db, ["Lightning Bolt", "Grizzly Bears"])
    _resolve(game, card_db, "Reckless Impulse")
    assert not game.can_cast(0, bolt)                  # no mana yet
    _put(game, card_db, 0, "Mountain", "battlefield")
    _put(game, card_db, 0, "Forest", "battlefield")
    assert game.can_cast(0, bolt) and game.can_cast(0, bears)
    assert bolt in game.get_legal_plays(0) and bears in game.get_legal_plays(0)
    game.current_phase = Phase.END_STEP
    assert game.can_cast(0, bolt)                      # an instant: any time
    assert not game.can_cast(0, bears)                 # a creature: main phase


def test_an_exiled_card_without_a_permission_stays_uncastable(card_db):
    game = _game()
    _put(game, card_db, 0, "Mountain", "battlefield")
    bolt = _put(game, card_db, 0, "Lightning Bolt", "exile")
    assert not game.can_cast(0, bolt)
    assert bolt not in game.get_legal_plays(0)


def test_a_permitted_exiled_land_is_the_turns_land_play(card_db):
    """A land played from exile under the permission is the turn's land
    play (CR 305.2): it enters, uses the drop, and a second permitted land
    waits for another turn."""
    game = _game()
    mountain, island = _library(game, card_db, ["Mountain", "Island"])
    _resolve(game, card_db, "Reckless Impulse")
    legal = game.get_legal_plays(0)
    assert mountain in legal and island in legal
    game.play_land(0, mountain)
    p = game.players[0]
    assert mountain in p.battlefield and mountain.zone == "battlefield"
    assert p.lands_played_this_turn == 1
    assert island not in game.get_legal_plays(0)
    game.play_land(0, island)
    assert island.zone == "exile"


def test_a_cast_permission_does_not_cover_a_land(card_db):
    """"You may cast" covers spells; only "you may play" covers a land
    (CR 305.1)."""
    from engine.effect_model import THIS_TURN, permit_play
    game = _game()
    land = _put(game, card_db, 0, "Mountain", "exile")
    game.continuous_effects.register_effect(
        permit_play(0, [land.instance_id], "cast", THIS_TURN))
    assert land not in game.get_legal_plays(0)
    game.play_land(0, land)
    assert land.zone == "exile"


@pytest.mark.parametrize("created_turn,active", [(5, 0), (6, 1)])
def test_until_the_end_of_your_next_turn_ends_at_that_cleanup(card_db,
                                                              created_turn,
                                                              active):
    """Created on your turn, it lasts through your next turn; created on
    another player's turn, through your coming turn (CR 611.2)."""
    from engine.turn_clock import Clock, ClockEvent, emit
    game = _game(turn=created_turn, active=active)
    _library(game, card_db, ["Lightning Bolt", "Mountain"])
    _resolve(game, card_db, "Reckless Impulse")
    permitted = _permitted(game)
    assert len(permitted) == 2
    for turn in range(created_turn, 8):
        game.turn_number = turn
        player = 0 if turn in (5, 7) else 1           # P1 owns turn 6
        emit(game, ClockEvent(Clock.CLEANUP, player))
        assert (_permitted(game) == permitted) is (turn < 7), turn


def test_a_this_turn_permission_ends_with_the_turn(card_db):
    from engine.turn_clock import Clock, ClockEvent, emit
    game = _game()
    _library(game, card_db, ["Lightning Bolt", "Mountain", "Island"])
    _resolve(game, card_db, "Act on Impulse")
    assert len(_permitted(game)) == 3
    emit(game, ClockEvent(Clock.CLEANUP, 0))
    assert _permitted(game) == set()


def test_a_card_cast_from_exile_is_a_new_object_the_permission_no_longer_names(
        card_db):
    """CR 400.7: once cast, the card is a new object -- if it comes back to
    exile (a flashback cast is exiled), the permission does not let it be
    cast again."""
    from engine import rules_query
    game = _game()
    (ritual,) = _library(game, card_db, ["Pyretic Ritual"])
    _resolve(game, card_db, "Reckless Impulse")
    for land in ("Mountain", "Mountain"):
        _put(game, card_db, 0, land, "battlefield")
    assert game.cast_spell(0, ritual)
    assert ritual.instance_id not in _permitted(game)
    game.stack.items.clear()
    ritual.zone = "exile"
    game.players[0].exile.append(ritual)
    assert not rules_query.play_permitted(game, 0, ritual)
    assert not game.can_cast(0, ritual)


def test_a_card_leaving_exile_through_the_zone_funnel_loses_its_permission(
        card_db):
    game = _game()
    bolt, mountain = _library(game, card_db, ["Lightning Bolt", "Mountain"])
    _resolve(game, card_db, "Reckless Impulse")
    game.zone_mgr.move_card(game, bolt, "exile", "graveyard")
    game.zone_mgr.move_card(game, bolt, "graveyard", "exile")
    assert _permitted(game) == {mountain.instance_id}


@pytest.mark.parametrize("name", ["Path to Exile", "Celestial Purge"])
def test_the_exile_executor_binds_only_the_top_of_your_own_library(card_db,
                                                                   name):
    """Exiling a target permanent is another rule (its owner is not this
    executor): the card-flow family refuses it."""
    from engine import effect_resolver as er
    host = card_db.get_card(name).effects.spell(0)
    assert not er.can_execute(host, "card_flow")


# ── The carriers: an impulse spell and an impulse enter trigger resolve
#    from their text through the dispatcher's card-flow family; a
#    classifier tag neither adds nor removes the effect. ──

def _cast(game, card_db, name, controller=0, x_value=0):
    """Resolve the spell through the engine's spell resolution path."""
    from engine.oracle_resolver import resolve_spell_from_oracle
    card = _put(game, card_db, controller, name, "hand")
    game.players[controller].hand.remove(card)
    card.zone = "stack"
    resolve_spell_from_oracle(game, card, controller, x_value=x_value)
    return card


@pytest.mark.parametrize("name,n", [("Reckless Impulse", 2),
                                    ("Wrenn's Resolve", 2),
                                    ("Act on Impulse", 3)])
def test_an_impulse_spell_exiles_and_permits_as_printed(card_db, name, n):
    """Tagged or not, the spell exiles the top N cards with a permission
    to play them -- never to hand, never a draw."""
    game = _game()
    top = _library(game, card_db, ["Lightning Bolt", "Mountain", "Island",
                                   "Forest"])
    _cast(game, card_db, name)
    p = game.players[0]
    assert p.exile == top[:n] and p.hand == []
    assert _permitted(game) == {c.instance_id for c in top[:n]}
    assert p.cards_drawn_this_turn == 0


def test_an_x_impulse_spell_exiles_x(card_db):
    game = _game()
    top = _library(game, card_db, ["Lightning Bolt", "Mountain", "Island",
                                   "Forest"])
    _cast(game, card_db, "Commune with Lava", x_value=3)
    assert game.players[0].exile == top[:3]
    assert _permitted(game) == {c.instance_id for c in top[:3]}


def test_a_classifier_tag_alone_never_makes_a_spell_impulse(card_db,
                                                             monkeypatch):
    """A spell tagged as impulse draw whose text draws a card draws it:
    the tag adds no exile and no permission."""
    import ai.oracle_classifier as oc
    monkeypatch.setattr(oc, "tags_for",
                        lambda name: frozenset({oc.Tag.IMPULSE_DRAW}))
    game = _game()
    top = _library(game, card_db, ["Lightning Bolt", "Mountain"])
    _cast(game, card_db, "Opt")
    p = game.players[0]
    assert p.exile == [] and _permitted(game) == set()
    assert len(p.hand) == 1 and p.hand[0] in top


@pytest.mark.parametrize("name", ["Kulrath Zealot", "Gundabad Opportunist"])
def test_an_enter_trigger_impulse_exiles_and_permits_as_printed(card_db,
                                                                name):
    from engine.oracle_resolver import resolve_etb_from_oracle
    game = _game()
    (top, below) = _library(game, card_db, ["Lightning Bolt", "Mountain"])
    creature = _put(game, card_db, 0, name, "battlefield")
    resolve_etb_from_oracle(game, creature, 0)
    assert game.players[0].exile == [top]
    assert _permitted(game) == {top.instance_id}
    assert game.players[0].library == [below]
