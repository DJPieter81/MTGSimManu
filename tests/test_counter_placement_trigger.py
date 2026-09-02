"""Counter-placement triggers — "whenever one or more +1/+1 counters are put
on this creature, <effect>" (CR 122, CR 603.2c).

# Mechanic under test

DB sizing (22,506 Modern cards, measured 2026-09-02): 16 cards carry the
self-scoped shape "whenever one or more +1/+1 counters are put on this
<creature|permanent>, …" — Basking Broodscale, Benthic Biomancer, Constable
of the Realm, Cursed Wombat, Dreamdrinker Vampire, Dusk Legion Duelist,
Emperor of Bones, Evolution Witness, Fetid Gargantua, Growth-Chamber
Guardian, Herd Baloth, Knighted Myr, Pensive Professor, Scurry Oak,
Sharktocrab, Berta (name-scoped).  Before this change NOTHING in the engine
recognised the trigger: counters were placed by ~13 independent
`plus_counters += n` writes scattered across engine/ and ai/, and none of
them could fire anything.

Deliberately NOT modelled (each below the ~10-card class threshold):
  * "another creature you control" / "a creature you control" watchers
    (Enduring Scalelord, Wildwood Scourge, Simic Ascendancy, … — 8 cards):
    a different watch scope, and two of them loop back onto counters.
  * "whenever A +1/+1 counter is put on" (2 cards) — fires once per COUNTER,
    not per event (CR 603.2c), so it is not the same trigger shape.

Rules modelled:
  * CR 603.2c / "one or more" — one placement event that puts N counters at
    once fires the trigger EXACTLY once; two separate placement events fire
    it twice.
  * CR 614.1c — a permanent that ENTERS with counters (modular, "enters
    with X +1/+1 counters", "…onto the battlefield with N counters") has had
    counters put on it, so the trigger fires (Herd Baloth ruling 2015-02-25).
  * CR 122 — counters put on an object that is not on the battlefield do not
    trigger battlefield abilities.
  * A token created by the trigger goes through the ONE token factory
    (`PermanentEffects.create_token`), so the "with '<ability>'" rider on
    the token (Eldrazi Spawn's "Sacrifice this token: Add {C}") is honoured.

Structural rule: every +1/+1 counter placement in engine/ and ai/ routes
through `CardInstance.add_plus_counters` — the single funnel that fires the
trigger.  A raw `plus_counters +=` anywhere else is a placement the trigger
cannot see.

Real DB cards appear below only as fixture carriers for the oracle shape;
the engine keys on the typed `CardTemplate.counter_placement_trigger` field,
never on a card name.
"""
from __future__ import annotations

import pathlib
import random
import re

import pytest

from engine.cards import (
    COUNTER_KIND_PLUS,
    COUNTER_TRIGGER_EFFECT_DRAW,
    COUNTER_TRIGGER_EFFECT_TOKEN,
    COUNTER_TRIGGER_EFFECT_UNRESOLVED,
    CardInstance,
    CardTemplate,
    CardType,
)
from engine.game_state import GameState
from engine.mana import ManaCost
from engine.oracle_parser import parse_counter_placement_trigger

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_SPAWN_CLAUSE = (
    "Whenever one or more +1/+1 counters are put on this creature, you may "
    "create a 0/1 colorless Eldrazi Spawn creature token with "
    "\"Sacrifice this token: Add {C}.\""
)
_DRAW_CLAUSE = (
    "Whenever one or more +1/+1 counters are put on this creature, "
    "draw a card."
)


# ── Fixture helpers ───────────────────────────────────────────────────

def _creature(oracle: str = "", name: str = "Test Creature",
              power: int = 2, toughness: int = 2) -> CardTemplate:
    return CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(),
        power=power,
        toughness=toughness,
        oracle_text=oracle,
    )


def _bf(game: GameState, tmpl: CardTemplate, controller: int = 0,
        zone: str = "battlefield") -> CardInstance:
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        game.players[controller].battlefield.append(card)
    return card


def _tokens(game: GameState, controller: int = 0):
    return [c for c in game.players[controller].battlefield if c.is_token]


# ── Parser: one oracle shape in, one typed field out ──────────────────

