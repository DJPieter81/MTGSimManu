"""A2 / M4 — the PANIC gear-shift must actually reach play selection.

Seed-anchored evidence (s60104, control vs artifact-aggro, Bo3): the
control player at panic life spent low-life turns landcycling and
passing Main 1 while holding castable removal.  Root cause: the M4
life-phase gear-shift applies its defensive up-weight MULTIPLICATIVELY
to a SIGNED EV — `ev *= 1.5` — so a removal spell whose projection EV
is negative (a card traded for a small creature usually is) gets
*more* negative at PANIC, gets pushed below `PLAY_VALUE_FLOOR`, and
the AI passes.  The gear-shift is not merely inert; it points
backwards for exactly the plays it exists to promote.

The fix is a sign-aware preference application
(`ai.strategy_profile.apply_preference_weight`): a weight w > 1 makes
the play strictly MORE attractive regardless of the EV's sign
(gains × w, costs ÷ w), and w < 1 strictly less attractive.  The
transformation is monotone-increasing in w for every fixed EV, which
is the property "up-weight" was always supposed to mean.

Class size: every defensive spell in Modern (removal / board_wipe /
counterspell / lifegain — thousands of cards) × every archetype with
a declared PANIC row.  No card names in the mechanism.
"""
from __future__ import annotations

import random

import pytest

from ai.clock import LifePhase, life_phase
from ai.ev_evaluator import snapshot_from_game, compute_play_ev
from ai.ev_player import EVPlayer
from ai.strategy_profile import (
    IDENTITY_PHASE_WEIGHTS,
    apply_preference_weight,
)
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


# ─────────────────────────────────────────────────────────────
# Rule 1 — preference weights are monotone in the weight, for
# EVERY sign of the base EV.  This is the algebraic property the
# M4 "up-weight" contract requires; plain multiplication violates
# it on the negative half-line.
# ─────────────────────────────────────────────────────────────


def test_preference_weight_is_monotone_in_weight():
    up, down = 1.5, 0.7  # any bonus / penalty pair; contract is direction

    # Identity weight is a no-op on both signs.
    for ev in (-8.0, -0.5, 0.0, 0.5, 8.0):
        assert apply_preference_weight(ev, IDENTITY_PHASE_WEIGHTS) == ev

    # Up-weight strictly raises every nonzero EV toward "more attractive".
    for ev in (-8.0, -0.5, 0.5, 8.0):
        assert apply_preference_weight(ev, up) > ev if ev < 0 else \
            apply_preference_weight(ev, up) > ev, (
                f"up-weight must make ev={ev} strictly more attractive"
            )

    # Down-weight strictly lowers every nonzero EV.
    for ev in (-8.0, -0.5, 0.5, 8.0):
        assert apply_preference_weight(ev, down) < ev if ev > 0 else \
            apply_preference_weight(ev, down) < ev, (
                f"down-weight must make ev={ev} strictly less attractive"
            )

    # No sign flips: a weight re-ranks, it never turns a cost into a
    # gain or vice versa.
    assert apply_preference_weight(-4.0, up) < 0
    assert apply_preference_weight(4.0, down) > 0

    # Monotone in w across a weight ladder, both signs.
    for ev in (-6.0, 6.0):
        applied = [apply_preference_weight(ev, w)
                   for w in (0.5, 0.8, 1.0, 1.3, 1.8)]
        assert applied == sorted(applied), (
            f"apply_preference_weight must be monotone-increasing in the "
            f"weight for ev={ev}, got {applied}"
        )


# ─────────────────────────────────────────────────────────────
# Fixture — the s60104 pattern: control player at PANIC life,
# small-creature aggro board opposite, castable removal in hand.
# Board shaped so the removal's raw projection EV is negative
# (card traded for a small body) — the exact class of play the
# pre-fix multiplier buried.
# ─────────────────────────────────────────────────────────────


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    else:
        game.players[controller].library.append(card)
    return card


