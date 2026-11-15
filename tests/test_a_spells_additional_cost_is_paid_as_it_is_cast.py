"""A spell's additional cost is paid as it is cast (CR 601.2b, 601.2f-h).

"As an additional cost to cast this spell, <cost>" is part of the spell's
total cost (CR 601.2f): its caster pays it as the spell is cast, whatever
route casts it (a spell cast without paying its mana cost still pays it,
CR 118.9), and a spell whose mandatory additional cost can't be paid can't
be cast (CR 601.2h). An optional one ("you may ...") may go unpaid. A
cost item the engine cannot pay keeps the spell from being cast rather
than letting it be cast for free.

The effect grammar typed each additional cost already (an ADDITIONAL_COST
host whose cost is the activation cost model); the cast procedure never
read it. Every such spell was cast for its mana cost alone: Grixis
Reanimator's four Abhorrent Oculus ("exile six cards from your
graveyard") came down as a three-mana 5/5 flier on an empty graveyard.
195 pool cards print a mandatory additional cost. The payer is the one
activations use (sacrifice, discard, an exile from the payer's graveyard,
life); which graveyard cards an exile takes is the payer's choice, asked
of it by the engine (escape's "exile N other cards" included).

Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone, template=None):
    card = CardInstance(template=template or card_db.get_card(name),
                        owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, land="Island", lands=4, callbacks=None):
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(lands):
        _add(game, card_db, land, 0, "battlefield")
    for _ in range(6):
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    return game


def _graveyard(game, card_db, n, name="Island"):
    return [_add(game, card_db, name, 0, "graveyard") for _ in range(n)]


def _resolve(game):
    while not game.stack.is_empty:
        game.resolve_stack()


# ── The cost, typed ───────────────────────────────────────────────────

def test_the_graveyard_exile_count_reads_every_number_word():
    from engine.oracle_parser import parse_activation_cost
    for word, n in (("four", 4), ("five", 5), ("six", 6), ("seven", 7)):
        cost = parse_activation_cost(
            f"Exile {word} cards from your graveyard")
        assert cost.exile_from_graveyard_cards == n and not cost.unpayable


def test_a_spells_additional_cost_is_read_from_its_typed_host(card_db):
    from engine.cast_manager import CastManager
    oculus = card_db.get_card("Abhorrent Oculus")
    assert CastManager.additional_cost(oculus).exile_from_graveyard_cards == 6
    assert CastManager.additional_cost(card_db.get_card(
        "Cathartic Reunion")).discard_cards == 2
    assert CastManager.additional_cost(card_db.get_card(
        "Lightning Bolt")) is None
    assert CastManager.additional_cost(card_db.get_card(
        "Requiting Hex")) is None, "an optional additional cost"


def test_the_typed_cost_is_the_grammars_reading_across_the_pool(card_db):
    """One reading of the sentence: the load-time field equals the cost of
    the effect grammar's ADDITIONAL_COST host (none when it is optional),
    for every pool card that prints one."""
    from engine.effect_spec import HostKind
    seen = 0
    for t in card_db.cards.values():
        if "additional cost to cast this spell" not in (
                t.oracle_text or "").lower():
            continue
        host = next((h for h in t.effects.front()
                     if h.kind is HostKind.ADDITIONAL_COST), None)
        if host is None:
            continue
        seen += 1
        want = None if "optional_cost" in host.flags else (
            host.cost.thaw() if host.cost is not None else None)
        got = t.additional_cost
        if want is None or got is None:
            assert want is None and (got is None or got.unpayable), t.name
        else:
            assert got == want, t.name
    assert seen >= 190


def test_paying_life_for_an_activation_is_losing_life(card_db):
    """CR 119.4, on the payer activations share: "Pay 7 life: Draw seven
    cards" loses its controller seven life this turn."""
    from engine.activation import ActivationManager
    game = _game(card_db)
    gris = _add(game, card_db, "Griselbrand", 0, "battlefield")
    for _ in range(7):
        _add(game, card_db, "Island", 0, "library")
    ability = next(a for a in gris.template.activated_abilities
                   if a.cost.life == 7)
    assert ActivationManager.activate(game, 0, gris, ability)
    assert game.players[0].life == 13
    assert game.players[0].life_lost_this_turn == 7


