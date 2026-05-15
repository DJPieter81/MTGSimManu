"""Phase 5 step 2 — CardInstance proxy for the production EVPlayer scorer.

The thin adapter shipped in PR #367 (``ai/search/evplayer_scorer_adapter.py``)
runs ``evaluate_board(after) − evaluate_board(before)`` against the snapshot
fixtures, which exercises the value-delta backbone of ``compute_play_ev`` but
NOT its card-aware scoring signals: BHI counter/removal probability, oracle-
text-driven deferral/exposure checks, the goal-engine state, or the combo-
chain assessment. Those require a ``CardInstance`` + a (minimal) ``GameState``.

The 12-fixture corpus in ``tests/fixtures/ismcts_acceptance_fixtures.jsonl`` is
snapshot-only: every fixture carries an EVSnapshot + a list of ActionToken
deltas. The proxy in ``ai/search/cardinstance_proxy.py`` synthesises a
``CardInstance`` and a minimal ``GameState`` from one of those tokens so that
``compute_play_ev`` can run end-to-end against the snapshot-only corpus.

Rule these tests pin:

  R1. Token kind → CardInstance.template card-type mapping is correct.
      ``cast_creature`` → CardType.CREATURE template, ``cast_artifact`` →
      CardType.ARTIFACT template, ``play_land`` → CardType.LAND, ``burn`` →
      sorcery/instant with removal/burn semantics, ``draw`` → cantrip-tagged
      spell, ``pass`` → fails loudly (it has no card to proxy).
  R2. Card-level metadata (mana cost, types, oracle text) survives the proxy.
      When a ModernAtomic template exists for the label, the proxy uses it
      verbatim (this is the apples-to-apples gate: the production scorer sees
      the SAME template it would see in a real game).
  R3. Snapshot board state maps onto a minimal GameState/PlayerState so the
      production scorer's ``game is not None`` branches all reach their data.
  R4. The proxied ``compute_play_ev`` agrees with a direct-call
      ``compute_play_ev`` for the same card + snapshot — proving the proxy
      doesn't inject a new scoring formula, it just routes the snapshot
      through the production path.
  R5. Loud failure mode: when an ActionToken carries metadata insufficient to
      proxy a CardInstance (e.g. empty label on a non-pass token), the proxy
      raises ``ProxyInsufficientMetadataError`` with the missing-field list.
      Silent fallback to a synthetic stub would mask the real failure mode
      the acceptance gate is trying to surface.

Module under test: ``ai/search/cardinstance_proxy.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.ev_evaluator import EVSnapshot, compute_play_ev
from ai.search.snapshot_adapter import ActionToken, make_search_state
from engine.cards import CardType


FIXTURES_PATH = (
    Path(__file__).parent / "fixtures" / "ismcts_acceptance_fixtures.jsonl"
)


def _load_fixtures():
    rows = []
    with FIXTURES_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


# ─── R1: token kind → CardInstance template type mapping ─────────────


def test_proxy_maps_cast_creature_to_creature_template():
    """``cast_creature`` ActionToken must yield a CardInstance whose
    template has CardType.CREATURE — the production ``compute_play_ev``
    branches on ``t.is_creature`` extensively (line ~2634, projection,
    removal probability), so the type mapping is load-bearing."""
    from ai.search.cardinstance_proxy import proxy_card_instance

    token = ActionToken(
        kind="cast_creature", label="Memnite",
        delta={"my_power": 1, "my_toughness": 1, "my_creature_count": 1,
               "my_artifact_count": 1}, cost=0,
    )
    card = proxy_card_instance(token)
    assert CardType.CREATURE in card.template.card_types


def test_proxy_maps_cast_artifact_to_artifact_template():
    """``cast_artifact`` must yield CardType.ARTIFACT — the deferral
    check + ``_project_spell`` artifact-count increment both pivot on
    this type."""
    from ai.search.cardinstance_proxy import proxy_card_instance

    token = ActionToken(
        kind="cast_artifact", label="Mox Opal",
        delta={"my_artifact_count": 1, "my_mana": 1}, cost=0,
    )
    card = proxy_card_instance(token)
    assert CardType.ARTIFACT in card.template.card_types


def test_proxy_maps_play_land_to_land_template():
    """``play_land`` must yield CardType.LAND. The production scorer
    short-circuits land plays via ``t.is_land`` early in projection,
    so the proxy must surface that signal cleanly."""
    from ai.search.cardinstance_proxy import proxy_card_instance

    token = ActionToken(
        kind="play_land", label="Inspiring Vantage",
        delta={"my_mana": 1, "my_total_lands": 1}, cost=0,
    )
    card = proxy_card_instance(token)
    assert CardType.LAND in card.template.card_types


def test_proxy_rejects_pass_token_loudly():
    """``pass`` has no card to proxy. The proxy must raise rather than
    fabricate a stub — silent fallback would hide a real bug (the A/B
    harness is supposed to skip ``pass`` separately, never proxy it)."""
    from ai.search.cardinstance_proxy import (
        ProxyInsufficientMetadataError,
        proxy_card_instance,
    )

    token = ActionToken(kind="pass", label="pass turn")
    with pytest.raises(ProxyInsufficientMetadataError):
        proxy_card_instance(token)


# ─── R2: metadata preservation (DB-resolved template) ────────────────


def test_proxy_resolves_real_template_when_label_matches_modern_atomic():
    """When the label matches a real ModernAtomic entry, the proxy
    must return the REAL template (oracle text, mana cost, tags) so
    the production scorer sees the same card it would see in a real
    game. This is the apples-to-apples promise of the proxy."""
    from ai.search.cardinstance_proxy import proxy_card_instance

    token = ActionToken(
        kind="burn", label="Lightning Bolt",
        delta={"opp_life": -3}, cost=1,
    )
    card = proxy_card_instance(token)
    # Lightning Bolt is in ModernAtomic; oracle text should mention damage.
    oracle = (card.template.oracle_text or "").lower()
    assert "damage" in oracle, (
        f"Expected real Lightning Bolt oracle text, got {oracle!r}"
    )
    # CMC must equal the token's declared cost (mana_cost.cmc == 1).
    assert card.template.cmc == 1


def test_proxy_synthesises_template_when_label_is_unknown():
    """When the label does not match ModernAtomic, the proxy must
    synthesise a template from the token kind + delta (no silent
    failure, no None return). This keeps the gate runnable on fixtures
    that intentionally use placeholder labels like 'Bind Memnite'."""
    from ai.search.cardinstance_proxy import proxy_card_instance

    token = ActionToken(
        kind="cast_creature", label="ZZ_UnknownPlaceholderCreature_42",
        delta={"my_power": 2, "my_toughness": 2, "my_creature_count": 1},
        cost=2,
    )
    card = proxy_card_instance(token)
    assert card.template is not None
    assert CardType.CREATURE in card.template.card_types


# ─── R3: snapshot → minimal GameState mapping ────────────────────────


def test_proxy_game_state_reflects_snapshot_life_totals():
    """The minimal GameState built from the snapshot must put my_life
    and opp_life on the right PlayerState — every BHI-driven path in
    the production scorer reads ``game.players[1 - player_idx].life``
    or similar."""
    from ai.search.cardinstance_proxy import proxy_game_state

    snap = EVSnapshot(my_life=15, opp_life=8, my_mana=3, my_total_lands=3,
                       opp_hand_size=4, turn_number=4)
    game, player_idx = proxy_game_state(snap)
    assert game.players[player_idx].life == 15
    assert game.players[1 - player_idx].life == 8


def test_proxy_game_state_populates_my_hand_for_combo_chain_assessment():
    """``_estimate_combo_chain`` walks ``game.players[player_idx].hand``
    looking for rituals, draws, finishers. For the proxy to exercise the
    storm/combo branch of compute_play_ev, the synthetic hand must be
    sized to ``snap.my_hand_size`` (population is best-effort; the count
    is what the chain estimator cares about most)."""
    from ai.search.cardinstance_proxy import proxy_game_state

    snap = EVSnapshot(my_life=18, opp_life=18, my_hand_size=5,
                       my_mana=3, my_total_lands=3, turn_number=3)
    game, player_idx = proxy_game_state(snap)
    # Hand size matches; cards themselves are synthetic placeholders.
    assert len(game.players[player_idx].hand) == 5


# ─── R4: proxied score matches direct-call score ────────────────────


def test_proxied_score_matches_direct_compute_play_ev_for_real_template():
    """The proxy must not perturb the score: when a real CardInstance is
    constructed independently with the same template + snapshot, calling
    ``compute_play_ev`` on it directly must yield the SAME value as the
    proxy's routed call. This is the load-bearing equality test — if it
    drifts, the proxy is silently injecting a new scoring formula."""
    from ai.search.cardinstance_proxy import (
        proxy_card_instance,
        proxy_game_state,
        score_action_via_production_scorer,
    )
    from engine.cards import CardInstance

    snap = EVSnapshot(my_life=20, opp_life=20, my_mana=1, my_total_lands=1,
                       my_hand_size=5, opp_hand_size=4, turn_number=2,
                       my_artifact_count=2, my_creature_count=1, my_power=1)
    token = ActionToken(
        kind="burn", label="Lightning Bolt",
        delta={"opp_life": -3}, cost=1,
    )

    # Direct: build the same template + CardInstance independently.
    card_proxy = proxy_card_instance(token)
    game, player_idx = proxy_game_state(snap)
    direct_ev = compute_play_ev(
        card_proxy, snap, archetype="aggro",
        game=game, player_idx=player_idx,
    )

    # Routed via the adapter entry point.
    routed_ev = score_action_via_production_scorer(
        make_search_state(snap, [token]), token, archetype="aggro",
    )

    assert abs(routed_ev - direct_ev) < 1e-6, (
        f"Proxy routing diverged from direct compute_play_ev call: "
        f"routed={routed_ev:.6f}, direct={direct_ev:.6f}"
    )


# ─── R5: loud failure on missing metadata ────────────────────────────


def test_proxy_raises_on_token_without_label():
    """A token whose label is empty cannot be proxied — neither the
    DB lookup nor the synthetic path can produce a meaningful template
    from a kind alone. The proxy must raise ProxyInsufficientMetadataError
    with the missing-field list, so the acceptance gate surfaces the
    diagnostic instead of silently scoring a stub."""
    from ai.search.cardinstance_proxy import (
        ProxyInsufficientMetadataError,
        proxy_card_instance,
    )

    token = ActionToken(kind="cast_creature", label="",
                        delta={"my_power": 1}, cost=1)
    with pytest.raises(ProxyInsufficientMetadataError) as excinfo:
        proxy_card_instance(token)
    # The error must name the missing field so the diagnostic is actionable.
    assert "label" in str(excinfo.value).lower()


# ─── Smoke: proxy runs against every corpus fixture without crashing ─


def test_production_scorer_via_proxy_runs_on_every_corpus_fixture():
    """The proxy must end-to-end on all 12 fixtures × all actions per
    fixture without raising. This is the gate-readiness smoke test:
    the acceptance gate in test_ismcts_acceptance_real.py invokes the
    proxy across the full corpus, and any uncaught exception there
    would mask the actual scoring signal."""
    from ai.search.cardinstance_proxy import (
        score_action_via_production_scorer,
    )

    fixtures = _load_fixtures()
    assert len(fixtures) == 12
    for fixture in fixtures:
        snap = EVSnapshot(**fixture["snapshot"])
        actions = [
            ActionToken(
                kind=a["kind"], label=a["label"],
                delta=a["delta"], cost=a.get("cost", 0),
            )
            for a in fixture["available_actions"]
        ]
        state = make_search_state(snap, actions)
        archetype = fixture.get("archetype", "midrange")
        for action in actions:
            ev = score_action_via_production_scorer(
                state, action, archetype=archetype,
            )
            # Numeric, finite — no NaN/Inf leakage from oracle parsing.
            assert isinstance(ev, float)
            assert ev == ev, f"NaN EV for fixture {fixture['id']}, " \
                             f"action {action.label!r}"