def _make_panic_state(card_db):
    """Control at 11 life vs a 3-power board with no blockers: past the
    development window, my survival buffer strictly shorter than the
    opponent's → PANIC per the W0-B classifier.  Two untapped lands;
    hand holds cheap removal plus held-up interaction (the holdback
    shape from the s60104 trace where Main 1 was passed)."""
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Azorius Control"
    game.players[1].deck_name = "Affinity"
    game.players[0].life = 11
    game.players[1].life = 20
    game.turn_number = 6
    game.active_player = 0
    game.current_phase = Phase.MAIN1

    for n in ("Hallowed Fountain", "Island"):
        land = _add(game, card_db, n, 0, "battlefield")
        land.tapped = False
    _add(game, card_db, "Prismatic Ending", 0, "hand")
    _add(game, card_db, "Orim's Chant", 0, "hand")
    _add(game, card_db, "Counterspell", 0, "hand")
    _add(game, card_db, "Wrath of the Skies", 0, "hand")
    _add(game, card_db, "Stock Up", 0, "hand")
    for n in ("Island", "Island", "Flooded Strand"):
        _add(game, card_db, n, 0, "library")

    for n in ("Memnite", "Ornithopter", "Frogmite"):
        _add(game, card_db, n, 1, "battlefield")
    # Memnite as the real threat (Cranial Plating-equipped, per the
    # s60104 audit trace this fixture is named for): mana value 0, so
    # it is reachable by Prismatic Ending's Converge condition from a
    # two-land WU board (max 2 colors of mana spent) — unlike Frogmite
    # (mana value 4), which Converge can never reach at 2 colors no
    # matter how much mana is spent. Before the Converge-reachability
    # fix this distinction didn't matter because nothing checked it;
    # now the fixture's "real threat" must actually be one Prismatic
    # Ending could hit.
    memnite = next(c for c in game.players[1].creatures if c.name == "Memnite")
    memnite.temp_power_mod = 4
    for n in ("Spire of Industry", "Spire of Industry", "Darksteel Citadel"):
        land = _add(game, card_db, n, 1, "battlefield")
        land.tapped = False
    for n in ("Cranial Plating", "Frogmite", "Memnite", "Springleaf Drum",
              "Darksteel Citadel"):
        _add(game, card_db, n, 1, "library")
    return game


# ─────────────────────────────────────────────────────────────
# Rule 2 — at PANIC, the gear-shift must never score a defensive
# play LOWER than the identity multiplier would.  Pre-fix this is
# red: the removal's negative raw EV × 1.5 lands strictly below
# the identity EV, i.e. the "up-weight" demotes the play.
# ─────────────────────────────────────────────────────────────


def test_panic_upweight_never_scores_defensive_play_below_identity(
        card_db, monkeypatch):
    game = _make_panic_state(card_db)
    snap = snapshot_from_game(game, 0)
    assert life_phase(snap) is LifePhase.PANIC, (
        f"fixture invariant: expected PANIC, got {life_phase(snap)}"
    )

    me = game.players[0]
    removal = next(c for c in me.hand
                   if "removal" in getattr(c.template, "tags", set()))

    player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                      rng=random.Random(0))
    player.bhi.initialize_from_game(game)

    ev_geared = compute_play_ev(removal, snap, "control", game, 0,
                                bhi=player.bhi)

    from ai import ev_evaluator
    monkeypatch.setattr(ev_evaluator, "phase_weight_multiplier",
                        lambda archetype, phase, tags: 1.0)
    ev_identity = compute_play_ev(removal, snap, "control", game, 0,
                                  bhi=player.bhi)

    # The fixture used to assert `ev_identity < 0` ("the removal's raw
    # projection EV must be negative here"). That negativity was an
    # artefact of `position_value`'s inverted no-clock branch
    # (docs/diagnostics/2026-08-30_clock_sign_inversion_fix_falsified.md
    # names this fixture as the place the bug was load-bearing): with the
    # sign repaired, removing an attacker while holding no clock of your
    # own is correctly a gain. The rule under test is the multiplier's
    # monotonicity — the gear-shift may never DEMOTE a defensive play —
    # which holds regardless of the raw EV's sign.
    assert ev_geared >= ev_identity, (
        f"PANIC defensive up-weight must never DEMOTE a defensive play: "
        f"geared EV {ev_geared:.3f} < identity EV {ev_identity:.3f}. "
        f"Multiplying a negative EV by a bonus weight inverts the "
        f"gear-shift (finding A2, s60104 pass-at-panic pattern)."
    )


# ─────────────────────────────────────────────────────────────
# Rule 3 — the gear-shift reaches decide_main_phase: at panic
# life with a castable defensive play available, the turn is not
# passed.  Pre-fix red: the amplified negative EV falls below
# PLAY_VALUE_FLOOR and decide_main_phase returns None (the
# s60104 "passes Main 1 at 2 life" pattern).
# ─────────────────────────────────────────────────────────────


def test_defensive_cast_not_passed_at_panic_life(card_db):
    game = _make_panic_state(card_db)
    snap = snapshot_from_game(game, 0)
    assert life_phase(snap) is LifePhase.PANIC

    player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                      rng=random.Random(0))
    player.bhi.initialize_from_game(game)

    decision = player.decide_main_phase(game)
    assert decision is not None, (
        "at PANIC with castable removal in hand and mana available, "
        "the AI must not pass the main phase (s60104 G2 pattern: "
        "passed Main 1 at 2 life, then died). Candidates were: "
        + ", ".join(f"{p.ev:+.2f} {p.action}:{p.card.name}"
                    for p in player._last_candidates)
    )
    action, card, _targets = decision
    assert action == "cast_spell" and \
        "removal" in getattr(card.template, "tags", set()), (
            f"expected the defensive (removal-tagged) cast, got "
            f"{action}: {card.name}"
        )
