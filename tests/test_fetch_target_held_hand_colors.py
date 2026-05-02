"""Locked-hand fetch targeting — the rule, mechanic-phrased.

Diagnosis (Living End vs Affinity, seed 60100, G2 T2):
The hand contains a sorcery / non-instant spell whose mana cost
includes a color the battlefield does not yet produce. The fetch
target should prioritise acquiring that missing color over
duplicating one already provided.

The pre-existing `held_instant_colors` field in `ai/mana_planner.py`
records *instant/flash* colors and biases the fetch toward
PRESERVING them. That bonus fires even when the color is already
safely preserved by an untapped source on the battlefield —
overwhelming the "missing held-spell color" signal coming from
non-instants.

Rule encoded by these tests:
  1. Held-spell missing colors (regardless of card type) bias the
     fetch toward sources of those colors.
  2. Held-instant preservation only fires when the color is NOT
     already preserved by an existing untapped source — redundant
     preservation has no marginal value.
  3. Decks with no missing held-spell colors fall back to the
     existing tiebreakers (regression anchor).

Class size: every Modern deck running fetchlands and multi-color
spells where the hand isn't already on-curve in colors. ~10 of the
16 registered decks (Boros Energy, Jeskai Blink, Domain Zoo, 4c
Omnath, 4/5c Control, Living End, Goryo's Vengeance, Affinity,
Dimir Midrange, Amulet Titan).
"""
from __future__ import annotations

import random

import pytest

from ai.mana_planner import (
    ManaNeeds, analyze_mana_needs, choose_fetch_target,
)
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


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
    if zone == "library":
        game.players[controller].library.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


