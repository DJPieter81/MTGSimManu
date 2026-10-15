"""A reflexive trigger follows the instruction that creates it (CR 603.12).

"<instruction>. When you do, [if <condition>,] <effect>": the instruction
creates the trigger, and only when it was performed during the resolution
(CR 603.12). Like any triggered ability, its intervening "if" is checked
when it triggers and again as it resolves (CR 603.4). Its targets are its
own: its controller chooses them, out of the legal choices, as it is put
on the stack after its parent has finished resolving (CR 603.3d). A
required target with no legal choice removes it.

The condition such a trigger most often carries counts permanents by a
description ("if you control a red permanent other than <this>"). The
description owner (`effect_conditions.permanent_described`, unit PF)
answers it, and "other" leaves out the trigger's source. The same count
reads any battlefield description in an instruction's own condition
("draw a card if you control a creature with a +1/+1 counter on it").

The defect: no host holding a reflexive trigger was in any family's strict
shape, so none resolved from its typed host. Ajani, Nacatl Avenger's 0
line (Boros Energy x4) went to its legacy kind's resolver, which read the
line as a number: no Cat token, 1 damage, and the condition ignored.

Card names are fixture carriers. The class is the 180 typed reflexive
hosts of the pool (269 cards print "when you do").
"""
from __future__ import annotations

import dataclasses
import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase

AJANI = "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger"


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, lands=("Mountain", "Plains", "Forest")):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for p in (0, 1):
        for name in lands:
            for _ in range(3):
                _add(game, card_db, name, p)
        for _ in range(4):
            _add(game, card_db, "Island", p, "library")
    return game


def _ajani(game, card_db):
    ajani = _add(game, card_db, AJANI, 0)
    ajani.is_transformed = True
    ajani.loyalty_counters = 3
    return ajani


def _cats(game, controller=0):
    return [c for c in game.players[controller].battlefield
            if getattr(c, "is_token", False) and "cat" in
            {str(s).lower() for s in c.effective_subtypes}]


def _zero_host(ajani):
    from engine.effect_carrier import loyalty_host
    line = ajani.template.back_face_loyalty_abilities["zero"]
    return line, loyalty_host(ajani, line)


# ── Through the loyalty carrier ───────────────────────────────────────

def test_the_trigger_resolves_after_its_instruction_when_its_condition_holds(
        card_db):
    """A red permanent other than the walker: the Cat token is created,
    then the walker deals damage equal to the creatures its controller
    controls -- the red creature and the Cat -- with no creature to aim
    at, to the opponent."""
    game = _game(card_db)
    ajani = _ajani(game, card_db)
    _add(game, card_db, "Monastery Swiftspear", 0)
    assert game.activate_planeswalker(0, ajani, "zero")
    assert len(_cats(game)) == 1
    assert game.players[1].life == 18


def test_a_false_intervening_if_means_the_trigger_does_nothing(card_db):
    """No red permanent other than the walker: the Cat token is still
    created, and no damage is dealt."""
    game = _game(card_db)
    ajani = _ajani(game, card_db)
    _add(game, card_db, "Grizzly Bears", 0)
    assert game.activate_planeswalker(0, ajani, "zero")
    assert len(_cats(game)) == 1
    assert game.players[1].life == 20


def test_its_targets_are_chosen_by_its_controller_as_it_goes_on_the_stack(
        card_db):
    """The trigger's own "any target" is its controller's pick
    (`callbacks.choose_trigger_targets`), asked once the parent has
    resolved -- the Cat is on the battlefield -- out of the legal
    choices; the pick is where the damage goes."""
    game = _game(card_db)
    ajani = _ajani(game, card_db)
    _add(game, card_db, "Monastery Swiftspear", 0)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    asked = []

    def pick(g, player_idx, source, spec, req, players, permanents,
             x_value=0):
        asked.append((player_idx, len(_cats(g)), list(players),
                      list(permanents)))
        return [bears]
    game.callbacks.choose_trigger_targets = pick
    assert game.activate_planeswalker(0, ajani, "zero")
    game.check_state_based_actions()
    assert len(asked) == 1
    controller, cats, players, permanents = asked[0]
    assert (controller, cats) == (0, 1), "asked of its controller, after"
    assert 1 in players and any(p is bears for p in permanents)
    assert bears.zone == "graveyard"
    assert game.players[1].life == 20


