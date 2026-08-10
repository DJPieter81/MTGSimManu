"""Generic "power and toughness are each equal to the number of X you
control" characteristic-defining ability (CDA) support.

`engine.oracle_parser.detect_power_scaling` had 4 buckets (domain,
tarmogoyf, delirium, graveyard) but no bucket for the single most
common CDA shape in Magic — "power/toughness = permanents of type X
you control" (Cultivator Colossus, Crusader of Odric, Darksteel
Juggernaut, Master of Etherium, ... 47 cards share this exact
phrasing in the registered card DB). Falling through to
`template.power or 0` = 0/0, Cultivator Colossus died to state-based
actions the moment it resolved.

Also fixes a false-positive in the pre-existing "graveyard" bucket:
the bare co-occurrence of 'exile' + 'instant'/'sorcery' + 'graveyard'
matched 293/21795 cards in the DB, almost all Embalm/Eternalize/
Scavenge reminder text ("Exile this card from your graveyard: ..."),
including live-pool card Murktide Regent (whose real scaling
mechanism is delve-triggered +1/+1 counters, not a continuous
graveyard-count CDA).

Card names appear only as fixture carriers (synthetic CardTemplates
and real DB lookups) per CLAUDE.md's ABSTRACTION CONTRACT — the
mechanic under test is the permanent-count CDA family, not any
specific card.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState
from engine.oracle_parser import detect_power_scaling


# ─── oracle-parser unit tests ──────────────────────────────────────


class TestDetectPermanentCountScaling:
    def test_lands_you_control_detected(self):
        assert detect_power_scaling(
            "Trample\nThis creature's power and toughness are each "
            "equal to the number of lands you control."
        ) == "permanent_count:lands"

    def test_creatures_you_control_detected(self):
        assert detect_power_scaling(
            "This creature's power and toughness are each equal to "
            "the number of creatures you control."
        ) == "permanent_count:creatures"

    def test_artifacts_you_control_detected(self):
        assert detect_power_scaling(
            "Indestructible\nThis creature's power and toughness are "
            "each equal to the number of artifacts you control."
        ) == "permanent_count:artifacts"

    def test_permanents_you_control_detected(self):
        assert detect_power_scaling(
            "This creature's power and toughness are each equal to "
            "the number of permanents you control."
        ) == "permanent_count:permanents"

    def test_subtype_you_control_detected(self):
        assert detect_power_scaling(
            "This creature's power and toughness are each equal to "
            "the number of Soldiers you control."
        ) == "permanent_count:soldiers"

    def test_no_scaling_returns_empty_string(self):
        assert detect_power_scaling("Deal 3 damage to any target.") == ""


class TestGraveyardScalingRequiresPTAnchor:
    """The graveyard bucket must not fire on Embalm/Eternalize/
    Scavenge reminder text that happens to mention exile+instant-or-
    sorcery+graveyard without ever defining a continuous P/T CDA."""

    def test_eternalize_reminder_text_is_not_graveyard_scaling(self):
        """Regression fixture mirroring the false-positive shape: an
        Eternalize creature whose reminder text says 'Exile this card
        from your graveyard' near unrelated instant/sorcery text
        elsewhere on the card — no power/toughness CDA is present."""
        oracle = (
            "Flying\n"
            "Whenever this creature attacks, target instant or "
            "sorcery card in your graveyard gains flashback.\n"
            "Eternalize {3}{U}{U} ({3}{U}{U}, Exile this card from "
            "your graveyard: Create a token that's a copy of it, "
            "except it's a 4/4 black Zombie in addition to its other "
            "types and isn't legendary.)"
        )
        assert detect_power_scaling(oracle) == ""

    def test_delve_counter_creature_is_not_graveyard_scaling(self):
        """Regression fixture mirroring Murktide Regent's exact shape
        — delve (exile from graveyard as a COST) plus +1/+1 counters
        triggered by instant/sorcery cards leaving the graveyard is
        NOT a continuous 'power = graveyard count' CDA."""
        oracle = (
            "Delve (Each card you exile from your graveyard while "
            "casting this spell pays for {1}.)\n"
            "Flying\n"
            "This creature enters with a +1/+1 counter on it for "
            "each instant and sorcery card exiled with it.\n"
            "Whenever an instant or sorcery card leaves your "
            "graveyard, put a +1/+1 counter on this creature."
        )
        assert detect_power_scaling(oracle) == ""

    def test_real_power_equals_graveyard_instants_is_still_detected(self):
        """Positive control: a genuine continuous CDA in this family
        (power is equal to the number of instant/sorcery cards in
        your graveyard) must still be detected — the P/T-anchor guard
        must not overcorrect into a false negative.

        Returns the structured `graveyard_count:power_only:
        instant_and_sorcery:your` bucket (the CDA coverage extension
        generalized the old bare "graveyard" string into a
        formula/type/scope-parameterized bucket — see
        tests/test_graveyard_count_cda.py). This is a power-ONLY CDA
        (no toughness clause), which the new bucket now tracks
        correctly; the old bare "graveyard" bucket applied the same
        count to toughness too, which was wrong for exactly this
        shape (Enigma Drake/Haughty Djinn/Kinetic Augur/Spellheart
        Chimera all share it in the real DB)."""
        oracle = (
            "Flying\n"
            "This creature's power is equal to the number of instant "
            "and sorcery cards in your graveyard."
        )
        assert detect_power_scaling(oracle) == (
            "graveyard_count:power_only:instant_and_sorcery:your"
        )

    def test_real_db_murktide_regent_not_graveyard_scaling(self, card_db):
        murktide = card_db.get_card("Murktide Regent")
        if murktide is None:
            pytest.skip("Murktide Regent not in DB")
        assert murktide.power_scales_with != "graveyard", (
            "Murktide Regent's power_scales_with must not be "
            "'graveyard' — its actual mechanism is delve-triggered "
            "+1/+1 counters (already modeled via plus_counters), not "
            "a continuous graveyard-count CDA."
        )
        assert not murktide.power_scales_with.startswith("graveyard_count:"), (
            "Murktide Regent must also not fall into the generalized "
            "graveyard_count:<formula>:<type>:<scope> bucket — same "
            "reasoning as above, just re-checked against the new "
            "bucket family."
        )


# ─── real-DB structured-field integration ──────────────────────────


class TestRealCardsGetPermanentCountBucket:
    def test_cultivator_colossus(self, card_db):
        t = card_db.get_card("Cultivator Colossus")
        if t is None:
            pytest.skip("Cultivator Colossus not in DB")
        assert t.power_scales_with == "permanent_count:lands"

    def test_crusader_of_odric(self, card_db):
        t = card_db.get_card("Crusader of Odric")
        if t is None:
            pytest.skip("Crusader of Odric not in DB")
        assert t.power_scales_with == "permanent_count:creatures"

    def test_no_false_positive_across_full_db(self, card_db):
        """The graveyard-bucket P/T-anchor guard must sharply reduce
        the false-positive count DB-wide, not just for the two
        fixtures above."""
        graveyard_tagged = [
            name for name, t in card_db.cards.items()
            if t.power_scales_with == "graveyard"
        ]
        assert len(graveyard_tagged) < 20, (
            f"expected the P/T-anchor guard to leave well under 20 "
            f"real graveyard-count CDAs DB-wide; got "
            f"{len(graveyard_tagged)}: {graveyard_tagged[:10]}"
        )


# ─── live P/T computation ──────────────────────────────────────────


def _permanent_count_creature(name, count_word, power=0, toughness=0):
    return CardTemplate(
        name=name, card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False,
        oracle_text=(
            f"This creature's power and toughness are each equal to "
            f"the number of {count_word} you control."
        ),
        tags=set(),
        power_scales_with=f"permanent_count:{count_word}",
    )


def _battlefield_permanent(game, name, controller, card_types,
                            subtypes=None):
    tmpl = CardTemplate(
        name=name, card_types=card_types,
        mana_cost=ManaCost(generic=0), supertypes=[],
        subtypes=subtypes or [], power=None, toughness=None,
        loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


class TestLivePermanentCountPT:
    def test_survives_its_own_etb_with_lands_present(self):
        """Fixes the audited bug: a permanent-count CDA creature with
        0/0 printed stats must NOT compute as 0/0 while lands (or
        whatever it counts) are present on the battlefield."""
        game = GameState(rng=random.Random(0))
        tmpl = _permanent_count_creature("Colossus-shaped", "lands")
        colossus = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        colossus._game_state = game
        game.players[0].battlefield.append(colossus)
        for i in range(4):
            _battlefield_permanent(game, f"Land{i}", 0, [CardType.LAND])

        assert colossus.power == 4
        assert colossus.toughness == 4

    def test_zero_lands_is_zero_zero(self):
        game = GameState(rng=random.Random(0))
        tmpl = _permanent_count_creature("Colossus-shaped", "lands")
        colossus = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        colossus._game_state = game
        game.players[0].battlefield.append(colossus)

        assert colossus.power == 0
        assert colossus.toughness == 0

    def test_creature_count_excludes_noncreatures(self):
        game = GameState(rng=random.Random(0))
        tmpl = _permanent_count_creature("Crusader-shaped", "creatures")
        crusader = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        crusader._game_state = game
        game.players[0].battlefield.append(crusader)
        _battlefield_permanent(game, "SomeArtifact", 0, [CardType.ARTIFACT])
        _battlefield_permanent(game, "SomeCreature", 0, [CardType.CREATURE])

        # Crusader itself counts as a creature too (CR 613 doesn't
        # exclude the CDA's own permanent from its own count).
        assert crusader.power == 2

    def test_subtype_count(self):
        game = GameState(rng=random.Random(0))
        tmpl = _permanent_count_creature("Tribal-shaped", "Soldiers")
        lord = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        lord._game_state = game
        game.players[0].battlefield.append(lord)
        _battlefield_permanent(game, "Soldier1", 0, [CardType.CREATURE],
                               subtypes=["Soldier"])
        _battlefield_permanent(game, "NotASoldier", 0, [CardType.CREATURE],
                               subtypes=["Goblin"])

        assert lord.power == 1

    def test_permanents_word_counts_everything(self):
        game = GameState(rng=random.Random(0))
        tmpl = _permanent_count_creature("Katilda-shaped", "permanents")
        katilda = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        katilda._game_state = game
        game.players[0].battlefield.append(katilda)
        _battlefield_permanent(game, "L", 0, [CardType.LAND])
        _battlefield_permanent(game, "A", 0, [CardType.ARTIFACT])

        assert katilda.power == 3  # itself + land + artifact
