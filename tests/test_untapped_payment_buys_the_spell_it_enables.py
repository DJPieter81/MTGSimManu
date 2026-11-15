"""A "pay life: this land enters untapped" payment is worth the spell it
enables this turn — and nothing when it enables none.

The pay/skip decision compared bare snapshot deltas (life down, one more
untapped mana). `position_value`'s mana term is a surplus over the
opponent's, not "can I cast my two-drop now", so once life was priced
honestly the payment always read as a loss: a control deck fetched its
shock tapped at 18 life and left a turn-two sweeper uncast (WR anchor pin
Pinnacle Affinity vs 4/5c Control s50000). The decision now also offers
one pay-variant per spell in hand castable only with the extra mana of
the land's colours, projected through the same spell projection every
cast is scored with.

Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_runner import AICallbacks
from engine.game_state import GameState, Phase
from engine.optional_costs import parse_optional_costs


def _card(game, card_db, name, controller, zone):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        game.players[controller].battlefield.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "4/5c Control"
    game.players[1].deck_name = "Pinnacle Affinity"
    game.turn_number = 3
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    plains = _card(game, card_db, "Plains", 0, "battlefield")
    plains.tapped = False
    shock = _card(game, card_db, "Sacred Foundry", 0, "battlefield")
    shock.tapped = True  # just entered; the payment is being offered
    for n in ("Memnite", "Ornithopter", "Frogmite"):
        _card(game, card_db, n, 1, "battlefield")
    return game, shock


def _offer(game, shock):
    opts = parse_optional_costs(shock, trigger="etb")
    assert opts, "fixture: the shock must offer its untapped payment"
    return opts[0]


def test_payment_is_taken_when_it_enables_a_spell_this_turn(card_db):
    game, shock = _game(card_db)
    _card(game, card_db, "Wrath of the Skies", 0, "hand")  # {W}{W}: needs both
    cb = AICallbacks()
    assert cb.decide_optional_cost(game, 0, _offer(game, shock)) is True


def test_payment_is_skipped_when_nothing_is_enabled(card_db):
    game, shock = _game(card_db)
    _card(game, card_db, "Solitude", 0, "hand")  # five mana: unreachable
    cb = AICallbacks()
    assert cb.decide_optional_cost(game, 0, _offer(game, shock)) is False


def test_payment_buys_the_marginal_spell_not_a_single_spells_cost(card_db):
    """Two one-drops on one untapped land: each is castable without paying,
    but only the payment lets BOTH be cast — the marginal spell is what the
    untapped land buys."""
    game, shock = _game(card_db)
    _card(game, card_db, "Lightning Bolt", 0, "hand")
    _card(game, card_db, "Ragavan, Nimble Pilferer", 0, "hand")
    # give the shock a red-capable sibling so colours are reachable
    cb = AICallbacks()
    assert cb.decide_optional_cost(game, 0, _offer(game, shock)) is True


def test_each_spell_the_payment_enables_is_priced_not_only_one_packing(
        card_db, monkeypatch):
    """Two two-drops on one untapped land: the payment enables either one
    (not both). The packing that fits the budget keeps only one of them, so
    the other -- a cost reducer here -- was never priced and the payment
    was declined. Each spell the payment alone enables is its own pay
    variant."""
    import ai.decision_kernel as dk
    game, shock = _game(card_db)
    game.players[0].deck_name = "Ruby Storm"
    for c in game.players[0].battlefield:
        if c.name == "Plains":
            game.players[0].battlefield.remove(c)
    _card(game, card_db, "Mountain", 0, "battlefield").tapped = False
    _card(game, card_db, "Manamorphose", 0, "hand")
    _card(game, card_db, "Ral, Monsoon Mage // Ral, Leyline Prodigy", 0, "hand")
    seen = []
    real = dk.best_choice

    def spy(g, idx, arch, choices, **kw):
        seen.extend(c.name for c in choices)
        return real(g, idx, arch, choices, **kw)
    monkeypatch.setattr(dk, "best_choice", spy)
    AICallbacks().decide_optional_cost(game, 0, _offer(game, shock))
    casts = {n.split("-> cast ")[1] for n in seen if "-> cast " in n}
    assert {"Manamorphose",
            "Ral, Monsoon Mage // Ral, Leyline Prodigy"} <= casts, seen


# ── A spell is enabled only if it is castable with the land untapped ──
# (adversarial review round 2: the colour check compared colour sets, not
# pips; hybrid and Phyrexian pips were invisible). The one castability
# owner, `GameState.can_cast`, decides: an enabled spell is one it rejects
# now and accepts with the paying land untapped.

def _bare_game(card_db, untapped, shock):
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "4/5c Control"
    game.players[1].deck_name = "Boros Energy"
    game.turn_number = 3
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for name in untapped:
        _card(game, card_db, name, 0, "battlefield").tapped = False
    land = _card(game, card_db, shock, 0, "battlefield")
    land.tapped = True
    return game, land


def _cast_names(game, land, monkeypatch):
    import ai.decision_kernel as dk
    seen = []
    real = dk.best_choice

    def spy(g, idx, arch, choices, **kw):
        seen.extend(c.name for c in choices)
        return real(g, idx, arch, choices, **kw)
    monkeypatch.setattr(dk, "best_choice", spy)
    paid = AICallbacks().decide_optional_cost(game, 0, _offer(game, land))
    return paid, {n.split("-> cast ")[1] for n in seen if "-> cast " in n}


def test_a_spell_needing_two_pips_of_a_colour_the_land_does_not_make_is_not_enabled(
        card_db, monkeypatch):
    """{W}{W} off a Plains and a Steam Vents: the Vents makes no white."""
    game, vents = _bare_game(card_db, ["Plains"], "Steam Vents")
    _card(game, card_db, "Counterspell", 0, "hand")      # {U}{U}: one blue only
    paid, casts = _cast_names(game, vents, monkeypatch)
    assert "Counterspell" not in casts
    assert paid is False


def test_a_hybrid_spell_no_source_can_pay_is_not_bought(card_db, monkeypatch):
    """{R/G}{R/G} off an Island and a Sacred Foundry: one red source only."""
    game, foundry = _bare_game(card_db, ["Island"], "Sacred Foundry")
    _card(game, card_db, "Burning-Tree Emissary", 0, "hand")
    paid, casts = _cast_names(game, foundry, monkeypatch)
    assert "Burning-Tree Emissary" not in casts
    assert paid is False