class TestCounterPlacementTriggerParses:

    def test_token_effect_parses_into_token_shape(self):
        trig = parse_counter_placement_trigger(_SPAWN_CLAUSE)
        assert trig is not None, "the self-scoped shape must parse"
        assert trig.effect == COUNTER_TRIGGER_EFFECT_TOKEN
        assert trig.count == 1
        assert "token" in trig.effect_text.lower()

    def test_draw_effect_parses_with_count(self):
        trig = parse_counter_placement_trigger(
            "Whenever one or more +1/+1 counters are put on this creature, "
            "you may draw two cards.")
        assert trig is not None
        assert trig.effect == COUNTER_TRIGGER_EFFECT_DRAW
        assert trig.count == 2

    def test_unknown_effect_still_yields_a_trigger(self):
        """The TRIGGER is the mechanic; an effect the engine cannot resolve
        must not hide the trigger (it fires and logs)."""
        trig = parse_counter_placement_trigger(
            "Whenever one or more +1/+1 counters are put on this creature, "
            "it gains double strike until end of turn.")
        assert trig is not None
        assert trig.effect == COUNTER_TRIGGER_EFFECT_UNRESOLVED

    def test_draw_then_discard_is_not_a_plain_draw(self):
        """'draw a card, then discard a card' is a loot — resolving only
        the draw half would hand out card advantage the card does not
        grant, so it stays unresolved."""
        trig = parse_counter_placement_trigger(
            "Whenever one or more +1/+1 counters are put on this creature, "
            "draw a card, then discard a card.")
        assert trig is not None
        assert trig.effect == COUNTER_TRIGGER_EFFECT_UNRESOLVED

    def test_other_permanent_watch_scope_is_not_parsed(self):
        assert parse_counter_placement_trigger(
            "Whenever one or more +1/+1 counters are put on another creature "
            "you control, put a +1/+1 counter on this creature.") is None

    def test_single_counter_shape_is_not_parsed(self):
        """'a +1/+1 counter is put on' fires per COUNTER (CR 603.2c), a
        different shape — deliberately outside this class."""
        assert parse_counter_placement_trigger(
            "Whenever a +1/+1 counter is put on this creature, "
            "draw a card.") is None

    def test_name_scoped_self_reference_parses_when_name_given(self):
        trig = parse_counter_placement_trigger(
            "Whenever one or more +1/+1 counters are put on Berta, add one "
            "mana of any color.", name="Berta, Wise Extrapolator")
        assert trig is not None

    def test_no_trigger_text_parses_to_none(self):
        assert parse_counter_placement_trigger(
            "Flying\nWhen this creature enters, draw a card.") is None

    def test_synthetic_template_derives_field_from_oracle(self):
        tmpl = _creature(_SPAWN_CLAUSE)
        assert tmpl.counter_placement_trigger is not None
        assert tmpl.counter_placement_trigger.effect == COUNTER_TRIGGER_EFFECT_TOKEN


class TestCounterPlacementTriggerPopulatedAtLoad:

    def test_db_card_carries_typed_field(self, card_db):
        tmpl = card_db.get_card("Basking Broodscale")
        if tmpl is None:
            pytest.skip("fixture carrier not in DB")
        trig = tmpl.counter_placement_trigger
        assert trig is not None, (
            "card_database must populate counter_placement_trigger at load")
        assert trig.effect == COUNTER_TRIGGER_EFFECT_TOKEN


# ── Funnel: the trigger fires once per placement EVENT ────────────────

class TestCounterPlacementFiresOncePerPlacementEvent:

    def test_two_counters_placed_at_once_fire_once(self):
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature(_SPAWN_CLAUSE))
        card.add_plus_counters(2, game)
        assert card.plus_counters == 2
        assert len(_tokens(game)) == 1, (
            "'one or more' — one placement event of 2 counters is ONE trigger")

    def test_two_separate_placements_fire_twice(self):
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature(_SPAWN_CLAUSE))
        card.add_plus_counters(1, game)
        card.add_plus_counters(1, game)
        assert card.plus_counters == 2
        assert len(_tokens(game)) == 2

    def test_zero_counters_is_not_a_placement(self):
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature(_SPAWN_CLAUSE))
        card.add_plus_counters(0, game)
        assert card.plus_counters == 0
        assert _tokens(game) == []

    def test_adjust_counters_plus_kind_routes_through_the_funnel(self):
        """The activation-cost path ('put a +1/+1 counter on this: …') puts
        counters via adjust_counters — that IS a placement event."""
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature(_SPAWN_CLAUSE))
        card.adjust_counters(COUNTER_KIND_PLUS, 1)
        assert card.plus_counters == 1
        assert len(_tokens(game)) == 1

    def test_removing_counters_does_not_fire(self):
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature(_SPAWN_CLAUSE))
        card.add_plus_counters(1, game)
        card.adjust_counters(COUNTER_KIND_PLUS, -1)
        assert card.plus_counters == 0
        assert len(_tokens(game)) == 1, "removal is not a placement"

    def test_trigger_logs_a_line(self):
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature(_SPAWN_CLAUSE, name="Test Creature"))
        card.add_plus_counters(1, game)
        assert any("Test Creature" in line and "+1/+1 counter" in line
                   for line in game.log)


# ── Effect resolution ─────────────────────────────────────────────────