# ── A cost that can't be paid keeps the spell from being cast ─────────

def test_a_spell_whose_additional_cost_cannot_be_paid_is_not_cast(card_db):
    game = _game(card_db)
    _graveyard(game, card_db, 5)
    oculus = _add(game, card_db, "Abhorrent Oculus", 0, "hand")
    assert not game.can_cast(0, oculus)
    assert oculus not in game.get_legal_plays(0)
    assert not game.cast_spell(0, oculus, [])
    _graveyard(game, card_db, 1)
    assert game.can_cast(0, oculus)


def test_a_free_cast_still_pays_its_additional_cost(card_db):
    game = _game(card_db)
    _graveyard(game, card_db, 5)
    oculus = _add(game, card_db, "Abhorrent Oculus", 0, "hand")
    assert not game.cast_spell(0, oculus, [], free_cast=True)
    _graveyard(game, card_db, 2)
    assert game.cast_spell(0, oculus, [], free_cast=True)
    assert len(game.players[0].graveyard) == 1
    assert len(game.players[0].exile) == 6


@pytest.mark.parametrize("name, land, board", [
    ("Goblin Grenade", "Mountain", "Goblin Guide"),   # sacrifice a Goblin
    ("Toxic Deluge", "Swamp", None),                  # pay X life
])
def test_an_additional_cost_the_engine_cannot_pay_keeps_the_spell_uncast(
        card_db, name, land, board):
    game = _game(card_db, land=land)
    if board:
        _add(game, card_db, board, 0, "battlefield")
    spell = _add(game, card_db, name, 0, "hand")
    assert not game.can_cast(0, spell)


def test_an_optional_additional_cost_may_go_unpaid(card_db):
    game = _game(card_db, land="Swamp")
    _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    hex_ = _add(game, card_db, "Requiting Hex", 0, "hand")
    assert game.can_cast(0, hex_)


# ── The cost is paid as the spell is cast ─────────────────────────────

def test_an_exile_from_the_graveyard_is_paid_as_the_spell_is_cast(card_db):
    game = _game(card_db)
    _graveyard(game, card_db, 7)
    oculus = _add(game, card_db, "Abhorrent Oculus", 0, "hand")
    assert game.cast_spell(0, oculus, [])
    assert len(game.players[0].graveyard) == 1
    assert len(game.players[0].exile) == 6, "paid before it resolves"
    _resolve(game)
    assert oculus.zone == "battlefield"


def test_a_discard_is_paid_from_the_rest_of_the_hand(card_db):
    game = _game(card_db, land="Mountain", lands=3)
    kept = [_add(game, card_db, "Lightning Bolt", 0, "hand") for _ in range(3)]
    reunion = _add(game, card_db, "Cathartic Reunion", 0, "hand")
    assert game.cast_spell(0, reunion, [])
    assert sum(c.zone == "graveyard" for c in kept) == 2
    assert reunion.zone == "stack"


def test_a_sacrifice_is_paid_as_the_spell_is_cast(card_db):
    game = _game(card_db, land="Swamp", lands=1)
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    rites = _add(game, card_db, "Village Rites", 0, "hand")
    assert game.can_cast(0, rites)
    assert game.cast_spell(0, rites, [])
    assert bears.zone == "graveyard", "sacrificed before it resolves"
    game2 = _game(card_db, land="Swamp", lands=1)
    rites2 = _add(game2, card_db, "Village Rites", 0, "hand")
    assert not game2.can_cast(0, rites2), "no creature to sacrifice"


