"""A sacrifice-cost activation must not cannibalise a live unbounded mana
engine. When the only permanent the AI could feed to a "sacrifice a
creature" cost is a member of an assembled unbounded mana loop
(CR 726.4 shortcut material — Devoted Druid + Vizier of Remedies, or any
other tap-for-mana + free-self-untap pair), the activation is not offered:
eating the engine to fetch mid-game value is a strictly worse board than
keeping the loop live. The rule is the engine-side predicate
`ActivationManager.engines_lost_if_removed` (the same one
`ai.clock._creature_static_value` prices), applied at the point the
sacrifice victim is chosen in `ai.activation_ev.activation_candidates`.

Class: every sacrifice-a-creature activation (Fiend Artisan, Birthing Pod,
altars, Viscera Seer, …) × every unbounded mana engine. Card names are
fixture carriers only.
"""
from __future__ import annotations

import random

from ai.activation_ev import activation_candidates
from ai.ev_evaluator import snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _bf(game, card_db, name, controller=0):
    t = card_db.get_card(name)
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _lib(game, card_db, names):
    for i, n in enumerate(names):
        c = CardInstance(template=card_db.get_card(n), owner=0, controller=0,
                         instance_id=500 + i, zone="library")
        c._game_state = game
        game.players[0].library.append(c)


def _artisan_candidates(game):
    game.current_phase = Phase.MAIN1  # activated tutors are main-phase plays
    snap = snapshot_from_game(game, 0)
    return [(reason, ev) for _p, _i, _t, ev, reason
            in activation_candidates(game, 0, snap)
            if reason.startswith("activate: tutor")]


# The tutor target is the deck's high-value payoff (Craterhoof Behemoth, MV 8)
# so the generic `ev > 0` gate is satisfied — the delivered value swamps the
# projected power loss — and the ONLY thing that can suppress the candidate is
# the engine gate. This isolates the bug from the "worthless fetch" case the
# position delta already handles.


def test_sac_tutor_is_not_offered_when_its_only_fodder_is_a_live_engine_member(card_db):
    """Druid + Vizier is an assembled unbounded engine; Fiend Artisan can
    only sacrifice "another creature", so its only fodder is an engine
    member. A high-value fetch swamps the projected loss of a low-power
    engine piece, so the tutor fires today. It must not: eating the loop
    for a mid-game body is a worse board than keeping the loop live."""
    game = GameState(rng=random.Random(0))
    for _ in range(6):
        _bf(game, card_db, "Forest")
    _bf(game, card_db, "Devoted Druid")       # taps for G
    _bf(game, card_db, "Vizier of Remedies")  # frees the -1/-1 untap → loop
    _bf(game, card_db, "Fiend Artisan")        # sac ANOTHER creature: tutor
    _lib(game, card_db, ["Craterhoof Behemoth"])  # the deck's payoff (MV 8)
    assert _artisan_candidates(game) == [], (
        "the only legal fodder is a live engine member — do not eat it")


def test_sac_tutor_is_still_offered_when_expendable_fodder_exists(card_db):
    """Same assembled engine, but now a non-engine creature is also on the
    board: the tutor IS offered — the victim is the expendable body, not
    the engine — so the gate does not over-suppress."""
    game = GameState(rng=random.Random(0))
    for _ in range(6):
        _bf(game, card_db, "Forest")
    _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    _bf(game, card_db, "Fiend Artisan")
    _bf(game, card_db, "Ornithopter")          # expendable fodder (0 mana)
    _lib(game, card_db, ["Craterhoof Behemoth"])
    cands = _artisan_candidates(game)
    assert cands, "an expendable body is available — the tutor is a candidate"


def test_a_non_engine_board_is_unaffected(card_db):
    """No unbounded engine on the board: the sacrifice gate never fires, so
    a Fiend Artisan whose only fodder is a plain creature still offers the
    tutor exactly as before."""
    game = GameState(rng=random.Random(0))
    for _ in range(6):
        _bf(game, card_db, "Forest")
    _bf(game, card_db, "Fiend Artisan")
    _bf(game, card_db, "Ornithopter")          # only fodder, but no engine
    # No loop mana here, so the fetch must be reachable off six lands; a
    # 0-power sacrifice keeps the projected cost near zero, so a modest
    # body still clears ev > 0.
    _lib(game, card_db, ["Eternal Witness"])   # MV 3, reachable off 6 mana
    cands = _artisan_candidates(game)
    assert cands, "no engine to protect — the tutor is a candidate"
