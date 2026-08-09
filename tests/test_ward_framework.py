""""Whenever this permanent becomes the target ... counter ... unless
its controller pays [cost]" — Ward (CR 702.21a).

Mechanic under test: Ward is a triggered ability that lives on the
TARGETED PERMANENT, not on the spell/ability that targets it. Before
this fix, no Ward enforcement existed anywhere in the engine — a spell
could legally target a Ward permanent and simply resolve, with the
tax-or-counter decision never offered to anyone. Confirmed via a
codebase-wide grep for "ward" (case-insensitive) across `engine/` and
`ai/` prior to this change: zero hits outside comments/docstrings.

Structurally this is the mirror image of the Phase-1a counter-tax
framework (`tests/test_counter_tax_framework.py`): there, a
counterSPELL taxes the TARGETED spell's controller; here, the TARGETED
PERMANENT taxes the SOURCE spell/ability's own caster for having
chosen it as a target at all. Same typed `OptionalCost` /
`decide_optional_cost` machinery, reused rather than a new mechanic-
named callback (`engine/optional_costs.py::offer_ward_tax`,
`ai/ev_evaluator.py::project_ward_tax_payment`).

Scope: mana-cost-shaped Ward ("Ward {N}") only — the dominant shape
DB-wide (76 cards vs 15 life-shaped, 26 other cost shapes — see
`parse_ward_cost`'s docstring for the full census). Life/discard/
sacrifice-shaped Ward and ward CONFERRED to another object (Equipment,
an activated ability that temporarily animates a permanent) are
documented, excluded gaps, not covered by this pass.

Card names appear only as fixture carriers (synthetic CardTemplates
preserving real oracle-text shapes) or as real-DB spot checks per
CLAUDE.md's ABSTRACTION CONTRACT — the mechanic under test is the
"becomes a target -> counter unless pay" ward trigger, not any
specific card.
"""
from __future__ import annotations

import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.cards import (
    Ability, AbilityType, CardInstance, CardTemplate, CardType, ManaCost,
)
from engine.game_state import GameState
from engine.oracle_parser import parse_ward_cost
from engine.stack import StackItem, StackItemType


# ─── oracle-parser unit tests ──────────────────────────────────────


class TestParseWardCost:
    """`parse_ward_cost` extracts the {N} from a mana-shaped Ward
    clause and returns 0 for every other shape (no ward at all,
    non-mana-shaped ward, ward conferred to another object)."""

    def test_no_ward_at_all(self):
        assert parse_ward_cost("Flying, vigilance.") == 0

    def test_standalone_ward_clause_extracts_amount(self):
        assert parse_ward_cost("Ward {2}") == 2

    def test_ward_clause_with_reminder_text(self):
        assert parse_ward_cost(
            "Ward {4} (Whenever this creature becomes the target of a "
            "spell or ability an opponent controls, counter it unless "
            "that player pays {4}.)"
        ) == 4

    def test_ward_among_other_abilities(self):
        oracle = "Flying\nWard {3}\nWhenever this creature attacks, draw a card."
        assert parse_ward_cost(oracle) == 3

    def test_life_shaped_ward_returns_zero(self):
        """'Ward—Pay N life' is a real ward, but a different cost
        shape (excluded from this pass — see parse_ward_cost's
        docstring)."""
        assert parse_ward_cost("Ward—Pay 7 life.") == 0

    def test_discard_shaped_ward_returns_zero(self):
        assert parse_ward_cost("Ward—Discard a card.") == 0

    def test_ward_conferred_to_another_object_not_captured(self):
        """Equipment granting ward to whatever it's attached to is a
        dynamic keyword grant, not a static field on the Equipment's
        OWN template — the clause doesn't START with 'ward' so it
        must not match."""
        assert parse_ward_cost(
            "Equipped creature gets +1/+0 and has haste and ward {1}. "
            "Equip {1}."
        ) == 0

    def test_ward_granted_by_activated_ability_not_captured(self):
        assert parse_ward_cost(
            "{5}{U}: Until end of turn, this land becomes a 7/7 blue "
            "Giant creature with ward {3}. It's still a land."
        ) == 0


