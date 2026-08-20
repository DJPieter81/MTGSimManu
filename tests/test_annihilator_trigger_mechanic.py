"""Annihilator attack-trigger mechanic (CR 702.86).

Rules under test
----------------
CR 702.86a:
    Annihilator N is a triggered ability that fires when the creature
    attacks.  The trigger reads "Whenever this creature attacks, defending
    player sacrifices N permanents."

CR 702.86b:
    The N in "annihilator N" is the *printed* value; the trigger always
    forces exactly that many sacrifices (or as many permanents as the
    defending player controls, if fewer than N remain).

These are mechanic-driven tests — no test names a card.  Any creature
with the annihilator keyword and the N encoded in oracle text will hit
this code path (Emrakul the Aeons Torn, Kozilek, Ulamog, Pathrazer,
It That Betrays, Nulldrifter, …).

Tests
-----
1. annihilator_n_forces_n_permanent_sacrifices_on_attack — oracle text
   says "annihilator 4", defending player has 6 permanents, exactly 4
   are removed.
2. annihilator_n_parsed_from_oracle_not_defaulting_to_2 — oracle text
   says "annihilator 6", confirms 6 sacrifices, NOT the former default-2.
3. annihilator_n_caps_at_defenders_total_permanent_count — N=4 but
   defending player only has 2 permanents; both are sacrificed, count=2.
4. annihilator_trigger_fires_on_attack_not_on_damage — trigger must fire
   in the attack-trigger window, before any combat damage step.
5. annihilator_1_forces_single_sacrifice — smallest legal N; exactly 1
   permanent removed.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, Keyword
from engine.game_state import GameState
from engine.mana import ManaCost
from engine.triggers import TriggerManager


# ── helpers ──────────────────────────────────────────────────────────────────


def _fresh_game() -> GameState:
    return GameState(rng=random.Random(42))


def _creature(
    game: GameState,
    name: str,
    controller: int,
    *,
    power: int = 2,
    toughness: int = 2,
    keywords: set | None = None,
    oracle_text: str = "",
) -> CardInstance:
    """Place a creature on the battlefield for *controller*."""
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=2),
        supertypes=[],
        subtypes=[],
        power=power,
        toughness=toughness,
        loyalty=None,
        keywords=set(keywords or ()),
        abilities=[],
        color_identity=set(),
        produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle_text,
        tags=set(),
    )
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _permanent(
    game: GameState,
    name: str,
    controller: int,
    cmc: int = 1,
) -> CardInstance:
    """Place a generic non-creature permanent on *controller*'s battlefield."""
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.ARTIFACT],
        mana_cost=ManaCost(generic=cmc),
        supertypes=[],
        subtypes=[],
        power=None,
        toughness=None,
        loyalty=None,
        keywords=set(),
        abilities=[],
        color_identity=set(),
        produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


def _annihilator_creature(
    game: GameState,
    controller: int,
    n: int,
) -> CardInstance:
    """Return a creature with 'annihilator N' in its oracle text and keyword."""
    oracle = (
        f"Annihilator {n} (Whenever this creature attacks, defending player "
        f"sacrifices {n} permanents of their choice.)"
    )
    return _creature(
        game,
        name=f"Annihilator{n} Test Creature",
        controller=controller,
        power=n + 4,
        toughness=n + 4,
        keywords={Keyword.ANNIHILATOR},
        oracle_text=oracle,
    )


# ── 1. N=4: exactly 4 permanents sacrificed ───────────────────────────────────


def test_annihilator_n_forces_n_permanent_sacrifices_on_attack():
    """Annihilator N trigger sacrifices exactly N permanents (CR 702.86).

    Defender has 6 permanents; after the trigger resolves only 2 remain.
    """
    game = _fresh_game()
    attacker_ctrl = 0
    defender_ctrl = 1

    attacker = _annihilator_creature(game, attacker_ctrl, n=4)

    # Defender controls 6 permanents
    for i in range(6):
        _permanent(game, f"Defender Permanent {i}", defender_ctrl, cmc=i + 1)

    before = len(game.players[defender_ctrl].battlefield)
    assert before == 6, f"setup: expected 6 permanents, got {before}"

    TriggerManager.trigger_attack(game, attacker, attacker_ctrl)

    after = len(game.players[defender_ctrl].battlefield)
    sacrificed = before - after
    assert sacrificed == 4, (
        f"annihilator 4 must sacrifice exactly 4 permanents; got {sacrificed}"
    )


