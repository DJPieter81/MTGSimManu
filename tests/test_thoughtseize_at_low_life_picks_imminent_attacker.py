"""W1b-11 — Discard advisor must prefer the imminently-castable
attacker when the defender is in panic-life territory.

Audit (2026-05-16 5-panel Bo3, Aggro Pattern C):

    Dimir at 4 life cast Thoughtseize against a hand containing
    Goblin Bombardment + Ragavan. The discard advisor stripped the
    Bombardment (higher static score, sacrifice engine) and left
    Ragavan in hand. Opponent untapped, played Ragavan, swung the
    next turn for game.

    Bombardment cannot fire without a creature to sacrifice, and the
    opp's board was empty.  Ragavan is castable with the lands the
    opp already has and represents the only on-curve next-turn
    attacker.  At panic life the right rule is "rip the imminent
    attacker," not "rip the highest-static-score card."

Mechanic-level fix (no card names):

    The advisor composes two existing primitives:

      * ``ai.mana_planner.effective_cmc(card, player)`` — the
        rules-correct CMC after domain / affinity / metalcraft cost
        reduction (W0-F).
      * the opp's currently-available mana (``opp_total_lands`` /
        ``opp_mana`` on the EVSnapshot) plus the rules-constant
        "one land drop per turn".

    ``bhi.predicted_turn_of_cast(card, snap)`` returns the earliest
    turn the opp can pay the card's effective mana cost.  At low
    life ("``my_life ≤ 2 × opp_avg_attack``" — derived condition:
    the defender dies in two combat steps) the advisor downranks
    cards whose ``predicted_turn_of_cast`` is far away in favour of
    cards castable next turn.

    No magic life threshold, no card names, no deck names.  The
    "2 × avg attack" expression is the standard `combat_clock`-style
    "turns to lethal" idiom: integer multiplier ``2`` of a snapshot-
    derived per-attacker damage average.

These tests pin three properties:

    1. Panic-life Dimir picks Ragavan over Bombardment.
    2. Non-panic-life Dimir falls back to the static CMC / score
       ranking (no behavioural regression away from the panic case).
    3. ``predicted_turn_of_cast`` composes ``effective_cmc`` rather
       than reading ``card.cmc`` directly (so domain / affinity cost
       reduction flows through correctly).
    4. The touched code in ``ai/bhi.py`` and ``ai/discard_advisor.py``
       contains no new bare life thresholds (``< 4``, ``<= 5``, etc.)
       — guardrail against the "magic number" anti-pattern.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState
from engine.game_runner import AICallbacks


def _make_game() -> GameState:
    """Wire the game state to AICallbacks so the discard path routes
    through ``ai.discard_advisor`` rather than the default raw-CMC
    sort."""
    return GameState(rng=random.Random(0), callbacks=AICallbacks())


def _make_card(game: GameState, card_db: CardDatabase, name: str,
               controller: int) -> CardInstance:
    tmpl = card_db.cards.get(name)
    assert tmpl is not None, f"missing card in DB: {name!r}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    card._game_state = game
    return card


def _build_hand(game: GameState, card_db: CardDatabase, player_idx: int,
                card_names, deck_name: str):
    player = game.players[player_idx]
    player.deck_name = deck_name
    player.hand = [_make_card(game, card_db, n, player_idx) for n in card_names]
    return player.hand


def _put_vanilla_attacker_on_battlefield(game: GameState, controller: int,
                                          power: int, toughness: int,
                                          name: str = "Test Vanilla 3/3"
                                          ) -> CardInstance:
    """Add a synthetic vanilla creature with no oracle text, so its
    contribution to ``snap.opp_power`` is exactly the requested ``power``.
    Used to produce a deterministic ``opp_avg_attack`` for the panic-life
    derivation without relying on a real card's scaling clauses."""
    tmpl = CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=None,
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _put_lands_on_battlefield(game: GameState, card_db: CardDatabase,
                               controller: int, count: int,
                               land_name: str = "Mountain"):
    """Drop `count` untapped basic lands onto the controller's
    battlefield so ``available_mana_estimate`` reads `count`."""
    tmpl = card_db.cards.get(land_name)
    assert tmpl is not None, f"missing land in DB: {land_name!r}"
    for _ in range(count):
        card = CardInstance(
            template=tmpl,
            owner=controller,
            controller=controller,
            instance_id=game.next_instance_id(),
            zone="battlefield",
        )
        card.tapped = False
        card._game_state = game
        game.players[controller].battlefield.append(card)


# ──────────────────────────────────────────────────────────────────
# Rule 1: panic life → strip the imminent attacker
# ──────────────────────────────────────────────────────────────────

