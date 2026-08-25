"""Activated abilities parse from oracle text into structured data.

Schema step for the generic activated-ability subsystem. NOTHING reads these
fields yet — this is deliberately a behaviour-neutral commit (see
`test_activation_schema_is_behaviour_neutral.py` for the invariant that makes
that claim checkable).

The grammar is "[Cost]: [Effect]" on a permanent (CR 602). Four traps this
pins, each found by probing the real card pool rather than reasoned about:

  * REMINDER TEXT. A large fraction of colon-bearing lines have their only
    colon inside parentheses — `Equip {3} ({3}: Attach ...)`, `Cycling {2}
    ({2}, Discard this card: Draw a card.)`. The engine has no reminder
    stripper (the only one in the repo lives in `ai/` and engine may not
    import it), so without stripping, keyword reminder text parses as real
    abilities.
  * QUOTED GRANTS. `Creatures you control have "{T}: Add {C}"` grants an
    ability to OTHER objects; the granting permanent has no such ability of
    its own.
  * UNPAYABLE COST ITEMS. A cost this tranche cannot charge (sacrifice, pay
    life, discard, ...) must be parsed and MARKED, never silently dropped —
    that is what makes a later tranche a payer addition rather than a re-parse.
  * "Activate only ..." RIDERS. These must be split off BEFORE effect
    classification or the classifier drops legitimate lines. Only two map to
    booleans; anything else is captured verbatim so it can be refused rather
    than ignored. Note `Activate only once.` (once per GAME) must NOT match
    the once-each-turn anchor.

Rules-phrased throughout; card names appear only as fixture carriers.
"""
from __future__ import annotations

from engine.cards import ActivationEffectKind
from engine.oracle_parser import (
    parse_activated_abilities,
    split_activation_riders,
    strip_reminder_text,
)


# ── reminder text ──────────────────────────────────────────────────────

def test_reminder_text_is_stripped_before_scanning():
    out = strip_reminder_text("Equip {3} ({3}: Attach to target creature.)")
    assert "Attach" not in out, f"reminder text must be removed, got {out!r}"


def test_nested_reminder_parentheses_are_stripped():
    out = strip_reminder_text("Foo (outer (inner) more) bar")
    assert "inner" not in out and "outer" not in out


def test_permanent_whose_only_colon_is_reminder_text_has_no_activated_ability():
    abilities = parse_activated_abilities(
        "Equip {3} ({3}: Attach to target creature. Equip only as a sorcery.)")
    assert abilities == [], (
        f"a keyword's reminder text is not an activated ability; got "
        f"{abilities}")


# ── quoted grants ──────────────────────────────────────────────────────

def test_quoted_ability_grant_is_not_the_granters_own_activation():
    abilities = parse_activated_abilities(
        'Creatures you control have "{T}: Add {C}."')
    assert abilities == [], (
        "an ability granted to OTHER objects is not this permanent's own "
        f"activated ability; got {abilities}")


# ── cost grammar ───────────────────────────────────────────────────────

def test_mana_and_tap_cost_is_parsed_as_payable():
    abilities = parse_activated_abilities("{1}{R}, {T}: Draw a card.")
    assert len(abilities) == 1, f"expected one ability, got {abilities}"
    ab = abilities[0]
    assert ab.cost.tap_self is True, "the {T} symbol must set the tap flag"
    assert ab.cost.mana.cmc == 2, f"expected cmc 2, got {ab.cost.mana.cmc}"
    assert ab.cost.unpayable == (), (
        "mana plus tap is fully payable in this tranche")


def test_unpayable_cost_item_is_marked_not_dropped():
    abilities = parse_activated_abilities(
        "{1}, Sacrifice this creature: Draw a card.")
    assert len(abilities) == 1, (
        "an ability whose cost this tranche cannot charge must still be "
        "PARSED — dropping it would force a re-parse in a later tranche")
    assert abilities[0].cost.unpayable, (
        "the un-chargeable cost item must be recorded so the ability can be "
        "refused rather than mis-activated")


# ── mana abilities (CR 605) ────────────────────────────────────────────

def test_tap_for_mana_is_flagged_as_a_mana_ability():
    abilities = parse_activated_abilities("{T}: Add {G}.")
    assert abilities and abilities[0].is_mana_ability, (
        "a mana ability must be flagged so the enumerator can skip it — mana "
        "is produced by the payment path, not by activating a play")


def test_non_mana_ability_is_not_flagged_as_a_mana_ability():
    abilities = parse_activated_abilities("{T}: Draw a card.")
    assert abilities and not abilities[0].is_mana_ability


# ── "Activate only ..." riders ─────────────────────────────────────────

def test_sorcery_speed_rider_is_recognised():
    body, restrictions, sorcery_only, _once = split_activation_riders(
        "Draw a card. Activate only as a sorcery.")
    assert "Activate only" not in body
    assert sorcery_only is True, "the recognised rider maps to its boolean"
    assert restrictions == (), (
        "a recognised rider maps to a boolean, not to the catch-all")


def test_once_each_turn_rider_does_not_match_once_per_game():
    """`Activate only once.` is once per GAME and must not be treated as
    once each turn — that would silently grant extra activations."""
    _body, restrictions, _sorc, once_each_turn = split_activation_riders(
        "Draw a card. Activate only once.")
    assert once_each_turn is False, (
        "'Activate only once.' is once per GAME — treating it as once each "
        "turn would silently grant extra activations")
    assert restrictions, (
        "an unrepresentable restriction must be captured verbatim so the "
        "ability is refused, not silently permitted")


def test_unrepresentable_restriction_is_captured_verbatim():
    _body, restrictions, _s, _o = split_activation_riders(
        "Draw a card. Activate only if you control a Dragon.")
    assert restrictions, (
        "a restriction the schema cannot express must land in `restrictions` "
        "so `can_activate` can refuse on it")


# ── effect classification ──────────────────────────────────────────────

def test_draw_effect_is_classified_with_its_amount():
    abilities = parse_activated_abilities("{2}: Draw a card.")
    assert abilities and abilities[0].effect_kind is ActivationEffectKind.DRAW_N
    assert abilities[0].amount == 1


def test_draw_with_a_rider_is_not_classified_as_a_plain_draw():
    """Anchored full-sentence matching: a draw with an extra clause is a
    DIFFERENT effect and must not be executed as a plain draw."""
    abilities = parse_activated_abilities(
        "{2}: Draw a card, then exile a card from your hand face down.")
    assert abilities, "the line is still an ability"
    assert abilities[0].effect_kind is not ActivationEffectKind.DRAW_N, (
        "a rider-bearing draw must not classify as a plain draw — executing "
        "only the draw half would silently drop the rider")