# ─── structured template field (real cards) ────────────────────────


class TestWardTemplateField:
    """`CardTemplate.ward_cost` is populated at DB-load time from
    `engine.oracle_parser.parse_ward_cost`."""

    def test_mana_shaped_ward_card_has_amount(self, card_db):
        kappa = card_db.get_card("Kappa Cannoneer")
        if kappa is None:
            pytest.skip("Kappa Cannoneer not in DB")
        assert kappa.ward_cost == 4

    def test_life_shaped_ward_card_has_zero(self, card_db):
        """Sire of Seven Deaths' 'Ward—Pay 7 life' is a real ward of
        an excluded cost shape — documented gap, not a false
        negative."""
        sire = card_db.get_card("Sire of Seven Deaths")
        if sire is None:
            pytest.skip("Sire of Seven Deaths not in DB")
        assert sire.ward_cost == 0

    def test_ward_conferred_by_equipment_not_on_equipment_itself(self, card_db):
        """Lavaspur Boots grants ward {1} to the EQUIPPED creature —
        the Boots' own template must not claim ward_cost, since the
        Boots themselves are never the thing that gets targeted."""
        boots = card_db.get_card("Lavaspur Boots")
        if boots is None:
            pytest.skip("Lavaspur Boots not in DB")
        assert boots.ward_cost == 0

    def test_ward_granted_by_land_animation_not_static(self, card_db):
        hall = card_db.get_card("Hall of Storm Giants")
        if hall is None:
            pytest.skip("Hall of Storm Giants not in DB")
        assert hall.ward_cost == 0

    def test_db_wide_positive_control(self, card_db):
        """At least one other real Modern-legal card besides the
        registered-pool's Kappa Cannoneer has a nonzero ward_cost —
        guards against the parser regressing to always-zero."""
        hits = [name for name, t in card_db.cards.items()
                if getattr(t, 'ward_cost', 0) > 0]
        assert len(hits) >= 10, (
            f"expected the mana-shaped Ward bucket to clear the "
            f"class-size bar (>=10 cards), got {len(hits)}"
        )

    def test_no_false_positive_on_plus_one_counter_or_toward_text(self, card_db):
        """Regression guard at DB scale: a card whose only relation to
        the substring 'ward' is an unrelated word ('toward', 'award',
        '+1/+1 counter') must not be flagged. Every flagged card's
        oracle text must contain a clause literally starting with the
        word 'ward'."""
        from engine.oracle_clauses import split_clauses
        false_positives = []
        for name, tmpl in card_db.cards.items():
            if getattr(tmpl, 'ward_cost', 0) <= 0:
                continue
            oracle = tmpl.oracle_text or ''
            if not any(c.strip().lower().startswith('ward')
                       for c in split_clauses(oracle)):
                false_positives.append(name)
        assert not false_positives, (
            f"{len(false_positives)} card(s) flagged ward_cost>0 "
            f"without a clause starting with 'ward': {false_positives[:10]}"
        )


# ─── resolution-engine integration ─────────────────────────────────


def _warded_permanent_template(ward_cost=2):
    return CardTemplate(
        name="Test Fixture: Warded Creature",
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=2),
        supertypes=[], subtypes=["Construct"],
        power=2, toughness=2, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text=f"Ward {{{ward_cost}}}",
        tags=set(),
        ward_cost=ward_cost,
    )


def _removal_spell_template():
    return CardTemplate(
        name="Test Fixture: Removal Spell",
        card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=1, red=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(),
        abilities=[Ability(ability_type=AbilityType.CAST,
                           description="Destroy target creature.",
                           targets_required=1)],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="Destroy target creature.",
        tags=set(),
    )