# ── The condition ─────────────────────────────────────────────────────

def test_the_condition_counts_what_its_description_names_other_than_the_source(
        card_db):
    """"If you control a red permanent other than <this>": the source
    itself is never counted, another red permanent is, and so is a
    permanent an effect has made red."""
    from engine import effect_conditions as ec
    game = _game(card_db)
    ajani = _ajani(game, card_db)
    _line, host = _zero_host(ajani)
    sub = next(s.payload for s in _walk(host) if _reflexive(s))
    cond = sub.host.trigger.intervening_if
    assert ec.state_condition_supported(cond)
    red = _add(game, card_db, "Monastery Swiftspear", 0)
    assert not ec.state_condition_holds(game, 0, cond, source=red)
    other = _add(game, card_db, "Raging Goblin", 0)
    assert ec.state_condition_holds(game, 0, cond, source=red)
    assert not ec.state_condition_holds(game, 1, cond, source=red), \
        "the opponent controls no red permanent"
    del other


def test_an_instructions_own_count_condition_reads_any_description(card_db):
    """"Draw a card if you control a creature with a +1/+1 counter on it"
    (an enter trigger): no such creature, no card; with one, a card."""
    game = _game(card_db)
    _add(game, card_db, "Grizzly Bears", 0)
    hand = len(game.players[0].hand)
    _cast(game, card_db, "Chronicler of Heroes")
    assert len(game.players[0].hand) == hand

    game = _game(card_db)
    bears = _add(game, card_db, "Grizzly Bears", 0)
    bears.add_plus_counters(1, game=game)
    hand = len(game.players[0].hand)
    _cast(game, card_db, "Chronicler of Heroes")
    assert len(game.players[0].hand) == hand + 1


def _cast(game, card_db, name, controller=0):
    card = _add(game, card_db, name, controller, "hand")
    assert game.cast_spell(controller, card, [])
    game.resolve_stack()
    return card


# ── On the dispatcher (synthetic variants of a real host) ─────────────

def _walk(host):
    from engine.effect_spec import iter_specs
    return list(iter_specs(host.specs))


def _reflexive(s):
    from engine.effect_spec import SubAbilityKind, Verb
    return (s.verb is Verb.CREATE_TRIGGER
            and s.payload.kind is SubAbilityKind.REFLEXIVE)


def _resolve(game, ajani, host):
    from engine import effect_resolver as er
    return er.resolve_ability(game, er.handle_of(ajani), 0, host, (),
                              family="tokens_counters", source_object=ajani)


def test_the_condition_is_checked_when_the_trigger_triggers(card_db):
    """CR 603.4: an intervening "if" false when the trigger event occurs
    means the ability does not trigger, even if the condition is true by
    the time it would be put on the stack. Here a later instruction of
    the parent creates a red Goblin after the Cat: the trigger was never
    created, so no damage is dealt."""
    game = _game(card_db)
    ajani = _ajani(game, card_db)
    _line, host = _zero_host(ajani)
    goblin = card_db.get_card("Goblin Instigator").effects.front()[0].specs[0]
    later = dataclasses.replace(goblin, seq=max(s.seq for s in _walk(host)) + 1)
    host = dataclasses.replace(host, specs=host.specs + (later,))
    assert _resolve(game, ajani, host)
    assert len(_cats(game)) == 1
    assert any("goblin" in {str(s).lower() for s in c.effective_subtypes}
               for c in game.players[0].battlefield)
    assert game.players[1].life == 20


