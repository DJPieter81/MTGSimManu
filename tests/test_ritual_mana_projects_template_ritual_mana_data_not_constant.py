"""Ritual mana projection must read parsed `template.ritual_mana[1]`,
not a flat constant + per-card patch.

# Mechanic the test names

A ritual is a spell that costs N mana and produces M ≥ N mana on
resolution. The actual M is parsed at card-load time by
`engine/oracle_parser.py::parse_ritual_mana` from oracle text and
stored on the template as a `(color, amount)` tuple — e.g. Pyretic
Ritual: ("R", 3); Manamorphose: ("any", 2). The EV projection in
`ai/ev_evaluator.py::_project_spell` must credit `projected.my_mana`
with the *parsed* amount, not a flat constant. Anything else is the
oracle-pattern projection blindspot named in
`docs/design/2026-05-10_oracle_pattern_projection_blindspot_audit.md`
— a flat estimate + per-card override (e.g. `if 'cantrip' in tags:
projected.my_mana -= 1` for Manamorphose) is exactly the per-card
override the abstraction contract bans.

# Class size

~85 cards in the printed Modern pool have `ritual_mana` data parsed
from oracle text (see the per-amount distribution: amount=2: 49
cards, amount=3: 28 cards, amount=4: 4 cards, amount=5: 2 cards,
amount=7: 1 card, amount=8: 1 card). A flat constant of 3 mis-
projects every card whose actual production is not 3 — at minimum,
49 cards with amount=2 and 8 cards with amount≥4 are wrong.

# Generalisation

Storm uses the 2-mana variants (Manamorphose) and 3-mana variants
(Pyretic / Desperate). Goryo's Vengeance and Living End cycling
chains touch the 2-mana variants when fed a ritual of the right
colour. Any future combo deck that adds a non-3 ritual benefits.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import _project_spell, snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


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
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _find_ritual_with_amount(card_db, target_amount, *,
                             require_cantrip=False,
                             exclude_cantrip=False):
    """Pick any printed ritual whose oracle-parsed `ritual_mana[1]`
    matches `target_amount`. Returns the card name or skips the test.

    No card naming: the test asserts *the projection follows the
    parsed value*, regardless of which card supplies it.
    """
    for name, t in card_db.cards.items():
        tags = getattr(t, 'tags', set())
        if 'ritual' not in tags:
            continue
        rm = getattr(t, 'ritual_mana', None)
        if rm is None or rm[1] != target_amount:
            continue
        if require_cantrip and 'cantrip' not in tags:
            continue
        if exclude_cantrip and 'cantrip' in tags:
            continue
        # Must be castable as a spell (instant/sorcery) — many
        # battlefield permanents tagged ritual produce mana via an
        # activated ability and are not on the projection path.
        if t.is_creature or t.is_land:
            continue
        if not (t.is_instant or t.is_sorcery):
            continue
        return name
    pytest.skip(
        f"no ritual found with amount={target_amount}, "
        f"require_cantrip={require_cantrip}, "
        f"exclude_cantrip={exclude_cantrip}"
    )


def _build_game_with_ritual_in_hand(card_db, ritual_name):
    game = GameState(rng=random.Random(0))
    # Plenty of untapped lands so the cost-subtraction is not
    # floored at 0 — the test asserts the *gross production added*
    # equals the parsed amount, which only holds when (snap.my_mana
    # - cmc) ≥ 0 before the ritual block runs. Eight Mountains is
    # safely above the largest ritual cmc in the printed pool (~7).
    for _ in range(8):
        _add(game, card_db, "Mountain", controller=0,
             zone="battlefield")
    ritual = _add(game, card_db, ritual_name, controller=0, zone="hand")
    for _ in range(10):
        _add(game, card_db, "Mountain", controller=0, zone="library")
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Dimir Midrange"
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1
    game.players[0].life = 20
    game.players[1].life = 20
    return game, ritual


class TestRitualManaProjectsParsedValue:
    """`_project_spell` must add `template.ritual_mana[1]` to
    `projected.my_mana`, not a flat constant. The cost has already
    been subtracted by `_project_spell` before this delta is
    applied, so the gross production equals the parsed amount and
    the *net* delta on `my_mana` (relative to the pre-cast snapshot)
    equals `ritual_mana[1] - cmc`.
    """

    def test_high_production_ritual_projects_parsed_amount_not_flat_three(
            self, card_db):
        """A ritual whose oracle parses to amount > 3 (Pyretic /
        Desperate are amount=3, but Geosurge-class produces 7, and
        the printed pool also has amount=4 / 5 / 8 rituals) must
        project the *parsed* gross production. The flat constant
        `RITUAL_MANA_PRODUCED = 3` mis-projects every above-3
        ritual.

        Bug-discriminator: with the flat-3 constant, projected
        my_mana delta = 3 - cmc; with the parsed-value fix, delta
        = parsed - cmc. The two diverge whenever parsed ≠ 3.
        """
        # Search for any non-cantrip ritual whose ritual_mana[1] > 3.
        # Geosurge (7), Soulbright Flamekin (8), Vessel of Volatility
        # (5), etc. all qualify — pick the first one available.
        name = None
        for amt in (4, 5, 7, 8):
            try:
                name = _find_ritual_with_amount(
                    card_db, target_amount=amt, exclude_cantrip=True)
                break
            except pytest.skip.Exception:
                continue
        if name is None:
            pytest.skip("no ritual with parsed amount > 3 in DB")

        game, ritual = _build_game_with_ritual_in_hand(card_db, name)
        snap = snapshot_from_game(game, 0)
        projected = _project_spell(ritual, snap, game=game, player_idx=0)

        parsed = ritual.template.ritual_mana[1]
        cmc = ritual.template.cmc or 0
        # `_project_spell` subtracts cmc up front, then the ritual
        # block adds back the gross production. Net delta on
        # my_mana from pre-cast snap is (parsed - cmc).
        expected_delta = parsed - cmc
        actual_delta = projected.my_mana - snap.my_mana
        assert actual_delta == expected_delta, (
            f"Ritual {name!r} (parsed amount={parsed}, cmc={cmc}) "
            f"projected my_mana delta={actual_delta}, expected "
            f"{expected_delta}. The projection must read "
            f"`template.ritual_mana[1]`, not a flat constant. "
            f"This is the oracle-pattern projection blindspot named "
            f"in docs/design/2026-05-10_oracle_pattern_projection_"
            f"blindspot_audit.md."
        )

    def test_low_production_noncantrip_ritual_projects_parsed_amount_not_flat_three(
            self, card_db):
        """A ritual whose oracle parses to amount=2 and is NOT
        cantrip-tagged (e.g. Seismic Spike-class, parsed=2 with no
        draw rider) must project gross production of 2, not 3. The
        flat-3 constant inflates this projection.

        Bug-discriminator: pre-fix the flat-3 + no-cantrip-patch
        path projects +3 mana gross; post-fix it projects +2.
        """
        name = _find_ritual_with_amount(
            card_db, target_amount=2, exclude_cantrip=True)
        game, ritual = _build_game_with_ritual_in_hand(card_db, name)
        snap = snapshot_from_game(game, 0)
        projected = _project_spell(ritual, snap, game=game, player_idx=0)

        parsed = ritual.template.ritual_mana[1]
        cmc = ritual.template.cmc or 0
        expected_delta = parsed - cmc  # 2 - cmc
        actual_delta = projected.my_mana - snap.my_mana
        assert actual_delta == expected_delta, (
            f"Ritual {name!r} (parsed amount={parsed}, cmc={cmc}, "
            f"non-cantrip) projected my_mana delta={actual_delta}, "
            f"expected {expected_delta}. The flat-3 constant "
            f"over-credited gross production by 1 — exactly the "
            f"under-/over-projection class the audit names. The "
            f"principled fix reads `template.ritual_mana[1]`."
        )

    def test_cantrip_ritual_projects_parsed_amount_without_perpatchcard_minus_one(
            self, card_db):
        """A 2-CMC cantrip-ritual whose oracle parses to amount=2
        (e.g. Manamorphose-class) must project gross production of
        2 — net mana delta = 2 - 2 = 0. The cantrip tag does NOT
        change the mana projection (it's the card-draw block's
        concern), so the historical patch
            `if 'cantrip' in tags: projected.my_mana -= 1`
        was a per-card override masking the missing parsed-value
        read. Removing it must NOT cause this case to project
        +1 mana from the flat-3 constant.

        Note: the pre-fix code (`flat 3 - cantrip 1 = 2`) and the
        post-fix code (`parsed 2`) coincide on this case for
        amount=2 cantrips — so this test is a *regression anchor*
        that holds across the fix, complementing the
        bug-discriminator tests above.
        """
        name = _find_ritual_with_amount(
            card_db, target_amount=2, require_cantrip=True)
        game, ritual = _build_game_with_ritual_in_hand(card_db, name)
        snap = snapshot_from_game(game, 0)
        projected = _project_spell(ritual, snap, game=game, player_idx=0)

        parsed = ritual.template.ritual_mana[1]
        cmc = ritual.template.cmc or 0
        expected_delta = parsed - cmc  # 2 - 2 = 0
        actual_delta = projected.my_mana - snap.my_mana
        assert actual_delta == expected_delta, (
            f"Ritual {name!r} (parsed amount={parsed}, cmc={cmc}, "
            f"cantrip-tagged) projected my_mana delta={actual_delta}, "
            f"expected {expected_delta}. The flat-3 constant + "
            f"cantrip-1 patch is the per-card override the "
            f"abstraction contract bans; the principled fix reads "
            f"`template.ritual_mana[1]`."
        )

    def test_baseline_ritual_projects_template_value_not_flat_three(
            self, card_db):
        """Conditional / threshold rituals (Cabal Ritual: 3B base /
        5B with threshold; not in the Modern DB so we surrogate by
        any non-3 ritual whose ritual_mana is parsed) must at
        minimum project the *baseline* (parsed) amount, not a flat
        3. Use any ritual whose parsed amount is ≠ 3 to anchor that
        the projection follows the parsed value, not the historical
        constant.
        """
        # Try amount=2 first (most plentiful); fall back to amount=4.
        name = None
        for amt in (2, 4, 5):
            for n, t in card_db.cards.items():
                tags = getattr(t, 'tags', set())
                if 'ritual' not in tags:
                    continue
                if t.is_creature or t.is_land:
                    continue
                if not (t.is_instant or t.is_sorcery):
                    continue
                rm = getattr(t, 'ritual_mana', None)
                if rm is None or rm[1] != amt:
                    continue
                name = n
                break
            if name:
                break
        if name is None:
            pytest.skip("no non-3 ritual found in DB")

        game, ritual = _build_game_with_ritual_in_hand(card_db, name)
        snap = snapshot_from_game(game, 0)
        projected = _project_spell(ritual, snap, game=game, player_idx=0)

        parsed = ritual.template.ritual_mana[1]
        cmc = ritual.template.cmc or 0
        expected_delta = parsed - cmc
        actual_delta = projected.my_mana - snap.my_mana
        assert actual_delta == expected_delta, (
            f"Non-3 ritual {name!r} (parsed amount={parsed}, "
            f"cmc={cmc}) projected my_mana delta={actual_delta}, "
            f"expected {expected_delta}. The projection ignored "
            f"`template.ritual_mana[1]` and substituted the flat "
            f"constant — exactly the blindspot the audit names."
        )
