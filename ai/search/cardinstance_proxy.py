"""CardInstance / GameState proxy for the production EV scorer.

Phase 5 step 2 — wires the snapshot-only acceptance corpus
(``tests/fixtures/ismcts_acceptance_fixtures.jsonl``) through the FULL
``ai.ev_evaluator.compute_play_ev`` scoring path: BHI counter / removal
probability, oracle-text-driven deferral checks, goal-engine state, and
the combo-chain assessment.

Why this module exists
----------------------
``ai/search/evplayer_scorer_adapter.py`` (PR #367) is a thin adapter
that re-implements ``compute_play_ev``'s value-delta backbone
(``evaluate_board(after) − evaluate_board(before)`` + urgency-factor
discount) directly on EVSnapshots. It is "production-style" but does
NOT exercise the card-aware scoring signals because those require a
``CardInstance`` and a (minimal) ``GameState`` — neither of which the
snapshot-only fixtures carry.

This module bridges that gap. Given an ``ActionToken`` from a fixture,
it synthesises:

  - a ``CardInstance`` whose ``template`` is the real ModernAtomic
    ``CardTemplate`` when the token's label matches a known card
    (preferred path — the production scorer then sees the SAME card
    it would see in a real game), or a synthetic template constructed
    from the token's kind + delta when the label is a placeholder
    (e.g. "Bind Memnite (1/1)").
  - a minimal ``GameState`` whose ``players[player_idx]`` carries the
    snapshot's life total, hand size, mana, and battlefield permanent
    count, enough that the production scorer's ``game is not None``
    branches all reach data instead of None-guarding.

Apples-to-apples promise
------------------------
For a token whose label is a real card, the proxy MUST route the
score through the same code paths the production EVPlayer uses when
that card is cast in a real game. The numeric agreement test
(``tests/test_cardinstance_proxy.py::
test_proxied_score_matches_direct_compute_play_ev_for_real_template``)
pins this — the proxy is a routing facility, not a new scorer.

Loud failure mode
-----------------
``ProxyInsufficientMetadataError`` is raised when a token cannot
support a CardInstance — empty label, the ``pass`` kind (which has no
card), or any future case where the snapshot is too thin for the
production scorer to consume. Silent fallback to a stub would mask
the very signal the acceptance gate is meant to surface: WHICH
fixtures the production scorer disagrees with the synthetic baseline
on, and WHY.

Reference: ``docs/research/2026-05_phase_4a_ismcts_scoping.md`` §
Interface contract; ``ai/search/evplayer_scorer_adapter.py`` (the
thin adapter this module supersedes for the card-aware path).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ai.ev_evaluator import EVSnapshot, compute_play_ev
from ai.search.snapshot_adapter import (
    ActionToken,
    SearchState,
    apply_action,
    enumerate_actions,
)
from engine.card_database import CardDatabase
from engine.cards import CardInstance, CardTemplate, CardType
from engine.game_state import GameState
from engine.mana import ManaCost
import random


# Singleton DB — loaded once on first access. Re-loading the 21k-card
# ModernAtomic dump per fixture would dominate the gate's wall clock.
_DB_SINGLETON: Optional[CardDatabase] = None


def _get_db() -> CardDatabase:
    global _DB_SINGLETON
    if _DB_SINGLETON is None:
        _DB_SINGLETON = CardDatabase()
    return _DB_SINGLETON


# ─────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────


class ProxyInsufficientMetadataError(RuntimeError):
    """Raised when an ActionToken doesn't carry enough metadata to
    support a CardInstance for the production scorer.

    The error message names the missing field(s) so the acceptance
    gate's diagnostic output is actionable: "fixture N: token X has
    no label" — not a silent KeyError or NoneType crash deep inside
    ``compute_play_ev``.
    """

    def __init__(self, token: ActionToken, missing: List[str]):
        self.token = token
        self.missing = missing
        super().__init__(
            f"ActionToken (kind={token.kind!r}, label={token.label!r}) "
            f"is missing required metadata: {missing}. Cannot proxy a "
            f"CardInstance for the production scorer."
        )


# ─────────────────────────────────────────────────────────────────────
# Kind → synthetic template mapping
# ─────────────────────────────────────────────────────────────────────


# Token kinds whose card-type is unambiguous from the kind alone. The
# mapping is the bridge contract between snapshot_adapter's token
# vocabulary and the production scorer's CardType enum. Adding a new
# token kind to ``snapshot_adapter.ActionToken`` requires adding it
# here too — silent fallback would mis-type the proxy.
#
# This is NOT a card-name table. It's a kind-to-supertype mapping —
# the same six kinds defined in ``ActionToken`` (see
# ``ai/search/snapshot_adapter.py`` lines 60-72). Allowed by the
# abstraction contract because (a) it does not branch on card.name,
# (b) it does not gate on a deck name, and (c) the class size is
# bounded by the ActionToken kind enum, not by Modern's 20k cards.
_KIND_TO_CARD_TYPES = {
    "cast_creature": [CardType.CREATURE],
    "cast_artifact": [CardType.ARTIFACT],
    "play_land": [CardType.LAND],
    # ``burn`` historically covers both instant-speed face damage
    # (Lightning Bolt) and one-shot removal effects (Leyline Binding).
    # Treat as instant by default; the real DB lookup overrides this
    # when the label resolves to a card.
    "burn": [CardType.INSTANT],
    # ``draw`` covers cantrips, library-dig tutors, and impulse-draw.
    # Sorcery by default (real DB lookup will refine).
    "draw": [CardType.SORCERY],
}


def _synthesize_template(token: ActionToken) -> CardTemplate:
    """Build a synthetic CardTemplate from a token's kind + delta.

    Used only when the token's label does not resolve to a real
    ModernAtomic entry — placeholder labels in fixtures like
    "Bind Memnite (1/1)" or "Equip Plating to Memnite" describe a
    play, not a card.

    The synthesised template carries:
      - card_types: from ``_KIND_TO_CARD_TYPES[token.kind]``
      - mana_cost: ``ManaCost(generic=token.cost)`` — generic-only,
        because the snapshot fixtures don't carry color metadata.
      - power / toughness: from ``token.delta.get('my_power')`` and
        ``my_toughness`` for creatures.
      - oracle_text: empty string. The production scorer's
        oracle-driven signals (cantrip, ritual, burn) will simply
        return False, which is the correct conservative behaviour
        for an unrecognised card.

    The synthesised path is the FALLBACK. Fixtures with real card
    names (Lightning Bolt, Memnite, Mox Opal) go through the DB
    lookup and get the real template — that's the apples-to-apples
    path the acceptance gate cares about.
    """
    card_types = _KIND_TO_CARD_TYPES.get(token.kind)
    if card_types is None:
        # Should not happen — token-kind validation is upstream — but
        # raise loudly if it does.
        raise ProxyInsufficientMetadataError(token, [f"unknown_kind:{token.kind}"])

    delta = token.delta or {}
    power = None
    toughness = None
    if CardType.CREATURE in card_types:
        # delta keys are EVSnapshot field names; the production
        # scorer's ``_project_spell`` reads template.power and
        # template.toughness directly.
        power = int(delta.get("my_power", 0)) or 1
        toughness = int(delta.get("my_toughness", 0)) or 1

    return CardTemplate(
        name=token.label,
        card_types=list(card_types),
        mana_cost=ManaCost(generic=int(token.cost or 0)),
        power=power,
        toughness=toughness,
        oracle_text="",
    )


# ─────────────────────────────────────────────────────────────────────
# Public surface: proxy_card_instance / proxy_game_state / score
# ─────────────────────────────────────────────────────────────────────


def proxy_card_instance(token: ActionToken) -> CardInstance:
    """Synthesise a ``CardInstance`` from an ``ActionToken``.

    Preferred path: look up the token's label in the ModernAtomic DB
    and wrap the REAL template in a CardInstance. The production
    scorer then sees the same card it would see in a real game —
    oracle text, tags, mana cost, power / toughness all from MTGJSON.

    Fallback path: when the label is a placeholder (no DB match),
    synthesise a minimal template from ``token.kind`` + ``token.delta``.
    The scorer's oracle-driven signals will return False, which is
    the conservative outcome for an unrecognised card.

    Raises ``ProxyInsufficientMetadataError`` when the token cannot
    support a CardInstance: ``pass`` kind (no card), empty label
    (DB lookup impossible, synthesis ambiguous), unknown kind.
    """
    if token.kind == "pass":
        raise ProxyInsufficientMetadataError(token, ["card"])
    if not token.label or not token.label.strip():
        raise ProxyInsufficientMetadataError(token, ["label"])

    # Preferred: real DB lookup.
    template: Optional[CardTemplate] = _get_db().get_card(token.label)
    if template is None:
        # Fallback: synthesise from kind + delta.
        template = _synthesize_template(token)

    return CardInstance(
        template=template,
        owner=0,
        controller=0,
        # instance_id collisions don't matter for the snapshot scorer —
        # the scorer doesn't dereference ids — but a positive, unique-ish
        # value is required by CardInstance's contract elsewhere.
        instance_id=id(token) & 0xFFFF,
        zone="hand",
    )


def _synthetic_hand_card(idx: int) -> CardInstance:
    """A placeholder land-typed CardInstance for filling the hand list.

    The combo-chain assessment walks ``game.players[player_idx].hand``
    looking for rituals / draws / finishers via tag membership. The
    snapshot fixtures don't carry hand contents, so we fill with
    typeless placeholder templates that none of the combo predicates
    match — equivalent to "five generic cards" in the chain estimator.
    """
    template = CardTemplate(
        name=f"__proxy_hand_filler_{idx}__",
        card_types=[CardType.SORCERY],
        mana_cost=ManaCost(generic=2),
        oracle_text="",
    )
    return CardInstance(
        template=template,
        owner=0,
        controller=0,
        instance_id=-(idx + 1),  # negative so it can't collide with real ids
        zone="hand",
    )


def _synthetic_land(idx: int, controller: int) -> CardInstance:
    """A placeholder basic-land CardInstance for the battlefield.

    Sized from ``snap.my_total_lands`` / ``snap.opp_total_lands``. The
    combo-chain assessment uses ``len(me.untapped_lands)`` for available
    mana; populating untapped lands so that count matches the snapshot
    keeps the production scorer's mana math consistent with the
    snapshot view.
    """
    template = CardTemplate(
        name=f"__proxy_land_filler_{controller}_{idx}__",
        card_types=[CardType.LAND],
        mana_cost=ManaCost(),  # lands are free
        produces_mana=["C"],
        oracle_text="",
    )
    inst = CardInstance(
        template=template,
        owner=controller,
        controller=controller,
        instance_id=-(1000 + 100 * controller + idx),
        zone="battlefield",
    )
    inst.tapped = False  # untapped → counted in `me.untapped_lands`
    return inst


def proxy_game_state(snap: EVSnapshot, player_idx: int = 0
                     ) -> Tuple[GameState, int]:
    """Synthesise a minimal ``GameState`` reflecting ``snap``.

    Populated fields (everything the production scorer dereferences):
      - players[player_idx].life ← snap.my_life
      - players[1 - player_idx].life ← snap.opp_life
      - players[player_idx].hand: synthetic fillers sized to
        snap.my_hand_size (combo-chain estimator walks this).
      - players[player_idx].battlefield: synthetic untapped lands
        sized to snap.my_total_lands (combo-chain mana count).
      - players[player_idx].mana_pool: pre-loaded with snap.my_mana
        generic mana (mirrors what ``cast_spell`` would have left in
        the pool when the spell resolved).
      - players[*].counter_density / removal_density / exile_density:
        zero — snapshot fixtures don't declare opponent deck composition,
        and the production scorer correctly returns 0 P(counter) /
        P(removal) when these are zero (the BHI fallback path).

    Returns ``(game, player_idx)`` so callers can pass both to
    ``compute_play_ev`` without recomputing the index.

    The GameState is a real ``engine.game_state.GameState`` instance,
    not a Mock — so any attribute the production scorer reads
    (``game.stack``, ``game.players``, ``game.callbacks``,
    ``game.zone_mgr``) is initialised by the real constructor. This
    is the cheapest way to satisfy ``compute_play_ev``'s implicit
    interface contract without an N-attr Mock that drifts every time
    the scorer touches a new field.
    """
    rng = random.Random(0)
    game = GameState(rng=rng)
    # Life totals
    game.players[player_idx].life = int(snap.my_life)
    game.players[1 - player_idx].life = int(snap.opp_life)
    # Mana pool — pre-loaded so projected.opp_mana reads correctly.
    # ManaPool stores by color; generic adds via .colorless for our purposes.
    for _ in range(int(snap.my_mana or 0)):
        game.players[player_idx].mana_pool.add("C", 1)
    for _ in range(int(snap.opp_mana or 0)):
        game.players[1 - player_idx].mana_pool.add("C", 1)
    # Hand fillers — combo-chain estimator counts these, doesn't
    # inspect them by name (it inspects them by `tags`, which the
    # filler templates explicitly lack).
    for i in range(int(snap.my_hand_size or 0)):
        game.players[player_idx].hand.append(_synthetic_hand_card(i))
    # Battlefield: lands so ``len(me.untapped_lands)`` matches snap.
    for i in range(int(snap.my_total_lands or 0)):
        game.players[player_idx].battlefield.append(
            _synthetic_land(i, player_idx)
        )
    for i in range(int(snap.opp_total_lands or 0)):
        game.players[1 - player_idx].battlefield.append(
            _synthetic_land(i, 1 - player_idx)
        )
    # Turn number + active player. ``snap.turn_number`` is the
    # in-game turn — feed it to the engine so ``compute_play_ev``'s
    # turn-aware branches read consistent state.
    game.turn_number = int(snap.turn_number or 1)
    game.active_player = player_idx
    game.priority_player = player_idx
    # Deck-density attrs default to 0.0 already (PlayerState dataclass
    # defaults), which is the conservative "no read" path for BHI.
    return game, player_idx


def _resolve_archetype(state: SearchState, archetype: Optional[str]) -> str:
    """Same precedence rule as ``evplayer_scorer_adapter._resolve_archetype``.

    Snapshot's ``archetype_subtype`` field comes from the deck gameplan
    when a snapshot was captured from a real game; on hand-built
    fixtures it is usually None and the caller-supplied archetype wins.
    """
    if archetype:
        return archetype
    sub = getattr(state.snapshot, "archetype_subtype", None)
    if sub:
        return sub
    return "midrange"


def score_action_via_production_scorer(
    state: SearchState,
    action: ActionToken,
    archetype: Optional[str] = None,
) -> float:
    """Score a single action through ``compute_play_ev``.

    This is the function the production-baseline picker calls. It is
    the FULL production scorer applied to a snapshot-only fixture:
    no synthetic ``evaluate_board`` delta, no urgency-only discount —
    the same code path a real ``EVPlayer.decide_main_phase`` uses on
    a real cast decision.

    For action kinds that can't proxy a CardInstance (``pass``) we
    return 0.0 — pass actions get their EV from a different path
    (``estimate_pass_ev``), and the A/B harness handles them via the
    enumerator, not the scorer. Bubble-up of
    ``ProxyInsufficientMetadataError`` is preserved for genuinely
    broken tokens (empty label etc.) so the gate's diagnostic output
    can name them.
    """
    if action.kind == "pass":
        # The acceptance gate's picker calls this function per action;
        # ``pass`` is a sentinel for "end the turn" and gets its EV
        # from ``estimate_pass_ev`` elsewhere. Returning 0.0 here
        # makes ``pass`` the neutral baseline against which non-pass
        # actions are ranked.
        return 0.0

    snap = state.snapshot
    archetype_to_use = _resolve_archetype(state, archetype)
    card = proxy_card_instance(action)
    game, player_idx = proxy_game_state(snap)
    return float(compute_play_ev(
        card, snap, archetype_to_use,
        game=game, player_idx=player_idx,
    ))


def production_scorer_picker_full(
    state: SearchState,
    rng: random.Random,
    archetype: Optional[str] = None,
) -> ActionToken:
    """Drop-in for ``snapshot_adapter.heuristic_rollout`` using the
    FULL production scorer (CardInstance proxy + GameState proxy).

    Mirrors ``ai/search/evplayer_scorer_adapter.production_scorer_picker``
    but routes via ``compute_play_ev`` instead of the thin
    ``evaluate_board`` delta. Same tie-break + jitter convention so
    determinism follows the seeded rng.
    """
    archetype_to_use = _resolve_archetype(state, archetype)
    actions = enumerate_actions(state)
    if not actions:
        return ActionToken(kind="pass", label="pass turn")

    scored: List[Tuple[float, ActionToken]] = []
    for a in actions:
        ev = score_action_via_production_scorer(
            state, a, archetype=archetype_to_use,
        )
        scored.append((ev, a))

    jitter = rng.random() * 0.001
    scored.sort(key=lambda pair: pair[0] + jitter, reverse=True)
    return scored[0][1]


def make_full_production_picker(archetype: Optional[str] = None):
    """Factory for the A/B-harness-compatible FULL-scorer picker.

    Returns a ``state -> ActionToken`` callable. Mirrors
    ``evplayer_scorer_adapter.make_production_picker`` so callers can
    swap from the thin adapter to the full proxy by changing the
    factory import.

    Usage in the acceptance gate test::

        picker = make_full_production_picker(
            archetype=fixture.get("archetype"),
        )
        # picker(state) -> ActionToken
    """
    def _picker(state: SearchState) -> ActionToken:
        rng = random.Random(0)
        return production_scorer_picker_full(state, rng, archetype=archetype)
    return _picker
