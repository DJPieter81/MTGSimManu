"""The silent-unhandled-effect diagnostic must be reachable from every
resolution path that can fall through to a no-op — not only the spell / ETB /
activated paths.

``engine.effect_diagnostics.record_unhandled_effect`` is the hook that makes a
card whose effect the engine cannot execute visible to
``tests/test_no_silent_unhandled_effects.py``. Historically it was called from
only four sites (activated x2, etb, spell). Three whole tails could never turn
that guardrail red because nothing recorded on them:

  * the STATIC / continuous-effect application path
    (``ContinuousEffectsManager.recalculate``),
  * the ALTERNATIVE-CAST enumeration path
    (``CastManager.can_suspend`` — a card that carries the mechanic keyword but
    whose alternative-cost clause the engine cannot parse),
  * the REPLACEMENT-effect resolution path
    (``ZoneManager.move_card`` — a "would be put into a graveyard, exile it
    instead" static that the engine does not model).

These tests drive each of those three fall-throughs with a synthetic fixture
(never a real card) and assert a distinct timing label is recorded. Recording
an unhandled effect never changes resolution behaviour, so this is a pure
guardrail — the sim outcome (and the WR anchor) is unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace

from engine import effect_diagnostics


def _fake_player():
    return SimpleNamespace(
        library=[], hand=[], battlefield=[], graveyard=[], exile=[],
        # ZoneManager.move_card advances this per-turn discard counter on a
        # hand -> graveyard transition (CR 701.8a); the fake must carry it.
        cards_discarded_or_cycled_this_turn=0,
    )


def test_static_application_records_effect_with_no_apply_callable():
    """A continuous effect that selects cards but carries no ``apply`` callable
    silently modifies nothing — the static-application path must record it."""
    from engine.continuous_effects import (
        ContinuousEffectsManager, ContinuousEffect, Layer, PTSublayer,
    )

    effect_diagnostics.reset()
    cem = ContinuousEffectsManager()
    # affected present, apply MISSING → matches its class but does nothing.
    # duration != "permanent" so the source-on-battlefield cleanup keeps it.
    cem.register(ContinuousEffect(
        source_id=999_001,
        source_name="SyntheticStaticNoOp",
        layer=Layer.POWER_TOUGHNESS,
        pt_sublayer=PTSublayer.STATIC_MOD,
        affected=lambda g, c: False,
        apply=None,
        duration="end_of_turn",
    ))
    game = SimpleNamespace(players=[_fake_player(), _fake_player()])
    cem.recalculate(game)

    assert ("SyntheticStaticNoOp", "static") in effect_diagnostics.unhandled_effects()


def test_static_application_does_not_record_fully_specified_effect():
    """A normal lord/anthem effect (affected AND apply present) must NOT record —
    only genuine no-op registrations do."""
    from engine.continuous_effects import (
        ContinuousEffectsManager, ContinuousEffect, Layer, PTSublayer,
    )

    effect_diagnostics.reset()
    cem = ContinuousEffectsManager()
    cem.register(ContinuousEffect(
        source_id=999_002,
        source_name="SyntheticWellFormedLord",
        layer=Layer.POWER_TOUGHNESS,
        pt_sublayer=PTSublayer.STATIC_MOD,
        affected=lambda g, c: False,
        apply=lambda g, c: None,
        duration="end_of_turn",
    ))
    game = SimpleNamespace(players=[_fake_player(), _fake_player()])
    cem.recalculate(game)

    assert not any(
        name == "SyntheticWellFormedLord"
        for name, _timing in effect_diagnostics.unhandled_effects()
    )


def test_alternative_cast_records_unparseable_suspend_clause():
    """A card that carries the SUSPEND keyword but whose alternative-cost clause
    the engine cannot parse falls through the alt-cast enumeration to a no-op —
    it must be recorded, not silently treated as un-suspendable."""
    from engine.cards import Keyword
    from engine.cast_manager import CastManager

    effect_diagnostics.reset()
    template = SimpleNamespace(
        name="SyntheticSuspendNoClause",
        keywords={Keyword.SUSPEND},
        # No parseable "Suspend N—{cost}" clause.
        oracle_text="Suspend (this text has no cost the parser can read).",
    )
    card = SimpleNamespace(zone="hand", template=template)
    # game is never reached — the record + return False happen at the clause
    # parse, before any player state is touched.
    ok = CastManager.can_suspend(SimpleNamespace(players=[]), 0, card)

    assert ok is False
    assert (
        ("SyntheticSuspendNoClause", "alt_cast")
        in effect_diagnostics.unhandled_effects()
    )


def test_replacement_records_unmodeled_graveyard_exile_static():
    """When a card is put into a graveyard while a permanent with the
    "would be put into a graveyard, exile it instead" static (Rest in Peace /
    Leyline of the Void family) is on the battlefield, the engine does not model
    that replacement. The replacement path must record the unmodeled static."""
    from engine.zone_manager import ZoneManager

    effect_diagnostics.reset()
    hate = SimpleNamespace(
        instance_id=1,
        template=SimpleNamespace(
            name="SyntheticGraveyardExiler",
            exiles_cards_bound_for_graveyard=True,
        ),
    )
    p0 = _fake_player()
    p0.battlefield.append(hate)
    moving = SimpleNamespace(
        owner=0, zone="hand", name="SomeDiscardedCard",
        template=SimpleNamespace(name="SomeDiscardedCard"),
        instance_id=2,
    )
    p0.hand.append(moving)
    game = SimpleNamespace(players=[p0, _fake_player()], log=[], display_turn=1)

    zm = ZoneManager()
    zm.move_card(game, moving, "hand", "graveyard", cause="discard")

    assert (
        ("SyntheticGraveyardExiler", "replacement")
        in effect_diagnostics.unhandled_effects()
    )


def test_replacement_not_recorded_without_hate_permanent():
    """A plain graveyard move with no replacement static present records
    nothing — the replacement hook is conservative."""
    from engine.zone_manager import ZoneManager

    effect_diagnostics.reset()
    p0 = _fake_player()
    moving = SimpleNamespace(
        owner=0, zone="hand", name="SomeDiscardedCard",
        template=SimpleNamespace(name="SomeDiscardedCard"),
        instance_id=2,
    )
    p0.hand.append(moving)
    game = SimpleNamespace(players=[p0, _fake_player()], log=[], display_turn=1)

    zm = ZoneManager()
    zm.move_card(game, moving, "hand", "graveyard", cause="discard")

    assert not any(
        timing == "replacement"
        for _name, timing in effect_diagnostics.unhandled_effects()
    )
