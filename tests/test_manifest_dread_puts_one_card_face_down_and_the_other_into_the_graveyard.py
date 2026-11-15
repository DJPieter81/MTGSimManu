"""Manifest dread puts one of the top two cards of its player's library onto
the battlefield face down as a 2/2 creature and the other into their
graveyard (CR 701.62a, 701.40a). A face-down permanent has no
characteristics but those of a 2/2 creature: no name, mana cost, colour,
subtypes or text (CR 708.2a). Once it leaves the battlefield it is the card
itself, revealed (CR 708.9) -- and the abilities that look back at it as it
left are the ones it had face down, none (CR 603.10a).

"At the beginning of each opponent's upkeep, manifest dread." Its player
looks at the top two cards of their library, manifests one of them and
puts the other into their graveyard. With one card left they manifest it.

The defect: the grammar typed "manifest dread" as a keyword action, but no
executor performed it and the engine had no face-down permanent, so the
step carrier refused Abhorrent Oculus and the legacy step pass did nothing.

Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance, CardType
from engine.game_state import GameState, Phase

OCULUS = "Abhorrent Oculus"


def _game(callbacks=None):
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _library_top(game, card_db, names, idx=0):
    """`names[0]` on top; the library holds only these."""
    cards = [_put(game, card_db, n, "library", idx) for n in names]
    game.players[idx].library[:] = cards
    return cards


def _opponents_upkeep(game):
    from engine.turn_clock import Clock, ClockEvent, emit
    game.active_player = game.priority_player = 1
    game.current_phase = Phase.UPKEEP
    emit(game, ClockEvent(Clock.STEP_BEGINS, 1, step="upkeep"))
    while game.stack.items:
        game.resolve_stack()


class _Manifests(DefaultCallbacks):
    """Manifests the card named `name` of the two it is shown."""

    def __init__(self, name):
        self.name = name

    def choose_cards(self, ctx, spec, pool, n):
        return [c for c in pool if c.name == self.name][:n] or list(pool)[:n]


def _face_down(game, idx=0):
    return [c for c in game.players[idx].battlefield
            if getattr(c, "face_down", False)]


def _manifest(card_db, top, name, extra=()):
    game = _game(_Manifests(name))
    _put(game, card_db, OCULUS, "battlefield")
    cards = _library_top(game, card_db, list(top) + list(extra))
    _opponents_upkeep(game)
    return game, cards


# ── The rule ───────────────────────────────────────────────────────────

def test_manifest_dread_puts_one_of_the_top_two_face_down_and_the_other_into_the_graveyard(
        card_db):
    game, (bears, bolt, island) = _manifest(
        card_db, ["Grizzly Bears", "Lightning Bolt"], "Grizzly Bears",
        extra=["Island"])
    (fd,) = _face_down(game)
    assert fd is bears and bears.zone == "battlefield"
    assert bolt.zone == "graveyard" and bolt in game.players[0].graveyard
    assert game.players[0].library == [island]


def test_a_face_down_permanent_is_a_nameless_colourless_two_two_creature(
        card_db):
    # Any card is manifested as a 2/2 creature, an instant included.
    game, (bolt, angel) = _manifest(
        card_db, ["Lightning Bolt", "Serra Angel"], "Lightning Bolt")
    (fd,) = _face_down(game)
    assert fd is bolt
    assert (fd.power, fd.toughness) == (2, 2)
    assert set(fd.template.card_types) == {CardType.CREATURE}
    assert not fd.template.subtypes and not fd.template.supertypes
    assert fd.name == "" and not fd.colors
    assert not fd.template.oracle_text and not fd.keywords
    assert fd.template.cmc == 0


def test_a_face_down_creature_has_no_abilities_of_its_card(card_db):
    # Serra Angel flies face up; face down it is a 2/2 with no keyword.
    game, (angel, _bolt) = _manifest(
        card_db, ["Serra Angel", "Lightning Bolt"], "Serra Angel")
    (fd,) = _face_down(game)
    from engine.cards import Keyword
    assert Keyword.FLYING not in fd.keywords


def test_a_face_down_creature_entering_performs_none_of_its_cards_enter_abilities(
        card_db):
    # "When this creature enters, draw a card": face down, it has no text.
    game, _ = _manifest(card_db, ["Elvish Visionary", "Lightning Bolt"],
                        "Elvish Visionary", extra=["Island", "Island"])
    hand_before = len(game.players[0].hand)
    assert _face_down(game)
    assert len(game.players[0].hand) == hand_before


def test_once_it_leaves_the_battlefield_it_is_the_card_itself(card_db):
    game, (bears, _bolt) = _manifest(
        card_db, ["Grizzly Bears", "Lightning Bolt"], "Grizzly Bears")
    assert _face_down(game) == [bears] and bears.name == ""
    game.zone_mgr.move_card(game, bears, "battlefield", "hand", cause="test")
    assert bears.zone == "hand" and bears.name == "Grizzly Bears"
    assert not getattr(bears, "face_down", False)
    assert (bears.template.power, bears.template.toughness) == (2, 2)


def test_a_face_down_creature_that_dies_triggers_none_of_its_cards_dies_abilities(
        card_db):
    # "When this creature dies, create a 1/1 white Spirit creature token
    # with flying": the abilities that look back at it as it died are the
    # face-down permanent's -- none (CR 603.10a, 708.9).
    game, (traveler, _bolt) = _manifest(
        card_db, ["Doomed Traveler", "Lightning Bolt"], "Doomed Traveler")
    game._creature_dies(traveler, cause="test")
    assert traveler.zone == "graveyard" and traveler.name == "Doomed Traveler"
    assert not [c for c in game.players[0].battlefield
                if getattr(c, "is_token", False)]


def test_with_one_card_in_the_library_its_player_manifests_that_card(card_db):
    game, (bears,) = _manifest(card_db, ["Grizzly Bears"], "Grizzly Bears")
    assert _face_down(game) == [bears]
    assert not game.players[0].library and not game.players[0].graveyard


def test_the_ai_puts_into_the_graveyard_the_card_that_serves_its_plan_there(
        card_db, monkeypatch):
    # A face-down 2/2 is the same body whatever card it is, so the choice
    # is which card is worth more in the graveyard
    # (`ai.discard_advisor.serves_plan_from_graveyard`).
    import ai.discard_advisor as da
    from engine.game_runner import AICallbacks
    monkeypatch.setattr(da, "serves_plan_from_graveyard",
                        lambda game, idx, c: c.name == "Lightning Bolt")
    game = _game(AICallbacks())
    _put(game, card_db, OCULUS, "battlefield")
    bolt, bears = _library_top(game, card_db, ["Lightning Bolt",
                                               "Grizzly Bears"])
    _opponents_upkeep(game)
    assert _face_down(game) == [bears] and bolt.zone == "graveyard"


def test_a_log_shows_a_face_down_permanent_by_its_status_never_its_cards_name(
        card_db):
    # Display only: every rule reads the empty name (CR 708.2a).
    from engine.face_down import FACE_DOWN_LABEL, shown_name
    from engine.replay_log import snapshot_board
    game, (bears, _bolt) = _manifest(
        card_db, ["Grizzly Bears", "Lightning Bolt"], "Grizzly Bears")
    assert bears.name == "" and shown_name(bears) == FACE_DOWN_LABEL
    shown = [c["name"] for c in snapshot_board(game, 0)["creatures"]]
    assert FACE_DOWN_LABEL in shown and "Grizzly Bears" not in shown
    # It attacks and deals combat damage under the same label.
    from engine.combat_manager import CombatManager
    bears.summoning_sick = False
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.DECLARE_ATTACKERS
    combat = CombatManager()
    combat.declare_attackers(game, [bears], 0)
    while game.stack.items:
        game.resolve_stack()
    combat.declare_blockers(game, {})
    combat.resolve_combat_damage(game)
    attack_lines = [line for line in game.log
                    if "Attack with" in line or "dmg to" in line]
    assert attack_lines and all(FACE_DOWN_LABEL in line
                                for line in attack_lines)
    assert not [line for line in game.log if "Grizzly Bears" in line]


def test_the_block_reasoning_shows_a_face_down_attacker_by_its_status(
        card_db):
    # The AI's block-assignment line, which the replay pipeline parses.
    from ai.ev_player import EVPlayer
    from engine.face_down import FACE_DOWN_LABEL
    game, (bears, _bolt) = _manifest(
        card_db, ["Grizzly Bears", "Lightning Bolt"], "Grizzly Bears")
    blocker = _put(game, card_db, "Grizzly Bears", "battlefield", idx=1)
    ai = EVPlayer(player_idx=1, deck_name="Dimir Midrange",
                  rng=random.Random(0))
    ai._log_block_assignments(
        game, {bears.instance_id: [blocker.instance_id]},
        {bears.instance_id: bears}, {blocker.instance_id: blocker},
        bears.power, blocker.power, "BLOCK")
    (line,) = [entry for entry in game.log if "[BLOCK]" in entry]
    assert f"blocks {FACE_DOWN_LABEL} (2/2)" in line, line


# ── Every carrier whose family runs a keyword action performs it ───────
# The keyword executor (family tokens_counters) performs amass and
# manifest dread; the enter carrier and the spell family took no host with
# a keyword action, so these did nothing.

def _cast(game, card_db, name, lands, idx=0):
    for land in lands:
        _put(game, card_db, land, "battlefield", idx)
    card = _put(game, card_db, name, "hand", idx)
    assert game.cast_spell(idx, card, targets=[])
    while game.stack.items:
        game.resolve_stack()
    return card


def _army(game, idx=0):
    return [c for c in game.players[idx].battlefield
            if "Army" in (c.effective_subtypes or ())]


def test_an_enter_trigger_that_manifests_dread_performs_it(card_db):
    game = _game(_Manifests("Grizzly Bears"))
    bears, bolt, _island = _library_top(
        game, card_db, ["Grizzly Bears", "Lightning Bolt", "Island"])
    _cast(game, card_db, "Unsettling Twins", ["Plains"] * 4)
    assert _face_down(game) == [bears] and bolt.zone == "graveyard"


def test_a_spell_that_manifests_dread_performs_it(card_db):
    game = _game(_Manifests("Grizzly Bears"))
    bears, bolt, _island = _library_top(
        game, card_db, ["Grizzly Bears", "Lightning Bolt", "Island"])
    _cast(game, card_db, "Manifest Dread", ["Forest"] * 2)
    assert _face_down(game) == [bears] and bolt.zone == "graveyard"


def test_an_enter_trigger_that_amasses_performs_it(card_db):
    game = _game()
    _cast(game, card_db, "Vizier of the Scorpion", ["Swamp"] * 3)
    (army,) = _army(game)
    assert army.plus_counters == 1 and (army.power, army.toughness) == (1, 1)


def test_a_spell_that_amasses_performs_it(card_db):
    game = _game()
    _cast(game, card_db, "Relentless Advance", ["Island"] * 4)
    (army,) = _army(game)
    assert army.plus_counters == 3


def test_amassing_zero_still_creates_the_army_its_player_does_not_control(
        card_db):
    # CR 701.47a: with no Army, a 0/0 Army token is created whatever N is;
    # with no counter it dies as state-based actions are checked. X here
    # counts the creatures its player controls: none.
    from engine import effect_resolver as er
    from engine.effect_grammar import parse_text_effects
    game = _game()
    source = _put(game, card_db, "Island", "battlefield")
    host = parse_text_effects("Amass Zombies X, where X is the number of "
                              "creatures you control.").spell(0)
    assert er.resolve_ability(game, er.handle_of(source), 0, host, (),
                              family="tokens_counters", source_object=source)
    (army,) = _army(game)
    assert (army.power, army.toughness) == (0, 0)
    game.check_state_based_actions()
    assert not _army(game)
    assert game.players[0].creatures_died_this_turn == 1


def test_the_audit_records_an_amass_that_leaves_its_player_no_army(
        card_db, audit, monkeypatch):
    from engine.permanent_effects import PermanentEffects
    def no_army(game, controller, n, subtype):   # the defect: no token
        return game.players[controller].battlefield[0]
    monkeypatch.setattr(PermanentEffects, "amass", staticmethod(no_army))
    game = _game()
    _cast(game, card_db, "Vizier of the Scorpion", ["Swamp"] * 3)
    assert "701.47a/amass" in _violations(audit)


def test_the_audit_is_silent_when_the_amass_leaves_an_army(card_db, audit):
    game = _game()
    _cast(game, card_db, "Vizier of the Scorpion", ["Swamp"] * 3)
    assert _army(game)
    assert "701.47a/amass" not in _violations(audit)


def test_the_registered_member_resolves_from_its_typed_host(card_db):
    from engine.effect_carrier import step_plan
    assert step_plan(card_db.get_card(OCULUS).effects.faces[0]) is not None


# ── Auditor (CR 708.2a) ───────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f["rule"] for f in audit.drain() if f["kind"] == "violation"]


def test_the_audit_records_a_face_down_permanent_showing_its_cards_name(
        card_db, audit, monkeypatch):
    from engine import face_down
    real = face_down.face_down_template
    monkeypatch.setattr(face_down, "face_down_template",
                        lambda card: card.template)       # the defect: face up
    _manifest(card_db, ["Grizzly Bears", "Lightning Bolt"], "Grizzly Bears")
    assert "708.2a/face_down" in _violations(audit)
    assert real is not None


def test_the_audit_is_silent_when_the_permanent_is_face_down(card_db, audit):
    _manifest(card_db, ["Grizzly Bears", "Lightning Bolt"], "Grizzly Bears")
    assert "708.2a/face_down" not in _violations(audit)