def test_a_life_payment_is_paid_and_is_life_lost(card_db):
    """CR 119.4: paying life is losing that much life."""
    template = CardTemplate(
        name="Life Bargain", card_types=[CardType.SORCERY],
        mana_cost=ManaCost(blue=1), supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None, keywords=set(),
        abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, tags=set(),
        oracle_text="As an additional cost to cast this spell, pay 3 "
                    "life.\nDraw a card.")
    game = _game(card_db, lands=1)
    spell = _add(game, card_db, None, 0, "hand", template=template)
    assert game.cast_spell(0, spell, [])
    assert game.players[0].life == 17
    assert game.players[0].life_lost_this_turn == 3
    game2 = _game(card_db, lands=1)
    game2.players[0].life = 2
    spell2 = _add(game2, card_db, None, 0, "hand", template=template)
    assert not game2.can_cast(0, spell2), "CR 119.4: not more than you have"


# ── Which graveyard cards an exile takes: the payer's choice ──────────

class _Chooser:
    def __init__(self):
        self.asked = []

    def __call__(self, game, player_idx, source, pool, n):
        self.asked.append((source, list(pool), n))
        return list(pool)[-n:]


def test_the_payer_chooses_which_graveyard_cards_an_exile_takes(card_db,
                                                                monkeypatch):
    game = _game(card_db)
    gy = _graveyard(game, card_db, 8)
    oculus = _add(game, card_db, "Abhorrent Oculus", 0, "hand")
    chooser = _Chooser()
    monkeypatch.setattr(game.callbacks, "choose_cost_exile", chooser)
    assert game.cast_spell(0, oculus, [])
    assert chooser.asked and chooser.asked[0][2] == 6
    assert [c.zone for c in gy] == ["graveyard"] * 2 + ["exile"] * 6


def test_escape_exiles_the_other_cards_its_payer_chooses(card_db,
                                                         monkeypatch):
    game = _game(card_db, land="Swamp", lands=2)
    for _ in range(2):
        _add(game, card_db, "Mountain", 0, "battlefield")
    gy = _graveyard(game, card_db, 6)
    kroxa = _add(game, card_db, "Kroxa, Titan of Death's Hunger", 0,
                 "graveyard")
    chooser = _Chooser()
    monkeypatch.setattr(game.callbacks, "choose_cost_exile", chooser)
    assert game.cast_spell(0, kroxa, [])
    pool = chooser.asked[0][1]
    assert kroxa not in pool and chooser.asked[0][2] == 5
    assert [c.zone for c in gy] == ["graveyard"] + ["exile"] * 5


def test_the_ai_exiles_what_serves_it_least_where_it_lies(card_db):
    """A reanimation pilot pays with the cards that are no resource there
    and keeps its reanimation target."""
    from ai.resolution_choices import pick_cost_exile
    game = _game(card_db)
    _add(game, card_db, "Persist", 0, "hand")
    archon = _add(game, card_db, "Archon of Cruelty", 0, "graveyard")
    rest = _graveyard(game, card_db, 6, name="Lightning Bolt")
    picked = pick_cost_exile(game, 0, None, game.players[0].graveyard, 6)
    assert archon not in picked and set(picked) == set(rest)


# ── The auditor (CR 601.2h) ───────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain()
            if f["rule"] == "601.2h/additional_cost_paid"]


def test_the_audit_records_a_spell_cast_without_its_additional_cost(
        card_db, audit, monkeypatch):
    from engine.activation import ActivationManager
    game = _game(card_db)
    _graveyard(game, card_db, 6)
    oculus = _add(game, card_db, "Abhorrent Oculus", 0, "hand")
    monkeypatch.setattr(ActivationManager, "pay_resource_costs",
                        staticmethod(lambda *a, **k: None))   # the defect
    assert game.cast_spell(0, oculus, [])
    assert _findings(audit)


def test_the_audit_is_silent_when_the_additional_cost_is_paid(card_db,
                                                              audit):
    game = _game(card_db)
    _graveyard(game, card_db, 6)
    oculus = _add(game, card_db, "Abhorrent Oculus", 0, "hand")
    assert game.cast_spell(0, oculus, [])
    assert not _findings(audit)
