"""A scarce one-shot payoff is not committed below the clock-derived
lethal threshold while a larger line is still assemblable.

# Mechanic the tests name (engine rule, not a card, not a deck)

The decision kernel must distinguish "a decisive line is executable
THIS turn" from "keep developing toward a bigger one" before it spends
a resource it cannot get back — a one-shot burn finisher, a tutor that
fetches one, any closer whose effect ends when it resolves. Committing
such a payoff for damage far below the opponent's life (the clock-
derived lethal threshold, CR 104.3a) forfeits the larger line a future
turn would assemble. The rule, phrased without naming a card:

  * Commit the payoff iff its projected damage reaches lethal
    (>= opp_life), OR holding cannot strictly dominate — the line
    cannot grow (no more fuel to draw) or we will not survive to take
    the turn that would grow it.
  * Otherwise HOLD.

This lives in the shared decision subsystem
(`ai.clock.should_commit_scarce_payoff`) so it applies to every deck
facing a commit-vs-build choice, not a storm code path.

# Why this is an engine-level class fix, not a Storm patch

`should_commit_scarce_payoff` is a pure clock primitive: its inputs are
lethal threshold, line-growth headroom, whether this turn's investment
is already sunk, and survival. Any archetype whose scoring routes a
scarce closer through it inherits the rule. The failure it fixes was
the TUTOR-as-finisher-access branch of `ai/combo_calc.py` firing the
payoff whenever no fuel sat in hand this turn — the sibling storm-
keyword branch had the storm-zero hold, the tutor branch never did, so
a tutor-led no-fuel hand cashed the payoff for trivial value (Storm vs
Azorius Control (WST) s55501 T10: a tutor -> burn finisher for 2 into
19 while the library was full of fuel). Storm cards below are only
fixture carriers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

from ai.clock import should_commit_scarce_payoff, scarce_payoff_commit_ev
from ai.combo_calc import ComboAssessment, card_combo_modifier
from ai.ev_evaluator import EVSnapshot


# ─── Part 1: the pure decision-kernel primitive (deck-agnostic) ────
#
# No cards, no decks — just the four clock-derived facts. These pin the
# gate as a general capability that any commit-vs-build decision can
# call, proving it is not combo-scoped.

class TestCommitGatePrimitive:
    def test_scarce_payoff_held_below_lethal_when_line_can_grow(self):
        """Sub-lethal, we survive, nothing sunk yet, and the line can
        still grow → HOLD (do not commit)."""
        assert should_commit_scarce_payoff(
            fire_now_damage=2, opp_life=19,
            line_can_grow=True, chain_uncommitted=True,
            survives_to_next_turn=True,
        ) is False

    def test_scarce_payoff_committed_at_clock_derived_lethal(self):
        """Projected damage reaches the lethal threshold → COMMIT,
        regardless of growth headroom."""
        assert should_commit_scarce_payoff(
            fire_now_damage=19, opp_life=19,
            line_can_grow=True, chain_uncommitted=True,
            survives_to_next_turn=True,
        ) is True

    def test_scarce_payoff_committed_when_line_cannot_grow(self):
        """No future draw can grow the line (empty/fuel-less library) →
        now is as big as it gets → COMMIT."""
        assert should_commit_scarce_payoff(
            fire_now_damage=2, opp_life=19,
            line_can_grow=False, chain_uncommitted=True,
            survives_to_next_turn=True,
        ) is True

    def test_scarce_payoff_committed_when_no_surviving_turn(self):
        """We die before the turn that would grow the line → now is the
        only window → COMMIT."""
        assert should_commit_scarce_payoff(
            fire_now_damage=2, opp_life=19,
            line_can_grow=True, chain_uncommitted=True,
            survives_to_next_turn=False,
        ) is True

    def test_committed_chain_cashes_in_rather_than_holds(self):
        """Once this turn's investment is sunk (chain_uncommitted
        False), a per-turn resource resets next turn, so holding
        forfeits it → COMMIT ("cash in what you built"). This is the
        boundary that keeps mid-chain fires — and any archetype whose
        line does not carry over between turns — behaving correctly."""
        assert should_commit_scarce_payoff(
            fire_now_damage=5, opp_life=15,
            line_can_grow=True, chain_uncommitted=False,
            survives_to_next_turn=True,
        ) is True


# ─── Part 1b: the decision is an EV comparison, not a flat penalty ──
#
# The commit/hold choice reduces to EV(fire now) − EV(develop a larger
# line), both in kill-fraction × combo_value units. These pin that the
# score IS that EV difference — its sign is the decision and its
# magnitude is the EV at stake — so there is no arbitrary hold penalty.

class TestBuildVsComboEV:
    CV = 80.0  # a combo whose completion is worth a kill

    def test_ev_now_is_capped_at_a_full_kill(self):
        """A payoff that reaches lethal is worth exactly one kill —
        overshoot damage adds nothing (min-cap at combo_value)."""
        # reach lethal (fire == opp), develop unreachable → EV = full kill.
        delta = scarce_payoff_commit_ev(20, opp_life=20, combo_value=self.CV,
                                        develop_reach_probability=0.0)
        assert delta == self.CV
        # overshoot (fire >> opp) is not worth more than a kill.
        over = scarce_payoff_commit_ev(40, opp_life=20, combo_value=self.CV,
                                       develop_reach_probability=0.0)
        assert over == self.CV

    def test_developed_line_outscores_a_trivial_fire(self):
        """Sub-lethal fire (2 into 19) vs a reachable developed combo:
        EV(develop) = 1×CV dominates EV(now) = (2/19)×CV, so the EV
        difference is negative (hold) and its size is the real gap."""
        delta = scarce_payoff_commit_ev(2, opp_life=19, combo_value=self.CV,
                                        develop_reach_probability=1.0)
        assert delta < 0
        assert delta == (2 / 19) * self.CV - self.CV

    def test_unreachable_develop_leaves_only_fire_value(self):
        """When the developed line cannot be reached (probability 0) the
        EV difference is exactly EV(now) — non-negative, so fire."""
        delta = scarce_payoff_commit_ev(2, opp_life=19, combo_value=self.CV,
                                        develop_reach_probability=0.0)
        assert delta == (2 / 19) * self.CV
        assert delta >= 0

    def test_develop_reach_probability_scales_the_hold_incentive(self):
        """The decision is graded, not a cliff: as the probability of
        reaching the developed line falls, EV(develop) shrinks, so the
        incentive to hold shrinks monotonically toward firing. This is
        the hook a BHI disruption discount plugs into."""
        certain = scarce_payoff_commit_ev(2, opp_life=19, combo_value=self.CV,
                                          develop_reach_probability=1.0)
        halved = scarce_payoff_commit_ev(2, opp_life=19, combo_value=self.CV,
                                         develop_reach_probability=0.5)
        assert halved > certain            # less sure → less negative
        assert halved == (2 / 19) * self.CV - 0.5 * self.CV


# ─── Part 2: integration through the tutor-as-finisher-access branch ─
#
# Mock helpers mirror tests/test_storm_wish_fires_when_fuel_depleted.py.

def _make_snap(opp_life=19, my_mana=5, storm_count=0, my_life=15,
               opp_power=4, **kwargs):
    defaults = dict(
        my_life=my_life, opp_life=opp_life, my_power=0,
        opp_power=opp_power, my_toughness=0, opp_toughness=0,
        my_creature_count=0, opp_creature_count=1, my_hand_size=2,
        opp_hand_size=5, my_mana=my_mana, opp_mana=5, my_total_lands=5,
        opp_total_lands=5, turn_number=10, storm_count=storm_count,
        my_gy_creatures=0, my_energy=0, my_evasion_power=0,
        my_lifelink_power=0, opp_evasion_power=0, cards_drawn_this_turn=0,
    )
    defaults.update(kwargs)
    return EVSnapshot(**defaults)


@dataclass
class MockTemplate:
    name: str = "Test"
    cmc: int = 2
    is_instant: bool = False
    is_sorcery: bool = True
    is_land: bool = False
    is_creature: bool = False
    oracle_text: str = ""
    tags: Set[str] = field(default_factory=set)
    keywords: Set[str] = field(default_factory=set)
    color_identity: Set = field(default_factory=set)
    has_flash: bool = False
    ritual_mana: Optional[tuple] = None
    domain_reduction: int = 0
    card_types: Set = field(default_factory=set)
    power: Optional[int] = None
    toughness: Optional[int] = None
    x_cost_data: Optional[dict] = None


@dataclass
class MockCard:
    name: str = "Test"
    instance_id: int = 0
    template: MockTemplate = field(default_factory=MockTemplate)
    zone: str = "hand"
    power: Optional[int] = None
    toughness: Optional[int] = None
    other_counters: dict = field(default_factory=dict)


def _storm_assessment(combo_value=80.0):
    return ComboAssessment(
        resource_zone="storm", is_ready=False,
        payoff_value=0.5, combo_value=combo_value, risk_discount=0.7,
        has_payoff=True, has_enabler=False,
        payoff_names={"Grapeshot", "Empty the Warrens"},
        _role_cache={"Wish": "fillers"},
    )


def _wish(instance_id=1):
    return MockCard(
        name="Wish", instance_id=instance_id,
        template=MockTemplate(name="Wish", cmc=3, is_sorcery=True,
                              tags={'tutor', 'combo'}),
    )


def _creature_filler(instance_id=3):
    """A creature that is NOT chain fuel (no ritual/cantrip/draw tag):
    it consumes mana without extending the chain."""
    return MockCard(
        name="Ral, Monsoon Mage // Ral, Leyline Prodigy",
        instance_id=instance_id,
        template=MockTemplate(
            name="Ral, Monsoon Mage // Ral, Leyline Prodigy",
            cmc=2, is_creature=True, is_sorcery=False,
            tags={'cost_reducer', 'creature', 'mana_source'}),
    )


def _library_fuel(n=30):
    from typing import List
    lib: List[MockCard] = []
    for i in range(n):
        lib.append(MockCard(
            name="Pyretic Ritual", instance_id=1000 + i,
            template=MockTemplate(name="Pyretic Ritual", cmc=2,
                                  tags={'ritual', 'mana_source'}),
            zone="library"))
    return lib


def _sb_storm_finisher():
    from engine.cards import Keyword as Kw
    return MockCard(
        name="Grapeshot", instance_id=999,
        template=MockTemplate(name="Grapeshot", cmc=2, tags={'combo'},
                              keywords={Kw.STORM}),
        zone="sideboard",
    )


def _me(hand, library, sideboard, storm):
    return type('', (), {
        'spells_cast_this_turn': storm,
        'hand': hand,
        'library': library,
        'graveyard': [],
        'battlefield': [],
        'sideboard': sideboard,
    })()


def _game(me):
    return type('', (), {'players': [me, me],
                         'can_cast': lambda *a: True})()


class TestPayoffNotCommittedBelowThreshold:
    def test_payoff_not_committed_below_clock_derived_lethal_threshold(self):
        """The T10 leak. Tutor-led hand: [Wish, creature filler],
        storm=0, opp at 19 (far above the tutor's `storm + 2 = 2`
        reach), library full of fuel, we survive. The tutor is a scarce
        finisher-access closer — it must be HELD, not fired for 2."""
        wish = _wish(1)
        me = _me(hand=[wish, _creature_filler(3)],
                 library=_library_fuel(30),
                 sideboard=[_sb_storm_finisher()], storm=0)
        snap = _make_snap(opp_life=19, my_mana=5, storm_count=0,
                          my_life=15, opp_power=4)
        mod = card_combo_modifier(wish, _storm_assessment(), snap, me,
                                  _game(me), 0)
        assert mod < 0, (
            f"tutor scored {mod:.2f} — a scarce finisher-access closer "
            f"fired for `storm + 2 = 2` damage into 19 life while the "
            f"library could still grow the chain and we survive. Below "
            f"the clock-derived lethal threshold with a larger line "
            f"reachable, the payoff must be held."
        )

    def test_tutor_committed_when_reach_is_lethal(self):
        """Regression: when `storm + 2 >= opp_life` the tutor's reach is
        lethal and it must FIRE regardless of library growth."""
        wish = _wish(1)
        me = _me(hand=[wish, _creature_filler(3)],
                 library=_library_fuel(30),
                 sideboard=[_sb_storm_finisher()], storm=0)
        # opp at 2 → storm + 2 = 2 >= 2 → lethal reach.
        snap = _make_snap(opp_life=2, my_mana=5, storm_count=0,
                          my_life=15, opp_power=4)
        mod = card_combo_modifier(wish, _storm_assessment(), snap, me,
                                  _game(me), 0)
        assert mod > 0, (
            f"tutor scored {mod:.2f} at lethal reach (opp_life=2, "
            f"storm=0) — the commit gate must defer to lethal and fire."
        )

    def test_tutor_committed_when_library_cannot_grow_line(self):
        """Regression: with no chain fuel left to draw, holding is
        pointless — fire the tutor for whatever it reaches."""
        wish = _wish(1)
        me = _me(hand=[wish, _creature_filler(3)],
                 library=[],  # nothing left to grow the chain
                 sideboard=[_sb_storm_finisher()], storm=0)
        snap = _make_snap(opp_life=19, my_mana=5, storm_count=0,
                          my_life=15, opp_power=4)
        mod = card_combo_modifier(wish, _storm_assessment(), snap, me,
                                  _game(me), 0)
        assert mod > 0, (
            f"tutor scored {mod:.2f} — with an empty library no future "
            f"draw can grow the chain, so the closer must fire now "
            f"rather than be held forever."
        )

    def test_tutor_committed_when_we_die_before_next_turn(self):
        """Regression: if we will be dead next turn, now is the only
        window — commit the tutor even below the lethal threshold."""
        wish = _wish(1)
        me = _me(hand=[wish, _creature_filler(3)],
                 library=_library_fuel(30),
                 sideboard=[_sb_storm_finisher()], storm=0)
        # opp_power (18) >= my_life (3) → am_dead_next True.
        snap = _make_snap(opp_life=19, my_mana=5, storm_count=0,
                          my_life=3, opp_power=18)
        assert snap.am_dead_next
        mod = card_combo_modifier(wish, _storm_assessment(), snap, me,
                                  _game(me), 0)
        assert mod > 0, (
            f"tutor scored {mod:.2f} while dying next turn — the commit "
            f"gate must fire when no surviving turn exists to grow the "
            f"line."
        )
