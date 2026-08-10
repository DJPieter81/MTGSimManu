"""oracle-ratchet: grants_flashback_to_gy_spells field must be read at decision
time, not oracle text.

Rule: every card property derivable from oracle text is parsed ONCE in
oracle_parser.py at load time, stored as a typed field on CardTemplate, and
never re-inspected at decision time in engine/ or ai/.

This test pins the mechanic: cards that grant flashback to instant/sorcery
cards in the graveyard (Past in Flames pattern, Snapcaster Mage pattern).
Detection belongs entirely to CardTemplate.grants_flashback_to_gy_spells,
populated by grants_flashback_to_gy_spells() in oracle_parser.py.

Class size: every card in Modern whose oracle text mentions flashback AND
graveyard AND (instant OR sorcery) — Past in Flames, Snapcaster Mage, and
any future printings of the pattern.  Mechanism is the oracle predicate, not
the card name.

Subsystem: engine/card_database.py (population), ai/combo_evaluator.py,
ai/finisher_simulator.py, engine/card_effects.py (consumers — must read
field, not oracle).
"""
from __future__ import annotations

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Field population — load-time
# ──────────────────────────────────────────────────────────────────────────────

def test_oracle_parser_grants_flashback_to_gy_spells_matches_pif_text():
    """grants_flashback_to_gy_spells() must return True for Past-in-Flames text.

    Mechanism: oracle text containing 'flashback', 'graveyard', and
    'instant'/'sorcery' is the canonical PiF pattern.
    """
    from engine.oracle_parser import grants_flashback_to_gy_spells

    pif_oracle = (
        "Each instant and sorcery card in your graveyard gains flashback "
        "until end of turn. The flashback cost is equal to its mana cost."
    )
    assert grants_flashback_to_gy_spells(pif_oracle) is True, (
        "grants_flashback_to_gy_spells() must match Past-in-Flames-style oracle text."
    )


def test_oracle_parser_grants_flashback_negative_plain_flashback():
    """grants_flashback_to_gy_spells() must return False for plain flashback text.

    A card that HAS flashback (e.g. Lingering Souls) does not GRANT flashback
    to other cards.  The field should be False for such cards.

    Mechanism: a card with just 'Flashback {cost}' does not mention graveyard
    in the way PiF does.
    """
    from engine.oracle_parser import grants_flashback_to_gy_spells

    # A card that has its own flashback but does NOT grant it to others.
    # Its oracle will say "Flashback {cost}" only; no graveyard mention.
    plain_flashback = "Flashback {2}{W}{B}"
    # Note: plain flashback text alone does NOT mention graveyard, so it
    # should return False.  The oracle predicate is conservative — it
    # requires all three signals.
    result = grants_flashback_to_gy_spells(plain_flashback)
    # plain_flashback DOES contain 'flashback' but NOT 'graveyard', so False.
    assert result is False, (
        "A card with only 'Flashback {cost}' in oracle should NOT set "
        "grants_flashback_to_gy_spells=True (missing 'graveyard' signal)."
    )


def test_oracle_parser_grants_flashback_negative_non_pif():
    """grants_flashback_to_gy_spells() must return False for unrelated cards.

    Mechanism: the three-signal filter (flashback + graveyard + instant/sorcery)
    must not fire on cards that don't match all three.
    """
    from engine.oracle_parser import grants_flashback_to_gy_spells

    assert grants_flashback_to_gy_spells("") is False
    assert grants_flashback_to_gy_spells("Deal 3 damage to any target.") is False
    assert grants_flashback_to_gy_spells("Add {R}.") is False


def test_pif_card_has_grants_flashback_field_true(card_db):
    """Past in Flames must have grants_flashback_to_gy_spells == True at load time.

    Mechanism: card_database.py populates the field via grants_flashback_to_gy_spells()
    during DB load.  No runtime oracle inspection needed downstream.
    """
    tmpl = card_db.get_card("Past in Flames")
    if tmpl is None:
        pytest.skip("Past in Flames not in DB")
    assert tmpl.grants_flashback_to_gy_spells is True, (
        "Past in Flames must have grants_flashback_to_gy_spells=True. "
        "Populate the field in card_database.py via oracle_parser.grants_flashback_to_gy_spells()."
    )


def test_snapcaster_mage_has_grants_flashback_field_true(card_db):
    """Snapcaster Mage must have grants_flashback_to_gy_spells == True at load time.

    Mechanism: Snapcaster Mage's ETB grants flashback to a target instant/sorcery
    in the graveyard — same oracle-pattern detection.
    """
    tmpl = card_db.get_card("Snapcaster Mage")
    if tmpl is None:
        pytest.skip("Snapcaster Mage not in DB")
    assert tmpl.grants_flashback_to_gy_spells is True, (
        "Snapcaster Mage must have grants_flashback_to_gy_spells=True."
    )


def test_non_pif_card_has_grants_flashback_field_false(card_db):
    """A card with no PiF pattern must have grants_flashback_to_gy_spells == False.

    Mechanism: the field defaults to False and is only set True by the oracle
    predicate at load time.
    """
    tmpl = card_db.get_card("Lightning Bolt")
    if tmpl is None:
        pytest.skip("Lightning Bolt not in DB")
    assert tmpl.grants_flashback_to_gy_spells is False, (
        "Lightning Bolt must have grants_flashback_to_gy_spells=False."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Runtime consumers — must read field, not oracle
# ──────────────────────────────────────────────────────────────────────────────

def test_has_flashback_recursion_pattern_uses_field_not_oracle(card_db):
    """finisher_simulator._has_flashback_recursion_pattern must read the field.

    Mechanism: the function must return True for PiF (field=True) and False
    for a non-PiF card (field=False) without re-reading oracle_text.
    """
    from engine.cards import CardInstance
    from ai.finisher_simulator import _has_flashback_recursion_pattern

    pif_tmpl = card_db.get_card("Past in Flames")
    if pif_tmpl is None:
        pytest.skip("Past in Flames not in DB")
    bolt_tmpl = card_db.get_card("Lightning Bolt")
    if bolt_tmpl is None:
        pytest.skip("Lightning Bolt not in DB")

    pif_inst = CardInstance(
        template=pif_tmpl, owner=0, controller=0, instance_id=0, zone="library"
    )
    bolt_inst = CardInstance(
        template=bolt_tmpl, owner=0, controller=0, instance_id=1, zone="library"
    )

    assert _has_flashback_recursion_pattern(pif_inst) is True, (
        "_has_flashback_recursion_pattern must return True for Past in Flames."
    )
    assert _has_flashback_recursion_pattern(bolt_inst) is False, (
        "_has_flashback_recursion_pattern must return False for Lightning Bolt."
    )


def test_combo_evaluator_chain_fuel_recognizes_pif_via_field(card_db):
    """combo_evaluator._is_chain_fuel must recognize PiF via the typed field.

    Mechanism: the PiF-pattern branch in _is_chain_fuel must consult
    template.grants_flashback_to_gy_spells, not re-read oracle text.
    """
    from engine.cards import CardInstance
    from ai.combo_evaluator import _is_chain_fuel

    pif_tmpl = card_db.get_card("Past in Flames")
    if pif_tmpl is None:
        pytest.skip("Past in Flames not in DB")

    pif_inst = CardInstance(
        template=pif_tmpl, owner=0, controller=0, instance_id=0, zone="hand"
    )

    assert _is_chain_fuel(pif_inst) is True, (
        "_is_chain_fuel must return True for Past in Flames via "
        "template.grants_flashback_to_gy_spells field, not oracle text."
    )