class TestCounterPlacementTriggerResolvesDeclaredEffect:

    def test_token_effect_creates_the_declared_token(self):
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature(_SPAWN_CLAUSE))
        card.add_plus_counters(1, game)
        toks = _tokens(game)
        assert len(toks) == 1
        tok = toks[0]
        assert tok.controller == 0
        assert tok.zone == "battlefield"
        assert (tok.power, tok.toughness) == (0, 1)
        assert CardType.CREATURE in tok.template.card_types

    def test_token_created_through_factory_keeps_its_granted_ability(self):
        """The declared token has 'Sacrifice this token: Add {C}' — only the
        shared factory parses that rider into sacrifice_mana_units."""
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature(_SPAWN_CLAUSE))
        card.add_plus_counters(1, game)
        tok = _tokens(game)[0]
        assert tok.template.sacrifice_mana_units, (
            "token must carry its sacrifice-for-mana ability")

    def test_draw_effect_draws_the_declared_number_of_cards(self):
        game = GameState(rng=random.Random(0))
        filler = _creature("", name="Filler")
        for _ in range(3):
            game.players[0].library.append(
                _bf(game, filler, zone="library"))
        hand_before = len(game.players[0].hand)
        card = _bf(game, _creature(_DRAW_CLAUSE))
        card.add_plus_counters(3, game)
        assert len(game.players[0].hand) == hand_before + 1

    def test_unresolved_effect_fires_without_side_effects(self):
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature(
            "Whenever one or more +1/+1 counters are put on this creature, "
            "it gains double strike until end of turn."))
        log_before = len(game.log)
        card.add_plus_counters(1, game)
        assert card.plus_counters == 1
        assert _tokens(game) == []
        assert len(game.log) > log_before, "the trigger itself is logged"


# ── Negative space ────────────────────────────────────────────────────

class TestCounterPlacementWithoutTriggerFiresNothing:

    def test_creature_without_trigger_places_counters_silently(self):
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature("Flying"))
        log_before = len(game.log)
        card.add_plus_counters(2, game)
        assert card.plus_counters == 2
        assert _tokens(game) == []
        assert len(game.log) == log_before

    def test_counters_on_a_card_not_on_the_battlefield_do_not_trigger(self):
        game = GameState(rng=random.Random(0))
        card = _bf(game, _creature(_SPAWN_CLAUSE), zone="hand")
        card.add_plus_counters(1, game)
        assert card.plus_counters == 1
        assert _tokens(game) == []


# ── Entering with counters IS a placement (CR 614.1c) ─────────────────

class TestEnteringWithCountersIsAPlacementEvent:

    def test_modular_entry_fires_the_trigger_once(self):
        from engine.spell_resolution import ResolutionManager
        game = GameState(rng=random.Random(0))
        tmpl = CardTemplate(
            name="Test Modular Creature",
            card_types=[CardType.ARTIFACT, CardType.CREATURE],
            mana_cost=ManaCost(), power=0, toughness=0,
            oracle_text=(
                "Modular 2 (This creature enters with 2 +1/+1 counters on "
                "it. When it dies, you may put its +1/+1 counters on target "
                "artifact creature.)\n" + _SPAWN_CLAUSE),
        )
        card = _bf(game, tmpl)
        ResolutionManager._handle_permanent_etb(game, card, 0)
        assert card.plus_counters == 2
        assert len(_tokens(game)) == 1, (
            "entering with 2 counters is ONE placement event")

    def test_modular_death_transfer_is_a_placement_on_the_recipient(self):
        from engine.permanent_effects import PermanentEffects
        game = GameState(rng=random.Random(0))
        donor_tmpl = CardTemplate(
            name="Test Modular Donor",
            card_types=[CardType.ARTIFACT, CardType.CREATURE],
            mana_cost=ManaCost(), power=0, toughness=0,
            oracle_text=(
                "Modular 1 (This creature enters with a +1/+1 counter on "
                "it. When it dies, you may put its +1/+1 counters on target "
                "artifact creature.)"),
        )
        recipient_tmpl = CardTemplate(
            name="Test Recipient",
            card_types=[CardType.ARTIFACT, CardType.CREATURE],
            mana_cost=ManaCost(), power=1, toughness=1,
            oracle_text=_SPAWN_CLAUSE,
        )
        donor = _bf(game, donor_tmpl)
        donor.add_plus_counters(1, game)
        recipient = _bf(game, recipient_tmpl)
        PermanentEffects._creature_dies(game, donor)
        assert recipient.plus_counters == 1
        assert len(_tokens(game)) == 1


# ── Structural: one funnel for every placement ────────────────────────

_RAW_PLACEMENT = re.compile(r"\.plus_counters\s*\+=")


def _raw_placement_sites():
    hits = []
    for sub in ("engine", "ai"):
        for path in sorted((_ROOT / sub).glob("*.py")):
            if path == _ROOT / "engine" / "cards.py":
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _RAW_PLACEMENT.search(line) and not line.strip().startswith("#"):
                    hits.append(f"{path.relative_to(_ROOT)}:{i}")
    return hits


def test_every_plus_counter_placement_routes_through_the_funnel():
    hits = _raw_placement_sites()
    assert hits == [], (
        "raw `plus_counters +=` bypasses the counter-placement trigger; "
        "use CardInstance.add_plus_counters(n, game):\n  " + "\n  ".join(hits))


def test_the_funnel_itself_is_the_only_raw_increment():
    src = (_ROOT / "engine" / "cards.py").read_text(encoding="utf-8")
    assert len(_RAW_PLACEMENT.findall(src)) == 1, (
        "engine/cards.py must hold exactly one raw increment — inside "
        "add_plus_counters")
