"""Impulse / library-dig (CR 120 card selection) resolves generically.

The "look at / reveal the top N cards of your library, take a
predicate-matching card to your hand, put the rest on the bottom / into
your graveyard" family (Ancient Stirrings, Malevolent Rumble, Consult the
Star Charts, Commune with Nature, Grisly Salvage, …) was previously
UNMODELED — the spells resolved to a silent no-op and were parked on the
`ALLOWED_UNHANDLED` allowlist in
``tests/test_no_silent_unhandled_effects.py``.

These tests pin the mechanic, not the cards:

  * a dig moves the best predicate-matching card into hand, and it does so
    through the zone funnel (``game.zone_mgr.move_card``) — NEVER
    ``game.draw_cards`` — so on-draw watchers (Orcish Bowmasters /
    Sheoldred) do NOT fire (CR 121.1c: impulse selection is not a draw);
  * the take-predicate filters which card is kept (a colorless-only dig
    never keeps a colored card);
  * the "rest" of the looked-at cards land in their declared destination
    (bottom of library vs graveyard);
  * a dynamic look count derives from board state (X = lands you control).

Test names describe the rule; the real card names are fixture carriers so
the DB-derived typed field (``CardTemplate.library_dig_data``) is populated
the same way it is in a real game.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Color
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


# ─── fixtures ──────────────────────────────────────────────────────


def _fresh_game() -> GameState:
    return GameState(rng=random.Random(0))


def _make_card(game: GameState, name: str, controller: int, zone: str,
               card_types: list, *, colors=None, oracle_text: str = "",
               cmc: int = 1, on_battlefield: bool = False) -> CardInstance:
    tmpl = CardTemplate(
        name=name,
        card_types=card_types,
        mana_cost=ManaCost(generic=cmc),
        supertypes=[], subtypes=[],
        power=1 if CardType.CREATURE in card_types else None,
        toughness=1 if CardType.CREATURE in card_types else None,
        loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(colors or set()),
        colors=set(colors or set()),
        produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle_text,
        tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if on_battlefield:
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    return card


def _put_bowmasters(game: GameState, controller: int) -> CardInstance:
    return _make_card(
        game, name="Orcish Bowmasters", controller=controller,
        zone="battlefield", card_types=[CardType.CREATURE],
        oracle_text=(
            "Whenever an opponent draws a card, except the first one they "
            "draw in each of their draw steps, this creature deals 1 damage "
            "to that player."),
        on_battlefield=True,
    )


def _cast(game: GameState, name: str, controller: int,
          oracle_text: str) -> CardInstance:
    spell = _make_card(game, name=name, controller=controller, zone="hand",
                       card_types=[CardType.SORCERY], oracle_text=oracle_text)
    game.players[controller].hand.append(spell)
    game.current_phase = Phase.MAIN1
    game.active_player = controller
    game.players[controller].cards_drawn_this_turn = 5  # past free first draw
    return spell


# ─── the mechanic ──────────────────────────────────────────────────


def test_impulse_dig_takes_matching_card_to_hand_without_firing_draw_watchers():
    """A permanent-predicate dig (graveyard-rest shape) keeps a permanent
    card from the top N and fires ZERO on-draw watchers.

    With two Bowmasters opposing, treating the dig as a draw would deal
    damage per moved card. The dig moves cards through the zone funnel,
    so the caster's life is unchanged. (A pure dig — no create-token /
    damage rider — since parse_library_dig refuses a dig that carries a
    rider it does not execute; Malevolent Rumble's real oracle, with its
    Eldrazi Spawn token, is deliberately routed to token creation instead.)
    """
    game = _fresh_game()
    caster, opp = 0, 1
    _put_bowmasters(game, opp)
    _put_bowmasters(game, opp)

    # Top of library: a creature (permanent) + three instants (non-permanent).
    creature = _make_card(game, "Top Dork", caster, "library",
                          [CardType.CREATURE], colors={Color.GREEN}, cmc=3)
    fillers = [_make_card(game, f"Top Bolt {i}", caster, "library",
                          [CardType.INSTANT], colors={Color.RED}, cmc=1)
               for i in range(3)]
    game.players[caster].library[:] = [creature] + fillers

    spell = _cast(
        game, "Synthetic Permanent Dig", caster,
        "Reveal the top four cards of your library. You may put a permanent "
        "card from among them into your hand. Put the rest into your "
        "graveyard.")

    life_before = game.players[caster].life
    hand_names_before = {c.name for c in game.players[caster].hand}

    from engine.oracle_resolver import resolve_spell_from_oracle
    assert resolve_spell_from_oracle(game, spell, caster) is True

    # The permanent card is now in hand; a non-permanent was not chosen.
    hand_now = {c.name for c in game.players[caster].hand} - hand_names_before
    assert "Top Dork" in hand_now, "permanent card must be taken to hand"

    # Zero Bowmasters fired: impulse selection is not a draw (CR 121.1c).
    assert game.players[caster].life == life_before, (
        f"dig must not fire on-draw watchers; life {life_before} -> "
        f"{game.players[caster].life}")

    # The rest of the looked-at cards went to the graveyard.
    gy = {c.name for c in game.players[caster].graveyard}
    assert {"Top Bolt 0", "Top Bolt 1", "Top Bolt 2"} <= gy


def test_impulse_dig_colorless_predicate_keeps_only_colorless_card():
    """A colorless-predicate dig (Ancient Stirrings shape) keeps the
    colorless card and never a colored one."""
    game = _fresh_game()
    caster = 0

    colorless = _make_card(game, "Colorless Rock", caster, "library",
                           [CardType.ARTIFACT], colors=set(), cmc=2)
    colored = _make_card(game, "Green Beast", caster, "library",
                         [CardType.CREATURE], colors={Color.GREEN}, cmc=6)
    rest = [_make_card(game, f"Filler {i}", caster, "library",
                       [CardType.INSTANT], colors={Color.BLUE}, cmc=1)
            for i in range(3)]
    # colored beast is higher-CMC; a naive "best" pick would grab it if the
    # predicate were ignored.
    game.players[caster].library[:] = [colored, colorless] + rest

    spell = _cast(
        game, "Ancient Stirrings", caster,
        "Look at the top five cards of your library. You may reveal a "
        "colorless card from among them and put it into your hand. Then put "
        "the rest on the bottom of your library in any order.")

    from engine.oracle_resolver import resolve_spell_from_oracle
    resolve_spell_from_oracle(game, spell, caster)

    hand_names = {c.name for c in game.players[caster].hand}
    assert "Colorless Rock" in hand_names
    assert "Green Beast" not in hand_names, (
        "colorless predicate must not keep a colored card")


def test_impulse_dig_rest_moves_to_bottom_of_library_not_graveyard():
    """A 'put the rest on the bottom' dig leaves the unchosen looked-at
    cards in the library (at the bottom), not the graveyard."""
    game = _fresh_game()
    caster = 0

    keep = _make_card(game, "Kept Rock", caster, "library",
                      [CardType.ARTIFACT], colors=set(), cmc=2)
    rest = [_make_card(game, f"Filler {i}", caster, "library",
                       [CardType.INSTANT], colors={Color.BLUE}, cmc=1)
            for i in range(3)]
    game.players[caster].library[:] = [keep] + rest

    spell = _cast(
        game, "Ancient Stirrings", caster,
        "Look at the top five cards of your library. You may reveal a "
        "colorless card from among them and put it into your hand. Then put "
        "the rest on the bottom of your library in any order.")

    from engine.oracle_resolver import resolve_spell_from_oracle
    resolve_spell_from_oracle(game, spell, caster)

    assert not game.players[caster].graveyard, (
        "bottom-of-library dig must not mill the rest")
    lib_names = [c.name for c in game.players[caster].library]
    # The three fillers were moved to the bottom (end) of the library.
    assert lib_names[-3:] == ["Filler 0", "Filler 1", "Filler 2"]


def test_impulse_dig_look_count_derives_from_lands_you_control():
    """A dynamic dig (Consult the Star Charts: X = lands you control) looks
    at exactly as many cards as the caster controls lands."""
    game = _fresh_game()
    caster = 0

    for i in range(3):
        _make_card(game, f"Land {i}", caster, "battlefield",
                   [CardType.LAND], on_battlefield=True)

    # Top three (== land count) are visible; the fourth must stay untouched.
    top = [_make_card(game, f"Top {i}", caster, "library",
                      [CardType.INSTANT], colors={Color.RED}, cmc=1)
           for i in range(3)]
    hidden = _make_card(game, "Hidden", caster, "library",
                        [CardType.CREATURE], colors={Color.GREEN}, cmc=5)
    game.players[caster].library[:] = top + [hidden]

    spell = _cast(
        game, "Consult the Star Charts", caster,
        "Look at the top X cards of your library, where X is the number of "
        "lands you control. Put one of those cards into your hand. Put the "
        "rest on the bottom of your library in a random order.")

    from engine.oracle_resolver import resolve_spell_from_oracle
    resolve_spell_from_oracle(game, spell, caster)

    # Exactly one of the three looked-at cards went to hand; "Hidden" (the
    # card below the look window) was never seen, so it stays in the library
    # and is not the card taken.
    hand_names = {c.name for c in game.players[caster].hand}
    assert len(hand_names & {"Top 0", "Top 1", "Top 2"}) == 1
    assert "Hidden" in {c.name for c in game.players[caster].library}
    assert "Hidden" not in hand_names
