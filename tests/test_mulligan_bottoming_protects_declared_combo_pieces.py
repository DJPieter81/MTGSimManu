"""Mulligan bottoming must protect gameplan-declared combo pieces.

Bug: docs/diagnostics/2026-07-05_storm_dimir_canonical_gap.md
================================================================
Reproduced at Bo3 seed 50000, Game 2 (seed 50001), Ruby Storm vs
Dimir Midrange::

    New hand (1 lands, 6 spells): ['Pyretic Ritual', 'Reckless
      Impulse', 'Mountain', 'Manamorphose', 'Ral, Monsoon Mage //
      Ral, Leyline Prodigy', 'Reckless Impulse', 'Pyretic Ritual']
    P1 mulligans to 5, bottoms: ['Ral, Monsoon Mage // Ral,
      Leyline Prodigy', 'Pyretic Ritual']

The deck bottoms its own engine (Ral) and fuel (a ritual) while
duplicate copies of interchangeable pieces sit in the same hand.

Class-wide mechanic
-------------------
A gameplan *declares* which cards its plan cannot function without:
``mulligan_keys``, ``mulligan_combo_sets``, ``mulligan_combo_paths``
role buckets, ``critical_pieces``, and ``always_early``.  The
bottoming policy (``MulliganDecider.choose_cards_to_bottom``) must
consult that same declared data: while non-declared alternatives
(excess lands, filler) exist, a declared piece is never bottomed;
among declared pieces, duplicate copies of the same name are
bottomed before a distinct singleton piece.

Six registered decks declare this data today (Ruby Storm, Goryo's
Vengeance, Living End, Amulet Titan, Pinnacle Affinity, Azorius
Control), so the protection is a class-wide mechanism, not a Storm
patch.  Card names below are test fixtures drawn from the Ruby
Storm gameplan; the production code reads only gameplan fields.
"""
from __future__ import annotations

from ai.gameplan import create_goal_engine
from ai.mulligan import MulliganDecider
from ai.strategy_profile import ArchetypeStrategy
from engine.cards import CardInstance


def _mk(card_db, name, iid):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return CardInstance(
        template=tmpl, owner=0, controller=0,
        instance_id=iid, zone="hand",
    )


def _storm_decider():
    ge = create_goal_engine("Ruby Storm")
    return MulliganDecider(ArchetypeStrategy.COMBO, ge), ge


def _declared_pieces(gp) -> set:
    """The union of every card name the gameplan declares as part of
    its plan — the same union the production code must consult."""
    names = (set(gp.mulligan_keys)
             | set(gp.critical_pieces)
             | set(gp.always_early))
    for combo_set in gp.mulligan_combo_sets:
        names |= set(combo_set)
    for path in gp.mulligan_combo_paths:
        for bucket in path.values():
            names |= set(bucket)
    return names


class TestBottomingProtectsDeclaredComboPieces:

    def test_bottoming_prefers_excess_lands_over_declared_pieces(
            self, card_db):
        """Hand: 3 lands (min_lands=1 for the gameplan → two are
        surplus to the floor) + 4 declared pieces.  Bottoming 2 must
        select from the surplus lands, never a declared piece,
        because non-declared alternatives exist and the land floor
        stays satisfied.

        Ral is a member of ``mulligan_combo_sets[0]`` and the
        ``engines`` role but NOT of ``mulligan_keys`` — the exact
        signal gap that made it score below a basic land.
        """
        hand = [
            _mk(card_db, "Scalding Tarn", 1),
            _mk(card_db, "Mountain", 2),
            _mk(card_db, "Mountain", 3),
            _mk(card_db, "Ral, Monsoon Mage // Ral, Leyline Prodigy", 4),
            _mk(card_db, "Pyretic Ritual", 5),
            _mk(card_db, "Wish", 6),
            _mk(card_db, "Grapeshot", 7),
        ]
        dec, ge = _storm_decider()
        declared = _declared_pieces(ge.gameplan)
        # Sanity: every non-land in this hand is a declared piece.
        assert all(c.name in declared for c in hand
                   if not c.template.is_land)
        bottom = dec.choose_cards_to_bottom(hand[:], 2)
        bottomed_pieces = [c.name for c in bottom if c.name in declared]
        assert not bottomed_pieces, (
            f"Bottoming discarded declared combo piece(s) "
            f"{bottomed_pieces} while {sum(1 for c in hand if c.template.is_land)} "
            f"lands (gameplan max_lands="
            f"{ge.gameplan.mulligan_max_lands}) were available to "
            f"bottom instead."
        )

    def test_bottoming_prefers_duplicate_copies_over_singleton_piece(
            self, card_db):
        """The seed-50001 replay hand verbatim: 1 land + 6 declared
        pieces, two of which are duplicated.  Bottoming 2 (keeping
        the land per min_lands=1) must discard duplicate copies, not
        the hand's singleton engine-role piece.
        """
        hand = [
            _mk(card_db, "Pyretic Ritual", 1),
            _mk(card_db, "Reckless Impulse", 2),
            _mk(card_db, "Mountain", 3),
            _mk(card_db, "Manamorphose", 4),
            _mk(card_db, "Ral, Monsoon Mage // Ral, Leyline Prodigy", 5),
            _mk(card_db, "Reckless Impulse", 6),
            _mk(card_db, "Pyretic Ritual", 7),
        ]
        dec, ge = _storm_decider()
        bottom = dec.choose_cards_to_bottom(hand[:], 2)
        bottom_names = [c.name for c in bottom]
        # Names appearing more than once in hand — the duplicates.
        from collections import Counter
        copies = Counter(c.name for c in hand)
        singleton_pieces_bottomed = [
            n for n in bottom_names
            if copies[n] == 1 and n in _declared_pieces(ge.gameplan)
        ]
        assert not singleton_pieces_bottomed, (
            f"Bottoming discarded singleton declared piece(s) "
            f"{singleton_pieces_bottomed} while duplicate copies "
            f"({[n for n, k in copies.items() if k > 1]}) were "
            f"available to bottom instead.  Bottom: {bottom_names}."
        )

    def test_bottoming_all_declared_pieces_still_functions(
            self, card_db):
        """Negative case: when the hand is ALL distinct declared
        pieces and something must go, bottoming still returns the
        requested count (no crash, no empty selection) — the
        protection is a relative preference, not a hard refusal.
        The pieces given up are the lowest-ranked by the same
        role/key/cmc keep-score order used for every other hand.
        """
        hand = [
            _mk(card_db, "Ral, Monsoon Mage // Ral, Leyline Prodigy", 1),
            _mk(card_db, "Ruby Medallion", 2),
            _mk(card_db, "Pyretic Ritual", 3),
            _mk(card_db, "Desperate Ritual", 4),
            _mk(card_db, "Manamorphose", 5),
            _mk(card_db, "Past in Flames", 6),
            _mk(card_db, "Grapeshot", 7),
        ]
        dec, ge = _storm_decider()
        declared = _declared_pieces(ge.gameplan)
        assert all(c.name in declared for c in hand)
        bottom = dec.choose_cards_to_bottom(hand[:], 2)
        assert len(bottom) == 2, (
            f"Bottoming an all-pieces hand must still select exactly "
            f"the requested count; got {[c.name for c in bottom]}"
        )
        assert all(c in hand for c in bottom)