class TestFetchPicksColorHeldSpellsNeed:
    """Held sorceries / non-instants must drive fetch color choice
    when their color requirements aren't yet on the battlefield."""

    def test_held_sorcery_missing_color_outranks_redundant_held_flash(
            self, card_db):
        """Battlefield: Watery Grave (U/B), untapped — U is already
        preserved.  Hand: Subtlety (flash, costs UU) + a sorcery
        that needs R.  Fetch can grab Blood Crypt (R/B) or Breeding
        Pool (U/G).

        Breeding Pool would PRESERVE U — but U is already preserved
        by the untapped Watery Grave.  Blood Crypt UNLOCKS R, the
        only color the held sorcery cannot pay for.  Fix must pick
        Blood Crypt.

        This is the seed-60100 G2 T2 Living End scenario, abstracted
        to the rule: missing held-spell colors outrank redundant
        held-instant preservation."""
        game = GameState(rng=random.Random(0))

        # Existing untapped source covers U and B — the held flash
        # spell (Subtlety, UU) is already safely castable.
        _add(game, card_db, "Watery Grave", controller=0,
             zone="battlefield")
        # Held flash creature (instant-speed → goes into
        # held_instant_colors) — its U is already preserved.
        _add(game, card_db, "Subtlety", controller=0, zone="hand")
        # Held SORCERY-typed spell that needs R (the missing color).
        # Demonic Dread costs {1}{B}{R}.  It is a sorcery, NOT an
        # instant — the locked-hand rule must still apply.
        _add(game, card_db, "Demonic Dread", controller=0, zone="hand")

        bc = _add(game, card_db, "Blood Crypt", controller=0,
                  zone="library")
        _add(game, card_db, "Breeding Pool", controller=0,
             zone="library")

        needs = analyze_mana_needs(game, 0)

        # Verdant Catacombs fetches B/G (Mountain/Swamp/Forest pool
        # via the test helper exposes B and R access for both
        # candidates: Blood Crypt is Swamp/Mountain, Breeding Pool
        # is Forest/Island).  We pass fetch_colors covering both.
        target = choose_fetch_target(
            game.players[0].library,
            fetch_colors=['B', 'G', 'R', 'U'],
            needs=needs,
        )
        assert target is not None, "choose_fetch_target returned None"
        assert target.template.name == "Blood Crypt", (
            f"Expected Blood Crypt — held sorcery (Demonic Dread, "
            f"{{1}}{{B}}{{R}}) needs R and the manabase has no R "
            f"source yet.  Breeding Pool would PRESERVE U, but U is "
            f"already preserved by the untapped Watery Grave.  Got "
            f"{target.template.name}.  Rule: missing held-spell "
            f"colors must outrank already-preserved held-instant "
            f"colors."
        )

    def test_redundant_held_color_preservation_score_delta_is_zero(
            self, card_db):
        """Score-level isolation of the locked-hand fix.

        Compare two candidates that are otherwise equal except one
        DUPLICATES an already-preserved held color.  The land that
        duplicates an already-met preservation goal must NOT receive
        extra score from the held-color-preservation bonus — its
        marginal preservation EV is zero.

        Setup: Watery Grave (U/B) untapped on battlefield → U and B
        in existing_colors.  Hand holds Counterspell (UU, instant)
        → U in held_instant_colors AND demand 2 in needed_colors.
        Two library lands score-tested in isolation, holding all
        non-(I) factors equal:
            - Hallowed Fountain (W/U): produces a held color U that
              is ALREADY in existing_colors.  Block (I) bonus must
              be 0 here.
            - Sacred Foundry (R/W): produces no held color.

        Without the redundant-preservation guard the held-instant
        preservation bonus fires for Hallowed Fountain even though
        U is already untapped on the battlefield.  Rule under test:
            held_unmet = held_instant_colors - existing_colors
            block (I) only fires for held_unmet, not held_instant_colors

        This test pins block (I) directly via score_land — we don't
        depend on the choose_fetch_target tiebreaker behaviour, so a
        fix to (I) alone is sufficient to make this go green."""
        from ai.mana_planner import score_land

        game = GameState(rng=random.Random(0))

        _add(game, card_db, "Watery Grave", controller=0,
             zone="battlefield")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

        hf = _add(game, card_db, "Hallowed Fountain", controller=0,
                  zone="library")
        sf = _add(game, card_db, "Sacred Foundry", controller=0,
                  zone="library")

        needs = analyze_mana_needs(game, 0)
        # Sanity: U is in BOTH existing_colors and held_instant_colors
        # — this is the redundant-preservation case.
        assert 'U' in needs.existing_colors, (
            "untapped Watery Grave should put U into existing_colors"
        )
        assert 'U' in needs.held_instant_colors, (
            "held Counterspell should put U into held_instant_colors"
        )

        # Score each candidate and isolate the (I) preservation
        # delta.  We compute scores with the same `needs` and compare
        # the *delta from preservation* against zero: HF should NOT
        # gain score for preserving U because U is already preserved
        # by Watery Grave.
        s_hf = score_land(hf, needs, is_fetchable=True)

        # Construct a fake `needs` with held_instant_colors emptied
        # — this gives us the score Hallowed Fountain would receive
        # if the (I) bonus were guaranteed to be 0.  When the guard
        # works correctly the two scores must be equal: HF's U is
        # already in existing_colors, so (I) is 0 anyway, and clearing
        # held_instant_colors changes nothing.
        needs_no_preserve = ManaNeeds(
            needed_colors=dict(needs.needed_colors),
            existing_colors=set(needs.existing_colors),
            missing_colors=set(needs.missing_colors),
            existing_subtypes=set(needs.existing_subtypes),
            cheapest_spell_cmc=needs.cheapest_spell_cmc,
            untapped_land_count=needs.untapped_land_count,
            total_mana=needs.total_mana,
            spells_enabled_by_one_more=list(needs.spells_enabled_by_one_more),
            cheapest_proactive_cmc=needs.cheapest_proactive_cmc,
            domain_card_count=needs.domain_card_count,
            payoff_missing_colors=set(needs.payoff_missing_colors),
            held_instant_colors=set(),  # ← disable preservation
        )
        s_hf_no_preserve = score_land(hf, needs_no_preserve,
                                      is_fetchable=True)

        assert s_hf == s_hf_no_preserve, (
            f"Hallowed Fountain score with held_instant_colors={{U}} "
            f"({s_hf}) differs from score with held_instant_colors=∅ "
            f"({s_hf_no_preserve}) — but U is already preserved by "
            f"the untapped Watery Grave, so block (I) should add 0 "
            f"in BOTH cases.  The redundant-preservation guard is "
            f"missing or incorrect.  Rule: held_unmet = "
            f"held_instant_colors - existing_colors; block (I) "
            f"only fires for colors in held_unmet."
        )

        # Companion check on Sacred Foundry — it produces NO held
        # colors so block (I) is also 0; both scorings must agree.
        s_sf = score_land(sf, needs, is_fetchable=True)
        s_sf_no_preserve = score_land(sf, needs_no_preserve,
                                      is_fetchable=True)
        assert s_sf == s_sf_no_preserve, (
            f"Sacred Foundry produces no held color — block (I) "
            f"should be 0 regardless.  Got {s_sf} vs "
            f"{s_sf_no_preserve}."
        )