class TestPanicLifePicksImminentAttacker:
    """Defender at panic life (``life ≤ 2 × opp_avg_attack``) must
    pick the imminently-castable attacker out of the victim's hand,
    even if a higher-static-score card is also present."""

    def test_at_panic_life_strips_imminent_attacker_over_flex_card(
            self, card_db):
        """Dimir caster at 4 life; victim's board has a 3-power
        creature (so ``opp_avg_attack=3`` and ``4 ≤ 2×3`` triggers
        panic). Hand contains:
          * Goblin Bombardment (CMC 2, sacrifice engine) — high
            static "strip me" score but useless without a sac target.
          * Ragavan (CMC 1) — the live next-turn attacker.

        Expected: the advisor picks Ragavan."""
        game = _make_game()
        # Defender = the discard CASTER, player 0 (Dimir, at panic
        # life, with the live attacker in front of them).
        defender = game.players[0]
        defender.life = 4
        defender.deck_name = "Dimir Midrange"

        # The victim = player 1 — owner of the hand whose card we
        # strip and the active aggressor whose board pressures the
        # defender.
        victim_idx = 1
        # Victim's board: one 3/3 (matches opp_avg_attack=3).
        # Using Tarmogoyf-style stats via a vanilla 3/3. Choose a
        # generic on-DB creature that's just a body.
        # Vanilla 3/3 → opp_avg_attack=3.0.
        _put_vanilla_attacker_on_battlefield(game, controller=victim_idx,
                                              power=3, toughness=3)
        # Victim has 1 land (so they can pay for a 1-CMC card next
        # turn but not a 2-CMC card — Ragavan castable, Bombardment
        # not).
        _put_lands_on_battlefield(game, card_db, victim_idx, count=1)

        hand = _build_hand(
            game, card_db, player_idx=victim_idx,
            card_names=[
                "Goblin Bombardment",   # high static score, slow
                "Ragavan, Nimble Pilferer",  # imminent attacker
            ],
            deck_name="Boros Energy",   # opp_gameplan won't list these
        )
        assert len(hand) == 2

        # The caster (defender, player_idx=0) Thoughtseizes the
        # victim (player_idx=1) — strip one card.
        game._force_discard(victim_idx, 1)

        graveyard = game.players[victim_idx].graveyard
        assert len(graveyard) == 1, (
            f"expected one card stripped, got {[c.name for c in graveyard]}"
        )
        picked = graveyard[0].name
        assert picked == "Ragavan, Nimble Pilferer", (
            f"At panic life ({defender.life}, opp avg attack 3) the "
            f"advisor must pick the imminent attacker (Ragavan). It "
            f"picked {picked!r} — the static-score heuristic ignored "
            f"the live combat threat."
        )


# ──────────────────────────────────────────────────────────────────
# Rule 2: comfortable life → fall back to the static ranking
# ──────────────────────────────────────────────────────────────────

class TestGrindLifeFallsBackToStaticScore:
    """When ``my_life > 2 × opp_avg_attack`` (no panic), the advisor
    must fall back to the standard threat-score ranking — no new
    behaviour leaks into the comfortable-life regime."""

    def test_at_grind_life_strips_static_top_score(self, card_db):
        """Defender at 15 life; victim's board has a 3-power creature
        (so ``15 > 2×3`` — comfortable). Same hand as the panic case.
        Without the panic switch, ``score_card_for_opponent_strip``
        ranks the cards by their tag-based and creature-threat
        scores. The advisor must respect that ranking."""
        from ai.ev_evaluator import score_card_for_opponent_strip

        game = _make_game()
        defender = game.players[0]
        defender.life = 15
        defender.deck_name = "Dimir Midrange"

        victim_idx = 1
        # Vanilla 3/3 → opp_avg_attack=3.0.
        _put_vanilla_attacker_on_battlefield(game, controller=victim_idx,
                                              power=3, toughness=3)
        _put_lands_on_battlefield(game, card_db, victim_idx, count=1)

        hand = _build_hand(
            game, card_db, player_idx=victim_idx,
            card_names=[
                "Goblin Bombardment",
                "Ragavan, Nimble Pilferer",
            ],
            deck_name="Boros Energy",
        )

        # Predict the static-score winner so the assertion stays
        # data-driven (not a hardcoded card name).
        scores = [(score_card_for_opponent_strip(c), c) for c in hand]
        scores.sort(key=lambda kv: -kv[0])
        expected_static_pick = scores[0][1].name

        game._force_discard(victim_idx, 1)

        graveyard = game.players[victim_idx].graveyard
        assert len(graveyard) == 1
        picked = graveyard[0].name
        assert picked == expected_static_pick, (
            f"At grind life ({defender.life}, opp avg attack 3) the "
            f"advisor must fall back to the static-score ranking. "
            f"Expected {expected_static_pick!r} (top static score), "
            f"got {picked!r}."
        )