# ── 2. N=6: oracle N != default-2 ────────────────────────────────────────────


def test_annihilator_n_parsed_from_oracle_not_defaulting_to_2():
    """Annihilator 6 in oracle text forces 6 sacrifices, not the former default of 2.

    The old code searched template.abilities[*].description for 'annihilator N'
    (no ability description ever contains that text) and fell back to a hardcoded
    2.  The correct implementation parses oracle_text directly.
    """
    game = _fresh_game()
    attacker_ctrl = 0
    defender_ctrl = 1

    attacker = _annihilator_creature(game, attacker_ctrl, n=6)

    for i in range(8):
        _permanent(game, f"Perm {i}", defender_ctrl, cmc=i + 1)

    before = len(game.players[defender_ctrl].battlefield)
    TriggerManager.trigger_attack(game, attacker, attacker_ctrl)
    after = len(game.players[defender_ctrl].battlefield)
    sacrificed = before - after

    assert sacrificed == 6, (
        f"annihilator 6 must sacrifice 6 permanents; "
        f"got {sacrificed} (likely fell back to the old default of 2)"
    )


# ── 3. Fewer permanents than N — caps at what's available ─────────────────────


def test_annihilator_n_caps_at_defenders_total_permanent_count():
    """When defending player has fewer permanents than N, all of them are sacrificed.

    CR 702.86 says 'sacrifices N permanents'; if fewer than N exist the
    player sacrifices everything they have (cannot sacrifice what isn't there).
    """
    game = _fresh_game()
    attacker_ctrl = 0
    defender_ctrl = 1

    attacker = _annihilator_creature(game, attacker_ctrl, n=4)

    # Defender has only 2 permanents
    for i in range(2):
        _permanent(game, f"Scarce Perm {i}", defender_ctrl, cmc=i + 1)

    before = len(game.players[defender_ctrl].battlefield)
    assert before == 2

    TriggerManager.trigger_attack(game, attacker, attacker_ctrl)

    after = len(game.players[defender_ctrl].battlefield)
    assert after == 0, (
        f"all {before} permanents should be sacrificed when N > permanents available"
    )


# ── 4. Trigger fires on attack declaration, not on damage ─────────────────────


def test_annihilator_trigger_fires_on_attack_not_on_damage():
    """The annihilator trigger fires when the creature is declared as attacker
    (attack-trigger window, CR 702.86a + CR 603.6e), before combat damage.

    We verify by calling trigger_attack directly and confirming sacrifice
    happens immediately — no combat damage step is needed.
    """
    game = _fresh_game()
    attacker_ctrl = 0
    defender_ctrl = 1

    attacker = _annihilator_creature(game, attacker_ctrl, n=2)
    perm = _permanent(game, "Sole Permanent", defender_ctrl)

    assert perm in game.players[defender_ctrl].battlefield

    TriggerManager.trigger_attack(game, attacker, attacker_ctrl)

    # The permanent should be gone purely from the attack-trigger window
    assert perm not in game.players[defender_ctrl].battlefield, (
        "annihilator must sacrifice on attack declaration, not on damage"
    )


# ── 5. Annihilator 1 — smallest N ─────────────────────────────────────────────


def test_annihilator_1_forces_single_sacrifice():
    """Annihilator 1 forces exactly one sacrifice (boundary: smallest legal N).

    Cards like Nulldrifter, Hand of Emrakul, Spawnsire, and Eldrazi Ravager
    carry annihilator 1.  The trigger must not over-count.
    """
    game = _fresh_game()
    attacker_ctrl = 0
    defender_ctrl = 1

    attacker = _annihilator_creature(game, attacker_ctrl, n=1)

    for i in range(4):
        _permanent(game, f"Perm {i}", defender_ctrl)

    before = len(game.players[defender_ctrl].battlefield)
    TriggerManager.trigger_attack(game, attacker, attacker_ctrl)
    after = len(game.players[defender_ctrl].battlefield)

    assert before - after == 1, (
        f"annihilator 1 must sacrifice exactly 1 permanent; "
        f"got {before - after}"
    )