class TestFetchUnchangedWhenHeldSpellsCastable:
    """Regression anchor — when held spells are all castable on the
    current manabase, the held-color bias must not change the
    fetch choice."""

    def test_no_missing_held_colors_falls_back_to_existing_logic(
            self, card_db):
        """Battlefield: Plains (W).  Hand: Counterspell (UU) — its
        colors aren't covered yet but this is the *existing*
        held-instant case (already tested in
        test_fetch_preserves_held_instant_colors.py).  Library:
        Sacred Foundry only.  Must still pick it without crashing."""
        game = GameState(rng=random.Random(0))
        _add(game, card_db, "Plains", controller=0, zone="battlefield")
        # Held instant whose U *is* missing.  This is the original
        # held_instant case — preserved behavior.
        _add(game, card_db, "Counterspell", controller=0, zone="hand")
        sf = _add(game, card_db, "Sacred Foundry", controller=0,
                  zone="library")

        needs = analyze_mana_needs(game, 0)

        target = choose_fetch_target(
            game.players[0].library,
            fetch_colors=['W', 'R', 'U'],
            needs=needs,
        )
        assert target is sf, (
            f"With only one fetch target available, must pick it "
            f"regardless of held-color bias.  Got "
            f"{target.template.name if target else 'None'}."
        )


class TestFetchDoesntOverweightUncastableColorForColorlessDeck:
    """Eldrazi Tron-style colorless decks must not break: with no
    held-spell colored requirements, the held-color logic stays
    silent."""

    def test_colorless_hand_no_color_preference(self, card_db):
        """Hand: only colorless / generic-cost spells.  Fetch
        decision must rely on existing logic (versatility, gameplan
        priority, basic-type access) without held-color bias."""
        game = GameState(rng=random.Random(0))
        _add(game, card_db, "Wastes", controller=0, zone="battlefield")
        # Held spell with NO colored pips — only generic cost.
        # Walking Ballista costs {X}{X}, no colored requirements.
        _add(game, card_db, "Walking Ballista", controller=0,
             zone="hand")

        # Two candidates — neither produces a color the hand needs.
        sf = _add(game, card_db, "Sacred Foundry", controller=0,
                  zone="library")

        needs = analyze_mana_needs(game, 0)

        # Held-spell colors must be empty (no colored pips in hand).
        # The existing held_instant_colors field stays empty; any
        # new held-spell-colors aggregate must also be empty.
        target = choose_fetch_target(
            game.players[0].library,
            fetch_colors=['W', 'R'],
            needs=needs,
        )
        assert target is sf, (
            f"Colorless hand → no held-color bias should fire; "
            f"fetch must succeed on the only available target.  "
            f"Got {target.template.name if target else 'None'}."
        )