def test_a_required_target_with_no_legal_choice_removes_the_trigger(card_db):
    """The trigger's target narrowed to "target creature an opponent
    controls": with none, it is removed (CR 603.3d) and nothing is dealt;
    with one, its controller chooses it."""
    from engine.effect_spec import SubAbility
    game = _game(card_db)
    ajani = _ajani(game, card_db)
    _add(game, card_db, "Monastery Swiftspear", 0)
    _line, host = _zero_host(ajani)

    def narrowed(s):
        if not _reflexive(s):
            return s
        sub = s.payload
        req = dataclasses.replace(sub.host.targets[0], zone="battlefield",
                                  types=frozenset({"creature"}),
                                  owner_scope="opponent")
        dmg = dataclasses.replace(sub.host.specs[0], target=req)
        sub_host = dataclasses.replace(sub.host, targets=(req,), specs=(dmg,))
        return dataclasses.replace(s, payload=SubAbility(
            kind=sub.kind, host=sub_host, timing=sub.timing))

    token = host.specs[0]
    host = dataclasses.replace(host, specs=(dataclasses.replace(
        token, then=tuple(narrowed(t) for t in token.then)),))
    assert _resolve(game, ajani, host)
    assert game.players[1].life == 20

    game = _game(card_db)
    ajani = _ajani(game, card_db)
    _add(game, card_db, "Monastery Swiftspear", 0)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    assert _resolve(game, ajani, host)
    assert bears.damage_marked == 2, \
        "the one legal choice, chosen by the engine's own pick"


# ── The AI values what the line does ──────────────────────────────────

def test_a_lines_reflexive_damage_is_valued_by_its_aim_while_its_condition_holds(
        card_db):
    """The walker's menu values a line by what the carrier performs: its
    reflexive damage is worth the aim the carrier's pick takes
    (`ai.damage_targets.choose_damage_recipient`) while its condition holds
    now, and nothing while it does not."""
    from ai.damage_targets import choose_damage_recipient
    from ai.pw_ability import reflexive_damage_value
    game = _game(card_db)
    ajani = _ajani(game, card_db)
    line, host = _zero_host(ajani)
    _add(game, card_db, "Grizzly Bears", 1)
    _add(game, card_db, "Grizzly Bears", 0)
    assert reflexive_damage_value(ajani, line, game, 0) == 0.0
    _add(game, card_db, "Monastery Swiftspear", 0)
    sub = next(s.payload for s in _walk(host) if _reflexive(s))
    aim = choose_damage_recipient(game, 0, ajani, 2, sub.host.targets[0],
                                  mana_committed=0)
    assert aim is not None and aim.value > 0
    assert reflexive_damage_value(ajani, line, game, 0) == aim.value


# ── Auditor (CR 603.12) ───────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _parent(host, *, made, nested):
    """The line creating `made` Cats -- none is an instruction that did
    nothing; `nested=False` re-creates a linker defect: the "when you do"
    trigger as a sibling of its instruction, not in its branch."""
    from engine.effect_spec import Amount, AmountKind
    token = host.specs[0]
    trigger = token.then[0]
    token = dataclasses.replace(token, amount=Amount(AmountKind.LITERAL,
                                                     n=made))
    if nested:
        return dataclasses.replace(host, specs=(token,))
    return dataclasses.replace(host, specs=(
        dataclasses.replace(token, then=()), trigger))


def test_the_audit_records_a_trigger_created_without_its_instruction(
        card_db, audit):
    game = _game(card_db)
    ajani = _ajani(game, card_db)
    _add(game, card_db, "Monastery Swiftspear", 0)
    _line, host = _zero_host(ajani)
    _resolve(game, ajani, _parent(host, made=0, nested=False))
    assert "603.12/reflexive_after_parent" in [f["rule"]
                                                for f in audit.drain()]


def test_the_audit_is_silent_when_the_trigger_follows_its_instruction(
        card_db, audit):
    game = _game(card_db)
    ajani = _ajani(game, card_db)
    _add(game, card_db, "Monastery Swiftspear", 0)
    _line, host = _zero_host(ajani)
    _resolve(game, ajani, _parent(host, made=0, nested=True))
    assert game.players[1].life == 20, "no Cat: no trigger, no damage"
    _resolve(game, ajani, _parent(host, made=1, nested=True))
    assert game.players[1].life < 20
    assert "603.12/reflexive_after_parent" not in [f["rule"]
                                                    for f in audit.drain()]
