"""Combat tricks: a targeted pump resolves on its chosen target, is aimed
at the caster's own creature, and can be cast in the priority window that
follows the declare-blockers step (CR 509.4 / 117.3b).

Three defects, one class (~320 "target creature gets +N/+M until end of
turn [and gains <keyword>]" instants and sorceries):

1. **Parser** — the typed pump fields read only the bare "+N/+M until end
   of turn" shape; "+1/+0 and gains first strike until end of turn" parsed
   as no pump at all (137 of the 323 cards).
2. **Targets** — the two bespoke pump handlers ignored `targets` and pumped
   the controller's biggest creature, and the AI chose NO target for a
   beneficial pump (`_choose_targets` returned []), so the spell was cast
   with an empty target list and resolved doing nothing when the caster
   had no creature (a 2-life Phyrexian pip paid for nothing, Prowess vs
   WST v2 / Domain Zoo, seed 50000).
3. **Timing** — the runner's AFTER_BLOCKERS_DECLARED step was `pass`: no
   player ever received priority after blocks, so a trick could never be
   cast where it matters (unblocked lethal, flipping a trade).

Rules pinned: the typed bonus carries through a keyword grant; a pump
resolves on the creature it targets; the AI targets its own creature and
declines to cast with none; after blockers are declared the active player
is offered a combat trick, the opponent is offered a response to it, and
the AI casts a trick exactly when the projected post-combat position
improves (lethal, a flipped trade) and holds it when nothing changes.
Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, Keyword
from engine.combat_manager import CombatManager
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def _game(phase=Phase.MAIN1):
    game = GameState(rng=random.Random(0))
    game.current_phase = phase
    game.active_player = 0
    return game


def _ai(idx, deck="Izzet Prowess"):
    from ai.ev_player import EVPlayer
    return EVPlayer(player_idx=idx, deck_name=deck, rng=random.Random(0))


def _combat(game, attackers, blocks):
    """Declare `attackers` for player 0 and `blocks` ({atk_id: [blk_ids]})."""
    cm = CombatManager()
    game.current_phase = Phase.DECLARE_ATTACKERS
    cm.declare_attackers(game, attackers, 0)
    game.current_phase = Phase.DECLARE_BLOCKERS
    cm.declare_blockers(game, blocks)
    return cm


# ─── 1. Parser: the bonus carries through a keyword grant ─────────────


def test_a_pump_that_also_grants_a_keyword_is_typed_with_its_bonus(card_db):
    from engine.oracle_parser import parse_pump_spell
    assert parse_pump_spell(
        "Target creature gets +1/+0 and gains first strike until end of turn."
    ) == (1, 0, "first strike")
    assert parse_pump_spell(
        "Target creature you control gets +2/+2 and gains hexproof until end "
        "of turn. (It can't be the target of spells or abilities your "
        "opponents control.)") == (2, 2, "hexproof")
    # The bare shape is unchanged.
    assert parse_pump_spell(
        "Target creature gets +3/+3 until end of turn.") == (3, 3, "")


# ─── 2. A pump resolves on the creature it targets ────────────────────


def test_a_bespoke_pump_handler_pumps_its_chosen_target_not_the_biggest_creature(card_db):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    game = _game()
    small = _add(game, card_db, "Memnite", 0, "battlefield")          # 1/1
    big = _add(game, card_db, "Watchwolf", 0, "battlefield")          # 3/3
    for name, bonus, kw in (("Mutagenic Growth", (2, 2), None),
                            ("Violent Urge", (1, 0), Keyword.FIRST_STRIKE)):
        spell = _add(game, card_db, name, 0, "hand")
        p0, t0, bp0 = small.power, small.toughness, big.power
        assert EFFECT_REGISTRY.execute(name, EffectTiming.SPELL_RESOLVE, game,
                                       spell, 0, targets=[small.instance_id],
                                       item=None)
        assert (small.power, small.toughness) == (p0 + bonus[0], t0 + bonus[1]), (
            f"{name} must pump the creature it targets")
        assert big.power == bp0, f"{name} pumped the biggest creature instead of its target"
        if kw is not None:
            assert kw in small.keywords


# ─── 3. The AI aims a beneficial pump at its own creature ─────────────


def test_a_beneficial_pump_targets_the_casters_own_creature_and_none_means_no_cast(card_db):
    game = _game()
    _add(game, card_db, "Mountain", 0, "battlefield")
    rage = _add(game, card_db, "Monstrous Rage", 0, "hand")
    _add(game, card_db, "Grizzly Bears", 1, "battlefield")
    mine = _add(game, card_db, "Memnite", 0, "battlefield")
    ai = _ai(0)
    assert ai._choose_targets(game, rage) == [mine.instance_id], (
        "a beneficial pump goes on the caster's own creature")
    game.players[0].battlefield.remove(mine)
    assert ai._choose_targets(game, rage) == [], (
        "with no own creature a beneficial pump has no target — the opposing "
        "creature is legal but never the choice")
    assert ai._spell_requires_targets(rage), (
        "a targeted pump with no chosen target must be skipped, not cast blank")


# ─── 4. The post-block priority window exists and is answerable ───────


def test_after_blockers_the_active_player_may_cast_a_trick_and_the_opponent_may_respond(card_db):
    from engine.game_runner import GameRunner
    game = _game()
    _add(game, card_db, "Mountain", 0, "battlefield")
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    rage = _add(game, card_db, "Monstrous Rage", 0, "hand")
    _add(game, card_db, "Island", 1, "battlefield")
    _add(game, card_db, "Island", 1, "battlefield")
    cm = _combat(game, [bears], {})

    active_ai, opp_ai = _ai(0), _ai(1, "Azorius Control")
    calls, offered = [], []

    def _trick(g, combat):
        calls.append(combat)
        return (rage, [bears.instance_id]) if not offered else None

    def _record(g, stack_item):
        offered.append(stack_item.source.name)
        return None

    active_ai.decide_combat_trick = _trick
    opp_ai.decide_response = _record
    runner = GameRunner(card_db=card_db)
    base = bears.power
    runner._combat_trick_window(game, active_ai, opp_ai, cm)

    assert calls and calls[0] is cm, "the active player must be offered a trick after blocks"
    assert rage not in game.players[0].hand, "the returned trick must be cast"
    assert offered == ["Monstrous Rage"], (
        f"the defending player must be offered a response (offered: {offered})")
    assert bears.power == base + 2, "the trick must resolve before combat damage"


# ─── 5. The AI casts a trick exactly when the post-combat position improves ──


def test_a_pump_that_makes_an_unblocked_attacker_lethal_is_cast(card_db):
    game = _game()
    game.players[1].life = 3
    _add(game, card_db, "Mountain", 0, "battlefield")
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")   # 2/2, unblocked
    rage = _add(game, card_db, "Monstrous Rage", 0, "hand")         # +2/+0
    cm = _combat(game, [bears], {})
    assert _ai(0).decide_combat_trick(game, cm) == (rage, [bears.instance_id])


def test_a_pump_that_flips_a_losing_trade_is_cast(card_db):
    game = _game()
    _add(game, card_db, "Forest", 0, "battlefield")
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")   # 2/2
    growth = _add(game, card_db, "Giant Growth", 0, "hand")          # +3/+3
    wolf = _add(game, card_db, "Watchwolf", 1, "battlefield")        # 3/3 blocks
    cm = _combat(game, [bears], {bears.instance_id: [wolf.instance_id]})
    assert _ai(0, "Domain Zoo").decide_combat_trick(game, cm) == (growth, [bears.instance_id]), (
        "the pump turns 'attacker dies, blocker lives' into the reverse")


def test_a_pump_that_changes_no_combat_outcome_is_held(card_db):
    game = _game()
    _add(game, card_db, "Forest", 0, "battlefield")
    memnite = _add(game, card_db, "Memnite", 0, "battlefield")       # 1/1
    _add(game, card_db, "Giant Growth", 0, "hand")                   # 4/4 still dies
    wall = _add(game, card_db, "Colossal Dreadmaw", 1, "battlefield")  # 6/6 blocks
    cm = _combat(game, [memnite], {memnite.instance_id: [wall.instance_id]})
    assert _ai(0, "Domain Zoo").decide_combat_trick(game, cm) is None, (
        "a trick that saves nothing and kills nothing is a card for nothing")


# ─── 6. Attack lethal counts the pump reach in hand ───────────────────


def test_attack_lethal_counts_the_castable_pump_reach_in_hand(card_db):
    game = _game()
    game.players[1].life = 6
    _add(game, card_db, "Mountain", 0, "battlefield")
    _add(game, card_db, "Forest", 0, "battlefield")
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")   # 2 on board
    _add(game, card_db, "Colossal Dreadmaw", 1, "battlefield")       # 6/6 untapped blocker
    ai = _ai(0)
    assert ai.decide_attackers(game) == [], (
        "fixture: without the tricks a 2/2 does not attack into a 6/6 at 6 life")
    _add(game, card_db, "Giant Growth", 0, "hand")                   # +3 reach → 5 < 6
    assert ai.decide_attackers(game) == [], "one pump short of lethal stays home"
    _add(game, card_db, "Monstrous Rage", 0, "hand")                 # +2 more → 7 ≥ 6
    assert ai.decide_attackers(game) == [bears], (
        "on-board power plus the castable pump reach (both packed into the "
        "two open mana) is lethal if unblocked — the same rule the on-board "
        "lethal alpha strike applies")