def _land(game, controller, n):
    """N untapped colorless-producing lands so the tax is affordable."""
    lands = []
    for _ in range(n):
        tmpl = CardTemplate(
            name="Test Fixture: Basic Land",
            card_types=[CardType.LAND],
            mana_cost=ManaCost(), supertypes=[], subtypes=[],
            power=None, toughness=None, loyalty=None,
            keywords=set(), abilities=[], color_identity=set(),
            produces_mana=["C"], enters_tapped=False,
            oracle_text="{T}: Add {C}.", tags=set(),
        )
        land = CardInstance(
            template=tmpl, owner=controller, controller=controller,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        land._game_state = game
        land.tapped = False
        game.players[controller].battlefield.append(land)
        lands.append(land)
    return lands


def _push_ward_scenario(game, ward_cost=2, warded_controller=0,
                        caster=1):
    """Warded creature on `warded_controller`'s battlefield; a removal
    spell controlled by `caster` targeting it, on top of the stack
    (about to resolve). Returns (warded_creature, removal_item_card)."""
    warded_template = _warded_permanent_template(ward_cost)
    warded = CardInstance(
        template=warded_template, owner=warded_controller,
        controller=warded_controller, instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    warded._game_state = game
    game.players[warded_controller].battlefield.append(warded)

    removal_template = _removal_spell_template()
    removal_card = CardInstance(
        template=removal_template, owner=caster, controller=caster,
        instance_id=game.next_instance_id(), zone="stack",
    )
    removal_card._game_state = game

    removal_item = StackItem(
        item_type=StackItemType.SPELL, source=removal_card,
        controller=caster, targets=[warded.instance_id], effect=None,
        description="Destroy target creature.",
    )
    game.stack.push(removal_item)
    return warded, removal_card


class _AlwaysPayCallbacks(DefaultCallbacks):
    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        return True


class _NeverPayCallbacks(DefaultCallbacks):
    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        return False


class _AssertNotOfferedCallbacks(DefaultCallbacks):
    """Fails the test if the tax is ever offered — used to verify the
    engine-side affordability gate skips the decision entirely."""
    def decide_optional_cost(self, game, player_idx, opt) -> bool:
        raise AssertionError(
            "decide_optional_cost was called despite the caster "
            "having insufficient mana — the affordability gate should "
            "have skipped the offer entirely."
        )


class TestWardResolution:
    def test_spell_resolves_and_kills_warded_creature_when_caster_pays(
            self, card_db):
        """Caster can afford the ward tax and the AI seam agrees to
        pay: the removal spell must resolve normally, destroying the
        warded creature."""
        game = GameState(rng=random.Random(0), callbacks=_AlwaysPayCallbacks())
        _land(game, controller=1, n=3)
        warded, removal_card = _push_ward_scenario(game, ward_cost=2)

        game.resolve_stack()

        assert warded not in game.players[0].battlefield, (
            "warded creature survived even though the caster paid "
            "the ward tax — the spell should have resolved normally"
        )
        assert not any("is countered" in line for line in game.log), (
            f"log incorrectly shows a counter: {game.log}"
        )

    def test_spell_is_countered_when_caster_does_not_pay(self, card_db):
        """Caster can afford the ward tax but chooses not to pay: the
        removal spell is countered and the warded creature survives."""
        game = GameState(rng=random.Random(0), callbacks=_NeverPayCallbacks())
        _land(game, controller=1, n=3)
        warded, removal_card = _push_ward_scenario(game, ward_cost=2)

        game.resolve_stack()

        assert warded in game.players[0].battlefield, (
            "warded creature was destroyed despite the ward tax going "
            "unpaid — the removal spell should have been countered"
        )
        assert removal_card.zone == "graveyard"
        assert removal_card in game.players[1].graveyard
        assert any("is countered" in line and "ward" in line
                   for line in game.log)

    def test_countered_without_offering_when_caster_cannot_afford(
            self, card_db):
        """Caster has NO untapped mana sources: the tax is unpayable,
        so no decision is offered at all (a rules gate, not a
        strategic choice) and the spell is simply countered."""
        game = GameState(rng=random.Random(0),
                         callbacks=_AssertNotOfferedCallbacks())
        # Deliberately no lands for the caster (player 1).
        warded, removal_card = _push_ward_scenario(game, ward_cost=2)

        game.resolve_stack()

        assert warded in game.players[0].battlefield
        assert removal_card.zone == "graveyard"

    def test_no_trigger_when_caster_targets_own_warded_permanent(
            self, card_db):
        """CR 702.21a only triggers vs. a spell/ability an OPPONENT
        controls. A player targeting their OWN warded permanent (e.g.
        a pump spell) must not trigger the tax at all."""
        game = GameState(rng=random.Random(0),
                         callbacks=_AssertNotOfferedCallbacks())
        # Same controller (0) for both the warded creature and the
        # targeting spell.
        warded, removal_card = _push_ward_scenario(
            game, ward_cost=2, warded_controller=0, caster=0)

        game.resolve_stack()

        # No ward offer means no interception at all — the spell
        # proceeds to resolve normally (and, per its own effect,
        # destroys its own creature — irrelevant to this test, which
        # only asserts the ward gate itself never fired). Check for
        # the ward-specific log phrasing, not a bare "ward" substring
        # — the fixture creature's own name ("Warded Creature")
        # otherwise collides with a naive substring check.
        assert not any("'s ward" in line for line in game.log)

    def test_no_ward_when_target_is_not_warded(self, card_db):
        """A plain (non-warded) creature target must resolve through
        completely unaffected by the new ward-check code path."""
        game = GameState(rng=random.Random(0), callbacks=_NeverPayCallbacks())
        warded, removal_card = _push_ward_scenario(game, ward_cost=0)

        game.resolve_stack()

        assert warded not in game.players[0].battlefield
        assert not any("'s ward" in line for line in game.log)

    def test_multiple_targets_only_warded_one_taxed(self, card_db):
        """A spell with two targets, only one of which is warded: an
        unpaid tax counters the WHOLE spell (CR 702.21a counters the
        spell/ability, not just the offending target)."""
        game = GameState(rng=random.Random(0), callbacks=_NeverPayCallbacks())
        _land(game, controller=1, n=1)  # affordable, but AI declines

        warded_template = _warded_permanent_template(ward_cost=2)
        warded = CardInstance(
            template=warded_template, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        warded._game_state = game
        game.players[0].battlefield.append(warded)

        plain_template = CardTemplate(
            name="Test Fixture: Plain Creature",
            card_types=[CardType.CREATURE],
            mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
            power=1, toughness=1, loyalty=None, keywords=set(),
            abilities=[], color_identity=set(), produces_mana=[],
            enters_tapped=False, oracle_text="", tags=set(),
        )
        plain = CardInstance(
            template=plain_template, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        plain._game_state = game
        game.players[0].battlefield.append(plain)

        sweeper_template = CardTemplate(
            name="Test Fixture: Two-Target Removal",
            card_types=[CardType.SORCERY],
            mana_cost=ManaCost(generic=2), supertypes=[], subtypes=[],
            power=None, toughness=None, loyalty=None, keywords=set(),
            abilities=[Ability(ability_type=AbilityType.CAST,
                               description="Destroy target creature and "
                                            "target creature.",
                               targets_required=2)],
            color_identity=set(), produces_mana=[], enters_tapped=False,
            oracle_text="Destroy target creature and target creature.",
            tags=set(),
        )
        sweeper = CardInstance(
            template=sweeper_template, owner=1, controller=1,
            instance_id=game.next_instance_id(), zone="stack",
        )
        sweeper._game_state = game

        item = StackItem(
            item_type=StackItemType.SPELL, source=sweeper, controller=1,
            targets=[warded.instance_id, plain.instance_id], effect=None,
            description="Destroy target creature and target creature.",
        )
        game.stack.push(item)

        game.resolve_stack()

        # Ward unpaid -> the ENTIRE spell is countered, so both
        # targets (warded and plain) survive.
        assert warded in game.players[0].battlefield
        assert plain in game.players[0].battlefield
        assert sweeper.zone == "graveyard"


class TestWardAppliesToAbilitiesNotJustSpells:
    """CR 702.21a triggers on "a spell OR ability" — verify the check
    isn't scoped to StackItemType.SPELL only."""

    def test_activated_ability_targeting_warded_permanent_is_countered(
            self, card_db):
        game = GameState(rng=random.Random(0), callbacks=_NeverPayCallbacks())

        warded_template = _warded_permanent_template(ward_cost=1)
        warded = CardInstance(
            template=warded_template, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        warded._game_state = game
        game.players[0].battlefield.append(warded)

        source_template = CardTemplate(
            name="Test Fixture: Ability Source",
            card_types=[CardType.ARTIFACT], mana_cost=ManaCost(generic=1),
            supertypes=[], subtypes=[], power=None, toughness=None,
            loyalty=None, keywords=set(), abilities=[],
            color_identity=set(), produces_mana=[], enters_tapped=False,
            oracle_text="{1}, {T}: Destroy target creature.", tags=set(),
        )
        source = CardInstance(
            template=source_template, owner=1, controller=1,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        source._game_state = game
        game.players[1].battlefield.append(source)

        effect_calls = []

        def _effect(g, src, ctrl, targets):
            effect_calls.append((src, ctrl, targets))

        item = StackItem(
            item_type=StackItemType.ACTIVATED_ABILITY, source=source,
            controller=1, targets=[warded.instance_id], effect=_effect,
            description="Destroy target creature.",
        )
        game.stack.push(item)

        game.resolve_stack()

        assert not effect_calls, (
            "ability effect executed despite the ward tax going unpaid"
        )
        assert warded in game.players[0].battlefield
        assert any("is countered" in line and "ward" in line
                   for line in game.log)

    def test_activated_ability_resolves_when_tax_is_paid(self, card_db):
        game = GameState(rng=random.Random(0), callbacks=_AlwaysPayCallbacks())
        _land(game, controller=1, n=2)

        warded_template = _warded_permanent_template(ward_cost=1)
        warded = CardInstance(
            template=warded_template, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        warded._game_state = game
        game.players[0].battlefield.append(warded)

        source_template = CardTemplate(
            name="Test Fixture: Ability Source",
            card_types=[CardType.ARTIFACT], mana_cost=ManaCost(generic=1),
            supertypes=[], subtypes=[], power=None, toughness=None,
            loyalty=None, keywords=set(), abilities=[],
            color_identity=set(), produces_mana=[], enters_tapped=False,
            oracle_text="{1}, {T}: Destroy target creature.", tags=set(),
        )
        source = CardInstance(
            template=source_template, owner=1, controller=1,
            instance_id=game.next_instance_id(), zone="battlefield",
        )
        source._game_state = game
        game.players[1].battlefield.append(source)

        effect_calls = []

        def _effect(g, src, ctrl, targets):
            effect_calls.append((src, ctrl, targets))

        item = StackItem(
            item_type=StackItemType.ACTIVATED_ABILITY, source=source,
            controller=1, targets=[warded.instance_id], effect=_effect,
            description="Destroy target creature.",
        )
        game.stack.push(item)

        game.resolve_stack()

        assert len(effect_calls) == 1, (
            "ability effect should have executed exactly once after "
            "the ward tax was paid"
        )


# ─── AI projection ──────────────────────────────────────────────────


class TestProjectWardTaxPayment:
    """`project_ward_tax_payment` mirrors `project_counter_tax_payment`
    (both reduce to "does my own stack item survive, minus a mana
    tax")."""

    def test_pay_branch_charges_the_tax_amount(self, card_db):
        from ai.ev_evaluator import (
            EVSnapshot, project_counter_tax_payment, project_ward_tax_payment,
        )
        removal = CardInstance(
            template=_removal_spell_template(), owner=1, controller=1,
            instance_id=999901, zone="stack",
        )
        snap = EVSnapshot(
            my_life=20, opp_life=20, my_power=0, opp_power=0,
            my_toughness=0, opp_toughness=0, my_creature_count=0,
            opp_creature_count=0, my_hand_size=3, opp_hand_size=3,
            my_mana=5, opp_mana=5, my_total_lands=5, opp_total_lands=5,
            turn_number=4,
        )
        ward_projected = project_ward_tax_payment(removal, snap, 2)
        counter_projected = project_counter_tax_payment(removal, snap, 2)

        # Same math, same result — see project_ward_tax_payment's
        # docstring for why this equivalence is intentional, not
        # accidental duplication.
        assert ward_projected.my_mana == counter_projected.my_mana
        assert ward_projected.my_mana <= snap.my_mana