# ──────────────────────────────────────────────────────────────────
# Rule 3: predicted_turn_of_cast composes effective_cmc
# ──────────────────────────────────────────────────────────────────

class TestPredictedTurnOfCastUsesEffectiveCMC:
    """``bhi.predicted_turn_of_cast`` must compose
    ``ai.mana_planner.effective_cmc``, not read ``card.cmc`` directly.
    Verifies the composition by monkey-patching ``effective_cmc`` and
    asserting it is called during ``predicted_turn_of_cast``."""

    def test_predicted_turn_of_cast_calls_effective_cmc(
            self, card_db, monkeypatch):
        from ai import bhi as bhi_module
        from ai import mana_planner as mp_module

        called: list[tuple] = []
        original = mp_module.effective_cmc

        def _spy(card, player, overrides=None):
            called.append((getattr(card, 'name', '?'), player))
            return original(card, player, overrides)

        # Patch the symbol both where it's defined and where bhi
        # imported it, to cover either binding pattern.
        monkeypatch.setattr(mp_module, "effective_cmc", _spy)
        if hasattr(bhi_module, "effective_cmc"):
            monkeypatch.setattr(bhi_module, "effective_cmc", _spy)

        game = _make_game()
        victim_idx = 1
        _put_lands_on_battlefield(game, card_db, victim_idx, count=1)
        bolt = _make_card(game, card_db, "Lightning Bolt", victim_idx)
        from ai.ev_evaluator import snapshot_from_game
        snap = snapshot_from_game(game, defender_idx_for_strip := 0)

        turns = bhi_module.predicted_turn_of_cast(bolt, snap,
                                                  victim_idx=victim_idx,
                                                  victim_player=game.players[victim_idx])
        assert turns >= 0
        assert called, (
            "predicted_turn_of_cast must compose effective_cmc — "
            "spy was never called.  This indicates the function "
            "is reading card.cmc directly, bypassing the W0-F "
            "domain/affinity reduction primitive."
        )


# ──────────────────────────────────────────────────────────────────
# Rule 4: no new bare life-threshold literals in the touched code
# ──────────────────────────────────────────────────────────────────

class TestNoMagicLifeThresholds:
    """The touched code must not introduce bare life-threshold
    comparisons (``< 4``, ``<= 5``, ``life < 8``). Panic detection is
    derived from snapshot fields, not from a hardcoded life number.

    Heuristic: scan ``ai/discard_advisor.py`` and the panic helper in
    ``ai/bhi.py`` for life-against-literal comparisons, excluding the
    EXEMPT_VALUES set from ``tools/check_magic_numbers.py`` (0, 1,
    -1, 2, 100 — these include the rules-derived "2 ×" multiplier).
    """

    EXEMPT = {"0", "1", "-1", "2", "100"}

    def _scan(self, path: Path) -> list[tuple[int, str]]:
        if not path.exists():
            return []
        bad = []
        # Match patterns like "life < 7", "life <= 5", "my_life > 12".
        pat = re.compile(
            r"\b(life|my_life|opp_life|defender_life|defender\.life)\s*"
            r"(<=?|>=?)\s*(-?\d+)"
        )
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Allow rules-constant rows with magic-allow marker.
            if "# magic-allow:" in line:
                continue
            for m in pat.finditer(line):
                literal = m.group(3)
                if literal in self.EXEMPT:
                    continue
                bad.append((lineno, line.rstrip()))
        return bad

    def test_discard_advisor_has_no_bare_life_thresholds(self):
        root = Path(__file__).resolve().parent.parent
        offenders = self._scan(root / "ai" / "discard_advisor.py")
        assert not offenders, (
            "ai/discard_advisor.py contains a bare life-threshold "
            "comparison.  Derive panic from snapshot fields (e.g. "
            "`my_life <= 2 * opp_avg_attack`) instead:\n"
            + "\n".join(f"  L{ln}: {src}" for ln, src in offenders)
        )

    def test_bhi_has_no_bare_life_thresholds(self):
        root = Path(__file__).resolve().parent.parent
        offenders = self._scan(root / "ai" / "bhi.py")
        assert not offenders, (
            "ai/bhi.py contains a bare life-threshold comparison.  "
            "Derive panic from snapshot fields instead:\n"
            + "\n".join(f"  L{ln}: {src}" for ln, src in offenders)
        )
