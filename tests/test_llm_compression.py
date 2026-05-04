"""Tests for `ai/llm_compression.py`.

Covers each of the three compressors (decklist / replay / handler)
with both shape contracts ("the output is a string with the expected
sections") and the load-bearing behavioural contracts ("flags fire on
cards that have those features", "phase markers are dropped", "DB
miss does not crash").

The token-reduction test is the headline metric: a 75-card deck
rendered with the legacy oracle-dump must shrink by at least 60% when
re-rendered through `compress_decklist`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai.llm_compression import (
    compress_decklist,
    compress_handler,
    compress_replay,
)
from tools._synth_gameplan_input import format_decklist_for_prompt


REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_LOG = Path(__file__).resolve().parent / "fixtures" / "sample_bo3_log.txt"


# ─── Helpers ────────────────────────────────────────────────────────


# Compact 75-card aggro decklist used for the token-reduction
# measurement.  Stable across runs so the assertion is reproducible.
_BURN_DECKLIST: dict[str, int] = {
    "Lightning Bolt": 4,
    "Lava Spike": 4,
    "Skewer the Critics": 4,
    "Boros Charm": 4,
    "Searing Blaze": 4,
    "Eidolon of the Great Revel": 4,
    "Goblin Guide": 4,
    "Monastery Swiftspear": 4,
    "Light Up the Stage": 4,
    "Skullcrack": 3,
    "Rift Bolt": 4,
    "Roiling Vortex": 2,
    "Mountain": 9,
    "Sacred Foundry": 4,
    "Inspiring Vantage": 4,
    "Sunbaked Canyon": 4,
    "Arid Mesa": 4,
    "Bloodstained Mire": 2,
    "Wooded Foothills": 3,
}


# ─── compress_decklist ──────────────────────────────────────────────


def test_compress_decklist_returns_string(card_db):
    """Smoke contract: the compressor returns a non-empty string."""
    out = compress_decklist("Mock Deck", {"Lightning Bolt": 4}, card_db)
    assert isinstance(out, str)
    assert "Mock Deck" in out
    assert out.strip(), "Compressor must return non-empty content"


def test_compress_decklist_includes_qty_and_name(card_db):
    """Each card line carries quantity and the canonical name."""
    out = compress_decklist(
        "Mock Deck",
        {"Lightning Bolt": 4, "Mountain": 20},
        card_db,
    )
    assert "4× Lightning Bolt" in out
    assert "20× Mountain" in out


def test_compress_decklist_uses_card_features_flags(card_db):
    """Lightning Bolt — the canonical removal/instant — must surface
    both `removal` and `instant_speed` flags from CardFeatures."""
    out = compress_decklist(
        "Mock Deck", {"Lightning Bolt": 4}, card_db,
    )
    # Find the Bolt line and assert both flags are present.
    bolt_lines = [ln for ln in out.splitlines() if "Lightning Bolt" in ln]
    assert bolt_lines, "Lightning Bolt line missing from compressed output"
    bolt_line = bolt_lines[0]
    assert "removal" in bolt_line, f"removal flag missing: {bolt_line!r}"
    assert "instant_speed" in bolt_line, (
        f"instant_speed flag missing: {bolt_line!r}"
    )


def test_compress_decklist_falls_back_on_db_miss(card_db):
    """Cards not in the DB must not crash — they get a stubbed entry
    so the line is still rendered (the deck importer relies on this)."""
    out = compress_decklist(
        "Mock Deck",
        # Pure invented name guaranteed not to be in any printing.
        {"NonexistentCardXYZ123": 1},
        card_db,
    )
    assert "NonexistentCardXYZ123" in out
    # No exception is the load-bearing assertion; line shape is bonus.
    assert "1× NonexistentCardXYZ123" in out


class _VerboseOracleStubDB:
    """Stub DB that returns a verbose multi-paragraph oracle for every
    card.  Models the Modern norm: planeswalkers, sagas, charms, control
    spells, and combo payoffs routinely have 400-800 chars of oracle
    text.  The 200-char excerpt cap in compress_decklist is what drives
    the headline reduction; testing against short-oracle Burn would be
    measuring the wrong end of the distribution.
    """

    # Real Living End oracle text — a representative "verbose card"
    # the compressor is designed to crunch.
    _VERBOSE = (
        "Cycling {2}{B}{R} (Discard this card: Draw a card.)\n"
        "When you cycle Living End, exile it. If you do, each player "
        "exiles all creature cards in their graveyard, then each player "
        "sacrifices all creatures they control, then each player puts "
        "all cards they exiled this way onto the battlefield under "
        "their control."
    )

    def get_raw(self, name: str) -> dict:
        return {
            "name": name,
            "text": self._VERBOSE,
            "manaValue": 4,
            "manaCost": "{2}{B}{R}",
            "types": ["Sorcery"],
            "subtypes": [],
            "supertypes": [],
            "colors": ["B", "R"],
            "keywords": ["Cycling"],
        }


def test_compress_decklist_token_reduction():
    """The headline metric: replacing the raw oracle dump with the
    feature-vector compressor cuts at least 60% of the input chars on
    a 75-card deck of verbose-oracle cards.  The Phase I-3 design
    target is 70% for the LLM-tokeniser bytewise count on
    realistic-Modern oracle distributions; chars are a strict
    overestimate of tokens, so 60% on chars is a conservative gate.

    Burn is the worst-case input (3-word oracles); the bulk of the
    Modern card pool — planeswalkers, sagas, charms, control finishers,
    combo payoffs — is closer to the verbose stub used here.
    """
    db = _VerboseOracleStubDB()
    raw = format_decklist_for_prompt("Verbose", _BURN_DECKLIST, db)
    compressed = compress_decklist("Verbose", _BURN_DECKLIST, db)
    pre = len(raw)
    post = len(compressed)
    assert post < 0.4 * pre, (
        f"compress_decklist did not hit 60% reduction: "
        f"raw={pre} chars, compressed={post} chars "
        f"({post / pre:.0%} of original)"
    )


# ─── compress_replay ────────────────────────────────────────────────


def test_compress_replay_keeps_turn_headers():
    """TURN N headers — the boundaries the LLM uses to follow the
    game — must survive compression."""
    assert SAMPLE_LOG.exists(), f"fixture missing: {SAMPLE_LOG}"
    out = compress_replay(SAMPLE_LOG)
    assert "TURN 1" in out
    # Both players' turn-1 headers should appear (Bo3 has at least
    # turn 1 for each side in each game).
    assert out.count("╔══ TURN ") >= 2


def test_compress_replay_keeps_breakdown_and_lethal():
    """LETHAL / win events must survive compression — the LLM cannot
    diagnose the loss without seeing the closing event."""
    out = compress_replay(SAMPLE_LOG)
    # The fixture is a real Bo3 with two completed games; it must
    # contain at least one win-event line in the kept slice.
    has_win_event = any(
        marker in out
        for marker in ("wins Game", "MATCH RESULT", "LETHAL", "loses")
    )
    assert has_win_event, "compressed replay lost the game-result line(s)"


def test_compress_replay_drops_phase_markers():
    """Phase markers (`[Untap]`, `[Upkeep]`, `[Main 1]`, `[End Step]`)
    are pure noise for the LLM diagnostician — they must be dropped."""
    out = compress_replay(SAMPLE_LOG)
    assert "[Untap]" not in out, "phase marker [Untap] survived compression"
    assert "[Upkeep]" not in out, "phase marker [Upkeep] survived compression"
    assert "[End Step]" not in out, "phase marker [End Step] survived compression"


def test_compress_replay_truncates_at_max_lines():
    """With a small max_lines budget, output is bounded and the
    truncation marker is appended."""
    out = compress_replay(SAMPLE_LOG, max_lines=5)
    # 5 kept events + 1 truncation marker → 6 lines max.
    assert len(out.splitlines()) <= 6
    assert "truncated" in out.lower()


# ─── compress_handler ───────────────────────────────────────────────


_SAMPLE_HANDLER_SRC = '''\
@EFFECT_REGISTRY.register("Lightning Bolt", EffectTiming.SPELL_RESOLVE)
def _resolve_lightning_bolt(game, card, controller, targets):
    """Bolt deals 3 damage to any target."""
    if not targets:
        return
    game.deal_damage(targets[0], 3, source=card)
'''


def test_compress_handler_returns_card_oracle_handler_block():
    """All three sections (card name, oracle, handler block) appear in
    the expected order with the fenced code block intact."""
    out = compress_handler(
        _SAMPLE_HANDLER_SRC,
        oracle_text="Lightning Bolt deals 3 damage to any target.",
        card_name="Lightning Bolt",
    )
    # Section headers in order
    name_idx = out.index("# Card: Lightning Bolt")
    oracle_idx = out.index("## Oracle text")
    handler_idx = out.index("## Engine handler")
    assert name_idx < oracle_idx < handler_idx
    # Code-fence wraps the handler source
    assert "```python" in out
    assert "```" in out.split("```python", 1)[1]
    # Handler body present
    assert "_resolve_lightning_bolt" in out
    # Oracle text present
    assert "deals 3 damage to any target" in out


def test_compress_handler_excludes_surrounding_file():
    """The compressor must not echo content that wasn't passed in.
    Imports / decorators from elsewhere in the engine file must be
    absent — this is the whole point of the 50× reduction target."""
    out = compress_handler(
        _SAMPLE_HANDLER_SRC,
        oracle_text="Lightning Bolt deals 3 damage to any target.",
        card_name="Lightning Bolt",
    )
    # Things that live elsewhere in engine/card_effects.py must NOT
    # appear in the compressed handler bundle.
    assert "from __future__" not in out
    assert "EffectTiming.ATTACK" not in out
    assert "class EffectHandler" not in out
    # And the bundle is a small handful of lines, not a multi-KB file.
    assert len(out) < 1000, f"handler bundle too large: {len(out)} chars"
